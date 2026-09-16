import importlib.util

import pytest

from balatro_horizons.config import ROOT


@pytest.fixture
def deploy(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("deploy_frontend", ROOT / "scripts/deploy_frontend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.deploy


def fixture(tmp_path):
    build, dist = tmp_path / "build", tmp_path / "dist"
    for path in (build, dist):
        (path / "assets").mkdir(parents=True)
    (build / "index.html").write_text('<script src="/assets/new.js"></script>')
    (build / "assets/new.js").write_text("new bundle")
    (dist / "index.html").write_text("old index")
    (dist / "assets/old.js").write_text("old bundle")
    return build, dist, tmp_path / "backup.html"


def test_frontend_only_publish_keeps_old_assets_and_exact_index_backup(deploy, tmp_path):
    build, dist, backup = fixture(tmp_path)
    result = deploy(build, dist, backup)
    assert result["backend_restarted"] is False
    assert backup.read_text() == "old index"
    assert (dist / "assets/old.js").read_text() == "old bundle"
    assert (dist / "assets/new.js").read_bytes() == (build / "assets/new.js").read_bytes()
    assert (dist / "index.html").read_bytes() == (build / "index.html").read_bytes()


@pytest.mark.parametrize("failure", ["missing", "collision"])
def test_bad_build_preserves_the_current_index(deploy, tmp_path, failure):
    build, dist, backup = fixture(tmp_path)
    if failure == "missing":
        (build / "assets/new.js").unlink()
    else:
        (dist / "assets/new.js").write_text("different existing content")
    with pytest.raises(ValueError):
        deploy(build, dist, backup)
    assert (dist / "index.html").read_text() == "old index"
    assert not backup.exists()
