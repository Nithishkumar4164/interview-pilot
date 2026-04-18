from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-in-production"  # noqa: S105 — sentinel for dev default detection


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="INTERVIEW_PILOT_", extra="ignore"
    )

    # Database
    database_url: str = "postgresql+asyncpg://copilot:copilot@localhost:5432/interview_pilot"
    database_url_sync: str = "postgresql://copilot:copilot@localhost:5432/interview_pilot"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret_key: str = DEFAULT_JWT_SECRET
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

    # Dev shortcut — bypasses auth and uses a seeded dev user. Must be
    # ``False`` in any non-development environment (see validator below).
    dev_mode: bool = True

    @model_validator(mode="after")
    def _fail_loud_on_insecure_prod(self) -> "Settings":
        """Refuse to boot with insecure placeholders in non-dev environments.

        Checked against ``environment`` rather than the runtime host so a
        single ``INTERVIEW_PILOT_ENVIRONMENT=production`` flip is enough to
        harden the config surface.
        """
        if self.environment == "development":
            return self

        errors: list[str] = []
        if self.jwt_secret_key == DEFAULT_JWT_SECRET or not self.jwt_secret_key:
            errors.append(
                "INTERVIEW_PILOT_JWT_SECRET_KEY must be set to a real secret "
                "when environment != 'development' (generate one with "
                "`openssl rand -hex 32`)."
            )
        if self.dev_mode:
            errors.append(
                "INTERVIEW_PILOT_DEV_MODE=true is only allowed when "
                "environment=development. Set dev_mode=false for prod."
            )
        if not self.anthropic_api_key and not self.openai_api_key:
            errors.append(
                "At least one of INTERVIEW_PILOT_ANTHROPIC_API_KEY or "
                "INTERVIEW_PILOT_OPENAI_API_KEY must be set in prod."
            )
        if not self.deepgram_api_key:
            errors.append("INTERVIEW_PILOT_DEEPGRAM_API_KEY must be set in prod.")
        if errors:
            raise RuntimeError("Insecure production config: " + " | ".join(errors))
        return self


settings = Settings()
