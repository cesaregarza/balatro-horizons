#!/usr/bin/env python3
"""Report reproducible Python and Lua file, comment, and function metrics."""

import argparse
import ast
import io
import json
import re
import tokenize
from pathlib import Path


def source_files(inputs):
    files = set()
    for supplied in inputs:
        path = Path(supplied).resolve()
        if path.is_relative_to("/mnt") or not path.exists():
            raise ValueError(f"INVALID_SOURCE_PATH: {supplied}")
        candidates = (
            (candidate for candidate in path.rglob("*") if candidate.suffix in {".py", ".lua"})
            if path.is_dir() else [path]
        )
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved.is_relative_to("/mnt") or resolved.suffix not in {".py", ".lua"}:
                raise ValueError(f"INVALID_SOURCE_PATH: {candidate}")
            files.add(resolved)
    if not files:
        raise ValueError("NO_SOURCE_FILES")
    return sorted(files)


LUA_TOKEN = re.compile(
    r"--\[\[.*?\]\]|--[^\n]*|\[\[.*?\]\]|'(?:\\.|[^'\\])*'|"
    r'"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z0-9_]*',
    re.DOTALL,
)


def lua_measure(source):
    """Measure the Lua block syntax used by the owned split patch modules."""
    stack = []
    functions = []
    comments = 0
    pending_loop_do = 0
    for match in LUA_TOKEN.finditer(source):
        word = match.group()
        if word.startswith("--"):
            comments += 1
            continue
        if word.startswith(("'", '"', "[[")):
            continue
        line = source.count("\n", 0, match.start()) + 1
        if word == "function":
            declaration = re.match(r"\s*([A-Za-z_][\w.:]*)?\s*\(", source[match.end():])
            name = declaration.group(1) if declaration and declaration.group(1) else "<anonymous>"
            stack.append(("function", line, name))
        elif word in {"if", "for", "while", "repeat"}:
            stack.append((word, line, ""))
            if word in {"for", "while"}:
                pending_loop_do += 1
        elif word == "do":
            if pending_loop_do:
                pending_loop_do -= 1
            else:
                stack.append(("do", line, ""))
        elif word in {"end", "until"}:
            if not stack:
                raise ValueError("LUA_UNBALANCED_BLOCK")
            kind, start, name = stack.pop()
            if (word == "until") != (kind == "repeat"):
                raise ValueError("LUA_UNBALANCED_BLOCK")
            if kind == "function":
                functions.append({"name": name, "lines": line - start + 1})
    if stack or pending_loop_do:
        raise ValueError("LUA_UNBALANCED_BLOCK")
    return comments, functions


def measure(path):
    source = path.read_text(encoding="utf8")
    if path.suffix == ".lua":
        comments, functions = lua_measure(source)
        return {
            "path": str(path), "lines": len(source.splitlines()),
            "comment_count": comments,
            "longest_function": max(
                functions, key=lambda item: (item["lines"], item["name"]), default=None
            ),
        }
    tree = ast.parse(source, filename=str(path))
    functions = [
        {"name": node.name, "lines": node.end_lineno - node.lineno + 1}
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    longest = max(functions, key=lambda item: (item["lines"], item["name"]), default=None)
    comments = sum(
        token.type == tokenize.COMMENT
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
    )
    return {
        "path": str(path),
        "lines": len(source.splitlines()),
        "comment_count": comments,
        "longest_function": longest,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="Python/Lua files or directories to scan")
    parser.add_argument("--max-file-lines", type=int)
    parser.add_argument("--max-function-lines", type=int)
    parser.add_argument(
        "--frozen-function-exception", action="append", default=[], metavar="FILE",
        help="Report but exempt a byte-identical frozen file from the function limit",
    )
    args = parser.parse_args(argv)
    if any(limit is not None and limit < 1
           for limit in (args.max_file_lines, args.max_function_lines)):
        parser.error("limits must be positive")
    try:
        rows = [measure(path) for path in source_files(args.paths)]
        exceptions = {str(Path(path).resolve()) for path in args.frozen_function_exception}
        if not exceptions <= {row["path"] for row in rows}:
            raise ValueError("EXCEPTION_NOT_IN_SCAN")
    except (OSError, SyntaxError, UnicodeError, ValueError) as error:
        parser.exit(2, f"{error}\n")
    violations = [
        row["path"]
        for row in rows
        if (args.max_file_lines is not None and row["lines"] > args.max_file_lines)
        or (row["path"] not in exceptions
            and args.max_function_lines is not None and row["longest_function"] is not None
            and row["longest_function"]["lines"] > args.max_function_lines)
    ]
    print(json.dumps({"files": rows, "frozen_function_exceptions": sorted(exceptions),
                      "violations": violations}, sort_keys=True, indent=2))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
