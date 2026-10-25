"""Execute explicit document priorities through observed UARB list layouts."""

import asyncio
from pathlib import Path
import re

from .collection import CollectionRequest, DocumentPriority
from .files import safe_download_path, validate_download


# Observed live: Other Documents, Key Documents, and Exhibits respectively.
# Use the union only inside one row, requiring exactly one matching identifier.
IDENTIFIER = ".fm_object_201 .text, .fm_object_268 .text, .fm_object_300 .text"


class DownloadStateUncertain(RuntimeError):
    """An uncorrelated late download could be mistaken for the next selection."""


async def open_category(page, category: str, count: int, timeout_ms: int) -> None:
    from playwright.async_api import expect

    if count == 0:
        raise ValueError(f"{category} has no documents")
    label = re.compile(rf"^{re.escape(category)}\s*-\s*{count}\s*$")
    selected = page.locator(".fm-selected").get_by_role("button", name=label)
    if not await selected.count():
        await page.get_by_role("button", name=label).click()
    await selected.wait_for(state="visible")
    grid = page.get_by_role("grid")
    number_object = {"Other Documents": 201, "Key Documents": 268, "Exhibits": 300,
                     "Transcripts": 201, "Recordings": 201}[category]
    first_number = grid.locator(f".fm_object_{number_object} .text").first
    pattern = re.compile(r"^[0-9]{2}/[0-9]{2}/[0-9]{4}\s*$") if category in {"Transcripts", "Recordings"} else re.compile(r"\S")
    await expect(first_number).to_have_text(pattern, timeout=timeout_ms, use_inner_text=True)
    if category in {"Transcripts", "Recordings"}:
        action_object = 291 if category == "Transcripts" else 292
        await grid.locator(f".fm_object_{action_object}").get_by_role("button", name="GO GET IT", exact=True).first.wait_for(state="visible")
    # Key Documents has no Found Count field. The other two observed layouts do.
    count_object = {"Other Documents": 213, "Exhibits": 319}.get(category)
    if count_object:
        await expect(page.locator(f".iwps_header .fm_object_{count_object}")).to_have_text(
            re.compile(rf"^Found Count:\s*{count}\s*$"), timeout=timeout_ms, use_inner_text=True,
        )


async def _ready_viewport(grid, count: int, timeout_ms: int) -> None:
    """Wait until populated virtual rows cover the scroller's current viewport."""
    from playwright.async_api import expect

    # Vaadin uses a separate invisible scrollbar and recycles translated rows.
    # Waiting for viewport coverage prevents racing the server's row update.
    await expect(grid).to_have_count(1, timeout=timeout_ms)
    await grid.page.wait_for_function("""count => {
        const grids = document.querySelectorAll('.v-grid:has([role=grid])');
        if (grids.length !== 1) return false;
        const grid = grids[0];
        const bar = grid.querySelector('.v-grid-scroller-vertical');
        const rows = [...grid.querySelectorAll('tbody.v-grid-body > tr')];
        if (!bar || !rows.length) return false;
        let low = Infinity, high = -Infinity;
        let populated = 0;
        for (const row of rows) {
            const id = row.querySelector('.fm_object_201 .text, .fm_object_268 .text, .fm_object_300 .text');
            if (!id || !id.innerText.trim()) continue;
            populated++;
            const t = getComputedStyle(row).transform;
            const y = t === 'none' ? row.offsetTop : new DOMMatrixReadOnly(t).m42;
            low = Math.min(low, y); high = Math.max(high, y + row.offsetHeight);
        }
        return populated >= count || (bar.clientHeight > 0 &&
            low <= bar.scrollTop + 1 && high >= Math.min(bar.scrollHeight, bar.scrollTop + bar.clientHeight) - 1);
    }""", arg=count, timeout=timeout_ms)


async def find_document(page, selected: DocumentPriority, count: int, timeout_ms: int):
    from playwright.async_api import expect

    grid = page.locator(".v-grid").filter(has=page.get_by_role("grid"))
    await expect(grid).to_have_count(1, timeout=timeout_ms)
    scroller = grid.locator(".v-grid-scroller-vertical")
    await expect(scroller).to_have_count(1, timeout=timeout_ms)
    await scroller.evaluate("e => {e.scrollTop = 0}")
    seen = set()
    document_number = selected.reference
    date_positions = set()

    async def position_target(target, y):
        await scroller.evaluate("(e,y) => {e.scrollTop=y}", y)
        await _ready_viewport(grid, count, timeout_ms)
        await expect(target).to_have_count(1, timeout=timeout_ms)
        await expect(target.locator(IDENTIFIER)).to_have_text(
            re.compile(rf"^{re.escape(document_number)}\s*$"), timeout=timeout_ms, use_inner_text=True,
        )
        return target
    # Each advance covers at least one row; the count bounds the traversal.
    for _ in range(count + 2):
        await _ready_viewport(grid, count, timeout_ms)
        identifiers = grid.locator(f"tbody.v-grid-body :is({IDENTIFIER})")
        visible = [(value.strip()) for value in await identifiers.all_inner_texts()]
        seen.update(visible)
        target = grid.get_by_role("row").filter(
            has=page.locator(IDENTIFIER).filter(has_text=re.compile(rf"^{re.escape(document_number)}\s*$")),
        )
        if selected.title is not None:
            target = target.filter(has=page.locator(".fm_object_202 .text").filter(
                has_text=re.compile(rf"^{re.escape(selected.title)}\s*$"),
            ))
        if await target.count():
            await expect(target).to_have_count(1, timeout=timeout_ms)
            row_y = await target.evaluate("e => { const t=getComputedStyle(e).transform; return t==='none' ? e.offsetTop : new DOMMatrixReadOnly(t).m42 }")
            if selected.hearing_date is None:
                return await position_target(target, row_y)
            # Hearing dates can repeat. Scan the complete list before accepting
            # a date/title reference as unique, including offscreen matches.
            date_positions.add(row_y)
            if len(date_positions) > 1:
                raise ValueError("Ambiguous hearing date/title reference; provide a distinguishing row title")
        position = await scroller.evaluate("""e => ({top:e.scrollTop, height:e.clientHeight, max:e.scrollHeight})""")
        if position["top"] + position["height"] >= position["max"] - 1:
            if date_positions:
                return await position_target(target, next(iter(date_positions)))
            raise ValueError(f"Document {document_number} not found in its requested category ({len(seen)} IDs observed)")
        moved = await scroller.evaluate("e => { const before=e.scrollTop; e.scrollTop += Math.max(1, Math.floor(e.clientHeight * .75)); return e.scrollTop > before }")
        if not moved:
            raise RuntimeError("Document list traversal made no progress")
    raise RuntimeError("Document list traversal exceeded its bounded step limit")


async def download_row(page, row, candidate: dict, run_dir: Path, report: dict, timeout_ms: int, allowed_url) -> None:
    from playwright.async_api import expect

    await expect(row.locator(IDENTIFIER)).to_have_text(
        re.compile(rf"^{re.escape(candidate['document_number'])}\s*$"), timeout=timeout_ms, use_inner_text=True,
    )
    title = row.locator(".fm_object_202 .text")
    if candidate.get("category") == "Transcripts":
        await expect(title.first).to_have_text(re.compile(r"\S"), timeout=timeout_ms, use_inner_text=True)
        candidate["witnesses"] = [value.strip() for value in await title.all_inner_texts()]
    else:
        await expect(title).to_have_text(re.compile(r"\S"), timeout=timeout_ms, use_inner_text=True)
        observed_title = (await title.inner_text()).strip()
        if candidate.get("title") is not None and candidate["title"] != observed_title:
            raise ValueError("Requested title does not match the observed document row")
        candidate["title"] = observed_title
    security = row.locator(".fm_object_204 .text")
    await expect(security).to_have_text(re.compile(r"\S"), timeout=timeout_ms, use_inner_text=True)
    candidate["security"] = (await security.inner_text()).strip()
    if candidate["security"].casefold() != "public":
        raise ValueError("Selected document is not labeled Public")

    report["stage"] = "prepare_download"
    await row.get_by_role("button", name="GO GET IT", exact=True).click()
    dialog = page.get_by_role("dialog", name="Download Files", exact=True)
    export = page.get_by_role("dialog", name="Export Field to File", exact=True)
    await dialog.or_(export).wait_for(state="visible")
    if await export.is_visible():
        # Observed for transcript PDFs and recordings: accept the site's default
        # filename, then FileMaker presents its normal Download Files dialog.
        report["stage"] = "prepare_export"
        filename = await export.get_by_role("textbox").input_value()
        if not filename.strip():
            raise ValueError("Export dialog has no default filename; refusing to guess a file format")
        # FileMaker emits an unquoted Content-Disposition filename. A comma in
        # its default name makes Chromium reject the response as multiple header
        # values. Change the export name through the UI, preserving its suffix.
        if "," in filename:
            candidate["original_export_filename"] = filename
            filename = filename.replace(",", "_")
            await export.get_by_role("textbox").fill(filename)
            await export.get_by_role("textbox").press("Tab")
        candidate["export_filename"] = filename
        await export.get_by_role("button", name="OK", exact=True).click()
        await export.wait_for(state="hidden")
        report["stage"] = "prepare_download"
    await dialog.wait_for(state="visible")
    button = dialog.get_by_role("button").and_(page.locator(".fm-download-button"))
    await expect(button).to_have_count(1, timeout=timeout_ms)
    await expect(button).to_have_text(re.compile(r"\S"), timeout=timeout_ms, use_inner_text=True)
    candidate["displayed_filename"] = (await button.inner_text()).strip()
    report["stage"] = "download_document"
    try:
        async with page.expect_download(timeout=timeout_ms) as pending:
            # The download event is the readiness signal. Do not also wait for
            # the attachment's navigation to load as an ordinary web page.
            await button.click(no_wait_after=True)
    except Exception as exc:
        raise DownloadStateUncertain(f"Download did not start reliably: {exc}") from exc
    download = await pending.value
    if not allowed_url(download.url):
        await download.cancel()
        raise ValueError("Download URL is outside the permitted UARB origin")
    candidate["suggested_filename"] = download.suggested_filename
    if download.suggested_filename != candidate["displayed_filename"]:
        await download.cancel()
        raise ValueError("Downloaded filename does not match the selected dialog filename")
    path = safe_download_path(run_dir / "downloads", download.suggested_filename)
    candidate["path"] = path.relative_to(run_dir.resolve()).as_posix()
    try:
        await asyncio.wait_for(download.save_as(path), timeout_ms / 1000)
    except Exception:
        try:
            await asyncio.wait_for(download.cancel(), timeout_ms / 1000)
        except Exception as cancel:
            raise DownloadStateUncertain(f"Failed to cancel an incomplete download: {cancel}") from cancel
        raise
    report["stage"] = "validate_download"
    candidate["file_evidence"] = validate_download(path).to_dict()
    if not candidate["file_evidence"]["format_validated"]:
        raise ValueError("Document persisted, but its file format is not validated")


async def close_download_dialog(page, timeout_ms: int) -> None:
    export = page.get_by_role("dialog", name="Export Field to File", exact=True)
    if await export.count():
        await export.get_by_role("button", name="Cancel", exact=True).click(timeout=timeout_ms)
        await export.wait_for(state="hidden", timeout=timeout_ms)
    dialog = page.get_by_role("dialog", name="Download Files", exact=True)
    if await dialog.count():
        await dialog.get_by_role("button", name="Close", exact=True).click(timeout=timeout_ms)
        await dialog.wait_for(state="hidden", timeout=timeout_ms)
    # Unexpected modal dialogs make continuing unsafe, including server errors.
    if await page.get_by_role("dialog").count():
        raise RuntimeError("An unexpected dialog prevents continuing document retrieval")


async def collect_documents(page, request: CollectionRequest, report: dict, run_dir: Path, timeout_ms: int, allowed_url) -> None:
    for selected in request.selected():
        candidate = {"matter_number": request.matter_number, **selected.to_dict(),
                     "document_number": selected.reference}
        report["download_candidate"] = candidate
        outcome = {"document": candidate, "status": "starting"}
        report["document_outcomes"].append(outcome)
        try:
            report["stage"] = "open_category"
            count = report["category_counts"][selected.category]
            await open_category(page, selected.category, count, timeout_ms)
            report["stage"] = "locate_document"
            row = await find_document(page, selected, count, timeout_ms)
            await download_row(page, row, candidate, run_dir, report, timeout_ms, allowed_url)
            previous = next((d for d in report["downloaded_documents"]
                             if d["file_evidence"]["sha256"] == candidate["file_evidence"]["sha256"]), None)
            if previous:
                candidate["same_content_as"] = previous["document_number"]
            # Different requested references can legitimately contain identical
            # bytes. Preserve both associations instead of dropping a selection.
            report["downloaded_documents"].append(dict(candidate))
            report["downloaded_document_count"] = len(report["downloaded_documents"])
            report["phase1_milestone_complete"] = True
            outcome["status"] = "downloaded"
        except Exception as exc:
            outcome.update(status="failed", stage=report["stage"],
                           error={"type": type(exc).__name__, "message": str(exc)})
            try:
                evidence = run_dir / "failures"
                evidence.mkdir(exist_ok=True)
                prefix = str(len(report["document_outcomes"]))
                (evidence / f"{prefix}.html").write_text(await page.content(), encoding="utf-8")
                (evidence / f"{prefix}.txt").write_text(await page.locator("body").inner_text(timeout=5000), encoding="utf-8")
                await page.screenshot(path=str(evidence / f"{prefix}.png"), timeout=5000)
                outcome["evidence_prefix"] = f"failures/{prefix}"
            except Exception as capture:
                report["capture_errors"].append(str(capture))
            if isinstance(exc, DownloadStateUncertain):
                raise
        report["stage"] = "close_download_dialog"
        # If closure fails, the outer workflow reports failure and still packages
        # validated successes. Remaining selections are recorded as not attempted.
        await close_download_dialog(page, timeout_ms)
