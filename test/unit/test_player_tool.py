"""tools/player.py: FFmpeg's configuration for q2player, and the parts that run no compiler. The builds themselves
(run, source, ffmpeg, build) need the network and a toolchain; test/player/run.py exercises them."""
import hashlib

import pytest

import player


def test_configure_enables_exactly_the_lists():
    args = player.configure_args('/p')
    assert args[0] == '--prefix=/p'
    for kind, names in (('demuxer', player.DEMUXERS), ('decoder', player.DECODERS), ('parser', player.PARSERS),
                        ('protocol', player.PROTOCOLS), ('bsf', player.BSFS)):
        assert [a for a in args if a.startswith(f'--enable-{kind}=')] == [f'--enable-{kind}={n}' for n in names]
        assert len(set(names)) == len(names)
    assert len(args) == len(set(args))


def test_configure_is_c_only_static_and_minimal():
    args = player.configure_args('/p')
    for flag in ('--disable-everything', '--disable-asm', '--disable-runtime-cpudetect', '--enable-static', '--disable-shared',
                 '--disable-programs', '--disable-autodetect', '--enable-pic', '--enable-network'):
        assert flag in args


def test_mpeg_audio_is_the_integer_decoder():
    """16-bit output, as stock's libmad: the float decoders would change the format the DAC is opened with."""
    assert {'mp3', 'mp2', 'mp1'} <= set(player.DECODERS)
    assert not [d for d in player.DECODERS if d.endswith('float')]


def test_what_phase_1_plays_is_configured():
    assert {'flac', 'mp3'} <= set(player.DEMUXERS) and 'flac' in player.DECODERS and 'file' in player.PROTOCOLS


def test_arch_is_stocks_abi():
    assert player.ARCH == ['-EL', '-march=mips32r2', '-mabi=32', '-mhard-float', '-mfp64', '-mnan=2008', '-mabs=2008']
    assert player.TRIPLE == 'mips-mti-linux-gnu'


def test_sha256(tmp_path):
    f = tmp_path/'x'
    data = bytes(range(256)) * 5000  # more than one 1 MB block
    f.write_bytes(data)
    assert player.sha256(f) == hashlib.sha256(data).hexdigest()
    (tmp_path/'empty').write_bytes(b'')
    assert player.sha256(tmp_path/'empty') == hashlib.sha256(b'').hexdigest()


def test_mbedtls_is_the_newest_cached_build(tmp_path, monkeypatch):
    monkeypatch.setattr(player, 'THIRDPARTY', tmp_path)
    with pytest.raises(SystemExit) as e:  # https is always built: no mbedTLS stops the build, naming what makes it
        player.mbedtls('mips')
    assert 'no mbedTLS' in str(e.value) and 'thirdparty.py' in str(e.value)
    for name in ('mbedtls-3.6.6-aa', 'mbedtls-3.6.7-bb'):
        (tmp_path/'out'/name/'usr/lib').mkdir(parents=True)
        (tmp_path/'out'/name/'usr/lib/libmbedtls.a').write_bytes(b'')
    (tmp_path/'out/mbedtls-3.6.8-cc/usr/lib').mkdir(parents=True)  # no library in it: not a finished build
    assert player.mbedtls('mips') == tmp_path/'out/mbedtls-3.6.7-bb/usr'


def test_toolchain(tmp_path, monkeypatch):
    monkeypatch.setattr(player, 'THIRDPARTY', tmp_path)
    with pytest.raises(SystemExit) as e:
        player.toolchain()
    assert 'no toolchain' in str(e.value) and 'thirdparty.py' in str(e.value)
    bindir = tmp_path/'toolchain/mips-mti-linux-gnu/2020.06-01/bin'
    bindir.mkdir(parents=True)
    (bindir/'mips-mti-linux-gnu-gcc').write_text('')
    assert player.toolchain() == bindir


def test_log_starts_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(player, 'CACHE', tmp_path/'cache')
    log = player._log('host')
    assert log == tmp_path/'cache/player-host.log' and log.read_text() == ''
    log.write_text('old')
    assert player._log('host').read_text() == ''
    assert player._log('mips').name == 'player-mips.log'


@pytest.mark.parametrize('argv', [['player.py'], ['player.py', 'ffmpeg'], ['player.py', 'ffmpeg', 'arm'], ['player.py', 'run', 'host'],
                                  ['player.py', 'build', 'host', 'more']])
def test_main_usage(argv, monkeypatch):
    monkeypatch.setattr(player.sys, 'argv', argv)
    with pytest.raises(SystemExit) as e:
        player.main()
    assert 'tools/player.py build host|mips' in str(e.value)


def test_main_dispatch(tmp_path, monkeypatch, capsys):
    (tmp_path/'lib').mkdir()
    (tmp_path/'lib/libb.a').write_bytes(b'12')
    (tmp_path/'lib/liba.a').write_bytes(b'1234')
    (tmp_path/'lib/notes.txt').write_bytes(b'1')
    binary = tmp_path/'q2player'
    binary.write_bytes(b'x' * 7)
    calls = []
    monkeypatch.setattr(player, 'ffmpeg', lambda target: calls.append(('ffmpeg', target)) or tmp_path)
    monkeypatch.setattr(player, 'build', lambda target: calls.append(('build', target)) or binary)
    monkeypatch.setattr(player.sys, 'argv', ['player.py', 'ffmpeg', 'mips'])
    player.main()
    assert capsys.readouterr().out == f'{tmp_path}\n' and calls == [('ffmpeg', 'mips')]
    monkeypatch.setattr(player.sys, 'argv', ['player.py', 'build', 'host'])
    player.main()
    assert capsys.readouterr().out == f'{binary}\n' and calls[-1] == ('build', 'host')
    monkeypatch.setattr(player.sys, 'argv', ['player.py', 'sizes', 'mips'])
    player.main()
    assert capsys.readouterr().out == f'{binary}: 7 bytes\n  liba.a: 4 bytes (archive)\n  libb.a: 2 bytes (archive)\n'
    assert calls[-2:] == [('build', 'mips'), ('ffmpeg', 'mips')]
