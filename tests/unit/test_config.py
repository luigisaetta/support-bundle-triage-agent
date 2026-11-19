"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Unit tests of the settings: values from the TOML file, secrets
    from the environment, defaults, and the problems reported by validate().
"""

# pylint: disable=missing-function-docstring

from pathlib import Path

import pytest

from triage.config import ModelProvider, OciAuth, Settings

VALID_TOML = """
[model]
provider = "openai"
id = "gpt-test"

[oci]
region = "eu-frankfurt-1"
project_id = "ocid1.generativeaiproject.oc1.eu-frankfurt-1.abc"
auth = "security_token"

[sandbox]
shape = "medium"
lifetime_minutes = 30
"""

NO_ENV_FILE = Path("/nonexistent/.env")


@pytest.fixture(autouse=True)
def fixture_no_secrets(monkeypatch):
    """Start every test without API keys in the environment."""
    monkeypatch.delenv("OCI_GENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _load(tmp_path, text: str) -> Settings:
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(text, encoding="utf-8")
    return Settings.load(settings_file=settings_file, env_file=NO_ENV_FILE)


def test_values_come_from_the_toml_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = _load(tmp_path, VALID_TOML)
    assert settings.model_provider is ModelProvider.OPENAI
    assert settings.model_id == "gpt-test"
    assert settings.oci_auth is OciAuth.SECURITY_TOKEN
    assert settings.sandbox_shape == "MEDIUM"
    assert settings.sandbox_lifetime_minutes == 30
    assert settings.sandbox_runtime == "python-3.11"  # default
    assert settings.model_api_key == "sk-test"
    assert not settings.validate()


def test_secrets_are_read_from_the_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-from-file\n", encoding="utf-8")
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(VALID_TOML, encoding="utf-8")
    settings = Settings.load(settings_file=settings_file, env_file=env_file)
    assert settings.openai_api_key == "sk-from-file"


def test_secrets_are_not_shown_in_repr(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    assert "sk-secret" not in repr(_load(tmp_path, VALID_TOML))


def test_missing_file_is_reported(tmp_path):
    settings = Settings.load(settings_file=tmp_path / "nope.toml", env_file=NO_ENV_FILE)
    problems = settings.validate()
    assert any("not found" in problem for problem in problems)
    assert "model.id is not set" in problems
    assert "oci.project_id is not set" in problems
    assert "OCI_GENAI_API_KEY (.env) is not set" in problems


def test_invalid_values_are_reported(tmp_path):
    text = """
[model]
provider = "azure"
[sandbox]
lifetime_minutes = "sixty"
shape = "HUGE"
"""
    problems = _load(tmp_path, text).validate()
    assert "model.provider must be one of: oci, openai" in problems
    assert "sandbox.lifetime_minutes must be an integer" in problems
    assert "sandbox.shape must be one of SMALL, MEDIUM, LARGE" in problems


def test_invalid_toml_is_reported(tmp_path):
    problems = _load(tmp_path, "[model\nid = ").validate()
    assert any("not valid TOML" in problem for problem in problems)
