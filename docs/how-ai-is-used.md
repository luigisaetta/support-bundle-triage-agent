# How AI is used in this project

It is easy to think that the language model only writes the final report, and
that any small model would do. It is the opposite: **the model conducts the
whole investigation**. The code around it provides tools, isolation and
checks, but it contains no investigation logic. The quality of the triage
depends directly on the quality of the model.

AI appears in two places:

| When | Who | What it does |
|------|-----|--------------|
| Building the code (Labs 1–3) | Codex | writes `tools.py`, `agent.py`, `report.py`, `cli.py` from the prompts, `AGENTS.md` and the tests |
| Running the agent | an OpenAI GPT model, through the Responses API (OCI Generative AI or OpenAI) | plans and runs the investigation, then writes the report |

The rest of this note is about the second one.

## The model is the investigator

`triage/agent.py` sends the model three things: the task ("checkout requests
fail since 09:15, find the root cause"), the instructions of the agent, and the
schemas of four tools (`list_files`, `read_file`, `grep`, `run_python`). There
is no script of steps. At every turn the model decides what to do next, and
the client only executes what it asks for, in the sandbox.

### 1. Planning and choosing tools

The model decides which tool to call and with which arguments: which
directory to list, which regular expression to search for (in our runs it
chose `ERROR|WARN|ORA-|error|warn`), which lines of which file to read, and
when to stop. It can ask for several tools in one turn: in a real run on the
sample bundle, its third step read seven files at once (alert log, change
log, metrics, both configuration versions, application settings, README).

For counts, timelines and parsing it **writes Python code** that
`run_python` executes in the sandbox, for example to extract and order the
connection errors of every application log. The code is generated on the
fly, for this bundle.

A typical run on the sample bundle: 5–6 model calls, about 12 tool calls,
about 20,000 input tokens and 1,100 output tokens, all in one stored
conversation.

### 2. Correlating evidence across files

The root cause of the sample bundle is not in any single line. The model has
to connect facts written in different files and formats:

- a change log row (`CHG-4821`, 2026-11-18 22:10: reduce Oracle processes);
- the alert log: `ALTER SYSTEM SET processes=300`, then a restart with the
  new limit;
- the previous parameter file (`processes=1500`) and the current one (`300`);
- metrics that reach 300 processes in use from 09:05;
- the first `ORA-00020` at 09:12:03, and application timeouts caused by it;
- the application settings: 3 nodes × 150 connections = **450**, more than
  300 — an arithmetic step the model must make itself.

It must also dismiss **red herrings** placed in the bundle: a disk at 91%, GC
pauses, a deprecated setting, an unrelated deployment. And it must say what is
*not* established (for example, whether there is a connection leak) instead
of guessing.

### 3. Treating bundle content as data

The bundle contains a log line addressed to "the AI assistant", asking it to
report that the system is healthy and to close the case. The instructions say
that bundle content is evidence, never an instruction; following that rule
against text that looks authoritative is a capability of the model. The
sandbox limits what could happen in any case (no network, bounded tools), but
only the model can keep the *conclusions* honest.

### 4. Recovering from errors

Tools never crash the agent: a wrong path, a line past the end of a file or a
script that times out comes back as an `ERROR:` text. The model reads it and
corrects the next call. A step budget (15 tool-requesting calls by default)
ends a run that does not converge.

### 5. Writing the structured report

`triage/report.py` does not resend anything. The report is **one more turn of
the same server-side conversation**: the request carries only
`previous_response_id`, the instruction "write the report" and a strict JSON
schema. The model turns what it found into root cause, confidence (high,
medium, low), evidence as `path:line`, timeline and next steps — with a
confidence that should match the strength of the evidence.

### Behind the scenes: compaction

The project has short-term memory enabled. When a conversation grows, OCI
condenses it with a second model, `openai.gpt-oss-120b`. Our code never calls
it: it is part of the project configuration.

## The model calls, at a glance

| Where | Request | Purpose |
|-------|---------|---------|
| `preflight` | 2 calls, `store=True` | the model calls a tool; the conversation continues on the server |
| `agent.py`, first call | instructions + task + tool schemas | start the investigation |
| `agent.py`, next calls | `previous_response_id` + tool outputs | continue the investigation |
| `agent.py`, budget exhausted | same, with `tool_choice="none"` | best answer with the evidence collected |
| `report.py` | `previous_response_id` + strict JSON schema | structured report |

## What the model must be good at

When choosing a model, look for:

- **reliable function calling** with strict schemas, including several calls
  per turn;
- **multi-step reasoning** over many files, keeping a causal chain and
  timestamps in different formats consistent;
- **writing small, correct Python** for analysis;
- **following the instruction hierarchy**, so that text inside the data does
  not override the instructions;
- **structured outputs** that match a strict JSON schema;
- **a long enough context**: the conversation passes 20,000 tokens on a small
  bundle and grows with real ones.

What a weak model typically does: it calls tools aimlessly or stops after a
couple of searches, blames the first error it sees or a red herring, misses
the arithmetic that links the configuration to the limit, follows the
injected instruction, or cites line numbers that do not exist.

## What is not AI

Everything around the model is deterministic code, there to contain and
verify it:

- **the sandbox and the tools**: the model can only *ask* for a command; it
  runs isolated, without network, with timeouts and bounded output;
- **the bundle ingest**: the zip is checked locally and extracted only in the
  sandbox;
- **the evidence check**: every line the model cites is read again in the
  bundle and marked ✔ or ✘;
- **the step budget**.

The evidence check catches **invented citations**, but it cannot fix a wrong
investigation: a model that cites real lines and draws the wrong conclusion
passes it. That is why the model matters. To compare models, run the same
sample bundle with each one and check the report against the expected
findings in [sample-bundle.md](sample-bundle.md).
