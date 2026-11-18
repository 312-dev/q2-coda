"""Automatic Time Zone (patch/autozone.c, docs/internals.md#automatic-time-zone): with the switch on and the
link up, the zone comes from an IP lookup (a fake libcurl here, never the real service), is matched to TIMEZONES and
set and saved as the time zone page's pick is; with the switch off nothing is sent."""
import ctypes as C
import datetime
import zoneinfo

import pytest

import host
from conftest import Files
from test_radio import Curl

ZONES = [zone for _, zone in host.tools_build.TIMEZONES]
POSIX = {'<-12>12': -12 * 3600, 'CST6': -6 * 3600, '<+0330>-3:30': 12600, '<+12>-12': 12 * 3600}
NOW = 1763467200  # 2025-11-18 12:00 UTC
DAY_MS = 24 * 3600 * 1000


def offset(zone, when):
    """zone's tm_gmtoff at when: Python's tzdata for an IANA name, the fixed offset of TIMEZONES' POSIX rules."""
    if zone in POSIX:
        return POSIX[zone]
    at = datetime.datetime.fromtimestamp(when, zoneinfo.ZoneInfo(zone))
    utc = at.utcoffset()
    assert utc is not None  # an aware datetime's
    return int(utc.total_seconds())


class Q2:
    """The stock side: config.ini as a dict, TZ as toolsSetTimeZone last set it, localtime_r from it, the link, the
    clock in ms, the threads started (not run until asked) and a fake libcurl answering each URL in turn."""
    def __init__(self, fw, autozone=None, answers=((200, b'America/Chicago\n'),), up=True):
        self.fw, self.ini, self.saved, self.tz, self.ms, self.up, self.threads = fw, {}, [], None, 1000, up, []
        if autozone is not None:
            self.ini['AUTOZONE'] = str(autozone)
        self.zone = fw.var('g_time_zone', C.c_ubyte)
        self.zone.value = 22
        self.answers, self.urls = list(answers), []
        fw.mock('toolsReadConfig', self.read)
        fw.mock('write_int_config', self.save)
        fw.mock('toolsSetTimeZone', lambda name, *_: setattr(self, 'tz', C.string_at(name).decode()))
        fw.mock('fw_localtime_r', self.local)
        fw.mock('fw_time', lambda *_: NOW)
        fw.mock('time_now_ms', lambda *_: self.ms)
        fw.mock('networkGetConnectStatus', lambda *_: 1 if self.up else 0)
        fw.mock('get_wifisignal', lambda *_: 3 if self.up else 0)
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: self.threads.append(fn) or 0)
        self.curl = Curl(fw)
        fw.mock('fw_curl_easy_perform', self.perform)
        # the rootfs's tzdata: a name Python knows is a zone file
        Files(fw, {f'/usr/share/zoneinfo/{z}': b'TZif' for z in zoneinfo.available_timezones()})

    def read(self, path, section, key, out, default, *_):
        v = self.ini.get(C.string_at(key).decode(), C.string_at(default).decode()).encode()
        C.memmove(out, v + b'\0', len(v) + 1)

    def save(self, value, section, key, *_):
        self.saved.append((C.string_at(section).decode(), C.string_at(key).decode(), C.c_int(value).value))

    def local(self, when, out, *_):
        C.c_int.from_address(out + 36).value = offset(self.tz or 'UTC', C.c_long.from_address(when).value)
        return out

    def perform(self, c, *_):
        self.urls.append(self.curl.opts[10002])
        self.curl.code, self.curl.body = self.answers.pop(0) if self.answers else (0, b'')
        return self.curl.transfer(c) if self.curl.code else 7  # CURLE_COULDNT_CONNECT

    def poll(self, ms=1000):
        self.ms += ms
        self.fw.fn('autozone_poll', None)()

    def lookup(self):
        """A poll that starts the lookup, its thread run, then the poll that takes its answer."""
        self.poll()
        assert self.threads, 'no lookup started'
        C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.threads.pop())(0)
        self.poll()


def match(fw, zone):
    Q2(fw)
    return fw.fn('autozone_match', C.c_int, C.c_char_p)(zone.encode())


@pytest.mark.parametrize('zone', ['America/Chicago', 'Asia/Shanghai', 'Europe/London', 'UTC', 'Asia/Kolkata'])
def test_a_zone_in_the_list_is_its_own_entry(fw, zone):
    assert ZONES[match(fw, zone)] == zone


@pytest.mark.parametrize('zone, entry', [
    ('America/Winnipeg', 'America/Chicago'),  # US Central rules, not Mexico City's CST6 with no daylight saving
    ('Europe/Paris', 'Europe/Amsterdam'),
    ('Europe/Dublin', 'Europe/London'),
    ('America/Toronto', 'America/New_York'),
    ('Africa/Abidjan', 'UTC'),                # +0 all year: UTC, not London's summer +1
    ('America/Regina', 'CST6'),               # -6 all year
    ('Asia/Singapore', 'Asia/Shanghai'),
])
def test_a_zone_not_in_the_list_takes_the_entry_with_its_offset_and_daylight_saving(fw, zone, entry):
    assert ZONES[match(fw, zone)] == entry


@pytest.mark.parametrize('zone', ['Nowhere/City', '../etc/passwd', '/usr/share/zoneinfo/UTC', ':UTC', 'EST5EDT,M3',
                                  '', 'a' * 80])
def test_an_answer_that_is_no_zone_matches_nothing(fw, zone):
    assert match(fw, zone) == -1


def test_the_match_sets_the_saved_zone_again(fw):
    q = Q2(fw)
    q.zone.value = 38
    fw.fn('autozone_match', C.c_int, C.c_char_p)(b'Europe/Paris')
    assert q.tz == ZONES[38]


def test_chicago_from_the_network_is_set_and_saved_as_the_page_does(fw):
    q = Q2(fw)
    q.lookup()
    chicago = ZONES.index('America/Chicago')
    assert q.urls == ['https://ipinfo.io/timezone']
    assert q.zone.value == chicago and q.tz == 'America/Chicago'
    assert q.saved == [('SYSSET', 'TIMEZONE', chicago)]
    opts = q.curl.opts
    assert (opts[10065], opts[64], opts[81], opts[78], opts[13], opts[99]) == ('/etc/scrobble-ca.pem', 1, 2, 5, 10, 1)


def test_a_failed_lookup_asks_the_fallback(fw):
    q = Q2(fw, answers=[(429, b'Too many requests'), (200, b'Europe/Paris')])
    q.lookup()
    assert q.urls == ['https://ipinfo.io/timezone', 'https://ipapi.co/timezone/']
    assert ZONES[q.zone.value] == 'Europe/Amsterdam'


@pytest.mark.parametrize('answers', [
    [],                                                   # no connection to either
    [(500, b''), (503, b'')],
    [(200, b'<html>blocked</html>'), (200, b'Nowhere/City')],
    [(200, b'x' * 200), (200, b'')],
])
def test_a_failure_keeps_the_zone_and_saves_nothing(fw, answers):
    q = Q2(fw, answers=answers)
    q.lookup()
    assert q.zone.value == 22 and q.saved == []


def test_switch_off_sends_nothing(fw):
    q = Q2(fw, autozone=0)
    for _ in range(5):
        q.poll()
    q.poll(DAY_MS)
    assert q.threads == [] and fw.calls('fw_curl_easy_init') == [] and q.urls == []


def test_no_link_sends_nothing(fw):
    q = Q2(fw, up=False)
    q.poll()
    assert q.threads == []


def test_at_most_once_a_day(fw):
    q = Q2(fw, answers=[(200, b'America/Chicago')] * 3)
    q.lookup()
    q.up = False
    q.poll()
    q.up = True
    q.poll()  # a new link, an hour on: no lookup
    q.poll(3600 * 1000)
    assert q.threads == []
    q.poll(DAY_MS)
    assert len(q.threads) == 1


def test_a_failed_lookup_is_tried_again_at_a_new_link(fw):
    q = Q2(fw, answers=[])
    q.lookup()
    q.up = False
    q.poll()
    q.up = True
    q.poll(10 * 60 * 1000)
    assert len(q.threads) == 1


def test_switch_turned_on_looks_up_at_once(fw):
    q = Q2(fw, answers=[(200, b'America/Chicago')] * 2)
    q.lookup()
    fw.fn('autozone_due', None)()
    q.poll()
    assert len(q.threads) == 1


def test_switch_defaults_on(fw):
    Q2(fw)
    assert fw.fn('autozone_enabled', C.c_int)() == 1


def test_a_running_lookup_keeps_the_link(fw):
    q = Q2(fw)
    q.poll()
    assert fw.fn('autozone_busy', C.c_int)() == 1
    C.CFUNCTYPE(C.c_void_p, C.c_void_p)(q.threads.pop())(0)
    q.poll()
    assert fw.fn('autozone_busy', C.c_int)() == 0


def test_the_lookups_own_write_leaves_the_switch_on(fw):
    q = Q2(fw)
    q.lookup()
    fw.fn('autozone_config', None, C.c_char_p, C.c_char_p)(b'SYSSET', b'TIMEZONE')
    assert ('Q2POD', 'AUTOZONE', 0) not in q.saved


def test_shipped_default_is_utc_not_shanghai():
    """A fresh install's config.ini (tools/build.py default_config) starts on UTC until the lookup runs."""
    build = host.tools_build
    stock = b'[SYSSET]\nAUTOSYNCTIME=1\nTIMEZONE=22\nCOVERMODE=1\nSTANDBYMODE=1\nWHEELMODE=1\n'
    config = build.default_config(stock)
    assert b'\nTIMEZONE=%d\n' % build.DEFAULT_TIMEZONE in config and b'TIMEZONE=22' not in config
    assert build.TIMEZONES[build.DEFAULT_TIMEZONE][1] == 'UTC'
