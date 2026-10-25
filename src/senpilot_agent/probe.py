"""Inspect UARB, extract matter details, and optionally persist one document."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from .files import safe_download_path, validate_download


UARB_URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"
UARB_HOST = "uarb.novascotia.ca"


def allowed_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme in {"https", "wss"}
            and parsed.hostname == UARB_HOST
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


async def inspect_site(
    output: Path, *, headed: bool = False, channel: str | None = None,
    timeout_ms: int = 30_000, inspect_seconds: int = 0,
    matter_number: str | None = None,
    download_one: bool = False,
) -> tuple[Path, dict]:
    """Capture live DOM and controls, or a truthful failure report.

    Imports are lazy so a missing Playwright installation still produces a useful
    diagnostic. Only explicitly allowed UARB network requests are permitted.
    """
    run_dir = output / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        + "_" + uuid4().hex[:8])
    run_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "url": UARB_URL,
        "purpose": "Collect matter metadata and category counts" if matter_number else "Inspect the live DOM",
        "intended_request": {"matter_number": matter_number},
        "verified_matter_number": None,
        "matter_metadata": {},
        "category_counts": {},
        "total_document_count": None,
        "metadata_counts_complete": False,
        "phase1_milestone_complete": False,
        "download_candidate": None,
        "downloaded_documents": [],
        "downloaded_document_count": 0,
        "status": "starting",
        "stage": "import_playwright",
        "blocked_origins": [],
        "failed_requests": [],
        "capture_errors": [],
    }
    browser = context = page = None
    tracing_started = False
    try:
        report["stage"] = "validate_request"
        if download_one:
            report["intended_request"].update(document_category="Other Documents", download_limit=1)
            if matter_number is None:
                raise ValueError("Downloading requires a matter number")
            report["purpose"] = "Download one Other Documents file after verifying matter details"
        report["stage"] = "import_playwright"
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            try:
                report["stage"] = "launch_browser"
                browser = await playwright.chromium.launch(
                    headless=not headed, channel=channel, timeout=timeout_ms,
                )
                context = await browser.new_context(
                    accept_downloads=True, viewport={"width": 1440, "height": 1000},
                    service_workers="block",
                )

                async def guard(route):
                    if allowed_url(route.request.url):
                        await route.continue_()
                    else:
                        parsed = urlsplit(route.request.url)
                        origin = f"{parsed.scheme}://{parsed.netloc}"
                        if origin not in report["blocked_origins"]:
                            report["blocked_origins"].append(origin)
                        await route.abort("blockedbyclient")

                await context.route("**/*", guard)

                async def guard_websocket(socket):
                    if allowed_url(socket.url):
                        socket.connect_to_server()
                    else:
                        parsed = urlsplit(socket.url)
                        origin = f"{parsed.scheme}://{parsed.netloc}"
                        if origin not in report["blocked_origins"]:
                            report["blocked_origins"].append(origin)
                        await socket.close(code=1008, reason="Origin is not allowed")

                await context.route_web_socket("**/*", guard_websocket)
                await context.tracing.start(screenshots=True, snapshots=True, sources=True)
                tracing_started = True
                page = await context.new_page()
                page.set_default_timeout(timeout_ms)
                page.set_default_navigation_timeout(timeout_ms)

                def request_failed(request):
                    # Exclude query strings and session identifiers from this log.
                    report["failed_requests"].append({
                        "host": urlsplit(request.url).hostname,
                        "reason": request.failure,
                    })

                context.on("requestfailed", request_failed)
                report["stage"] = "navigate"
                response = await page.goto(UARB_URL, wait_until="domcontentloaded")
                report["http_status"] = response.status if response else None
                if response is not None and response.status >= 400:
                    raise RuntimeError(f"UARB returned HTTP {response.status}")
                report["stage"] = "inspect_dom"
                await page.locator("body").wait_for(state="visible")
                # Generic readiness for inspection, not a UARB selector contract.
                await page.locator(
                    "input:visible, button:visible, [role=button]:visible, "
                    "[contenteditable=true]:visible"
                ).first.wait_for(state="visible")
                if matter_number is not None:
                    detail = await _search_matter(page, matter_number, report)
                    await _extract_matter_details(detail, report, timeout_ms)
                    if download_one:
                        await _download_one_other_document(page, detail, report, run_dir, timeout_ms)
                if inspect_seconds:
                    # Explicit human inspection window, not a readiness heuristic.
                    await asyncio.sleep(inspect_seconds)
                report["title"] = await page.title()
                report["frames"] = []
                for index, frame in enumerate(page.frames):
                    if frame.url != "about:blank" and not allowed_url(frame.url):
                        continue
                    controls = await frame.locator(
                        "input, textarea, select, button, a, [role], [contenteditable]"
                    ).evaluate_all("""elements => elements.map(el => ({
                        tag: el.tagName, id: el.id, name: el.getAttribute('name'),
                        role: el.getAttribute('role'), type: el.getAttribute('type'),
                        ariaLabel: el.getAttribute('aria-label'),
                        placeholder: el.getAttribute('placeholder'),
                        text: (el.innerText || '').trim().slice(0, 250),
                        visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                    }))""")
                    (run_dir / f"frame-{index}.html").write_text(
                        await frame.content(), encoding="utf-8",
                    )
                    report["frames"].append({"index": index, "controls": controls})
                report["status"] = (
                    "document_downloaded" if download_one else
                    "matter_found" if matter_number is not None else "inspection_captured"
                )
                if matter_number is None:
                    report["stage"] = "inspect_dom"
            finally:
                if page is not None:
                    try:
                        await page.screenshot(path=str(run_dir / "page.png"), full_page=True,
                                              timeout=5000)
                        (run_dir / "page.html").write_text(await page.content(), encoding="utf-8")
                        (run_dir / "page.txt").write_text(
                            await page.locator("body").inner_text(timeout=5000), encoding="utf-8",
                        )
                    except Exception as exc:
                        report["capture_errors"].append(str(exc))
                if tracing_started:
                    try:
                        await context.tracing.stop(path=str(run_dir / "trace.zip"))
                    except Exception as exc:
                        report["capture_errors"].append(str(exc))
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception as exc:
                        report["capture_errors"].append(str(exc))
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return run_dir, report


async def _search_matter(page, matter_number: str, report: dict):
    report["stage"] = "locate_matter_field"
    search = page.locator(".iwps_body").filter(
        has=page.get_by_text("Go Directly to Matter", exact=True),
    )
    field = search.locator(".fm-textarea").filter(
        has=page.get_by_text("eg M01234", exact=True),
    ).locator(".text")
    await field.click()
    editor = field.and_(page.locator("[contenteditable=true]"))
    await editor.fill(matter_number)
    # FileMaker commits the active editor on Tab; clicking Search alone loses the value.
    await editor.press("Tab")

    report["stage"] = "submit_matter_search"
    # These are stable FileMaker layout-object classes, observed in two fresh sessions.
    # Object 258 is the direct-matter Search; two other Search buttons serve other forms.
    await search.locator(".fm_object_258").get_by_role("button", name="Search", exact=True).click()

    report["stage"] = "verify_matter"
    detail = page.locator(".iwps_body").filter(
        has=page.get_by_text(re.compile(r"^Matter No\s*Status$")),
    ).filter(has=page.get_by_role("button", name="Back to Search Results", exact=True))
    identifier = detail.locator(".fm_object_286.v-readonly .text").filter(has_text=re.compile(r"\S"))
    no_result = page.get_by_text("No records matched your search request. Click OK to return to Search page.", exact=True)
    await identifier.or_(no_result).wait_for(state="visible")
    if await no_result.is_visible():
        raise RuntimeError(f"Matter {matter_number} not found: No Records Found")
    observed = (await identifier.inner_text()).strip()
    report["observed_matter_number"] = observed
    if observed != matter_number:
        raise RuntimeError(f"Matter identifier mismatch: requested {matter_number}, observed {observed!r}")
    report["verified_matter_number"] = observed
    return detail


async def _extract_matter_details(detail, report: dict, timeout_ms: int) -> None:
    from playwright.async_api import expect

    report["stage"] = "extract_metadata"
    # shortcut: UARB layout classes are site-specific; recheck them after layout changes.
    title = detail.locator(".csFM-4BAC3292-04BC-FC44-A278-B61F356EAB45 .text")
    await expect(title, "Matter title must be nonempty").to_have_text(
        re.compile(r"\S"), timeout=timeout_ms, use_inner_text=True,
    )
    metadata = {
        "matter_number": report["verified_matter_number"],
        "title": (await title.inner_text()).strip(),
    }
    for name, object_number in {
        "status": 289, "type": 287, "category": 298,
        "date_received": 292, "decision_date": 294, "outcome": 295,
    }.items():
        field = detail.locator(f".fm_object_{object_number}.v-readonly .text")
        await field.wait_for(state="visible")
        metadata[name] = (await field.inner_text()).strip() or None
    report["matter_metadata"] = metadata

    report["stage"] = "extract_category_counts"
    counts = {}
    for category in ("Exhibits", "Key Documents", "Other Documents", "Transcripts", "Recordings"):
        button = detail.locator(".fm_object_276").get_by_role(
            "button", name=re.compile(rf"^{re.escape(category)}(?:\s|$)"),
        )
        pattern = re.compile(rf"^{re.escape(category)}\s*-\s*([0-9]+)\s*$")
        await expect(button, f"Invalid or unavailable count for {category}").to_have_text(
            pattern, timeout=timeout_ms, use_inner_text=True,
        )
        label = await button.inner_text()
        match = pattern.fullmatch(label)
        if match is None:
            raise ValueError(f"Invalid count for {category}: {label!r}")
        counts[category] = int(match.group(1))
    report["category_counts"] = counts
    report["total_document_count"] = sum(counts.values())
    report["metadata_counts_complete"] = True


async def _download_one_other_document(page, detail, report: dict, run_dir: Path, timeout_ms: int) -> None:
    """Follow the observed category -> GO GET IT -> filename dialog workflow.

    The first row means the site's current display order, without changing its
    sort. This milestone deliberately retrieves one file from Other Documents.
    """
    from playwright.async_api import expect

    category = "Other Documents"
    count = report["category_counts"][category]
    report["stage"] = "open_category"
    if count == 0:
        raise ValueError("Other Documents has no documents to download")
    label = re.compile(rf"^{category}\s*-\s*{count}\s*$")
    await detail.locator(".fm_object_276").get_by_role("button", name=label).click()
    # The list is a new FileMaker layout. Confirm both its selected tab and count
    # before using rows; stale rows from another category must not be collected.
    await page.locator(".fm-selected").get_by_role("button", name=label).wait_for(state="visible")
    await expect(page.locator(".iwps_header .fm_object_213")).to_have_text(
        re.compile(rf"^Found Count:\s*{count}\s*$"), timeout=timeout_ms, use_inner_text=True,
    )

    report["stage"] = "select_document"
    action = page.get_by_role("button", name="GO GET IT", exact=True)
    grid = page.get_by_role("grid").filter(has=action)
    await expect(grid).to_have_count(1, timeout=timeout_ms)
    row = grid.get_by_role("row").filter(has=action).first
    number = row.locator(".fm_object_201 .text")
    title = row.locator(".fm_object_202 .text")
    await expect(number).to_have_text(re.compile(r"^[0-9]+\s*$"), timeout=timeout_ms, use_inner_text=True)
    await expect(title).to_have_text(re.compile(r"\S"), timeout=timeout_ms, use_inner_text=True)
    candidate = {
        "matter_number": report["verified_matter_number"],
        "document_category": category,
        "document_number": (await number.inner_text()).strip(),
        "title": (await title.inner_text()).strip(),
    }
    report["download_candidate"] = candidate

    report["stage"] = "prepare_download"
    await row.get_by_role("button", name="GO GET IT", exact=True).click()
    dialog = page.get_by_role("dialog", name="Download Files", exact=True)
    await dialog.wait_for(state="visible")
    filename_button = dialog.get_by_role("button").and_(page.locator(".fm-download-button"))
    await expect(filename_button).to_have_count(1, timeout=timeout_ms)
    await expect(filename_button).to_have_text(re.compile(r"\S"), timeout=timeout_ms, use_inner_text=True)
    candidate["displayed_filename"] = (await filename_button.inner_text()).strip()

    report["stage"] = "download_document"
    # GO GET IT prepares a dialog; only its filename button emits the download.
    async with page.expect_download(timeout=timeout_ms) as pending:
        await filename_button.click()
    download = await pending.value
    if not allowed_url(download.url):
        raise ValueError("Download URL is outside the permitted UARB origin")
    candidate["suggested_filename"] = download.suggested_filename
    destination = safe_download_path(run_dir / "downloads", download.suggested_filename)
    candidate["path"] = destination.relative_to(run_dir.resolve()).as_posix()
    # save_as waits for completion; persist before context closure deletes temps.
    await asyncio.wait_for(download.save_as(destination), timeout=timeout_ms / 1000)

    report["stage"] = "validate_download"
    evidence = validate_download(destination).to_dict()
    candidate["file_evidence"] = evidence
    if not evidence["format_validated"]:
        raise ValueError("Document persisted, but its file format is not validated")
    report["downloaded_documents"].append(dict(candidate))
    report["downloaded_document_count"] = len(report["downloaded_documents"])
    report["phase1_milestone_complete"] = True
