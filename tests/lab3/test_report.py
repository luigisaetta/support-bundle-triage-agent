"""
Author: L. Saetta
Date Last Modified: 2026-11-20
License: MIT
Description: Verify strict reports, tolerant citation parsing and Markdown rendering.
"""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.lab3.fakes import ModelFake, message, report_data, response, settings
from tests.unit.fakes import FakeSandboxClient
from triage.agent import AgentResult
from triage.report import (
    REPORT_REQUEST,
    REPORT_SCHEMA,
    Evidence,
    EvidenceChecker,
    ReportError,
    ReportWriter,
    TriageReport,
    render_markdown,
)
from triage.sandbox import SandboxError, SandboxSession
from triage.tools import BUNDLE_DIR, ToolRegistry


def _result() -> AgentResult:
    """Build completed accounting for the report continuation request."""
    return AgentResult("Findings", "investigation-last", "answered", 2, (), 10, 5)


def _assert_strict(schema: dict) -> None:
    """Check strict objects at every nesting level, including array items."""
    if schema["type"] == "object":
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == set(schema["required"])
        for child in schema["properties"].values():
            _assert_strict(child)
    elif schema["type"] == "array":
        _assert_strict(schema["items"])


def test_schema_is_strict_at_every_level() -> None:
    """Require strict nested schemas and the specified confidence choices."""
    _assert_strict(REPORT_SCHEMA)
    assert REPORT_SCHEMA["properties"]["confidence"]["enum"] == [
        "high",
        "medium",
        "low",
    ]
    assert (
        "verified" not in REPORT_SCHEMA["properties"]["evidence"]["items"]["properties"]
    )


def test_writer_continues_server_conversation() -> None:
    """Send the report request and last id without resending investigation history."""
    client = ModelFake([response("report", message(json.dumps(report_data())))])
    report = ReportWriter(client, "test-model").write(_result())
    assert isinstance(report, TriageReport)
    assert report.root_cause == report_data()["root_cause"]
    assert report.evidence == (Evidence("db/alert_orcl.log:2", "ORA-00020 reported."),)
    assert client.requests == [
        {
            "model": "test-model",
            "previous_response_id": "investigation-last",
            "input": REPORT_REQUEST,
            "store": True,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "triage_report",
                    "schema": REPORT_SCHEMA,
                    "strict": True,
                }
            },
        }
    ]


@pytest.mark.parametrize(
    "text", ["not JSON", "", "[]", "null", "{}", "```json\n{}\n```"]
)
def test_invalid_json_and_wrong_root(text: str) -> None:
    """Reject malformed JSON, wrong roots and fenced output with ReportError."""
    writer = ReportWriter(ModelFake([response("r", message(text))]), "test-model")
    with pytest.raises(ReportError):
        writer.write(_result())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", "certain"),
        ("summary", 5),
        ("evidence", "a file"),
        ("evidence", [{"location": "a:1"}]),
        ("evidence", [{"location": "a:1", "observation": "x", "extra": "no"}]),
        ("timeline", [{"time": "now", "event": "x", "evidence": []}]),
        ("next_steps", [None]),
        ("unexpected", "extra"),
    ],
)
def test_schema_mismatches(field: str, value: object) -> None:
    """Validate types, required fields, enums and additional fields locally."""
    data = report_data()
    data[field] = value
    client = ModelFake([response("r", message(json.dumps(data)))])
    with pytest.raises(ReportError):
        ReportWriter(client, "test-model").write(_result())


def test_roundtrip_annotations_and_immutability() -> None:
    """Round-trip local annotations without accepting model-supplied verification."""
    original = report_data()
    before = deepcopy(original)
    report = TriageReport.from_dict(original)
    assert original == before
    checked = replace(report, evidence=(replace(report.evidence[0], verified=True),))
    assert TriageReport.from_dict(checked.to_dict()) == checked
    with pytest.raises(FrozenInstanceError):
        setattr(checked, "confidence", "low")
    with pytest.raises(FrozenInstanceError):
        setattr(checked.evidence[0], "verified", False)
    data = checked.to_dict()
    data["evidence"][0]["verified"] = "yes"
    with pytest.raises(ReportError):
        TriageReport.from_dict(data)
    client = ModelFake([response("r", message(json.dumps(checked.to_dict())))])
    with pytest.raises(ReportError):
        ReportWriter(client, "test-model").write(_result())


def test_fragmented_json_and_incomplete_response() -> None:
    """Join JSON fragments exactly and reject explicitly incomplete responses."""
    text = json.dumps(report_data())
    client = ModelFake([response("r", message(text[:25], text[25:]))])
    assert (
        ReportWriter(client, "test-model").write(_result()).summary
        == report_data()["summary"]
    )
    client = ModelFake([response("r", message(text), status="incomplete")])
    with pytest.raises(ReportError, match="incomplete"):
        ReportWriter(client, "test-model").write(_result())


def test_tolerant_citations_and_missing_evidence(tmp_path: Path) -> None:
    """Recognize all prompt formats, mixed lists, wrappers and Unicode ranges."""
    session = SandboxSession(FakeSandboxClient(tmp_path), "fake", owns_sandbox=False)
    session.write_file(f"{BUNDLE_DIR}/db/alert_orcl.log", "INFO\nERROR ORA-00020\n\n")
    tools = ToolRegistry(session)
    valid = [
        "db/alert_orcl.log:2",
        "`db/alert_orcl.log:2`",
        "db/alert_orcl.log:2-4",
        "db/alert_orcl.log:2–4",
        "db/alert_orcl.log:2, 9",
        "db/alert_orcl.log:2,29-34",
        "  'db/alert_orcl.log:2'  ",
        '"db/alert_orcl.log:2"',
        "db/alert_orcl.log:2—4",
        "db/alert_orcl.log:2–4, 9, 20—25",
        " `db/alert_orcl.log: 2, 9-12, 20–24` ",
        "db/alert_orcl.log:3",
    ]
    invalid = [
        "missing.log:1",
        "db/alert_orcl.log:99",
        "db/alert_orcl.log",
        "db/alert_orcl.log:0",
        "db/alert_orcl.log:1,no",
        "/etc/passwd:1",
        "../secret:1",
        "db/alert_orcl.log:two",
        "db/alert_orcl.log:2,",
        "",
        None,
    ]
    report = replace(
        TriageReport.from_dict(report_data()),
        evidence=tuple(
            Evidence(location, "Observation") for location in valid + invalid
        ),
    )
    with patch.object(tools, "call", wraps=tools.call) as call:
        checked = EvidenceChecker(tools).check(report)
    assert [item.verified for item in checked.evidence] == [True] * len(valid) + [
        False
    ] * len(invalid)
    assert all(item.verified is None for item in report.evidence)
    for invocation in call.call_args_list:
        assert invocation.args[0] == "read_file"
        assert invocation.args[1]["start_line"] == invocation.args[1]["end_line"]
    assert call.call_args_list[0].args[1] == {
        "path": "db/alert_orcl.log",
        "start_line": 2,
        "end_line": 2,
    }
    with patch.object(tools, "call", side_effect=SandboxError("disconnected")):
        checked = EvidenceChecker(tools).check(report)
    assert all(item.verified is False for item in checked.evidence)


def test_markdown_sections_and_verification_marks() -> None:
    """Render all sections, metadata, numbered actions and escaped table cells."""
    report = replace(
        TriageReport.from_dict(report_data()),
        evidence=(
            Evidence("db/alert_orcl.log:2", "ERROR | <script>\nmore", True),
            Evidence("missing:1", "Not found", False),
            Evidence("unchecked:1", "Unchecked"),
        ),
    )
    metadata = {
        "bundle": "sample.zip",
        "date": "2026-11-20",
        "model": "test-model",
        "sandbox": "fake",
        "steps": "2",
        "tool_calls": "1",
        "tokens": "10/5",
    }
    text = render_markdown(report, metadata)
    for heading in (
        "# Support Bundle Triage Report",
        "## Summary",
        "## Root cause",
        "## Evidence",
        "## Timeline",
        "## Next steps",
    ):
        assert heading in text
    assert "✔" in text and "✘" in text and "—" in text
    assert "ERROR \\| &lt;script&gt;<br>more" in text
    assert "1. Review process limits" in text
    assert "| Tool Calls | 1 |" in text
    assert all(value in text for value in metadata.values())
    assert "first cited line exists" in text


def test_writer_from_settings() -> None:
    """Use the provided client factory with the configured model identifier."""
    configured = settings()
    client = ModelFake([response("r", message(json.dumps(report_data())))])
    with patch("triage.report.ModelClientFactory") as factory:
        factory.return_value.create.return_value = client
        writer = ReportWriter.from_settings(configured)
        factory.assert_called_once_with(configured)
        factory.return_value.create.assert_called_once_with()
    writer.write(_result())
    assert client.requests[0]["model"] == configured.model_id
