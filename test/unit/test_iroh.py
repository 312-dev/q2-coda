"""tools/iroh.py: the q2-iroh pin (docs/remote.md#car-remote-across-networks)."""
import hashlib
import pathlib
import sys
import types

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


@pytest.fixture
def cargo(tmp_path, monkeypatch):
    """q2/build.sh stood in for: it prints the path of the binary it 'built' (data), as the real one does last."""
    built = tmp_path/'target/q2/q2-iroh'
    built.parent.mkdir(parents=True)
    (built.parent/'sysroot').mkdir()  # the stock /lib copy each build makes, dropped after
    stub = types.SimpleNamespace(data=b'\x7fELF fresh', calls=[], built=built)

    def run(cmd, **kw):
        stub.calls.append(cmd)
        built.write_bytes(stub.data)
        return iroh.subprocess.CompletedProcess(cmd, 0, stdout=f'Compiling q2-iroh\n{built}\n')
    monkeypatch.setattr(iroh.subprocess, 'run', run)
    monkeypatch.setenv('CARGO_TARGET_DIR', str(tmp_path/'target'))
    monkeypatch.setattr(iroh, 'CACHE', tmp_path/'cache')
    return stub


def test_a_build_is_kept_in_the_cache_under_its_sha(cargo):
    data, digest, crate = iroh.build()
    assert data == cargo.data and digest == hashlib.sha256(data).hexdigest() and crate == iroh.crate_sha256()
    assert (iroh.CACHE/digest/'q2-iroh').read_bytes() == data and (iroh.CACHE/digest/'q2-iroh').stat().st_mode & 0o111
    assert cargo.calls == [['sh', str(iroh.CRATE/'q2/build.sh'), str(cargo.built.parent)]]
    assert not (cargo.built.parent/'sysroot').exists()


def test_build_iroh_uses_what_it_built_and_says_when_the_pin_is_stale(cargo, monkeypatch, capsys):
    """build.py --build-iroh (binary(fresh=True)): the new binary is installed, pinned or not."""
    assert iroh.binary(fresh=True) == cargo.data
    assert 'not the pinned one; PIN = ' in capsys.readouterr().err
    digest = hashlib.sha256(cargo.data).hexdigest()
    monkeypatch.setattr(iroh, 'PIN', (digest, len(cargo.data), iroh.crate_sha256()))
    assert iroh.binary(fresh=True) == cargo.data
    assert capsys.readouterr().err == ''


def test_main_builds_and_prints_the_pin(cargo, capsys):
    assert iroh.main(['iroh.py']) == 0
    digest = hashlib.sha256(cargo.data).hexdigest()
    assert capsys.readouterr().out == f'PIN = {(digest, len(cargo.data), iroh.crate_sha256())!r}\n'


def test_main_check_passes_on_the_pinned_binary_and_fails_without_it(pinned, capsys):
    cache, digest, data = pinned
    assert iroh.main(['iroh.py', '--check']) == 1
    assert 'is not in' in capsys.readouterr().out
    (cache/digest).mkdir()
    (cache/digest/'q2-iroh').write_bytes(data)
    assert iroh.main(['iroh.py', '--check']) == 0
    assert capsys.readouterr().out == f'q2-iroh {digest[:12]}: pinned, cached, crate unchanged\n'


def test_main_refuses_other_arguments(capsys):
    assert iroh.main(['iroh.py', '--frobnicate']) == 2
    assert 'python3 tools/iroh.py' in capsys.readouterr().err


def test_a_crate_edited_during_the_build_pins_nothing(cargo, tmp_path, monkeypatch, capsys):
    """The crate was hashed after the build, so an edit while cargo ran paired the old source's binary with the new
    source's hash (2026-11-22, seen only by the binary's size). Hashed before and again after, a change stops it."""
    crate = tmp_path/'crate'
    for name, text in {'Cargo.toml': 'a', 'Cargo.lock': 'b', 'src/main.rs': 'fn main() {}', 'q2/build.sh': 'x'}.items():
        (crate/name).parent.mkdir(parents=True, exist_ok=True)
        (crate/name).write_text(text)
    monkeypatch.setattr(iroh, 'CRATE', crate)
    run = iroh.subprocess.run

    def edited_meanwhile(cmd, **kw):
        (crate/'src/main.rs').write_text('fn main() { println!("new"); }')  # saved while cargo builds the old one
        return run(cmd, **kw)
    monkeypatch.setattr(iroh.subprocess, 'run', edited_meanwhile)
    with pytest.raises(SystemExit, match='remote/iroh-sidecar changed while q2-iroh was built'):
        iroh.main(['iroh.py'])
    assert 'PIN = ' not in capsys.readouterr().out
