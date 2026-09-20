#!/usr/bin/env python3
"""Report reproducible Python file, comment, and function-size metrics."""

import argparse
import ast
import io
import json
import tokenize
from pathlib import Path


def python_files(inputs):
    files = set()
    for supplied in inputs:
        path = Path(supplied).resolve()
        if path.is_relative_to("/mnt") or not path.exists():
            raise ValueError(f"INVALID_SOURCE_PATH: {supplied}")
        candidates = path.rglob("*.py") if path.is_dir() else [path]
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved.is_relative_to("/mnt") or resolved.suffix != ".py":
                raise ValueError(f"INVALID_SOURCE_PATH: {candidate}")
            files.add(resolved)
    if not files:
        raise ValueError("NO_PYTHON_FILES")
    return sorted(files)


def measure(path):
    source = path.read_text(encoding="utf8")
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
    parser.add_argument("paths", nargs="+", help="Python files or directories to scan")
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
        rows = [measure(path) for path in python_files(args.paths)]
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
