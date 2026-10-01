That 2-second load time when clicking "Proceed to booking" is actually the most important piece of the puzzle.
When you click "Proceed to booking", Damac's system sends a backend call to pull the dates and available slots. That network fetch takes 2 full seconds to come back from their server.
What This Means for Tonight's Strategy
If we click "Proceed to booking" after midnight, we lose 2 seconds just waiting for Damac's backend to reply. That is why other bots beat us—they don't wait for that page to load after midnight.
To beat them, we need to kick off that 2-second load BEFORE midnight so the dates and time slots finish loading the exact millisecond 00:00:00 hits.
The Winning Sequence
 * 23:59:58 (2 seconds before midnight): The script clicks "Proceed to booking".
 * 23:59:58 \rightarrow 00:00:00: Damac spends those 2 seconds loading the date and time options while the clock counts down.
 * 00:00:00.0 (Midnight): The time slots finish rendering on screen right as they unlock on the server.
 * 00:00:00.1: The script instantly selects the date, clicks 09:00 PM, and hits Book Now.
Final Code (book_padel.py)
Here is the code tuned to trigger "Proceed to booking" exactly 2.2 seconds before midnight so those 2 seconds of loading happen before the clock strikes 12:
import asyncio
import os
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright
import aiohttp

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")

DIRECT_AMENITY_URL = "https://www.damacliving.com/requests/residency/book-amenity?unit_id=a0x07000008cDwoAAE"

PREFERRED_SLOTS = [
    ("09:00 PM", "8:00 PM - 9:00 PM"),
    ("08:00 PM", "7:00 PM - 8:00 PM"),
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
        print(f"[SYNC] Time sync failed ({e}), proceeding with local clock.", flush=True)


def synced_now_gst():
    return datetime.now(timezone.utc) + CLOCK_OFFSET + timedelta(hours=4)


async def sleep_until_pre_booking_time():
    gst_now = synced_now_gst()
    today_midnight = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight = today_midnight + timedelta(days=1)

    if gst_now < today_midnight + timedelta(minutes=15):
        return

    target_pre_time = next_midnight - timedelta(minutes=2)
    if gst_now < target_pre_time:
        wait_seconds = (target_pre_time - gst_now).total_seconds()
        print(f"[{gst_now:%H:%M:%S} GST] Sleeping {wait_seconds:.2f}s until pre-booking window...", flush=True)
        await asyncio.sleep(wait_seconds)


async def wait_until_exact_time(target_seconds_before_midnight):
    gst_now = synced_now_gst()
    today_midnight = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)

    if gst_now >= today_midnight and (gst_now - today_midnight).total_seconds() > 600:
        today_midnight += timedelta(days=1)

    target_time = today_midnight - timedelta(seconds=target_seconds_before_midnight)
    wait_seconds = (target_time - gst_now).total_seconds()

    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)


async def main():
    await sync_server_time()
    await sleep_until_pre_booking_time()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigating to Damac Living and logging in...", flush=True)
        try:
            await page.goto("https://damacliving.com", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector("#userName", timeout=20000)
            await page.fill("#userName", USERNAME)
            await page.fill("input[type='password']", PASSWORD)
            await page.click("button.button__primary:has-text('Sign in')")
            await page.wait_for_timeout(4000)
            print("Logged in successfully.", flush=True)

            print("Navigating to Amenity Page...", flush=True)
            await page.goto(DIRECT_AMENITY_URL, wait_until="domcontentloaded", timeout=30000)

            new_booking_btn = page.locator("a.button__primary:has-text('New booking'), a:has-text('New booking')").first
            await new_booking_btn.wait_for(state="visible", timeout=30000)
            await new_booking_btn.click()

            await page.wait_for_selector(".modal-body", timeout=15000)
            court_option = page.locator(".modal-body").get_by_text("D2 Sports Town - Paddle Court 1", exact=True)
            await court_option.click(force=True)

            next_btn = page.locator("button.button__primary:has-text('Next')")
            await next_btn.click()
            print("Court selected. Standing by for pre-midnight trigger...", flush=True)

        except Exception as e:
            print(f"Setup failed: {e}", flush=True)
            await browser.close()
            return

        # 1. Click "Proceed to booking" at 23:59:57.8 GST (2.2 seconds before midnight)
        await wait_until_exact_time(2.2)
        print(f"[{synced_now_gst():%H:%M:%S.%f}] Clicking Proceed to booking early to absorb the 2-second load time...", flush=True)
        proceed_btn = page.locator("button:has-text('Proceed to booking')")
        await proceed_btn.wait_for(state="visible", timeout=5000)
        await proceed_btn.click()

        # 2. Select Date & Time immediately as midnight strikes
        try:
            date_dropdown = page.locator("div[title='Date'] a.dropdown-toggle")
            await date_dropdown.wait_for(state="visible", timeout=10000)
            await date_dropdown.click()

            date_target = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]:visible").last
            await date_target.wait_for(state="visible", timeout=10000)
            await date_target.click()

            # 3. Fast Slot Execution
            for slot_code, slot_label in PREFERRED_SLOTS:
                time_dropdown = page.locator("div[title='Time'] a.dropdown-toggle")
                await time_dropdown.wait_for(state="visible", timeout=5000)
                await time_dropdown.click()

                slot_locator = page.locator(f"a.dropdown-item[data-rr-ui-dropdown-item]:visible:has-text('{slot_code}')")
                if await slot_locator.count() > 0:
                    print(f"[{synced_now_gst():%H:%M:%S.%f}] Attempting slot: {slot_code} ({slot_label})", flush=True)
                    await slot_locator.first.click()

                    book_now_btn = page.locator("button.button__primary[type='submit']:has-text('Book now')")
                    await book_now_btn.wait_for(state="visible", timeout=5000)
                    await book_now_btn.click(force=True)

                    combined_result = page.locator(
                        "text=/booking confirmed|successfully booked|booking successful|already booked|no longer available|slot unavailable|failed|error/i"
                    )
                    try:
                        await combined_result.first.wait_for(state="visible", timeout=5000)

                        if await page.locator("text=/booking confirmed|successfully booked|booking successful/i").count() > 0:
                            print(f"CONFIRMED: Successfully booked {slot_label}!", flush=True)
                            break
                        else:
                            print(f"TAKEN: {slot_label} was taken. Attempting fallback slot...", flush=True)
                    except Exception:
                        print("No response within timeout, checking state...", flush=True)

            await page.wait_for_timeout(1000)
            await page.screenshot(path="booking_result_final.png")

        except Exception as e:
            print(f"Execution failed: {e}", flush=True)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

