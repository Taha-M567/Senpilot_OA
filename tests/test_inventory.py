import asyncio

import pytest
from playwright.async_api import Locator, Page

from senpilot_agent import downloads, files, inventory
from senpilot_agent.downloads import CATEGORIES
from senpilot_agent.probe import inspect_site


@pytest.fixture
def forbid_documents(monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail('Planning must never open or download document contents')

    def no_archive(*args, **kwargs):
        pytest.fail('Planning must never create a document ZIP')

    original_click = Locator.click

    async def checked_click(locator, *args, **kwargs):
        assert (await locator.inner_text()).strip().lower() not in {'preview', 'go get it'}
        return await original_click(locator, *args, **kwargs)

    monkeypatch.setattr(Locator, 'click', checked_click)
    monkeypatch.setattr(downloads, '_download_row', unexpected)
    monkeypatch.setattr(Page, 'expect_download', no_archive)
    monkeypatch.setattr(files, 'create_archive', no_archive)
    monkeypatch.setattr(downloads, 'create_archive', no_archive)


def test_complete_inventory_switches_categories_and_deduplicates(
    document_site, forbid_documents, tmp_path,
):
    document_site(many=True, media=True)
    directory, report = asyncio.run(inspect_site(
        tmp_path, matter_number='M12205', inventory_categories=list(CATEGORIES), timeout_ms=2000,
    ))
    assert report['status'] == 'inventory_ready', report.get('error')
    assert report['inventory_complete']
    assert report['inventory_category_counts'] == report['category_counts']
    candidates = report['candidate_inventory']
    assert len(candidates) == 15
    assert len({item['candidate_id'] for item in candidates}) == 15
    shared = next(item for item in candidates if item['document_number'] == '1000')
    assert shared['categories'] == ['Key Documents', 'Other Documents', 'Transcripts', 'Recordings']
    assert shared['list_order'] == dict.fromkeys(shared['categories'], 0)
    assert shared['date'] == '01/01/2026'
    other = [item for item in candidates if 'Other Documents' in item['categories']]
    assert [item['document_number'] for item in other] == [str(1000 + i) for i in range(13)]
    assert [item['list_order']['Other Documents'] for item in other] == list(range(13))
    assert report['downloads'] == [] and report['archive'] is None
    assert not list(directory.rglob('downloads'))
    assert [path.name for path in directory.rglob('*.zip')] == ['trace.zip']
    assert 'Download Files' not in (directory / 'page.txt').read_text()


@pytest.mark.parametrize('categories', [['Exhibits'], ['Transcripts', 'Recordings']])
def test_inventory_restricts_categories_and_skips_empty_tabs(document_site, tmp_path, categories):
    document_site()
    _, report = asyncio.run(inspect_site(
        tmp_path, matter_number='M12205', inventory_categories=categories, timeout_ms=2000,
    ))
    assert report['status'] == 'inventory_ready', report.get('error')
    assert set(report['inventory_category_counts']) == set(categories)
    assert len(report['candidate_inventory']) == (2 if categories == ['Exhibits'] else 0)
    assert all(item['categories'] == categories for item in report['candidate_inventory'])


@pytest.mark.parametrize('mode', ['early_end', 'duplicate_rows', 'stuck_grid', 'missing_date'])
def test_incomplete_inventory_is_a_failure(document_site, tmp_path, mode):
    document_site(mode)
    _, report = asyncio.run(inspect_site(
        tmp_path, matter_number='M12205', inventory_categories=['Other Documents'], timeout_ms=1000,
    ))
    assert report['status'] == 'failed'
    assert not report['inventory_complete']
    assert report['error']['message']


def test_blank_displayed_date_is_preserved_as_null(document_site, tmp_path):
    document_site('blank_date')
    _, report = asyncio.run(inspect_site(
        tmp_path, matter_number='M12205', inventory_categories=['Exhibits'], timeout_ms=2000,
    ))
    assert report['status'] == 'inventory_ready', report.get('error')
    assert all(item['date'] is None for item in report['candidate_inventory'])


def test_category_count_can_arrive_before_virtual_row_fields(document_site, tmp_path):
    document_site('delayed_fields', many=True)
    _, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205',
        inventory_categories=['Key Documents', 'Other Documents'], timeout_ms=2000))
    assert report['status'] == 'inventory_ready', report.get('error')
    assert report['inventory_complete']
    assert report['inventory_category_counts'] == {'Key Documents': 4, 'Other Documents': 13}


def test_oversized_category_requests_narrower_search(document_site, tmp_path):
    document_site(oversized=True)
    _, report = asyncio.run(inspect_site(
        tmp_path, matter_number='M12205', inventory_categories=['Other Documents'], timeout_ms=2000,
    ))
    assert report['status'] == 'clarification'
    assert 'narrow' in report['clarification_question']
    assert report['candidate_inventory'] == []


def test_unique_candidate_cap_applies_across_categories(document_site, tmp_path, monkeypatch):
    document_site()
    monkeypatch.setattr(inventory, 'MAX_CANDIDATES', 7)
    _, report = asyncio.run(inspect_site(
        tmp_path, matter_number='M12205', inventory_categories=['Exhibits', 'Other Documents'], timeout_ms=2000,
    ))
    assert report['status'] == 'clarification'
    assert len(report['candidate_inventory']) == 7
    assert not report['inventory_complete']


@pytest.mark.parametrize('categories', [[], ['Unknown'], ['Exhibits', 'Exhibits']])
def test_invalid_inventory_categories_rejected_before_launch(tmp_path, categories):
    with pytest.raises(ValueError):
        asyncio.run(inspect_site(tmp_path, matter_number='M12205', inventory_categories=categories))
    assert not list(tmp_path.iterdir())
