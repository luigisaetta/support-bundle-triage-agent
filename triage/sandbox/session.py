"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: SandboxSession, the application's handle on one running OCI
    Sandbox: run commands, move files, take snapshots, extend and remove it.

    The service has a few behaviors that are not obvious from the API. They
    were measured on the live service and shape this class:

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

Usage:

    with manager.create() as session:
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
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from oci.exceptions import ServiceError
from oci.generative_ai_sandbox import models

from triage.sandbox.lifecycle import SandboxLifecycle
from triage.sandbox.models import CommandResult, SandboxError

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

#: Marker printed in place of the dropped middle of a long output.
TRUNCATION_MARKER = "bytes of output omitted"


class SandboxSession:
    """One running OCI Sandbox.

    Create sessions with :class:`~triage.sandbox.manager.SandboxManager`.
    A session that owns its sandbox stops and deletes it on :meth:`close`,
    also when used as a context manager.

    Args:
        client: A ``GenerativeAiSandboxClient``.
        sandbox_id: OCID of a RUNNING sandbox.
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
        self._lifecycle = SandboxLifecycle(client)
        self._closed = False

    # ------------------------------------------------------------------ info

    @property
    def sandbox_id(self) -> str:
        """OCID of the sandbox."""
        return self._sandbox_id

    @property
    def owns_sandbox(self) -> bool:
        """True when :meth:`close` stops and deletes the sandbox."""
        return self._owns_sandbox

    def state(self) -> str:
        """The current lifecycle state of the sandbox, for example ``RUNNING``."""
        return self._lifecycle.state(self._sandbox_id)

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
        details = models.RunSandboxCommandDetails(
            command=self._wrap(command, timeout_seconds),
            cwd=WORKSPACE,
            timeout=f"PT{timeout_seconds + KILL_GRACE_SECONDS + SERVICE_GRACE_SECONDS}S",
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
            f"cat {joined} > {shlex.quote(path)} && rm -f {joined}", timeout_seconds=120
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
        self, retention: str = "P7D", wait: bool = True, timeout_seconds: float = 300
    ) -> str:
        """Save the workspace files in a snapshot.

        Only files are saved: running processes and memory are not.

        Args:
            retention: How long the snapshot is kept, as an ISO 8601
                duration (for example ``P7D``).
            wait: Block until the snapshot is ACTIVE (usable for a restore).
            timeout_seconds: The longest time to wait.

        Returns:
            OCID of the snapshot.

        Raises:
            SandboxError: The snapshot failed or was not ACTIVE in time.
        """
        details = models.CreateSandboxSnapshotDetails(
            sandbox_id=self._sandbox_id, expiration_duration=retention
        )
        snapshot_id = self._client.create_sandbox_snapshot(details).data.id
        if wait:
            self._lifecycle.wait_until_snapshot_active(snapshot_id, timeout_seconds)
        return snapshot_id

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

    def close(self) -> None:
        """Stop and delete the sandbox if this session owns it.

        Calling it again does nothing. Download files or take a snapshot
        first: the workspace is lost.
        """
        if self._closed:
            return
        if self._owns_sandbox:
            self._lifecycle.stop_and_delete(self._sandbox_id)
        self._closed = True

    def __enter__(self) -> "SandboxSession":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# ---------------------------------------------------------------- helpers


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
