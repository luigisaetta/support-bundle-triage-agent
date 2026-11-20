"""
Author: L. Saetta
Date Last Modified: 2026-11-20
License: MIT
Description: Run sandbox investigations, save reports and manage resource cleanup.
"""

import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import sys
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
            print(f"\r{message}\033[K", end="", file=self._stream, flush=True)
            self._active = True
            return
        state = message.partition(",")[0].strip()
        now = monotonic()
        if (
            state != self._last_state
            or now - self._last_output >= PROGRESS_INTERVAL_SECONDS
        ):
            print(message, file=self._stream, flush=True)
            self._last_state = state
            self._last_output = now

    def close(self) -> None:
        """Finish an active terminal progress line, including on exceptional exits."""
        if self._active:
            print(file=self._stream, flush=True)
            self._active = False


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
        self._stream = stream

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
            self._print(f"ERROR: {_message(error)}")
            raise
        except KeyboardInterrupt:
            self._print("ERROR: Interrupted by the user.")
            raise

    def _run(self, options: _RunOptions) -> Path:
        """Acquire a session and preserve any original error during cleanup."""
        self._print("1. Sandbox")
        if options.sandbox_id is not None:
            self._print(f"Attaching to sandbox {options.sandbox_id}.")
            session = self._manager.attach(options.sandbox_id)
        else:
            self._print("Creating sandbox; a cold start can take minutes.")
            with closing(_Progress(self._stream)) as progress:
                session = self._manager.create(
                    display_name=f"triage-{options.bundle.stem}", on_progress=progress
                )
        try:
            return self._investigate(session, options)
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
                self._print(f"WARNING: Cleanup also failed: {_message(cleanup_error)}")

    def _investigate(self, session: SandboxSession, options: _RunOptions) -> Path:
        """Upload, investigate, request structured findings and check citations."""
        self._print("2. Bundle: uploading and extracting in the sandbox.")
        summary = BundleUploader(session).upload(options.bundle)
        self._print(summary.describe())
        self._print("3. Investigation: waiting for the model and its tools.")
        tools = ToolRegistry(session)
        agent = TriageAgent(
            self._model_client,
            self._settings.model_id,
            tools,
            max_steps=options.max_steps,
            on_event=self._print,
        )
        result = agent.run(options.task)
        self._print(
            "4. Report: requesting structured findings and checking cited lines."
        )
        report = ReportWriter(self._model_client, self._settings.model_id).write(result)
        report = EvidenceChecker(tools).check(report)
        return self._save(report, result, session.sandbox_id, options.bundle)

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
        self._print(f"Markdown: {markdown_path}")
        self._print(f"JSON: {json_path}")
        self._print(f"Root cause: {report.root_cause}")
        self._print(f"Confidence: {report.confidence}")
        verified = sum(item.verified is True for item in report.evidence)
        self._print(
            f"Evidence verified: {verified}/{len(report.evidence)} (first cited lines)"
        )
        return markdown_path

    def _cleanup(self, session: SandboxSession, options: _RunOptions) -> None:
        """Snapshot if requested, then respect session ownership and keep policy."""
        self._print("5. Cleanup")
        try:
            if options.snapshot:
                self._print("Taking snapshot; waiting for it to become active.")
                self._print(f"Snapshot: {session.snapshot()}")
        finally:
            if options.sandbox_id is not None:
                self._print(f"Attached sandbox preserved: {session.sandbox_id}")
            elif options.keep:
                self._print(f"Sandbox kept: {session.sandbox_id}")
                self._print(
                    f"Reuse: python -m triage.cli {shlex.quote(str(options.bundle))} "
                    f"--sandbox-id {shlex.quote(session.sandbox_id)}"
                )
            else:
                self._close_owned(session)

    def _close_owned(self, session: SandboxSession) -> None:
        """Treat an unfinished sandbox close as a warning, preserving the report."""
        self._print(f"Closing sandbox {session.sandbox_id}.")
        try:
            with closing(_Progress(self._stream)) as progress:
                session.close(on_progress=progress)
        except SandboxError as error:
            self._print(
                f"WARNING: Cleanup of sandbox {session.sandbox_id} is unfinished: "
                f"{_message(error)}; delete it from the OCI Console once it is STOPPED."
            )

    def _print(self, text: str) -> None:
        """Flush console messages immediately so long operations remain visible."""
        print(text, file=self._stream, flush=True)


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
    try:
        settings = Settings.load()
        problems = settings.validate()
        if problems:
            for problem in problems:
                print(f"ERROR: {_message(ValueError(problem))}", file=sys.stdout)
            return 1
        manager = SandboxManager.from_settings(settings, OciClients(settings))
        model_client = ModelClientFactory(settings).create()
    except _EXPECTED_ERRORS as error:
        print(f"ERROR: {_message(error)}", file=sys.stdout)
        return 1
    except KeyboardInterrupt:
        print("ERROR: Interrupted by the user.", file=sys.stdout)
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
