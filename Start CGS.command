#!/bin/zsh
cd "${0:A:h}" || exit 1
CGS_PYTHON_BIN="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [[ ! -x "$CGS_PYTHON_BIN" ]]; then
  CGS_PYTHON_BIN="$(command -v python3)"
fi
if [[ -z "$CGS_PYTHON_BIN" ]]; then
  print 'Python 3.10 or newer is required.'
  exit 1
fi
exec "$CGS_PYTHON_BIN" server.py --port 8765
