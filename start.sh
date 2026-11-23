#!/usr/bin/env bash
# Local AI OS V1 - Startup Script
# Reliable root-level launcher for Git Bash

set -euo pipefail

# Resolve repository root reliably, regardless of invocation directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Activate the existing project virtual environment if available
VENV_PATH="$(cd "$REPO_ROOT/.venv" && pwd)"
if [ -f "$VENV_PATH/Scripts/activate" ]; then
    source "$VENV_PATH/Scripts/activate"
    echo "Virtual environment activated."
else
    echo "Warning: No virtual environment found at $VENV_PATH"
fi

# Check that the required Python environment and dependencies are available
if ! command -v python &>/dev/null; then
    echo "Error: Python not found. Please ensure Python is installed and available."
    exit 1
fi

# Check Python version
PYTHON_VERSION=$(python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
REQ_VERSION="3.11"
if [ "$(printf '%s\n' "$REQ_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQ_VERSION" ]; then
    echo "Error: Python $PYTHON_VERSION detected, but version >= $REQ_VERSION is required."
    exit 1
fi

# Check for required dependencies
if ! python -c "import sqlite3" &>/dev/null; then
    echo "Error: sqlite3 Python module not available."
    exit 1
fi

# Launch the existing V1 CLI
echo "Starting Local AI OS V1 CLI..."
echo "=========================================="

# Show interactive menu
while true; do
    echo ""
    echo "Local AI OS V1 - Menu"
    echo "1. Submit a task"
    echo "2. Check task status"
    echo "3. Retrieve task results"
    echo "4. View task history"
    echo "5. Exit"
    echo "=========================================="
    read -rp "Enter choice [1-5]: " choice

    case "$choice" in
        1)
            echo "Submit a task (enter task description):"
            read -rp "Task description: " task_desc
            if [ -z "$task_desc" ]; then
                echo "Error: Task description cannot be empty."
                continue
            fi
            python -m matlab_os.cli submit --description "$task_desc"
            ;;
        2)
            echo "Check task status:"
            python -m matlab_os.cli status
            ;;
        3)
            echo "Retrieve task results:"
            python -m matlab_os.cli retrieve
            ;;
        4)
            echo "View task history:"
            python -m matlab_os.cli history
            ;;
        5)
            echo "Exiting Local AI OS V1. Goodbye!"
            exit 0
            ;;
        *)
            echo "Error: Invalid choice. Please enter 1-5."
            ;;
    esac
done