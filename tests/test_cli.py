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
