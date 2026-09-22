"""Validate and package the Balatro guide and its frozen read_rules lookup entries.

Run with the repository environment:
  uv run bh guide package --guide docs/balatro-guide \
    --output docs/balatro-guide.zip --report reports/verification/balatro-guide.json

The rules.json payload supplies entries/aliases for the existing rules helper.
It is not a replacement for an environment-bound native rules manifest.
"""

import hashlib
import io
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

ORIGIN = re.compile(r"\b(?:fable|chatgpt|gpt|claude)\b", re.I)
LINK = re.compile(r"\[([^\]\n]+)\]\(([^\s()]+)\)")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
ROOT_KEY = "guide/balatro-orchestrator"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def native_path(path):
    resolved = path.resolve()
    if resolved.is_relative_to("/mnt"):
        raise ValueError("Guide packaging must use native Linux paths")
    return resolved


def topic_key(path):
    if path == Path("README.md"):
        return None
    if path == Path("KNOWN_LIMITS.md"):
        return "guide/limits"
    if path == Path("SOURCES.md"):
        return "guide/sources"
    if len(path.parts) == 3 and path.parts[0] == "skills" and path.name == "SKILL.md":
        return "guide/" + path.parts[1]
    if len(path.parts) == 4 and path.parts[0] == "skills" and path.parts[2] == "references":
        return "guide/" + path.parts[1] + "/" + path.stem
    raise ValueError(f"Unmapped guide chapter: {path}")


def _collect_documents(directory):
    documents, bodies, topics, skills, catalog = {}, {}, {}, [], []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink in guide: {path.relative_to(directory)}")
        if not path.is_file():
            continue
        relative = path.relative_to(directory)
        if relative == Path("rules.json"):
            continue  # Generated afresh from Markdown; never an input.
        if path.suffix != ".md":
            raise ValueError(f"Unexpected guide file: {relative}")
        content = path.read_text(encoding="utf-8")
        if ORIGIN.search(content) or ORIGIN.search(relative.as_posix()):
            raise ValueError(f"Origin attribution remains: {relative}")
        if "/root/" in content or "/mnt/" in content:
            raise ValueError(f"Nonportable host path: {relative}")
        documents[relative.as_posix()] = content.encode()
        match = FRONTMATTER.match(content)
        if path.name == "SKILL.md":
            fields = _skill_fields(relative, path, match)
            skills.append(fields["name"])
            catalog.append(
                {
                    "name": fields["name"],
                    "description": fields["description"],
                    "key": "guide/" + fields["name"],
                }
            )
            content = content[match.end() :]
        bodies[relative] = content
        topics[relative] = topic_key(relative)
    return documents, bodies, topics, skills, catalog


def _skill_fields(relative, path, match):
    if not match:
        raise ValueError(f"Missing skill frontmatter: {relative}")
    fields = yaml.safe_load(match.group(1))
    if not isinstance(fields, dict):
        raise ValueError(f"Invalid skill frontmatter: {relative}")
    if fields.get("name") != path.parent.name or not re.fullmatch(
        r"[a-z0-9]+(?:-[a-z0-9]+)*", str(fields.get("name", ""))
    ):
        raise ValueError(f"Invalid skill name: {relative}")
    description = fields.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError(f"Invalid skill description: {relative}")
    return {"name": fields["name"], "description": description}


def _validate_topics(bodies, topics):
    if Path("README.md") not in bodies or ROOT_KEY not in topics.values():
        raise ValueError("Guide requires README.md and the orchestrator skill")
    if len({key for key in topics.values() if key}) != len(topics) - 1:
        raise ValueError("Guide topic keys must be unique")


def _rewrite_entries(directory, bodies, topics):
    link_count = 0

    def rewrite_link(source, match):
        nonlocal link_count
        label, target = match.groups()
        url = urlsplit(target)
        if url.scheme in ("http", "https"):
            return match.group(0)
        if url.scheme or url.netloc or url.query:
            raise ValueError(f"Unsupported link in {source}: {target}")
        if not url.path:
            return match.group(0)
        resolved = (directory / source.parent / unquote(url.path)).resolve()
        if not resolved.is_relative_to(directory):
            raise ValueError(f"Link escapes guide in {source}: {target}")
        relative = resolved.relative_to(directory)
        if relative not in bodies:
            raise ValueError(f"Broken chapter link in {source}: {target}")
        link_count += 1
        key = topics[relative]
        if topics[source] and not key:
            raise ValueError(f"Player chapter links to operator README: {source}")
        return f"{label} (read_rules key: {key})" if key else label

    entries = {}
    for path, body in bodies.items():
        rewritten = LINK.sub(lambda match, path=path: rewrite_link(path, match), body)
        if topics[path]:
            entries[topics[path]] = rewritten.strip()
    return entries, link_count


def _build_rules(documents, entries, catalog):
    content_hash = sha256(encode({path: sha256(data) for path, data in documents.items()}))
    rules = {
        "format": "balatro-horizons-guide-v1",
        "guide_content_sha256": content_hash,
        "entries": entries,
        "aliases": {"guide": ROOT_KEY, "guide/index": ROOT_KEY},
        "skills": catalog,
    }
    rules["bundle_hash"] = sha256(
        json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    )
    return rules, content_hash


def compile_guide(directory):
    directory = native_path(directory)
    documents, bodies, topics, skills, catalog = _collect_documents(directory)
    _validate_topics(bodies, topics)
    entries, link_count = _rewrite_entries(directory, bodies, topics)
    rules, content_hash = _build_rules(documents, entries, catalog)
    return (
        documents,
        rules,
        {
            "result": "pass",
            "markdown_files": len(documents),
            "skills": sorted(skills),
            "internal_links_checked": link_count,
            "lookup_entries": len(entries),
            "guide_content_sha256": content_hash,
            "origin_attribution_matches": 0,
        },
    )


def archive_bytes(documents, rules):
    stream = io.BytesIO()
    members = {**documents, "rules.json": encode(rules)}
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo("balatro-guide/" + name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    result = stream.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        if archive.testzip() is not None:
            raise ValueError("Archive CRC check failed")
        for name, data in members.items():
            if archive.read("balatro-guide/" + name) != data:
                raise ValueError(f"Archive content mismatch: {name}")
    return result


def atomic_write(path, data):
    path = native_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temp_path = Path(temporary.name)
        temporary.write(data)
    try:
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _portable_archive(archive, documents, rules):
    with tempfile.TemporaryDirectory(prefix="balatro-guide-check-") as temporary:
        with zipfile.ZipFile(io.BytesIO(archive)) as packaged:
            packaged.extractall(temporary)
        copied, copied_rules, _ = compile_guide(Path(temporary) / "balatro-guide")
        if copied != documents or copied_rules != rules:
            raise ValueError("Portable bundle reconstruction differs")


def configure_parser(parser):
    parser.description = __doc__
    parser.add_argument("--guide", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="ZIP destination; omit for validation only")
    parser.add_argument("--report", type=Path, help="Optional JSON validation receipt")
    parser.set_defaults(operation_handler=run, operation_parser=parser)
    return parser


def run(args):
    try:
        guide = native_path(args.guide)
        if args.output and native_path(args.output).is_relative_to(guide):
            raise ValueError("Write the ZIP outside the guide directory")
        documents, rules, report = compile_guide(guide)
        if args.output:
            archive = archive_bytes(documents, rules)
            _portable_archive(archive, documents, rules)
            atomic_write(guide / "rules.json", encode(rules))
            atomic_write(args.output, archive)
            report.update(archive_sha256=sha256(archive), archive_bytes=len(archive), portable=True)
        if args.report:
            atomic_write(args.report, encode(report))
        print(encode(report).decode(), end="")
    except (OSError, ValueError, yaml.YAMLError, zipfile.BadZipFile) as error:
        print(f"Guide packaging failed: {error}", file=sys.stderr)
        return 1
    return 0
