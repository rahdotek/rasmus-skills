#!/bin/bash
# Kører check_updates.py, men kun hvis en rigtig python3 findes.
# Undgår macOS' "installer udviklerværktøjer"-popup, hvis python3 kun er en stub.
PY=$(command -v python3) || exit 0
if [ "$PY" = "/usr/bin/python3" ] && [ "$(uname)" = "Darwin" ]; then
  xcode-select -p >/dev/null 2>&1 || exit 0
fi
exec "$PY" "$(dirname "$0")/check_updates.py" 2>/dev/null
exit 0
