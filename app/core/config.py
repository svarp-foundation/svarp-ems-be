from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    # App
    PROJECT_NAME: str = "Examination Portal"
    API_V1_STR: str = "/api/v1"
    ALLOWED_ORIGINS: List[str] = ["http://localhost:5173"]

    # Database
    DATABASE_URL: str

    # Portal User Integration
    PORTAL_USER_BASE_URL: str
    PORTAL_USER_API_KEY: str
    PORTAL_USER_API_SECRET: str

    # Session Config
    HEARTBEAT_TIMEOUT_SECONDS: int = 60
    SESSION_EXPIRY_MINUTES: int = 1440

    # Session Bypass Config
    BYPASS_SINGLE_SESSION: bool = False
    SESSION_BYPASS_KEY: str = "exam_session_bypass_key"
    AUTO_REVOKE_EXISTING_SESSIONS: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
