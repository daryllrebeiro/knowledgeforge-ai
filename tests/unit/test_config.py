import pytest

from knowledgeforge.config import Settings


def make_settings(**overrides: object) -> Settings:
    # Pop jwt_secret_key and gemini_api_key from overrides if provided, else use defaults
    jwt_secret_key = overrides.pop("jwt_secret_key", "a-strong-secret-that-is-long-enough")
    gemini_api_key = overrides.pop("gemini_api_key", "real-key")
    local_embeddings = overrides.pop("local_embeddings", False)
    environment = overrides.pop("environment", "staging")
    return Settings(
        environment=environment,
        jwt_secret_key=jwt_secret_key,
        gemini_api_key=gemini_api_key,
        local_embeddings=local_embeddings,
        _env_file=None,
        **overrides,
    )


def test_development_environment_enforces_jwt_secret() -> None:
    # Development environment no longer allows weak defaults (CS-01)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY must be set"):
        Settings(environment="development", _env_file=None).validate_runtime()


def test_default_jwt_secret_is_rejected_everywhere() -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        make_settings(jwt_secret_key="change-me-in-production").validate_runtime()


def test_placeholder_jwt_secret_is_rejected_everywhere() -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        make_settings(jwt_secret_key="REPLACE_WITH_32_CHAR_MIN_SECRET_OR_STARTUP_WILL_FAIL").validate_runtime()


def test_empty_jwt_secret_is_rejected_everywhere() -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        make_settings(jwt_secret_key="").validate_runtime()


def test_short_jwt_secret_is_rejected_everywhere() -> None:
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        make_settings(jwt_secret_key="too-short").validate_runtime()


def test_missing_gemini_key_rejected_unless_local_embeddings() -> None:
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        make_settings(gemini_api_key="replace-me").validate_runtime()

    # Both local_embeddings AND local_generation must be True to bypass Gemini key requirement
    make_settings(gemini_api_key="replace-me", local_embeddings=True, local_generation=True).validate_runtime()


def test_valid_production_settings_pass() -> None:
    make_settings().validate_runtime()


def test_billing_webhook_secret_required_when_billing_enabled_outside_dev() -> None:
    with pytest.raises(RuntimeError, match="STRIPE_WEBHOOK_SECRET must be configured"):
        make_settings(environment="production", stripe_secret_key="sk_live_123", stripe_webhook_secret="").validate_runtime()


def test_billing_secret_key_required_when_webhook_secret_set_outside_dev() -> None:
    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY must be configured"):
        make_settings(environment="production", stripe_secret_key="", stripe_webhook_secret="whsec_123").validate_runtime()


def test_billing_fully_configured_passes_outside_dev() -> None:
    make_settings(environment="production", stripe_secret_key="sk_live_123", stripe_webhook_secret="whsec_123").validate_runtime()