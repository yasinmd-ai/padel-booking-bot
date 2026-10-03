import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright
import aiohttp

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

DIRECT_AMENITY_URL = "https://www.damacliving.com/requests/residency/book-amenity?unit_id=a0x07000008cDwoAAE"

# Priority order of preferred slots. Written in a normalized, zero-padded
# "HH:MM AM/PM - HH:MM AM/PM" form -- actual dropdown text is normalized the
# same way before comparing (see normalize_slot_text), so differences in
# dash character (-, --, en dash) or incidental whitespace won't cause a
# false "not available" the way strict `==` matching did last night.
#
# 9:00 PM - 10:00 PM is included as an explicit third choice: on the night
# you tested, it was the ONLY PM slot released at all (7-8 and 8-9 PM were
# not even in the list), so treating it as a real fallback instead of
# aborting makes sense if that pattern repeats.
PREFERRED_SLOTS = [
    "08:00 PM - 09:00 PM",
    "07:00 PM - 08:00 PM",
    "09:00 PM - 10:00 PM",
]

CLOCK_OFFSET = timedelta(0)  # Populated by sync_server_time()


def normalize_slot_text(text: str) -> str:
    """
    Normalizes a slot label for robust comparison:
    - collapses all dash variants (-, --, en dash, em dash) to a single '-'
    - collapses all whitespace runs to a single space
    - zero-pads a leading single-digit hour (e.g. '8:00 PM' -> '08:00 PM')
    - strips leading/trailing whitespace
    This avoids the exact-match bug where '8:00 PM - 9:00 PM' never matched
    the real dropdown text '08:00 PM - 09:00 PM'.
    """
    if not text:
        return ""
    t = text.strip()
    t = re.sub(r"[\u2010-\u2015\u2212]", "-", t)  # normalize dash variants
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s*-\s*", " - ", t)
    # zero-pad single-digit hour at start of a time token, e.g. "8:00" -> "08:00"
    t = re.sub(r"(?<!\d)(\d):(\d{2})", r"0\1:\2", t)
    return t.strip()


async def sync_server_time():
    """
    Fetches the Date header from damacliving.com and computes the offset
    between the runner's local clock and Damac's server clock.
    """
    global CLOCK_OFFSET
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head("https://www.damacliving.com", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                date_header = resp.headers.get("Date")
                if date_header:
                    server_time = datetime.strptime(date_header, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                    local_time = datetime.now(timezone.utc)
                    CLOCK_OFFSET = server_time - local_time
                    print(f"[SYNC] Server-local clock offset: {CLOCK_OFFSET.total_seconds():.2f}s", flush=True)
                else:
                    print("[SYNC] No Date header returned, proceeding with local clock (offset=0).", flush=True)
    except Exception as e:
        print(f"[SYNC] Time sync failed ({e}), proceeding with local clock (offset=0).", flush=True)


def synced_now_gst():
    """Local time corrected by CLOCK_OFFSET, converted to GST (UTC+4)."""
    return datetime.now(timezone.utc) + CLOCK_OFFSET + timedelta(hours=4)


async def sleep_until_pre_booking_time():
    """Sleeps BEFORE opening browser until ~11:58:00 PM GST to avoid session timeouts."""
    if TEST_MODE:
        print("[TEST_MODE] Skipping pre-booking sleep timer.", flush=True)
        return

    gst_now = synced_now_gst()
    today_midnight = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight = today_midnight + timedelta(days=1)

    if gst_now < today_midnight + timedelta(minutes=15):
        print(f"[{gst_now:%H:%M:%S} GST] Launched just past midnight. Running immediately!", flush=True)
        return

    target_pre_time = next_midnight - timedelta(minutes=2)

    if gst_now < target_pre_time:
        wait_seconds = (target_pre_time - gst_now).total_seconds()
        print(f"[{gst_now:%H:%M:%S} GST] Sleeping {wait_seconds:.2f}s until 11:58 PM GST before logging in...", flush=True)
        await asyncio.sleep(wait_seconds)
    else:
        print(f"[{gst_now:%H:%M:%S} GST] Within login window. Proceeding to login immediately...", flush=True)


async def wait_for_exact_midnight():
    """Holds on pre-booking screen until exactly 11:59:59 PM GST."""
    if TEST_MODE:
        print("[TEST_MODE] Skipping exact midnight wait timer.", flush=True)
        return

    gst_now = synced_now_gst()
    today_midnight = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)

    if gst_now >= today_midnight and (gst_now - today_midnight).total_seconds() > 600:
        today_midnight += timedelta(days=1)

    target_time = today_midnight - timedelta(seconds=1)
    wait_seconds = (target_time - gst_now).total_seconds()

    if wait_seconds > 0:
        print(f"[{gst_now:%H:%M:%S} GST] Ready on pre-booking screen. Waiting {wait_seconds:.2f}s for 11:59:59 PM GST...", flush=True)
        await asyncio.sleep(wait_seconds)

    print(f"[{synced_now_gst():%H:%M:%S} GST] Midnight release reached! Clicking proceed...", flush=True)


async def main():
    await sync_server_time()
    await sleep_until_pre_booking_time()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigating to Damac Living...", flush=True)
        try:
            await page.goto("https://damacliving.com", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"Navigation failed: {e}", flush=True)
            await browser.close()
            return

        # 2. Login
        try:
            await page.wait_for_selector("#userName", timeout=20000)
            await page.fill("#userName", USERNAME)
            await page.fill("input[type='password']", PASSWORD)
            await page.wait_for_timeout(500)

            login_btn = page.locator("button.button__primary:has-text('Sign in')")
            await login_btn.click()

            await page.wait_for_timeout(5000)
            print("Logged in successfully.", flush=True)
        except Exception as e:
            print(f"Login failed: {e}", flush=True)
            await browser.close()
            return

        # 3. Setup Amenity
        try:
            print("Navigating to Amenity Page...", flush=True)
            await page.goto(DIRECT_AMENITY_URL, wait_until="domcontentloaded", timeout=30000)

            new_booking_btn = page.locator("a.button__primary:has-text('New booking'), a:has-text('New booking')").first
            await new_booking_btn.wait_for(state="visible", timeout=30000)
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

            print("Court selected. Holding at pre-booking screen...", flush=True)

        except Exception as e:
            print(f"Pre-booking setup failed: {e}", flush=True)
            await browser.close()
            return

        # 4. Hold until 11:59:59 PM GST (or instant if TEST_MODE)
        await wait_for_exact_midnight()

        # 5. Rapid Execution
        booking_success = False
        try:
            proceed_btn = page.locator("button:has-text('Proceed to booking')")
            await proceed_btn.wait_for(state="visible", timeout=5000)
            await proceed_btn.click()

            print("Clicked proceed. Waiting directly for date dropdown...", flush=True)
            date_dropdown = page.locator("div[title='Date'] a.dropdown-toggle")
            await date_dropdown.wait_for(state="visible", timeout=10000)
            await date_dropdown.click()

            date_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]:visible")
            await date_items.last.wait_for(state="visible", timeout=10000)

            selected_text = (await date_items.last.text_content()).strip()
            print(f"Selected furthest available date: '{selected_text}'", flush=True)
            await date_items.last.click()

            time_dropdown = page.locator("div[title='Time'] a.dropdown-toggle")
            await time_dropdown.wait_for(state="visible", timeout=10000)
            await time_dropdown.click()

            # Log every visible slot (raw text) so the full list is always in
            # the Actions log, and also build a normalized lookup map so
            # matching doesn't depend on exact punctuation/padding.
            all_slot_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]:visible")
            all_elements = await all_slot_items.all()
            raw_texts = [(await el.text_content()).strip() for el in all_elements]
            print(f"All visible time slots tonight: {raw_texts}", flush=True)

            normalized_to_element = {}
            for el, raw in zip(all_elements, raw_texts):
                norm = normalize_slot_text(raw)
                if norm and norm not in normalized_to_element:
                    normalized_to_element[norm] = el

            selected_slot_label = None
            matched_element = None
            for slot_label in PREFERRED_SLOTS:
                norm_target = normalize_slot_text(slot_label)
                if norm_target in normalized_to_element:
                    selected_slot_label = slot_label
                    matched_element = normalized_to_element[norm_target]
                    print(f"Selecting preferred slot (normalized match): {slot_label}...", flush=True)
                    await matched_element.click()
                    break

            if not selected_slot_label:
                print(f"None of preferred slots {PREFERRED_SLOTS} available. Aborting booking.", flush=True)
                await page.screenshot(path="booking_result_noslots.png")
                await browser.close()
                return

            # Confirm Booking
            book_now_btn = page.locator("button.button__primary[type='submit']:has-text('Book now')")
            await book_now_btn.wait_for(state="visible", timeout=10000)
            await book_now_btn.click()
            print(f"Clicked 'Book now' for {selected_slot_label}. Waiting for confirmation...", flush=True)

            # --- Explicit success/failure detection ---
            # "Submitted" confirmed as real confirmation text from last
            # night's actual successful booking log line:
            #   "CONFIRMED: Submitted Date — slot: ..."
            success_locator = page.locator(
                "text=/booking confirmed|successfully booked|booking successful|submitted|thank you|completed/i"
            )
            error_locator = page.locator(
                "text=/already booked|no longer available|slot unavailable|failed|error/i"
            )

            try:
                await page.wait_for_timeout(2000)  # Let toast/modal render
                if await success_locator.count() > 0:
                    booking_success = True
                    msg = (await success_locator.first.text_content() or "").strip()
                    print(f"CONFIRMED: {msg or 'success message detected'} — slot: {selected_slot_label}", flush=True)
                elif await error_locator.count() > 0:
                    msg = (await error_locator.first.text_content() or "").strip()
                    print(f"FAILED: {msg or 'error message detected'} — slot: {selected_slot_label}", flush=True)
                else:
                    print("UNKNOWN: no explicit success/error text matched. Capturing screenshot for review.", flush=True)
            except Exception as e:
                print(f"Result-detection step errored: {e}", flush=True)

            # Always capture final screenshot as evidence
            try:
                await page.screenshot(path="booking_result_final.png")
            except Exception:
                pass

            await asyncio.sleep(5)

        except Exception as e:
            print(f"Booking attempt failed: {e}", flush=True)

        await browser.close()
        print(f"RUN RESULT: {'SUCCESS' if booking_success else 'UNCONFIRMED/FAILED'}", flush=True)


asyncio.run(main())
