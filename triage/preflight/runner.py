"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Runs the preflight checks in order and prints a report.

    - After a blocking failure, the remaining checks are skipped, except
      those marked always_run (the cleanup), which run in every case, also
      when the run is interrupted with Ctrl+C.
    - An unexpected exception in a check becomes a blocking failure.
    - Each result is printed as soon as it is known, since creating a
      sandbox takes several seconds.
"""

import os
import sys
from collections.abc import Sequence
from typing import TextIO

from triage.preflight.base import Check, CheckResult, PreflightContext, Status


class ConsoleReporter:
    """Prints check results, with colors when the output is a terminal.

    Colors are off when the ``NO_COLOR`` environment variable is set.

    Args:
        stream: Where to print; standard output by default.
    """

    SYMBOLS = {Status.PASS: "✔", Status.WARN: "!", Status.FAIL: "✘", Status.SKIP: "–"}
    COLORS = {
        Status.PASS: "\033[32m",
        Status.WARN: "\033[33m",
        Status.FAIL: "\033[31m",
        Status.SKIP: "\033[2m",
    }
    RESET = "\033[0m"
    NAME_WIDTH = 20

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout
        self._color = self._stream.isatty() and "NO_COLOR" not in os.environ

    def result(self, check: Check, result: CheckResult) -> None:
        """Print the outcome of one check, and its hint if any.

        Args:
            check: The check that ran.
            result: Its outcome.
        """
        symbol = self._paint(result.status, self.SYMBOLS[result.status])
        print(
            f" {symbol} {check.name:<{self.NAME_WIDTH}} {result.detail}",
            file=self._stream,
        )
        if result.hint:
            indent = " " * 3
            print(f"{indent}→ {result.hint}", file=self._stream)
        self._stream.flush()

    def summary(self, results: Sequence[CheckResult]) -> None:
        """Print the closing line of the report.

        Args:
            results: The outcomes of all checks.
        """
        failures = sum(result.status is Status.FAIL for result in results)
        warnings = sum(result.status is Status.WARN for result in results)
        print(file=self._stream)
        if failures:
            text = f"{failures} check(s) failed. Fix them and run the preflight again."
            print(self._paint(Status.FAIL, text), file=self._stream)
        elif warnings:
            text = f"All checks passed, with {warnings} warning(s). You are ready for Lab 1."
            print(self._paint(Status.WARN, text), file=self._stream)
        else:
            print(
                self._paint(Status.PASS, "All checks passed. You are ready for Lab 1."),
                file=self._stream,
            )

    def _paint(self, status: Status, text: str) -> str:
        """``text`` in the color of ``status``, when colors are on."""
        return f"{self.COLORS[status]}{text}{self.RESET}" if self._color else text


class PreflightRunner:
    """Runs a sequence of checks against one context.

    Args:
        checks: The checks, in the order they run.
        reporter: Prints each result as it arrives.
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

    @staticmethod
    def _run_one(check: Check, context: PreflightContext) -> CheckResult:
        """Run one check; an unexpected exception becomes a blocking failure."""
        try:
            return check.run(context)
        except Exception as error:  # pylint: disable=broad-exception-caught
            return CheckResult.failed(
                f"unexpected error: {type(error).__name__}: {error}"
            )
