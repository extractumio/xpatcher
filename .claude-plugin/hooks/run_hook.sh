#!/usr/bin/env bash
# Venv-activating wrapper for xpatcher hooks.
# Usage: run_hook.sh <hook_script.py>
#
# This ensures hooks always run with the xpatcher venv Python,
# which has pydantic, pyyaml, rich, and other dependencies available.

HOOK_DIR="$(cd "$(dirname "$0")" && pwd -P)"
XPATCHER_HOME="$(cd "$HOOK_DIR/../.." && pwd -P)"
exec "$XPATCHER_HOME/.venv/bin/python" "$HOOK_DIR/$1"
