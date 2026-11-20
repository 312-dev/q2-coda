#!/usr/bin/env python3
"""The local radio fetcher, q2radiofetch (patch/radiofetch.c, docs/internals.md#local-radio-fetch), built for this
machine against its libcurl and run end to end.

    python3 test/radiofetch.py           # against a local server answering as the services did (no network)
    python3 test/radiofetch.py --live    # against the real services: per-source coverage and timings, cold and warm

The recorded answers are test/fixtures/radiofetch/ (Chicago, 2026-11-20): every source's streams, the deadline's
partial list, the cache's fresh and stale paths and its lock, the geolocation race and the last place. The core's
readers alone are test/unit/test_radiofetch.py's."""
import fcntl
import http.server
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT/'test/fixtures/radiofetch'
DATA = ROOT/'assets/radio'
CA = ROOT/'assets/scrobble-ca.pem'
# --live's places: downtown Chicago, a city in another market and a rural town
PLACES = (('Chicago (--at)', ['--at', '41.8708,-87.6242']), ('Austin TX 78701', ['--zip', '78701']),
          ('North Platte NE', ['--zip', '69101']))


def build(out):
    subprocess.run(['clang', '-DRF_HOST', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    ROOT/'patch/radiofetch.c', ROOT/'patch/radiofetch_core.c', '-o', out/'q2radiofetch', '-lm',
                    '-l:libcurl.so.4'], check=True)
    return out


class Services:
    """A local server standing in for every host: GET /<host>/<path>?<query> is answered from `routes` (full path
    and query first, then the path alone), else 404. A route is (status, body, seconds to wait first)."""

    def __init__(self):
        self.routes, self.seen = {}, []
        services = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                services.seen.append(self.path[1:])
                status, body, delay = services.routes.get(self.path[1:]) or \
                    services.routes.get(self.path[1:].split('?')[0]) or (404, b'not here', 0)
                time.sleep(delay)
                try:
                    self.send_response(status)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:  # the client gave up first
                    pass

            def log_message(self, *a):
                pass
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def chicago(self):
        r = self.routes
        r['api.iheart.com/api/v2/content/liveStations'] = (200, (FIX/'iheart.json').read_bytes(), 0)
        for st in ('Illinois', 'Indiana', 'Wisconsin'):
            r['all.api.radio-browser.info/json/stations/search?countrycode=US&state=' + st +
              '&hidebroken=true&limit=1500'] = (200, (FIX/f'rb_{st.lower()}.json').read_bytes(), 0)
        r['opml.radiotime.com/Browse.ashx'] = (200, (FIX/'tunein_local.json').read_bytes(), 0)
        r['opml.radiotime.com/Tune.ashx?id=s28340&render=json&formats=mp3,aac'] = (200, (FIX/'tunein_tune.json').read_bytes(), 0)
        r['playerservices.streamtheworld.com/api/livestream'] = (200, (FIX/'triton_404.xml').read_bytes(), 0)
        r['playerservices.streamtheworld.com/api/livestream?version=1.9&mount=WTBCFMAAC'] = \
            (200, (FIX/'triton_ok.xml').read_bytes(), 0)
        r['playerservices.streamtheworld.com/api/livestream?version=1.9&mount=WSBCAM'] = \
            (200, (FIX/'triton_ok.xml').read_bytes(), 0)
        r['live.amperwave.net/playlist/audacy-wcfsfmaac-imc.m3u'] = (200, (FIX/'amperwave.m3u').read_bytes(), 0)
        r['live.mystreamplayer.com/WEEFAM'] = (200, (FIX/'soundstack.html').read_bytes(), 0)
        return self

    def close(self):
        self.server.shutdown()


def run(built, web, tmp_path, *args, timeout=20):
    r = subprocess.run([built/'q2radiofetch', '--data', DATA, '--cache', tmp_path/'cache', *args, '-v'],
                       capture_output=True, text=True, timeout=timeout,
                       env={'Q2RF_TEST_BASE': web.base, 'PATH': '/usr/bin:/bin'})
    lines = r.stdout.splitlines()
    rows = {line.split('\t')[0]: line.split('\t') for line in lines[1:]}
    return r, (lines[0] if lines else ''), rows


def sources(row):
    return sorted({f.split('=', 1)[0] for f in row[7:]})


def test_chicago_end_to_end(built, web, tmp_path):
    web.chicago()
    r, head, rows = run(built, web, tmp_path, '--at', '41.8708,-87.6242')
    assert r.returncode == 0, r.stderr
    assert head.startswith('#Q2RADIO 2 ') and ' complete ' in head
    assert sources(rows['WXRT']) == ['iheart', 'radiobrowser']
    assert sources(rows['WTBC-FM']) == ['triton']      # probed: no first-round source has it
    # no AAC mount: the MP3 one, asked for only after the AAC one said 404
    assert rows['WSBC'][7:] == ['triton=https://playerservices.streamtheworld.com/api/livestream-redirect/WSBCAM.mp3']
    assert sources(rows['WCFS-FM']) == ['amperwave'], rows['WCFS-FM']  # Audacy's, probed on Amperwave
    assert rows['WCFS-FM'][7] == 'amperwave=https://live.amperwave.net/direct/audacy-wcfsfmaac-imc'
    assert sources(rows['WEEF']) == ['soundstack']
    assert 'tunein' in sources(rows['WDRV'])
    assert rows['WGCI-FM'][4] == '80' and int(rows['WPPN'][4]) < 60  # the strength: near and strong, far
    assert rows['WGCI-FM'][5] == 'WGCI'  # iHeart's "107.5 WGCI", the frequency the tuner shows already left off
    # the probes only went to stations the first round left without a stream
    probed = {p.split('mount=')[1] for p in web.seen if 'mount=' in p}
    assert 'WTBCFMAAC' in probed and 'WXRTFMAAC' not in probed
    assert {'WSBCAMAAC', 'WSBCAM'} <= probed and 'WSBCAM' not in [p.split('mount=')[1] for p in web.seen
                                                                   if 'mount=' in p][:1]
    assert 'complete' in r.stderr.splitlines()[-1]
    cached = (tmp_path/'cache/g+41.9-87.6.tsv').read_text()
    assert cached == r.stdout
    assert (tmp_path/'cache/last').read_text() == 'g+41.9-87.6'


def test_fresh_cache_needs_no_network(built, web, tmp_path):
    web.chicago()
    first = run(built, web, tmp_path, '--zip', '60605')[0]
    assert first.returncode == 0 and ' ZIP 60605' in first.stdout.splitlines()[0]
    web.seen.clear()
    second = run(built, web, tmp_path, '--zip', '60605')[0]
    assert second.returncode == 0 and second.stdout == first.stdout and web.seen == []


def test_stale_cache_is_printed_then_refreshed(built, web, tmp_path):
    web.chicago()
    cache = tmp_path/'cache'
    cache.mkdir()
    old = '#Q2RADIO 2 1000 41.8700 -87.6200 complete ZIP 60605\nWXRT\tFM\t93.1\t3.1\t80\tOld\tChicago, IL\tx=http://old\n'
    (cache/'z60605.tsv').write_text(old)
    r = run(built, web, tmp_path, '--zip', '60605')[0]
    assert r.returncode == 0 and r.stdout == old  # the old list at once; the new one goes to the cache
    new = (cache/'z60605.tsv').read_text()
    assert new != old and '\nWXRT\tFM\t93.1\t' in new and 'iheart=' in new
    # a partial list is fresh for an hour only
    (cache/'z60605.tsv').write_text(old.replace('1000', str(int(time.time()) - 7200)).replace('complete', 'partial'))
    web.seen.clear()
    run(built, web, tmp_path, '--zip', '60605')
    assert web.seen


def test_out_file_and_an_older_format(built, web, tmp_path):
    web.chicago()
    cache = tmp_path/'cache'
    cache.mkdir()
    (cache/'z60605.tsv').write_text('#Q2RADIO 1 %d 41.87 -87.62 complete ZIP 60605\nWXRT\tFM\t93.1\t3.1\tOld\tChicago, IL\n'
                                    % int(time.time()))
    r = run(built, web, tmp_path, '--zip', '60605', '--out', tmp_path/'list')[0]
    text = (tmp_path/'list').read_text()  # the format before the strength column: fetched again, not shown
    assert r.returncode == 0 and r.stdout == '' and text.startswith('#Q2RADIO 2 ') and 'iheart=' in text
    assert (cache/'z60605.tsv').read_text() == text


def test_stale_cache_refresh_held_by_a_lock(built, web, tmp_path):
    web.chicago()
    cache = tmp_path/'cache'
    cache.mkdir()
    old = '#Q2RADIO 2 1000 41.8700 -87.6200 complete ZIP 60605\n'
    (cache/'z60605.tsv').write_text(old)
    with open(cache/'.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        r = run(built, web, tmp_path, '--zip', '60605')[0]
    assert r.stdout == old and web.seen == [] and (cache/'z60605.tsv').read_text() == old


def test_deadline_gives_a_partial_list(built, web, tmp_path):
    web.chicago()
    web.routes['api.iheart.com/api/v2/content/liveStations'] = (200, (FIX/'iheart.json').read_bytes(), 30)
    t = time.monotonic()
    r, head, rows = run(built, web, tmp_path, '--at', '41.8708,-87.6242', '--deadline', '2500')
    took = time.monotonic() - t
    assert r.returncode == 0 and ' partial ' in head and took < 4.5
    assert 'iheart' not in sources(rows['WXRT']) and sources(rows['WXRT']) == ['radiobrowser']
    assert sources(rows['WTBC-FM']) == ['triton']  # the probes went out at the phase's end regardless


def test_ip_race(built, web, tmp_path):
    web.chicago()
    web.routes['ipinfo.io/json'] = (200, b'{"loc":"41.8886,-87.7986"}', 2)
    web.routes['ipwho.is/'] = (500, b'', 0)
    web.routes['get.geojs.io/v1/ip/geo.json'] = (200, b'{"latitude":"41.8907","longitude":"-87.7908"}', 0)
    r, head, rows = run(built, web, tmp_path)
    assert r.returncode == 0 and 'located by 127.0.0.1' in r.stderr
    assert head.split()[3:5] == ['41.8907', '-87.7908']
    assert (tmp_path/'cache/g+41.9-87.8.tsv').exists()


def test_ip_fails_falls_back_to_the_last_place(built, web, tmp_path):
    web.chicago()
    first = run(built, web, tmp_path, '--zip', '60605')[0]
    r = run(built, web, tmp_path)[0]  # no geolocation service answers
    assert r.returncode == 0 and r.stdout == first.stdout
    (tmp_path/'cache/last').write_text('z99999')
    r = run(built, web, tmp_path)[0]
    assert r.returncode == 1 and 'no list for the last place' in r.stderr
    (tmp_path/'cache/last').write_text('')
    assert run(built, web, tmp_path)[0].returncode == 1


def test_arguments(built, web, tmp_path):
    assert run(built, web, tmp_path, '--zip', '6060')[0].returncode == 2
    assert run(built, web, tmp_path, '--at', '41.8')[0].returncode == 2
    assert run(built, web, tmp_path, '--bogus')[0].returncode == 2
    r = subprocess.run([built/'q2radiofetch', '--data', tmp_path, '--zip', '60605'], capture_output=True, text=True)
    assert r.returncode == 1 and 'no ZIP table' in r.stderr
    r = subprocess.run([built/'q2radiofetch', '--data', tmp_path, '--cache', tmp_path/'c', '--at', '41,-87'],
                       capture_output=True, text=True, env={'Q2RF_TEST_BASE': web.base})
    assert r.returncode == 1 and 'no station table' in r.stderr


def live(built, tmp):
    """Each of PLACES fetched cold (--refresh) and warm, then this network's own place: what each source gave and
    how long the run took."""
    for label, args in [*PLACES, ("this network's IP", [])]:
        for how in ('cold', 'warm'):
            t = time.monotonic()
            r = subprocess.run([built/'q2radiofetch', '--data', DATA, '--cache', tmp/'cache', '--ca', CA, '-v', *args,
                                *(['--refresh'] if how == 'cold' else [])], capture_output=True, text=True, timeout=30)
            took = time.monotonic() - t
            rows = [line.split('\t') for line in r.stdout.splitlines()[1:]]
            by: dict[str, int] = {}
            for row in rows:
                for src in {f.split('=', 1)[0] for f in row[7:]}:
                    by[src] = by.get(src, 0) + 1
            fm, am = [x for x in rows if x[1] == 'FM'], [x for x in rows if x[1] == 'AM']
            print(f'{label:18} {how:4} {took * 1000:6.0f} ms  FM {sum(len(x) > 7 for x in fm)}/{len(fm)}  '
                  f'AM {sum(len(x) > 7 for x in am)}/{len(am)}  {dict(sorted(by.items()))}  exit {r.returncode}')
            if how == 'cold' and r.stderr.strip():
                print('   ', r.stderr.strip().splitlines()[-1])


def main(argv):
    with tempfile.TemporaryDirectory(prefix='q2-radiofetch-') as d:
        tmp = pathlib.Path(d)
        built = build(tmp)
        if '--live' in argv:
            return live(built, tmp)
        tests = [(n, f) for n, f in globals().items() if n.startswith('test_') and callable(f)]
        for name, f in tests:
            web = Services()
            case = tmp/name
            case.mkdir()
            try:
                f(built, web, case)
            finally:
                web.close()
            print('ok', name)
        print(f'{len(tests)} passed')


if __name__ == '__main__':
    main(sys.argv[1:])
