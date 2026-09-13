import asyncio
import os
import re
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")


async def wait_until_midnight():
    """Holds execution until exactly 12:00:00 AM GST."""
    now = datetime.now()
    target = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if now >= target:
        target += timedelta(days=1)

    wait_seconds = (target - datetime.now()).total_seconds()

    if wait_seconds > 300:
        print("Manual run or outside release window — proceeding immediately.")
        return

    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] Standing by on booking screen. Waiting {wait_seconds:.2f}s for midnight drop..."
    )
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Midnight hit! Scraping date...")


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
            await page.wait_for_selector("#userName", timeout=15000)
            await page.fill("#userName", USERNAME)
            await page.fill("input[type='password']", PASSWORD)
            await page.wait_for_timeout(500)

            login_btn = page.locator("button.button__primary:has-text('Sign in')")
            await login_btn.click()
            print("Logged in successfully.")
        except Exception as e:
            print(f"Login failed: {e}")
            await browser.close()
            return

        # 2. Select Court and Stop
        try:
            await page.wait_for_timeout(5000)
            
            # Step A: Click the main "Book Amenity" section on dashboard
            print("Clicking Book Amenity section...")
            book_amenity_card = page.locator("text=/Book Amenity/i").first
            await book_amenity_card.wait_for(state="visible", timeout=20000)
            await book_amenity_card.click(force=True)

            # Wait for dashboard view to settle
            await page.wait_for_timeout(4000)

            # Step B: Click "New booking"
            print("Clicking New booking...")
            new_booking_btn = page.locator("text=/New booking/i").first
            await new_booking_btn.wait_for(state="visible", timeout=20000)
            await new_booking_btn.click(force=True)

            await page.wait_for_selector(".modal-body", timeout=15000)
            await page.wait_for_timeout(1000)

            court_option = page.locator(".modal-body").get_by_text(
                "D2 Sports Town - Paddle Court 1", exact=True
            )
            await court_option.wait_for(state="visible", timeout=15000)
            await court_option.click(force=True)

            next_btn = page.locator("button.button__primary:has-text('Next')")
            await next_btn.wait_for(state="visible", timeout=15000)
            await next_btn.click()

            print("Court selected successfully. Waiting at the pre-booking screen...")

        except Exception as e:
            print(f"Pre-booking setup failed: {e}")
            try:
                page_text = await page.inner_text("body")
                print("--- PAGE TEXT DUMP ---")
                print(page_text[:1500])
                print("----------------------")
            except Exception as dump_err:
                print(f"Could not dump page text: {dump_err}")
            await browser.close()
            return

        # 3. Precision Hold until 12:00:00 AM
        await wait_until_midnight()

        # 4. Instant Execution at 12:00:00 AM
        try:
            proceed_btn = page.locator("button:has-text('Proceed to booking')")
            await proceed_btn.wait_for(state="visible", timeout=5000)
            await proceed_btn.click()
            
            # Allow full 3-second page loading transition
            print("Clicked proceed to booking. Waiting 3 seconds for full page load...")
            await asyncio.sleep(3)
            await page.wait_for_load_state("networkidle", timeout=10000)

            # Select Newly Unlocked Date
            date_dropdown = page.locator("div[title='Date'] a.dropdown-toggle")
            await date_dropdown.wait_for(state="visible", timeout=10000)
            await date_dropdown.click()

            date_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]")
            date_count = await date_items.count()
            
            if date_count > 0:
                latest_date = date_items.nth(date_count - 1)
                date_text = (await latest_date.text_content()).strip()
                print(f"Latest available date in dropdown: '{date_text}'")
                
                if "27" in date_text and "Sep" in date_text:
                    print("Notice: 27th September already has an active manual booking.")
                
                await latest_date.click()

            await page.wait_for_timeout(2000)

            # Select Time Slot (07:00 PM - 08:00 PM preferred)
            time_dropdown = page.locator("div[title='Time'] a.dropdown-toggle")
            await time_dropdown.wait_for(state="visible", timeout=10000)
            await time_dropdown.click()

            target_time = page.locator(
                "a.dropdown-item[data-rr-ui-dropdown-item]:has-text('07:00 PM - 08:00 PM')"
            )
            if await target_time.count() > 0:
                print("Selecting 07:00 PM - 08:00 PM...")
                await target_time.click()
            else:
                print("Preferred slot unavailable or already booked. Selecting alternative available slot...")
                time_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]")
                time_count = await time_items.count()
                if time_count > 0:
                    await time_items.nth(time_count - 1).click()

            await page.wait_for_timeout(1000)

            # Confirm Booking
            book_now_btn = page.locator(
                "button.button__primary[type='submit']:has-text('Book now')"
            )
            await book_now_btn.wait_for(state="visible", timeout=10000)
            await book_now_btn.click()
            print("Booking submitted successfully!")

            await page.wait_for_timeout(3000)
            body_text = await page.inner_text("body")
            if "already have a booking" in body_text.lower():
                print("Website Notice: You already have a booking for this date/time window.")
            else:
                print("Booking confirmed!")

        except Exception as e:
            print(f"Booking attempt encountered an issue: {e}")

        await asyncio.sleep(5)
        await browser.close()


asyncio.run(main())
