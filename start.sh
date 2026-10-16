#!/usr/bin/env sh
# One-command start on macOS/Linux: sets up Python packages once, then opens the website.
# Needs Python 3.12 or newer. Run from anywhere: sh start.sh
set -e
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
if [ ! -d .venv ]; then
    echo "Creating the Python environment (first start only)..."
    "$PYTHON" -m venv .venv
fi
echo "Installing or checking packages..."
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r app/requirements.txt
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env - put your own ANTHROPIC_API_KEY there to enable Claude (optional)."
fi
echo "Opening the website in your browser. Press Ctrl+C to stop it."
exec .venv/bin/streamlit run app/streamlit_app.py
