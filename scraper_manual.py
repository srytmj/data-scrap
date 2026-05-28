"""
Scraper for https://simpang.medan.go.id/?menu=harga
Scrapes commodity prices per kecamatan using Playwright (headless) + Full pagination support.

Usage:
    pip install playwright playwright-stealth
    playwright install chromium
    python scrape_harga_medan.py

Output: harga_medan.csv
"""

import asyncio
import csv
from pathlib import Path
import re

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from datetime import datetime

date = datetime.now().strftime("%Y-%m-%d")
OUTPUT_FILE = f"harga_medan_{date}.csv"
BASE_URL      = "https://simpang.medan.go.id/?menu=harga"

# ── Selectors ──────────────────────────────────────────────────────────────────
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


# ── Helpers ────────────────────────────────────────────────────────────────────

async def debug_page_state(page):
    """Print diagnostics to help identify selector issues."""
    info = await page.evaluate("""() => {
        const sel = document.querySelector("select[name='id_kecamatan2']")
                 || document.querySelector("select#id_kecamatan2");
        if (!sel) return { found: false };
        return {
            found: true,
            optionCount: sel.options.length,
            firstFew: [...sel.options].slice(0,3).map(o => o.value + ':' + o.text)
        };
    }""")
    if info.get('found'):
        print(f"  [debug] Found select with {info['optionCount']} options")


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
                print(f"  [ok] Found select with selector: {selector}")
                return options
        except PlaywrightTimeout:
            pass
        except Exception as e:
            print(f"  [warn] Error with selector {selector}: {e}")

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
        # Wait for it to appear (only visible after first table load)
        await page.wait_for_selector(SEL_LENGTH, timeout=5_000)
        await page.select_option(SEL_LENGTH, value=value)
        await asyncio.sleep(1.0)
        # Trigger change event
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
    
    # Wait for spinner to disappear
    for _ in range(20):
        processing = await page.query_selector(processing_sel)
        if not processing:
            break
        style = await processing.get_attribute("style") or ""
        if "display: none" in style or "display:none" in style:
            break
        await asyncio.sleep(0.5)
    
    await asyncio.sleep(0.8)
    
    # Check if there's data
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
        # Pattern: "Showing 1 to 10 of 47 entries"
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
        
        texts = []
        for cell in cells:
            text = await cell.inner_text()
            texts.append(text.strip())
        
        # Skip "No data available" row
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
    """
    Scrape all pages of the DataTable by clicking through pagination.
    First tries to show 100 rows per page, then paginates if needed.
    """
    all_rows = []
    
    # Try to set to 100 rows per page first
    print(f"  Attempting to show 100 rows per page...")
    if await set_show_entries(page, "100"):
        print(f"  Successfully set to 100 rows per page")
        await asyncio.sleep(1.0)
        
        # Get all rows on current page
        rows = await scrape_current_page(page)
        all_rows.extend(rows)
        
        # Check if there are more pages
        total_rows = await get_total_rows_count(page)
        print(f"  Showing {len(rows)} of {total_rows} total rows")
        
        # If we have all rows, return
        if total_rows > 0 and len(rows) >= total_rows:
            return all_rows
    
    # If 100 rows per page didn't work or there are still more pages, paginate
    print(f"  Paginating through all pages...")
    page_num = 1
    
    while True:
        # Scrape current page
        rows = await scrape_current_page(page)
        if not rows:
            break
        
        print(f"  Page {page_num}: {len(rows)} rows")
        all_rows.extend(rows)
        
        # Check if there's a next button and it's not disabled
        next_btn = await page.query_selector(SEL_NEXT_BTN)
        if not next_btn:
            break
        
        # Check if next button is disabled
        classes = await next_btn.get_attribute("class") or ""
        if "disabled" in classes:
            break
        
        # Click next button
        await next_btn.click()
        await asyncio.sleep(PAGE_NAV_WAIT)
        
        # Wait for table to update
        await wait_for_table_data(page)
        await asyncio.sleep(0.5)
        
        page_num += 1
        
        # Safety limit to prevent infinite loops
        if page_num > 100:
            print(f"  [warn] Reached page limit (100), stopping")
            break
    
    print(f"  Total pages scraped: {page_num}, total rows: {len(all_rows)}")
    return all_rows


async def click_send_button_and_wait(page):
    """Click the Tampilkan button and wait for table to load."""
    # Scroll to button if needed
    await page.evaluate(f"""() => {{
        const btn = document.querySelector("{SEL_SEND_BTN}");
        if (btn) btn.scrollIntoView({{behavior: 'smooth', block: 'center'}});
    }}""")
    await asyncio.sleep(0.5)
    
    # Click button
    await page.click(SEL_SEND_BTN, timeout=8_000)
    await asyncio.sleep(WAIT_AFTER_CLICK)
    
    # Wait for table to load
    await wait_for_table_data(page)


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    records = []

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
        
        # Apply stealth to bypass Cloudflare
        await Stealth().apply_stealth_async(page)
        
        # Additional stealth scripts
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """)
        
        page.set_default_timeout(30_000)
        
        print(f"Loading {BASE_URL} …")
        try:
            await page.goto(BASE_URL, wait_until="networkidle", timeout=45_000)
        except PlaywrightTimeout:
            print("[warn] Page load timeout, continuing...")
        
        print("Waiting for JS to initialize …")
        await asyncio.sleep(5)
        
        # Diagnostic
        await debug_page_state(page)
        
        # Get kecamatan options
        options = await get_kecamatan_options(page)
        
        if not options:
            print("\n[error] Could not find kecamatan options.")
            await page.screenshot(path="debug.png", full_page=True)
            await browser.close()
            return
        
        print(f"\nFound {len(options)} kecamatan(s)")
        
        # Scrape each kecamatan
        for idx, (value, label) in enumerate(options, start=1):
            print(f"\n[{idx}/{len(options)}] {label} (value={value})")
            
            try:
                # Select kecamatan
                await select_kecamatan(page, value, label)
                
                # Click Tampilkan button and wait for data
                await click_send_button_and_wait(page)
                
                # Check if data exists
                has_data = await wait_for_table_data(page)
                if not has_data:
                    print(f"  → No data available for this kecamatan")
                    continue
                
                # Scrape all pages
                rows = await scrape_all_pages(page)
                print(f"  → Collected {len(rows)} total rows")
                
                # Add kecamatan name to each row
                for row in rows:
                    records.append({
                        "id":              row["no"],
                        "kecamatan":       label,
                        "komoditas":       row["komoditas"],
                        "harga_terendah":  row["harga_terendah"],
                        "harga_tertinggi": row["harga_tertinggi"],
                        "harga_rata_rata": row["harga_rata_rata"],
                    })
                
                # Small delay between kecamatan
                await asyncio.sleep(2)
                
            except PlaywrightTimeout as e:
                print(f"  [timeout] {e}")
                await page.screenshot(path=f"timeout_{label}.png")
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
        
        # Print summary by kecamatan
        print("\n📊 Summary by kecamatan:")
        kecamatan_counts = {}
        for record in records:
            kecamatan = record["kecamatan"]
            kecamatan_counts[kecamatan] = kecamatan_counts.get(kecamatan, 0) + 1
        for kec, count in kecamatan_counts.items():
            print(f"  {kec}: {count} commodities")
    else:
        print("\n[warn] No records collected. CSV not written.")


if __name__ == "__main__":
    asyncio.run(main())