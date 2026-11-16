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
 * bluetoothd is position independent, so this is too, by construction: every function is inlined into
 * the one entry (no jal), there is no data or constant pool (no absolute address), and libbtctl's
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

/* pthread's lock or unlock on m, called with $t9 at it and bluetoothd's $gp, as its PLT stub expects */
INLINE void call1(char *gp, void *fn, void *m) {
    register void *t9 __asm__("$25") = fn;
    register void *a0 __asm__("$4") = m;
    register char *g __asm__("$28") = gp;
    __asm__ volatile("jalr $25\n\tnop"
                     : "+r"(t9), "+r"(a0), "+r"(g)
                     :
                     : "$1", "$2", "$3", "$5", "$6", "$7", "$8", "$9", "$10", "$11", "$12", "$13", "$14",
                       "$15", "$24", "$31", "memory");
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
    (void)session, (void)transaction;
    unsigned len = (unsigned)pdu[5] << 8 | pdu[6], nattr, i, pos = 1, count = 0;
    u32 asked[ATTR_LAST];
    u8 *params = pdu + 7;
    if (len < 9) return reject(pdu);
    nattr = params[8];
    if (len < 9 + nattr * 4) return reject(pdu);
    /* the ids asked for, the valid ones once each, in order; none asked: all of them */
    unsigned n = 0;
    if (!nattr)
        for (n = 0; n < ATTR_LAST; ++n) asked[n] = n + 1;
    for (i = 0; i < nattr && n < ATTR_LAST; ++i) {
        u32 id = be32(params + 9 + i * 4), seen = 0;
        for (unsigned k = 0; k < n; ++k) seen |= asked[k] == id;
        if (id >= 1 && id <= ATTR_LAST && !seen) asked[n++] = id;
    }
    if (!n) return reject(pdu);
    void *mutex = got(gp, BT_META_GOT_NOTIFY_MUTEX);
    call1(gp, got(gp, BT_META_GOT_LOCK), mutex);
    const struct track_meta *m = *(struct track_meta **)got(gp, BT_META_GOT_TRACK_META);
    for (i = 0; i < n; ++i) {
        char num[12];
        unsigned tl = 0, w;
        const char *t = m ? attr_text(m, asked[i], num, &tl) : 0;
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
