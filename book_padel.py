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
                await asyncio.sleep(4)  # Wait for slots to load for this date
            except Exception as e:
                print(f"Date selection note: {e}")

            # 5. Select Time Slot and Book (with Fallback to ANY available slot for testing)
            print("Searching for available time slots...", flush=True)
            booked = False
            
            # First try preferred slots
            for slot_label in PREFERRED_SLOTS:
                try:
                    slot_btn = page.locator(f"text='{slot_label}'").first
                    if await slot_btn.count() > 0:
                        print(f"Found preferred slot {slot_label}! Clicking...", flush=True)
                        await slot_btn.click()
                        booked = True
                        break
                except Exception:
                    continue

            # Fallback: If no preferred slot found, grab ANY available slot on the page for testing
            if not booked:
                print("Preferred slots not found. Looking for *any* available slot to test booking flow...", flush=True)
                for generic_selector in [".slot-item:not(.booked)", "button.slot", "[class*='slot']:not([disabled])", "div.time-slot"]:
                    try:
                        slot_elem = page.locator(generic_selector).first
                        if await slot_elem.count() > 0:
                            slot_text = await slot_elem.inner_text()
                            print(f"Found available test slot: '{slot_text.strip()}'. Clicking...", flush=True)
                            await slot_elem.click()
                            booked = True
                            break
                    except Exception:
                        continue

            if booked:
                await asyncio.sleep(2)
                # Click Confirm / Book
                confirm_btn = page.locator("button:has-text('Book'), button:has-text('Confirm'), button:has-text('Proceed')").first
                if await confirm_btn.count() > 0:
                    print("Clicking final book/confirm button...", flush=True)
                    await confirm_btn.click()
                    await asyncio.sleep(5)
                    print("SUCCESS: Test booking completed successfully!")
                else:
                    print("Clicked slot, but could not locate final confirmation button.")
            else:
                print("No slots whatsoever were available to click on this date.")

        except Exception as e:
            print(f"An error occurred during booking: {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(run_full_booking())
