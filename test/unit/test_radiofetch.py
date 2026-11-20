"""The local radio fetcher's core (patch/radiofetch_core.c, docs/internals.md#local-radio-fetch), built for this
machine: the bundled tables (assets/radio/), the JSON walker, and each source's reader over its answers as recorded
in test/fixtures/radiofetch/ (Chicago, 2026-11-20). The program itself, its transfers and its cache, is
test/radiofetch.py's: it runs processes and a server, which this folder's tests may not."""
import ctypes as C
import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT/'test/fixtures/radiofetch'
DATA = ROOT/'assets/radio'
CHICAGO = (41.8708, -87.6242)
RF_STATIONS, RF_STREAMS = 96, 4


class Stream(C.Structure):
    _fields_ = [('src', C.c_char * 16), ('url', C.c_char * 300)]


class Station(C.Structure):
    _fields_ = [('call', C.c_char * 8), ('base', C.c_char * 6), ('name', C.c_char * 48), ('city', C.c_char * 40),
                ('tunein', C.c_char * 16), ('freq', C.c_ushort), ('flags', C.c_ubyte), ('km', C.c_float),
                ('reach', C.c_float),
                ('n', C.c_int), ('s', Stream * RF_STREAMS)]


class List(C.Structure):
    _fields_ = [('lat', C.c_double), ('lon', C.c_double), ('n', C.c_int), ('st', Station * RF_STATIONS)]


JSON_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_int, C.c_int, C.c_char_p, C.c_char_p)


@pytest.fixture(scope='module')
def L(tmp_path_factory):
    out = tmp_path_factory.mktemp('radiofetch')/'core.so'
    subprocess.run(['clang', '-DRF_HOST', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-shared',
                    '-fPIC', ROOT/'patch/radiofetch_core.c', '-o', out, '-lm'], check=True)
    lib = C.CDLL(str(out))
    lib.rf_km.restype = C.c_double
    lib.rf_km.argtypes = [C.c_double] * 4 + [C.POINTER(C.c_double)]
    lib.rf_near.argtypes = [C.c_char_p, C.c_double, C.c_double, C.POINTER(List)]
    lib.rf_zip.argtypes = [C.c_char_p, C.c_char_p, C.POINTER(C.c_double), C.POINTER(C.c_double)]
    lib.rf_geo.argtypes = [C.c_char_p, C.POINTER(C.c_double), C.POINTER(C.c_double)]
    lib.rf_json.argtypes = [C.c_char_p, C.c_uint, JSON_CB, C.c_void_p]
    for name in ('rf_iheart', 'rf_radiobrowser', 'rf_tunein_local'):
        getattr(lib, name).argtypes = [C.POINTER(List), C.c_char_p, C.c_uint]
    lib.rf_tunein_tune.argtypes = [C.POINTER(Station), C.c_char_p, C.c_uint]
    lib.rf_triton.argtypes = [C.POINTER(Station), C.c_char_p, C.c_char_p]
    lib.rf_soundstack.argtypes = [C.POINTER(Station), C.c_char_p]
    lib.rf_add.argtypes = [C.POINTER(Station), C.c_char_p, C.c_char_p]
    lib.rf_text.argtypes = [C.POINTER(List), C.c_char_p, C.c_uint, C.c_long, C.c_int, C.c_char_p]
    lib.rf_header.argtypes = [C.c_char_p, C.POINTER(C.c_long), C.POINTER(C.c_int)]
    lib.rf_key.argtypes = [C.c_char_p, C.c_uint, C.c_char_p, C.c_double, C.c_double]
    lib.rf_state_name.restype = C.c_char_p
    lib.rf_states.argtypes = [C.POINTER(List), C.POINTER(C.c_char_p), C.c_int]
    return lib


def near(L, lat=CHICAGO[0], lon=CHICAGO[1]):
    lst = List()
    assert L.rf_near(str(DATA/'stations.bin').encode(), lat, lon, C.byref(lst)) == lst.n
    return lst


def station(lst, call):
    return next(lst.st[i] for i in range(lst.n) if lst.st[i].call.decode() == call)


def streams(st):
    return [(st.s[k].src.decode(), st.s[k].url.decode()) for k in range(st.n)]


# ---- the tables ----

def test_distance_and_bearing(L):
    b = C.c_double()
    km = L.rf_km(41.8708, -87.6242, 42.8708, -87.6242, C.byref(b))  # a degree north
    assert 110.5 < km < 111.5 and abs(b.value) < 0.01
    L.rf_km(0, 0, 0, -1, C.byref(b))
    assert abs(b.value - 270) < 0.01
    assert L.rf_km(10, 10, 10, 10, None) == 0


def test_stations_reaching_chicago(L):
    lst = near(L)
    calls = [lst.st[i].call.decode() for i in range(lst.n)]
    # the market's big signals, FM before AM, each band by frequency
    for call in ('WBEZ', 'WXRT', 'WBBM-FM', 'WGCI-FM', 'WLEY-FM', 'WGN', 'WBBM', 'WLS'):
        assert call in calls
    am = [bool(lst.st[i].flags & 1) for i in range(lst.n)]
    assert am == sorted(am)
    fm = [lst.st[i].freq for i in range(lst.n) if not am[i]]
    assert fm == sorted(fm)
    wxrt = station(lst, 'WXRT')
    assert (wxrt.base, wxrt.freq, wxrt.city) == (b'WXRT', 931, b'Chicago, IL') and wxrt.km < 10
    assert station(lst, 'WBBM-FM').base == b'WBBM' and station(lst, 'WBBM-FM').flags & 4  # Audacy's
    # a station 140 km off whose contour does not reach downtown (WIIL, Union Grove WI)
    assert 'WIIL' not in calls
    assert lst.n < 70  # contours, not a 100 km circle: about 30 FM and 25 AM


def test_rural_and_open_sea(L):
    lst = near(L, 41.15, -100.81)  # North Platte, Nebraska
    calls = [lst.st[i].call.decode() for i in range(lst.n)]
    assert 'KNPQ' in calls and 'KRVN' in calls and 10 < lst.n < 30
    assert near(L, 30.0, -40.0).n == 0  # mid-Atlantic


def test_table_missing_or_wrong(L, tmp_path):
    lst = List()
    assert L.rf_near(str(tmp_path/'none.bin').encode(), 1, 1, C.byref(lst)) == -1
    (tmp_path/'bad.bin').write_bytes(b'XXXX' + bytes(60))
    assert L.rf_near(str(tmp_path/'bad.bin').encode(), 1, 1, C.byref(lst)) == -1


def test_full_list_keeps_the_nearest(L, tmp_path):
    # 100 stations at one place, a degree apart in latitude south of it, all reaching it: the 96 nearest stay
    import struct
    recs = []
    for i in range(100):
        lat = 40.0 - 0.01 * (100 - i)
        recs.append(struct.pack('<8sHBBiiI16s', f'W{i:03d}'.encode(), 881 + i, 0, 0, round(lat * 1e5),
                                round(-90 * 1e5), 0, bytes([200] * 16)))
    pool = b'Somewhere, IL\0'
    (tmp_path/'s.bin').write_bytes(struct.pack('<4sB3xII', b'Q2RS', 1, 100, 16 + 40 * 100) + b''.join(recs) + pool)
    lst = List()
    assert L.rf_near(str(tmp_path/'s.bin').encode(), 40.0, -90.0, C.byref(lst)) == 96
    calls = {lst.st[i].call.decode() for i in range(lst.n)}
    assert 'W099' in calls and 'W000' not in calls and 'W003' not in calls and 'W004' in calls
    assert lst.st[0].city == b'Somewhere, IL'


def zip_of(L, z):
    lat, lon = C.c_double(), C.c_double()
    r = L.rf_zip(str(DATA/'zcta.bin').encode(), z, C.byref(lat), C.byref(lon))
    return r, round(lat.value, 2), round(lon.value, 2)


def test_zip(L, tmp_path):
    r, lat, lon = zip_of(L, b'60605')
    assert r == 1 and abs(lat - 41.87) < 0.05 and abs(lon + 87.62) < 0.05
    r, lat, lon = zip_of(L, b'60690')  # a Chicago PO box ZIP: the nearest listed one of 606
    assert r == 2 and abs(lat - 41.88) < 0.3 and abs(lon + 87.63) < 0.3
    assert zip_of(L, b'69101')[0] == 1
    for bad in (b'6060', b'606055', b'6O605', b''):
        assert zip_of(L, bad)[0] == 0
    assert zip_of(L, b'00000')[0] == 0  # no ZIP of 000 is listed
    lat = C.c_double()
    assert L.rf_zip(str(tmp_path/'none').encode(), b'60605', C.byref(lat), C.byref(lat)) == -1


# ---- JSON ----

def walk(L, text, n=None):
    events = []

    def cb(ctx, ev, depth, key, val):
        events.append((ev, depth, key.decode(), val.decode()))
    f = JSON_CB(cb)
    data = text.encode() if isinstance(text, str) else text
    ok = L.rf_json(data, len(data) if n is None else n, f, None)
    return ok, events


def test_json_walk(L):
    ok, ev = walk(L, '{"a": [1, "x\\"y", {"b": true}], "c": "\\u00e9\\ud83d\\ude00\\n", "d": null}')
    assert ok == 1
    values = [(d, k, v) for e, d, k, v in ev if e == 4]
    assert values == [(2, '', '1'), (2, '', 'x"y'), (3, 'b', 'true'), (1, 'c', 'é😀\n'), (1, 'd', 'null')]
    assert [e for e, *_ in ev][:3] == [0, 2, 4]  # object, array, value
    assert ev[-1] == (1, 0, '', '')              # the root closes at depth 0


def test_json_cut_short(L):
    text = '[{"name": "one", "url": "http://a"}, {"name": "two", "url": "http://b'
    ok, ev = walk(L, text)
    assert ok == 0
    assert [v for e, d, k, v in ev if e == 4] == ['one', 'http://a', 'two']
    assert walk(L, '[1, 2')[0] == 0                  # a number the end may have cut is not reported
    assert walk(L, '{"a": 1}]')[0] == 1              # the text ends with the root
    assert walk(L, '[}')[0] == 0 and walk(L, '')[0] == 0
    assert walk(L, '[' * 30)[0] == 0                  # past the depth it keeps


def test_geolocation_answers(L):
    lat, lon = C.c_double(), C.c_double()
    for body in ('{"ip":"x","loc":"41.8886,-87.7986","city":"Oak Park"}',
                 '{"latitude":41.8886,"longitude":-87.7986}', '{"latitude":"41.8886","longitude":"-87.7986"}',
                 '{"lat":41.8886,"lon":-87.7986}'):
        assert L.rf_geo(body.encode(), C.byref(lat), C.byref(lon)) == 1
        assert (round(lat.value, 4), round(lon.value, 4)) == (41.8886, -87.7986)
    for body in ('{"latitude":0,"longitude":0}', '{"latitude":95,"longitude":1}', '{"latitude":40}',
                 '{"loc":"41.8"}', '<html>', '{"latitude":"n/a","longitude":"1"}'):
        assert L.rf_geo(body.encode(), C.byref(lat), C.byref(lon)) == 0


# ---- the sources ----

def test_iheart(L):
    lst = near(L)
    body = (FIX/'iheart.json').read_bytes()
    found = L.rf_iheart(C.byref(lst), body, len(body))
    assert found >= 30
    wxrt = station(lst, 'WXRT')
    assert wxrt.name == b'93XRT'
    assert streams(wxrt) == [('iheart', 'https://live.amperwave.net/direct/audacy-wxrtfmaac-imc'),
                             ('iheart', 'http://live.amperwave.net/direct/audacy-wxrtfmaac-imc')]
    assert streams(station(lst, 'WSCR'))  # 670 AM, by its -AM call letters
    assert not streams(station(lst, 'WTMX'))  # Hubbard's: not on iHeart


def test_names_lose_a_leading_frequency(L):
    lst = near(L)

    def hit(call, name):
        return dict(callLetters=call, name=name, streams=dict(secure_shoutcast_stream=f'https://s/{call}'))
    body = json.dumps(dict(hits=[hit('WLIT-FM', '93.9 Lite FM'), hit('WCHI-FM', 'Rock 95.5'), hit('WGCI-FM', '107.5'),
                                 hit('WBBM-AM', '780 AM - Newsradio'), hit('WVAZ-FM', '102.7 FM V103')])).encode()
    assert L.rf_iheart(C.byref(lst), body, len(body)) == 5
    names = {c: station(lst, c).name for c in ('WLIT-FM', 'WCHI-FM', 'WGCI-FM', 'WBBM', 'WVAZ')}
    assert names == {'WLIT-FM': b'Lite FM', 'WCHI-FM': b'Rock 95.5', 'WGCI-FM': b'107.5', 'WBBM': b'Newsradio',
                     'WVAZ': b'V103'}


def test_radiobrowser(L):
    lst = near(L)
    total = 0
    for st in ('illinois', 'indiana', 'wisconsin'):
        body = (FIX/f'rb_{st}.json').read_bytes()
        total += L.rf_radiobrowser(C.byref(lst), body, len(body))
    assert total >= 10
    assert all(src == 'radiobrowser' for src, _ in streams(station(lst, 'WBEZ')))
    assert streams(station(lst, 'WBEZ'))


def test_radiobrowser_matching(L):
    lst = near(L)

    def match(*entries):
        body = json.dumps([dict(name=n, tags=t, homepage=h, url=u, url_resolved='', lastcheckok=ok)
                           for n, t, h, u, ok in entries]).encode()
        return L.rf_radiobrowser(C.byref(lst), body, len(body))
    assert match(('WBBMFM B96', '', '', 'http://b96', 1)) == 1         # call and suffix run together
    assert streams(station(lst, 'WBBM-FM')) == [('radiobrowser', 'http://b96')]
    assert match(('Newsradio WBBM 780', '', '', 'http://780', 1)) == 1  # both bands: the frequency decides
    assert streams(station(lst, 'WBBM')) == [('radiobrowser', 'http://780')]
    assert match(('WBBM news', '', '', 'http://x', 1)) == 0            # both bands, no frequency: neither
    assert match(('Classic', 'rock', 'https://www.wxrt.com/', 'http://xrt', 1)) == 1  # the home page's host
    assert match(('WXRT backup', '', '', 'http://dead', 0)) == 0       # its last check failed
    assert match(('WINDY CITY', '', '', 'http://w', 1)) == 0           # WIND is a word inside a word
    assert match(('KWXRT', '', '', 'http://k', 1)) == 0                # not at a word's start
    assert match(('WXRT', '', '', 'ftp://x', 1)) == 0                  # not a stream URL the player opens
    assert match(('WXRT', '', '', 'http://xrt', 1)) == 0               # that URL is there already


def test_tunein(L):
    lst = near(L)
    body = (FIX/'tunein_local.json').read_bytes()
    assert L.rf_tunein_local(C.byref(lst), body, len(body)) >= 8
    wdrv = station(lst, 'WDRV')
    assert wdrv.tunein == b's28340' and wdrv.name == b'The Drive'  # by frequency: the only 97.1
    assert station(lst, 'WKQX').tunein == b's30358'                     # "101 WKQX": by call sign
    tune = (FIX/'tunein_tune.json').read_bytes()
    assert L.rf_tunein_tune(C.byref(wdrv), tune, len(tune)) == 1
    assert streams(wdrv) == [('tunein', 'https://playerservices.streamtheworld.com/api/livestream-redirect/'
                                        'WDRVFMAAC.aac')]  # TuneIn's tokened playlist, as the mount's own URL
    st = Station(base=b'WSCR', flags=0)
    aw = b'{"body": [{"url": "https://live.amperwave.net/playlist/audacy-wscrfmaac-imc.m3u?source=TuneIn"}]}'
    assert L.rf_tunein_tune(C.byref(st), aw, len(aw)) == 1
    assert streams(st) == [('tunein', 'https://live.amperwave.net/direct/audacy-wscrfmaac-imc')]
    other = b'{"body": [{"url": "https://ice.example/x"}, {"url": "https://ice.example/y"}]}'
    st = Station()
    assert L.rf_tunein_tune(C.byref(st), other, len(other)) == 1 and streams(st) == [('tunein', 'https://ice.example/x')]


def test_triton_and_soundstack(L):
    st = Station(base=b'WTBC')
    ok, missing = (FIX/'triton_ok.xml').read_bytes(), (FIX/'triton_404.xml').read_bytes()
    assert L.rf_triton(C.byref(st), b'WTBCFMAAC', missing) == 0
    assert L.rf_triton(C.byref(st), b'WTBCFMAAC', ok) == 1
    assert L.rf_triton(C.byref(st), b'WTBCFM', ok) == 1
    assert streams(st) == [('triton', 'https://playerservices.streamtheworld.com/api/livestream-redirect/WTBCFMAAC.aac'),
                           ('triton', 'https://playerservices.streamtheworld.com/api/livestream-redirect/WTBCFM.mp3')]
    st = Station()
    assert L.rf_soundstack(C.byref(st), (FIX/'soundstack.html').read_bytes()) == 1
    assert streams(st) == [('soundstack', 'http://hemnos.cdnstream.com:80/1447_64')]  # "https":0
    page = b'x; streams = [{"format":"iceaac","host":"ais-sa8.cdnstream1.com","id":"pp\\/cj","https":1,"port":443}];'
    assert L.rf_soundstack(C.byref(st), page) == 1 and streams(st)[1] == ('soundstack', 'https://ais-sa8.cdnstream1.com/pp/cj')
    for bad in (b'no player', b'streams = [{"host":"a b","id":"x"}]', b'streams = [{"host":"h","id":""}]',
                b'streams = [{"host":"h","id":"x","port":0,"https":0}]', b'streams = [{"host":"h"'):
        assert L.rf_soundstack(C.byref(st), bad) == 0


def test_add_limits(L):
    st = Station()
    for i in range(RF_STREAMS):
        assert L.rf_add(C.byref(st), b'x', f'http://h/{i}'.encode()) == 1
    assert L.rf_add(C.byref(st), b'x', b'http://h/more') == 0
    st = Station()
    assert L.rf_add(C.byref(st), b'x', b'http://' + b'a' * 300) == 0


def test_states(L):
    lst = near(L)
    out = (C.c_char_p * 3)()
    n = L.rf_states(C.byref(lst), out, 3)
    assert [out[i] for i in range(n)] == [b'Illinois', b'Indiana', b'Wisconsin']
    assert L.rf_state_name(b'PR') == b'Puerto Rico' and L.rf_state_name(b'ON') is None
    assert L.rf_state_name(b'ILL') is None


def test_signal(L):
    lst = near(L)
    L.rf_signal.argtypes = [C.POINTER(Station)]
    wmbi, wppn = station(lst, 'WMBI-FM'), station(lst, 'WPPN')
    # the static model's own figures for these two (static-model.md): 59 and 52.5
    assert abs(L.rf_signal(C.byref(wmbi)) - 59) <= 3 and abs(L.rf_signal(C.byref(wppn)) - 52.5) <= 3
    assert L.rf_signal(C.byref(station(lst, 'WXRT'))) == 80  # 3 km: the top of the range
    far = Station(km=200.0, reach=20.0)
    near_ = Station(km=0.1, reach=20.0)
    assert L.rf_signal(C.byref(far)) == 24 and L.rf_signal(C.byref(near_)) == 80


def test_text_round_trip(L):
    lst = near(L)
    body = (FIX/'iheart.json').read_bytes()
    L.rf_iheart(C.byref(lst), body, len(body))
    station(lst, 'WXRT').name = b'93\tXRT'  # a tab in a name would split the line
    buf = C.create_string_buffer(96 << 10)
    n = L.rf_text(C.byref(lst), buf, len(buf), 1795160000, 1, b'ZIP 60605')
    text = buf.value.decode()
    assert n == len(text)
    lines = text.splitlines()
    assert lines[0] == '#Q2RADIO 2 1795160000 41.8708 -87.6242 complete ZIP 60605'
    wxrt = next(line.split('\t') for line in lines if line.startswith('WXRT\t'))
    assert wxrt[:7] == ['WXRT', 'FM', '93.1', wxrt[3], '80', '93 XRT', 'Chicago, IL'] and float(wxrt[3]) < 10
    assert wxrt[7] == 'iheart=https://live.amperwave.net/direct/audacy-wxrtfmaac-imc'
    assert next(line for line in lines if line.startswith('WBBM\t')).split('\t')[1:3] == ['AM', '780']
    when, whole = C.c_long(), C.c_int()
    assert L.rf_header(text.encode(), C.byref(when), C.byref(whole)) == 1 and (when.value, whole.value) == (1795160000, 1)
    assert L.rf_text(C.byref(lst), buf, 200, 1, 0, b'') == 0  # does not fit
    assert L.rf_header(b'#Q2RADIO 2 5 1.0 2.0 partial \n', C.byref(when), C.byref(whole)) == 1 and whole.value == 0
    for bad in (b'#Q2RADIO 1 5 1 2 complete x', b'#Q2RADIO 2 x', b'#Q2RADIO 2 5 1 2 maybe x', b''):
        assert L.rf_header(bad, C.byref(when), C.byref(whole)) == 0
    key = C.create_string_buffer(32)
    L.rf_key(key, 32, b'60605', 0, 0)
    assert key.value == b'z60605'
    L.rf_key(key, 32, None, 41.8708, -87.6242)
    assert key.value == b'g+41.9-87.6'
