import asyncio
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright
import aiohttp

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

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

    target_time = today_midnight - timedelta(milliseconds=200)  # Fire 200ms early
    wait_seconds = (target_time - gst_now).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    print(f"[{synced_now_gst():%H:%M:%S.%f} GST] Midnight target reached! Firing execution...", flush=True)


async def safe_goto(page, url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            print(f"[goto] Attempt {attempt} navigating to {url}...", flush=True)
            await page.goto(url, wait_until="commit", timeout=30000)
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            return
        except Exception as e:
            print(f"[goto] Attempt {attempt} failed: {e}", flush=True)
            if attempt == retries:
                raise
            await page.wait_for_timeout(2000)


async def book_court_target(p, court_name, target_time_code):
    browser = await p.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 768},
        locale="en-AE",
        timezone_id="Asia/Dubai",
    )
    page = await context.new_page()
    booking_success = False

    try:
        # STEP 0: Login
        print(f"[STEP 0] Navigating to Damac Living login...", flush=True)
        await safe_goto(page, "https://www.damacliving.com")

        await page.wait_for_selector("#userName", timeout=20000)
        print(f"[STEP 0] Entering login credentials...", flush=True)
        await page.fill("#userName", USERNAME)
        await page.fill("input[type='password']", PASSWORD)

        await page.locator("button.button__primary:has-text('Sign in')").click()

        print(f"[STEP 0] Waiting for login redirect chain to finish...", flush=True)
        await page.wait_for_url(lambda u: "login" not in u.lower(), timeout=30000)
        await page.wait_for_load_state("networkidle", timeout=30000)
        print(f"[STEP 0] Logged in successfully!", flush=True)
        await page.screenshot(path="01_after_login.png")

        # STEP 1: Book Amenity
        print(f"[STEP 1] Navigating to Book Amenity page...", flush=True)
        await safe_goto(page, DIRECT_AMENITY_URL)
        await page.wait_for_timeout(2000)
        await page.screenshot(path="02_after_amenity.png")

        # STEP 2: New Booking
        print(f"[STEP 2] Clicking 'New booking'...", flush=True)
        new_booking_btn = page.locator("a.button__primary:has-text('New booking'), button:has-text('New booking'), a:has-text('New booking')").first
        await new_booking_btn.wait_for(state="visible", timeout=20000)
        await new_booking_btn.click()

        await page.wait_for_selector(".modal-body", timeout=15000)

        # STEP 3: Selecting Court 2
        print(f"[STEP 3] Selecting court: '{court_name}'...", flush=True)
        court_option = page.locator(".modal-body").get_by_text(court_name, exact=True)
        if await court_option.count() == 0:
            court_option = page.get_by_text("Paddle Court 2", exact=False)

        await court_option.wait_for(state="visible", timeout=15000)
        await court_option.click(force=True)
        print(f"[STEP 3] Selected {court_name}!", flush=True)

        # STEP 4: Click Next
        print(f"[STEP 4] Clicking 'Next'...", flush=True)
        next_btn = page.locator("button.button__primary:has-text('Next'), button:has-text('Next')").first
        await next_btn.wait_for(state="visible", timeout=15000)
        await next_btn.click(force=True)
        print(f"[STEP 4] Clicked 'Next'!", flush=True)
        await page.screenshot(path="03_after_court_select.png")

        print(f"[Court2] Prepped successfully. Holding for midnight...", flush=True)

        # Hold until exact midnight release
        await wait_for_exact_midnight()

        # STEP 5: Proceed to Booking
        print(f"[STEP 5] Inspecting visible buttons before 'Proceed to booking'...", flush=True)
        buttons = await page.evaluate('''() => Array.from(document.querySelectorAll("button, a.button__primary"))
            .filter(b => b.offsetParent !== null)
            .map(b => (b.innerText || "").trim())''')
        print(f"[STEP 5] Visible buttons: {buttons}", flush=True)
        await page.screenshot(path="04_before_proceed.png")

        print(f"[STEP 5] Clicking 'Proceed to booking'...", flush=True)
        proceed_btn = page.locator("button:has-text('Proceed to booking')")
        await proceed_btn.wait_for(state="visible", timeout=15000)
        await proceed_btn.click(force=True)
        print(f"[STEP 5] Clicked 'Proceed to booking' successfully!", flush=True)

        await page.wait_for_timeout(2000)

        # STEP 6a: Selecting Date
        print(f"[STEP 6a] Opening Date dropdown...", flush=True)
        await page.evaluate('''() => {
            const toggles = Array.from(document.querySelectorAll("a.dropdown-toggle, .dropdown-toggle"));
            if (toggles.length > 0) toggles[0].click();
        }''')

        await page.wait_for_timeout(1000)

        date_selected = await page.evaluate('''() => {
            const items = Array.from(document.querySelectorAll("a.dropdown-item"));
            if (items.length > 0) {
                const target = items[items.length - 1];
                target.click();
                return target.innerText.trim();
            }
            return null;
        }''')

        print(f"[STEP 6a] Target date selected via JS: '{date_selected}'", flush=True)

        if not date_selected or "sat" in date_selected.lower() or "sun" in date_selected.lower():
            print(f"[STEP 6a] Weekend or invalid date detected. Skipping!", flush=True)
            await page.screenshot(path="booking_result_Court2.png")
            await browser.close()
            return False

        # STEP 6b: Selecting Time
        print(f"[STEP 6b] Opening Time slot dropdown...", flush=True)
        await page.evaluate('''() => {
            const toggles = Array.from(document.querySelectorAll("a.dropdown-toggle, .dropdown-toggle"));
            if (toggles.length > 1) toggles[1].click();
            else if (toggles.length > 0) toggles[0].click();
        }''')

        await page.wait_for_timeout(1000)

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

        print(f"[STEP 6b] Selected slot via JS: {time_selected}", flush=True)
        if not time_selected:
            print(f"[STEP 6b] Slot {target_time_code} not available.", flush=True)
            await page.screenshot(path="booking_result_Court2.png")
            await browser.close()
            return False

        # DRY RUN check
        if DRY_RUN:
            print("[DRY RUN] Stopping before final 'Book now' click. Dry run successful!", flush=True)
            await page.screenshot(path="booking_result_Court2.png")
            await browser.close()
            return True

        # STEP 7: Book Now
        print(f"[STEP 7] Clicking 'Book now'...", flush=True)
        book_now_btn = page.locator("button.button__primary[type='submit']:has-text('Book now'), button:has-text('Book now')").first
        await book_now_btn.wait_for(state="visible", timeout=5000)
        await book_now_btn.click(force=True)
        print(f"[STEP 7] Clicked 'Book now'!", flush=True)

        await page.wait_for_timeout(3000)
        success_locator = page.locator("text=/booking confirmed|successfully booked|booking successful/i")
        if await success_locator.count() > 0:
            booking_success = True
            print(f"[Court2] SUCCESS: Booked successfully!", flush=True)
        else:
            print(f"[Court2] Submitted, checking result.", flush=True)

        await page.screenshot(path="booking_result_Court2.png")

    except Exception as e:
        print(f"[ERROR] Exception during execution: {e}", flush=True)
        traceback.print_exc()
        try:
            await page.screenshot(path="booking_result_Court2_error.png")
            with open("error_page.html", "w") as f:
                f.write(await page.content())
        except:
            pass

    await browser.close()
    return booking_success


async def main():
    if not USERNAME or not PASSWORD:
        print("[CRITICAL ERROR] DAMAC_USERNAME or DAMAC_PASSWORD environment variables are missing!", flush=True)
        sys.exit(1)

    await sync_server_time()
    await sleep_until_pre_booking_time()

    async with async_playwright() as p:
        success = await book_court_target(
            p,
            court_name="D2 Sports Town - Paddle Court 2",
            target_time_code="07:00 PM"
        )

        if success:
            print("OVERALL: COURT SECURED SUCCESSFULLY!", flush=True)
        else:
            print("OVERALL: COURT NOT SECURED.", flush=True)
            sys.exit(1)


asyncio.run(main())
