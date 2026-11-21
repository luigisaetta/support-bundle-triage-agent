"""
Author: L. Saetta
Date Last Modified: 2026-11-21
License: MIT
Description: Run sandbox investigations, save reports and manage resource cleanup.
"""

import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import sys
import textwrap
from time import monotonic
from typing import TextIO

from oci.exceptions import ServiceError
from openai import APIError, OpenAI

from triage.agent import DEFAULT_MAX_STEPS, AgentResult, TriageAgent
from triage.config import Settings
from triage.ingest import BundleError, BundleUploader
from triage.llm import ModelClientFactory
from triage.oci_clients import OciClients
from triage.report import (
    EvidenceChecker,
    ReportError,
    ReportWriter,
    TriageReport,
    render_markdown,
)
from triage.sandbox import SandboxError, SandboxManager, SandboxSession
from triage.tools import ToolRegistry

DEFAULT_TASK = "Find the root cause of the issue described in the support bundle."
DEFAULT_OUTPUT_DIR = Path("out")
PROGRESS_INTERVAL_SECONDS = 30.0
BOX_WIDTH = 64
INDENT = "  "
_EXPECTED_ERRORS = (
    BundleError,
    SandboxError,
    ReportError,
    APIError,
    ServiceError,
    OSError,
    ValueError,
)


def _message(error: BaseException) -> str:
    """Keep diagnostics on one line without dumping OCI request headers."""
    text = error.message if isinstance(error, ServiceError) else str(error)
    return " ".join(text.split()).replace("ERROR:", "").strip() or type(error).__name__


class _Progress:
    """Rewrite terminal progress or throttle repeated states in redirected output.

    Args:
        stream: Destination console stream.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._terminal = stream.isatty()
        self._last_state: str | None = None
        self._last_output = float("-inf")
        self._active = False

    def __call__(self, message: str) -> None:
        """Display an update immediately on state change or after the log interval.

        Args:
            message: Sandbox callback text, such as CREATING, 6 s.
        """
        if self._terminal:
            print(f"\r{INDENT}{message}\033[K", end="", file=self._stream, flush=True)
            self._active = True
            return
        state = message.partition(",")[0].strip()
        now = monotonic()
        if (
            state != self._last_state
            or now - self._last_output >= PROGRESS_INTERVAL_SECONDS
        ):
            print(f"{INDENT}{message}", file=self._stream, flush=True)
            self._last_state = state
            self._last_output = now

    def close(self) -> None:
        """Finish an active terminal progress line, including on exceptional exits."""
        if self._active:
            print(file=self._stream, flush=True)
            self._active = False


class ConsoleView:
    """Draw the banner, the steps and the result panel of a triage run.

    Colors are used only on a terminal, and never when NO_COLOR is set, so
    redirected output stays plain text.

    Args:
        stream: Destination console stream.
    """

    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    DIM = "\033[2m"
    RESET = "\033[0m"
    CONFIDENCE_COLORS = {"high": GREEN, "medium": YELLOW, "low": RED}

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._color = stream.isatty() and "NO_COLOR" not in os.environ

    def banner(self, bundle: Path, model_id: str, max_steps: int) -> None:
        """Print the title box that opens every run.

        Args:
            bundle: Bundle being investigated.
            model_id: Model that runs the investigation.
            max_steps: Step budget of the agent.
        """
        rows = [
            self._row("SUPPORT BUNDLE TRIAGE AGENT"),
            self._row("Oracle LiveLabs · Triage run"),
            self._row(""),
            self._row(f"Bundle  {bundle.name}"),
            self._row(f"Model   {model_id} · max {max_steps} steps"),
        ]
        self._write("")
        self._box("", rows)
        self._write("")

    def step(self, number: int, title: str) -> None:
        """Print the header of a numbered step.

        Args:
            number: Step number, from 1 to 5.
            title: Short step name.
        """
        self._write(self._paint(f"▸ {number}. {title}", self.CYAN))

    def info(self, text: str) -> None:
        """Print a plain line inside the current step."""
        self._write(f"{INDENT}{text}")

    def done(self, text: str) -> None:
        """Print a completed action inside the current step."""
        self._write(f"{INDENT}{self._paint('✔', self.GREEN)} {text}")

    def event(self, text: str) -> None:
        """Print an agent event, such as a tool call."""
        self._write(self._paint(f"{INDENT}· {text}", self.DIM))

    def warning(self, text: str) -> None:
        """Print a warning on one line."""
        self._write(self._paint(f"⚠ WARNING: {text}", self.YELLOW))

    def error(self, text: str) -> None:
        """Print an error on one line."""
        self._write(self._paint(f"✘ ERROR: {text}", self.RED))

    def progress(self) -> "_Progress":
        """Return a progress callback for a long sandbox operation."""
        return _Progress(self._stream)

    def result(self, report: TriageReport) -> None:
        """Print the panel with summary, root cause, confidence and evidence.

        Args:
            report: Checked report of the run.
        """
        width = BOX_WIDTH - 6 - len(INDENT)
        verified = sum(item.verified is True for item in report.evidence)
        confidence = self._paint(
            report.confidence, self.CONFIDENCE_COLORS.get(report.confidence, "")
        )
        tail = f" · Evidence verified: {verified}/{len(report.evidence)}"
        rows = [self._row("Summary:")]
        rows += [
            self._row(INDENT + line) for line in textwrap.wrap(report.summary, width)
        ]
        rows += [self._row(""), self._row("Root cause:")]
        rows += [
            self._row(INDENT + line) for line in textwrap.wrap(report.root_cause, width)
        ]
        rows += [
            self._row(""),
            self._row(
                f"Confidence: {report.confidence}{tail}",
                f"Confidence: {confidence}{tail}",
            ),
        ]
        self._write("")
        self._box("Triage result", rows)

    def _row(self, text: str, painted: str | None = None) -> tuple[str, str]:
        """Fit a row to the box, cutting it with an ellipsis when too long.

        Returns:
            The row as plain text and as text to print (maybe colored).
        """
        inner = BOX_WIDTH - 6  # two spaces of margin on each side
        if len(text) > inner:
            text, painted = text[: inner - 1] + "…", None
        padding = " " * (inner - len(text))
        return text, (painted or text) + padding

    def _box(self, title: str, rows: list[tuple[str, str]]) -> None:
        """Print rows inside a rounded box of BOX_WIDTH characters."""
        label = f"─ {title} " if title else ""
        top = f"╭{label}{'─' * (BOX_WIDTH - 2 - len(label))}╮"
        bottom = f"╰{'─' * (BOX_WIDTH - 2)}╯"
        side = self._paint("│", self.CYAN)
        self._write(self._paint(top, self.CYAN))
        for _, printed in rows:
            self._write(f"{side}  {printed}  {side}")
        self._write(self._paint(bottom, self.CYAN))

    def _paint(self, text: str, color: str) -> str:
        """Color text when colors are enabled."""
        return f"{color}{text}{self.RESET}" if self._color and color else text

    def _write(self, text: str) -> None:
        """Flush each line, so long operations remain visible."""
        print(text, file=self._stream, flush=True)


@dataclass(frozen=True)
class _RunOptions:
    """Arguments shared by the investigation and lifecycle helpers."""

    bundle: Path
    sandbox_id: str | None
    keep: bool
    snapshot: bool
    max_steps: int
    task: str


class TriageApp:
    """Coordinate an investigation with injected sandbox and model dependencies.

    Args:
        settings: Application settings, validated before allocating resources.
        manager: Manager used to create or attach sandbox sessions.
        model_client: Model client shared by the agent and report writer.
        output_dir: Local destination for Markdown and JSON reports.
        stream: Destination for numbered steps, progress, findings and errors.
    """

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        settings: Settings,
        manager: SandboxManager,
        model_client: OpenAI,
        output_dir: Path,
        stream: TextIO,
    ) -> None:
        self._settings = settings
        self._manager = manager
        self._model_client = model_client
        self._output_dir = Path(output_dir)
        self._view = ConsoleView(stream)

    def run(  # pylint: disable=too-many-arguments
        self,
        bundle: Path,
        *,
        sandbox_id: str | None = None,
        keep: bool = False,
        snapshot: bool = False,
        max_steps: int = DEFAULT_MAX_STEPS,
        task: str = DEFAULT_TASK,
    ) -> Path:
        """Investigate a bundle and save reports, cleaning up on every exit path.

        Args:
            bundle: Local zip, extracted only in the sandbox.
            sandbox_id: Existing sandbox to attach and preserve.
            keep: Preserve a sandbox created by this run and print its reuse command.
            snapshot: Take a snapshot before cleanup, including after failures.
            max_steps: Maximum number of tool-requesting model calls.
            task: Investigation request sent to the agent.

        Returns:
            Markdown report path, even if sandbox closure needs manual completion.

        Raises:
            BundleError: The bundle is missing, unsafe or cannot be extracted.
            SandboxError: Sandbox creation, commands or snapshot failed.
            ReportError: The model report is not valid structured JSON.
            openai.APIError: The model request failed.
            ValueError: Settings or the step budget are invalid.
            KeyboardInterrupt: The run was interrupted, after cleanup.
        """
        try:
            self._view.banner(Path(bundle), self._settings.model_id, max_steps)
            problems = self._settings.validate()
            if problems:
                raise ValueError("Invalid settings: " + "; ".join(problems))
            valid_budget = isinstance(max_steps, int) and not isinstance(
                max_steps, bool
            )
            if not valid_budget or max_steps < 1:
                raise ValueError("max_steps must be a positive integer")
            options = _RunOptions(
                Path(bundle), sandbox_id, keep, snapshot, max_steps, task
            )
            return self._run(options)
        except _EXPECTED_ERRORS as error:
            self._view.error(_message(error))
            raise
        except KeyboardInterrupt:
            self._view.error("Interrupted by the user.")
            raise

    def _run(self, options: _RunOptions) -> Path:
        """Acquire a session and preserve any original error during cleanup."""
        view = self._view
        view.step(1, "Sandbox")
        if options.sandbox_id is not None:
            view.info(f"Attaching to sandbox {options.sandbox_id}.")
            session = self._manager.attach(options.sandbox_id)
        else:
            view.info("Creating sandbox: a cold start can take minutes.")
            with closing(view.progress()) as progress:
                session = self._manager.create(
                    display_name=f"triage-{options.bundle.stem}", on_progress=progress
                )
        view.done(f"Sandbox {session.sandbox_id}")
        try:
            path, report = self._investigate(session, options)
        finally:
            original_error = sys.exception()
            try:
                self._cleanup(session, options)
            except _EXPECTED_ERRORS as cleanup_error:
                if original_error is None:
                    raise
                original_error.add_note(
                    f"Cleanup also failed: {_message(cleanup_error)}"
                )
                view.warning(f"Cleanup also failed: {_message(cleanup_error)}")
        view.result(report)
        return path

    def _investigate(
        self, session: SandboxSession, options: _RunOptions
    ) -> tuple[Path, TriageReport]:
        """Upload, investigate, request structured findings and check citations."""
        view = self._view
        view.step(2, "Bundle")
        view.info("Uploading and extracting the bundle in the sandbox.")
        summary = BundleUploader(session).upload(options.bundle)
        view.done(summary.describe())
        view.step(3, "Investigation")
        view.info("Waiting for the model and its tools.")
        tools = ToolRegistry(session)
        agent = TriageAgent(
            self._model_client,
            self._settings.model_id,
            tools,
            max_steps=options.max_steps,
            on_event=view.event,
        )
        result = agent.run(options.task)
        view.done(
            f"{result.stopped_reason}: {result.steps} steps, "
            f"{len(result.tool_calls)} tool calls"
        )
        view.step(4, "Report")
        view.info("Requesting the structured report and checking the cited lines.")
        report = ReportWriter(self._model_client, self._settings.model_id).write(result)
        report = EvidenceChecker(tools).check(report)
        path = self._save(report, result, session.sandbox_id, options.bundle)
        return path, report

    def _save(
        self, report: TriageReport, result: AgentResult, sandbox_id: str, bundle: Path
    ) -> Path:
        """Write both artifacts, then print findings and verification totals."""
        metadata = {
            "bundle": bundle.name,
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": self._settings.model_id,
            "sandbox": sandbox_id,
            "steps": str(result.steps),
            "tool_calls": str(len(result.tool_calls)),
            "tokens": (
                f"{result.input_tokens} input / {result.output_tokens} output "
                "(investigation)"
            ),
        }
        self._output_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = self._output_dir / f"{bundle.stem}-report.md"
        json_path = self._output_dir / f"{bundle.stem}-report.json"
        markdown_path.write_text(render_markdown(report, metadata), encoding="utf-8")
        json_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        verified = sum(item.verified is True for item in report.evidence)
        self._view.done(
            f"Evidence verified: {verified}/{len(report.evidence)} (first cited lines)"
        )
        self._view.info(f"Markdown  {markdown_path}")
        self._view.info(f"JSON      {json_path}")
        return markdown_path

    def _cleanup(self, session: SandboxSession, options: _RunOptions) -> None:
        """Snapshot if requested, then respect session ownership and keep policy."""
        view = self._view
        view.step(5, "Cleanup")
        try:
            if options.snapshot:
                view.info("Taking a snapshot: waiting for it to become active.")
                view.done(f"Snapshot: {session.snapshot()}")
        finally:
            if options.sandbox_id is not None:
                view.done(f"Attached sandbox preserved: {session.sandbox_id}")
            elif options.keep:
                view.done(f"Sandbox kept: {session.sandbox_id}")
                view.info(
                    f"Reuse: python -m triage.cli {shlex.quote(str(options.bundle))} "
                    f"--sandbox-id {shlex.quote(session.sandbox_id)}"
                )
            else:
                self._close_owned(session)

    def _close_owned(self, session: SandboxSession) -> None:
        """Treat an unfinished sandbox close as a warning, preserving the report."""
        self._view.info(f"Closing sandbox {session.sandbox_id}.")
        try:
            with closing(self._view.progress()) as progress:
                session.close(on_progress=progress)
        except SandboxError as error:
            self._view.warning(
                f"Cleanup of sandbox {session.sandbox_id} is unfinished: "
                f"{_message(error)}; delete it from the OCI Console once it is STOPPED."
            )
        else:
            self._view.done("Sandbox closed")


def _positive_int(value: str) -> int:
    """Parse a positive CLI step budget."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _parser() -> argparse.ArgumentParser:
    """Describe the workshop's supported CLI options."""
    parser = argparse.ArgumentParser(
        description="Triage a support bundle in an OCI Sandbox."
    )
    parser.add_argument("bundle", type=Path, metavar="BUNDLE")
    parser.add_argument("--sandbox-id", metavar="ID")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--max-steps", type=_positive_int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main() -> int:
    """Parse arguments, validate settings and run the application.

    Returns:
        Zero on success (including slow closure), one on expected errors, or 130
        after Ctrl+C. Argument parsing uses argparse's standard exit handling.
    """
    arguments = _parser().parse_args()
    view = ConsoleView(sys.stdout)
    try:
        settings = Settings.load()
        problems = settings.validate()
        if problems:
            for problem in problems:
                view.error(_message(ValueError(problem)))
            return 1
        manager = SandboxManager.from_settings(settings, OciClients(settings))
        model_client = ModelClientFactory(settings).create()
    except _EXPECTED_ERRORS as error:
        view.error(_message(error))
        return 1
    except KeyboardInterrupt:
        view.error("Interrupted by the user.")
        return 130
    try:
        TriageApp(
            settings, manager, model_client, arguments.output_dir, sys.stdout
        ).run(
            arguments.bundle,
            sandbox_id=arguments.sandbox_id,
            keep=arguments.keep,
            snapshot=arguments.snapshot,
            max_steps=arguments.max_steps,
            task=arguments.task,
        )
    except _EXPECTED_ERRORS:
        return 1  # TriageApp has already printed the error once.
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
