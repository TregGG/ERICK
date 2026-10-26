"""End-to-end scenario in two real browser sessions (Playwright + Chromium).

Requires the server to be running (start-erick.cmd) with demo data and OTP_DEV_MODE=true.
GPS is driven through the browser Geolocation API with deterministic fixtures, so the
driver app's real watchPosition code path is exercised.

    .venv313\\Scripts\\python -m pytest e2e -s
"""
import os
from concurrent.futures import ThreadPoolExecutor
import re
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8091")
SHOTS = Path(__file__).parent / "screenshots"
DRIVER_PHONE, PASSENGER_PHONE, PASSENGER2_PHONE, ADMIN_PHONE = "9000000001", "8000000001", "8000000002", "7000000001"
HOSPITAL = {"latitude": 29.861718, "longitude": 77.892923, "accuracy": 8}
LIBRARY = {"latitude": 29.865311, "longitude": 77.89477, "accuracy": 6}
NEAR_LIBRARY = {"latitude": 29.86440, "longitude": 77.89420, "accuracy": 7}
RAJENDRA = {"latitude": 29.87101, "longitude": 77.894076, "accuracy": 6}


def api_login(phone: str, app: str) -> dict:
    with httpx.Client(base_url=BASE, timeout=10) as c:
        for _ in range(40):
            r = c.post("/api/auth/otp/request", json={"phone": phone, "app": app})
            if r.status_code != 429:
                break
            time.sleep(int(r.headers.get("Retry-After", "2")))
        r.raise_for_status()
        v = c.post("/api/auth/otp/verify", json={"phone": phone, "app": app, "code": r.json()["dev_otp"]})
        v.raise_for_status()
        return {"Authorization": f"Bearer {v.json()['access_token']}"}


def reset_demo_state():
    """Cancel leftover active rides (one admin session, so demo accounts are not spammed with codes)."""
    with httpx.Client(base_url=BASE, timeout=10) as c:
        h = api_login(ADMIN_PHONE, "admin")
        for ride in c.get("/api/admin/rides?active_only=true", headers=h).json():
            c.post(f"/api/admin/rides/{ride['id']}/cancel", headers=h, json={"reason": "E2E reset"})


def ui_login(page: Page, phone: str):
    page.fill("#login-phone", phone)
    code = page.locator("[data-dev-code]")
    for _ in range(5):
        page.click("[data-action=send]")
        page.wait_for_function("() => document.querySelector('[data-error]').textContent || !document.querySelector('[data-step=code]').hidden")
        message = page.locator("[data-error]").inner_text()
        wait = re.search(r"wait (\d+) seconds", message)
        if not wait:
            break
        time.sleep(int(wait.group(1)) + 1)  # the account was just used by reset_demo_state: honour the resend cooldown
    expect(code).to_have_text(re.compile(r"^\d{6}$"), timeout=10000)
    page.fill("#login-code", code.inner_text())
    page.click("[data-action=verify]")
    expect(page.locator(".auth-screen")).to_have_count(0, timeout=10000)


def track(page: Page, name: str, problems: list):
    page.on("console", lambda m: m.type == "error" and problems.append(f"[{name} console] {m.text}"))
    page.on("pageerror", lambda e: problems.append(f"[{name} pageerror] {e}"))
    page.on("response", lambda r: r.status >= 500 and problems.append(f"[{name} HTTP {r.status}] {r.url}"))
    # The login code resend cooldown (HTTP 429 on otp/request) is expected and handled by the UI;
    # any other 429 is a problem. Browsers also log failed loads to the console, so those are matched by URL here.
    page.on("response", lambda r: r.status == 429 and not r.url.endswith("/api/auth/otp/request")
            and problems.append(f"[{name} HTTP 429] {r.url}"))


def token_of(page: Page, key: str) -> dict:
    return {"Authorization": f"Bearer {page.evaluate(f'localStorage.getItem({key!r})')}"}


def test_full_passenger_driver_scenario():
    SHOTS.mkdir(exist_ok=True)
    reset_demo_state()
    problems: list = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        driver_ctx = browser.new_context(viewport={"width": 412, "height": 860}, geolocation=HOSPITAL,
                                         permissions=["geolocation"], base_url=BASE, is_mobile=True, has_touch=True)
        passenger_ctx = browser.new_context(viewport={"width": 390, "height": 844}, geolocation=LIBRARY,
                                            permissions=["geolocation"], base_url=BASE, is_mobile=True, has_touch=True)
        driver, passenger = driver_ctx.new_page(), passenger_ctx.new_page()
        track(driver, "driver", problems)
        track(passenger, "passenger", problems)

        # 1-3. Driver signs in, goes on duty; backend sees an eligible GPS location.
        driver.goto("/driver/")
        ui_login(driver, DRIVER_PHONE)
        expect(driver.locator("#duty-btn")).to_be_visible(timeout=10000)
        if "on duty" in driver.locator("#duty-btn").inner_text().lower():  # may already be on duty from a previous run
            driver.click("#duty-btn")
        expect(driver.locator("[data-gps]")).to_contain_text("GPS ±8 m", timeout=15000)
        expect(driver.locator("[data-gps]")).to_contain_text("Shared", timeout=15000)
        state = httpx.get(f"{BASE}/api/driver/state", headers=token_of(driver, "campusrick.driver.token")).json()
        assert state["profile"]["is_online"] and state["location_eligible"]
        assert abs(state["location"]["lat"] - HOSPITAL["latitude"]) < 1e-6 and state["location"]["source"] == "gps"
        driver.screenshot(path=SHOTS / "01-driver-on-duty.png")

        # 4. Passenger signs in and books Library -> Rajendra Bhawan.
        passenger.goto("/passenger/")
        ui_login(passenger, PASSENGER_PHONE)
        passenger.click("[data-field=pickup]")
        passenger.fill("#place-search", "Central Library")
        passenger.click(".search-results button:has-text('Mahatma Gandhi Central Library')")
        passenger.click("[data-field=dropoff]")
        passenger.fill("#place-search", "Rajendra")
        passenger.click(".search-results button:has-text('Rajendra Bhawan')")
        expect(passenger.locator(".trip-summary")).to_be_visible(timeout=15000)
        expect(passenger.locator("text=nearby")).to_be_visible(timeout=20000)
        passenger.screenshot(path=SHOTS / "02-passenger-booking.png")
        passenger.click("#request-btn")
        expect(passenger.get_by_role("heading", name="Finding you a rickshaw")).to_be_visible()

        # 5. Driver receives the offer without reloading.
        expect(driver.locator("text=New ride request")).to_be_visible(timeout=10000)
        expect(driver.locator(".offer")).to_contain_text("Mahatma Gandhi Central Library")
        driver.screenshot(path=SHOTS / "03-driver-offer.png")

        # 6. Accept; passenger sees the assigned driver and vehicle.
        driver.click("#accept-btn")
        expect(driver.get_by_role("heading", name="Head to pickup")).to_be_visible()
        expect(passenger.get_by_role("heading", name="Driver on the way")).to_be_visible(timeout=10000)
        expect(passenger.locator(".driver-card")).to_contain_text("Aarav Singh (Demo)")
        expect(passenger.locator(".plate")).to_have_text("UK 08 ER 1201")

        # 7-8. Driver moves (browser GPS fixture); passenger map and freshness update.
        ride_id = httpx.get(f"{BASE}/api/passenger/state", headers=token_of(passenger, "campusrick.passenger.token")).json()["active_ride"]["id"]
        driver_ctx.set_geolocation(NEAR_LIBRARY)
        def passenger_sees(lat):
            r = httpx.get(f"{BASE}/api/passenger/rides/{ride_id}", headers=token_of(passenger, "campusrick.passenger.token")).json()
            return r.get("driver_location") and abs(r["driver_location"]["lat"] - lat) < 1e-6
        for _ in range(40):
            if passenger_sees(NEAR_LIBRARY["latitude"]):
                break
            time.sleep(0.25)
        assert passenger_sees(NEAR_LIBRARY["latitude"]), "driver position did not reach the passenger's ride"
        expect(passenger.locator("[data-freshness]")).to_contain_text("Updated", timeout=5000)
        expect(passenger.locator("[data-freshness]")).to_contain_text("Live")
        expect(passenger.locator(".vehicle-marker")).to_have_count(1)
        marker_box = passenger.locator(".vehicle-marker").bounding_box()
        driver_ctx.set_geolocation(LIBRARY)
        time.sleep(3.5)  # the driver app sends at most one position every 3 s
        moved_box = passenger.locator(".vehicle-marker").bounding_box()
        assert marker_box != moved_box, "vehicle marker did not move on the passenger map"
        passenger.screenshot(path=SHOTS / "04-passenger-tracking.png")

        # 9. Arrive, start, complete.
        driver.click("#step-btn")  # I have arrived
        expect(passenger.get_by_role("heading", name="Your rickshaw is here")).to_be_visible(timeout=10000)
        driver.click("#step-btn")  # start trip
        expect(passenger.get_by_role("heading", name="On your way")).to_be_visible(timeout=10000)
        driver_ctx.set_geolocation(RAJENDRA)
        time.sleep(3.5)
        driver.click("#step-btn")  # complete
        driver.click("text=Cash received — complete")
        # 10. Both sides show the final state.
        expect(driver.get_by_role("heading", name="Trip completed")).to_be_visible(timeout=10000)
        expect(passenger.get_by_role("heading", name="You have arrived")).to_be_visible(timeout=10000)
        expect(passenger.locator(".trip-summary")).to_contain_text("Paid in cash")
        passenger.click("[data-star='5']")
        passenger.click("#rate-btn")
        expect(passenger.locator("text=You rated this trip")).to_be_visible()
        passenger.screenshot(path=SHOTS / "05-passenger-completed.png")
        driver.screenshot(path=SHOTS / "06-driver-completed.png")

        # 11. History in the UI and in the API.
        passenger.click("#done-btn")
        passenger.click("#menu-btn")
        passenger.click("[data-go=history]")
        expect(passenger.locator(".drawer .list-item").first).to_contain_text("Completed")
        history = httpx.get(f"{BASE}/api/passenger/rides/{ride_id}", headers=token_of(passenger, "campusrick.passenger.token")).json()
        assert [t["to"] for t in history["timeline"]] == ["REQUESTED", "OFFERED", "ACCEPTED", "ARRIVED", "IN_PROGRESS", "COMPLETED"]
        assert history["payment"]["status"] == "PAID" and history["my_rating"] == 5

        # 13a. Cancellation while the driver holds the offer: the offer disappears from the driver's screen.
        driver.click("#finish-btn")
        p2 = api_login(PASSENGER2_PHONE, "passenger")
        r = httpx.post(f"{BASE}/api/passenger/rides", headers=p2, json={
            "pickup": {"lat": RAJENDRA["latitude"], "lng": RAJENDRA["longitude"], "name": "Rajendra Bhawan"},
            "dropoff": {"lat": LIBRARY["latitude"], "lng": LIBRARY["longitude"], "name": "Library"}}).json()
        expect(driver.locator("text=New ride request")).to_be_visible(timeout=10000)
        httpx.post(f"{BASE}/api/passenger/rides/{r['id']}/cancel", headers=p2, json={"reason": "E2E cancellation"})
        expect(driver.locator("text=New ride request")).to_have_count(0, timeout=10000)
        expect(driver.locator("text=Waiting for ride requests")).to_be_visible()

        # 13b. Competing acceptance: a second driver tries to take the offered ride; the offered driver double-taps.
        # Put driver 1 at the pickup so dispatch (nearest first) deterministically offers it to them.
        driver_ctx.set_geolocation(LIBRARY)
        time.sleep(3.5)
        d2 = api_login("9000000002", "driver")
        httpx.post(f"{BASE}/api/driver/online", headers=d2, json={"online": True})
        httpx.post(f"{BASE}/api/driver/location", headers=d2, json={
            "lat": CENTURY_GATE[0], "lng": CENTURY_GATE[1], "accuracy_m": 10, "timestamp": now_iso()})
        r = httpx.post(f"{BASE}/api/passenger/rides", headers=p2, json={
            "pickup": {"lat": LIBRARY["latitude"], "lng": LIBRARY["longitude"], "name": "Library"},
            "dropoff": {"lat": RAJENDRA["latitude"], "lng": RAJENDRA["longitude"], "name": "Rajendra Bhawan"}}).json()
        expect(driver.locator("text=New ride request")).to_be_visible(timeout=10000)  # nearest driver gets it
        steal = httpx.post(f"{BASE}/api/driver/rides/{r['id']}/accept", headers=d2)
        assert steal.status_code == 409, steal.text
        d1 = token_of(driver, "campusrick.driver.token")

        # Five simultaneous accept requests (threads: Playwright's sync API already owns an event loop).
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: httpx.post(f"{BASE}/api/driver/rides/{r['id']}/accept", headers=d1), range(5)))
        assert [x.status_code for x in results].count(200) >= 1 and all(x.status_code in (200, 409) for x in results)
        final = httpx.get(f"{BASE}/api/passenger/rides/{r['id']}", headers=p2).json()
        assert final["status"] == "ACCEPTED" and final["driver"]["name"] == "Aarav Singh (Demo)"
        assert [t["to"] for t in final["timeline"]].count("ACCEPTED") == 1
        expect(driver.get_by_role("heading", name="Head to pickup")).to_be_visible(timeout=10000)
        httpx.post(f"{BASE}/api/passenger/rides/{r['id']}/cancel", headers=p2, json={"reason": "E2E cleanup"})
        httpx.post(f"{BASE}/api/driver/online", headers=d2, json={"online": False})

        # Admin console renders live data.
        admin = browser.new_context(viewport={"width": 1280, "height": 900}, base_url=BASE).new_page()
        track(admin, "admin", problems)
        admin.goto("/admin/")
        ui_login(admin, ADMIN_PHONE)
        expect(admin.get_by_role("heading", name="Live operations")).to_be_visible()
        admin.click("[data-tab=rides]")
        expect(admin.locator("tbody tr").first).to_be_visible()
        admin.click("[data-tab=reports]")
        expect(admin.get_by_role("heading", name="Daily demand")).to_be_visible()
        admin.screenshot(path=SHOTS / "07-admin-reports.png")
        browser.close()

    # 14. No client errors or server failures. Map tiles come from openstreetmap.org and are
    # excluded only when this machine has no internet access.
    relevant = [p for p in problems if "tile.openstreetmap.org" not in p
                and "status of 429 (Too Many Requests)" not in p]  # 429s are checked by URL in track()
    assert not relevant, "\n".join(relevant)


CENTURY_GATE = (29.868686, 77.890187)


def now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
