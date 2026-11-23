# The easy path: run the demo with the reference solutions

In the labs you build four files with Codex: `triage/tools.py` (Lab 1),
`triage/agent.py` (Lab 2), `triage/report.py` and `triage/cli.py` (Lab 3).
Reference versions of all of them are in `solutions/`. On the easy path you
copy them into `triage/` and go straight to a real investigation, without
Codex.

Choose it when you want to see the agent at work first, when time is short,
or when you only want to try one lab and need the others done.

## 1. Prepare the environment

Follow the README from the start to "Configure the API key and the agent"
(steps 1–5): Python environment, dependencies, OCI project, API key. The easy
path skips only the coding, not the setup.

## 2. Run the preflight

Do not skip this step: it finds a configuration problem in seconds, with a
hint on how to fix it, instead of after minutes of waiting for a sandbox.

```bash
python -m triage.preflight --quick
```

The quick preflight checks the local environment, the configuration, the OCI
credentials, the project (server-side conversation included), the
permissions and the model's tool calling. It creates no sandbox. Fix any
failed check and rerun it before going on.

Recommended, if you have a few minutes:

```bash
python -m triage.preflight
```

The full preflight also creates a test sandbox, checks it and deletes it.
You can skip it on the easy path, because the CLI creates its own sandbox in
step 5: a sandbox problem would then show up there.

## 3. Copy the solutions

From the repository root:

```bash
./copy_solutions.sh all
```

It copies the four files into `triage/`. A file that is already there (for
example code you built with Codex) is kept: the script says so. To replace
it with the reference version, add `--force`:

```bash
./copy_solutions.sh all --force
```

You can also copy one lab at a time: `./copy_solutions.sh lab1`, `lab2` or
`lab3`. Each lab needs the files of the labs before it.

## 4. Check that everything works

```bash
python -m pytest tests -q
```

All the tests pass. They run locally with fakes: no sandbox and no model
call.

## 5. Run a real investigation

Create the sample bundle, then run the triage on a new sandbox:

```bash
python -m triage.make_bundle
```

```bash
python -m triage.cli bundles/sample-ora00020.zip
```

Starting a sandbox can take several minutes. The CLI shows each step, then
a panel with the summary, the root cause, the confidence and how many cited
lines were verified. The report is in `out/sample-ora00020-report.md`.
[The sample bundle](sample-bundle.md) lists what a good report finds.

## 6. Clean up

The CLI deletes the sandbox it created. If the service was slow to stop it,
or if you used `--keep`, remove the leftovers:

```bash
python -m triage.cleanup --delete
```

## Going back to the labs

The copied files are not committed (they are in `.gitignore`). To build them
yourself with Codex, remove them and start from Lab 1:

```bash
./clean.sh all
```

Then follow the [Lab 1 guide](../labs/lab1.md).
