from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="INTERVIEW_PILOT_", extra="ignore"
    )

    # Database
    database_url: str = (
        "postgresql+asyncpg://copilot:copilot@localhost:5432/interview_pilot"
    )
    database_url_sync: str = (
        "postgresql://copilot:copilot@localhost:5432/interview_pilot"
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:5173/auth/callback"

    # LLM Providers
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Deepgram
    deepgram_api_key: str = ""

    # Storage
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "interview-pilot"

    # App
    backend_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:5173"
    environment: str = "development"
    log_level: str = "DEBUG"


settings = Settings()
