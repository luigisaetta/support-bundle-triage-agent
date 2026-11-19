"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Building blocks of the preflight check: the result of one check,
    the context shared by all checks, and the Check base class that every
    concrete check extends.

    This module imports only the standard library at load time, so the local
    environment check can run (and explain the problem) even when a required
    package is missing. The application modules are imported on first use.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from triage.config import Settings
    from triage.oci_clients import OciClients
    from triage.sandbox import SandboxManager, SandboxSession


class Status(str, Enum):
    """The outcome of one check."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one check, with what to do about it.

    Attributes:
        status: Pass, warning, failure or skipped.
        detail: What was observed, in one line.
        hint: How to fix a problem; empty when there is none.
        blocking: For a failure, True when the following checks cannot run.
    """

    status: Status
    detail: str
    hint: str = ""
    blocking: bool = False

    @classmethod
    def passed(cls, detail: str) -> "CheckResult":
        """A successful check."""
        return cls(Status.PASS, detail)

    @classmethod
    def warning(cls, detail: str, hint: str = "") -> "CheckResult":
        """A check that passed with something worth knowing."""
        return cls(Status.WARN, detail, hint)

    @classmethod
    def failed(
        cls, detail: str, hint: str = "", blocking: bool = True
    ) -> "CheckResult":
        """A failed check; by default the following checks are skipped."""
        return cls(Status.FAIL, detail, hint, blocking)

    @classmethod
    def skipped(cls, detail: str) -> "CheckResult":
        """A check that did not run."""
        return cls(Status.SKIP, detail)


@dataclass
class PreflightContext:
    """State shared by the checks of one preflight run.

    Checks read the settings and the clients, and pass results to later
    checks through this object (for example, the sandbox created by one
    check is used by the next ones and removed by the last one).

    Attributes:
        settings: The application settings.
        keep_sandbox: Leave the test sandbox running at the end.
        session: The test sandbox, once created.
        project: The Generative AI project, once read.
    """

    settings: "Settings"
    keep_sandbox: bool = False
    session: "SandboxSession | None" = None
    project: Any = None

    @cached_property
    def clients(self) -> "OciClients":
        """The OCI SDK clients, built on first use."""
        from triage.oci_clients import (  # pylint: disable=import-outside-toplevel
            OciClients,
        )

        return OciClients(self.settings)

    @cached_property
    def manager(self) -> "SandboxManager":
        """The sandbox manager for the configured project."""
        from triage.sandbox import (  # pylint: disable=import-outside-toplevel
            SandboxManager,
        )

        return SandboxManager.from_settings(self.settings, self.clients)


class Check(ABC):
    """One preflight check.

    Subclasses set :attr:`name` and implement :meth:`run`. A check with
    :attr:`always_run` runs even after a blocking failure (used for cleanup).
    """

    #: The label shown in the report.
    name: str = ""

    #: Run even when an earlier check failed with a blocking failure.
    always_run: bool = False

    @abstractmethod
    def run(self, context: PreflightContext) -> CheckResult:
        """Run the check.

        Args:
            context: State shared by the checks of this run.

        Returns:
            The outcome. Unexpected exceptions are turned into blocking
            failures by the runner.
        """
