import ast
import os
import sys

ROOT = r"E:\matlab os"

print("=" * 75)
print("STAGE 4 ARCHITECTURE INSPECTION")
print("=" * 75)

print("\nPYTHON")
print("Executable:", sys.executable)
print("Version:", sys.version)

FILES = [
    "matlab_os/__init__.py",
    "matlab_os/intelligence/__init__.py",
    "matlab_os/intelligence/engine.py",
    "matlab_os/intelligence/model_provider.py",
    "matlab_os/intelligence/model_registry/__init__.py",
    "matlab_os/intelligence/model_registry/registry.py",
    "matlab_os/intelligence/model_registry/hardening.py",
    "matlab_os/intelligence/model_registry/runtime_adapter.py",
    "tests/intelligence/test_model_registry.py",
    "tests/intelligence/test_model_registry_hardening.py",
]

def describe_node(node, indent=""):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        try:
            signature = ast.unparse(node.args)
        except Exception:
            signature = "(signature unavailable)"
        print(indent + "METHOD:", node.name + "(" + signature + ")")
        if ast.get_docstring(node):
            print(indent + "  DOC:", ast.get_docstring(node).splitlines()[0])

    elif isinstance(node, ast.ClassDef):
        print(indent + "CLASS:", node.name)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                describe_node(child, indent + "  ")

    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        print(indent + "IMPORT:", ast.dump(node, include_attributes=False))

for relative in FILES:
    path = os.path.join(ROOT, relative)

    print("\n" + "-" * 75)
    print("FILE:", relative)

    if not os.path.isfile(path):
        print("STATUS: MISSING")
        continue

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            source = f.read()

        print("SIZE:", len(source), "characters")

        try:
            tree = ast.parse(source)
            print("SYNTAX: PASS")

            for node in tree.body:
                describe_node(node)

        except SyntaxError as exc:
            print("SYNTAX ERROR:", exc)

        if relative.endswith("__init__.py"):
            print("\n--- INITIALIZER SOURCE ---")
            print(source[:12000])

        if relative.endswith("hardening.py"):
            print("\n--- HARDENING PUBLIC METHODS ---")
            print(source[:16000])

    except Exception as exc:
        print("READ ERROR:", repr(exc))

print("\n" + "=" * 75)
print("INTELLIGENCE PACKAGE FILES")
print("=" * 75)

folder = os.path.join(ROOT, "matlab_os", "intelligence")

for current, dirs, files in os.walk(folder):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for name in sorted(files):
        path = os.path.join(current, name)
        print(os.path.relpath(path, ROOT), os.path.getsize(path), "bytes")

print("\nINSPECTION_COMPLETE")