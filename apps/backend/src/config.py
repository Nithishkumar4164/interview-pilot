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
    # OpenID provider endpoints. Default to Google; overridable so tests and
    # local end-to-end runs can point at a stand-in provider.
    oidc_issuer: str = "https://accounts.google.com"
    oidc_authorization_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    oidc_token_url: str = "https://oauth2.googleapis.com/token"
    oidc_jwks_url: str = "https://www.googleapis.com/oauth2/v3/certs"
    # Who may sign in (comma-separated). A login is allowed if its verified
    # email is listed or its domain is. Required outside development.
    allowed_login_emails: str = ""
    allowed_login_domains: str = ""
    # Users whose verified email is listed get role=admin (re-evaluated at each login).
    admin_emails: str = ""
    # Lifetime of the per-session token the dashboard hands to the extension.
    pairing_token_expire_hours: int = 6

    # LLM Providers
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Deepgram
    deepgram_api_key: str = ""

    # Mock LLM (used when no provider keys are set): simulated per-call latency,
    # so local runs can exercise slow calls (e.g. crash during report generation).
    mock_llm_latency_ms: int = 0
    # Provider used when no LLM keys are configured (development): "mock" or "scripted".
    dev_llm_provider: str = "mock"

    # Upper bound on parallel LLM calls while generating one interview plan.
    plan_generation_max_concurrency: int = 4
    # Session graph only: the analysis chooses follow-ups (contradiction check, hint draft, code check).
    agentic_routing_enabled: bool = False
    # Reports: score each criterion with evidence tools, then one synthesis (single call if off).
    report_agentic_scoring: bool = False
    # Session graph: the copilot acts as a peer interviewer (suggested probes, claim checks,
    # clarifications, interviewer coaching, pacing, coverage, scorecard, ask-the-copilot).
    # Turns on the follow-up fan-out of agentic routing for its own actions.
    peer_interviewer_enabled: bool = False
    # Price of a Sonnet token relative to an Opus token, for report cost accounting.
    # An assumption (1:5 held for earlier generations), not read from any price list.
    report_cost_sonnet_ratio: float = 0.2

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
        if not self.google_client_id or not self.google_client_secret:
            errors.append(
                "INTERVIEW_PILOT_GOOGLE_CLIENT_ID and INTERVIEW_PILOT_GOOGLE_CLIENT_SECRET must be set in prod."
            )
        if not self.allowed_login_emails.strip() and not self.allowed_login_domains.strip():
            errors.append(
                "Set INTERVIEW_PILOT_ALLOWED_LOGIN_EMAILS and/or INTERVIEW_PILOT_ALLOWED_LOGIN_DOMAINS "
                "in prod; otherwise any Google account could sign in."
            )
        if errors:
            raise RuntimeError("Insecure production config: " + " | ".join(errors))
        return self

    @staticmethod
    def _csv(value: str) -> frozenset[str]:
        return frozenset(v.strip().lower() for v in value.split(",") if v.strip())

    @property
    def allowed_login_email_set(self) -> frozenset[str]:
        return self._csv(self.allowed_login_emails)

    @property
    def allowed_login_domain_set(self) -> frozenset[str]:
        return self._csv(self.allowed_login_domains)

    @property
    def admin_email_set(self) -> frozenset[str]:
        return self._csv(self.admin_emails)

    @property
    def secure_cookies(self) -> bool:
        return self.environment != "development"


settings = Settings()
