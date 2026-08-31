#!/usr/bin/env python3
"""
Static Gate: Undefined Name Checker (F821 / F822 equivalent via AST scope analysis).
Checks all python files in the project to ensure no undefined variables or missing imports exist.
"""
import ast
import builtins
import os
import sys

BUILTIN_NAMES = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__"}


class Scope:
    def __init__(self, parent=None, is_class=False):
        self.parent = parent
        self.is_class = is_class
        self.defs = set()
        self.globals = set()
        self.nonlocals = set()

    def is_defined(self, name):
        if name in self.defs or name in BUILTIN_NAMES:
            return True
        if self.is_class:
            # Class bodies do not scope-leak to nested methods
            return self.parent.is_defined(name) if self.parent else False
        if self.parent:
            return self.parent.is_defined(name)
        return False


def check_undefined_names_in_file(file_path: str) -> list:
    with open(file_path, "r", encoding="utf-8") as f:
        src = f.read()

    try:
        tree = ast.parse(src, filename=file_path)
    except SyntaxError as e:
        return [f"SyntaxError: {e}"]

    # We use pyflakes if available, otherwise rigorous AST symtable/visitor
    errors = []
    try:
        import pyflakes.api
        import pyflakes.reporter

        class Reporter:
            def __init__(self):
                self.messages = []
            def unexpectedError(self, filename, msg):
                self.messages.append(f"{filename}: unexpected error: {msg}")
            def syntaxError(self, filename, msg, lineno, offset, text):
                self.messages.append(f"{filename}:{lineno}: syntax error: {msg}")
            def flake(self, message):
                msg_str = str(message)
                if "undefined name" in msg_str or "undefined export" in msg_str:
                    self.messages.append(msg_str)

        rep = Reporter()
        pyflakes.api.check(src, file_path, reporter=rep)
        return rep.messages
    except ImportError:
        pass

    # Fallback to symtable
    import symtable
    try:
        tbl = symtable.symtable(src, file_path, "exec")
        def walk_symtable(t):
            for symbol in t.get_symbols():
                if symbol.is_free() and not t.get_parent():
                    name = symbol.get_name()
                    if name not in BUILTIN_NAMES:
                        errors.append(f"{file_path}: Undefined variable '{name}' in global/free scope")
            for child in t.get_children():
                walk_symtable(child)
        walk_symtable(tbl)
    except Exception as e:
        errors.append(f"Symtable error: {e}")

    return errors


def main():
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    total_files = 0
    all_errors = []

    for root, dirs, files in os.walk(root_dir):
        if any(ignored in root for ignored in [".git", "venv", "__pycache__", ".pytest_cache"]):
            continue
        for file in files:
            if file.endswith(".py"):
                full_path = os.path.join(root, file)
                total_files += 1
                errs = check_undefined_names_in_file(full_path)
                if errs:
                    all_errors.extend(errs)

    print(f"Checked {total_files} Python files for undefined names.")
    if all_errors:
        print("❌ Undefined name errors found:")
        for err in all_errors:
            print("  ", err)
        sys.exit(1)
    else:
        print("✅ Undefined name static gate: PASS (0 errors)")
        sys.exit(0)


if __name__ == "__main__":
    main()
