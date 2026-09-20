import asyncio
import os
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")

DIRECT_AMENITY_URL = "https://www.damacliving.com/requests/residency/book-amenity?unit_id=a0x07000008cDwoAAE"


async def sleep_until_pre_booking_time():
    """Sleeps BEFORE opening browser until 11:58:00 PM GST to avoid session timeouts."""
    gst_now = datetime.utcnow() + timedelta(hours=4)
    today_midnight = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Target 11:58:00 PM GST (2 minutes before midnight)
    target_pre_time = today_midnight - timedelta(minutes=2)
    
    seconds_past_midnight = (gst_now - today_midnight).total_seconds()

    # Case A: Launched slightly past midnight (0 - 15 mins) -> Execute immediately
    if 0 <= seconds_past_midnight <= 900:
        print(f"[{gst_now.strftime('%H:%M:%S')} GST] Launched past midnight ({seconds_past_midnight:.1f}s past). Running immediately!", flush=True)
        return

    # Case B: Launched before 11:58 PM GST -> Sleep until 11:58 PM GST
    if gst_now < target_pre_time:
        wait_seconds = (target_pre_time - gst_now).total_seconds()
        print(f"[{gst_now.strftime('%H:%M:%S')} GST] Runner started early. Sleeping {wait_seconds:.2f}s until 11:58 PM GST before logging in...", flush=True)
        await asyncio.sleep(wait_seconds)
    else:
        print(f"[{gst_now.strftime('%H:%M:%S')} GST] Within login window. Proceeding to login immediately...", flush=True)


async def wait_for_exact_midnight():
    """Holds on pre-booking screen until 11:59:59 PM GST."""
    gst_now = datetime.utcnow() + timedelta(hours=4)
    today_midnight = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if gst_now >= today_midnight and (gst_now - today_midnight).total_seconds() > 600:
        today_midnight += timedelta(days=1)

    target_time = today_midnight - timedelta(seconds=1)
    wait_seconds = (target_time - gst_now).total_seconds()

    if wait_seconds > 0:
        print(f"[{gst_now.strftime('%H:%M:%S')} GST] Ready on pre-booking screen. Waiting {wait_seconds:.2f}s for 11:59:59 PM GST...", flush=True)
        await asyncio.sleep(wait_seconds)

    print(f"[{datetime.utcnow() + timedelta(hours=4):%H:%M:%S} GST] Midnight release reached! Clicking proceed...", flush=True)


async def main():
    # 1. Wait FIRST before launching browser/logging in
    await sleep_until_pre_booking_time()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigating to Damac Living...", flush=True)
        try:
            await page.goto("https://damacliving.com", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"Navigation failed: {e}", flush=True)
            await browser.close()
            return

        # 2. Login
        try:
            await page.wait_for_selector("#userName", timeout=20000)
            await page.fill("#userName", USERNAME)
            await page.fill("input[type='password']", PASSWORD)
            await page.wait_for_timeout(500)

            login_btn = page.locator("button.button__primary:has-text('Sign in')")
            await login_btn.click()

            await page.wait_for_timeout(5000)
            print("Logged in successfully.", flush=True)
        except Exception as e:
            print(f"Login failed: {e}", flush=True)
            await browser.close()
            return

        # 3. Setup Amenity
        try:
            print("Navigating to Amenity Page...", flush=True)
            await page.goto(DIRECT_AMENITY_URL, wait_until="domcontentloaded", timeout=30000)

            new_booking_btn = page.locator("a.button__primary:has-text('New booking'), a:has-text('New booking')").first
            await new_booking_btn.wait_for(state="visible", timeout=30000)
            await new_booking_btn.click()

            await page.wait_for_selector(".modal-body", timeout=15000)
            await page.wait_for_timeout(500)

            court_option = page.locator(".modal-body").get_by_text(
                "D2 Sports Town - Paddle Court 1", exact=True
            )
            await court_option.wait_for(state="visible", timeout=15000)
            await court_option.click(force=True)

            next_btn = page.locator("button.button__primary:has-text('Next')")
            await next_btn.wait_for(state="visible", timeout=15000)
            await next_btn.click()

            print("Court selected. Holding at pre-booking screen...", flush=True)

        except Exception as e:
            print(f"Pre-booking setup failed: {e}", flush=True)
            await browser.close()
            return

        # 4. Short Hold until Midnight (if logged in before 12:00:00 AM)
        await wait_for_exact_midnight()

        # 5. Rapid Execution
        try:
            proceed_btn = page.locator("button:has-text('Proceed to booking')")
            await proceed_btn.wait_for(state="visible", timeout=5000)
            await proceed_btn.click()

            print("Clicked proceed. Waiting 1.5s for Damac backend date release...", flush=True)
            await asyncio.sleep(1.5)

            # Open Date Dropdown
            date_dropdown = page.locator("div[title='Date'] a.dropdown-toggle")
            await date_dropdown.wait_for(state="visible", timeout=10000)
            await date_dropdown.click()

            # Select the newly released date (last item)
            date_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]:visible")
            await date_items.last.wait_for(state="visible", timeout=5000)
            
            selected_text = (await date_items.last.text_content()).strip()
            print(f"Selected furthest available date: '{selected_text}'", flush=True)
            await date_items.last.click()

            await page.wait_for_timeout(1000)

            # Open Time Dropdown
            time_dropdown = page.locator("div[title='Time'] a.dropdown-toggle")
            await time_dropdown.wait_for(state="visible", timeout=10000)
            await time_dropdown.click()

            # Priority Slots: 08:00 PM (7-8 PM) first, fallback to 07:00 PM (6-7 PM)
            preferred_slots = [("08:00 PM", "7:00 PM - 8:00 PM"), ("07:00 PM", "6:00 PM - 7:00 PM")]
            selected_slot_label = None

            for slot_code, slot_label in preferred_slots:
                slot_locator = page.locator(f"a.dropdown-item[data-rr-ui-dropdown-item]:visible:has-text('{slot_code}')")
                if await slot_locator.count() > 0:
                    selected_slot_label = slot_label
                    print(f"Selecting slot: {slot_code} ({slot_label})...", flush=True)
                    await slot_locator.first.click()
                    break

            if not selected_slot_label:
                print("Neither 7-8 PM nor 6-7 PM slots are available. Aborting booking.", flush=True)
                await browser.close()
                return

            await page.wait_for_timeout(500)

            # Confirm Booking
            book_now_btn = page.locator("button.button__primary[type='submit']:has-text('Book now')")
            await book_now_btn.wait_for(state="visible", timeout=10000)
            await book_now_btn.click()
            print(f"Clicked 'Book now' for {selected_slot_label}. Waiting for backend response...", flush=True)

            # Wait for backend response to ensure request completes
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
                print(f"Network request completed successfully for {selected_slot_label}!", flush=True)
            except Exception:
                print("Network idle wait timed out. Holding extra 10s for slow processing...", flush=True)

            # Final 10 second safety wait
            await asyncio.sleep(10)

        except Exception as e:
            print(f"Booking attempt failed: {e}", flush=True)

        await browser.close()


asyncio.run(main())
