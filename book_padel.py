import asyncio
import re
from datetime import datetime, timezone, timedelta
import aiohttp
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
LOGIN_URL = "https://damacliving.com/login"
AMENITY_URL = "https://damacliving.com/amenities"  # Adjust if direct amenity URL differs
COURT_NAME = "D2 Sports Town - Paddle Court 1"
TARGET_SLOT_LABEL = "08:00 PM - 09:00 PM"
GST = timezone(timedelta(hours=4))

async def get_server_clock_offset():
    """Sync local execution clock against Damac's server to mitigate drift."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get("https://damacliving.com", timeout=5) as response:
                server_date = response.headers.get("Date")
                if server_date:
                    server_dt = datetime.strptime(server_date, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                    local_dt = datetime.now(timezone.utc)
                    offset = (server_dt - local_dt).total_seconds()
                    print(f"[SYNC] Server-local clock offset: {offset:+.2f}s", flush=True)
                    return offset
        except Exception as e:
            print(f"[SYNC WARNING] Clock sync failed: {e}. Defaulting offset to 0.0s", flush=True)
    return 0.0

async def run_booking():
    offset = await get_server_clock_offset()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # --- STEP 1: Late Session Login ---
        now_gst = datetime.now(GST)
        target_login = now_gst.replace(hour=23, minute=58, second=0, microsecond=0)
        
        if now_gst < target_login:
            sleep_sec = (target_login - now_gst).total_seconds()
            print(f"[{now_gst.strftime('%H:%M:%S')} GST] Sleeping {sleep_sec:.2f}s until 11:58 PM GST before logging in...", flush=True)
            await asyncio.sleep(sleep_sec)

        print("Navigating to Damac Living...", flush=True)
        await page.goto(LOGIN_URL, wait_until="networkidle")

        # Fill credentials (uses environment variables in GitHub Actions)
        await page.fill("input[name='username'], input[type='email']", "YOUR_EMAIL_HERE")
        await page.fill("input[name='password'], input[type='password']", "YOUR_PASSWORD_HERE")
        await page.click("button[type='submit']")
        await page.wait_for_load_state("networkidle")
        print("Logged in successfully.", flush=True)

        # --- STEP 2: Navigate to Amenity & Hold ---
        print("Navigating to Amenity Page...", flush=True)
        await page.goto(AMENITY_URL, wait_until="networkidle")
        
        # Select target court
        await page.click(f"text='{COURT_NAME}'")
        print("Court selected. Holding at pre-booking screen...", flush=True)

        # --- STEP 3: Midnight Release Wait ---
        now_gst = datetime.now(GST)
        target_release = now_gst.replace(hour=23, minute=59, second=59, microsecond=0)
        
        if now_gst < target_release:
            wait_time = (target_release - now_gst).total_seconds() - offset
            if wait_time > 0:
                print(f"[{now_gst.strftime('%H:%M:%S')} GST] Ready on pre-booking screen. Waiting {wait_time:.2f}s for 11:59:59 PM GST...", flush=True)
                await asyncio.sleep(wait_time)

        print(f"[{datetime.now(GST).strftime('%H:%M:%S')} GST] Midnight release reached! Clicking proceed...", flush=True)
        await page.click("button:has-text('Proceed'), button:has-text('Book')")
        print("Clicked proceed. Waiting directly for date dropdown...", flush=True)

        # --- STEP 4: Select Furthest Available Date & Slot ---
        date_dropdown = page.locator("select[name='date'], div[role='combobox']").first
        await date_dropdown.wait_for(state="visible", timeout=10000)
        await date_dropdown.click()

        # Pick furthest available date in dropdown options
        options = await page.locator("option, div[role='option']").all_text_contents()
        furthest_date = options[-1].strip() if options else ""
        print(f"Selected furthest available date: '{furthest_date}'", flush=True)
        await page.select_option("select[name='date']", label=furthest_date)

        # List all visible slots
        time_slots = await page.locator("select[name='time'] option, .time-slot").all_text_contents()
        print(f"All visible time slots tonight: {time_slots}", flush=True)

        # Select target time slot
        print(f"Selecting slot: 09:00 PM ({TARGET_SLOT_LABEL})...", flush=True)
        await page.select_option("select[name='time']", label=TARGET_SLOT_LABEL)

        # --- STEP 5: Confirm Booking & Capture True Result ---
        # Wait for 'Book now' button to be active (not blocked/disabled by the loading spinner)
        book_now_btn = page.locator("button.button__primary[type='submit']:has-text('Book now'):not([disabled])")
        await book_now_btn.wait_for(state="visible", timeout=10000)
        await book_now_btn.click()
        print(f"Clicked 'Book now' for {TARGET_SLOT_LABEL}. Waiting for confirmation...", flush=True)

        # Wait for Damac's loading overlay/spinner to detach before taking snapshot
        try:
            await page.locator(".spinner, .loading-overlay, .smiley-spinner").wait_for(state="detached", timeout=3000)
        except Exception:
            await page.wait_for_timeout(2000)

        # Capture clear screenshot after UI finishes rendering
        await page.screenshot(path="booking_result_final.png")

        # --- STEP 6: Check Success Text ---
        try:
            success_locator = page.locator(
                "text=/booking|confirmed|submitted|successful|reference|request id|my bookings/i"
            )
            await success_locator.wait_for(state="visible", timeout=3000)
            print("RUN RESULT: CONFIRMED", flush=True)
        except Exception:
            print("UNKNOWN: no explicit success/error text matched. Capturing screenshot for review.", flush=True)
            print("RUN RESULT: UNCONFIRMED/FAILED", flush=True)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_booking())
