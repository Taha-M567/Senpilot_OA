import asyncio

import pytest

from senpilot_agent.probe import inspect_site


def candidate(index, category='Other Documents', candidate_id='C0001'):
    return dict(candidate_id=candidate_id, document_number=f'H-{index + 1}' if category == 'Exhibits' else str(1000 + index),
                title=f'Document {index + 1}', date=f'01/{index + 1:02d}/2026', categories=[category])


def test_exact_selections_traverse_pages_and_categories_once_in_rank_order(document_site, tmp_path):
    document_site(many=True)
    records = [candidate(12), candidate(1, 'Exhibits', 'C0002'), candidate(0, 'Key Documents', 'C0003')]
    records[-1]['categories'].append('Other Documents')
    run, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205', selected_records=records, timeout_ms=2000))
    assert report['status'] == 'documents_ready', report.get('error') or report['download_errors']
    assert report['retrieval_complete']
    assert [item['candidate_id'] for item in report['downloads']] == [item['candidate_id'] for item in records]
    for item, index in zip(report['downloads'], [12, 1, 0]):
        assert f'fixture {index}'.encode() in (run / item['relative_path']).read_bytes()
    assert len(list((run / 'downloads').iterdir())) == 3
    assert report['archive'] is None


@pytest.mark.parametrize('mode', ['changed_title', 'changed_date', 'ambiguous_record'])
def test_missing_changed_or_ambiguous_records_never_download_a_substitute(document_site, tmp_path, mode):
    document_site(mode)
    run, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205', selected_records=[candidate(0)], timeout_ms=1000))
    assert report['status'] == 'partial_download'
    assert report['downloads'] == []
    assert len(report['download_errors']) == 1
    assert not list(run.rglob('downloads'))


def test_missing_record_and_failed_file_do_not_stop_other_selected_downloads(document_site, tmp_path):
    document_site('partial')
    records = [candidate(1), candidate(0, candidate_id='C0002'), candidate(99, candidate_id='C0003')]
    _, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205', selected_records=records, timeout_ms=1000))
    assert report['status'] == 'partial_download'
    assert [item['candidate_id'] for item in report['downloads']] == ['C0002']
    assert [item['candidate_id'] for item in report['download_errors']] == ['C0001', 'C0003']


def test_duplicate_candidates_or_identities_are_rejected_before_browser(tmp_path):
    for records in ([candidate(0), candidate(1)], [candidate(0), candidate(0, candidate_id='C0002')]):
        with pytest.raises(ValueError, match='duplicate'):
            asyncio.run(inspect_site(tmp_path, matter_number='M12205', selected_records=records))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('mode', ['same_tab_no_reset', 'active_tab_reload'])
def test_selected_tab_that_keeps_scroll_position_is_reset_before_exact_download(document_site, tmp_path, mode):
    document_site(mode, many=True)
    run, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205',
        selected_records=[candidate(0)], timeout_ms=2000))
    assert report['status'] == 'documents_ready', report.get('error') or report['download_errors']
    assert b'fixture 0' in (run / report['downloads'][0]['relative_path']).read_bytes()


def test_stalled_selected_download_closes_session_before_attempting_more_files(document_site, tmp_path):
    document_site('download_timeout')
    records = [candidate(1), candidate(0, candidate_id='C0002')]
    _, report = asyncio.run(inspect_site(tmp_path, matter_number='M12205', selected_records=records,
        restart_on_download_timeout=True, timeout_ms=1000))
    assert report['status'] == 'partial_download'
    assert report['downloads'] == []
    assert [x['type'] for x in report['download_errors']] == ['TimeoutError', 'SessionRestartRequired']
    assert all(x['retryable'] for x in report['download_errors'])
    document_site('success')
    _, recovered = asyncio.run(inspect_site(tmp_path, matter_number='M12205', selected_records=records,
        restart_on_download_timeout=True, timeout_ms=2000))
    assert recovered['status'] == 'documents_ready'
    assert len(recovered['downloads']) == 2
