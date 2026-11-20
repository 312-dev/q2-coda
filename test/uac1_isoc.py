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
Kernel calls are modeled as test/uac1_bind.py models them. Exit 0 when all hold."""
import argparse
import importlib.util
from pathlib import Path
import struct
import sys

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
SUB_STREAM, SUB_RUNTIME = 0x30, 0x70
RT_STATUS, RT_PERIOD_SIZE, RT_FRAME_BITS, RT_DMA_AREA, RT_DMA_BYTES = 0xb4, 0x54, 0x68, 0x244, 0x24c
SNDRV_PCM_STATE_RUNNING = 3
RING_FRAMES, PERIOD_FRAMES = 4096, 1024


class Machine(uac1_bind.Machine):
    def __init__(self, data):
        super().__init__(data)
        self.queue = []

    def service(self, name, a):
        if name in ('_snd_pcm_stream_lock_irqsave', '_raw_spin_lock_irqsave', 'snd_pcm_stream_unlock_irqrestore',
                    '_raw_spin_unlock_irqrestore', 'snd_pcm_period_elapsed'):
            return 0
        if name == 'usb_ep_queue':
            self.queue.append(a[1])
            return 0
        return super().service(name, a)


def run(data, resend, reqs, steps=900):
    """(frames the host got, in order; hw_ptr's travel in frames; in_missed; in_sent) for steps completions"""
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
    for i in range(steps):
        req = m.queue.pop(0)
        sent = i % 3 != 2
        if sent:
            n = m.read(req + REQ_LENGTH)
            host += struct.unpack(f'<{n // 4}I', m.read(m.read(req + REQ_BUF), n))
        m.write(req + REQ_STATUS, 0 if sent else ENODATA)
        m.call('u_audio_iso_complete', ep, req)
        hw = m.read(prm + PRM_HW_PTR) // 4
        moved += (hw - last) % RING_FRAMES
        last = hw
    return host, moved, m.read(sym['in_missed']), m.read(sym['in_sent'])


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
        host, moved, missed, sent = run(data, resend, reqs)
        r = runs(host)
        got = len(host) - next(i for i, v in enumerate(host) if v) + 1  # from the counter's frame 0, after the zeroed packets
        what = f'resend_missed {resend}, {reqs} requests'
        print(f'{what}: host got {got} counter frames, hw_ptr moved {moved}; runs and skips '
              f'{sorted(set(r[2:-2])) if len(r) > 4 else r}; in_missed {missed}, in_sent {sent}')
        if (missed, sent) != (300, 600):
            wrong.append(f'{what}: in_missed {missed} and in_sent {sent}, not 300 and 600')
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
    for w in wrong:
        print('FAIL  ' + w)
    print('ok' if not wrong else f'{len(wrong)} wrong')
    return 1 if wrong else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
