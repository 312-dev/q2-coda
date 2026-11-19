/*
 * bluealsa: keep the A2DP stream going with silence through a skip (docs/internals.md#bluetooth-aac).
 * tools/build.py (bt_silence) links this at BT_SILENCE, with BUDGET defined, and jumps here from
 * io_poll_and_read_pcm (0x4145d0): s0 its result, s1 the PCM, s2 the io state (frames 0x1c,
 * poll timeout 0x30, -1 or Drain's 100), s5 samples wanted, s6 the buffer, fp -1.
 *
 * A skip: hciplayer sends Drain (its last PCM drains, then Drain's 100 ms poll times out), or demo
 * pauses it first and it sends PAUSE, which the build points at Drain's case. Stock then sends
 * nothing until the next song's PCM, ~300 ms on, and the headset's buffer runs dry. Here that
 * 100 ms timeout returns silence, and later polls don't wait, so the encoder's
 * own pacing sets the rate (catching up the 100 ms in a burst) until real PCM comes back, or
 * BUDGET samples of silence have gone since the last real PCM.
 * ponytail: one quiet/silent for every encoder thread, fine for one headset at a time; a second
 * stream, or a stream torn down mid-silence, shares or inherits up to BUDGET of silence.
 *
 * Also SBC's bitpool, lowered while the radio cannot keep up (sbc_bp below,
 * docs/internals.md#bluetooth-link-monitor), and the A2DP write MTU, cut so each media packet fits one
 * 2-DH5 (mtu_clamp below).
 */
.set noreorder
.set noat
.text

/* 0x41465c, before poll (its delay slot sets a1): while filling, poll without waiting. */
.globl timeout
timeout:
    lw    $6, 0x30($18)
    addiu $3, $zero, -1
    bne   $6, $3, 1f          /* Drain's wait: as stock */
    lui   $8, %hi(silent)
    lw    $9, %lo(silent)($8)
    beqz  $9, 1f
    nop
    lw    $9, %lo(quiet)($8)
    movn  $6, $zero, $9       /* budget left: timeout 0 */
1:  j     0x414664
    nop

/* 0x414684, poll timed out (its delay slot clears a1). */
.globl fill
fill:
    jal   0x430ac0            /* pthread_setcancelstate(DISABLE), as stock */
    addiu $4, $zero, 1
    lw    $3, 0x30($18)
    addiu $8, $zero, -1
    beq   $3, $8, 2f          /* our timeout 0 */
    nop
    jal   0x42ff70            /* Drain's: wake its waiter, block again, as stock */
    addiu $4, $17, 0x78
    sw    $30, 0x30($18)
2:  lui   $8, %hi(quiet)
    lw    $9, %lo(quiet)($8)
    beqz  $9, 5f              /* nothing played lately: return 0, as stock */
    nop
    beqz  $21, 5f
    addiu $9, $zero, 1
    sw    $9, %lo(silent)($8)
    lw    $9, %lo(quiet)($8)
    subu  $10, $9, $21
    sltu  $9, $9, $21
    movn  $10, $zero, $9      /* quiet = max(quiet - samples, 0) */
    sw    $10, %lo(quiet)($8)
    lhu   $3, 0x2a($17)
    ext   $3, $3, 8, 6        /* bytes a sample */
    mul   $3, $3, $21
    addu  $3, $3, $22
    move  $9, $22
3:  beq   $9, $3, 4f
    nop
    sb    $zero, 0($9)
    b     3b
    addiu $9, $9, 1
4:  move  $16, $21            /* as if read: pacing starts if it hasn't, return them */
    j     0x4147c8
    lw    $2, 0x1c($18)
5:  j     0x41469c            /* return s0, 0 */
    nop

/* 0x414754, in place of jal io_pcm_read: real PCM re-arms the budget and ends a fill. */
.globl read
read:
    addiu $sp, $sp, -24
    sw    $31, 20($sp)
    jal   0x41414c
    nop
    lw    $31, 20($sp)
    blez  $2, 6f
    addiu $sp, $sp, 24
    lui   $8, %hi(quiet)
    sw    $zero, %lo(silent)($8)
    li    $9, BUDGET
    sw    $9, %lo(quiet)($8)
6:  jr    $31
    nop

.align 2
.globl quiet
quiet:  .word 0
silent: .word 0

/* 0x406580, in place of the SBC encoder thread's jal sbc_encode (its one call; a0 the thread's sbc_t,
 * a1-a3 and the stack's two arguments left as they are). The bitpool is sbc_pool's, as q2guard
 * wrote it, but never above the one the thread started with (its packets are sized for that one's
 * frames), and that one for 0 or 1. libsbc's sbc_encode takes a new bitpool at its next frame (it
 * compares the sbc_t's with its frame's and works out the new length): no renegotiation and no
 * gap. The starting bitpool is kept in the sbc_t's padding byte (11, after endian), which
 * sbc_init_a2dp cleared, and published in sbc_own; the one used goes to sbc_now. t0-t3 only. */
.globl sbc_bp
sbc_bp:
    lbu   $8, 11($4)          /* the thread's own, 0 before its first frame */
    lui   $10, %hi(sbc_pool)
    bnez  $8, 7f
    lbu   $9, 9($4)
    sb    $9, 11($4)
    move  $8, $9
    sb    $9, %lo(sbc_own)($10)
7:  lbu   $9, %lo(sbc_pool)($10)
    sltiu $11, $9, 2
    movn  $9, $8, $11         /* none asked: its own */
    sltu  $11, $8, $9
    movn  $9, $8, $11         /* never above its own */
    sb    $9, 9($4)
    j     0x4038e0            /* sbc_encode's stub, as stock called it */
    sb    $9, %lo(sbc_now)($10)

/* 0x40abf8, transport_acquire_bt_a2dp, in place of its jal g_dbus_message_get_unix_fd_list (whose delay slot,
 * left as stock, stores Acquire's write MTU in the transport, s0 + 0x48). Every encoder sizes its packets from
 * that word, so it is cut to MTU_MAX: an L2CAP packet that fits one 2-DH5, the best packet a controller without
 * 3 Mbps EDR sends (docs/internals.md#bluetooth-link-monitor, Packet size). LDAC (codec 0x2dff, s0 + 0x1e) keeps
 * it: libldacBT refuses an MTU under 679 and already sizes its packets for one 2-DH5. t0 and t1 only. */
.globl mtu_clamp
mtu_clamp:
    lhu   $8, 0x1e($16)
    addiu $9, $zero, 0x2dff
    beq   $8, $9, 8f
    lw    $8, 0x48($16)
    sltiu $8, $8, MTU_MAX + 1
    bnez  $8, 8f              /* fits already: as stock */
    addiu $9, $zero, MTU_MAX
    sw    $9, 0x48($16)
8:  j     0x430110            /* g_dbus_message_get_unix_fd_list's stub, as stock called it */
    nop

/* At BT_SILENCE + 0x200 (tools/build.py BT_SBC_POOL, patch/q2bt.h), where q2guard finds them. */
.org 0x200
.globl sbc_pool
sbc_pool: .byte 0
sbc_own:  .byte 0
sbc_now:  .byte 0
          .byte 0
