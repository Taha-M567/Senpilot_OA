# Download and ZIP investigation

## Scope and implementation

The starting checkout was clean at `44674ce`. The assignment PDF and existing
implementation were read again; no AGENTS.md was found. The task separates
execution from email interpretation: `CollectionRequest` receives resolved
references and numeric priorities, selects at most 10 in stable priority order,
and passes them to browser retrieval and packaging. No LLM or email code is
introduced. Playwright remains the only runtime dependency; ZIP handling uses
the Python standard library.

The implementation is described in `download-and-zip-plan.md`. Existing matter
verification, metadata/category counts, URL restrictions, TLS checks, diagnostic
inspection, and the `--download-one` command are retained.

## Observed live layouts

Evidence is saved locally under ignored `artifacts/collection-observation/`
(M12205) and `artifacts/collection-observation-M12451/` (M12451). Values below are
historical observations, not runtime defaults.

| Category | Row reference | Additional behavior |
|---|---|---|
| Other Documents | `.fm_object_201 .text`, numeric document number | Found Count in header object 213 |
| Key Documents | `.fm_object_268 .text`, numeric document number | No separate Found Count; scrollbar can be hidden when all rows fit |
| Exhibits | `.fm_object_300 .text`, exhibit label such as H-2 | Found Count in header object 319; confidential rows are explicitly labeled |
| Transcripts | `.fm_object_201 .text`, hearing date | Repeated object 202 fields list witnesses; GO GET IT action object 291 |
| Recordings | `.fm_object_201 .text`, hearing date | Object 202 is title; GO GET IT action object 292 |

The selected category has `.fm-selected`. Numeric/exhibit document titles use
object 202, and security labels use object 204. Only rows labeled Public qualify
for download. Hearing dates can repeat; an exact title/witness label can narrow
the reference, and traversal checks for ambiguity across the category.

FileMaker uses a separate `.v-grid-scroller-vertical` scrollbar and recycles
translated grid rows. Setting its scroll position changes which document data
is rendered. Row indexes/element handles must not be cached as document identity.
Retrieval waits for populated rows to cover the viewport, or all rows to be
populated for a short list. A hidden scrollbar can have zero client height even
when every row is already loaded. Category-specific field/action readiness also
prevents using a previous layout while the selected tab is changing.

For transcripts and recordings, GO GET IT first opens **Export Field to File**.
The textbox contains a site-provided default filename. Observed defaults include
`NSEB-M12451-January 7, 2026.pdf` and `260106-T001.MP3`. Confirming OK opens the
normal Download Files dialog; clicking its filename emits the download. Blank
export filenames fail without guessing a format. Recovery cancels an export
dialog or closes Download Files before attempting another selection.

## Early verification and fixes

`artifacts/20261025T004651Z_432954e2/` contains an initial successful two-file
collection: Other Documents 97291 (beyond the initial viewport) followed by
102674 (near the top). The resulting ZIP was reopened after browser closure,
with exact manifest order, CRC, and SHA-256 checks verified independently.

A later ten-selection run at `artifacts/20261025T005546Z_88c78450/` exposed the
hidden-scrollbar readiness issue in Key Documents. Per-selection failure capture
was added, and `artifacts/20261025T005836Z_a63b4d69/failures/1.*` confirmed that all
six Key Documents rows were present while the scrollbar was hidden. The readiness
condition was corrected and the offline short-list fixture reproduces it.

The same early run observed identical bytes for distinct Other Documents
references 102329 and 102330. Both requested references are now retained, with
`same_content_as` explaining the shared hash. No selected reference is silently
dropped or replaced because of content deduplication.

Offline virtual-list tests also exposed a detached-row race with
`scroll_into_view_if_needed()`. Retrieval now positions the actual virtual
scrollbar, then uses locators resolved by the selected identifier. Tests model
replaced rows, hidden scrollbars, category changes, delayed dialogs, and failed
downloads. The targeted request/browser/archive/file/CLI suite passed 107 tests
before the final full regression run.

MP3 validation checks ID3 tag bounds and a plausible complete first MPEG Layer
III frame. It does not decode audio or guarantee integrity throughout a recording.
PDF and ZIP checks retain their documented limits. Unknown formats are saved
with evidence and excluded from delivery.

## Archive contract

`delivery.zip` contains only validated requested documents in selected priority
order plus `manifest.json`. Source evidence is rechecked before packaging, and
member names/counts, CRC, manifest bytes, file sizes, and SHA-256 hashes are checked
before the temporary ZIP is atomically published. Tests include a file changing
during packaging and a simulated write failure, neither of which may publish a
delivery ZIP.

The manifest includes verified metadata/all five category counts, the supplied
request, selections, per-document outcomes, and file evidence. Invalid files and
browser evidence are excluded. Partial outcomes produce an explicitly partial
ZIP of successes and a nonzero CLI exit. With no successes, no ZIP is produced.

Email interpretation, priority creation, mailbox integration, and delivery remain
upstream/downstream stages outside this implementation.

## Ten-document live acceptance

The CLI run at `artifacts/20261025T010839Z_986a46fc/` completed with exit 0,
`status: collection_complete`, and `collection_complete: true`. The input had
11 prioritized candidates and a global limit of 10. The first selected item was
Other Documents 97291, beyond the initial viewport. Selection/download/archive
order was:

1. Other Documents 97291.
2. Key Documents 97289.
3. Exhibit H-2.
4. Other Documents 102674.
5. Other Documents 102454.
6. Other Documents 102417.
7. Other Documents 102329.
8. Other Documents 102330.
9. Other Documents 102293.
10. Other Documents 102295.

The lower-priority eleventh candidate 102266 was not downloaded. Live category
counts were 13 Exhibits, 6 Key Documents, 43 Other Documents, 0 Transcripts, and
0 Recordings; total 62. These remain historical observations.

After browser closure, an independent ZIP read verified exactly ten document
members plus the manifest, in the expected order; every member's byte size and
SHA-256 matched both the manifest and the saved file. CRC checks passed. The ZIP
was 2,426,609 bytes, SHA-256
`d0fcda85facf5ddd6ba7241b04dc536a9945f6369b615a5ce45498893ab4b44e`.
The final screenshot was reviewed against report metadata/counts. No origins were
blocked and no capture errors occurred. Ten `net::ERR_ABORTED` diagnostics reflect
the attachment navigations, as with the earlier one-file milestone.

Subsequent review tightened download handling: the filename click relies on the
download event instead of waiting for attachment navigation to load, save failures
cancel known downloads, and an uncertain event timeout stops remaining selections.
The filename must match the selected Download Files label. This prevents a late
event from silently being associated with the next selection. The associated
offline test requires a partial ZIP and a not-attempted later selection.

## Live transcript and recording acceptance

The first transcript attempt at `artifacts/20261025T025825Z_ca289458/` exposed a
server-header issue: the unquoted comma-containing filename caused Chromium's
`net::ERR_RESPONSE_HEADERS_MULTIPLE_CONTENT_DISPOSITION` failure. No file was
counted or packaged, and the remaining selection was not attempted after the
uncertain download timeout. No host restriction or TLS check was changed.

The export dialog now replaces commas in the site-provided filename with
underscores, commits that value with Tab, and confirms OK. An offline regression
fixture reproduces FileMaker's unquoted Content-Disposition header and verifies
the changed filename. The original and actual export names remain in evidence.

The fresh CLI run at `artifacts/20261025T030235Z_8327ff1c/` exited 0 with two
validated files and a complete verified ZIP:

- M12451 transcript for `01/07/2026`, recording the five displayed witnesses.
  Original export name `NSEB-M12451-January 7, 2026.pdf`; actual export name
  `NSEB-M12451-January 7_ 2026.pdf`. Size 1,078,958 bytes; SHA-256
  `698ecf67582d1125963aef3404b7c1400cd33d0d773b4bb3338e4a5c6c79e20d`.
- M12451 recording for `01/13/2026`, exact title **Day 5 - TRK 2**. The supplied
  title disambiguated the two recordings on that date. Export name
  `day 5 TRK 2.MP3`; size 2,832,320 bytes; SHA-256
  `03be9cdcc42c32491ce4427a2216194e16b3442776a232203a75c41b736e8815`.
- ZIP size 3,450,823 bytes; SHA-256
  `2957431438e0c43234f31008b4f051b3b69c6f104c22d84c1341cc34d2b59708`.

After browser closure, the ZIP was independently read and checked against its
manifest: two document members plus the manifest, correct order, matching sizes
and SHA-256 values, and passing CRC checks. No blocked origins or capture errors
occurred. Two attachment navigation aborts were retained as diagnostics.
This complements the earlier ten-file run to exercise all five categories live.

## Final regression result

The complete suite passed **154 tests on Python 3.14.2** in 291.06 seconds.
The command saved its final output, exit code 0, and JUnit results under ignored
`artifacts/downloader-release-tests.log`, `artifacts/downloader-release-tests.exit`,
and `artifacts/downloader-release-tests.xml`. The run used:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp artifacts/pytest-downloader-release -o cache_dir=artifacts/pytest-cache --tb=short --junitxml artifacts/downloader-release-tests.xml
```

Explicit workspace temp/cache locations avoid Windows access errors on directories
left by an earlier sandbox session. Browser-fixture timeouts were increased from
1–3 seconds to 5–10 seconds after observed startup/navigation timeouts on this
machine; the application's configurable default remains 30 seconds. No automatic
application retries were added. Existing metadata/count/one-file regressions,
the complete new collection path, archive failures, and the export-header fix all
passed in the final full run. `git diff --check` also passed.
