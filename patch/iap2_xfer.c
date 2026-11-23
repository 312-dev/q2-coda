/* iAP2's file transfers, on the file transfer session (PodBox, docs/iphone-mode.md#the-car-s-cover): the song's
 * attribute 26 names a transfer; the Q2 sets it up as [ID, 04, an 8-byte size, 00 02 (artwork)]; the car answers
 * [ID, 01] to go; the JPEG follows as [ID, op, bytes], op C0 the only piece, else 80 the first, 00 the middle ones and
 * 40 the last; [ID, 02] cancels either way. An empty one (size 0) when there is no cover: PodBox's car waits
 * otherwise. Up Next's list (ql_announce) and the library's lists (lists_pump) go the same way, in the pieces, ids and
 * 8-byte song ids here. Part of the PodBox port (iap2.h). */
#include "q2_iap_sys.h"
#include "iap2.h"
#include "iap2_link.h"
#include "iap2_xfer.h"
#include "iap2_library.h"

#define ART_MAX 65536
static u8 art[ART_MAX];
static unsigned art_len;
unsigned art_sent;
u8 art_id, next_transfer = 0x80;
int art_going;

/* Up Next's list on its way (ql_announce) */
u8 ql[QLIST_MAX * 8], ql_id;
unsigned ql_len, ql_going;
long ql_seen_at;

void file_send(u8 id, u8 op, const u8 *d, unsigned n) {
    u8 b[MAX_PACKET];
    if (!file_session || n + 2 > sizeof b) return;
    b[0] = id, b[1] = op;
    if (n) memcpy(b + 2, d, n);
    enqueue(file_session, b, n + 2);
}

/* A file transfer's pieces: the bytes a piece holds (the car's packet or BULK_MAX, less the header, its checksum and
 * the piece's id and op; MIN_PEER_MAX keeps it above 100), each piece's op (C0 the only, 80 the first, 00 one in the
 * middle, 40 the last), a new transfer's id (80 to FF, round), and a song's 8-byte id */
unsigned piece_room(void) { return (peer_max < BULK_MAX ? peer_max : BULK_MAX) - HEADER_LEN - 1 - 2; }
u8 piece_op(int first, int last) { return first && last ? 0xC0 : first ? 0x80 : last ? 0x40 : 0x00; }
static void file_pieces(u8 id, const u8 *d, unsigned len, unsigned *sent, unsigned room) {
    while (*sent < len) {
        unsigned k = len - *sent < room ? len - *sent : room;
        file_send(id, piece_op(*sent == 0, *sent + k == len), d + *sent, k);
        *sent += k;
    }
}
u8 transfer_take(void) {
    u8 id = next_transfer;
    next_transfer = id == 0xFF ? 0x80 : id + 1;
    return id;
}
void put_id8(u8 *p, u32 hi, u32 lo) {
    for (int b = 0; b < 4; ++b) p[b] = (u8)(hi >> (24 - 8 * b)), p[4 + b] = (u8)(lo >> (24 - 8 * b));
}

/* A baseline JPEG as PodBox's car needed it: each quantisation and Huffman table in a segment of its own, the bytes
 * otherwise as they are. 0 for anything else (a progressive JPEG, a PNG, a broken file). */
static unsigned jpeg_tables_apart(const u8 *in, unsigned n, u8 *out, unsigned cap) {
    unsigned i = 2, o = 2, baseline = 0;
    if (n < 4 || in[0] != 0xFF || in[1] != 0xD8 || cap < 2) return 0;
    out[0] = 0xFF, out[1] = 0xD8;
    while (i + 4 <= n) {
        if (in[i] != 0xFF) return 0;
        u8 mk = in[i + 1];
        unsigned seg = in[i + 2] << 8 | in[i + 3];
        if (seg < 2 || i + 2 + seg > n) return 0;
        if (mk == 0xC0 || mk == 0xC1) baseline = 1;
        if (mk == 0xC2) return 0;
        if (mk == 0xDB || mk == 0xC4) {
            unsigned at = i + 4, end = i + 2 + seg;
            while (at < end) {
                unsigned t = mk == 0xDB ? 1 + ((in[at] >> 4) ? 128 : 64) : 0;
                if (mk == 0xC4) {
                    if (at + 17 > end) return 0;
                    t = 17;
                    for (unsigned k = 1; k <= 16; ++k) t += in[at + k];
                }
                if (at + t > end || o + 4 + t > cap) return 0;
                out[o++] = 0xFF, out[o++] = mk, out[o++] = (t + 2) >> 8, out[o++] = t + 2;
                memcpy(out + o, in + at, t), o += t, at += t;
            }
        } else {
            if (o + 2 + seg > cap) return 0;
            memcpy(out + o, in + i, 2 + seg), o += 2 + seg;
        }
        i += 2 + seg;
        if (mk == 0xDA) { /* the scan, to the end */
            if (o + (n - i) > cap) return 0;
            memcpy(out + o, in + i, n - i), o += n - i;
            return baseline ? o : 0;
        }
    }
    return 0;
}

/* A new song's transfer, the last cancelled if it still went; its setup goes after the message naming it. */
u8 art_start(const struct iap2_np *n) {
    static u8 raw[ART_MAX];
    char t[120];
    if (!file_session) return 0;
    if (art_going && art_id) file_send(art_id, 0x02, 0, 0);
    art_id = transfer_take();
    /* a stream has no cover, but is named one, empty: with none named the car keeps the last song's */
    unsigned got = H->artwork && !n->live ? H->artwork(n, raw, sizeof raw) : 0;
    art_len = got ? jpeg_tables_apart(raw, got, art, sizeof art) : 0;
    art_sent = 0, art_going = 1;
    snprintf(t, sizeof t, "art: transfer %02x, %u bytes (%u as found)", art_id, art_len, got);
    H->log(t);
    return art_id;
}

void art_setup(void) {
    u8 setup[10] = { 0, 0, 0, 0, art_len >> 24, art_len >> 16, art_len >> 8, art_len, 0, 2 };
    file_send(art_id, 0x04, setup, sizeof setup);
}

/* The car's answers on the file transfer session. */
void iap2_file(const u8 *d, unsigned n) {
    char t[96];
    if (n < 2) return;
    snprintf(t, sizeof t, "file < transfer %02x op %02x (%u bytes)", d[0], d[1], n);
    H->log(t);
    if (list_transfer_word(d)) return;
    if (d[0] == ql_id && ql_going && d[1] == 0x01) { /* the queue list: every piece */
        unsigned sent = 0;
        file_pieces(ql_id, ql, ql_len, &sent, piece_room() - piece_room() % 8);
        return;
    }
    if (d[0] == ql_id && (d[1] == 0x02 || d[1] == 0x05)) {
        ql_going = 0;
        return;
    }
    if (d[0] != art_id || !art_going) return;
    if (d[1] == 0x01) { /* go: every piece, as the window allows */
        if (!art_len) file_send(art_id, 0xC0, 0, 0);
        file_pieces(art_id, art, art_len, &art_sent, piece_room());
    } else if (d[1] == 0x02 || d[1] == 0x05) {
        art_going = 0;
        H->log(d[1] == 0x05 ? "art: the car took it" : "art: the car cancelled it");
    }
}
