# Five-minute headed showcase

The local rehearsal has been prepared and verified. Open
[READY.md](../../artifacts/demo/READY.md) for its JSON reply, original PDF, ZIP,
and exact commands. The email-driven headed hero run took **62.47 seconds**; its
completed-run resume used zero new API calls or browser sessions. Those outputs
are local, ignored artifacts; on another machine, rehearse the command below first.

The hero request asks for one known, short PDF from M12205. Rehearse it before
presenting so its PDF, ZIP, and JSON reply are already available.
During the presentation, start a **fresh headed run** of that same request and
show the prepared results while the visible browser works. This avoids waiting
for fresh Gemini/UARB responses to finish before discussing the output.

`--headed` shows the actual browser, not a replay. The JSON runner opens
one browser for inventory and another for exact-file retrieval. The browser
windows can close between stages while Gemini interprets, ranks, or reviews.
The inspection option below holds each final browser view briefly for the audience.

## Rehearse the hero request

From the repository root, using the configured `.env`:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.cache\ms-playwright"
.\.venv\Scripts\senpilot-plan.exe --emails-json examples/demo/hero.json --headed --inspect-seconds 8 --timeout-ms 20000 --output artifacts/demo/hero
```

After it completes, open the printed `replies.json`. Each email's `content`
contains the readable findings, citations, coverage limitations, errors, and
file/ZIP paths. Keep that reply, its PDF, and its ZIP open for the presentation.
There is no Markdown review file. The linked `plan.json`/`review.json` files are
internal checkpoints rather than additional human-facing reports.
Each rerun creates a separate directory, preserving the rehearsal outputs.
The email itself requests one document and asks content questions. The first LLM
call extracts that count and enables download/review. There are no CLI count or
review overrides. The application enforces a maximum of ten documents.
The latest rehearsal's first call returned `requested_count: 1`,
`download_requested: true`, and `review_requested: true`.
Gmail integration and delivery are not implemented: these are local inbox/reply
files, and the ZIP is a local attachment reference.

## Presentation timing

| Time | Show |
|---|---|
| 0:00-0:30 | Open `hero.json`: one exact order, natural-language questions, no invented substitute. |
| 0:30-1:30 | Run the same command in a terminal; watch the live matter search, category grids, and retrieval when each browser opens. |
| 1:30-2:45 | Read the prepared `replies.json` content and open its PDF: approved cost, reporting requirements, exact page citations. These are explicitly results from the rehearsal. |
| 2:45-3:30 | Open the prepared ZIP and `replies.json`: one file, same email ID, selection reason, findings, attachment path. |
| 3:30-4:15 | Show prepared edge-case replies and the second-matter metadata results using the inputs below. |
| 4:15-4:50 | Open the saved `plan.json`: show the extracted count, download/review flags, complete inventory, and locally enforced result limit. |

The presentation fits under five minutes by using prepared results. A completely
fresh live run has no five-minute guarantee: provider retries and file-server
timeouts can take longer. If it is still running, leave it visible and present
the labeled rehearsal results. A prepared result does not make a fresh request
instant or bypass its API calls. Avoid running every test inbox during the talk.

## Other emails to prepare before presenting

```powershell
.\.venv\Scripts\senpilot-plan.exe --emails-json examples/demo/different-matters.json --timeout-ms 20000 --output artifacts/demo/matters
.\.venv\Scripts\senpilot-plan.exe --emails-json examples/demo/edge-cases.json --timeout-ms 20000 --output artifacts/demo/edges
.\.venv\Scripts\senpilot-plan.exe --emails-json examples/demo/limits-and-filters.json --timeout-ms 20000 --output artifacts/demo/limits
```

These three packs explicitly request metadata only. The first call should leave
both action flags false. The limits pack asks for fifteen documents to demonstrate
the application's hard ceiling of ten; it may return fewer when fewer exist.

For the third supported action, `download-only.json` requests one PDF and a ZIP
while explicitly declining analysis. Run it without mode/count flags:

```powershell
.\.venv\Scripts\senpilot-plan.exe --emails-json examples/demo/download-only.json --timeout-ms 20000 --output artifacts/demo/download-auto
```

This was verified live: the first call returned count 1, downloads true, and
review false; the result contained one ZIPped PDF and zero content-review calls.

| Email ID | Expected behavior |
|---|---|
| `earlier_water_project` | M11999: one Key Document; independent matter verification; no downloads. |
| `municipal_boundary` | M12383: at most two Key Documents in the requested date window, newest first. |
| `missing_matter` | Clarification before browser launch. |
| `multiple_matters` | Clarification: one matter per request; do not choose one silently. |
| `unsupported_category` | Clarification; no category substitution or browser launch. |
| `conflicting_order` | Clarification before browser launch. |
| `empty_category` | Complete with no matches if Transcripts is still empty; no fallback category. |
| `untrusted_literal_title` | Complete with no matches for the absent literal title; no fabricated document or obedience to the title's text. |
| `requested_count_exceeds_app_limit` | At most ten real records, despite the email requesting fifteen; fewer when fewer exist. |
| `future_date_no_matches` | Complete with no matches; no older substitute. |

The edge-case batch normally exits **2** because clarification is an expected
result. Later emails must still receive replies. API/browser failures instead
make the batch exit **1**; report that as a service failure, not a passed case.
The literal-title test may expose an interpretation/ranking limitation; inspect
the actual result rather than assuming it passed.

Rehearsal finding (October 27): the literal-title stress case returned two real
but nonmatching documents instead of zero matches. This is a known strict-title
filtering limitation, not a passing test. No invented document 999999 was returned.
Keep this case for an honest limitations discussion; use missing/multiple matters
and the empty category for the short presentation's successful edge cases.

`invalid-inbox.json` deliberately contains a blank email body. Run it separately:

```powershell
.\.venv\Scripts\senpilot-plan.exe --emails-json examples/demo/invalid-inbox.json --output artifacts/demo/invalid
```

Expected: exit **2** before any model call, browser launch, or output directory
creation. Even the otherwise valid first email must not be processed.

## Coverage beyond email wording

Emails cannot reliably force a real provider 503, corrupt PDF, or invented model
citation. Demonstrate those with the existing controlled tests, clearly labeled
as mocked service responses and fixture PDFs:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_review.py tests/test_planner.py::test_transient_sdk_errors_retry_with_diagnostics_and_retry_after tests/test_selected_downloads.py::test_stalled_selected_download_closes_session_before_attempting_more_files -q
```

Run these before the presentation and show their real result. They cover partial
failure, citation rejection, timeout recovery, unchanged-file reuse, prompt
upgrades, and resume without paying for completed work again. The full regression
suite takes longer than the presentation and is not part of the five-minute run.

Matter metadata/category availability was checked against UARB on October 27,
2026. M11999 is a water capital-expenditure matter; M12383 is a municipal-boundary
matter. Both had empty Transcripts/Recordings categories at that check. Future
site changes can alter results. Reviews cover extracted PDF text; semantic
interpretation still needs human judgment.
