/* q2-bip's OBEX and BIP, with no reads of the device (q2bip.h; docs/internals.md#bluetooth-cover-art).
 * One request packet in, one response out: OBEX never has the responder speak first, and Single
 * Response Mode, the one exception, is never agreed to (the SRM header is passed over, which OBEX
 * defines as "off"), so the controller's next GET paces every packet of a cover. */
#include "q2bip.h"

typedef unsigned char u8;

/* AVRCP 1.6's cover art service, the Target a controller connects to (and the Who it is answered) */
static const u8 COVER_ART_UUID[16] = { 0x71, 0x63, 0xdd, 0x54, 0x4a, 0x7e, 0x11, 0xe2,
                                       0xb4, 0x7c, 0x00, 0x50, 0xc2, 0x49, 0x00, 0x48 };

static unsigned be16(const u8 *p) { return (unsigned)p[0] << 8 | p[1]; }
static void put16(u8 *p, unsigned v) { p[0] = (u8)(v >> 8), p[1] = (u8)v; }
static unsigned slen(const char *s) {
    unsigned n = 0;
    while (s[n]) ++n;
    return n;
}
static int same(const void *a, const void *b, unsigned n) {
    const u8 *x = a, *y = b;
    while (n--)
        if (*x++ != *y++) return 0;
    return 1;
}
static void copy(void *d, const void *s, unsigned n) {
    u8 *x = d;
    const u8 *y = s;
    while (n--) *x++ = *y++;
}

void bip_handle(unsigned key, char *out) {
    for (int i = BIP_HANDLE - 1; i >= 0; --i) out[i] = (char)('0' + key % 10), key /= 10;
    out[BIP_HANDLE] = 0;
}

/* The header at p (left bytes of packet remain): its value and the value's length; the whole
 * header's length, 0 for one that runs past the packet. */
static unsigned header(const u8 *p, unsigned left, const u8 **value, unsigned *len) {
    unsigned n;
    if (!left) return 0;
    switch (p[0] >> 6) {
    case 2: *value = p + 1, *len = 1, n = 2; break;
    case 3: *value = p + 1, *len = 4, n = 5; break;
    default:
        if (left < 3 || (n = be16(p + 1)) < 3) return 0;
        *value = p + 3, *len = n - 3;
    }
    return n <= left ? n : 0;
}

/* a response of code alone */
static unsigned answer(u8 *out, unsigned code) {
    out[0] = (u8)code;
    put16(out + 1, 3);
    return 3;
}

static void get_reset(struct bip *b) {
    b->kind = BIP_NONE, b->sending = 0, b->described = 0, b->handle[0] = 0;
    b->body = 0, b->body_len = b->body_at = 0;
}

/* name="value" in xml: the value's start and length, 0 when the attribute is not there */
static const char *attribute(const char *xml, unsigned n, const char *name, unsigned *len) {
    unsigned k = slen(name);
    for (unsigned i = 0; i + k + 2 <= n; ++i) {
        if (!same(xml + i, name, k) || xml[i + k] != '=' || (i && xml[i - 1] != ' ')) continue;
        char q = xml[i + k + 1];
        if (q != '"' && q != '\'') continue;
        unsigned at = i + k + 2, e = at;
        while (e < n && xml[e] != q) ++e;
        if (e == n) return 0;
        *len = e - at;
        return xml + at;
    }
    return 0;
}

/* a decimal number at *s (moved past it); -1 for none */
static int number(const char **s, const char *end) {
    int v = -1;
    while (*s < end && **s >= '0' && **s <= '9') v = (v < 0 ? 0 : v * 10) + (*(*s)++ - '0'), v = v > 99999 ? 99999 : v;
    return v;
}

/* BIP's pixel forms: "W*H", "W1*H1-W2*H2" and "W1**-W2*H2" (a range of one aspect, whose H1 is H2
 * scaled). An encoding other than JPEG, a fixed size other than 200*200 or a range without it does
 * not fit; no attribute asks for nothing. */
int bip_descriptor_fits(const char *xml, unsigned n) {
    unsigned len = 0;
    const char *v = attribute(xml, n, "encoding", &len);
    if (v && !(len == 4 && same(v, "JPEG", 4)) && len) return 0;
    if (!(v = attribute(xml, n, "pixel", &len)) || !len) return 1;
    const char *e = v + len;
    int w1 = number(&v, e), h1, w2, h2;
    if (w1 < 0 || v == e || *v++ != '*') return 0;
    if (v < e && *v == '*') h1 = -1, ++v; /* W1**: the height follows the aspect */
    else if ((h1 = number(&v, e)) < 0) return 0;
    if (v == e) return h1 == BIP_SIDE && w1 == BIP_SIDE;
    if (*v++ != '-') return 0;
    w2 = number(&v, e);
    if (w2 < 0 || v == e || *v++ != '*' || (h2 = number(&v, e)) < 0 || v != e) return 0;
    if (h1 < 0) return w2 == h2 && w1 <= BIP_SIDE && BIP_SIDE <= w2; /* a square aspect alone */
    return w1 <= BIP_SIDE && BIP_SIDE <= w2 && h1 <= BIP_SIDE && BIP_SIDE <= h2;
}

static unsigned put_text(char *out, unsigned at, const char *s) {
    while (*s) out[at++] = *s++;
    return at;
}

/* GetImageProperties' XML for handle: the one native version, no variants */
static unsigned properties(char *out, const char *handle, unsigned size) {
    char num[12];
    unsigned k = 0, at;
    do num[k++] = (char)('0' + size % 10), size /= 10;
    while (size);
    at = put_text(out, 0, "<image-properties version=\"1.0\" handle=\"");
    at = put_text(out, at, handle);
    at = put_text(out, at, "\"><native encoding=\"JPEG\" pixel=\"200*200\" size=\"");
    while (k) out[at++] = num[--k];
    return put_text(out, at, "\"/></image-properties>");
}

/* CONNECT: version, flags, the peer's largest packet, then its headers. Only AVRCP's cover art
 * target is served. */
static unsigned do_connect(struct bip *b, const u8 *in, unsigned n, u8 *out) {
    const u8 *v;
    unsigned len, h, at = 7, target = 0;
    if (n < 7) return answer(out, OBEX_BAD_REQUEST);
    for (; at < n; at += h) {
        if (!(h = header(in + at, n - at, &v, &len))) return answer(out, OBEX_BAD_REQUEST);
        if (in[at] == OBEX_H_TARGET) target = len == 16 && same(v, COVER_ART_UUID, 16);
    }
    if (!target) return answer(out, OBEX_NOT_ACCEPTABLE);
    b->peer_max = be16(in + 5) < BIP_PKT_MIN ? BIP_PKT_MIN : be16(in + 5);
    b->connected = 1, b->close = 0;
    get_reset(b);
    out[0] = OBEX_SUCCESS;
    out[3] = 0x10, out[4] = 0; /* OBEX 1.0, no flags */
    put16(out + 5, BIP_PKT);
    out[7] = OBEX_H_CONNECTION, out[8] = out[9] = out[10] = 0, out[11] = 1;
    out[12] = OBEX_H_WHO, put16(out + 13, 19);
    copy(out + 15, COVER_ART_UUID, 16);
    put16(out + 1, 31);
    return 31;
}

/* The next piece of the body: Continue with Body while more is left after it, Success with
 * End-of-Body for the last. GetImage's first carries the Length header BIP asks for. */
static unsigned send_body(struct bip *b, u8 *out) {
    unsigned max = b->peer_max < BIP_PKT ? b->peer_max : BIP_PKT, at = 3, room, n, last;
    if (b->kind != BIP_PROPERTIES && !b->body_at) {
        out[at] = OBEX_H_LENGTH;
        out[at + 1] = (u8)(b->body_len >> 24), out[at + 2] = (u8)(b->body_len >> 16);
        put16(out + at + 3, b->body_len & 0xffff);
        at += 5;
    }
    room = max - at - 3;
    n = b->body_len - b->body_at;
    last = n <= room;
    if (!last) n = room;
    out[at] = last ? OBEX_H_END : OBEX_H_BODY;
    put16(out + at + 1, n + 3);
    copy(out + at + 3, b->body + b->body_at, n);
    at += 3 + n;
    b->body_at += n;
    out[0] = last ? OBEX_SUCCESS : OBEX_CONTINUE;
    put16(out + 1, at);
    if (last) get_reset(b);
    return at;
}

static const struct {
    const char *type;
    int kind;
} TYPES[] = { { "x-bt/img-properties", BIP_PROPERTIES }, { "x-bt/img-img", BIP_IMAGE }, { "x-bt/img-thm", BIP_THUMBNAIL } };

static unsigned do_get(struct bip *b, const u8 *in, unsigned n, u8 *out) {
    const u8 *v;
    unsigned len, h, at = 3;
    for (; at < n; at += h) {
        if (!(h = header(in + at, n - at, &v, &len))) goto bad;
        switch (in[at]) {
        case OBEX_H_CONNECTION:
            if (v[0] | v[1] | v[2] | (v[3] ^ 1)) return get_reset(b), answer(out, OBEX_UNAVAILABLE);
            break;
        case OBEX_H_TYPE:
            if (b->sending) goto bad;
            b->kind = BIP_NONE;
            for (unsigned i = 0; i < sizeof TYPES / sizeof *TYPES; ++i) {
                unsigned k = slen(TYPES[i].type); /* with its NUL, or without from a lax peer */
                if ((len == k || (len == k + 1 && !v[k])) && same(v, TYPES[i].type, k)) b->kind = TYPES[i].kind;
            }
            if (!b->kind) return get_reset(b), answer(out, OBEX_BAD_REQUEST);
            break;
        case OBEX_H_HANDLE: /* UTF-16BE digits and a NUL */
            if (b->sending || len != (BIP_HANDLE + 1) * 2) goto bad;
            for (unsigned i = 0; i < BIP_HANDLE; ++i) {
                if (v[i * 2] || v[i * 2 + 1] < '0' || v[i * 2 + 1] > '9') goto bad;
                b->handle[i] = (char)v[i * 2 + 1];
            }
            b->handle[BIP_HANDLE] = 0;
            break;
        case OBEX_H_DESCRIPTOR:
            if (b->sending) goto bad;
            b->described = !bip_descriptor_fits((const char *)v, len);
            break;
        }
    }
    if (b->sending) return send_body(b, out);
    if (!(in[0] & OBEX_FINAL)) return answer(out, OBEX_CONTINUE); /* more headers to come */
    if (!b->kind || !b->handle[0]) goto bad;
    if (!b->image || !b->image(b->ctx, b->handle, &b->body, &b->body_len) || !b->body_len)
        return get_reset(b), answer(out, OBEX_NOT_FOUND);
    if (b->kind == BIP_IMAGE && b->described) return get_reset(b), answer(out, OBEX_NOT_ACCEPTABLE);
    if (b->kind == BIP_PROPERTIES) b->body_len = properties(b->xml, b->handle, b->body_len), b->body = (const u8 *)b->xml;
    b->sending = 1, b->body_at = 0;
    return send_body(b, out);
bad:
    get_reset(b);
    return answer(out, OBEX_BAD_REQUEST);
}

unsigned bip_feed(struct bip *b, const u8 *in, unsigned n, u8 *out) {
    if (n < 3 || be16(in + 1) != n || n > BIP_PKT) return answer(out, OBEX_BAD_REQUEST);
    if (in[0] == OBEX_CONNECT) return do_connect(b, in, n, out);
    if (!b->connected) return answer(out, OBEX_UNAVAILABLE);
    switch (in[0]) {
    case OBEX_DISCONNECT:
        b->connected = 0, b->close = 1;
        get_reset(b);
        return answer(out, OBEX_SUCCESS);
    case OBEX_ABORT:
        get_reset(b);
        return answer(out, OBEX_SUCCESS);
    case OBEX_GET:
    case OBEX_GET | OBEX_FINAL: return do_get(b, in, n, out);
    }
    get_reset(b);
    return answer(out, OBEX_NOT_IMPLEMENTED);
}
