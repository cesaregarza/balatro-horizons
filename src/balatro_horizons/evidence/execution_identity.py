"""Compare executable agent behavior, allowing only explicit recovery plumbing.

Native bytes are compared separately. These exact AST rewrites recognize the
old and new source guards; an arbitrary edit inside those methods still differs.
Historical code is parsed as data, never imported or executed.
"""

import ast
from copy import deepcopy

from balatro_horizons.storage.journal import digest


def _node(source):
    return ast.parse(source).body[0]


def _same(left, right):
    return ast.dump(left) == ast.dump(right)


OLD_DECISION_GUARD = _node('''
if self.protocol and self.protocol["implementation_hash"] != implementation_fingerprint():
    raise HarnessFailure("AGENT_PROTOCOL_IMPLEMENTATION_CHANGED", stage="decision_start")
''')
NEW_DECISION_GUARD = _node('''
if self.protocol and getattr(self, "execution_implementation_hash", self.protocol["implementation_hash"]) != implementation_fingerprint():
    raise HarnessFailure("AGENT_PROTOCOL_IMPLEMENTATION_CHANGED", stage="decision_start")
''')
OLD_RESTORE_GUARD = _node('''
if bundle["implementation_hash"] != current_implementation:
    raise ValueError("AGENT_PROTOCOL_IMPLEMENTATION_CHANGED")
''')
NEW_RESTORE_GUARD = _node('''
if bundle["implementation_hash"] != current_implementation:
    from balatro_horizons.evidence.compatibility import require_protocol_compatibility
    require_protocol_compatibility(store, checkpoint, bundle)
''')
CHECKPOINT_GUARD = _node('''
if not resume.get("continuation_hash"):
    raise ValueError("CHECKPOINT_CONTINUATION_MISSING")
''')
RUNTIME_BINDINGS = [
    _node('self.execution_implementation_hash = implementation_fingerprint()'),
    _node('self.source_compatibility = deepcopy(resume.get("source_compatibility")) if resume else None'),
]
# These entry points are not called by the restore worker. Keep imports, class
# structure and every other method, including any newly introduced helper.
OTHER_SERVICE_ENTRYPOINTS = {
    "start", "branch", "continue_budget", "run_batch", "preflight_batch",
    "_freeze_batch", "_slot_affordable", "_run_batch",
}


def _normalize_method(path, method):
    replacements, removable = [], []
    if path == "harness/decision.py" and method.name == "_check_protocol_integrity":
        replacements = [(NEW_DECISION_GUARD, OLD_DECISION_GUARD)]
    if path == "harness/context/freeze.py" and method.name == "restore_protocol":
        replacements = [(NEW_RESTORE_GUARD, OLD_RESTORE_GUARD)]
    if path == "harness/runtime.py":
        if method.name == "freeze_or_restore_protocol":
            removable = RUNTIME_BINDINGS
        if method.name == "rehydrate_resume_state":
            removable = [CHECKPOINT_GUARD]
        if method.name == "_checkpoint_payload":
            _strip_compatibility_reference(method)
    body = []
    for statement in method.body:
        if any(_same(statement, item) for item in removable):
            continue
        replacement = next((old for new, old in replacements if _same(statement, new)), statement)
        body.append(deepcopy(replacement))
    method.body = body


def _strip_compatibility_reference(method):
    expected = ast.parse("deepcopy(self.source_compatibility)", mode="eval").body
    for node in ast.walk(method):
        if isinstance(node, ast.Dict):
            pairs = [(key, value) for key, value in zip(node.keys, node.values, strict=True)
                     if not (isinstance(key, ast.Constant) and key.value == "source_compatibility"
                             and _same(value, expected))]
            node.keys, node.values = [key for key, _ in pairs], [value for _, value in pairs]


def _normalize_coordinator(path, tree):
    if path == "service.py":
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "RunService":
                node.body = [item for item in node.body if not (
                    isinstance(item, ast.FunctionDef) and item.name in OTHER_SERVICE_ENTRYPOINTS
                )]
    if path == "service_execution.py":
        # #57 made resumed workers require the production release gate too.
        old = ast.parse("calibration or bool(request.resume)", mode="eval").body
        for method in tree.body:
            if isinstance(method, ast.FunctionDef) and method.name == "prepare_execution":
                for node in ast.walk(method):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ExecutionPlan":
                        for keyword in node.keywords:
                            if keyword.arg == "calibration" and _same(keyword.value, old):
                                keyword.value = ast.Name(id="calibration", ctx=ast.Load())


def execution_manifest(sources):
    """Pin agent behavior and the coordinator methods used by restored workers."""
    prefix = "src/balatro_horizons/"
    result = {}
    for name, content in sources.items():
        path = name.removeprefix(prefix)
        if not name.startswith(prefix) or not (
            path.startswith("harness/")
            or path in ("config.py", "workbench/policies.py", "service.py", "service_execution.py")
        ):
            continue
        tree = ast.parse(content)
        _normalize_coordinator(path, tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _normalize_method(path, node)
        result[name] = digest(ast.dump(tree))
    return result
