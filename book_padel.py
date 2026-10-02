import asyncio
import os
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright
import aiohttp

USERNAME = os.getenv("DAMAC_USERNAME")
PASSWORD = os.getenv("DAMAC_PASSWORD")
IS_TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

AMENITY_ID = "a5Y07000000wqfhEAA"
UNIT_ID = "a0x07000008cDwoAAE"
ACCOUNT_ID = "0010700000gYVSsAAO"
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


async def extract_live_auth_and_slots(target_date):
    print("Sniffing live auth headers and real slot API request via Playwright...", flush=True)
    async with async_playwright() as p:
        # Run non-headless so you can see what it's clicking if needed, or keep headless=True
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        bearer_token = None
        custom_identifier = None
        sniffed_slots_data = {}

        def handle_request(req):
            nonlocal bearer_token, custom_identifier
            for k, v in req.headers.items():
                if k.lower() == "authorization" and "Bearer" in v:
                    bearer_token = v
                if k.lower() == "x-custom-identifier":
                    custom_identifier = v

            if "damacliving.com/damacliving/api/v1/" in req.url:
                print(f"[SNIFFED API] {req.method} | {req.url}", flush=True)
                if req.post_data:
                    print(f"   Payload: {req.post_data}", flush=True)

        async def handle_response(response):
            nonlocal sniffed_slots_data
            if "amenities" in response.url and response.status == 200:
                try:
                    body = await response.json()
                    if isinstance(body, dict) and "data" in body:
                        items = body.get("data", [])
                        if isinstance(items, list) and len(items) > 0:
                            print(f"\n[CAPTURED SLOTS API RESPONSE from {response.url}]", flush=True)
                            for item in items:
                                slot_label = item.get("slot_time") or item.get("name")
                                slot_id = item.get("id") or item.get("amenity_slot_id")
                                is_available = item.get("is_available", True)
                                if slot_label and slot_id and is_available:
                                    sniffed_slots_data[slot_label] = slot_id
                except Exception:
                    pass

        page.on("request", handle_request)
        page.on("response", handle_response)

        # 1. Login
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

        print("Logged in. Navigating to amenities...", flush=True)
        await asyncio.sleep(3)

        # 2. Go to amenities and interact to trigger the exact slot query
        await page.goto("https://www.damacliving.com/amenities", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(5)

        # Try to click on Paddle / Padel court if visible
        try:
            padel_card = page.locator("text=Paddle").first
            if await padel_card.count() > 0:
                print("Found Padel court in UI, clicking...", flush=True)
                await padel_card.click()
                await asyncio.sleep(5)
        except Exception as e:
            print(f"UI interaction note: {e}", flush=True)

        await browser.close()
        return bearer_token, custom_identifier, sniffed_slots_data


async def main():
    await sync_server_time()

    days_out = 2 if IS_TEST_MODE else 14
    target_date = (synced_now_gst() + timedelta(days=days_out)).strftime("%Y-%m-%d")
    print(f"Targeting booking date: {target_date}", flush=True)

    bearer_token, custom_identifier, sniffed_slots = await extracellular_auth if False else await extract_live_auth_and_slots(target_date)

    if sniffed_slots:
        print("\nSuccessfully captured slots via browser interaction:", flush=True)
        for label, sid in sniffed_slots.items():
            print(f" - {label} : {sid}", flush=True)
    else:
        print("\nNo slots captured automatically from browser UI. Let's check sniffed API routes above.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
