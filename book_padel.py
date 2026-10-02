import asyncio
import os
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright
import aiohttp

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")
IS_TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

DIRECT_AMENITY_URL = "https://www.damacliving.com/requests/residency/book-amenity?unit_id=a0x07000008cDwoAAE"

# Your production preferred slots
PREFERRED_SLOTS = [
    ("09:00 PM", "8:00 PM - 9:00 PM"),
    ("08:00 PM", "7:00 PM - 8:00 PM"),
]

CLOCK_OFFSET = timedelta(0)  # Populated by sync_server_time()


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
                    print(f"[SYNC] Server-local clock offset: {CLOCK_OFFSET.total_seconds():.2f}s", flush=True)
                else:
                    print("[SYNC] No Date header returned, proceeding with local clock (offset=0).", flush=True)
    except Exception as e:
        print(f"[SYNC] Time sync failed ({e}), proceeding with local clock (offset=0).", flush=True)


def synced_now_gst():
    return datetime.now(timezone.utc) + CLOCK_OFFSET + timedelta(hours=4)


async def sleep_until_pre_booking_time():
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
    
    if IS_TEST_MODE:
        print("[TEST MODE] Bypassing pre-booking sleep timer.", flush=True)
    else:
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

        # 4. Hold until midnight (or bypass if test mode)
        if IS_TEST_MODE:
            print("[TEST MODE] Bypassing midnight hold timer. Proceeding instantly...", flush=True)
        else:
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

            # Log every visible slot so we see the full list in GitHub Actions
            all_slot_items = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]:visible")
            all_slots_text = [(await el.text_content()).strip() for el in await all_slot_items.all()]
            print(f"All visible time slots tonight: {all_slots_text}", flush=True)

            selected_slot_label = None
            for slot_code, slot_label in PREFERRED_SLOTS:
                slot_locator = page.locator(f"a.dropdown-item[data-rr-ui-dropdown-item]:visible:has-text('{slot_code}')")
                if await slot_locator.count() > 0:
                    selected_slot_label = slot_label
                    print(f"Selecting preferred slot: {slot_code} ({slot_label})...", flush=True)
                    await slot_locator.first.click()
                    break

            # Fallback for testing: if preferred slots are taken, grab the first available slot!
            if not selected_slot_label and IS_TEST_MODE:
                print("Preferred slots not available. [TEST MODE] Selecting the first available open slot...", flush=True)
                first_slot = page.locator("a.dropdown-item[data-rr-ui-dropdown-item]:visible").first
                if await first_slot.count() > 0:
                    selected_slot_label = (await first_slot.text_content()).strip()
                    print(f"Selecting fallback test slot: '{selected_slot_label}'", flush=True)
                    await first_slot.click()

            if not selected_slot_label:
                print("No time slots available to book. Aborting.", flush=True)
                await page.screenshot(path="booking_result_noslots.png")
                await browser.close()
                return

            # Confirm Booking
            book_now_btn = page.locator("button.button__primary[type='submit']:has-text('Book now')")
            await book_now_btn.wait_for(state="visible", timeout=10000)
            await book_now_btn.click()
            print(f"Clicked 'Book now' for {selected_slot_label}. Waiting for confirmation...", flush=True)

            # --- Explicit success/failure detection ---
            success_locator = page.locator(
                "text=/booking confirmed|successfully booked|booking successful/i"
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
