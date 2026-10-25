# Senpilot Regulatory Document AI Agent

The collection stage accepts **supplied document priorities**, downloads up to
**10 selected documents**, and creates a verified **delivery ZIP** with a
manifest. Live matter verification, metadata, and all five category counts are
retained. Email interpretation, LLM ranking, and email intake/delivery are deferred.

Values in the assignment PDF are examples, never application defaults. The
command has no default matter number and downloads only when explicitly asked.

## Structure and dependencies

```text
src/senpilot_agent/
  cli.py       # Inspection, matter-details, and one-document command
  probe.py     # Browser lifecycle, search, metadata/counts, reports
  collection.py # Strict request contract and supplied priority order
  retrieval.py # Category navigation, virtual scrolling, download by reference
  archive.py   # Delivery ZIP, manifest, CRC and checksum verification
  files.py     # Safe filenames, basic PDF/ZIP/MP3 checks, SHA-256
tests/         # Offline tests; no requests to the public site
docs/          # Assignment and investigation notes
artifacts/     # Ignored local reports, DOM, screenshots, traces
```

Python **3.12+** and Playwright are the runtime requirements. Playwright operates
the browser deterministically; pytest is the only development dependency. The
helpers use the Python standard library. No LLM, email SDK, agent framework,
database, or ZIP dependency is required for downloading and packaging.

## Setup on this Windows machine

Run from the repository root in PowerShell:

```powershell
# Substitute the path to your Python 3.12+ executable.
& 'C:\Path\To\Python\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install --cache-dir .cache/pip -e ".[dev]"

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

## Download and ZIP supplied priorities

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.cache\ms-playwright"
.\.venv\Scripts\senpilot-probe.exe --collection-request examples/collection-request.json
```

The sample contains historical M12205 references and requests three files; it
does not imply those documents will always remain available. Supply current
references from the upstream selection stage or a manually prepared JSON file:

```json
{
  "matter_number": "M12205",
  "limit": 10,
  "documents": [
    {"category": "Other Documents", "document_number": "102674", "priority": 1},
    {"category": "Exhibits", "document_number": "H-2", "priority": 2}
  ]
}
```

Smaller priority numbers come first; ties preserve input order. The limit is
global, defaults to 10, and must be 1–10. All supplied entries are validated
before browser launch, including entries beyond the selected limit. Unknown
fields, arbitrary URLs, duplicate numeric/exhibit identifiers, and invalid
matter/category/priority values are rejected. `--collection-request` cannot be
combined with `--matter` or `--download-one`.

For Transcripts and Recordings, supply `hearing_date` in the site's MM/DD/YYYY
format instead of `document_number`. An optional exact `title` distinguishes
rows sharing a date (a displayed witness name for transcripts). For example:

```json
{"category": "Recordings", "hearing_date": "01/12/2026", "title": "Day 4 - TRK 2", "priority": 1}
```

The downloader verifies the selected matter/category, navigates the virtual
list, and locates each reference rather than downloading the first visible rows.
Date references are checked across the category for ambiguity. Only Public rows
are downloaded. Where present, Export Field to File is confirmed using the
site's default filename, replacing commas with underscores to avoid FileMaker's
malformed download header, followed by the Download Files filename button.
Each file is saved and validated before the dialog closes and retrieval proceeds.

The run contains `delivery.zip` with `documents/` and `manifest.json`. The manifest
includes verified metadata/counts, supplied priorities, selected references,
per-document outcomes, file sizes, and SHA-256 hashes. Archive member names,
sizes, CRC, manifest bytes, and document hashes are verified before publication.
Browser traces, HTML, session evidence, and failed files remain outside delivery.

Missing, private, ambiguous, unvalidated, or failed files are recorded without
substitution. Retrieval continues when dialog/browser state can be restored.
Different selected references with identical bytes are both retained and linked
by `same_content_as`. No lower-ranked candidate is automatically substituted.
An uncertain download-event timeout stops further attempts to prevent attributing
a late download to another selection. The actual downloaded filename must also
match the filename shown in the selected dialog.

| Report status | Meaning | Exit code |
|---|---|---|
| `collection_complete` | Every selected reference saved/validated and ZIP verified | 0 |
| `collection_partial` | ZIP contains successes; some selections failed or were not attempted | 1 |
| `collection_empty` | Empty input list; matter metadata captured; no ZIP | 0 |
| `failed` | No successful files, browser failure before collection, or archive failure | 1 |

An empty category for a supplied reference is a failed selection. No successful
files means no ZIP. `collection_complete` is true only for a complete collection
with a verified archive; `phase1_milestone_complete` still describes the earlier
metadata/counts plus one validated file milestone.

See the [download and ZIP plan](docs/download-and-zip-plan.md). The earlier
[email-guided selection proposal](docs/document-selection-plan.md) is historical.
The [single-call LLM plan](docs/single-call-llm-plan.md) defines the next stage:
email text and a real catalog enter one model call, then its ranked references
feed this collector. Email interpretation/ranking remain unimplemented; mailbox
intake and delivery are left until last.

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
Without `--download-one`, extraction ends here.

To also save the first Other Documents file in the site's current display order:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.cache\ms-playwright"
.\.venv\Scripts\senpilot-probe.exe --matter M12205 --download-one
```

The command opens Other Documents, verifies its selected tab and found count,
and reads the first document row's number and title. `GO GET IT` opens a
**Download Files** dialog; the filename button inside that dialog triggers the
actual download. The command begins waiting before that second click, saves via
the safe-filename helper into the run's `downloads/` directory, and validates the
file before browser closure. No new download host was needed.

Zero Other Documents, ambiguous controls, missing dialogs, download/save
failures, and invalid files produce failure reports. An unfamiliar format is
saved with evidence but fails this validation milestone. There are no retries.
This diagnostic flag retrieves one file from Other Documents only; use
`--collection-request` for multiple files and a ZIP.

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
- `download_candidate`: selected matter/category, document number/title, dialog
  filename, suggested filename, and relative saved path as they become known.
- `downloaded_documents`: validated files with those associations plus filename,
  size, SHA-256, validation description, and `format_validated`.
- `downloaded_document_count`: number of persisted files that passed validation.
- Collection mode also records `collection_request`, `selected_documents`,
  `document_outcomes`, `archive`, and `collection_complete`. Failures can include
  per-selection HTML/text/screenshots under `failures/`.

`document_downloaded` and exit code 0 mean the one-file workflow succeeded.
`phase1_milestone_complete` becomes true only after metadata/count extraction
and file persistence/validation succeed. It remains false for metadata-only
runs. A later download failure retains the verified matter, metadata, counts,
and any candidate/path evidence; failed or unvalidated files are not counted.

`inspection_captured` and exit code 0 mean inspection completed without a search.
`failed` exits with code 1 and records the error and stage, including
`locate_matter_field`, `submit_matter_search`, `verify_matter`, `extract_metadata`,
`extract_category_counts`, `open_category`, `select_document`, `prepare_download`,
`locate_document`, `prepare_export`, `download_document`, `validate_download`,
`close_download_dialog`, or `create_archive`. A verified identifier remains
available if later extraction fails; completed metadata is also retained if
count extraction fails.
Failure evidence uses the same directory structure.

`--timeout-ms 60000` changes bounded browser/element/download timeouts, including
the wait for file persistence. `--inspect-seconds` adds an optional debugging
pause **after** the requested workflow succeeds; it is not an application
readiness condition.

Only HTTPS/WSS on `uarb.novascotia.ca:443` is permitted. Other origins are blocked
and recorded. If live inspection establishes a required resource host, review it
and add that exact host deliberately. Do not disable TLS verification to bypass
a browser certificate error. Artifacts can contain session identifiers and are
excluded from Git. Chromium can report `net::ERR_ABORTED` when a navigation
becomes an attachment; this raw diagnostic is retained in `failed_requests` even
when saving and validation succeed.

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
A fresh live `--download-one` run also saved document 102674, **Board Order**, as
a 170,571-byte PDF and verified its checksum after browser closure. See the
investigation notes for the historical evidence; future runs use live data.
The collection stage has also produced a verified ten-document ZIP across
Other Documents, Key Documents, and Exhibits, plus a separate live transcript
and MP3 recording ZIP. See [collection investigation](docs/collection-investigation.md).

The offline browser fixtures reproduce the observed styled-div activation,
Tab commit, three Search controls, asynchronous detail data, and no-result
feedback, followed by delayed metadata and category-count updates. They also
reproduce the observed category/grid/dialog flow and fulfill attachment
responses locally in Chromium without contacting UARB. They cover
success, zero counts, blank optional fields, malformed/missing counts, missing or
ambiguous widgets, workflow timeouts, failed saves, and invalid downloads.
Install Chromium as above before running the complete suite.
The earlier one-file milestone passed **81 tests on Python 3.14.2**; the collection
stage adds request, virtual-list retrieval, recovery, and ZIP integrity tests.
The complete collection-stage suite passed **154 tests on Python 3.14.2**.
If Windows denies access to a temp directory from an earlier sandbox session,
use `--basetemp artifacts/pytest-local -o cache_dir=artifacts/pytest-cache`.

The existing validator rejects empty files, HTML error responses, basic malformed
PDFs, corrupt ZIP containers, and MP3 files without a plausible complete first
MPEG Layer III frame, and calculates SHA-256. PDF markers and MP3 headers are
basic checks, not full parsing/decoding. Unknown formats are explicitly reported
as unvalidated and excluded from delivery. Email functionality remains pending.

Official references: [Playwright installation](https://playwright.dev/python/docs/intro),
[download events and persistence](https://playwright.dev/python/docs/downloads),
[browser context routing](https://playwright.dev/python/docs/api/class-browsercontext).
