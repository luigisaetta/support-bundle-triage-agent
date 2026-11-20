"""
Author: L. Saetta
Date Last Modified: 2026-11-20
License: MIT
Description: Unit tests of the project and model checks of the preflight:
    server-side conversation settings of the project, and a model call that
    continues on the server with previous_response_id. No OCI or model call
    is made.
"""

# pylint: disable=missing-function-docstring

from pathlib import Path
from types import SimpleNamespace

from triage.config import Settings
from triage.preflight import checks
from triage.preflight.base import PreflightContext, Status


def _project(conversation=True, memory=True):
    """A project as returned by get_generative_ai_project."""
    networking = SimpleNamespace(
        network_mode="MANAGED", network_policy=SimpleNamespace(mode="DENY_ALL")
    )
    return SimpleNamespace(
        display_name="proj-test",
        lifecycle_state="ACTIVE",
        conversation_config=(
            SimpleNamespace(conversations_retention_in_hours=720)
            if conversation
            else None
        ),
        short_term_memory_optimization_config=SimpleNamespace(
            is_enabled=memory,
            condenser_config=SimpleNamespace(
                llm_selection=SimpleNamespace(model_id="openai.gpt-oss-120b")
            ),
        ),
        sandbox_config=SimpleNamespace(
            is_enabled=True, sandbox_outbound_networking_config=networking
        ),
    )


def _context(monkeypatch, project=None):
    """A context whose OCI clients return ``project``."""
    monkeypatch.delenv("OCI_GENAI_API_KEY", raising=False)
    settings = Settings.load(
        settings_file=Path("/nonexistent/settings.toml"),
        env_file=Path("/nonexistent/.env"),
    )
    context = PreflightContext(settings=settings)
    generative_ai = SimpleNamespace(
        get_generative_ai_project=lambda project_id: SimpleNamespace(data=project)
    )
    context.__dict__["clients"] = SimpleNamespace(generative_ai=generative_ai)
    return context


class FakeResponses:
    """Scripted ``client.responses``; records the arguments of each call."""

    def __init__(self, second_call_error=None):
        self.calls = []
        self._second_call_error = second_call_error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            call = SimpleNamespace(
                type="function_call", name="get_utc_time", call_id="call_1"
            )
            return SimpleNamespace(id="resp_1", output=[call])
        if self._second_call_error:
            raise self._second_call_error
        return SimpleNamespace(id="resp_2", output=[SimpleNamespace(type="message")])


def _fake_model(monkeypatch, responses):
    client = SimpleNamespace(responses=responses)
    factory = SimpleNamespace(create=lambda: client)
    monkeypatch.setattr(checks, "ModelClientFactory", lambda settings: factory)


def test_project_with_conversation_and_memory_passes(monkeypatch):
    result = checks.ProjectCheck().run(_context(monkeypatch, _project()))
    assert result.status is Status.PASS
    assert "conversations 720 h" in result.detail
    assert "openai.gpt-oss-120b" in result.detail


def test_project_without_conversation_fails_without_blocking(monkeypatch):
    result = checks.ProjectCheck().run(
        _context(monkeypatch, _project(conversation=False))
    )
    assert result.status is Status.FAIL and not result.blocking
    assert "conversations are not configured" in result.detail


def test_project_without_short_term_memory_fails(monkeypatch):
    result = checks.ProjectCheck().run(_context(monkeypatch, _project(memory=False)))
    assert result.status is Status.FAIL
    assert "short-term memory" in result.detail


def test_model_check_continues_the_conversation_on_the_server(monkeypatch):
    responses = FakeResponses()
    _fake_model(monkeypatch, responses)
    result = checks.ModelCheck().run(_context(monkeypatch))
    assert result.status is Status.PASS
    assert len(responses.calls) == 2
    first, second = responses.calls[0], responses.calls[1]
    assert first["store"] is True and "previous_response_id" not in first
    assert second["store"] is True
    assert second["previous_response_id"] == "resp_1"
    assert second["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": checks.TIME_TOOL_OUTPUT,
        }
    ]


def test_model_check_reports_a_failed_server_side_conversation(monkeypatch):
    _fake_model(monkeypatch, FakeResponses(second_call_error=RuntimeError("404")))
    result = checks.ModelCheck().run(_context(monkeypatch))
    assert result.status is Status.FAIL
    assert "server-side conversation failed" in result.detail
