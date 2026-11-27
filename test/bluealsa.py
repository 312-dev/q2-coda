#!/usr/bin/env python3
"""Run the actual MIPS DROP paths with the encoder signal deliberately delayed, SBC's bitpool step, AAC's peak step
and the A2DP write MTU clamp through each codec's encoder thread.

Usage: python test/bluealsa.py BUILD_DIRECTORY (requires the existing Unicorn).
"""
import pathlib, platform, struct, subprocess, sys
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import *
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
from build import (BLUEALSA, BT_AAC_RATE, BT_DROP, BT_DROP_HANDLER, BT_MTU_MAX, BT_PCM_CALLERS, BT_SBC_POOL,
                   BT_SILENCE_SAMPLES, bt_silence, load_elf, machine, read, word)


def check_drop(raw, queued, caller=None):
    u = machine(raw, 0x1000000)
    pcm, transport, state, stop = 0x1000000, 0x1000200, 0x1000400, 0x1000600
    word(u, pcm, transport)
    word(u, pcm + 4, transport + 0x50)
    word(u, state + 0x1c, 48000 * 300)  # the previous track's pacing clock
    word(u, state + 0x30, 100)
    fifo = [queued]
    locked = [False]
    signals, flushes = [], []
    def hook(u, address, size, _):
        if address == 0x414640:  # stock DROP repoll
            u.emu_stop()
            return
        if address == 0x4140b8:  # stock nonblocking io_pcm_flush
            assert u.reg_read(UC_MIPS_REG_A0) == pcm
            batch = min(fifo[0], 32768)
            fifo[0] -= batch
            flushes.append((batch, locked[0]))
            u.reg_write(UC_MIPS_REG_V0, batch // 2)
        elif address == 0x40b738:  # stock send_signal
            assert u.reg_read(UC_MIPS_REG_A0) == transport + 0x50
            assert not locked[0]
            signals.append(u.reg_read(UC_MIPS_REG_A1))
            u.reg_write(UC_MIPS_REG_V0, 0)
        elif address in (0x430690, 0x4303a0):  # PCM fd mutex
            assert u.reg_read(UC_MIPS_REG_A0) == pcm + 0xc
            assert locked[0] == (address == 0x4303a0)
            locked[0] = address == 0x430690
            u.reg_write(UC_MIPS_REG_V0, 0)
        else:
            return
        u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE, hook)
    saved = (UC_MIPS_REG_S0, UC_MIPS_REG_S1, UC_MIPS_REG_S2, UC_MIPS_REG_S7)
    for i, register in enumerate(saved): u.reg_write(register, 0xabcd0000 + i)
    stack = 0x100ff00
    u.reg_write(UC_MIPS_REG_SP, stack)
    u.reg_write(UC_MIPS_REG_A0, pcm)
    u.reg_write(UC_MIPS_REG_RA, stop)
    u.emu_start(BT_DROP, stop, count=1000)
    assert u.reg_read(UC_MIPS_REG_PC) == stop
    assert u.reg_read(UC_MIPS_REG_V0) == 0 and u.reg_read(UC_MIPS_REG_SP) == stack
    assert [u.reg_read(r) for r in saved] == [0xabcd0000 + i for i in range(len(saved))]
    assert signals == [6]
    empty_at_ack = fifo[0] == 0 and all(held for _, held in flushes)
    # New track data is written after the acknowledged DROP, before the encoder
    # finally handles it. The stock handler wrongly discards these samples.
    fifo[0] = 4096
    flush_count = len(flushes)
    u.reg_write(UC_MIPS_REG_S1, pcm)
    u.reg_write(UC_MIPS_REG_S2, state)
    if caller:
        address, offset = caller
        word(u, stack + 0x54, address)
        buffer = 0x1001000
        if offset is not None:
            ffb = state + offset
            for off, value in ((0, buffer), (4, buffer+128), (8, 1024), (12, 2)):
                word(u, ffb+off, value)
            u.reg_write(UC_MIPS_REG_S6, buffer+128)
            u.reg_write(UC_MIPS_REG_S5, 960)
        else:
            word(u, stack+0x40, 0)  # caller's base offset, saved s4
            word(u, stack+0x4c, 128)  # caller's partial byte offset, saved s7
            word(u, stack+0x48, 224)  # caller's remaining samples, saved s6
            u.reg_write(UC_MIPS_REG_S6, buffer+128)
            u.reg_write(UC_MIPS_REG_S5, 224)
    u.emu_start(BT_DROP_HANDLER, 0x41463c, count=1000)
    rewound = True
    if caller:
        rewound = u.reg_read(UC_MIPS_REG_S6) == buffer
        if offset is not None:
            rewound &= read(u, ffb+4)==buffer and u.reg_read(UC_MIPS_REG_S5)==1024
            rewound &= read(u, ffb)==buffer and read(u, ffb+8)==1024 and read(u, ffb+12)==2
        else:
            rewound &= read(u, stack+0x4c)==0 and read(u, stack+0x48)==256 and u.reg_read(UC_MIPS_REG_S5)==256
    # Stock repolls at 0x414640; stop before the imported cancellation function.
    return empty_at_ack and fifo[0] == 4096 and len(flushes) == flush_count and read(u, state + 0x1c) == 0 and read(u, state + 0x30) == 0xffffffff and rewound


def silence_machine(raw):
    """io_poll_and_read_pcm under Unicorn, poll and io_pcm_read scripted: call(poll result, ...)."""
    u = machine(raw, 0x1000000)
    io, pcm, transport, buffer, stop = 0x1000000, 0x1000100, 0x1000300, 0x1001000, 0x100f000
    word(u, pcm + 4, transport)
    log = {}
    def signed(v): return v - (1 << 32 if v >> 31 else 0)
    def hook(u, address, size, _):
        if address == 0x4307a0:  # poll(fds, 2, timeout): a signal first, if scripted
            log['timeout'] = signed(u.reg_read(UC_MIPS_REG_A2))
            log.setdefault('timeouts', []).append(log['timeout'])
            signal = log.pop('signal', None)
            u.mem_write(u.reg_read(UC_MIPS_REG_A0) + 6, struct.pack('<H', signal is not None))
            u.reg_write(UC_MIPS_REG_V0, 1 if signal is not None else log['poll'])
            if signal is not None: log['next'] = signal
        elif address == 0x40b7c4:  # the transport's signal read
            word(u, u.reg_read(UC_MIPS_REG_A1), log.pop('next'))
        elif address == 0x41414c:  # io_pcm_read
            u.reg_write(UC_MIPS_REG_V0, log['got'])
        elif address == 0x42ff70:  # pthread_cond_signal: Drain's waiter woken
            log['drained'] = True
        elif address == 0x42ff00:  # clock_gettime: asrsync_init
            log['asrs'] = True
        elif address not in (0x430ac0, 0x40c7a0):  # setcancelstate, pcm active
            return
        u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE, hook)
    def call(poll, timeout=-1, frames=1, samples=256, got=64, width=2, signal=None):
        log.clear()
        log.update(poll=poll, got=got, **({'signal': signal} if signal is not None else {}))
        word(u, io + 0x30, timeout)
        word(u, io + 0x1c, frames)
        u.mem_write(pcm + 0x2a, struct.pack('<H', width << 8 | width * 8))
        u.mem_write(buffer, b'\xff' * 2048)
        for register, value in ((UC_MIPS_REG_SP, 0x100e000), (UC_MIPS_REG_A0, io), (UC_MIPS_REG_A1, pcm),
                                (UC_MIPS_REG_A2, buffer), (UC_MIPS_REG_A3, samples), (UC_MIPS_REG_RA, stop)):
            u.reg_write(register, value)
        u.emu_start(0x4145d0, stop, count=20000)
        log['timeout_after'] = signed(read(u, io + 0x30))
        log['zeroed'] = bytes(u.mem_read(buffer, 2048)).index(b'\xff')
        return signed(u.reg_read(UC_MIPS_REG_V0))
    return u, call, log


def check_silence(stock, raw):
    """Drain's timeout, or PAUSE's 100 ms, after real PCM fills silence (stock sends nothing), later
    polls don't wait, real PCM ends it, and with the budget spent or none armed it behaves as stock."""
    _, call, log = silence_machine(stock)
    assert call(1) == 64 and call(0, timeout=100) == 0, 'stock: Drain timeout returns nothing'
    # (the scripted second poll then returns 0, which a real unbounded one wouldn't)
    assert call(0, signal=3) == 0 and log['timeouts'] == [-1, -1], 'stock: PAUSE polls again without a timeout'
    u, call, log = silence_machine(raw)
    quiet = bt_silence()[1]['quiet']
    assert call(1) == 64 and log['timeout'] == -1 and read(u, quiet) == BT_SILENCE_SAMPLES, 'no idle wait'
    assert call(0, timeout=100) == 256 and log['timeout'] == 100 and log['drained'], "Drain's timeout fills"
    assert log['timeout_after'] == -1 and log['zeroed'] == 512, '256 S16 samples, waiter woken'
    assert call(0, frames=0) == 256 and log['timeout'] == 0 and log.get('asrs'), 'filling: no wait, pacing starts'
    assert call(0, width=4) == 256 and log['zeroed'] == 1024, 'S32 (LDAC): 4 bytes a sample'
    assert read(u, quiet) == BT_SILENCE_SAMPLES - 768
    assert call(1, got=0) == 0 and read(u, quiet) == BT_SILENCE_SAMPLES - 768, 'PCM closed: no re-arm'
    word(u, quiet, 100)
    assert call(0) == 256 and read(u, quiet) == 0, 'the budget runs out'
    assert call(1) == 64 and log['timeout'] == -1 and read(u, quiet) == BT_SILENCE_SAMPLES
    assert call(1) == 64 and log['timeout'] == -1, 'real PCM ends the fill'
    assert call(0, signal=3) == 256 and log['timeouts'] == [-1, 100], 'PAUSE (a skip): 100 ms, then the fill'
    assert call(1) == 64
    word(u, quiet, 0)
    assert call(0, timeout=100) == 0 and log['drained'] and log['timeout_after'] == -1, 'no budget: drain as stock'


def sbc_machine(raw):
    """The SBC encoder thread's sbc_encode call (0x406578 to 0x406588) under Unicorn, on an sbc_t from the
    shipped libsbc's sbc_init_a2dp (48 kHz joint stereo, 16 blocks, 8 subbands, loudness, bitpool 2 to 53) with
    the thread's starting bitpool 33 (SBC's medium quality at 48 kHz): frame(pool) sets BT_SBC_POOL's byte and
    encodes one frame, returning its bitpool, its length and BT_SBC_POOL's own and now bytes."""
    u = machine(raw, 0x1000000)
    u.mem_map(0x2000000, 0x100000)  # the heap
    try:
        u.mem_map(BT_SBC_POOL & ~0xfff, 0x1000)  # stock has no payload there: its byte is never read
    except Exception:
        pass
    heap = [0x2000000]
    def hook(u, address, size, _):
        a0, a1, a2 = (u.reg_read(r) for r in (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2))
        if address == 0x4300e0:  # memset
            u.mem_write(a0, bytes([a1 & 0xff]) * a2)
            result = a0
        elif address == 0x430670:  # malloc
            result = heap[0] = (heap[0] + 63) & ~63
            heap[0] += a0
        elif 0x42fcc0 <= address < 0x430c00:
            raise AssertionError(f'libc call {address:#x} not expected')
        else:
            return
        u.reg_write(UC_MIPS_REG_V0, result)
        u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE, hook)
    sp, conf, pcm, out, stop = 0x1004000, 0x1000100, 0x1001000, 0x1002000, 0x100ff00
    sbc = sp + 0x280
    u.mem_write(sbc, b'\xee' * 20)
    u.mem_write(conf, bytes([0x11, 0x15, 2, 53]))
    for register, value in ((UC_MIPS_REG_A0, sbc), (UC_MIPS_REG_A1, 0), (UC_MIPS_REG_A2, conf), (UC_MIPS_REG_A3, 4),
                            (UC_MIPS_REG_T9, 0x421c2c), (UC_MIPS_REG_SP, 0x100f000), (UC_MIPS_REG_RA, stop)):
        u.reg_write(register, value)
    u.emu_start(0x421c2c, stop, count=100000)
    assert u.reg_read(UC_MIPS_REG_V0) == 0 and u.mem_read(sbc + 11, 1) == b'\0', 'sbc_init_a2dp clears the sbc_t'
    u.mem_write(sbc + 9, bytes([33]))  # 0x406294: the thread's bitpool, sbc_a2dp_get_bitpool's
    u.mem_write(pcm, bytes(range(256)) * 2)
    def frame(pool):
        u.mem_write(BT_SBC_POOL, bytes([pool]))
        u.mem_write(out, bytes(256))
        word(u, sp + 0x10, 256)  # out_len
        for register, value in ((UC_MIPS_REG_SP, sp), (UC_MIPS_REG_V0, sp + 0x2b4), (UC_MIPS_REG_FP, pcm),
                                (UC_MIPS_REG_A2, 512), (UC_MIPS_REG_A3, out)):
            u.reg_write(register, value)
        u.emu_start(0x406578, 0x406588, count=200000)
        assert u.reg_read(UC_MIPS_REG_PC) == 0x406588 and u.reg_read(UC_MIPS_REG_V0) == 512, 'one frame of PCM'
        head = bytes(u.mem_read(out, 3))
        assert head[0] == 0x9c, 'an SBC frame'
        own, now = bytes(u.mem_read(BT_SBC_POOL + 1, 2))
        return head[2], read(u, sp + 0x2b4), own, now
    return frame


def sbc_length(bitpool):  # joint stereo, 8 subbands, 16 blocks: header, scale factors, join bits and samples
    return 4 + 8 + (8 + 16 * bitpool + 7) // 8


def check_sbc_bitpool(stock, raw):
    """The encoder's bitpool follows BT_SBC_POOL's byte from the next frame, never above the thread's own; stock
    ignores it."""
    frame = sbc_machine(stock)
    assert [frame(p)[:2] for p in (0, 19, 29)] == [(33, sbc_length(33))] * 3, 'stock: the bitpool stays'
    frame = sbc_machine(raw)
    assert frame(0) == (33, 79, 33, 33), 'none asked: its own, published'
    assert frame(29) == (29, 71, 33, 29), 'a rung down, the next frame, 8 bytes shorter'
    assert frame(19) == (19, sbc_length(19), 33, 19) and sbc_length(19) == 51, 'the floor'
    assert frame(45)[:2] == (33, 79), 'never above its own: its packets are sized for it'
    assert frame(1)[:2] == (33, 79) and frame(0)[:2] == (33, 79), 'back to its own'


# A2DP codec ids as bluealsa keeps them in the transport (s0 + 0x1e), each with its encoder thread.
SBC, AAC, APTX, APTX_HD, LDAC = 0x0000, 0x0002, 0x4fff, 0x24ff, 0x2dff
ENCODERS = {SBC: 0x4060e0, AAC: 0x416d0c, APTX: 0x417e80, APTX_HD: 0x418988}
# Stock's .plt stubs the harness answers (all others return 0): libc's memory calls, libopenaptx's and fdk-aac's.
STUBS = {0x430670: 'malloc', 0x430030: 'calloc', 0x42ff80: 'realloc', 0x430270: 'free', 0x4300e0: 'memset',
         0x430450: 'memcpy', 0x430830: 'memmove', 0x430200: 'aptx_init', 0x430710: 'aptx_encode',
         0x430560: 'aptx_finish', 0x4309d0: 'aacEncOpen', 0x430980: 'aacEncInfo', 0x430b60: 'aacEncEncode',
         0x430600: 'aacEncoder_SetParam'}
PLT = (0x42fcc0, 0x430b80)
L2CAP_2DH5 = 679  # a 2-DH5's payload: the most one baseband packet carries on 2 Mbps EDR


def acquire(raw, codec, mtu):
    """transport_acquire_bt_a2dp from Acquire's answer to the fd list (0x40abe8 to 0x40ac00) under Unicorn: the
    write MTU it leaves in the transport (s0 + 0x48) for codec, from Acquire's mtu."""
    u = machine(raw, 0x1000000)
    t, sp, stop = 0x1000000, 0x1008000, 0x40ac00
    u.mem_write(t + 0x1e, struct.pack('<H', codec))
    u.mem_write(sp + 0x30, struct.pack('<HH', mtu, 672))  # g_variant_get's write and read MTUs
    def hook(u, address, size, _):
        if address == 0x430110:  # g_dbus_message_get_unix_fd_list
            u.reg_write(UC_MIPS_REG_V0, 0)
            u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE, hook)
    for register, value in ((UC_MIPS_REG_S0, t), (UC_MIPS_REG_SP, sp), (UC_MIPS_REG_S1, 0)):
        u.reg_write(register, value)
    u.emu_start(0x40abe8, stop, count=100)
    assert u.reg_read(UC_MIPS_REG_PC) == stop and read(u, t + 0x44) == 672, 'the read MTU as stock'
    return read(u, t + 0x48)


def encode(raw, aptx, codec, mtu, aac=(), packets=3, aac_conf=None, asks=None, sets=None, set_fails=(), rate=None):
    """codec's encoder thread under Unicorn, on the shipped encoder (SBC is built into bluealsa, aptX and aptX HD are
    libopenaptx, loaded at 0), with mtu as the transport's write MTU, fed PCM as fast as it asks: the first packets
    it writes. AAC's fdk-aac is scripted instead: each aacEncEncode gives the next of aac's frame lengths. For AAC,
    aac_conf is the stream's configuration (the transport's four bytes at 0x1a4: object type, rates and channels,
    VBR and bitrate), asks maps a frame's index to the byte q2guard writes at BT_AAC_RATE before it, and sets (a
    list) gets each aacEncoder_SetParam made after aacEncInfo, (the index of the frame it comes before, the handle,
    the parameter, the value); a call before a frame in set_fails answers an error. rate (a list) gets the four
    bytes at BT_AAC_RATE once the packets are written."""
    u = machine(raw, 0x1000000)
    load_elf(u, aptx)
    u.mem_map(0x2000000, 0x200000)  # the heap
    stubs = dict(STUBS)
    for name, address in (('malloc', 0x35a0), ('free', 0x3590), ('memset', 0x35b0), ('memmove', 0x3580)):
        stubs[address] = name  # libopenaptx's own
    lib = {'aptx_init': 0x1e3c, 'aptx_encode': 0x1ebc, 'aptx_finish': 0x1ea4}
    heap, out, frames = [0x2010000], [], list(aac)
    t, pcm = 0x2000000, 0x2000000 + 0x1c0
    width = 4 if codec == APTX_HD else 2
    u.mem_write(t + 0x1c, struct.pack('<HH', 1, codec))  # A2DP source
    word(u, t + 0x48, mtu)
    word(u, t + 0x1ec, 2)  # channels
    word(u, t + 0x1f0, 48000)
    word(u, t + 0x50, t)  # the thread's transport
    u.mem_write(pcm + 0x2a, struct.pack('<H', width << 8 | width * 8))
    if codec == SBC:
        u.mem_write(t + 0x1a4, bytes([0x11, 0x15, 2, 53]))  # 48 kHz joint stereo, 16 blocks, 8 subbands
    if codec == AAC and aac_conf:
        u.mem_write(t + 0x1a4, aac_conf)
    try:
        u.mem_map(BT_SBC_POOL & ~0xfff, 0x1000)  # stock has no payload there
    except Exception:
        pass
    asks = dict(asks or {})
    if 0 in asks:
        u.mem_write(BT_AAC_RATE, bytes([asks[0]]))
    encoded, info = [0], [False]
    def alloc(n):
        heap[0] = (heap[0] + 15) & ~15
        heap[0] += n
        return heap[0] - n
    def hook(u, address, size, _):
        a = [u.reg_read(r) for r in (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)]
        name = stubs.get(address)
        if name in lib:
            u.reg_write(UC_MIPS_REG_T9, lib[name])
            u.reg_write(UC_MIPS_REG_PC, lib[name])
            return
        if name in ('malloc', 'calloc', 'realloc'):
            n = a[0] * a[1] if name == 'calloc' else a[1] if name == 'realloc' else a[0]
            result = alloc(n)
            u.mem_write(result, bytes(n))
            if name == 'realloc' and a[0]:
                u.mem_write(result, bytes(u.mem_read(a[0], n)))
        elif name == 'memset':
            u.mem_write(a[0], bytes([a[1] & 0xff]) * a[2])
            result = a[0]
        elif name in ('memcpy', 'memmove'):
            u.mem_write(a[0], bytes(u.mem_read(a[1], a[2])))
            result = a[0]
        elif name == 'aacEncOpen':
            word(u, a[0], 0x2000f00)
            result = 0
        elif name == 'aacEncInfo':  # maxOutBufBytes, maxAncBytes, inBufFillLevel, inputChannels, frameLength
            u.mem_write(a[1], struct.pack('<5I', 6144 * 2, 0, 0, 2, 1024))
            result = 0
            info[0] = True
        elif name == 'aacEncoder_SetParam':
            result = 0
            if info[0] and sets is not None:  # past the encoder's setup: the loop's
                sets.append((encoded[0], a[0], a[1], a[2]))
                result = 0x20 if encoded[0] in set_fails else 0  # AACENC_INVALID_CONFIG
        elif name == 'aacEncEncode':
            result = 0
            if a[1]:
                sp = u.reg_read(UC_MIPS_REG_SP)
                args = read(u, sp + 0x10)
                n = frames.pop(0) if frames else 0
                if n:
                    u.mem_write(read(u, read(u, a[2] + 4)), bytes(i & 0xff for i in range(len(out) * 7, len(out) * 7 + n)))
                word(u, args, n)
                word(u, args + 4, read(u, a[3]))  # every sample taken
                encoded[0] += 1
                if encoded[0] in asks:  # q2guard's write, between frames
                    u.mem_write(BT_AAC_RATE, bytes([asks[encoded[0]]]))
        elif address == 0x4145d0:  # io_poll_and_read_pcm: all it asks for
            u.mem_write(a[2], bytes((i * 37) & 0xff for i in range(a[3] * width)))
            result = a[3]
        elif address == 0x413d14:  # io_bt_write: a packet
            out.append(bytes(u.mem_read(a[1], a[2])))
            result = a[2]
            if len(out) == packets:
                u.emu_stop()
        elif address in (0x40b49c, 0x40c5e4, 0x4049d8) or PLT[0] <= address < PLT[1]:
            result = 0  # thread state, a stop with no clients, asrsync_sync's pacing, the rest of libc and libpthread
        else:
            return
        u.reg_write(UC_MIPS_REG_V0, result)
        u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE, hook, begin=PLT[0], end=PLT[1])  # only where it answers: the encoders run unhooked
    for address in (*stubs, 0x4145d0, 0x413d14, 0x40b49c, 0x40c5e4, 0x4049d8):
        if not PLT[0] <= address < PLT[1]:
            u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
    for register, value in ((UC_MIPS_REG_A0, t + 0x50), (UC_MIPS_REG_SP, 0x100f000), (UC_MIPS_REG_RA, 0x100ff00)):
        u.reg_write(register, value)
    u.emu_start(ENCODERS[codec], 0x100ff00, count=50_000_000)
    assert len(out) == packets, f'{codec:#x}: {len(out)} packets'
    if rate is not None:
        rate.extend(u.mem_read(BT_AAC_RATE, 4))
    return out


AACENC_BITRATE, AACENC_PEAK_BITRATE, AAC_HANDLE = 0x101, 0x207, 0x2000f00
# The transport's AAC configuration (0x1a4): MPEG-2 AAC LC, 48 kHz, stereo, then VBR (bit 7) and the 23-bit bitrate.
AAC_VBR_240 = bytes([0x80, 0x00, 0x84, 0x80 | 0x03, 0xa9, 0x80])
AAC_CBR_240 = bytes([0x80, 0x00, 0x84, 0x03, 0xa9, 0x80])


def check_aac_peak(stock, raw, aptx):
    """The AAC encoder thread sets fdk-aac's peak (VBR) or bitrate (CBR) from BT_AAC_RATE's byte before the next
    frame, once a change, only below the stream's own, and its own again at 0; it publishes its own; stock ignores
    the byte. fdk-aac is scripted (encode's), so this is the calls made, not the frames' new sizes."""
    frames = [400] * 10
    def run(binary, conf, asks, fails=(), rate=None):
        sets = []
        out = encode(binary, aptx, AAC, BT_MTU_MAX, aac=frames, packets=10, aac_conf=conf, asks=asks, sets=sets,
                     set_fails=fails, rate=rate)
        assert [len(p) for p in out] == [412] * 10, 'every frame one packet, as without the byte'
        return sets
    asks = {2: 192, 4: 160, 5: 160, 6: 128, 8: 0}
    assert run(stock, AAC_VBR_240, asks) == [], 'stock: the byte is not read'
    sets = run(raw, AAC_VBR_240, asks)
    assert sets == [(2, AAC_HANDLE, AACENC_PEAK_BITRATE, 192000), (4, AAC_HANDLE, AACENC_PEAK_BITRATE, 160000),
                    (6, AAC_HANDLE, AACENC_PEAK_BITRATE, 128000), (8, AAC_HANDLE, AACENC_PEAK_BITRATE, 240000)], \
        'VBR: the peak, before the next frame, once a change, back to the configured 240000 at 0'
    assert run(raw, AAC_CBR_240, asks) == [(f, h, AACENC_BITRATE, v) for f, h, _, v in sets], 'CBR: the bitrate'
    assert run(raw, AAC_VBR_240, {1: 240, 2: 250, 3: 255}) == [], 'at or above its own: its own, already in use'
    assert run(raw, AAC_VBR_240, {0: 128}) == [(0, AAC_HANDLE, AACENC_PEAK_BITRATE, 128000)], \
        'a byte left from before: from the first frame'
    rate = []
    assert run(raw, AAC_VBR_240, {2: 192, 4: 160}, fails=(2,), rate=rate) == [
        (2, AAC_HANDLE, AACENC_PEAK_BITRATE, 192000)], 'a set that fails: nothing more asked of the stream'
    assert rate == [160, 0, 0, 5], 'its own published as 0 after the failure, the own peak still in use'
    for conf, mode in ((AAC_VBR_240, 5), (AAC_CBR_240, 0)):
        rate = []
        run(raw, conf, {3: 160}, rate=rate)
        assert rate == [160, 240, 160, mode], 'asked, its own (kbit/s), in use, its mode (VBR 5 as the build sets it)'
    rate = []
    run(stock, AAC_VBR_240, {3: 160}, rate=rate)
    assert rate == [160, 0, 0, 0], 'stock publishes nothing'


def ldac_mtu(ldac, mtu):
    """libldacBT's ldacBT_init_handle_encode on a cleared handle with mtu (990 kbps, stereo, S32, 48 kHz): its
    result and the packet it then builds to, its transmit size (handle + 0x24)."""
    u = machine(ldac, 0x2000000)
    handle = 0x2001000
    def hook(u, address, size, _):
        a0, a1, a2 = (u.reg_read(r) for r in (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2))
        if address == 0xad30:  # memset
            u.mem_write(a0, bytes([a1 & 0xff]) * a2)
        elif address == 0xacf0:  # memcpy
            u.mem_write(a0, bytes(u.mem_read(a1, a2)))
        elif address in (0xad10, 0xad40, 0xace0, 0xad20, 0xad00):  # malloc, calloc, free, floorf, sqrtf
            raise AssertionError(f'libc call {address:#x}')
        else:
            return
        u.reg_write(UC_MIPS_REG_V0, a0)
        u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE, hook)
    for register, value in ((UC_MIPS_REG_A0, handle), (UC_MIPS_REG_A1, mtu), (UC_MIPS_REG_A2, 0), (UC_MIPS_REG_A3, 1),
                            (UC_MIPS_REG_T9, 0x1a00), (UC_MIPS_REG_SP, 0x200f000), (UC_MIPS_REG_RA, 0x200ff00)):
        u.reg_write(register, value)
    word(u, 0x200f000 + 0x10, 4)  # LDACBT_SMPL_FMT_S32
    word(u, 0x200f000 + 0x14, 48000)
    u.emu_start(0x1a00, 0x200ff00, count=100000)
    return u.reg_read(UC_MIPS_REG_V0) & 0xffffffff, read(u, handle + 0x24)



def sbc_frames(packet):
    """An SBC packet's frame count (its media header) and each frame's length (from the first frame's bitpool)."""
    return packet[12] & 0xf, sbc_length(packet[15])


def check_mtu(stock, raw, aptx, ldac):
    """Acquire's write MTU of 1021 (the RTL8733BS's ACL MTU) is cut to BT_MTU_MAX for every codec but LDAC, so each
    encoder's packets fit one 2-DH5 and are still filled with as many frames as fit; stock keeps 1021, whose
    packets each need a second baseband packet."""
    assert BT_MTU_MAX + 4 == L2CAP_2DH5, "a 2-DH5's payload less L2CAP's header (Android's 663 plus RTP's 12)"
    for codec in (SBC, AAC, APTX, APTX_HD, LDAC):
        assert acquire(stock, codec, 1021) == 1021, 'stock: Acquire\'s MTU as it is'
        assert acquire(raw, codec, 600) == 600 and acquire(raw, codec, BT_MTU_MAX) == BT_MTU_MAX, 'a smaller one kept'
        assert acquire(raw, codec, 1021) == (1021 if codec == LDAC else BT_MTU_MAX)
    # LDAC: libldacBT refuses an MTU under 679 and builds every packet to 661 bytes, which with RTP's 12 and
    # LDAC's 1-byte media header is one 2-DH5 at any MTU it takes.
    assert ldac_mtu(ldac, BT_MTU_MAX) == (0xffffffff, 0) and ldac_mtu(ldac, 678)[0] == 0xffffffff
    assert ldac_mtu(ldac, 679) == (0, 661) and ldac_mtu(ldac, 1021) == (0, 661)
    assert 661 + 12 + 1 <= BT_MTU_MAX
    for binary, fits in ((stock, False), (raw, True)):
        mtu = acquire(binary, APTX_HD, 1021)
        sizes = [len(p) for p in encode(binary, aptx, APTX_HD, mtu)]
        assert sizes == [12 + (mtu - 12) // 6 * 6] * 3, 'aptX HD: RTP, then as many 6-byte frames as fit'
        assert (sizes[0] <= BT_MTU_MAX) == fits, f'aptX HD packets of {sizes[0]}'
    assert [len(p) for p in encode(raw, aptx, APTX_HD, BT_MTU_MAX)] == [12 + 110 * 6] * 3, '110 aptX HD frames'
    assert [len(p) for p in encode(raw, aptx, APTX, BT_MTU_MAX)] == [168 * 4] * 3, '168 aptX frames, no RTP'
    assert [len(p) for p in encode(stock, aptx, APTX, 1021)] == [255 * 4] * 3
    if platform.machine() == 'aarch64':
        # Unicorn 2.1.4 (and its dev branch) on an arm64 host leaves s1 and s4 0 after the loads at 0x406274/0x406278
        # in this SBC run, stock's binary as well, and the encoder jumps to 0; x86-64's Unicorn runs it right
        # (docs/building.md#building-on-macos). The MTU itself is checked above, by Acquire, for every codec.
        print('SBC packets at the MTU: not run on arm64 (Unicorn mis-runs the SBC encoder there)')
    else:
        for mtu in (1021, BT_MTU_MAX):
            packets = encode(raw, aptx, SBC, mtu)
            count, length = sbc_frames(packets[0])
            assert count == (mtu - 13) // length and [len(p) for p in packets] == [13 + count * length] * 3, 'SBC fills'
        assert sbc_frames(packets[0]) == (9, 71) and len(packets[0]) == 652, 'SBC at bitpool 29: 9 frames, not 14'
    # AAC: a frame longer than the packet's room is split across packets (RFC 3016), marked on its last.
    frames = [900, 400, BT_MTU_MAX - 12, BT_MTU_MAX - 11]
    packets = encode(raw, aptx, AAC, BT_MTU_MAX, aac=frames, packets=6)
    assert [(len(p), p[1] >> 7) for p in packets] == [(675, 0), (249, 1), (412, 1), (675, 1), (675, 0), (13, 1)]
    assert packets[0][12:] + packets[1][12:] == bytes(i & 0xff for i in range(900)), 'the frame whole, in order'
    assert [(len(p), p[1] >> 7) for p in encode(stock, aptx, AAC, 1021, aac=frames, packets=4)] == [
        (912, 1), (412, 1), (675, 1), (676, 1)], 'stock: no AAC frame split at 1021'


def check(directory):
    directory = pathlib.Path(directory)
    def extract(name):
        return subprocess.check_output(['unsquashfs', '-cat', str(directory / name), BLUEALSA])
    stock, patched = extract('stock.squashfs'), extract('rootfs.squashfs')
    # Execute stock argument construction independently of the reset patch's
    # metadata: it must identify the actual caller's PCM tail, not nearby memory.
    layouts = ((0x406474, 0x220, 0x294), (0x4150a8, 0x118, 0x14c),
               (0x417438, 0x280, 0x318), (0x4180e0, 0x220, 0x254),
               (0x418c50, 0x220, 0x280), (0x41a6d8, 0x2a0, None))
    for (caller, offset), (start, io, ffb) in zip(BT_PCM_CALLERS, layouts):
        u = machine(stock, 0x1000000)
        stack, buffer = 0x1008000, 0x1001000
        u.reg_write(UC_MIPS_REG_SP, stack)
        if ffb is not None:
            assert ffb-io == offset
            u.mem_write(stack+ffb, struct.pack('<4I', buffer, buffer+128, 1024, 2))
            # The first caller caches data/tail/element-size before this block.
            for register, value in ((UC_MIPS_REG_FP,buffer), (UC_MIPS_REG_A2,buffer+128), (UC_MIPS_REG_T0,2)):
                u.reg_write(register,value)
            want = 960
        else:
            for register, value in ((UC_MIPS_REG_S1,buffer), (UC_MIPS_REG_S7,128), (UC_MIPS_REG_S6,224)):
                u.reg_write(register,value)
            want = 224
        u.emu_start(start, 0x4145d0, count=100)
        assert u.reg_read(UC_MIPS_REG_PC)==0x4145d0, hex(caller)
        assert u.reg_read(UC_MIPS_REG_A0)==stack+io and u.reg_read(UC_MIPS_REG_A2)==buffer+128
        assert u.reg_read(UC_MIPS_REG_A3)==want
    for queued in (0, 4096, 65536):
        assert not check_drop(stock, queued), 'Regression must reproduce against stock'
        assert check_drop(patched, queued), f'DROP lost new-track audio with {queued} old bytes'
    for caller in BT_PCM_CALLERS:
        assert not check_drop(stock, 4096, caller)
        assert check_drop(patched, 4096, caller), f'DROP retained partial PCM for {caller[0]:x}'
    check_silence(stock, patched)
    check_sbc_bitpool(stock, patched)
    lib = lambda name: subprocess.check_output(['unsquashfs', '-cat', str(directory / 'rootfs.squashfs'), name])
    check_aac_peak(stock, patched, lib('usr/lib/libopenaptx.so.0.2.1'))
    check_mtu(stock, patched, lib('usr/lib/libopenaptx.so.0.2.1'), lib('usr/lib/libldacBT.so.2.0.19'))
    print("Bluetooth silence: Drain's or PAUSE's 100 ms timeout fills silence until real PCM or the budget ends; stock sends nothing.")
    print('SBC bitpool: the encoder follows BT_SBC_POOL from the next frame, never above its own; stock ignores it.')
    print("AAC peak: the encoder sets fdk-aac's peak (VBR) or bitrate (CBR) from BT_AAC_RATE before the next frame, "
          'only below its own; stock ignores it.')
    filled = 'AAC and aptX' if platform.machine() == 'aarch64' else 'SBC, AAC and aptX'  # check_mtu: SBC's not run on arm64
    print(f'A2DP write MTU: {BT_MTU_MAX} (one 2-DH5) for SBC, AAC, aptX and aptX HD, {filled} packets filled; LDAC keeps its own; stock keeps 1021.')
    print('Bluetooth DROP: delayed encoder signal preserves new PCM, flushes all old PCM and resets pacing; stock reproduces the fault.')


if __name__ == '__main__':
    check(sys.argv[1])
