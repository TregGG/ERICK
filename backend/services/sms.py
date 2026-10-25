"""OTP delivery. The console provider is for development only and never prints the code."""
import logging

import httpx

from ..config import settings
from ..security import mask_phone

log = logging.getLogger("erick.sms")


class SmsError(Exception):
    pass


async def send_otp(phone: str, code: str) -> None:
    message = f"{code} is your IIT Roorkee Campus Rick login code. It expires in {settings.OTP_EXPIRY_SECONDS // 60} minutes."
    if settings.SMS_PROVIDER == "console":
        log.info("Development OTP issued for %s (code shown only through OTP_DEV_MODE)", mask_phone(phone))
        return
    if settings.SMS_PROVIDER == "twilio":
        await _send_twilio(phone, message)
        return
    raise SmsError(f"Unknown SMS_PROVIDER {settings.SMS_PROVIDER!r}")


async def _send_twilio(phone: str, message: str) -> None:
    # Not exercised in automated tests: requires real Twilio credentials.
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER):
        raise SmsError("Twilio credentials are not configured")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            url, data={"To": phone, "From": settings.TWILIO_FROM_NUMBER, "Body": message},
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
        )
    if response.status_code >= 300:
        log.error("Twilio rejected OTP SMS for %s: HTTP %s", mask_phone(phone), response.status_code)
        raise SmsError("SMS provider rejected the message")
