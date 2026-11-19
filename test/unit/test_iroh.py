"""tools/iroh.py: the q2-iroh pin (docs/remote.md#car-remote-across-networks)."""
import hashlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]/'tools'))
import iroh  # noqa: E402


def test_the_pin_names_the_crate_as_it_is():
    """A change to remote/iroh-sidecar needs the binary built and pinned again, or images carry the old one."""
    assert iroh.PIN[2] == iroh.crate_sha256(), 'run python3 tools/iroh.py and set PIN in tools/iroh.py'
    assert len(iroh.PIN[0]) == 64 and iroh.PIN[1] > 0


def test_the_crate_hash_follows_its_sources(tmp_path, monkeypatch):
    for name, text in {'Cargo.toml': 'a', 'Cargo.lock': 'b', 'src/main.rs': 'fn main() {}', 'q2/build.sh': 'x'}.items():
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(text)
    monkeypatch.setattr(iroh, 'CRATE', tmp_path)
    first = iroh.crate_sha256()
    (tmp_path/'target').mkdir()
    (tmp_path/'target/junk').write_text('built')
    assert iroh.crate_sha256() == first, 'build output is not a source'
    (tmp_path/'src/main.rs').write_text('fn main() { }')
    assert iroh.crate_sha256() != first


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    data = b'\x7fELF q2-iroh'
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(iroh, 'CACHE', tmp_path)
    monkeypatch.setattr(iroh, 'PIN', (digest, len(data), iroh.crate_sha256()))
    return tmp_path, digest, data


def test_a_missing_binary_says_how_to_make_it(pinned):
    cache, digest, data = pinned
    assert 'run python3 tools/iroh.py' in iroh.problem()
    with pytest.raises(SystemExit):
        iroh.binary()
    (cache/digest).mkdir()
    (cache/digest/'q2-iroh').write_bytes(data)
    assert iroh.problem() is None
    assert iroh.binary() == data


def test_a_cached_file_that_is_not_the_pinned_one_is_refused(pinned):
    cache, digest, data = pinned
    (cache/digest).mkdir()
    (cache/digest/'q2-iroh').write_bytes(data + b'!')
    assert iroh.problem() is not None


def test_a_stale_pin_stops_the_build(pinned, monkeypatch):
    cache, digest, data = pinned
    (cache/digest).mkdir()
    (cache/digest/'q2-iroh').write_bytes(data)
    monkeypatch.setattr(iroh, 'PIN', (digest, len(data), '0' * 64))
    assert 'changed since q2-iroh was pinned' in iroh.problem()
