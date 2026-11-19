#!/bin/zsh
cd "$(dirname "$0")"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Virtual environment missing. Run install steps from README first."
  echo "Press Return to close."
  read
  exit 1
fi

echo "Starting Subtitle Generator..."
echo "Browser should open at http://127.0.0.1:8765"
echo "Keep this window open while you use it. Close the window to stop."
echo

exec .venv/bin/python run.py
