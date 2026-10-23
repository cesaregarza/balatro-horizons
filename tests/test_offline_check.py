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
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test_only")
    calls = []

    def run(command, *, cwd, env, check):
        calls.append(command)
        assert cwd == tmp_path
        assert check is True
        assert "OPENAI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_WORKSPACE_ID" not in env
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


def _checkout(tmp_path):
    (tmp_path / "src/balatro_horizons").mkdir(parents=True)
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").touch()
    return tmp_path


def _main(argv):
    try:
        return check_offline.main(argv)
    except SystemExit as exit:
        return exit.code


def test_web_only_runs_the_browser_lane_without_the_python_suite(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    calls = []
    monkeypatch.setattr(check_offline.subprocess, "run", lambda command, **_: calls.append(command))
    assert (_main(["--root", str(root), "--web-only"]), calls) == (0, [
        ["npm", "--prefix", str(root / "web"), "run", "build"],
        ["npm", "--prefix", str(root / "web"), "test"],
    ])


def test_web_only_cannot_write_a_source_bound_report(tmp_path, monkeypatch, capsys):
    # Evidence reuse accepts any passed check_offline report; a partial run must not produce one.
    root = _checkout(tmp_path)
    calls = []
    monkeypatch.setattr(check_offline.subprocess, "run", lambda command, **_: calls.append(command))
    code = _main(["--root", str(root), "--web-only", "--report", str(tmp_path / "r.json")])
    assert (code, calls, (tmp_path / "r.json").exists()) == (2, [], False)
    assert "--web-only cannot write --report" in capsys.readouterr().err


def test_ci_web_job_runs_only_the_browser_lane_with_headroom():
    import yaml

    workflow = yaml.safe_load(
        (Path(check_offline.ROOT) / ".github/workflows/offline.yml").read_text()
    )
    jobs = workflow["jobs"]
    runs = {name: [step.get("run", "") for step in job["steps"]] for name, job in jobs.items()}
    assert ".venv/bin/python scripts/check_offline.py" in runs["python"]
    assert ".venv/bin/python scripts/check_offline.py --web-only" in runs["web"]
    assert ".venv/bin/python scripts/check_offline.py --web" not in runs["web"]
    assert jobs["web"]["timeout-minutes"] >= 15


def test_local_web_still_runs_the_python_suite_before_the_browser_lane(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    calls = []
    monkeypatch.setattr(check_offline.subprocess, "run", lambda command, **_: calls.append(command))
    assert (_main(["--root", str(root), "--web"]), calls) == (0, check_offline.commands(root, web=True))
    assert [command[:3] for command in calls[:1]] == [[str(root / ".venv/bin/python"), "-m", "pytest"]]
    assert len(calls) == 5


def test_web_and_web_only_are_mutually_exclusive(tmp_path, monkeypatch, capsys):
    root = _checkout(tmp_path)
    calls = []
    monkeypatch.setattr(check_offline.subprocess, "run", lambda command, **_: calls.append(command))
    assert (_main(["--root", str(root), "--web", "--web-only"]), calls) == (2, [])
    assert "not allowed with argument" in capsys.readouterr().err


def test_ci_web_job_checks_out_without_full_history():
    import yaml

    workflow = yaml.safe_load(
        (Path(check_offline.ROOT) / ".github/workflows/offline.yml").read_text()
    )
    checkout = next(step for step in workflow["jobs"]["web"]["steps"]
                    if step.get("uses", "").startswith("actions/checkout@"))
    # Only the python job's release-source tests need full history.
    assert "fetch-depth" not in checkout.get("with", {})
