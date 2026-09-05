import pytest

from knowledgeforge.config import Settings


def make_settings(**overrides: object) -> Settings:
    return Settings(
        environment="staging",
        jwt_secret_key="a-strong-secret-that-is-long-enough",
        gemini_api_key="real-key",
        local_embeddings=False,
        _env_file=None,
        **overrides,
    )


def test_development_environment_allows_defaults() -> None:
    Settings(environment="development", _env_file=None).validate_runtime()


def test_default_jwt_secret_is_rejected_outside_development() -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        make_settings(jwt_secret_key="change-me-in-production").validate_runtime()


def test_empty_jwt_secret_is_rejected_outside_development() -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        make_settings(jwt_secret_key="").validate_runtime()


def test_short_jwt_secret_is_rejected_outside_development() -> None:
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        make_settings(jwt_secret_key="too-short").validate_runtime()


def test_missing_gemini_key_rejected_unless_local_embeddings() -> None:
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        make_settings(gemini_api_key="replace-me").validate_runtime()

    make_settings(gemini_api_key="replace-me", local_embeddings=True).validate_runtime()


def test_valid_production_settings_pass() -> None:
    make_settings().validate_runtime()
