import json
import sys
from pathlib import Path

import pytest

from senpilot_agent import email_flow, plan_cli


def inbox(tmp_path, text=None):
    path = tmp_path / 'inbox.json'
    path.write_text(text if text is not None else json.dumps({'emails': {
        'email1': {'subject': 'M12205', 'content': 'Review one Board Order.'}}}), encoding='utf-8')
    return path


@pytest.mark.parametrize(('status', 'expected_code'), [('completed', 0), ('clarification', 2), ('failed', 1)])
def test_json_file_preserves_ids_and_uses_email_driven_actions(tmp_path, monkeypatch, capsys, status, expected_code):
    async def plan(text, output, **options):
        assert text == 'Subject: M12205\n\nReview one Board Order.'
        assert options == dict(headed=True, channel='msedge', timeout_ms=2000, inspect_seconds=1)
        return output / 'run', dict(status=status, basis='PDF text only.', documents=[], archive=None,
            clarification_question='Which matter?', error={'message': 'Service unavailable'})

    monkeypatch.setattr(email_flow, 'plan_email', plan)
    path = inbox(tmp_path)
    path.write_text('\ufeff' + path.read_text(encoding='utf-8'), encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--emails-json', str(path), '--output', str(tmp_path),
        '--headed', '--channel', 'msedge', '--timeout-ms', '2000', '--inspect-seconds', '1'])
    assert plan_cli.main() == expected_code
    output = capsys.readouterr().out
    assert 'Processed 1 email(s).' in output
    reply_path = next(tmp_path.glob('*/replies.json'))
    assert str(reply_path) in output
    replies = json.loads(reply_path.read_text(encoding='utf-8'))
    assert list(replies['emails']) == ['email1']
    assert replies['emails']['email1']['subject'] == 'Re: M12205'


@pytest.mark.parametrize('text', ['', ' ', 'not JSON', '{}', '{"emails": {}}',
    '{"emails": {"one": {"subject": "S", "content": ""}}}'])
def test_invalid_json_never_starts_pipeline(tmp_path, monkeypatch, text):
    def unexpected(*args, **kwargs):
        pytest.fail('Invalid input must not start processing')
    monkeypatch.setattr(plan_cli, 'process_emails', unexpected)
    path = inbox(tmp_path, text)
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--emails-json', str(path)])
    with pytest.raises(SystemExit) as error:
        plan_cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize('extra', [[], ['--email-file', 'request.txt'], ['--resume', 'run'],
    ['--matter', 'M12205'], ['--category', 'Key Documents'], ['--review'], ['--limit', '1'],
    ['--timeout-ms', '2'], ['--inspect-seconds', '61']])
def test_removed_inputs_and_invalid_options_are_rejected_before_pipeline(tmp_path, monkeypatch, extra):
    def unexpected(*args, **kwargs):
        pytest.fail('Rejected arguments must not start processing')
    monkeypatch.setattr(plan_cli, 'process_emails', unexpected)
    source = [] if not extra else ['--emails-json', str(inbox(tmp_path))]
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', *source, *extra])
    with pytest.raises(SystemExit) as error:
        plan_cli.main()
    assert error.value.code == 2


def test_standard_input_is_not_an_entry_point(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--emails-json', '-'])
    with pytest.raises(SystemExit) as error:
        plan_cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize('kind', ['missing', 'directory', 'invalid_utf8'])
def test_unreadable_input_is_invalid_input(tmp_path, monkeypatch, kind):
    path = tmp_path / 'source.json'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'invalid_utf8':
        path.write_bytes(b'\xff')
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--emails-json', str(path)])
    with pytest.raises(SystemExit) as error:
        plan_cli.main()
    assert error.value.code == 2


def test_reply_write_failure_exits_one(tmp_path, monkeypatch, capsys):
    async def process(*args, **kwargs):
        raise PermissionError('Cannot write replies')
    monkeypatch.setattr(plan_cli, 'process_emails', process)
    monkeypatch.setattr(sys, 'argv', ['senpilot-plan', '--emails-json', str(inbox(tmp_path))])
    assert plan_cli.main() == 1
    assert 'Cannot write email replies' in capsys.readouterr().err


def test_only_json_cli_is_registered():
    import tomllib
    project = Path(__file__).resolve().parents[1] / 'pyproject.toml'
    scripts = tomllib.loads(project.read_text(encoding='utf-8'))['project']['scripts']
    assert scripts == {'senpilot-plan': 'senpilot_agent.plan_cli:main'}
