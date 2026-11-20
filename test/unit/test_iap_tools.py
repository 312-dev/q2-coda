"""Route 1's host tools (docs/iphone-mode.md) where they need no toolchain, no kernel tree and no Shanling file:
tools/iap_probe/probe_build.py's test tone and command line, tools/uac1_module/uac1_build.py's hash and its check that
q2.config was taken. The rest of each (cross-compiling, the kernel tree, the stock kernel's ELF) is marked no mutate
block and run by their own suites: test/iap_probe.py, and uac1_build.py itself against the stock kernel."""
import array
import hashlib
import importlib.util
import math
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT/rel)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


probe_build = load('tools/iap_probe/probe_build.py', 'iap_probe_build')
uac1_build = load('tools/uac1_module/uac1_build.py', 'uac1_module_build')


def test_tone_is_a_whole_number_of_cycles_at_minus_20_dbfs():
    """S16_LE stereo, both channels the same, TONE_S seconds, peak -20 dBFS, ending where it starts so a loop of it
    has no click."""
    pcm = array.array('h', probe_build.tone())
    rate, seconds = probe_build.TONE_RATE, probe_build.TONE_S
    assert len(pcm) == rate * seconds * 2 and pcm[0::2] == pcm[1::2]
    left = pcm[0::2]
    assert abs(20 * math.log10(max(left) / 32767) - probe_build.TONE_DB) < 0.05
    assert left[0] == 0 and abs(left[-1] - round(32767 * 10 ** (probe_build.TONE_DB / 20) *
                                                  math.sin(-2 * math.pi * probe_build.TONE_HZ / rate))) <= 1


def test_main_refuses_a_rootfs_without_libc(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        probe_build.main(['--rootfs', str(tmp_path), '--out', str(tmp_path/'out')])
    assert 'libc-2.28.so' in str(e.value)


def test_main_builds_into_out(tmp_path, monkeypatch, capsys):
    (tmp_path/'lib').mkdir()
    (tmp_path/'lib/libc-2.28.so').write_bytes(b'libc')
    seen = []
    monkeypatch.setattr(probe_build, 'build', lambda rootfs, out: seen.append((rootfs, out)) or out)
    assert probe_build.main(['--rootfs', str(tmp_path), '--out', str(tmp_path/'out')]) == 0
    assert seen == [(tmp_path, tmp_path/'out')] and capsys.readouterr().out == f'{tmp_path/"out"}\n'


def test_sha256_of_a_file(tmp_path):
    f = tmp_path/'f'
    f.write_bytes(b'abc')
    assert uac1_build.sha256(f) == hashlib.sha256(b'abc').hexdigest()


def test_config_mismatches_names_what_did_not_take(tmp_path, monkeypatch):
    """q2.config's settings, set or not set, against the final .config: those the configuration dropped."""
    here = tmp_path/'here'
    here.mkdir()
    (here/'q2.config').write_text('# a comment\nCONFIG_A=y\n# CONFIG_B is not set\nCONFIG_C="+"\nCONFIG_D=y\n')
    src = tmp_path/'src'
    src.mkdir()
    (src/'.config').write_text('#\n# Linux/mips 5.10.21 Kernel Configuration\nCONFIG_A=y\nCONFIG_B=y\nCONFIG_C="+"\n'
                               '# CONFIG_D is not set\nCONFIG_E=m\n')
    monkeypatch.setattr(uac1_build, 'HERE', here)
    assert uac1_build.config_mismatches(src) == ['CONFIG_B=n (got y)', 'CONFIG_D=y (got n)']
    (src/'.config').write_text('CONFIG_A=y\nCONFIG_C="+"\nCONFIG_D=y\n')
    assert uac1_build.config_mismatches(src) == []
