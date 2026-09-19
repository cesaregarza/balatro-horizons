"""Strict source boundary for connection/startup-only evidence acceptance.

Gameplay RPC encoding, response handling, action execution, native restoration,
projection and mechanics must remain identical. This does not migrate historical
checkpoint certificates or claim fresh restoration evidence.
"""

import ast

from balatro_horizons.engine.provenance import native_components

PREFIX = "src/balatro_horizons/engine/"
CONTEXT_IMPORT = ast.parse("from balatro_horizons.engine.windows_context import bridge_environment").body[0]
DEADLINE = ast.parse("if self._startup_deadline is not None:\n    deadline = min(deadline, self._startup_deadline)").body[0]
CLEANUP = ast.parse("try:\n    self.close()\nexcept (NativeFailure, OSError):\n    pass").body[0]
CONTEXT_ERROR = ast.parse("try:\n    pass\nexcept ValueError as error:\n    raise NativeFailure(str(error)) from None").body[0].handlers[0]


def same(left, right):
    return ast.dump(left, include_attributes=False) == ast.dump(right, include_attributes=False)


class ConnectionOnly(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        return None if same(node, CONTEXT_IMPORT) else node

    def visit_ClassDef(self, node):
        for method in node.body:
            if not isinstance(method, ast.FunctionDef):
                continue
            if node.name == "WindowsBridge" and method.name == "launch":
                method.body = [ast.Pass()]
            if node.name == "WindowsBridge" and method.name == "__init__":
                method.body = [line for line in method.body if not (
                    isinstance(line, ast.Assign) and len(line.targets) == 1
                    and any(same(line, ast.parse(f"self.{key} = None").body[0])
                            for key in ("_launch_process", "_startup_deadline"))
                )]
        return self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess" and node.func.attr in ("Popen", "run"):
            node.keywords = [item for item in node.keywords if not (
                item.arg == "env" and same(item.value, ast.parse("bridge_environment()", mode="eval").body)
            )]
        return self.generic_visit(node)

    def visit_Assign(self, node):
        if (len(node.targets) == 1 and same(node.targets[0], ast.parse("self._launch_process = None").body[0].targets[0])
                and isinstance(node.value, ast.Call)):
            return ast.Expr(value=self.visit(node.value))
        return self.generic_visit(node)

    def visit_If(self, node):
        return None if same(node, DEADLINE) else self.generic_visit(node)

    def visit_Try(self, node):
        if same(node, CLEANUP):
            return ast.parse("self.close()").body[0]
        node.handlers = [handler for handler in node.handlers if not same(handler, CONTEXT_ERROR)]
        return self.generic_visit(node)


def require_startup_scope(before, after):
    old, new = native_components(before), native_components(after)
    for name in (PREFIX + "native.py", PREFIX + "windows_context.py"):
        old.pop(name, None)
        new.pop(name, None)
    if old != new:
        raise ValueError("NON_STARTUP_NATIVE_COMPONENTS_CHANGED")
    trees = [ConnectionOnly().visit(ast.parse(source[PREFIX + "native.py"])) for source in (before, after)]
    if not same(*trees):
        raise ValueError("GAMEPLAY_TRANSPORT_OR_EXECUTION_CHANGED")
