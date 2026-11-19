"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Unit tests of SandboxSession and SandboxManager against the fake
    client: command wrapper (exit code, timeout, output cap), file transfer
    (checksum, chunking, 404) and lifecycle (wait, cleanup on failure).
"""

# pylint: disable=missing-function-docstring

import pytest

from tests.unit.fakes import FakeSandboxClient
from triage.sandbox import SandboxError, SandboxManager, SandboxSession
from triage.sandbox import session as session_module


@pytest.fixture(name="client")
def fixture_client(tmp_path):
    """A fake sandbox client rooted in a temporary directory."""
    return FakeSandboxClient(tmp_path)


@pytest.fixture(name="session")
def fixture_session(client):
    """A session on the fake sandbox."""
    return SandboxSession(client, "ocid1.sandbox.fake")


def test_run_returns_output_and_exit_code(session):
    result = session.run("echo hello; echo oops >&2; exit 3", timeout_seconds=10)
    assert result.exit_code == 3
    assert "hello" in result.output and "oops" in result.output
    assert not result.ok and not result.timed_out and not result.truncated


def test_run_stops_a_command_at_its_deadline(session):
    result = session.run("sleep 5", timeout_seconds=1)
    assert result.timed_out
    assert result.exit_code == 124


def test_run_keeps_head_and_tail_of_long_output(client):
    session = SandboxSession(client, "ocid1.sandbox.fake", max_output_bytes=200_000)
    command = "python3 -c \"print('START'); print('x' * 1_000_000); print('END')\""
    result = session.run(command, timeout_seconds=30)
    assert result.ok and result.truncated
    assert result.output.startswith("START") and result.output.rstrip().endswith("END")
    assert len(result.output) < 201_000


def test_file_round_trip(session):
    session.write_file("/workspace/dir/a.txt", "ciao")
    assert session.read_file("/workspace/dir/a.txt") == b"ciao"


def test_large_file_is_written_in_parts(session, client, monkeypatch):
    monkeypatch.setattr(session_module, "UPLOAD_CHUNK_BYTES", 1000)
    data = bytes(range(256)) * 20  # 5120 bytes: 6 parts
    session.write_file("/workspace/big.bin", data)
    assert session.read_file("/workspace/big.bin") == data
    assert not list(client.workspace.glob("big.bin.part-*"))


def test_missing_file_raises_file_not_found(session):
    with pytest.raises(FileNotFoundError):
        session.read_file("/workspace/nope.txt")


def test_paths_outside_workspace_are_refused(session):
    with pytest.raises(ValueError):
        session.write_file("/tmp/x.txt", "x")
    with pytest.raises(ValueError):
        session.read_file("relative.txt")


def test_close_deletes_an_owned_sandbox_once(session, client):
    session.close()
    session.close()
    assert client.calls.count("delete") == 1


def test_attached_session_does_not_delete(client):
    SandboxManager(client, "ocid1.generativeaiproject.fake").attach("x").close()
    assert "delete" not in client.calls


def test_create_waits_until_running(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    client = FakeSandboxClient(tmp_path, states=["CREATING", "CREATING", "RUNNING"])
    manager = SandboxManager(
        client, "ocid1.generativeaiproject.fake", lifetime_minutes=15
    )
    with manager.create(display_name="t1") as session:
        assert session.state() == "RUNNING"
    assert client.created_details.expires_in_minutes == 15
    assert client.calls[-1] == "delete"


def test_create_cleans_up_a_sandbox_that_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    client = FakeSandboxClient(tmp_path, states=["CREATING", "FAILED"])
    manager = SandboxManager(client, "ocid1.generativeaiproject.fake")
    with pytest.raises(SandboxError):
        manager.create()
    assert "delete" in client.calls
