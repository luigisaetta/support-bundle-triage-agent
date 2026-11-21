# Repository layout

Use this map to find the workshop guides, the code you work on and the
supporting files. For the participant walkthrough, start with the
[README](README.md).

```text
README.md                          participant entry point and quick start
layout.md                          this repository map
AGENTS.md                          rules for Codex (read-only)
requirements.txt                   application dependencies
requirements-dev.txt               test, formatting and lint tools
config/
  settings.toml                    model, OCI and sandbox configuration; no secrets
  project-conversation.json        OCI project: conversation retention
  project-short-term-memory.json   OCI project: short-term memory and compaction
  sandbox-project-deny-all.json    OCI project: sandboxes with DENY_ALL networking
.env.example                       API key template; copy to your local .env
triage/
  config.py                        settings and secret loading (provided)
  oci_clients.py                   OCI SDK clients (provided)
  llm.py                           configured model client (provided)
  sandbox.py                       sandbox lifecycle, commands and files (provided)
  ingest.py                        safe bundle upload and extraction (provided)
  make_bundle.py                   sample bundle generator (provided)
  preflight/                       environment and OCI checks (provided)
  tools.py                         sandbox function tools (Lab 1)
  agent.py                         Responses API investigation loop (Lab 2)
  report.py                        structured report and evidence checks (Lab 3)
  cli.py                           command line for a complete investigation (Lab 3)
labs/                              participant guides: lab0.md through lab3.md
prompts/                           the Codex prompt for each lab
tests/unit/                        infrastructure tests and fake sandbox client
tests/labN/                        acceptance tests for lab N
solutions/labN/                    reference solutions for participants
docs/                              lab overview and sample bundle findings
bundles/                           generated support bundles
out/                               generated Markdown and JSON reports
```

The `(provided)` modules supply the infrastructure used by the labs.
Labs 1–3 focus on `tools.py`, `agent.py`, `report.py` and `cli.py`; follow
each guide and its Codex prompt to implement or review those modules.
This checkout also includes their implementations in `triage/`.

Each lab has a guide in `labs/`, a Codex prompt in `prompts/` and
acceptance tests in `tests/labN/`. Reference solutions in `solutions/`
are for participants who want to compare their work or catch up.

Keep API keys in your local `.env` file. Generated bundles and reports
are stored in `bundles/` and `out/`; they are excluded from version control.
