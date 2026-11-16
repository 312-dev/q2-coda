#!/usr/bin/env python3
"""The q2eq helper (patch/q2eq.c, docs/adb.md#q2eq) built for this machine, against a fake demo: a thread that answers
its request file as q2eq_poll would, so the command line, the request it becomes, the wait and the JSON it prints are
checked with no Q2. demo's side is test/fw/test_q2eq.py. Run it as python3 test/q2eq.py (pytest, host cc)."""
import ctypes as C
import json
import os
import pathlib
import stat
import subprocess
import sys
import threading
import time

import pytest

PATCH = pathlib.Path(__file__).resolve().parents[1]/'patch'
(STATE, LIST, EXPORT, ON, PER_OUTPUT, APPLY, DISCARD, RESET, PREAMP, BALANCE, BAND, CHANNEL, TYPE, FREQ, Q, GAIN, LOAD,
 STOCK, SAVE, SAVE_AS, RENAME, DELETE, IMPORT_AS, SIMPLE, SIMPLE_GAIN, SIMPLE_RESET, SIMPLE_PRESET) = range(27)
NOW, REPLACE = 1, 2


class Band(C.Structure):
    _fields_ = [('enabled', C.c_int), ('type', C.c_int), ('frequency', C.c_double), ('gain', C.c_double), ('q', C.c_double)]


class Preset(C.Structure):
    _fields_ = [('count', C.c_int), ('bypass', C.c_int), ('preamp', C.c_double), ('bands', Band * 30),
                ('balance', C.c_double), ('automatic', C.c_int), ('spare', C.c_int)]


class EqState(C.Structure):
    _fields_ = [('mode', C.c_int), ('hold', C.c_int), ('peq', Preset), ('eq3', Preset)]


class Request(C.Structure):
    _fields_ = [('nonce', C.c_uint), *((k, C.c_int) for k in 'op band arg flags'.split()), ('output', C.c_char * 20),
                ('name', C.c_char * 256), ('text', C.c_char * 256)]


class Reply(C.Structure):
    _fields_ = [('nonce', C.c_uint), *((k, C.c_int) for k in 'ok page flag per_output dirty'.split()),
                ('message', C.c_char * 160), ('owner', C.c_char * 20), ('name', C.c_char * 256),
                ('applied', C.c_char * 256), ('automatic', C.c_double), ('state', EqState), ('draft', Preset)]


@pytest.fixture(scope='module')
def binary(tmp_path_factory):
    """The helper with its files under one directory, root, and a 1.5 s wait."""
    out = tmp_path_factory.mktemp('q2eq')
    root = out/'root'
    subprocess.run(['cc', '-DQ2EQ_HOST', f'-DQ2EQ_ROOT="{root}"', '-DQ2EQ_WAIT_MS=1500', '-O1', '-Wall', '-Wextra',
                    '-Werror', '-Wno-unused-parameter', str(PATCH/'q2eq.c'), '-o', str(out/'q2eq')], check=True)
    return out/'q2eq', root


def preset(count=2, bypass=0):
    p = Preset(count, bypass, -3.5)
    p.bands[0] = Band(1, 0, 1000, 2.5, 1.41)
    p.bands[1] = Band(3, 6, 60, 0, 30)
    p.automatic = 1
    return p


class Demo:
    """demo's q2eq_poll, faked: each request answered with reply (its nonce copied), after the text for a list or an
    export; requests is what came, with each file's mode, and imports each import's text and mode."""
    def __init__(self, root, reply=None, text=b'', nonce=None):
        self.root, self.text, self.nonce, self.requests, self.modes, self.imports = root, text, nonce, [], [], []
        self.reply = reply or Reply(ok=1, message=b'Applied', state=EqState(2, 0, preset(), preset(3)), draft=preset())
        self.stop = threading.Event()
        (root/'tmp').mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()

    def run(self):
        request = self.root/'tmp/q2eq.req'
        while not self.stop.wait(0.01):
            if not request.exists():
                continue
            self.modes.append(stat.S_IMODE(request.stat().st_mode))
            copy = self.root/'tmp/q2eq.in'  # an import's text, written before the request
            if copy.exists():
                self.imports.append((copy.read_bytes(), stat.S_IMODE(copy.stat().st_mode)))
                copy.unlink()
            rq = Request.from_buffer_copy(request.read_bytes())
            request.unlink()
            self.requests.append(rq)
            if self.text:
                (self.root/'tmp/q2eq.txt').write_bytes(self.text)
            self.reply.nonce = rq.nonce if self.nonce is None else self.nonce
            (self.root/'tmp/q2eq.rep.tmp').write_bytes(b'Q2EQR01\0' + bytes(self.reply))
            os.rename(self.root/'tmp/q2eq.rep.tmp', self.root/'tmp/q2eq.rep')


def run(binary, *args, stdin=b''):
    return subprocess.run([str(binary[0]), *args], input=stdin, capture_output=True, timeout=30)


@pytest.mark.parametrize('args, op, band, arg, flags, name, text, output', [
    (['current'], STATE, 0, 0, 0, '', '', ''),
    (['list'], LIST, 0, 0, 0, '', '', ''),
    (['outputs'], STATE, 0, 0, 0, '', '', ''),
    (['on'], ON, 0, 1, 0, '', '', ''),
    (['off', '--output', '4.4mm'], ON, 0, 0, 0, '', '', '4.4mm'),
    (['per-output', 'on'], PER_OUTPUT, 0, 1, 0, '', '', ''),
    (['per-output', 'off'], PER_OUTPUT, 0, 0, 0, '', '', ''),
    (['apply'], APPLY, 0, 0, 0, '', '', ''),
    (['discard'], DISCARD, 0, 0, 0, '', '', ''),
    (['reset', '--json'], RESET, 0, 0, 0, '', '', ''),
    (['preamp', 'auto'], PREAMP, 0, 0, 0, '', 'auto', ''),
    (['--now', 'balance', '-1.5'], BALANCE, 0, 0, NOW, '', '-1.5', ''),
    (['band', '3', 'on'], BAND, 2, 1, 0, '', '', ''),
    (['band', '30', 'off'], BAND, 29, 0, 0, '', '', ''),
    (['band', '1', 'channel', 'left'], CHANNEL, 0, 2, 0, '', 'left', ''),
    (['band', '1', 'channel', 'right'], CHANNEL, 0, 3, 0, '', 'right', ''),
    (['band', '2', 'type', 'notch'], TYPE, 1, 6, 0, '', 'notch', ''),
    (['band', '2', 'type', 'allpass'], TYPE, 1, 7, 0, '', 'allpass', ''),
    (['band', '4', 'freq', '440'], FREQ, 3, 0, 0, '', '440', ''),
    (['band', '4', 'q', '0.7'], Q, 3, 0, 0, '', '0.7', ''),
    (['band', '4', 'gain', '-3'], GAIN, 3, 0, 0, '', '-3', ''),
    (['load', 'My EQ'], LOAD, 0, 0, 0, 'My EQ', '', ''),
    (['stock', 'Rock', '--now'], STOCK, 0, 0, NOW, 'Rock', '', ''),
    (['save', '10', '--replace'], SAVE, 0, 9, REPLACE, '', '', ''),
    (['save-as', 'Warm'], SAVE_AS, 0, 0, 0, 'Warm', '', ''),
    (['rename', 'A', 'B'], RENAME, 0, 0, 0, 'A', 'B', ''),
    (['delete', 'A'], DELETE, 0, 0, 0, 'A', '', ''),
    (['export'], EXPORT, 0, 0, 0, '', '', ''),
    (['simple', 'on'], SIMPLE, 0, 1, 0, '', '', ''),
    (['simple', 'off'], SIMPLE, 0, 0, 0, '', '', ''),
    (['simple', 'reset'], SIMPLE_RESET, 0, 0, 0, '', '', ''),
    (['simple', 'high', '2'], SIMPLE_GAIN, 2, 0, 0, '', '2', ''),
    (['simple', 'preset', 'Jazz'], SIMPLE_PRESET, 0, 0, 0, 'Jazz', '', ''),
])
def test_each_command_is_its_request(binary, args, op, band, arg, flags, name, text, output):
    with Demo(binary[1], text=b'x\n') as demo:
        done = run(binary, *args)
    assert done.returncode == 0, done.stderr
    (rq,) = demo.requests
    assert (rq.op, rq.band, rq.arg, rq.flags, rq.name.decode(), rq.text.decode(), rq.output.decode()) == (
        op, band, arg, flags, name, text, output)
    assert demo.modes == [0o600]  # root's alone, as q2eq_poll requires


@pytest.mark.parametrize('args', [[], ['bogus'], ['band', '0', 'on'], ['band', 'x', 'on'], ['band', '1', 'channel', 'off'],
                                  ['band', '1', 'type', 'shelf'], ['band', '1', 'gain'], ['band', '1', 'loud', '3'],
                                  ['save', '11'], ['save', '0'], ['rename', 'A'], ['simple', 'treble', '1'],
                                  ['simple', 'low'], ['per-output', 'maybe'], ['preamp'], ['load']])
def test_a_command_it_does_not_know_prints_usage_and_asks_nothing(binary, args):
    with Demo(binary[1]) as demo:
        done = run(binary, *args)
    assert done.returncode == 2 and done.stderr.startswith(b'usage: q2eq') and not demo.requests


def test_help_lists_every_command(binary):
    done = run(binary, '--help')
    assert done.returncode == 0
    for word in ('current', 'list', 'outputs', 'per-output', 'apply', 'discard', 'reset', 'preamp', 'balance', 'band',
                 'channel', 'type', 'freq', 'gain', 'load', 'stock', 'save', 'save-as', 'rename', 'delete', 'import',
                 'export', 'simple', '--now', '--output', '--replace'):
        assert word.encode() in done.stdout, word


def test_the_state_as_json(binary):
    with Demo(binary[1]):
        done = run(binary, 'current')
    out = json.loads(done.stdout)
    assert out['ok'] and out['message'] == 'Applied' and out['mode'] == 'advanced' and out['output'] is None
    assert out['advanced']['preamp'] == -3.5 and out['advanced']['preamp_auto'] is True
    assert out['advanced']['bands'] == [
        {'band': 1, 'on': True, 'channel': 'both', 'type': 'peaking', 'freq': 1000, 'gain': 2.5, 'q': 1.41},
        {'band': 2, 'on': True, 'channel': 'right', 'type': 'notch', 'freq': 60, 'gain': 0, 'q': 30}]
    assert out['simple'] == {'low': 2.5, 'mid': 0, 'high': 0, 'preamp': -3.5}
    assert out['draft']['dirty'] is False and out['draft']['enabled'] is True and len(out['draft']['preset']['bands']) == 2


def test_names_and_messages_are_escaped(binary):
    reply = Reply(ok=0, message=b'Line 2: "x" \\ \t', name=b'a"b', state=EqState(0, 0, preset(0), preset(0)), draft=preset(0),
                  per_output=1, owner=b'00:11:22:33:44:55')
    with Demo(binary[1], reply) as demo:
        done = run(binary, 'apply')
    out = json.loads(done.stdout)
    assert done.returncode == 1 and not out['ok'] and out['message'] == 'Line 2: "x" \\ \t' and out['draft']['name'] == 'a"b'
    assert out['output'] == '00:11:22:33:44:55' and out['mode'] == 'off' and demo.requests


def test_list_carries_the_names(binary):
    with Demo(binary[1], text=b'B\xc3\xa4ss.txt\nair.TXT\n'):
        done = run(binary, 'list')
    assert json.loads(done.stdout)['presets'] == ['Bäss.txt', 'air.TXT']


def test_outputs_lists_the_remembered_ones(binary):
    outputs = binary[1]/'mnt/data/peq-outputs'
    outputs.mkdir(parents=True, exist_ok=True)
    for name in ('owner', '3.5mm', 'usb', 'usb.tmp', 'AA:BB:CC:DD:EE:FF'):
        (outputs/name).write_bytes(b'x')
    with Demo(binary[1]):
        done = run(binary, 'outputs')
    assert sorted(json.loads(done.stdout)['outputs']) == ['3.5mm', 'AA:BB:CC:DD:EE:FF', 'usb']


def test_another_output_s_remembered_eq_is_read_not_asked_for(binary):
    outputs = binary[1]/'mnt/data/peq-outputs'
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs/'3.5mm').write_bytes(b'Q2PEQ03\0' + bytes(EqState(1, 0, preset(1), preset(3))))
    reply = Reply(ok=1, per_output=1, owner=b'4.4mm', state=EqState(2, 0, preset(), preset(3)), draft=preset())
    with Demo(binary[1], reply) as demo:
        done = run(binary, 'current', '--output', '3.5mm')
        mine = run(binary, 'current', '--output', '4.4mm')
        missing = run(binary, 'current', '--output', 'usb')
        escape = run(binary, 'current', '--output', '../peq-active')
    out = json.loads(done.stdout)
    assert out['remembered'] and out['output'] == '3.5mm' and out['mode'] == 'simple' and len(out['advanced']['bands']) == 1
    assert json.loads(mine.stdout)['output'] == '4.4mm' and 'draft' in json.loads(mine.stdout)
    assert missing.returncode == 2 and escape.returncode == 2
    assert all(not rq.output for rq in demo.requests)  # current asks for the state alone


def test_export_prints_the_draft_s_text_or_a_saved_preset(binary):
    folder = binary[1]/'mnt/mmc/EQ'
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'Warm.txt').write_bytes(b'Preamp: -2 dB\n')
    with Demo(binary[1], text=b'Preamp: -1 dB\n') as demo:
        assert run(binary, 'export').stdout == b'Preamp: -1 dB\n'
        assert run(binary, 'export', 'Warm').stdout == b'Preamp: -2 dB\n'
        assert run(binary, 'export', 'Cold').returncode == 2
    assert [rq.op for rq in demo.requests] == [EXPORT]


def test_import_hands_demo_the_text_under_its_name(binary, tmp_path):
    source = tmp_path/'Sennheiser HD 600 ParametricEQ.txt'
    source.write_bytes(b'Preamp: -6 dB\n')
    with Demo(binary[1]) as demo:
        assert run(binary, 'import', str(source), '--replace').returncode == 0
    assert demo.imports == [(b'Preamp: -6 dB\n', 0o600)]
    (rq,) = demo.requests
    assert rq.op == IMPORT_AS and rq.name == b'Sennheiser HD 600 ParametricEQ' and rq.flags == REPLACE


def test_import_from_stdin_needs_a_name_and_a_file_under_16_kb(binary, tmp_path):
    with Demo(binary[1]) as demo:
        assert json.loads(run(binary, 'import', '-').stdout)['message'] == 'import - needs --name NAME'
        assert run(binary, 'import', '-', '--name', 'Pasted', stdin=b'Preamp: 0 dB\n').returncode == 0
        big = tmp_path/'big.txt'
        big.write_bytes(b'#' * 16385)
        assert json.loads(run(binary, 'import', str(big)).stdout)['message'] == 'Larger than 16 KB'
        assert json.loads(run(binary, 'import', str(tmp_path/'none.txt')).stdout)['message'] == 'Cannot read that file'
    assert [rq.name for rq in demo.requests] == [b'Pasted']


def test_no_answer_is_said_and_the_request_withdrawn(binary):
    (binary[1]/'tmp').mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    done = run(binary, 'apply')
    assert done.returncode == 2 and 'did not answer' in json.loads(done.stdout)['message']
    assert 1.4 < time.monotonic() - started < 10 and not (binary[1]/'tmp/q2eq.req').exists()


def test_an_answer_to_another_request_is_not_this_one_s(binary):
    with Demo(binary[1], nonce=12345) as demo:
        done = run(binary, 'apply')
    assert done.returncode == 2 and demo.requests


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
