"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Builds the OCI SDK clients the application needs, from the
    settings. Two clients, two endpoints:

    - GenerativeAiSandboxClient (inference endpoint): sandboxes, commands,
      files and snapshots.
    - GenerativeAiClient (management endpoint): Generative AI projects, used
      to read the sandbox configuration of the project.

    Both sign their requests with an API key or a session token (created with
    "oci session authenticate"), as set in oci.auth.

Usage:

    clients = OciClients(settings)
    sandbox_client = clients.sandbox
"""

from functools import cached_property
from typing import Any

import oci
from oci.generative_ai import GenerativeAiClient
from oci.generative_ai_sandbox import GenerativeAiSandboxClient

from triage.config import OciAuth, Settings

#: Seconds to wait for a connection to the OCI endpoints.
CONNECT_TIMEOUT_SECONDS = 10

#: Extra seconds on the read timeout, beyond the longest command. A command
#: answers only when it ends, so the read timeout must outlast every command.
READ_TIMEOUT_MARGIN_SECONDS = 90


class OciClients:
    """Lazily built OCI SDK clients that share one configuration and signer.

    Args:
        settings: The application settings.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @cached_property
    def config(self) -> dict[str, Any]:
        """The OCI SDK configuration, with the region from the settings.

        Raises:
            oci.exceptions.ConfigFileNotFound: The configuration file is missing.
            oci.exceptions.ProfileNotFound: The profile is not in the file.
            oci.exceptions.InvalidConfig: A required entry is missing or invalid.
        """
        config = oci.config.from_file(
            file_location=str(self._settings.oci_config_file),
            profile_name=self._settings.oci_profile,
        )
        if self._settings.oci_region:
            config["region"] = self._settings.oci_region
        if self._settings.oci_auth is OciAuth.API_KEY:
            oci.config.validate_config(config)
        return config

    @cached_property
    def signer(self) -> Any | None:
        """The request signer for session-token auth; None for API-key auth.

        With API-key auth the SDK builds its signer from the configuration.

        Raises:
            KeyError: The profile has no ``security_token_file`` or ``key_file``.
            OSError: The token or the private key cannot be read.
        """
        if self._settings.oci_auth is not OciAuth.SECURITY_TOKEN:
            return None
        config = self.config
        with open(config["security_token_file"], encoding="utf-8") as token_file:
            token = token_file.read().strip()
        private_key = oci.signer.load_private_key_from_file(config["key_file"])
        return oci.auth.signers.SecurityTokenSigner(token, private_key)

    @cached_property
    def sandbox(self) -> GenerativeAiSandboxClient:
        """Client for sandboxes: lifecycle, commands, files and snapshots."""
        read_timeout = (
            self._settings.sandbox_max_command_seconds + READ_TIMEOUT_MARGIN_SECONDS
        )
        return GenerativeAiSandboxClient(
            self.config, timeout=(CONNECT_TIMEOUT_SECONDS, read_timeout), **self._auth()
        )

    @cached_property
    def generative_ai(self) -> GenerativeAiClient:
        """Client for the Generative AI management API (projects)."""
        return GenerativeAiClient(self.config, **self._auth())

    def _auth(self) -> dict[str, Any]:
        """Extra client arguments for the configured auth method."""
        return {"signer": self.signer} if self.signer is not None else {}
