"""tools/uac1.py: the usb_f_uac1.ko pin (docs/iphone-mode.md#the-uac1-module)."""
import hashlib
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]/'tools'))
import uac1  # noqa: E402


def test_the_pin_names_the_sources_as_they_are():
    """A change to tools/uac1_module needs the module built and pinned again, or images carry the old one."""
    assert uac1.PIN[2] == uac1.sources_sha256(), 'run python3 tools/uac1.py and set PIN in tools/uac1.py'
    assert len(uac1.PIN[0]) == 64 and uac1.PIN[1] > 0


def test_the_sources_hash_follows_its_files(tmp_path, monkeypatch):
    for name, text in {'uac1_build.py': 'a', 'Kbuild': 'b', 'patches/0001.patch': 'c'}.items():
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(text)
    monkeypatch.setattr(uac1, 'SOURCES', tmp_path)
    first = uac1.sources_sha256()
    (tmp_path/'__pycache__').mkdir()
    (tmp_path/'__pycache__/uac1_build.cpython-312.pyc').write_text('compiled')
    assert uac1.sources_sha256() == first, 'a byte-code cache is not a source'
    (tmp_path/'patches/0001.patch').write_text('d')
    assert uac1.sources_sha256() != first


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    data = b'\x7fELF usb_f_uac1'
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(uac1, 'CACHE', tmp_path)
    monkeypatch.setattr(uac1, 'PIN', (digest, len(data), uac1.sources_sha256()))
    return tmp_path, digest, data


def test_a_missing_module_says_how_to_make_it(pinned):
    cache, digest, data = pinned
    assert 'run python3 tools/uac1.py' in uac1.problem()
    with pytest.raises(SystemExit):
        uac1.module()
    (cache/digest).mkdir()
    (cache/digest/'usb_f_uac1.ko').write_bytes(data)
    assert uac1.problem() is None
    assert uac1.module() == data


def test_a_cached_file_that_is_not_the_pinned_one_is_refused(pinned):
    cache, digest, data = pinned
    (cache/digest).mkdir()
    (cache/digest/'usb_f_uac1.ko').write_bytes(data + b'!')
    assert uac1.problem() is not None


def test_a_stale_pin_stops_the_build(pinned, monkeypatch):
    cache, digest, data = pinned
    (cache/digest).mkdir()
    (cache/digest/'usb_f_uac1.ko').write_bytes(data)
    monkeypatch.setattr(uac1, 'PIN', (digest, len(data), '0' * 64))
    assert 'changed since usb_f_uac1.ko was pinned' in uac1.problem()


@pytest.fixture
def kbuild(tmp_path, monkeypatch):
    """uac1_build.py stood in for: it leaves its module in --work's module/, as the real one does."""
    stub = types.SimpleNamespace(data=b'\x7fELF fresh', calls=[])

    def run(cmd, **kw):
        stub.calls.append(cmd)
        (tmp_path/'work/module').mkdir(parents=True, exist_ok=True)
        (tmp_path/'work/module/usb_f_uac1.ko').write_bytes(stub.data)
        return uac1.subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(uac1.subprocess, 'run', run)
    monkeypatch.setattr(uac1, 'WORK', tmp_path/'work')
    monkeypatch.setattr(uac1, 'CACHE', tmp_path/'cache')
    return stub


def test_a_build_is_kept_in_the_cache_under_its_sha(kbuild):
    data, digest, sources = uac1.build()
    assert data == kbuild.data and digest == hashlib.sha256(data).hexdigest() and sources == uac1.sources_sha256()
    assert (uac1.CACHE/digest/'usb_f_uac1.ko').read_bytes() == data
    assert kbuild.calls == [[sys.executable, str(uac1.SOURCES/'uac1_build.py'), '--work', str(uac1.WORK)]]


def test_main_builds_and_prints_the_pin(kbuild, capsys):
    assert uac1.main(['uac1.py']) == 0
    digest = hashlib.sha256(kbuild.data).hexdigest()
    assert capsys.readouterr().out == f'PIN = {(digest, len(kbuild.data), uac1.sources_sha256())!r}\n'


def test_main_check_passes_on_the_pinned_module_and_fails_without_it(pinned, capsys):
    cache, digest, data = pinned
    assert uac1.main(['uac1.py', '--check']) == 1
    assert 'is not in' in capsys.readouterr().out
    (cache/digest).mkdir()
    (cache/digest/'usb_f_uac1.ko').write_bytes(data)
    assert uac1.main(['uac1.py', '--check']) == 0
    assert capsys.readouterr().out == f'usb_f_uac1.ko {digest[:12]}: pinned, cached, sources unchanged\n'


def test_main_refuses_other_arguments(capsys):
    assert uac1.main(['uac1.py', '--frobnicate']) == 2
    assert 'python3 tools/uac1.py' in capsys.readouterr().err


def test_sources_edited_during_the_build_pin_nothing(kbuild, tmp_path, monkeypatch, capsys):
    """As tools/iroh.py's crate: the sources were hashed after the build, so an edit while it ran paired the old
    sources' module with the new sources' hash. Hashed before and again after, a change stops it."""
    src = tmp_path/'src'
    (src/'patches').mkdir(parents=True)
    (src/'Kbuild').write_text('obj-m += x.o\n')
    monkeypatch.setattr(uac1, 'SOURCES', src)
    run = uac1.subprocess.run

    def edited_meanwhile(cmd, **kw):
        (src/'patches/q2.patch').write_text('changed while it built\n')
        return run(cmd, **kw)
    monkeypatch.setattr(uac1.subprocess, 'run', edited_meanwhile)
    with pytest.raises(SystemExit, match='tools/uac1_module changed while usb_f_uac1.ko was built'):
        uac1.main(['uac1.py'])
    assert 'PIN = ' not in capsys.readouterr().out
