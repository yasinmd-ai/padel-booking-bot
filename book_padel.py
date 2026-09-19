import asyncio
import os
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")

DIRECT_AMENITY_URL = "https://www.damacliving.com/requests/residency/book-amenity?unit_id=a0x07000008cDwoAAE"


async def wait_until_midnight():
    """Holds execution until 1 second before midnight GST (11:59:59 PM GST)."""
    gst_now = datetime.utcnow() + timedelta(hours=4)
    target_gst = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Align to next midnight drop
    if gst_now >= target_gst and (gst_now - target_gst).total_seconds() > 60:
        target_gst += timedelta(days=1)

    target_time = target_gst - timedelta(seconds=1)
    wait_seconds = (target_time - gst_now).total_seconds()

    if wait_seconds > 0:
        print(
            f"[{gst_now.strftime('%H:%M:%S')} GST] Logged in & ready on pre-booking screen. Waiting {wait_seconds:.2f}s for release...",
            flush=True,
        )
        await asyncio.sleep(wait_seconds)

    print(f"[{datetime.utcnow() + timedelta(hours=4):%H:%M:%S} GST] Release time reached! Executing Proceed to booking...", flush=True)


async def main():
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

        # 1. Login
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

        # 2. Setup Amenity
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

            print("Court selected successfully. Holding at pre-booking screen...", flush=True)

        except Exception as e:
            print(f"Pre-booking setup failed: {e}", flush=True)
            await browser.close()
            return

        # 3. Midnight Hold
        await wait_until_midnight()

        # 4. Rapid Execution & Selection
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

            # Buffer allowing time dropdown options to render for the newly picked date
            await page.wait_for_timeout(1000)

            # Open Time Dropdown
            time_dropdown = page.locator("div[title='Time'] a.dropdown-toggle")
            await time_dropdown.wait_for(state="visible", timeout=10000)
            await time_dropdown.click()

            # Target 08:00 PM (7:00 PM - 8:00 PM slot)
            slot_locator = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]:visible:has-text('08:00 PM')")
            await slot_locator.first.wait_for(state="visible", timeout=5000)
            print("Selecting 08:00 PM slot...", flush=True)
            await slot_locator.first.click()

            await page.wait_for_timeout(500)

            # Confirm Booking
            book_now_btn = page.locator("button.button__primary[type='submit']:has-text('Book now')")
            await book_now_btn.wait_for(state="visible", timeout=10000)
            await book_now_btn.click()
            print("Booking submitted successfully for 7:00 PM - 8:00 PM!", flush=True)

        except Exception as e:
            print(f"Booking attempt failed: {e}", flush=True)

        await asyncio.sleep(5)
        await browser.close()


asyncio.run(main())
