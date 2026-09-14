import asyncio
import os
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")

DIRECT_AMENITY_URL = "https://www.damacliving.com/requests/residency/book-amenity?unit_id=a0x07000008cDwoAAE"


async def wait_until_midnight():
    """Holds execution until 3 seconds before 12:00:00 AM GST (11:59:57 PM)."""
    now = datetime.now()
    target = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=3)

    if now >= target:
        target += timedelta(days=1)

    wait_seconds = (target - datetime.now()).total_seconds()

    if wait_seconds > 600:
        print("Manual run or outside release window — proceeding immediately.")
        return

    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] Standing by on booking screen. Waiting {wait_seconds:.2f}s to click Proceed (at 11:59:57 PM)..."
    )
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 3 seconds to midnight! Executing Proceed to booking...")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigating to Damac Living...")
        try:
            await page.goto("https://damacliving.com", wait_until="commit", timeout=30000)
        except Exception as e:
            print(f"Navigation failed: {e}")
            await browser.close()
            return

        # 1. Login
        try:
            await page.wait_for_selector("#userName", timeout=15000)
            await page.fill("#userName", USERNAME)
            await page.fill("input[type='password']", PASSWORD)
            await page.wait_for_timeout(500)

            login_btn = page.locator("button.button__primary:has-text('Sign in')")
            await login_btn.click()

            await page.wait_for_timeout(5000)
            print("Logged in successfully.")
        except Exception as e:
            print(f"Login failed: {e}")
            await browser.close()
            return

        # 2. Direct Navigation & Amenity Loading (Restored Working Logic)
        try:
            print("Navigating to Amenity Page...")
            await page.goto(DIRECT_AMENITY_URL, wait_until="domcontentloaded", timeout=30000)

            new_booking_btn = page.locator("a.button__primary:has-text('New booking'), a:has-text('New booking')").first
            await new_booking_btn.wait_for(state="visible", timeout=30000)

            print("Successfully reached Amenity page! Clicking New booking...")
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

            print("Court selected successfully. Waiting at pre-booking screen...")

        except Exception as e:
            print(f"Pre-booking setup failed: {e}")
            await browser.close()
            return

        # 3. Hold execution until 11:59:57 PM GST
        await wait_until_midnight()

        # 4. Rapid Execution starting at 11:59:57 PM GST
        try:
            proceed_btn = page.locator("button:has-text('Proceed to booking')")
            await proceed_btn.wait_for(state="visible", timeout=5000)
            await proceed_btn.click()

            print("Clicked proceed to booking. Loading slots...")
            await asyncio.sleep(2)

            # Select Date
            date_dropdown = page.locator("div[title='Date'] a.dropdown-toggle")
            await date_dropdown.wait_for(state="visible", timeout=10000)
            await date_dropdown.click()

            date_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]:visible")
            date_count = await date_items.count()

            if date_count > 0:
                latest_date = date_items.nth(date_count - 1)
                date_text = (await latest_date.text_content()).strip()
                print(f"Selecting date: '{date_text}'")
                await latest_date.click()

            await page.wait_for_timeout(1000)

            # Select Time Slot (Target Priority: 07:00 PM -> 08:00 PM -> 09:00 PM)
            time_dropdown = page.locator("div[title='Time'] a.dropdown-toggle")
            await time_dropdown.wait_for(state="visible", timeout=10000)
            await time_dropdown.click()

            preferred_slots = ["07:00 PM", "08:00 PM", "09:00 PM"]
            selected_slot_name = None

            for slot in preferred_slots:
                target_locator = page.locator(
                    f"a.dropdown-item[data-rr-ui-dropdown-item]:visible:has-text('{slot}')"
                )
                if await target_locator.count() > 0:
                    selected_slot_name = slot
                    print(f"Selecting slot: {slot}...")
                    await target_locator.first.click()
                    break

            if not selected_slot_name:
                print("No preferred evening slots (07:00 PM, 08:00 PM, 09:00 PM) were available. Aborting booking.")
                await browser.close()
                return

            await page.wait_for_timeout(500)

            # Confirm Booking
            book_now_btn = page.locator(
                "button.button__primary[type='submit']:has-text('Book now')"
            )
            await book_now_btn.wait_for(state="visible", timeout=10000)
            await book_now_btn.click()
            print(f"Booking submitted successfully for slot {selected_slot_name}!")

        except Exception as e:
            print(f"Booking attempt failed: {e}")

        await asyncio.sleep(5)
        await browser.close()


asyncio.run(main())
