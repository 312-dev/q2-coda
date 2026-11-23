#!/usr/bin/env python3
"""Run a built usb_f_uac1.ko's own u_audio_iso_complete under Unicorn against a controller that completes one
playback request in three unsent, as the Q2's dwc2 did at the desk, and check what the host receives
(docs/iphone-mode.md#missed-isochronous-packets).

    python3 test/uac1_isoc.py ~/.cache/q2-uac1/module/usb_f_uac1.ko

The ring holds a counter (frame n is the 32-bit word n), req_number requests start as u_audio_start_playback leaves
them (176 bytes, at 44.1 kHz and a 1 ms interval), and the controller takes them in queue order: of every three
completions two are sent and given back with status 0, the third is given back with -ENODATA (-61) and never sent, as
dwc2's BDMA isochronous flow does for a request whose (micro)frame went by. Each completion runs the module's code,
which queues the request again through usb_ep_queue (modeled: the queue's tail). Checked: with resend_missed off and
2 requests (5.10.21's behaviour and the probe's old setting) the host's frames run 88 or 89 and then skip 44 or 45,
and hw_ptr moves 1.5 times what the host got: the pattern recorded at the desk on 2026-11-20. With resend_missed on
and 2 requests the same (a resent packet would follow later samples, so the module does not resend). With
resend_missed on and 1 request (the module's default and the probe's setting) the host gets every frame in order and
hw_ptr moves exactly what the host got. in_missed and in_sent count the two kinds of completion.
Then the rate (docs/iphone-mode.md#per-song-rate, rate_run): the module bound with the probe's attributes and the
playback interface at alt 1, a host's GET_CUR answers 44100 and the packets are 176 or 180 bytes; after SET_CUR 48000
GET_CUR answers 48000, every packet is 192 bytes (48 frames) and p_rate, rate_sets and rate_asked say so; a SET_CUR of
96000, which the format does not list, changes nothing but rate_asked; and alt 0 and back keeps 48 kHz. On a module
built before q2-uac1-playback-rates.patch the packets stay 44.1 kHz's and GET_CUR answers whatever ep0's buffer held.
Then the stream's life (docs/iphone-mode.md#the-stream-s-life, life_run): a pause keeps the ring position, alt 1 again
queues nothing twice, a -ESHUTDOWN completion is counted, and a disable stops playback and forgets the host's rate.
Kernel calls are modeled as test/uac1_bind.py models them. Exit 0 when all hold."""
import argparse
import importlib.util
from pathlib import Path
import struct
import sys
from unicorn.mips_const import UC_MIPS_REG_V1

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('uac1_bind', ROOT/'test/uac1_bind.py')
assert _spec and _spec.loader
uac1_bind = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(ROOT/'test'))
_spec.loader.exec_module(uac1_bind)

ENODATA = -61
# the module's private layout (5.10.21's u_audio.c, 32-bit MIPS, spinlock_t of 16 bytes), as its own
# u_audio_iso_complete reads it: struct uac_req {pp, req}; struct uac_rtd_params {uac 0, ep_enabled 4, ss 8,
# hw_ptr 12, rbuf 16, max_psize 20, ureq 24, lock 28}; struct snd_uac_chip {audio_dev 0, p_prm 4, c_prm 48, card 92,
# pcm 96, p_interval 100, p_residue 104, p_pktsize 108, p_pktsize_residue 112, p_framesize 116}
PRM_UAC, PRM_ENABLED, PRM_SS, PRM_HW_PTR = 0, 4, 8, 12
UAC_P_PRM, UAC_P_INTERVAL = 4, 100
# struct g_audio (u_audio.h): func (0x6c bytes, q2_size_usb_function), gadget, in_ep, out_ep, in_ep_maxpsize,
# out_ep_maxpsize, uac, then params, whose seventh int is req_number
GA_REQ_NUMBER = 0x6c + 6 * 4 + 6 * 4
# struct usb_request: buf 0, length 4, complete 0x1c, context 0x20, status 0x30, actual 0x34 (uac1_build.STOCK)
REQ_BUF, REQ_LENGTH, REQ_CONTEXT, REQ_STATUS = 0x0, 0x4, 0x20, 0x30
# the kernel's: substream stream 0x30 and runtime 0x70 (STOCK); runtime status 0xb4 (its state first), period_size
# 0x54, frame_bits 0x68, dma_area 0x244, dma_bytes 0x24c
SUB_PRIVATE, SUB_STREAM, SUB_RUNTIME = 0x8, 0x30, 0x70  # private_data: STOCK q2_off_sub_private_data
RT_STATUS, RT_PERIOD_SIZE, RT_FRAME_BITS, RT_DMA_AREA, RT_DMA_BYTES = 0xb4, 0x54, 0x68, 0x244, 0x24c
SNDRV_PCM_STATE_RUNNING = 3
RING_FRAMES, PERIOD_FRAMES = 4096, 1024


class Machine(uac1_bind.Machine):
    def __init__(self, data):
        super().__init__(data)
        self.queue = []
        self.t = 0  # ktime_get's nanoseconds: run() moves it a millisecond a completion, the isochronous interval


    def service(self, name, a):
        if name in ('_snd_pcm_stream_lock_irqsave', '_raw_spin_lock_irqsave', 'snd_pcm_stream_unlock_irqrestore',
                    '_raw_spin_unlock_irqrestore', 'snd_pcm_period_elapsed'):
            return 0
        if name == 'usb_ep_queue':
            self.queue.append(a[1])
            return 0
        if name == 'ktime_get':  # an s64 in v0 (low) and v1 (high), o32's way
            self.uc.reg_write(UC_MIPS_REG_V1, self.t >> 32)
            return self.t & 0xffffffff
        return super().service(name, a)


def run(data, resend, reqs, steps=900, ddma=False):
    """(frames the host got, in order; hw_ptr's travel in frames; in_missed; in_sent) for steps completions. The
    controller gives back every third request unsent with -ENODATA; with ddma, as the Q2's dwc2 does instead (its
    isochronous IN in descriptor DMA, g_dma_desc 1 in its debugfs params): the core runs the chain of requests queued
    when it was built; a request queued again while fewer than two of the chain's are left to fetch comes after the
    chain's last descriptor, the core runs off the end (BNA), gives that request back unsent with status 0 and actual
    0 and builds the chain afresh from the queue. With one request every other completion is empty, with two every
    third, with three or more none: the desk's sweep on the Q2 (2026-11-21)."""
    m = Machine(data)
    sym = m.symbols
    if 'resend_missed' not in sym:
        sys.exit('FAIL  the module has no resend_missed: built before the fix (uac1_build.py MISSED)')
    m.uc.mem_write(sym['resend_missed'], bytes([resend]))
    uac, ga = m.alloc(0x80), m.alloc(0x100)
    m.write(uac, ga)
    m.write(ga + GA_REQ_NUMBER, reqs)
    prm = uac + UAC_P_PRM
    for off, v in ((UAC_P_INTERVAL, 1000), (UAC_P_INTERVAL + 4, 0), (UAC_P_INTERVAL + 8, 176),
                   (UAC_P_INTERVAL + 12, 400), (UAC_P_INTERVAL + 16, 4)):
        m.write(uac + off, v)
    sub, rt, st = m.alloc(0x100), m.alloc(0x300), m.alloc(0x40)
    ring = m.alloc(RING_FRAMES * 4)
    m.uc.mem_write(ring, struct.pack(f'<{RING_FRAMES}I', *range(RING_FRAMES)))
    m.write(sub + SUB_STREAM, 0)  # SNDRV_PCM_STREAM_PLAYBACK
    m.write(sub + SUB_RUNTIME, rt)
    m.write(rt + RT_STATUS, st)
    m.write(st, SNDRV_PCM_STATE_RUNNING)
    m.write(rt + RT_PERIOD_SIZE, PERIOD_FRAMES)
    m.write(rt + RT_FRAME_BITS, 32)
    m.write(rt + RT_DMA_AREA, ring)
    m.write(rt + RT_DMA_BYTES, RING_FRAMES * 4)
    m.write(prm + PRM_UAC, uac)
    m.write(prm + 20, 400)  # max_psize (PRM_MAX_PSIZE): the AS IN endpoint's wMaxPacketSize, uac1_build.py CARRY_F
    m.uc.mem_write(prm + PRM_ENABLED, b'\x01')
    m.write(prm + PRM_SS, sub)
    ep = m.alloc(0x40)
    for _ in range(reqs):  # as u_audio_start_playback leaves them: zeroed buffers of p_pktsize, queued
        req, ur, buf = m.alloc(0x40), m.alloc(8), m.alloc(200)
        m.write(ur, prm)
        m.write(ur + 4, req)
        m.write(req + REQ_BUF, buf)
        m.write(req + REQ_LENGTH, 176)
        m.write(req + REQ_CONTEXT, ur)
        m.queue.append(req)
    host, moved, last = [], 0, 0
    chain, late = list(m.queue), set()  # ddma: the requests the core fetched, and those queued after its end
    for i in range(steps):
        req = m.queue.pop(0)
        sent = i % 3 != 2
        if ddma:
            sent = req not in late
            late.discard(req)
            if req in chain:
                chain.remove(req)
        m.t += 1000000
        n = 0
        if sent:
            n = m.read(req + REQ_LENGTH)
            host += struct.unpack(f'<{n // 4}I', m.read(m.read(req + REQ_BUF), n))
        m.write(req + REQ_ACTUAL, n)
        m.write(req + REQ_STATUS, 0 if sent or ddma else ENODATA)
        m.call('u_audio_iso_complete', ep, req)
        if ddma and not sent:  # BNA: the chain built afresh from what is queued
            chain, late = list(m.queue), set()
        elif ddma and len(chain) >= 2:  # queued in time: the chain grows
            chain.append(req)
        elif ddma:
            late.add(req)
        hw = m.read(prm + PRM_HW_PTR) // 4
        moved += (hw - last) % RING_FRAMES
        last = hw
    counted = {k: m.read(sym[k]) for k in COUNTED if k in sym}
    return host, moved, m.read(sym['in_missed']), m.read(sym['in_sent']), counted


# uac1_build.py COUNTED's counters, where the module has them
COUNTED = ('in_bytes', 'in_zero', 'in_enodata', 'in_exdev', 'in_eshutdown', 'in_other', 'in_resent', 'in_gaps2',
           'in_pkt_min', 'in_pkt_max', 'in_gap_max')


# struct g_audio's in_ep, uac and params.p_srate; struct usb_gadget's ep0 and speed (STOCK); usb_composite_dev.req;
# usb_request's complete and actual, ep->desc and the maxpacket halfword (STOCK, uac1_build.py's q2_maxpacket check)
GA_IN_EP, GA_UAC, GA_P_SRATE = 0x70, 0x80, 0x88
GADGET_EP0, GADGET_SPEED, USB_SPEED_HIGH = 0x18, 0x24, 3
CDEV_REQ, REQ_COMPLETE, REQ_ACTUAL, EP_DESC, EP_MAXPACKET = 0x4, 0x1c, 0x34, 0x24, 0x1a
PRM_RBUF, PRM_MAX_PSIZE, PRM_UREQ = 16, 20, 24
AS_IN = 1  # the playback interface, uac1_bind.py's check
SET_CUR, GET_CUR, SAMPLE_RATE = (0x22, 0x01), (0xa2, 0x81), 0x0100  # to the endpoint; wValue's control selector 1


class Bound(Machine):
    """Machine with the calls set_alt and ep0's requests make: ep0's queued request is .ep0_req, the playback
    endpoint's go to .queue"""
    ep0 = None
    ep0_req = None

    def service(self, name, a):
        if name == 'usb_ep_queue' and a[0] == self.ep0:
            self.ep0_req = a[1]
            return 0
        if name == 'config_ep_by_speed':  # the full-speed endpoint descriptor, its wMaxPacketSize
            d = next(d for d in self.fs if d[1] == 5)
            desc = self.alloc(len(d))
            self.uc.mem_write(desc, d)
            self.write(a[2] + EP_DESC, desc)
            self.uc.mem_write(a[2] + EP_MAXPACKET, d[4:6])
            return 0
        if name == 'usb_ep_alloc_request':
            return self.alloc(0x40)
        if name in ('usb_ep_enable', 'usb_ep_disable', 'usb_ep_dequeue', 'composite_setup_complete'):
            return 0
        return super().service(name, a)


def control(m, kind, wlength, data=None):
    """(the bytes of ep0's reply, or None when the module stalled) of a class request to the playback endpoint; data
    is a SET's data stage, given to the request's completion as the controller would"""
    req = m.read(m.cdev + CDEV_REQ)
    m.write(req + REQ_COMPLETE, m.external('composite_setup_complete'))  # as composite_setup sets them each time
    m.write(req + REQ_CONTEXT, m.cdev)
    ctrl = m.alloc(8)
    m.uc.mem_write(ctrl, struct.pack('<BBHHH', *kind, SAMPLE_RATE, uac1_bind.IN_EP, wlength))
    m.ep0_req = None
    status = m.call('f_audio_setup', m.function, ctrl)
    if status >> 31 or m.ep0_req is None:
        return None
    n = m.read(req + REQ_LENGTH)
    if data is not None:
        m.uc.mem_write(m.read(req + REQ_BUF), data)
        m.write(req + REQ_ACTUAL, len(data))
        m.write(req + REQ_STATUS, 0)
        m.call(m.read(req + REQ_COMPLETE), m.ep0, req)
    return m.read(m.read(req + REQ_BUF), n)


def streaming(data, reqs=1):
    """(machine, function, playback prm, substream, playback ep): the module bound with the probe's attributes and
    reqs requests, at high speed, with u_audio's chip as g_audio_setup (modeled) would leave it and an open PCM as
    run()'s, its substream's chip that chip, as the PCM's trigger reads it"""
    m = uac1_bind.bound(data, Bound, {**uac1_bind.PROBE, 'req_number': reqs})
    f = m.function
    m.ep0 = m.alloc(0x40)
    m.write(m.gadget + GADGET_EP0, m.ep0)
    m.write(m.gadget + GADGET_SPEED, USB_SPEED_HIGH)
    req = m.alloc(0x40)
    m.write(m.cdev + CDEV_REQ, req)
    m.write(req + REQ_BUF, m.alloc(64))
    m.uc.mem_write(m.read(req + REQ_BUF), b'\xee' * 64)  # what an earlier request left there
    # u_audio's chip, as g_audio_setup (modeled) would leave it, and an open PCM as test/uac1_isoc.py's run has
    uac = m.alloc(0x80)
    prm = uac + UAC_P_PRM
    m.write(f + GA_UAC, uac)
    m.write(uac, f)
    m.write(prm + PRM_UAC, uac)
    m.write(prm + PRM_RBUF, m.alloc(200 * reqs))  # max_psize a request, as g_audio_setup's kzalloc
    m.write(prm + PRM_MAX_PSIZE, 200)
    m.write(prm + PRM_UREQ, m.alloc(8 * reqs))
    sub, rt, st, ring = m.alloc(0x100), m.alloc(0x300), m.alloc(0x40), m.alloc(RING_FRAMES * 4)
    m.write(sub + SUB_PRIVATE, uac)
    m.write(sub + SUB_RUNTIME, rt)
    m.write(rt + RT_STATUS, st)
    m.write(st, SNDRV_PCM_STATE_RUNNING)
    m.write(rt + RT_PERIOD_SIZE, PERIOD_FRAMES)
    m.write(rt + RT_FRAME_BITS, 32)
    m.write(rt + RT_DMA_AREA, ring)
    m.write(rt + RT_DMA_BYTES, RING_FRAMES * 4)
    ep = m.read(f + GA_IN_EP)
    return m, f, prm, sub, ep


def rate_run(data):
    """What is wrong with the rate the host sets (docs/iphone-mode.md#per-song-rate), one line each, and what was seen.
    The module is bound with the probe's attributes and req_number 1, the playback interface set to alt 1 at high
    speed (bInterval 4: one packet a millisecond), then as a host does: GET_CUR, SET_CUR 48000 while streaming,
    GET_CUR, SET_CUR 96000 (not offered), the interface to alt 0 and back. Each step's packets are the next 20
    completions' lengths."""
    m, f, prm, sub, ep = streaming(data)

    def packets(n=20):
        out = []
        for _ in range(n):
            r = m.queue.pop(0)
            m.write(r + REQ_STATUS, 0)
            m.write(r + REQ_ACTUAL, m.read(r + REQ_LENGTH))  # sent whole, as a controller reports it
            m.call('u_audio_iso_complete', ep, r)
            out.append(m.read(r + REQ_LENGTH))
        return out

    def rate(reply):
        return None if reply is None else int.from_bytes(reply[:3], 'little')

    def param(name):
        return m.read(m.symbols[name]) if name in m.symbols else None

    wrong, seen = [], []
    status = m.call('f_audio_set_alt', f, AS_IN, 1)
    if status:
        return [f'set_alt(1, 1) returned {status}'], seen
    m.write(prm + PRM_SS, sub)  # the PCM's trigger
    first = packets()
    seen.append(f'alt 1 at 44.1 kHz: packets {sorted(set(first))}')
    if sorted(set(first)) != [176, 180]:
        wrong.append(f'at 44.1 kHz the packets are {sorted(set(first))} bytes, not 176 and 180 (44 or 45 frames)')
    got = rate(control(m, GET_CUR, 3))
    seen.append(f'GET_CUR {got}')
    if got != 44100:
        wrong.append(f'GET_CUR before any SET_CUR answers {got}, not p_srate 44100')
    control(m, SET_CUR, 3, (48000).to_bytes(3, 'little'))
    after = packets()
    got = rate(control(m, GET_CUR, 3))
    seen.append(f'SET_CUR 48000: packets {sorted(set(after))}, GET_CUR {got}, p_rate {param("p_rate")}, '
                f'rate_sets {param("rate_sets")}, rate_asked {param("rate_asked")}')
    if set(after) != {192}:
        wrong.append(f'after SET_CUR 48000 the packets are {sorted(set(after))} bytes, not 192 (48 frames)')
    if got != 48000:
        wrong.append(f'GET_CUR after SET_CUR 48000 answers {got}')
    if (param('p_rate'), param('rate_sets'), param('rate_asked')) != (48000, 1, 48000):
        wrong.append(f'p_rate {param("p_rate")}, rate_sets {param("rate_sets")}, rate_asked {param("rate_asked")}, '
                     'not 48000, 1 and 48000')
    control(m, SET_CUR, 3, (96000).to_bytes(3, 'little'))
    odd = packets()
    got = rate(control(m, GET_CUR, 3))
    seen.append(f'SET_CUR 96000: packets {sorted(set(odd))}, GET_CUR {got}')
    if set(odd) != {192} or got != 48000 or param('rate_asked') != 96000:
        wrong.append(f'SET_CUR 96000, not offered, changed the stream: packets {sorted(set(odd))}, GET_CUR {got}, '
                     f'rate_asked {param("rate_asked")}')
    m.call('f_audio_set_alt', f, AS_IN, 0)
    m.queue.clear()  # dequeued
    m.call('f_audio_set_alt', f, AS_IN, 1)
    again = packets()[1:]  # the first is the request queued again as it was
    seen.append(f'alt 0 and back: packets {sorted(set(again))}')
    if set(again) != {192}:
        wrong.append(f'after alt 0 and back the packets are {sorted(set(again))} bytes, not 192')
    return wrong, seen


# the trigger's commands (include/sound/pcm.h) and the errno the controller gives a request at a disconnect
TRIGGER_STOP, TRIGGER_START, TRIGGER_PAUSE_PUSH, TRIGGER_PAUSE_RELEASE = 0, 1, 3, 4
ESHUTDOWN = -143  # MIPS's errno (arch/mips/include/uapi/asm/errno.h), not the generic 108


def life_run(data):
    """What is wrong in the playback stream's life (docs/iphone-mode.md#the-uac1-module), one line each, and what was
    seen. The module bound as rate_run binds it, with patch/q2car.sh's four requests:
    - pause: the PCM's trigger with PAUSE_PUSH and PAUSE_RELEASE (the player's snd_pcm_pause, player/output.c) answers
      0 and keeps the ring position, sending nothing of the ring while paused; START and STOP start it at 0. 5.10.21
      advertised pause, answered both with -EINVAL and zeroed the position first, so the stream ran on from the
      ring's start;
    - a request the controller gives back with -ESHUTDOWN (a disconnect) is counted in in_eshutdown, which 5.10.21's
      early return made unreachable;
    - the host setting alternate 1 again while the stream runs queues none of its four requests a second time;
    - the function disabled (a disconnect, a reset, the gadget removed) stops the playback endpoint as it stops the
      capture one, and the host's rate is forgotten: p_srate's again for the next host."""
    m, f, prm, sub, ep = streaming(data, 4)
    sym = m.symbols
    wrong, seen = [], []
    trigger = sym['uac_pcm_trigger'] if 'uac_pcm_trigger' in sym else None

    def trig(cmd):
        return m.call('uac_pcm_trigger', sub, cmd) if trigger else None

    if trigger is None:
        return ['the module has no uac_pcm_trigger symbol'], seen
    m.write(prm + PRM_SS, 0)
    status = trig(TRIGGER_START)
    m.write(prm + PRM_HW_PTR, 4000)  # where the ring had got to
    push = trig(TRIGGER_PAUSE_PUSH)
    after_push = (m.read(prm + PRM_HW_PTR), m.read(prm + PRM_SS))
    release = trig(TRIGGER_PAUSE_RELEASE)
    after_release = (m.read(prm + PRM_HW_PTR), m.read(prm + PRM_SS))
    seen.append(f'pause: START {status}, PAUSE_PUSH {push - (1 << 32) if push >> 31 else push} (hw_ptr, ss {after_push}), '
                f'PAUSE_RELEASE {release - (1 << 32) if release >> 31 else release} (hw_ptr, ss {after_release})')
    if push or after_push != (4000, 0):
        wrong.append(f'PAUSE_PUSH answers {push:#x} and leaves hw_ptr, ss {after_push}, not 0 and (4000, 0)')
    if release or after_release != (4000, sub):
        wrong.append(f'PAUSE_RELEASE answers {release:#x} and leaves hw_ptr, ss {after_release}, not 0 and (4000, the PCM)')
    trig(TRIGGER_STOP)
    trig(TRIGGER_START)
    if m.read(prm + PRM_HW_PTR) != 0:
        wrong.append(f'STOP then START leaves hw_ptr {m.read(prm + PRM_HW_PTR)}, not 0')
    m.write(prm + PRM_SS, 0)
    # alternate 1, then 1 again while it runs
    m.queue.clear()
    m.call('f_audio_set_alt', f, AS_IN, 1)
    first = len(m.queue)
    m.call('f_audio_set_alt', f, AS_IN, 1)
    seen.append(f'alt 1: {first} requests queued; alt 1 again: {len(m.queue) - first} more')
    if first != 4 or len(m.queue) != 4:
        wrong.append(f'alt 1 queued {first} requests and alt 1 again {len(m.queue) - first} more, not 4 and 0')
    # a request given back with -ESHUTDOWN
    r = m.queue.pop(0)
    m.write(r + REQ_STATUS, ESHUTDOWN & 0xffffffff)
    m.write(r + REQ_ACTUAL, 0)
    m.call('u_audio_iso_complete', ep, r)
    shut = m.read(sym['in_eshutdown']) if 'in_eshutdown' in sym else None
    seen.append(f'-ESHUTDOWN: in_eshutdown {shut}, queued again {r in m.queue}')
    if shut != 1 or r in m.queue:
        wrong.append(f'a -ESHUTDOWN completion leaves in_eshutdown {shut} and queued again {r in m.queue}, not 1 and no')
    # the host's rate, then the function disabled
    control(m, SET_CUR, 3, (48000).to_bytes(3, 'little'))
    dequeued = []
    real = m.service

    def service(name, a):
        if name == 'usb_ep_dequeue':
            dequeued.append(a[1])
        return real(name, a)
    m.service = service
    m.call('f_audio_disable', f)
    m.service = real
    enabled = m.read(prm + PRM_ENABLED, 1)[0]
    rate = m.read(f + GA_P_SRATE)
    p_rate = m.read(sym['p_rate']) if 'p_rate' in sym else None
    seen.append(f'disabled: playback enabled {enabled}, {len(dequeued)} requests dequeued, p_srate {rate}, p_rate {p_rate}')
    if enabled or len(dequeued) != 4:
        wrong.append(f'f_audio_disable leaves the playback endpoint enabled {enabled}, {len(dequeued)} requests dequeued, not 0 and 4')
    if (rate, p_rate) != (44100, 44100):
        wrong.append(f'after the disable p_srate {rate} and p_rate {p_rate}, not the configured 44100: the last host\'s rate kept')
    return wrong, seen


def runs(host):
    """[(frames in order, frames skipped after them)] of the host's counter frames after the zeroed first packets,
    the last run with 0"""
    first = next(i for i, v in enumerate(host) if v)
    out, n = [], 1
    for a, b in zip(host[first:], host[first + 1:]):
        gap = (b - a) % RING_FRAMES
        if gap == 1:
            n += 1
        else:
            out.append((n, gap - 1))
            n = 1
    return out + [(n, 0)]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('ko', type=Path, help='a usb_f_uac1.ko from tools/uac1_module/uac1_build.py')
    a = ap.parse_args(argv)
    data = a.ko.read_bytes()
    wrong = []
    for resend, reqs, cured in ((0, 2, False), (1, 2, False), (0, 1, False), (1, 1, True)):
        host, moved, missed, sent, counted = run(data, resend, reqs)
        r = runs(host)
        got = len(host) - next(i for i, v in enumerate(host) if v) + 1  # from the counter's frame 0, after the zeroed packets
        what = f'resend_missed {resend}, {reqs} requests'
        print(f'{what}: host got {got} counter frames, hw_ptr moved {moved}; runs and skips '
              f'{sorted(set(r[2:-2])) if len(r) > 4 else r}; in_missed {missed}, in_sent {sent}')
        if (missed, sent) != (300, 600):
            wrong.append(f'{what}: in_missed {missed} and in_sent {sent}, not 300 and 600')
        if counted:
            # each miss -ENODATA and, resent only with one request; the bytes the host got; packets of 44 or 45
            # frames at 44.1 kHz (176 or 180 bytes); two intervals at most between completions (2 ms, so none over
            # 2 ms), in 1024 ns units
            want = dict(in_bytes=4 * len(host), in_zero=0, in_enodata=300, in_exdev=0, in_eshutdown=0, in_other=0,
                        in_resent=300 if resend and reqs == 1 else 0, in_gaps2=0, in_pkt_min=176, in_pkt_max=180,
                        in_gap_max=2000000 >> 10)
            print(f'{what}: counted {counted}')
            if counted != want:
                wrong.append(f'{what}: counted {counted}, not {want}')
        if cured:
            if len(r) != 1:
                wrong.append(f'{what}: the host\'s frames break {len(r) - 1} times')
            if moved - got not in range(0, 45 * reqs + 1):  # the samples queued and not yet given back
                wrong.append(f'{what}: hw_ptr moved {moved}, the host got {got}')
        else:
            steady = r[2:-2]
            if not steady or {n for n, _ in steady} != {88, 89} or {g for _, g in steady} != {44, 45}:
                wrong.append(f'{what}: not the desk pattern (88 or 89 frames, then 44 or 45 skipped): {steady[:6]}')
            if abs(moved / got - 1.5) > 0.01:
                wrong.append(f'{what}: hw_ptr moved {moved / got:.3f} times what the host got, not 1.5')
    # The car's stutter (2026-11-21, q2muse-1123): the Q2's dwc2 in descriptor DMA sent every other packet empty with
    # one request in flight, and the car got half the audio. With one or two requests the model's core gives back that
    # share empty and its frames are lost; with three or four none, and the host gets 44.1 frames a millisecond, in
    # order: patch/q2car.sh's req_number is 4.
    for reqs, empty in ((1, 450), (2, 300), (3, 0), (4, 0)):
        host, moved, missed, sent, counted = run(data, 1, reqs, ddma=True)
        r = runs(host)
        got = len(host) - next(i for i, v in enumerate(host) if v) + 1
        zero = counted.get('in_zero', -1)
        print(f'descriptor DMA, {reqs} requests: host got {got} frames in {len(r)} runs, hw_ptr moved {moved}, '
              f'empty {zero}')
        if 'in_zero' in counted and zero != empty:
            wrong.append(f'descriptor DMA, {reqs} requests: {zero} empty completions, not {empty}')
        if not empty and (len(r) != 1 or moved - got not in range(0, 45 * reqs + 1)):
            wrong.append(f'descriptor DMA, {reqs} requests: the host\'s frames break or lag: {r[:4]}, moved {moved}, got {got}')
    import re
    car = re.search(r'req_number=(\d+)', (ROOT/'patch/q2car.sh').read_text())
    print(f'patch/q2car.sh: req_number {car.group(1) if car else None}')
    if not car or int(car.group(1)) < 3:
        wrong.append(f'patch/q2car.sh gives the car {car.group(1) if car else "no"} requests: descriptor DMA needs 3 or more')
    rate_wrong, seen = rate_run(data)
    print('rates: ' + '; '.join(seen))
    wrong += rate_wrong
    life_wrong, seen = life_run(data)
    print('stream life: ' + '; '.join(seen))
    wrong += life_wrong
    for w in wrong:
        print('FAIL  ' + w)
    print('ok' if not wrong else f'{len(wrong)} wrong')
    return 1 if wrong else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
