#!/usr/bin/env python3
"""q2scrobd (scrobd/q2scrobd.c, docs/vamp-os-services.md#scrobbling) built for this machine and run against a stand-in
Q2PLAY/1 that sends it now lines: a listen (half the song, at most 240 s, for a song over 30 s) logged once in Rockbox's
AudioScrobbler 1.1 format with the library row's track number; seeks and pauses not counted; a short song, an
artist-less one not logged; .scrobble.ini's ALBUM_ARTIST and PERCENT; a song played again logged again; and the upload
(`q2scrobd upload`, and AUTO=1's) against a stand-in HTTPS server with a CA made for the run, never a real key or
host: tools/player.py's host mbedTLS, the image's release and settings, is linked in. Run it as python3 test/scrobd.py
(pytest). Q2SCROBD_SOURCE builds another copy, to show a broken one fails here."""
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import player  # noqa: E402
SOURCE = pathlib.Path(os.environ.get('Q2SCROBD_SOURCE') or ROOT/'scrobd/q2scrobd.c')


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('scrobd')
    (out/'q2scrobd.c').write_bytes(SOURCE.read_bytes())
    inc = sorted(pathlib.Path(os.environ.get('Q2_THIRDPARTY_CACHE', pathlib.Path.home()/'.cache/q2-thirdparty')).glob('out/sqlite-*/usr/include'))
    lib = next(pathlib.Path('/usr/lib').glob('*-linux-gnu/libsqlite3.so.0'))
    tls = player.mbedtls('host')  # tools/player.py's: the image's mbedTLS release and settings, built for this machine
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', *(['-isystem', str(inc[-1])] if inc else []),
                    '-isystem', str(tls/'include'), '-o', str(out/'q2scrobd'), str(out/'q2scrobd.c'), str(lib),
                    *(str(tls/'lib'/a) for a in ('libmbedtls.a', 'libmbedx509.a', 'libmbedcrypto.a'))], check=True)
    return out/'q2scrobd'


class Play:
    """Q2PLAY/1's socket: greets, takes watch and now, then sends what the test gives it"""
    def __init__(self, root):
        (root/'run/vamp').mkdir(parents=True)
        self.s = socket.socket(socket.AF_UNIX)
        self.s.bind(str(root/'run/vamp/play.sock'))
        self.s.listen(1)
        self.s.settimeout(5)

    def accept(self):
        self.c, _ = self.s.accept()
        self.c.recv(100)
        self.c.sendall(b'Q2PLAY/1 ok\nok\n')

    def now(self, id, pos, len, state=1, title='Alpha', artist='Ann', album='First%20Light'):
        self.c.sendall(f'now title={title} artist={artist} album={album} state={state} pos={pos} len={len} volume=30 id={id}\n'.encode())


@pytest.fixture
def run(binary, tmp_path):
    for d in ('mnt/mmc', 'mnt/data/vamp'):
        (tmp_path/d).mkdir(parents=True)
    db = sqlite3.connect(tmp_path/'mnt/data/vamp/library.db')
    db.execute('create table songtable(id integer primary key, albumsonger, track)')
    db.executemany('insert into songtable values (?, ?, ?)', [(1, 'The Anns', 3), (2, '', 0)])
    db.commit()
    play = Play(tmp_path)
    p = subprocess.Popen([str(binary), '--root', str(tmp_path)], stderr=subprocess.PIPE, text=True)
    play.accept()
    yield tmp_path, play
    p.terminate()
    p.wait(5)


def log(root, wait=0.4):
    time.sleep(wait)
    f = root/'mnt/mmc/.scrobbler.log'
    return f.read_text().splitlines() if f.exists() else []


def heard(play, id, seconds, len, start=0, **kw):
    for pos in range(start, start + seconds + 1):
        play.now(id, pos, len, **kw)


def test_half_the_song_is_a_listen_logged_once_in_rockbox_s_format(run):
    root, play = run
    heard(play, 1, 19, 40)
    assert log(root) == [], '19 s of a 40 s song is not yet half'
    t = int(time.time())
    heard(play, 1, 10, 40, start=19)
    lines = log(root)
    assert lines[:3] == ['#AUDIOSCROBBLER/1.1', '#TZ/UTC', '#CLIENT/Vamp'] and len(lines) == 4, lines
    f = lines[3].split('\t')
    assert f[:6] == ['Ann', 'First Light', 'Alpha', '3', '40', 'L'] and f[7] == '' and abs(int(f[6]) - (t - 20)) <= 2


def test_long_songs_need_240_s(run):
    root, play = run
    heard(play, 1, 239, 1000)
    assert log(root) == []
    play.now(1, 240, 1000)
    assert len(log(root)) == 4


def test_seeks_and_pauses_are_not_heard(run):
    root, play = run
    play.now(1, 0, 40)
    play.now(1, 30, 40)  # a seek
    for pos in range(30, 40):
        play.now(1, pos, 40, state=2)  # paused, the position moving (a scrub)
    assert log(root) == []


def test_a_short_song_and_one_with_no_artist_are_not_logged(run):
    root, play = run
    heard(play, 2, 30, 30)
    heard(play, 1, 30, 40, artist='')
    assert log(root) == []


def test_album_artist_and_percent_from_the_ini(run):
    root, play = run
    (root/'mnt/mmc/.scrobble.ini').write_text('[SCROBBLE]\r\nALBUM_ARTIST=1\nPERCENT=90\n')
    heard(play, 1, 33, 40)
    assert log(root) == []
    play.now(1, 34, 40)
    lines = log(root)
    assert len(lines) == 4 and lines[3].startswith('The Anns\t')


def test_a_song_played_again_is_logged_again(run):
    root, play = run
    heard(play, 1, 20, 40)
    heard(play, 1, 20, 40)
    assert len(log(root)) == 5


# ---- the upload (patch/scrobble.c's, ported), against a stand-in HTTPS server; never a real key or host ----

import hashlib  # noqa: E402
import http.server  # noqa: E402
import json  # noqa: E402
import ssl  # noqa: E402
import threading  # noqa: E402
import urllib.parse  # noqa: E402

HEADER = '#AUDIOSCROBBLER/1.1\n#TZ/UTC\n#CLIENT/Vamp\n'


def line(i, artist='Ann', album='First', title=None, rating='L', length='200'):
    return f'{artist}\t{album}\t{title or f"Song {i}"}\t{i}\t{length}\t{rating}\t{1700000000 + i}\t\n'


@pytest.fixture(scope='session')
def certs(tmp_path_factory):
    """a CA, a leaf for localhost it signs, and a stranger's self-signed leaf for localhost"""
    d = tmp_path_factory.mktemp('certs')
    o = lambda *a: subprocess.run(['openssl', *a], cwd=d, check=True, capture_output=True)  # noqa: E731
    o('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'ca.key', '-out', 'ca.pem', '-days', '2', '-subj', '/CN=Test CA')
    o('req', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'leaf.key', '-out', 'leaf.csr', '-subj', '/CN=localhost')
    (d/'ext').write_text('subjectAltName=DNS:localhost\n')
    o('x509', '-req', '-in', 'leaf.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-CAcreateserial', '-out', 'leaf.pem', '-days', '2', '-extfile', 'ext')
    o('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'other.key', '-out', 'other.pem', '-days', '2', '-subj', '/CN=localhost',
      '-addext', 'subjectAltName=DNS:localhost')
    return d


class Server:
    """ListenBrainz's and Last.fm's endpoints on one local port: each request kept, each answered as self.answer says"""
    def __init__(self, certs, leaf='leaf'):
        self.got, self.answer = [], {}
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length'])).decode()
                outer.got.append((self.path, dict(self.headers), body))
                form = urllib.parse.parse_qs(body) if self.path == '/2.0/' else {}
                method = form.get('method', [''])[0]
                code, text = outer.answer.get(method or self.path, (200, '{"status":"ok"}'))
                if method == 'auth.getMobileSession' and method not in outer.answer:
                    text = '{"session":{"name":"ann","key":"sk123","subscriber":0}}'
                self.send_response(code)
                self.send_header('Content-Length', str(len(text)))
                self.end_headers()
                self.wfile.write(text.encode())

            def log_message(self, *a):
                pass
        self.srv = http.server.HTTPServer(('127.0.0.1', 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certs/f'{leaf}.pem', certs/f'{leaf}.key')
        self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def urls(self):
        return ['--listenbrainz', f'https://localhost:{self.port}/1/submit-listens', '--lastfm', f'https://localhost:{self.port}/2.0/']


@pytest.fixture
def card(tmp_path, certs):
    for d in ('mnt/mmc', 'etc', 'tmp'):
        (tmp_path/d).mkdir(parents=True, exist_ok=True)
    (tmp_path/'etc/scrobble-ca.pem').write_bytes((certs/'ca.pem').read_bytes())
    return tmp_path


def upload(binary, root, server):
    r = subprocess.run([str(binary), 'upload', '--root', str(root), *server.urls()], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stderr


LB = '[LISTENBRAINZ]\nTOKEN = tok-1\n'
FM = '[LASTFM]\nAPI_KEY=key1\nAPI_SECRET=sec1\nSESSION_KEY=sk9\n'


def lb_body(lines):
    out = []
    for l in lines:
        f = [x.replace('\\', '\\\\').replace('"', '\\"') for x in l.rstrip('\n').split('\t')]
        meta = f'"artist_name":"{f[0]}","track_name":"{f[2]}"' + (f',"release_name":"{f[1]}"' if f[1] else '')
        out.append(f'{{"listened_at":{f[6]},"track_metadata":{{{meta},"additional_info":{{"submission_client":"Vamp"}}}}}}')
    return '{"listen_type":"import","payload":[' + ','.join(out) + ']}'


def fm_body(params, secret='sec1'):
    """a signed call as Last.fm checks it: sorted names and values, then the secret, MD5'd"""
    items = sorted(params.items())
    sig = hashlib.md5((''.join(k + v for k, v in items) + secret).encode()).hexdigest()
    q = lambda s: urllib.parse.quote(s, safe='-_.~')  # noqa: E731
    return ''.join(f'{q(k)}={q(v)}&' for k, v in items) + f'api_sig={sig}&format=json'


def test_listens_go_to_listenbrainz_and_leave_the_log(binary, card, certs):
    s = Server(certs)
    (card/'mnt/mmc/.scrobble.ini').write_text(LB)
    good = [line(1), line(2, album=''), line(3, title='Say "Hi"\\now')]
    (card/'mnt/mmc/.scrobbler.log').write_text(HEADER + good[0] + line(9, rating='S') + good[1] + 'junk\n' + good[2])
    assert upload(binary, card, s)[0] == 0
    assert len(s.got) == 1
    path, head, body = s.got[0]
    assert path == '/1/submit-listens' and head['Authorization'] == 'Token tok-1' and head['Content-Type'] == 'application/json'
    assert body == lb_body(good), 'byte for byte; the album left out where there is none'
    assert json.loads(body)['payload'][2]['track_metadata']['track_name'] == 'Say "Hi"\\now', 'quotes and backslashes escaped'
    assert (card/'mnt/mmc/.scrobbler.log').read_text() == HEADER
    assert (card/'mnt/mmc/.scrobbler.log.sent').read_text() == good[0] + line(9, rating='S') + good[1] + 'junk\n' + good[2]


def test_last_fm_with_a_session_key_gets_a_signed_scrobble(binary, card, certs):
    s = Server(certs)
    (card/'mnt/mmc/.scrobble.ini').write_text(FM)
    (card/'mnt/mmc/.scrobbler.log').write_text(HEADER + line(1) + line(2, length='0'))
    assert upload(binary, card, s)[0] == 0
    assert [g[0] for g in s.got] == ['/2.0/']
    want = {'method': 'track.scrobble', 'sk': 'sk9', 'api_key': 'key1', 'artist[0]': 'Ann', 'track[0]': 'Song 1', 'album[0]': 'First',
            'timestamp[0]': '1700000001', 'duration[0]': '200', 'artist[1]': 'Ann', 'track[1]': 'Song 2', 'album[1]': 'First',
            'timestamp[1]': '1700000002'}
    assert s.got[0][2] == fm_body(want), 'no duration for a length of 0'


def test_last_fm_with_a_password_signs_in_once(binary, card, certs):
    s = Server(certs)
    (card/'mnt/mmc/.scrobble.ini').write_text('[LASTFM]\nUSER=ann\nPASSWORD=p w\nAPI_KEY=key1\nAPI_SECRET=sec1\n')
    (card/'mnt/mmc/.scrobbler.log').write_text(HEADER + line(1))
    assert upload(binary, card, s)[0] == 0
    assert s.got[0][2] == fm_body({'method': 'auth.getMobileSession', 'username': 'ann', 'password': 'p w', 'api_key': 'key1'})
    assert urllib.parse.parse_qs(s.got[1][2])['sk'] == ['sk123'], "the session key Last.fm gave"


def test_a_backlog_goes_in_batches_of_50_to_both(binary, card, certs):
    s = Server(certs)
    (card/'mnt/mmc/.scrobble.ini').write_text(LB + FM)
    (card/'mnt/mmc/.scrobbler.log').write_text(HEADER + ''.join(line(i) for i in range(1, 121)))
    assert upload(binary, card, s)[0] == 0
    lb = [len(json.loads(b)['payload']) for p, _, b in s.got if p == '/1/submit-listens']
    fm = [sum(k.startswith('artist[') for k in urllib.parse.parse_qs(b)) for p, _, b in s.got if p == '/2.0/']
    assert lb == [50, 50, 20] and fm == [50, 50, 20]
    assert (card/'mnt/mmc/.scrobbler.log').read_text() == HEADER


@pytest.mark.parametrize('ini, answer', [(LB, {'/1/submit-listens': (500, 'oops')}),
                                         (LB + FM, {'track.scrobble': (200, '{"error":9,"message":"Invalid session key"}')})])
def test_a_batch_a_service_refuses_stays_in_the_log(binary, card, certs, ini, answer):
    s = Server(certs)
    s.answer = answer
    (card/'mnt/mmc/.scrobble.ini').write_text(ini)
    text = HEADER + line(1) + line(2)
    (card/'mnt/mmc/.scrobbler.log').write_text(text)
    code, err = upload(binary, card, s)
    assert code == 1 and 'a batch failed' in err
    assert (card/'mnt/mmc/.scrobbler.log').read_text() == text and not (card/'mnt/mmc/.scrobbler.log.sent').exists()


def test_a_server_the_ca_file_does_not_vouch_for_gets_nothing(binary, card, certs):
    s = Server(certs, leaf='other')
    (card/'mnt/mmc/.scrobble.ini').write_text(LB)
    (card/'mnt/mmc/.scrobbler.log').write_text(HEADER + line(1))
    code, err = upload(binary, card, s)
    assert code == 1 and 'TLS refused' in err and s.got == []
    assert (card/'mnt/mmc/.scrobbler.log').read_text() == HEADER + line(1)


def test_no_account_sends_nothing(binary, card, certs):
    s = Server(certs)
    (card/'mnt/mmc/.scrobble.ini').write_text('[LASTFM]\nAPI_KEY=key1\n')
    (card/'mnt/mmc/.scrobbler.log').write_text(HEADER + line(1))
    assert upload(binary, card, s)[0] == 2 and s.got == []


class Net:
    """Q2NET/1: its status as self.state, each command kept"""
    def __init__(self, root, state='connected'):
        self.state, self.got = state, []
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(str(root/'run/vamp/net.sock'))
        self.srv.listen(4)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            c, _ = self.srv.accept()
            f = c.makefile('rw')
            for l in f:
                l = l.rstrip('\n')
                self.got.append(l)
                f.write('Q2NET/1 ok\n' if l == 'Q2NET/1' else f'status state={self.state} ssid=Home ip= smart=1 held=0\n' if l == 'status' else 'ok\n')
                f.flush()
            c.close()


@pytest.mark.parametrize('state, auto', [('connected', 1), ('off', 1), ('connected', 0)])
def test_the_automatic_upload(binary, tmp_path, certs, state, auto):
    """minutes of 10 ms: a listen's upload due 176 of them on, Wi-Fi asked first and held while it runs"""
    for d in ('mnt/mmc', 'mnt/data/vamp', 'etc', 'tmp'):
        (tmp_path/d).mkdir(parents=True, exist_ok=True)
    (tmp_path/'etc/scrobble-ca.pem').write_bytes((certs/'ca.pem').read_bytes())
    (tmp_path/'mnt/mmc/.scrobble.ini').write_text(LB + f'[SCROBBLE]\nAUTO={auto}\n')
    s = Server(certs)
    play = Play(tmp_path)
    net = Net(tmp_path, state)
    p = subprocess.Popen([str(binary), '--root', str(tmp_path), '--minute-ms', '10', *s.urls()], stderr=subprocess.PIPE, text=True)
    try:
        play.accept()
        heard(play, 2, 21, 40)
        time.sleep(4)
        if state == 'connected' and auto:
            assert len(s.got) == 1 and json.loads(s.got[0][2])['payload'][0]['track_metadata']['track_name'] == 'Alpha'
            assert 'hold 300' in net.got
            assert (tmp_path/'mnt/mmc/.scrobbler.log').read_text() == HEADER
        else:
            assert s.got == []
            assert ('wake' in net.got) == (state == 'off' and bool(auto))
    finally:
        p.terminate()
        p.wait(5)


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
