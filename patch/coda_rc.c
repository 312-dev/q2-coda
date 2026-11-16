/* coda-rc, the car remote daemon (docs/remote-protocol.md, docs/internals.md#car-remote): the platform under
 * coda_rc_core.c. It listens on TCP 7720 and, through BlueZ, as a BLE GATT peripheral (coda_rc_ble.c), serves
 * the library from SQLite (coda_rc_lib.c), and talks to demo only through files in /tmp that the payload's
 * shim (patch/coda_rc_shim.c) polls: Now Playing out; play, transport, seek, volume, mode, rate and queue
 * commands and the pairing code in.
 *
 * demo starts it when Settings > Car Remote is on and kills it when that goes off. The rebuilt bluetoothd
 * starts a second, coda-rc --avrcp, which serves AVRCP browsing (coda_rc_avrcp.c) on a UNIX socket and nothing
 * else. Each runs at nice 19. --root DIR prefixes every path (the host tests); --no-ble and --port N are for them too. */
#include "coda_rc_sys.h"
#include "coda_rc.h"
#include "monocypher/monocypher.h"

#define MAX_TCP 4
#define HELLO_MS 10000
#define AUTH_MS 150000
#define ACK_MS 4000
#define POLL_MS 250
#define LOG_MAX 65536

enum { TR_TCP, TR_BLE };

static char root[160];
static int port = CRC_TCP_PORT, use_ble = 1, quit, rnd = -1, avrcp;
uint8_t crc_device_id[8];

typedef struct {
    int seq, state, pos_ms, len_s, id, shuffle, repeat, volume, qpos, qlen, qgen;
    int hearts; /* 0-5, -1 when the song can't be rated */
    char path[512], title[160], artist[160], album[160];
} np_t;

typedef struct {
    int fd, used;
    uint32_t opened;
    crc_session s;
} conn;
static conn tcp[MAX_TCP];
static conn ble;

/* ---- paths, files and the log ---- */

const char *crc_path(char *buf, size_t cap, const char *path) {
    snprintf(buf, cap, "%s%s", root, path);
    return buf;
}

static int read_file(const char *path, void *buf, size_t cap) {
    char p[256];
    int fd = open(crc_path(p, sizeof p, path), O_RDONLY | O_CLOEXEC), n = 0;
    if (fd < 0) return -1;
    while ((size_t)n < cap) {
        int r = (int)read(fd, (char *)buf + n, cap - n);
        if (r <= 0) break;
        n += r;
    }
    close(fd);
    return n;
}

/* path replaced whole: a temporary beside it, synced, renamed over it */
int crc_write_file(const char *path, const void *buf, size_t n, unsigned mode) {
    char p[256], t[264];
    crc_path(p, sizeof p, path);
    snprintf(t, sizeof t, "%s.new", p);
    int fd = open(t, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, mode), ok = fd >= 0;
    if (ok) {
        ok = write(fd, buf, n) == (ssize_t)n;
        ok &= fsync(fd) == 0;
        ok &= close(fd) == 0;
    }
    if (ok) ok = rename(t, p) == 0;
    if (!ok) unlink(t);
    return ok ? 0 : -1;
}

static void unlink_at(const char *path) {
    char p[256];
    unlink(crc_path(p, sizeof p, path));
}

void crc_log(const char *fmt, ...) {
    char p[256], b[300];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof b - 2) n = sizeof b - 2;
    b[n++] = '\n';
    int fd = open(crc_path(p, sizeof p, "/tmp/coda-rc.log"), O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (fd < 0) return;
    if (lseek(fd, 0, 2) > LOG_MAX) (void)!ftruncate(fd, 0);
    (void)!write(fd, b, n);
    close(fd);
}

/* ---- the platform functions coda_rc.h asks for ---- */

void crc_random(void *buf, size_t n) {
    char p[256];
    if (rnd < 0) rnd = open(crc_path(p, sizeof p, "/dev/urandom"), O_RDONLY | O_CLOEXEC);
    if (rnd < 0) rnd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    size_t got = 0;
    while (rnd >= 0 && got < n) {
        ssize_t r = read(rnd, (char *)buf + got, n - got);
        if (r <= 0) break;
        got += r;
    }
    if (got < n) {  /* no randomness, no keys: nothing this daemon does is safe without it */
        crc_log("no /dev/urandom; exiting");
        exit(2);
    }
}

uint32_t crc_now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint32_t)(t.tv_sec * 1000u + t.tv_nsec / 1000000);
}

uint32_t crc_now_s(void) { return (uint32_t)time(0); }

#define PEERS "/mnt/data/coda-rc/peers"
#define PEER_REC 76

int crc_peers_load(crc_peer *peers, int cap) {
    uint8_t b[CRC_MAX_PEERS * PEER_REC];
    int n = read_file(PEERS, b, sizeof b);
    if (n <= 0) return 0;
    n /= PEER_REC;
    if (n > cap) n = cap;
    for (int i = 0; i < n; ++i) {
        const uint8_t *r = b + i * PEER_REC;
        memcpy(peers[i].id, r, 8);
        memcpy(peers[i].ltk, r + 8, 32);
        memcpy(peers[i].name, r + 40, 32);
        peers[i].name[CRC_NAME - 1] = 0;
        peers[i].created = r[72] | r[73] << 8 | r[74] << 16 | (uint32_t)r[75] << 24;
    }
    crypto_wipe(b, sizeof b);
    return n;
}

int crc_peers_save(const crc_peer *peers, int n) {
    uint8_t b[CRC_MAX_PEERS * PEER_REC];
    char p[256];
    if (n > CRC_MAX_PEERS) return -1;
    memset(b, 0, sizeof b);
    for (int i = 0; i < n; ++i) {
        uint8_t *r = b + i * PEER_REC;
        memcpy(r, peers[i].id, 8);
        memcpy(r + 8, peers[i].ltk, 32);
        memcpy(r + 40, peers[i].name, 32);
        for (int k = 0; k < 4; ++k) r[72 + k] = peers[i].created >> (8 * k);
    }
    mkdir(crc_path(p, sizeof p, "/mnt/data/coda-rc"), 0700);
    int e = crc_write_file(PEERS, b, n * PEER_REC, 0600);
    crypto_wipe(b, sizeof b);
    return e;
}

/* The pairing dialog is the shim's (coda_rc_shim.c): it shows /tmp/coda-rc.pair while it exists, writes
 * .pair-cancel when the user closes it, and toasts what .pair-end says. */
void crc_pair_show(const char *code, const char *client_name) {
    char b[64];
    int n = snprintf(b, sizeof b, "%s\n%s\n", code, client_name);
    unlink_at("/tmp/coda-rc.pair-cancel");
    crc_write_file("/tmp/coda-rc.pair", b, n, 0600);
    crypto_wipe(b, sizeof b);
    crc_log("pairing: code shown for \"%s\"", client_name);
}

void crc_pair_hide(int outcome) {
    unlink_at("/tmp/coda-rc.pair");
    crc_write_file("/tmp/coda-rc.pair-end", outcome > 0 ? "1\n" : outcome == 0 ? "0\n" : "-1\n", outcome < 0 ? 3 : 2,
                   0600);
    crc_log("pairing: %s", outcome > 0 ? "paired" : outcome == 0 ? "wrong code" : "ended");
}

int crc_pair_cancelled(void) {
    char p[256];
    if (access(crc_path(p, sizeof p, "/tmp/coda-rc.pair-cancel"), 0)) return 0;
    unlink(p);
    return 1;
}

int crc_write(crc_session *s, const uint8_t *buf, size_t n) {
    if (s->transport == TR_BLE) return ble_write(buf, n);
    if (s->transport == CRC_TR_AVRCP) {
        avrcp_capture(buf, n);
        return 0;
    }
    conn *c = s->io;
    while (n) {
        ssize_t w = send(c->fd, buf, n, MSG_NOSIGNAL);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) return -1;  /* SO_SNDTIMEO: a client that stops reading is dropped */
        buf += w;
        n -= w;
    }
    return 0;
}

/* ---- Now Playing: the shim's /tmp/coda-rc.now (coda_rc_shim.c np_write) ---- */

static np_t np, np_sent;
static uint32_t np_at, pos_pushed;

static void np_parse(const char *b, np_t *o) {
    memset(o, 0, sizeof *o);
    for (const char *line = b; line && *line;) {
        const char *eq = strchr(line, '='), *end = strchr(line, '\n');
        if (!end) end = line + strlen(line);
        if (eq && eq < end) {
            size_t k = eq - line, vn = end - eq - 1;
            const char *v = eq + 1;
            char *dst = 0;
            size_t cap = 0;
#define NUM(name, f) else if (k == sizeof name - 1 && !memcmp(line, name, k)) o->f = (int)strtoul(v, 0, 10);
#define STR(name, f) else if (k == sizeof name - 1 && !memcmp(line, name, k)) dst = o->f, cap = sizeof o->f;
            if (0) {}
            NUM("state", state) NUM("pos", pos_ms) NUM("len", len_s) NUM("id", id) NUM("shuffle", shuffle)
            NUM("repeat", repeat) NUM("volume", volume) NUM("qpos", qpos) NUM("qlen", qlen) NUM("qgen", qgen)
            NUM("seq", seq) NUM("hearts", hearts) STR("path", path) STR("title", title) STR("artist", artist) STR("album", album)
            if (dst) {
                if (vn > cap - 1) vn = cap - 1;
                memcpy(dst, v, vn);
                dst[vn] = 0;
            }
        }
        line = *end ? end + 1 : 0;
    }
}

static int np_read(void) {
    char b[1400];
    int n = read_file("/tmp/coda-rc.now", b, sizeof b - 1);
    if (n <= 0) return 0;
    b[n] = 0;
    np_t t;
    np_parse(b, &t);
    if (t.seq == np.seq && np_at) return 0;
    np = t;
    np_at = crc_now_ms();
    return 1;
}

void crc_avrcp_now(int *qgen, int *qpos) {
    np_read();
    *qgen = np.qgen;
    *qpos = np.qpos;
}

int crc_avrcp_state(void) {
    np_read();
    return np.state;
}

int crc_shim_alive(void) {
    char p[256];
    return access(crc_path(p, sizeof p, "/tmp/coda-rc.now"), 0) == 0;
}

uint32_t crc_caps(void) {
    return CAP_BROWSE | CAP_SEARCH | CAP_QUEUE | CAP_COVERS |
           (crc_shim_alive() ? CAP_PLAY | CAP_SEEK | CAP_MODES | CAP_QUEUE_JUMP | CAP_RATE | CAP_RESUME : 0) |
           (lib_has_server() ? CAP_SERVER : 0);
}

static uint32_t position_now(void) {
    uint32_t p = np.pos_ms;
    if (np.state == 1) p += crc_now_ms() - np_at;
    if (np.len_s && p > np.len_s * 1000u) p = np.len_s * 1000u;
    return p;
}

static void put_now_playing(pbw *w) {
    char id[24] = "", art[24] = "";
    size_t m = pb_begin(w, 1);
    if (np.id > 0) snprintf(id, sizeof id, "song:%c%d", np.id >= 0x40000000 ? 'c' : 't', np.id);
    lib_art_of(np.path, art, sizeof art);
    pb_str(w, 1, id);
    pb_str(w, 2, np.title);
    pb_str(w, 3, np.artist);
    pb_str(w, 4, np.album);
    pb_varint(w, 5, np.len_s * 1000u);
    pb_str(w, 6, art);
    pb_varint(w, 7, np.state);
    pb_varint(w, 8, np.shuffle ? 1 : 0);
    pb_varint(w, 9, np.repeat);
    pb_varint(w, 10, np.volume);
    pb_varint(w, 11, np.hearts > 0 ? np.hearts : 0);
    pb_varint(w, 12, np.hearts >= 0 && np.id > 0);
    pb_end(w, m);
}

static void put_position(pbw *w) {
    size_t m = pb_begin(w, 2);
    pb_varint(w, 1, position_now());
    pb_varint(w, 2, crc_now_ms());
    pb_end(w, m);
}

static void put_queue(pbw *w) {
    size_t m = pb_begin(w, 3);
    pb_varint(w, 1, np.qgen);
    pb_varint(w, 2, np.qpos);
    pb_varint(w, 3, np.qlen);
    pb_end(w, m);
}

static int send_state(crc_session *s, unsigned req_id, int parts) {
    uint8_t b[1400];
    pbw w = {b, 0, sizeof b, 0};
    if (parts & 1) put_now_playing(&w);
    if (parts & 2) put_position(&w);
    if (parts & 4) put_queue(&w);
    return w.over ? crc_error(s, req_id, E_INTERNAL, "state") : crc_send(s, T_STATE, req_id, b, w.n);
}

static void each_session(void (*f)(crc_session *, int), int arg) {
    for (int i = 0; i < MAX_TCP; ++i)
        if (tcp[i].used && tcp[i].s.state == S_AUTHED) f(&tcp[i].s, arg);
    if (ble.used && ble.s.state == S_AUTHED) f(&ble.s, arg);
}

static void push(crc_session *s, int parts) {
    if (s->subscribed && (parts != 2 || s->subscribed & 2)) send_state(s, 0, parts);
}

static void np_poll(void) {
    if (!np_read() && !(np.state == 1 && crc_now_ms() - pos_pushed >= 1000)) return;
    int parts = 0;
    if (np.state != np_sent.state || np.id != np_sent.id || np.shuffle != np_sent.shuffle ||
        np.repeat != np_sent.repeat || np.volume != np_sent.volume || np.hearts != np_sent.hearts ||
        strcmp(np.path, np_sent.path) ||
        strcmp(np.title, np_sent.title))
        parts |= 1 | 2;
    if (np.qgen != np_sent.qgen || np.qpos != np_sent.qpos || np.qlen != np_sent.qlen) parts |= 4;
    if (np.pos_ms != np_sent.pos_ms) parts |= 2;  /* a seek, or the shim's once-a-second refresh */
    if (np.state == 1 && crc_now_ms() - pos_pushed >= 1000) parts |= 2;
    np_sent = np;
    if (!parts) return;
    lib_now(np.qgen, np.id);
    if (parts & 2) pos_pushed = crc_now_ms();
    each_session(push, parts);
}

/* ---- commands to the shim, and their acks ---- */

static struct {
    crc_session *s;
    unsigned req_id, seq;
    uint32_t at;
    char verb[24];
} pend;
static unsigned cmd_seq;

/* the shim's answer: 0 done, 3 the song named is not there (NOT_FOUND), anything else not done */
static void pend_answer(int code) {
    if (!pend.s) return;
    crc_log("demo: %s %s", pend.verb, !code ? "done" : "not done");
    if (!strncmp(pend.verb, "play ", 5)) {
        np_read(); /* the shim wrote the new queue's Now Playing before its ack */
        lib_played(!code, np.qgen);
        lib_now(np.qgen, np.id); /* its first song now: another list may start before the next push */
    }
    if (pend.s->state == S_AUTHED) {
        if (!code) crc_send(pend.s, T_ACK, pend.req_id, 0, 0);
        else if (code == 3) crc_error(pend.s, pend.req_id, E_NOT_FOUND, "not the Q2's song now");
        else crc_error(pend.s, pend.req_id, E_UNAVAILABLE, "the Q2 did not take it");
    }
    pend.s = 0;
}

/* the shim's command, answered once it acks or after ACK_MS */
int crc_shim_command(crc_session *s, unsigned req_id, const char *verb) {
    char b[96];
    if (!crc_shim_alive()) return crc_error(s, req_id, E_UNAVAILABLE, "demo is not answering");
    pend_answer(1);  /* one in flight: a newer command supersedes it */
    unsigned seq = ++cmd_seq ? cmd_seq : ++cmd_seq;
    int n = snprintf(b, sizeof b, "%u %s\n", seq, verb);
    if (crc_write_file("/tmp/coda-rc.cmd", b, n, 0600)) return crc_error(s, req_id, E_INTERNAL, "cmd");
    pend.s = s;
    pend.req_id = req_id;
    pend.seq = seq;
    pend.at = crc_now_ms();
    snprintf(pend.verb, sizeof pend.verb, "%s", verb);
    return 0;
}

static void ack_poll(void) {
    if (!pend.s) return;
    char b[32];
    int n = read_file("/tmp/coda-rc.ack", b, sizeof b - 1);
    if (n > 0) {
        b[n] = 0;
        char *sp = strchr(b, ' ');
        if ((unsigned)strtoul(b, 0, 10) == pend.seq) {
            unlink_at("/tmp/coda-rc.ack");
            pend_answer(sp ? atoi(sp + 1) : 1);
            return;
        }
    }
    if (crc_now_ms() - pend.at > ACK_MS) pend_answer(1);
}

/* ---- transport ---- */

static int transport(crc_session *s, unsigned req_id, const uint8_t *p, size_t n) {
    pbr r = {p, p + n};
    pbf f;
    unsigned cmd = 0, seek = 0, vol = 0;
    int k;
    while ((k = pb_next(&r, &f)) > 0) {
        if (f.wire != 0) continue;
        if (f.field == 1) cmd = (unsigned)f.v;
        if (f.field == 2) seek = (unsigned)f.v;
        if (f.field == 3) vol = (unsigned)f.v;
    }
    if (k < 0) return crc_error(s, req_id, E_BAD_REQUEST, "Transport");
    /* Play/Pause, Next and Previous go to the shim as a headset's buttons would (coda_rc_shim.c cr_command),
     * which answers Pause and Play once the player shows them, so the ack is the player's, not a hope */
    static const char *const verbs[] = {0, "resume", "pause", "toggle", "next", "previous"};
    char verb[48];
    switch (cmd) {
    case 1: case 2: case 3: case 4: case 5: snprintf(verb, sizeof verb, "%s", verbs[cmd]); break;
    case 6: snprintf(verb, sizeof verb, "seek %u", seek / 1000); break;
    case 7: case 8: case 9: {
        int v = cmd == 9 ? (int)vol : np.volume + (cmd == 7 ? 5 : -5);
        if (cmd == 9 && vol > 100) return crc_error(s, req_id, E_BAD_REQUEST, "volume 0..100");
        snprintf(verb, sizeof verb, "volume %d", v < 0 ? 0 : v > 100 ? 100 : v);
        break;
    }
    default: return crc_error(s, req_id, E_BAD_REQUEST, "unknown command");
    }
    /* one line a command: what was asked, and the state the phone last saw (and how old it is) */
    crc_log("transport %s: state %d seq %d %ums old", verb, np.state, np.seq, crc_now_ms() - np_at);
    return crc_shim_command(s, req_id, verb);
}

/* ---- modes, rating and the queue: the shim's mode, rate and jump ---- */

/* a message's varint fields 1..4 (the rest skipped); -1 when malformed */
static int fields(const uint8_t *p, size_t n, unsigned v[5], char *str, size_t cap, unsigned str_field) {
    pbr r = {p, p + n};
    pbf f;
    int k;
    memset(v, 0, 5 * sizeof *v);
    if (str) *str = 0;
    while ((k = pb_next(&r, &f)) > 0) {
        if (f.wire == 0 && f.field < 5) v[f.field] = f.v > 0xffff ? 0xffff : (unsigned)f.v;
        if (f.wire == 2 && str && f.field == str_field) pb_cstr(&f, str, cap);
    }
    return k;
}

static int set_mode(crc_session *s, unsigned req_id, const uint8_t *p, size_t n) {
    unsigned v[5];
    char verb[32];
    /* SetMode: set_shuffle 1, shuffle 2, set_repeat 3, repeat 4 */
    if (fields(p, n, v, 0, 0, 0) < 0 || v[2] > 1 || v[4] > 2 || (!v[1] && !v[3]))
        return crc_error(s, req_id, E_BAD_REQUEST, "SetMode");
    snprintf(verb, sizeof verb, "mode %d %d", v[1] ? (int)v[2] : -1, v[3] ? (int)v[4] : -1);
    return crc_shim_command(s, req_id, verb);
}

static int rate(crc_session *s, unsigned req_id, const uint8_t *p, size_t n) {
    unsigned v[5];
    char id[40], verb[32];
    /* Rate: track_id 1, hearts 2 */
    if (fields(p, n, v, id, sizeof id, 1) < 0 || v[2] > 5) return crc_error(s, req_id, E_BAD_REQUEST, "Rate");
    int rid = lib_song_rid(id);
    if (!rid || rid != np.id) return crc_error(s, req_id, E_NOT_FOUND, "not the playing song");
    if (np.hearts < 0) return crc_error(s, req_id, E_UNAVAILABLE, "this song can't be rated");
    snprintf(verb, sizeof verb, "rate %u %d", v[2], rid);
    return crc_shim_command(s, req_id, verb);
}

static int queue_item(crc_session *s, unsigned req_id, const uint8_t *p, size_t n) {
    unsigned v[5];
    char id[40], verb[32];
    /* PlayQueueItem: index 1, track_id 2 */
    if (fields(p, n, v, id, sizeof id, 2) < 0) return crc_error(s, req_id, E_BAD_REQUEST, "PlayQueueItem");
    int rid = lib_song_rid(id);
    if (!rid || v[1] >= (unsigned)np.qlen) return crc_error(s, req_id, E_NOT_FOUND, "not in the queue");
    snprintf(verb, sizeof verb, "jump %u %d", v[1], rid);
    return crc_shim_command(s, req_id, verb);
}

/* ---- authenticated requests ---- */

int crc_app(crc_session *s, int type, unsigned req_id, const uint8_t *p, size_t n) {
    switch (type) {
    case T_SUBSCRIBE: {
        pbr r = {p, p + n};
        pbf f;
        int k, pos = 0;
        while ((k = pb_next(&r, &f)) > 0)
            if (f.field == 1 && f.wire == 0) pos = f.v != 0;
        if (k < 0) return crc_error(s, req_id, E_BAD_REQUEST, "Subscribe");
        s->subscribed = 1 | pos << 1;
        crc_send(s, T_ACK, req_id, 0, 0);
        np_read();
        return send_state(s, 0, 7);
    }
    case T_GET_STATE: np_read(); return send_state(s, req_id, 7);
    case T_TRANSPORT: return transport(s, req_id, p, n);
    case T_SET_MODE: np_read(); return set_mode(s, req_id, p, n);
    case T_RATE: np_read(); return rate(s, req_id, p, n);
    case T_PLAY_QUEUE_ITEM: np_read(); return queue_item(s, req_id, p, n);
    case T_BROWSE: case T_SEARCH: case T_GET_QUEUE: case T_GET_COVER: case T_PLAY:
        np_read();
        return lib_request(s, type, req_id, p, n, np.qgen, np.qpos);
    }
    return crc_error(s, req_id, E_UNSUPPORTED, "unknown type");
}

/* ---- sessions on TCP ---- */

static void conn_close(conn *c) {
    if (pend.s == &c->s) pend.s = 0;
    crc_close(&c->s);
    if (c->fd >= 0) close(c->fd);
    c->fd = -1;
    c->used = 0;
}

static void tcp_accept(int lfd) {
    int fd = accept(lfd, 0, 0);
    if (fd < 0) return;
    conn *c = 0;
    for (int i = 0; i < MAX_TCP && !c; ++i)
        if (!tcp[i].used) c = &tcp[i];
    if (!c) { close(fd); return; }
    struct timeval tv = {2, 0};
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
    c->fd = fd;
    c->used = 1;
    c->opened = crc_now_ms();
    crc_open(&c->s, c, TR_TCP);
}

static void tcp_read(conn *c) {
    uint8_t b[2048];
    ssize_t r = read(c->fd, b, sizeof b);
    if (r < 0 && (errno == EAGAIN || errno == EINTR)) return;
    if (r <= 0 || crc_feed(&c->s, b, r) < 0) conn_close(c);
}

static int tcp_listen(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    if (fd < 0) return -1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in a;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
#ifdef CODA_HOST
    a.sin_addr.s_addr = htonl(0x7f000001);  /* the tests' daemon is never on the network */
#else
    a.sin_addr.s_addr = INADDR_ANY;
#endif
    if (bind(fd, (void *)&a, sizeof a) || listen(fd, 4)) {
        close(fd);
        return -1;
    }
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
    return fd;
}

/* ---- BLE's session (coda_rc_ble.c calls these) ---- */

void crc_ble_connected(void) {
    if (ble.used) conn_close(&ble);
    ble.used = 1;
    ble.fd = -1;
    ble.opened = crc_now_ms();
    crc_open(&ble.s, &ble, TR_BLE);
}

void crc_ble_bytes(const uint8_t *p, size_t n) {
    if (!ble.used) crc_ble_connected();
    if (crc_feed(&ble.s, p, n) < 0) {
        conn_close(&ble);
        ble_disconnect();
    }
}

void crc_ble_gone(void) {
    if (ble.used) conn_close(&ble);
}

/* ---- the loop ---- */

static void on_term(int sig) { quit = 1; }

static void timeouts(conn *c) {
    if (!c->used) return;
    uint32_t age = crc_now_ms() - c->opened;
    if ((c->s.state == S_NEW && age > HELLO_MS) || (c->s.state != S_AUTHED && age > AUTH_MS)) {
        conn_close(c);
        if (c == &ble) ble_disconnect();
    }
}

static void peers_poll(void) {
    crc_peer peers[CRC_MAX_PEERS];
    int n = crc_peers_load(peers, CRC_MAX_PEERS);
    conn *all[MAX_TCP + 1];
    for (int i = 0; i < MAX_TCP; ++i) all[i] = &tcp[i];
    all[MAX_TCP] = &ble;
    for (int i = 0; i <= MAX_TCP; ++i) {
        conn *c = all[i];
        if (!c->used || (c->s.state != S_AUTHED && c->s.state != S_PAIRED)) continue;
        int found = 0;
        for (int k = 0; k < n; ++k) found |= !memcmp(peers[k].id, c->s.peer, CRC_PEER_ID);
        if (!found) {  /* forgotten in Settings: its key is gone, and so is the session */
            crc_log("session of a forgotten phone closed");
            crc_error(&c->s, 0, E_UNAUTHORIZED, "forgotten on the Q2");
            conn_close(c);
            if (c == &ble) ble_disconnect();
        }
    }
    crypto_wipe(peers, sizeof peers);
}

static int already_running(const char *pidfile) {
    char b[16];
    int n = read_file(pidfile, b, sizeof b - 1);
    if (n <= 0) return 0;
    b[n] = 0;
    int pid = atoi(b);
    return pid > 1 && pid != getpid() && kill(pid, 0) == 0;
}

static void device_id(void) {
    char p[256];
    if (read_file("/mnt/data/coda-rc/id", crc_device_id, 8) == 8) return;
    crc_random(crc_device_id, 8);
    mkdir(crc_path(p, sizeof p, "/mnt/data/coda-rc"), 0700);
    crc_write_file("/mnt/data/coda-rc/id", crc_device_id, 8, 0600);
}

/* ---- coda-rc --avrcp: bluetoothd's browsing (coda_rc_avrcp.c) ---- */

#define AVRCP_CLIENTS 2

typedef struct {
    int fd;
    size_t n;
    uint8_t in[2 + 1024];
} avrcp_client;

static int avrcp_listen(void) {
    char p[256];
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    crc_path(p, sizeof p, CRC_AVRCP_SOCKET);
    if (strlen(p) >= sizeof a.sun_path) return -1;
    memcpy(a.sun_path, p, strlen(p) + 1);
    unlink(p);
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    /* root's alone (bluetoothd's): the socket plays music */
    if (bind(fd, (const void *)&a, sizeof a) || chmod(p, 0600) || listen(fd, 2)) {
        close(fd);
        return -1;
    }
    return fd;
}

/* the frames a client has sent: {u16 length, request}; each answered {u16 length, reply} in turn */
static int avrcp_serve(avrcp_client *c) {
    static uint8_t rsp[2 + 1024];
    ssize_t r = read(c->fd, c->in + c->n, sizeof c->in - c->n);
    if (r < 0 && (errno == EAGAIN || errno == EINTR)) return 0;
    if (r <= 0) return -1;
    c->n += r;
    while (c->n >= 2) {
        size_t len = (size_t)c->in[0] << 8 | c->in[1];
        if (len > sizeof c->in - 2) return -1;
        if (c->n < 2 + len) break;
        size_t out = 0;
        if (len >= 5 && c->in[2] == CRC_AVRCP_FORGET)
            avrcp_forget((uint32_t)c->in[3] << 24 | c->in[4] << 16 | c->in[5] << 8 | c->in[6]);
        else out = avrcp_request(c->in + 2, len, rsp + 2, sizeof rsp - 2);
        if (out) {
            rsp[0] = out >> 8;
            rsp[1] = out;
            if (write(c->fd, rsp, 2 + out) != (ssize_t)(2 + out)) return -1;
        }
        memmove(c->in, c->in + 2 + len, c->n - 2 - len);
        c->n -= 2 + len;
    }
    return 0;
}

static int avrcp_main(void) {
    if (already_running(CRC_AVRCP_PID)) return 0;
    char b[16];
    crc_write_file(CRC_AVRCP_PID, b, snprintf(b, sizeof b, "%d\n", getpid()), 0644);
    cmd_seq = 0x40000000; /* apart from the Car Remote daemon's, should both write the shim a command */
    int lfd = avrcp_listen();
    crc_log("avrcp: %s", lfd >= 0 ? "listening" : "no socket");
    avrcp_client cl[AVRCP_CLIENTS];
    for (int i = 0; i < AVRCP_CLIENTS; ++i) cl[i].fd = -1;
    while (!quit && lfd >= 0) {
        struct pollfd fds[AVRCP_CLIENTS + 1] = {{lfd, POLLIN, 0}};
        for (int i = 0; i < AVRCP_CLIENTS; ++i) fds[i + 1] = (struct pollfd){cl[i].fd, POLLIN, 0};
        if (poll(fds, AVRCP_CLIENTS + 1, POLL_MS) < 0 && errno != EINTR) break;
        if (fds[0].revents & POLLIN) {
            int fd = accept(lfd, 0, 0), placed = 0;
            for (int i = 0; fd >= 0 && i < AVRCP_CLIENTS && !placed; ++i)
                if (cl[i].fd < 0) cl[i].fd = fd, cl[i].n = 0, placed = 1;
            if (fd >= 0 && !placed) close(fd);
        }
        for (int i = 0; i < AVRCP_CLIENTS; ++i)
            if (cl[i].fd >= 0 && fds[i + 1].revents && avrcp_serve(&cl[i]) < 0) {
                close(cl[i].fd);
                cl[i].fd = -1;
            }
        ack_poll();
    }
    for (int i = 0; i < AVRCP_CLIENTS; ++i)
        if (cl[i].fd >= 0) close(cl[i].fd);
    if (lfd >= 0) close(lfd);
    unlink_at(CRC_AVRCP_SOCKET);
    unlink_at(CRC_AVRCP_PID);
    return 0;
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--port") && i + 1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--no-ble")) use_ble = 0;
        else if (!strcmp(argv[i], "--avrcp")) avrcp = 1;
    }
    setpriority(PRIO_PROCESS, 0, 19);
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_term);
    signal(SIGHUP, on_term);
    if (avrcp) return avrcp_main();
    if (already_running("/tmp/coda-rc.pid")) return 0;
    char b[16];
    crc_write_file("/tmp/coda-rc.pid", b, snprintf(b, sizeof b, "%d\n", getpid()), 0644);
    device_id();
    unlink_at("/tmp/coda-rc.pair");
    int lfd = tcp_listen();
    crc_log("start: tcp %d %s", port, lfd >= 0 ? "listening" : "failed");
    int bfd = use_ble ? ble_start() : -1;
    for (int i = 0; i < MAX_TCP; ++i) tcp[i].fd = -1;
    uint32_t last_tick = 0;
    while (!quit) {
        struct pollfd fds[MAX_TCP + 3];
        int nf = 0;
        if (lfd >= 0) fds[nf++] = (struct pollfd){lfd, POLLIN, 0};
        if (bfd >= 0) fds[nf++] = (struct pollfd){bfd, (short)ble_events(), 0};
        int at[MAX_TCP];
        for (int i = 0; i < MAX_TCP; ++i) {
            at[i] = -1;
            if (tcp[i].used) { at[i] = nf; fds[nf++] = (struct pollfd){tcp[i].fd, POLLIN, 0}; }
        }
        int r = poll(fds, nf, POLL_MS);
        if (r < 0 && errno != EINTR) break;
        int k = 0;
        if (lfd >= 0 && fds[k++].revents & POLLIN) tcp_accept(lfd);
        if (bfd >= 0) ++k;
        ble_handle(); /* every pass: libdbus may hold bytes it has already read */
        for (int i = 0; i < MAX_TCP; ++i)
            if (at[i] >= 0 && tcp[i].used && fds[at[i]].revents) tcp_read(&tcp[i]);
        np_poll();
        ack_poll();
        uint32_t now = crc_now_ms();
        if (now - last_tick >= 1000) {
            last_tick = now;
            crc_tick();
            peers_poll();
            for (int i = 0; i < MAX_TCP; ++i) timeouts(&tcp[i]);
            timeouts(&ble);
            if (bfd < 0 && use_ble && ble_retry_due(now)) bfd = ble_start();
            else if (bfd >= 0) ble_retry_due(now);
        }
    }
    crc_log("stop");
    for (int i = 0; i < MAX_TCP; ++i)
        if (tcp[i].used) conn_close(&tcp[i]);
    if (ble.used) conn_close(&ble);
    ble_stop();
    unlink_at("/tmp/coda-rc.pair");
    unlink_at("/tmp/coda-rc.pid");
    return 0;
}
