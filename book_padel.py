import asyncio
import os
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright
import aiohttp

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")

# Identifiers
AMENITY_ID = "a5Y07000000wqfhEAA"      # D2 Sports Town - Paddle Court 1
UNIT_ID = "a0x07000008cDwoAAE"         # Corrected Booking Unit ID
ACCOUNT_ID = "0010700000gYVSsAAO"       # Account ID

REGISTRATION_ENDPOINT = "https://api.damacliving.com/damacliving/api/v1/amenities/registration"
SLOTS_ENDPOINT_TEMPLATE = f"https://api.damacliving.com/damacliving/api/v1/amenities/{{booking_date}}?account-id={ACCOUNT_ID}&amentiy-id={AMENITY_ID}&bookingUnit-id={UNIT_ID}"
STATIC_API_TOKEN = "a740e9a60b62418ee08d65d53740c3346eef6edf994a0784f0def9ca13822a9b"

PREFERRED_SLOTS = ["08:00 PM - 09:00 PM", "07:00 PM - 08:00 PM"]

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
    gst_now = synced_now_gst()
    today_midnight = gst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight = today_midnight + timedelta(days=1)

    # If already past midnight (within 15 mins), start immediately
    if gst_now < today_midnight + timedelta(minutes=15):
        return

    wait_seconds = (next_midnight - gst_now).total_seconds()
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
            auth = req.headers.get("authorization")
            cid = req.headers.get("x-custom-identifier")
            if auth and "Bearer" in auth:
                bearer_token = auth
            if cid:
                custom_identifier = cid

        page.on("request", handle_request)

        await page.goto("https://damacliving.com", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector("#userName", timeout=20000)
        await page.fill("#userName", USERNAME)
        await page.fill("input[type='password']", PASSWORD)
        await page.click("button.button__primary:has-text('Sign in')")
        await page.wait_for_timeout(4000)

        await browser.close()
        print("Auth headers extracted successfully.", flush=True)
        return bearer_token, custom_identifier


async def fetch_available_slots(session, headers, target_date):
    url = SLOTS_ENDPOINT_TEMPLATE.format(booking_date=target_date)
    async with session.get(url, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            slots = {}
            for item in data.get("data", []):
                slot_label = item.get("slot_time") or item.get("name")
                slot_id = item.get("id") or item.get("amenity_slot_id")
                if slot_label and slot_id:
                    slots[slot_label] = slot_id
            return slots
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
    except Exception as e:
        print(f"Failed to submit booking for {label}: {e}", flush=True)
        return None, str(e)


async def main():
    await sync_server_time()

    bearer_token, custom_identifier = await extract_live_auth()
    if not bearer_token:
        print("CRITICAL: Failed to retrieve Bearer token.", flush=True)
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

    # Wait until exact midnight GST when slots unlock
    await sleep_until_midnight()

    # Target date is 14 days out from today
    target_date = (synced_now_gst() + timedelta(days=14)).strftime("%Y-%m-%d")

    async with aiohttp.ClientSession() as session:
        # Retry fetching slots for up to 5 seconds right at midnight
        slots_map = {}
        for attempt in range(10):
            slots_map = await fetch_available_slots(session, headers, target_date)
            if slots_map:
                print(f"[{synced_now_gst():%H:%M:%S.%f}] Successfully fetched slots on attempt {attempt+1}.", flush=True)
                break
            await asyncio.sleep(0.5)

        # Dispatch bookings concurrently for preferred slots
        tasks = []
        for slot_label in PREFERRED_SLOTS:
            slot_id = slots_map.get(slot_label)
            if slot_id:
                tasks.append(fire_booking(session, headers, target_date, slot_id, slot_label))

        if tasks:
            await asyncio.gather(*tasks)
        else:
            print(f"No matching target slots found for {target_date}.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
