#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PYTHON="$VENV_DIR/bin/python"
REQUIRED_KEYS=(APIFY_TOKEN OPENROUTER_API_KEY OPENROUTER_MODEL TAVILY_API_KEY EXA_API_KEY)

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

command -v "$PYTHON_BIN" >/dev/null 2>&1 ||
    fail "Python was not found. Install Python 3.10+ and ensure '$PYTHON_BIN' is on PATH."

if [[ ! -x "$VENV_PYTHON" ]]; then
    printf 'Creating virtual environment in %s...\n' "$VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR" ||
        fail "Could not create the virtual environment."
fi

printf 'Installing Python requirements...\n'
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -r requirements.txt

if [[ ! -f .env ]]; then
    fail "Missing .env. Copy .env.example to .env and fill in the required API keys."
fi

missing_keys=()
for key in "${REQUIRED_KEYS[@]}"; do
    value="$(awk -F= -v key="$key" '
        $0 !~ /^[[:space:]]*#/ && $1 ~ "^[[:space:]]*" key "[[:space:]]*$" {
            sub(/^[^=]*=/, "", $0)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $0)
            gsub(/^"|"$/, "", $0)
            print $0
            exit
        }
    ' .env)"
    [[ -n "$value" ]] || missing_keys+=("$key")
done

if (( ${#missing_keys[@]} > 0 )); then
    printf 'ERROR: Missing or empty required .env keys: %s\n' "${missing_keys[*]}" >&2
    printf '       Copy .env.example to .env and set each value before retrying.\n' >&2
    exit 1
fi

command -v hermes >/dev/null 2>&1 ||
    fail "The 'hermes' CLI is missing. Install Hermes and ensure it is on PATH."

[[ -f pipeline.py ]] || fail "pipeline.py was not found in the repository root."

printf 'Running the CrowdWisdom pipeline...\n'
exec "$VENV_PYTHON" pipeline.py
