"""
Fetching the assumeutxo snapshot.

The file is ~9 GB, so the download is the fragile part of setting a node up.
These pin the behaviour that makes a dropped connection survivable: resume from
what is on disk, fall through to another mirror, and never call a partial file
complete.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mining_dark import snapshot


def test_the_path_is_named_after_the_height(monkeypatch, tmp_path) -> None:
    from mining_dark import paths

    monkeypatch.setattr(paths, "SNAPSHOTS_DIR", tmp_path)
    assert snapshot.snapshot_path(935_000).name == "utxo-935000.dat"


def test_every_mirror_carries_the_height() -> None:
    urls = snapshot.mirror_urls(880_000)

    assert urls, "a lista de espelhos nao pode ficar vazia"
    assert all("880000" in url for url in urls)


# ═══════════════════════════════════════════════════════════════════════════════
#  A partial file is never mistaken for a complete one
#
#  loadtxoutset on a truncated snapshot fails hours in, after Core has already
#  built a chainstate directory it then throws away.
# ═══════════════════════════════════════════════════════════════════════════════
def test_a_short_file_is_not_complete(tmp_path: Path) -> None:
    partial = tmp_path / "utxo.dat"
    partial.write_bytes(b"x" * 100)

    assert not snapshot.is_complete(partial, 200)
    assert snapshot.is_complete(partial, 100)


def test_a_missing_file_is_not_complete(tmp_path: Path) -> None:
    assert not snapshot.is_complete(tmp_path / "nao_existe", 100)


def test_an_unknown_expected_size_is_never_complete(tmp_path: Path) -> None:
    """A server that will not send Content-Length must not green-light a load."""
    whole = tmp_path / "utxo.dat"
    whole.write_bytes(b"x" * 100)

    assert not snapshot.is_complete(whole, 0)


def test_local_size_of_a_missing_file_is_zero(tmp_path: Path) -> None:
    assert snapshot.local_size(tmp_path / "nada") == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  Resume and mirror fallback
# ═══════════════════════════════════════════════════════════════════════════════
class _Response:
    """Minimal stand-in for what urlopen returns."""

    def __init__(self, body: bytes, status: int = 206) -> None:
        self._body = body
        self.status = status
        self.headers = {"Content-Length": str(len(body))}

    def read(self, size: int) -> bytes:
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_) -> None:
        return None


def test_the_download_resumes_instead_of_restarting(monkeypatch, tmp_path) -> None:
    """The whole point: a dropped 9 GB download must not start over."""
    target = tmp_path / "utxo.dat"
    target.write_bytes(b"a" * 60)                 # what a previous run left

    seen = {}

    def _urlopen(request, timeout=0):
        seen["range"] = request.headers.get("Range")
        return _Response(b"b" * 40)

    monkeypatch.setattr(snapshot.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(snapshot, "remote_size", lambda url, timeout=0: 100)

    snapshot.download(target)

    assert seen["range"] == "bytes=60-", "pediu o arquivo inteiro de novo"
    assert target.read_bytes() == b"a" * 60 + b"b" * 40


def test_a_server_ignoring_range_is_not_appended_to(monkeypatch, tmp_path) -> None:
    """
    Answering 200 means the whole file is coming; appending it to what is
    already there would produce a file longer than the snapshot, and Core would
    reject a hash computed over garbage.
    """
    target = tmp_path / "utxo.dat"
    target.write_bytes(b"a" * 60)

    monkeypatch.setattr(snapshot.urllib.request, "urlopen",
                        lambda request, timeout=0: _Response(b"z" * 100, status=200))
    monkeypatch.setattr(snapshot, "remote_size", lambda url, timeout=0: 100)

    snapshot.download(target)

    assert target.read_bytes() == b"z" * 100


def test_a_dead_mirror_falls_through_to_the_next(monkeypatch, tmp_path) -> None:
    target = tmp_path / "utxo.dat"
    tried = []

    def _urlopen(request, timeout=0):
        tried.append(request.full_url)
        if "morto" in request.full_url:
            raise snapshot.urllib.error.URLError("host fora do ar")
        return _Response(b"x" * 10)

    monkeypatch.setattr(snapshot, "MIRRORS",
                        ("https://morto/{height}.dat", "https://vivo/{height}.dat"))
    monkeypatch.setattr(snapshot.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(snapshot, "remote_size", lambda url, timeout=0: 10)

    snapshot.download(target)

    assert any("morto" in url for url in tried)
    assert any("vivo" in url for url in tried)
    assert target.read_bytes() == b"x" * 10


def test_every_mirror_failing_raises_with_the_reasons(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(snapshot, "MIRRORS", ("https://morto/{height}.dat",))
    monkeypatch.setattr(snapshot.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            snapshot.urllib.error.URLError("recusado")))
    monkeypatch.setattr(snapshot, "remote_size", lambda url, timeout=0: 10)

    with pytest.raises(snapshot.SnapshotError, match="recusado"):
        snapshot.download(tmp_path / "utxo.dat")


def test_cancelling_keeps_what_was_downloaded(monkeypatch, tmp_path) -> None:
    """Stopping must leave a file the next run can resume, not delete it."""
    target = tmp_path / "utxo.dat"

    monkeypatch.setattr(snapshot.urllib.request, "urlopen",
                        lambda request, timeout=0: _Response(b"x" * 100))
    monkeypatch.setattr(snapshot, "remote_size", lambda url, timeout=0: 100)

    snapshot.download(target, should_stop=lambda: True)

    assert target.exists()


def test_an_already_complete_file_is_not_downloaded_again(monkeypatch, tmp_path) -> None:
    target = tmp_path / "utxo.dat"
    target.write_bytes(b"x" * 100)

    monkeypatch.setattr(snapshot, "remote_size", lambda url, timeout=0: 100)
    monkeypatch.setattr(snapshot.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("baixou um arquivo que ja estava inteiro"))

    assert snapshot.download(target) == target
