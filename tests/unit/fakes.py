"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: A fake GenerativeAiSandboxClient for unit tests. Commands run
    locally with sh, in a temporary directory that stands in for /workspace,
    so the real command wrapper of SandboxSession is exercised. A small
    "timeout" shim is put on the PATH for systems (such as macOS) that do not
    ship GNU timeout.
"""

# pylint: disable=unused-argument

import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from oci.exceptions import ServiceError

TIMEOUT_SHIM = f"""#!{sys.executable}
import subprocess, sys
args = sys.argv[1:]
if args[0] == "-k":
    args = args[2:]
seconds, command = float(args[0]), args[1:]
try:
    sys.exit(subprocess.run(command, timeout=seconds).returncode)
except subprocess.TimeoutExpired:
    sys.exit(124)
"""


class FakeSandboxClient:
    """Implements the client methods used by the sandbox package.

    Args:
        root: A temporary directory standing in for ``/workspace``.
        states: Lifecycle states returned by successive ``get_sandbox``
            calls before the sandbox is reported RUNNING.
    """

    def __init__(self, root: Path, states: list[str] | None = None) -> None:
        self.root = root
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.bin_dir = root / "bin"
        self.bin_dir.mkdir()
        shim = self.bin_dir / "timeout"
        shim.write_text(TIMEOUT_SHIM, encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        self._pending_states = list(states or [])
        self.state = "RUNNING"
        self.calls: list[str] = []

    # -------------------------------------------------------- path mapping

    def _local(self, text: str) -> str:
        return text.replace("/workspace", str(self.workspace))

    # ------------------------------------------------------------ commands

    def run_sandbox_command_and_stream(self, sandbox_id, details):
        """Run the command with sh and return NDJSON events."""
        self.calls.append("run")
        env = {**os.environ, "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}"}
        completed = subprocess.run(
            ["sh", "-c", self._local(details.command)],
            cwd=self.workspace,
            env=env,
            capture_output=True,
            check=False,
        )
        events = [
            {"seq": 1, "event": "STARTED"},
            {
                "seq": 2,
                "event": "STDOUT",
                "data": completed.stdout.decode(errors="replace"),
            },
            {"seq": 3, "event": "FINISHED", "exitCode": completed.returncode},
        ]
        body = "\n".join(json.dumps(event) for event in events).encode()
        return SimpleNamespace(data=body)

    # --------------------------------------------------------------- files

    def write_sandbox_file(
        self, sandbox_id, file_path, stream, opc_content_sha256=None
    ):
        """Write a file, checking the base64 SHA-256 like the service does."""
        body = stream.read()
        expected = base64.b64encode(hashlib.sha256(body).digest()).decode()
        if opc_content_sha256 is not None and opc_content_sha256 != expected:
            raise ServiceError(412, "PreconditionFailed", {}, "checksum mismatch")
        target = Path(self._local(file_path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)

    def read_sandbox_file(self, sandbox_id, file_path):
        """Read a file; 404 when it does not exist."""
        target = Path(self._local(file_path))
        if not target.is_file():
            raise ServiceError(404, "NotFound", {}, "no such file")
        return SimpleNamespace(data=SimpleNamespace(content=target.read_bytes()))

    # ----------------------------------------------------------- lifecycle

    def create_sandbox(self, project_id, details):
        """Pretend to create a sandbox."""
        self.calls.append("create")
        self.created_details = details  # pylint: disable=attribute-defined-outside-init
        return SimpleNamespace(data=SimpleNamespace(id="ocid1.sandbox.fake"))

    def get_sandbox(self, sandbox_id):
        """Return the next pending state, then the current one."""
        if self._pending_states:
            self.state = self._pending_states.pop(0)
        return SimpleNamespace(
            data=SimpleNamespace(
                lifecycle_state=self.state, runtime="python-3.11", shape="SMALL"
            )
        )

    def stop_sandbox(self, sandbox_id, details):
        """Pretend to stop the sandbox."""
        self.calls.append("stop")
        self.state = "STOPPED"

    def delete_sandbox(self, sandbox_id):
        """Pretend to delete the sandbox."""
        self.calls.append("delete")
        self.state = "DELETED"
