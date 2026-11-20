"""tools/radiodata.py, the local radio fetcher's tables (docs/internals.md#local-radio-fetch): FCC and Census lines
of the shapes the real files have, made small, into stations.bin and zcta.bin as patch/radiofetch_core.c reads
them (test/unit/test_radiofetch.py reads the real ones)."""
import math
import struct
import zipfile

import pytest

import radiodata as rd


def fmq(call, mhz, svc='FM', status='LIC', country='US', lms='a1', owner='SOME LICENSEE LLC', city='CHICAGO',
        state='IL'):
    """An fmq list=4 line: the fields radiodata.py reads, in their places, among 41."""
    f = [''] * 41
    f[1], f[2], f[3], f[9], f[10], f[11], f[12] = call, f'{mhz}  MHz', svc, status, city, state, country
    f[14], f[27], f[38] = '5.7    kW', owner, lms
    return '|'.join(f)


def amq(call, khz, kw, hours='UNL', status='LIC', country='US', owner='AM OWNER', lat=('N', 42, 2, 31.4),
        lon=('W', 87, 52, 2.0)):
    f = [''] * 35
    f[1], f[2], f[3], f[5], f[9], f[10], f[11], f[12] = call, f'{khz}   kHz', 'AM', hours, status, 'CHICAGO', 'IL', country
    f[14], f[27] = f'{kw}   kW', owner
    f[19:23], f[23:27] = [str(v) for v in lat], [str(v) for v in lon]
    return '|'.join(f)


def contour(lms, lat, lon, km):
    """A contour line: a circle of km around (lat, lon), 360 points a degree apart."""
    pts = []
    for a in range(360):
        d = km / 6371.0
        p1, l1, b = math.radians(lat), math.radians(lon), math.radians(a)
        p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
        l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
        pts.append(f'{math.degrees(p2):.5f} ,{math.degrees(l2):.5f}')
    return '|'.join(['1234 ', 'FM ', f'{lms} ', '01', f'{lat:.5f} ,{lon:.5f}', *pts, ''])


def test_helpers():
    assert rd.number('5.7    kW') == 5.7 and rd.number('-') == 0 and rd.number('19.') == 19
    assert rd.degrees(['N', '41', '30', '0'], 0) == 41.5 and rd.degrees(['W', '87', '0', '36'], 0) == -87.01
    km, bearing = rd.distance_bearing(41.0, -87.0, 42.0, -87.0)
    assert 110 < km < 112 and bearing == pytest.approx(0, abs=1e-6)
    assert rd.distance_bearing(0, 0, 0, 1)[1] == pytest.approx(90)
    assert rd.city('ELMWOOD PARK', 'IL') == 'Elmwood Park, IL'
    assert rd.am_reach(50) == 150 and rd.am_reach(0.1) == 15 and rd.am_reach(4) == 60


def test_contours():
    text = '\n'.join(['application_id|service|lms_application_id|dts|site|0|1', '<html>', '^|',
                      contour('a1', 41.9, -87.62, 66.0)])
    shapes = rd.contours(text)
    assert list(shapes) == ['a1']
    (lat, lon), radii = shapes['a1']
    assert (lat, lon) == (41.9, -87.62) and len(radii) == rd.RADII
    assert all(r == pytest.approx(66.0, abs=0.1) for r in radii)


def test_fm_stations():
    shapes = {'a1': ((41.9, -87.62), [66.0] * 16), 'a2': ((41.9, -87.62), [70.0] * 16),
              'a3': ((41.8, -87.6), [5.0] * 16), 'a4': ((41.0, -87.0), [30.0] * 16)}
    text = '\n'.join([
        fmq('WBEZ', 91.5, lms='a1', city='CHICAGO'),
        fmq('WBEZ', 91.5, svc='FS', lms='a2'),                  # a booster: not a station
        fmq('WXRT', 93.1, status='STA', lms='a2', owner='AUDACY LICENSE, LLC'),
        fmq('WXRT', 93.1, status='LIC', lms='a1', owner='AUDACY LICENSE, LLC'),  # the licence wins over the STA
        fmq('WLPN-LP', 105.5, svc='FL', lms='a3'),
        fmq('CIXX', 99.9, country='CA', lms='a4'),              # not US
        fmq('WNONE', 88.1, lms='zz'),                           # no contour
        fmq('WAPP', 88.3, status='APP', lms='a4'),              # an application only
        'short|line'])
    st = rd.fm_stations(text, shapes)
    assert sorted(st) == ['WBEZ', 'WLPN-LP', 'WXRT']
    assert st['WBEZ']['freq'] == 915 and st['WBEZ']['city'] == 'Chicago, IL' and st['WBEZ']['flags'] == 0
    assert st['WXRT']['flags'] == rd.AUDACY and st['WXRT']['radii'] == [66.0] * 16
    assert st['WLPN-LP']['flags'] == rd.LPFM
    two = rd.fm_stations('\n'.join([fmq('WAAA', 90.1, lms='a1'), fmq('WAAA', 90.1, lms='a2')]), shapes)
    assert two['WAAA']['radii'] == [70.0] * 16  # of two licences, the larger reach, in either order
    two = rd.fm_stations('\n'.join([fmq('WAAA', 90.1, lms='a2'), fmq('WAAA', 90.1, lms='a1')]), shapes)
    assert two['WAAA']['radii'] == [70.0] * 16


def test_am_stations():
    text = '\n'.join([
        amq('WBBM', 780, 45.0, hours='DAY', owner='AUDACY LICENSE, LLC'),
        amq('WBBM', 780, 43.0, hours='NIG'),       # night: not used
        amq('WBBM', 780, 50.0, hours='UNL'),       # the more powerful daytime record
        amq('WBBM', 780, 5.0, hours='DAY'),        # a weaker one after it
        amq('WSMALL', 1500, 1.0, status='CP'),     # a permit only
        amq('CFOO', 600, 10, country='CA'),
        amq('WLESS', 1600, 4.0, hours='UNL', lat=('S', 10, 0, 0), lon=('E', 20, 0, 0)), 'x|y'])
    st = rd.am_stations(text)
    assert sorted(st) == ['WBBM-AM', 'WLESS-AM']
    w = st['WBBM-AM']
    assert w['call'] == 'WBBM' and w['freq'] == 780 and w['rank'] == 50.0 and w['radii'] == [150.0] * 16
    assert w['flags'] == rd.AM | rd.NO_CONTOUR
    assert st['WLESS-AM']['lat'] == -10 and st['WLESS-AM']['lon'] == 20 and st['WLESS-AM']['radii'][0] == 60


def test_stations_table():
    stations = [dict(call='WXRT', freq=931, flags=rd.AUDACY, lat=41.9, lon=-87.6, radii=[66.4] * 16, city='Chicago, IL'),
                dict(call='KNPQ', freq=1073, flags=0, lat=41.1, lon=-100.8, radii=[0.2] + [300.0] * 15,
                     city='North Platte, NE'),
                dict(call='WBBM', freq=780, flags=rd.AM, lat=42.0, lon=-87.8, radii=[150.0] * 16, city='Chicago, IL')]
    data = rd.stations_table(stations)
    magic, version, count, pool = struct.unpack('<4sB3xII', data[:16])
    assert (magic, version, count) == (b'Q2RS', 1, 3) and pool == 16 + 3 * rd.RECORD
    recs = [struct.unpack('<8sHBBiiI16s', data[16 + i * 40:56 + i * 40]) for i in range(3)]
    assert [r[0].rstrip(b'\0') for r in recs] == [b'KNPQ', b'WXRT', b'WBBM']  # by latitude
    call, freq, flags, _, lat, lon, city, radii = recs[0]
    assert (freq, flags, lat, lon) == (1073, 0, 4110000, -10080000)
    assert radii == bytes([1] + [255] * 15)  # at least 1 km, at most 255
    assert data[pool + city:].split(b'\0')[0] == b'North Platte, NE'
    assert recs[1][6] == recs[2][6]  # Chicago once in the pool
    assert recs[1][7] == bytes([66] * 16)


def test_zcta_table():
    text = '\n'.join(['GEOID\tALAND\tAWATER\tA\tB\tINTPTLAT\tINTPTLONG',
                      '60605\t1\t2\t3\t4\t41.867\t-87.617', '00601\t1\t2\t3\t4\t18.180555\t-66.749961',
                      'bad line', 'XXXXX\t1\t2\t3\t4\t1\t1'])
    data = rd.zcta_table(text)
    assert len(data) == rd.ZIPS // 8 + 8
    assert data[601 // 8] >> (601 % 8) & 1 and data[60605 // 8] >> (60605 % 8) & 1
    assert struct.unpack('<hhhh', data[rd.ZIPS // 8:]) == (1818, -6675, 4187, -8762)


def inputs(src):
    src.mkdir()
    (src/rd.FMQ[0]).write_text(fmq('WBEZ', 91.5, lms='a1'), 'latin-1')
    (src/rd.AMQ[0]).write_text(amq('WBBM', 780, 50.0), 'latin-1')
    with zipfile.ZipFile(src/rd.CONTOURS[0], 'w') as z:
        z.writestr('FM_service_contour_current.txt', contour('a1', 41.9, -87.62, 66.0))
    with zipfile.ZipFile(src/rd.ZCTA[0], 'w') as z:
        z.writestr('2024_Gaz_zcta_national.txt', 'GEOID\tA\tB\tC\tD\tLAT\tLON\n60605\t1\t2\t3\t4\t41.867\t-87.617\n')


def test_build_and_main(tmp_path, capsys):
    inputs(tmp_path/'in')
    sizes = rd.build(tmp_path/'in', tmp_path/'out')
    assert sizes == dict(fm=1, am=1, stations_bytes=16 + 2 * 40 + len(b'Chicago, IL\0'), zcta_bytes=12500 + 4)
    assert (tmp_path/'out/stations.bin').read_bytes()[:4] == b'Q2RS'
    calls = []
    rd.main([str(tmp_path/'in'), '--offline'], run=lambda *a, **k: calls.append(a), out=tmp_path/'out2')
    assert calls == [] and "'fm': 1" in capsys.readouterr().out
    rd.main([str(tmp_path/'in')], run=lambda argv, check: calls.append((argv, check)), out=tmp_path/'out3')
    assert [c[0][-1] for c in calls] == [url for _, url in rd.INPUTS] and all(c[1] for c in calls)
    assert calls[0][0][:4] == ['curl', '-sSfL', '--max-time', '600']
    assert calls[0][0][-2] == str(tmp_path/'in'/rd.FMQ[0])
