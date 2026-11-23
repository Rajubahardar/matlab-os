import os
from pathlib import Path

base = Path("E:/matlab os")

# Create __init__.py files
inits = {
    "matlab_os/__init__.py": '"""MATLAB OS - MATLAB Expert Agent Package"""\n__version__ = "0.1.0"',
    "matlab_os/agent/__init__.py": '"""MATLAB Agent Package"""',
    "matlab_os/integration/__init__.py": '"""Integration Package"""',
    "matlab_os/memory/__init__.py": '"""Memory Package"""',
    "matlab_os/intelligence/__init__.py": '"""Intelligence Package"""',
    "matlab_os/validation/__init__.py": '"""Validation Package"""',
}

for path_str, content in inits.items():
    path = base / path_str
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    print(f"Created: {path}")
print("Init files created")