/* coda-rc, the car remote daemon (docs/remote-protocol.md, docs/internals.md#car-remote): the platform under
 * coda_rc_core.c. It listens on TCP 7720 and, as a BLE GATT peripheral with its own ATT server (coda_rc_ble.c), serves
 * the library from SQLite (coda_rc_lib.c), and talks to demo only through files in /tmp that the payload's
 * shim (patch/coda_rc_shim.c) polls: Now Playing out; play, transport, seek, volume, mode, rate, queue and
 * radio commands, the pairing code and the connected phones in, and a card app to start for a cover the car wants
 * (crc_cover_ask, which asks the app itself through its /tmp files).
 *
 * demo starts it when Settings > Car Remote is on and kills it when that goes off; nothing else starts it.
 * It also answers mDNS for Q2 Muse Remote's Wi-Fi tab (coda_rc_mdns.c) on wlan0, or the interface --mdns-if
 * names (the emulator's), and not at all with --no-mdns; the host build answers only with --mdns-if.
 * It runs at nice 19. --root DIR prefixes every path (the host tests); --no-ble, --ble-sim DIR and --port N are for
 * them too. */
#include "coda_rc_sys.h"
#include "coda_rc.h"
#include "monocypher/monocypher.h"

#define MAX_TCP 4
#define HELLO_MS 10000
#define AUTH_MS 150000
#define ACK_MS 4000
#define TICK_MS 1000 /* the loop's longest sleep: pairing's end, forgotten phones, the handshakes' limits */
#define LOG_MAX 65536

enum { TR_TCP, TR_BLE };

static char root[160];
static int port = CRC_TCP_PORT, use_ble = 1, quit, rnd = -1;
#ifdef CODA_HOST
static const char *mdns_if; /* the tests' daemon is never on the network unless asked */
#else
static const char *mdns_if = "wlan0";
#endif
uint8_t crc_device_id[8];

typedef struct {
    int seq, state, pos_ms, len_s, id, shuffle, repeat, volume, qpos, qlen, qgen, radio;
    int hearts; /* 0-5, -1 when the song can't be rated */
    int buf, bhave, btotal, bms; /* a server song loading: its bytes and length (np_stream.c) */
    int noten;                   /* the Q2 Muse's toasts so far; note the last, while shown */
    char path[512], title[160], artist[160], album[160], note[96];
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

/* --tell-fd: the write end of demo's pipe (coda_rc_shim.c cr_start). A byte after each change to a file the shim
 * reads, so it reads them then instead of on its clock; nothing else goes on it and nothing comes back. A write
 * refused for any reason but a full pipe (demo gone) closes it, and the shim's clock is all there is. */
static int tell_fd = -1;
static void tell_shim(const char *path) {
    static const char *const read_by_shim[] = { "/tmp/coda-rc.cmd", "/tmp/coda-rc.pair", "/tmp/coda-rc.pair-end",
                                                "/tmp/coda-rc.wake" };
    if (tell_fd < 0) return;
    for (unsigned i = 0; i < sizeof read_by_shim / sizeof *read_by_shim; ++i)
        if (!strcmp(path, read_by_shim[i])) {
            if (write(tell_fd, "1", 1) < 0 && errno != EAGAIN) close(tell_fd), tell_fd = -1;
            return;
        }
}

/* The fd --tell-fd names, if it is open for writing only, as a pipe's write end is: nonblocking, and not
 * inherited further. */
static int tell_open(int fd) {
    int fl = fd > 2 ? fcntl(fd, F_GETFL) : -1;
    if (fl < 0 || (fl & 3) != O_WRONLY) return -1;
    fcntl(fd, F_SETFL, fl | O_NONBLOCK);
    fcntl(fd, F_SETFD, FD_CLOEXEC);
    return fd;
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
    if (ok) tell_shim(path);
    return ok ? 0 : -1;
}

static void unlink_at(const char *path) {
    char p[256];
    if (!unlink(crc_path(p, sizeof p, path))) tell_shim(path);
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
static uint32_t np_at, pos_pushed, buf_pushed;

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
            NUM("seq", seq) NUM("hearts", hearts) NUM("buf", buf) NUM("bhave", bhave) NUM("btotal", btotal)
            NUM("bms", bms) NUM("radio", radio) NUM("noten", noten) STR("note", note) STR("path", path) STR("title", title) STR("artist", artist) STR("album", album)
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

int crc_shim_alive(void) {
    char p[256];
    return access(crc_path(p, sizeof p, "/tmp/coda-rc.now"), 0) == 0;
}

uint32_t crc_caps(void) {
    return CAP_BROWSE | CAP_SEARCH | CAP_QUEUE | CAP_COVERS | CAP_FAVORITES | CAP_RECENT |
           (crc_shim_alive() ? CAP_PLAY | CAP_SEEK | CAP_MODES | CAP_QUEUE_JUMP | CAP_RATE | CAP_RESUME | CAP_BUFFERING | CAP_RADIO | CAP_NOTICE : 0) |
           (lib_has_server() ? CAP_SERVER : 0);
}

static uint32_t position_now(void) {
    uint32_t p = np.pos_ms;
    if (np.state == 1) p += crc_now_ms() - np_at;
    if (np.len_s && p > np.len_s * 1000u) p = np.len_s * 1000u;
    return p;
}

/* A streaming song (no library record, the shim's id 0, its file's name for a title): the catalogue's server song
 * for it (coda_rc_lib.c lib_stream_song), looked up once per path. */
static const lib_song_t *np_stream(void) {
    static char path[sizeof np.path];
    static lib_song_t song;
    static int found;
    if (np.id > 0 || !*np.path) return 0;
    if (strcmp(path, np.path)) {
        snprintf(path, sizeof path, "%s", np.path);
        found = lib_stream_song(np.path, &song);
    }
    return found ? &song : 0;
}

static void put_now_playing(pbw *w) {
    char id[24] = "", art[48] = "";
    size_t m = pb_begin(w, 1);
    const lib_song_t *ls = np_stream();
    int rid = ls ? ls->id : np.id;
    if (rid > 0) snprintf(id, sizeof id, "song:%c%d", rid >= 0x40000000 ? 'c' : 't', rid);
    if (ls)
        snprintf(art, sizeof art, "%s", ls->art);
    else
        lib_art_of(np.path, art, sizeof art);
    pb_str(w, 1, id);
    pb_str(w, 2, ls ? ls->title : np.title);
    pb_str(w, 3, ls ? ls->artist : np.artist);
    pb_str(w, 4, ls ? ls->album : np.album);
    pb_varint(w, 5, (np.len_s > 0 || !ls ? np.len_s : ls->secs) * 1000u);
    pb_str(w, 6, art);
    pb_varint(w, 7, np.state);
    pb_varint(w, 8, np.shuffle ? 1 : 0);
    pb_varint(w, 9, np.repeat);
    pb_varint(w, 10, np.volume);
    pb_varint(w, 11, np.hearts > 0 ? np.hearts : 0);
    pb_varint(w, 12, np.hearts >= 0 && rid > 0);
    pb_varint(w, 13, np.buf != 0); /* CAP_BUFFERING: a server song loading, how far it has come */
    pb_varint(w, 14, (uint32_t)np.bhave);
    pb_varint(w, 15, (uint32_t)np.btotal);
    pb_varint(w, 16, crc_buffered_ms((uint32_t)np.bhave, (uint32_t)np.btotal, (uint32_t)np.bms));
    pb_varint(w, 17, np.radio != 0); /* CAP_RADIO */
    pb_str(w, 18, np.note);          /* CAP_NOTICE: the toast shown, while it is */
    pb_varint(w, 19, (uint32_t)np.noten);
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
        np.radio != np_sent.radio || np.noten != np_sent.noten || strcmp(np.note, np_sent.note) ||
        strcmp(np.path, np_sent.path) || np.buf != np_sent.buf ||
        /* its bytes: at most twice a second */
        (np.bhave != np_sent.bhave && crc_now_ms() - buf_pushed >= 500) ||
        strcmp(np.title, np_sent.title))
        parts |= 1 | 2;
    if (np.qgen != np_sent.qgen || np.qpos != np_sent.qpos || np.qlen != np_sent.qlen) parts |= 4;
    if (np.pos_ms != np_sent.pos_ms) parts |= 2;  /* a seek, or the shim's once-a-second refresh */
    if (np.state == 1 && crc_now_ms() - pos_pushed >= 1000) parts |= 2;
    int bhave = parts & 1 ? np.bhave : np_sent.bhave; /* bytes held back are sent at a later look */
    np_sent = np;
    np_sent.bhave = bhave;
    if (parts & 1) buf_pushed = crc_now_ms();
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

/* ---- covers the card lacks, asked of their card app (docs/internals.md#car-remote-covers) ---- */

/* A GetCover for a server album with no cover on the card (crc_cover_ask, from coda_rc_lib.c's lib_cover) asks the
 * album's card app, when its q2app says covers=1: /tmp/q2-<id>.coverask holds a line per cover asked and not yet
 * answered, "<seq> <px> <album>", and the app answers each in its state file, coverack=<seq> <code>, in seq order
 * (q2-subsonic coverask.go). An app that doesn't run is started by demo's shim, which /tmp/coda-rc.wake names it to.
 * The GetCover's answer is held meanwhile, without stopping the loop: once the app has answered, or after
 * COVER_HOLD_MS, it goes out through lib_cover, the cover if the app saved it, else NOT_FOUND, which the car shows as
 * its placeholder and asks again when it next draws the row. An album is asked once: answered without a cover, or
 * not answered in COVER_ASK_MS, it is not asked again for COVER_AGAIN_MS (the app remembers its own failures too). */
#define COVER_HOLD_MS 5000    /* under the phone's 8 s for any answer (q2-auto-remote CodaSession.TIMEOUT_MS), past
                                 which it closes the session */
#define COVER_ASK_MS 60000    /* an app that hasn't answered by then is taken as having none */
#define COVER_AGAIN_MS 600000 /* an album answered without a cover is not asked again for this long */
#define COVER_HOLDS 8
#define COVER_ASKS 16

static struct {
    crc_session *s;
    unsigned req_id, limit, max_px, seq;
    uint32_t at;
    char art[48];
} holds[COVER_HOLDS];

static struct {
    int state; /* 0 free, 1 waiting for the app's answer, 2 answered (or given up) at at */
    unsigned seq, px;
    uint32_t at;
    char id[16], album[48];
} asks[COVER_ASKS];
static unsigned ask_seq;

static int after(unsigned a, unsigned b) { return (int)(a - b) > 0; }

/* the card app's q2app says covers=1 */
static int covers_app(const char *id) {
    char path[64], b[1024];
    snprintf(path, sizeof path, "/mnt/mmc/.%s/q2app", id);
    int n = read_file(path, b, sizeof b - 1);
    if (n <= 0) return 0;
    b[n] = 0;
    for (char *l = b; l; l = strchr(l, '\n'), l = l ? l + 1 : 0)
        if (!strncmp(l, "covers=1", 8) && (l[8] == '\n' || l[8] == '\r' || !l[8])) return 1;
    return 0;
}

/* /tmp/q2-<id>.coverask: the app's asks still waiting; none, no file */
static void asks_write(const char *id) {
    char path[48], b[COVER_ASKS * 72];
    int n = 0;
    for (int i = 0; i < COVER_ASKS; ++i)
        if (asks[i].state == 1 && !strcmp(asks[i].id, id))
            n += snprintf(b + n, sizeof b - n, "%u %u %s\n", asks[i].seq, asks[i].px, asks[i].album);
    snprintf(path, sizeof path, "/tmp/q2-%s.coverask", id);
    if (n) crc_write_file(path, b, n, 0644);
    else unlink_at(path);
}

int crc_cover_ask(crc_session *s, unsigned req_id, const char *art, unsigned limit, unsigned max_px, const char *id,
                  const char *album, unsigned px) {
    uint32_t now = crc_now_ms();
    int a = -1;
    if (strlen(id) > 15 || strlen(album) > 47 || strlen(art) > 47 || strchr(album, ' ') || strchr(album, '\n'))
        return 0;
    for (const char *k = id; *k; ++k)
        if (!((*k >= 'a' && *k <= 'z') || (*k >= '0' && *k <= '9') || *k == '-')) return 0;
    for (int i = 0; i < COVER_ASKS && a < 0; ++i)
        if (asks[i].state && asks[i].px == px && !strcmp(asks[i].id, id) && !strcmp(asks[i].album, album)) a = i;
    if (a >= 0 && asks[a].state == 2) {
        if (now - asks[a].at < COVER_AGAIN_MS) return 0; /* asked lately, and the app had none */
        asks[a].state = 0;
        a = -1;
    }
    if (a < 0) {
        if (!covers_app(id)) return 0;
        for (int i = 0; i < COVER_ASKS; ++i) /* a free one, else the answer kept longest, else the oldest ask */
            if (a < 0 || !asks[i].state || (asks[a].state && (asks[i].state < asks[a].state ||
                                                             (asks[i].state == asks[a].state && after(asks[a].at, asks[i].at)))))
                a = i;
        if (!ask_seq) ask_seq = now | 1; /* above any seq an earlier run of the daemon gave since boot */
        asks[a].state = 1, asks[a].seq = ask_seq++, asks[a].px = px, asks[a].at = now;
        snprintf(asks[a].id, sizeof asks[a].id, "%s", id);
        snprintf(asks[a].album, sizeof asks[a].album, "%s", album);
        asks_write(id);
        char sock[48], p[256];
        snprintf(sock, sizeof sock, "/tmp/q2-%s.browse.sock", id);
        if (access(crc_path(p, sizeof p, sock), 0)) { /* not running: demo starts it */
            char line[20];
            crc_write_file("/tmp/coda-rc.wake", line, snprintf(line, sizeof line, "%s\n", id), 0644);
        }
        crc_log("cover: asked %s for %s at %u px", id, album, px);
    }
    for (int i = 0; i < COVER_HOLDS; ++i)
        if (!holds[i].s) {
            holds[i].s = s, holds[i].req_id = req_id, holds[i].limit = limit, holds[i].max_px = max_px;
            holds[i].seq = asks[a].seq, holds[i].at = now;
            snprintf(holds[i].art, sizeof holds[i].art, "%s", art);
            return 1;
        }
    return 0; /* every hold in use: NOT_FOUND now, and the app fetches it all the same */
}

/* the app's answers: asks it has answered, or that waited COVER_ASK_MS, are done; then holds whose ask is done, or
 * that waited COVER_HOLD_MS, are answered */
static void cover_poll(void) {
    uint32_t now = crc_now_ms();
    for (int i = 0; i < COVER_ASKS; ++i) {
        if (asks[i].state != 1) continue;
        int j = 0;
        while (j < i && !(asks[j].state == 1 && !strcmp(asks[j].id, asks[i].id))) ++j;
        if (j < i) continue; /* this app's file was read for an earlier one */
        char path[48], b[1600];
        snprintf(path, sizeof path, "/tmp/q2-%s.state", asks[i].id);
        int n = read_file(path, b, sizeof b - 1), changed = 0;
        b[n > 0 ? n : 0] = 0;
        char *v = strstr(b, "coverack=");
        if (v && v != b && v[-1] != '\n') v = 0;
        unsigned acked = v ? (unsigned)strtoul(v + 9, 0, 10) : 0;
        for (int k = i; k < COVER_ASKS; ++k)
            if (asks[k].state == 1 && !strcmp(asks[k].id, asks[i].id) &&
                ((v && !after(asks[k].seq, acked)) || now - asks[k].at >= COVER_ASK_MS))
                asks[k].state = 2, asks[k].at = now, changed = 1;
        if (changed) asks_write(asks[i].id);
    }
    for (int i = 0; i < COVER_HOLDS; ++i) {
        if (!holds[i].s) continue;
        int waiting = 0;
        for (int k = 0; k < COVER_ASKS; ++k) waiting |= asks[k].state == 1 && asks[k].seq == holds[i].seq;
        if (waiting && now - holds[i].at < COVER_HOLD_MS) continue;
        crc_session *s = holds[i].s;
        holds[i].s = 0;
        if (s->state == S_AUTHED) lib_cover(s, holds[i].req_id, holds[i].art, 0, holds[i].limit, holds[i].max_px, 0);
    }
}

/* the soonest a hold is over: ms from now, 0xffffffff none */
static uint32_t cover_due(uint32_t now) {
    uint32_t w = 0xffffffffu;
    for (int i = 0; i < COVER_HOLDS; ++i)
        if (holds[i].s) {
            uint32_t d = holds[i].at + COVER_HOLD_MS + 1 - now;
            d = d >= 0x80000000u ? 0 : d;
            if (d < w) w = d;
        }
    return w;
}

/* a session gone: its held answers with it */
static void cover_drop(crc_session *s) {
    for (int i = 0; i < COVER_HOLDS; ++i)
        if (holds[i].s == s) holds[i].s = 0;
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
    const lib_song_t *ls = np_stream(); /* a streaming song: its server song's id, its record's (0) to the shim */
    if (!rid || rid != (ls ? ls->id : np.id)) return crc_error(s, req_id, E_NOT_FOUND, "not the playing song");
    if (np.hearts < 0) return crc_error(s, req_id, E_UNAVAILABLE, "this song can't be rated");
    snprintf(verb, sizeof verb, "rate %u %d", v[2], np.id);
    return crc_shim_command(s, req_id, verb);
}

static int queue_item(crc_session *s, unsigned req_id, const uint8_t *p, size_t n) {
    unsigned v[5];
    char id[40], verb[32];
    /* PlayQueueItem: index 1, track_id 2 */
    if (fields(p, n, v, id, sizeof id, 2) < 0) return crc_error(s, req_id, E_BAD_REQUEST, "PlayQueueItem");
    int rid = lib_song_rid(id);
    if (!rid || v[1] >= (unsigned)np.qlen) return crc_error(s, req_id, E_NOT_FOUND, "not in the queue");
    /* a stream download, named by its server song (GetQueue), is its record's 0 to the shim */
    snprintf(verb, sizeof verb, "jump %u %d", v[1], lib_queue_rid(v[1], rid));
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
    case T_BROWSE: case T_SEARCH: case T_GET_QUEUE: case T_GET_COVER: case T_PLAY: case T_START_RADIO:
        np_read();
        return lib_request(s, type, req_id, p, n, np.qgen, np.qpos);
    }
    return crc_error(s, req_id, E_UNSUPPORTED, "unknown type");
}

/* ---- sessions on TCP ---- */

static void conn_close(conn *c) {
    if (pend.s == &c->s) pend.s = 0;
    cover_drop(&c->s);
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

int crc_tcp_port(void) { return port; }

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

/* Whether a phone's Bluetooth scan can find this Q2 now: the Pair a Phone page reads it (carremote_page.c
 * cr_howto_mode) to give the Bluetooth steps only then, the Wi-Fi ones otherwise. */
void crc_ble_advertising(int on) {
    if (on) crc_write_file("/tmp/coda-rc.ble", "1\n", 2, 0644);
    else unlink_at("/tmp/coda-rc.ble");
}

/* ---- the loop ---- */

static void on_term(int sig) { quit = 1; }
#ifdef CODA_HOST
void crc_host_quit(void) { quit = 1; } /* the tests' SIGTERM: the daemon runs on a thread of theirs */
#endif

/* ms from now to at, 0 when it has passed */
static uint32_t until(uint32_t at, uint32_t now) {
    uint32_t d = at - now;
    return d >= 0x80000000u ? 0 : d;
}

static int position_wanted(void) {
    for (int i = 0; i < MAX_TCP; ++i)
        if (tcp[i].used && tcp[i].s.state == S_AUTHED && tcp[i].s.subscribed & 2) return 1;
    return ble.used && ble.s.state == S_AUTHED && ble.s.subscribed & 2;
}

/* How long the loop's poll may sleep: until the next thing falls due, at most TICK_MS. A socket's bytes and demo's
 * files (renamed into /tmp by its shim: inotify's IN_MOVED_TO) end it sooner. Due: the second's tick, mDNS's next
 * look or announcement, a playing song's position for a phone that follows it, Now Playing's bytes held back,
 * a command's ACK_MS. */
static int wait_ms(uint32_t now, uint32_t last_tick) {
    uint32_t w = until(last_tick + TICK_MS, now), d;
    if ((d = mdns_due(now)) < w) w = d;
    if (np.state == 1 && position_wanted() && (d = until(pos_pushed + 1000, now)) < w) w = d;
    if (np.bhave != np_sent.bhave && (d = until(buf_pushed + 500, now)) < w) w = d;
    if (pend.s && (d = until(pend.at + ACK_MS + 1, now)) < w) w = d;
    if ((d = cover_due(now)) < w) w = d;
    return (int)w;
}

/* demo's shim renames each file into /tmp: a watch on it ends the loop's sleep. Without one (no inotify) the loop
 * looks every TICK_MS. */
static int files_watch(void) {
    char p[256];
    int fd = inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
    if (fd >= 0 && inotify_add_watch(fd, crc_path(p, sizeof p, "/tmp"), IN_MOVED_TO) < 0) close(fd), fd = -1;
    return fd;
}

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

#define ONLINE_REC (CRC_PEER_ID + 1)
#define SEEN "/mnt/data/coda-rc/seen"
#define SEEN_REC (CRC_PEER_ID + 4)

/* Last Connected, for Settings > Car Remote's phone page (carremote_page.c): SEEN holds a record per paired
 * phone, its peer id and the wall clock (little-endian seconds) when a session of it last authenticated or
 * ended. The phones in changed (n ids) take the time now, the others keep theirs, and a phone no longer
 * paired is dropped. Written only when a phone comes or goes, so the flash is not worn. */
static void seen_stamp(const uint8_t *changed, int n) {
    crc_peer peers[CRC_MAX_PEERS];
    uint8_t old[CRC_MAX_PEERS * SEEN_REC], out[CRC_MAX_PEERS * SEEN_REC];
    int np = crc_peers_load(peers, CRC_MAX_PEERS), k = read_file(SEEN, old, sizeof old), m = 0;
    uint32_t now = crc_now_s();
    k = k > 0 ? k / SEEN_REC : 0;
    for (int i = 0; i < np; ++i) {
        uint8_t *r = out + m * SEEN_REC;
        int stamp = 0, kept = 0;
        for (int j = 0; j < n; ++j) stamp |= !memcmp(changed + j * CRC_PEER_ID, peers[i].id, CRC_PEER_ID);
        memcpy(r, peers[i].id, CRC_PEER_ID);
        if (stamp)
            for (int b = 0; b < 4; ++b) r[CRC_PEER_ID + b] = now >> (8 * b);
        for (int j = 0; j < k && !stamp && !kept; ++j)
            if (!memcmp(old + j * SEEN_REC, peers[i].id, CRC_PEER_ID))
                memcpy(r + CRC_PEER_ID, old + j * SEEN_REC + CRC_PEER_ID, 4), kept = 1;
        m += stamp || kept;
    }
    crypto_wipe(peers, sizeof peers);
    crc_write_file(SEEN, out, m * SEEN_REC, 0600);
}

/* id (8 bytes) among the n ONLINE_REC records at set */
static int online_has(const uint8_t *set, int n, const uint8_t *id) {
    for (int i = 0; i < n; ++i)
        if (!memcmp(set + i * ONLINE_REC, id, CRC_PEER_ID)) return 1;
    return 0;
}

/* The paired phones connected now, for Settings > Car Remote's rows (carremote_page.c): a record per
 * authenticated session in /tmp/coda-rc.online, its peer id and its link (0 Wi-Fi, TCP; 1 Bluetooth, BLE),
 * written when the set changes; a phone that came or went is stamped in SEEN. Ids are not secret (every
 * Hello carries one in the clear). */
static void online_poll(void) {
    static uint8_t was[(MAX_TCP + 1) * ONLINE_REC];
    static int was_n = -1;
    uint8_t ids[(MAX_TCP + 1) * ONLINE_REC], changed[(MAX_TCP + 1) * 2 * CRC_PEER_ID];
    int n = 0, nc = 0;
    for (int i = 0; i <= MAX_TCP; ++i) {
        conn *c = i < MAX_TCP ? &tcp[i] : &ble;
        if (!c->used || c->s.state != S_AUTHED) continue;
        memcpy(ids + ONLINE_REC * n, c->s.peer, CRC_PEER_ID);
        ids[ONLINE_REC * n++ + CRC_PEER_ID] = c->s.transport == TR_BLE;
    }
    if (n == was_n && !memcmp(ids, was, n * ONLINE_REC)) return;
    if (crc_write_file("/tmp/coda-rc.online", ids, n * ONLINE_REC, 0644)) return; /* tried again next pass */
    for (int i = 0; i < n; ++i)
        if (!online_has(was, was_n, ids + i * ONLINE_REC)) memcpy(changed + CRC_PEER_ID * nc++, ids + i * ONLINE_REC, CRC_PEER_ID);
    for (int i = 0; i < was_n; ++i)
        if (!online_has(ids, n, was + i * ONLINE_REC)) memcpy(changed + CRC_PEER_ID * nc++, was + i * ONLINE_REC, CRC_PEER_ID);
    if (nc) seen_stamp(changed, nc);
    memcpy(was, ids, n * ONLINE_REC);
    was_n = n;
}

static int already_running(void) {
    char b[16];
    int n = read_file("/tmp/coda-rc.pid", b, sizeof b - 1);
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

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--port") && i + 1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--no-ble")) use_ble = 0;
        else if (!strcmp(argv[i], "--mdns-if") && i + 1 < argc) mdns_if = argv[++i];
        else if (!strcmp(argv[i], "--no-mdns")) mdns_if = 0;
#ifdef CODA_HOST
        else if (!strcmp(argv[i], "--ble-sim") && i + 1 < argc) ble_sim(argv[++i]);
#endif
        else if (!strcmp(argv[i], "--tell-fd") && i + 1 < argc) tell_fd = tell_open(atoi(argv[++i]));
    }
    setpriority(PRIO_PROCESS, 0, 19);
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_term);
    signal(SIGHUP, on_term);
    if (already_running()) return 0;
    char b[16];
    crc_write_file("/tmp/coda-rc.pid", b, snprintf(b, sizeof b, "%d\n", getpid()), 0644);
    device_id();
    unlink_at("/tmp/coda-rc.pair");
    unlink_at("/tmp/coda-rc.ble"); /* a killed daemon's */
    int lfd = tcp_listen();
    crc_log("start: tcp %d %s", port, lfd >= 0 ? "listening" : "failed");
    if (use_ble) ble_start();
    if (mdns_if) mdns_start(mdns_if);
    for (int i = 0; i < MAX_TCP; ++i) tcp[i].fd = -1;
    int wfd = files_watch();
    uint32_t last_tick = crc_now_ms() - TICK_MS;
    while (!quit) {
        struct pollfd fds[MAX_TCP + 3 + BLE_FDS];
        int nf = 0, mfd = mdns_fd();
        if (lfd >= 0) fds[nf++] = (struct pollfd){lfd, POLLIN, 0};
        if (mfd >= 0) fds[nf++] = (struct pollfd){mfd, POLLIN, 0};
        if (wfd >= 0) fds[nf++] = (struct pollfd){wfd, POLLIN, 0};
        int bat = nf, bn = use_ble ? ble_fds(fds + nf) : 0;
        nf += bn;
        int at[MAX_TCP];
        for (int i = 0; i < MAX_TCP; ++i) {
            at[i] = -1;
            if (tcp[i].used) { at[i] = nf; fds[nf++] = (struct pollfd){tcp[i].fd, POLLIN, 0}; }
        }
        int r = poll(fds, nf, wait_ms(crc_now_ms(), last_tick));
        if (r < 0 && errno != EINTR) break;
        int k = 0;
        if (lfd >= 0 && fds[k++].revents & POLLIN) tcp_accept(lfd);
        if (mfd >= 0 && fds[k++].revents & POLLIN) mdns_handle();
        if (wfd >= 0 && fds[k++].revents & POLLIN) { /* which file does not matter: each is read below */
            char ev[512];
            while (read(wfd, ev, sizeof ev) > 0) {}
        }
        if (bn) ble_handle(fds + bat, bn);
        for (int i = 0; i < MAX_TCP; ++i)
            if (at[i] >= 0 && tcp[i].used && fds[at[i]].revents) tcp_read(&tcp[i]);
        np_poll();
        ack_poll();
        cover_poll();
        online_poll();
        uint32_t now = crc_now_ms();
        mdns_poll(now);
        if (now - last_tick >= TICK_MS) {
            last_tick = now;
            crc_tick();
            peers_poll();
            for (int i = 0; i < MAX_TCP; ++i) timeouts(&tcp[i]);
            timeouts(&ble);
            if (use_ble) ble_tick(now);
        }
    }
    crc_log("stop");
    for (int i = 0; i < MAX_TCP; ++i)
        if (tcp[i].used) conn_close(&tcp[i]);
    if (ble.used) conn_close(&ble);
    if (use_ble) ble_stop();
    mdns_stop();
    if (wfd >= 0) close(wfd);
    unlink_at("/tmp/coda-rc.pair");
    unlink_at("/tmp/coda-rc.online");
    unlink_at("/tmp/coda-rc.pid");
    return 0;
}
