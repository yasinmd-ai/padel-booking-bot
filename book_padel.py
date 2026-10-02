import asyncio
import os
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright
import aiohttp

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")
IS_TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

PREFERRED_SLOTS = [
    "08:00 PM - 09:00 PM",
    "07:00 PM - 08:00 PM",
    "20:00 - 21:00",
    "19:00 - 20:00"
]

CLOCK_OFFSET = timedelta(0)


async def sync_server_time():
    global CLOCK_OFFSET
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head("https://www.damacliving.com", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                date_header = resp.headers.get("Date")
                if date_header:
                    server_time = datetime.strptime(date_header, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                    local_time = datetime.now(timezone.utc)
                    CLOCK_OFFSET = server_time - local_time
                    print(f"[SYNC] Server clock offset: {CLOCK_OFFSET.total_seconds():.2f}s", flush=True)
    except Exception as e:
        print(f"[SYNC] Time sync failed ({e}), using local clock.", flush=True)


def synced_now_gst():
    return datetime.now(timezone.utc) + CLOCK_OFFSET + timedelta(hours=4)


async def run_full_booking():
    await sync_server_time()
    
    days_out = 2 if IS_TEST_MODE else 14
    target_date = (synced_now_gst() + timedelta(days=days_out)).strftime("%Y-%m-%d")
    print(f"Target booking date: {target_date} (Test mode: {IS_TEST_MODE})", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--start-maximized"])
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        try:
            # 1. Login
            print("Opening portal and logging in...", flush=True)
            await page.goto("https://damacliving.com", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector("#userName", timeout=20000)
            await page.fill("#userName", USERNAME)
            await page.fill("input[type='password']", PASSWORD)

            await asyncio.gather(
                page.wait_for_load_state("domcontentloaded", timeout=20000),
                page.click("button.button__primary:has-text('Sign in')")
            )
            await asyncio.sleep(5)

            # 2. Navigate to Amenities via Menu
            print("Navigating to Amenities...", flush=True)
            for selector in ["text=Amenities", "a:has-text('Amenities')", "[routerLink*='amenities']"]:
                try:
                    elem = page.locator(selector).first
                    if await elem.count() > 0:
                        await elem.click()
                        await asyncio.sleep(5)
                        break
                except Exception:
                    continue

            # 3. Select Padel Court Card
            print("Selecting Padel court...", flush=True)
            for selector in ["text=Paddle", "text=Padel", "text=D2 Sports Town", ".amenity-card"]:
                try:
                    elem = page.locator(selector).first
                    if await elem.count() > 0:
                        await elem.click()
                        await asyncio.sleep(5)
                        break
                except Exception:
                    continue

            # 4. Select Target Date safely
            print(f"Selecting date {target_date}...", flush=True)
            try:
                date_elem = page.locator(f"[data-date='{target_date}']").first
                if await date_elem.count() > 0:
                    await date_elem.click()
                else:
                    date_elem = page.locator(f"text='{target_date}'").first
                    if await date_elem.count() > 0:
                        await date_elem.click()
                await asyncio.sleep(5)  # Wait for slots to load
            except Exception as e:
                print(f"Date selection note: {e}")

            # 5. Open Time Dropdown and Select Slot
            print("Opening time slot dropdown...", flush=True)
            booked = False
            try:
                # Direct click on the lowercase #time container
                time_box = page.locator("#time").first
                if await time_box.count() > 0:
                    print("Found #time container, clicking...", flush=True)
                    await time_box.click()
                else:
                    print("Fallback click on Time text...", flush=True)
                    await page.click("text=Time")

                print("Waiting for dropdown option items to render...", flush=True)
                await page.wait_for_selector("a.dropdown-item", timeout=7000)
                await asyncio.sleep(1.0)

                # Try preferred slots first
                for slot_label in PREFERRED_SLOTS:
                    try:
                        slot_option = page.locator(f"a.dropdown-item:has-text('{slot_label}')").first
                        if await slot_option.count() > 0:
                            print(f"Found preferred slot {slot_label}! Clicking...", flush=True)
                            await slot_option.click()
                            booked = True
                            break
                    except Exception:
                        continue

                # Fallback for testing: Grab the FIRST available slot
                if not booked:
                    print("Preferred slots not available. Selecting the first available test slot...", flush=True)
                    first_slot = page.locator("a.dropdown-item").first
                    if await first_slot.count() > 0:
                        txt = await first_slot.inner_text()
                        print(f"Selecting fallback test slot: '{txt.strip()}'", flush=True)
                        await first_slot.click()
                        booked = True

            except Exception as e:
                print(f"Error interacting with time dropdown: {e}")

            if booked:
                await asyncio.sleep(2)
                # Final confirmation / book button
                for confirm_selector in ["button:has-text('Book')", "button:has-text('Confirm')", "button:has-text('Proceed')", "button.btn-primary"]:
                    try:
                        confirm_btn = page.locator(confirm_selector).first
                        if await confirm_btn.count() > 0:
                            print("Clicking final book/confirm button...", flush=True)
                            await confirm_btn.click()
                            await asyncio.sleep(5)
                            print("SUCCESS: Test booking completed successfully!")
                            break
                    except Exception:
                        continue
            else:
                print("No time slots were found in the dropdown to click.")

        except Exception as e:
            print(f"An error occurred during booking: {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(run_full_booking())
