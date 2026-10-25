# Plan: execute document priorities and create a delivery ZIP

Status: implemented and verified. See
[collection-investigation.md](collection-investigation.md) for live artifacts and
the final 154-test regression result.

This stage accepts priorities already resolved to UARB document IDs. Email
interpretation, semantic matching, and hierarchy creation remain upstream and
are deferred. The previous `document-selection-plan.md` describes that later
work; this plan defines the downloader/packager boundary and implementation.

## Input contract

One matter, a global limit from 1 through 10 (default 10), and candidate documents:

```json
{
  "matter_number": "M12205",
  "limit": 10,
  "documents": [
    {"category": "Other Documents", "document_number": "102674", "priority": 1},
    {"category": "Other Documents", "document_number": "102329", "priority": 2}
  ]
}
```

These IDs are historical examples, not defaults. Smaller numeric priority comes
first; equal priorities preserve input order. The upstream stage resolves any
type/topic preferences to concrete IDs before calling this stage. Accept all
five named UARB categories, reject malformed/unknown fields, duplicate document
IDs, invalid matter numbers, booleans/floats in integer fields, and arbitrary
URLs/paths. Do not download by an input URL. An empty document list is a valid
no-documents request. `document_number` is the exact displayed identifier: numeric
for the observed document lists, or an exhibit label such as `H-1`. A category
and identifier together locate a row; they are not a URL or filename.

Transcripts and Recordings use `hearing_date` (MM/DD/YYYY) instead of
`document_number`, reflecting their actual list layouts. An optional exact
`title` narrows a reference (a displayed witness name for a transcript row).
Dates may repeat, so date references are checked across the full category for
ambiguity. Require a distinguishing row label when the date alone is ambiguous.

Select at most `limit` distinct documents from the supplied priority order.
Never fill missing/failed selections with unspecified or lower-ranked documents.
Keep selections and attempts separate from successful downloads.

## Browser workflow

1. Validate the complete input before launching a browser.
2. Reuse matter search/verification and live metadata/all five category counts.
3. Open the category for each selected ID, confirming the selected tab/count and
   any separate found count the layout provides. Key Documents has no separate
   Found Count field in the observed layout. Reuse a category when consecutive
   selected documents share it.
4. Locate the requested ID through the site's actual virtual scrolling, with
   bounded waits and end/no-progress detection. Record observed row title and
   number; do not select by row index or cached DOM element.
5. Click that row's GO GET IT, then its unambiguous filename button in Download
   Files, beginning the download wait before the filename click.
   Handle the observed Export Field to File dialog for transcripts/recordings
   by accepting its nonempty site-provided filename before Download Files.
   Replace commas with underscores through that dialog: FileMaker's unquoted
   download header otherwise causes Chromium to reject comma-containing names.
6. Save via `safe_download_path()` before browser closure, validate via
   `validate_download()`, and retain SHA-256 and size evidence.
7. Close the dialog before the next selection. Preserve per-document failures;
   continue only when the browser is in a known usable state. No automatic
   retries, substitutions, or changes to supplied priorities.
   A timeout before a download event stops further selections because a late
   event cannot safely be correlated with the next request. Cancel known
   incomplete downloads on save failures. Require the downloaded filename to
   match the selected dialog filename.

Keep the current secure UARB host restriction, TLS verification, lazy Playwright
imports, report/evidence behavior, and existing inspection/one-file commands.
Supported-format checks retain their existing limits. Unrecognized formats are
reported as unvalidated and excluded from delivery; category acceptance does not
claim a parser for every possible recording/file format. Add a bounded MP3
ID3/first-frame check for the observed recording format, without an audio decoder.

## ZIP and reporting

Build `delivery.zip` from successful validated documents only. Include a
`manifest.json` containing verified matter metadata, all five live counts/total,
input priorities, selected IDs, successful file evidence, and failed outcomes.
Use safe unique relative member names, preserve selected priority order, verify
member names/counts, CRC and file hashes, then atomically publish the ZIP. Never
include traces, raw HTML, browser sessions, invalid files, or arbitrary paths.

No successful downloads means no delivery ZIP. Distinguish complete, partial,
no-documents, and failed results. Archive creation/verification failure fails the
job while retaining saved files and diagnostic evidence. Record archive size,
checksum, and member list in the report. A partial ZIP is explicitly labeled as
partial; its download count is the actual successful count.
Different requested references with identical file bytes retain separate file
associations and archive members, with a `same_content_as` note. Do not silently
drop a requested reference because its hash matches another selected file.

## Implementation order

1. Strict input dataclasses/JSON loading, stable priority selection, and a sample
   request. Add `--collection-request PATH` alongside the existing CLI modes.
2. Live-inspect category layouts, virtual scrolling, filename-dialog closure.
   Extract reusable category/row/download operations; execute selected IDs.
3. Standard-library ZIP creation and verification with a manifest.
4. Offline fixtures/tests for priority order, cap at 10, offscreen IDs,
   category changes, malformed inputs, missing IDs, broken downloads, dialog
   recovery, partial/no-files results, and safe ZIP membership/integrity.
5. Live-verify a ten-file request including an ID beyond the initial viewport;
   compare the archive manifest, saved file hashes, observed IDs, and counts.
6. Update README/investigation notes and commit/push the completed work.

## Completion evidence

- Existing metadata/one-file regression tests remain green.
- New contract, browser-execution, and archive tests pass without network access.
- Live CLI execution honors input priority and produces at most 10 documents.
- Reopening the persisted ZIP after browser closure proves its exact document
  members, manifest associations, byte sizes, and SHA-256 hashes.
- No LLM, mailbox integration, ranking inference, or new runtime dependency is
  introduced by this stage.
