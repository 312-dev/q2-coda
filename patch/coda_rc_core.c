/* coda-rc's transport-agnostic core: frames, the protobuf subset, pairing, authentication and sealing
 * (docs/remote-protocol.md). Nothing here touches a socket or a file; coda_rc.c supplies the crc_* platform
 * functions in coda_rc.h. */
#include "coda_rc_sys.h"
#include "coda_rc.h"
#include "monocypher/monocypher.h"
#include "monocypher/monocypher-ed25519.h"

/* ---- protobuf ---- */

static void put(pbw *w, const void *s, size_t n) {
    if (w->n + n > w->cap) { w->over = 1; return; }
    memcpy(w->p + w->n, s, n);
    w->n += n;
}

static void raw_varint(pbw *w, uint64_t v) {
    uint8_t b[10];
    int i = 0;
    do { b[i] = (v & 127) | (v > 127 ? 128 : 0); v >>= 7; ++i; } while (v);
    put(w, b, i);
}

void pb_varint(pbw *w, unsigned field, uint64_t v) {
    raw_varint(w, field << 3);
    raw_varint(w, v);
}

void pb_bytes(pbw *w, unsigned field, const void *s, size_t n) {
    raw_varint(w, field << 3 | 2);
    raw_varint(w, n);
    put(w, s, n);
}

void pb_str(pbw *w, unsigned field, const char *s) {
    if (s && *s) pb_bytes(w, field, s, strlen(s));
}

/* A nested message's length is written as a fixed two-byte varint (a padded encoding protobuf accepts), so the
 * body is written in place and patched: nested messages here are under 16 KB. */
size_t pb_begin(pbw *w, unsigned field) {
    raw_varint(w, field << 3 | 2);
    size_t mark = w->n;
    put(w, "\x80\x00", 2);
    return mark;
}

void pb_end(pbw *w, size_t mark) {
    size_t n = w->n - mark - 2;
    if (w->over) return;
    if (n >= 16384) { w->over = 1; return; }
    w->p[mark] = 128 | (n & 127);
    w->p[mark + 1] = n >> 7;
}

static int get_varint(pbr *r, uint64_t *v) {
    *v = 0;
    for (int i = 0; i < 10; ++i) {
        if (r->p >= r->end) return -1;
        uint8_t b = *r->p++;
        *v |= (uint64_t)(b & 127) << (7 * i);
        if (!(b & 128)) return 0;
    }
    return -1;
}

int pb_next(pbr *r, pbf *f) {
    uint64_t key;
    if (r->p >= r->end) return 0;
    if (get_varint(r, &key) || key >> 3 == 0 || key >> 32) return -1;
    f->field = key >> 3;
    f->wire = key & 7;
    f->s = 0;
    f->n = 0;
    f->v = 0;
    if (f->wire == 0) return get_varint(r, &f->v) ? -1 : 1;
    if (f->wire == 2) {
        uint64_t n;
        if (get_varint(r, &n) || n > (uint64_t)(r->end - r->p)) return -1;
        f->s = r->p;
        f->n = n;
        r->p += n;
        return 1;
    }
    if (f->wire == 5) { if (r->end - r->p < 4) return -1; r->p += 4; return 1; }
    if (f->wire == 1) { if (r->end - r->p < 8) return -1; r->p += 8; return 1; }
    return -1;
}

void pb_cstr(const pbf *f, char *buf, size_t cap) {
    size_t n = f->wire == 2 ? f->n : 0;
    if (n > cap - 1) n = cap - 1;
    for (size_t i = 0; i < n; ++i) buf[i] = f->s[i] && f->s[i] != '\n' ? (char)f->s[i] : ' ';
    buf[n] = 0;
}

/* ---- keys (tools/coda_rc_crypto.py computes the same) ---- */

static void mac(uint8_t out[64], const uint8_t *key, size_t kn, const char *label, const void *a, size_t an,
                const void *b, size_t bn, const void *c, size_t cn) {
    crypto_sha512_hmac_ctx ctx;
    crypto_sha512_hmac_init(&ctx, key, kn);
    crypto_sha512_hmac_update(&ctx, (const uint8_t *)label, strlen(label));
    crypto_sha512_hmac_update(&ctx, a, an);
    crypto_sha512_hmac_update(&ctx, b, bn);
    crypto_sha512_hmac_update(&ctx, c, cn);
    crypto_sha512_hmac_final(&ctx, out);
}

static int zero32(const uint8_t *p) {
    static const uint8_t z[32];
    return crypto_verify32(p, z) == 0;
}

/* ---- frames ---- */

static void nonce_of(uint8_t n[12], uint64_t ctr) {
    memset(n, 0, 4);
    for (int i = 0; i < 8; ++i) n[4 + i] = ctr >> (8 * i);
}

static uint8_t tx[CRC_MAX_FRAME];

int crc_send(crc_session *s, int type, unsigned req_id, const uint8_t *p, size_t n) {
    if (s->state == S_CLOSED || n > CRC_MAX_PAYLOAD) return -1;
    int sealed = s->state == S_AUTHED;
    size_t body = 4 + n + (sealed ? CRC_TAG : 0);
    tx[0] = body >> 8;
    tx[1] = body;
    tx[2] = type;
    tx[3] = sealed ? CRC_FLAG_SEALED : 0;
    tx[4] = req_id >> 8;
    tx[5] = req_id;
    if (sealed) {
        uint8_t nonce[12];
        crypto_aead_ctx ctx;
        nonce_of(nonce, s->tx_ctr++);
        crypto_aead_init_ietf(&ctx, s->kd, nonce);
        crypto_aead_write(&ctx, tx + CRC_HDR, tx + CRC_HDR + n, tx, CRC_HDR, p, n);
        crypto_wipe(&ctx, sizeof ctx);
    } else if (n) {
        memcpy(tx + CRC_HDR, p, n);
    }
    return crc_write(s, tx, 2 + body);
}

int crc_error(crc_session *s, unsigned req_id, int code, const char *message) {
    uint8_t b[160];
    pbw w = {b, 0, sizeof b, 0};
    pb_varint(&w, 1, code);
    pb_str(&w, 2, message);
    return crc_send(s, T_ERROR, req_id, b, w.n);
}

/* ---- pairing ---- */

/* One pairing at a time for the whole device, so the code on the screen belongs to one phone. The
 * code lives CRC_PAIR_TIMEOUT_MS from when it was shown, whichever of that phone's sessions holds it:
 * a phone whose connection drops while the user types reconnects to the same code
 * (docs/remote-protocol.md#pairing-a-pake-on-the-6-digit-code). Keeping it costs nothing: a session
 * that never sends PairFinish learns nothing of the code, and a wrong guess still ends it. */
static struct {
    crc_session *owner;  /* the session pairing on the code; 0 while its phone reconnects */
    char code[7];        /* the code on screen; empty when none */
    char name[CRC_NAME]; /* the phone it is shown for */
    uint32_t since, locked_until;
    int fails;
    int shown;           /* the code is Pair a Phone's, from its QR code (crc_code_shown) */
    uint32_t shows[CRC_PAIR_SHOWS]; /* when the last CRC_PAIR_SHOWS codes went up, oldest at shows_n's slot */
    unsigned shows_n;
} pair;

/* The owner's part in the pairing wiped, the code kept */
static void pair_leave(void) {
    if (!pair.owner) return;
    crypto_wipe(pair.owner->scalar, 32);
    if (pair.owner->state == S_PAIRING) pair.owner->state = S_HELLO;
    pair.owner = 0;
}

static void pair_end(int outcome) {
    if (!pair.code[0]) return;
    pair_leave();
    if (pair.shown) crc_code_spent(); /* one pairing per code shown, whatever its end: the page shows another */
    pair.shown = 0;
    crypto_wipe(pair.code, sizeof pair.code);
    crypto_wipe(pair.name, sizeof pair.name);
    crc_pair_hide(outcome);
}

static void new_code(char code[7]) {
    uint32_t v;
    do crc_random(&v, sizeof v); while (v >= 4294000000u);  /* 4294000000 is a multiple of 10^6: no bias */
    v %= 1000000;
    for (int i = 5; i >= 0; --i) { code[i] = '0' + v % 10; v /= 10; }
    code[6] = 0;
}

static void generator(uint8_t g[32], const char *code, const uint8_t sid[32]) {
    static const char info[] = "coda-rc pake v1";
    uint8_t h[64];
    crypto_sha512_ctx ctx;
    crypto_sha512_init(&ctx);
    crypto_sha512_update(&ctx, (const uint8_t *)info, sizeof info - 1);
    crypto_sha512_update(&ctx, (const uint8_t *)code, 6);
    crypto_sha512_update(&ctx, sid, 32);
    crypto_sha512_final(&ctx, h);
    crypto_elligator_map(g, h);
    crypto_wipe(h, sizeof h);
}

static int locked(void) {
    return pair.locked_until && (int32_t)(pair.locked_until - crc_now_ms()) > 0;
}

static int pair_start(crc_session *s, unsigned req_id, const uint8_t *p, size_t n) {
    pbr r = {p, p + n};
    pbf f;
    int k;
    s->client_name[0] = 0;
    while ((k = pb_next(&r, &f)) > 0)
        if (f.field == 1) pb_cstr(&f, s->client_name, sizeof s->client_name);
    if (k < 0) return crc_error(s, req_id, E_BAD_REQUEST, "PairStart");
    crc_peer peers[CRC_MAX_PEERS];
    if (crc_peers_load(peers, CRC_MAX_PEERS) >= CRC_MAX_PEERS)
        return crc_error(s, req_id, E_PAIRING_FULL, "forget a phone on the Q2 first");
    if (locked()) return crc_error(s, req_id, E_PAIRING_LOCKED, "too many wrong codes; try again later");
    if (pair.owner && pair.owner != s) return crc_error(s, req_id, E_BUSY, "another phone is pairing");
    const char *name = s->client_name[0] ? s->client_name : "Phone";
    int fresh = !pair.code[0] || strcmp(pair.name, name) ||
                crc_now_ms() - pair.since > CRC_PAIR_TIMEOUT_MS; /* else the same phone again: the same code */
    /* Each new code puts the pairing card up and lights the screen; a peer that asks over and over, under
     * new names, gets CRC_PAIR_SHOWS of them a CRC_PAIR_SHOW_WINDOW_MS */
    if (fresh && pair.shows_n >= CRC_PAIR_SHOWS &&
        crc_now_ms() - pair.shows[pair.shows_n % CRC_PAIR_SHOWS] < CRC_PAIR_SHOW_WINDOW_MS)
        return crc_error(s, req_id, E_BUSY, "pairing asked too often; try again in a minute");
    pair_leave();
    if (fresh) {
        pair.shows[pair.shows_n++ % CRC_PAIR_SHOWS] = crc_now_ms();
        pair_end(-1);
        pair.since = crc_now_ms();
        /* Pair a Phone's QR code carries a code, shown under it, for a phone that scans it
         * (docs/remote-protocol.md#pairing-by-qr-code); else one made now, shown in the pairing card */
        pair.shown = crc_code_shown(pair.code);
        if (!pair.shown) new_code(pair.code);
        memcpy(pair.name, name, strlen(name) + 1); /* client_name holds under CRC_NAME */
        crc_pair_show(pair.code, name);
    }
    pair.owner = s;
    generator(s->gen, pair.code, s->sid);
    crc_random(s->scalar, 32);
    crypto_x25519(s->dpoint, s->scalar, s->gen);
    s->state = S_PAIRING;
    uint8_t b[40];
    pbw w = {b, 0, sizeof b, 0};
    pb_bytes(&w, 1, s->dpoint, 32);
    return crc_send(s, T_PAIR_CHALLENGE, req_id, b, w.n);
}

static int pair_finish(crc_session *s, unsigned req_id, const uint8_t *p, size_t n) {
    pbr r = {p, p + n};
    pbf f;
    const uint8_t *cpoint = 0, *confirm = 0;
    int k;
    while ((k = pb_next(&r, &f)) > 0) {
        if (f.field == 1 && f.wire == 2 && f.n == 32) cpoint = f.s;
        if (f.field == 2 && f.wire == 2 && f.n == 32) confirm = f.s;
    }
    if (k < 0 || !cpoint || !confirm) return crc_error(s, req_id, E_BAD_REQUEST, "PairFinish");
    if (s->state != S_PAIRING || pair.owner != s) return crc_error(s, req_id, E_PAIRING_FAILED, "no pairing");
    uint8_t shared[32], prk[64], m[64], id[CRC_PEER_ID];
    crypto_x25519(shared, s->scalar, cpoint);
    int bad = zero32(shared);
    mac(prk, shared, 32, "coda-rc pair v1", s->sid, 32, cpoint, 32, s->dpoint, 32);
    mac(m, prk, 64, "client confirm", 0, 0, 0, 0, 0, 0);
    bad |= crypto_verify32(m, confirm) != 0;
    crypto_wipe(shared, 32);
    if (bad) {
        crypto_wipe(prk, 64);
        if (++pair.fails >= CRC_PAIR_FAILS) { pair.locked_until = crc_now_ms() + CRC_PAIR_LOCK_MS; pair.fails = 0; }
        pair_end(0);
        return crc_error(s, req_id, E_PAIRING_FAILED, "wrong code");
    }
    crc_peer peers[CRC_MAX_PEERS + 1];
    int np = crc_peers_load(peers, CRC_MAX_PEERS);
    crc_random(id, sizeof id);
    memset(&peers[np], 0, sizeof peers[np]);
    memcpy(peers[np].id, id, sizeof id);
    mac(m, prk, 64, "long-term key", 0, 0, 0, 0, 0, 0);
    memcpy(peers[np].ltk, m, CRC_KEY);
    memcpy(peers[np].name, s->client_name[0] ? s->client_name : "Phone", CRC_NAME);
    peers[np].name[CRC_NAME - 1] = 0;
    peers[np].created = crc_now_s();
    int saved = np < CRC_MAX_PEERS && crc_peers_save(peers, np + 1) == 0;
    crypto_wipe(peers, sizeof peers);
    if (!saved) {
        crypto_wipe(prk, 64);
        pair_end(0);
        return crc_error(s, req_id, E_INTERNAL, "could not save the pairing");
    }
    pair.fails = 0;
    memcpy(s->peer, id, sizeof id);
    memcpy(s->ltk, m, CRC_KEY);
    mac(m, prk, 64, "device confirm", id, sizeof id, 0, 0, 0, 0);
    crypto_wipe(prk, 64);
    s->state = S_PAIRED;
    pair_end(1);
    if (s->iroh_via) crc_iroh_proved(s); /* paired over iroh: the phone's key goes with its new record */
    uint8_t b[60];
    pbw w = {b, 0, sizeof b, 0};
    pb_bytes(&w, 1, id, sizeof id);
    pb_bytes(&w, 2, m, 32);
    crypto_wipe(m, 64);
    return crc_send(s, T_PAIR_RESULT, req_id, b, w.n);
}

int crc_pair_busy(void) { return pair.code[0] != 0; }

void crc_tick(void) {
    if (!pair.code[0]) return;
    crc_session *s = pair.owner;
    if (crc_pair_cancelled()) {
        pair_end(-1);
        if (s) crc_error(s, 0, E_PAIRING_CANCELLED, "canceled on the Q2");
    } else if (crc_now_ms() - pair.since > CRC_PAIR_TIMEOUT_MS) {
        pair_end(-1);
        if (s) crc_error(s, 0, E_PAIRING_FAILED, "the code expired");
    }
}

/* ---- the handshake ---- */

static int hello(crc_session *s, unsigned req_id, const uint8_t *p, size_t n) {
    pbr r = {p, p + n};
    pbf f;
    int k;
    uint64_t version = 0;
    const uint8_t *cn = 0, *peer = 0;
    while ((k = pb_next(&r, &f)) > 0) {
        if (f.field == 1 && f.wire == 0) version = f.v;
        if (f.field == 2) pb_cstr(&f, s->client_name, sizeof s->client_name);
        if (f.field == 3 && f.wire == 2 && f.n == 16) cn = f.s;
        if (f.field == 4 && f.wire == 2 && f.n == CRC_PEER_ID) peer = f.s;
    }
    if (k < 0 || !cn) return crc_error(s, req_id, E_BAD_REQUEST, "Hello"), -1;
    if (version < 1) return crc_error(s, req_id, E_VERSION, "proto_version 1 or later"), -1;
    memcpy(s->sid, cn, 16);
    crc_random(s->sid + 16, 16);
    int known = 0;
    if (peer) {
        crc_peer peers[CRC_MAX_PEERS];
        int np = crc_peers_load(peers, CRC_MAX_PEERS);
        for (int i = 0; i < np; ++i)
            if (!memcmp(peers[i].id, peer, CRC_PEER_ID)) {
                memcpy(s->peer, peer, CRC_PEER_ID);
                memcpy(s->ltk, peers[i].ltk, CRC_KEY);
                known = 1;
            }
        crypto_wipe(peers, sizeof peers);
    }
    s->state = known ? S_PAIRED : S_HELLO;
    uint8_t b[96];
    pbw w = {b, 0, sizeof b, 0};
    pb_varint(&w, 1, CRC_PROTO_VERSION);
    pb_varint(&w, 2, 1);  /* min_version */
    pb_bytes(&w, 3, s->sid + 16, 16);
    pb_varint(&w, 4, known);
    pb_varint(&w, 5, crc_caps());
    pb_str(&w, 6, "Shanling Q2");
    pb_bytes(&w, 7, crc_device_id, 8);
    return crc_send(s, T_HELLO_RESP, req_id, b, w.n);
}

static int auth(crc_session *s, unsigned req_id, const uint8_t *p, size_t n) {
    pbr r = {p, p + n};
    pbf f;
    const uint8_t *proof = 0;
    int k;
    while ((k = pb_next(&r, &f)) > 0)
        if (f.field == 1 && f.wire == 2 && f.n == 32) proof = f.s;
    if (k < 0 || !proof) return crc_error(s, req_id, E_BAD_REQUEST, "Auth"), -1;
    if (s->state != S_PAIRED) return crc_error(s, req_id, E_UNAUTHORIZED, "not paired"), -1;
    uint8_t m[64];
    mac(m, s->ltk, CRC_KEY, "coda-rc auth v1", "client", 6, s->sid, 32, 0, 0);
    if (crypto_verify32(m, proof)) {
        crypto_wipe(m, 64);
        return crc_error(s, req_id, E_UNAUTHORIZED, "wrong key"), -1;
    }
    mac(m, s->ltk, CRC_KEY, "coda-rc auth v1", "device", 6, s->sid, 32, 0, 0);
    uint8_t b[40];
    pbw w = {b, 0, sizeof b, 0};
    pb_bytes(&w, 1, m, 32);
    mac(m, s->ltk, CRC_KEY, "coda-rc session v1", s->sid, 32, 0, 0, 0, 0);
    memcpy(s->kc, m, 32);
    memcpy(s->kd, m + 32, 32);
    crypto_wipe(m, 64);
    s->rx_ctr = s->tx_ctr = 0;
    int e = crc_send(s, T_AUTH_RESP, req_id, b, w.n);  /* the last plain frame */
    s->state = S_AUTHED;
    if (s->iroh_via) crc_iroh_proved(s);
    return e;
}

/* ---- dispatch ---- */

static uint8_t plain[CRC_MAX_PAYLOAD];

static int frame(crc_session *s, const uint8_t *fr, size_t len) {
    int type = fr[2], flags = fr[3];
    unsigned req_id = fr[4] << 8 | fr[5];
    const uint8_t *p = fr + CRC_HDR;
    size_t n = len - CRC_HDR;
    if (s->state == S_AUTHED) {
        uint8_t nonce[12];
        crypto_aead_ctx ctx;
        if (flags != CRC_FLAG_SEALED || n < CRC_TAG) return -1;
        n -= CRC_TAG;
        nonce_of(nonce, s->rx_ctr++);
        crypto_aead_init_ietf(&ctx, s->kc, nonce);
        int bad = crypto_aead_read(&ctx, plain, p + n, fr, CRC_HDR, p, n);
        crypto_wipe(&ctx, sizeof ctx);
        if (bad) return -1;  /* forged, replayed or reordered: nothing more from this link is trusted */
        if (type == T_PING) return crc_send(s, T_PONG, req_id, plain, n);
        return crc_app(s, type, req_id, plain, n);
    }
    if (flags) return -1;
    /* Through q2-iroh from a key no paired phone registered: only while Pair a Phone shows its code (q2-iroh admits
     * such a peer only then too), and then only the handshake and the pairing; at any other time it is closed. */
    if (s->iroh_via && !s->iroh_ok) {
        char code[7];
        int open = crc_code_shown(code);
        crypto_wipe(code, sizeof code);
        if (!open || type == T_PING) return crc_error(s, req_id, E_UNAUTHORIZED, "not a paired phone's iroh key"), -1;
    }
    switch (type) {
    case T_HELLO: return s->state == S_NEW ? hello(s, req_id, p, n) : -1;
    case T_PAIR_START: return s->state == S_HELLO || s->state == S_PAIRING ? pair_start(s, req_id, p, n) : -1;
    case T_PAIR_FINISH: return s->state == S_PAIRING ? pair_finish(s, req_id, p, n) : -1;
    case T_AUTH: return auth(s, req_id, p, n);
    case T_PING: /* before Auth only a short echo: a peer that is not paired takes little air time */
        if (n > CRC_PING_PLAIN) return crc_error(s, req_id, E_TOO_LARGE, "Ping before Auth"), 0;
        return crc_send(s, T_PONG, req_id, p, n);
    }
    crc_error(s, req_id, s->state == S_NEW ? E_BAD_REQUEST : E_UNAUTHORIZED, "authenticate first");
    return 0;
}

void crc_open(crc_session *s, void *io, int transport) {
    memset(s, 0, sizeof *s);
    s->io = io;
    s->transport = transport;
}

int crc_feed(crc_session *s, const uint8_t *p, size_t n) {
    while (n) {
        size_t take;
        if (s->rx_n < 2) {
            take = 2 - s->rx_n;
        } else {
            size_t len = s->rx[0] << 8 | s->rx[1];
            if (len < 4 || 2 + len > CRC_MAX_FRAME) return -1;
            take = 2 + len - s->rx_n;
        }
        if (take > n) take = n;
        memcpy(s->rx + s->rx_n, p, take);
        s->rx_n += take;
        p += take;
        n -= take;
        if (s->rx_n == 2) {  /* a length out of range closes at once, not when its body would have come */
            size_t len = s->rx[0] << 8 | s->rx[1];
            if (len < 4 || 2 + len > CRC_MAX_FRAME) return -1;
        }
        if (s->rx_n >= 2 && s->rx_n == 2u + (s->rx[0] << 8 | s->rx[1])) {
            size_t len = s->rx_n;
            s->rx_n = 0;
            if (len < CRC_HDR || frame(s, s->rx, len) < 0) return -1;
            if (s->state == S_CLOSED) return -1;
        }
    }
    return 0;
}

void crc_close(crc_session *s) {
    if (pair.owner == s) pair_leave(); /* the code stays up for the phone's next session */
    s->state = S_CLOSED;
    crypto_wipe(s->ltk, sizeof s->ltk);
    crypto_wipe(s->kc, sizeof s->kc);
    crypto_wipe(s->kd, sizeof s->kd);
    crypto_wipe(s->scalar, sizeof s->scalar);
}

uint32_t crc_buffered_ms(uint32_t have, uint32_t total, uint32_t ms) {
    if (!total || !ms) return 0;
    if (have >= total) return ms;
    while (total > 65535) total >>= 1, have >>= 1; /* so (ms % total) * have stays within 32 bits */
    if (!total) return 0;
    return ms / total * have + ms % total * have / total;
}
