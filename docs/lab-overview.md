# Support Bundle Triage Agent — Lab Overview

Build an AI agent that diagnoses a customer support bundle. The model is reached
through the OpenAI Responses API (on OCI Generative AI or on OpenAI); every
command the agent runs executes inside an isolated OCI Sandbox. You write the
code with Codex, guided by `AGENTS.md`, the lab guides and acceptance tests.

**Total time:** about **40–60 minutes**, including time to read the guides and
review the generated code. Sandbox creation may occasionally take longer.
Each lab normally takes 5–7 minutes of hands-on work; the total estimate allows
additional time for reading and review.

| Lab | Topic | You build | Runs on | Hands-on time |
|-----|-------|-----------|---------|------|
| 0 | Setup and preflight | nothing (provided) | OCI | 5–7 min |
| 1 | Sandbox tools | `triage/tools.py` | local fake | 5–7 min |
| 2 | Agent loop | `triage/agent.py` | local fake + model | 5–7 min |
| 3 | Report and end-to-end run | `triage/report.py`, `triage/cli.py` | OCI Sandbox + model | 5–7 min |

Each lab has four parts:

| Part | Where |
|------|-------|
| Guide for the participant | `labs/labN.md` |
| Prompt for Codex | `prompts/labN.md` |
| Acceptance tests | `tests/labN/` |
| Reference solution | `solutions/labN/` |

Read the guide, give the prompt to Codex, review what it writes, run the
tests. The code you build stays in `triage/`, and each lab builds on the
previous ones. If you fall behind, copy `solutions/labN/` into `triage/` and
move on.

---

## Lab 0 — Setup and preflight

**Goal:** a working environment and an OCI project ready for the agent.

**Steps:**
1. Create the Python environment and install the requirements.
2. Create the Generative AI project: conversations on, short-term memory on
   (compaction model `openai.gpt-oss-120b`), long-term memory off, sandboxes
   on with `MANAGED` network and `DENY_ALL`.
3. Put your API key in `.env` (secrets only); set model, region and project
   OCID in `config/settings.toml`.
4. Run `python -m triage.preflight --quick`: configuration, OCI credentials,
   project settings, permissions, and a model call that continues on the
   server.
5. Run `python -m triage.preflight`: a test sandbox starts, runs commands,
   moves a file, cannot reach the internet, and is deleted. Creation and
   deletion together normally take less than 20 seconds. Creation may
   occasionally take several minutes: read Lab 1 meanwhile.
6. Read `AGENTS.md`: the rules Codex follows in this repo.

**You learn:** Generative AI projects (conversations, short-term memory,
sandboxes), managed network policy (`DENY_ALL`), the Responses API on OCI vs
OpenAI, secrets vs configuration.

**Done when:** all preflight checks pass (a slow cleanup warning is fine).

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
Paths outside the bundle and symbolic links that leave it are refused.

**You learn:** function tool design, why the model never runs code locally,
timeouts and output limits in the sandbox, model input as untrusted input.

**Done when:** `pytest tests/unit tests/lab1` passes (against the local fake).

---

## Lab 2 — Agent loop

**Goal:** an agent that investigates the bundle by calling the tools.

**Build with Codex:** `triage/agent.py`, a loop on the Responses API:
send the task and the tool schemas, execute the requested function calls in the
sandbox, return the results, repeat until the model answers or the step budget
runs out. The conversation is kept on the server: after the first call, only
`previous_response_id` and the new tool outputs are sent. Tool errors are
returned to the model, not raised.

**You learn:** function calling with Responses, server-side conversation
(`store=True`, `previous_response_id`), several tool calls per step, step
budgets, treating bundle content as untrusted data (prompt injection).

**Done when:** `pytest tests/lab2` passes; optionally, watch the agent
investigate a small bundle with the real model.

---

## Lab 3 — Report and end-to-end run

**Goal:** a triage report from the sample bundle, on a real OCI Sandbox.

**Before:** `python -m triage.make_bundle` writes
`bundles/sample-ora00020.zip`, a customer case with a known root cause, red
herrings and a line that tries to give orders to the AI assistant.

**Build with Codex:**
- `triage/report.py` — the report is one more turn of the same server-side
  conversation, with structured output (strict JSON schema): root cause,
  confidence, evidence as `file:line`, timeline, next steps. Each cited line
  is read in the sandbox and marked ✔ or ✘. Rendered as Markdown.
- `triage/cli.py` — `python -m triage.cli bundles/<bundle>.zip`: create a
  sandbox, upload the bundle, run the agent, write the report to `out/`,
  clean up.

**Steps:** run the CLI on the sample bundle with `--keep` (sandbox creation
may occasionally take longer); compare the report with the known root cause;
run it again with `--sandbox-id` to reuse the same sandbox without waiting;
optionally take a snapshot with `--snapshot`.

**You learn:** structured outputs, continuing a stored conversation,
evidence-based reports, sandbox lifecycle (create, attach, snapshot,
cleanup).

**Done when:** `pytest tests/lab3` passes and the report names the root
cause of the sample bundle with verified evidence.
