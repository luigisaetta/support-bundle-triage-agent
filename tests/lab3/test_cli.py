"""
Author: L. Saetta
Date Last Modified: 2026-11-21
License: MIT
Description: Test CLI output, progress throttling and sandbox lifecycle with local fakes.
"""

from dataclasses import dataclass, replace
import io
import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from openai import APIError
import pytest

from tests.lab3.fakes import (
    ModelFake,
    SnapshotFake,
    investigation_script,
    message,
    report_data,
    response,
    settings,
)
from triage import cli
from triage.cli import BOX_WIDTH, DEFAULT_TASK, TriageApp
from triage.config import Settings
from triage.ingest import BundleError
from triage.report import ReportError
from triage.sandbox import SandboxError, SandboxManager, SandboxSession

COLOR_CODE = re.compile(r"\033\[[0-9;]*m")
BOX_CHARS = ("╭", "│", "╰")


def plain(text: str) -> str:
    """Remove color codes, keeping the cursor controls of the progress line.

    Args:
        text: Captured console output.

    Returns:
        The same text without SGR color sequences.
    """
    return COLOR_CODE.sub("", text)


def box_lines(text: str) -> list[str]:
    """Collect the lines of the banner and of the result panel.

    Args:
        text: Captured console output, without color codes.

    Returns:
        Every line drawn as part of a box.
    """
    return [line for line in text.splitlines() if line.startswith(BOX_CHARS)]


class TerminalStream(io.StringIO):
    """Capture raw terminal writes for progress rendering tests."""

    def isatty(self) -> bool:
        """Report terminal behavior.

        Returns:
            True, without needing an interactive terminal in the test runner.
        """
        return True


@dataclass
class Harness:
    """Dependencies for one inert end-to-end investigation."""

    client: SnapshotFake
    manager: SandboxManager
    model: ModelFake
    bundle: Path
    output: Path
    stream: io.StringIO
    settings: Settings

    def app(self) -> TriageApp:
        """Construct an app with real orchestration and fake external services.

        Returns:
            Application backed by the current test's resources.
        """
        return TriageApp(
            self.settings, self.manager, self.model, self.output, self.stream
        )


@pytest.fixture(name="harness")
def fixture_harness(tmp_path: Path) -> Harness:
    """Create a bundle and real manager over the provided local sandbox fake.

    Args:
        tmp_path: Isolated filesystem root for the test.

    Returns:
        Dependencies for one investigation, without OCI or model network access.
    """
    root = tmp_path / "sandbox"
    root.mkdir()
    client = SnapshotFake(root)
    configured = settings()
    manager = SandboxManager(client, configured.oci_project_id)
    bundle = tmp_path / "sample.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(
            "db/alert_orcl.log", "10:00 INFO started\n10:01 ERROR ORA-00020\n"
        )
        archive.writestr("config.ini", "processes=300\n")
    return Harness(
        client,
        manager,
        ModelFake(investigation_script()),
        bundle,
        tmp_path / "out",
        io.StringIO(),
        configured,
    )


def test_end_to_end_reports_and_cleanup(harness: Harness) -> None:
    """Run real upload, tool execution, report checking and lifecycle operations."""
    path = harness.app().run(harness.bundle)
    assert path == harness.output / "sample-report.md"
    markdown = path.read_text(encoding="utf-8")
    saved = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert saved["root_cause"] == report_data()["root_cause"]
    assert saved["evidence"][0]["verified"] is True
    assert "✔" in markdown and "| Steps | 2 |" in markdown
    assert "30 input / 11 output (investigation)" in markdown
    assert harness.client.calls[0] == "create"
    assert harness.client.calls[-2:] == ["stop", "delete"]
    requests = harness.model.requests
    assert requests[1]["previous_response_id"] == "investigation-1"
    assert (
        "db/alert_orcl.log:2: 10:01 ERROR ORA-00020"
        in requests[1]["input"][0]["output"]
    )
    assert requests[2]["previous_response_id"] == "investigation-2"
    assert requests[2]["text"]["format"]["strict"] is True
    assert all(request["store"] is True for request in requests)
    text = harness.stream.getvalue()
    for step in (
        "1. Sandbox",
        "2. Bundle",
        "3. Investigation",
        "4. Report",
        "5. Cleanup",
    ):
        assert step in text
    for value in (
        "a cold start can take minutes",
        "RUNNING",
        "2 files",
        "step 1: read_file",
        "Root cause:",
        "Confidence: high",
        "Evidence verified: 1/1",
        str(path),
        str(path.with_suffix(".json")),
    ):
        assert value in text


def test_banner_and_result_panel(harness: Harness) -> None:
    """Open with the banner and end with the summary and root cause in a panel."""
    harness.app().run(harness.bundle)
    text = harness.stream.getvalue()
    assert "\033[" not in text  # no escape codes on a non-terminal stream
    assert text.index("SUPPORT BUNDLE TRIAGE AGENT") < text.index("1. Sandbox")
    assert "sample.zip" in text and "test-model" in text
    panel = text[text.index("Triage result") :]
    assert text.index("5. Cleanup") < text.index("Triage result")
    for value in (
        "Summary:",
        report_data()["summary"],
        "Root cause:",
        report_data()["root_cause"],
        "Confidence: high",
        "Evidence verified: 1/1",
    ):
        assert value in panel
    assert box_lines(text) and all(len(line) == BOX_WIDTH for line in box_lines(text))


def test_long_texts_wrap_inside_the_boxes(harness: Harness) -> None:
    """Wrap a long summary without losing words; cut a long model id in the banner."""
    summary = " ".join(f"word{index}" for index in range(60))
    long_report = {**report_data(), "summary": summary}
    script = investigation_script()
    script[-1] = response("report-1", message(json.dumps(long_report)))
    harness.model = ModelFake(script)
    harness.settings = replace(harness.settings, model_id="m" * 200)
    harness.app().run(harness.bundle)
    text = harness.stream.getvalue()
    lines = box_lines(text)
    assert all(len(line) == BOX_WIDTH for line in lines)
    start = next(i for i, line in enumerate(lines) if "Triage result" in line)
    panel = " ".join(lines[start:])
    assert all(f"word{index}" in panel.split() for index in range(60))
    assert "…" in text


@pytest.mark.parametrize("no_color", [False, True])
def test_terminal_colors_respect_no_color(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, no_color: bool
) -> None:
    """Color a terminal stream, and keep it plain when NO_COLOR is set."""
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    harness.stream = TerminalStream()
    harness.app().run(harness.bundle)
    text = harness.stream.getvalue()
    assert bool(COLOR_CODE.search(text)) is not no_color
    assert all(len(line) == BOX_WIDTH for line in box_lines(plain(text)))


def test_keep_prints_reuse_command(harness: Harness) -> None:
    """Preserve a newly created sandbox and show its id and reuse command."""
    assert harness.app().run(harness.bundle, keep=True).is_file()
    assert "create" in harness.client.calls
    assert "stop" not in harness.client.calls and "delete" not in harness.client.calls
    assert "Sandbox kept: ocid1.sandbox.fake" in harness.stream.getvalue()
    assert "--sandbox-id ocid1.sandbox.fake" in harness.stream.getvalue()
    assert "python -m triage.cli" in harness.stream.getvalue()


def test_attached_sandbox_is_never_closed(harness: Harness) -> None:
    """Do not call close on an attached session, even though close itself is safe."""
    with patch.object(SandboxSession, "close", autospec=True) as close:
        harness.app().run(harness.bundle, sandbox_id="existing")
    close.assert_not_called()
    assert "create" not in harness.client.calls and "stop" not in harness.client.calls
    assert "Attached sandbox preserved: existing" in harness.stream.getvalue()


def test_snapshot_before_close(harness: Harness) -> None:
    """Exercise real session snapshot handling with a fake ACTIVE response."""
    harness.app().run(harness.bundle, snapshot=True)
    assert harness.client.calls[-3:] == ["snapshot", "stop", "delete"]
    assert "Snapshot: snapshot-fake" in harness.stream.getvalue()


def test_slow_close_keeps_successful_report(harness: Harness) -> None:
    """Warn with manual deletion guidance and return the report after slow closure."""
    with patch.object(
        SandboxSession, "close", side_effect=SandboxError("still STOPPING...")
    ):
        path = harness.app().run(harness.bundle)
    assert path.is_file() and path.with_suffix(".json").is_file()
    text = harness.stream.getvalue()
    assert "WARNING:" in text and "ocid1.sandbox.fake" in text
    assert "delete it from the OCI Console once it is STOPPED" in text
    assert "ERROR:" not in text


def test_bad_bundle_prints_once_and_cleans_up(harness: Harness) -> None:
    """Reject bad zip data before model use, then still close the created session."""
    harness.bundle.write_text("not a zip", encoding="utf-8")
    with pytest.raises(BundleError, match="not a zip"):
        harness.app().run(harness.bundle)
    assert harness.client.calls[-2:] == ["stop", "delete"]
    assert harness.model.requests == []
    assert harness.stream.getvalue().count("ERROR:") == 1
    assert "Traceback" not in harness.stream.getvalue()
    assert "5. Cleanup" in harness.stream.getvalue()


@pytest.mark.parametrize("attached", [False, True])
def test_bundle_failure_respects_ownership(harness: Harness, attached: bool) -> None:
    """Preserve the selected sandbox after failure when attached or explicitly kept."""
    harness.bundle.write_text("bad zip", encoding="utf-8")
    with patch.object(SandboxSession, "close", autospec=True) as close:
        with pytest.raises(BundleError):
            harness.app().run(
                harness.bundle,
                keep=not attached,
                sandbox_id="existing" if attached else None,
            )
    close.assert_not_called()


@pytest.mark.parametrize("failure", ["api", "report", "interrupt"])
def test_model_failure_and_ctrl_c_cleanup(harness: Harness, failure: str) -> None:
    """Clean up after API errors, bad report JSON or KeyboardInterrupt."""
    if failure == "api":
        harness.model = ModelFake(
            [
                APIError(
                    "Model unavailable\nretry later",
                    request=SimpleNamespace(),
                    body=None,
                )
            ]
        )
        expected = APIError
    elif failure == "interrupt":
        harness.model = ModelFake([KeyboardInterrupt()])
        expected = KeyboardInterrupt
    else:
        harness.model = ModelFake(
            [
                response("answer", message("Findings")),
                response("report", message("bad JSON")),
            ]
        )
        expected = ReportError
    with pytest.raises(expected):
        harness.app().run(harness.bundle)
    assert harness.client.calls[-2:] == ["stop", "delete"]
    errors = [
        line for line in harness.stream.getvalue().splitlines() if "ERROR:" in line
    ]
    assert len(errors) == 1 and "Traceback" not in errors[0]


def test_slow_close_does_not_hide_original_error(harness: Harness) -> None:
    """Keep an investigation failure as a failure when cleanup also needs attention."""
    harness.bundle.write_text("bad zip", encoding="utf-8")
    with patch.object(
        SandboxSession, "close", side_effect=SandboxError("still STOPPING")
    ):
        with pytest.raises(BundleError, match="not a zip"):
            harness.app().run(harness.bundle)
    text = harness.stream.getvalue()
    assert text.count("ERROR:") == 1 and "WARNING:" in text


def test_snapshot_failure_still_closes_owned_session(harness: Harness) -> None:
    """Treat a requested snapshot failure as an error while ensuring closure."""
    with patch.object(
        SandboxSession, "snapshot", side_effect=SandboxError("snapshot failed")
    ):
        with pytest.raises(SandboxError, match="snapshot failed"):
            harness.app().run(harness.bundle, snapshot=True)
    assert harness.client.calls[-2:] == ["stop", "delete"]
    assert harness.stream.getvalue().count("ERROR:") == 1


def test_invalid_settings_allocate_no_resources(harness: Harness) -> None:
    """Validate settings before creating or attaching a sandbox."""
    harness.settings = replace(harness.settings, model_id="")
    with pytest.raises(ValueError, match="model.model_id"):
        harness.app().run(harness.bundle)
    assert harness.client.calls == [] and harness.model.requests == []


def test_output_error_still_closes_session(harness: Harness) -> None:
    """Clean up if a report destination is occupied by a regular file."""
    harness.output.write_text("occupied", encoding="utf-8")
    with pytest.raises(OSError):
        harness.app().run(harness.bundle)
    assert harness.client.calls[-2:] == ["stop", "delete"]
    assert harness.stream.getvalue().count("ERROR:") == 1


def test_many_nonterminal_states_are_throttled(harness: Harness) -> None:
    """Reduce repeated real-manager callbacks using a patched progress clock."""
    assert harness.stream.isatty() is False
    harness.client.queue_states(["CREATING"] * 100 + ["RUNNING"])
    with (
        patch("triage.sandbox.time.sleep"),
        patch("triage.cli.monotonic", side_effect=range(1000)),
    ):
        harness.app().run(harness.bundle)
    lines = harness.stream.getvalue().splitlines()
    assert len([line for line in lines if line.strip().startswith("CREATING,")]) == 4
    assert len([line for line in lines if line.strip().startswith("RUNNING,")]) == 1
    assert (
        "\r" not in harness.stream.getvalue()
        and "\033[K" not in harness.stream.getvalue()
    )


def test_state_changes_print_immediately(harness: Harness) -> None:
    """Print first state, periodic updates and state changes on redirected streams."""
    times = [0, 1, 29, 30, 31, 59, 60, 61, 62, 63]
    with patch("triage.cli.monotonic", side_effect=times):
        progress = cli._Progress(harness.stream)
        for index in range(7):
            progress(f"CREATING, {times[index]} s")
        progress("RUNNING, 61 s")
        progress("STOPPING, 62 s")
        progress("STOPPING, 63 s")
        progress.close()
    assert [line.strip() for line in harness.stream.getvalue().splitlines()] == [
        "CREATING, 0 s",
        "CREATING, 30 s",
        "CREATING, 60 s",
        "RUNNING, 61 s",
        "STOPPING, 62 s",
    ]


def test_terminal_progress_rewrites_and_finishes_each_step(harness: Harness) -> None:
    """Keep state messages on one terminal line and terminate it before the next step."""
    harness.stream = TerminalStream()
    harness.client.queue_states(["CREATING"] * 20 + ["RUNNING"])
    with patch("triage.sandbox.time.sleep"):
        harness.app().run(harness.bundle)
    text = plain(harness.stream.getvalue())
    assert len(re.findall(r"\r[^\n\r]*CREATING,", text)) == 20
    assert not re.search(r"\n[^\n\r]*CREATING,", text)
    assert re.search(r"\r[^\n\r]*(CREATING|RUNNING),[^\n\r]*\033\[K\n", text)
    assert re.search(r"\r[^\n\r]*deleting\033\[K\n", text)


def test_terminal_progress_finishes_after_creation_error(harness: Harness) -> None:
    """End an active status line even when creation fails before returning a session."""
    harness.stream = TerminalStream()

    def fail_create(**kwargs: object) -> None:
        """Emit one state and fail without allocating a real resource."""
        kwargs["on_progress"]("CREATING, 0 s")
        raise SandboxError("cannot start")

    with patch.object(harness.manager, "create", side_effect=fail_create):
        with pytest.raises(SandboxError, match="cannot start"):
            harness.app().run(harness.bundle)
    text = plain(harness.stream.getvalue())
    assert re.search(r"\033\[K\n[^\n]*ERROR: cannot start\n", text)


def _main(harness: Harness, arguments: list[str]) -> int:
    """Run the real parser and main, replacing only external dependency factories."""
    with (
        patch("sys.argv", ["triage.cli", str(harness.bundle), *arguments]),
        patch("sys.stdout", harness.stream),
        patch("triage.cli.Settings.load", return_value=harness.settings),
        patch("triage.cli.OciClients") as clients,
        patch(
            "triage.cli.SandboxManager.from_settings", return_value=harness.manager
        ) as manager,
        patch("triage.cli.ModelClientFactory") as model_factory,
    ):
        model_factory.return_value.create.return_value = harness.model
        status = cli.main()
        if not harness.settings.validate():
            clients.assert_called_once_with(harness.settings)
            manager.assert_called_once_with(harness.settings, clients.return_value)
            model_factory.assert_called_once_with(harness.settings)
        else:
            clients.assert_not_called()
            manager.assert_not_called()
            model_factory.assert_not_called()
    return status


def test_main_options_and_forced_final_answer(harness: Harness) -> None:
    """Pass all run options through the real argument parser and orchestrator."""
    assert (
        _main(
            harness,
            [
                "--output-dir",
                str(harness.output),
                "--task",
                "Custom task",
                "--max-steps",
                "1",
                "--keep",
                "--snapshot",
            ],
        )
        == 0
    )
    assert (harness.output / "sample-report.md").is_file()
    assert harness.model.requests[0]["input"] == [
        {"role": "user", "content": "Custom task"}
    ]
    assert harness.model.requests[1]["tool_choice"] == "none"
    assert "snapshot" in harness.client.calls and "stop" not in harness.client.calls


def test_main_defaults_and_attachment(harness: Harness) -> None:
    """Use the default task and honor --sandbox-id without allocating a sandbox."""
    assert (
        _main(
            harness, ["--output-dir", str(harness.output), "--sandbox-id", "existing"]
        )
        == 0
    )
    assert harness.model.requests[0]["input"] == [
        {"role": "user", "content": DEFAULT_TASK}
    ]
    assert "create" not in harness.client.calls and "stop" not in harness.client.calls
    arguments = cli._parser().parse_args([str(harness.bundle)])
    assert arguments.output_dir == Path("out") and arguments.max_steps == 15


def test_main_invalid_settings_exit_one(harness: Harness) -> None:
    """Print configuration problems without creating external clients."""
    harness.settings = replace(harness.settings, model_id="", oci_region="")
    assert _main(harness, []) == 1
    assert "model.model_id is not set" in harness.stream.getvalue()
    assert "oci.region is not set" in harness.stream.getvalue()
    assert harness.client.calls == []


def test_main_bad_bundle_exit_one(harness: Harness) -> None:
    """Print one expected error and return one after cleaning up the sandbox."""
    harness.bundle.write_text("bad zip", encoding="utf-8")
    assert _main(harness, []) == 1
    assert harness.stream.getvalue().count("ERROR:") == 1
    assert harness.client.calls[-2:] == ["stop", "delete"]


def test_main_ctrl_c_exit_code(harness: Harness) -> None:
    """Return the conventional interruption status after cleanup."""
    harness.model = ModelFake([KeyboardInterrupt()])
    assert _main(harness, []) == 130
    assert harness.client.calls[-2:] == ["stop", "delete"]
    assert harness.stream.getvalue().count("ERROR:") == 1


def test_main_slow_close_exits_zero(harness: Harness) -> None:
    """Preserve CLI success after close raises SandboxError, as required by the prompt."""
    with patch.object(
        SandboxSession, "close", side_effect=SandboxError("still STOPPING...")
    ):
        status = _main(harness, ["--output-dir", str(harness.output)])
    assert status == 0
    assert (harness.output / "sample-report.md").is_file()
    assert "WARNING:" in harness.stream.getvalue()
    assert (
        "delete it from the OCI Console once it is STOPPED" in harness.stream.getvalue()
    )
    assert "ERROR:" not in harness.stream.getvalue()
