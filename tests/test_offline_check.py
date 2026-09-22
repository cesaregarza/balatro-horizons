import json
import subprocess
from pathlib import Path

import pytest

from balatro_horizons.cli import offline as check_offline


def test_commands_use_explicit_checkout_and_web_is_opt_in():
    root = Path("/tmp/checkout with spaces")
    checks = check_offline.commands(root)
    assert len(checks) == 3
    assert checks[0] == [str(root / ".venv/bin/python"), "-m", "pytest", "-q"]
    assert check_offline.commands(root, web=True)[3:] == [
        ["npm", "--prefix", str(root / "web"), "run", "build"],
        ["npm", "--prefix", str(root / "web"), "test"],
    ]


def test_checks_stop_on_failure_and_remove_provider_credentials(tmp_path, monkeypatch):
    (tmp_path / "src/balatro_horizons").mkdir(parents=True)
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").touch()
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-anthropic")
    calls = []

    def run(command, *, cwd, env, check):
        calls.append(command)
        assert cwd == tmp_path
        assert check is True
        assert "OPENAI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env
        if len(calls) == 2:
            raise subprocess.CalledProcessError(7, command)

    monkeypatch.setattr(check_offline.subprocess, "run", run)
    assert check_offline.main(["--root", str(tmp_path), "--web"]) == 7
    assert len(calls) == 2


def test_invalid_checkout_is_rejected(tmp_path):
    with pytest.raises(SystemExit) as exc:
        check_offline.main(["--root", str(tmp_path)])
    assert exc.value.code == 2


def test_success_report_requires_completed_checks_and_unchanged_source(tmp_path, monkeypatch):
    source = tmp_path/'src/balatro_horizons/harness/loop.py'
    source.parent.mkdir(parents=True)
    source.write_text('original')
    (tmp_path/'.venv/bin').mkdir(parents=True)
    (tmp_path/'.venv/bin/python').touch()
    report = tmp_path/'success.json'
    monkeypatch.setattr(check_offline, 'run_checks', lambda *args, **kwargs: source.write_text('changed'))
    assert check_offline.main(['--root', str(tmp_path), '--report', str(report)]) == 1
    assert not report.exists()
    monkeypatch.setattr(check_offline, 'run_checks', lambda *args, **kwargs: None)
    assert check_offline.main(['--root', str(tmp_path), '--report', str(report)]) == 0
    result = json.loads(report.read_text())
    assert result['status'] == 'passed' and result['suite'] == 'check_offline'
    assert result['implementation_hash'] and len(result['commands']) == 3
