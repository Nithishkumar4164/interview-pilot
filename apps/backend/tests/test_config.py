"""Regression tests for the Settings post-init validator.

Verifies the fix for `B2 — JWT default secret shouldn't fall back silently
in prod` and the companion `B3 — DEV_MODE flag` validator.
"""

import pytest

from src.config import DEFAULT_JWT_SECRET, Settings


def _kwargs(**overrides):
    base = {
        "environment": "production",
        "jwt_secret_key": "strong-secret-abc123",
        "anthropic_api_key": "real-anthropic-key",
        "openai_api_key": "",
        "deepgram_api_key": "real-deepgram-key",
        "dev_mode": False,
    }
    base.update(overrides)
    return base


class TestProductionConfigValidation:
    def test_default_prod_config_is_valid_when_all_secrets_supplied(self):
        Settings(**_kwargs())  # does not raise

    def test_development_mode_tolerates_defaults(self):
        Settings(
            environment="development",
            jwt_secret_key=DEFAULT_JWT_SECRET,
            dev_mode=True,
            anthropic_api_key="",
            openai_api_key="",
            deepgram_api_key="",
        )

    def test_prod_rejects_default_jwt_secret(self):
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            Settings(**_kwargs(jwt_secret_key=DEFAULT_JWT_SECRET))

    def test_prod_rejects_empty_jwt_secret(self):
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            Settings(**_kwargs(jwt_secret_key=""))

    def test_prod_rejects_dev_mode_true(self):
        with pytest.raises(RuntimeError, match="DEV_MODE"):
            Settings(**_kwargs(dev_mode=True))

    def test_prod_rejects_missing_llm_keys(self):
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            Settings(**_kwargs(anthropic_api_key="", openai_api_key=""))

    def test_prod_accepts_openai_only(self):
        Settings(**_kwargs(anthropic_api_key="", openai_api_key="ok"))

    def test_prod_rejects_missing_deepgram(self):
        with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY"):
            Settings(**_kwargs(deepgram_api_key=""))
