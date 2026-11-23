/* The control channel's GetElementAttributes for stock's bluetoothd (docs/internals.md#track-metadata),
 * linked at BT_META in tools/build.py and put in stock's control handler table in place of upstream's
 * handler, which reads a registered BlueZ player that stock never has, so headsets and cars got empty
 * attributes. This answers from libbtctl's g_track_meta, the record demo keeps current through
 * NotifyChanged, as Shanling's own browsing handlers do.
 *
 * Called as a control_pdu_handler: (session, pdu, transaction) -> AVC response type. pdu is the AVRCP
 * header (company id, PDU id, packet type, params_len big-endian, params). The request's params are an
 * 8-byte identifier, the attribute count and the attributes asked for (none: all); the answer is the
 * count, then each attribute (id, UTF-8 character set, length, text), in one packet: a text that would
 * pass BT_META_ROOM is cut short.
 *
 * Attribute 8, AVRCP 1.6's cover art, is the playing song's image handle, which demo's payload keeps
 * in BT_COVER_FILE's first seven bytes while the song has a cover (patch/bt_cover.c) and q2-bip
 * serves (docs/internals.md#bluetooth-cover-art); a song with no cover answers it with no value
 * (length 0, as AVRCP 1.6.2's table 6.27 allows), so the controller is told the song has none
 * instead of keeping the last song's. It exists only for a controller whose own SDP record says it
 * fetches covers (COVER_CT): for any other the file is never opened and 8 is no attribute, asked
 * for or not.
 *
 * GetPlayStatus (PDU 0x30) is upstream's in stock too, and with no player it answers every song as length
 * 0xFFFFFFFF at position 0, so a car never learnt where the song was and ran its own clock.
 * bt_meta_get_play_status answers the length and position demo's Now Playing (patch/now_source.c) keeps in
 * BT_PLAY_FILE, "position length stamp" in ms (-1: none, a live stream), the stamp on the monotonic clock;
 * while g_play_status says playing, the time since the stamp is added. The status is g_play_status's, as
 * stock's.
 *
 * bluetoothd is position independent, so this is too, by construction: every function is inlined into
 * its entry (no jal; there are three: this, GetPlayStatus's and bt_cover_sdp below), there is no data or constant pool (no absolute address), and libbtctl's
 * objects and pthread's functions are reached through bluetoothd's GOT at its $gp, which the caller
 * leaves set (BT_META_GOT_*: each slot's offset from $gp, filled in by -D). tools/build.py
 * (bt_meta) refuses an object with any relocation. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

/* libbtctl's record (docs/bluetoothd.md) */
struct track_meta {
    int track, total, time; /* time: ms */
    const char *title, *artist, *album, *genre;
};

#define INLINE static inline __attribute__((always_inline))

/* bluetoothd's GOT slot at offset from its $gp: what the dynamic linker put there */
INLINE void *got(char *gp, int offset) { return *(void *volatile *)(gp + offset); }

#define CTYPE_STABLE 0x0c
#define CTYPE_REJECTED 0x0a
#define STATUS_INVALID_PARAM 0x01
#define CHARSET_UTF8 106
#define ATTR_LAST 7
#define ATTR_COVER 8
#define HANDLE_DIGITS 7
/* A controller's AVRCP 1.6 feature bits for cover art: GetImageProperties, GetImage, GetLinkedThumbnail */
#define COVER_CT 0x380
/* struct avrcp's target, the remote controller's record as bluetoothd's data_init read it at connect
 * (session + 0xc in stock), and its features (+8) */
#define SESSION_TARGET 0xc
#define TARGET_FEATURES 8

/* An imported function (pthread's lock or unlock, libc's open, read or close) on up to three
 * arguments, called with $t9 at it and bluetoothd's $gp, as its PLT stub expects; what it returns */
INLINE int call3(char *gp, void *fn, void *x, void *y, unsigned z) {
    register void *t9 __asm__("$25") = fn;
    register void *a0 __asm__("$4") = x;
    register void *a1 __asm__("$5") = y;
    register unsigned a2 __asm__("$6") = z;
    register char *g __asm__("$28") = gp;
    register int v0 __asm__("$2");
    __asm__ volatile("addiu $29, $29, -16\n\tjalr $25\n\tnop\n\taddiu $29, $29, 16"
                     : "=r"(v0), "+r"(t9), "+r"(a0), "+r"(a1), "+r"(a2), "+r"(g)
                     :
                     : "$1", "$3", "$7", "$8", "$9", "$10", "$11", "$12", "$13", "$14", "$15", "$24", "$31",
                       "memory");
    return v0;
}
INLINE void call1(char *gp, void *fn, void *m) { (void)call3(gp, fn, m, 0, 0); }

/* Up to cap bytes of the file whose path's four words are w0..w3 into out: how many, or -1 for no file. The
 * path is built on the stack a word at a time (no constant pool). */
INLINE int read_small(char *gp, u32 w0, u32 w1, u32 w2, u32 w3, char *out, unsigned cap) {
    volatile u32 path[4];
    path[0] = w0, path[1] = w1, path[2] = w2, path[3] = w3;
    int fd = call3(gp, got(gp, BT_META_GOT_OPEN), (void *)path, 0 /* O_RDONLY */, 0), n;
    if (fd < 0) return -1;
    n = call3(gp, got(gp, BT_META_GOT_READ), (void *)(long)fd, out, cap);
    (void)call3(gp, got(gp, BT_META_GOT_CLOSE), (void *)(long)fd, 0, 0);
    return n;
}

/* The playing song's image handle into out: 1 when BT_COVER_FILE starts with HANDLE_DIGITS digits. */
INLINE int cover_handle(char *gp, char *out) {
    int n = read_small(gp, BT_COVER_PATH_0, BT_COVER_PATH_1, BT_COVER_PATH_2, BT_COVER_PATH_3, out, HANDLE_DIGITS),
        ok = 1;
    if (n != HANDLE_DIGITS) return 0;
    for (n = 0; n < HANDLE_DIGITS; ++n) ok &= out[n] >= '0' && out[n] <= '9';
    return ok;
}

INLINE u32 be32(const u8 *p) { return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }

INLINE unsigned slen(const char *s) {
    unsigned n = 0;
    while (s && s[n]) ++n;
    return n;
}

/* n as decimal into out; its length (n >= 0) */
INLINE unsigned decimal(int n, char *out) {
    char t[12];
    unsigned k = 0, i = 0;
    do t[k++] = (char)('0' + n % 10), n /= 10;
    while (n > 0);
    while (k) out[i++] = t[--k];
    return i;
}

/* Attribute id's text from m into num (numbers) or as m's pointer; 0 for none (a number below 0, a null text) */
INLINE const char *attr_text(const struct track_meta *m, u32 id, char *num, unsigned *len) {
    int v = 0;
    const char *s = 0;
    switch (id) {
    case 1: s = m->title; break;
    case 2: s = m->artist; break;
    case 3: s = m->album; break;
    case 6: s = m->genre; break;
    case 4: v = m->track; goto number;
    case 5: v = m->total; goto number;
    case 7: v = m->time; goto number;
    default: return 0;
    }
    if (!s) return 0;
    *len = slen(s);
    return s;
number:
    if (v < 0) return 0;
    *len = decimal(v, num);
    return num;
}

/* One attribute at out (room bytes left); bytes written, 0 when not even its header fits */
INLINE unsigned put_attr(u8 *out, unsigned room, u32 id, const char *text, unsigned len) {
    if (room < 8) return 0;
    if (len > room - 8) { /* cut short, at a character's start: one packet */
        len = room - 8;
        while (len && ((u8)text[len] & 0xc0) == 0x80) --len;
    }
    out[0] = (u8)(id >> 24), out[1] = (u8)(id >> 16), out[2] = (u8)(id >> 8), out[3] = (u8)id;
    out[4] = 0, out[5] = CHARSET_UTF8;
    out[6] = (u8)(len >> 8), out[7] = (u8)len;
    for (unsigned i = 0; i < len; ++i) out[8 + i] = (u8)text[i];
    return 8 + len;
}

INLINE u8 reject(u8 *pdu) {
    pdu[5] = 0, pdu[6] = 1;
    pdu[7] = STATUS_INVALID_PARAM;
    return CTYPE_REJECTED;
}

/* The entry stock's table calls: bluetoothd's $gp, which the compiled code may use as a register (o32
 * callers restore it), passed on as a fourth argument. */
__asm__(".text\n.globl bt_meta_get_element_attributes\n.set noreorder\n"
        "bt_meta_get_element_attributes:\n\tb bt_meta_impl\n\tmove $7, $28\n.set reorder\n");

u8 bt_meta_impl(void *session, u8 *pdu, u8 transaction, char *gp) {
    char space = ' ', none = 0; /* the no-track title and an empty value, on the stack: no constants */
    char handle[HANDLE_DIGITS];
    (void)transaction;
    unsigned len = (unsigned)pdu[5] << 8 | pdu[6], nattr, i, pos = 1, count = 0;
    u32 asked[ATTR_COVER];
    /* the controller's record: one that fetches covers has an eighth attribute, empty for a song with no cover */
    const char *target = *(const char *volatile *)((char *)session + SESSION_TARGET);
    unsigned last = ATTR_LAST;
    if (target && (*(const int *)(target + TARGET_FEATURES) & COVER_CT)) last = ATTR_COVER;
    u8 *params = pdu + 7;
    if (len < 9) return reject(pdu);
    nattr = params[8];
    if (len < 9 + nattr * 4) return reject(pdu);
    /* the ids asked for, the valid ones once each, in order; none asked: all of them */
    unsigned n = 0;
    if (!nattr)
        for (n = 0; n < last; ++n) asked[n] = n + 1;
    for (i = 0; i < nattr && n < last; ++i) {
        u32 id = be32(params + 9 + i * 4), seen = 0;
        for (unsigned k = 0; k < n; ++k) seen |= asked[k] == id;
        if (id >= 1 && id <= last && !seen) asked[n++] = id;
    }
    if (!n) return reject(pdu);
    int cover = 0; /* read before the lock: the file is demo's, not libbtctl's */
    for (i = 0; i < n; ++i)
        if (asked[i] == ATTR_COVER) cover = cover_handle(gp, handle);
    void *mutex = got(gp, BT_META_GOT_NOTIFY_MUTEX);
    call1(gp, got(gp, BT_META_GOT_LOCK), mutex);
    const struct track_meta *m = *(struct track_meta **)got(gp, BT_META_GOT_TRACK_META);
    for (i = 0; i < n; ++i) {
        char num[12];
        unsigned tl = 0, w;
        const char *t = m ? attr_text(m, asked[i], num, &tl) : 0;
        if (m && asked[i] == ATTR_COVER) t = cover ? handle : &none, tl = cover ? HANDLE_DIGITS : 0;
        if (!m && !nattr && asked[i] == 1) t = &space, tl = 1; /* no track: a title, as Shanling's browsing gives */
        if (!t && !(nattr && !m)) continue; /* an attribute with no value is left out */
        if (!t) t = &none, tl = 0;            /* asked for with no track: its empty value */
        w = put_attr(params + pos, BT_META_ROOM - pos, asked[i], t, tl);
        if (!w) break;
        pos += w, ++count;
    }
    call1(gp, got(gp, BT_META_GOT_UNLOCK), mutex);
    params[0] = (u8)count;
    pdu[4] = 0; /* a single packet */
    pdu[5] = (u8)(pos >> 8), pdu[6] = (u8)pos;
    return CTYPE_STABLE;
}

/* ---- GetPlayStatus (PDU 0x30) ---- */
#define PLAY_ROOM 40
#define PLAYING 1 /* g_play_status: AVRCP's PLAY_STATUS, 1 playing */

/* A decimal at *at in s (n bytes), after spaces, signed: 1 and *out, or 0 for none there */
INLINE int number_at(const char *s, int n, int *at, int *out) {
    int i = *at, neg = 0, any = 0;
    unsigned v = 0;
    while (i < n && s[i] == ' ') ++i;
    if (i < n && s[i] == '-') neg = 1, ++i;
    while (i < n && s[i] >= '0' && s[i] <= '9') v = v * 10 + (unsigned)(s[i++] - '0'), any = 1;
    *at = i, *out = neg ? -(int)v : (int)v;
    return any;
}

INLINE void put_be32(u8 *p, u32 v) { p[0] = (u8)(v >> 24), p[1] = (u8)(v >> 16), p[2] = (u8)(v >> 8), p[3] = (u8)v; }

__asm__(".text\n.globl bt_meta_get_play_status\n.set noreorder\n"
        "bt_meta_get_play_status:\n\tb bt_play_impl\n\tmove $7, $28\n.set reorder\n");

u8 bt_play_impl(void *session, u8 *pdu, u8 transaction, char *gp) {
    char b[PLAY_ROOM];
    int pos = -1, len = -1, stamp = 0, at = 0;
    volatile int ts[2]; /* struct timespec: seconds, nanoseconds */
    (void)session, (void)transaction;
    if (pdu[5] || pdu[6]) return reject(pdu);
    int n = read_small(gp, BT_PLAY_PATH_0, BT_PLAY_PATH_1, BT_PLAY_PATH_2, BT_PLAY_PATH_3, b, PLAY_ROOM);
    int known = n > 0 && number_at(b, n, &at, &pos) && number_at(b, n, &at, &len) && number_at(b, n, &at, &stamp);
    (void)call3(gp, got(gp, BT_META_GOT_CLOCK_GETTIME), (void *)1 /* CLOCK_MONOTONIC */, (void *)ts, 0);
    u32 now = (u32)ts[0] * 1000u + (u32)ts[1] / 1000000u;
    void *mutex = got(gp, BT_META_GOT_NOTIFY_MUTEX);
    call1(gp, got(gp, BT_META_GOT_LOCK), mutex);
    int status = *(volatile int *)got(gp, BT_META_GOT_PLAY_STATUS);
    const struct track_meta *m = *(struct track_meta **)got(gp, BT_META_GOT_TRACK_META);
    int time = m ? m->time : -1;
    call1(gp, got(gp, BT_META_GOT_UNLOCK), mutex);
    if (!known) pos = -1, len = time > 0 ? time : -1;
    else if (pos >= 0 && status == PLAYING) {
        pos += (int)(now - (u32)stamp);
        if (len > 0 && pos > len) pos = len;
    }
    put_be32(pdu + 7, len < 0 ? 0xffffffffu : (u32)len);
    put_be32(pdu + 11, pos < 0 ? 0xffffffffu : (u32)pos);
    pdu[15] = (u8)status;
    pdu[4] = 0, pdu[5] = 0, pdu[6] = 9;
    return CTYPE_STABLE;
}

/* ---- the AVRCP target's SDP record (docs/internals.md#bluetooth-cover-art) ----
 * Stock's record builder calls a helper (0x383b8) that adds the browsing channel to the record's
 * AdditionalProtocolDescriptorLists. tools/build.py sends the target's call here (through a far jump
 * laid over stock's GetElementAttributes, which nothing reaches any more), and this adds two lists
 * where the helper added one: browsing as the helper wrote it (L2CAP PSM 0x1b, AVCTP at the version
 * the caller holds), then cover art as AVRCP 1.6 has it (L2CAP at q2-bip's PSM, OBEX). The lists are
 * built with bluetoothd's own SDP functions, reached through its GOT (BT_SDP_GOT_*). */
#define SDP_UINT16 0x09
#define UUID_L2CAP 0x0100
#define UUID_AVCTP 0x0017
#define UUID_OBEX 0x0008
#define PSM_BROWSING 0x1b

#define SDP(name, x, y) ((void *)(long)call3(gp, got(gp, BT_SDP_GOT_##name), (void *)(x), (void *)(y), 0))

__asm__(".text\n.globl bt_cover_sdp\n.set noreorder\n"
        "bt_cover_sdp:\n\tb bt_cover_sdp_impl\n\tmove $6, $28\n.set reorder\n");

void bt_cover_sdp_impl(void *record, void *avctp_version, char *gp) {
    u32 l2cap[5], avctp[5], obex[5]; /* BlueZ's uuid_t: 20 bytes */
    volatile u16 psm_browsing = PSM_BROWSING, psm_cover = BT_COVER_PSM;
    (void)SDP(UUID16, l2cap, UUID_L2CAP), (void)SDP(UUID16, avctp, UUID_AVCTP), (void)SDP(UUID16, obex, UUID_OBEX);
    void *browse_psm = SDP(DATA_ALLOC, SDP_UINT16, &psm_browsing), *cover_psm = SDP(DATA_ALLOC, SDP_UINT16, &psm_cover);
    void *b0 = SDP(LIST_APPEND, SDP(LIST_APPEND, 0, l2cap), browse_psm);
    void *b1 = SDP(LIST_APPEND, SDP(LIST_APPEND, 0, avctp), avctp_version);
    void *browse = SDP(LIST_APPEND, SDP(LIST_APPEND, 0, b0), b1);
    void *c0 = SDP(LIST_APPEND, SDP(LIST_APPEND, 0, l2cap), cover_psm);
    void *c1 = SDP(LIST_APPEND, 0, obex);
    void *cover = SDP(LIST_APPEND, SDP(LIST_APPEND, 0, c0), c1);
    void *all = SDP(LIST_APPEND, SDP(LIST_APPEND, 0, browse), cover);
    (void)SDP(SET_ADD_ACCESS_PROTOS, record, all);
    (void)SDP(FREE, browse_psm, 0), (void)SDP(FREE, cover_psm, 0);
    (void)SDP(LIST_FREE, b0, 0), (void)SDP(LIST_FREE, b1, 0), (void)SDP(LIST_FREE, browse, 0);
    (void)SDP(LIST_FREE, c0, 0), (void)SDP(LIST_FREE, c1, 0), (void)SDP(LIST_FREE, cover, 0);
    (void)SDP(LIST_FREE, all, 0);
}
