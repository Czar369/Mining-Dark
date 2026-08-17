"""
Fetching the assumeutxo snapshot the node loads to skip most of the IBD.

The file is ~9 GB, so the download is the fragile part of setting a node up:
one dropped connection used to leave a partial `.dat` and no way forward from
the interface.  Everything here is built around surviving that - resume from
whatever is on disk, retry, and fall through to another mirror - and around
never handing a truncated file to `loadtxoutset`, which would fail deep into a
load that takes hours.

Trust is not a concern: Bitcoin Core checks the snapshot against a hash
compiled into its own binary, so a tampered file is rejected no matter which
mirror served it.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from mining_dark import paths
from mining_dark.i18n import t

#: The height Core 31.1 knows the hash for.  It also accepts 840000, 880000 and
#: 910000; the highest leaves the least chain left to sync afterwards.
SNAPSHOT_HEIGHT = 935_000

#: Where the snapshot can be fetched from, tried in order.  Community hosted -
#: Bitcoin Core does not publish these - which is exactly why more than one
#: entry matters: the single host this project used dropped mid-download and
#: there was nothing to fall through to.  Add mirrors here as they are found;
#: the hash check in Core makes a new mirror safe to trust on sight.
MIRRORS: tuple[str, ...] = (
    "https://files-vps02.jaonoctus.dev/utxo-{height}.dat",
)

_CHUNK = 1 << 20            # 1 MiB, so progress ticks often enough to be useful
_TIMEOUT = 60.0
_ATTEMPTS_PER_MIRROR = 3

#: Callback signature: (bytes_done, bytes_total_or_zero).
ProgressHook = Callable[[int, int], None]


class SnapshotError(RuntimeError):
    """The snapshot could not be fetched or is not usable."""


def snapshot_path(height: int = SNAPSHOT_HEIGHT) -> Path:
    """Where the snapshot for `height` lives once downloaded."""
    return paths.SNAPSHOTS_DIR / f"utxo-{height}.dat"


def mirror_urls(height: int = SNAPSHOT_HEIGHT) -> list:
    return [template.format(height=height) for template in MIRRORS]


def remote_size(url: str, timeout: float = 15.0) -> int:
    """Content-Length for `url`, or 0 when the server will not say."""
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.headers.get("Content-Length") or 0)
    except (urllib.error.URLError, ValueError, OSError):
        return 0


def local_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def is_complete(path: Path, expected: int) -> bool:
    """
    Whether `path` is the whole file.

    Size only - the real integrity check is Core's, against the hash in its own
    binary.  This exists to avoid spending hours on a `loadtxoutset` that was
    always going to fail on a truncated file.
    """
    return expected > 0 and local_size(path) == expected


def download(
    dest: Optional[Path] = None,
    *,
    height: int = SNAPSHOT_HEIGHT,
    on_progress: Optional[ProgressHook] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Path:
    """
    Fetch the snapshot, resuming whatever is already on disk.

    Blocking, and long - call it from a worker thread.  `should_stop` is polled
    between chunks so a caller can cancel without killing the process; the
    partial file is left in place and the next call picks up from there.
    """
    target = dest or snapshot_path(height)
    target.parent.mkdir(parents=True, exist_ok=True)

    urls = mirror_urls(height)
    failures: list = []

    for url in urls:
        expected = remote_size(url)
        if is_complete(target, expected):
            return target

        for attempt in range(1, _ATTEMPTS_PER_MIRROR + 1):
            try:
                _fetch(url, target, expected, on_progress, should_stop)
            except _Cancelled:
                return target
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                failures.append(t("snap.attempt_error", url=url, attempt=attempt, error=exc))
                continue

            if expected == 0 or is_complete(target, expected):
                return target
            # Server closed early without erroring: retry resumes the rest.
            failures.append(t("snap.attempt_cut", url=url, attempt=attempt))

    raise SnapshotError(
        t("snap.download_failed") + "\n" + "\n".join(f"  {f}" for f in failures[-6:])
    )


class _Cancelled(Exception):
    """Raised internally when `should_stop` asks the download to end."""


def _fetch(
    url: str,
    target: Path,
    expected: int,
    on_progress: Optional[ProgressHook],
    should_stop: Optional[Callable[[], bool]],
) -> None:
    """One attempt: resume `target` from its current size to the end."""
    have = local_size(target)
    if expected and have > expected:
        # A previous run against a different mirror left something longer than
        # this file can be; start over rather than serve a hybrid.
        target.unlink(missing_ok=True)
        have = 0

    headers = {"Range": f"bytes={have}-"} if have else {}
    request = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        # A server that ignores Range answers 200 with the whole file; appending
        # then would duplicate what is already there.
        resuming = response.status == 206
        mode = "ab" if resuming and have else "wb"
        done = have if resuming and have else 0

        with open(target, mode) as handle:
            while True:
                if should_stop is not None and should_stop():
                    raise _Cancelled
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if on_progress is not None:
                    on_progress(done, expected)
