"""Twilio Verify / Messaging integration against a simulated Twilio API (no network, no real SMS)."""
import base64
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import func, select

from backend.config import settings
from backend.db import SessionLocal
from backend.models import OtpChallenge
from backend.services import sms


class FakeTwilio:
    """Implements the parts of the Twilio Verify and Messaging APIs the app uses."""

    def __init__(self):
        self.requests, self.pending, self.messages = [], {}, []
        self.fail_with = None  # (http_status, twilio_error_code) for the next request

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requests.append((request.url.path, form, request.headers.get("authorization")))
        if self.fail_with:
            status, code = self.fail_with
            self.fail_with = None
            return httpx.Response(status, json={"code": code, "message": "simulated", "status": status})
        if request.url.path.endswith("/Verifications"):
            self.pending[form["To"]] = "424242"
            return httpx.Response(201, json={"sid": "VE1", "status": "pending", "to": form["To"], "channel": "sms"})
        if request.url.path.endswith("/VerificationCheck"):
            expected = self.pending.get(form["To"])
            if expected is None:
                return httpx.Response(404, json={"code": 20404, "message": "not found", "status": 404})
            if form["Code"] == expected:
                del self.pending[form["To"]]
                return httpx.Response(200, json={"status": "approved", "valid": True})
            return httpx.Response(200, json={"status": "pending", "valid": False})
        if request.url.path.endswith("/Messages.json"):
            self.messages.append(form)
            return httpx.Response(201, json={"sid": "SM1", "status": "queued"})
        return httpx.Response(404, json={"code": 20404})


@pytest.fixture
def twilio(monkeypatch):
    fake = FakeTwilio()
    monkeypatch.setattr(sms, "_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(settings, "SMS_PROVIDER", "twilio_verify")
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "secret-token")
    monkeypatch.setattr(settings, "TWILIO_VERIFY_SERVICE_SID", "VAtest")
    return fake


async def request_code(client, phone, app="passenger"):
    return await client.post("/api/auth/otp/request", json={"phone": phone, "app": app})


async def verify(client, phone, code, app="passenger"):
    return await client.post("/api/auth/otp/verify", json={"phone": phone, "app": app, "code": code, "name": "Riya"})


async def test_verify_login_end_to_end(api, client, twilio):
    phone = api.next_phone()
    r = await request_code(client, phone)
    assert r.status_code == 200
    assert "dev_otp" not in r.json()  # the code only exists at Twilio, even with OTP_DEV_MODE on
    path, form, auth = twilio.requests[0]
    assert path == "/v2/Services/VAtest/Verifications"
    assert form == {"To": "+91" + phone, "Channel": "sms"}
    assert base64.b64decode(auth.split()[1]).decode() == "ACtest:secret-token"

    r = await verify(client, phone, "424242")
    assert r.status_code == 200, r.text
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["name"] == "Riya"
    assert twilio.requests[-1][0] == "/v2/Services/VAtest/VerificationCheck"


async def test_wrong_codes_use_local_attempt_limit(api, client, twilio):
    phone = api.next_phone()
    await request_code(client, phone)
    for remaining in (4, 3, 2, 1):
        r = await verify(client, phone, "000000")
        assert r.status_code == 400 and f"{remaining} attempt" in r.json()["detail"]
    r = await verify(client, phone, "000000")
    assert "Too many incorrect attempts" in r.json()["detail"]
    assert (await verify(client, phone, "424242")).status_code == 400  # locked even with the right code


async def test_expired_verification(api, client, twilio):
    phone = api.next_phone()
    await request_code(client, phone)
    twilio.pending.clear()  # Twilio expired/deleted the verification
    r = await verify(client, phone, "424242")
    assert r.status_code == 400 and "expired" in r.json()["detail"]


@pytest.mark.parametrize("status, code, expected_status, phrase", [
    (400, 60200, 422, "cannot receive SMS"),
    (400, 21608, 422, "trial account"),
    (429, 60203, 429, "Too many codes"),
    (401, 20003, 503, "could not send"),
])
async def test_twilio_errors_become_clear_messages(api, client, twilio, status, code, expected_status, phrase):
    twilio.fail_with = (status, code)
    phone = api.next_phone()
    r = await request_code(client, phone)
    assert r.status_code == expected_status and phrase in r.json()["detail"]
    async with SessionLocal() as db:  # a failed send must not consume the resend cooldown
        assert (await db.execute(select(func.count(OtpChallenge.id)))).scalar_one() == 0
    assert (await request_code(client, phone)).status_code == 200


async def test_network_failure(api, client, twilio, monkeypatch):
    def boom(request):
        raise httpx.ConnectError("unreachable")
    monkeypatch.setattr(sms, "_transport", httpx.MockTransport(boom))
    r = await request_code(client, api.next_phone())
    assert r.status_code == 503


async def test_plain_twilio_messaging_provider(api, client, twilio, monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "twilio")
    monkeypatch.setattr(settings, "TWILIO_FROM_NUMBER", "+15005550006")
    phone = api.next_phone()
    r = await request_code(client, phone)
    code = r.json()["dev_otp"]  # app-generated code (dev mode in tests)
    sent = twilio.messages[0]
    assert sent["To"] == "+91" + phone and sent["From"] == "+15005550006" and code in sent["Body"]
    assert (await verify(client, phone, code)).status_code == 200


def test_missing_credentials_are_reported(monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "twilio_verify")
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "")
    monkeypatch.setattr(settings, "TWILIO_VERIFY_SERVICE_SID", "")
    assert "TWILIO_VERIFY_SERVICE_SID" in sms.config_problems()[0]
    with pytest.raises(RuntimeError):
        settings.validate_for_runtime()
    monkeypatch.setattr(settings, "SMS_PROVIDER", "carrier-pigeon")
    assert "must be one of" in sms.config_problems()[0]
