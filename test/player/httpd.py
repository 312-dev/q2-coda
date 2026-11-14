#!/usr/bin/env python3
"""A local web server for the player's stream tests (docs/player-plan.md, phase 5), HTTP or HTTPS.

It serves the files of one directory at /s/PLAN/NAME, NAME a file there and PLAN how to misbehave, a comma list of
(or `-` for none):

    rate=N        send at most N bytes a second (a slow link, for the cache's fill)
    stall=AT:S    stop for S seconds once AT bytes of the file have gone, on the first connection only
    drop=AT       close the connection once AT bytes of the file have gone, on the first connection only
    cut=AT        every connection ends at AT bytes of the file, with the whole length declared (a stream that ends
                  early)
    norange       answer every request with the whole file, 200, and no Accept-Ranges (a server without ranges)
    redirect      answer 302, to the same path without this word

`log` holds each request as (path, Range header or None). Certificates for HTTPS are made once with openssl
(`certificates`): a CA, a server certificate it signs for `localhost`, one it signs for another name, and a
self-signed one for `localhost`.
"""
import http.server, os, pathlib, re, socketserver, ssl, subprocess, threading, time


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a): pass

    def do_GET(self):
        srv = self.server
        srv.log.append((self.path, self.headers.get('Range')))
        m = re.match(r'/s/([^/]+)/([^?]+)', self.path)  # a query (QQ Music's qm_songid) is not the file's name
        path = m and srv.root() / m[2]
        if not m or not path.is_file():
            return self.answer(404, b'')
        plan = {} if m[1] == '-' else dict((k.split('=') + [''])[:2] for k in m[1].split(','))
        if 'redirect' in plan:
            rest = ','.join(k + ('=' + v if v else '') for k, v in plan.items() if k != 'redirect') or '-'
            self.send_response(302); self.send_header('Location', f'/s/{rest}/{self.path.split("/", 3)[3]}'); self.send_header('Content-Length', '0')
            self.end_headers(); return
        size = path.stat().st_size
        first = srv.first.setdefault(m[2], True)
        srv.first[m[2]] = False
        start, end = 0, size - 1
        r = re.match(r'bytes=(\d+)-(\d*)$', self.headers.get('Range') or '')
        if r and 'norange' not in plan:
            start, end = int(r[1]), min(int(r[2]) if r[2] else size - 1, size - 1)
            if start >= size:
                self.send_response(416); self.send_header('Content-Range', f'bytes */{size}'); self.send_header('Content-Length', '0')
                self.end_headers(); return
            self.send_response(206); self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        else:
            self.send_response(200)
        if 'norange' not in plan: self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        stop = end + 1
        if 'cut' in plan: stop = min(stop, int(plan['cut']))
        if 'drop' in plan and first: stop = min(stop, int(plan['drop']))
        stall = [int(x) for x in plan['stall'].split(':')] if 'stall' in plan and first else None
        rate = int(plan['rate']) if 'rate' in plan else 0
        t0, sent = time.monotonic(), 0
        with open(path, 'rb') as f:
            f.seek(start)
            at = start
            try:
                while at < stop:
                    n = min(16384, stop - at)
                    if stall and at < stall[0] <= at + n: n = stall[0] - at or n
                    data = f.read(n)
                    self.wfile.write(data); self.wfile.flush()
                    at += len(data); sent += len(data)
                    if stall and at == stall[0]: time.sleep(stall[1]); stall = None; t0, sent = time.monotonic(), 0
                    if rate: time.sleep(max(0, t0 + sent / rate - time.monotonic()))
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass
        self.close_connection = True

    def answer(self, code, body):
        self.send_response(code); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(root, cert=None):
    """A server on 127.0.0.1 at a free port, on its own thread, serving root (a path, or a callable that gives one when
    asked); cert, a (certificate, key) pair, makes it HTTPS. Returns the server: .port, .log, .shutdown()."""
    srv = Server(('127.0.0.1', 0), Handler)
    srv.root = root if callable(root) else (lambda: pathlib.Path(root))
    srv.log, srv.first, srv.port = [], {}, srv.server_address[1]
    if cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*map(str, cert))
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def certificates(d):
    """In directory d (made once and kept): ca.pem, and the pairs good (localhost, signed by the CA), wrong (another
    name, signed by the CA) and self (localhost, signed by itself), each NAME.pem and NAME.key. Returns d."""
    d = pathlib.Path(d)
    if (d / 'done').exists(): return d
    d.mkdir(parents=True, exist_ok=True)
    def ssl_(*a): subprocess.run(['openssl', *map(str, a)], check=True, capture_output=True, cwd=d)
    ssl_('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-keyout', 'ca.key', '-out', 'ca.pem',
         '-days', '36500', '-subj', '/CN=q2player test CA', '-addext', 'basicConstraints=critical,CA:TRUE',
         '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
    for name, host in (('good', 'localhost'), ('wrong', 'wrong.example')):
        ssl_('req', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-keyout', f'{name}.key', '-out', f'{name}.csr',
             '-subj', f'/CN={host}')
        (d / f'{name}.ext').write_text(f'subjectAltName=DNS:{host}\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n')
        ssl_('x509', '-req', '-in', f'{name}.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-CAcreateserial', '-out', f'{name}.pem',
             '-days', '36500', '-extfile', f'{name}.ext')
    ssl_('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-keyout', 'self.key', '-out', 'self.pem',
         '-days', '36500', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost')
    (d / 'done').touch()
    return d


if __name__ == '__main__':
    import sys
    s = serve(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
    print(f'http://127.0.0.1:{s.port}/s/-/NAME')
    threading.Event().wait()
