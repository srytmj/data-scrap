# scraper.py
"""
Main scraping logic for Medan commodity prices.
This script is called by app.py and handles the actual scraping.
"""

import asyncio
import csv
from pathlib import Path
import re
import sys
import traceback

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from datetime import datetime

date = datetime.now().strftime("%Y-%m-%d")
OUTPUT_FILE = f"harga_medan_{date}.csv"
BASE_URL      = "https://simpang.medan.go.id/?menu=harga"

# Selectors
SEL_KECAMATAN = "select[name='id_kecamatan2']"
SEL_SEND_BTN  = "#sendtbl"
SEL_TABLE     = "#datatable-responsive"
SEL_TBODY     = "#datatable-responsive tbody"
SEL_LENGTH    = "select[name='datatable-responsive_length']"
SEL_PAGINATE  = "#datatable-responsive_paginate"
SEL_NEXT_BTN  = "#datatable-responsive_next"
SEL_PREV_BTN  = "#datatable-responsive_previous"
SEL_INFO      = "#datatable-responsive_info"

WAIT_AFTER_CLICK = 3.0
PAGE_NAV_WAIT   = 2.0


async def get_kecamatan_options(page) -> list[tuple[str, str]]:
    """Wait for the kecamatan <select> and return (value, label) pairs."""
    for selector in [
        "select[name='id_kecamatan2']",
        "select#id_kecamatan2",
    ]:
        try:
            await page.wait_for_selector(selector, timeout=10_000)
            options = await page.evaluate(f"""() => {{
                const sel = document.querySelector("{selector}");
                if (!sel) return [];
                return [...sel.options].map(o => [o.value, o.text.trim()]);
            }}""")
            options = [(v, l) for v, l in options if v.strip() and l.strip()]
            if options:
                return options
        except PlaywrightTimeout:
            pass
        except Exception:
            pass
    
    return []


async def select_kecamatan(page, value: str, label: str):
    """Set the kecamatan select value and trigger events."""
    await page.evaluate("""([val, lbl]) => {
        const sel = document.querySelector("select[name='id_kecamatan2']")
                 || document.getElementById('id_kecamatan2');
        if (!sel) throw new Error('Kecamatan select not found in DOM');
        sel.value = val;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        if (typeof $ !== 'undefined') {
            $(sel).trigger('change');
            $(sel).trigger({
                type: 'select2:select',
                params: { data: { id: val, text: lbl } }
            });
        }
    }""", [value, label])
    await asyncio.sleep(0.5)


async def set_show_entries(page, value: str = "100"):
    """Set the DataTables page-length dropdown."""
    try:
        await page.wait_for_selector(SEL_LENGTH, timeout=5_000)
        await page.select_option(SEL_LENGTH, value=value)
        await asyncio.sleep(1.0)
        await page.evaluate(f"""() => {{
            const sel = document.querySelector("{SEL_LENGTH}");
            if (sel && typeof $ !== 'undefined') {{
                $(sel).trigger('change');
            }}
        }}""")
        await asyncio.sleep(1.0)
        return True
    except PlaywrightTimeout:
        return False


async def wait_for_table_data(page) -> bool:
    """Wait for DataTables to load data."""
    processing_sel = "#datatable-responsive_processing"
    
    for _ in range(20):
        processing = await page.query_selector(processing_sel)
        if not processing:
            break
        style = await processing.get_attribute("style") or ""
        if "display: none" in style or "display:none" in style:
            break
        await asyncio.sleep(0.5)
    
    await asyncio.sleep(0.8)
    
    rows = await page.query_selector_all(f"{SEL_TBODY} tr")
    if not rows:
        return False
    
    first_td = await rows[0].query_selector("td")
    if first_td:
        txt = (await first_td.inner_text()).strip().lower()
        if "no data" in txt or "tidak ada" in txt:
            return False
    
    return True


async def get_total_rows_count(page) -> int:
    """Get total number of rows from DataTables info text."""
    try:
        info_el = await page.query_selector(SEL_INFO)
        if not info_el:
            return 0
        text = await info_el.inner_text()
        match = re.search(r'of\s+([\d,]+)\s+entries?', text, re.IGNORECASE)
        if match:
            return int(match.group(1).replace(",", ""))
    except Exception:
        pass
    return 0


async def scrape_current_page(page) -> list[dict]:
    """Extract all rows from the current DataTable page."""
    rows = await page.query_selector_all(f"{SEL_TBODY} tr")
    results = []
    
    for row in rows:
        cells = await row.query_selector_all("td")
        if len(cells) < 5:
            continue
        
        texts = [await cell.inner_text() for cell in cells]
        texts = [t.strip() for t in texts]
        
        if texts and ("no data" in texts[0].lower() or "tidak ada" in texts[0].lower()):
            continue
        
        results.append({
            "no":             texts[0],
            "komoditas":      texts[1],
            "harga_terendah": texts[2],
            "harga_tertinggi": texts[3],
            "harga_rata_rata": texts[4],
        })
    
    return results


async def scrape_all_pages(page) -> list[dict]:
    """Scrape all pages of the DataTable."""
    all_rows = []
    
    # Try to set to 100 rows per page first
    if await set_show_entries(page, "100"):
        await asyncio.sleep(1.0)
        rows = await scrape_current_page(page)
        all_rows.extend(rows)
        
        total_rows = await get_total_rows_count(page)
        if total_rows > 0 and len(rows) >= total_rows:
            return all_rows
    
    # Paginate through all pages
    page_num = 1
    
    while True:
        rows = await scrape_current_page(page)
        if not rows:
            break
        
        all_rows.extend(rows)
        
        next_btn = await page.query_selector(SEL_NEXT_BTN)
        if not next_btn:
            break
        
        classes = await next_btn.get_attribute("class") or ""
        if "disabled" in classes:
            break
        
        await next_btn.click()
        await asyncio.sleep(PAGE_NAV_WAIT)
        await wait_for_table_data(page)
        await asyncio.sleep(0.5)
        
        page_num += 1
        if page_num > 100:
            break
    
    return all_rows


async def click_send_button_and_wait(page):
    """Click the Tampilkan button and wait for table to load."""
    await page.evaluate(f"""() => {{
        const btn = document.querySelector("{SEL_SEND_BTN}");
        if (btn) btn.scrollIntoView({{behavior: 'smooth', block: 'center'}});
    }}""")
    await asyncio.sleep(0.5)
    
    await page.click(SEL_SEND_BTN, timeout=8_000)
    await asyncio.sleep(WAIT_AFTER_CLICK)
    await wait_for_table_data(page)


async def main():
    """Main scraping function"""
    records = []
    
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={'width': 1920, 'height': 1080},
                locale='id-ID',
            )
            
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)
            
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
            """)
            
            page.set_default_timeout(30_000)
            
            print(f"Loading {BASE_URL} …")
            await page.goto(BASE_URL, wait_until="networkidle", timeout=45_000)
            
            print("Waiting for JS to initialize …")
            await asyncio.sleep(5)
            
            options = await get_kecamatan_options(page)
            
            if not options:
                print("\n[error] Could not find kecamatan options.")
                await page.screenshot(path="debug.png", full_page=True)
                await browser.close()
                return 1
            
            print(f"\nFound {len(options)} kecamatan(s)")
            
            for idx, (value, label) in enumerate(options, start=1):
                print(f"\n[{idx}/{len(options)}] {label} (value={value})")
                
                try:
                    await select_kecamatan(page, value, label)
                    await click_send_button_and_wait(page)
                    
                    has_data = await wait_for_table_data(page)
                    if not has_data:
                        print(f"  → No data available")
                        continue
                    
                    rows = await scrape_all_pages(page)
                    print(f"  → Collected {len(rows)} rows")
                    
                    for row in rows:
                        records.append({
                            "id":              row["no"],
                            "kecamatan":       label,
                            "komoditas":       row["komoditas"],
                            "harga_terendah":  row["harga_terendah"],
                            "harga_tertinggi": row["harga_tertinggi"],
                            "harga_rata_rata": row["harga_rata_rata"],
                        })
                    
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    print(f"  [error] {e}")
                    await page.screenshot(path=f"error_{label}.png")
            
            await browser.close()
        
        # Write CSV
        if records:
            fieldnames = ["id", "kecamatan", "komoditas",
                          "harga_terendah", "harga_tertinggi", "harga_rata_rata"]
            out = Path(OUTPUT_FILE)
            with out.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(records)
            print(f"\n✓ Saved {len(records)} rows → {out.resolve()}")
            return 0
        else:
            print("\n[warn] No records collected.")
            return 1
            
    except Exception as e:
        print(f"Fatal error: {e}")
        traceback.print_exc()
        # Create debug file to signal Cloudflare issue
        Path("debug.png").touch()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)