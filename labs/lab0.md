# Lab 0 — Setup and preflight

**Time:** about 20 minutes, plus the wait for a sandbox to start.
**You build:** nothing; everything in this lab is provided.
**Runs on:** your machine and OCI.

## Goal

Prepare everything the next labs need, and prove that it works:

- a Python environment with the required packages;
- an OCI Generative AI project with server-side conversations, short-term
  memory, and sandboxes with no outbound network;
- the configuration of the agent (model, region, project);
- a model that answers through the Responses API and calls a function tool;
- a sandbox that starts, runs commands, moves files and cannot reach the
  internet.

The **preflight** (`python -m triage.preflight`) checks all of this in one
run and tells you what to fix.

## Concepts

- **OCI Generative AI project.** Conversations and sandboxes belong to a
  project. The project decides how long conversations are kept, whether
  sandboxes are enabled and what network they get.
- **Server-side conversation.** The agent calls the Responses API with
  `store=True`: OCI keeps the conversation, and each call continues it with
  `previous_response_id` instead of resending the whole history.
- **Short-term memory.** As an investigation grows, OCI condenses the older
  part of the conversation with a compaction model
  (`openai.gpt-oss-120b`), so long conversations stay within the context of
  the model. We enable only short-term memory; long-term memory (across
  conversations) is not used.
- **Network policy.** We use `MANAGED` networking with `DENY_ALL`: code in the
  sandbox cannot reach the internet, so a hostile bundle cannot send data out.
  The policy limits only outbound traffic; your machine still drives the
  sandbox through the OCI API. The policy cannot be changed after the project
  is created.
- **Responses API on two providers.** The agent uses the official `openai`
  library. With `provider = "oci"` it calls OCI Generative AI (an OpenAI GPT
  model hosted on OCI); with `provider = "openai"` it calls OpenAI. Only the
  configuration changes, not the code.
- **Secrets vs configuration.** API keys go in `.env`, which is never
  committed. Everything else is in `config/settings.toml`, which is versioned.

## Prerequisites

- An OCI tenancy and a compartment where you can create Generative AI projects
  and sandboxes (ask your tenancy administrator for the policies).
- The OCI SDK configured on your machine (`~/.oci/config`, API key or
  session token) and the OCI CLI, to create the project.
- An OCI Generative AI API key (or an OpenAI API key, to use OpenAI).
- Python 3.11 or later (we use conda in the examples).
- Codex, for the next labs.

## Steps

### 1. Create the Python environment

From the repository root:

```bash
conda create -n support-bundle-triage-agent python=3.12 -y
conda activate support-bundle-triage-agent
pip install -r requirements.txt -r requirements-dev.txt
```

### 2. Create the Generative AI project

The project settings are in `config/`:

| Setting | File | Value |
|---------|------|-------|
| Conversation | `project-conversation.json` | enabled, conversations and responses kept 720 hours |
| Short-term memory | `project-short-term-memory.json` | enabled, compaction model `openai.gpt-oss-120b` |
| Long-term memory | — | not enabled |
| Sandboxes | `sandbox-project-deny-all.json` | enabled, `MANAGED` network, `DENY_ALL` |

```bash
oci generative-ai generative-ai-project create \
  --compartment-id <compartment-ocid> \
  --display-name support-triage \
  --conversation-config file://config/project-conversation.json \
  --short-term-memory-optimization-config file://config/project-short-term-memory.json \
  --sandbox-config file://config/sandbox-project-deny-all.json
```

Or, in the OCI Console: create the project, enable conversations and
short-term memory with `openai.gpt-oss-120b` as the compaction model, leave
long-term memory off, enable sandboxes with managed networking and
`DENY_ALL`.

Write down the OCID of the new project (`ocid1.generativeaiproject...`).
The network policy cannot be changed later: if it is wrong, create a new
project.

### 3. Configure the agent

Secrets:

```bash
cp .env.example .env
```

Edit `.env` and set `OCI_GENAI_API_KEY` (or `OPENAI_API_KEY` for OpenAI).

Configuration: edit `config/settings.toml`:

- `[model]`: `provider` and `model_id`.
- `[oci]`: `region`, `project_id` (the OCID from step 2), the OCI SDK
  `profile` and `auth`.
- `[sandbox]`: leave the defaults.

### 4. Run the quick preflight

```bash
python -m triage.preflight --quick
```

It checks your environment, the configuration, the OCI credentials, the
project (conversations, short-term memory, sandboxes, network) and your
permissions. Then it calls the model twice: the model calls a tool, and the
second call continues the same conversation on the server, sending only the
tool output and `previous_response_id`, as the agent will do. It takes a few seconds
and creates no sandbox. Each failed check prints a hint (`→ ...`): fix it and
run the preflight again.

### 5. Run the full preflight

```bash
python -m triage.preflight
```

It adds the sandbox checks: it creates a test sandbox, looks for the tools
the agent needs, writes and reads back a 1 MiB file, tries an outbound
request (which must be blocked), and deletes the sandbox.

**Starting a sandbox can take several minutes.** The preflight shows the
state while you wait. You can read Lab 1 meanwhile: it does not need a
sandbox.

A successful run looks like this (shortened):

```
 ✔ [ 1/11] Local environment  Python 3.12.0 (...), oci 2.192.0, openai 3.55.1, ...
 ✔ [ 4/11] Project            support-triage: ACTIVE, conversations 720 h, short-term memory (openai.gpt-oss-120b), sandboxes enabled, network MANAGED/DENY_ALL
 ✔ [ 6/11] Model              openai.gpt-... called get_utc_time, conversation continued on the server (4.4 s)
 ✔ [ 7/11] Sandbox            RUNNING in ... s (python-3.11, SMALL)
 ✔ [ 8/11] Runtime tools      python3 grep awk sed timeout unzip curl
 ✔ [ 9/11] File transfer      1 MiB written and read back intact (2.0 s)
 ✔ [10/11] Network isolation  outbound blocked in ... s (...)
 ✔ [11/11] Cleanup            test sandbox stopped and deleted

All checks passed. You are ready for Lab 1.
```

If the cleanup only warns that the sandbox is still stopping, the stop was
requested and the service is slow to finish it: delete the sandbox from the
OCI Console once it is `STOPPED`.

You do not need to keep this sandbox: in Lab 3 the triage command creates
its own, and can keep it for reuse. To leave the test sandbox running and
explore it now, add `--keep`: the preflight prints its id, and the sandbox
expires after 20 minutes (it then becomes `STOPPED`; delete it from the OCI
Console).

### 6. Read AGENTS.md

`AGENTS.md` holds the rules Codex follows in this repository: which files it
must not change, the code style, the sandbox and security rules, the commands
it runs before saying it is done. Read it once: in the next labs you will
check that Codex followed it.

## Done when

- `python -m triage.preflight` ends with "All checks passed" (a cleanup
  warning is acceptable).
- You can explain why the project uses `DENY_ALL` and where secrets and
  configuration live.

## Troubleshooting

| Symptom | What to do |
|---------|------------|
| `Local environment` fails | The wrong Python is active: `conda activate support-bundle-triage-agent`, then `pip install -r requirements.txt`. |
| `Configuration` fails | A key in `.env` or `config/settings.toml` is missing: the message names it. |
| `Project` fails with 404 | Check `project_id` and `region` in `config/settings.toml`. |
| `Network isolation` fails | The project was not created with `DENY_ALL`; create a new project (step 2). |
| `Sandbox` times out | The start is slower than `ready_timeout_seconds`: raise it in `config/settings.toml` and retry. |
