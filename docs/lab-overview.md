# Support Bundle Triage Agent — Lab Overview

Build an AI agent that diagnoses a customer support bundle. The model is reached
through the OpenAI Responses API (on OCI Generative AI or on OpenAI); every
command the agent runs executes inside an isolated OCI Sandbox. You write the
code with Codex, guided by `AGENTS.md`, the lab guides and acceptance tests.

**Total time:** about 2 hours.

| Lab | Topic | You build | Runs on | Time |
|-----|-------|-----------|---------|------|
| 0 | Setup and preflight | nothing (provided) | OCI | 20 min |
| 1 | Sandbox tools | `triage/tools.py` | local fake | 30 min |
| 2 | Agent loop | `triage/agent.py` | local fake + model | 35 min |
| 3 | Report and end-to-end run | `triage/report.py`, `triage/cli.py` | OCI Sandbox | 35 min |

Each lab follows the same pattern: read the guide in `labs/`, paste the prompt
from `prompts/` into Codex, review what it writes, run the tests in
`tests/labN/`. If you fall behind, copy `solutions/labN/` and move on.

---

## Lab 0 — Setup and preflight

**Goal:** a working environment, and a sandbox that is already running when you
need it.

**Steps:**
1. Create the Python environment and install the requirements.
2. Put your API key in `.env` (secrets only); set model, region and project
   OCID in `config/settings.toml`.
3. Run `python -m triage.preflight --quick`: configuration, OCI credentials,
   project, permissions, model call.
4. Run `python -m triage.preflight --keep`: it creates a sandbox and keeps it
   running for Lab 3. A cold start can take minutes; continue with Lab 1 while
   it starts.
5. Read `AGENTS.md`: the rules Codex follows in this repo.

**You learn:** OCI Generative AI projects with sandboxes enabled, managed
network policy (`DENY_ALL`), the Responses API on OCI vs OpenAI.

**Done when:** all preflight checks pass and you have a sandbox id.

---

## Lab 1 — Sandbox tools

**Goal:** give the model safe, small tools to explore a bundle.

**Build with Codex:** `triage/tools.py`, function tools on top of the provided
`SandboxSession`:
- `list_files` — bundle tree with sizes
- `read_file` — a line range of a file
- `grep` — matches with `file:line`
- `run_python` — run a short analysis script in `/workspace`

Each tool has a JSON schema for the model and returns short, bounded output.

**You learn:** function tool design, why the model never runs code locally,
timeouts and output limits in the sandbox.

**Done when:** `pytest tests/unit tests/lab1` passes (against the local fake).

---

## Lab 2 — Agent loop

**Goal:** an agent that investigates the bundle by calling the tools.

**Build with Codex:** `triage/agent.py`, a loop on the Responses API:
send the task and the tool schemas, execute the requested function calls in the
sandbox, return the results, repeat until the model answers or the step budget
runs out. Tool errors are returned to the model, not raised.

**You learn:** function calling with Responses, `store=False`, step budgets,
treating bundle content as untrusted data (prompt injection).

**Done when:** `pytest tests/lab2` passes, and the agent investigates the
sample bundle with the local fake.

---

## Lab 3 — Report and end-to-end run

**Goal:** a triage report from a real bundle, on a real OCI Sandbox.

**Build with Codex:**
- `triage/report.py` — structured output (JSON schema): root cause, evidence
  as `file:line`, timeline, confidence, next steps; rendered as Markdown.
- `triage/cli.py` — `python -m triage.cli bundles/<bundle>.zip`: upload the
  bundle, run the agent, write the report to `out/`.

**Steps:** run the CLI on the sample bundle, attached to the sandbox from
Lab 0; compare the report with the known root cause; optionally take a
snapshot of the sandbox to keep the investigation.

**You learn:** structured outputs, evidence-based reports, sandbox lifecycle
(attach, snapshot, cleanup).

**Done when:** `pytest tests/lab3` passes and the report finds the root cause
of the sample bundle with correct evidence.
