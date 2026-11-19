# Support Bundle Triage Agent

An AI agent that diagnoses customer support bundles. The model is reached
through the OpenAI Responses API (on OCI Generative AI or on OpenAI) and every
command runs inside an isolated OCI Sandbox.

## Layout

```
config/                  OCI project configuration (sandboxes, DENY_ALL network)
triage/
  config.py              settings, read from .env
  oci_clients.py         OCI SDK clients (sandbox and Generative AI)
  llm.py                 OpenAI client for the configured provider
  sandbox/
    manager.py           SandboxManager: create, restore, attach
    session.py           SandboxSession: run, files, snapshot, extend, close
    lifecycle.py         polling and cleanup of sandboxes and snapshots
    models.py            CommandResult, SandboxError
  preflight/             environment check (python -m triage.preflight)
tests/unit/              unit tests, with a fake sandbox client
docs/  labs/  bundles/   documentation, lab guides, sample bundles
```

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # then fill in your values
python -m triage.preflight  # add --quick to skip the sandbox checks
```

Create the Generative AI project with sandboxes enabled and outbound network
denied (Lab 0):

```bash
oci generative-ai generative-ai-project create \
  --compartment-id <compartment-ocid> \
  --display-name support-triage \
  --sandbox-config file://config/sandbox-project-deny-all.json
```
