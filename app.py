# app.py
"""
Production-ready scraper for Medan commodity prices with auto-retry and Cloudflare bypass.
Designed for AWS deployment with daily scheduling.

Usage:
    python app.py                    # Run once with retries
    python app.py --once             # Run once without retry
    python app.py --max-retries 5    # Run with custom max retries

Output: harga_medan.csv
"""

import asyncio
import csv
import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import traceback

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scraper.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Constants
SCRIPT_DIR = Path(__file__).parent.absolute()
OUTPUT_FILE = SCRIPT_DIR / "harga_medan.csv"
DEBUG_FILE = SCRIPT_DIR / "debug.png"
SCRAPER_SCRIPT = SCRIPT_DIR / "scraper.py"
STATE_FILE = SCRIPT_DIR / "scraper_state.json"

# Configuration
DEFAULT_MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 30


class ScraperOrchestrator:
    """Manages the scraping process with retry logic and state tracking"""
    
    def __init__(self, max_retries: int = DEFAULT_MAX_RETRIES):
        self.max_retries = max_retries
        self.current_retry = 0
        self.state = self._load_state()
        
    def _load_state(self) -> Dict[str, Any]:
        """Load previous scraping state"""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load state file: {e}")
        return {
            'last_successful_run': None,
            'total_retries_today': 0,
            'last_error': None
        }
    
    def _save_state(self):
        """Save current state"""
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")
    
    def _cleanup_debug_files(self):
        """Remove debug files from previous failed runs"""
        if DEBUG_FILE.exists():
            logger.info(f"Removing existing debug file: {DEBUG_FILE}")
            DEBUG_FILE.unlink()
        
        # Also clean up any other debug files
        for pattern in ["error_*.png", "timeout_*.png", "failed_*.png"]:
            for file in SCRIPT_DIR.glob(pattern):
                logger.info(f"Removing old debug file: {file}")
                file.unlink()
    
    def _check_scraper_exists(self) -> bool:
        """Verify the scraper script exists"""
        if not SCRAPER_SCRIPT.exists():
            logger.error(f"Scraper script not found at {SCRAPER_SCRIPT}")
            return False
        return True
    
    async def run_scraper(self) -> tuple[bool, Optional[Path]]:
        """Run the main scraper script and return success status"""
        logger.info(f"Running scraper (attempt {self.current_retry + 1}/{self.max_retries})")
        
        try:
            # Run the scraper as a subprocess
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(SCRAPER_SCRIPT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            # Decode output
            stdout_text = stdout.decode('utf-8', errors='ignore')
            stderr_text = stderr.decode('utf-8', errors='ignore')
            
            # Log output
            if stdout_text:
                logger.info(f"Scraper output:\n{stdout_text[:500]}")  # First 500 chars
            if stderr_text:
                logger.warning(f"Scraper stderr:\n{stderr_text[:500]}")
            
            # Check if scraper was successful
            success = process.returncode == 0
            
            # Check if debug.png was created (indicates Cloudflare error)
            cloudflare_blocked = DEBUG_FILE.exists()
            
            if success and not cloudflare_blocked:
                return True, None
            elif cloudflare_blocked:
                logger.warning("Cloudflare block detected (debug.png found)")
                return False, "CLOUDFLARE_BLOCKED"
            else:
                logger.warning(f"Scraper failed with return code {process.returncode}")
                return False, "SCRAPER_ERROR"
                
        except Exception as e:
            logger.error(f"Failed to run scraper: {e}")
            logger.error(traceback.format_exc())
            return False, f"EXCEPTION: {str(e)}"
    
    def _check_output_valid(self) -> bool:
        """Verify that the output CSV has valid data"""
        if not OUTPUT_FILE.exists():
            logger.warning("Output file not found")
            return False
        
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                rows = list(reader)
                
            # Should have header + at least some data
            if len(rows) < 2:
                logger.warning("Output file has no data rows")
                return False
            
            logger.info(f"Output file has {len(rows) - 1} data rows")
            return True
            
        except Exception as e:
            logger.error(f"Failed to validate output: {e}")
            return False
    
    def _backup_output(self):
        """Create a backup of successful output"""
        if OUTPUT_FILE.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = SCRIPT_DIR / f"harga_medan_{timestamp}.csv"
            try:
                import shutil
                shutil.copy2(OUTPUT_FILE, backup_file)
                logger.info(f"Created backup: {backup_file}")
                
                # Keep only last 30 backups
                backups = sorted(SCRIPT_DIR.glob("harga_medan_*.csv"))
                for old_backup in backups[:-30]:
                    old_backup.unlink()
                    
            except Exception as e:
                logger.warning(f"Failed to create backup: {e}")
    
    async def run_with_retry(self) -> bool:
        """Run scraper with automatic retry on Cloudflare issues"""
        
        # Check if scraper exists
        if not self._check_scraper_exists():
            return False
        
        # Clean up previous debug files
        self._cleanup_debug_files()
        
        logger.info(f"Starting scraper with max {self.max_retries} retries")
        
        for attempt in range(self.max_retries):
            self.current_retry = attempt
            
            # Run the scraper
            success, error_type = await self.run_scraper()
            
            if success:
                # Verify output is valid
                if self._check_output_valid():
                    logger.info(f"✓ Scraping completed successfully on attempt {attempt + 1}")
                    
                    # Update state
                    self.state['last_successful_run'] = datetime.now().isoformat()
                    self.state['total_retries_today'] = 0
                    self.state['last_error'] = None
                    self._save_state()
                    
                    # Create backup
                    self._backup_output()
                    
                    return True
                else:
                    logger.warning("Scraper reported success but output is invalid")
                    if attempt < self.max_retries - 1:
                        logger.info(f"Retrying in {RETRY_DELAY_SECONDS} seconds...")
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
            
            # Handle failure
            if error_type == "CLOUDFLARE_BLOCKED":
                logger.warning(f"Cloudflare blocked the scraper (attempt {attempt + 1})")
            else:
                logger.warning(f"Scraper failed: {error_type} (attempt {attempt + 1})")
            
            # Update state
            self.state['last_error'] = {
                'timestamp': datetime.now().isoformat(),
                'attempt': attempt + 1,
                'error_type': error_type
            }
            self.state['total_retries_today'] = attempt + 1
            self._save_state()
            
            # Retry if not last attempt
            if attempt < self.max_retries - 1:
                logger.info(f"Waiting {RETRY_DELAY_SECONDS} seconds before retry...")
                await asyncio.sleep(RETRY_DELAY_SECONDS)
                self._cleanup_debug_files()  # Clean up before retry
        
        logger.error(f"Failed after {self.max_retries} attempts")
        return False


async def send_alert(message: str, alert_config: Optional[Dict] = None):
    """Send alerts for monitoring (webhook, email, etc.)"""
    # You can implement various alert methods here
    # Example: Send to Slack webhook
    if alert_config and alert_config.get('slack_webhook'):
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                await session.post(alert_config['slack_webhook'], 
                                 json={'text': message})
        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")
    
    # Log critical alerts
    if "FAILED" in message or "ERROR" in message:
        logger.critical(message)
    else:
        logger.info(message)


async def main():
    """Main entry point with command line arguments"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Medan Commodity Price Scraper')
    parser.add_argument('--once', action='store_true', 
                       help='Run once without retry')
    parser.add_argument('--max-retries', type=int, default=DEFAULT_MAX_RETRIES,
                       help=f'Maximum number of retry attempts (default: {DEFAULT_MAX_RETRIES})')
    parser.add_argument('--no-cleanup', action='store_true',
                       help='Skip cleaning debug files before run')
    
    args = parser.parse_args()
    
    # Override max retries if --once is used
    if args.once:
        max_retries = 0  # No retries
    else:
        max_retries = args.max_retries
    
    # Create orchestrator
    orchestrator = ScraperOrchestrator(max_retries=max_retries)
    
    # Run with retry logic
    start_time = datetime.now()
    logger.info(f"=== Scraping started at {start_time} ===")
    
    success = await orchestrator.run_with_retry()
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    # Send alerts based on result
    if success:
        await send_alert(f"✅ Scraping successful! Duration: {duration:.1f}s")
        logger.info(f"=== Scraping completed successfully in {duration:.1f}s ===")
        return 0
    else:
        await send_alert(f"❌ Scraping FAILED after {orchestrator.current_retry + 1} attempts. Duration: {duration:.1f}s")
        logger.error(f"=== Scraping FAILED after {duration:.1f}s ===")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)