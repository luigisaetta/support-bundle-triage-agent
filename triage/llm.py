"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Builds the OpenAI client for the configured model provider. The
    application always uses the official openai library and the Responses API;
    only the endpoint, the API key and the project change:

    - oci: the OpenAI-compatible endpoint of OCI Generative AI, an OCI
      Generative AI API key and the project OCID.
    - openai: the default OpenAI endpoint and an OpenAI API key.

Usage:

    client = ModelClientFactory(settings).create()
    response = client.responses.create(model=settings.model_id, input="Hello")
"""

from openai import OpenAI

from triage.config import ModelProvider, Settings

#: The OpenAI-compatible endpoint of OCI Generative AI, per region.
OCI_OPENAI_BASE_URL = (
    "https://inference.generativeai.{region}.oci.oraclecloud.com/openai/v1"
)


class ModelClientFactory:
    """Creates an OpenAI client pointed at the configured provider.

    Args:
        settings: The application settings.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def create(self) -> OpenAI:
        """Create the client.

        Returns:
            An ``openai.OpenAI`` client ready for ``client.responses.create``.
        """
        settings = self._settings
        if settings.model_provider is ModelProvider.OCI:
            return OpenAI(
                base_url=OCI_OPENAI_BASE_URL.format(region=settings.oci_region),
                api_key=settings.oci_genai_api_key,
                project=settings.oci_project_id,
            )
        return OpenAI(api_key=settings.openai_api_key)
