#!/usr/bin/env python3
"""Report and gate context-package file and function lengths."""

import argparse
import ast
import io
import json
import tokenize
from pathlib import Path


def metrics(path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    longest = max(functions, key=lambda node: node.end_lineno - node.lineno + 1,
                  default=None)
    comments = {token.start[0] for token in tokenize.generate_tokens(io.StringIO(source).readline)
                if token.type == tokenize.COMMENT}
    return {
        "file": str(path), "lines": len(source.splitlines()), "comment_lines": len(comments),
        "longest_function": longest.name if longest else None,
        "longest_function_lines": longest.end_lineno - longest.lineno + 1 if longest else 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Explicit context package directory")
    parser.add_argument("--max-file-lines", type=int, default=399)
    parser.add_argument("--max-function-lines", type=int, default=60)
    args = parser.parse_args()
    if not args.path.is_dir() or args.max_file_lines < 1 or args.max_function_lines < 1:
        parser.error("path must be a directory and limits must be positive")
    files = sorted(args.path.glob("*.py"))
    if not files:
        parser.error("path contains no Python files")
    results = [metrics(path) for path in files]
    failed = [row["file"] for row in results
              if row["lines"] > args.max_file_lines
              or row["longest_function_lines"] > args.max_function_lines]
    print(json.dumps({"files": results, "failed": failed}, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
