#!/bin/bash
set -e

# Ensure we are in the Magistrate root directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "Initializing Magistrate Presidential Secure Console..."

# Source the backend virtual environment where the dependencies live
if [ -f "backend/venv/bin/activate" ]; then
    source backend/venv/bin/activate
else
    echo "ERROR: Virtual environment not found at backend/venv/bin/activate."
    echo "Please run setup_server.sh first to build the dependencies."
    exit 1
fi

# Run the Python CLI dashboard
python cli/magistrate "$@"
