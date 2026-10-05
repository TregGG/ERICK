from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://localhost/erickshaw"
    REDIS_URL: str = "redis://localhost:6379"
    GOOGLE_MAPS_API_KEY: str = ""
    JWT_SECRET: str = "super_secret_default_key_change_me_in_prod"
    JWT_EXPIRY_HOURS: int = 24
    OTP_EXPIRY_SECONDS: int = 300
    DEFAULT_MAX_DETOUR_METERS: int = 500
    CAMPUS_CENTER_LAT: float = 0.0
    CAMPUS_CENTER_LNG: float = 0.0
    CAMPUS_RADIUS_METERS: int = 2000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
