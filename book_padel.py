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
    "const modal = document.querySelector('.modal.show, .modal.in, .modal[style*=\"display: block\"]');"
    "const root = modal || document.body;"
    "const toggles = Array.from(root.querySelectorAll('select, .dropdown-toggle, [data-toggle=\"dropdown\"], [data-bs-toggle=\"dropdown\"]'))"
    ".filter(e => !e.closest('header, nav, footer, .navbar') && e.offsetParent !== null);"
    "toggles.forEach((e, i) => e.setAttribute('data-bot-dd', String(i)));"
    "return toggles.map((e, i) => ({ idx: i, tag: e.tagName, text: ((e.innerText || e.value || '') + '').trim().slice(0, 60) }));"
    "}"
)


async def tag_dropdowns(page):
    return await page.evaluate(TAG_DROPDOWNS_JS)


async def get_menu_items(page, toggle):
    container = toggle.locator("xpath=ancestor::*[contains(@class,'dropdown')][1]")
    items = container.locator(".dropdown-menu a, .dropdown-menu button, .dropdown-menu .dropdown-item")
    if await items.count() == 0:
        items = page.locator(".dropdown-menu.show a, .dropdown-menu.show button, .dropdown-menu.show .dropdown-item")
    return items


async def read_options(page, idx):
    """Returns (kind, [option texts]). Leaves menus closed."""
    toggle = page.locator(f'[data-bot-dd="{idx}"]').first
    tag = await toggle.evaluate("e => e.tagName")
    if tag == "SELECT":
        texts = await toggle.evaluate("e => Array.from(e.options).map(o => o.text.trim())")
        return "select", [t for t in texts if t]

    await toggle.click()
    await page.wait_for_timeout(250)
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
    await page.wait_for_timeout(250)
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
