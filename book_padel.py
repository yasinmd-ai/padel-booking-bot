Python
import asyncio
import os
from playwright.async_api import async_playwright

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigating to Damac Living...")
        await page.goto("https://damacliving.com", wait_until="domcontentloaded")

        # 1. Login
        try:
            await page.wait_for_selector("#userName", timeout=10000)
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

        # 2. Open Amenities & Court Modal
        try:
            await page.wait_for_timeout(4000)
            amenity_link = page.locator(
                "#action-book-amenity, a.actionsitem__itemlink"
            ).first
            await amenity_link.wait_for(state="visible", timeout=15000)
            await amenity_link.click(force=True)

            new_booking_btn = page.locator(
                "a.button.button__primary:has-text('New booking')"
            )
            await new_booking_btn.wait_for(state="visible", timeout=15000)
            await new_booking_btn.click()

            # Select Court
            await page.wait_for_selector(".modal-body", timeout=10000)
            await page.wait_for_timeout(1000)

            court_option = page.locator(".modal-body").get_by_text(
                "D2 Sports Town - Paddle Court 1", exact=True
            )
            await court_option.wait_for(state="visible", timeout=10000)
            await court_option.click(force=True)

            next_btn = page.locator("button.button__primary:has-text('Next')")
            await next_btn.wait_for(state="visible", timeout=10000)
            await next_btn.click()

            # Proceed to booking
            proceed_btn = page.locator(
                "button.button__primary:has-text('Proceed to booking')"
            )
            await proceed_btn.wait_for(state="visible", timeout=10000)
            await proceed_btn.click()

            await page.wait_for_timeout(1000)

            # Select Date (Latest Date)
            date_dropdown = page.locator("div[title='Date'] a.dropdown-toggle")
            await date_dropdown.wait_for(state="visible", timeout=10000)
            await date_dropdown.click()

            date_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]")
            date_count = await date_items.count()
            if date_count > 0:
                latest_date = date_items.nth(date_count - 1)
                date_text = await latest_date.text_content()
                print(f"Selecting latest date: {date_text.strip()}")
                await latest_date.click()

            await page.wait_for_timeout(1000)

            # Select Time Slot (07:00 PM - 08:00 PM)
            time_dropdown = page.locator("div[title='Time'] a.dropdown-toggle")
            await time_dropdown.wait_for(state="visible", timeout=10000)
            await time_dropdown.click()

            target_time = page.locator(
                "a.dropdown-item[data-rr-ui-dropdown-item]:has-text('07:00 PM - 08:00 PM')"
            )
            if await target_time.count() > 0:
                print("Selecting 07:00 PM - 08:00 PM slot...")
                await target_time.click()
            else:
                print("07:00 PM - 08:00 PM not available, picking fallback slot...")
                time_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]")
                time_count = await time_items.count()
                if time_count > 0:
                    await time_items.nth(time_count - 1).click()

            await page.wait_for_timeout(1000)

            # Submit Booking
            book_now_btn = page.locator(
                "button.button__primary[type='submit']:has-text('Book now')"
            )
            await book_now_btn.wait_for(state="visible", timeout=10000)
            await book_now_btn.click()
            print("Booking submitted successfully!")

        except Exception as e:
            print(f"Booking step failed: {e}")

        await asyncio.sleep(5)
        await browser.close()


asyncio.run(main())
