"""LocalSend's protocol core (patch/localsend_core.c, docs/localsend.md) built for this machine and driven through
ctypes: JSON, a device's info, the devices found, HTTP heads, chunked bodies, safe names, the receiving session and
the sender's bodies. The texts marked REF are what the reference app (LocalSend CLI 1.18.2) sent in the checks
docs/localsend.md records; test/localsend_e2e.py runs the daemon whole against the CLI itself."""
import ctypes
import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = ROOT / 'patch'
HOST = b'''#include <stddef.h>
#include <sys/random.h>
void ls_random(void *b, size_t n) { (void)!getrandom(b, n, 0); }
'''

# REF: the CLI's announcement and register body, and a prepare-upload, as received
ANNOUNCE = (b'{"alias":"Reference CLI","version":"2.2","deviceModel":"CLI","deviceType":"headless",'
            b'"fingerprint":"1DB16DC121D4079257525664C4087655EFEEE8689C674EDF77DFEFD3F6224417","port":53322,'
            b'"protocol":"https","download":false,"announce":true}')
PREPARE = (b'{"info":{"alias":"Reference CLI","version":"2.2","deviceModel":"CLI","deviceType":"headless",'
           b'"fingerprint":"1DB16DC121D4079257525664C4087655EFEEE8689C674EDF77DFEFD3F6224417","port":53322,'
           b'"protocol":"https","download":false},"files":{"7e515cab-f2ba-4744-9484-c32aaecea987":'
           b'{"id":"7e515cab-f2ba-4744-9484-c32aaecea987","fileName":"to-q2.txt","size":1450,"fileType":"text/plain",'
           b'"sha256":null,"preview":null,"metadata":{"modified":"2026-11-18T18:58:05.123Z","accessed":null}}}}')


class Info(ctypes.Structure):
    _fields_ = [('alias', ctypes.c_char * 64), ('model', ctypes.c_char * 32), ('type', ctypes.c_char * 16),
                ('fp', ctypes.c_char * 65), ('version', ctypes.c_char * 8), ('port', ctypes.c_int),
                ('https', ctypes.c_int), ('download', ctypes.c_int), ('announce', ctypes.c_int)]


class Peer(ctypes.Structure):
    _fields_ = [('info', Info), ('ip', ctypes.c_char * 16), ('seen_ms', ctypes.c_uint32)]


class Http(ctypes.Structure):
    _fields_ = [('method', ctypes.c_char * 8), ('path', ctypes.c_char * 256), ('query', ctypes.c_char * 768),
                ('ctype', ctypes.c_char * 64), ('status', ctypes.c_int), ('length', ctypes.c_longlong),
                ('chunked', ctypes.c_int)]


class Chunk(ctypes.Structure):
    _fields_ = [('st', ctypes.c_int), ('left', ctypes.c_ulonglong), ('line', ctypes.c_char * 20), ('ln', ctypes.c_int)]


class File(ctypes.Structure):
    _fields_ = [('id', ctypes.c_char * 72), ('token', ctypes.c_char * 72), ('name', ctypes.c_char * 512),
                ('sha', ctypes.c_char * 65), ('type', ctypes.c_char * 48), ('size', ctypes.c_longlong),
                ('done', ctypes.c_longlong), ('state', ctypes.c_int)]


class Session(ctypes.Structure):
    _fields_ = [('id', ctypes.c_char * 72), ('from_', Info), ('f', ctypes.POINTER(File)), ('n', ctypes.c_int),
                ('want', ctypes.c_int), ('total', ctypes.c_longlong)]


SKIP, WANT, BUSY, DONE, FAIL = range(5)


@pytest.fixture(scope='module')
def L(tmp_path_factory):
    d = tmp_path_factory.mktemp('localsend')
    (d / 'host.c').write_bytes(HOST)
    so = d / 'core.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DLS_HOST', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-I', str(PATCH), str(PATCH / 'localsend_core.c'), str(d / 'host.c'),
                    '-o', str(so)], check=True)
    lib = ctypes.CDLL(str(so))
    lib.ls_info_text.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(Info)]
    lib.ls_info_json.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(Info), ctypes.c_int, ctypes.c_int]
    lib.ls_peer_seen.argtypes = [ctypes.POINTER(Peer), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(Info),
                                 ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p]
    lib.ls_peer_expire.argtypes = [ctypes.POINTER(Peer), ctypes.POINTER(ctypes.c_int), ctypes.c_uint32]
    lib.ls_http_head.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(Http), ctypes.c_int]
    lib.ls_query.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    lib.ls_chunk_feed.argtypes = [ctypes.POINTER(Chunk), ctypes.c_char_p, ctypes.c_int]
    lib.ls_chunk_done.argtypes = [ctypes.POINTER(Chunk)]
    lib.ls_safe_name.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    lib.ls_prepare_parse.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(Session)]
    lib.ls_prepare_reply.argtypes = [ctypes.POINTER(Session), ctypes.c_char_p, ctypes.c_int]
    lib.ls_upload_lookup.argtypes = [ctypes.POINTER(Session), ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
    lib.ls_session_free.argtypes = [ctypes.POINTER(Session)]
    lib.ls_prepare_body.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(Info), ctypes.POINTER(File),
                                    ctypes.c_int]
    lib.ls_prepare_answer.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(Session)]
    return lib


def info(L, text):
    i = Info()
    assert L.ls_info_text(text, len(text), ctypes.byref(i)) == 0
    return i


def me():
    return Info(alias='Vamp "Gray\'s"'.encode(), model=b'Q2', type=b'headless', fp=b'AB' * 32, port=53317)


def test_info_from_the_reference_announcement(L):
    i = info(L, ANNOUNCE)
    assert (i.alias, i.model, i.type, i.port, i.https, i.download, i.announce) == \
        (b'Reference CLI', b'CLI', b'headless', 53322, 1, 0, 1)
    assert i.fp.startswith(b'1DB16DC1') and len(i.fp) == 64
    i = info(L, b'{"alias":"Old","fingerprint":"x","protocol":"http","port":"no"}')
    assert (i.https, i.port, i.announce) == (0, 53317, -1)
    for bad in (b'{"version":"2.2"}', b'{"alias":', b'[1,2]', b'{"alias":"a"', b'{"alias":"a\x01"}', b''):
        assert L.ls_info_text(bad, len(bad), ctypes.byref(Info())) == -1, bad


def test_info_json_round_trips_and_is_what_the_app_reads(L):
    out = ctypes.create_string_buffer(512)
    for with_port, announce, keys in ((1, 1, {'port', 'protocol', 'announce'}), (0, -1, set()), (1, -1, {'port', 'protocol'})):
        n = L.ls_info_json(out, 512, ctypes.byref(me()), with_port, announce)
        d = json.loads(out.raw[:n])
        assert d['alias'] == 'Vamp "Gray\'s"' and d['version'] == '2.2' and d['fingerprint'] == 'AB' * 32
        assert {'port', 'protocol', 'announce'} & set(d) == keys
        if announce == 1:
            assert d['announce'] is True and d['port'] == 53317 and d['protocol'] == 'https'
    assert L.ls_info_json(out, 40, ctypes.byref(me()), 1, 1) == -1


def test_json_strings_unescape_to_utf8(L):
    text = json.dumps({'alias': 'Gräß \U0001f3b5 "q" \\ /\n', 'fingerprint': 'f'}).encode()
    assert info(L, text).alias.decode() == 'Gräß \U0001f3b5 "q" \\ /\n'
    for bad in (rb'{"alias":"\ud83c"}', rb'{"alias":"\u0000"}', rb'{"alias":"\x"}', rb'{"alias":"\udc00x"}'):
        assert L.ls_info_text(bad, len(bad), ctypes.byref(Info())) == -1, bad


def test_peers_skip_self_refresh_evict_and_expire(L):
    tab, n = (Peer * 16)(), ctypes.c_int(0)
    a = info(L, ANNOUNCE)
    assert L.ls_peer_seen(tab, ctypes.byref(n), ctypes.byref(a), b'10.0.0.5', 1000, a.fp) == -1
    assert L.ls_peer_seen(tab, ctypes.byref(n), ctypes.byref(a), b'10.0.0.5', 1000, b'SELF') == 0
    assert L.ls_peer_seen(tab, ctypes.byref(n), ctypes.byref(a), b'10.0.0.6', 2000, b'SELF') == 0
    assert n.value == 1 and tab[0].ip == b'10.0.0.6' and tab[0].seen_ms == 2000
    for k in range(20):
        p = Info(alias=b'p%d' % k, fp=b'%064d' % k)
        L.ls_peer_seen(tab, ctypes.byref(n), ctypes.byref(p), b'10.0.1.%d' % k, 3000 + k, b'SELF')
    assert n.value == 16 and b'p19' in [tab[i].info.alias for i in range(16)]
    assert a.fp not in [tab[i].info.fp for i in range(16)]  # the oldest went first
    assert L.ls_peer_expire(tab, ctypes.byref(n), 3010 + 120000) == 6 and n.value == 10  # p4 to p9, heard before 3010


def head(L, text, resp=0):
    h = Http()
    return L.ls_http_head(text, len(text), ctypes.byref(h), resp), h


def test_http_heads(L):
    req = (b'POST /api/localsend/v2/upload?sessionId=a%20b&fileId=f&token=t HTTP/1.1\r\nhost: x\r\n'
           b'transfer-encoding: chunked\r\ncontent-type: application/octet-stream\r\n\r\nBODY')
    n, h = head(L, req)
    assert n == len(req) - 4 and (h.method, h.path, h.chunked, h.length) == \
        (b'POST', b'/api/localsend/v2/upload', 1, -1)
    q = ctypes.create_string_buffer(64)
    assert L.ls_query(h.query, b'sessionId', q, 64) == 3 and q.value == b'a b'
    assert L.ls_query(h.query, b'token', q, 64) == 1 and L.ls_query(h.query, b'session', q, 64) == -1
    assert L.ls_query(b'a=%zz', b'a', q, 64) == -1 and L.ls_query(b'a=%00', b'a', q, 64) == -1
    n, h = head(L, b'GET /api/localsend/v2/info HTTP/1.1\r\nContent-Length:  12 \r\n\r\n')
    assert n > 0 and h.length == 12 and h.query == b''
    assert head(L, b'GET / HTTP/1.1\r\nHost: x\r\n')[0] == 0  # not all here yet
    n, h = head(L, b'HTTP/1.1 204 No Content\r\ncontent-length: 0\r\n\r\n', 1)
    assert n > 0 and (h.status, h.length) == (204, 0)
    for bad in (b'GET /\r\n\r\n', b'GET / HTTP/1.1\r\nno colon\r\n\r\n', b'GET / HTTP/1.1\r\nContent-Length: -1\r\n\r\n',
                b'GET / HTTP/1.1\r\nContent-Length: 1x\r\n\r\n', b'GET / HTTP/1.1\r\nTransfer-Encoding: gzip\r\n\r\n',
                b'X' * 16384):
        assert head(L, bad)[0] == -1, bad
    assert head(L, b'HTTP/1.1 9\r\n\r\n', 1)[0] == -1


def chunked(body, sizes):
    out, i = b'', 0
    for s in sizes:
        out += b'%x\r\n' % s + body[i:i + s] + b'\r\n'
        i += s
    return out + b'0\r\nx-trailer: 1\r\n\r\n'


@pytest.mark.parametrize('step', [1, 3, 7, 64, 100000])
def test_chunked_bodies_in_any_pieces(L, step):
    body = bytes(range(256)) * 40
    wire = chunked(body, [1, 4095, 2000, len(body) - 6096]) + b'NEXT'
    c, got = Chunk(), b''
    for i in range(0, len(wire), step):
        if L.ls_chunk_done(ctypes.byref(c)):
            break
        buf = ctypes.create_string_buffer(wire[i:i + step], step)
        k = L.ls_chunk_feed(ctypes.byref(c), buf, len(wire[i:i + step]))
        assert k >= 0
        got += buf.raw[:k]
    assert L.ls_chunk_done(ctypes.byref(c)) and got == body


@pytest.mark.parametrize('wire', [b'zz\r\n', b'5\r\nabcdeXY', b'1\r\na\r\n' + b'f' * 40 + b'\r\n', b'\r\n'])
def test_chunked_bodies_malformed(L, wire):
    c = Chunk()
    assert L.ls_chunk_feed(ctypes.byref(c), ctypes.create_string_buffer(wire, len(wire)), len(wire)) == -1


@pytest.mark.parametrize('sent, kept', [
    ('song.flac', 'song.flac'), ('Album/CD1/01 Intro.flac', 'Album/CD1/01 Intro.flac'),
    ('a:b*c?"d"<e>|f\\g.txt', 'a_b_c__d__e__f_g.txt'), ('name. . ', 'name'), ('Ünïcødé 🎵.mp3', 'Ünïcødé 🎵.mp3'),
    ('dir./x', 'dir/x'),
    ('.DS_Store', None), ('Album/._01.flac', None), ('../etc/passwd', None), ('a/../b', None), ('./a', None),
    ('/abs', None), ('a//b', None), ('a/', None), ('', None), ('a\x01b', None), ('a\x7fb', None), ('...', None),
    (' . ', None), ('x' * 256, None), ('/'.join(['d' * 200] * 3), None)])
def test_safe_names(L, sent, kept):
    out = ctypes.create_string_buffer(512)
    n = L.ls_safe_name(sent.encode(), out, 512)
    assert (out.value.decode() if n > 0 else None) == kept


def session(L, body):
    s = Session()
    return L.ls_prepare_parse(body, len(body), ctypes.byref(s)), s


def test_prepare_upload_from_the_reference_and_its_reply(L):
    code, s = session(L, PREPARE)
    assert code == 0 and (s.n, s.want, s.total) == (1, 1, 1450) and s.from_.alias == b'Reference CLI'
    f = s.f[0]
    assert (f.id, f.name, f.size, f.state, f.sha) == (b'7e515cab-f2ba-4744-9484-c32aaecea987', b'to-q2.txt', 1450,
                                                      WANT, b'')
    out = ctypes.create_string_buffer(4096)
    n = L.ls_prepare_reply(ctypes.byref(s), out, 4096)
    r = json.loads(out.raw[:n])
    assert r == {'sessionId': s.id.decode(), 'files': {f.id.decode(): f.token.decode()}}
    assert len(s.id) == 32 and len(f.token) == 32
    idx = ctypes.c_int(-1)
    q = b'sessionId=%s&fileId=%s&token=%s' % (s.id, f.id, f.token)
    assert L.ls_upload_lookup(ctypes.byref(s), q, ctypes.byref(idx)) == 0 and idx.value == 0
    assert L.ls_upload_lookup(ctypes.byref(s), q.replace(f.token, b'0' * 32), ctypes.byref(idx)) == 403
    assert L.ls_upload_lookup(ctypes.byref(s), q.replace(s.id, b'1' * 32), ctypes.byref(idx)) == 403
    assert L.ls_upload_lookup(ctypes.byref(s), b'sessionId=%s&fileId=x&token=y' % s.id, ctypes.byref(idx)) == 403
    assert L.ls_upload_lookup(ctypes.byref(s), b'sessionId=%s' % s.id, ctypes.byref(idx)) == 400
    s.f[0].state = DONE
    assert L.ls_upload_lookup(ctypes.byref(s), q, ctypes.byref(idx)) == 409
    L.ls_session_free(ctypes.byref(s))


def test_prepare_upload_skips_hidden_and_refuses_malformed(L):
    files = {f'i{k}': {'id': f'i{k}', 'fileName': name, 'size': 10, 'fileType': 'x'}
             for k, name in enumerate(['Album/.DS_Store', 'Album/01.flac', '../x', 'Album/02.flac'])}
    body = json.dumps({'info': {'alias': 'Pixel', 'fingerprint': 'P'}, 'files': files}).encode()
    code, s = session(L, body)
    assert code == 0 and (s.n, s.want, s.total) == (4, 2, 20)
    assert [s.f[i].state for i in range(4)] == [SKIP, WANT, SKIP, WANT]
    out = ctypes.create_string_buffer(4096)
    n = L.ls_prepare_reply(ctypes.byref(s), out, 4096)
    assert set(json.loads(out.raw[:n])['files']) == {'i1', 'i3'}
    L.ls_session_free(ctypes.byref(s))
    good = {'id': 'a', 'fileName': 'a', 'size': 1}
    for bad in ({'files': {'a': good}},                                   # no info
                {'info': {'alias': 'P'}, 'files': {}},                    # no files
                {'info': {'alias': 'P'}, 'files': {'a': dict(good, size=-1)}},
                {'info': {'alias': 'P'}, 'files': {'a': dict(good, size='1')}},
                {'info': {'alias': 'P'}, 'files': {'a': {'fileName': 'a', 'size': 1}}},
                {'info': {'alias': 'P'}, 'files': {'a': good, 'b': good}},  # the same id twice
                {'info': {}, 'files': {'a': good}}):
        assert session(L, json.dumps(bad).encode())[0] == 400, bad
    assert session(L, b'{"info":')[0] == 400


def test_sender_bodies(L):
    files = (File * 2)()
    files[0].id, files[0].name, files[0].size, files[0].type = b'f0', b'q2-log.0', 262144, b'text/plain'
    files[1].id, files[1].name, files[1].size, files[1].sha = b'f1', b'battery "log".txt', 10, b'ab' * 32
    out = ctypes.create_string_buffer(8192)
    n = L.ls_prepare_body(out, 8192, ctypes.byref(me()), files, 2)
    d = json.loads(out.raw[:n])
    assert d['info']['port'] == 53317 and d['info']['protocol'] == 'https' and 'announce' not in d['info']
    assert d['files']['f0'] == {'id': 'f0', 'fileName': 'q2-log.0', 'size': 262144, 'fileType': 'text/plain'}
    assert d['files']['f1']['fileName'] == 'battery "log".txt' and d['files']['f1']['sha256'] == 'ab' * 32
    assert d['files']['f1']['fileType'] == 'application/octet-stream'
    assert L.ls_prepare_body(out, 100, ctypes.byref(me()), files, 2) == -1
    # REF: the CLI's answer, UUIDs for the session and each token; f1 declined
    s = Session(f=files, n=2)
    ans = b'{"sessionId":"93386082-938f-437f-8568-d55272adecab","files":{"f0":"caaa6df0-51aa-4f07-8828-8c84f0aa4afb"}}'
    assert L.ls_prepare_answer(ans, len(ans), ctypes.byref(s)) == 0
    assert s.id == b'93386082-938f-437f-8568-d55272adecab' and s.want == 1
    assert (files[0].state, files[0].token, files[1].state) == (WANT, b'caaa6df0-51aa-4f07-8828-8c84f0aa4afb', SKIP)
    assert L.ls_prepare_answer(b'{"files":{}}', 12, ctypes.byref(s)) == -1
