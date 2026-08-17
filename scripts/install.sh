#!/usr/bin/env bash
# =============================================================================
#  Mining-Dark - Cross-platform installer (Ubuntu/Debian + macOS)
#
#  What it does:
#    1. Detects OS (Linux / Darwin) and installs system prerequisites
#       (libsecp256k1, build tools, python3-venv, etc.) via apt / brew.
#    2. Creates a Python virtual environment in .venv.
#    3. Installs Mining-Dark in editable mode with `pip install -e .`.
#    4. Verifies the `mining-dark` CLI is reachable.
#
#  Usage:  bash scripts/install.sh
# =============================================================================

set -euo pipefail

# Move to project root regardless of where the user invoked us from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

VENV_DIR=".venv"

green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[1;33m%s\033[0m\n' "$*"; }
red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
cyan()   { printf '\033[0;36m%s\033[0m\n' "$*"; }
step()   { printf '\n\033[0;32m==>\033[0m %s\n' "$*"; }
info()   { printf '   \033[0;36m->\033[0m %s\n' "$*"; }
ok()     { printf '   \033[0;32m[ok]\033[0m %s\n' "$*"; }
warn()   { printf '   \033[1;33m!\033[0m %s\n' "$*"; }

# -- Detect OS ----------------------------------------------------------------
OS="$(uname -s)"

case "${OS}" in
    Linux)
        PLATFORM="linux"
        ;;
    Darwin)
        PLATFORM="macos"
        ;;
    *)
        red "Unsupported OS: ${OS}. This installer supports Linux and macOS only."
        exit 1
        ;;
esac

green "=========================================================="
green "  Mining-Dark installer  (${PLATFORM})"
green "=========================================================="

# -- [1/3] System dependencies ------------------------------------------------
step "[1/3] Installing system dependencies"

if [ "${PLATFORM}" = "linux" ]; then
    if ! command -v apt-get >/dev/null 2>&1; then
        red "apt-get not found. This script targets Debian/Ubuntu on Linux."
        red "Please install manually: python3-venv python3-dev build-essential libssl-dev libffi-dev libsecp256k1-dev pkg-config"
        exit 1
    fi

    info "Requires sudo to install apt packages."
    sudo apt-get update -q
    sudo apt-get install -y \
        python3-pip \
        python3-venv \
        python3-dev \
        build-essential \
        libssl-dev \
        libffi-dev \
        pkg-config

    if apt-cache show libsecp256k1-dev >/dev/null 2>&1; then
        sudo apt-get install -y libsecp256k1-dev
        ok "libsecp256k1-dev installed (fast path for coincurve)"
    else
        warn "libsecp256k1-dev unavailable in apt - coincurve will build its own (slower first install)"
    fi

elif [ "${PLATFORM}" = "macos" ]; then
    if ! command -v brew >/dev/null 2>&1; then
        red "Homebrew not found. Install it first: https://brew.sh"
        exit 1
    fi

    info "Installing via Homebrew..."
    brew update
    brew install python@3.12 secp256k1 openssl@3 pkg-config
    ok "System dependencies installed via brew"
fi

# -- [2/3] Python virtual environment -----------------------------------------
step "[2/3] Creating virtual environment in ${VENV_DIR}"

# Pick the best available Python: prefer 3.12, then 3.11, then 3.10.
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
        PYTHON_BIN="${candidate}"
        break
    fi
done

if [ -z "${PYTHON_BIN}" ]; then
    red "No suitable python3 found. Install Python 3.10 or newer."
    exit 1
fi

info "Using ${PYTHON_BIN} ($( "${PYTHON_BIN}" -V ))"

# A virtualenv bakes its own absolute path into the shebang of every console
# script, so one left behind after the project folder moved - onto an external
# SSD, or onto another machine - looks present but fails with
# "bad interpreter".  Testing bin/python3 is not enough: that is a symlink to
# the system interpreter and keeps working.  The shebang is what goes stale.
if [ -d "${VENV_DIR}" ]; then
    VENV_ABS="$(cd "$(dirname "${VENV_DIR}")" && pwd)/$(basename "${VENV_DIR}")"
    VENV_STALE=0

    if [ ! -f "${VENV_DIR}/bin/pip" ]; then
        VENV_STALE=1
    else
        case "$(head -1 "${VENV_DIR}/bin/pip")" in
            "#!${VENV_ABS}/"*) ;;          # shebang matches where we actually are
            *) VENV_STALE=1 ;;
        esac
    fi

    if [ "${VENV_STALE}" -eq 1 ]; then
        warn "Existing ${VENV_DIR} was built for a different path (project moved or copied)."
        warn "Rebuilding it. Your data/ and config.yaml are untouched."
        rm -rf "${VENV_DIR}"
    fi
fi

"${PYTHON_BIN}" -m venv "${VENV_DIR}"
ok "Virtualenv ready at ${VENV_DIR}"

# -- [3/3] Install Mining-Dark package ----------------------------------------
step "[3/3] Installing Mining-Dark (editable)"

"${VENV_DIR}/bin/pip" install --upgrade pip wheel setuptools
"${VENV_DIR}/bin/pip" install -e .

# The graphical dashboard is optional: a headless box (server, CI, WSL without
# an X server) should still end up with a working CLI, so a failure here warns
# instead of aborting the whole install.
info "Installing the optional graphical dashboard (dearpygui)..."
if "${VENV_DIR}/bin/pip" install -e '.[gui]'; then
    ok "GUI available - run: mining-dark gui --simulate"
else
    warn "dearpygui could not be installed; the CLI still works. Retry later with:"
    warn "    ${VENV_DIR}/bin/pip install -e '.[gui]'"
fi

info "Verifying entry point..."
if "${VENV_DIR}/bin/mining-dark" --help >/dev/null 2>&1; then
    ok "mining-dark CLI is working"
else
    warn "mining-dark CLI failed a self-check; try: source ${VENV_DIR}/bin/activate && mining-dark --help"
fi

green ""
green "============================================================"
green "  Installation complete!"
green ""
cyan  "  Activate the venv and try it out:"
echo  "      source ${VENV_DIR}/bin/activate"
echo  "      mining-dark keygen -n 3"
echo  "      mining-dark gui --simulate      # graphical dashboard, no UTXO db needed"
echo  ""
cyan  "  Or without activating:"
echo  "      ${VENV_DIR}/bin/mining-dark --help"
echo  ""
cyan  "  Next step - install Bitcoin Core to build the UTXO database:"
echo  "      bash scripts/setup_bitcoin_core.sh"
green "============================================================"
