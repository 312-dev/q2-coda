/* AVRCP browsing for the rebuilt bluetoothd (docs/bluetoothd.md#browsing): a car's or a headset's GetFolderItems,
 * ChangePath, GetItemAttributes, GetTotalNumberOfItems, Search, SetBrowsedPlayer and PlayItem, answered from the
 * library through coda_rc_lib.c's Browse, Search, GetQueue and Play, so a car lists what a phone's Car Remote
 * does: the same lists, orders, letter buckets and, with Local + Server on, server songs. The library is paged
 * from SQLite a request at a time; nothing of it is kept here but the path a car has walked to (an id per level).
 *
 * The player is database unaware (AVRCP 1.6 6.10.3): the UID counter is always 0 and an item's UID is its place in
 * the folder it was listed in, plus one, so a UID means the same item until the car changes folder. A car that
 * asks for a UID of another folder gets what is at that place in this one, as on any database-unaware player.
 *
 * bluetoothd sends each PDU over coda-rc's AVRCP socket (coda_rc.c avrcp_main) as {pdu, tag u32, room u16, its
 * parameters}, tag naming the car's connection and room the most its reply may hold, and gets back {pdu, the
 * reply's parameters}. Everything is big-endian, as AVRCP is. */
#include "coda_rc_sys.h"
#include "coda_rc.h"

#define PLAYER_ID 1
#define CHARSET_UTF8 0x6a
#define DEPTH_MAX 12
#define CARS 4
#define ROOT "library"

enum {
    ST_INVALID_COMMAND = 0, ST_INVALID_PARAM, ST_INTERNAL = 3, ST_SUCCESS, ST_INVALID_DIRECTION = 7,
    ST_NOT_A_DIRECTORY, ST_DOES_NOT_EXIST, ST_INVALID_SCOPE, ST_OUT_OF_BOUNDS, ST_NOT_PLAYABLE, ST_MEDIA_IN_USE,
    ST_NO_RESULTS = 0x14, ST_INVALID_PLAYER = 0x11,
};
enum { SCOPE_PLAYERS, SCOPE_VFS, SCOPE_SEARCH, SCOPE_NOW_PLAYING };
enum { ATTR_TITLE = 1, ATTR_ARTIST, ATTR_ALBUM, ATTR_DURATION = 7 };
#define ATTRS (1u << ATTR_TITLE | 1u << ATTR_ARTIST | 1u << ATTR_ALBUM | 1u << ATTR_DURATION)

/* A car: the ids of the folders it has walked into, from the root's children, and its last search */
typedef struct {
    uint32_t tag, used_at;
    int depth;
    char path[DEPTH_MAX][420];
    char query[128];
} car_t;
static car_t cars[CARS];
static uint32_t clock_;

/* ---- coda_rc_lib.c's answers, caught from crc_send (coda_rc.c crc_write) ---- */

static crc_session lib_s;
static uint8_t got[CRC_MAX_PAYLOAD];
static size_t got_n;
static int got_type;

void avrcp_capture(const uint8_t *frame, size_t n) {
    if (n < CRC_HDR || n - CRC_HDR > sizeof got) return;
    got_type = frame[2];
    got_n = n - CRC_HDR;
    memcpy(got, frame + CRC_HDR, got_n);
}

static int ask(int type, const uint8_t *req, size_t n) {
    int qgen = 0, qpos = 0;
    crc_avrcp_now(&qgen, &qpos);
    if (!lib_s.transport) crc_open(&lib_s, 0, CRC_TR_AVRCP);
    got_type = -1;
    lib_request(&lib_s, type, 1, req, n, qgen, qpos);
    return got_type;
}

/* one item of a Browse, Search or GetQueue answer; a song's artist, album and length from its row (tag) */
typedef struct {
    char id[480], title[256], artist[160], album[160];
    int browsable;
    unsigned ms;
} item_t;

static void read_item(const pbf *outer, item_t *it) {
    memset(it, 0, sizeof *it);
    pbr r = {outer->s, outer->s + outer->n};
    pbf f;
    while (pb_next(&r, &f) > 0) {
        if (f.field == 1) pb_cstr(&f, it->id, sizeof it->id);
        if (f.field == 2) pb_cstr(&f, it->title, sizeof it->title);
        if (f.field == 6 && f.wire == 0) it->browsable = f.v != 0;
        if (f.field == 9 && f.wire == 0) it->ms = (unsigned)f.v;
    }
}

/* the songs' tags: an item's subtitle is its artist in one list and its length in another (coda_rc_lib.c
 * put_rows), so they are read from the library */
static void tag(item_t *items, int n) {
    const char *ids[16];
    static lib_meta meta[16];
    if (n <= 0 || n > 16) return;
    for (int i = 0; i < n; ++i) ids[i] = items[i].id;
    if (lib_songs_meta(ids, n, meta)) return;
    for (int i = 0; i < n; ++i) {
        if (!meta[i].found) continue;
        snprintf(items[i].artist, sizeof items[i].artist, "%s", meta[i].artist);
        snprintf(items[i].album, sizeof items[i].album, "%s", meta[i].album);
        if (!items[i].ms && meta[i].secs > 0) items[i].ms = meta[i].secs * 1000u;
    }
}

/* Browse node from offset: its total, and up to max items. -1 when the node names nothing. */
static int browse(const char *node, unsigned offset, unsigned max, item_t *items, int *n) {
    uint8_t b[512];
    pbw w = {b, 0, sizeof b, 0};
    pb_str(&w, 1, node);
    pb_varint(&w, 3, offset);
    pb_varint(&w, 4, max < 1 ? 1 : max > 100 ? 100 : max);
    *n = 0;
    if (ask(T_BROWSE, b, w.n) != T_BROWSE_RESP) return -1;
    pbr r = {got, got + got_n};
    pbf f;
    int total = 0;
    while (pb_next(&r, &f) > 0) {
        if (f.field == 3 && f.wire == 0) total = (int)f.v;
        if (f.field == 5 && f.wire == 2 && (unsigned)*n < max) read_item(&f, &items[(*n)++]);
    }
    tag(items, *n);
    return total;
}

/* The car's last search, flattened: Tracks, then Albums, then Artists, as coda_rc_lib.c groups them */
static int search(const char *q, unsigned offset, unsigned max, item_t *items, int *n) {
    uint8_t b[160];
    pbw w = {b, 0, sizeof b, 0};
    pb_str(&w, 1, q);
    pb_varint(&w, 2, 50);
    *n = 0;
    if (!*q || ask(T_SEARCH, b, w.n) != T_SEARCH_RESP) return 0;
    pbr r = {got, got + got_n};
    pbf g;
    int at = 0;
    while (pb_next(&r, &g) > 0) {
        if (g.field != 1 || g.wire != 2) continue;
        pbr gr = {g.s, g.s + g.n};
        pbf f;
        while (pb_next(&gr, &f) > 0) {
            if (f.field != 2 || f.wire != 2) continue;
            if ((unsigned)at >= offset && (unsigned)*n < max) read_item(&f, &items[(*n)++]);
            ++at;
        }
    }
    tag(items, *n);
    return at;
}

/* The queue from offset, and its total */
static int queue(unsigned offset, unsigned max, item_t *items, int *n) {
    uint8_t b[32];
    pbw w = {b, 0, sizeof b, 0};
    pb_varint(&w, 1, offset);
    pb_varint(&w, 2, max < 1 ? 1 : max > 100 ? 100 : max);
    *n = 0;
    if (ask(T_GET_QUEUE, b, w.n) != T_QUEUE_RESP) return 0;
    pbr r = {got, got + got_n};
    pbf f;
    int total = 0;
    while (pb_next(&r, &f) > 0) {
        if (f.field == 3 && f.wire == 0) total = (int)f.v;
        if (f.field == 5 && f.wire == 2 && (unsigned)*n < max) read_item(&f, &items[(*n)++]);
    }
    tag(items, *n);
    return total;
}

/* ---- the reply ---- */

typedef struct {
    uint8_t *p;
    size_t n, cap;
} out_t;

static void put(out_t *o, const void *s, size_t n) {
    if (o->n + n <= o->cap) memcpy(o->p + o->n, s, n);
    o->n += n;
}
static void u8(out_t *o, unsigned v) { uint8_t b = v; put(o, &b, 1); }
static void u16(out_t *o, unsigned v) { u8(o, v >> 8), u8(o, v); }
static void u32(out_t *o, uint32_t v) { u16(o, v >> 16), u16(o, v); }
static void u64(out_t *o, uint64_t v) { u32(o, v >> 32), u32(o, (uint32_t)v); }
static void text(out_t *o, const char *s) {
    size_t n = strlen(s);
    u16(o, n);
    put(o, s, n);
}
static unsigned be16(const uint8_t *b) { return b[0] << 8 | b[1]; }
static uint32_t be32(const uint8_t *b) { return (uint32_t)be16(b) << 16 | be16(b + 2); }
static uint64_t be64(const uint8_t *b) { return (uint64_t)be32(b) << 32 | be32(b + 4); }

static size_t status_only(out_t *o, int status) {
    o->n = 0;
    u8(o, status);
    return o->n;
}

/* which of title, artist, album and length a request's attribute list asks for: count 0 all, 0xff none */
static unsigned wanted(const uint8_t *ids, unsigned count) {
    if (count == 0) return ATTRS;
    unsigned m = 0;
    for (unsigned i = 0; count != 0xff && i < count; ++i) {
        uint32_t a = be32(ids + 4 * i);
        if (a < 32 && ATTRS & 1u << a) m |= 1u << a;
    }
    return m;
}

/* the attributes an item has of those wanted: written (o) or only counted (o null) */
static unsigned attrs(out_t *o, const item_t *it, unsigned want) {
    char ms[16];
    snprintf(ms, sizeof ms, "%u", it->ms);
    const char *v[8] = {0, it->title, it->artist, it->album, 0, 0, 0, it->ms ? ms : ""};
    unsigned n = 0;
    for (unsigned a = 1; a < 8; ++a) {
        if (!(want & 1u << a) || !v[a] || (a != ATTR_TITLE && !*v[a])) continue;
        ++n;
        if (o) u32(o, a), u16(o, CHARSET_UTF8), text(o, v[a]);
    }
    return n;
}

/* AVRCP's folder types: by what the folder lists (6.10.2.2) */
static int folder_type(const char *id) {
    static const struct { const char *prefix; int type; } types[] = {
        {"tracks", 1}, {"artist-tracks:", 1}, {"genre-tracks:", 1}, {"album:", 1}, {"pl:", 1}, {"albums", 2},
        {"artist:", 2}, {"genre:", 2}, {"year:", 2}, {"artists", 3}, {"genres", 4}, {"playlists", 5}, {"years", 6}};
    if (strchr(id, '|')) return 0; /* a letter bucket */
    for (unsigned i = 0; i < sizeof types / sizeof *types; ++i)
        if (!strncmp(id, types[i].prefix, strlen(types[i].prefix))) return types[i].type;
    return 0;
}

/* a folder PlayItem plays: a list of songs (coda_rc_lib.c play), not a list of lists */
static int folder_playable(const char *id) {
    static const char *const lists[] = {"tracks", "album:", "artist:", "genre:", "pl:", "year:", "artist-tracks:",
                                        "genre-tracks:"};
    for (unsigned i = 0; i < sizeof lists / sizeof *lists; ++i)
        if (!strncmp(id, lists[i], strlen(lists[i]))) return !strchr(id, '|') || !strncmp(id, "tracks|", 7);
    return 0;
}

/* Item i (from 0) of a folder listing, as a folder item or a media element item (6.10.2.2, 6.10.2.3) */
static int put_item(out_t *o, unsigned i, const item_t *it, unsigned want) {
    size_t mark = o->n;
    int folder = it->browsable;
    u8(o, folder ? 2 : 3);
    size_t len_at = o->n;
    u16(o, 0);
    u64(o, i + 1);
    if (folder) {
        u8(o, folder_type(it->id));
        u8(o, folder_playable(it->id));
        u16(o, CHARSET_UTF8);
        text(o, it->title);
    } else {
        u8(o, 0); /* audio */
        u16(o, CHARSET_UTF8);
        text(o, it->title);
        u8(o, attrs(0, it, want));
        attrs(o, it, want);
    }
    if (o->n > o->cap) {
        o->n = mark;
        return 0;
    }
    size_t len = o->n - len_at - 2;
    o->p[len_at] = len >> 8;
    o->p[len_at + 1] = len;
    return 1;
}

static void put_player(out_t *o, const char *name) {
    /* the passthrough operations stock's AVCTP answers (Volume Up and Down, Play, Stop, Pause, Rewind, Fast
     * Forward, Forward, Backward), then Advanced Control, Browsing, Searching, NowPlaying and NumberOfItems
     * (AVRCP 1.6 table 6.11: bit n is octet n / 8, bit n % 8) */
    static const uint8_t bits[] = {37, 38, 40, 41, 42, 44, 45, 47, 48, 58, 59, 60, 65, 67};
    uint8_t features[16] = {0};
    for (unsigned i = 0; i < sizeof bits; ++i) features[bits[i] >> 3] |= 1 << (bits[i] & 7);
    u8(o, 1);
    u16(o, 2 + 1 + 4 + 1 + 16 + 2 + 2 + strlen(name));
    u16(o, PLAYER_ID);
    u8(o, 1);  /* audio */
    u32(o, 0); /* no subtype */
    int state = crc_avrcp_state();
    u8(o, state == 1 ? 1 : state == 2 ? 2 : 0); /* playing, paused or stopped */
    put(o, features, 16);
    u16(o, CHARSET_UTF8);
    text(o, name);
}

/* ---- the PDUs ---- */

static car_t *car_of(uint32_t tag) {
    car_t *c = 0, *oldest = &cars[0];
    for (int i = 0; i < CARS; ++i) {
        if (cars[i].used_at && cars[i].tag == tag) c = &cars[i];
        if (cars[i].used_at < oldest->used_at) oldest = &cars[i];
    }
    if (!c) {
        c = oldest;
        memset(c, 0, sizeof *c);
        c->tag = tag;
    }
    c->used_at = ++clock_;
    return c;
}

static const char *here(const car_t *c) { return c->depth ? c->path[c->depth - 1] : ROOT; }

/* item (from 0) of the car's folder in scope */
static int item_at(car_t *c, int scope, unsigned i, item_t *it) {
    int n = 0;
    if (scope == SCOPE_VFS) browse(here(c), i, 1, it, &n);
    else if (scope == SCOPE_SEARCH) search(c->query, i, 1, it, &n);
    else if (scope == SCOPE_NOW_PLAYING) queue(i, 1, it, &n);
    return n == 1 && (*it->id || scope == SCOPE_NOW_PLAYING);
}

static size_t set_browsed_player(car_t *c, const uint8_t *p, size_t n, out_t *o) {
    if (n < 2) return status_only(o, ST_INVALID_PARAM);
    if (be16(p) != PLAYER_ID) return status_only(o, ST_INVALID_PLAYER);
    c->depth = 0;
    item_t it;
    int k, total = browse(ROOT, 0, 1, &it, &k);
    if (total < 0) return status_only(o, ST_INTERNAL);
    u8(o, ST_SUCCESS);
    u16(o, 0);
    u32(o, total);
    u16(o, CHARSET_UTF8);
    u8(o, 0); /* the root: no folder names */
    return o->n;
}

static size_t get_folder_items(car_t *c, const uint8_t *p, size_t n, out_t *o) {
    if (n < 10 || n < 10 + 4u * (p[9] == 0xff ? 0 : p[9])) return status_only(o, ST_INVALID_PARAM);
    int scope = p[0];
    uint32_t start = be32(p + 1), end = be32(p + 5);
    unsigned want = wanted(p + 10, p[9]);
    if (end < start) return status_only(o, ST_OUT_OF_BOUNDS);
    u8(o, ST_SUCCESS);
    u16(o, 0);
    size_t count_at = o->n;
    u16(o, 0);
    unsigned count = 0;
    if (scope == SCOPE_PLAYERS) {
        if (start > 0) return status_only(o, ST_OUT_OF_BOUNDS);
        put_player(o, "Q2");
        count = 1;
    } else if (scope >= SCOPE_VFS && scope <= SCOPE_NOW_PLAYING) {
        static item_t items[16];
        unsigned max = end - start + 1 < 16 ? end - start + 1 : 16;
        int k = 0, total = scope == SCOPE_VFS      ? browse(here(c), start, max, items, &k)
                           : scope == SCOPE_SEARCH ? search(c->query, start, max, items, &k)
                                                   : queue(start, max, items, &k);
        if (total < 0) return status_only(o, ST_INTERNAL);
        if (start >= (unsigned)total) return status_only(o, ST_OUT_OF_BOUNDS);
        for (int i = 0; i < k && put_item(o, start + i, &items[i], want); ++i) ++count;
        if (!count) return status_only(o, ST_INTERNAL); /* not even one fits the car's room */
    } else {
        return status_only(o, ST_INVALID_SCOPE);
    }
    o->p[count_at] = count >> 8;
    o->p[count_at + 1] = count;
    return o->n;
}

static size_t change_path(car_t *c, const uint8_t *p, size_t n, out_t *o) {
    if (n < 11) return status_only(o, ST_INVALID_PARAM);
    item_t it;
    int k;
    if (p[2] == 0) {
        if (!c->depth) return status_only(o, ST_INVALID_DIRECTION);
        --c->depth;
    } else if (p[2] == 1) {
        uint64_t uid = be64(p + 3);
        if (!uid || uid > 0x7fffffff || !item_at(c, SCOPE_VFS, (unsigned)uid - 1, &it))
            return status_only(o, ST_DOES_NOT_EXIST);
        if (!it.browsable) return status_only(o, ST_NOT_A_DIRECTORY);
        if (c->depth == DEPTH_MAX) return status_only(o, ST_INTERNAL);
        snprintf(c->path[c->depth++], sizeof c->path[0], "%s", it.id);
    } else {
        return status_only(o, ST_INVALID_DIRECTION);
    }
    int total = browse(here(c), 0, 1, &it, &k);
    u8(o, ST_SUCCESS);
    u32(o, total < 0 ? 0 : total);
    return o->n;
}

static size_t get_item_attributes(car_t *c, const uint8_t *p, size_t n, out_t *o) {
    if (n < 12 || n < 12 + 4u * p[11]) return status_only(o, ST_INVALID_PARAM);
    uint64_t uid = be64(p + 1);
    item_t it;
    if (p[0] < SCOPE_VFS || p[0] > SCOPE_NOW_PLAYING) return status_only(o, ST_INVALID_SCOPE);
    if (!uid || uid > 0x7fffffff || !item_at(c, p[0], (unsigned)uid - 1, &it)) return status_only(o, ST_DOES_NOT_EXIST);
    unsigned want = wanted(p + 12, p[11]);
    u8(o, ST_SUCCESS);
    u8(o, attrs(0, &it, want));
    attrs(o, &it, want);
    return o->n > o->cap ? status_only(o, ST_INTERNAL) : o->n;
}

static size_t get_total(car_t *c, const uint8_t *p, size_t n, out_t *o) {
    if (n < 1) return status_only(o, ST_INVALID_PARAM);
    item_t it;
    int k, total = p[0] == SCOPE_PLAYERS        ? 1
                   : p[0] == SCOPE_VFS          ? browse(here(c), 0, 1, &it, &k)
                   : p[0] == SCOPE_SEARCH       ? search(c->query, 0, 0, &it, &k)
                   : p[0] == SCOPE_NOW_PLAYING ? queue(0, 1, &it, &k)
                                                : -2;
    if (total == -2) return status_only(o, ST_INVALID_SCOPE);
    if (total < 0) return status_only(o, ST_INTERNAL);
    u8(o, ST_SUCCESS);
    u16(o, 0);
    u32(o, total);
    return o->n;
}

static size_t search_pdu(car_t *c, const uint8_t *p, size_t n, out_t *o) {
    if (n < 4 || n < 4u + be16(p + 2)) return status_only(o, ST_INVALID_PARAM);
    if (be16(p) != CHARSET_UTF8) return status_only(o, ST_INVALID_PARAM);
    unsigned len = be16(p + 2);
    if (len >= sizeof c->query) len = sizeof c->query - 1;
    memcpy(c->query, p + 4, len);
    c->query[len] = 0;
    item_t it;
    int k, total = search(c->query, 0, 0, &it, &k);
    if (!total) return status_only(o, ST_NO_RESULTS);
    u8(o, ST_SUCCESS);
    u16(o, 0);
    u32(o, total);
    return o->n;
}

/* Play the item: a song from its place in the folder it is in, a folder of songs from its start, a song of the
 * queue by its place (coda_rc.c queue_item's jump); a search's song alone. */
static size_t play_item(car_t *c, const uint8_t *p, size_t n, out_t *o) {
    if (n < 11) return status_only(o, ST_INVALID_PARAM);
    int scope = p[0];
    uint64_t uid = be64(p + 1);
    item_t it;
    if (scope < SCOPE_VFS || scope > SCOPE_NOW_PLAYING) return status_only(o, ST_INVALID_SCOPE);
    if (!uid || uid > 0x7fffffff || !item_at(c, scope, (unsigned)uid - 1, &it)) return status_only(o, ST_DOES_NOT_EXIST);
    char verb[48];
    uint8_t b[1024];
    pbw w = {b, 0, sizeof b, 0};
    if (scope == SCOPE_NOW_PLAYING) {
        int rid = lib_song_rid(it.id);
        if (!rid) return status_only(o, ST_DOES_NOT_EXIST);
        snprintf(verb, sizeof verb, "jump %u %d", (unsigned)uid - 1, rid);
        got_type = -1;
        crc_shim_command(&lib_s, 1, verb);
        return status_only(o, got_type == T_ERROR ? ST_MEDIA_IN_USE : ST_SUCCESS);
    }
    if (it.browsable) {
        if (!folder_playable(it.id)) return status_only(o, ST_NOT_PLAYABLE);
        pb_str(&w, 1, it.id);
    } else if (scope == SCOPE_SEARCH) {
        pb_str(&w, 1, it.id);
    } else {
        pb_str(&w, 1, here(c));
        pb_str(&w, 3, it.id);
    }
    int type = ask(T_PLAY, b, w.n);
    return status_only(o, type == T_ERROR ? ST_MEDIA_IN_USE : ST_SUCCESS);
}

size_t avrcp_request(const uint8_t *req, size_t n, uint8_t *rsp, size_t cap) {
    if (n < 7 || cap < 2) return 0;
    unsigned room = be16(req + 5);
    out_t o = {rsp + 1, 0, (room < cap - 1 ? room : cap - 1)};
    car_t *c = car_of(be32(req + 1));
    const uint8_t *p = req + 7;
    size_t pn = n - 7, len;
    rsp[0] = req[0];
    switch (req[0]) {
    case 0x70: len = set_browsed_player(c, p, pn, &o); break;
    case 0x71: len = get_folder_items(c, p, pn, &o); break;
    case 0x72: len = change_path(c, p, pn, &o); break;
    case 0x73: len = get_item_attributes(c, p, pn, &o); break;
    case 0x74: len = play_item(c, p, pn, &o); break;
    case 0x75: len = get_total(c, p, pn, &o); break;
    case 0x80: len = search_pdu(c, p, pn, &o); break;
    default: len = status_only(&o, ST_INVALID_COMMAND); break;
    }
    return 1 + (len > o.cap ? status_only(&o, ST_INTERNAL) : len);
}

/* bluetoothd forgets a car: its path goes with it */
void avrcp_forget(uint32_t tag) {
    for (int i = 0; i < CARS; ++i)
        if (cars[i].used_at && cars[i].tag == tag) memset(&cars[i], 0, sizeof cars[i]);
}
