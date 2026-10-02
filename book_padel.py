import asyncio
import os
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright
import aiohttp

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")
IS_TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

# Identifiers
AMENITY_ID = "a5Y07000000wqfhEAA"      # D2 Sports Town - Paddle Court 1
UNIT_ID = "a0x07000008cDwoAAE"          # Booking Unit ID
ACCOUNT_ID = "0010700000gYVSsAAO"       # Account ID

REGISTRATION_ENDPOINT = "https://api.damacliving.com/damacliving/api/v1/amenities/registration"
SLOTS_ENDPOINT_TEMPLATE = "https://api.damacliving.com/damacliving/api/v1/amenities/{booking_date}?account_id=" + ACCOUNT_ID + "&amenity_id=" + AMENITY_ID + "&booking_unit_id=" + UNIT_ID

STATIC_API_TOKEN = "a740e9a60b62418ee08d65d53740c3346eef6edf994a0784f0def9ca13822a9b"

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


async def sleep_until_midnight():
    if IS_TEST_MODE:
        print("[TEST MODE] Skipping midnight sleep delay. Running immediately...", flush=True)
        return

    gst_now = synced_now_gst()

    if gst_now.hour >= 23:
        target_midnight = (gst_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        target_midnight = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)

    wait_seconds = (target_midnight - gst_now).total_seconds()
    if wait_seconds > 0:
        print(f"[{gst_now:%H:%M:%S} GST] Waiting {wait_seconds:.2f}s until 00:00:00 GST...", flush=True)
        await asyncio.sleep(wait_seconds)


async def extract_live_auth():
    print("Pre-fetching live auth headers via Playwright...", flush=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        bearer_token = None
        custom_identifier = None

        def handle_request(req):
            nonlocal bearer_token, custom_identifier
            for k, v in req.headers.items():
                if k.lower() == "authorization" and "Bearer" in v:
                    bearer_token = v
                if k.lower() == "x-custom-identifier":
                    custom_identifier = v

        page.on("request", handle_request)

        await page.goto("https://damacliving.com", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector("#userName", timeout=20000)
        await page.fill("#userName", USERNAME)
        await page.fill("input[type='password']", PASSWORD)

        try:
            await asyncio.gather(
                page.wait_for_load_state("networkidle", timeout=20000),
                page.click("button.button__primary:has-text('Sign in')")
            )
        except Exception:
            await asyncio.sleep(5)

        await asyncio.sleep(2)
        await browser.close()

        if bearer_token:
            print("Auth headers extracted successfully.", flush=True)
        else:
            print("CRITICAL: Failed to retrieve Bearer token.", flush=True)

        return bearer_token, custom_identifier


async def fetch_available_slots(session, headers, target_date):
    url = SLOTS_ENDPOINT_TEMPLATE.format(booking_date=target_date)
    async with session.get(url, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            slots = {}
            raw_items = data.get("data", [])
            print(f"[API] Retrieved {len(raw_items)} slot items from API.", flush=True)
            if not raw_items:
                print(f"[API] Raw response body (empty items list): {data}", flush=True)
            for item in raw_items:
                slot_label = item.get("slot_time") or item.get("name")
                slot_id = item.get("id") or item.get("amenity_slot_id")
                is_available = item.get("is_available", True)

                if slot_label and slot_id:
                    print(f" - Found Slot: '{slot_label}' | ID: {slot_id} | Available: {is_available}", flush=True)
                    if is_available:
                        slots[slot_label] = slot_id
                else:
                    print(f" - UNRECOGNIZED slot item shape, skipping: {item}", flush=True)
            return slots
        else:
            body = await resp.text()
            print(f"[API] Non-200 response fetching slots: status={resp.status}, body={body}", flush=True)
    return {}


async def fire_booking(session, headers, target_date, slot_id, label):
    payload = {
        "account_id": ACCOUNT_ID,
        "amenity_id": AMENITY_ID,
        "amenity_slot_id": slot_id,
        "booking_date": target_date,
        "booking_unit_id": UNIT_ID,
        "comments": "",
        "fm_case_id": "",
        "no_of_guest": None,
        "origin": "Portal",
    }

    print(f"[{synced_now_gst():%H:%M:%S.%f}] DISPATCHING API BOOKING for {label} (Slot ID: {slot_id})...", flush=True)
    try:
        async with session.post(REGISTRATION_ENDPOINT, json=payload, headers=headers, timeout=5) as resp:
            text = await resp.text()
            print(f"[{synced_now_gst():%H:%M:%S.%f}] SERVER RESPONSE ({label}) [{resp.status}]: {text}", flush=True)
            return resp.status, text
    except asyncio.TimeoutError:
        print(f"[{synced_now_gst():%H:%M:%S.%f}] TIMEOUT waiting for response on {label} -- status UNKNOWN, check manually before retrying.", flush=True)
        return "TIMEOUT", "request timed out, booking status unknown"
    except Exception as e:
        print(f"Failed to submit booking for {label}: {e}", flush=True)
        return None, str(e)


async def book_with_fallback(session, headers, target_date, slots_map):
    available_preferred = [
        (label, slots_map[label]) for label in PREFERRED_SLOTS if label in slots_map
    ]

    if not available_preferred:
        print(f"No preferred target slots available for {target_date}.", flush=True)
        return

    for label, slot_id in available_preferred:
        status, response = await fire_booking(session, headers, target_date, slot_id, label)

        if status in (200, 201):
            print(f"SUCCESS: Successfully booked {label} for {target_date}!", flush=True)
            return

        if status == "TIMEOUT":
            print(f"Stopping after timeout on {label}. Verify manually whether this booking actually went through before the next run.", flush=True)
            return

        print(f"BOOKING FAILED for {label} (status: {status}). Trying next preferred slot if any remain...", flush=True)

    print("Exhausted all available preferred slots -- none succeeded.", flush=True)


async def main():
    await sync_server_time()

    bearer_token, custom_identifier = await extract_live_auth()
    if not bearer_token:
        return

    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en",
        "api-token": STATIC_API_TOKEN,
        "authorization": bearer_token,
        "content-type": "application/json",
        "origin": "https://www.damacliving.com",
        "referer": "https://www.damacliving.com/",
        "user-agent": "Mozilla/5.0 (Linux; Android 16; Pixel 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Mobile Safari/537.36",
        "x-custom-identifier": custom_identifier or "f92ce3684667d4627201f81479ed7162",
    }

    await sleep_until_midnight()

    days_out = 2 if IS_TEST_MODE else 14
    target_date = (synced_now_gst() + timedelta(days=days_out)).strftime("%Y-%m-%d")

    print(f"Targeting booking date: {target_date}", flush=True)

    async with aiohttp.ClientSession() as session:
        slots_map = {}
        for attempt in range(20):
            slots_map = await fetch_available_slots(session, headers, target_date)
            if slots_map:
                print(f"[{synced_now_gst():%H:%M:%S.%f}] Successfully fetched slots on attempt {attempt + 1}.", flush=True)
                break
            await asyncio.sleep(0.15)

        await book_with_fallback(session, headers, target_date, slots_map)


if __name__ == "__main__":
    asyncio.run(main())
