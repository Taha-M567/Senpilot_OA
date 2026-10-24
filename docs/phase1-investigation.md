# Phase 1 investigation - 2026-10-24

## Repository and assignment

The existing repository had an initial commit and the assignment PDF, with no
application code. Its GitHub remote was already configured. The assignment was
read locally; its example metadata and counts were not used as live results.

## Environment observations

- `C:\Python312\python.exe` is Python 3.12.7 and was used to create `.venv`.
- The default `python` command resolves to Python 3.11, so setup instructions use
  an explicit interpreter rather than relying on PATH.
- `uv` is installed. Python Playwright was not installed or found in the local
  dependency cache. No browser was exposed through the browser-control tool.
- The bundled tooling has Node Playwright and an installed Microsoft Edge, used
  only to diagnose browser navigation. Node is not a project dependency.

## Actual attempts

1. Installing `playwright pytest pypdf` in `.venv` with uv failed fetching
   `https://pypi.org/simple/playwright/`: a connection was denied by the local
   execution environment (`os error 10013`). `pypdf` was an investigation tool,
   not a runtime dependency; the assignment was subsequently read using the
   bundled Python runtime's existing PDF reader.
2. A web-tool fetch of the target UARB URL timed out. This alone does not
   establish an outage.
3. Bundled Node Playwright successfully launched headless Microsoft Edge. Its
   `page.goto()` to the exact assignment URL failed with
   `net::ERR_NETWORK_ACCESS_DENIED` before a usable page was available.
4. Running the new Python command in `.venv` wrote a failure report with stage
   `import_playwright` and `ModuleNotFoundError`. This is the expected result
   until dependency installation is possible.

These errors establish a restriction in this execution environment. They do not
establish that the UARB server is down or that FileMaker automation is infeasible.
No HTTP success status, live DOM, matter metadata, category count, or real file
was obtained during those initial restricted attempts.

## Network access verified after session permission change

The current session reports network enabled and filesystem sandboxing disabled.
No Windows firewall rules or system networking settings were changed by the agent.
The working runtime state verifies that the original session restriction has been
removed; it does not establish whether a particular saved config entry caused it.

- HTTPS to PyPI returned HTTP 200 and 307,663 bytes.
- An independent subagent verified HTTPS to both PyPI and UARB using default TLS
  verification. UARB returned HTTP 200 with FileMaker/Vaadin HTML after its
  same-host redirect.
- `uv pip install --python .venv\Scripts\python.exe --cache-dir .cache/uv -e '.[dev]'`
  succeeded, installing Python Playwright 1.64.1 and pytest 9.1.1.
- `python -m playwright install chromium` successfully downloaded the browser
  into the workspace's `.cache/ms-playwright` directory.
- The actual Python probe completed with `inspection_captured`, HTTP 200, title
  `UARB15 (APUARB11)`, and no blocked origins, failed requests, or capture errors.
  Its text and screenshot show the Public Documents Database and Go Directly to
  Matter form. Report, DOM, screenshot, and trace are saved under
  `artifacts/20261024T172309Z_a271075c/` (ignored by Git).
- All **29 tests pass inside the project's own Python 3.12 virtual environment**.

The network-access goal is verified. Matter search, metadata/count extraction,
and a real document download remain the next Phase 1 work.

## Validation scope

Offline tests cover filename containment/uniqueness, empty and HTML downloads,
basic PDF markers, checksums, ZIP container integrity, URL restrictions, and
failure-report creation when Playwright is unavailable. They do not simulate or
prove the unknown UARB DOM or download behavior. Python 3.12 compilation is also
checked. The final offline run passed **29 tests on Python 3.12.7**, using the
existing local pytest installation on the test runner's import path because
PyPI installation was blocked. The subsequent successful clean editable-package
installation, Python Playwright execution, and virtual-environment test run are
recorded above. Unit tests do not contact the public website; the explicit live
inspection probe does.

## Matter search verified

Live inspection established the following interaction across fresh sessions:

- The `eg M01234` placeholder belongs to a `.fm-textarea` widget. Clicking its
  `.text` child asynchronously activates `contenteditable=true`; clicking the
  placeholder itself is intercepted by that child.
- Filling and clicking Search alone returned No Records Found, even for M12205.
  Pressing Tab after filling commits the FileMaker value, then Search opens the
  detail page. This is an observed editor requirement, not a delay heuristic.
- The stable layout class `.fm_object_258` contains the direct-matter Search
  button. The two other Search buttons serve separate controls. Generated IDs
  such as `b0p0o258i0i0r1` are not selector contracts.
- The detail layout has a combined `Matter No<br>Status` label and Back to Search
  Results button. Its returned matter number is `.fm_object_286.v-readonly .text`.
  The layout can appear before data; verification waits for a nonempty field
  and compares the observed identifier exactly with the request.
- No-result feedback reads: `No records matched your search request. Click OK
  to return to Search page.` This fails clearly instead of timing out.

Local observation evidence is under `artifacts/observation-1/` through
`artifacts/observation-6/`. Early attempts also documented activation and combined
label selector failures; these were corrected before final verification.

The implemented CLI verified M12205 with exit code 0 in two fresh sessions:

- Headed: `artifacts/20261024T222116Z_ffa369cc/`.
- Headless: `artifacts/20261024T222211Z_c07d5420/`.

Both reports record `matter_found`, `verify_matter`, requested and verified
identifier M12205, and `phase1_milestone_complete: false`. They include final
screenshots, HTML, text, frame inventories, and traces. Verification needed no
manual input. The complete offline suite passes **52 tests**, including all 29
existing tests and new CLI/browser-fixture coverage. The browser fixture is a
reduced version of observed markup with local event handlers, not a replay of
server internals.

## Remaining website behavior

Metadata field associations, extraction of all five independent category counts,
category tab behavior, and Go Get It download/popup behavior remain unimplemented.
The next milestone extracts metadata/counts, then persists and validates one real
download. Pagination and all-category support follow the one-file path. ZIP
creation and email integration remain deferred.
