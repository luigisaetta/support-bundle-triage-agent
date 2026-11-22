# Lab 3 — Report and end-to-end run

**Time:** about 35 minutes, plus the wait for a sandbox to start.
**You build:** `triage/report.py` and `triage/cli.py`, with Codex.
**Runs on:** a real OCI Sandbox and the real model.

## Goal

Put everything together: one command takes a support bundle, runs the
investigation in an OCI Sandbox and writes a triage report.

```
python -m triage.cli bundles/sample-ora00020.zip
```

1. **Sandbox** — create one (or attach to an existing one).
2. **Bundle** — check the zip locally, upload it, extract it in the sandbox.
3. **Investigation** — the agent of Lab 2 explores the bundle with the tools
   of Lab 1.
4. **Report** — a structured report, with every cited line checked in the
   bundle.
5. **Cleanup** — delete the sandbox, keep it for another run, or snapshot it.

## Why it matters

- **The report is one more turn of the same conversation.** The model already
  holds every tool result on the server: the client sends only
  `previous_response_id` and a request for the report, and nothing is resent.
- **Structured output.** The report follows a strict JSON schema (root cause,
  confidence, evidence, timeline, next steps), so a program can use it, and a
  Markdown version is rendered for people.
- **Evidence you can check.** For each citation such as
  `db/alert_orcl.log:29`, the CLI reads that line in the sandbox and marks it
  ✔ or ✘. A ✘ means the model cited a line that does not exist.
- **Sandbox lifecycle.** Creating a sandbox takes minutes; `--keep` and
  `--sandbox-id` let you reuse one; `--snapshot` saves the workspace of the
  investigation.

## Before you start

Lab 3 builds on your `triage/tools.py` (Lab 1) and `triage/agent.py`
(Lab 2). Check them:

```bash
python -m pytest tests/lab1 tests/lab2 -q
```

If the tests fail or a file is missing, copy the reference solutions:

```bash
cp solutions/lab1/tools.py solutions/lab2/agent.py triage/
```

Then write the sample bundle:

```bash
python -m triage.make_bundle
```

It writes `bundles/sample-ora00020.zip`: logs, configuration, metrics and a
change log of a customer case, with a known root cause, some red herrings and
a line that tries to give orders to the AI assistant.

## Steps

### 1. See the tests fail

```bash
python -m pytest tests/lab3 -q
```

They fail with `ModuleNotFoundError`: `triage.report` and `triage.cli` do
not exist yet.

### 2. Ask Codex to build the report and the CLI

Open Codex in the repository root and enter:

```
Read prompts/lab3.md and do what it says (everything below the first line).
```

The tests use a fake model and a fake sandbox: Codex does not start a real
sandbox. You will run the CLI yourself in step 5.

### 3. Review what Codex wrote

- `triage/report.py`: `ReportWriter.write()` sends `previous_response_id`,
  `store=True` and the strict JSON schema; `EvidenceChecker` reads the first
  cited line of each evidence and never raises.
- `triage/cli.py`: the five numbered steps; the cleanup runs also after an
  error or Ctrl+C; a sandbox given with `--sandbox-id` is never deleted.

### 4. Run the checks

```bash
python -m pytest tests/unit tests/lab1 tests/lab2 tests/lab3 -q
python -m black --check triage tests
python -m pylint triage
```

### 5. Run the triage on a real sandbox

```bash
python -m triage.cli bundles/sample-ora00020.zip --keep
```

The sandbox start can take several minutes: the CLI shows its state while
you wait. It opens with a banner and shows the five steps as they run;
because of `--keep`, the cleanup step prints the id of the sandbox and the
command to reuse it. It ends with a result panel: the summary, the root
cause, the confidence and how many cited lines were verified.

Open the report:

```bash
open -t out/sample-ora00020-report.md
```

Check it:

- Does the root cause name the change that lowered the database `processes`
  limit, and the gap between that limit and the connection pools?
- Are all cited lines verified (✔)?
- Did the model ignore the red herrings (disk usage, GC pauses, deprecated
  setting)?
- Did it follow the line addressed to the AI assistant? It must not.

### 6. Run again without waiting (optional)

Reuse the sandbox from step 5 — no start-up wait:

```bash
python -m triage.cli bundles/sample-ora00020.zip --sandbox-id <sandbox-id>
```

Try `--snapshot` to save the workspace, or `--task` to ask a different
question about the same bundle.

### 7. Clean up

A sandbox kept with `--keep` expires after `sandbox.lifetime_minutes` (60 by
default) and becomes `STOPPED`, but it is not deleted. Without `--keep`, the
CLI deletes the sandbox at the end; if the service is slow to stop it, the
CLI prints a warning and the sandbox is left behind. Leftover sandboxes use
quota. List the sandboxes of your project:

```bash
python -m triage.cleanup
```

Then remove them (the command shows the list and asks for a confirmation):

```bash
python -m triage.cleanup --delete
```

It deletes the sandboxes that are `STOPPED` or `FAILED` and asks the running
ones to stop, without waiting: stopping can take several minutes. Run it
again later to delete those that were running. It considers every sandbox of
the project, so run it only on the project you created for the labs.

## Done when

- `python -m pytest tests/unit tests/lab1 tests/lab2 tests/lab3 -q` passes.
- `out/sample-ora00020-report.md` names the right root cause with verified
  evidence.
- You can explain why the report request sends no history, and what a ✘ in
  the evidence table would mean.

## Stuck?

Copy the reference solution and run step 5:

```bash
cp solutions/lab3/report.py solutions/lab3/cli.py triage/
```
