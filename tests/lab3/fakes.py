"""
Author: L. Saetta
Date Last Modified: 2026-11-20
License: MIT
Description: Inert model responses, snapshot support and configuration for Lab 3 tests.
"""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from tests.unit.fakes import FakeSandboxClient
from triage.config import ModelProvider, OciAuth, Settings


class ModelFake:
    """Expose responses.create with recorded requests and scripted outcomes.

    Args:
        script: Responses or exceptions consumed in order.
    """

    def __init__(self, script: list[Any]) -> None:
        self._script = iter(script)
        self.requests: list[dict[str, Any]] = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> SimpleNamespace:
        """Return or raise the next scripted outcome without any network call."""
        self.requests.append(deepcopy(kwargs))
        value = next(self._script)
        if isinstance(value, BaseException):
            raise value
        return value


class SnapshotFake(FakeSandboxClient):
    """Extend the provided sandbox fake with immediately active snapshots."""

    def create_sandbox_snapshot(self, details: Any) -> SimpleNamespace:
        """Record snapshot creation and return its id.

        Args:
            details: OCI snapshot request.

        Returns:
            A snapshot response with an inert identifier.
        """
        self.calls.append("snapshot")
        return SimpleNamespace(data=SimpleNamespace(id="snapshot-fake"))

    def get_sandbox_snapshot(self, snapshot_id: str) -> SimpleNamespace:
        """Return an immediately active fake snapshot.

        Args:
            snapshot_id: Snapshot identifier.

        Returns:
            An ACTIVE lifecycle response.
        """
        return SimpleNamespace(data=SimpleNamespace(lifecycle_state="ACTIVE"))


def message(*texts: str) -> ResponseOutputMessage:
    """Build completed assistant output using real SDK types.

    Args:
        *texts: Text fragments for output_text parts.

    Returns:
        A completed assistant message.
    """
    return ResponseOutputMessage(
        id="message-fake",
        type="message",
        role="assistant",
        status="completed",
        content=[
            ResponseOutputText(type="output_text", text=text, annotations=[])
            for text in texts
        ],
    )


def response(response_id: str, *output: Any, **extra: Any) -> SimpleNamespace:
    """Wrap SDK output items in a minimal response.

    Args:
        response_id: Id for testing server-side continuation.
        *output: Real SDK output items.
        **extra: Optional usage or status fields.

    Returns:
        A scripted response.
    """
    return SimpleNamespace(id=response_id, output=list(output), **extra)


def report_data() -> dict:
    """Provide a fresh valid report citing the synthetic test bundle.

    Returns:
        JSON-compatible report fields, without local verification metadata.
    """
    return {
        "summary": "New connections fail.",
        "root_cause": "The database process limit was reached.",
        "confidence": "high",
        "evidence": [
            {"location": "db/alert_orcl.log:2", "observation": "ORA-00020 reported."}
        ],
        "timeline": [
            {
                "time": "10:01",
                "event": "Connections rejected.",
                "evidence": "db/alert_orcl.log:2",
            }
        ],
        "next_steps": ["Review process limits and connection pool sizing."],
    }


def investigation_script() -> list[SimpleNamespace]:
    """Script a real tool call, investigation answer and structured report.

    Returns:
        Three responses ready for an end-to-end application test.
    """
    call = ResponseFunctionToolCall(
        type="function_call",
        name="read_file",
        call_id="call-read",
        arguments=json.dumps(
            {"path": "db/alert_orcl.log", "start_line": 2, "end_line": 2}
        ),
    )
    return [
        response(
            "investigation-1",
            call,
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        ),
        response(
            "investigation-2",
            message("Limit reached (db/alert_orcl.log:2)."),
            usage=SimpleNamespace(input_tokens=20, output_tokens=6),
        ),
        response("report-1", message(json.dumps(report_data()))),
    ]


def settings() -> Settings:
    """Construct valid test settings without reading real configuration or secrets.

    Returns:
        Inert settings with fake credentials and identifiers.
    """
    return Settings(
        model_provider=ModelProvider.OPENAI,
        model_id="test-model",
        oci_genai_api_key="",
        openai_api_key="fake-test-key",
        oci_region="test-region",
        oci_project_id="ocid1.generativeaiproject.fake",
        oci_config_file=Path("unused"),
        oci_profile="TEST",
        oci_auth=OciAuth.API_KEY,
        sandbox_runtime="python-3.11",
        sandbox_shape="SMALL",
        sandbox_lifetime_minutes=30,
        sandbox_max_command_seconds=60,
        sandbox_ready_timeout_seconds=10,
        sandbox_warm_pool="",
    )
