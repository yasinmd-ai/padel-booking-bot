import asyncio
import os
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright
import aiohttp

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

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
    if TEST_MODE:
        print("[TEST MODE] Skipping pre-booking sleep.", flush=True)
        return

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
    if TEST_MODE:
        print("[TEST MODE] Skipping midnight hold.", flush=True)
        return

    gst_now = synced_now_gst()
    today_midnight = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if gst_now >= today_midnight and (gst_now - today_midnight).total_seconds() > 600:
        today_midnight += timedelta(days=1)

    target_time = today_midnight - timedelta(milliseconds=200) # Fire 200ms early
    wait_seconds = (target_time - gst_now).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    print(f"[{synced_now_gst():%H:%M:%S.%f} GST] Midnight target reached! Firing execution...", flush=True)


async def book_court_target(p, court_name, target_time_code, target_time_label):
    browser = await p.chromium.launch(headless=True)
    context = await browser.new_context()
    page = await context.new_page()
    booking_success = False

    try:
        print(f"[Court2] Navigating to Damac Living...", flush=True)
        await page.goto("https://damacliving.com", wait_until="domcontentloaded", timeout=30000)

        # Login
        await page.wait_for_selector("#userName", timeout=20000)
        await page.fill("#userName", USERNAME)
        await page.fill("input[type='password']", PASSWORD)
        await page.locator("button.button__primary:has-text('Sign in')").click()
        await page.wait_for_timeout(3000)

        # Direct Amenity Page (Bypasses "New booking" button since unit_id is specified)
        print(f"[Court2] Navigating directly to amenity URL...", flush=True)
        await page.goto(DIRECT_AMENITY_URL, wait_until="domcontentloaded", timeout=30000)
        
        # Wait directly for modal/court options to load
        await page.wait_for_selector(".modal-body, .card, body", timeout=15000)
        await page.wait_for_timeout(2000)
        
        # Select Court
        court_option = page.locator("get_by_text, .modal-body").get_by_text(court_name, exact=True)
        if await court_option.count() == 0:
            court_option = page.get_by_text(court_name, exact=True)
            
        await court_option.wait_for(state="visible", timeout=15000)
        await court_option.click(force=True)

        next_btn = page.locator("button.button__primary:has-text('Next')")
        await next_btn.wait_for(state="visible", timeout=15000)
        await next_btn.click()

        print(f"[Court2] {court_name} prepped. Holding for midnight...", flush=True)

        # Hold until exact midnight release
        await wait_for_exact_midnight()

        # Proceed instantly
        proceed_btn = page.locator("button:has-text('Proceed to booking')")
        await proceed_btn.wait_for(state="visible", timeout=15000)
        await proceed_btn.click(force=True)

        # Wait for booking form elements to settle
        await page.wait_for_timeout(2000)

        # Resilient Date Dropdown Clicker via JS
        print("[Court2] Opening Date dropdown via JS...", flush=True)
        await page.evaluate('''() => {
            const toggles = Array.from(document.querySelectorAll("a.dropdown-toggle, .dropdown-toggle, div[title='Date'] a"));
            if (toggles.length > 0) {
                toggles[0].click();
            }
        }''')

        await page.wait_for_timeout(1000)

        # Select the last item in the date dropdown (the newly opened date)
        date_selected = await page.evaluate('''() => {
            const items = Array.from(document.querySelectorAll("a.dropdown-item"));
            if (items.length > 0) {
                const target = items[items.length - 1];
                target.click();
                return target.innerText.trim();
            }
            return null;
        }''')

        print(f"[Court2] Target date selected via JS: '{date_selected}'", flush=True)
        
        if not date_selected or "sat" in date_selected.lower() or "sun" in date_selected.lower():
            print(f"[Court2] Weekend or invalid date detected. Skipping!", flush=True)
            await browser.close()
            return False

        # Resilient Time Slot Dropdown Clicker via JS
        print("[Court2] Opening Time dropdown via JS...", flush=True)
        await page.wait_for_timeout(1000)
        await page.evaluate('''() => {
            const toggles = Array.from(document.querySelectorAll("a.dropdown-toggle, .dropdown-toggle, div[title='Time'] a"));
            if (toggles.length > 1) {
                toggles[1].click();
            } else if (toggles.length > 0) {
                toggles[0].click();
            }
        }''')

        await page.wait_for_timeout(1000)

        # Pick the exact time slot safely
        time_selected = await page.evaluate('''async (timeCode) => {
            const items = Array.from(document.querySelectorAll("a.dropdown-item"));
            for (const item of items) {
                if (item.innerText.includes(timeCode)) {
                    item.click();
                    return item.innerText.trim();
                }
            }
            return null;
        }''', target_time_code)

        print(f"[Court2] Selected slot via JS: {time_selected}", flush=True)
        if not time_selected:
            print(f"[Court2] Slot {target_time_code} not available.", flush=True)
            await browser.close()
            return False

        # Confirm Booking instantly
        book_now_btn = page.locator("button.button__primary[type='submit']:has-text('Book now'), button:has-text('Book now')").first
        await book_now_btn.wait_for(state="visible", timeout=5000)
        await book_now_btn.click(force=True)
        print(f"[Court2] Clicked 'Book now'!", flush=True)

        await page.wait_for_timeout(3000)
        success_locator = page.locator("text=/booking confirmed|successfully booked|booking successful/i")
        if await success_locator.count() > 0:
            booking_success = True
            print(f"[Court2] SUCCESS: Booked successfully!", flush=True)
        else:
            print(f"[Court2] Submitted, checking result.", flush=True)

        await page.screenshot(path="booking_result_Court2.png")

    except Exception as e:
        print(f"[Court2] Error: {e}", flush=True)
        try:
            await page.screenshot(path="booking_result_Court2_error.png")
        except:
            pass

    await browser.close()
    return booking_success


async def main():
    await sync_server_time()
    await sleep_until_pre_booking_time()

    async with async_playwright() as p:
        success = await book_court_target(
            p, 
            court_name="D2 Sports Town - Paddle Court 2", 
            target_time_code="07:00 PM", 
            target_time_label="7:00 PM - 8:00 PM"
        )

        if success:
            print("OVERALL: COURT SECURED SUCCESSFULLY!", flush=True)
        else:
            print("OVERALL: COURT NOT SECURED (Either taken, weekend, or error).", flush=True)


asyncio.run(main())
