#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 -B server.py
elif [ -x /Users/yutianya/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 ]; then
  /Users/yutianya/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -B server.py
else
  echo "需要 Python 3.9 或以上。"
  read -r
fi
