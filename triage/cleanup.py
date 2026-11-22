"""
Author: L. Saetta
Date Last Modified: 2026-11-22
License: MIT
Description: Finds and removes the sandboxes left over by the labs.

    A sandbox kept with --keep, or one whose stop took too long, stays in the
    project and uses quota: a STOPPED sandbox is never deleted by the
    service. This tool lists the sandboxes of the project and, with
    --delete, removes them without waiting:

    - RUNNING (or SUSPENDED, ...): a stop is requested;
    - STOPPED or FAILED: the sandbox is deleted;
    - CREATING, UPDATING or STOPPING: it is left alone for now.

    Stopping can take many minutes, so a sandbox that was RUNNING is deleted
    by a second run, once it is STOPPED.

    Every sandbox of the project is considered, whatever its name: the
    project is the one created for the labs (Lab 0), and the name of a
    sandbox depends on the code that created it (the CLI built with Codex
    chooses its own). The list is always shown before anything is removed.

Usage:

    python -m triage.cleanup                 # list the leftover sandboxes
    python -m triage.cleanup --delete        # remove them, after a confirmation
    python -m triage.cleanup --delete --yes  # remove them without asking
"""

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import sys
from typing import Any, TextIO

from oci.exceptions import ClientError, ServiceError

from triage.config import Settings
from triage.oci_clients import OciClients
from triage.sandbox import (
    DELETABLE_STATES,
    STOPPABLE_STATES,
    SandboxError,
    SandboxManager,
)


@dataclass(frozen=True)
class LeftoverSandbox:
    """A sandbox found in the project.

    Attributes:
        sandbox_id: Id of the sandbox.
        name: Display name.
        state: Lifecycle state, in upper case.
        created: Creation time, or None when the service does not say.
    """

    sandbox_id: str
    name: str
    state: str
    created: datetime | None

    @classmethod
    def from_summary(cls, summary: Any) -> "LeftoverSandbox":
        """Build it from a sandbox summary returned by the service.

        Args:
            summary: An item of ``list_sandboxes``.

        Returns:
            The sandbox, with the fields the cleanup needs.
        """
        return cls(
            sandbox_id=summary.id,
            name=summary.display_name or "",
            state=str(summary.lifecycle_state).upper(),
            created=getattr(summary, "time_created", None),
        )


@dataclass(frozen=True)
class CleanupSummary:
    """What one cleanup run did.

    Attributes:
        deleted: Sandboxes deleted.
        stopping: Sandboxes asked to stop: delete them with a second run.
        skipped: Sandboxes left alone (busy or failed requests).
    """

    deleted: int = 0
    stopping: int = 0
    skipped: int = 0


class SandboxCleaner:
    """Lists the leftover sandboxes of a project and removes them.

    Args:
        manager: The sandbox manager of the project.
        stream: Where the list and the results are printed.
    """

    def __init__(self, manager: SandboxManager, stream: TextIO) -> None:
        self._manager = manager
        self._stream = stream

    def find(self) -> list[LeftoverSandbox]:
        """The sandboxes of the project that are not DELETED.

        Returns:
            The sandboxes, oldest first.
        """
        sandboxes = [
            LeftoverSandbox.from_summary(item)
            for item in self._manager.list_sandboxes()
        ]
        return sorted(sandboxes, key=lambda item: item.created or datetime.min)

    def show(self, sandboxes: list[LeftoverSandbox]) -> None:
        """Print one line per sandbox: state, creation time, name and id.

        Args:
            sandboxes: The sandboxes to show.
        """
        if not sandboxes:
            self._print("No leftover sandboxes.")
            return
        self._print(f"{len(sandboxes)} sandbox(es):")
        for item in sandboxes:
            created = item.created.strftime("%Y-%m-%d %H:%M") if item.created else "-"
            self._print(
                f"  {item.state:<10} {created:<16}  {item.name or '-'}  "
                f"{item.sandbox_id}"
            )

    def clean(self, sandboxes: list[LeftoverSandbox]) -> CleanupSummary:
        """Delete the stopped sandboxes and ask the running ones to stop.

        Never waits and never raises for a single sandbox: a refused request
        is printed and the cleanup goes on with the others.

        Args:
            sandboxes: The sandboxes to remove.

        Returns:
            How many were deleted, are stopping, or were skipped.
        """
        counts = {"deleted": 0, "stopping": 0, "skipped": 0}
        for item in sandboxes:
            outcome = self._clean_one(item)
            counts[outcome] += 1
        summary = CleanupSummary(**counts)
        self._print(
            f"Deleted: {summary.deleted}, stopping: {summary.stopping}, "
            f"skipped: {summary.skipped}."
        )
        if summary.stopping:
            self._print(
                "Stopping can take several minutes: run this command again "
                "later to delete the sandboxes that are now STOPPING."
            )
        return summary

    def _clean_one(self, item: LeftoverSandbox) -> str:
        """Remove one sandbox as far as its state allows, without waiting."""
        label = item.name or item.sandbox_id
        try:
            if item.state in DELETABLE_STATES:
                self._manager.delete(item.sandbox_id)
                self._print(f"  deleted        {label}")
                return "deleted"
            if item.state in STOPPABLE_STATES:
                self._manager.request_stop(item.sandbox_id)
                self._print(f"  stop requested {label}")
                return "stopping"
        except ServiceError as error:
            self._print(f"  failed         {label}: HTTP {error.status} {error.code}")
            return "skipped"
        self._print(f"  skipped        {label}: {item.state}, retry later")
        return "skipped"

    def _print(self, text: str) -> None:
        """Print one line and flush it."""
        print(text, file=self._stream, flush=True)


def _parser() -> argparse.ArgumentParser:
    """The command line options."""
    parser = argparse.ArgumentParser(
        prog="python -m triage.cleanup",
        description="List and remove the sandboxes left over by the labs.",
    )
    parser.add_argument(
        "--delete", action="store_true", help="remove the sandboxes listed"
    )
    parser.add_argument(
        "--yes", action="store_true", help="do not ask for a confirmation"
    )
    return parser


def main(
    argv: list[str] | None = None,
    stream: TextIO | None = None,
    ask: Callable[[str], str] = input,
) -> int:
    """List the leftover sandboxes and, with --delete, remove them.

    Args:
        argv: Arguments; sys.argv[1:] by default.
        stream: Output stream; sys.stdout by default.
        ask: Reads the confirmation answer; ``input`` by default.

    Returns:
        0 on success, 1 on errors or when the user does not confirm.
    """
    args = _parser().parse_args(argv)
    stream = stream or sys.stdout
    try:
        settings = Settings.load()
        manager = SandboxManager.from_settings(settings, OciClients(settings))
        cleaner = SandboxCleaner(manager, stream)
        sandboxes = cleaner.find()
        cleaner.show(sandboxes)
        if not sandboxes:
            return 0
        if not args.delete:
            print("Run again with --delete to remove them.", file=stream)
            return 0
        if not args.yes:
            answer = ask(f"Remove these {len(sandboxes)} sandbox(es)? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                print("Nothing removed.", file=stream)
                return 1
        cleaner.clean(sandboxes)
    except (ServiceError, ClientError, SandboxError, ValueError, OSError) as error:
        message = error.message if isinstance(error, ServiceError) else str(error)
        print(f"ERROR: {' '.join(str(message).split())}", file=stream)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted: nothing else removed.", file=stream)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
