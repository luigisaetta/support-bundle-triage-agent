# Lab 1 — Sandbox tools

**Time:** about 30 minutes.
**You build:** `triage/tools.py`, with Codex.
**Runs on:** your machine, against a fake sandbox. No OCI calls.

## Goal

The triage agent never runs code on your machine and never sees the whole
bundle at once. It explores the bundle by calling a few small **function
tools**, and every tool runs inside the OCI Sandbox. In this lab you build
those tools:

| Tool | What it gives the model |
|------|-------------------------|
| `list_files` | the bundle tree, with file sizes |
| `read_file` | a range of lines of one file |
| `grep` | the lines matching a regular expression |
| `run_python` | the output of a short analysis script |

Every result that points into the bundle uses the format `path:line: text`.
In Lab 3 the triage report cites its evidence in the same format.

## Why it matters

- **Isolation.** A support bundle comes from a customer system: it is
  untrusted data. Commands on it run in the sandbox, not on your laptop, and
  the sandbox has no outbound network.
- **Bounded output.** A log can be gigabytes; the model context cannot.
  Each tool returns at most a few thousand characters, and says what it cut.
- **Errors the model can use.** A tool never crashes the agent: a wrong path
  or a timeout comes back as a short `ERROR:` message that tells the model how
  to retry.
- **Untrusted input from the model, too.** Paths and patterns come from the
  model, which reads the bundle. The tools reject paths outside the bundle,
  never follow symbolic links that leave it, and never let a pattern become a
  shell command.

## What is provided

- `triage/sandbox.py` — `SandboxSession`: `run(command, timeout_seconds)`,
  `write_file(path, data)`, `read_file(path)`. You use it; you do not change it.
- `tests/unit/fakes.py` — `FakeSandboxClient`: runs the sandbox commands on
  your machine in a temporary directory, so the tests are fast and free.
- `tests/lab1/` — the acceptance tests of this lab.
- `AGENTS.md` — the rules Codex follows in this repository.

## Steps

### 1. See the tests fail

```bash
python -m pytest tests/lab1 -q
```

They fail with `ModuleNotFoundError: triage.tools`: the module does not exist
yet. Your job is to make them pass.

### 2. Ask Codex to build the tools

Open Codex in the repository root and enter:

```
Read prompts/lab1.md and do what it says (everything below the first line).
```

The prompt gives Codex the interface (`Tool`, the four tools,
`ToolRegistry`), the output format with examples, the security rules and the
tests to pass. Codex writes `triage/tools.py`, runs the tests, black and
pylint, and reports.

### 3. Review what Codex wrote

Open `triage/tools.py` and check:

- Each tool has its own class, with a `schema()` for the model and a `run()`
  that builds one short command for the sandbox.
- `ToolRegistry.call()` catches every error and returns `ERROR: ...`.
- Values from the model reach the shell only through `shlex.quote`;
  `run_python` writes the code to a file in `/workspace/scratch` and runs it.
- Every command has a timeout.

### 4. Run the checks

```bash
python -m pytest tests/unit tests/lab1 -q
python -m black --check triage tests
python -m pylint triage
```

All tests pass, black reports nothing, pylint reports no problems.

### 5. Try the tools by hand (optional)

```python
import tempfile
from pathlib import Path

from tests.unit.fakes import FakeSandboxClient
from triage.sandbox import SandboxSession
from triage.tools import ToolRegistry

session = SandboxSession(FakeSandboxClient(Path(tempfile.mkdtemp())), "sbx-fake")
session.write_file("/workspace/bundle/logs/app.log", "start\nORA-04031 shared pool\nend\n")
tools = ToolRegistry(session)

print(tools.call("list_files", {"path": "."}))
print(tools.call("grep", '{"pattern": "ORA-[0-9]+", "path": "."}'))
print(tools.call("read_file", {"path": "../../etc/passwd", "start_line": 1, "end_line": 3}))
```

The second call passes the arguments as a JSON string, as the model will do in
Lab 2. The third one shows the error the model receives for a path outside the
bundle.

## Done when

- `python -m pytest tests/unit tests/lab1 -q` passes.
- You can explain what each tool returns and why its output is bounded.

## Stuck?

Copy the reference solution and go on to Lab 2:

```bash
cp solutions/lab1/tools.py triage/tools.py
```
