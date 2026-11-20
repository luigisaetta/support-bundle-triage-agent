"""
Author: L. Saetta
Date Last Modified: 2026-11-20
License: MIT
Description: Runs the preflight checks in order and prints a report.

    - Each check is numbered ("[7/11]") and announced before it runs, with
      what it is doing, so the user knows what is being waited for.
    - Long checks send status messages (for example "CREATING, 6 s"). In a
      terminal the line is updated in place; otherwise a status line is
      printed at most every PLAIN_PROGRESS_SECONDS.
    - After a blocking failure, the remaining checks are skipped, except
      those marked always_run (the cleanup), which run in every case, also
      when the run is interrupted with Ctrl+C.
    - An unexpected exception in a check becomes a blocking failure.
"""

import os
import shutil
import sys
import time
from collections.abc import Sequence
from typing import TextIO

from triage.preflight.base import Check, CheckResult, PreflightContext, Status

#: Seconds between two status lines when the output is not a terminal.
PLAIN_PROGRESS_SECONDS = 30.0


class ConsoleReporter:
    """Prints the progress and the results of the checks.

    In a terminal, the line of the running check is rewritten as status
    messages arrive and replaced by the result. Colors are used in a
    terminal, unless the ``NO_COLOR`` environment variable is set.

    Args:
        total: The number of checks, for the "[n/total]" counter.
        stream: Where to print; standard output by default.
    """

    SYMBOLS = {Status.PASS: "✔", Status.WARN: "!", Status.FAIL: "✘", Status.SKIP: "–"}
    RUNNING_SYMBOL = "…"
    COLORS = {
        Status.PASS: "\033[32m",
        Status.WARN: "\033[33m",
        Status.FAIL: "\033[31m",
        Status.SKIP: "\033[2m",
    }
    RESET = "\033[0m"
    CLEAR_LINE = "\r\033[K"
    NAME_WIDTH = 18
    BANNER_WIDTH = 64
    BANNER_COLOR = "\033[36m"

    def __init__(self, total: int, stream: TextIO | None = None) -> None:
        self._total = total
        self._stream = stream or sys.stdout
        self._live = self._stream.isatty()
        self._color = self._live and "NO_COLOR" not in os.environ
        self._step = 0
        self._running: Check | None = None
        self._last_plain_progress = 0.0

    def banner(self, quick: bool = False) -> None:
        """Print the workshop title and the selected preflight mode.

        Args:
            quick: Show that sandbox checks are skipped in quick mode.
        """
        mode = "QUICK MODE" if quick else "FULL MODE"
        scope = "No sandbox created" if quick else "Includes sandbox validation"
        content = [
            "SUPPORT BUNDLE TRIAGE AGENT",
            "Oracle LiveLabs · Environment preflight",
            "",
            f"{mode} · {self._total} checks · {scope}",
        ]
        border = "─" * (self.BANNER_WIDTH - 2)
        lines = [f"╭{border}╮"]
        lines.extend(f"│  {text:<{self.BANNER_WIDTH - 4}}│" for text in content)
        lines.append(f"╰{border}╯")
        banner = "\n".join(lines)
        if self._color:
            banner = f"{self.BANNER_COLOR}{banner}{self.RESET}"
        self._write(f"\n{banner}\n\n")

    # ------------------------------------------------------------ check events

    def start(self, check: Check) -> None:
        """Announce a check that is about to run.

        Args:
            check: The check.
        """
        self._step += 1
        self._running = check
        self._last_plain_progress = time.monotonic()
        line = self._line(self.RUNNING_SYMBOL, check, f"{check.description}...")
        if self._live:
            self._write(self.CLEAR_LINE + self._fit(line))
        else:
            self._write(line + "\n")

    def progress(self, message: str) -> None:
        """Show a status message for the running check.

        In a terminal the line of the check is rewritten; otherwise a new
        line is printed, at most every PLAIN_PROGRESS_SECONDS.

        Args:
            message: A short status, for example "CREATING, 6 s".
        """
        if self._running is None:
            return
        check = self._running
        line = self._line(
            self.RUNNING_SYMBOL, check, f"{check.description}... {message}"
        )
        if self._live:
            self._write(self.CLEAR_LINE + self._fit(line))
        elif time.monotonic() - self._last_plain_progress >= PLAIN_PROGRESS_SECONDS:
            self._last_plain_progress = time.monotonic()
            self._write(line + "\n")

    def result(self, check: Check, result: CheckResult) -> None:
        """Print the outcome of a check, and its hint if any.

        Args:
            check: The check that ran (or was skipped).
            result: Its outcome.
        """
        if self._running is not check:
            self._step += 1  # a skipped check: it was never started
        self._running = None
        symbol = self._paint(result.status, self.SYMBOLS[result.status])
        prefix = self.CLEAR_LINE if self._live else ""
        self._write(prefix + self._line(symbol, check, result.detail) + "\n")
        if result.hint:
            self._write(f"{' ' * 10}→ {result.hint}\n")

    def summary(self, results: Sequence[CheckResult]) -> None:
        """Print the closing line of the report.

        Args:
            results: The outcomes of all checks.
        """
        failures = sum(result.status is Status.FAIL for result in results)
        warnings = sum(result.status is Status.WARN for result in results)
        if failures:
            status = Status.FAIL
            text = f"{failures} check(s) failed. Fix them and run the preflight again."
        elif warnings:
            status = Status.WARN
            text = f"All checks passed, with {warnings} warning(s). Ready for Lab 1."
        else:
            status, text = Status.PASS, "All checks passed. You are ready for Lab 1."
        self._write("\n" + self._paint(status, text) + "\n")

    # ----------------------------------------------------------------- helpers

    def _line(self, symbol: str, check: Check, text: str) -> str:
        """One report line: symbol, step counter, check name and text."""
        width = len(str(self._total))
        counter = f"[{self._step:>{width}}/{self._total}]"
        return f" {symbol} {counter} {check.name:<{self.NAME_WIDTH}} {text}"

    def _fit(self, line: str) -> str:
        """Cut a line to the terminal width, so it can be rewritten in place."""
        columns = shutil.get_terminal_size().columns
        return line if len(line) < columns else line[: columns - 2] + "…"

    def _paint(self, status: Status, text: str) -> str:
        """``text`` in the color of ``status``, when colors are on."""
        return f"{self.COLORS[status]}{text}{self.RESET}" if self._color else text

    def _write(self, text: str) -> None:
        """Write to the stream and flush, so progress appears at once."""
        self._stream.write(text)
        self._stream.flush()


class PreflightRunner:
    """Runs a sequence of checks against one context.

    Args:
        checks: The checks, in the order they run.
        reporter: Announces each check and prints its result.
    """

    def __init__(self, checks: Sequence[Check], reporter: ConsoleReporter) -> None:
        self._checks = list(checks)
        self._reporter = reporter

    def run(self, context: PreflightContext) -> list[CheckResult]:
        """Run the checks and print the report.

        Args:
            context: State shared by the checks.

        Returns:
            The outcome of every check, in order.
        """
        context.progress = self._reporter.progress
        results: list[CheckResult] = []
        blocked = False
        try:
            for check in self._checks:
                if blocked and not check.always_run:
                    result = CheckResult.skipped("skipped: an earlier check failed")
                else:
                    result = self._run_one(check, context)
                    blocked = blocked or (
                        result.status is Status.FAIL and result.blocking
                    )
                results.append(result)
                self._reporter.result(check, result)
        finally:
            # Interrupted: still run the cleanup checks that have not run yet.
            for check in self._checks[len(results) :]:
                if check.always_run:
                    self._reporter.result(check, self._run_one(check, context))
        self._reporter.summary(results)
        return results

    def _run_one(self, check: Check, context: PreflightContext) -> CheckResult:
        """Announce and run one check; an unexpected exception is a blocking failure."""
        self._reporter.start(check)
        try:
            return check.run(context)
        except Exception as error:  # pylint: disable=broad-exception-caught
            return CheckResult.failed(
                f"unexpected error: {type(error).__name__}: {error}"
            )
