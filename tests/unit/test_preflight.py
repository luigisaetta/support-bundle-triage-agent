"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Unit tests of the preflight runner and of the settings
    validation. No OCI or model call is made.
"""

# pylint: disable=missing-function-docstring

import io
from pathlib import Path

from triage.config import Settings
from triage.preflight.base import Check, CheckResult, PreflightContext, Status
from triage.preflight.runner import ConsoleReporter, PreflightRunner


class FixedCheck(Check):
    """A check that returns a fixed result and records that it ran."""

    def __init__(self, name, result, always_run=False, ran=None):
        self.name = name
        self.always_run = always_run
        self._result = result
        self._ran = ran if ran is not None else []

    def run(self, context):
        self._ran.append(self.name)
        return self._result


class BrokenCheck(Check):
    """A check that raises."""

    name = "broken"

    def run(self, context):
        raise RuntimeError("boom")


def _context(monkeypatch):
    for name in ("MODEL_ID", "OCI_REGION", "OCI_PROJECT_ID", "OCI_GENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return PreflightContext(
        settings=Settings.from_env(env_file=Path("/nonexistent/.env"))
    )


def test_blocking_failure_skips_later_checks_but_not_cleanup(monkeypatch):
    ran: list[str] = []
    checks = [
        FixedCheck("a", CheckResult.passed("ok"), ran=ran),
        FixedCheck("b", CheckResult.failed("bad"), ran=ran),
        FixedCheck("c", CheckResult.passed("ok"), ran=ran),
        FixedCheck("cleanup", CheckResult.passed("ok"), always_run=True, ran=ran),
    ]
    output = io.StringIO()
    results = PreflightRunner(checks, ConsoleReporter(output)).run(
        _context(monkeypatch)
    )
    assert ran == ["a", "b", "cleanup"]
    assert [r.status for r in results] == [
        Status.PASS,
        Status.FAIL,
        Status.SKIP,
        Status.PASS,
    ]
    assert "1 check(s) failed" in output.getvalue()


def test_non_blocking_failure_lets_later_checks_run(monkeypatch):
    ran: list[str] = []
    checks = [
        FixedCheck("a", CheckResult.failed("bad", blocking=False), ran=ran),
        FixedCheck("b", CheckResult.passed("ok"), ran=ran),
    ]
    PreflightRunner(checks, ConsoleReporter(io.StringIO())).run(_context(monkeypatch))
    assert ran == ["a", "b"]


def test_unexpected_exception_becomes_a_blocking_failure(monkeypatch):
    results = PreflightRunner(
        [BrokenCheck(), FixedCheck("next", CheckResult.passed("ok"))],
        ConsoleReporter(io.StringIO()),
    ).run(_context(monkeypatch))
    assert results[0].status is Status.FAIL and "boom" in results[0].detail
    assert results[1].status is Status.SKIP


def test_settings_validation_lists_missing_values(monkeypatch):
    problems = _context(monkeypatch).settings.validate()
    assert "MODEL_ID is not set" in problems
    assert "OCI_PROJECT_ID is not set" in problems
    assert "OCI_GENAI_API_KEY is not set" in problems
