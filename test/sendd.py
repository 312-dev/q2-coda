#!/usr/bin/env python3
"""q2sendd (sendd/q2sendd.c, docs/vamp-os-services.md#localsend): LocalSend's receive side on Vamp OS. Today's q2send
(patch/localsend.c, patch/localsend_core.c) built for this machine against its OpenSSL, run by q2sendd beside stand-ins
for Q2UI/1 (docs/platform/notices.md: the socket q2ui will serve), Q2NET/1 and Q2SYS/1; a sender made here speaks
LocalSend v2 over TLS with a client certificate. Checked: the question asked as a Q2UI/1 card and the files taken only
on its first choice; a decline, a dismissal, no UI socket and a sender who gives up each refused (a card withdrawn);
a trusted sender taken with a toast instead; nothing taken while the card is a computer's over USB; the radio held;
SIGTERM ending both. Run it as python3 test/sendd.py (pytest). Q2SENDD_SOURCE builds another copy of q2sendd.c, to
show a broken one fails here."""
import hashlib
import json
import os
import pathlib
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2SENDD_SOURCE') or ROOT/'sendd/q2sendd.c')


@pytest.fixture(scope='session')
def build(tmp_path_factory):
    out = tmp_path_factory.mktemp('sendd')
    subprocess.run(['cc', '-DLS_HOST', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-deprecated-declarations', '-Wno-format-truncation',
                    f'-I{ROOT/"patch"}', str(ROOT/'patch/localsend.c'), str(ROOT/'patch/localsend_core.c'),
                    '-lssl', '-lcrypto', '-lpthread', '-o', str(out/'q2send')], check=True)
    (out/'q2sendd.c').write_bytes(SOURCE.read_bytes())
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-o', str(out/'q2sendd'), str(out/'q2sendd.c')], check=True)
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-subj', '/CN=LocalSend User',
                    '-days', '3650', '-keyout', str(out/'peer.key'), '-out', str(out/'peer.crt')], check=True, capture_output=True)
    der = subprocess.run(['openssl', 'x509', '-in', str(out/'peer.crt'), '-outform', 'DER'], check=True, capture_output=True).stdout
    return out, hashlib.sha256(der).hexdigest().upper()


class Stand(threading.Thread):
    """another service's socket: greets VERSION ok, keeps each line, answers by reply(line) (a list of lines)"""
    def __init__(self, path, version, reply):
        super().__init__(daemon=True)
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.bind(str(path))
        self.s.listen(4)
        self.version, self.reply, self.lines, self.conns = version, reply, [], []
        self.start()

    def run(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self.serve, args=(c,), daemon=True).start()

    def serve(self, c):
        f = c.makefile('rw')
        try:
            for line in f:
                line = line.strip()
                out = [f'{self.version} ok'] if line == self.version else (self.lines.append(line) or self.reply(line))
                for o in out:
                    f.write(o + '\n')
                f.flush()
        except (OSError, ValueError):
            pass

    def push(self, line):
        for c in self.conns:
            try:
                c.sendall((line + '\n').encode())
            except OSError:
                pass


class Q2:
    def __init__(self, build, tmp, answer='1', ui=True, usb='adb'):
        self.out, self.fp = build
        self.tmp = tmp
        self.socks = tmp/'vamp'
        self.socks.mkdir(parents=True)
        for d in ('run', 'data', 'card'):
            (tmp/d).mkdir()
        self.answer = answer

        def ui_reply(line):
            if line.startswith('ask '):
                aid = line.split()[1]
                return ['ok'] + ([f'answer {aid} {self.answer}'] if self.answer is not None else [])
            return ['ok']
        self.ui = Stand(self.socks/'ui.sock', 'Q2UI/1', ui_reply) if ui else None
        self.net = Stand(self.socks/'net.sock', 'Q2NET/1', lambda line: ['ok'])
        self.sys = Stand(self.socks/'sys.sock', 'Q2SYS/1', lambda line: [f'usb {usb}'] if line == 'usb' else ['ok'])
        self.port = free_port()
        self.p = subprocess.Popen([str(self.out/'q2sendd'), '--sockets', str(self.socks), '--q2send', str(self.out/'q2send'),
                                   '--run', str(tmp/'run'), '--data', str(tmp/'data'), '--recv', str(tmp/'card/Received'),
                                   '--port', str(self.port), '--alias', 'Vamp'], stderr=subprocess.PIPE, text=True)
        end = time.monotonic() + 15
        while time.monotonic() < end and not port_open(self.port):
            time.sleep(0.1)
        assert port_open(self.port), 'q2send serves its port'

    def send(self, name='song.txt', body=b'hello', timeout=10):
        """prepare-upload for one file, then its upload when taken: the status of each"""
        files = {'f1': {'id': 'f1', 'fileName': name, 'size': len(body), 'fileType': 'text/plain'}}
        info = {'alias': 'Pixel', 'version': '2.2', 'deviceModel': 'Pixel 8', 'deviceType': 'mobile', 'fingerprint': self.fp,
                'port': 53318, 'protocol': 'https', 'download': False}
        st, got = request(self, 'POST', '/api/localsend/v2/prepare-upload', json.dumps({'info': info, 'files': files}).encode(), timeout)
        if st != 200:
            return st, None
        a = json.loads(got)
        q = urllib.parse.urlencode({'sessionId': a['sessionId'], 'fileId': 'f1', 'token': a['files']['f1']})
        return st, request(self, 'POST', f'/api/localsend/v2/upload?{q}', body, timeout)[0]

    def end(self):
        self.p.terminate()
        try:
            return self.p.wait(10)
        finally:
            if self.p.poll() is None:
                self.p.kill()


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def port_open(port):
    try:
        socket.create_connection(('127.0.0.1', port), timeout=0.2).close()
        return True
    except OSError:
        return False


def request(q2, method, path, body, timeout):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # the receiver is pinned by fingerprint, not a CA
    ctx.load_cert_chain(q2.out/'peer.crt', q2.out/'peer.key')
    with socket.create_connection(('127.0.0.1', q2.port), timeout=timeout) as raw, ctx.wrap_socket(raw) as s:
        s.sendall(f'{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n'
                  f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
        data = b''
        try:
            while chunk := s.recv(65536):
                data += chunk
        except (ssl.SSLError, OSError):
            pass
    head, _, rest = data.partition(b'\r\n\r\n')
    return int(head.split()[1]) if head else 0, rest


@pytest.fixture
def q2(build, tmp_path):
    made = []

    def make(**kw):
        made.append(Q2(build, tmp_path/str(len(made)), **kw))
        return made[-1]
    yield make
    for m in made:
        if m.p.poll() is None:
            m.end()


def until(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not fn():
        time.sleep(0.05)
    return fn()


def test_accepted_on_the_card_s_first_choice(q2):
    m = q2(answer='1')
    assert m.send(body=b'hello') == (200, 200)
    assert m.ui.lines[0] == 'ask recv-1 Receive%20files? Pixel%20sends%201%20file%20(5%20B) Accept Decline', m.ui.lines
    assert until(lambda: (m.tmp/'card/Received/song.txt').exists()) and (m.tmp/'card/Received/song.txt').read_bytes() == b'hello'


@pytest.mark.parametrize('answer', ['2', '0', 'gone'])
def test_declined_dismissed_or_gone_is_refused(q2, answer):
    m = q2(answer=answer)
    assert m.send() == (403, None), 'no answer is not yes (docs/platform/notices.md)'
    assert not (m.tmp/'card/Received').exists()


def test_no_ui_socket_is_refused_at_once(q2):
    m = q2(ui=False)
    t = time.monotonic()
    assert m.send() == (403, None)
    assert time.monotonic() - t < 5, 'refused at once, not after the 60 s wait'


def test_a_sender_who_gives_up_withdraws_the_card(q2):
    m = q2(answer=None)  # the card stays open
    assert m.send(timeout=1.5) == (0, None), 'no answer within the sender\'s patience: it hangs up'
    assert until(lambda: any(line == 'withdraw recv-1' for line in m.ui.lines), 8), m.ui.lines


def test_a_trusted_sender_is_told_not_asked(q2, build):
    m = q2(answer='2')
    (m.tmp/'data/trusted').write_text(f'{build[1]}\tPixel\n')
    assert m.send() == (200, 200)
    assert until(lambda: 'toast Receiving%20from%20Pixel' in m.ui.lines) and not any(x.startswith('ask ') for x in m.ui.lines), (m.ui.lines, (m.tmp/'run/q2send.state').read_text())


def test_nothing_taken_while_the_card_is_a_computer_s(q2):
    m = q2(usb='storage')
    assert until(lambda: (m.tmp/'run/q2send.hold').exists()), 'usb storage at the start: held'
    assert m.send() == (403, None) and not any(x.startswith('ask ') for x in m.ui.lines)
    m.sys.push('usb adb')
    assert until(lambda: not (m.tmp/'run/q2send.hold').exists()), 'the card back: taken again'
    m.sys.push('usb storage')
    assert until(lambda: (m.tmp/'run/q2send.hold').exists()), 'a usb storage event: held'


def test_the_radio_is_held_and_sigterm_ends_both(q2):
    m = q2()
    assert until(lambda: any(x.startswith('hold ') for x in m.net.lines)), m.net.lines
    pid = int((m.tmp/'run/q2send.pid').read_text())
    assert m.end() == 0
    assert until(lambda: not pathlib.Path(f'/proc/{pid}').exists() or 'zombie' in pathlib.Path(f'/proc/{pid}/status').read_text().lower()), 'q2send ended too'


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
