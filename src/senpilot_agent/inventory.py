"""Read complete UARB title/date inventories without opening any documents."""

from .downloads import IDENTIFIER, select_category


MAX_CANDIDATES = 1000


class InventoryTooLarge(ValueError):
    pass


async def collect_inventory(page, report: dict, categories: list[str], timeout_ms: int) -> None:
    report.update(candidate_inventory=[], inventory_category_counts={}, inventory_complete=False)
    candidates = {}
    for category in categories:
        available = report['category_counts'][category]
        if available > MAX_CANDIDATES:
            raise InventoryTooLarge('Please narrow the categories to at most 1,000 candidate documents.')
        if available == 0:
            report['inventory_category_counts'][category] = 0
            continue
        report['stage'] = 'select_inventory_category'
        # The matter-detail bar is 276; the document-list bar is 195.
        controls = page.locator('.fm_object_276, .fm_object_195')
        grid = await select_category(page, controls, category, available, timeout_ms)
        seen = set()
        report['stage'] = 'read_inventory'
        async for current in grid_pages(grid, timeout_ms):
            keys = [(item['document_number'], item['title'], item['date'] or None) for item in current]
            for key in keys:
                if key in seen:
                    continue
                seen.add(key)
                if key not in candidates:
                    if len(candidates) == MAX_CANDIDATES:
                        raise InventoryTooLarge('Please narrow the categories to at most 1,000 candidate documents.')
                    item = {'candidate_id': f'C{len(candidates) + 1:04d}',
                            'document_number': key[0], 'title': key[1], 'date': key[2],
                            'categories': [], 'list_order': {}, 'original_order': len(candidates)}
                    candidates[key] = item
                    report['candidate_inventory'].append(item)
                item = candidates[key]
                item['categories'].append(category)
                item['list_order'][category] = len(seen) - 1
            if len(seen) > available:
                raise RuntimeError(f'Inventory count exceeds verified count for {category}')
            if len(seen) == available:
                break
        if len(seen) != available:
            raise RuntimeError(f'Incomplete inventory for {category}: read {len(seen)} of {available}')
        report['inventory_category_counts'][category] = len(seen)
    report['inventory_complete'] = True


async def grid_pages(grid, timeout_ms: int):
    """Read atomic page snapshots while traversing a recycling virtual grid."""
    from playwright.async_api import expect

    rows = grid.locator('tr.v-grid-row-has-data')
    await rows.first.wait_for(state='visible')
    seen_pages = set()
    while True:
        # A category's rowcount can arrive before its recycled row fields. Take
        # the snapshot in the readiness poll so a second DOM read cannot race it.
        snapshot = await grid.page.wait_for_function('''identifier => {
            const grids = document.querySelectorAll('.iwps_fm-list-container .v-grid');
            if (grids.length !== 1) return false;
            const rows = [...grids[0].querySelectorAll('tr.v-grid-row-has-data')];
            const selectors = [identifier, '.fm_object_202 .text', '.fm_object_203 .text'];
            if (!rows.length || !rows.every(row => selectors.every(selector =>
                    row.querySelectorAll(selector).length === 1))) return false;
            const records = rows.map(row => {
                const read = selector => row.querySelector(selector).innerText.trim();
                return {document_number: read(identifier), title: read(selectors[1]),
                        date: read(selectors[2]), text: row.innerText};
            });
            return records.every(row => row.document_number && row.title) ? records : false;
        }''', arg=IDENTIFIER, timeout=timeout_ms)
        try:
            current = await snapshot.json_value()
        finally:
            await snapshot.dispose()
        keys = tuple((item['document_number'], item['title'], item['date']) for item in current)
        if not keys or any(not key[0] or not key[1] for key in keys) or len(set(keys)) != len(keys):
            raise ValueError('Invalid or duplicate inventory rows')
        if keys in seen_pages:
            raise RuntimeError('Incomplete inventory: virtual grid repeated a page')
        seen_pages.add(keys)
        yield current
        scroller = grid.locator('.v-grid-scroller-vertical')
        moved = await scroller.evaluate('''el => {
            const before = el.scrollTop;
            el.scrollTop += Math.max(1, el.clientHeight * 0.8);
            return el.scrollTop > before;
        }''')
        if not moved:
            return
        await expect(rows).not_to_have_text([item['text'] for item in current],
                                           use_inner_text=True, timeout=timeout_ms)
