# Set up the workshop with Python venv

Use this guide as an alternative to the Conda setup in the
[main README](../README.md). It takes you through the same preparation using
Python's built-in `venv` module. Run **one command at a time** and complete
each step before continuing.

The commands below use Bash or Zsh on macOS or Linux. On Windows, use WSL
and run the commands inside your Linux environment.

## Before you start

Have these ready:

- Python 3.12 installed and available as `python3.12`. The project requires
  Python 3.11 or later; this guide uses 3.12 to match the main walkthrough.
- Git and Codex.
- Access to an OCI compartment where you can create Generative AI projects
  and sandboxes.
- The OCI CLI installed and credentials configured in `~/.oci/config`.
- An OCI Generative AI API key, or an OpenAI API key for the model.

You need OCI for the sandboxes even if the model provider is OpenAI.
The [Lab 0 guide](../labs/lab0.md) explains the OCI prerequisites and project
settings in more detail.

## 1. Get the workshop files

Clone the repository. Skip this command if you already have a checkout:

```bash
git clone https://github.com/luigisaetta/support-bundle-triage-agent.git
```

Enter the repository directory, or open your existing checkout. Run all
subsequent setup commands from this directory:

```bash
cd support-bundle-triage-agent
```

## 2. Check the Python interpreter

Start in a terminal with no other virtual environment active. Confirm that
the Python interpreter you want to use is installed:

```bash
python3.12 --version
```

You should see `Python 3.12.x`. If your supported interpreter has another
command name, use that name in this check and in the environment creation
command below. `venv` uses an existing Python installation; it does not
install a Python version for you.

## 3. Create the virtual environment

Create a local environment in `.venv`:

```bash
python3.12 -m venv .venv
```

This creates a directory for this project's Python environment and packages.
The repository already excludes `.venv/` from version control. Create the
environment once per checkout; you will activate it again in future sessions.

If Python reports that `venv` or `ensurepip` is unavailable, install your
operating system's venv package for the selected Python version, then retry.

## 4. Activate the environment

Activate `.venv` in your current terminal:

```bash
source .venv/bin/activate
```

Your prompt will usually show `(.venv)`. From this point, use `python` for
the commands in this guide and in the labs.

Check which interpreter the terminal will use:

```bash
python -c "import sys; print(sys.executable)"
```

The printed path should point to `.venv/bin/python` inside your checkout.
If it points elsewhere, activate the environment before installing packages
or running the labs.

## 5. Install the dependencies

Install the application packages and the test, formatting and lint tools
into the active environment:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Wait for the installation to finish successfully. Using `python -m pip`
ensures that packages are installed for the same interpreter you will use
to run the agent.

The OCI CLI is a separate prerequisite. Confirm that it is still available
in this terminal:

```bash
oci --version
```

## 6. Create the OCI Generative AI project

The project holds the conversations and sandboxes used by the agent. The
configuration files below enable server-side conversations, short-term
memory and sandboxes with outbound traffic denied.

Replace `YOUR_COMPARTMENT_OCID` with your compartment OCID, then run this
single command. The backslashes continue it across lines:

```bash
oci generative-ai generative-ai-project create \
  --compartment-id YOUR_COMPARTMENT_OCID \
  --display-name support-triage \
  --conversation-config file://config/project-conversation.json \
  --short-term-memory-optimization-config file://config/project-short-term-memory.json \
  --sandbox-config file://config/sandbox-project-deny-all.json
```

Save the **project OCID** returned by the command. You will put it in the
configuration file in the next step.

You can also create the project in the OCI Console using
[Lab 0, step 2](../labs/lab0.md#2-create-the-generative-ai-project). If you
already have a project, check that it has the settings described there
before reusing it.

## 7. Configure the API key and the agent

Create your local secrets file from the example. Skip this command if
`.env` already exists, so you preserve your existing credentials:

```bash
cp .env.example .env
```

Open `.env` in your editor and set `OCI_GENAI_API_KEY`, or `OPENAI_API_KEY`
if you use OpenAI for the model. Never commit this file.

The two similarly named paths serve different purposes: `.venv/` contains
the Python environment, while `.env` contains your API keys.

Open `config/settings.toml` and fill in the configuration:

| Section | What to set |
|---------|-------------|
| `[model]` | `provider` (`oci` or `openai`) and `model_id` |
| `[oci]` | `region`, your `project_id`, and the OCI SDK `profile` and `auth` |
| `[sandbox]` | Keep the workshop defaults to start with |

The OCI region and credential profile must match the project you will use.
See [Lab 0, step 3](../labs/lab0.md#3-configure-the-agent) for more context.

## 8. Check the configuration and model

With `.venv` active, run the quick preflight. It checks the local environment,
configuration, OCI credentials, project, permissions and model tool calling.
It calls the model but **does not create a sandbox**:

```bash
python -m triage.preflight --quick
```

If a check fails, follow the printed hint, fix the problem and rerun this
command before continuing. If the hint mentions Conda activation, use the
venv activation command from step 4 instead.

## 9. Check the sandbox

Run the full preflight to create a test sandbox, check its tools and file
transfer, verify network isolation, and clean it up:

```bash
python -m triage.preflight
```

Starting and stopping a sandbox can take several minutes. The preflight
shows progress while you wait. Continue when the checks pass.

A warning that the sandbox is still stopping is acceptable. Delete it later
with `python -m triage.cleanup --delete`.

## 10. Continue with the labs

Read [AGENTS.md](../AGENTS.md), then start with the [Lab 1 guide](../labs/lab1.md).
Keep `.venv` active while working through Labs 1–3. The `python` commands
in the guides work with this environment too.

After completing Lab 1, run its acceptance tests and the infrastructure tests:

```bash
python -m pytest tests/unit tests/lab1 -q
```

These tests use fakes and make no OCI or model calls. Use each subsequent
lab's test command as you progress. The
[main README](../README.md#run-your-finished-agent) explains how to run the
finished agent after Lab 3.

## Return to the workshop in a new terminal

First, enter your checkout. Replace `/path/to/support-bundle-triage-agent`
with its actual location on your machine:

```bash
cd /path/to/support-bundle-triage-agent
```

Activate the existing environment again:

```bash
source .venv/bin/activate
```

You can now continue with the lab commands. You do not need to recreate
the environment or reinstall its packages for each session.

When you finish working in this terminal, leave the environment with:

```bash
deactivate
```

Deactivation changes your local shell's Python environment. Follow the
[Lab 3 cleanup instructions](../labs/lab3.md#7-clean-up) to clean up any
sandbox you chose to keep.
