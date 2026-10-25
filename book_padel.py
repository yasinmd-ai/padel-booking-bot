import asyncio
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright
import aiohttp

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

DIRECT_AMENITY_URL = "https://www.damacliving.com/requests/residency/book-amenity?unit_id=a0x07000008cDwoAAE"

COURT_NAME = "D2 Sports Town - Paddle Court 2"
# Matches slots starting at 7 PM: "07:00 PM", "7:00 PM", "7 PM"
TIME_START_PATTERN = r"0?7(:00)?\s*(PM)"
ANY_TIME_PATTERN = r"\d{1,2}(:\d{2})?\s*(AM|PM)"
SKIP_WEEKENDS = True      # set False to allow Saturday/Sunday bookings
POLL_SECONDS = 30         # how long to wait after midnight for the new date

CLOCK_OFFSET = timedelta(0)


# ---------------------------------------------------------------- time helpers
async def sync_server_time():
    global CLOCK_OFFSET
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head("https://www.damacliving.com", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                date_header = resp.headers.get("Date")
                if date_header:
                    server_time = datetime.strptime(date_header, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                    CLOCK_OFFSET = server_time - datetime.now(timezone.utc)
                    print(f"[SYNC] Server-local clock offset: {CLOCK_OFFSET.total_seconds():.2f}s", flush=True)
    except Exception as e:
        print(f"[SYNC] Time sync failed ({e}), proceeding with local clock.", flush=True)


def synced_now_gst():
    return datetime.now(timezone.utc) + CLOCK_OFFSET + timedelta(hours=4)


def window_already_open():
    """True if we are within 10 minutes after midnight GST (e.g. GitHub started us late)."""
    now = synced_now_gst()
    return now.hour == 0 and now.minute < 10


async def sleep_until_pre_booking_time():
    if TEST_MODE:
        print("[TEST MODE] Skipping pre-booking sleep.", flush=True)
        return

    gst_now = synced_now_gst()
    if window_already_open():
        return

    today_midnight = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight = today_midnight + timedelta(days=1)
    target_pre_time = next_midnight - timedelta(minutes=3)    # start prep at 11:57 PM
    if gst_now < target_pre_time:
        wait_seconds = (target_pre_time - gst_now).total_seconds()
        print(f"[{gst_now:%H:%M:%S} GST] Sleeping {wait_seconds:.0f}s until 11:57 PM GST...", flush=True)
        await asyncio.sleep(wait_seconds)


async def wait_for_exact_midnight():
    if TEST_MODE or window_already_open():
        print("[WAIT] No midnight hold needed.", flush=True)
        return

    gst_now = synced_now_gst()
    next_midnight = gst_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    wait_seconds = (next_midnight - gst_now).total_seconds() - 0.3    # start polling 300ms early
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    print(f"[{synced_now_gst():%H:%M:%S.%f} GST] Midnight reached, polling for the new date...", flush=True)


# ------------------------------------------------------------- page helpers
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


async def save_debug(page, name):
    try:
        await page.screenshot(path=f"{name}.png")
        with open(f"{name}.html", "w", encoding="utf-8") as f:
            f.write(await page.content())
    except Exception:
        pass


TAG_DROPDOWNS_JS = (
    "() => {"
    "document.querySelectorAll('[data-bot-dd]').forEach(e => e.removeAttribute('data-bot-dd'));"
    "const modal = document.querySelector('.modal.show, .modal.in, .modal[style*=\"display: block\"], .card, form');"
    "const root = modal || document.body;"
    "const selectors = 'select, .dropdown-toggle, [data-toggle=\"dropdown\"], [data-bs-toggle=\"dropdown\"], [role=\"combobox\"], .form-control, .form-select, input[type=\"text\"], .dropdown';"
    "const toggles = Array.from(root.querySelectorAll(selectors))"
    ".filter(e => !e.closest('header, nav, footer, .navbar') && e.offsetParent !== null);"
    "toggles.forEach((e, i) => e.setAttribute('data-bot-dd', String(i)));"
    "return toggles.map((e, i) => ({ idx: i, tag: e.tagName, text: ((e.innerText || e.value || e.placeholder || '') + '').trim().slice(0, 60) }));"
    "}"
)


async def tag_dropdowns(page):
    return await page.evaluate(TAG_DROPDOWNS_JS)


async def get_menu_items(page, toggle):
    container = toggle.locator("xpath=ancestor::*[contains(@class,'dropdown')][1]")
    items = container.locator(".dropdown-menu a, .dropdown-menu button, .dropdown-menu .dropdown-item, [role='option']")
    if await items.count() == 0:
        items = page.locator(".dropdown-menu.show a, .dropdown-menu.show button, .dropdown-menu.show .dropdown-item, [role='option']")
    return items


async def read_options(page, idx):
    """Returns (kind, [option texts]). Leaves menus closed."""
    toggle = page.locator(f'[data-bot-dd="{idx}"]').first
    tag = await toggle.evaluate("e => e.tagName")
    if tag == "SELECT":
        texts = await toggle.evaluate("e => Array.from(e.options).map(o => o.text.trim())")
        return "select", [t for t in texts if t]

    await toggle.click()
    await page.wait_for_timeout(350)
    items = await get_menu_items(page, toggle)
    texts = []
    for i in range(await items.count()):
        t = (await items.nth(i).inner_text()).strip()
        if t:
            texts.append(t)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(100)
    return "menu", texts


async def choose_option(page, idx, kind, wanted_text):
    toggle = page.locator(f'[data-bot-dd="{idx}"]').first
    if kind == "select":
        await toggle.select_option(label=wanted_text)
        return True

    await toggle.click()
    await page.wait_for_timeout(350)
    items = await get_menu_items(page, toggle)
    for i in range(await items.count()):
        if (await items.nth(i).inner_text()).strip() == wanted_text:
            await items.nth(i).click()
            return True
    return False


async def find_date_dropdown(page):
    """Date dropdown = first dropdown (inside the popup) whose options contain no clock times."""
    for d in await tag_dropdowns(page):
        kind, texts = await read_options(page, d["idx"])
        if texts and not any(re.search(ANY_TIME_PATTERN, t, re.I) for t in texts):
            return d["idx"], kind, texts
    return None, None, []


async def find_time_dropdown(page):
    for d in await tag_dropdowns(page):
        kind, texts = await read_options(page, d["idx"])
        if any(re.search(ANY_TIME_PATTERN, t, re.I) for t in texts):
            return d["idx"], kind, texts
    return None, None, []


async def click_proceed(page):
    proceed_btn = page.locator("button:has-text('Proceed to booking')")
    await proceed_btn.wait_for(state="visible", timeout=15000)
    await proceed_btn.click(force=True)
    await page.wait_for_timeout(1500)


async def refresh_form(page):
    """Go Back then Proceed again so the server re-sends the date list."""
    try:
        await page.locator("button:has-text('Back')").first.click(timeout=3000)
        await page.wait_for_timeout(500)
        await click_proceed(page)
    except Exception as e:
        print(f"[POLL] refresh_form failed: {e}", flush=True)


# ------------------------------------------------------------------ main flow
async def book_court_target(p):
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
        print("[STEP 0] Navigating to Damac Living login...", flush=True)
        await safe_goto(page, "https://www.damacliving.com")
        await page.wait_for_selector("#userName", timeout=20000)
        await page.fill("#userName", USERNAME)
        await page.fill("input[type='password']", PASSWORD)
        await page.locator("button.button__primary:has-text('Sign in')").click()

        print("[STEP 0] Waiting for dashboard (Book Amenity tile)...", flush=True)
        await page.get_by_text("Book Amenity", exact=False).first.wait_for(state="visible", timeout=45000)
        print("[STEP 0] Logged in successfully!", flush=True)

        # STEP 1: Book Amenity page
        print("[STEP 1] Navigating to Book Amenity page...", flush=True)
        await safe_goto(page, DIRECT_AMENITY_URL)
        await page.wait_for_timeout(2000)

        # STEP 2: New booking
        print("[STEP 2] Clicking 'New booking'...", flush=True)
        new_booking_btn = page.locator("a.button__primary:has-text('New booking'), button:has-text('New booking'), a:has-text('New booking')").first
        await new_booking_btn.wait_for(state="visible", timeout=20000)
        await new_booking_btn.click()
        await page.wait_for_selector(".modal-body", timeout=15000)

        # STEP 3: Select court
        print(f"[STEP 3] Selecting court: '{COURT_NAME}'...", flush=True)
        court_option = page.locator(".modal-body").get_by_text(COURT_NAME, exact=True)
        if await court_option.count() == 0:
            court_option = page.get_by_text("Paddle Court 2", exact=False)
        await court_option.wait_for(state="visible", timeout=15000)
        await court_option.click(force=True)

        # STEP 4: Next
        print("[STEP 4] Clicking 'Next'...", flush=True)
        next_btn = page.locator("button.button__primary:has-text('Next'), button:has-text('Next')").first
        await next_btn.wait_for(state="visible", timeout=15000)
        await next_btn.click(force=True)

        # STEP 5: Go to the date/time form BEFORE midnight (works any time)
        print("[STEP 5] Clicking 'Proceed to booking'...", flush=True)
        await click_proceed(page)

        # Give the page a moment to render the dropdowns after clicking proceed
        print("[STEP 5] Waiting for dropdowns to load...", flush=True)
        await page.wait_for_timeout(2000)

        dds = await tag_dropdowns(page)
        print(f"[STEP 5] Dropdowns found in booking form: {dds}", flush=True)
        await save_debug(page, "05_after_proceed")

        idx, kind, baseline = await find_date_dropdown(page)
        if idx is None:
            print("[STEP 6a] Could not find a date dropdown. See 05_after_proceed.html", flush=True)
            return False
        print(f"[STEP 6a] Date options BEFORE midnight ({kind}): {baseline}", flush=True)

        print("[Court2] Form ready. Holding for midnight...", flush=True)
        await wait_for_exact_midnight()

        # STEP 6a: Wait for the new date, then select it
        skip_wait = TEST_MODE or window_already_open()
        deadline = time.monotonic() + POLL_SECONDS
        attempt = 0
        target_date = None

        while True:
            idx, kind, texts = await find_date_dropdown(page)
            if idx is not None and texts:
                new_dates = [t for t in texts if t not in baseline]
                if skip_wait:
                    target_date = texts[-1]
                    break
                if new_dates:
                    target_date = new_dates[-1]
                    break
            if time.monotonic() > deadline:
                print(f"[STEP 6a] No new date appeared within {POLL_SECONDS}s. Last options: {texts}", flush=True)
                await save_debug(page, "06_no_new_date")
                return False
            attempt += 1
            if attempt % 4 == 0:
                await refresh_form(page)
            await asyncio.sleep(0.25)

        print(f"[STEP 6a] Target date: '{target_date}'", flush=True)
        if SKIP_WEEKENDS and re.search(r"\b(sat|sun)", target_date, re.I):
            print("[STEP 6a] Weekend date. Skipping!", flush=True)
            await save_debug(page, "06_weekend")
            return False

        if not await choose_option(page, idx, kind, target_date):
            print("[STEP 6a] Could not click the target date.", flush=True)
            await save_debug(page, "06_date_click_failed")
            return False

        # STEP 6b: Select 7 PM slot (time list may load after the date is chosen)
        print("[STEP 6b] Looking for the 7 PM slot...", flush=True)
        slot_text = None
        for _ in range(8):
            await page.wait_for_timeout(700)
            t_idx, t_kind, t_texts = await find_time_dropdown(page)
            if t_idx is not None:
                print(f"[STEP 6b] Time options: {t_texts}", flush=True)
                matches = [t for t in t_texts if re.match(r"\s*" + TIME_START_PATTERN, t, re.I)]
                if matches:
                    if await choose_option(page, t_idx, t_kind, matches[0]):
                        slot_text = matches[0]
                    break
        if not slot_text:
            print("[STEP 6b] 7 PM slot not available.", flush=True)
            await save_debug(page, "06_no_slot")
            return False
        print(f"[STEP 6b] Selected slot: {slot_text}", flush=True)
        await save_debug(page, "07_before_book")

        # DRY RUN
        if DRY_RUN:
            print("[DRY RUN] Stopping before final 'Book now'. Dry run successful!", flush=True)
            return True

        # STEP 7: Book now
        print("[STEP 7] Clicking 'Book now'...", flush=True)
        book_now_btn = page.locator("button:has-text('Book now')").first
        await book_now_btn.wait_for(state="visible", timeout=5000)
        await book_now_btn.click(force=True)

        await page.wait_for_timeout(3000)
        success_locator = page.locator("text=/booking confirmed|successfully booked|booking successful|confirmed/i")
        body_text = (await page.inner_text("body"))[:400].replace("\n", " | ")
        if await success_locator.count() > 0:
            booking_success = True
            print("[Court2] SUCCESS: Booked successfully!", flush=True)
        else:
            print(f"[Court2] No confirmation text found. Page says: {body_text}", flush=True)
        await save_debug(page, "08_booking_result")

    except Exception as e:
        print(f"[ERROR] Exception during execution: {e}", flush=True)
        traceback.print_exc()
        await save_debug(page, "error_page")
    finally:
        await browser.close()

    return booking_success


async def main():
    if not USERNAME or not PASSWORD:
        print("[CRITICAL ERROR] DAMAC_USERNAME or DAMAC_PASSWORD environment variables are missing!", flush=True)
        sys.exit(1)

    print(f"[CONFIG] TEST_MODE={TEST_MODE} DRY_RUN={DRY_RUN}", flush=True)
    await sync_server_time()
    await sleep_until_pre_booking_time()

    async with async_playwright() as p:
        success = await book_court_target(p)

    if success:
        print("OVERALL: SUCCESS!", flush=True)
    else:
        print("OVERALL: COURT NOT SECURED.", flush=True)
        sys.exit(1)


asyncio.run(main())
