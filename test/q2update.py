#!/usr/bin/env python3
"""The q2update helper (patch/q2update.sh, docs/adb.md#installing-an-update-over-adb) under this machine's sh, against a
fake demo: a thread that takes its request file as q2update_poll would and answers in the ack file, so the request's
shape and mode, the wait for "taken" and the final word, the output and the exit status are checked with no Q2. demo's
side is test/fw/test_q2update.py. Run it as python3 test/q2update.py (pytest, sh)."""
import os
import pathlib
import stat
import subprocess
import sys
import threading
import time

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1]/'patch/q2update.sh'
INSTALLED = 'V0JYi'


@pytest.fixture
def root(tmp_path):
    """A stand-in /tmp under Q2UPDATE_ROOT, and a usleep on PATH (busybox's; this machine may have none)."""
    (tmp_path/'tmp').mkdir()
    bin_ = tmp_path/'bin'
    bin_.mkdir()
    (bin_/'usleep').write_text('#!/bin/sh\nexec sleep "$(awk "BEGIN { print $1 / 1000000 }")"\n')
    (bin_/'usleep').chmod(0o755)
    return tmp_path


def run(root, *args, wait=None):
    env = dict(os.environ, Q2UPDATE_ROOT=str(root), PATH=f'{root/"bin"}:{os.environ["PATH"]}',
               **({'Q2UPDATE_WAIT': str(wait)} if wait else {}))
    return subprocess.run(['sh', str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)


class Demo:
    """Answers each request with the words in turn, "NONCE WORD INSTALLED UPDATE"; the requests and their modes kept."""
    def __init__(self, root, words, update='-', nonce=None):
        self.root, self.words, self.update, self.nonce = root, list(words), update, nonce
        self.requests, self.modes = [], []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()

    def ack(self, nonce, word):
        tmp = self.root/'tmp/q2update.ack.tmp'
        tmp.write_text(f'{self.nonce or nonce} {word} {INSTALLED} {self.update}\n')
        tmp.rename(self.root/'tmp/q2update.ack')

    def serve(self):
        req = self.root/'tmp/q2update.req'
        while not self.stop.is_set():
            if req.exists():
                self.modes.append(stat.S_IMODE(req.stat().st_mode))
                text = req.read_text()
                req.unlink()
                self.requests.append(text)
                nonce = text.split()[0]
                for word in self.words:
                    time.sleep(.3)
                    self.ack(nonce, word)
            time.sleep(.05)


def test_version_asks_once_and_prints_the_answer(root):
    with Demo(root, ['version']) as demo:
        done = run(root, 'version')
    assert done.returncode == 0 and done.stdout.split()[1:] == ['version', INSTALLED, '-']
    nonce, op = demo.requests[0].split()
    assert op == 'version' and nonce == done.stdout.split()[0] and demo.modes == [0o600]
    assert demo.requests[0].endswith('\n')


def test_install_waits_past_taken_for_installing(root):
    with Demo(root, ['taken', 'installing'], update='V0JZi') as demo:
        done = run(root, 'install')
    assert done.returncode == 0 and done.stdout.split()[1:] == ['installing', INSTALLED, 'V0JZi']
    assert demo.requests[0].split()[1] == 'install'


@pytest.mark.parametrize('word', ['low', 'busy', 'bad', 'latest', 'unknown', 'failed', 'cancelled'])
def test_a_refusal_is_printed_and_exits_1(root, word):
    with Demo(root, ['taken', word]):
        done = run(root, 'check')
    assert done.returncode == 1 and done.stdout.split()[1] == word


def test_check_passing_is_ok(root):
    with Demo(root, ['taken', 'ok'], update='V0JZi'):
        done = run(root, 'check')
    assert done.returncode == 0 and done.stdout.split()[1] == 'ok'


def test_no_demo_is_said_within_seconds_and_the_request_withdrawn(root):
    started = time.monotonic()
    done = run(root, 'install')
    assert done.returncode == 2 and 'no answer from demo' in done.stderr
    assert time.monotonic() - started < 15 and not (root/'tmp/q2update.req').exists()


def test_taken_and_then_silence_ends_at_the_wait(root):
    with Demo(root, ['taken']):
        done = run(root, 'check', wait=2)
    assert done.returncode == 2 and 'taken' in done.stderr


def test_an_answer_to_another_request_is_not_this_ones(root):
    with Demo(root, ['ok'], nonce='12345'):
        done = run(root, 'check')
    assert done.returncode == 2


@pytest.mark.parametrize('args', [[], ['flash'], ['--help']])
def test_usage(root, args):
    done = run(root, *args)
    assert done.returncode == 2 and 'usage' in done.stderr and not (root/'tmp/q2update.req').exists()


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
