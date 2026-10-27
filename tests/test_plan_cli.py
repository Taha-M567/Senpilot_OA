import io
import json
import sys

import pytest

from senpilot_agent import email_flow, plan_cli


@pytest.mark.parametrize(('status', 'exit_code'), [('completed', 0), ('clarification', 2), ('failed', 1)])
def test_plan_cli_stdin_and_exit_codes(monkeypatch, tmp_path, capsys, status, exit_code):
    async def plan(email, output, **kwargs):
        assert email == 'Priority decisions and orders for M12205'
        assert kwargs == dict(limit=3, headed=True, channel='msedge', timeout_ms=2000, inspect_seconds=0)
        return tmp_path, dict(status=status, basis='Titles and metadata only; contents were not reviewed.',
                              clarification_question='Which matter?', error={'message': 'API failed'},
                              ranked_selections=[dict(document_number='123', title='Board Order',
                                  categories=['Key Documents'], date='01/02/2026', reason='Title indicates an order.')])

    monkeypatch.setattr(plan_cli, 'plan_email', plan)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('Priority decisions and orders for M12205'))
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--email-file', '-', '--limit', '3', '--headed',
                                    '--channel', 'msedge', '--timeout-ms', '2000'])
    assert plan_cli.main() == exit_code
    output = capsys.readouterr().out
    assert 'plan.json' in output
    assert 'contents were not reviewed' in output
    assert {'completed': '1. 123 | Board Order | Key Documents | 01/02/2026',
            'clarification': 'Which matter?', 'failed': 'API failed'}[status] in output


def test_plan_cli_reads_utf8_file_and_prints_no_matches(monkeypatch, tmp_path, capsys):
    source = tmp_path / 'email.txt'
    source.write_text('\ufeffM12205 water project – priority documents', encoding='utf-8')

    async def plan(email, output, **kwargs):
        assert email == 'M12205 water project – priority documents'
        return tmp_path, dict(status='completed', basis='Metadata only', ranked_selections=[])

    monkeypatch.setattr(plan_cli, 'plan_email', plan)
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--email-file', str(source)])
    assert plan_cli.main() == 0
    assert 'No matching documents.' in capsys.readouterr().out


@pytest.mark.parametrize('args', [[], ['--email-file', 'missing.txt'], ['--email-file', '-', '--limit', '0'],
    ['--email-file', '-', '--limit', '11'], ['--email-file', '-', '--timeout-ms', '2'],
    ['--email-file', '-', '--inspect-seconds', '61'], ['--email-file', '-']])
def test_plan_cli_invalid_input_does_not_launch(args, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Invalid input must not start planning')

    monkeypatch.setattr(plan_cli, 'plan_email', unexpected)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(''))
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', *args])
    with pytest.raises(SystemExit) as exc:
        plan_cli.main()
    assert exc.value.code == 2


def test_plan_cli_output_error_is_visible_failure(monkeypatch, capsys):
    async def plan(*args, **kwargs):
        raise PermissionError('Cannot create output')

    monkeypatch.setattr(plan_cli, 'plan_email', plan)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('M12205'))
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--email-file', '-'])
    assert plan_cli.main() == 1
    assert 'Cannot write planning output' in capsys.readouterr().err


@pytest.mark.parametrize(('status', 'exit_code'), [('completed', 0), ('partial_review', 1)])
def test_review_option_reports_final_status_and_artifacts(monkeypatch, tmp_path, capsys, status, exit_code):
    async def plan(email, output, **kwargs):
        assert kwargs['review'] is True
        return tmp_path, dict(status=status, basis='PDF text only; images were not reviewed.',
            downloaded_count=1, reviewed_count=1 if status == 'completed' else 0,
            failed_count=0 if status == 'completed' else 1,
            documents=[dict(document_number='123', title='Board Order', status='reviewed')],
            archive={'relative_path': 'M12205_selected_documents.zip'})

    monkeypatch.setattr(plan_cli, 'plan_email', plan)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('M12205'))
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--email-file', '-', '--review'])
    assert plan_cli.main() == exit_code
    output = capsys.readouterr().out
    assert 'review.json' in output and 'review.md' in output
    assert 'ZIP:' in output
    assert 'Downloaded 1; reviewed' in output


@pytest.mark.parametrize('stdin', [False, True])
def test_json_input_writes_email_shaped_replies_with_same_ids(monkeypatch, tmp_path, capsys, stdin):
    async def plan(text, output, **options):
        assert text == 'Subject: M12205\n\nRecommend documents.'
        assert options == dict(limit=2, review=True, headed=True, channel='msedge',
                               timeout_ms=2000, inspect_seconds=0)
        return output / 'run', dict(status='completed', basis='PDF text only.', documents=[], archive=None)

    monkeypatch.setattr(email_flow, 'plan_email', plan)
    data = json.dumps({'emails': {'email1': {'subject': 'M12205', 'content': 'Recommend documents.'}}})
    path = tmp_path / 'inbox.json'
    path.write_text('\ufeff' + data, encoding='utf-8')
    monkeypatch.setattr(sys, 'stdin', io.StringIO(data))
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--emails-json', '-' if stdin else str(path),
                                    '--output', str(tmp_path), '--limit', '2', '--review', '--headed',
                                    '--channel', 'msedge', '--timeout-ms', '2000'])
    assert plan_cli.main() == 0
    output = capsys.readouterr().out
    assert 'Processed 1 email(s).' in output
    reply_path = next(tmp_path.glob('*/replies.json'))
    assert str(reply_path) in output
    replies = json.loads(reply_path.read_text(encoding='utf-8'))
    assert replies['emails']['email1']['subject'] == 'Re: M12205'
    assert 'No matching documents' in replies['emails']['email1']['content']


@pytest.mark.parametrize('text', ['not JSON', '{}', '{"emails": {}}',
    '{"emails": {"one": {"subject": "S", "content": ""}}}'])
def test_invalid_json_does_not_start_workflow(monkeypatch, text):
    def unexpected(*args, **kwargs):
        pytest.fail('Invalid JSON must not start processing')

    monkeypatch.setattr(plan_cli, 'process_emails', unexpected)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(text))
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--emails-json', '-'])
    with pytest.raises(SystemExit) as exc:
        plan_cli.main()
    assert exc.value.code == 2


def test_json_and_text_inputs_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--email-file', '-', '--emails-json', '-'])
    with pytest.raises(SystemExit) as exc:
        plan_cli.main()
    assert exc.value.code == 2


def test_json_reply_output_error_exits_one(monkeypatch, capsys):
    async def process(*args, **kwargs):
        raise PermissionError('Cannot write replies')

    monkeypatch.setattr(plan_cli, 'process_emails', process)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(
        '{"emails": {"email1": {"subject": "M12205", "content": "Recommend documents."}}}'))
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--emails-json', '-'])
    assert plan_cli.main() == 1
    assert 'Cannot write email replies' in capsys.readouterr().err


def test_resume_uses_saved_workflow_and_prints_review_paths(monkeypatch, tmp_path, capsys):
    async def resume(directory, **options):
        assert directory == tmp_path
        assert options['timeout_ms'] == 2000
        return directory, dict(status='completed', basis='PDF text only', documents=[],
            downloaded_count=0, reviewed_count=0, failed_count=0, archive=None)
    monkeypatch.setattr(plan_cli, 'resume_email', resume)
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--resume', str(tmp_path), '--timeout-ms', '2000'])
    assert plan_cli.main() == 0
    assert 'review.json' in capsys.readouterr().out


@pytest.mark.parametrize('extra', [['--email-file', '-'], ['--emails-json', '-'], ['--limit','3']])
def test_resume_cannot_change_input_or_limit(monkeypatch, tmp_path, extra):
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--resume', str(tmp_path), *extra])
    with pytest.raises(SystemExit) as error:
        plan_cli.main()
    assert error.value.code == 2


def test_missing_resume_checkpoint_returns_invalid_input(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--resume', str(tmp_path/'missing')])
    with pytest.raises(SystemExit) as error:
        plan_cli.main()
    assert error.value.code == 2
