"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: OCI Sandboxes for the triage agent, in one module.

    - SandboxManager creates sandboxes in a Generative AI project: a new one
      (create), one seeded from a snapshot (restore), or a handle on one
      that already exists (attach).
    - SandboxSession is the handle on one running sandbox: run commands,
      move files, take snapshots, extend and remove it.

    The service has a few behaviors that are not obvious from the API. They
    were measured on the live service and shape this module:

    1. Only /workspace is writable. The root filesystem and /tmp are
       read-only, so the session keeps its own state in /workspace/.session.
    2. The service timeout does not stop a command: the call returns only
       when the command would have ended anyway. Every command therefore runs
       under "timeout" inside the sandbox, which kills it at the deadline.
       The service timeout is only a backstop, a little later.
    3. A command that prints more than about 1 MB returns no output at all.
       Output is written to a file inside the sandbox and only its head and
       tail come back.
    4. Streaming the command events is about three times faster than
       waiting for the result, so commands run through the stream API.
    5. A single file write accepts about 10 MB. Larger files are written in
       parts and joined inside the sandbox. Every write carries the SHA-256
       of its body, base64-encoded (hex is refused).
    6. A sandbox can be stopped only when RUNNING, SUSPENDING, SUSPENDED or
       RESUMING, and deleted only when STOPPED or FAILED. Cleanup therefore
       waits for each state change before the next request.
    7. Start and stop times vary a lot: a cold start took seconds in one
       project and more than five minutes in another; stopping can take
       minutes. Timeouts are generous and progress is reported while waiting.

    Sandbox states: CREATING, RUNNING, UPDATING, SUSPENDING, SUSPENDED,
    RESUMING, STOPPING, STOPPED, FAILED, DELETED.
    Snapshot states: CREATING, ACTIVE, FAILED, DELETING, DELETED.

Usage:

    manager = SandboxManager.from_settings(settings, OciClients(settings))
    with manager.create(display_name="ticket-123") as session:
        session.write_file("/workspace/hello.py", "print('hello')")
        result = session.run("python3 /workspace/hello.py", timeout_seconds=30)
        print(result.exit_code, result.output)
"""

import base64
import hashlib
import io
import json
import shlex
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from oci.exceptions import ServiceError
from oci.generative_ai_sandbox import models

from triage.config import Settings
from triage.oci_clients import OciClients

# ---------------------------------------------------------------- constants

#: The sandbox working directory: the only writable one.
WORKSPACE = "/workspace"

#: Where the session keeps captured command output, inside the sandbox.
STATE_DIR = f"{WORKSPACE}/.session"

#: The most output one command returns. The service loses all output a
#: little above 1 MB, so this stays well below it.
MAX_OUTPUT_BYTES = 900_000

#: Bytes kept from the start of a long output; the rest of the budget goes
#: to its end, where errors and summaries usually are.
HEAD_OUTPUT_BYTES = 100_000

#: Marker printed in place of the dropped middle of a long output.
TRUNCATION_MARKER = "bytes of output omitted"

#: Bytes per file write. The service accepted 10 MB and refused 50 MB.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024

#: Seconds between the stop signal and a kill, for a command past its deadline.
KILL_GRACE_SECONDS = 5

#: Seconds the service timeout runs past the in-sandbox deadline.
SERVICE_GRACE_SECONDS = 30

#: Exit status of "timeout" when it stops a command at the deadline.
TIMEOUT_EXIT_CODE = 124

#: Exit status of a command killed with SIGKILL (after the grace period).
KILLED_EXIT_CODE = 128 + 9

#: Default seconds to wait for a new sandbox to reach RUNNING. A cold start
#: took seconds in one project and more than five minutes in another.
READY_TIMEOUT_SECONDS = 600.0

#: Seconds to wait for a sandbox to stop. Stopping can take minutes.
STOP_TIMEOUT_SECONDS = 300.0

#: Seconds a failed start waits for the sandbox to leave CREATING, so it can
#: be stopped and deleted. Past this, the sandbox is left to expire.
FAILED_START_CLEANUP_SECONDS = 60.0

#: Seconds between two state checks.
POLL_SECONDS = 2.0

#: Sandbox states from which a sandbox never becomes RUNNING again.
TERMINAL_STATES = frozenset({"FAILED", "STOPPING", "STOPPED", "DELETED"})

#: Sandbox states in which the service accepts a stop request.
STOPPABLE_STATES = frozenset({"RUNNING", "SUSPENDING", "SUSPENDED", "RESUMING"})

#: Sandbox states that end on their own: wait before stopping.
TRANSITIONAL_STATES = frozenset({"CREATING", "UPDATING"})

#: Sandbox states in which the service accepts a delete request.
DELETABLE_STATES = frozenset({"STOPPED", "FAILED"})

#: Callback that receives short progress messages, such as "CREATING, 6 s".
ProgressCallback = Callable[[str], None]


# ----------------------------------------------------- value objects, errors


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


# ------------------------------------------------------------------ session


class SandboxSession:
    """One running OCI Sandbox.

    Create sessions with :class:`SandboxManager`. A session that owns its
    sandbox stops and deletes it on :meth:`close`, also when used as a
    context manager.

    Args:
        client: A ``GenerativeAiSandboxClient``.
        sandbox_id: Id of a RUNNING sandbox.
        owns_sandbox: Stop and delete the sandbox on :meth:`close`.
        max_output_bytes: The most output one command returns.
    """

    def __init__(
        self,
        client: Any,
        sandbox_id: str,
        *,
        owns_sandbox: bool = True,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> None:
        if max_output_bytes <= HEAD_OUTPUT_BYTES:
            raise ValueError(f"max_output_bytes must exceed {HEAD_OUTPUT_BYTES}")
        self._client = client
        self._sandbox_id = sandbox_id
        self._owns_sandbox = owns_sandbox
        self._max_output_bytes = max_output_bytes
        self._closed = False

    # ------------------------------------------------------------------ info

    @property
    def sandbox_id(self) -> str:
        """Id of the sandbox."""
        return self._sandbox_id

    @property
    def owns_sandbox(self) -> bool:
        """True when :meth:`close` stops and deletes the sandbox."""
        return self._owns_sandbox

    def state(self) -> str:
        """The current lifecycle state of the sandbox, for example ``RUNNING``."""
        return _sandbox_state(self._client, self._sandbox_id)

    def details(self) -> Any:
        """The sandbox resource as the service describes it.

        Returns:
            An ``oci.generative_ai_sandbox.models.Sandbox`` (runtime, shape,
            lifecycle state, expiry time, ...).
        """
        return self._client.get_sandbox(self._sandbox_id).data

    # -------------------------------------------------------------- commands

    def run(
        self,
        command: str,
        timeout_seconds: int = 60,
        on_output: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Run a shell command in the sandbox.

        The command runs with ``sh -c`` in ``/workspace``, without standard
        input, and is stopped at ``timeout_seconds``.

        Args:
            command: A POSIX shell command line.
            timeout_seconds: Seconds before the command is stopped.
            on_output: Optional callback, called with each chunk of output.

        Returns:
            The exit code, the (possibly truncated) output and the timing.

        Raises:
            SandboxError: The call failed before the deadline, for example
                because the sandbox is not RUNNING.
        """
        timeout_seconds = max(1, int(timeout_seconds))
        service_timeout = timeout_seconds + KILL_GRACE_SECONDS + SERVICE_GRACE_SECONDS
        details = models.RunSandboxCommandDetails(
            command=self._wrap(command, timeout_seconds),
            cwd=WORKSPACE,
            timeout=f"PT{service_timeout}S",
        )
        started = time.monotonic()
        try:
            response = self._client.run_sandbox_command_and_stream(
                self._sandbox_id, details
            )
            events = [json.loads(line) for line in _lines(response.data) if line]
        except Exception as error:  # pylint: disable=broad-exception-caught
            elapsed = time.monotonic() - started
            if elapsed < timeout_seconds:
                raise SandboxError(f"command failed: {error}") from error
            # Past the deadline a dropped connection means a timeout.
            return CommandResult(command, None, "", elapsed, timed_out=True)

        output, exit_code = _collect(events, on_output)
        elapsed = time.monotonic() - started
        return CommandResult(
            command=command,
            exit_code=exit_code,
            output=output,
            duration_seconds=elapsed,
            timed_out=_is_timeout(exit_code, elapsed, timeout_seconds),
            truncated=TRUNCATION_MARKER in output,
        )

    def _wrap(self, command: str, timeout_seconds: int) -> str:
        """Wrap a command with an in-sandbox deadline and an output cap.

        The output goes to a file in the state directory. If it is larger
        than the cap, only its head, a marker and its tail are printed.
        The exit status of the original command is preserved.
        """
        head = HEAD_OUTPUT_BYTES
        tail = self._max_output_bytes - HEAD_OUTPUT_BYTES
        limit = self._max_output_bytes
        statements = [
            f"mkdir -p {STATE_DIR} || exit 125",
            f'out="{STATE_DIR}/cmd-$$.out"',
            f"timeout -k {KILL_GRACE_SECONDS} {timeout_seconds} "
            f'sh -c {shlex.quote(command)} > "$out" 2>&1 < /dev/null',
            "code=$?",
            'size=$(wc -c < "$out" | tr -d " ")',
            f'if [ "$size" -gt {limit} ]; then '
            f'head -c {head} "$out"; '
            f'printf "\\n[... %s {TRUNCATION_MARKER} ...]\\n" $((size - {limit})); '
            f'tail -c {tail} "$out"; '
            f'else cat "$out"; fi',
            'rm -f "$out"',
            "exit $code",
        ]
        return "; ".join(statements)

    # ----------------------------------------------------------------- files

    def write_file(self, path: str, data: bytes | str) -> None:
        """Create or replace a file in the sandbox.

        Parent directories are created by the service.

        Args:
            path: Absolute path under ``/workspace``.
            data: The file contents; text is encoded as UTF-8.

        Raises:
            ValueError: The path is not under ``/workspace``.
            SandboxError: A large file could not be joined from its parts.
            oci.exceptions.ServiceError: The service refused a write.
        """
        _check_path(path)
        body = data.encode("utf-8") if isinstance(data, str) else data
        if len(body) <= UPLOAD_CHUNK_BYTES:
            self._write_part(path, body)
            return

        parts = []
        for index, start in enumerate(range(0, len(body), UPLOAD_CHUNK_BYTES)):
            part = f"{path}.part-{index:04d}"
            self._write_part(part, body[start : start + UPLOAD_CHUNK_BYTES])
            parts.append(shlex.quote(part))
        joined = " ".join(parts)
        result = self.run(
            f"cat {joined} > {shlex.quote(path)} && rm -f {joined}",
            timeout_seconds=120,
        )
        if not result.ok:
            self.run(f"rm -f {joined}", timeout_seconds=60)
            raise SandboxError(f"upload of {path} failed: {result.output.strip()}")

    def _write_part(self, path: str, body: bytes) -> None:
        """Write one body of at most UPLOAD_CHUNK_BYTES, with its checksum."""
        checksum = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        self._client.write_sandbox_file(
            self._sandbox_id, path, io.BytesIO(body), opc_content_sha256=checksum
        )

    def read_file(self, path: str) -> bytes:
        """Read a file from the sandbox.

        Args:
            path: Absolute path under ``/workspace``.

        Returns:
            The file contents.

        Raises:
            ValueError: The path is not under ``/workspace``.
            FileNotFoundError: No such file (or the path is a directory).
            oci.exceptions.ServiceError: Any other service error.
        """
        _check_path(path)
        try:
            response = self._client.read_sandbox_file(self._sandbox_id, path)
        except ServiceError as error:
            if error.status == 404:
                raise FileNotFoundError(path) from error
            raise
        return _response_bytes(response.data)

    # ------------------------------------------------------------- lifecycle

    def snapshot(
        self,
        retention: str = "P7D",
        wait: bool = True,
        timeout_seconds: float = 300,
    ) -> str:
        """Save the workspace files in a snapshot.

        Only files are saved: running processes and memory are not.

        Args:
            retention: How long the snapshot is kept, as an ISO 8601
                duration (for example ``P7D``).
            wait: Block until the snapshot is ACTIVE (usable for a restore).
            timeout_seconds: The longest time to wait.

        Returns:
            Id of the snapshot.

        Raises:
            SandboxError: The snapshot failed or was not ACTIVE in time.
        """
        details = models.CreateSandboxSnapshotDetails(
            sandbox_id=self._sandbox_id, expiration_duration=retention
        )
        snapshot_id = self._client.create_sandbox_snapshot(details).data.id
        if wait:
            self._wait_until_snapshot_active(snapshot_id, timeout_seconds)
        return snapshot_id

    def _wait_until_snapshot_active(
        self, snapshot_id: str, timeout_seconds: float
    ) -> None:
        """Block until a snapshot is ACTIVE; raise if it fails or takes too long."""
        deadline = time.monotonic() + timeout_seconds
        state = "UNKNOWN"
        while time.monotonic() < deadline:
            snapshot = self._client.get_sandbox_snapshot(snapshot_id).data
            state = str(snapshot.lifecycle_state).upper()
            if state == "ACTIVE":
                return
            if state in {"FAILED", "DELETING", "DELETED"}:
                raise SandboxError(f"snapshot {snapshot_id} is {state}")
            time.sleep(POLL_SECONDS)
        raise SandboxError(
            f"snapshot {snapshot_id} not ACTIVE after {timeout_seconds:g} s "
            f"(last state: {state})"
        )

    def extend(self, minutes: int) -> datetime | None:
        """Add time to the sandbox lifetime.

        Args:
            minutes: Minutes to add.

        Returns:
            The new expiry time reported by the service.
        """
        details = models.ExtendSandboxDetails(additional_duration=f"PT{int(minutes)}M")
        sandbox = self._client.extend_sandbox(self._sandbox_id, details).data
        return getattr(sandbox, "time_expires", None)

    def close(self, on_progress: ProgressCallback | None = None) -> None:
        """Stop and delete the sandbox if this session owns it.

        Calling it again does nothing. Download files or take a snapshot
        first: the workspace is lost.

        Args:
            on_progress: Optional callback for progress messages.
        """
        if self._closed:
            return
        if self._owns_sandbox:
            _stop_and_delete(self._client, self._sandbox_id, on_progress)
        self._closed = True

    def __enter__(self) -> "SandboxSession":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# ------------------------------------------------------------------ manager


class SandboxManager:
    """Creates sandboxes in one project, with fixed runtime, shape and lifetime.

    Every new sandbox has a lifetime: if the application crashes, the
    service still ends it. If anything fails after a sandbox exists, it is
    stopped and deleted before the error reaches the caller.

    Args:
        client: A ``GenerativeAiSandboxClient``.
        project_id: OCID of a Generative AI project with sandboxes enabled.
        runtime: Runtime of new sandboxes (``python-3.11`` or an OCIR image).
        shape: Compute shape: ``SMALL``, ``MEDIUM`` or ``LARGE``.
        lifetime_minutes: Lifetime of new sandboxes (at most 48 hours).
        ready_timeout_seconds: The longest wait for a new sandbox to run.
        warm_pool: Name of a warm pool of the project; new sandboxes are
            taken from it. None starts them cold.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        client: Any,
        project_id: str,
        *,
        runtime: str = "python-3.11",
        shape: str = "SMALL",
        lifetime_minutes: int = 60,
        ready_timeout_seconds: float = READY_TIMEOUT_SECONDS,
        warm_pool: str | None = None,
    ) -> None:
        self._client = client
        self._project_id = project_id
        self._runtime = runtime
        self._shape = shape
        self._lifetime_minutes = lifetime_minutes
        self._ready_timeout_seconds = ready_timeout_seconds
        self._warm_pool = warm_pool or None

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
            ready_timeout_seconds=settings.sandbox_ready_timeout_seconds,
            warm_pool=settings.sandbox_warm_pool,
        )

    def create(
        self,
        display_name: str | None = None,
        lifetime_minutes: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> SandboxSession:
        """Create a new sandbox and wait until it is RUNNING.

        Args:
            display_name: A name for the sandbox; generated when omitted.
            lifetime_minutes: Overrides the manager's default lifetime.
            on_progress: Optional callback for progress messages.

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
            warmpool_name=self._warm_pool,
        )
        return self._start(details, on_progress)

    def restore(
        self,
        snapshot_id: str,
        display_name: str | None = None,
        lifetime_minutes: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> SandboxSession:
        """Create a new sandbox from a snapshot and wait until it is RUNNING.

        The workspace files of the snapshot are restored; processes are not.

        Args:
            snapshot_id: Id of an ACTIVE snapshot.
            display_name: A name for the sandbox; generated when omitted.
            lifetime_minutes: Overrides the manager's default lifetime.
            on_progress: Optional callback for progress messages.

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
        return self._start(details, on_progress)

    def attach(self, sandbox_id: str) -> SandboxSession:
        """Get a session on a sandbox that already exists.

        The session does not own the sandbox: closing it leaves the sandbox
        running.

        Args:
            sandbox_id: Id of a RUNNING sandbox.
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

    def _start(
        self, details: Any, on_progress: ProgressCallback | None
    ) -> SandboxSession:
        """Create a sandbox from ``details`` and wait until it is RUNNING.

        If waiting fails, the sandbox is removed and the original error is
        raised. A failure of the cleanup itself is attached to that error as
        a note, never raised in its place.
        """
        sandbox_id = self._client.create_sandbox(self._project_id, details).data.id
        try:
            self._wait_until_running(sandbox_id, on_progress)
        except BaseException as error:
            try:
                _stop_and_delete(
                    self._client,
                    sandbox_id,
                    on_progress,
                    transition_timeout_seconds=FAILED_START_CLEANUP_SECONDS,
                )
            except Exception as cleanup_error:  # pylint: disable=broad-exception-caught
                error.add_note(f"cleanup not completed: {_error_text(cleanup_error)}")
            raise
        return SandboxSession(self._client, sandbox_id, owns_sandbox=True)

    def _wait_until_running(
        self, sandbox_id: str, on_progress: ProgressCallback | None
    ) -> None:
        """Block until the sandbox is RUNNING.

        Raises:
            SandboxError: The sandbox reached a terminal state, or did not
                run within the ready timeout.
        """
        started = time.monotonic()
        state = "UNKNOWN"
        while time.monotonic() - started < self._ready_timeout_seconds:
            state = _sandbox_state(self._client, sandbox_id, missing="CREATING")
            _report(on_progress, f"{state}, {time.monotonic() - started:.0f} s")
            if state == "RUNNING":
                return
            if state in TERMINAL_STATES:
                raise SandboxError(f"sandbox {sandbox_id} is {state}, not RUNNING")
            time.sleep(POLL_SECONDS)
        raise SandboxError(
            f"sandbox {sandbox_id} not RUNNING after {self._ready_timeout_seconds:g} s "
            f"(last state: {state})"
        )


# ------------------------------------------------------------------ helpers


def _sandbox_state(client: Any, sandbox_id: str, missing: str | None = None) -> str:
    """The lifecycle state of a sandbox, in upper case.

    Args:
        client: A ``GenerativeAiSandboxClient``.
        sandbox_id: Id of the sandbox.
        missing: State to report when the service answers 404 Not Found
            (for example, a sandbox created a moment ago). When None, the
            404 is raised.
    """
    try:
        return str(client.get_sandbox(sandbox_id).data.lifecycle_state).upper()
    except ServiceError as error:
        if error.status == 404 and missing is not None:
            return missing
        raise


def _stop_and_delete(
    client: Any,
    sandbox_id: str,
    on_progress: ProgressCallback | None = None,
    transition_timeout_seconds: float | None = None,
) -> None:
    """Stop a sandbox and delete it. A sandbox that is already gone is fine.

    The service accepts a stop only in some states, and a delete only when
    the sandbox is STOPPED or FAILED, so the steps are:

    1. CREATING or UPDATING: wait until the sandbox leaves that state.
    2. RUNNING, SUSPENDING, SUSPENDED or RESUMING: request a stop.
    3. Wait for STOPPED (stopping can take a few minutes).
    4. Delete.

    Args:
        client: A ``GenerativeAiSandboxClient``.
        sandbox_id: Id of the sandbox.
        on_progress: Optional callback for progress messages.
        transition_timeout_seconds: The longest wait in step 1;
            READY_TIMEOUT_SECONDS by default.

    Raises:
        SandboxError: The sandbox did not reach a state that can be deleted
            in time. The service stops it when its lifetime expires, but
            a STOPPED sandbox is not deleted: delete it later.
        oci.exceptions.ServiceError: The service refused a call (other than
            404 Not Found).
    """
    try:
        state = _wait_while(
            client,
            sandbox_id,
            TRANSITIONAL_STATES,
            on_progress,
            timeout_seconds=transition_timeout_seconds,
        )
        if state == "DELETED":
            return
        if state in STOPPABLE_STATES:
            _report(on_progress, f"stopping ({state})")
            client.stop_sandbox(sandbox_id, models.StopSandboxDetails(is_force=False))
            state = _wait_while(
                client,
                sandbox_id,
                STOPPABLE_STATES | {"STOPPING"},
                on_progress,
                timeout_seconds=STOP_TIMEOUT_SECONDS,
            )
        elif state == "STOPPING":
            state = _wait_while(
                client,
                sandbox_id,
                frozenset({"STOPPING"}),
                on_progress,
                timeout_seconds=STOP_TIMEOUT_SECONDS,
            )
        if state not in DELETABLE_STATES:
            raise SandboxError(
                f"sandbox {sandbox_id} is still {state}, so it was not deleted; "
                "delete it once it is STOPPED"
            )
        _report(on_progress, "deleting")
        client.delete_sandbox(sandbox_id)
    except ServiceError as error:
        if error.status != 404:
            raise


def _wait_while(
    client: Any,
    sandbox_id: str,
    states: frozenset[str],
    on_progress: ProgressCallback | None,
    timeout_seconds: float | None = None,
) -> str:
    """Poll until the sandbox is in none of ``states``, or the time is up.

    Args:
        client: A ``GenerativeAiSandboxClient``.
        sandbox_id: Id of the sandbox.
        states: The states to wait out.
        on_progress: Optional callback for progress messages.
        timeout_seconds: The longest wait; READY_TIMEOUT_SECONDS by default.

    Returns:
        The last state seen.
    """
    if timeout_seconds is None:
        timeout_seconds = READY_TIMEOUT_SECONDS
    started = time.monotonic()
    state = _sandbox_state(client, sandbox_id)
    while state in states and time.monotonic() - started < timeout_seconds:
        _report(on_progress, f"{state}, {time.monotonic() - started:.0f} s")
        time.sleep(POLL_SECONDS)
        state = _sandbox_state(client, sandbox_id)
    return state


def _error_text(error: BaseException) -> str:
    """A short description of an error: the service message, not its full dump."""
    if isinstance(error, ServiceError):
        return f"HTTP {error.status} {error.code}: {error.message}"
    return str(error)


def _report(on_progress: ProgressCallback | None, message: str) -> None:
    """Send a progress message to the callback, if there is one."""
    if on_progress is not None:
        on_progress(message)


def _generated_name(prefix: str) -> str:
    """A unique, readable display name, for example ``triage-3f9c1a2b``."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _check_path(path: str) -> None:
    """Refuse paths the service would reject, with a clearer message."""
    if not (path == WORKSPACE or path.startswith(WORKSPACE + "/")):
        raise ValueError(f"sandbox paths must be absolute, under {WORKSPACE}: {path!r}")


def _is_timeout(exit_code: int | None, elapsed: float, timeout_seconds: int) -> bool:
    """True when a command was stopped at its deadline (or never finished)."""
    if exit_code is None or exit_code == TIMEOUT_EXIT_CODE:
        return True
    return exit_code == KILLED_EXIT_CODE and elapsed >= timeout_seconds


def _lines(data: Any) -> Iterable[bytes | str]:
    """The NDJSON lines of a streamed response."""
    if hasattr(data, "iter_lines"):
        return data.iter_lines()
    raw = getattr(data, "raw", None)
    if raw is not None:
        return raw.read().splitlines()
    if isinstance(data, (bytes, str)):
        return data.splitlines()
    return data


def _collect(
    events: list[dict[str, Any]], on_output: Callable[[str], None] | None
) -> tuple[str, int | None]:
    """Join the output events in sequence order and read the exit code.

    The stream carries STARTED, STDOUT, STDERR and FINISHED events, each
    with a sequence number. An exit code of -1 (or no FINISHED event) means
    the command did not finish.
    """
    chunks: list[str] = []
    exit_code: int | None = None
    for event in sorted(events, key=lambda e: e.get("seq") or 0):
        kind = event.get("event")
        if kind in ("STDOUT", "STDERR"):
            chunk = str(event.get("data") or "")
            chunks.append(chunk)
            if on_output is not None and chunk:
                on_output(chunk)
        elif kind == "FINISHED":
            raw_code = event.get("exitCode", event.get("exit_code"))
            if isinstance(raw_code, int) and raw_code >= 0:
                exit_code = raw_code
    return "".join(chunks), exit_code


def _response_bytes(data: Any) -> bytes:
    """The body of a file-read response, whatever shape the SDK returns."""
    content = getattr(data, "content", None)
    if content is None:
        raw = getattr(data, "raw", None)
        content = raw.read() if raw is not None else data
    return content.encode("utf-8") if isinstance(content, str) else bytes(content)
