"""OTP delivery providers.

console        development only: no SMS is sent and the code is never logged
               (it is returned to the app only when OTP_DEV_MODE=true).
twilio         we generate the code and send it as a plain SMS through Twilio Messaging.
               Sending to Indian numbers this way requires DLT registration of the
               sender ID and message template.
twilio_verify  Twilio Verify generates, delivers and checks the code. Recommended for
               India: Verify uses Twilio's pre-registered templates, so no DLT
               registration of our own is needed.
"""
import logging
from typing import Optional

import httpx

from ..config import settings
from ..security import mask_phone

log = logging.getLogger("erick.sms")

PROVIDERS = ("console", "twilio", "twilio_verify")
TWILIO_API = "https://api.twilio.com/2010-04-01"
VERIFY_API = "https://verify.twilio.com/v2"

# Tests replace this with an httpx.MockTransport; None means real network access.
_transport: Optional[httpx.AsyncBaseTransport] = None


class SmsError(Exception):
    """A delivery or verification failure with a message that is safe to show the user."""

    def __init__(self, message: str, status: int = 503, kind: str = "unavailable"):
        super().__init__(message)
        self.message, self.status, self.kind = message, status, kind


def uses_provider_codes() -> bool:
    """True when the SMS provider generates and checks the code itself."""
    return settings.SMS_PROVIDER == "twilio_verify"


def config_problems() -> list:
    provider = settings.SMS_PROVIDER
    if provider not in PROVIDERS:
        return [f"SMS_PROVIDER must be one of {', '.join(PROVIDERS)}"]
    missing = []
    if provider in ("twilio", "twilio_verify"):
        missing += [k for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN") if not getattr(settings, k)]
    if provider == "twilio" and not settings.TWILIO_FROM_NUMBER:
        missing.append("TWILIO_FROM_NUMBER")
    if provider == "twilio_verify" and not settings.TWILIO_VERIFY_SERVICE_SID:
        missing.append("TWILIO_VERIFY_SERVICE_SID")
    return [f"SMS_PROVIDER={provider} needs {', '.join(missing)}"] if missing else []


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10, transport=_transport,
                             auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))


def _error(response: httpx.Response, phone: str, action: str) -> SmsError:
    """Translate Twilio error codes (https://www.twilio.com/docs/api/errors) into user-facing errors."""
    try:
        code = response.json().get("code")
    except ValueError:
        code = None
    log.warning("Twilio %s failed for %s: HTTP %s, Twilio code %s", action, mask_phone(phone), response.status_code, code)
    if code in (60200, 21211, 21614):
        return SmsError("This mobile number cannot receive SMS. Check the number and try again.", 422, "invalid_number")
    if code == 60205:
        return SmsError("SMS cannot be sent to a landline. Use a mobile number.", 422, "invalid_number")
    if code in (21608, 21219):
        return SmsError("This number is not verified on the Twilio trial account. Verify it in the Twilio console "
                        "or upgrade the account.", 422, "trial_unverified")
    if code in (60203, 60202, 20429, 60410):
        return SmsError("Too many codes or attempts for this number. Please wait and try again later.", 429, "provider_limit")
    if response.status_code in (401, 403) or code == 20003:
        log.error("Twilio rejected the credentials; check TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN")
    return SmsError("We could not send the code right now. Please try again shortly.", 503, "unavailable")


async def send_otp(phone: str, code: str) -> None:
    """Deliver a code we generated (console / twilio providers)."""
    if settings.SMS_PROVIDER == "console":
        log.info("Development OTP issued for %s (code shown only through OTP_DEV_MODE)", mask_phone(phone))
        return
    if settings.SMS_PROVIDER != "twilio":
        raise SmsError(f"SMS_PROVIDER {settings.SMS_PROVIDER!r} does not send app-generated codes")
    body = f"{code} is your IIT Roorkee Campus Rick login code. It expires in {settings.OTP_EXPIRY_SECONDS // 60} minutes."
    try:
        async with _client() as client:
            response = await client.post(f"{TWILIO_API}/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json",
                                         data={"To": phone, "From": settings.TWILIO_FROM_NUMBER, "Body": body})
    except httpx.HTTPError as exc:
        log.warning("Twilio unreachable for %s: %s", mask_phone(phone), type(exc).__name__)
        raise SmsError("We could not send the code right now. Please try again shortly.")
    if response.status_code >= 300:
        raise _error(response, phone, "message send")


async def start_verification(phone: str) -> None:
    """Ask Twilio Verify to generate and send a code."""
    try:
        async with _client() as client:
            response = await client.post(f"{VERIFY_API}/Services/{settings.TWILIO_VERIFY_SERVICE_SID}/Verifications",
                                         data={"To": phone, "Channel": "sms"})
    except httpx.HTTPError as exc:
        log.warning("Twilio Verify unreachable for %s: %s", mask_phone(phone), type(exc).__name__)
        raise SmsError("We could not send the code right now. Please try again shortly.")
    if response.status_code >= 300:
        raise _error(response, phone, "verification start")
    log.info("Twilio Verify code sent to %s", mask_phone(phone))


async def check_verification(phone: str, code: str) -> bool:
    """True when Twilio Verify approves the code. Raises SmsError(kind='expired') when no code is pending."""
    try:
        async with _client() as client:
            response = await client.post(f"{VERIFY_API}/Services/{settings.TWILIO_VERIFY_SERVICE_SID}/VerificationCheck",
                                         data={"To": phone, "Code": code})
    except httpx.HTTPError as exc:
        log.warning("Twilio Verify unreachable for %s: %s", mask_phone(phone), type(exc).__name__)
        raise SmsError("We could not check the code right now. Please try again shortly.")
    if response.status_code == 404:  # Twilio deletes verifications once approved, expired or out of attempts
        raise SmsError("This code has expired. Request a new one.", 400, "expired")
    if response.status_code >= 300:
        raise _error(response, phone, "verification check")
    return response.json().get("status") == "approved"
