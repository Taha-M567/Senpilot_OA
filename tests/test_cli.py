import sys

import pytest

from senpilot_agent import cli


@pytest.mark.parametrize('value', [
    '', 'M1220', 'M122055', '12205', 'X12205', 'M12 05', 'M１２２０５',
    'M١٢٢٠٥', 'M12205 extra', 'M12205\nM54321',
])
def test_invalid_matter_is_an_argument_error_before_browser_launch(value, monkeypatch, capsys):
    def unexpected_launch(*args, **kwargs):
        pytest.fail('Invalid arguments must not launch the browser')

    monkeypatch.setattr(cli, 'inspect_site', unexpected_launch)
    monkeypatch.setattr(sys, 'argv', ['senpilot-probe', '--matter', value])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert 'five ASCII digits' in capsys.readouterr().err


@pytest.mark.parametrize(('value', 'expected'), [(' m12205 ', 'M12205'), ('M00000', 'M00000')])
def test_matter_normalization(value, expected):
    assert cli.matter_argument(value) == expected


@pytest.mark.parametrize(('argument', 'status', 'exit_code'), [
    (' m12205 ', 'matter_found', 0),
    ('M12205', 'failed', 1),
    (None, 'inspection_captured', 0),
])
def test_cli_passes_optional_matter_and_reports_workflow_status(
    argument, status, exit_code, tmp_path, monkeypatch, capsys,
):
    async def inspect(output, **kwargs):
        assert kwargs['matter_number'] == ('M12205' if argument else None)
        return tmp_path, {
            'status': status, 'stage': 'verify_matter',
            'verified_matter_number': 'M12205' if status == 'matter_found' else None,
            'error': {'message': 'Matter M12205 not found'},
        }

    monkeypatch.setattr(cli, 'inspect_site', inspect)
    monkeypatch.setattr(sys, 'argv', ['senpilot-probe'] + (['--matter', argument] if argument else []))
    assert cli.main() == exit_code
    output = capsys.readouterr().out
    assert ('Verified matter: M12205' in output) == (status == 'matter_found')
    assert 'report.json' in output


def test_cli_prints_metadata_counts_and_total(tmp_path, monkeypatch, capsys):
    async def inspect(output, **kwargs):
        return tmp_path, {
            'status': 'matter_found', 'stage': 'extract_category_counts',
            'verified_matter_number': 'M12205', 'metadata_counts_complete': True,
            'matter_metadata': {'title': 'Fixture matter', 'decision_date': None},
            'category_counts': {'Exhibits': 2, 'Key Documents': 4, 'Other Documents': 7,
                                'Transcripts': 0, 'Recordings': 0},
            'total_document_count': 13,
        }

    monkeypatch.setattr(cli, 'inspect_site', inspect)
    monkeypatch.setattr(sys, 'argv', ['senpilot-probe', '--matter', 'M12205'])
    assert cli.main() == 0
    output = capsys.readouterr().out
    for text in ('Title: Fixture matter', 'Decision date: (not provided)', 'Exhibits: 2',
                 'Key Documents: 4', 'Other Documents: 7', 'Transcripts: 0', 'Recordings: 0',
                 'Total documents: 13', 'download remains unverified'):
        assert text in output


def test_download_flag_requires_matter_before_launch(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['senpilot-probe', '--download-one'])
    monkeypatch.setattr(cli, 'inspect_site', lambda *a, **k: pytest.fail('Unexpected browser launch'))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert '--download-one requires --matter' in capsys.readouterr().err


def test_cli_passes_download_request_and_prints_saved_file(tmp_path, monkeypatch, capsys):
    async def inspect(output, **kwargs):
        assert kwargs['matter_number'] == 'M12205'
        assert kwargs['download_one'] is True
        return tmp_path, {
            'status': 'document_downloaded', 'stage': 'validate_download',
            'verified_matter_number': 'M12205', 'metadata_counts_complete': True,
            'matter_metadata': {}, 'category_counts': {}, 'total_document_count': 7,
            'phase1_milestone_complete': True, 'downloaded_document_count': 1,
            'downloaded_documents': [{'path': 'downloads/fixture.pdf'}],
        }

    monkeypatch.setattr(cli, 'inspect_site', inspect)
    monkeypatch.setattr(sys, 'argv', ['senpilot-probe', '--matter', 'M12205', '--download-one'])
    assert cli.main() == 0
    output = capsys.readouterr().out
    assert 'Downloaded and validated: 1 Other Documents file(s)' in output
    assert str(tmp_path / 'downloads/fixture.pdf') in output
    assert 'remains unverified' not in output


@pytest.mark.parametrize('contents', [
    '{', '{}', '{"matter_number":"M12205","limit":11,"documents":[]}',
    '{"matter_number":"M12205","documents":[{"category":"Other Documents","document_number":"123","priority":true}]}',
])
def test_invalid_collection_json_fails_before_browser_launch(contents, tmp_path, monkeypatch, capsys):
    path = tmp_path / 'request.json'
    path.write_text(contents)
    monkeypatch.setattr(sys, 'argv', ['senpilot-probe','--collection-request',str(path)])
    monkeypatch.setattr(cli, 'inspect_site', lambda *a, **k: pytest.fail('Unexpected browser launch'))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert 'Invalid collection request' in capsys.readouterr().err


@pytest.mark.parametrize(('status','exit_code'), [('collection_complete',0),('collection_partial',1),('collection_empty',0)])
def test_collection_cli_passes_priorities_and_reports_archive(status, exit_code, tmp_path, monkeypatch, capsys):
    path = tmp_path / 'request.json'
    path.write_text('{"matter_number":"M12205","documents":[{"category":"Other Documents","document_number":"123","priority":2}]}')

    async def inspect(output, **kwargs):
        request = kwargs['collection_request']
        assert request.matter_number == 'M12205'
        assert request.selected()[0].document_number == '123'
        assert request.selected()[0].priority == 2
        return tmp_path, {
            'status':status, 'stage':'create_archive', 'metadata_counts_complete':True,
            'matter_metadata':{}, 'category_counts':{}, 'total_document_count':7,
            'downloaded_document_count': 0 if status=='collection_empty' else 1,
            'archive': None if status=='collection_empty' else {'path':'delivery.zip'},
        }

    monkeypatch.setattr(cli, 'inspect_site', inspect)
    monkeypatch.setattr(sys, 'argv', ['senpilot-probe','--collection-request',str(path)])
    assert cli.main() == exit_code
    text = capsys.readouterr().out
    assert ('Delivery ZIP:' in text) == (status != 'collection_empty')
    assert 'download remains unverified' not in text


def test_collection_cli_rejects_conflicting_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['senpilot-probe','--matter','M12205','--collection-request','unused.json'])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize('contents',['{}','{"subject":"a","body_text":null}',
    '{"subject":"a","body_text":"M12205","thread_id":"x"}',
    '{"subject":"a","subject":"b","body_text":"M12205"}'])
def test_invalid_email_file_fails_before_workflow(contents,tmp_path,monkeypatch):
    path=tmp_path/'email.json';path.write_text(contents)
    monkeypatch.setattr(sys,'argv',['senpilot-probe','--email-request',str(path)])
    monkeypatch.setattr(cli,'inspect_site',lambda *a,**k:pytest.fail('Invalid email launched workflow'))
    with pytest.raises(SystemExit) as exc: cli.main()
    assert exc.value.code==2


@pytest.mark.parametrize('args',[['--dry-run'],['--matter','M12205','--email-request','unused'],
    ['--collection-request','unused','--email-request','unused'],['--email-request','unused','--download-one']])
def test_email_cli_rejects_conflicting_or_missing_modes(monkeypatch,args):
    monkeypatch.setattr(sys,'argv',['senpilot-probe',*args])
    with pytest.raises(SystemExit) as exc: cli.main()
    assert exc.value.code==2


@pytest.mark.parametrize('status,exit_code',[('selection_ready',0),('collection_complete',0),
    ('no_matches',1),('insufficient_detail',1),('ranking_failed',1),('ranking_capacity_exceeded',1)])
def test_email_cli_passes_text_and_reports_terminal_outcomes(tmp_path,monkeypatch,capsys,status,exit_code):
    path=tmp_path/'email.json';path.write_text('{"subject":"M12205","body_text":"Only Board Orders"}')
    async def inspect(output,**kwargs):
        assert kwargs['email_request'].body_text=='Only Board Orders'
        assert kwargs['dry_run'] is True
        return tmp_path,{'status':status,'stage':'rank_documents',
            'selection_message':'Submit a new request.' if status=='insufficient_detail' else None,
            'error':{'message':'Synthetic failure'} if status in {'ranking_failed','ranking_capacity_exceeded'} else {}}
    monkeypatch.setattr(cli,'inspect_site',inspect)
    monkeypatch.setattr(sys,'argv',['senpilot-probe','--email-request',str(path),'--dry-run'])
    assert cli.main()==exit_code
    output=capsys.readouterr().out
    assert output.count('Submit a new request.') <= 1
