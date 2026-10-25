"""Data must survive a full backend process restart."""
import asyncio
import os
import subprocess
import sys

import httpx
import pytest

from .conftest import REPO, TEST_DB, Api, HOSPITAL, LIBRARY, free_port

pytestmark = pytest.mark.skipif(not TEST_DB.startswith("postgresql"), reason="restart test targets PostgreSQL")


def start_backend(port: int) -> subprocess.Popen:
    env = {**os.environ, "DISPATCH_ENABLED": "true", "PORT": str(port)}
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


async def wait_healthy(base: str, proc: subprocess.Popen) -> None:
    async with httpx.AsyncClient() as c:
        for _ in range(150):
            if proc.poll() is not None:
                raise RuntimeError(proc.stderr.read().decode(errors="replace"))
            try:
                if (await c.get(f"{base}/api/health")).json()["database"] == "ok":
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise TimeoutError("backend did not start")


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


async def test_completed_ride_survives_restart():
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = start_backend(port)
    try:
        await wait_healthy(base, proc)
        async with httpx.AsyncClient(base_url=base, timeout=10) as c:
            api = Api(c)
            api._phone = 9200000000
            driver = await api.driver(at=LIBRARY, name="Restart Driver")
            passenger = await api.passenger("Restart Passenger")
            ride = await api.request_ride(passenger)
            for action in ("accept", "arrived", "start"):
                await api.act(driver, ride["id"], action)
            await api.act(driver, ride["id"], "complete", {"cash_collected": True})
    finally:
        stop(proc)

    proc = start_backend(port)
    try:
        await wait_healthy(base, proc)
        async with httpx.AsyncClient(base_url=base, timeout=10) as c:
            # Tokens issued before the restart remain valid: sessions are stored in the database.
            history = (await c.get("/api/passenger/rides", headers=passenger["h"])).json()
            assert history[0]["id"] == ride["id"] and history[0]["status"] == "COMPLETED"
            assert history[0]["payment"]["status"] == "PAID"
            earnings = (await c.get("/api/driver/earnings", headers=driver["h"])).json()
            assert earnings["all_time"]["trips"] == 1
    finally:
        stop(proc)
