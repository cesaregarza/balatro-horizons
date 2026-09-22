#!/usr/bin/env python3
"""Install pinned instrumentation into a dedicated licensed Windows runtime copy."""

import argparse
import hashlib
import io
import json
import secrets
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from balatro_horizons.game.patches import patch_rearrange

ROOT = Path(__file__).resolve().parents[1]
BOT = "e7c6db8a9ad88318f6e4128eefd6e61aafc94885"
SMODS = "39182f0cc7b1af86d3d3d6afc5422661a07b4312"
LOVELY_SHA = "40b994a055ee75e5f2aba81e7ae06f2c17460e18cc346483089921899fadd1f7"


def fetch(url, target, expected=None):
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=90) as response:
            data = response.read()
        if expected and hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Downloaded artifact hash mismatch")
        target.write_bytes(data)
    data = target.read_bytes()
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Cached artifact hash mismatch")
    return data


def tree_hash(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if (
            path.is_file()
            and ".git" not in path.parts
            and "lovely" not in path.relative_to(root).parts
        ):
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    # The licensed source and owned runtime are explicit installer arguments;
    # the destination guard prevents touching a personal game installation.
    p.add_argument(
        "--source", type=Path, default=Path("/mnt/d/SteamLibrary/steamapps/common/Balatro")
    )
    p.add_argument("--destination", type=Path, default=Path("/mnt/d/BalatroHorizonsRuntime"))
    p.add_argument("--refresh-instrumentation", action="store_true")
    args = p.parse_args()
    source, dest = args.source.resolve(), args.destination.resolve()
    if source == dest or dest != Path("/mnt/d/BalatroHorizonsRuntime"):
        p.error("Only the dedicated D:\\BalatroHorizonsRuntime destination is supported")
    if not (source / "Balatro.exe").is_file():
        p.error("Licensed Balatro.exe is missing")
    if dest.exists() and not (dest / "horizons-owned.json").is_file():
        p.error("Refusing an existing directory without the Horizons ownership marker")
    dest.mkdir(exist_ok=True)
    (dest / "horizons-owned.json").write_text(
        json.dumps({"schema_version": 1, "purpose": "isolated-balatro-horizons"})
    )
    for src in source.iterdir():
        if src.is_file() and src.name != "version.dll" and not (dest / src.name).exists():
            shutil.copy2(src, dest / src.name)
    vendor = ROOT / "vendor"
    vendor.mkdir(exist_ok=True)
    if str(vendor.resolve()).startswith("/mnt/"):
        p.error("Dependencies must stay on the native Linux filesystem")
    lovely = fetch(
        "https://github.com/ethangreen-dev/lovely-injector/releases/download/v0.9.0/lovely-x86_64-pc-windows-msvc.zip",
        vendor / "lovely-0.9.0.zip",
        LOVELY_SHA,
    )
    with zipfile.ZipFile(io.BytesIO(lovely)) as archive:
        names = [n for n in archive.namelist() if n.endswith("version.dll")]
        if len(names) != 1:
            raise ValueError("Unexpected Lovely archive")
        (dest / "version.dll").write_bytes(archive.read(names[0]))
    for name, repo, commit in [
        ("smods", "Steamodded/smods", SMODS),
        ("balatrobot", "coder/balatrobot", BOT),
    ]:
        target = vendor / name
        if not target.exists():
            data = fetch(
                f"https://codeload.github.com/{repo}/tar.gz/{commit}",
                vendor / (name + "-" + commit + ".tar.gz"),
            )
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                archive.extractall(vendor, filter="data")
            (vendor / (repo.split("/")[1] + "-" + commit)).rename(target)
        if (target / ".git").exists():
            actual = subprocess.check_output(
                ["git", "-C", str(target), "rev-parse", "HEAD"], text=True
            ).strip()
            if actual != commit:
                raise ValueError("Unexpected upstream commit")
        mod = dest / "Mods" / name
        shutil.copytree(
            target,
            mod,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                ".git", "docs", "tests", ".github", ".claude", ".mux", "__pycache__"
            ),
        )
    start = dest / "Mods/balatrobot/src/lua/endpoints/start.lua"
    source_text = start.read_text()
    source_text = source_text.replace(
        "G.GAME.viewed_back:change_to(deck_data)",
        "if G.GAME.viewed_back then G.GAME.viewed_back:change_to(deck_data) end",
    )
    start.write_text(source_text)
    rearrange = dest / "Mods/balatrobot/src/lua/endpoints/rearrange.lua"
    rearrange.write_text(patch_rearrange(rearrange.read_text()))
    for ignored in (".claude", ".mux"):
        leftover = dest / "Mods/balatrobot" / ignored
        if leftover.is_dir():
            shutil.rmtree(leftover)
    shutil.copytree(ROOT / "native/isolation", dest / "Mods/HorizonsIsolation", dirs_exist_ok=True)
    shutil.copy2(ROOT / "native/bridge.ps1", dest / "bridge.ps1")
    for extra in (ROOT / "native/patches").glob("*.lua"):
        shutil.copy2(extra, dest / "Mods/balatrobot" / extra.name)
    extension = ROOT / "native/patches/horizons.lua"
    if extension.exists():
        shutil.copy2(extension, dest / "Mods/balatrobot/horizons.lua")
        main = dest / "Mods/balatrobot/balatrobot.lua"
        with main.open("a") as stream:
            stream.write('\nassert(SMODS.load_file("horizons.lua"))()\n')
    if not (dest / "token.txt").exists():
        (dest / "token.txt").write_text(secrets.token_hex(32))
    (dest / "checkpoints").mkdir(exist_ok=True)
    game = hashlib.sha256((dest / "Balatro.exe").read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "game_version": "1.0.1o-FULL",
        "game_sha256": game,
        "lovely_version": "0.9.0",
        "lovely_archive_sha256": LOVELY_SHA,
        "injector_sha256": hashlib.sha256((dest / "version.dll").read_bytes()).hexdigest(),
        "balatrobot_commit": BOT,
        "steamodded_commit": SMODS,
        "steamodded_release": "26.829.0",
        "mods_sha256": tree_hash(dest / "Mods"),
        "bridge_sha256": hashlib.sha256((dest / "bridge.ps1").read_bytes()).hexdigest(),
        "identity": "BalatroHorizons",
        "native_certified": False,
        "certificates": [],
    }
    (ROOT / "private").mkdir(mode=0o700, exist_ok=True)
    (ROOT / "private/environment.lock.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (dest / "environment.lock.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {"installed": True, "game_version": manifest["game_version"], "native_certified": False}
        )
    )


if __name__ == "__main__":
    main()
