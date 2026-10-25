"""WebSocket authorization and event delivery against a real uvicorn server."""
import asyncio
import json

import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from backend.api import ws as ws_module
from backend.main import app

from .conftest import HOSPITAL, LIBRARY, free_port, now_iso


@pytest.fixture
async def server():
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    task = asyncio.create_task(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.02)
    yield f"ws://127.0.0.1:{port}/ws"
    srv.should_exit = True
    await task


async def open_socket(url, token):
    sock = await connect(url)
    await sock.send(json.dumps({"type": "auth", "token": token}))
    ready = json.loads(await asyncio.wait_for(sock.recv(), 5))
    assert ready["type"] == "ready"
    sync = json.loads(await asyncio.wait_for(sock.recv(), 5))
    assert sync["type"] == "sync"
    return sock, sync["data"]


async def next_event(sock, wanted, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        message = json.loads(await asyncio.wait_for(sock.recv(), max(remaining, 0.01)))
        if message["type"] == wanted:
            return message


async def assert_no_event(sock, unwanted, wait=0.5):
    try:
        while True:
            message = json.loads(await asyncio.wait_for(sock.recv(), wait))
            assert message["type"] != unwanted, message
    except asyncio.TimeoutError:
        pass


async def closed_code(sock, timeout=5.0):
    try:
        while True:
            await asyncio.wait_for(sock.recv(), timeout)
    except ConnectionClosed as exc:
        return exc.rcvd.code if exc.rcvd else None


async def test_unauthenticated_sockets_are_closed(server):
    sock = await connect(server)
    await sock.send(json.dumps({"type": "ping"}))
    assert await closed_code(sock) == 4401
    sock = await connect(server)
    await sock.send(json.dumps({"type": "auth", "token": "not-a-token"}))
    assert await closed_code(sock) == 4401


async def test_live_ride_events_and_location_privacy(server, api):
    driver = await api.driver(at=HOSPITAL, name="Imran Ali")
    passenger = await api.passenger("Kabir Khan")
    bystander = await api.passenger("Priya Nair")
    d_sock, d_sync = await open_socket(server, driver["token"])
    p_sock, _ = await open_socket(server, passenger["token"])
    b_sock, _ = await open_socket(server, bystander["token"])
    assert d_sync["role"] == "driver" and d_sync["offer"] is None

    ride = await api.request_ride(passenger)
    offer = await next_event(d_sock, "offer.new")  # pushed, no polling or refresh
    assert offer["data"]["ride_id"] == ride["id"]

    await api.act(driver, ride["id"], "accept")
    update = await next_event(p_sock, "ride.updated")
    while update["data"]["status"] != "ACCEPTED":
        update = await next_event(p_sock, "ride.updated")
    assert update["data"]["driver"]["name"] == "Imran Ali"
    assert update["data"]["driver"]["vehicle_number"]

    # Driver streams a position over the socket; only this ride's passenger receives it.
    await d_sock.send(json.dumps({"type": "location", "id": 7, "data": {
        "lat": LIBRARY[0], "lng": LIBRARY[1], "accuracy_m": 6, "timestamp": now_iso(), "source": "gps"}}))
    ack = await next_event(d_sock, "location.ack")
    assert ack["id"] == 7 and ack["data"]["accepted"]
    moved = await next_event(p_sock, "driver.location")
    assert moved["data"]["lat"] == LIBRARY[0] and moved["data"]["source"] == "gps" and moved["data"]["recorded_at"]
    await assert_no_event(b_sock, "driver.location")

    # Invalid payloads are rejected, not stored.
    await d_sock.send(json.dumps({"type": "location", "id": 8, "data": {"lat": 200, "lng": 0, "accuracy_m": 5, "timestamp": now_iso()}}))
    rejected = await next_event(d_sock, "location.rejected")
    assert rejected["id"] == 8

    # Passengers cannot inject locations.
    await p_sock.send(json.dumps({"type": "location", "data": {"lat": 1, "lng": 1, "accuracy_m": 1, "timestamp": now_iso()}}))
    assert (await next_event(p_sock, "error"))["data"]["detail"] == "Only drivers share location"

    # A reconnecting client receives the authoritative state in the sync snapshot.
    await p_sock.close()
    p_sock, p_sync = await open_socket(server, passenger["token"])
    assert p_sync["active_ride"]["id"] == ride["id"] and p_sync["active_ride"]["status"] == "ACCEPTED"
    for s in (d_sock, p_sock, b_sock):
        await s.close()


async def test_logout_closes_open_socket(server, api, monkeypatch):
    monkeypatch.setattr(ws_module, "REVALIDATE_EVERY_S", 0.2)
    passenger = await api.passenger()
    sock, _ = await open_socket(server, passenger["token"])
    await api.c.post("/api/auth/logout", headers=passenger["h"])
    assert await closed_code(sock) == 4401


async def test_message_flood_is_cut_off(server, api):
    passenger = await api.passenger()
    sock, _ = await open_socket(server, passenger["token"])
    for _ in range(40):
        await sock.send(json.dumps({"type": "ping"}))
    assert await closed_code(sock) == 4408
