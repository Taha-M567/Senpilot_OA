# Senpilot Regulatory Document AI Agent

Phase 1 is **in progress; verified matter metadata and category counts work**.
This repository provides a Playwright inspection/details command and offline
tests. It does **not** yet download a UARB document.

The target milestone is to retrieve live metadata and all five category counts
for `M12205`, then download and validate one file from **Other Documents**. Values
in the assignment PDF are examples, never application defaults.

## Structure and dependencies

```text
src/senpilot_agent/
  cli.py       # Standalone inspection and matter-details command
  probe.py     # Async Playwright search, metadata/count extraction, and evidence
  files.py     # Safe filenames, basic format checks, SHA-256
tests/         # Offline tests; no requests to the public site
docs/          # Assignment and investigation notes
artifacts/     # Ignored local reports, DOM, screenshots, traces
```

Python **3.12+** and Playwright are the runtime requirements. Playwright operates
the browser deterministically; pytest is the only development dependency. The
helpers use the Python standard library. Pydantic, Gmail, LLMs, LangGraph, storage,
and Docker are deferred to their planned phases. There is no reason to install
them for a browser feasibility check.

## Setup on this Windows machine

Run from `C:\Users\taham\Repos\Senpilot_OA` in PowerShell:

```powershell
# Python 3.12 was found here; substitute your own 3.12+ executable elsewhere.
uv venv --python C:\Python312\python.exe .venv --cache-dir .cache/uv
uv pip install --python .venv\Scripts\python.exe --cache-dir .cache/uv -e ".[dev]"

# Keep the browser installation inside this workspace.
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.cache\ms-playwright"
.\.venv\Scripts\python.exe -m playwright install chromium

.\.venv\Scripts\senpilot-probe.exe --headed --inspect-seconds 30
.\.venv\Scripts\senpilot-probe.exe --matter M12205 --headed
.\.venv\Scripts\python.exe -m pytest -q
```

The environment variable must also be set in subsequent terminal sessions using
that browser installation. Alternatively, use an already installed Chromium
browser with `senpilot-probe --channel msedge --headed`; that avoids downloading
Chromium, but still requires the Python Playwright package.

Without an editable package installation, run from source:

```powershell
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m senpilot_agent.cli --headed
```

## Searching and reading the evidence

To search, verify, and extract details for a supplied matter in a fresh session:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.cache\ms-playwright"
.\.venv\Scripts\senpilot-probe.exe --matter M12205 --headed
```

Omit `--headed` for headless operation. Omit `--matter` for inspection only;
there is no default matter. Matter numbers are trimmed, uppercased, and must
contain `M` followed by exactly five ASCII digits. Invalid arguments exit with
code 2 before launching a browser.

Search success requires the returned **read-only matter identifier in the detail
area** to exactly match the requested number. The submitted input and HTTP 200
are not proof. The probe commits the active FileMaker editor with Tab, clicks
the associated direct-matter Search button, and waits for detail data or the
observed No Records Found response. Ambiguous controls, missing data, mismatches,
and timeouts fail without automatic retries.

After verification, the command reads the title, status, type, category, date
received, decision date, and outcome from their observed detail-page widgets.
Dates retain the site's displayed format. The title must be nonempty; blank
optional values are reported as `null`. Missing or ambiguous widgets fail instead
of being interpreted as blank values.

Each of the five document-category buttons is read independently. Counts must
be nonnegative integers; explicit zero counts are valid, but missing, ambiguous,
or malformed counts fail. Their sum is the total across those five categories.
No category tab is opened and no document is downloaded during this step.

Each invocation creates a unique directory under `artifacts/` with `report.json`.
When browser startup/navigation succeeds, it also attempts to save:

- `page.png`, `page.html`, and `page.txt` for the final page state.
- `frame-*.html` and an inventory of controls in `report.json` for selector review.
- `trace.zip` for Playwright Trace Viewer.

```powershell
.\.venv\Scripts\python.exe -m playwright show-trace artifacts\RUN_DIRECTORY\trace.zip
```

`matter_found` and exit code 0 mean the detail identifier was verified and metadata
and counts were captured. The report records `intended_request.matter_number`,
`observed_matter_number`, and `verified_matter_number`. It also includes:

- `matter_metadata`: `matter_number`, `title`, `status`, `type`, `category`,
  `date_received`, `decision_date`, and `outcome`.
- `category_counts`: integer counts keyed by Exhibits, Key Documents, Other
  Documents, Transcripts, and Recordings.
- `total_document_count`: their sum; `null` until every count has been validated.
- `metadata_counts_complete`: true only after both extraction steps succeed.

`inspection_captured` and exit code 0 mean inspection completed without a search.
`failed` exits with code 1 and records the error and stage, including
`locate_matter_field`, `submit_matter_search`, `verify_matter`, `extract_metadata`,
or `extract_category_counts`. A verified identifier remains available if later
extraction fails; completed metadata is also retained if count extraction fails.
Failure evidence uses the same directory structure.

`phase1_milestone_complete` remains false because a persisted, validated download
is still pending. `--timeout-ms 60000` changes bounded browser/element
timeouts. `--inspect-seconds` adds an optional debugging pause **after** the
requested workflow succeeds; it is not an application readiness condition.

Only HTTPS/WSS on `uarb.novascotia.ca:443` is permitted. Other origins are blocked
and recorded. If live inspection establishes a required resource host, review it
and add that exact host deliberately. Do not disable TLS verification to bypass
a browser certificate error. Artifacts can contain session identifiers and are
excluded from Git.

## Confirmed results and remaining work

See [Phase 1 investigation](docs/phase1-investigation.md) for the actual attempts.
The initial network restriction is resolved. M12205 was searched and verified
without manual intervention in fresh headed and headless Chromium sessions. The
selector contract comes from live DOM observation across fresh sessions; static
FileMaker layout-object classes distinguish the direct Search button and
read-only result field. No session-specific element IDs or button indexes are
used. A site layout change may require updating those selectors.

Metadata and all five category counts were also verified in a fresh live M12205
run, with structured output compared against the saved page text and screenshot.
The next milestone is one persisted and validated download. Selecting Other
Documents and the first Go Get It action still needs investigation: whether it
emits a download event, opens a popup, or navigates to a file remains unknown.
ZIP creation and email integration remain deferred.

The offline browser fixtures reproduce the observed styled-div activation,
Tab commit, three Search controls, asynchronous detail data, and no-result
feedback, followed by delayed metadata and category-count updates. They cover
success, zero counts, blank optional fields, malformed/missing counts, missing or
ambiguous widgets, and workflow timeouts without contacting UARB.
Install Chromium as above before running the complete suite.

The existing validator rejects empty files, HTML error responses, basic malformed
PDFs, and corrupt ZIP containers, and calculates SHA-256. A PDF header/end marker
is a basic check rather than a full PDF parse. Unknown formats are explicitly
reported as unvalidated. No archive delivery or email functionality exists yet.

Official references: [Playwright installation](https://playwright.dev/python/docs/intro),
[download events and persistence](https://playwright.dev/python/docs/downloads),
[browser context routing](https://playwright.dev/python/docs/api/class-browsercontext).
