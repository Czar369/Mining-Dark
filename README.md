# Mining-Dark - Bitcoin Balance Scanner Pro

> **An educational research project on Bitcoin cryptography.**
> **by: Czar**

Cross-platform: **Ubuntu / Debian Linux** and **macOS** (Intel + Apple Silicon).

> The interface ships in **English and Portuguese** - window and command line
> alike. See [Languages](#languages).

---

## Features

- Key generation on the **secp256k1** elliptic curve
- **6 address formats** derived from the same private key:
  - `P2PKH` - compressed legacy `1...`
  - `P2PKH uncompressed` - uncompressed legacy `1...` *(Satoshi era / early blocks)*
  - `P2SH-P2WPKH` - nested SegWit `3...`
  - `P2WPKH` - native SegWit `bc1q...`
  - `P2WSH` - witness script hash `bc1q...`
  - `P2TR` - Taproot `bc1p...`
- **Local UTXO database** (SQLite) - instant lookups (~0.1 ms per address, no internet)
- **Random** mode (random keys) and **HD Wallet** mode (BIP39 seed phrases
  derived through BIP32/44/49/84/86)
- Real-time terminal dashboard with live statistics
- **Optional graphical panel** (Dear PyGui) - cyberpunk HUD with a worker list,
  activity radar, streaming log and three neon themes
- Everything configurable from the window: paths, scanner, Bitcoin Core node,
  UTXO database, theme and language (**Portuguese or English**, and the same
  setting drives the command line)
- Browser for found wallets, private keys masked by default
- Automatic saving to `.txt`, `.json` and `summary.csv`
- Private keys **never** appear in the logs

---

## Project Layout

The whole architecture is **project-local**: code in `src/`, bulk data in `data/`.
Nothing is written to `~/.bitcoin`, `~/Library/Application Support/`, and so on.

```
Mining-Dark/
├── src/mining_dark/          # Python package
│   ├── core/                 # keys, addresses, hashes
│   ├── generators/           # random + HD wallet
│   ├── checkers/             # local UTXO lookups
│   ├── ui/                   # Rich dashboard + interactive menu (terminal)
│   ├── gui/                  # Dear PyGui graphical panel (optional)
│   │   ├── theme.py          # neon palettes, mono fonts, DPG themes
│   │   ├── i18n.py           # pt/en interface catalogue
│   │   ├── state.py          # thread-safe EventBus + UIState
│   │   ├── services.py       # node, UTXO database and file opening (threaded)
│   │   ├── panels/           # header, workers, activity, log, footer,
│   │   │                     # settings, found wallets
│   │   ├── backends/         # simulated (demo) + live (real scanner)
│   │   └── app.py            # window, layout and render loop
│   ├── utils/                # logger, file manager, SQLite
│   ├── config/               # Pydantic settings
│   ├── paths.py              # single point of path resolution
│   ├── bitcoin_node.py       # bitcoind/bitcoin-cli wrapper (automatic datadir)
│   ├── utxo_updater.py       # imports the UTXO set into SQLite
│   ├── doctor.py             # audits the installation (mining-dark doctor)
│   └── cli.py                # entry point (mining-dark)
├── scripts/
│   ├── install.sh            # cross-platform installer
│   └── setup_bitcoin_core.sh # Bitcoin Core + bitcoin-utxo-dump
├── data/                     # runtime data
│   ├── bitcoin-core/         # blockchain + chainstate (~15 GB once synced)
│   ├── snapshots/            # assumeutxo .dat files (~9.4 GB each)
│   ├── utxo/utxo.db          # SQLite database (~3 GB after the first build)
│   ├── logs/                 # rotating logs
│   └── found_wallets/        # found wallets
├── tests/                    # pytest
├── config.yaml               # user configuration
├── pyproject.toml            # metadata + dependencies
├── main.py                   # shim for legacy invocation (python3 main.py ...)
└── README.md
```

> To move `data/` to another disk, export
> `MINING_DARK_DATA_DIR=/absolute/path/` and every subpath follows - no code
> changes.

### Moving the project to another machine or an external SSD

Paths are **derived, not recorded**: `paths.py` walks up from its own file until
it finds the folder holding `pyproject.toml` + `src/mining_dark/`, and everything
else hangs off that. The four path keys in `config.yaml` are left empty (`''`),
which means exactly "work it out from the project folder".

What that buys you: **copy the whole folder anywhere and every path re-adjusts
itself** - including the ones the Paths tab shows, which are read live each frame.

One thing does **not** come along: `.venv/`. A virtualenv records its own
absolute path in every script's shebang, so after a move `mining-dark` fails with
*bad interpreter*. Run the installer again:

```bash
bash scripts/install.sh
```

It detects the stale `.venv/` (by comparing the shebang against where the project
now lives), rebuilds only that, and touches neither `data/` nor `config.yaml`.

Two cases that still need your attention:

- If you filled in a path key in `config.yaml` with an **absolute** path, it does
  not follow the move - by definition, you asked for a fixed location.
- `MINING_DARK_DATA_DIR` is absolute too. If the external SSD mounts somewhere
  else on the other machine (`/media/you/SSD` vs `/Volumes/SSD`), update it.

---

## Installation

```bash
cd ~/Projects/Mining-Dark
bash scripts/install.sh
```

The script detects your operating system (Linux or macOS) and:

- **Ubuntu/Debian**: installs `python3-venv`, `build-essential`, `libsecp256k1-dev`, etc. via `apt`
- **macOS**: installs `python@3.12`, `secp256k1`, `openssl@3` via `brew`

It then creates `.venv/` and installs Mining-Dark in editable mode
(`pip install -e .`), exposing the **`mining-dark`** command inside the venv.

<details>
<summary>Manual installation</summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

System requirements:

**Ubuntu/Debian**
```bash
sudo apt-get install python3-venv python3-dev build-essential \
    libssl-dev libffi-dev libsecp256k1-dev pkg-config
```

**macOS**
```bash
brew install python@3.12 secp256k1 openssl@3 pkg-config
```
</details>

---

## Local UTXO Database

The scanner needs a SQLite database holding **every Bitcoin address with a
balance**. It is built from Bitcoin Core's own UTXO set.

### The order, and why it is that one

Four steps, and the order between them is not a preference - each one is only
possible once the previous one is done:

```
  1. Install Bitcoin Core             bitcoind, bitcoin-cli, bitcoin-utxo-dump
            |
            v
  2. Download the snapshot (~9.4 GB)  file in data/snapshots/, node stopped
            |
            v
  3. Start the node -> headers -> load the snapshot
            |                          (loadtxoutset)
            v
  4. Build the UTXO database          chainstate -> CSV -> SQLite
```

| Step                     | Only possible once                                        |
|--------------------------|-----------------------------------------------------------|
| 2. Download the snapshot | always - it is just an HTTP download, no node needed      |
| 3a. Start the node       | Bitcoin Core is installed                                 |
| 3b. Load the snapshot    | the node is up and its headers are past 935,000           |
| 4. Build the database    | a complete chainstate exists at the tip                   |

**Why loading needs headers.** Core anchors the snapshot to a header it already
knows. Asking before that fails with an error that reads like a corrupt file and
sends you hunting the wrong problem - which is why the panel holds the button
until the headers arrive rather than letting the click fail.

**Why the database needs the tip.** `bitcoin-utxo-dump` reads LevelDB straight
off disk. A node that is behind produces a silently incomplete database: the dump
exits successfully and nothing downstream can tell that apart from a correct one.

**Why the snapshot comes before starting the node.** Starting first breaks
nothing, but the node begins its initial block download immediately - and the
snapshot exists precisely to skip that. You would spend bandwidth and hours on
blocks `loadtxoutset` is about to discard. Download the file first, then start
the node: it syncs only the headers (minutes), loads the snapshot, and is usable.

The snapshot is **optional**. Without it, step 3 becomes an ordinary 2-to-5-day
sync and step 4 waits for it. The end result is identical.

**In the graphical panel** these steps are the three numbered sections of the
**Node & UTXO** tab, in the same order. Each section has a single button whose
label changes with whatever is left to do, and which stays disabled - saying what
is missing - until the previous step is ready. You do not need to know any of
this table to drive the panel: it only ever offers what will work.

### 1. Install Bitcoin Core (once)

```bash
bash scripts/setup_bitcoin_core.sh
```

The script:

- Detects OS + architecture (`x86_64-linux-gnu`, `aarch64-linux-gnu`, `arm64-apple-darwin`, `x86_64-apple-darwin`)
- Downloads official **Bitcoin Core 31.1** and checks the SHA256
- Verifies the builders' GPG signatures (`SHA256SUMS.asc` against the keys in the
  `guix.sigs` repository) and **aborts** if no valid signature is found
- Installs `bitcoind` and `bitcoin-cli` into `/usr/local/bin`
- Creates `data/bitcoin-core/bitcoin.conf` in **pruned** mode (20 GB of blocks +
  the whole UTXO set)
- Generates random RPC credentials in `data/bitcoin-core/rpc_credentials` (mode 600)
- Installs `bitcoin-utxo-dump` (release binary or via `go install`)
- **Asks whether to download the assumeutxo snapshot** (~9.4 GB, section 2). The
  download is resumable and size-checked: if the connection drops, run the script
  again and it picks up where it left off. If a snapshot is already loaded, it
  detects that and downloads nothing.

Check everything landed:

```bash
which bitcoind bitcoin-cli bitcoin-utxo-dump
bitcoind --version | head -1        # must say v31.1.0 or newer
```

> **Why 31.1 and not any version?** That is where Core starts shipping assumeutxo
> snapshot parameters up to height 935,000 (the 27.x series only went to
> 840,000). Older versions reject the snapshot from section 2.
>
> **Do not use 30.0 or 30.1.** Both were withdrawn over a bug where a failed
> wallet migration could delete files from the wallets directory. Fixed in 30.2.

### 2. Download the assumeutxo snapshot (optional, but recommended)

Instead of validating 900,000 blocks in order, Core loads a ready-made picture of
the UTXO set at height 935,000 and becomes usable in **hours instead of days**.
It then revalidates the whole history from genesis in the background, on its own,
while you are already using the database.

Download it **before starting the node**: starting first makes bitcoind begin its
initial block download immediately, spending bandwidth on blocks the snapshot is
about to discard.

If you answered `y` at step 4 of `setup_bitcoin_core.sh`, the file is already in
`data/snapshots/` and you can skip straight to section 3.

```bash
mining-dark node download-snapshot   # ~9.4 GB, resumable
```

The download **resumes where it stopped**: interrupting is safe, nothing already
fetched is lost, and running the command again continues. The same is true of the
graphical panel, of `bash scripts/setup_bitcoin_core.sh`, and of a hand-rolled
`curl`:

```bash
mkdir -p data/snapshots
curl -fL -C - --retry 10 \
  -o data/snapshots/utxo-935000.dat \
  https://files-vps02.jaonoctus.dev/utxo-935000.dat
```

**You do not have to trust whoever hosts the file.** Bitcoin Core compares the
snapshot against a hash compiled into its own binary; a single altered byte makes
`loadtxoutset` reject the whole thing. That is why the Core version matters: only
a build carrying the height-935,000 parameter accepts this `.dat`.

Heights Core 31.1 accepts: **840,000**, **880,000**, **910,000** and **935,000**.
Always prefer the highest - it leaves the background sync less history to replay.

### 3. Start the node and load the snapshot

Bitcoin Core runs with its **datadir inside the project** - never in
`~/.bitcoin`. Use the `mining-dark node` wrapper so you never type `-datadir=...`:

```bash
source .venv/bin/activate        # the mining-dark command lives inside the venv

mining-dark node start           # start bitcoind
mining-dark node status          # sync progress
mining-dark node stop            # stop bitcoind safely
```

> The venv is needed because `mining-dark` is installed into `.venv/bin/`.
> `bitcoind` itself lives in `/usr/local/bin` and runs in the background
> (`daemon=1`), so after `node start` you can deactivate the venv or close the
> terminal and the node keeps syncing. To call it without activating, use the
> full path: `.venv/bin/mining-dark node status`.

Once up, the node goes through **two phases that never advance together** - the
panel shows a bar for each:

| Phase       | What happens                                    | Duration                         |
|-------------|-------------------------------------------------|----------------------------------|
| 2.1 Headers | Downloads the header chain, no block content    | minutes                          |
| 2.2 Blocks  | Downloads and validates the blocks themselves   | days, or hours with the snapshot |

`loadtxoutset` only works once the **headers** are past height 935,000 - Core has
to know the header it will anchor the snapshot to. Asking earlier fails with an
error that reads like a corrupt file and sends you hunting the wrong problem.
There is no need to wait for phase 2.2.

```bash
mining-dark node snapshot            # loads what section 2 downloaded
```

> In the graphical panel this is a single button. With the node stopped it reads
> **START NODE AND LOAD**: it starts bitcoind, waits for the headers and fires
> the load by itself. There is no order to memorise and no right moment to click.

The command blocks for tens of minutes to a few hours - that is normal, do not
interrupt it. When it finishes:

```bash
mining-dark node status      # shows both chainstates while the sync runs
```

> **Disk space:** during the background sync Core keeps **two** chainstates alive
> at once. Budget ~35 GB at peak against ~15 GB in steady state. The `.dat` can
> be deleted once the snapshot has been loaded.

Or, if you prefer raw `bitcoin-cli` (with the datadir forwarded automatically):

```bash
mining-dark node cli getblockchaininfo
mining-dark node cli getpeerinfo
mining-dark node cli -help
```

> **After powering the machine down**, run `mining-dark node start` again - the
> node continues from where it stopped. If the shutdown was abrupt and it
> complains about corruption, read the troubleshooting section below before
> reaching for `--reindex`.

**Without the snapshot**, this section is just `node start`: syncing from scratch
takes **2-5 days**. To follow it live:

```bash
watch -n 30 'mining-dark node status'
```

### 4. Build the UTXO database

Once `Sync >= 99.99%`:

> If you used the snapshot from section 2, the UTXO set is already complete at
> height 935,000 the moment `loadtxoutset` finishes - **there is no need to wait
> for the background sync** before running `utxo update`. The command says so on
> screen when the data comes from a snapshot still being validated.

```bash
mining-dark utxo update          # builds the SQLite from the UTXO set
mining-dark utxo update --force  # rebuild even if it is current
mining-dark utxo status          # database health
```

The command **stops the node by itself** before exporting, and starts it again
afterwards if it was up. That is not convenience: `bitcoin-utxo-dump` reads
LevelDB straight off disk, and a running node only flushes its coins cache when
that cache fills. With bitcoind up, the directory can lag the tip by an unbounded
amount - and the dump still exits successfully. The shutdown is what forces the
flush.

It takes tens of minutes to a few hours. Detailed progress goes to the terminal;
the graphical panel shows the phases and the file sizes.

**Disk space during a rebuild.** The old database is only removed at the very
end, when the new one atomically takes its place. So the peak needs room for all
three at once:

```
old database  +  new database  +  temporary CSV
```

The CSV is the largest of the three (plain text against compressed LevelDB). The
panel estimates the peak before starting, using what the *last* rebuild actually
cost rather than a guess.

When it finishes only the new database remains - the CSV and the temporary
database are removed, and the old data has been replaced.

### Exporting with the node stopped

```bash
mining-dark utxo update --from-snapshot
```

Reads the UTXO set straight from `chainstate_snapshot/` without needing the node
up. It is the way out when `bitcoind` will not start - a chainstate corrupted by
an unclean shutdown, say - while the snapshot on disk is intact.

It is safe because Bitcoin Core only writes `base_blockhash` after deserialising
every coin and checking the whole UTXO set against the hash built into its
binary. The command refuses if that file is missing, if the node is running, or
if Core complained about the database recently (`--ignore-node-errors` overrides
that last check, once you have satisfied yourself it is fine).

### "Is it working?"

```bash
mining-dark doctor
```

One command that looks at all eight parts of the setup - binaries,
`bitcoin.conf`, the snapshot file, the snapshot loaded into the node, the Bitcoin
Core process, the UTXO database, disk space and the found-wallets folder - and
ends by naming **the next step**, exactly one:

```
 OK    Binaries             bitcoind, bitcoin-cli and bitcoin-utxo-dump present
 OK    bitcoin.conf         prune=20000 MiB  ·  checklevel=1
 OK    Snapshot file        9.39 GB in data/snapshots
 OK    Snapshot in the node loaded and validated
 WARN  Bitcoin Core         stopped
 OK    UTXO database        56,596,711 addresses  ·  3.12 GB  ·  0 days
 OK    Disk space           1,456.1 GB free  ·  rebuild peak ~14.8 GB
 OK    Found wallets        data/found_wallets  ·  0 already saved

 Next step: mining-dark node start
```

> The output above is real. It follows the configured language, so the same run
> in Portuguese reads `AVISO` for `WARN` and `FALHA` for `FAIL` - see
> [Languages](#languages).

It exits non-zero only on a **failure** - a warning means "not finished yet",
which is the normal state halfway through setup and must not break a script.

Nothing is started or stopped. The only thing it writes anywhere is a probe file
inside `data/found_wallets/`, removed immediately: a found key is the one
artefact this program cannot reproduce, and the only way to know whether it could
be written is to write. Asking the system (`os.access`) would answer "yes" on a
full disk or a read-only mount.

> **Numbers stay in US format** (`56,596,711` and `3.12 GB`) in both languages,
> deliberately. It is the same format `bitcoin-cli`, `debug.log` and every block
> explorer use, so one figure does not change shape depending on where you read
> it. The graphical panel follows the same rule.

### Troubleshooting

**Bitcoin Core is already running** (`Cannot obtain a lock on data directory`):

```bash
mining-dark node stop
# wait for the "bitcoind parou limpo" message
mining-dark node start
```

**"Corrupted block database detected" that is NOT corruption**

If the log carries this alongside it:

```
[error] Verification error: coin database inconsistencies found (last 5 blocks, ...)
Corrupted block database detected.
Please restart with -reindex or -reindex-chainstate to recover.
```

**do not follow that advice.** On a pruned node it is almost always wrong, and it
is expensive: `--reindex` throws away days of syncing to rebuild from blocks
pruning has already deleted.

What is happening: Core's startup check *disconnects* the last few blocks to
verify them, and to do that it reads the undo files (`rev*.dat`) - which pruning
is entirely entitled to have deleted. With the undo data missing, Core reports it
as a corrupt database. The data is intact.

Seen here with `prune=2048` during an assumeutxo background sync: the node was
holding recent blocks at the tip *and* 2015-era blocks fetched to validate from
genesis, and 2 GB could not hold the undo data for both. Starting the same
datadir with a shallower check brought the node back at the correct tip, without
touching anything.

The panel and `mining-dark node start` **detect this case by themselves** and
retry with `-checklevel=1`, saying so in the log. To force it by hand:

```bash
mining-dark node start --shallow-verify
```

Prevention lives in the `bitcoin.conf` the setup script writes: `prune=20000`
(20 GB, with room for both ranges) and `checklevel=1`. If your `bitcoin.conf` is
older and still says `prune=2048`, it is worth fixing.

**Real corruption after an abrupt shutdown** (`ReadBlockFromDisk failed`):

```bash
mining-dark node start --reindex
```

> `-reindex-chainstate` **does not work** in pruned mode - always use
> `--reindex`. And check first that it is not the case above: `--reindex` on a
> pruned node is very expensive.

**`loadtxoutset` refuses the file** (`Unable to load UTXO snapshot`):

Almost always a Core build too old for the snapshot's height. Check:

```bash
bitcoind --version | head -1     # must be >= v31.1.0 for height 935,000
```

`mining-dark node snapshot` already blocks this case before calling the RPC, with
the minimum required version in the error message.

**A snapshot is already loaded** (`chainstate_snapshot/` present):

The previous background sync did not finish. Let it complete (`mining-dark node
status`) - loading a second snapshot on top is not supported.

---

## Usage

Activate the venv (once per session):

```bash
source .venv/bin/activate
```

### The normal path: the graphical panel

```bash
mining-dark gui
```

Nearly everything that used to require memorising a command now has a button:
start/pause/stop the scan, change mode and worker count, choose where files are
saved, start and stop Bitcoin Core, rebuild the UTXO database, and open the found
wallets. Details in the **Graphical Panel** section.

If you have never run anything yet, start here - no UTXO database required:

```bash
mining-dark gui --simulate
```

### The CLI

Still complete, and the way to automate, to work over SSH, and to reach anything
the window does not cover.

| Command                                   | What it does                                        |
|-------------------------------------------|-----------------------------------------------------|
| **Panel**                                 |                                                     |
| `mining-dark gui`                         | Opens the graphical panel                           |
| `mining-dark gui --simulate`              | Panel on simulated data, no UTXO database needed    |
| `mining-dark gui --lang en`               | English interface (overrides `config.yaml`)         |
| **Scanner**                               |                                                     |
| `mining-dark scan`                        | Starts the scanner in the terminal (guided menu)    |
| `mining-dark scan --workers 20`           | Scanner with 20 workers                             |
| `mining-dark scan --mode hd`              | HD wallet mode                                      |
| `mining-dark scan --no-menu`              | Skips the menu and uses `config.yaml` directly      |
| `mining-dark scan -c other.yaml`          | Uses a different configuration file                 |
| `mining-dark check <address>`             | Looks one address up in the UTXO database           |
| `mining-dark found`                       | Lists previously found wallets                      |
| `mining-dark keygen -n 3`                 | Generates 3 sample wallets (no balance check)       |
| `mining-dark paths`                       | Prints where every file lives                       |
| `mining-dark doctor`                      | Audits the installation and names the next step     |
| **Bitcoin Core**                          |                                                     |
| `mining-dark node start`                  | Starts bitcoind (with the project datadir)          |
| `mining-dark node start --reindex`        | Same, rebuilding the block index                    |
| `mining-dark node start --shallow-verify` | Same, with -checklevel=1 (pruned node, no undo)     |
| `mining-dark node stop`                   | Stops bitcoind with a clean shutdown                |
| `mining-dark node status`                 | Node state + sync progress                          |
| `mining-dark node snapshot <.dat>`        | Loads an assumeutxo snapshot (skips the IBD)        |
| `mining-dark node cli <args...>`          | Passthrough to `bitcoin-cli` (datadir handled)      |
| **UTXO**                                  |                                                     |
| `mining-dark utxo update`                 | Rebuilds the UTXO database from Bitcoin Core        |
| `mining-dark utxo update --force`         | Same, ignoring the freshness check                  |
| `mining-dark utxo status`                 | UTXO database health                                |

Every command also works through `python3 main.py <subcommand>` (legacy shim).

> **Why `mining-dark node` instead of calling `bitcoind`/`bitcoin-cli` directly?**
> So you **never have to remember** `-datadir="$PWD/data/bitcoin-core"`. Forget
> it once and `bitcoind` falls back to `~/.bitcoin/` (Linux) or
> `~/Library/Application Support/Bitcoin/` (macOS) - exactly what this project is
> built to avoid.

---

## Graphical Panel

A HUD built with **Dear PyGui** (OpenGL, a single dependency, no Qt/Tk). It
replaces nothing: it is a presentation layer over the same scanner
`mining-dark scan` drives.

### Installation

```bash
pip install -e '.[gui]'      # or simply: pip install dearpygui
```

### Running it

```bash
mining-dark gui --simulate               # demo, no UTXO database needed
mining-dark gui                          # real scanner
mining-dark gui --theme amber --autostart
python -m mining_dark.gui --simulate     # alternative entry point, no CLI
```

Every flag is optional - with none of them the panel uses whatever is in
`config.yaml`.

| Option                | What it does                                             |
|-----------------------|----------------------------------------------------------|
| `--simulate`          | Uses simulated data - ideal for trying the interface out |
| `--mode`              | `random` or `hd` (also settable in the window)           |
| `--workers`           | Number of parallel workers                               |
| `--theme`             | `matrix` (green), `amber` or `ice` (cyan)                |
| `--lang`              | `pt` or `en` - interface only                            |
| `--font-scale`        | Font multiplier for HiDPI screens (e.g. `1.25`)          |
| `--autostart`         | Starts the scan as soon as the window opens              |
| `--screenshot`        | Writes a PNG of the panel (Dear PyGui framebuffer)       |
| `--screenshot-frames` | Frame the PNG is written at (default `120`, ~2 s)        |

### Layout

| Region | Contents                                                                     |
|--------|------------------------------------------------------------------------------|
| Top    | Identity, mode, workers, backend, state badge, uptime and the two buttons    |
| Left   | One bar per worker, with status and addresses checked                        |
| Centre | Activity radar and six live metrics                                          |
| Right  | Recent addresses (public keys only) + `STREAM LOG` coloured by severity      |
| Footer | Start / Pause / Stop and UTXO database health                                |

**Each worker's bar is its share of the fastest one** - `checked ÷ the leader's
checked`. With everything healthy all the bars sit full; a worker that stalls or
falls behind visibly shrinks while the rest stay at 100%. It is the only
per-worker fact worth watching: they used to show a decorative sawtooth that read
the same on all twenty rows.

> There were also three `SCAN → VERIFY → MERGE` bars between the radar and the
> metrics. They were removed. Measured against a real scan: the key queue never
> exceeded 0.2% occupancy (one generator feeds twenty consumers), the fraction of
> busy workers sat pinned at 100%, and `MERGE` only leaves zero when a wallet is
> found. All three were correct and none said anything; the numbers just below
> say the same thing steadily.

### SETTINGS

The top-right button opens an editor for `config.yaml` with four tabs. `SAVE`
writes the file **preserving its comments** and drops a one-time
`config.yaml.bak`. `RELOAD` re-reads the file from disk, useful after editing it
by hand.

| Tab             | What is in it                                                                                                                                       |
|-----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| **Scanner**     | Mode, workers, queue size, minimum balance, children per seed (HD) and which of the 6 address formats to check                                      |
| **Paths**       | Read-only map of the installation: project, data, wallets, logs, UTXO database, Core datadir, snapshots and `config.yaml` - each with `OPEN`/`COPY` |
| **Node & UTXO** | The three ordered steps: assumeutxo snapshot, Bitcoin Core, and the local UTXO database                                                             |
| **Appearance**  | Language, theme and font scale                                                                                                                      |

The Scanner fields lock while a scan runs - changing the worker count mid-flight
would have no effect and would only make the panel harder to read.

The **Paths** tab deliberately has no editable fields: every path is derived from
the project folder, so there is nothing useful to type - only to look at, open
and copy. To relocate anything, the two usual mechanisms still apply:
`MINING_DARK_DATA_DIR` moves the whole `data/` folder, and the path keys in
`config.yaml` move one specific folder. The panel honours those overrides and
**does not erase them** when saving.

Rebuilding the UTXO database is the one operation the window cannot show in full
detail: `utxo_updater` draws its own Rich progress bar on stdout, and turning
that into log lines would produce ANSI garbage. The panel shows the phase and the
byte counts; the fine-grained progress stays in the terminal that launched it.

### FOUND WALLETS

The second button at the top lists the contents of `data/found_wallets/`.

**These files contain the private keys.** The window is designed around that:

- the **table** shows public metadata only, read from the `.json` (date, address,
  type, balance);
- **`OPEN .TXT`** hands the file to the system's default application, so the key
  never passes through this process's framebuffer and cannot end up in a
  screenshot or a screen share of the panel;
- the **built-in preview** masks every key by default (`4c08****...****7f4f`),
  behind a `reveal private keys` checkbox that shows a warning when ticked.

### Languages

The interface is available in **English and Portuguese** - the window and the
command line both. One setting drives both surfaces:

```yaml
ui:
  language: en        # or: pt
```

In the window it is switchable live from the Appearance tab. On the command line
it is read at startup, and `--lang` overrides it for a single run:

```bash
mining-dark --lang en doctor
mining-dark --lang pt utxo status
```

What is **not** translated, deliberately:

- everything the project **generates** - wallet files, logs on disk, CSV headers.
  A found wallet has to be readable by whoever opens it, in any locale;
- pipeline jargon - `SCANNING`, `VERIFYING`, `STREAM LOG`;
- the commands quoted inside hints. `mining-dark utxo update` is typed, not read,
  so translating it would make it wrong;
- `--help`. Typer reads it when the module is imported, before any config has
  been loaded and so before the language is known; it stays English, like the
  code it documents.

The log history keeps whatever language each line was written in; switching
applies from the next line onwards.

### How it works inside

Dear PyGui is only ever touched on the main thread. Each backend runs on its own
thread and publishes immutable events onto an `EventBus` (a bounded queue that
drops the oldest events rather than blocking the scanner). The render loop drains
that queue once per frame and folds everything into a `UIState` the panels read.

```
  backend thread                         main thread (60 FPS)
┌────────────────────┐   EventBus    ┌───────────────────────────┐
│ LiveBackend        │──────────────►│ drain() → UIState.apply() │
│  · RandomKeyGen    │  (lossy queue)│ panels.update()           │
│  · BalanceChecker  │◄──────────────│ start / pause / stop      │
│  · UTXODatabase    │   commands    └───────────────────────────┘
└────────────────────┘
```

Pausing needs no change to the core at all: the key queue is a `_PausableQueue`
that stalls consumers, so the workers idle and the generator fills the queue and
blocks - neither class knows a pause happened.

**Private keys never cross the `EventBus`** - public addresses only. On top of
that, every log line goes through `utils.logger.contains_secret()` before being
drawn; anything that looks like a hex key, a WIF or a BIP39 mnemonic is replaced
with `[REDACTED]`.

### Plugging in your own backend

```python
from mining_dark.gui.app import MiningDarkGUI
from mining_dark.gui.backends.base import ScanBackend
from mining_dark.gui.state import EventBus, LogLevel, StatsEvent

class MyBackend(ScanBackend):
    name = "my-scanner"

    def _run(self) -> None:
        while not self.should_stop:
            self.wait_while_paused()
            self.bus.emit(StatsEvent(keys_generated=..., keys_per_second=...))
            self.log(LogLevel.INFO, "all good")

bus = EventBus()
MiningDarkGUI(MyBackend(bus), bus).run()
```

---

## Configuration

Edit `config.yaml` by hand, or use the **SETTINGS** button in the graphical panel
- both write the same file. Every field is optional; the defaults live in
`src/mining_dark/config/settings.py`.

**scanner** (generation and checking)

| Key                            | Default  | What it does                            |
|--------------------------------|----------|-----------------------------------------|
| `scanner.mode`                 | `random` | `random` or `hd`                        |
| `scanner.workers`              | `10`     | Parallel async workers (1 to 512)       |
| `scanner.queue_size`           | `500`    | Size of the internal key queue          |
| `scanner.address_types`        | 6 types  | Which formats to check                  |
| `scanner.min_balance_satoshis` | `0`      | Only save wallets above this balance    |

**hd_wallet** (BIP32/44/49/84/86 mode)

| Key                          | Default         | What it does                                     |
|------------------------------|-----------------|--------------------------------------------------|
| `hd_wallet.derivation_paths` | BIP 44/49/84/86 | Derivation templates (`{i}` is the child index)  |
| `hd_wallet.child_count`      | `20`            | Children derived per seed (BIP44 gap limit = 20) |

**output** (where found wallets are written)

| Key                        | Default               | What it does                           |
|----------------------------|-----------------------|----------------------------------------|
| `output.found_wallets_dir` | `data/found_wallets/` | Override the output directory          |
| `output.save_csv`          | `true`                | Also append to a rolling `summary.csv` |
| `output.json_indent`       | `2`                   | Indentation of the generated JSON      |

**logging** (Loguru)

| Key                 | Default      | What it does                                         |
|---------------------|--------------|------------------------------------------------------|
| `logging.level`     | `INFO`       | `TRACE` \| `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| `logging.logs_dir`  | `data/logs/` | Override the log directory                           |
| `logging.rotation`  | `50 MB`      | Size at which a log file rotates                     |
| `logging.retention` | `7 days`     | How long old logs are kept                           |

**utxo** (local database)

| Key            | Default             | What it does                    |
|----------------|---------------------|---------------------------------|
| `utxo.db_file` | `data/utxo/utxo.db` | Override the SQLite path        |

> There is deliberately **no** `bitcoin_core_dir` key. The node always runs with
> `-datadir=data/bitcoin-core/`; set `MINING_DARK_DATA_DIR` to move the whole
> `data/` tree, node included. A second way to point at the datadir would only
> let the two disagree.

**ui** (interfaces)

| Key                    | Default  | What it does                                       |
|------------------------|----------|----------------------------------------------------|
| `ui.refresh_fps`       | `4`      | Terminal dashboard refresh rate                    |
| `ui.recent_table_rows` | `15`     | Recent addresses in the terminal table             |
| `ui.theme`             | `dark`   | Terminal dashboard: `dark` or `light`              |
| `ui.language`          | `pt`     | Interface language, window and CLI: `pt` or `en`   |
| `ui.palette`           | `matrix` | Panel accent: `matrix`, `amber` or `ice`           |
| `ui.font_scale`        | `1.0`    | Panel font multiplier (0.6 - 2.5)                  |

> The panel's **Appearance** tab edits the last three keys, and `SAVE` writes
> everything back to `config.yaml` without dropping the comments.

**Environment variables**

| Name                   | Effect                                                              |
|------------------------|---------------------------------------------------------------------|
| `MINING_DARK_DATA_DIR` | Relocates the whole `data/` folder elsewhere (another disk, say)    |

---

## Security

- Private keys **never** appear in the logs (Loguru filter - hex, WIF and BIP39
  mnemonics)
- The graphical panel only ever receives public addresses, and applies the same
  filter again before drawing each line
- The wallet browser masks keys by default and opens files through the system's
  own application, so they never pass through the panel's framebuffer
- A found wallet is written **immediately**, before anything else: the content
  goes to a temporary file, is forced to disk with `fsync`, and only then
  atomically replaces the target. The file either does not exist or is complete -
  never half a key. Closing the program mid-write loses nothing
- Each file carries the private key in **hex** and in both **WIF** forms, the
  public keys, all six addresses and the balances. **In HD mode it also carries
  the seed phrase and the derivation path** (`m/84'/0'/0'/0/3`): the WIF already
  spends the address that matched, but an HD hit is a hit on *one child* of a
  seed, and the phrase is what restores the whole tree in an ordinary wallet and
  reaches the siblings the scan never looked at
- Files are created with mode **600** - only your user can read them
- The files in `data/found_wallets/` (`.txt`, `.json` and `summary.csv`)
  **contain the private keys** - treat that folder the way you treat a seed phrase
- All data stays **local**, under `data/` (wallets in `data/found_wallets/`, UTXO
  database in `data/utxo/`, blockchain in `data/bitcoin-core/`, logs in
  `data/logs/`)
- Bitcoin Core RPC credentials are generated randomly and stored mode 600
- Nothing is ever sent to a third party

---

## Development

Run the tests:

```bash
pip install -e '.[dev]'
pytest
```

Lint:

```bash
ruff check src/ tests/
```

---

## Licence

MIT License - free to use for educational and research purposes.
