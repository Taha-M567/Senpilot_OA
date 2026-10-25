"""Inspect UARB, extract matter details, and execute document collection requests."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from .archive import create_delivery_archive
from .collection import CollectionRequest
from .retrieval import collect_documents, download_row
from .catalog import build_catalog
from .llm import LLMConfig, rank_documents
from .selection import EmailRequest, RequestFailure, extract_matter_number, validate_selection


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
    collection_request: CollectionRequest | None = None,
    email_request: EmailRequest | None = None,
    llm_config: LLMConfig | None = None,
    dry_run: bool = False,
) -> tuple[Path, dict]:
    """Capture live DOM and controls, or a truthful failure report.

    Imports are lazy so a missing Playwright installation still produces a useful
    diagnostic. Only explicitly allowed UARB network requests are permitted.
    """
    if matter_number is None and isinstance(collection_request, CollectionRequest):
        matter_number = collection_request.matter_number
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
        "document_outcomes": [],
        "archive": None,
        "collection_complete": False,
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
        if dry_run and email_request is None:
            raise ValueError("dry_run requires an email-text request")
        if email_request is not None:
            if (not isinstance(email_request, EmailRequest) or matter_number is not None
                    or collection_request is not None or download_one):
                raise ValueError("Conflicting or invalid email-text request")
            matter_number = extract_matter_number(email_request.subject, email_request.body_text)
            report["intended_request"] = {"matter_number": matter_number}
            report["purpose"] = "Rank email-requested documents and create a delivery ZIP"
            (run_dir / "request.json").write_text(json.dumps({
                "subject": email_request.subject, "body_text": email_request.body_text,
            }, indent=2, ensure_ascii=False), encoding="utf-8")
            report["stage"] = "configure_model"
            llm_config = llm_config or LLMConfig.from_env()
        if collection_request is not None:
            if not isinstance(collection_request, CollectionRequest):
                raise ValueError("collection_request must be a validated CollectionRequest")
            if download_one or matter_number != collection_request.matter_number:
                raise ValueError("Conflicting collection, matter, or one-file requests")
            report["collection_request"] = collection_request.to_dict()
            report["selected_documents"] = [d.to_dict() for d in collection_request.selected()]
            report["purpose"] = "Download supplied document priorities and create a delivery ZIP"
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
                    if email_request is not None:
                        report["stage"] = "catalog_documents"
                        catalog = await build_catalog(page, report, timeout_ms)
                        (run_dir / "catalog.json").write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
                        report["catalog"] = {k: v for k, v in catalog.items() if k != "documents"}
                        report["stage"] = "rank_documents"
                        selection = await rank_documents(email_request, catalog, llm_config, run_dir, report)
                        (run_dir / "selection.json").write_text(json.dumps(selection, indent=2, ensure_ascii=False), encoding="utf-8")
                        report["stage"] = "validate_selection"
                        try:
                            collection_request = validate_selection(selection, catalog)
                        except (TypeError, ValueError, KeyError) as exc:
                            raise RequestFailure("invalid_selection", str(exc)) from exc
                        report["selection_outcome"] = selection["status"]
                        report["selection_message"] = (
                            "This request ended because more detail is needed. Submit a new detailed request from scratch."
                            if selection["status"] == "insufficient_detail" else selection["message"]
                        )
                        if collection_request is None:
                            report["status"] = selection["status"]
                        else:
                            report["collection_request"] = {
                                "matter_number": collection_request.matter_number,
                                "limit": collection_request.limit,
                                "documents": [d.to_dict() for d in collection_request.documents],
                            }
                            (run_dir / "collection-request.json").write_text(json.dumps(report["collection_request"], indent=2), encoding="utf-8")
                            if dry_run:
                                report["status"] = "selection_ready"
                            else:
                                report["selected_documents"] = [d.to_dict() for d in collection_request.selected()]
                    if download_one:
                        await _download_one_other_document(page, detail, report, run_dir, timeout_ms)
                    elif collection_request is not None and not dry_run:
                        await collect_documents(page, collection_request, report, run_dir, timeout_ms, allowed_url)
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
                if email_request is None or (collection_request is not None and not dry_run):
                    report["status"] = (
                        "collection_pending_archive" if collection_request is not None else
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
        report["status"] = exc.status if isinstance(exc, RequestFailure) else "failed"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if isinstance(collection_request, CollectionRequest) and "selected_documents" in report:
            _finalize_collection(run_dir, report)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return run_dir, report


def _finalize_collection(run_dir: Path, report: dict) -> None:
    """Package saved successes even when a later browser operation failed."""
    selected = report["selected_documents"]
    for document in selected[len(report["document_outcomes"]):]:
        report["document_outcomes"].append({"document": document, "status": "not_attempted",
                                            "reason": "Workflow stopped before this selection could be attempted"})
    if report["downloaded_documents"]:
        complete = (len(report["document_outcomes"]) == len(selected)
                    and all(d["status"] == "downloaded" for d in report["document_outcomes"])
                    and report["status"] != "failed")
        report["stage"] = "create_archive"
        try:
            report["archive"] = create_delivery_archive(run_dir, report["downloaded_documents"], {
                "schema_version": 1,
                "status": "complete" if complete else "partial",
                "collection_request": report["collection_request"],
                "selected_documents": selected,
                "matter_metadata": report["matter_metadata"],
                "category_counts": report["category_counts"],
                "total_document_count": report["total_document_count"],
                "downloaded_document_count": report["downloaded_document_count"],
                "document_outcomes": report["document_outcomes"],
                "workflow_error": report.get("error"),
            })
            report["collection_complete"] = complete
            report["status"] = "collection_complete" if complete else "collection_partial"
        except Exception as exc:
            if report.get("error"):
                report["workflow_error"] = report["error"]
            report["status"] = "failed"
            report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    elif report["status"] != "failed":
        report["stage"] = "collect_documents"
        if not selected:
            report["status"] = "collection_empty"
        else:
            report["status"] = "failed"
            report["error"] = {"type": "CollectionError", "message": "No selected document was successfully downloaded; see document_outcomes"}


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

    await download_row(page, row, candidate, run_dir, report, timeout_ms, allowed_url)
    report["downloaded_documents"].append(dict(candidate))
    report["downloaded_document_count"] = len(report["downloaded_documents"])
    report["phase1_milestone_complete"] = True
