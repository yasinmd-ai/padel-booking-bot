import asyncio
import os
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright
import aiohttp

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")

DIRECT_AMENITY_URL = "https://www.damacliving.com/requests/residency/book-amenity?unit_id=a0x07000008cDwoAAE"

CLOCK_OFFSET = timedelta(0)


async def sync_server_time():
    global CLOCK_OFFSET
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head("https://www.damacliving.com", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                date_header = resp.headers.get("Date")
                if date_header:
                    server_time = datetime.strptime(date_header, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                    local_time = datetime.now(timezone.utc)
                    CLOCK_OFFSET = server_time - local_time
                    print(f"[SYNC] Server-local clock offset: {CLOCK_OFFSET.total_seconds():.2f}s", flush=True)
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
        print(f"[{gst_now:%H:%M:%S} GST] Sleeping {wait_seconds:.2f}s until 11:58 PM GST...", flush=True)
        await asyncio.sleep(wait_seconds)


async def wait_for_exact_midnight():
    gst_now = synced_now_gst()
    today_midnight = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if gst_now >= today_midnight and (gst_now - today_midnight).total_seconds() > 600:
        today_midnight += timedelta(days=1)

    target_time = today_midnight - timedelta(milliseconds=200) # Fire 200ms early to account for network transmission
    wait_seconds = (target_time - gst_now).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    print(f"[{synced_now_gst():%H:%M:%S.%f} GST] Midnight target reached! Firing execution...", flush=True)


async def book_court_target(p, task_id, court_name, target_time_code, target_time_label):
    browser = await p.chromium.launch(headless=True)
    context = await browser.new_context()
    page = await context.new_page()
    booking_success = False

    try:
        print(f"[{task_id}] Navigating to Damac Living...", flush=True)
        await page.goto("https://damacliving.com", wait_until="domcontentloaded", timeout=30000)

        # Login
        await page.wait_for_selector("#userName", timeout=20000)
        await page.fill("#userName", USERNAME)
        await page.fill("input[type='password']", PASSWORD)
        await page.locator("button.button__primary:has-text('Sign in')").click()
        await page.wait_for_selector("a:has-text('New booking')", timeout=15000)

        # Amenity Page
        await page.goto(DIRECT_AMENITY_URL, wait_until="domcontentloaded", timeout=30000)
        new_booking_btn = page.locator("a.button__primary:has-text('New booking'), a:has-text('New booking')").first
        await new_booking_btn.wait_for(state="visible", timeout=30000)
        await new_booking_btn.click()

        await page.wait_for_selector(".modal-body", timeout=15000)
        
        # Select Court
        court_option = page.locator(".modal-body").get_by_text(court_name, exact=True)
        await court_option.wait_for(state="visible", timeout=15000)
        await court_option.click(force=True)

        next_btn = page.locator("button.button__primary:has-text('Next')")
        await next_btn.wait_for(state="visible", timeout=15000)
        await next_btn.click()

        print(f"[{task_id}] {court_name} prepped. Holding for midnight...", flush=True)

        # Hold until exact midnight release
        await wait_for_exact_midnight()

        # Proceed instantly
        proceed_btn = page.locator("button:has-text('Proceed to booking')")
        await proceed_btn.wait_for(state="visible", timeout=5000)
        await proceed_btn.click(force=True)

        # Select Date dropdown
        date_dropdown = page.locator("div[title='Date'] a.dropdown-toggle")
        await date_dropdown.wait_for(state="visible", timeout=10000)
        await date_dropdown.click(force=True)

        date_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]:visible")
        await date_items.last.wait_for(state="visible", timeout=10000)
        
        selected_date_text = (await date_items.last.text_content()).strip()
        print(f"[{task_id}] Target date dropdown value: '{selected_date_text}'", flush=True)
        
        # Weekend Check
        if "sat" in selected_date_text.lower() or "sun" in selected_date_text.lower():
            print(f"[{task_id}] Weekend detected ({selected_date_text}). Skipping booking!", flush=True)
            await browser.close()
            return False

        await date_items.last.click(force=True)

        # Select Time Slot dropdown
        time_dropdown = page.locator("div[title='Time'] a.dropdown-toggle")
        await time_dropdown.wait_for(state="visible", timeout=10000)
        await time_dropdown.click(force=True)

        slot_locator = page.locator(f"a.dropdown-item[data-rr-ui-dropdown-item]:visible:has-text('{target_time_code}')")
        if await slot_locator.count() > 0:
            await slot_locator.first.click(force=True)
            print(f"[{task_id}] Selected slot: {target_time_code} ({target_time_label})", flush=True)
        else:
            print(f"[{task_id}] Slot {target_time_code} not available.", flush=True)
            await browser.close()
            return False

        # Confirm Booking instantly
        book_now_btn = page.locator("button.button__primary[type='submit']:has-text('Book now')")
        await book_now_btn.wait_for(state="visible", timeout=5000)
        await book_now_btn.click(force=True)
        print(f"[{task_id}] Clicked 'Book now'!", flush=True)

        await page.wait_for_timeout(2000)
        success_locator = page.locator("text=/booking confirmed|successfully booked|booking successful/i")
        if await success_locator.count() > 0:
            booking_success = True
            print(f"[{task_id}] SUCCESS: Booked successfully!", flush=True)
        else:
            print(f"[{task_id}] Submitted, checking result.", flush=True)

        await page.screenshot(path=f"booking_result_{task_id}.png")

    except Exception as e:
        print(f"[{task_id}] Error: {e}", flush=True)
        try:
            await page.screenshot(path=f"booking_result_{task_id}_error.png")
        except:
            pass

    await browser.close()
    return booking_success


async def main():
    await sync_server_time()
    await sleep_until_pre_booking_time()

    async with async_playwright() as p:
        results = await asyncio.gather(
            book_court_target(p, "Court1", "D2 Sports Town - Paddle Court 1", "07:00 PM", "7:00 PM - 8:00 PM"),
            book_court_target(p, "Court2", "D2 Sports Town - Paddle Court 2", "08:00 PM", "8:00 PM - 9:00 PM")
        )

        if any(results):
            print("OVERALL: AT LEAST ONE COURT SECURED!", flush=True)
        else:
            print("OVERALL: NO COURTS SECURED (Either taken, weekend, or error).", flush=True)


asyncio.run(main())
