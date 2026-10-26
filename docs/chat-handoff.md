# Chat handoff: single-call LLM document selection

Saved October 25, 2026. This is a portable record of this chat's decisions,
implementation, and remaining work, rather than a raw chat/session export.
Git carries this file and the implementation; conversation and `/goal` state
are managed separately by the chat client.

## Goal and current state

The goal is **implement the plan** in
[single-call-llm-plan.md](single-call-llm-plan.md). The implementation is committed
and pushed to `origin/main` at:

- Commit: `69d8f3c8f08cce62eb6d430f8da9e86cbb6548cb`.
- Message: `Implement bounded LLM document ranking from email text`.
- Remote: `git@github.com:Taha-M567/Senpilot_OA.git`.
- The worktree was clean and synchronized before this handoff was added.

The chat goal was marked **blocked**, not complete: real-provider evaluations
and a real model-selected live download/ZIP have not run. The user said they
would configure the API key at the end and asked development to proceed without
waiting for it. `OPENAI_API_KEY` and `SENPILOT_LLM_MODEL` were still unset in the
agent's environment at the latest check. No external model generation was made.

Do not rebuild the existing implementation or add email integration to finish
this milestone. Complete the remaining validation and fix actual findings.

## Agreed architecture and decisions

1. A local JSON file supplies `subject` and `body_text`. A future email adapter
   will supply the same interface; mailbox intake and delivery come last.
2. Regex first extracts exactly one distinct complete `M[0-9]{5}` token,
   accepting lowercase and repeated mentions of the same matter. Missing,
   partial, or multiple distinct matters end the request before browser/model
   work. The model does not decide the matter number.
3. Verify the matter in UARB and catalog listing metadata across all five
   categories. Do not download all documents to rank them. Private or ambiguous
   rows remain in audit evidence but are excluded from model input.
4. One logical model request receives the original text and complete eligible
   catalog, infers category/subtype intent, and returns up to ten actual candidate
   IDs in preferred order with reasons and exact matching metadata evidence.
5. One identical retry is allowed for transient transport/provider failures:
   two attempts maximum, SDK automatic retries disabled. No repair prompts,
   second ranking stage, model fallback, or replacement selection.
6. Python checks the final schema, matter, candidate membership/eligibility,
   scope, duplicates, evidence, and global cap. It does not reinterpret the email
   or rerank selections. Accepted IDs map to the existing `CollectionRequest`.
7. The existing collector downloads selected native references, validates files,
   and creates the verified ZIP. Ten is a maximum, never a minimum; no backfills
   with unselected documents after failures.

Explicit categories, quantities, priorities, and exclusions override defaults.
"Only" restricts matches; "prefer" permits lower-priority eligible material.
Subtypes such as orders do not automatically imply the Key Documents category.
Generic requests favor complementary written documents: decisions/orders,
application, substantive evidence, then compliance material, ahead of routine
correspondence. Recency breaks ties. There are no fixed category quotas.
Transcripts/recordings require explicit or clearly implied intent.

The complete input must fit its configured budget or the job ends explicitly.
There is no first-X, newest-X, first-screen, or per-category shortlist. The
default conservative token upper bound is serialized UTF-8 bytes plus 4096;
defaults are 96,000 input budget, 128,000 configured context allowance, 4096
output tokens, and 60 seconds per model attempt. These are configurable; the
chosen model must support the configured allowance and strict Responses schema.

There are no clarification conversations, waiting requests, reply-thread
matching, or follow-up model calls. Unusable instructions end the job; the
insufficient-detail notice tells the sender to submit a new request from scratch.
Ranking uses listing metadata, not PDF contents or audio transcription. Dates
retain their site meaning and are not assumed file creation timestamps.

## Code map

| File | Responsibility |
|---|---|
| [selection.py](../src/senpilot_agent/selection.py) | Subject/body contract, matter regex, final selection validation/mapping |
| [catalog.py](../src/senpilot_agent/catalog.py) | Complete virtual-list enumeration, eligibility, fingerprint and count verification |
| [llm.py](../src/senpilot_agent/llm.py) | Prompt/schema, configuration, input budget, real Responses call and bounded retries |
| [probe.py](../src/senpilot_agent/probe.py) | Browser lifecycle, verified matter metadata/counts, ranking-to-collection orchestration and evidence |
| [cli.py](../src/senpilot_agent/cli.py) | `--email-request`, `--dry-run`, existing inspection/explicit-priority commands |
| [retrieval.py](../src/senpilot_agent/retrieval.py) | Category navigation, virtual scrolling, native-reference downloads |
| [archive.py](../src/senpilot_agent/archive.py), [files.py](../src/senpilot_agent/files.py) | Verified ZIP and persisted-file checks |
| [evaluate_selection.py](../scripts/evaluate_selection.py) | Local fixture checks and opt-in real-provider evaluations |

Read [README](../README.md), [selection investigation](selection-investigation.md),
and the [assignment PDF](Co-op%20Technical%20Assignment%20-%20Winter%202027.pdf)
before changing the workflow. No repository AGENTS.md was found; check again.

## Verified results and limits

- A 246-test full regression passed, followed by 58 affected browser/CLI checks
  after buffer-row and terminal-message hardening, then 62 adapter/contract/judge
  checks after final retry-audit hardening. All **248 currently collected cases**
  have passing evidence across those runs; this was not one 248-test full run.
- Python was 3.14.2, Playwright 1.64.1, pytest 9.1.1, OpenAI SDK 2.54.0. The
  project supports Python 3.12+. Playwright is the runtime dependency; the
  optional `llm` extra adds the official SDK. No framework or database was added.
- Live catalog snapshots covered M12205 (62 rows, 50 eligible, about 3 seconds)
  and M12451 (331 rows, 269 eligible, about 13 seconds). These are historical
  observations, not defaults, current guarantees, or a size distribution.
- A live UARB run downloaded two real PDFs and verified the ZIP after browser
  closure, prioritizing Other Documents 97291 at listing position 42 before
  102674. Its SDK response was explicitly **simulated**. This proves browser,
  SDK wiring, and packaging integration, not model relevance or provider access.
- Catalogs use two passes, contiguous row coverage, and re-query the matter
  detail page to verify counts. Some document tabs leave another tab's count
  blank. Vaadin also moves recycled rows before replacing text: readiness must
  honor populated-row state and active client work, including unloaded buffers.
- Browser traffic remains restricted to HTTPS/WSS on `uarb.novascotia.ca:443`;
  service workers are blocked and TLS verification remains enabled. Do not
  remove these protections to fix navigation/download issues.

Detailed evidence paths and test results are recorded in the investigation doc.
`artifacts/`, browser installations, `.venv`, credentials, and raw session
evidence are ignored and **are not transferred by this Git push**. Regenerate
needed evidence on the laptop; do not assume those local files exist there.

## Continue on the laptop

Pull `main` with a clean checkout. Create a laptop-local `.venv` using an
installed Python 3.12+ as described in the README, then use its explicit
interpreter in PowerShell from the repository root:

```powershell
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install --cache-dir .cache/pip -e ".[dev,llm]"
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.cache\ms-playwright"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q --basetemp artifacts/pytest-resume -o cache_dir=artifacts/pytest-cache
```

Configure `OPENAI_API_KEY` and `SENPILOT_LLM_MODEL` in the process running the
command. `gpt-6-luna` is the documented initial configurable candidate, not a
measured relevance winner; confirm account access. [.env.example](../.env.example)
is a reference and is not loaded automatically. Keep keys outside Git/chat.

The remaining acceptance work is:

```powershell
# Validates the 13 synthetic labelled fixtures without provider calls.
.\.venv\Scripts\python.exe scripts/evaluate_selection.py
# Opt-in paid model checks: start with one case, then evaluate all cases.
.\.venv\Scripts\python.exe scripts/evaluate_selection.py --live --case generic_overview
.\.venv\Scripts\python.exe scripts/evaluate_selection.py --live
# Real model selection feeding real UARB downloads and a ZIP.
.\.venv\Scripts\senpilot-probe.exe --email-request examples/email-offscreen-request.json
```

Inspect fresh reasons, selected candidate IDs/native references, request limits,
offscreen prioritization, persisted file validation, and the ZIP's members/CRC/
hashes. Do not substitute historical counts, filenames, positions, or sample
metadata for live observations. Fix findings, rerun appropriate checks, update
the investigation, and commit/push completed work. Mark the goal complete only
when the remaining real-provider gates are actually proven.

If starting a new chat, use this handoff prompt:

> Read docs/chat-handoff.md and its referenced plan/investigation. Continue the
> existing implementation on main toward the goal "implement the plan". The
> remaining work is real-provider relevance evaluations and a real model-selected
> live UARB download/ZIP acceptance check. Inspect current Git/configuration first,
> preserve the agreed single-call architecture, and keep email intake/delivery
> deferred. Do not treat SDK simulations as real-model evidence.
