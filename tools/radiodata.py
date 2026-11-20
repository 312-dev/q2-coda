#!/usr/bin/env python3
"""The local radio fetcher's bundled tables (docs/internals.md#local-radio-fetch), made from public US data.

    python3 tools/radiodata.py DOWNLOADS            # fetch the four inputs into DOWNLOADS, then write the tables
    python3 tools/radiodata.py DOWNLOADS --offline  # write the tables from what DOWNLOADS already holds

assets/radio/stations.bin is every licensed US full-power and low-power FM station, with its FCC service contour
(the F(50,50) 60 dBu line the Audio Division publishes daily) as 16 radii, and every licensed US AM station with a
radius from its daytime power, all sorted by latitude. assets/radio/zcta.bin is the Census Bureau's ZIP Code
Tabulation Areas, each ZIP's interior point. Both are read by patch/radiofetch_core.c, whose header names the
layouts; run this again to refresh them for a firmware release. Python's standard library only."""
import math
import pathlib
import re
import struct
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT/'assets/radio'
# The inputs: (file name in DOWNLOADS, URL).
FMQ = ('fmq.txt', 'https://transition.fcc.gov/fcc-bin/fmq?call=&fileno=&state=&city=&freq=0.0&fre2=107.9&serv=&vac='
                  '&facid=&asrn=&type=0&list=4&size=9')
AMQ = ('amq.txt', 'https://transition.fcc.gov/fcc-bin/amq?call=&fileno=&state=&city=&freq=530&fre2=1700&type=0'
                  '&list=4&size=9')
CONTOURS = ('fm_contours.zip', 'https://transition.fcc.gov/bureaus/mb/databases/map/FM_service_contour_current.zip')
ZCTA = ('zcta.zip', 'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gaz_zcta_national.zip')
INPUTS = (FMQ, AMQ, CONTOURS, ZCTA)
UA = 'Mozilla/5.0'

MAGIC, VERSION = b'Q2RS', 1
RADII = 16           # contour radii per station, every 22.5 degrees from true north
RECORD = 40          # bytes per station: radiofetch.h RF_RECORD
CALL_MAX = 8         # the call sign, NUL-padded
AM, LPFM, AUDACY, NO_CONTOUR = 1, 2, 4, 8  # flags (radiofetch.h RF_AM ...)
ZIPS = 100000
# An AM station's reach, which the FCC publishes no contour for: 30 km times the square root of its daytime
# kilowatts, within 15 and 150 km (5 kW is 67 km, 50 kW 150). A rough rule of ours, not a propagation model.
AM_KM, AM_MIN, AM_MAX = 30.0, 15.0, 150.0


def download(dest, run=subprocess.run):
    """Each of INPUTS fetched into dest by curl: fcc.gov's front end refuses Python's own HTTP client whatever its
    User-Agent (403, measured 2026-11-20) and serves curl."""
    dest.mkdir(parents=True, exist_ok=True)
    for name, url in INPUTS:
        run(['curl', '-sSfL', '--max-time', '600', '-A', UA, '-o', str(dest/name), url], check=True)


def fields(line):
    """An fmq or amq line's pipe-separated fields, trimmed."""
    return [f.strip() for f in line.split('|')]


def degrees(f, i):
    """The N/S or E/W, degrees, minutes, seconds at f[i:i+4] as signed degrees."""
    value = float(f[i + 1]) + float(f[i + 2]) / 60 + float(f[i + 3]) / 3600
    return -value if f[i] in ('S', 'W') else value


def number(text):
    """The number text starts with ('5.7    kW'), else 0."""
    m = re.match(r'[0-9]+(\.[0-9]*)?', text)
    return float(m.group()) if m else 0.0


def distance_bearing(lat1, lon1, lat2, lon2):
    """Great-circle km and the initial bearing in degrees from the first point to the second."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    km = 2 * 6371.0 * math.asin(math.sqrt(a))
    y, x = math.sin(dl) * math.cos(p2), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return km, math.degrees(math.atan2(y, x)) % 360


def contours(text):
    """{LMS application id: (centre, RADII radii in km)} from the FCC's contour file. Each line is an application,
    its centre, then 360 points, one a degree from true north; a line without all 360 (the file holds a few
    stray HTML lines) is skipped."""
    out = {}
    for line in text.splitlines():
        f = line.split('|')
        if len(f) < 365 or not f[0].strip().isdigit():
            continue
        lat, lon = (float(v) for v in f[4].split(','))
        radii = []
        for k in range(RADII):
            plat, plon = (float(v) for v in f[5 + k * 360 // RADII].split(','))
            radii.append(distance_bearing(lat, lon, plat, plon)[0])
        out[f[2].strip()] = ((lat, lon), radii)
    return out


def city(name, state):
    """'CHICAGO', 'IL' as 'Chicago, IL'."""
    return f'{name.title()}, {state}'


def fm_stations(text, shapes):
    """{call: station} for each US FM (FM, and FL for low power) whose licence (LIC, or STA with no licence) has a
    contour; of several, the licence over a temporary authority, then the larger reach."""
    best = {}
    for line in text.splitlines():
        f = fields(line)
        if len(f) < 40 or f[12] != 'US' or f[3] not in ('FM', 'FL') or f[9] not in ('LIC', 'STA'):
            continue
        shape = shapes.get(f[38])
        if not shape:
            continue
        (lat, lon), radii = shape
        flags = (LPFM if f[3] == 'FL' else 0) | (AUDACY if 'AUDACY' in f[27] else 0)
        st = dict(call=f[1], freq=round(number(f[2]) * 10), flags=flags, lat=lat, lon=lon, radii=radii,
                  city=city(f[10], f[11]), rank=(f[9] == 'LIC', sum(radii)))
        if f[1] not in best or st['rank'] > best[f[1]]['rank']:
            best[f[1]] = st
    return best


def am_reach(kw):
    """An AM station's reach in km from its daytime power (AM_KM)."""
    return max(AM_MIN, min(AM_MAX, AM_KM * math.sqrt(kw)))


def am_stations(text):
    """{call-AM: station} for each licensed US AM station on the air by day (DAY or UNL); of several records, the
    most powerful."""
    best = {}
    for line in text.splitlines():
        f = fields(line)
        if len(f) < 33 or f[12] != 'US' or f[3] != 'AM' or f[9] != 'LIC' or f[5] not in ('DAY', 'UNL'):
            continue
        kw = number(f[14])
        st = dict(call=f[1], freq=round(number(f[2])), flags=AM | NO_CONTOUR | (AUDACY if 'AUDACY' in f[27] else 0),
                  lat=degrees(f, 19), lon=degrees(f, 23), radii=[am_reach(kw)] * RADII, city=city(f[10], f[11]),
                  rank=kw)
        key = f[1] + '-AM'
        if key not in best or kw > best[key]['rank']:
            best[key] = st
    return best


def stations_table(stations):
    """stations.bin: a 16-byte header (MAGIC, VERSION, 3 zero bytes, the count and the city pool's offset, u32s), the
    RECORD-byte stations by latitude, then the pool: each city once, NUL-terminated.
    A station: call (CALL_MAX bytes, NUL-padded), frequency (u16: FM in 100 kHz, AM in kHz), flags (u8), 0,
    latitude and longitude (i32, 1e-5 degrees), the pool offset of its city (u32), RADII radii (u8 km, 255 at most)
    from true north clockwise."""
    pool, where, recs = bytearray(), {}, []
    for st in sorted(stations, key=lambda s: (s['lat'], s['lon'], s['call'])):
        if st['city'] not in where:
            where[st['city']] = len(pool)
            pool += st['city'].encode('latin-1') + b'\0'
        call = st['call'].encode('ascii')[:CALL_MAX - 1]
        radii = bytes(min(255, max(1, round(r))) for r in st['radii'])
        recs.append(struct.pack('<8sHBBiiI16s', call, st['freq'], st['flags'], 0, round(st['lat'] * 1e5),
                                round(st['lon'] * 1e5), where[st['city']], radii))
    start = 16 + RECORD * len(recs)
    return struct.pack('<4sB3xII', MAGIC, VERSION, len(recs), start) + b''.join(recs) + bytes(pool)


def zcta_table(text):
    """zcta.bin: a bitmap of the ZIPs present (ZIPS bits, bit z%8 of byte z//8), then each present ZIP's interior
    point in ZIP order as two i16s, latitude and longitude in hundredths of a degree."""
    bits, points = bytearray(ZIPS // 8), {}
    for line in text.splitlines()[1:]:
        f = line.split('\t')
        if len(f) < 7 or not f[0].isdigit():
            continue
        z = int(f[0])
        bits[z // 8] |= 1 << (z % 8)
        points[z] = (round(float(f[5]) * 100), round(float(f[6]) * 100))
    return bytes(bits) + b''.join(struct.pack('<hh', *points[z]) for z in sorted(points))


def unzipped(path):
    """The one file in the zip at path, as text."""
    with zipfile.ZipFile(path) as z:
        return z.read(z.namelist()[0]).decode('latin-1')


def build(src, out=OUT):
    """Both tables written to out from the inputs in src; their sizes."""
    shapes = contours(unzipped(src/CONTOURS[0]))
    fm = fm_stations((src/FMQ[0]).read_text('latin-1'), shapes)
    am = am_stations((src/AMQ[0]).read_text('latin-1'))
    out.mkdir(parents=True, exist_ok=True)
    stations = stations_table([*fm.values(), *am.values()])
    zips = zcta_table(unzipped(src/ZCTA[0]))
    (out/'stations.bin').write_bytes(stations)
    (out/'zcta.bin').write_bytes(zips)
    return dict(fm=len(fm), am=len(am), stations_bytes=len(stations), zcta_bytes=len(zips))


def main(argv, run=subprocess.run, out=OUT):
    src = pathlib.Path(argv[0])
    if '--offline' not in argv:
        download(src, run)
    print(build(src, out))


if __name__ == '__main__':
    main(sys.argv[1:])
