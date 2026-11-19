"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: SandboxManager creates OCI Sandboxes in a Generative AI project
    and hands back a SandboxSession for each one. Three ways to get a session:

    - create(): a new, empty sandbox (one per ticket).
    - restore(): a new sandbox seeded from a snapshot (reopen a ticket).
    - attach(): a sandbox that already exists; it is never deleted.

    Every new sandbox has a lifetime: if the application crashes, the
    service still ends it. If anything fails after a sandbox exists, it is
    stopped and deleted before the error reaches the caller.

Usage:

    manager = SandboxManager.from_settings(settings, OciClients(settings))
    with manager.create(display_name="ticket-123") as session:
        print(session.run("uname -a").output)
"""

import uuid
from typing import Any

from oci.generative_ai_sandbox import models

from triage.config import Settings
from triage.oci_clients import OciClients
from triage.sandbox.lifecycle import SandboxLifecycle
from triage.sandbox.session import SandboxSession

#: Seconds to wait for a new sandbox to reach RUNNING (cold start is ~10 s).
READY_TIMEOUT_SECONDS = 300.0


class SandboxManager:
    """Creates sandboxes in one project, with fixed runtime, shape and lifetime.

    Args:
        client: A ``GenerativeAiSandboxClient``.
        project_id: OCID of a Generative AI project with sandboxes enabled.
        runtime: Runtime of new sandboxes (``python-3.11`` or an OCIR image).
        shape: Compute shape: ``SMALL``, ``MEDIUM`` or ``LARGE``.
        lifetime_minutes: Lifetime of new sandboxes (at most 48 hours).
    """

    def __init__(
        self,
        client: Any,
        project_id: str,
        *,
        runtime: str = "python-3.11",
        shape: str = "SMALL",
        lifetime_minutes: int = 60,
    ) -> None:
        self._client = client
        self._project_id = project_id
        self._runtime = runtime
        self._shape = shape
        self._lifetime_minutes = lifetime_minutes
        self._lifecycle = SandboxLifecycle(client)

    @classmethod
    def from_settings(cls, settings: Settings, clients: OciClients) -> "SandboxManager":
        """Build a manager from the application settings.

        Args:
            settings: The application settings.
            clients: The OCI clients built from the same settings.
        """
        return cls(
            clients.sandbox,
            settings.oci_project_id,
            runtime=settings.sandbox_runtime,
            shape=settings.sandbox_shape,
            lifetime_minutes=settings.sandbox_lifetime_minutes,
        )

    def create(
        self, display_name: str | None = None, lifetime_minutes: int | None = None
    ) -> SandboxSession:
        """Create a new sandbox and wait until it is RUNNING.

        Args:
            display_name: A name for the sandbox; generated when omitted.
            lifetime_minutes: Overrides the manager's default lifetime.

        Returns:
            A session that owns the sandbox: closing it deletes the sandbox.

        Raises:
            SandboxError: The sandbox failed or did not start in time.
            oci.exceptions.ServiceError: The service refused the request.
        """
        details = models.CreateSandboxDetails(
            display_name=display_name or _generated_name("triage"),
            runtime=self._runtime,
            shape=self._shape,
            expires_in_minutes=lifetime_minutes or self._lifetime_minutes,
        )
        return self._start(details)

    def restore(
        self,
        snapshot_id: str,
        display_name: str | None = None,
        lifetime_minutes: int | None = None,
    ) -> SandboxSession:
        """Create a new sandbox from a snapshot and wait until it is RUNNING.

        The workspace files of the snapshot are restored; processes are not.

        Args:
            snapshot_id: OCID of an ACTIVE snapshot.
            display_name: A name for the sandbox; generated when omitted.
            lifetime_minutes: Overrides the manager's default lifetime.

        Returns:
            A session that owns the new sandbox.

        Raises:
            SandboxError: The sandbox failed or did not start in time.
            oci.exceptions.ServiceError: The service refused the request.
        """
        details = models.CreateSandboxDetails(
            display_name=display_name or _generated_name("restored"),
            source=models.SandboxSnapshotSource(snapshot_id=snapshot_id),
            shape=self._shape,
            expires_in_minutes=lifetime_minutes or self._lifetime_minutes,
        )
        return self._start(details)

    def attach(self, sandbox_id: str) -> SandboxSession:
        """Get a session on a sandbox that already exists.

        The session does not own the sandbox: closing it leaves the sandbox
        running.

        Args:
            sandbox_id: OCID of a RUNNING sandbox.
        """
        return SandboxSession(self._client, sandbox_id, owns_sandbox=False)

    def list_running(self) -> list[Any]:
        """The RUNNING sandboxes of the project, for example leftovers.

        Returns:
            Sandbox summaries (id, display name, state, ...).
        """
        response = self._client.list_sandboxes(
            project_id=self._project_id, lifecycle_state="RUNNING"
        )
        return list(response.data.items)

    def _start(self, details: Any) -> SandboxSession:
        """Create a sandbox from ``details`` and wait until it is RUNNING."""
        sandbox_id = self._client.create_sandbox(self._project_id, details).data.id
        try:
            self._lifecycle.wait_until_running(sandbox_id, READY_TIMEOUT_SECONDS)
        except BaseException:
            # Whatever failed, the sandbox must not outlive this call.
            self._lifecycle.stop_and_delete(sandbox_id)
            raise
        return SandboxSession(self._client, sandbox_id, owns_sandbox=True)


def _generated_name(prefix: str) -> str:
    """A unique, readable display name, for example ``triage-3f9c1a2b``."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
