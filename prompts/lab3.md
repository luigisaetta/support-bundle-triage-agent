# Lab 3 — Prompt for Codex: triage report and CLI

Paste everything below the line into Codex, from the repository root.

---

Read `AGENTS.md` and follow it. Then build Lab 3: the structured triage
report and the command line that runs a whole investigation on a real OCI
Sandbox. Do not change any provided file, and do not change
`triage/tools.py` or `triage/agent.py`.

## Context

- `triage/agent.py` (Lab 2): `TriageAgent.from_settings(settings, tools,
  max_steps=..., on_event=...)`, `run(task) -> AgentResult` with `answer`,
  `response_id`, `stopped_reason`, `steps`, `tool_calls`, `input_tokens`,
  `output_tokens`. The conversation is stored on the server (`store=True`).
- `triage/tools.py` (Lab 1): `ToolRegistry(session)`, `call(name,
  arguments) -> str`; `read_file` returns lines as `path:line: text`.
- `triage/ingest.py` (provided): `BundleUploader(session).upload(path) ->
  BundleSummary` checks a zip locally (without extracting it), uploads it and
  extracts it in the sandbox under `/workspace/bundle`; raises `BundleError`.
  `BundleSummary.describe()` gives one line for the console.
- `triage/sandbox.py` (provided): `SandboxManager.from_settings(settings,
  OciClients(settings))`, `create(display_name=..., on_progress=...)`,
  `attach(sandbox_id)`; `SandboxSession.snapshot() -> str`,
  `close(on_progress=...)` (stops and deletes only a sandbox the session
  created), `sandbox_id`. Creating a sandbox can take minutes: always pass
  `on_progress`.
- `triage/make_bundle.py` (provided): `python -m triage.make_bundle` writes
  `bundles/sample-ora00020.zip`, the sample bundle.

## How the report is produced (verified on OCI)

The report is one more turn of the **same server-side conversation**: the
model already has every tool result, so the client sends nothing but a
request and the id of the last response:

```python
client.responses.create(
    model=model_id,
    previous_response_id=agent_result.response_id,
    input=REPORT_REQUEST,
    text={"format": {"type": "json_schema", "name": "triage_report",
                     "schema": REPORT_SCHEMA, "strict": True}},
    store=True,
)
```

The answer is a JSON document that follows `REPORT_SCHEMA` (the `text` of the
`output_text` parts of the `message` items).

## What to build (1): `triage/report.py`

- `REPORT_SCHEMA`: strict JSON schema (every property in `required`,
  `additionalProperties: false` at every level) with:
  `summary` (string), `root_cause` (string), `confidence` (`"high"`,
  `"medium"` or `"low"`), `evidence` (array of `{location, observation}`;
  `location` is `path:line`, `path:line-line` or `path:line,line`),
  `timeline` (array of `{time, event, evidence}`), `next_steps` (array of
  strings).
- `REPORT_REQUEST`: the request to the model: write the triage report from
  the investigation, citing only evidence found with the tools.
- Frozen dataclasses `Evidence` (`location`, `observation`,
  `verified: bool | None = None`), `TimelineEvent`, `TriageReport`, with
  `TriageReport.from_dict(data)` and `to_dict()`.
- `ReportError(Exception)`: the answer is not valid JSON or does not match the
  schema.
- `ReportWriter(client, model_id)`, `from_settings(settings)`,
  `write(result: AgentResult) -> TriageReport`: the call above.
- `EvidenceChecker(tools: ToolRegistry)`, `check(report) -> TriageReport`:
  for each evidence, reads the first cited line with the `read_file` tool and
  sets `verified` to `True` when the line exists, `False` otherwise (missing
  file, line past the end, location that cannot be parsed). It never raises.
  This shows which evidence really exists in the bundle.
  **Parse locations tolerantly**: the model writes them in many ways, and a
  correct citation must not be marked ✘ because of its format. Strip
  surrounding backticks, quotes and spaces; accept `-`, `–` and `—` for
  ranges; accept spaces after commas; accept lists that mix lines and ranges.
  All of these must be verified when line 2 exists:
  `db/alert_orcl.log:2`, `` `db/alert_orcl.log:2` ``,
  `db/alert_orcl.log:2-4`, `db/alert_orcl.log:2–4`,
  `db/alert_orcl.log:2, 9`, `db/alert_orcl.log:2,29-34`.
  The first number after the last `:` is the line to check.
- `render_markdown(report, metadata: dict[str, str]) -> str`: a readable
  report: title, a metadata table (bundle, date, model, sandbox, steps, tool
  calls, tokens), Summary, Root cause with confidence, an Evidence table
  (location, observation, verified ✔ / ✘), a Timeline table, Next steps as
  a numbered list.

## What to build (2): `triage/cli.py`

```
python -m triage.cli BUNDLE [--sandbox-id ID] [--keep] [--snapshot]
                            [--max-steps N] [--task TEXT] [--output-dir DIR]
```

- `TriageApp(settings, manager, model_client, output_dir, stream)`, with
  `run(bundle, *, sandbox_id=None, keep=False, snapshot=False, max_steps=...,
  task=...) -> Path` (the Markdown report). `main()` builds the real
  dependencies from `Settings.load()`, parses the arguments, runs the app and
  returns the exit code.
- The run, with numbered steps printed to `stream` so the user always knows
  what is being waited for:
  1. **Sandbox**: attach to `--sandbox-id`, or create a new one, printing the
     state while it starts ("a cold start can take minutes").
  2. **Bundle**: upload and extract it; print `summary.describe()`.
  3. **Investigation**: run the agent; print each `on_event` line.
  4. **Report**: `ReportWriter`, then `EvidenceChecker`; write
     `<output-dir>/<bundle-stem>-report.md` and `-report.json`; print both
     paths, the root cause, the confidence and how many evidence lines were
     verified.
  5. **Cleanup**: with `--snapshot`, take a snapshot and print its id. Then
     close a sandbox created by this run, unless `--keep`: in that case print
     its id and the command to reuse it (`--sandbox-id`). Never close a
     sandbox given with `--sandbox-id`.
- **Progress while waiting**: a sandbox can take many minutes to start or
  stop, and the state is polled every few seconds. Do not print a new line
  for each state message. When `stream` is a terminal, rewrite the same line
  in place (`\r` and clear to end of line) and end it with a newline when the
  step is over; otherwise print a state line at most every 30 seconds, and
  whenever the state changes.
- **A slow cleanup is a warning, not a failure**: the service can take more
  than five minutes to stop a sandbox, and `close()` then raises
  `SandboxError` ("still STOPPING..."). The report is already written: print
  a warning with the sandbox id and "delete it from the OCI Console once it is
  STOPPED", and exit 0.
- Defaults: `--output-dir out`, `--max-steps` 15, a default `--task` that
  asks to find the root cause of the issue described in the bundle.
- Validate the settings first (`settings.validate()`): print the problems and
  exit 1.
- The cleanup also runs after an error or Ctrl+C. Expected errors
  (`BundleError`, `SandboxError`, `ReportError`, `openai.APIError`) print one
  clear line and exit 1, without a traceback.

## Tests: `tests/lab3/` (already written: read them first)

The acceptance tests of this lab are already in `tests/lab3/`. They are
the exact contract: besides the interface above, they import or patch a
few more names (constants, helper functions, names imported in the
module). Read them **before** writing code, then make them pass **without
changing them**. If you want more tests, add them in a new file,
`tests/lab3/test_extra.py`.

The tests never call OCI or the model:

- Model: a fake client whose `responses.create(**kwargs)` returns scripted
  responses and records its arguments (output items built with the real
  openai types, as in `tests/lab2`).
- Sandbox: a real `SandboxManager` and `SandboxSession` over
  `FakeSandboxClient` (`tests/unit/fakes.py`); bundles built with
  `zipfile` in `tmp_path` or with `triage.make_bundle.SampleBundle`.

They cover:

- `ReportWriter`: the request has `previous_response_id`, `store=True` and
  the strict `json_schema` format; valid JSON becomes a `TriageReport`;
  invalid JSON raises `ReportError`.
- `REPORT_SCHEMA` is strict at every level.
- `EvidenceChecker`: every location format listed above is verified when the
  line exists; a missing file, a line past the end and a malformed location
  are not, and nothing raises.
- `render_markdown`: all sections and the ✔ / ✘ marks are present.
- `TriageApp.run` end to end with the fakes: the report files are written;
  a created sandbox is closed; with `keep=True` it is not, and its id is
  printed; an attached sandbox is never closed; a bad bundle prints one
  error line and still runs the cleanup; a `close()` that raises
  `SandboxError` prints a warning and the run still succeeds.
- Progress: many state messages on a non-terminal stream produce only a few
  lines (use a stream whose `isatty()` returns False, and patch the clock).

## Done

Run `python -m pytest tests/unit tests/lab1 tests/lab2 tests/lab3 -q`,
`python -m black --check triage tests` and `python -m pylint triage`, fix
any problem, and report what you built, the commands you ran and their
results. Do not run the CLI against OCI: creating a sandbox takes minutes,
and the user will run it.
