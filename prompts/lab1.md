# Lab 1 — Prompt for Codex: sandbox tools

Paste everything below the line into Codex, from the repository root.

---

Read `AGENTS.md` and follow it. Then build Lab 1: the function tools the
triage agent uses to explore a support bundle. Do not change any provided file.

## Context

- A support bundle (logs, alert log, configuration files, metrics) is
  extracted in the sandbox under `/workspace/bundle`.
- The model explores it only through the tools you write here. Every tool runs
  in the sandbox through the provided `SandboxSession` (`triage/sandbox.py`):
  `run(command, timeout_seconds)` returns a `CommandResult` (`exit_code`,
  `output`, `timed_out`, `truncated`, `ok`); `write_file(path, data)` and
  `read_file(path)` move files.
- In Lab 2 an agent loop on the OpenAI Responses API will pass the tool
  schemas to the model and call the tools by name with the JSON arguments the
  model sends. Build exactly the interface below, so Lab 2 can use it.

## What to build: `triage/tools.py`

### Constants

- `BUNDLE_DIR = "/workspace/bundle"`: root of the bundle; all tool paths are
  relative to it.
- `SCRATCH_DIR = "/workspace/scratch"`: where `run_python` writes its scripts.
- `DEFAULT_TIMEOUT_SECONDS = 60`, `MAX_OUTPUT_CHARS = 8000`,
  `MAX_LIST_ENTRIES = 200`, `MAX_READ_LINES = 200`, `MAX_GREP_MATCHES = 50`.

### `Tool` (abstract base class)

- Class attributes: `name: str`, `description: str` (written for the model:
  what the tool does and when to use it), `parameters: dict` (JSON schema of
  the arguments).
- `schema() -> dict`: the tool in the Responses API format:
  `{"type": "function", "name": ..., "description": ..., "parameters": ...,
  "strict": True}`. Strict mode means every property is listed in `required`
  and `additionalProperties` is `False`.
- `run(session: SandboxSession, arguments: dict) -> str` (abstract): executes
  the tool in the sandbox and returns text for the model.

### The four tools

| Class | `name` | Arguments | Returns |
|-------|--------|-----------|---------|
| `ListFilesTool` | `list_files` | `path` (directory, `"."` for the root) | one line per file: relative path and size in bytes, sorted, at most `MAX_LIST_ENTRIES` (say how many were omitted) |
| `ReadFileTool` | `read_file` | `path`, `start_line`, `end_line` (1-based, inclusive) | the lines as `path:line: text`, at most `MAX_READ_LINES` per call; say when the file ends before `end_line` |
| `GrepTool` | `grep` | `pattern` (extended regular expression), `path` (file or directory, `"."` for all) | matches as `path:line: text`, at most `MAX_GREP_MATCHES` (say how many were omitted); a clear message when nothing matches |
| `RunPythonTool` | `run_python` | `code` (a Python 3 script) | the script's exit code and output; it runs with the bundle directory as current directory, so it can open files by relative path |

`path:line: text` is the evidence format of the final report (Lab 3): paths are
always relative to `BUNDLE_DIR`, in results and in error messages alike.

### Expected output

What the model sees (successful results have no status header):

```
list_files {"path": "."}
config/init.ora: 37 bytes
logs/app.log: 9457 bytes
[skipped: config/link (symbolic link outside the bundle)]

read_file {"path": "logs/app.log", "start_line": 299, "end_line": 305}
logs/app.log:299: 2026-11-20 10:58 INFO tick 298
logs/app.log:300: 2026-11-20 10:59 INFO tick 299
logs/app.log:301: 2026-11-20 11:00 ERROR ORA-04031: unable to allocate shared memory
[end of file at line 301]

grep {"pattern": "ORA-[0-9]+", "path": "."}
logs/app.log:301: 2026-11-20 11:00 ERROR ORA-04031: unable to allocate shared memory

run_python {"code": "print(1 + 1)"}
2

run_python {"code": "while True: pass"}
ERROR: the script was stopped after 60 seconds. Make it do less and retry.

read_file {"path": "logs/missing.log", "start_line": 1, "end_line": 5}
ERROR: logs/missing.log does not exist. Use list_files to see the bundle.
```

### `ToolRegistry`

- `__init__(session: SandboxSession, tools: list[Tool] | None = None,
  timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS)`; by default it holds the
  four tools.
- `schemas() -> list[dict]`: the schemas of all tools, for `responses.create`.
- `names() -> list[str]`.
- `call(name: str, arguments: str | dict) -> str`: runs a tool. `arguments`
  can be the JSON string sent by the model. It never raises: an unknown tool,
  invalid JSON, a missing or wrong argument, a path outside the bundle, a
  timeout or a `SandboxError` all become a short message starting with
  `ERROR:` that tells the model what went wrong and how to retry.

## Requirements

- **Readable code:** each tool keeps its own logic in its own class: it builds
  one short shell command (or a short inline `python3` snippet of a few lines)
  and turns the `CommandResult` into text. Do not put the logic of several
  tools in one shared script selected by a "mode" argument, and do not embed
  long programs in strings: code inside a string is not linted or formatted.
  Shared steps (path checks, output capping, error text) go in small helper
  methods or functions.
- **Paths:** reject absolute paths and any path that escapes `BUNDLE_DIR`
  (`..`), before running anything.
- **Symbolic links:** a symbolic link that points outside the bundle is never
  followed. If it is the requested path, return an `ERROR:`. While listing or
  searching a directory, skip it and add a `[skipped: ...]` note, then go on
  with the other files: one bad link must not hide the rest of the bundle.
- **Binary files:** `grep` skips them (for example `grep -I`); `list_files`
  still lists them; `read_file` on a binary file returns an `ERROR:`.
- **Output:** a successful result is just the data (no "exit code 0" header).
  An error is reported once, as a single message starting with `ERROR:`, with
  paths relative to the bundle.
- **Shell safety:** quote every value that goes into a shell command with
  `shlex.quote`; never build commands by plain string concatenation of model
  input. For `run_python`, write the code to a new file in `SCRATCH_DIR` with
  `session.write_file` and run that file; do not pass the code on the command
  line.
- **Bounded output:** every result fits in `MAX_OUTPUT_CHARS`; when longer,
  keep the head and the tail and say how much was cut. Report timeouts and
  non-zero exit codes in the text.
- **Portable commands:** the sandbox has `sh`, `python3`, `grep`, `awk`,
  `sed`, `timeout`. The unit tests run the same commands on the local machine
  (macOS or Linux) through the fake client, so use only portable POSIX options
  (no GNU-only flags such as `find -printf` or `grep -P`), or a short
  `python3` script.

## Tests: `tests/lab1/test_tools.py`

Create `tests/lab1/__init__.py` and the tests. Use `FakeSandboxClient` from
`tests/unit/fakes.py` with a real `SandboxSession`, and write a small sample
bundle (a log file, a config file, a subdirectory) into `/workspace/bundle`
with `session.write_file` in a fixture. Cover at least:

- `schemas()` returns four valid strict function schemas.
- `list_files`, `read_file`, `grep` return the expected lines in the
  `path:line: text` format, with relative paths.
- Limits: long files and many matches are capped, with a note.
- `run_python` reads a bundle file by relative path; a script that loops
  forever is stopped by the timeout (use a registry with
  `timeout_seconds=2`, so the test stays fast); a failing script reports its
  exit code.
- `call` returns `ERROR:` messages for an unknown tool, invalid JSON, a
  missing argument and a path such as `../../etc/passwd` or `/etc/passwd`.
- A pattern or path containing shell metacharacters (`; rm -rf x`, `$(...)`)
  is treated as plain text.
- A symbolic link to a file outside the bundle (create it with
  `session.run("ln -s ...")`): `list_files(".")` and `grep(".")` still return
  the other files plus a `[skipped: ...]` note; `read_file` on the link
  returns `ERROR:`.
- A binary file containing the pattern is not reported by `grep`.
- Successful results contain no status header; every error output contains
  exactly one `ERROR:` and no absolute path.

## Done

Run `python -m pytest tests/unit tests/lab1 -q`, `python -m black --check
triage tests` and `python -m pylint triage`, fix any problem, and report what
you built, the commands you ran and their results.
