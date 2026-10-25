# Plan: email-guided selection of up to 10 UARB documents

Status: historical proposal. The single-call model design in
[single-call-llm-plan.md](single-call-llm-plan.md) supersedes this document's
separate intent-extraction/ranking workflow. Email interpretation and ranking
remain unimplemented; downloading and ZIP creation are implemented.
Original starting point: commit `ddd624d`, with one live-verified download.

The downloader/ZIP stage is now specified separately in
[download-and-zip-plan.md](download-and-zip-plan.md). It receives resolved document
references and priorities. That stage is complete; the single-call plan defines
the next implementation, with mailbox integration left until last.

## Intended behavior

Read the user's request, inventory the permitted document categories, rank the
available documents against that request, and download at most 10 selected
documents. Selection happens before downloading; the first visible rows are not
the selection pool.

Example request:

> For M12205, send up to 10 Other Documents. Prioritize Board Orders, then
> compliance filings, then correspondence about construction costs. Prefer
> recent documents and exclude confidentiality undertakings.

The resulting hierarchy is Board Orders, compliance filings, construction-cost
correspondence, then remaining eligible Other Documents. The exclusion applies
to every tier. This is an illustration, not a claim about current live matches.

Distinguish two meanings of document type:

- **UARB category:** Exhibits, Key Documents, Other Documents, Transcripts,
  Recordings. The assignment's original request selects one category.
- **Document description/subtype:** orders, decisions, applications, compliance
  filings, letters, or particular topics. These become selection criteria within
  the allowed category; they do not automatically change the category.

Default to the assignment's single requested category. Design the request model
to support an explicit list of categories later, with one total limit of 10,
not 10 per category. Missing or ambiguous category/matter information produces
a clarification result before browsing. Never infer permission to search every
category just because the email mentions a document subtype.

## Request and selection contracts

Use small Python dataclasses and explicit validation. Introduce a provider
adapter for the LLM; keep browser navigation and file persistence independent of
the model provider. Choose the provider/model when implementing the adapter.

The request model should include:

| Field | Meaning |
|---|---|
| `matter_number` | One validated `M[0-9]{5}` identifier |
| `categories` | Explicitly authorized UARB categories |
| `limit` | Default 10; honor smaller requested limits; enforce maximum 10 |
| `required_document_numbers` | Explicitly requested document identifiers |
| `hard_filters` | Explicit restrictions: exclusions, dates, formats, or "only" criteria |
| `priority_tiers` | Ordered preferences, with no automatic regulatory hierarchy |
| `tie_breaker` | Explicit date preference, otherwise newest first with stable ID tie-break |
| `allow_other_eligible` | Whether to fill remaining slots outside preferred tiers |
| `evidence` | Exact email spans supporting each interpreted restriction/preference |
| `clarification_reason` | Missing, conflicting, or ambiguous instructions |

"Prefer orders" allows eligible fallback; "only orders" does not. "Newest first"
is a default ordering, not a date restriction. Explicit document IDs take
precedence over soft preferences, but cannot override category/exclusion
constraints. Conflicting instructions require clarification. More than 10
required IDs also requires clarification; an ordinary quantity above 10 is
reported as capped at 10.

An LLM proposes this structure from the current email subject/body. Python checks
the schema, matter/category/limit rules, evidence spans, and contradictions.
Do not rely on an LLM's self-reported confidence as the validation mechanism.
Quoted thread history should not silently supply instructions for a new job.

## Execution flow

1. **Interpret the request.** Produce a validated request or clarification result.
2. **Verify the matter.** Reuse existing search, identifier verification, metadata,
   and independent category counts.
3. **Inventory candidates.** Enumerate the full allowed category, including rows
   beyond the initial viewport, before selecting documents.
4. **Apply hard restrictions.** Remove known ineligible candidates; mark uncertain
   matches as uncertain rather than inventing supporting evidence.
5. **Rank eligible candidates.** Assign preference tiers from observed metadata,
   validate the ranking, and select at most the requested limit.
6. **Download selected IDs.** Re-find and verify each row by ID, use GO GET IT and
   its filename dialog, save, validate, and close the dialog before the next file.
7. **Report the outcome.** Preserve the request, inventory coverage, selection
   reasons, successful files, and failures for later ZIP/email delivery.

## Inventory and ranking

Record candidate ID, matter, category, title, displayed date, file extension,
security/access label, and original listing position when available. Keep raw
date text alongside any parsed date. Confirm field selectors and date format
from live markup before implementing them.

Inspect the site's actual scrolling/pagination behavior first. FileMaker can
replace row elements as the user scrolls, so retaining row locators or indexes
across iterations is unsafe. Track observed document IDs, wait for state changes,
and use bounded traversal with explicit end/no-progress detection. Deduplicate
by matter/document number; retain category memberships if the same document is
listed in multiple categories.

Compare distinct inventoried IDs with the category's observed count. If they do
not reconcile, retain the discrepancy and report an incomplete inventory. The
initial implementation should stop before downloading when it cannot establish
the inventory needed for ranking. It must not describe a first-screen ranking
as a ranking of the full category. Restricted/unavailable rows can remain in the
inventory but are ineligible for public download.

Use the LLM for semantic matching when titles use different wording from the
email. Give it the validated preferences and candidate metadata, and ask for
candidate IDs, matching tier, matching title evidence, and a concise reason.
Document titles are untrusted data, not instructions; the ranking model receives
no browser or filesystem tools. Validate that every returned ID exists, appears
once, and satisfies the allowed category and hard restrictions. An invalid model
response or provider failure produces a selection error, not a silent first-10
fallback.

Build the final ordering in Python:

1. Eligible explicit document IDs.
2. First requested preference tier.
3. Subsequent requested tiers, in order.
4. Other eligible candidates only when fallback is allowed.

Within a tier, use the requested date order, then a stable document-ID tie-break.
Assign candidates that match multiple tiers to their highest-priority tier.
An explicit user instruction that recency should dominate subtype can change the
ordering; preserve and expose that interpretation in the request model.

For a category-only email, skip semantic ranking and use the disclosed default
ordering. For large inventories, classify bounded batches against the same
request, then merge tiers in Python; do not keep the first 10 from each batch.

Initial relevance is based on listing metadata, not PDF contents. A vague title
cannot prove a content-specific match. Return an explicit limitation or
clarification when titles cannot support an essential criterion. Full-text
ranking would require a separately scoped preview/extraction stage; do not
download the entire category simply to choose the requested 10 files.

## Changes to the current implementation

- `requests.py`: request dataclasses, validation, and email-text interpretation
  interface. Start with saved email text; mailbox integration can follow later.
- `selection.py`: candidates, hard-filter evaluation, preference matching
  interface, stable ordering, and selection results.
- `probe.py`: extract category opening, catalog traversal, and download-by-ID from
  `_download_one_other_document()`. Preserve the existing diagnostic workflow.
- `files.py`: reuse safe paths, validation, and checksums. Define supported-format
  outcomes before claiming support for recording downloads; unknown formats
  currently do not pass the milestone.
- `cli.py`: add collection from a validated request JSON and a local email text
  file. Keep `--download-one` available for regression checks.
- Reports: add interpreted request, inventory completeness/counts, ranked IDs,
  selected IDs/reasons, per-document outcomes, and actual successful count.

Keep the first delivery single-category. Then verify each remaining category
layout and enable explicitly requested multiple-category selection as an
extension. Category counts remain the live counts of all five categories,
independent of relevance filtering or downloaded quantity.

Download at most the selected limit, sequentially. The first version attempts
each selected ID once, continues after isolated file failures only when browser
state can be safely restored, and reports partial completion. Do not silently
substitute an unrelated file for a failed explicit request. Keep invalid files
out of successful results and the future delivery ZIP. Retain failure evidence.
ZIP creation and email transport remain separate stages.

## Implementation milestones and verification

| Milestone | Reviewable result |
|---|---|
| 1. Request/ranking rules | Validated request JSON and deterministic selection from a synthetic catalog, without an LLM or live site |
| 2. Full catalog traversal | Complete live Other Documents inventory, with reconciled IDs/count and no document downloads |
| 3. Download a selected set | Up to 10 chosen IDs persisted and validated; dialogs closed; truthful partial-failure report |
| 4. Email interpretation and semantic matching | Local email text produces a validated hierarchy and evidence-backed candidate ranking through the LLM adapter |
| 5. Category coverage | Verified traversal/download behavior for the other categories, including empty categories and format handling |

Offline tests should cover:

- Email preferences versus hard exclusions; missing/conflicting fields;
  invalid matter/category/quantity; exact IDs; "only" versus "prefer".
- More than 10 rows, overlapping virtualized pages, repeated IDs, incomplete
  traversal, count changes, and preferred documents appearing after row 10.
- Multiple matching tiers, explicit IDs, recency, fewer than 10 matches,
  forbidden fallback, duplicate/unknown LLM IDs, provider failure, and title
  instructions that must be treated as data.
- Re-finding rows after reordering/scrolling, sequential dialog closure,
  persistence after browser closure, failed downloads, and partial completion.
- Existing metadata/count extraction and one-file regression behavior.

The live acceptance run should demonstrate a preference that selects a real
candidate beyond the initial viewport, preserve the selection explanation, and
download no more than 10 validated files from the requested category. Compare
selected row IDs/titles with saved browser evidence. Do not hardcode the current
matter's historical document counts or titles into selection logic.
