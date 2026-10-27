"""Retrieve UARB grid documents through the observed FileMaker download dialog."""

import asyncio
from pathlib import Path
import re

from .files import create_archive, safe_download_path, validate_download


CATEGORIES = ('Exhibits', 'Key Documents', 'Other Documents', 'Transcripts', 'Recordings')
IDENTIFIER = '.fm-textarea.hand-cursor .text'


def document_key(item: dict) -> tuple:
    return item['document_number'], item['title'], item['date'] or None


def validate_selected_records(records: list[dict]) -> None:
    if not isinstance(records, list) or not 1 <= len(records) <= 10:
        raise ValueError('Select between one and ten candidate records')
    for item in records:
        if (not isinstance(item, dict)
                or not isinstance(item.get('candidate_id'), str)
                or re.fullmatch(r'C[0-9]{4}', item['candidate_id']) is None
                or any(not isinstance(item.get(name), str) or not item[name].strip()
                       for name in ('document_number', 'title'))
                or 'date' not in item or (item['date'] is not None and not isinstance(item['date'], str))
                or not isinstance(item.get('categories'), list) or not item['categories']
                or any(value not in CATEGORIES for value in item['categories'])):
            raise ValueError('Selected records require valid candidate IDs, identity fields, and categories')
    if (len({item['candidate_id'] for item in records}) != len(records)
            or len({document_key(item) for item in records}) != len(records)):
        raise ValueError('Selected records contain duplicate candidates or document identities')


async def retrieve_selected_documents(page, run_dir: Path, report: dict,
                                      records: list[dict], timeout_ms: int) -> None:
    """Recheck complete category lists before downloading exact selected identities."""
    from .inventory import collect_inventory, grid_pages

    report['requested_document_count'] = len(records)
    groups = {}
    for item in records:
        groups.setdefault(item['categories'][0], []).append(item)
    for category, selected in groups.items():
        pending = {item['candidate_id']: item for item in selected}
        try:
            report['stage'] = 'verify_selected_inventory'
            fresh = {'category_counts': report['category_counts']}
            await collect_inventory(page, fresh, [category], timeout_ms)
            available_keys = {document_key(item) for item in fresh['candidate_inventory']}
            for candidate_id, item in list(pending.items()):
                if document_key(item) not in available_keys:
                    report['download_errors'].append({**item, 'type': 'RecordChanged',
                        'message': 'Selected number/title/date is missing or changed in its category'})
                    del pending[candidate_id]
            if not pending:
                continue
            report['stage'] = 'find_selected_documents'
            grid = await select_category(page, page.locator('.fm_object_276, .fm_object_195'),
                                         category, report['category_counts'][category], timeout_ms)
            async for current in grid_pages(grid, timeout_ms):
                visible = {document_key(item) for item in current}
                for candidate_id, item in list(pending.items()):
                    if document_key(item) not in visible:
                        continue
                    row = grid.locator('tr.v-grid-row-has-data')
                    for selector, value in ((IDENTIFIER, item['document_number']),
                                            ('.fm_object_202 .text', item['title']),
                                            ('.fm_object_203 .text', item['date'] or '')):
                        row = row.filter(has=page.locator(selector).filter(
                            has_text=re.compile(rf'^\s*{re.escape(value)}\s*$')))
                    report['stage'] = 'download_selected_document'
                    try:
                        await row.scroll_into_view_if_needed()
                        if await row.count() != 1:
                            raise ValueError('Selected record became missing or ambiguous before download')
                        evidence = await _download_row(page, row, run_dir, timeout_ms)
                        report['downloads'].append({**item, **evidence, 'download_category': category})
                        report['downloaded_document_count'] = len(report['downloads'])
                    except Exception as exc:
                        report['download_errors'].append({**item, 'type': type(exc).__name__, 'message': str(exc)})
                        dialog = page.get_by_role('dialog', name='Download Files', exact=True)
                        if await dialog.is_visible():
                            await dialog.get_by_role('button', name='Close', exact=True).click()
                            await dialog.wait_for(state='hidden')
                    del pending[candidate_id]
                if not pending:
                    break
        except Exception as exc:
            for item in pending.values():
                report['download_errors'].append({**item, 'type': type(exc).__name__, 'message': str(exc)})
            pending.clear()
        for item in pending.values():
            report['download_errors'].append({**item, 'type': 'RecordChanged',
                'message': 'Selected record disappeared during retrieval'})
    order = {item['candidate_id']: index for index, item in enumerate(records)}
    report['downloads'].sort(key=lambda item: order[item['candidate_id']])
    report['download_errors'].sort(key=lambda item: order[item['candidate_id']])
    report['retrieval_complete'] = len(report['downloads']) == len(records) and not report['download_errors']


async def retrieve_documents(page, detail, run_dir: Path, report: dict,
                             category: str, limit: int, timeout_ms: int) -> None:
    from playwright.async_api import expect

    report['stage'] = 'select_category'
    available = report['category_counts'][category]
    target = min(limit, available)
    report['requested_document_count'] = target
    if target == 0:
        report['retrieval_complete'] = True
        return
    grid = await select_category(page, detail.locator('.fm_object_276'), category, available, timeout_ms)
    rows = grid.locator('tr.v-grid-row-has-data')
    identifiers = rows.locator(IDENTIFIER)
    await identifiers.first.wait_for(state='visible')
    attempted = set()
    paths = []
    try:
        while len(attempted) < target:
            report['stage'] = 'read_document_list'
            current = await identifiers.all_inner_texts()
            document_ids = [value.strip() for value in current]
            if any(not value for value in document_ids):
                raise ValueError('Document list contains a missing or invalid identifier')
            if len(set(document_ids)) != len(document_ids):
                raise ValueError('Document list contains duplicate identifiers')
            document_id = next((value for value in document_ids if value not in attempted), None)
            if document_id is None:
                scroller = grid.locator('.v-grid-scroller-vertical')
                moved = await scroller.evaluate('''el => {
                    const before = el.scrollTop;
                    el.scrollTop += Math.max(1, el.clientHeight * 0.8);
                    return el.scrollTop > before;
                }''')
                if not moved:
                    raise RuntimeError(f'Document list ended before {target} distinct documents')
                await expect(identifiers).not_to_have_text(current, timeout=timeout_ms)
                continue
            row = rows.filter(has=page.locator(IDENTIFIER).filter(
                has_text=re.compile(rf'^\s*{re.escape(document_id)}\s*$'),
            ))
            await row.scroll_into_view_if_needed()
            await expect(row.locator(IDENTIFIER)).to_have_text(
                document_id, timeout=timeout_ms,
            )
            title = (await row.locator('.fm_object_202 .text').inner_text()).strip()
            attempted.add(document_id)
            report['stage'] = 'download_document'
            try:
                evidence = await _download_row(page, row, run_dir, timeout_ms)
            except Exception as exc:
                report['download_errors'].append({
                    'document_id': document_id, 'title': title,
                    'type': type(exc).__name__, 'message': str(exc),
                })
                # A failed dialog must close before another row can be attempted.
                dialog = page.get_by_role('dialog', name='Download Files', exact=True)
                if await dialog.is_visible():
                    await dialog.get_by_role('button', name='Close', exact=True).click()
                    await dialog.wait_for(state='hidden')
                continue
            report['downloads'].append({'document_id': document_id, 'title': title, **evidence})
            report['downloaded_document_count'] = len(report['downloads'])
            paths.append(run_dir / evidence['relative_path'])
    finally:
        if paths:
            previous_stage = report['stage']
            report['stage'] = 'create_archive'
            name = f"{report['verified_matter_number']}_{category.replace(' ', '_')}.zip"
            archive = create_archive(paths, run_dir / name)
            report['archive'] = {**archive.to_dict(), 'document_count': len(paths)}
            report['stage'] = previous_stage
    report['retrieval_complete'] = len(paths) == target and not report['download_errors']


async def select_category(page, controls, category: str, available: int, timeout_ms: int):
    """Open a verified category and wait for its selected tab and grid count."""
    from playwright.async_api import expect

    selected = page.locator('.fm-selected').get_by_role(
        'button', name=re.compile(rf'^{re.escape(category)}\s*-'),
    )
    # Reclicking an active FileMaker tab starts another asynchronous list reload.
    if not await selected.is_visible():
        await controls.get_by_role(
            'button', name=re.compile(rf'^{re.escape(category)}\s*-'),
        ).click()
    # shortcut: these FileMaker layout classes were observed live; recheck after site changes.
    await expect(selected).to_be_visible(timeout=timeout_ms)
    grid = page.locator('.iwps_fm-list-container .v-grid')
    await expect(grid.get_by_role('grid')).to_have_attribute(
        'aria-rowcount', str(available), timeout=timeout_ms,
    )
    rows = grid.locator('tr.v-grid-row-has-data')
    current = await rows.all_inner_texts()
    moved = await grid.locator('.v-grid-scroller-vertical').evaluate('''el => {
        const before = el.scrollTop;
        el.scrollTop = 0;
        return before > 0;
    }''')
    if moved and len(current) < available:
        await expect(rows).not_to_have_text(current, use_inner_text=True, timeout=timeout_ms)
    return grid


async def _download_row(page, row, run_dir: Path, timeout_ms: int) -> dict:
    await row.get_by_role(
        'button', name=re.compile(r'^Go Get It$', re.IGNORECASE),
    ).click()
    dialog = page.get_by_role('dialog', name='Download Files', exact=True)
    await dialog.wait_for(state='visible')
    # Each observed row resolves to one file. Fail on ambiguous multi-file dialogs.
    button = dialog.locator('.fm-download-button')
    async with page.expect_download(timeout=timeout_ms) as pending:
        await button.click()
    download = await pending.value
    destination = safe_download_path(run_dir / 'downloads', download.suggested_filename)
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            await download.save_as(destination)
        evidence = validate_download(destination)
    except BaseException:
        await download.cancel()
        destination.unlink(missing_ok=True)
        raise
    await dialog.get_by_role('button', name='Close', exact=True).click()
    await dialog.wait_for(state='hidden')
    return {**evidence.to_dict(), 'suggested_filename': download.suggested_filename,
            'relative_path': destination.relative_to(run_dir.resolve()).as_posix()}
