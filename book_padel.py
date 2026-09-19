import asyncio
import os
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")

DIRECT_AMENITY_URL = "https://www.damacliving.com/requests/residency/book-amenity?unit_id=a0x07000008cDwoAAE"


async def wait_until_midnight():
    """Holds execution until 1 second before 12:00:00 AM GST (11:59:59 PM GST)."""
    # Explicitly calculate current time in Dubai (GST = UTC+4)
    gst_now = datetime.utcnow() + timedelta(hours=4)
    target_gst = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)

    # If past midnight, align to next midnight drop
    if gst_now >= target_gst and (gst_now - target_gst).total_seconds() > 60:
        target_gst += timedelta(days=1)

    target_time = target_gst - timedelta(seconds=1)
    wait_seconds = (target_time - gst_now).total_seconds()

    if wait_seconds > 0:
        print(
            f"[{gst_now.strftime('%H:%M:%S')} GST] Standing by on pre-booking screen. Waiting {wait_seconds:.2f}s to click Proceed..."
        )
        await asyncio.sleep(wait_seconds)

    print(f"[{datetime.utcnow() + timedelta(hours=4):%H:%M:%S} GST] Executing Proceed to booking...")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigating to Damac Living...")
        try:
            await page.goto("https://damacliving.com", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"Navigation failed: {e}")
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
            print("Logged in successfully.")
        except Exception as e:
            print(f"Login failed: {e}")
            await browser.close()
            return

        # 2. Direct Navigation & Amenity Setup
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

        # 3. Hold execution until release window
        await wait_until_midnight()

        # 4. Rapid Execution & Selection
        try:
            proceed_btn = page.locator("button:has-text('Proceed to booking')")
            await proceed_btn.wait_for(state="visible", timeout=5000)
            await proceed_btn.click()

            print("Clicked proceed to booking. Loading slots...")
            await asyncio.sleep(1.5)

            # Evaluate dates using Dubai Time (GST)
            gst_now = datetime.utcnow() + timedelta(hours=4)
            current_year = gst_now.year

            # Poll date dropdown up to 10 attempts (approx 5-8 seconds)
            for attempt in range(10):
                date_dropdown = page.locator("div[title='Date'] a.dropdown-toggle")
                await date_dropdown.wait_for(state="visible", timeout=10000)
                await date_dropdown.click()

                date_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]:visible")
                date_count = await date_items.count()

                if date_count > 0:
                    best_index = -1
                    latest_parsed_date = None

                    # Find the absolute latest date entry across the entire menu
                    for i in range(date_count):
                        raw_text = (await date_items.nth(i).text_content()).strip()
                        try:
                            parsed_dt = datetime.strptime(f"{raw_text} {current_year}", "%d %B %Y")
                            if latest_parsed_date is None or parsed_dt > latest_parsed_date:
                                latest_parsed_date = parsed_dt
                                best_index = i
                        except ValueError:
                            continue

                    if best_index != -1:
                        target_item = date_items.nth(best_index)
                        selected_text = (await target_item.text_content()).strip()

                        # Click the maximum parsed calendar date found (e.g. 04 October over 03 October)
                        print(f"Selecting furthest calendar date found: '{selected_text}'")
                        await target_item.click()
                        break

                if attempt < 9:
                    print(f"Attempt {attempt + 1}: Waiting for fresh date drop... Retrying.")
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.5)

            await page.wait_for_timeout(1000)

            # Select Time Slot (Targeting 08:00 PM for the 7:00 PM - 8:00 PM slot)
            time_dropdown = page.locator("div[title='Time'] a.dropdown-toggle")
            await time_dropdown.wait_for(state="visible", timeout=10000)
            await time_dropdown.click()

            preferred_slots = ["08:00 PM"]
            selected_slot_name = None

            for slot in preferred_slots:
                target_locator = page.locator(
                    f"a.dropdown-item[data-rr-ui-dropdown-item]:visible:has-text('{slot}')"
                )
                if await target_locator.count() > 0:
                    selected_slot_name = slot
                    print(f"Selecting slot: {slot} (7:00 PM - 8:00 PM)...")
                    await target_locator.first.click()
                    break

            if not selected_slot_name:
                print("The 7:00 PM - 8:00 PM slot is not available. Aborting booking.")
                await browser.close()
                return

            await page.wait_for_timeout(500)

            # Confirm Booking
            book_now_btn = page.locator(
                "button.button__primary[type='submit']:has-text('Book now')"
            )
            await book_now_btn.wait_for(state="visible", timeout=10000)
            await book_now_btn.click()
            print("Booking submitted successfully for 7:00 PM - 8:00 PM!")

        except Exception as e:
            print(f"Booking attempt failed: {e}")

        await asyncio.sleep(5)
        await browser.close()


asyncio.run(main())
