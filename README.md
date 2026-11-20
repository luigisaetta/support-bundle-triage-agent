# Support Bundle Triage Agent

An AI agent that diagnoses customer support bundles. The model is reached
through the OpenAI Responses API (on OCI Generative AI or on OpenAI), the
conversation is kept on the server, and every command the agent runs executes
inside an isolated OCI Sandbox with no outbound network.

This repository is a LiveLab: in four labs you build the agent with Codex.
Start from [docs/lab-overview.md](docs/lab-overview.md).

## Labs

| Lab | Guide | You build |
|-----|-------|-----------|
| 0 | [Setup and preflight](labs/lab0.md) | nothing (provided) |
| 1 | [Sandbox tools](labs/lab1.md) | `triage/tools.py` |
| 2 | [Agent loop](labs/lab2.md) | `triage/agent.py` |
| 3 | [Report and end-to-end run](labs/lab3.md) | `triage/report.py`, `triage/cli.py` |

## Layout

```
AGENTS.md                  rules for Codex in this repository (read-only)
config/
  settings.toml            configuration (model, OCI, sandbox); versioned, no secrets
  project-conversation.json        OCI project: conversation retention
  project-short-term-memory.json   OCI project: short-term memory, compaction model
  sandbox-project-deny-all.json    OCI project: sandboxes enabled, DENY_ALL network
.env.example               secrets only (API keys); copy to .env, never commit .env
triage/
  config.py                settings: config/settings.toml + secrets in .env   (provided)
  oci_clients.py           OCI SDK clients (sandbox and Generative AI)         (provided)
  llm.py                   OpenAI client for the configured provider           (provided)
  sandbox.py               SandboxManager, SandboxSession: lifecycle, commands,
                           files, snapshots                                    (provided)
  ingest.py                safe upload of a bundle zip into the sandbox        (provided)
  make_bundle.py           writes the sample bundle (python -m triage.make_bundle)
  preflight/               environment check (python -m triage.preflight)     (provided)
  tools.py                 function tools over the sandbox                     (Lab 1)
  agent.py                 Responses API loop with function calling            (Lab 2)
  report.py                structured triage report, evidence check            (Lab 3)
  cli.py                   python -m triage.cli BUNDLE                         (Lab 3)
labs/                      participant guides (lab0.md ... lab3.md)
prompts/                   the prompt for Codex of each lab
tests/unit/                tests of the provided code, with a fake sandbox client
tests/labN/                acceptance tests of lab N
solutions/labN/            reference solutions, to read or to catch up
docs/                      lab overview, expected findings of the sample bundle
bundles/                   sample bundles (generated, not versioned)
out/                       triage reports (generated, not versioned)
```

The files of Labs 1–3 are not in the repository: you build them with Codex
in `triage/`. The reference versions are in `solutions/`.

## Quick start

```bash
conda create -n support-bundle-triage-agent python=3.12 -y
conda activate support-bundle-triage-agent
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env              # secrets only: the API key
# edit config/settings.toml        # model, region, project OCID, sandbox
python -m triage.preflight --quick # configuration, project, model (no sandbox)
python -m triage.preflight         # adds the sandbox checks (the start takes minutes)
```

Create the Generative AI project with server-side conversations, short-term
memory, and sandboxes with outbound network denied (Lab 0):

```bash
oci generative-ai generative-ai-project create \
  --compartment-id <compartment-ocid> \
  --display-name support-triage \
  --conversation-config file://config/project-conversation.json \
  --short-term-memory-optimization-config file://config/project-short-term-memory.json \
  --sandbox-config file://config/sandbox-project-deny-all.json
```

## Run the finished agent

With the code of all labs in `triage/` (yours, or copied from `solutions/`):

```bash
python -m triage.make_bundle
python -m triage.cli bundles/sample-ora00020.zip --keep
```

The report is written to `out/`. With `--keep` the sandbox stays up and the
CLI prints the command to reuse it with `--sandbox-id`.

## Checks

```bash
python -m pytest tests/unit -q          # provided code
python -m pytest tests/lab1 -q          # and so on, after each lab
python -m black --check triage tests
python -m pylint triage
```
