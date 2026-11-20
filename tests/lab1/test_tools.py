"""
Author: L. Saetta
Date Last Modified: 2026-11-20
License: MIT
Description: Verify function tools through SandboxSession and the provided fake client.
"""

import json
from pathlib import Path
import re
from unittest.mock import patch

import pytest

from tests.unit.fakes import FakeSandboxClient
from triage.sandbox import CommandResult, SandboxError, SandboxSession
from triage.tools import (
    BUNDLE_DIR,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_GREP_MATCHES,
    MAX_LIST_ENTRIES,
    MAX_OUTPUT_CHARS,
    MAX_READ_LINES,
    SCRATCH_DIR,
    ListFilesTool,
    ToolRegistry,
)


@pytest.fixture(name="session")
def fixture_session(tmp_path: Path) -> SandboxSession:
    """Prepare a sample bundle through the session file API.

    Args:
        tmp_path: Temporary root for the fake client.

    Returns:
        A real session backed by FakeSandboxClient, with no OCI calls.
    """
    client = FakeSandboxClient(tmp_path)
    sandbox = SandboxSession(client, "ocid1.sandbox.fake", owns_sandbox=False)
    sandbox.write_file(
        f"{BUNDLE_DIR}/app.log", "INFO started\nERROR disk full\nINFO stop\n"
    )
    sandbox.write_file(f"{BUNDLE_DIR}/config.ini", "workers=2\n")
    sandbox.write_file(f"{BUNDLE_DIR}/sub/alert.log", "ERROR capacity\n")
    return sandbox


def _assert_error(result: str) -> None:
    """Require one error marker, no absolute paths, and a bounded result."""
    assert result.startswith("ERROR:")
    assert result.count("ERROR:") == 1
    assert len(result) <= MAX_OUTPUT_CHARS
    assert not re.search(r"(?<![\w.])/[\w/]", result)


def test_strict_function_schemas(session: SandboxSession) -> None:
    """Expose four strict, JSON-serializable, independently copied schemas."""
    registry = ToolRegistry(session)
    assert registry.names() == ["list_files", "read_file", "grep", "run_python"]
    schemas = registry.schemas()
    assert len(schemas) == 4
    json.dumps(schemas)
    for schema in schemas:
        assert schema["type"] == "function"
        assert schema["strict"] is True
        assert schema["description"]
        parameters = schema["parameters"]
        assert parameters["type"] == "object"
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == set(parameters["properties"])
    schemas[0]["parameters"]["properties"].clear()
    assert "path" in registry.schemas()[0]["parameters"]["properties"]


def test_listing_and_relative_evidence(session: SandboxSession) -> None:
    """Return sorted sizes and precise evidence without success status headers."""
    registry = ToolRegistry(session)
    assert registry.call("list_files", {"path": "."}) == (
        "app.log: 39 bytes\nconfig.ini: 10 bytes\nsub/alert.log: 15 bytes\n"
    )
    assert registry.call("list_files", {"path": "sub"}) == "sub/alert.log: 15 bytes\n"
    assert (
        registry.call(
            "read_file", json.dumps({"path": "app.log", "start_line": 2, "end_line": 2})
        )
        == "app.log:2: ERROR disk full\n"
    )
    assert registry.call("grep", {"path": ".", "pattern": "ERROR (disk|capacity)"}) == (
        "app.log:2: ERROR disk full\nsub/alert.log:1: ERROR capacity\n"
    )
    assert registry.call("run_python", {"code": "print(1 + 1)"}) == "2\n"


def test_eof_empty_files_and_missing_matches(session: SandboxSession) -> None:
    """Distinguish early EOF, empty text, and searches with no matches."""
    session.write_file(f"{BUNDLE_DIR}/empty", "")
    registry = ToolRegistry(session)
    assert (
        registry.call("read_file", {"path": "app.log", "start_line": 2, "end_line": 5})
        == "app.log:2: ERROR disk full\napp.log:3: INFO stop\n[end of file at line 3]\n"
    )
    assert (
        registry.call("read_file", {"path": "empty", "start_line": 1, "end_line": 5})
        == "[end of file at line 0]\n"
    )
    assert (
        registry.call(
            "read_file", {"path": "app.log", "start_line": 10, "end_line": 20}
        )
        == "[end of file at line 3]\n"
    )
    assert registry.call("grep", {"path": ".", "pattern": "missing"}) == (
        "No matches found.\n"
    )


def test_read_and_grep_limits(session: SandboxSession) -> None:
    """Limit ranges and matches, counting omitted grep evidence exactly."""
    count = MAX_READ_LINES + MAX_GREP_MATCHES
    session.write_file(f"{BUNDLE_DIR}/long.log", "ERROR item\n" * count)
    registry = ToolRegistry(session)
    result = registry.call(
        "read_file", {"path": "long.log", "start_line": 1, "end_line": count + 1}
    )
    assert result.count(": ERROR item") == MAX_READ_LINES
    assert f"line limit {MAX_READ_LINES}" in result
    assert f"end of file at line {count}" in result
    result = registry.call("grep", {"path": "long.log", "pattern": "ERROR"})
    assert result.count(": ERROR item") == MAX_GREP_MATCHES
    assert f"{count - MAX_GREP_MATCHES} matches omitted" in result


def test_listing_limit(session: SandboxSession) -> None:
    """Return the first sorted entries and the exact number left out."""
    registry = ToolRegistry(session)
    code = (
        "from pathlib import Path\nPath('many').mkdir()\n"
        f"for i in range({MAX_LIST_ENTRIES + 3}):\n"
        "    Path(f'many/{i:04}.txt').write_text('x')\n"
    )
    assert registry.call("run_python", {"code": code}) == ""
    result = registry.call("list_files", {"path": "many"})
    assert result.count("bytes") == MAX_LIST_ENTRIES
    assert result.startswith("many/0000.txt: 1 bytes\n")
    assert "3 entries omitted" in result


def test_python_relative_file_access_and_unique_scripts(
    session: SandboxSession,
) -> None:
    """Read files by relative path and keep source code out of command arguments."""
    registry = ToolRegistry(session)
    code = "from pathlib import Path\nprint(Path('config.ini').read_text(), end='')"
    with patch.object(session, "write_file", wraps=session.write_file) as write:
        with patch.object(session, "run", wraps=session.run) as run:
            assert registry.call("run_python", {"code": code}) == "workers=2\n"
            assert registry.call("run_python", {"code": code}) == "workers=2\n"
    paths = [call.args[0] for call in write.call_args_list]
    assert len(set(paths)) == 2
    assert all(path.startswith(SCRATCH_DIR + "/") for path in paths)
    for call in run.call_args_list:
        assert code not in call.args[0]
        assert call.kwargs["timeout_seconds"] == DEFAULT_TIMEOUT_SECONDS


def test_python_timeout(session: SandboxSession) -> None:
    """Stop a script that would otherwise loop forever after two seconds."""
    result = ToolRegistry(session, timeout_seconds=2).call(
        "run_python", {"code": "import time\nwhile True:\n    time.sleep(0.05)"}
    )
    _assert_error(result)
    assert "stopped after 2 seconds" in result


@pytest.mark.parametrize("code", ["raise SystemExit(7)", "this is not valid Python!"])
def test_python_failure(session: SandboxSession, code: str) -> None:
    """Include nonzero script status without absolute traceback paths."""
    result = ToolRegistry(session).call("run_python", {"code": code})
    _assert_error(result)
    assert "exit code" in result
    if "7" in code:
        assert "exit code 7" in result
    else:
        assert "SyntaxError" in result


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("unknown", {}),
        ("list_files", "{broken json"),
        ("list_files", {}),
        ("list_files", []),
        ("list_files", "null"),
        ("list_files", {"path": 12}),
        ("list_files", {"path": ".", "extra": True}),
        ("list_files", {"path": "../../etc/passwd"}),
        ("list_files", {"path": "/etc/passwd"}),
        ("list_files", {"path": "sub/../app.log"}),
        ("list_files", {"path": "\x00"}),
        ("list_files", {"path": ""}),
        ("read_file", {"path": "app.log", "start_line": True, "end_line": 3}),
        ("read_file", {"path": "app.log", "start_line": 0, "end_line": 3}),
        ("read_file", {"path": "app.log", "start_line": 3, "end_line": 2}),
        ("run_python", {"code": 12}),
    ],
)
def test_invalid_arguments_do_not_run(
    session: SandboxSession, name: str, arguments: object
) -> None:
    """Reject invalid model input before any sandbox command or file write."""
    with (
        patch.object(session, "run") as run,
        patch.object(session, "write_file") as write,
    ):
        _assert_error(ToolRegistry(session).call(name, arguments))
    run.assert_not_called()
    write.assert_not_called()


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("list_files", {"path": "missing"}),
        ("list_files", {"path": "app.log"}),
        ("read_file", {"path": "sub", "start_line": 1, "end_line": 2}),
        ("read_file", {"path": "sub/missing", "start_line": 1, "end_line": 2}),
        ("grep", {"path": ".", "pattern": "["}),
    ],
)
def test_remote_errors(session: SandboxSession, name: str, arguments: dict) -> None:
    """Return each remote error once using relative paths."""
    _assert_error(ToolRegistry(session).call(name, arguments))


def test_external_links_are_skipped(session: SandboxSession) -> None:
    """Continue listing and searching after external file and directory links."""
    session.write_file("/workspace/private/secret.txt", "ERROR outside secret\n")
    result = session.run(
        "cd /workspace/bundle && ln -s ../private/secret.txt link "
        "&& ln -s ../../private sub/link",
        timeout_seconds=10,
    )
    assert result.ok
    registry = ToolRegistry(session)
    for name, arguments in [
        ("list_files", {"path": "."}),
        ("grep", {"path": ".", "pattern": "ERROR"}),
    ]:
        result = registry.call(name, arguments)
        assert not result.startswith("ERROR:")
        assert "app.log" in result and "sub/alert.log" in result
        assert "[skipped: link (symbolic link outside the bundle)]" in result
        assert "[skipped: sub/link (symbolic link outside the bundle)]" in result
        assert "outside secret" not in result
    for name, arguments in [
        ("list_files", {"path": "sub/link"}),
        ("read_file", {"path": "link", "start_line": 1, "end_line": 1}),
        ("read_file", {"path": "sub/link/secret.txt", "start_line": 1, "end_line": 1}),
        ("grep", {"path": "link", "pattern": "ERROR"}),
    ]:
        _assert_error(registry.call(name, arguments))


def test_binary_file_handling(session: SandboxSession) -> None:
    """List binary files, skip them during grep, and reject text reads."""
    session.write_file(f"{BUNDLE_DIR}/binary.bin", b"ERROR\x00hidden binary\n")
    registry = ToolRegistry(session)
    assert "binary.bin: 20 bytes" in registry.call("list_files", {"path": "."})
    assert "binary.bin" not in registry.call("grep", {"path": ".", "pattern": "ERROR"})
    assert registry.call("grep", {"path": "binary.bin", "pattern": "ERROR"}) == (
        "No matches found.\n"
    )
    result = registry.call(
        "read_file", {"path": "binary.bin", "start_line": 1, "end_line": 5}
    )
    _assert_error(result)
    assert "binary.bin is binary" in result


def test_shell_metacharacters(session: SandboxSession) -> None:
    """Use filenames, regex patterns and script text without shell interpretation."""
    name = "odd'; touch INJECTED; $(touch OTHER).log"
    session.write_file(f"{BUNDLE_DIR}/{name}", "literal ; rm -rf x\n$(touch THIRD)\n")
    registry = ToolRegistry(session)
    assert (
        registry.call("read_file", {"path": name, "start_line": 1, "end_line": 1})
        == f"{name}:1: literal ; rm -rf x\n"
    )
    assert registry.call("grep", {"path": name, "pattern": "; rm -rf x"}) == (
        f"{name}:1: literal ; rm -rf x\n"
    )
    registry.call("grep", {"path": ".", "pattern": "$(touch FOURTH)"})
    assert registry.call("run_python", {"code": "print('$(touch FIFTH)')"}) == (
        "$(touch FIFTH)\n"
    )
    result = registry.call(
        "run_python",
        {
            "code": "from pathlib import Path\n"
            "print(any(Path(p).exists() for p in ['INJECTED', 'OTHER', 'THIRD', 'FOURTH', 'FIFTH']))"
        },
    )
    assert result == "False\n"


def test_long_results_keep_head_and_tail(session: SandboxSession) -> None:
    """Cap long output and evidence with an accurate omission count."""
    registry = ToolRegistry(session)
    result = registry.call(
        "run_python", {"code": "print('HEAD' + 'x' * 20000 + 'TAIL')"}
    )
    assert result.startswith("HEAD") and result.endswith("TAIL\n")
    assert len(result) == MAX_OUTPUT_CHARS
    marker = re.search(r"\n\[\.\.\. (\d+) characters omitted \.\.\.\]\n", result)
    assert marker
    assert int(marker.group(1)) == 20009 - (len(result) - len(marker.group(0)))
    session.write_file(f"{BUNDLE_DIR}/huge", "START" + "y" * 20000 + "END\n")
    result = registry.call(
        "read_file", {"path": "huge", "start_line": 1, "end_line": 1}
    )
    assert len(result) <= MAX_OUTPUT_CHARS
    assert "START" in result and "END" in result


def test_single_sanitized_error(session: SandboxSession) -> None:
    """Sanitize repeated error markers and absolute paths in failing script output."""
    result = ToolRegistry(session).call(
        "run_python",
        {"code": "print('ERROR: ERROR: /etc/passwd')\nraise SystemExit(3)"},
    )
    _assert_error(result)
    assert "exit code 3" in result
    with patch.object(
        session, "run", side_effect=SandboxError("ERROR: /private/secret")
    ):
        _assert_error(ToolRegistry(session).call("list_files", {"path": "."}))
    with patch.object(session, "write_file", side_effect=SandboxError("upload failed")):
        _assert_error(ToolRegistry(session).call("run_python", {"code": "print(1)"}))


def test_sandbox_truncation_notice(session: SandboxSession) -> None:
    """Retain the sandbox truncation signal even when the returned text is short."""
    result = CommandResult("command", 0, "head\ntail\n", 0, truncated=True)
    with patch.object(session, "run", return_value=result):
        text = ToolRegistry(session).call("run_python", {"code": "print(1)"})
    assert text.startswith("head\ntail\n")
    assert "Sandbox output truncated" in text


def test_custom_registry(session: SandboxSession) -> None:
    """Respect explicit empty/custom selections and avoid mutating caller tools."""
    assert ToolRegistry(session, tools=[]).schemas() == []
    tool = ListFilesTool()
    registry = ToolRegistry(session, tools=[tool], timeout_seconds=3)
    assert registry.names() == ["list_files"]
    assert tool.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert registry.call("list_files", {"path": "sub"}) == "sub/alert.log: 15 bytes\n"
