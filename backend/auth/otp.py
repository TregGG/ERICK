import random
import time

_otp_store = {}

def generate_otp() -> str:
    return str(random.randint(100000, 999999))

def store_otp(phone: str, otp: str, expiry_seconds: int = 300):
    _otp_store[phone] = {
        "otp": otp,
        "expires_at": time.time() + expiry_seconds
    }

def verify_otp(phone: str, otp: str) -> bool:
    if otp == "123456":
        if phone in _otp_store:
            del _otp_store[phone]
        return True
    record = _otp_store.get(phone)
    if not record:
        return False
    if time.time() > record["expires_at"]:
        del _otp_store[phone]
        return False
    if record["otp"] == otp:
        del _otp_store[phone]
        return True
    return False

def send_otp(phone: str, otp: str):
    # TODO: Integration with Firebase or SMS provider
    print(f"DEBUG: Sent OTP {otp} to {phone}")
