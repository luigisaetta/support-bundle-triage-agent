"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Application settings, from two sources:

    - config/settings.toml: all configuration (model, OCI, sandbox). It holds
      no secrets and is versioned.
    - .env (or the environment): secrets only, the API keys. Never committed;
      see .env.example.

    Settings are immutable once loaded. Loading never raises: problems such as
    a missing file or an invalid value are collected, and validate() reports
    them, so callers like the preflight check can explain them to the user.

Usage:

    from triage.config import Settings

    settings = Settings.load()
    problems = settings.validate()
"""

import os
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

#: The configuration file, relative to the repository root.
DEFAULT_SETTINGS_FILE = Path("config/settings.toml")

#: The secrets file, relative to the repository root.
DEFAULT_ENV_FILE = Path(".env")

#: Compute shapes offered by OCI Sandboxes.
SANDBOX_SHAPES = ("SMALL", "MEDIUM", "LARGE")

#: The longest initial lifetime of a sandbox: 48 hours.
MAX_LIFETIME_MINUTES = 48 * 60


class ModelProvider(str, Enum):
    """Where the model is served. The application code is the same for both."""

    OCI = "oci"
    OPENAI = "openai"


class OciAuth(str, Enum):
    """How the OCI SDK signs its requests."""

    API_KEY = "api_key"
    SECURITY_TOKEN = "security_token"


@dataclass(frozen=True)
class Settings:  # pylint: disable=too-many-instance-attributes
    """Immutable application settings.

    Attributes:
        model_provider: Where the model is served.
        model_id: The model to call (an OCI model id or an OpenAI model name).
        oci_genai_api_key: OCI Generative AI API key (provider ``oci`` only).
        openai_api_key: OpenAI API key (provider ``openai`` only).
        oci_region: OCI region of the project and its sandboxes.
        oci_project_id: OCID of the Generative AI project with sandboxes enabled.
        oci_config_file: Path of the OCI SDK configuration file.
        oci_profile: Profile to use in the OCI configuration file.
        oci_auth: How the OCI SDK signs its requests.
        sandbox_runtime: Runtime of new sandboxes.
        sandbox_shape: Compute shape of new sandboxes.
        sandbox_lifetime_minutes: Lifetime of new sandboxes, in minutes.
        sandbox_max_command_seconds: Longest command the application runs.
        sandbox_ready_timeout_seconds: Longest wait for a new sandbox to run.
        sandbox_warm_pool: Name of the project's warm pool to start sandboxes
            from; empty to start them cold.
        load_problems: Problems found while reading the configuration file.
    """

    model_provider: ModelProvider
    model_id: str
    oci_genai_api_key: str = field(repr=False)
    openai_api_key: str = field(repr=False)
    oci_region: str
    oci_project_id: str
    oci_config_file: Path
    oci_profile: str
    oci_auth: OciAuth
    sandbox_runtime: str
    sandbox_shape: str
    sandbox_lifetime_minutes: int
    sandbox_max_command_seconds: int
    sandbox_ready_timeout_seconds: int
    sandbox_warm_pool: str
    load_problems: tuple[str, ...] = field(default=(), compare=False)

    @classmethod
    def load(
        cls,
        settings_file: Path = DEFAULT_SETTINGS_FILE,
        env_file: Path = DEFAULT_ENV_FILE,
    ) -> "Settings":
        """Load the configuration file and the secrets.

        Secrets already set in the environment win over the .env file.
        A missing or invalid value falls back to its default and is recorded
        in :attr:`load_problems`.

        Args:
            settings_file: The TOML configuration file.
            env_file: The .env file with the secrets; optional.

        Returns:
            The loaded settings.
        """
        config = TomlConfig.from_file(settings_file)
        load_dotenv(env_file, override=False)
        return cls(
            model_provider=config.choice("model", "provider", ModelProvider.OCI),
            model_id=config.text("model", "model_id"),
            oci_genai_api_key=_secret("OCI_GENAI_API_KEY"),
            openai_api_key=_secret("OPENAI_API_KEY"),
            oci_region=config.text("oci", "region"),
            oci_project_id=config.text("oci", "project_id"),
            oci_config_file=Path(
                config.text("oci", "config_file", "~/.oci/config")
            ).expanduser(),
            oci_profile=config.text("oci", "profile", "DEFAULT"),
            oci_auth=config.choice("oci", "auth", OciAuth.API_KEY),
            sandbox_runtime=config.text("sandbox", "runtime", "python-3.11"),
            sandbox_shape=config.text("sandbox", "shape", "SMALL").upper(),
            sandbox_lifetime_minutes=config.integer("sandbox", "lifetime_minutes", 60),
            sandbox_max_command_seconds=config.integer(
                "sandbox", "max_command_seconds", 300
            ),
            sandbox_ready_timeout_seconds=config.integer(
                "sandbox", "ready_timeout_seconds", 600
            ),
            sandbox_warm_pool=config.text("sandbox", "warm_pool"),
            load_problems=tuple(config.problems),
        )

    def validate(self) -> list[str]:
        """Check the settings for missing or invalid values.

        Returns:
            One human-readable message per problem; empty when all is well.
            Configuration keys are named as in the TOML file (for example
            ``oci.project_id``), secrets by their variable name.
        """
        problems = list(self.load_problems)

        required = {
            "model.model_id": self.model_id,
            "oci.region": self.oci_region,
            "oci.project_id": self.oci_project_id,
        }
        if self.model_provider is ModelProvider.OCI:
            required["OCI_GENAI_API_KEY (.env)"] = self.oci_genai_api_key
        else:
            required["OPENAI_API_KEY (.env)"] = self.openai_api_key
        problems += [
            f"{name} is not set" for name, value in required.items() if not value
        ]

        if self.oci_project_id and not self.oci_project_id.startswith(
            "ocid1.generativeaiproject."
        ):
            problems.append("oci.project_id is not a Generative AI project OCID")
        if self.sandbox_shape not in SANDBOX_SHAPES:
            problems.append(f"sandbox.shape must be one of {', '.join(SANDBOX_SHAPES)}")
        if not 1 <= self.sandbox_lifetime_minutes <= MAX_LIFETIME_MINUTES:
            problems.append(
                f"sandbox.lifetime_minutes must be between 1 and {MAX_LIFETIME_MINUTES}"
            )
        if self.sandbox_max_command_seconds < 1:
            problems.append("sandbox.max_command_seconds must be a positive integer")
        if self.sandbox_ready_timeout_seconds < 1:
            problems.append("sandbox.ready_timeout_seconds must be a positive integer")
        return problems

    @property
    def model_api_key(self) -> str:
        """The API key for the configured model provider."""
        if self.model_provider is ModelProvider.OCI:
            return self.oci_genai_api_key
        return self.openai_api_key

    def describe(self) -> str:
        """A one-line summary of the settings, without any secret.

        Returns:
            For example ``provider=oci, model=..., region=eu-frankfurt-1``.
        """
        return (
            f"provider={self.model_provider.value}, model={self.model_id or '?'}, "
            f"region={self.oci_region or '?'}"
        )


class TomlConfig:
    """Typed, forgiving access to the values of a TOML configuration file.

    Each getter returns the value of ``[section] key``, or a default when it
    is missing. A value of the wrong type or outside its allowed choices is
    replaced by the default and recorded in :attr:`problems`.

    Args:
        data: The parsed TOML document.
        problems: Problems already found (for example, while reading the file).
    """

    def __init__(self, data: dict[str, Any], problems: list[str] | None = None) -> None:
        self._data = data
        self.problems: list[str] = problems or []

    @classmethod
    def from_file(cls, path: Path) -> "TomlConfig":
        """Read and parse a TOML file.

        Args:
            path: The file to read.

        Returns:
            The configuration; empty, with a recorded problem, if the file is
            missing or is not valid TOML.
        """
        try:
            with open(path, "rb") as file:
                return cls(tomllib.load(file))
        except FileNotFoundError:
            return cls({}, [f"configuration file {path} not found"])
        except tomllib.TOMLDecodeError as error:
            return cls({}, [f"{path} is not valid TOML: {error}"])

    def text(self, section: str, key: str, default: str = "") -> str:
        """A string value, stripped of surrounding spaces."""
        value = self._value(section, key)
        if value is None:
            return default
        if not isinstance(value, str):
            self.problems.append(f"{section}.{key} must be a string")
            return default
        return value.strip()

    def integer(self, section: str, key: str, default: int) -> int:
        """An integer value."""
        value = self._value(section, key)
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            self.problems.append(f"{section}.{key} must be an integer")
            return default
        return value

    def choice(self, section: str, key: str, default: Enum) -> Any:
        """A value that must be one of the members of ``default``'s enum."""
        raw = self.text(section, key).lower()
        if not raw:
            return default
        enum_type = type(default)
        try:
            return enum_type(raw)
        except ValueError:
            allowed = ", ".join(member.value for member in enum_type)
            self.problems.append(f"{section}.{key} must be one of: {allowed}")
            return default

    def _value(self, section: str, key: str) -> Any:
        """The raw value of ``[section] key``, or None when it is missing."""
        table = self._data.get(section)
        return table.get(key) if isinstance(table, dict) else None


def _secret(name: str) -> str:
    """A secret from the environment (or .env), stripped of surrounding spaces."""
    return os.environ.get(name, "").strip()
