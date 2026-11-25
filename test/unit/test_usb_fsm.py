"""The USB port's state machine (patch/usb_fsm.c, docs/internals.md#usb-shadow): its rules one by one, the properties
the design promises over random event traces, and recorded traces from the device's logs replayed through it
(test/fixtures/usb_fsm, each saying what its log has and what was inferred). Built for this machine and driven by
ctypes; the traces are turned into its events by tools/usbtrace_report.py fsm_events, as q2usbtrace_core.c feeds them."""
import ctypes as C
import pathlib
import random
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import usbtrace_report as R  # noqa: E402

FIXTURES = ROOT / 'test/fixtures/usb_fsm'
NEVER = -0x7fffffffffffffff
MS = 1_000_000
S = 1_000_000_000
VBUS, IDLOW, ONLINE, POWER, HOSTDEV, PCM, UDC, TIMER, WAKE, CONNECT = range(10)
ACTIONS = ['grant', 'revoke', 'gadget-on', 'gadget-off', 'cable-in', 'cable-out', 'host-seen', 'charger', 'replug',
           'dac', 'dac-gone']


def define(name):
    text = (ROOT / 'patch/usb_fsm.h').read_text()
    return int(text.split(f'#define {name} ', 1)[1].split()[0])


VBUS_GONE, RESIDUE, RESIDUE_REPLUG, CHARGER = (define(n) for n in ('UF_VBUS_GONE_MS', 'UF_RESIDUE_MS',
                                                                   'UF_RESIDUE_REPLUG_MS', 'UF_CHARGER_MS'))
WINDOW, FRESH, PHANTOM, TRIAL = (define(n) for n in ('UF_WINDOW_MS', 'UF_ID_FRESH_MS', 'UF_ID_PHANTOM_MS', 'UF_TRIAL_MS'))
WRONG, RETRY, VBUS_OFF = (define(n) for n in ('UF_WRONG_MS', 'UF_RETRY_MS', 'UF_VBUS_OFF_MS'))
CONNECT_MS, TRIAL_SLOW = (define(n) for n in ('UF_CONNECT_MS', 'UF_TRIAL_SLOW_MS'))


class Out(C.Structure):
    _fields_ = [('actions', C.c_uint), ('state', C.c_int), ('deadline', C.c_longlong), ('why', C.c_char_p)]


@pytest.fixture(scope='module')
def L(tmp_path_factory):
    return build(tmp_path_factory.mktemp('usb_fsm'))


def build(folder):
    """usb_fsm.c as a library in folder, its functions typed."""
    out = folder / 'usb_fsm.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    ROOT / 'patch/usb_fsm.c', ROOT / 'test/unit/usb_fsm_host.c', '-o', out], check=True)
    lib = C.CDLL(str(out))
    lib.usb_fsm_step.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_longlong, C.POINTER(Out)]
    lib.usb_fsm_step.restype = C.c_uint
    lib.usb_fsm_advance.argtypes = [C.c_void_p, C.c_longlong, C.POINTER(Out)]
    lib.usb_fsm_advance.restype = C.c_uint
    lib.usb_fsm_feed.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_longlong, C.POINTER(Out)]
    lib.usb_fsm_feed.restype = C.c_uint
    lib.usb_fsm_deadline.argtypes = [C.c_void_p]
    lib.usb_fsm_deadline.restype = C.c_longlong
    lib.usb_fsm_state_name.restype = C.c_char_p
    lib.usb_fsm_action_name.restype = C.c_char_p
    lib.usb_fsm_init.argtypes = [C.c_void_p]
    return lib


class Fsm:
    """One machine, its actions logged as (t ns, action, why) with each timer fired at its own deadline."""

    def __init__(self, L, shadow=False):
        self.L, self.f, self.out = L, C.create_string_buffer(L.usb_fsm_size()), Out()
        L.usb_fsm_init(self.f)
        C.c_int.from_buffer(self.f, L.usb_fsm_shadow_offset()).value = int(shadow)  # q2-usbtrace's: stock's power
        self.acts, self.t = [], 0

    def _log(self, a, t):
        for i, name in enumerate(ACTIONS):
            if a & 1 << i:
                self.acts.append((t, name, (self.out.why or b'').decode()))

    def until(self, t):
        while True:
            d = self.L.usb_fsm_deadline(self.f)
            if d == NEVER or d > t:
                break
            self._log(self.L.usb_fsm_step(self.f, TIMER, 0, d, C.byref(self.out)), d)
        self.t = t

    def ev(self, ev, v, t=None):
        t = self.t if t is None else t
        self.until(t)
        self._log(self.L.usb_fsm_step(self.f, ev, v, t, C.byref(self.out)), t)
        return self

    def after(self, ms):
        self.until(self.t + ms * MS)
        return self

    def now_state(self):
        o = Out()
        self.L.usb_fsm_advance(self.f, self.t, C.byref(o))
        return self.L.usb_fsm_state_name(o.state).decode()

    def names(self):
        return [a for _, a, _ in self.acts]

    def at(self, name):
        return [t for t, a, _ in self.acts if a == name]


def idle(L, shadow=False):
    """A machine at rest at t = 60 s: no VBUS, ID high, online 0, nothing on the host port, no VBUS seen leaving."""
    m = Fsm(L, shadow)
    for ev, v in ((VBUS, 0), (IDLOW, 0), (ONLINE, 0), (POWER, 0), (HOSTDEV, 0), (UDC, 0)):
        m.ev(ev, v, 0)
    m.until(60 * S)
    return m


# ---- the rules ----

def test_a_computer_is_charged_and_offered_the_gadget_at_once_and_seen_at_its_address(L):
    m = idle(L).ev(VBUS, 1).after(5).ev(ONLINE, 1).after(100).ev(UDC, 1)
    assert m.names() == ['gadget-on', 'cable-in', 'host-seen'] and m.now_state() == 'host-present'


def test_a_source_that_never_addresses_the_gadget_is_a_charger_at_3_s(L):
    m = idle(L).ev(VBUS, 1).ev(ONLINE, 1).after(10_000)
    assert m.names() == ['gadget-on', 'cable-in', 'charger']
    assert m.at('charger')[0] - m.at('cable-in')[0] == CHARGER * MS
    m.ev(UDC, 1)  # a slow host is still taken
    assert m.names()[-1] == 'host-seen'


def test_pd17_low_with_online_0_is_residue_no_charging_and_one_replug_at_2_s(L):
    m = idle(L).ev(VBUS, 1).ev(ONLINE, 0).after(1000)
    assert m.names() == [] and m.now_state() == 'residue'
    m.after(30_000)
    assert m.names() == ['replug'] and m.at('replug')[0] - 60 * S == RESIDUE_REPLUG * MS


def test_a_slow_pmic_still_makes_a_sink_within_the_debounce(L):
    m = idle(L).ev(VBUS, 1).ev(ONLINE, 0).after(50).ev(ONLINE, 0).after(200).ev(ONLINE, 1)
    assert m.names() == ['gadget-on', 'cable-in']


def test_an_unplug_is_one_cable_out_after_its_bounce(L):
    m = idle(L).ev(VBUS, 1).ev(ONLINE, 1).after(5000)
    m.acts.clear()
    m.ev(VBUS, 0).after(5).ev(VBUS, 1).after(5).ev(VBUS, 0).after(VBUS_GONE - 1)
    assert m.names() == []
    m.after(100)
    assert m.names() == ['gadget-off', 'cable-out'] and m.now_state() == 'idle'


def test_an_accessory_with_no_unplug_in_10_s_is_powered_after_200_ms_and_kept_with_its_device(L):
    m = idle(L).ev(IDLOW, 1).after(FRESH - 1)
    assert m.names() == []
    m.after(1)
    assert m.names() == ['grant']
    m.after(560).ev(HOSTDEV, 1).after(800).ev(PCM, 1)
    m.ev(IDLOW, 0).after(5000)  # the earbuds' ID high while enumerated: kept
    assert m.names() == ['grant', 'dac'] and m.now_state() == 'host'
    m.ev(HOSTDEV, 0)
    assert m.names() == ['grant', 'dac', 'revoke', 'dac-gone']


def test_an_id_low_inside_the_window_waits_the_phantom_rule(L):
    m = idle(L).ev(VBUS, 1).ev(ONLINE, 1).after(5000).ev(VBUS, 0).ev(ONLINE, 0).after(3000)
    m.acts.clear()
    m.ev(IDLOW, 1).after(PHANTOM - 1)
    assert m.names() == []
    m.after(1)
    assert m.names() == ['grant']


def test_a_trial_with_nothing_on_the_host_port_ends_says_replug_tries_once_more_then_refuses(L):
    m = idle(L).ev(IDLOW, 1).after(FRESH + TRIAL)
    assert m.names() == ['grant', 'revoke']
    m.after(WRONG)
    assert m.names()[-1] == 'replug'
    m.after(RETRY - WRONG)
    assert m.names()[-1] == 'grant'
    m.after(TRIAL + RETRY + 60_000)
    assert m.names() == ['grant', 'revoke', 'replug', 'grant', 'revoke'] and m.now_state() == 'refused'
    m.ev(IDLOW, 0).after(10).ev(IDLOW, 1).after(FRESH + 1)  # an ID high ends the refusal
    assert m.names()[-1] == 'grant'


def test_a_trial_with_no_connection_on_the_host_port_ends_at_the_connect_rule_says_replug_and_never_retries(L):
    """HPRT0's connect status read 0 (UF_EV_CONNECT 0): no device pulled D+ or D- up, so nothing will enumerate (the
    two laptops the wrong way round, usbtrace-2026-11-25); the replug comes with no grant after it, so the replug
    never meets the Q2's 5 V (F3)."""
    m = idle(L).ev(CONNECT, 0).ev(IDLOW, 1).after(FRESH + CONNECT_MS - 1)
    assert m.names() == ['grant']
    m.after(1)
    assert m.names() == ['grant', 'revoke'] and m.at('revoke')[0] - m.at('grant')[0] == CONNECT_MS * MS
    m.after(WRONG)
    assert m.names()[-1] == 'replug'
    m.after(60_000)
    assert m.names() == ['grant', 'revoke', 'replug'] and m.now_state() == 'refused'


def test_a_device_that_connects_but_enumerates_slowly_keeps_the_power_to_the_slow_trial(L):
    m = idle(L).ev(CONNECT, 0).ev(IDLOW, 1).after(FRESH + 300).ev(CONNECT, 1).after(TRIAL + 1000)
    assert m.names() == ['grant'] and m.now_state() == 'src-trial', 'connected: past UF_TRIAL_MS'
    m.ev(HOSTDEV, 1)
    assert m.now_state() == 'host'
    n = idle(L).ev(CONNECT, 0).ev(IDLOW, 1).after(FRESH + 300).ev(CONNECT, 1).after(TRIAL_SLOW)
    assert n.names() == ['grant', 'revoke'] and n.at('revoke')[0] - n.at('grant')[0] == TRIAL_SLOW * MS
    n.after(RETRY)
    assert n.names()[-1] == 'grant', 'a device was there: the trial once more'


def test_with_no_connect_status_read_the_trial_is_as_it_was(L):
    m = idle(L).ev(IDLOW, 1).after(FRESH + TRIAL)
    assert m.names() == ['grant', 'revoke']


def test_a_real_source_on_a_powered_bus_turns_the_power_off_at_once(L):
    m = idle(L).ev(IDLOW, 1).after(FRESH + 10)
    m.ev(VBUS, 1).after(5)  # the Q2's own VBUS: nothing
    assert m.names() == ['grant']
    m.ev(ONLINE, 1)  # a source's: never both
    assert m.names() == ['grant', 'revoke', 'gadget-on', 'cable-in']


def test_after_the_power_ends_pd17_is_own_vbus_for_650_ms_then_judged_by_online(L):
    m = idle(L).ev(IDLOW, 1).after(FRESH + 10).ev(HOSTDEV, 1).after(5000).ev(VBUS, 1).ev(HOSTDEV, 0)
    m.ev(IDLOW, 0).after(VBUS_OFF + 10).ev(ONLINE, 0).after(1600)
    assert m.names() == ['grant', 'revoke'] and m.now_state() == 'residue'
    m.ev(VBUS, 0).after(100)
    assert m.now_state() == 'idle' and m.names() == ['grant', 'revoke']


def test_a_wake_starts_from_the_levels_read_again(L):
    m = idle(L).ev(IDLOW, 1).after(FRESH + 10).ev(HOSTDEV, 1)
    m.ev(WAKE, 0).ev(VBUS, 0).ev(ONLINE, 0).ev(IDLOW, 0).ev(HOSTDEV, 0)
    assert m.now_state() == 'idle' and m.names() == ['grant']


def test_in_shadow_a_grant_waits_for_the_bus_power_to_time_its_trial(L):
    m = idle(L, shadow=True).ev(IDLOW, 1).after(FRESH + 60_000)
    assert m.names() == ['grant'] and m.now_state() == 'src-trial'
    m.ev(POWER, 1).ev(VBUS, 1).after(TRIAL - 1)
    assert m.names() == ['grant']
    m.after(1)
    assert m.names() == ['grant', 'revoke']


def test_in_shadow_an_id_high_before_any_power_ends_the_grant(L):
    m = idle(L, shadow=True).ev(IDLOW, 1).after(FRESH + 10).ev(IDLOW, 0)
    assert m.names() == ['grant', 'revoke'] and m.now_state() == 'idle'


def test_the_names(L):
    assert [L.usb_fsm_action_name(1 << i).decode() for i in range(11)] == ACTIONS
    assert L.usb_fsm_action_name(3) == b'' and L.usb_fsm_state_name(99) == b'?'


# ---- the properties (section 7 of the USB study) ----

def random_trace(rng, n=200):
    """Events at random times: the port's levels flipping, the PMIC's reads, devices, the UDC, power, wakes."""
    t, out = 0, []
    for _ in range(n):
        t += rng.choice((1, 5, 15, 30, 120, 260, 600, 2100, 3600, 9000, 20000)) * MS
        ev = rng.choice((VBUS, VBUS, IDLOW, IDLOW, ONLINE, ONLINE, POWER, HOSTDEV, PCM, UDC, WAKE))
        out.append((t, ev, rng.randint(0, 1)))
    return out


@pytest.mark.parametrize('seed', range(40))
@pytest.mark.parametrize('shadow', [False, True])
def test_never_a_grant_with_online_1_nor_onto_a_real_vbus(L, seed, shadow):
    rng = random.Random(seed)
    m = Fsm(L, shadow)
    online = vbus = power = None
    off_at = None
    for t, ev, v in random_trace(rng):
        before = len(m.acts)
        m.until(t)
        timed = m.acts[before:]
        m.ev(ev, v, t)
        for at, name, why in timed + m.acts[before + len(timed):]:
            if name == 'grant':
                assert online == 0, (seed, at, why, 'granted with online', online)
                own = power == 1 or (off_at is not None and at - off_at < VBUS_OFF * MS)
                assert vbus != 1 or own, (seed, at, why, 'granted onto PD17 low')
            if name == 'revoke':
                off_at = at
        if ev == ONLINE:
            online = v
        elif ev == VBUS:
            vbus, online = v, None  # the machine forgets a read of the other VBUS
        elif ev == POWER:
            power = v
            if not v:
                off_at = t
        elif ev == WAKE:
            online = vbus = power = None


@pytest.mark.parametrize('start', [0, 500, 2150, 4280, 7020, 9900])
@pytest.mark.parametrize('length', [100, 199, 1000, 2150, 3500, 4280, 4999])
def test_never_a_grant_inside_a_phantom_shorter_than_the_rule(L, start, length):
    """An ID low starting `start` ms after VBUS left, lasting `length` ms: shorter than its rule (UF_ID_PHANTOM_MS
    inside the window, UF_ID_FRESH_MS outside), it is never powered."""
    m = idle(L).ev(VBUS, 1).ev(ONLINE, 1).after(5000).ev(VBUS, 0).ev(ONLINE, 0).after(start)
    rule = PHANTOM if start < WINDOW else FRESH
    m.ev(IDLOW, 1).after(length).ev(IDLOW, 0).after(20_000)
    assert ('grant' in m.names()) == (length >= rule)


@pytest.mark.parametrize('seed', range(30))
def test_every_pd17_burst_yields_exactly_one_cable_in_or_out(L, seed):
    """A plug or an unplug of a real source, its contacts bouncing for up to 15 ms: one cable-in, or one cable-out."""
    rng = random.Random(seed)
    m = idle(L)
    present = False
    for _ in range(12):
        m.after(rng.choice((40, 300, 2000, 8000)))
        present = not present
        before = list(m.names())
        for _ in range(rng.randint(0, 4)):  # the bounce: the other level for a few ms, then back
            m.ev(VBUS, int(present)).after(rng.randint(1, 4)).ev(VBUS, int(not present)).after(rng.randint(1, 4))
        m.ev(VBUS, int(present))
        if present:
            m.ev(ONLINE, 1)
        m.after(VBUS_GONE + 50)
        new = m.names()[len(before):]
        want = 'cable-in' if present else 'cable-out'
        assert new.count(want) == 1 and new.count('cable-in' if not present else 'cable-out') == 0, (seed, new)


# ---- the recorded traces ----

def replay(L, name):
    """The fixture's runs (each `start` a fresh machine in shadow, as q2-usbtrace runs it): [(machine, lines)]."""
    lines = R.parse((FIXTURES / name).read_text().splitlines())
    runs: list = []
    for t, ev, v in R.fsm_events(lines):
        if ev == 'start':
            runs.append((Fsm(L, shadow=True), [ln for ln in lines if ln.t >= t]))
            runs[-1][0].t = t
            continue
        runs[-1][0].ev(ev, v, t)
    for i, (m, _) in enumerate(runs):  # the timers due by the run's last line: what comes after is not in the log
        m.until(max(m.t, runs[i + 1][0].t if i + 1 < len(runs) else lines[-1].t))
    return runs


def stock_on(lines, after=0, before=None):
    """Stock's otg-on times (a sample's PC20=1) in [after, before)."""
    out, last = [], None
    for ln in lines:
        if ln.kind == 's' and 'PC20' in ln.fields():
            v = ln.fields()['PC20']
            if v == '1' and last != '1' and ln.t >= after and (before is None or ln.t < before):
                out.append(ln.t)
            last = v
    return out


def test_fixtures_say_where_they_come_from():
    for f in FIXTURES.glob('*.trace'):
        text = f.read_text()
        assert '# Source: ' in text, f.name
        assert R.parse(text.splitlines()), f.name


def test_the_2026_11_17_back_feed_runs_get_no_shadow_power_before_stock_s(L):
    """p32b powered the bus 0.53 s into each ID low that followed an unplug from a computer (1.07 to 3.18 s after the
    VBUS left: inside the window); the shadow waits UF_ID_PHANTOM_MS there, so at stock's otg-on it had none: the
    disagreement the blank was made for, in the shadow's favour."""
    runs = replay(L, 'backfeed-2026-11-17.trace')
    assert len(runs) == 5
    lines = R.parse((FIXTURES / 'backfeed-2026-11-17.trace').read_text().splitlines())
    ons = stock_on(lines)
    assert len(ons) == 5
    for (m, _), on in zip(runs, ons):
        assert [t for t in m.at('grant') if t <= on] == [], 'the shadow powered the bus before stock'


def test_the_pull_up_phantoms_get_no_shadow_power(L):
    """The four lows with the pull-up on: the two uncut ones (2.15 s) never powered; the two stock's power cut short
    were not powered by the shadow before stock's otg-on (3.21 and 4.28 s into them). The study's 3.5 s rule would
    have powered the first at 3.5 s, 0.78 s before stock: UF_ID_PHANTOM_MS is 5 s for it."""
    (m, lines), = replay(L, 'pullup-phantoms-2026-11-17.trace')
    first, second = stock_on(lines)
    grants = m.at('grant')
    assert not [t for t in grants if t < first], grants
    assert not [t for t in grants if 1095 * S < t < second], grants
    assert not [t for t in grants if t > 1108 * S], 'the uncut lows after 1108 s'
    low1, cut1 = 842.20, 846.48
    assert 3500 < (cut1 - low1) * 1000 < PHANTOM, 'the first low outlasts the study\'s 3.5 s, not UF_ID_PHANTOM_MS'
    assert m.at('cable-in') and m.at('cable-out'), 'the laptop at 1120.62: a sink, then out'


def test_the_floating_pin_s_late_low_is_granted_early_and_its_trial_timed_from_stock_s_power(L):
    """22:43, the pin floating: the low began 12.1 s after VBUS left, outside the window, so the shadow would grant at
    200 ms (stock's settle took 6.5 s); its trial runs from stock's power, finds nothing, and the second trial too,
    then it refuses until the ID high. What a floating pin did, so not a rule's test: the shadow and stock agree that
    it was powered, 6.3 s apart."""
    (m, lines), = replay(L, 'floating-2026-11-17-2243.trace')
    on, = stock_on(lines)
    g = m.at('grant')
    assert g[0] == 443_930 * MS + FRESH * MS and on - g[0] > 6 * S
    assert [n for t, n, _ in m.acts if t > 431 * S][:7] == ['gadget-off', 'cable-out', 'grant', 'revoke', 'replug',
                                                            'grant', 'revoke']
    assert m.at('revoke')[0] == on + TRIAL * MS


def test_the_earbuds_night_residue_seats_and_both_grants(L):
    """2026-11-23: each seat with PD17 low and online 0 is residue (no charging, a replug at 2 s, as stock's no-source
    toast did); each ID low gets the shadow's grant before stock's 6 s settle; the trial, timed from stock's power,
    sees 1-1 within UF_TRIAL_MS (it appeared 2.06 and 1.87 s on); each device's leaving ends it."""
    (m, lines), = replay(L, 'earbuds-2026-11-23.trace')
    ons = stock_on(lines)
    assert len(ons) == 2
    grants, revokes = m.at('grant'), m.at('revoke')
    assert len(grants) == 2 and all(g < on for g, on in zip(grants, ons))
    # each low counted from PD17's high (1131.642 with its 20 ms, and 1167.720): inside the window, so UF_ID_PHANTOM_MS
    assert grants == [1131_642 * MS + PHANTOM * MS, 1173_968 * MS + PHANTOM * MS]
    assert [round((on - g) / MS) for g, on in zip(grants, ons)] == [1004, 1651]
    assert revokes == [1144_976 * MS, 1189_218 * MS], 'off as each device left, not at a trial\'s end'
    assert len(m.at('replug')) == 4, 'one replug for each of the four residue seats'
    assert not [t for t in m.at('cable-in') if t > 989 * S], 'no seat was a source'


def test_the_wrong_way_laptop_gets_its_replug_long_before_stock_s(L):
    """Round 3: the ID low with no VBUS, 32 s after the last unplug (outside the window): the shadow's grant at
    200 ms, its trial from stock's otg-on (7.4 s later) empty, the replug 1 s after its end, a second trial, then
    refused until the cable came out. Stock's wrong-role toast came 3 s after its 8 s trial: 8 s after the shadow's."""
    (m, lines), = replay(L, 'wrong-way-laptop-2026-11-23.trace')
    on, = stock_on(lines)
    replug, = m.at('replug')
    assert replug == on + (TRIAL + WRONG) * MS
    stock_toast = 17793.82 + 3
    assert stock_toast * S - replug > 7 * S
    assert m.names().count('grant') == 2
    assert m.names().count('host-seen') >= 3, 'rounds 1, 2 and the replug: the laptop as a host each time'


def test_tonight_s_two_grants_would_come_6_s_sooner_and_end_with_their_devices(L):
    (m, lines), = replay(L, 'grants-2026-11-25.trace')
    ons = stock_on(lines)
    grants = m.at('grant')
    assert [round((on - g) / MS) for g, on in zip(grants, ons)] == [6000 - FRESH] * 2
    assert m.at('dac') and len(m.at('dac')) == 2
    assert m.at('revoke') == [1354_346 * MS, 1554_932 * MS]


def test_a_wake_with_a_computer_keeps_the_link_and_a_slow_one_reads_as_a_charger_first(L):
    """After the first wake the host is there at once; after the second it answered 5 s on, past UF_CHARGER_MS, so
    the shadow would have said "charger" at 3 s and then taken the host: a case stock does not toast."""
    (m, lines), = replay(L, 'standby-wake-2026-11-22.trace')
    hosts, chargers = m.at('host-seen'), m.at('charger')
    assert len(hosts) == 3 and chargers == [4027_374 * MS + CHARGER * MS]
    assert not m.at('grant') and not m.at('replug')


def test_tonight_s_owner_plugs_two_wrong_way_laptops_and_headphones_behind_a_hub(L):
    """2026-11-25 (usblog2.txt). The first laptop the wrong way round, its ID low by 5.06 s after the unplug (inside the
    window): the shadow's grant at UF_ID_PHANTOM_MS, its trial from stock's otg-on, the replug 1 s after its end, at
    847.921, 8 s before stock's wrong-role toast (855.917). The headphones behind a hub (1-1, then 1-1.1): powered 2.8 s
    before stock, kept while 1-1 was there, off as it left with stock's why=gone. The second laptop: its trial empty
    (8.4 s after its grant, which waited for stock's power), off at 4608.614, 3 s before stock's why=id-high (4611.579);
    the ID went high before any replug was due, and its VBUS came 10.5 s after the shadow's off."""
    (m, lines), = replay(L, 'owner-2026-11-25.trace')
    grants, revokes, replugs = m.at('grant'), m.at('revoke'), m.at('replug')
    assert grants == [840_673 * MS, 848_921 * MS, 4580_806 * MS, 4602_814 * MS]
    assert revokes == [846_921 * MS, 851_921 * MS, 4587_277 * MS, 4608_614 * MS]
    assert replugs == [847_921 * MS] and 855_917 * MS - replugs[0] > 7 * S
    assert m.at('host-seen')[-1] == 4624_168 * MS


# ---- the owner's trace of 2026-11-25, every cycle ----

USBTRACE = 'usbtrace-2026-11-25.trace'


def usbtrace_runs(L):
    """Each run of the tracer in the fixture, in shadow as q2-usbtrace ran it: [(machine, its lines, its cycles)]."""
    out = []
    for lines in R.runs(R.parse((FIXTURES / USBTRACE).read_text().splitlines())):
        m = Fsm(L, shadow=True)
        m.t = lines[0].t
        for t, ev, v in R.fsm_events(lines):
            if ev != 'start':
                m.ev(ev, v, t)
        m.until(lines[-1].t)
        out.append((m, lines, [c for c in R.cycles(lines) if c.n]))
    return out


def in_cycle(m, c, name):
    return [t for t in m.at(name) if c.start <= t <= c.end]


def test_the_owner_s_trace_in_shadow_every_dac_kept_and_both_wrong_way_laptops_end_at_the_connect_rule(L):
    """Every cycle of the five runs: each USB DAC's grant waited for stock's power, saw HPRT0 connect at its first read
    (0.25 to 0.41 s on) and was kept with its device until it left; each laptop the wrong way round (cycles 7 and 12
    of the last run, HPRT0 0x1000 through stock's 8 s trial) is off UF_CONNECT_MS after stock's power, says replug
    UF_WRONG_MS later and is never powered again before the replug; no other cycle is powered."""
    runs = usbtrace_runs(L)
    dacs, wrong = 0, []
    for m, lines, cs in runs:
        ons = R.grants(lines)
        for c in cs:
            grants = in_cycle(m, c, 'grant')
            stock = [g for g in ons if c.start <= g.on <= c.end]
            if c.summary['type'] == 'dac':
                dacs += 1
                g, = stock
                assert grants and in_cycle(m, c, 'dac'), c.n
                assert g.connect and g.connect - g.on < 410 * MS, (c.n, 'connected at the first read')
                assert g.dev
                left = g.off or c.end
                assert not [t for t in in_cycle(m, c, 'revoke') if grants[0] < t < min(left, g.dev + 1)], (c.n, 'kept')
            elif stock and not any(g.dev for g in stock):
                g, = stock
                wrong.append((c.n, g.on))
                assert grants == [grants[0]] and grants[0] < g.on, c.n
                assert in_cycle(m, c, 'revoke') == [g.on + CONNECT_MS * MS], c.n
                assert in_cycle(m, c, 'replug') == [g.on + (CONNECT_MS + WRONG) * MS], c.n
            else:
                assert not grants, (c.n, c.summary['type'])
    assert dacs == 7 and [n for n, _ in wrong] == [7, 12]
    # cycle 12: stock's power went off 20.681 s into it, and nothing said replug before 23.7 s (that day's 3 s
    # OTG_WRONG_MS after)
    n, on = wrong[1]
    m, lines, cs = runs[-1]
    c12, = [c for c in cs if c.n == 12]
    assert (in_cycle(m, c12, 'revoke')[0] - c12.start) / S < 14.3 < 20.681


def own_replay(L, lines, cs):
    """A run replayed with the machine owning the bus power (shadow 0): the port's levels from the trace, but none of
    stock's grants (their power, the PD17 low and online reads it caused, the device's arrival under it), and each
    grant the machine makes gets the device of its cycle's stock grant at the offsets that grant measured (HPRT0's
    connect read, 1-1, the PCM); for a grant whose device never connected, HPRT0 read with no connect at once. A
    device's leaving (the cable out) stays at its own time."""
    ons = R.grants(lines)
    powered = [(x.on - S // 2, (x.off or lines[-1].t) + 2 * S) for x in ons]  # its PD17 edge comes before the sample
    keep = (VBUS, IDLOW, ONLINE, UDC, HOSTDEV, PCM)
    queue = [(t, ev, v) for t, ev, v in R.fsm_events(lines) if ev in keep and not (ev in (HOSTDEV, PCM) and v) and
             not (ev in (VBUS, ONLINE) and any(a <= t <= b for a, b in powered))]
    m = Fsm(L)
    m.t = queue[0][0]
    pending, seen = [], 0

    def schedule():
        nonlocal seen
        for at in m.at('grant')[seen:]:
            g = next((x for c in cs if c.start <= at <= c.end for x in ons if c.start <= x.on <= c.end), None)
            if g and g.connect:
                pending.extend([(at + g.connect - g.on, CONNECT, 1)] +
                               [(at + x - g.on, ev, 1) for x, ev in ((g.dev, HOSTDEV), (g.pcm, PCM)) if x])
            else:
                pending.append((at, CONNECT, 0))
        seen = len(m.at('grant'))
        pending.sort()

    while queue or pending:
        nxt = min(([queue[0][0]] if queue else []) + ([pending[0][0]] if pending else []))
        d = L.usb_fsm_deadline(m.f)
        if d != NEVER and d < nxt:
            m.until(d)
            schedule()
            continue
        t, ev, v = (pending if pending and pending[0][0] == nxt else queue).pop(0)
        m.ev(ev, v, max(t, m.t))
        schedule()
    m.until(lines[-1].t)
    return m


def test_the_owner_s_trace_owned_every_dac_is_powered_and_kept_and_cycle_12_is_a_device_again_in_11_1_s(L):
    """The phase 2 decision for this case, the machine owning the bus power: cycle 12 (ID low 3.584 s after the
    laptop's VBUS left, inside the window) is granted at UF_ID_PHANTOM_MS, off at UF_CONNECT_MS with no connect,
    11.084 s into the cycle where stock's power stayed on to 20.681 s, and says replug 1 s later with no grant after
    it; cycle 7 (outside the window) is off 2.7 s after its ID low; every DAC cycle, given its own device's measured
    timings, is powered and kept."""
    dacs = 0
    for _, lines, cs in usbtrace_runs(L):
        ons = R.grants(lines)
        m = own_replay(L, lines, cs)
        for c in cs:
            stock = [x for x in ons if c.start <= x.on <= c.end]
            grants = in_cycle(m, c, 'grant')
            if not stock:
                assert not grants, (c.n, c.summary['type'])
                continue
            g = stock[0]
            if c.summary['type'] == 'dac':
                dacs += 1
                assert len(grants) == 1 and in_cycle(m, c, 'dac') and g.dev, c.n
                assert not [t for t in in_cycle(m, c, 'revoke') if grants[0] < t < grants[0] + (g.dev - g.on) + S], c.n
            else:
                idlow = c.start + int(float(c.summary['id-']) * S)
                need = PHANTOM if c.n == 12 else FRESH
                assert len(grants) == 1 and abs(grants[0] - idlow - need * MS) < MS, c.n  # id- is in ms
                assert in_cycle(m, c, 'revoke') == [grants[0] + CONNECT_MS * MS], c.n
                assert in_cycle(m, c, 'replug') == [grants[0] + (CONNECT_MS + WRONG) * MS], c.n
                if c.n == 12:
                    assert round((in_cycle(m, c, 'revoke')[0] - c.start) / MS) == 11_084
    assert dacs == 7
