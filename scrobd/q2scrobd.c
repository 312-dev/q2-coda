/* q2scrobd: VampOS's scrobble log (docs/vamp-os-services.md#scrobbling). It watches Q2PLAY/1 (/run/vamp/play.sock)
 * and, for each song heard long enough, appends a line to the card's /mnt/mmc/.scrobbler.log in Rockbox's
 * AudioScrobbler 1.1 format, as today's Vamp does (docs/internals.md#scrobbling), for any .scrobbler.log uploader.
 *
 * A song is a now line's id (a new play when the id changes or pos goes back to 0); the seconds heard grow while
 * state=1 and pos moves on by 1 to 3 s (a seek or a pause adds nothing). A listen: half the song or 240 s, whichever is
 * less, for a song over 30 s; with [SCROBBLE] PERCENT=N in /mnt/mmc/.scrobble.ini, N % of the song less 2 s. Once a
 * play. The artist (or with [SCROBBLE] ALBUM_ARTIST=1 the album artist, when there is one) and the track number come
 * from the song's library row (Library 1: library.db's songtable, catalogue.db's servertable); the title, artist and
 * album otherwise from the now line. No artist, or a clock before 2020: not logged. When Q2PLAY/1 is not there it
 * tries again every 2 s. --root prefixes every path, for test/scrobd.py.
 *
 * `q2scrobd upload` sends the log to ListenBrainz and Last.fm (below); the daemon starts it by itself with
 * [SCROBBLE] AUTO=1, by today's rule. */
#define _GNU_SOURCE
#include <ctype.h>
#include <stdint.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <poll.h>
#include <signal.h>
#include <sqlite3.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <mbedtls/ctr_drbg.h>
#include <mbedtls/entropy.h>
#include <mbedtls/md5.h>
#include <mbedtls/net_sockets.h>
#include <mbedtls/ssl.h>
#include <mbedtls/x509_crt.h>
#include <psa/crypto.h>

#define LISTEN_MIN_S 30
#define LISTEN_MAX_S 240
#define CAT_ID_BASE 0x40000000u
#define T 512

static char root[256];
static volatile sig_atomic_t stopping;

static char *path(const char *rel) {
    static char b[2][700];
    static int i;
    i ^= 1;
    snprintf(b[i], sizeof b[i], "%s%s", root, rel);
    return b[i];
}

static void on_term(int s) { (void)s, stopping = 1; }

static int hexv(int c) { return isdigit(c) ? c - '0' : isxdigit(c) ? (tolower(c) - 'a' + 10) : -1; }

/* a now field's value, percent-decoded (docs/platform/README.md#the-common-shape) */
static void field(const char *line, const char *key, char *out, size_t room) {
    char k[32];
    snprintf(k, sizeof k, " %s=", key);
    const char *p = strstr(line, k);
    size_t n = 0;
    if (p) {
        for (p += strlen(k); *p && *p != ' ' && n + 1 < room; ++p) {
            if (*p == '%' && hexv(p[1]) >= 0 && hexv(p[2]) >= 0) out[n++] = (char)(hexv(p[1]) << 4 | hexv(p[2])), p += 2;
            else out[n++] = *p;
        }
    }
    out[n] = 0;
}

static int config_int(const char *section, const char *key) { /* .scrobble.ini's [section] key=, 0 when missing */
    FILE *f = fopen(path("/mnt/mmc/.scrobble.ini"), "r");
    char line[300], sec[64] = "";
    int v = 0;
    while (f && fgets(line, sizeof line, f)) {
        char *s = line + strspn(line, " \t");
        s[strcspn(s, "\r\n")] = 0;
        if (*s == '[') snprintf(sec, sizeof sec, "%.*s", (int)strcspn(s + 1, "]"), s + 1);
        else if (!strcasecmp(sec, section) && !strncasecmp(s, key, strlen(key)) && s[strlen(key)] == '=') v = atoi(s + strlen(key) + 1);
    }
    if (f) fclose(f);
    return v;
}

/* the library row's album artist and track for id (0 and "" when none) */
static void row_of(int id, char *aa, size_t room, int *track) {
    sqlite3 *db;
    sqlite3_stmt *st;
    int server = (unsigned)id >= CAT_ID_BASE;
    aa[0] = 0, *track = 0;
    if (sqlite3_open_v2(path(server ? "/mnt/mmc/.vamp/catalogue.db" : "/mnt/data/vamp/library.db"), &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK &&
        sqlite3_prepare_v2(db, server ? "select albumsonger, track from servertable where id = ?" : "select albumsonger, track from songtable where id = ?",
                           -1, &st, 0) == SQLITE_OK) {
        sqlite3_bind_int(st, 1, id);
        if (sqlite3_step(st) == SQLITE_ROW) {
            const char *a = (const char *)sqlite3_column_text(st, 0);
            snprintf(aa, room, "%s", a ? a : "");
            *track = sqlite3_column_int(st, 1);
        }
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
}

/* ---- the upload (docs/vamp-os-services.md#scrobbling), patch/scrobble.c's port ----
 * The log's listened lines go to ListenBrainz and Last.fm, in batches of BATCH, over TLS verified against the
 * image's /etc/scrobble-ca.pem (mbedTLS); a batch leaves the log (for .scrobbler.log.sent) only once every service
 * with an account took it. Accounts in /mnt/mmc/.scrobble.ini: [LISTENBRAINZ] TOKEN; [LASTFM] API_KEY, API_SECRET
 * and SESSION_KEY (tools/lastfm_key.py), or USER and PASSWORD, which fetch a session key once an upload. The log is
 * locked (flock on /tmp/q2scrobd.lock) while it is read or rewritten, as the daemon locks it to append. */
#define BATCH 50              /* Last.fm's limit; ListenBrainz takes 100 */
#define LINE_BYTES 2560       /* Rockbox's longest line, as scrobble.c reads it */
#define BODY_MAX (384 << 10)  /* 50 lines of LINE_BYTES, URL-encoded, and the names */
#define LOG_HEAD (1 << 20)    /* a window of the log: its whole lines; the rest waits for the next */
#define CONNECT_S 15
#define REPLY_S 60
static const char *lb_url = "https://api.listenbrainz.org/1/submit-listens", *fm_url = "https://ws.audioscrobbler.com/2.0/";
static const char HEADER[] = "#AUDIOSCROBBLER/1.1\n#TZ/UTC\n#CLIENT/Vamp\n";

static int log_lock(void) { /* the lock's fd, held until closed */
    int fd = open(path("/tmp/q2scrobd.lock"), O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (fd >= 0) flock(fd, LOCK_EX);
    return fd;
}

/* .scrobble.ini's [section] key's text, its spaces trimmed; "" when missing */
static void config_str(const char *section, const char *key, char *out, size_t room) {
    FILE *f = fopen(path("/mnt/mmc/.scrobble.ini"), "r");
    char line[600], sec[64] = "";
    out[0] = 0;
    while (f && fgets(line, sizeof line, f)) {
        char *s = line + strspn(line, " \t"), *eq;
        s[strcspn(s, "\r\n")] = 0;
        if (*s == '[') snprintf(sec, sizeof sec, "%.*s", (int)strcspn(s + 1, "]"), s + 1);
        else if (!strcasecmp(sec, section) && (eq = strchr(s, '='))) {
            char *k = s, *ke = eq, *v = eq + 1;
            while (ke > k && (ke[-1] == ' ' || ke[-1] == '\t')) --ke;
            if ((size_t)(ke - k) != strlen(key) || strncasecmp(k, key, strlen(key))) continue;
            v += strspn(v, " \t");
            size_t n = strlen(v);
            while (n && (v[n - 1] == ' ' || v[n - 1] == '\t')) --n;
            snprintf(out, room, "%.*s", (int)n, v);
        }
    }
    if (f) fclose(f);
}

enum { LB_TOKEN, FM_USER, FM_PASS, FM_KEY, FM_SECRET, FM_SK, CONFIG_N };
static char cfg[CONFIG_N][256];

/* bit 0 ListenBrainz, bit 1 Last.fm (its API pair, and a session key or the user and password) */
static int accounts(void) {
    static const char *const keys[] = {"TOKEN", "USER", "PASSWORD", "API_KEY", "API_SECRET", "SESSION_KEY"};
    for (int i = 0; i < CONFIG_N; ++i) config_str(i ? "LASTFM" : "LISTENBRAINZ", keys[i], cfg[i], sizeof cfg[i]);
    return (cfg[LB_TOKEN][0] != 0) | (cfg[FM_KEY][0] && cfg[FM_SECRET][0] && (cfg[FM_SK][0] || (cfg[FM_USER][0] && cfg[FM_PASS][0]))) << 1;
}

typedef struct { char *p; size_t n, cap; } buf_t; /* n == cap: overflowed */
typedef struct { char *artist, *album, *title, *length, *time; } entry_t;
typedef struct { char name[16]; const char *value; } param_t;

/* mode 0 raw, 1 URL-encoded, 2 a JSON string's contents (control bytes become spaces) */
static void put(buf_t *b, const char *s, int mode) {
    static const char hex[] = "0123456789ABCDEF";
    for (; s && *s; s++) {
        unsigned char c = (unsigned char)*s;
        char e[3] = {(char)c};
        unsigned k = 1;
        if (mode == 1 && !((c | 32) >= 'a' && (c | 32) <= 'z') && !(c >= '0' && c <= '9') && c != '-' && c != '_' && c != '.' && c != '~')
            e[0] = '%', e[1] = hex[c >> 4], e[2] = hex[c & 15], k = 3;
        else if (mode == 2 && (c == '"' || c == '\\')) e[0] = '\\', e[1] = (char)c, k = 2;
        else if (mode == 2 && c < 32) e[0] = ' ';
        if (b->n + k >= b->cap) { b->n = b->cap; return; }
        for (unsigned i = 0; i < k; i++) b->p[b->n++] = e[i];
        b->p[b->n] = 0;
    }
}

/* a line's time: 1 to 10 digits, not all zero */
static int epoch(const char *s) {
    int nonzero = 0, n = 0;
    for (; *s; ++s, ++n) {
        if (*s < '0' || *s > '9') return 0;
        nonzero |= *s != '0';
    }
    return nonzero && n <= 10;
}

/* One Rockbox line: artist, album, title, track, length, rating, time, MusicBrainz id. Only listened (L) lines with
 * an artist, a title and a time of digits are sent. */
static int parse(char *line, entry_t *e) {
    char *f[8] = {line};
    int n = 1;
    if (strlen(line) > LINE_BYTES) return 0;
    for (char *s = line; *s && n < 8; s++)
        if (*s == '\t') *s = 0, f[n++] = s + 1;
    e->artist = f[0], e->album = f[1], e->title = f[2], e->length = f[4], e->time = f[6];
    return n >= 7 && *f[0] && *f[2] && f[5][0] == 'L' && epoch(f[6]);
}

/* ---- HTTPS: one POST a connection, the server's name and chain checked ---- */

static int tcp_connect(const char *host, const char *port) {
    struct addrinfo hints = {.ai_family = AF_UNSPEC, .ai_socktype = SOCK_STREAM}, *res, *a;
    int fd = -1;
    if (getaddrinfo(host, port, &hints, &res)) return -1;
    for (a = res; a && fd < 0; a = a->ai_next) {
        fd = socket(a->ai_family, a->ai_socktype | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
        if (fd < 0) continue;
        struct pollfd p = {.fd = fd, .events = POLLOUT};
        int err = 0;
        socklen_t el = sizeof err;
        if ((connect(fd, a->ai_addr, a->ai_addrlen) && errno != EINPROGRESS) || poll(&p, 1, CONNECT_S * 1000) != 1 ||
            getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &el) || err)
            close(fd), fd = -1;
    }
    freeaddrinfo(res);
    if (fd >= 0) fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) & ~O_NONBLOCK);
    return fd;
}

/* mbedTLS's I/O on the socket (the image's mbedTLS has no net module): fd in *ctx, a read waiting REPLY_S at most */
static int bio_send(void *ctx, const unsigned char *b, size_t n) {
    ssize_t w = send(*(int *)ctx, b, n, MSG_NOSIGNAL);
    return w >= 0 ? (int)w : errno == EINTR ? MBEDTLS_ERR_SSL_WANT_WRITE : MBEDTLS_ERR_NET_SEND_FAILED;
}

static int bio_recv(void *ctx, unsigned char *b, size_t n, uint32_t ms) {
    struct pollfd p = {.fd = *(int *)ctx, .events = POLLIN};
    int r = poll(&p, 1, ms ? (int)ms : -1);
    if (r == 0) return MBEDTLS_ERR_SSL_TIMEOUT;
    if (r < 0) return errno == EINTR ? MBEDTLS_ERR_SSL_WANT_READ : MBEDTLS_ERR_NET_RECV_FAILED;
    ssize_t got = recv(p.fd, b, n, 0);
    return got >= 0 ? (int)got : errno == EINTR ? MBEDTLS_ERR_SSL_WANT_READ : MBEDTLS_ERR_NET_RECV_FAILED;
}

/* POST body to url (https:// only); the HTTP status, 0 when no answer; the answer's body in reply */
static long post(const char *url, const char *body, const char *auth, char *reply, size_t room) {
    char host[256], port[8] = "443", where[512];
    const char *h = url + 8, *slash, *colon;
    long code = 0;
    reply[0] = 0;
    if (strncmp(url, "https://", 8) || !(slash = strchr(h, '/'))) return 0;
    colon = memchr(h, ':', (size_t)(slash - h));
    snprintf(host, sizeof host, "%.*s", (int)((colon ? colon : slash) - h), h);
    if (colon) snprintf(port, sizeof port, "%.*s", (int)(slash - colon - 1), colon + 1);
    snprintf(where, sizeof where, "%s", slash);
    mbedtls_ssl_context ssl;
    mbedtls_ssl_config conf;
    mbedtls_x509_crt ca;
    mbedtls_ctr_drbg_context drbg;
    mbedtls_entropy_context ent;
    int fd = -1;
    mbedtls_ssl_init(&ssl), mbedtls_ssl_config_init(&conf), mbedtls_x509_crt_init(&ca), mbedtls_ctr_drbg_init(&drbg);
    mbedtls_entropy_init(&ent);
    psa_crypto_init();
    char *req = 0;
    int n = asprintf(&req, "POST %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: Vamp\r\nConnection: close\r\n%s%sContent-Type: %s\r\n"
                           "Content-Length: %zu\r\n\r\n%s", where, host, auth ? auth : "", auth ? "\r\n" : "",
                     auth ? "application/json" : "application/x-www-form-urlencoded", strlen(body), body);
    if (n < 0) req = 0;
    int ok = req && !mbedtls_ctr_drbg_seed(&drbg, mbedtls_entropy_func, &ent, (const unsigned char *)"q2scrobd", 8) &&
             mbedtls_x509_crt_parse_file(&ca, path("/etc/scrobble-ca.pem")) >= 0 &&
             !mbedtls_ssl_config_defaults(&conf, MBEDTLS_SSL_IS_CLIENT, MBEDTLS_SSL_TRANSPORT_STREAM, MBEDTLS_SSL_PRESET_DEFAULT);
    if (ok) {
        mbedtls_ssl_conf_authmode(&conf, MBEDTLS_SSL_VERIFY_REQUIRED); /* credentials go only over verified TLS */
        mbedtls_ssl_conf_ca_chain(&conf, &ca, 0);
        mbedtls_ssl_conf_rng(&conf, mbedtls_ctr_drbg_random, &drbg);
        mbedtls_ssl_conf_read_timeout(&conf, REPLY_S * 1000);
        ok = !mbedtls_ssl_setup(&ssl, &conf) && !mbedtls_ssl_set_hostname(&ssl, host) && (fd = tcp_connect(host, port)) >= 0;
    }
    if (ok) {
        mbedtls_ssl_set_bio(&ssl, &fd, bio_send, 0, bio_recv);
        int r;
        while ((r = mbedtls_ssl_handshake(&ssl)) == MBEDTLS_ERR_SSL_WANT_READ || r == MBEDTLS_ERR_SSL_WANT_WRITE) {
        }
        if (r) fprintf(stderr, "q2scrobd: %s: TLS refused (-0x%x)\n", host, (unsigned)-r), ok = 0;
    }
    for (size_t off = 0; ok && off < (size_t)n;) {
        int w = mbedtls_ssl_write(&ssl, (const unsigned char *)req + off, (size_t)n - off);
        if (w > 0) off += (size_t)w;
        else if (w != MBEDTLS_ERR_SSL_WANT_WRITE) ok = 0;
    }
    if (ok) { /* the answer whole, up to the room: the status line's code, the body after the headers */
        char *all = malloc(65537);
        size_t got = 0;
        int r;
        while (all && got < 65536 && ((r = mbedtls_ssl_read(&ssl, (unsigned char *)all + got, 65536 - got)) > 0 ||
                                      r == MBEDTLS_ERR_SSL_WANT_READ))
            if (r > 0) got += (size_t)r;
        if (all) {
            all[got] = 0;
            char *b = strstr(all, "\r\n\r\n");
            if (!strncmp(all, "HTTP/1.", 7) && strlen(all) > 12) code = strtol(all + 9, 0, 10);
            if (b) snprintf(reply, room, "%s", b + 4);
        }
        free(all);
        mbedtls_ssl_close_notify(&ssl);
    }
    free(req);
    if (fd >= 0) close(fd);
    mbedtls_ssl_free(&ssl), mbedtls_ssl_config_free(&conf), mbedtls_x509_crt_free(&ca);
    mbedtls_ctr_drbg_free(&drbg), mbedtls_entropy_free(&ent);
    return code;
}

static char reply[4096];

static int listenbrainz(const entry_t *e, int n, buf_t *b) {
    char auth[300];
    b->n = 0;
    put(b, "{\"listen_type\":\"import\",\"payload\":[", 0);
    for (int i = 0; i < n; i++) {
        put(b, i ? ",{\"listened_at\":" : "{\"listened_at\":", 0);
        put(b, e[i].time, 0);
        put(b, ",\"track_metadata\":{\"artist_name\":\"", 0);
        put(b, e[i].artist, 2);
        put(b, "\",\"track_name\":\"", 0);
        put(b, e[i].title, 2);
        if (*e[i].album) put(b, "\",\"release_name\":\"", 0), put(b, e[i].album, 2);
        put(b, "\",\"additional_info\":{\"submission_client\":\"Vamp\"}}}", 0);
    }
    put(b, "]}", 0);
    snprintf(auth, sizeof auth, "Authorization: Token %s", cfg[LB_TOKEN]);
    return b->n < b->cap && post(lb_url, b->p, auth, reply, sizeof reply) == 200;
}

static void add(param_t *p, int *n, const char *name, int i, const char *value) {
    if (!value || !*value) return;
    snprintf(p[*n].name, sizeof p->name, i < 0 ? "%s" : "%s[%d]", name, i);
    p[(*n)++].value = value;
}

static int by_name(const void *a, const void *b) { return strcmp(((const param_t *)a)->name, ((const param_t *)b)->name); }

/* a signed Last.fm call: api_sig is the MD5 of the sorted names and values, then the secret */
static int lastfm(param_t *p, int n, buf_t *b) {
    mbedtls_md5_context md;
    unsigned char d[16];
    char sig[33];
    add(p, &n, "api_key", -1, cfg[FM_KEY]);
    qsort(p, (size_t)n, sizeof *p, by_name);
    mbedtls_md5_init(&md), mbedtls_md5_starts(&md);
    b->n = 0;
    for (int i = 0; i < n; i++) {
        mbedtls_md5_update(&md, (const unsigned char *)p[i].name, strlen(p[i].name));
        mbedtls_md5_update(&md, (const unsigned char *)p[i].value, strlen(p[i].value));
        put(b, p[i].name, 1), put(b, "=", 0), put(b, p[i].value, 1), put(b, "&", 0);
    }
    mbedtls_md5_update(&md, (const unsigned char *)cfg[FM_SECRET], strlen(cfg[FM_SECRET]));
    mbedtls_md5_finish(&md, d), mbedtls_md5_free(&md);
    for (int i = 0; i < 16; i++) snprintf(sig + 2 * i, 3, "%02x", d[i]);
    put(b, "api_sig=", 0), put(b, sig, 0), put(b, "&format=json", 0);
    return b->n < b->cap && post(fm_url, b->p, 0, reply, sizeof reply) == 200 && !strstr(reply, "\"error\"");
}

/* auth.getMobileSession: a session key, once an upload, when the file has none */
static int session(char *sk, buf_t *b) {
    param_t p[5];
    int n = 0;
    add(p, &n, "method", -1, "auth.getMobileSession");
    add(p, &n, "username", -1, cfg[FM_USER]);
    add(p, &n, "password", -1, cfg[FM_PASS]);
    const char *k = lastfm(p, n, b) ? strstr(reply, "\"key\":\"") : 0;
    unsigned i = 0;
    for (; k && k[7 + i] && k[7 + i] != '"' && i < 63; i++) sk[i] = k[7 + i];
    sk[i] = 0;
    return i > 0;
}

static int scrobble_lastfm(const entry_t *e, int n, const char *sk, buf_t *b) {
    param_t p[4 + 5 * BATCH];
    int m = 0;
    add(p, &m, "method", -1, "track.scrobble");
    add(p, &m, "sk", -1, sk);
    for (int i = 0; i < n; i++) {
        add(p, &m, "artist", i, e[i].artist);
        add(p, &m, "track", i, e[i].title);
        add(p, &m, "album", i, e[i].album);
        add(p, &m, "timestamp", i, e[i].time);
        if (atoi(e[i].length) > 0) add(p, &m, "duration", i, e[i].length);
    }
    return lastfm(p, m, b);
}

/* path's first bytes, at most max */
static char *read_head(const char *p, size_t max, size_t *size) {
    FILE *f = fopen(p, "rb");
    char *b = f ? calloc(1, max + 1) : 0;
    *size = b ? fread(b, 1, max, f) : 0;
    if (f) fclose(f);
    return b;
}

/* The log's first done bytes' listens to .sent, the rest kept under the header: the new log written and synced first,
 * so a full card fails before .sent gains anything; .sent synced before the rename drops them. */
static int retire(size_t done) {
    int lk = log_lock();
    char *p = calloc(1, done + 1), chunk[4096], tmp[700];
    snprintf(tmp, sizeof tmp, "%s.tmp", path("/mnt/mmc/.scrobbler.log"));
    FILE *in = p ? fopen(path("/mnt/mmc/.scrobbler.log"), "rb") : 0;
    FILE *f = in && fread(p, 1, done, in) == done ? fopen(tmp, "wb") : 0;
    int ok = f != 0;
    if (f) {
        ok = fwrite(HEADER, sizeof HEADER - 1, 1, f) == 1;
        for (size_t k; ok && (k = fread(chunk, 1, sizeof chunk, in));) ok = fwrite(chunk, k, 1, f) == 1;
        if (ferror(in) || fflush(f) || fsync(fileno(f))) ok = 0;
        if (fclose(f)) ok = 0;
    }
    if (in) fclose(in);
    FILE *sent = ok ? fopen(path("/mnt/mmc/.scrobbler.log.sent"), "ab") : 0;
    ok = sent != 0;
    for (size_t i = 0, j; ok && i < done; i = j) {
        for (j = i; j < done && p[j++] != '\n';) {
        }
        if (p[i] != '#') ok = fwrite(p + i, j - i, 1, sent) == 1;
    }
    if (sent) {
        if (fflush(sent) || fsync(fileno(sent))) ok = 0;
        if (fclose(sent)) ok = 0;
    }
    if (ok) ok = !rename(tmp, path("/mnt/mmc/.scrobbler.log"));
    if (!ok) unlink(tmp);
    free(p);
    if (lk >= 0) close(lk);
    return ok;
}

/* q2scrobd upload: 0 when every listen went (or there was none), 1 when a batch failed, 2 no account */
static int upload(void) {
    entry_t e[BATCH];
    char got[64] = "";
    int on = accounts(), sent = 0;
    if (!on) return fprintf(stderr, "q2scrobd: no account in .scrobble.ini: nothing sent\n"), 2;
    int lb = on & 1, fm = !!(on & 2);
    const char *sk = cfg[FM_SK][0] ? cfg[FM_SK] : got;
    buf_t b = {calloc(1, BODY_MAX), 0, BODY_MAX};
    int ok = b.p != 0;
    for (int more = 1; ok && more;) {
        size_t done = 0, size;
        int lk = log_lock();
        char *log = read_head(path("/mnt/mmc/.scrobbler.log"), LOG_HEAD, &size);
        if (lk >= 0) close(lk);
        for (size_t at = 0; ok && log;) {
            int n = 0;
            while (n < BATCH && at < size) { /* complete lines only */
                size_t j = at;
                while (j < size && log[j] != '\n') j++;
                if (j == size) break;
                log[j] = 0;
                if (log[at] != '#' && parse(log + at, &e[n])) n++;
                at = j + 1;
            }
            if (!n) { /* a cut line waits; in a full window, one longer than any real line is junk */
                done = size == LOG_HEAD && size - at > LINE_BYTES ? size : at;
                break;
            }
            ok = (!fm || *sk || session(got, &b)) && (!lb || listenbrainz(e, n, &b)) && (!fm || scrobble_lastfm(e, n, sk, &b));
            if (ok) done = at, sent += n;
        }
        free(log);
        more = size == LOG_HEAD && done;
        if (done && !retire(done)) ok = 0;
    }
    free(b.p);
    fprintf(stderr, "q2scrobd: %d scrobbles sent%s\n", sent, ok ? "" : "; a batch failed, kept for the next upload");
    return ok ? 0 : 1;
}

/* ---- the automatic upload ([SCROBBLE] AUTO=1), scrobble_ui.c's rule ----
 * On Wi-Fi the log goes up by itself once its oldest unsent listen is SA_MAX_MIN old, SA_STEP_MIN sooner per listen
 * since, at least every SA_MIN_MIN; a failed one tries again after SA_RETRY_MIN. Wi-Fi is asked of Q2NET/1 (woken
 * when it is off), at most once a minute; the radio is held while the upload runs. */
#define SA_MAX_MIN 180
#define SA_MIN_MIN 60
#define SA_STEP_MIN 4
#define SA_RETRY_MIN 30
#define HOLD_S 300
static long long minute_ms = 60000; /* --minute-ms, for test/scrobd.py */
static struct { int pending; long long first_at, check_at; pid_t child; } sa;

static long long mono_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static long long due_ms(int pending) {
    int min = SA_MAX_MIN - SA_STEP_MIN * pending;
    return (min < SA_MIN_MIN ? SA_MIN_MIN : min) * minute_ms;
}

static void listened(void) {
    if (!sa.pending++) sa.first_at = mono_ms();
}

/* one Q2NET/1 command; the first answer line in out */
static int net_ask(const char *cmd, char *out, size_t room) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/net.sock"));
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    char line[200], buf[600];
    out[0] = 0;
    int n = snprintf(line, sizeof line, "Q2NET/1\n%s\n", cmd);
    if (fd < 0 || connect(fd, (struct sockaddr *)&a, sizeof a) || write(fd, line, (size_t)n) != n) {
        if (fd >= 0) close(fd);
        return -1;
    }
    struct timeval tv = {.tv_sec = 3};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    size_t got = 0;
    ssize_t r;
    while (got < sizeof buf - 1 && (r = read(fd, buf + got, sizeof buf - 1 - got)) > 0) {
        got += (size_t)r, buf[got] = 0;
        char *nl1 = strchr(buf, '\n'), *nl2 = nl1 ? strchr(nl1 + 1, '\n') : 0;
        if (nl2) { snprintf(out, room, "%.*s", (int)(nl2 - nl1 - 1), nl1 + 1); break; }
    }
    close(fd);
    return out[0] ? 0 : -1;
}

static void auto_tick(void) {
    long long now = mono_ms();
    if (sa.child > 0) {
        int st;
        if (waitpid(sa.child, &st, WNOHANG) != sa.child) return;
        sa.child = 0;
        if (WIFEXITED(st) && WEXITSTATUS(st) == 0) sa.pending = 0;
        else sa.first_at = now - due_ms(sa.pending) + SA_RETRY_MIN * minute_ms;
        return;
    }
    if (!sa.pending || now - sa.first_at < due_ms(sa.pending) || now - sa.check_at < minute_ms) return;
    sa.check_at = now;
    if (config_int("SCROBBLE", "AUTO") != 1 || !accounts()) { /* off, or no account: the card again later */
        sa.first_at = now - due_ms(sa.pending) + SA_RETRY_MIN * minute_ms;
        return;
    }
    char st[300], hold[32];
    if (net_ask("status", st, sizeof st) || !strstr(st, "state=connected")) {
        net_ask("wake", st, sizeof st); /* the radio up; the next minute's look sends it */
        return;
    }
    snprintf(hold, sizeof hold, "hold %d", HOLD_S);
    net_ask(hold, st, sizeof st);
    sa.child = fork();
    if (sa.child == 0) {
        execl("/proc/self/exe", "q2scrobd", "upload", "--root", root, "--listenbrainz", lb_url, "--lastfm", fm_url, (char *)0);
        _exit(127);
    }
    if (sa.child < 0) sa.child = 0;
}

static char *tag(char *o, const char *s) { /* a column: tabs and line ends made spaces, then the tab */
    for (; *s; ++s) *o++ = *s == '\t' || *s == '\n' || *s == '\r' ? ' ' : *s;
    *o++ = '\t';
    return o;
}

static struct { int id, pos, heard, len, sent; char title[T], artist[T], album[T]; } s = {.id = -1};

static void log_listen(void) {
    char aa[T], line[4 * T + 64], *o = line;
    int track;
    time_t now = time(0);
    row_of(s.id, aa, sizeof aa, &track);
    const char *artist = config_int("SCROBBLE", "ALBUM_ARTIST") == 1 && *aa ? aa : s.artist;
    if (!*artist) return (void)fprintf(stderr, "q2scrobd: %s has no artist: not logged\n", s.title);
    if (now < 1577836800) return (void)fprintf(stderr, "q2scrobd: the clock is not set: not logged\n");
    int lk = log_lock(); /* an upload's rewrite of the log waits, or this does */
    FILE *f = fopen(path("/mnt/mmc/.scrobbler.log"), "a");
    if (!f) {
        if (lk >= 0) close(lk);
        return (void)fprintf(stderr, "q2scrobd: .scrobbler.log: %s\n", strerror(errno));
    }
    if (ftell(f) == 0) fputs(HEADER, f);
    o = tag(o, artist), o = tag(o, s.album), o = tag(o, s.title);
    if (track > 0) o += sprintf(o, "%d", track);
    o += sprintf(o, "\t%d\tL\t%lld\t\n", s.len, (long long)(now - s.heard));
    fwrite(line, 1, (size_t)(o - line), f);
    fclose(f);
    if (lk >= 0) close(lk);
    listened();
    fprintf(stderr, "q2scrobd: logged %s by %s\n", s.title, artist);
}

static void now_line(const char *line) {
    char v[T];
    field(line, "id", v, sizeof v);
    int id = atoi(v), pos, state, len;
    field(line, "pos", v, sizeof v), pos = atoi(v);
    field(line, "state", v, sizeof v), state = atoi(v);
    field(line, "len", v, sizeof v), len = atoi(v);
    if (id != s.id || (pos < s.pos && pos <= 1)) { /* a new play */
        s.id = id, s.heard = 0, s.sent = 0;
    } else if (state == 1 && pos - s.pos >= 1 && pos - s.pos <= 3) {
        s.heard += pos - s.pos;
    }
    s.pos = pos, s.len = len;
    field(line, "title", s.title, sizeof s.title), field(line, "artist", s.artist, sizeof s.artist), field(line, "album", s.album, sizeof s.album);
    int pct = config_int("SCROBBLE", "PERCENT"), listen = len / 2 < LISTEN_MAX_S ? len / 2 : LISTEN_MAX_S;
    if (pct > 0) listen = (len - 2) * pct / 100;
    if (!s.sent && id && len > LISTEN_MIN_S && s.heard >= listen) s.sent = 1, log_listen();
}

int main(int argc, char **argv) {
    int up = argc > 1 && !strcmp(argv[1], "upload");
    for (int i = 1 + up; i < argc; ++i)
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--minute-ms") && i + 1 < argc) minute_ms = atoll(argv[++i]);
        else if (!strcmp(argv[i], "--listenbrainz") && i + 1 < argc) lb_url = argv[++i]; /* a test's stand-in */
        else if (!strcmp(argv[i], "--lastfm") && i + 1 < argc) fm_url = argv[++i];
        else return fprintf(stderr, "usage: q2scrobd [upload] [--root DIR] [--minute-ms N]\n"), 2;
    if (up) return upload();
    if (minute_ms < 1) minute_ms = 1;
    struct sigaction sa = {.sa_handler = on_term};
    sigaction(SIGTERM, &sa, 0), sigaction(SIGINT, &sa, 0);
    signal(SIGPIPE, SIG_IGN);
    while (!stopping) {
        struct sockaddr_un a = {.sun_family = AF_UNIX};
        snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/play.sock"));
        int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (fd < 0 || connect(fd, (struct sockaddr *)&a, sizeof a) || write(fd, "Q2PLAY/1\nwatch\nnow\n", 19) != 19) {
            if (fd >= 0) close(fd);
            sleep(2);
            continue;
        }
        char buf[8192];
        size_t n = 0;
        while (!stopping) {
            struct pollfd pf = {.fd = fd, .events = POLLIN};
            auto_tick();
            if (poll(&pf, 1, 1000) <= 0) continue; /* the automatic upload's look, at least once a second */
            ssize_t got = read(fd, buf + n, sizeof buf - 1 - n);
            if (got <= 0) break;
            n += (size_t)got, buf[n] = 0;
            char *p = buf, *nl;
            while ((nl = strchr(p, '\n'))) {
                *nl = 0;
                if (!strncmp(p, "now ", 4)) now_line(p + 3);
                p = nl + 1;
            }
            n = strlen(p), memmove(buf, p, n + 1);
            if (n >= sizeof buf - 1) n = 0;
        }
        close(fd);
        if (!stopping) auto_tick(), sleep(2);
    }
    return 0;
}
