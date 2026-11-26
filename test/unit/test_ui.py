"""tools/ui.py: where q2ui's build finds its inputs, and its command line. The build itself (build) needs a
compiler and the caches; test/ui.py runs it."""
import pathlib

import pytest

import player
import thirdparty
import ui


def test_stb_is_fetched_by_its_pin(monkeypatch, tmp_path):
    asked = []

    def fetch(url, digest, name):
        asked.append((url, digest, name))
        return tmp_path/name

    monkeypatch.setattr(thirdparty, 'fetch', fetch)
    assert ui.stb() == tmp_path/'stb_truetype-1.26'
    version, url, digest = ui.STB
    assert asked == [(url, digest, f'stb_truetype-{version}/stb_truetype.h')]
    # a commit, not a branch: the bytes the SHA-256 names cannot move
    assert url.startswith('https://raw.githubusercontent.com/nothings/stb/') and len(url.split('/')[5]) == 40
    assert len(digest) == 64 and url.endswith('/stb_truetype.h')


def test_sqlite_include_is_the_newest_built(monkeypatch, tmp_path):
    monkeypatch.setattr(player, 'THIRDPARTY', tmp_path)
    with pytest.raises(SystemExit) as e:
        ui.sqlite_include()
    assert 'no SQLite' in str(e.value) and 'thirdparty.py' in str(e.value)
    for name in ('sqlite-3.50.0-aa', 'sqlite-3.54.1-bb'):
        (tmp_path/'out'/name/'usr/include').mkdir(parents=True)
        (tmp_path/'out'/name/'usr/include/sqlite3.h').write_text('')
    (tmp_path/'out/sqlite-3.99.0-cc/usr/include').mkdir(parents=True)  # no header in it: not a finished build
    assert ui.sqlite_include() == tmp_path/'out/sqlite-3.54.1-bb/usr/include'


def test_main(monkeypatch, capsys, tmp_path):
    binary = tmp_path/'q2ui'
    binary.write_bytes(b'x' * 7)
    built = []
    monkeypatch.setattr(ui, 'build', lambda target: built.append(target) or binary)
    monkeypatch.setattr(ui.sys, 'argv', ['ui.py', 'build', 'mips'])
    ui.main()
    assert capsys.readouterr().out == f'{binary}\n'
    monkeypatch.setattr(ui.sys, 'argv', ['ui.py', 'size', 'host'])
    ui.main()
    assert capsys.readouterr().out == f'{binary}: 7 bytes\n' and built == ['mips', 'host']
    for argv in (['ui.py'], ['ui.py', 'build'], ['ui.py', 'run', 'host'], ['ui.py', 'build', 'arm'], ['ui.py', 'build', 'host', 'x']):
        monkeypatch.setattr(ui.sys, 'argv', argv)
        with pytest.raises(SystemExit) as e:
            ui.main()
        assert e.value.code == ui.__doc__
    assert built == ['mips', 'host']


def test_the_host_library_is_a_file_path():
    assert isinstance(ui.HOST_SQLITE, pathlib.Path) and ui.HOST_SQLITE.name.startswith('libsqlite3.so')


def test_the_build_takes_the_q2s_binary(monkeypatch, tmp_path):
    """tools/build.py ui_binary: a --ui-spike build's q2ui is tools/ui.py's mips build, never this machine's."""
    import build
    binary = tmp_path/'q2ui'
    binary.write_bytes(b'\x7fELF q2ui')
    asked = []
    monkeypatch.setattr(ui, 'build', lambda target: asked.append(target) or binary)
    assert build.ui_binary() == b'\x7fELF q2ui' and asked == ['mips']
    assert build.UI == 'usr/bin/q2ui'
    old, new = build.UI_SPIKE_HOOK
    # the hook keeps demo's loop whole after q2ui's line: whatever q2ui does, demo still starts
    assert new.endswith(old) and new.count(b'\n') == old.count(b'\n') + 1
    assert b'= q2ui ] && { killall q2splash 2>/dev/null; /usr/bin/q2ui ' in new and build.UI_TARGET.encode() in new
