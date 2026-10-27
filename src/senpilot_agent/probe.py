"""Inspect UARB or retrieve verified matter details, documents, and ZIP evidence."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from .downloads import CATEGORIES, retrieve_documents


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
    matter_number: str | None = None, category: str | None = None, limit: int = 10,
    inventory_categories: list[str] | None = None,
    selected_records: list[dict] | None = None, download_directory: Path | None = None,
    restart_on_download_timeout: bool = False, download_callback=None,
) -> tuple[Path, dict]:
    """Inspect live controls and save diagnostics, or a truthful failure report.

    Imports are lazy so a missing Playwright installation still produces a useful
    diagnostic. Only explicitly allowed UARB network requests are permitted.
    """
    if matter_number is not None and re.fullmatch(r'M[0-9]{5}', matter_number) is None:
        raise ValueError('Matter number must be M followed by five ASCII digits')
    if category is not None and (category not in CATEGORIES or matter_number is None):
        raise ValueError('A supported category and matter number are required for downloads')
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('Download limit must be between 1 and 10')
    if inventory_categories is not None and (
        matter_number is None or category is not None or not inventory_categories
        or len(set(inventory_categories)) != len(inventory_categories)
        or any(value not in CATEGORIES for value in inventory_categories)
    ):
        raise ValueError('Inventory requires a matter and distinct supported categories, without downloads')
    if selected_records is not None:
        from .downloads import validate_selected_records
        validate_selected_records(selected_records)
        if matter_number is None or category is not None or inventory_categories is not None:
            raise ValueError('Selected downloads require a matter, without category downloads or inventory mode')
    if download_directory is not None and selected_records is None:
        raise ValueError('A download directory requires selected records')
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
        "status": "starting",
        "stage": "import_playwright",
        "blocked_origins": [],
        "failed_requests": [],
        "capture_errors": [],
        "downloads": [],
        "download_errors": [],
        "downloaded_document_count": 0,
        "archive": None,
        "retrieval_complete": False,
    }
    if category is not None:
        report['intended_request'].update(category=category, limit=limit)
        report['purpose'] = 'Retrieve category documents and create a validated ZIP'
    if inventory_categories is not None:
        report['intended_request']['inventory_categories'] = inventory_categories
        report['purpose'] = 'Collect complete document title/date inventory without opening documents'
    if selected_records is not None:
        report['intended_request']['selected_records'] = selected_records
        report['purpose'] = 'Download exact shortlisted document identities'
        report['download_base'] = str((download_directory or run_dir).resolve())
    browser = context = page = None
    tracing_started = False
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            try:
                report["stage"] = "launch_browser"
                browser = await playwright.chromium.launch(
                    headless=not headed, channel=channel, timeout=max(timeout_ms, 10_000),
                )
                context = await browser.new_context(
                    accept_downloads=inventory_categories is None, viewport={"width": 1440, "height": 1000},
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

                page.on("requestfailed", request_failed)
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
                    if category is not None:
                        await retrieve_documents(page, detail, run_dir, report, category, limit, timeout_ms)
                    if inventory_categories is not None:
                        from .inventory import collect_inventory
                        await collect_inventory(page, report, inventory_categories, timeout_ms)
                    if selected_records is not None:
                        from .downloads import retrieve_selected_documents
                        await retrieve_selected_documents(page, download_directory or run_dir, report,
                            selected_records, timeout_ms, restart_on_timeout=restart_on_download_timeout,
                            download_callback=download_callback)
                if inspect_seconds:
                    # Explicit human inspection window, not a readiness heuristic.
                    import asyncio
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
                    report["frames"].append({"index": index, "controls": controls})
                report["status"] = "matter_found" if matter_number is not None else "inspection_captured"
                if category is not None:
                    report['status'] = ('documents_ready' if report['retrieval_complete']
                                        else 'partial_download') if report['requested_document_count'] else 'no_documents'
                    report['stage'] = 'retrieval_complete'
                else:
                    report["stage"] = "extract_category_counts" if matter_number is not None else "inspect_dom"
                if inventory_categories is not None:
                    report.update(status='inventory_ready', stage='inventory_complete')
                if selected_records is not None:
                    report.update(status='documents_ready' if report['retrieval_complete'] else 'partial_download',
                                  stage='selected_retrieval_complete')
            finally:
                if page is not None:
                    try:
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
        from .inventory import InventoryTooLarge
        if isinstance(exc, InventoryTooLarge):
            report.update(status='clarification', clarification_question=str(exc))
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
