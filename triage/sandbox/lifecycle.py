"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Waits on and ends the lifecycle of sandboxes and snapshots.
    Shared by SandboxManager (which creates sandboxes) and SandboxSession
    (which removes the sandbox it owns), so the polling and cleanup rules
    live in one place.

    Sandbox states: CREATING, RUNNING, UPDATING, SUSPENDING, SUSPENDED,
    RESUMING, STOPPING, STOPPED, FAILED, DELETED.
    Snapshot states: CREATING, ACTIVE, FAILED, DELETING, DELETED.
"""

import time
from typing import Any

from oci.exceptions import ServiceError
from oci.generative_ai_sandbox import models

from triage.sandbox.models import SandboxError

#: Sandbox states from which a sandbox never becomes RUNNING again.
TERMINAL_STATES = frozenset({"FAILED", "STOPPING", "STOPPED", "DELETED"})


class SandboxLifecycle:
    """Polls sandbox and snapshot states and removes sandboxes.

    Args:
        client: A ``GenerativeAiSandboxClient``.
        poll_seconds: Seconds between two state checks.
    """

    def __init__(self, client: Any, poll_seconds: float = 2.0) -> None:
        self._client = client
        self._poll_seconds = poll_seconds

    def state(self, sandbox_id: str) -> str:
        """The current lifecycle state of a sandbox, in upper case.

        Args:
            sandbox_id: OCID of the sandbox.
        """
        return str(self._client.get_sandbox(sandbox_id).data.lifecycle_state).upper()

    def wait_until_running(self, sandbox_id: str, timeout_seconds: float) -> None:
        """Block until the sandbox is RUNNING.

        Args:
            sandbox_id: OCID of the sandbox.
            timeout_seconds: The longest time to wait.

        Raises:
            SandboxError: The sandbox reached a terminal state, or did not
                run within ``timeout_seconds``.
        """
        deadline = time.monotonic() + timeout_seconds
        state = "UNKNOWN"
        while time.monotonic() < deadline:
            state = self.state(sandbox_id)
            if state == "RUNNING":
                return
            if state in TERMINAL_STATES:
                raise SandboxError(f"sandbox {sandbox_id} is {state}, not RUNNING")
            time.sleep(self._poll_seconds)
        raise SandboxError(
            f"sandbox {sandbox_id} not RUNNING after {timeout_seconds:g}s "
            f"(last state: {state})"
        )

    def wait_until_snapshot_active(
        self, snapshot_id: str, timeout_seconds: float
    ) -> None:
        """Block until the snapshot is ACTIVE, that is, usable for a restore.

        Args:
            snapshot_id: OCID of the snapshot.
            timeout_seconds: The longest time to wait.

        Raises:
            SandboxError: The snapshot failed, or was not ACTIVE in time.
        """
        deadline = time.monotonic() + timeout_seconds
        state = "UNKNOWN"
        while time.monotonic() < deadline:
            snapshot = self._client.get_sandbox_snapshot(snapshot_id).data
            state = str(snapshot.lifecycle_state).upper()
            if state == "ACTIVE":
                return
            if state in {"FAILED", "DELETING", "DELETED"}:
                raise SandboxError(f"snapshot {snapshot_id} is {state}")
            time.sleep(self._poll_seconds)
        raise SandboxError(
            f"snapshot {snapshot_id} not ACTIVE after {timeout_seconds:g}s "
            f"(last state: {state})"
        )

    def stop_and_delete(
        self, sandbox_id: str, stop_timeout_seconds: float = 120.0
    ) -> None:
        """Stop the sandbox, wait for STOPPED, then delete it.

        A sandbox that is already gone is not an error. Files in the
        workspace are lost: download them or take a snapshot first.

        Args:
            sandbox_id: OCID of the sandbox.
            stop_timeout_seconds: The longest time to wait for STOPPED before
                deleting anyway.

        Raises:
            oci.exceptions.ServiceError: The service refused a call (other
                than 404 Not Found).
        """
        try:
            state = self.state(sandbox_id)
            if state == "DELETED":
                return
            if state not in {"STOPPING", "STOPPED"}:
                self._client.stop_sandbox(
                    sandbox_id, models.StopSandboxDetails(is_force=False)
                )
            deadline = time.monotonic() + stop_timeout_seconds
            while state != "STOPPED" and time.monotonic() < deadline:
                time.sleep(self._poll_seconds)
                state = self.state(sandbox_id)
            self._client.delete_sandbox(sandbox_id)
        except ServiceError as error:
            if error.status != 404:
                raise
