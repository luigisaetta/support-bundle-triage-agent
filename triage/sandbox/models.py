"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Value objects and errors of the sandbox package.
"""

from dataclasses import dataclass


class SandboxError(RuntimeError):
    """A sandbox could not be created, used or removed."""


@dataclass(frozen=True)
class CommandResult:
    """The outcome of one command run in a sandbox.

    Attributes:
        command: The command line, as requested by the caller.
        exit_code: The exit status; None when the command did not finish.
        output: Standard output and standard error, interleaved in the order
            the command wrote them. Long output keeps only its head and tail.
        duration_seconds: Wall-clock time of the call, seen from the client.
        timed_out: True when the command was stopped at its deadline.
        truncated: True when part of the output was dropped.
    """

    command: str
    exit_code: int | None
    output: str
    duration_seconds: float
    timed_out: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        """True when the command finished with exit status 0."""
        return self.exit_code == 0
