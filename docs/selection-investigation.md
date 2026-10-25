# Email-text selection implementation and verification

The starting checkout was clean at `c08ab7f`. The assignment PDF, existing
collector, and agreed single-call plan were read before implementation. No
repository AGENTS.md was found. Email intake and delivery remain deferred.
The user explicitly reserved API-key configuration until the end; no real
provider generation has been performed or represented as verified.

## Implemented flow

`selection.py` accepts a local subject/body object, extracts exactly one distinct
complete ASCII matter number before browser/model work, and validates the final
selection. `catalog.py` traverses every category twice and reconciles contiguous
row positions with observed counts. `llm.py` sends the original request and all
eligible listing metadata through the official optional OpenAI SDK, using a
strict Responses schema and one logical ranking stage. The adapter permits one
identical retry for transient failures; SDK retries are disabled. The final
selection maps directly to the existing `CollectionRequest`, downloader, and ZIP
path in the same browser session. No model repair call or replacement selection
is made. `--dry-run` saves accepted priorities without downloading.

Generic requests use complementary written-document coverage, with usefulness
ahead of recency and no fixed category quotas. Explicit or inferred requests can
include transcripts/recordings; the default overview cannot. Reasons and exact
matching metadata are saved outside the delivery ZIP. No full document contents
are read for relevance, and structural checks do not establish semantic quality.

The optional dependency is `openai>=2,<3` (installed verification version 2.54.0).
There is no framework, database, email SDK, tokenizer, or evaluation dependency.
Ordinary inspection and explicit-priority collection retain lazy browser imports
and do not require OpenAI installation/configuration.

## Live catalog observations

The October 25, 2026 snapshots in ignored
`artifacts/catalog-acceptance-20261025T212736Z/` established:

| Matter | Listed rows across five categories | Eligible listing candidates | Enumeration and verification | Conservative input-token bound for illustrative generic request |
|---|---:|---:|---:|---:|
| M12205 | 62 | 50 | 2.753 seconds | 20,408 |
| M12451 | 331 | 269 | 13.047 seconds | 68,037 |

Twelve M12205 rows and 62 M12451 rows were not Public. The eligible counts include
cross-category aliases (six and seven respectively); final selection validation
prevents downloading a native reference twice. These are historical observations,
not defaults or a distribution/max-size estimate. The input bounds include
instructions/schema and framing headroom; they are not provider-reported token
usage or a cost measurement. Both fit the default 96,000 input budget. Model
generation was not invoked during these catalog checks.

Dates use row object 203 for written documents, with displayed label Date Filed
on Exhibits and Date on Key/Other Documents. Hearing dates are native references
for Transcripts/Recordings. Extension selectors differ: 314 for Exhibits, 284 for
Key Documents, and 308 for Other Documents. Media layouts do not expose the same
extension field. Titles/witnesses use 202 and security 204, as in the existing
collector. Do not infer original filenames, creation timestamps, file sizes, or
unread contents from these fields.

Early live attempts caught issues absent from the initial fixtures:

- Extension selector unions accidentally included an action label from another
  layout. Catalog reads now use category-specific selectors.
- Vaadin moves recycled rows before replacing their text. A position can briefly
  contain another row's identifier. Shared readiness now requires populated
  `v-grid-row-has-data` rows and no active Vaadin client work. This is a state
  check, not a fixed sleep. Offline fixtures reproduce recycled stale text.
- After document navigation, tabs belong to a header, and some layouts leave
  another tab's count blank. Catalog verification re-searches the matter through
  the existing verified search helpers and compares all five detail-page counts.

UI snapshots are not database transactions. Two passes and count re-verification
detect observed instability; the downloader still re-resolves native references
and bound title/witness metadata immediately before retrieval. A later change
can produce the existing explicit failure/partial ZIP outcome.

## Offline and provider verification boundaries

Tests exercise the real OpenAI SDK over an in-memory HTTP transport, including
the Responses payload, complete candidate input, one/two/zero attempt limits,
retryable versus terminal errors, timeouts, refusals, incomplete/malformed
responses, final reference validation, and browser-to-ZIP integration. Browser
fixtures do not contact UARB. A mocked provider answer verifies wiring, not LLM
understanding or account access.

`tests/fixtures/selection-evals.json` supplies 13 synthetic labelled cases.
`scripts/evaluate_selection.py` validates them without a provider by default;
`--live` explicitly enables paid model calls. The judge checks allowed/required
matches, exclusions, quantities, and preference tiers without imposing one order
on equivalent candidates. It includes generic overviews, recency, implicit scope,
explicit categories/audio, only/prefer, contradictions, no matches, vague titles,
and unrelated instructions embedded in email/catalog text.

Real-provider evaluations and one real model-selected live download/ZIP remain
pending the user's deferred credentials. Run the labelled evaluations, then the
README email-text command (a relevant offscreen selection must outrank initial
rows) and inspect reasons, canonical references, persisted files, and ZIP checks.
No claim of measured live-model relevance is made before those checks pass.

## Final verification evidence

The full regression passed 246 tests on Python 3.14.2 in 433.80 seconds, with
zero failures/errors/skips (`artifacts/llm-release.xml` and `.log`, exit file 0).
After the final catalog-buffer filter and fixed insufficient-detail message,
all 58 affected browser/CLI tests passed in 144.83 seconds, including one new
buffer-row regression (`artifacts/selection-final-boundary.xml` and `.log`, exit
file 0). The original 154-test collection baseline was included in the full run.
A final 62-test adapter/contract/evaluation run passed after recording timeout
and retry configuration and rejecting injected SDK clients with automatic
retries enabled (`artifacts/final-adapter-audit.xml`). All 248 currently collected
cases have passing evidence across these runs. The labelled fixture
structure/judge checks and `pip check` also passed.

`artifacts/20261025T214726Z_848b6e2f/` records a real UARB browser/download run
using the real SDK with a deliberately simulated provider HTTP response. Its
model/response identifiers and `verification-note.json` explicitly label the
simulation. No external model call was made. It cataloged M12205, selected Other
Documents 97291 (Hearing Order, zero-based listing position 42) before 102674
(Board Order), downloaded both real PDFs, and completed the delivery ZIP.
The one logical SDK request, order, persisted bytes, exact archive members, CRC,
and SHA-256 associations were checked independently after browser closure.
The ZIP was 342,390 bytes with SHA-256
`40e05a362672b94f0020c22fc8a1d4e1f24449e6ea661fb75e1a84c68d2e001d`.
There were no blocked origins or capture errors. This verifies live browser and
packaging integration, not model interpretation or provider access.

`examples/email-offscreen-request.json` provides a subtype-based request for the
remaining real-provider acceptance run. Live results must establish its current
matches; no historical reference or example is substituted by the application.
