"""Application settings loaded from environment variables and backend/.env.

Every secret and environment-specific value lives here. `validate_for_runtime`
refuses to start a production process with development-only shortcuts enabled.
"""
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent
REPO_DIR = BACKEND_DIR.parent
ENV_PATH = BACKEND_DIR / ".env"

_INSECURE_SECRETS = {"", "change_me", "super_secret_key_change_me", "super_secret_default_key_change_me_in_prod"}


class Settings(BaseSettings):
    APP_ENV: str = "development"  # development | test | production
    HOST: str = "127.0.0.1"
    PORT: int = 8091

    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:55432/erickshaw"

    # Authentication
    JWT_SECRET: str = "change_me"
    JWT_EXPIRY_HOURS: int = 24
    OTP_EXPIRY_SECONDS: int = 300
    OTP_RESEND_COOLDOWN_SECONDS: int = 30
    OTP_MAX_ATTEMPTS: int = 5
    OTP_MAX_PER_HOUR: int = 6
    OTP_REQUESTS_PER_IP_PER_10_MIN: int = 10
    # Development-only: return the OTP in the API response so it can be shown on screen.
    OTP_DEV_MODE: bool = False
    SMS_PROVIDER: str = "console"  # console | twilio
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""

    CORS_ORIGINS: List[str] = Field(default_factory=list)

    # Routing (OSRM-compatible HTTP API). Empty disables network routing; estimates are then labelled approximate.
    ROUTING_BASE_URL: str = "https://router.project-osrm.org"
    ROUTING_PROFILE: str = "driving"
    ROUTING_TIMEOUT_SECONDS: float = 4.0
    ROUTING_MIN_INTERVAL_SECONDS: float = 1.0
    # Travel times are derived from road distance at this average e-rickshaw speed.
    ERICKSHAW_AVG_SPEED_KMH: float = 15.0

    # Location validation
    LOCATION_MAX_ACCURACY_METERS: float = 100.0
    LOCATION_MAX_AGE_SECONDS: int = 120
    LOCATION_MAX_FUTURE_SKEW_SECONDS: int = 30
    LOCATION_MIN_INTERVAL_SECONDS: float = 1.0
    GEOFENCE_BUFFER_METERS: float = 150.0
    # Allows the clearly labelled driver location simulator. Never enable in production.
    SIMULATION_ENABLED: bool = False

    DISPATCH_TICK_SECONDS: float = 1.0
    DISPATCH_ENABLED: bool = True

    model_config = SettingsConfigDict(env_file=(str(ENV_PATH),), env_file_encoding="utf-8", extra="ignore")

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    def validate_for_runtime(self) -> None:
        problems = []
        if self.is_production:
            if self.JWT_SECRET in _INSECURE_SECRETS or len(self.JWT_SECRET) < 32:
                problems.append("JWT_SECRET must be a random value of at least 32 characters")
            if self.OTP_DEV_MODE:
                problems.append("OTP_DEV_MODE must be false")
            if self.SIMULATION_ENABLED:
                problems.append("SIMULATION_ENABLED must be false")
            if self.SMS_PROVIDER == "console":
                problems.append("SMS_PROVIDER must be a real SMS provider")
            if "*" in self.CORS_ORIGINS:
                problems.append("CORS_ORIGINS must list explicit origins")
            if self.is_sqlite:
                problems.append("DATABASE_URL must point to PostgreSQL/PostGIS")
        elif self.JWT_SECRET in _INSECURE_SECRETS and self.APP_ENV != "test":
            print("WARNING: JWT_SECRET is a placeholder. Set a random value in backend/.env.")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


settings = Settings()
