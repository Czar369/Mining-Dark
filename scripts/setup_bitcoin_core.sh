#!/usr/bin/env bash
# =============================================================================
#  Mining-Dark - Bitcoin Core + bitcoin-utxo-dump setup (Linux + macOS)
#
#  What it does:
#    1. Downloads and installs Bitcoin Core (pruned mode) matching the host
#       OS/architecture:
#         Linux x86_64     -> x86_64-linux-gnu
#         Linux aarch64    -> aarch64-linux-gnu
#         macOS arm64      -> arm64-apple-darwin
#         macOS x86_64     -> x86_64-apple-darwin
#    2. Creates data/bitcoin-core/bitcoin.conf with pruned + RPC settings.
#    3. Installs bitcoin-utxo-dump (release binary or via Go).
#
#  The Bitcoin datadir lives INSIDE the project at data/bitcoin-core/, not the
#  default ~/.bitcoin (Linux) / ~/Library/Application Support/Bitcoin (macOS).
#
#  Usage:  bash scripts/setup_bitcoin_core.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# 31.1 is the first line that ships assumeutxo snapshot params up to height
# 935,000 (27.x only had 840,000).  Do NOT drop to 30.0 or 30.1 - both were
# withdrawn over a wallet-migration bug that could delete wallet files:
#   https://bitcoincore.org/en/2026/01/05/wallet-migration-bug/
BITCOIN_VERSION="31.1"
UTXO_DUMP_VERSION="1.1.0"

# Highest assumeutxo height Core ${BITCOIN_VERSION} knows about.  The others it
# accepts are 840000 / 880000 / 910000; the highest leaves the least history for
# the background sync to replay.  Bump this only alongside BITCOIN_VERSION - an
# older binary has no parameters for a newer height and rejects the file.
SNAPSHOT_HEIGHT="935000"
SNAPSHOT_URL="https://files-vps02.jaonoctus.dev/utxo-${SNAPSHOT_HEIGHT}.dat"

# Builder keys used to verify the release signatures.
GUIX_SIGS_TARBALL="https://codeload.github.com/bitcoin-core/guix.sigs/tar.gz/refs/heads/main"

BITCOIN_DIR="${PROJECT_ROOT}/data/bitcoin-core"
SNAPSHOT_DIR="${PROJECT_ROOT}/data/snapshots"
SNAPSHOT_FILE="${SNAPSHOT_DIR}/utxo-${SNAPSHOT_HEIGHT}.dat"
TMP="$(mktemp -d -t bitcoin-setup-XXXXXX)"
trap 'rm -rf "${TMP}"' EXIT

# -- Helpers ------------------------------------------------------------------
green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[1;33m%s\033[0m\n' "$*"; }
red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
cyan()   { printf '\033[0;36m%s\033[0m\n' "$*"; }
step()   { printf '\n\033[0;32m[%s/%s]\033[0m %s\n' "$1" "$2" "$3"; }
info()   { printf '   \033[0;36m->\033[0m %s\n' "$*"; }
ok()     { printf '   \033[0;32m[ok]\033[0m %s\n' "$*"; }
warn()   { printf '   \033[1;33m!\033[0m %s\n' "$*"; }

# -- Detect OS + architecture -------------------------------------------------
OS_RAW="$(uname -s)"
ARCH_RAW="$(uname -m)"

case "${OS_RAW}-${ARCH_RAW}" in
    Linux-x86_64)   BITCOIN_ARCH="x86_64-linux-gnu";   PLATFORM="linux";  DOWNLOADER="wget" ;;
    Linux-aarch64)  BITCOIN_ARCH="aarch64-linux-gnu";  PLATFORM="linux";  DOWNLOADER="wget" ;;
    Darwin-arm64)   BITCOIN_ARCH="arm64-apple-darwin"; PLATFORM="macos";  DOWNLOADER="curl" ;;
    Darwin-x86_64)  BITCOIN_ARCH="x86_64-apple-darwin";PLATFORM="macos";  DOWNLOADER="curl" ;;
    *)
        red "Unsupported OS/architecture combo: ${OS_RAW} / ${ARCH_RAW}"
        red "This script supports Linux x86_64 / aarch64 and macOS arm64 / x86_64."
        exit 1
        ;;
esac

# -- Preflight: download tools ------------------------------------------------
if [ "${DOWNLOADER}" = "wget" ] && ! command -v wget >/dev/null 2>&1; then
    red "wget not installed. Install it: sudo apt-get install wget"
    exit 1
fi
if [ "${DOWNLOADER}" = "curl" ] && ! command -v curl >/dev/null 2>&1; then
    red "curl not installed."
    exit 1
fi
if ! command -v shasum >/dev/null 2>&1 && ! command -v sha256sum >/dev/null 2>&1; then
    red "Neither shasum nor sha256sum is available - cannot verify checksum."
    exit 1
fi

# Small download wrapper that abstracts wget vs curl.
download() {
    local url="$1" dest="$2"
    if [ "${DOWNLOADER}" = "wget" ]; then
        wget -q --show-progress -O "${dest}" "${url}"
    else
        curl -fL --progress-bar -o "${dest}" "${url}"
    fi
}

# Size of a remote file in bytes, from the Content-Length header.  Echoes
# nothing when the server doesn't report one (chunked encoding, etc).
remote_size() {
    local url="$1"
    if [ "${DOWNLOADER}" = "wget" ]; then
        wget --spider -S "${url}" 2>&1
    else
        curl -sfIL "${url}"
    fi | awk 'tolower($1) == "content-length:" { gsub(/\r/, "", $2); n = $2 } END { if (n) print n }'
}

# Size of a local file in bytes - stat's flags differ between GNU and BSD.
local_size() {
    stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo 0
}

# Free space in KiB on the filesystem holding a path, walking up to the first
# directory that exists (the target dir may not be created yet).  -P forces the
# POSIX single-line output that GNU and BSD df share.
free_kib() {
    local path="$1"
    while [ ! -d "${path}" ] && [ "${path}" != "/" ]; do
        path="$(dirname "${path}")"
    done
    df -Pk "${path}" 2>/dev/null | awk 'NR == 2 { print $4 }'
}

# Yes/no prompt that answers "no" on its own when nothing is attached to stdin,
# so piping the script into bash can't hang forever on a question.
confirm() {
    local prompt="$1" reply
    if [ ! -t 0 ]; then
        warn "Sem terminal interativo - assumindo \"não\" para: ${prompt}"
        return 1
    fi
    read -r -p "   ${prompt} [y/N] " reply
    case "${reply}" in
        [Yy]*) return 0 ;;
        *)     return 1 ;;
    esac
}

# Resumable download for multi-GB files, verified by length.
#
# A dropped connection can leave curl/wget exiting 0 with a short file, and a
# truncated UTXO snapshot fails deep inside `loadtxoutset` after the node has
# already chewed through part of it.  So we re-run the transfer (resuming from
# the byte we reached) until the local size matches Content-Length, rather than
# trusting a single exit code.
download_resumable() {
    local url="$1" dest="$2" attempts="${3:-6}"
    local expected have i

    # `|| true` so a server that refuses HEAD falls through to the no-size
    # branch below instead of killing the script via set -e / pipefail.
    expected="$(remote_size "${url}" || true)"
    if [ -z "${expected}" ]; then
        warn "Servidor não informou Content-Length - não dá para verificar o tamanho."
    else
        info "Tamanho esperado: ${expected} bytes"
    fi

    for i in $(seq 1 "${attempts}"); do
        have="$(local_size "${dest}")"
        if [ -n "${expected}" ] && [ "${have}" = "${expected}" ]; then
            ok "Download completo e verificado (${have} bytes)"
            return 0
        fi
        [ "${i}" -gt 1 ] && warn "Tentativa ${i}/${attempts} - retomando de ${have} bytes..."

        if [ "${DOWNLOADER}" = "wget" ]; then
            wget -c -q --show-progress -O "${dest}" "${url}" || true
        else
            curl -fL -C - --retry 5 --retry-delay 5 --progress-bar -o "${dest}" "${url}" || true
        fi
    done

    have="$(local_size "${dest}")"
    if [ -z "${expected}" ]; then
        warn "Baixado (${have} bytes), sem verificação de tamanho possível."
        return 0
    fi
    if [ "${have}" = "${expected}" ]; then
        ok "Download completo e verificado (${have} bytes)"
        return 0
    fi

    red "Download incompleto após ${attempts} tentativas: ${have} de ${expected} bytes."
    red "Rode o script de novo - o download retoma de onde parou."
    return 1
}

# Shasum wrapper - macOS ships `shasum -a 256`, Linux ships `sha256sum`.
sha256_check() {
    local sums_file="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum --check --ignore-missing "${sums_file}"
    else
        # macOS: emulate --ignore-missing by piping only lines whose file exists.
        while read -r sum name; do
            [ -f "${name}" ] || continue
            printf '%s  %s\n' "${sum}" "${name}" | shasum -a 256 --check
        done < "${sums_file}"
    fi
}

# Verify SHA256SUMS.asc against the Bitcoin Core builder keys.
#
# The SHA256 check alone only proves the tarball matches the sums file we
# downloaded over TLS.  The detached signatures prove independent builders
# reproduced the same binaries, which is the property that actually matters.
# Degrades to a loud warning when gpg or the keys aren't reachable, rather than
# blocking an install on a machine without gpg.
gpg_verify() {
    local sums="$1" asc="$2"

    if ! command -v gpg >/dev/null 2>&1; then
        warn "gpg not installed - skipping signature verification."
        warn "Install it (apt-get install gnupg / brew install gnupg) for a stronger check."
        return 0
    fi

    info "Fetching Bitcoin Core builder keys..."
    local keyring="${TMP}/gnupg"
    mkdir -p "${keyring}"; chmod 700 "${keyring}"

    if ! download "${GUIX_SIGS_TARBALL}" "${TMP}/guix.sigs.tar.gz" 2>/dev/null; then
        warn "Could not fetch builder keys - skipping signature verification."
        return 0
    fi
    tar -xzf "${TMP}/guix.sigs.tar.gz" -C "${TMP}" 2>/dev/null || {
        warn "Could not unpack builder keys - skipping signature verification."
        return 0
    }

    local keydir
    keydir="$(find "${TMP}" -type d -name builder-keys -print -quit)"
    if [ -z "${keydir}" ]; then
        warn "builder-keys/ not found in guix.sigs - skipping signature verification."
        return 0
    fi

    # shellcheck disable=SC2086
    find "${keydir}" -name '*.gpg' -exec \
        gpg --homedir "${keyring}" --quiet --import {} + 2>/dev/null || true

    info "Verifying signatures on SHA256SUMS..."
    local out good
    out="$(gpg --homedir "${keyring}" --status-fd 1 --verify "${asc}" "${sums}" 2>/dev/null || true)"
    good="$(printf '%s\n' "${out}" | grep -c '^\[GNUPG:\] GOODSIG' || true)"

    if [ "${good}" -eq 0 ]; then
        red "No valid signature on SHA256SUMS - refusing to install."
        red "Do not use these binaries. Re-download from https://bitcoincore.org/en/download/"
        exit 1
    fi

    ok "SHA256SUMS signed by ${good} known builder(s)"
}

green "=========================================================="
green "  Bitcoin Core + bitcoin-utxo-dump setup  (${PLATFORM})"
green "=========================================================="
info "OS/Arch          : ${OS_RAW} ${ARCH_RAW}"
info "Bitcoin target   : ${BITCOIN_ARCH}"
info "Bitcoin datadir  : ${BITCOIN_DIR}"

# -- Skip if already installed ------------------------------------------------
SKIP_BITCOIN=""
if command -v bitcoind >/dev/null 2>&1; then
    INSTALLED_VER=$(bitcoind --version | head -1 | awk '{print $NF}' | tr -d 'v')
    warn "Bitcoin Core ${INSTALLED_VER} already installed (target: ${BITCOIN_VERSION})."

    # Swapping the binary under a live daemon leaves it running the old code
    # until restarted, which silently defeats an upgrade done for assumeutxo.
    if [ -f "${BITCOIN_DIR}/bitcoind.pid" ] && kill -0 "$(cat "${BITCOIN_DIR}/bitcoind.pid")" 2>/dev/null; then
        warn "bitcoind is RUNNING. Stop it first so the new binary takes effect:"
        echo  "       mining-dark node stop"
    fi

    confirm "Install ${BITCOIN_VERSION} over it?" || SKIP_BITCOIN=1
fi

# -- [1/3] Bitcoin Core -------------------------------------------------------
if [ -z "${SKIP_BITCOIN}" ]; then
    step 1 4 "Downloading Bitcoin Core ${BITCOIN_VERSION}"

    TARBALL="bitcoin-${BITCOIN_VERSION}-${BITCOIN_ARCH}.tar.gz"
    BASE_URL="https://bitcoincore.org/bin/bitcoin-core-${BITCOIN_VERSION}"
    URL="${BASE_URL}/${TARBALL}"
    SUMS_URL="${BASE_URL}/SHA256SUMS"
    SIGS_URL="${BASE_URL}/SHA256SUMS.asc"

    info "URL: ${URL}"
    download "${URL}" "${TMP}/${TARBALL}"
    download "${SUMS_URL}" "${TMP}/SHA256SUMS"
    download "${SIGS_URL}" "${TMP}/SHA256SUMS.asc"

    gpg_verify "${TMP}/SHA256SUMS" "${TMP}/SHA256SUMS.asc"

    info "Verifying SHA256..."
    ( cd "${TMP}" && sha256_check "SHA256SUMS" )
    ok "Checksum verified"

    info "Extracting..."
    tar -xzf "${TMP}/${TARBALL}" -C "${TMP}"

    info "Installing bitcoind and bitcoin-cli to /usr/local/bin (needs sudo)..."
    if [ "${PLATFORM}" = "linux" ]; then
        sudo install -m 0755 -o root -g root \
            -t /usr/local/bin \
            "${TMP}/bitcoin-${BITCOIN_VERSION}/bin/bitcoind" \
            "${TMP}/bitcoin-${BITCOIN_VERSION}/bin/bitcoin-cli"
    else
        # macOS `install` doesn't accept -o root -g root without changing ownership.
        sudo install -m 0755 \
            "${TMP}/bitcoin-${BITCOIN_VERSION}/bin/bitcoind" /usr/local/bin/
        sudo install -m 0755 \
            "${TMP}/bitcoin-${BITCOIN_VERSION}/bin/bitcoin-cli" /usr/local/bin/
    fi
    ok "bitcoind and bitcoin-cli installed"
fi

# -- [2/3] bitcoin.conf inside project ----------------------------------------
step 2 4 "Configuring Bitcoin Core (pruned) at ${BITCOIN_DIR}"

mkdir -p "${BITCOIN_DIR}"

if [ -f "${BITCOIN_DIR}/bitcoin.conf" ]; then
    warn "bitcoin.conf already exists - backing up to bitcoin.conf.bak"
    cp "${BITCOIN_DIR}/bitcoin.conf" "${BITCOIN_DIR}/bitcoin.conf.bak"
fi

if command -v openssl >/dev/null 2>&1; then
    RPC_PASS="$(openssl rand -hex 20)"
else
    # POSIX fallback
    RPC_PASS="$(head -c 20 /dev/urandom | od -A n -t x1 | tr -d ' \n')"
fi

cat > "${BITCOIN_DIR}/bitcoin.conf" <<EOF
# =============================================================================
#  Bitcoin Core - Pruned mode for Mining-Dark UTXO scanner
#  Generated by scripts/setup_bitcoin_core.sh
#  Datadir: ${BITCOIN_DIR}
# =============================================================================

# Pruned: keep only recent blocks; the full UTXO set is always preserved.
#
# 20 GB, not the 2 GB this used to say.  An assumeutxo background sync makes
# the node hold two ranges at once - recent blocks at the tip, and the old
# blocks it is fetching to validate from genesis - and 2 GB could not keep the
# undo data for both.  Core then deletes the tip's undo files, its startup
# check cannot disconnect the last blocks, and it reports
#   "Corrupted block database detected. Please restart with -reindex"
# on a datadir that is perfectly intact.  Following that advice on a pruned
# node destroys days of syncing to rebuild from blocks that were pruned away.
prune=20000

# Startup verification only goes as deep as a pruned node can support.  Levels
# 3 and 4 replay the last blocks backwards out of the undo files, which pruning
# is entitled to delete at any moment; level 1 still reads and validates every
# block it checks.  Without this the failure above can return whenever pruning
# lands badly.
checklevel=1

# No transaction index (saves ~60 GB).
txindex=0

# RPC - required for bitcoin-cli and mining-dark utxo update.
server=1
daemon=1
rpcuser=bitcoinrpc
rpcpassword=${RPC_PASS}
rpcallowip=127.0.0.1

# Performance tuning
dbcache=512
maxmempool=100
EOF

ok "bitcoin.conf created"

cat > "${BITCOIN_DIR}/rpc_credentials" <<EOF
rpcuser=bitcoinrpc
rpcpassword=${RPC_PASS}
EOF
chmod 600 "${BITCOIN_DIR}/rpc_credentials"
ok "RPC credentials saved to ${BITCOIN_DIR}/rpc_credentials (mode 600)"

# -- [3/3] bitcoin-utxo-dump --------------------------------------------------
step 3 4 "Installing bitcoin-utxo-dump ${UTXO_DUMP_VERSION}"

case "${PLATFORM}-${ARCH_RAW}" in
    linux-x86_64)   UTXO_DUMP_ASSET="bitcoin-utxo-dump-linux-amd64" ;;
    linux-aarch64)  UTXO_DUMP_ASSET="bitcoin-utxo-dump-linux-arm64" ;;
    macos-arm64)    UTXO_DUMP_ASSET="bitcoin-utxo-dump-darwin-arm64" ;;
    macos-x86_64)   UTXO_DUMP_ASSET="bitcoin-utxo-dump-darwin-amd64" ;;
esac

UTXO_DUMP_URL="https://github.com/in3rsha/bitcoin-utxo-dump/releases/download/v${UTXO_DUMP_VERSION}/${UTXO_DUMP_ASSET}"

if download "${UTXO_DUMP_URL}" "${TMP}/bitcoin-utxo-dump" 2>/dev/null && [ -s "${TMP}/bitcoin-utxo-dump" ]; then
    sudo install -m 0755 "${TMP}/bitcoin-utxo-dump" /usr/local/bin/bitcoin-utxo-dump
    ok "bitcoin-utxo-dump installed to /usr/local/bin/"
elif command -v go >/dev/null 2>&1; then
    info "Release binary unavailable - building from source with Go..."
    # Confine every Go artefact to ${TMP} so we don't pollute $HOME with a
    # ~/go/ tree.  ${TMP} is wiped by the EXIT trap after this script ends.
    export GOPATH="${TMP}/gopath"
    export GOBIN="${GOPATH}/bin"
    export GOMODCACHE="${GOPATH}/pkg/mod"
    export GOCACHE="${TMP}/gocache"
    mkdir -p "${GOBIN}" "${GOMODCACHE}" "${GOCACHE}"
    go install github.com/in3rsha/bitcoin-utxo-dump@latest
    sudo install -m 0755 "${GOBIN}/bitcoin-utxo-dump" /usr/local/bin/
    ok "bitcoin-utxo-dump built and installed via Go (build files cleaned up)"
else
    warn "Could not fetch a bitcoin-utxo-dump binary automatically."
    warn "Install Go and re-run this script, or install manually:"
    echo  "     ${UTXO_DUMP_URL}"
    echo  "     sudo install -m 0755 <download> /usr/local/bin/bitcoin-utxo-dump"
fi

# -- [4/4] assumeutxo snapshot (optional) -------------------------------------
step 4 4 "Baixando snapshot assumeutxo (altura ${SNAPSHOT_HEIGHT}, opcional)"

# ~9.4 GB for the .dat, plus room for the two chainstates the background sync
# keeps alive.  Warn instead of aborting: the user may be pointing data/ at
# another disk, or plan to delete the .dat right after loading it.
SNAPSHOT_KIB_NEEDED=$((10 * 1024 * 1024))

# loaded = já carregado no datadir | ready = .dat íntegro em disco | none
SNAPSHOT_STATE="none"
SNAPSHOT_ABORTED=0

# `loadtxoutset` writes base_blockhash only when it finishes, so an interrupted
# load leaves the directory behind without the marker.  Core then ignores it
# ("snapshot chainstate dir is malformed") and silently falls back to a full IBD
# from genesis.  Keying "loaded" on the directory instead of the marker would
# skip the download the user still needs AND tell them to delete the .dat that
# is their only way to retry - so the debris is reported, never mistaken for a
# finished load, and it does not short-circuit the download logic below.
if [ ! -f "${BITCOIN_DIR}/chainstate_snapshot/base_blockhash" ] \
   && [ -d "${BITCOIN_DIR}/chainstate_snapshot" ]; then
    SNAPSHOT_ABORTED=1
    warn "Existe um chainstate_snapshot/ incompleto de um load interrompido."
    info "O Core o ignora e sincroniza por IBD normal - é só espaço ocupado."
    info "NÃO apague o .dat: ele ainda não foi carregado."
fi

if [ -f "${BITCOIN_DIR}/chainstate_snapshot/base_blockhash" ]; then
    # Core only supports one snapshot load per datadir; a second loadtxoutset is
    # rejected, so neither downloading nor re-loading buys anything here.  This
    # check comes first precisely because it overrides the disk state below.
    SNAPSHOT_STATE="loaded"
    ok "Um snapshot já foi carregado neste datadir - nada a baixar."
    if [ -f "${SNAPSHOT_FILE}" ]; then
        info "O .dat já cumpriu seu papel e pode ser apagado:"
        echo  "       rm ${SNAPSHOT_FILE}"
    fi
elif [ -f "${SNAPSHOT_FILE}" ]; then
    HAVE_BYTES="$(local_size "${SNAPSHOT_FILE}")"
    WANT_BYTES="$(remote_size "${SNAPSHOT_URL}" || true)"

    if [ -n "${WANT_BYTES}" ] && [ "${HAVE_BYTES}" = "${WANT_BYTES}" ]; then
        SNAPSHOT_STATE="ready"
        ok "Snapshot já baixado e íntegro: ${SNAPSHOT_FILE}"
    else
        warn "Snapshot existe mas está incompleto (${HAVE_BYTES} bytes)."
        if confirm "Retomar o download de onde parou?"; then
            download_resumable "${SNAPSHOT_URL}" "${SNAPSHOT_FILE}" && SNAPSHOT_STATE="ready"
        else
            info "Pulado. Rode o script de novo quando quiser retomar."
        fi
    fi
else
    info "O snapshot pula a maior parte do IBD: horas em vez de 2-5 dias."
    info "Arquivo: ${SNAPSHOT_URL}"
    info "Tamanho: ~9,4 GB  (o download é retomável - pode interromper)"

    FREE_KIB="$(free_kib "${SNAPSHOT_DIR}")"
    if [ -n "${FREE_KIB}" ]; then
        info "Espaço livre em $(dirname "${SNAPSHOT_DIR}"): $((FREE_KIB / 1024 / 1024)) GB"
        if [ "${FREE_KIB}" -lt "${SNAPSHOT_KIB_NEEDED}" ]; then
            warn "Menos de 10 GB livres - o download provavelmente não cabe."
        fi
    fi

    if confirm "Baixar o snapshot agora?"; then
        mkdir -p "${SNAPSHOT_DIR}"
        download_resumable "${SNAPSHOT_URL}" "${SNAPSHOT_FILE}" && SNAPSHOT_STATE="ready"
    else
        info "Pulado - o nó vai sincronizar do gênesis (2-5 dias)."
        info "Dá para baixar depois rodando este script de novo."
    fi
fi

# -- Summary ------------------------------------------------------------------
green ""
green "============================================================"
green "  Bitcoin Core setup complete!"
green ""
# The number of next steps depends on the snapshot state, so number them from a
# counter instead of hardcoding - a summary that skips from "2." to "4." reads
# like the user missed something.
N=0
next() { N=$((N + 1)); cyan "  ${N}. $*"; }

next "Start the node (uses project-local datadir):"
echo  "      mining-dark node start"
echo  "      # equivalente cru: bitcoind -datadir=${BITCOIN_DIR}"
echo  ""

if [ "${SNAPSHOT_ABORTED}" = "1" ]; then
    next "Limpe o chainstate_snapshot/ incompleto antes de carregar de novo:"
    echo  "      mining-dark node stop"
    echo  "      rm -rf ${BITCOIN_DIR}/chainstate_snapshot"
    echo  ""
fi

case "${SNAPSHOT_STATE}" in
    loaded)
        next "O snapshot já está carregado - só acompanhe o background sync:"
        echo  "      mining-dark node status"
        ;;
    ready)
        next "Load the snapshot (blocks for a while - don't interrupt):"
        echo  "      mining-dark node snapshot ${SNAPSHOT_FILE}"
        echo  ""
        next "Watch it catch up:"
        echo  "      mining-dark node status"
        ;;
    *)
        next "Watch sync progress (2-5 days from scratch):"
        echo  "      mining-dark node status"
        echo  ""
        next "Or skip most of it with an assumeutxo snapshot:"
        echo  "      bash scripts/setup_bitcoin_core.sh   # baixa o .dat"
        echo  "      mining-dark node snapshot ${SNAPSHOT_FILE}"
        echo  "      # Core ${BITCOIN_VERSION} accepts heights 840000 / 880000 / 910000 / 935000"
        ;;
esac

echo  ""
next "When verificationprogress >= 0.9999, build the UTXO db:"
echo  "      mining-dark utxo update"
echo  ""
yellow "  Disk space: ~10-15 GB normally; ~35 GB peak while a snapshot"
yellow "  background sync keeps two chainstates alive."
green "============================================================"
