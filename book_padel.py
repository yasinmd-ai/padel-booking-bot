import asyncio
import os
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")

# Unit-specific amenity page URL from DevTools route
DIRECT_AMENITY_URL = "https://www.damacliving.com/requests/residency/book-amenity?unit_id=a0x07000008cDwoAAE"


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
            await page.wait_for_timeout(300)

            login_btn = page.locator("button.button__primary:has-text('Sign in')")
            await login_btn.click()
            
            # Wait for login to complete and dashboard to load
            await page.wait_for_selector("a:has-text('Book Amenity'), div:has-text('Book Amenity')", timeout=15000)
            print("Logged in successfully.")
        except Exception as e:
            print(f"Login failed: {e}")
            await browser.close()
            return

        # 2. Client-Side SPA Navigation to Amenity Page
        try:
            print("Navigating to Amenity Page via SPA router...")
            # Use JS pushState to trigger Next.js client-side navigation without breaking session state
            await page.evaluate(f"window.location.href = '{DIRECT_AMENITY_URL}'")
            
            # Target exact element found in DevTools DOM dump
            new_booking_btn = page.locator("a.button__primary:has-text('New booking'), a:has-text('New booking')").first
            await new_booking_btn.wait_for(state="visible", timeout=15000)
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
            try:
                page_text = await page.inner_text("body")
                print("--- PAGE TEXT DUMP ---")
                print(page_text[:1500])
                print("----------------------")
            except Exception as dump_err:
                print(f"Could not dump page text: {dump_err}")
            await browser.close()
            return

        # 3. Hold execution until 12:00:00 AM GST
        await wait_until_midnight()

        # 4. Rapid Execution at 12:00:00 AM GST
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

            date_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]")
            date_count = await date_items.count()
            
            if date_count > 0:
                latest_date = date_items.nth(date_count - 1)
                date_text = (await latest_date.text_content()).strip()
                print(f"Selecting date: '{date_text}'")
                await latest_date.click()

            await page.wait_for_timeout(1000)

            # Select Time Slot
            time_dropdown = page.locator("div[title='Time'] a.dropdown-toggle")
            await time_dropdown.wait_for(state="visible", timeout=10000)
            await time_dropdown.click()

            target_time = page.locator(
                "a.dropdown-item[data-rr-ui-dropdown-item]:has-text('07:00 PM - 08:00 PM')"
            )
            if await target_time.count() > 0:
                print("Selecting slot: 07:00 PM - 08:00 PM...")
                await target_time.click()
            else:
                print("Selecting first available slot for trial run...")
                time_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]")
                if await time_items.count() > 0:
                    selected_slot = await time_items.nth(0).text_content()
                    print(f"Selecting slot: {selected_slot.strip()}")
                    await time_items.nth(0).click()

            await page.wait_for_timeout(500)

            # Confirm Booking
            book_now_btn = page.locator(
                "button.button__primary[type='submit']:has-text('Book now')"
            )
            await book_now_btn.wait_for(state="visible", timeout=10000)
            await book_now_btn.click()
            print("Booking submitted successfully!")

        except Exception as e:
            print(f"Booking attempt failed: {e}")

        await asyncio.sleep(5)
        await browser.close()


asyncio.run(main())
