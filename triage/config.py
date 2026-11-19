"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Application settings. All configuration is read from environment
    variables, optionally loaded from a .env file in the repository root (see
    .env.example). Settings are immutable once loaded; validate() reports what
    is missing or invalid without raising, so callers such as the preflight
    check can explain the problem to the user.

Usage:

    from triage.config import Settings

    settings = Settings.from_env()
    problems = settings.validate()
"""

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv


class ModelProvider(str, Enum):
    """Where the model is served. The application code is the same for both."""

    OCI = "oci"
    OPENAI = "openai"


class OciAuth(str, Enum):
    """How the OCI SDK signs its requests."""

    API_KEY = "api_key"
    SECURITY_TOKEN = "security_token"


#: Compute shapes offered by OCI Sandboxes.
SANDBOX_SHAPES = ("SMALL", "MEDIUM", "LARGE")

#: The longest initial lifetime of a sandbox: 48 hours.
MAX_LIFETIME_MINUTES = 48 * 60


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
    """

    model_provider: ModelProvider
    model_id: str
    oci_genai_api_key: str
    openai_api_key: str
    oci_region: str
    oci_project_id: str
    oci_config_file: Path
    oci_profile: str
    oci_auth: OciAuth
    sandbox_runtime: str
    sandbox_shape: str
    sandbox_lifetime_minutes: int
    sandbox_max_command_seconds: int

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        """Load the settings from the environment and an optional .env file.

        Values already set in the environment win over the .env file.
        Invalid enum or integer values are kept as safe defaults here and
        reported by :meth:`validate` through the raw environment.

        Args:
            env_file: The .env file to load. Defaults to ``.env`` in the
                current directory, if it exists.

        Returns:
            The loaded settings.
        """
        load_dotenv(env_file or Path(".env"), override=False)
        return cls(
            model_provider=_enum(ModelProvider, "MODEL_PROVIDER", ModelProvider.OCI),
            model_id=_text("MODEL_ID"),
            oci_genai_api_key=_text("OCI_GENAI_API_KEY"),
            openai_api_key=_text("OPENAI_API_KEY"),
            oci_region=_text("OCI_REGION"),
            oci_project_id=_text("OCI_PROJECT_ID"),
            oci_config_file=Path(
                _text("OCI_CONFIG_FILE", "~/.oci/config")
            ).expanduser(),
            oci_profile=_text("OCI_PROFILE", "DEFAULT"),
            oci_auth=_enum(OciAuth, "OCI_AUTH", OciAuth.API_KEY),
            sandbox_runtime=_text("SANDBOX_RUNTIME", "python-3.11"),
            sandbox_shape=_text("SANDBOX_SHAPE", "SMALL").upper(),
            sandbox_lifetime_minutes=_integer("SANDBOX_LIFETIME_MINUTES", 60),
            sandbox_max_command_seconds=_integer("SANDBOX_MAX_COMMAND_SECONDS", 300),
        )

    def validate(self) -> list[str]:
        """Check the settings for missing or invalid values.

        Returns:
            One human-readable message per problem; empty when all is well.
        """
        problems: list[str] = []

        for name, choices in (("MODEL_PROVIDER", ModelProvider), ("OCI_AUTH", OciAuth)):
            raw = _text(name).lower()
            if raw and raw not in {choice.value for choice in choices}:
                problems.append(f"{name} has an invalid value: {raw!r}")

        required = {
            "MODEL_ID": self.model_id,
            "OCI_REGION": self.oci_region,
            "OCI_PROJECT_ID": self.oci_project_id,
        }
        if self.model_provider is ModelProvider.OCI:
            required["OCI_GENAI_API_KEY"] = self.oci_genai_api_key
        else:
            required["OPENAI_API_KEY"] = self.openai_api_key
        problems += [
            f"{name} is not set" for name, value in required.items() if not value
        ]

        if self.oci_project_id and not self.oci_project_id.startswith(
            "ocid1.generativeaiproject."
        ):
            problems.append("OCI_PROJECT_ID is not a Generative AI project OCID")
        if self.sandbox_shape not in SANDBOX_SHAPES:
            problems.append(f"SANDBOX_SHAPE must be one of {', '.join(SANDBOX_SHAPES)}")
        if not 1 <= self.sandbox_lifetime_minutes <= MAX_LIFETIME_MINUTES:
            problems.append(
                f"SANDBOX_LIFETIME_MINUTES must be between 1 and {MAX_LIFETIME_MINUTES}"
            )
        if self.sandbox_max_command_seconds < 1:
            problems.append("SANDBOX_MAX_COMMAND_SECONDS must be a positive integer")
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


def _text(name: str, default: str = "") -> str:
    """An environment variable as a stripped string."""
    return os.environ.get(name, default).strip()


def _integer(name: str, default: int) -> int:
    """An environment variable as an integer; -1 when it is not a number."""
    raw = _text(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return -1


def _enum(enum_type: type[Enum], name: str, default: Enum):
    """An environment variable as a member of ``enum_type``, or ``default``."""
    raw = _text(name).lower()
    try:
        return enum_type(raw) if raw else default
    except ValueError:
        return default
