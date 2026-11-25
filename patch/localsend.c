/* q2send: LocalSend v2 on the Q2 (docs/localsend.md), the platform under patch/localsend_core.c. One process,
 * started by demo while the LocalSend page or the share sheet is open and ended with SIGTERM:
 * - announces itself on 224.0.0.167:53317 (three times, then on each "scan"), answers other devices'
 *   announcements by registering with them over HTTPS, and lists what it hears;
 * - serves LocalSend's HTTPS API on its port: info, register, prepare-upload (held open until demo's answer,
 *   at most ASK_MS), upload (streamed to a .part file, renamed when whole) and cancel;
 * - sends files to a listed device when demo asks: prepare-upload, then each upload.
 * Its identity is a P-256 key and a self-signed certificate made on the first start and kept in --data; the
 * fingerprint every peer pins is the certificate's SHA-256. It presents the certificate as a client certificate
 * on every request it makes, which the reference app requires.
 *
 * demo talks to it through files in --run (default /tmp), each written whole and renamed into place:
 *   q2send.state     written here: "me", then a "peer" line per device, "recv" and "send" for the transfers
 *   q2send.ask       written here while an incoming request waits: id, alias, model, type, files, bytes
 *   q2send.answer    demo's: "<id> 1" accepts, "<id> 1 trust" accepts and trusts the sender, "<id> 0" declines
 *   q2send.cmd       demo's: "scan", "cancel", "stop", "untrust\t<fingerprint>", "untrust-all", or
 *                    "send\t<fingerprint>" and a path a line
 *   q2send.hold      present: refuse every incoming request (the card is a computer's over USB)
 *   q2send.received  appended here: each file received whole, a path a line, for the library scan
 *   q2send.pid, q2send.log
 * --data also keeps the trusted devices (data/trusted): a sender whose client certificate is one of them is
 * not asked about; the space, name and one-session checks still apply.
 * State lines are tab-separated: "peer fp ip port type alias model", "recv|send phase files_done files bytes_done
 * bytes alias current" (recv adds 1 when its sender is trusted), "trusted fp alias", phase one of idle ask wait run done declined busy failed cancelled refused-room
 * refused-card refused-hold. */
#include "localsend_sys.h"
#include "localsend.h"

#define ASK_MS 60000      /* an incoming request not answered by then is declined */
#define REPLY_MS 600000   /* the most a send waits for the other device's user */
#define IO_S 30           /* a connection silent this long is dropped */
#define MAX_CONNS 8
#define BUF 65536
#define MARGIN (16LL << 20) /* free space left on the card after a receive */
#define LOG_MAX 65536

static struct {
    char run[192], data[192], recv[192];
    ls_info me;
    uint32_t iface;
    SSL_CTX *srv, *cli;
    EVP_PKEY *key;
    X509 *cert;
    pthread_mutex_t mu;
    ls_peer peers[LS_MAX_PEERS];
    int npeers, conns, dirty, logfd;
    volatile int stop;
    /* the incoming session: phase, demo's answer (1 yes, 2 no), its uploads running, what is done */
    ls_session rs;
    char r_phase[16], r_name[LS_PATH];
    int r_answer, r_uploads, r_files, r_trusted;
    long long r_done;
    /* the outgoing one */
    ls_session ss;
    char s_phase[16], s_alias[LS_ALIAS], s_name[LS_PATH];
    int s_cancel, s_files, s_running;
    long long s_done;
    struct {
        char fp[LS_FP];
        uint32_t at;
    } asked[LS_MAX_PEERS];
} g = {.mu = PTHREAD_MUTEX_INITIALIZER};

static uint32_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint32_t)t.tv_sec * 1000u + (uint32_t)(t.tv_nsec / 1000000);
}

static void lg(const char *fmt, ...) {
    char b[400];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof b - 2) n = sizeof b - 2;
    b[n++] = '\n';
    if (g.logfd >= 0 && ls_lseek(g.logfd, 0, SEEK_END) < LOG_MAX) (void)!write(g.logfd, b, n);
}

static void path(char *out, int cap, const char *name) { snprintf(out, cap, "%s/q2send.%s", g.run, name); }

static int put_file(const char *p, const char *text, int n) {
    char tmp[256];
    snprintf(tmp, sizeof tmp, "%s.tmp", p);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return -1;
    int ok = write(fd, text, n) == n;
    close(fd);
    return ok && !rename(tmp, p) ? 0 : (unlink(tmp), -1);
}

static int get_file(const char *p, char *out, int cap) {
    int fd = open(p, O_RDONLY | O_CLOEXEC), n = 0;
    if (fd < 0) return -1;
    for (int k; n < cap - 1 && (k = (int)read(fd, out + n, cap - 1 - n)) > 0;) n += k;
    close(fd);
    out[n] = 0;
    return n;
}

void ls_random(void *buf, size_t n) {
    if (RAND_bytes(buf, (int)n) != 1) {
        lg("RAND_bytes failed");
        exit(3);
    }
}

static void ip_str(uint32_t a, char *out) {
    const unsigned char *b = (const unsigned char *)&a;
    snprintf(out, 16, "%u.%u.%u.%u", b[0], b[1], b[2], b[3]);
}

static int ip_parse(const char *s, uint32_t *out) {
    unsigned char b[4];
    for (int i = 0; i < 4; ++i) {
        char *e;
        unsigned long v = strtoul(s, &e, 10);
        if (e == s || v > 255 || (i < 3 ? *e != '.' : *e != 0)) return -1;
        b[i] = (unsigned char)v;
        s = e + 1;
    }
    memcpy(out, b, 4);
    return 0;
}

/* ---------------------------------------------------------------- identity */

static void sha256(const void *d, size_t n, unsigned char out[32]) {
    EVP_MD_CTX *c = EVP_MD_CTX_new();
    unsigned len = 32;
    EVP_DigestInit_ex(c, EVP_sha256(), 0);
    EVP_DigestUpdate(c, d, n);
    EVP_DigestFinal_ex(c, out, &len);
    EVP_MD_CTX_free(c);
}

static void fp_of(X509 *x, char out[LS_FP]) {
    unsigned char *der = 0, h[32];
    int n = i2d_X509(x, &der);
    out[0] = 0;
    if (n <= 0) return;
    sha256(der, n, h);
    OPENSSL_free(der);
    for (int i = 0; i < 32; ++i) snprintf(out + 2 * i, 3, "%02X", h[i]);
}

static int load_der(const char *name, unsigned char *buf, int cap) {
    char p[256];
    snprintf(p, sizeof p, "%s/%s", g.data, name);
    int fd = open(p, O_RDONLY | O_CLOEXEC), n = 0;
    if (fd < 0) return -1;
    for (int k; n < cap && (k = (int)read(fd, buf + n, cap - n)) > 0;) n += k;
    close(fd);
    return n;
}

static int save_der(const char *name, const unsigned char *d, int n) {
    char p[256], tmp[260];
    snprintf(p, sizeof p, "%s/%s", g.data, name);
    snprintf(tmp, sizeof tmp, "%s.tmp", p);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) return -1;
    int ok = write(fd, d, n) == n && !fsync(fd);
    close(fd);
    return ok && !rename(tmp, p) ? 0 : (unlink(tmp), -1);
}

/* The kept identity, or a new one: P-256, CN=LocalSend User, valid 1975 to 4096 as the app's (written as
 * strings, so neither an unset clock nor a 32-bit time_t enters it). */
static int identity(void) {
    unsigned char kb[512], cb[2048];
    int kn = load_der("key.der", kb, sizeof kb), cn = load_der("cert.der", cb, sizeof cb);
    if (kn > 0 && cn > 0) {
        const unsigned char *p = kb, *q = cb;
        g.key = d2i_AutoPrivateKey(0, &p, kn);
        g.cert = d2i_X509(0, &q, cn);
        if (g.key && g.cert) return 0;
        lg("identity unreadable: made again");
    }
    EC_KEY *ec = EC_KEY_new_by_curve_name(NID_X9_62_prime256v1);
    g.key = EVP_PKEY_new();
    g.cert = X509_new();
    if (!ec || !g.key || !g.cert || !EC_KEY_generate_key(ec) || !EVP_PKEY_assign(g.key, EVP_PKEY_EC, ec)) return -1;
    unsigned char serial[4];
    ls_random(serial, sizeof serial);
    X509_set_version(g.cert, 2);
    ASN1_INTEGER_set(X509_get_serialNumber(g.cert), (long)(serial[0] & 0x7f) << 24 | serial[1] << 16 | serial[2] << 8 | serial[3]);
    if (!ASN1_TIME_set_string(X509_getm_notBefore(g.cert), "19750101000000Z") ||
        !ASN1_TIME_set_string(X509_getm_notAfter(g.cert), "40960101000000Z"))
        return -1;
    X509_NAME *nm = X509_get_subject_name(g.cert);
    X509_NAME_add_entry_by_txt(nm, "CN", MBSTRING_ASC, (const unsigned char *)"LocalSend User", -1, -1, 0);
    X509_set_issuer_name(g.cert, nm);
    X509_set_pubkey(g.cert, g.key);
    if (!X509_sign(g.cert, g.key, EVP_sha256())) return -1;
    unsigned char *kd = 0, *cd = 0;
    kn = i2d_PrivateKey(g.key, &kd);
    cn = i2d_X509(g.cert, &cd);
    mkdir(g.data, 0700);
    int bad = kn <= 0 || cn <= 0 || save_der("key.der", kd, kn) || save_der("cert.der", cd, cn);
    OPENSSL_free(kd);
    OPENSSL_free(cd);
    if (bad) lg("identity not saved in %s: a new one each start", g.data);
    return 0;
}

static int any_cert(int ok, X509_STORE_CTX *c) { return 1; } /* peers are self-signed: pinned by fingerprint */

static SSL_CTX *tls_ctx(int server) {
    SSL_CTX *c = SSL_CTX_new(server ? TLS_server_method() : TLS_client_method());
    if (!c || SSL_CTX_use_certificate(c, g.cert) != 1 || SSL_CTX_use_PrivateKey(c, g.key) != 1) return 0;
    SSL_CTX_set_min_proto_version(c, TLS1_2_VERSION);
    SSL_CTX_set_options(c, SSL_OP_CIPHER_SERVER_PREFERENCE | SSL_OP_PRIORITIZE_CHACHA | SSL_OP_NO_RENEGOTIATION);
    /* the CPU has no AES instructions: ChaCha20 first */
    SSL_CTX_set_ciphersuites(c, "TLS_CHACHA20_POLY1305_SHA256:TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384");
    SSL_CTX_set_cipher_list(c, "ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:"
                               "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256");
    SSL_CTX_set_verify(c, SSL_VERIFY_PEER, any_cert); /* asks for a client certificate; none is no failure */
    /* a peer resumes its session on each request (rustls does); with a client certificate asked for, OpenSSL
     * refuses a resumption without a session id context */
    if (server) SSL_CTX_set_session_id_context(c, (const unsigned char *)"q2send", 6);
    return c;
}

/* ---------------------------------------------------------------- connections */

typedef struct {
    int fd;
    SSL *ssl;
    char ip[16], fp[LS_FP];
    int have; /* bytes in buf past the head */
    unsigned char buf[BUF];
} conn;

static void timeouts(int fd, int s) {
    struct timeval tv = {s, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
}

static void conn_close(conn *c) {
    if (c->ssl) {
        SSL_shutdown(c->ssl);
        SSL_free(c->ssl);
    }
    if (c->fd >= 0) close(c->fd);
    free(c);
}

static int tls_write(conn *c, const void *d, int n) {
    for (int o = 0, k; o < n; o += k)
        if ((k = SSL_write(c->ssl, (const char *)d + o, n - o)) <= 0) return -1;
    return 0;
}

/* Bytes into buf from have on, at most cap; 0 at the end, -1 on an error. */
static int tls_read(conn *c, int cap) {
    int k = SSL_read(c->ssl, c->buf + c->have, cap - c->have);
    return k > 0 ? (c->have += k, k) : k == 0 ? 0 : -1;
}

/* The head into h, the bytes after it left at buf's start. */
static int read_head(conn *c, ls_http *h, int resp) {
    for (;;) {
        int n = c->have ? ls_http_head((char *)c->buf, c->have, h, resp) : 0;
        if (n < 0) return -1;
        if (n) {
            memmove(c->buf, c->buf + n, c->have - n);
            c->have -= n;
            return 0;
        }
        if (c->have >= BUF - 1 || tls_read(c, BUF - 1) <= 0) return -1;
        c->buf[c->have] = 0;
    }
}

/* The body, handed to sink in pieces; cap bounds a body taken whole (0: none). 0, or -1. */
typedef int (*sink_fn)(void *ctx, const unsigned char *d, int n);
static int read_body(conn *c, const ls_http *h, sink_fn sink, void *ctx) {
    ls_chunk ch = {0};
    long long left = h->chunked ? -1 : h->length < 0 ? 0 : h->length;
    for (;;) {
        int n = c->have;
        if (h->chunked) {
            n = ls_chunk_feed(&ch, c->buf, c->have);
            if (n < 0) return -1;
        } else if (n > left)
            n = (int)left;
        if (n && sink(ctx, c->buf, n)) return -1;
        c->have = 0;
        if (!h->chunked) left -= n;
        if (h->chunked ? ls_chunk_done(&ch) : !left) return 0;
        if (tls_read(c, BUF) <= 0) return -1;
    }
}

typedef struct {
    char *b;
    int n, cap;
} mem;
static int mem_sink(void *ctx, const unsigned char *d, int n) {
    mem *m = ctx;
    if (m->n + n >= m->cap) return -1;
    memcpy(m->b + m->n, d, n);
    m->n += n;
    m->b[m->n] = 0;
    return 0;
}

static const char *reason(int code) {
    switch (code) {
    case 200: return "OK";
    case 204: return "No Content";
    case 400: return "Bad Request";
    case 403: return "Forbidden";
    case 404: return "Not Found";
    case 409: return "Conflict";
    case 422: return "Unprocessable Entity";
    default: return "Internal Server Error";
    }
}

static void reply(conn *c, int code, const char *json) {
    char h[192];
    int bn = json ? (int)strlen(json) : 0;
    int n = snprintf(h, sizeof h, "HTTP/1.1 %d %s\r\n%sContent-Length: %d\r\nConnection: close\r\n\r\n", code,
                     reason(code), json ? "Content-Type: application/json\r\n" : "", bn);
    if (!tls_write(c, h, n) && bn) tls_write(c, json, bn);
}

static void phase(char *dst, const char *p) {
    snprintf(dst, 16, "%s", p);
    g.dirty = 1;
}

/* ---------------------------------------------------------------- receiving */

static int ask_answer(void) {
    char p[256], b[160], id[LS_TOKEN + 4];
    path(p, sizeof p, "answer");
    if (get_file(p, b, sizeof b) <= 0) return 0;
    unlink(p);
    char *sp = strchr(b, ' ');
    if (!sp || sp - b >= (int)sizeof id) return 0;
    memcpy(id, b, sp - b);
    id[sp - b] = 0;
    return strcmp(id, g.rs.id) ? 0 : sp[1] == '1' ? (strstr(sp, "trust") ? 3 : 1) : 2;
}

/* ---- trusted devices: data/trusted, "fingerprint\talias" a line. A sender is trusted by its client
 * certificate's fingerprint alone, never by the name or the fingerprint its body claims, which anyone can set. */
#define TRUST_MAX 32
static int trust_read(char fps[][LS_FP], char names[][LS_ALIAS]) {
    char p[256], b[TRUST_MAX * (LS_FP + LS_ALIAS + 2) + 1];
    snprintf(p, sizeof p, "%s/trusted", g.data);
    int n = 0;
    if (get_file(p, b, sizeof b) <= 0) return 0;
    for (char *line = b, *nl; *line && n < TRUST_MAX; line = nl + 1) {
        nl = strchr(line, '\n');
        if (!nl) nl = line + strlen(line);
        char *tab = memchr(line, '\t', nl - line);
        if (tab && tab - line == 64) {
            memcpy(fps[n], line, 64), fps[n][64] = 0;
            int an = (int)(nl - tab - 1) < LS_ALIAS - 1 ? (int)(nl - tab - 1) : LS_ALIAS - 1;
            memcpy(names[n], tab + 1, an), names[n][an] = 0;
            ++n;
        }
        if (!*nl) break;
    }
    return n;
}

static void trust_write(char fps[][LS_FP], char names[][LS_ALIAS], int n) {
    char p[256], b[TRUST_MAX * (LS_FP + LS_ALIAS + 2) + 1];
    int k = 0;
    for (int i = 0; i < n; ++i) k += snprintf(b + k, sizeof b - k, "%s\t%s\n", fps[i], names[i]);
    snprintf(p, sizeof p, "%s/trusted", g.data);
    if (!n) unlink(p);
    else if (put_file(p, b, k)) lg("trusted devices not saved");
    g.dirty = 1;
}

static int trusted(const char *fp) {
    char fps[TRUST_MAX][LS_FP], names[TRUST_MAX][LS_ALIAS];
    int n = trust_read(fps, names);
    for (int i = 0; fp[0] && i < n; ++i)
        if (!strcmp(fps[i], fp)) return 1;
    return 0;
}

/* fp added (alias its name, the newest kept when full), or taken out (alias 0); fp 0 takes out every one */
static void trust_set(const char *fp, const char *alias) {
    char fps[TRUST_MAX][LS_FP], names[TRUST_MAX][LS_ALIAS];
    int n = fp ? trust_read(fps, names) : 0, o = 0;
    for (int i = 0; i < n; ++i)
        if (strcmp(fps[i], fp)) memcpy(fps[o], fps[i], LS_FP), memcpy(names[o], names[i], LS_ALIAS), ++o;
    if (fp && alias) {
        if (o == TRUST_MAX) memmove(fps, fps + 1, --o * LS_FP), memmove(names, names + 1, o * LS_ALIAS);
        snprintf(fps[o], LS_FP, "%s", fp);
        snprintf(names[o], LS_ALIAS, "%s", alias);
        for (char *t = names[o]; *t; ++t)
            if (*t == '\t' || *t == '\n') *t = ' ';
        ++o;
    }
    trust_write(fps, names, o);
}

/* Free bytes where the Received folder is or would be; -1 when the card is not there. */
static long long room(void) {
    char p[192];
    snprintf(p, sizeof p, "%s", g.recv);
    for (;;) {
        ls_vfs v;
        if (!access(p, 0) && !ls_statvfs(p, &v)) return LS_VFS_FREE(v);
        char *s = strrchr(p, '/');
        if (!s || s == p) return -1;
        *s = 0;
    }
}

/* The client went away: its socket reads at its end. */
static int gone(conn *c) {
    struct pollfd p = {c->fd, POLLIN, 0};
    char b;
    return poll(&p, 1, 0) > 0 && (p.revents & (POLLHUP | POLLERR) || recv(c->fd, &b, 1, MSG_PEEK | MSG_DONTWAIT) == 0);
}

static void on_prepare(conn *c, const ls_http *h) {
    mem m = {malloc(4 << 20), 0, 4 << 20};
    if (!m.b || read_body(c, h, mem_sink, &m)) {
        free(m.b);
        return reply(c, 400, 0);
    }
    ls_session s;
    int code = ls_prepare_parse(m.b, m.n, &s);
    free(m.b);
    if (code) return reply(c, code, 0);
    if (c->fp[0]) snprintf(s.from.fp, sizeof s.from.fp, "%s", c->fp); /* who it is: the client certificate */
    pthread_mutex_lock(&g.mu);
    char hold[256];
    path(hold, sizeof hold, "hold");
    long long free_b = room();
    const char *refused = !access(hold, 0) ? "refused-hold" : free_b < 0 ? "refused-card"
                          : free_b < s.total + MARGIN ? "refused-room" : 0;
    if (!strcmp(g.r_phase, "ask") || !strcmp(g.r_phase, "run") || g.r_uploads) {
        pthread_mutex_unlock(&g.mu);
        ls_session_free(&s);
        return reply(c, 409, 0);
    }
    ls_session_free(&g.rs);
    g.rs = s;
    g.r_done = 0, g.r_files = 0, g.r_answer = 0, g.r_name[0] = 0;
    g.r_trusted = c->fp[0] && trusted(c->fp); /* the certificate's, not the body's */
    if (refused || !s.want) {
        phase(g.r_phase, refused ? refused : "done");
        pthread_mutex_unlock(&g.mu);
        lg("prepare-upload from %s: %s", s.from.alias, refused ? refused : "nothing to take");
        return reply(c, refused ? 403 : 204, 0);
    }
    char ask[512], p[256];
    if (g.r_trusted) { /* no card: demo shows "Receiving from <name>" instead (the recv line's trusted flag) */
        char *body = malloc(g.rs.n * 160 + 128);
        int ok = body && ls_prepare_reply(&g.rs, body, g.rs.n * 160 + 128) > 0;
        phase(g.r_phase, ok ? "run" : "failed");
        pthread_mutex_unlock(&g.mu);
        lg("prepare-upload from trusted %s (%s): %d files, %lld bytes", s.from.alias, c->ip, s.want, s.total);
        reply(c, ok ? 200 : 500, ok ? body : 0);
        free(body);
        return;
    }
    phase(g.r_phase, "ask");
    int n = snprintf(ask, sizeof ask, "%s\t%s\t%s\t%s\t%d\t%lld\n", s.id, s.from.alias, s.from.model, s.from.type, s.want,
                     s.total);
    path(p, sizeof p, "ask");
    put_file(p, ask, n);
    pthread_mutex_unlock(&g.mu);
    lg("prepare-upload from %s (%s): %d files, %lld bytes: asking", s.from.alias, c->ip, s.want, s.total);
    uint32_t until = now_ms() + ASK_MS;
    int answer = 0;
    while (!answer && !g.stop) {
        poll(0, 0, 100);
        pthread_mutex_lock(&g.mu);
        if (strcmp(g.r_phase, "ask")) answer = 2; /* cancelled meanwhile */
        else if ((answer = ask_answer()) == 0 && ((int32_t)(now_ms() - until) > 0 || gone(c)))
            answer = 2;
        pthread_mutex_unlock(&g.mu);
    }
    pthread_mutex_lock(&g.mu);
    unlink(p);
    char *body = 0;
    if ((answer == 1 || answer == 3) && !strcmp(g.r_phase, "ask")) {
        if (answer == 3 && c->fp[0]) trust_set(c->fp, g.rs.from.alias); /* "Always accept from this device" */
        body = malloc(g.rs.n * 160 + 128);
        if (body && ls_prepare_reply(&g.rs, body, g.rs.n * 160 + 128) > 0)
            phase(g.r_phase, "run");
        else
            free(body), body = 0, phase(g.r_phase, "failed");
    } else if (!strcmp(g.r_phase, "ask"))
        phase(g.r_phase, "declined");
    pthread_mutex_unlock(&g.mu);
    lg("prepare-upload: %s", body ? "accepted" : "declined");
    reply(c, body ? 200 : 403, body);
    free(body);
}

/* Each folder of the path under the Received folder. */
static void mkdirs(const char *full) {
    char p[LS_PATH + 200];
    snprintf(p, sizeof p, "%s", full);
    for (char *s = p + 1; (s = strchr(s, '/')); ++s) {
        *s = 0;
        mkdir(p, 0755);
        *s = '/';
    }
}

/* A free name for name under the Received folder: "name", then "name (2).ext" on. */
static void unique(const char *name, char *out, int cap) {
    const char *slash = strrchr(name, '/'), *dot = strrchr(name, '.');
    if (dot && (dot < (slash ? slash : name) || dot == (slash ? slash + 1 : name))) dot = 0;
    int stem = dot ? (int)(dot - name) : (int)strlen(name);
    for (int k = 1; k < 1000; ++k) {
        char part[LS_PATH + 220];
        if (k == 1) snprintf(out, cap, "%s/%s", g.recv, name);
        else snprintf(out, cap, "%s/%.*s (%d)%s", g.recv, stem, name, k, dot ? dot : "");
        snprintf(part, sizeof part, "%s.part", out);
        if (access(out, 0) && access(part, 0)) return;
    }
}

typedef struct {
    int fd, idx;
    EVP_MD_CTX *md;
    long long n;
} upload;

static int file_sink(void *ctx, const unsigned char *d, int n) {
    upload *u = ctx;
    if (u->n + n > g.rs.f[u->idx].size || write(u->fd, d, n) != n) return lg("sink: %lld + %d of %lld", u->n, n, g.rs.f[u->idx].size), -1;
    EVP_DigestUpdate(u->md, d, n);
    u->n += n;
    pthread_mutex_lock(&g.mu);
    int live = !strcmp(g.r_phase, "run");
    g.rs.f[u->idx].done = u->n;
    g.r_done += n;
    g.dirty = 1;
    pthread_mutex_unlock(&g.mu);
    if (!live) lg("sink: phase %s", g.r_phase);
    return live ? 0 : -1;
}

static void on_upload(conn *c, const ls_http *h) {
    int idx = -1, code;
    pthread_mutex_lock(&g.mu);
    code = strcmp(g.r_phase, "run") ? 409 : ls_upload_lookup(&g.rs, h->query, &idx);
    char dest[LS_PATH + 220], part[LS_PATH + 230];
    if (!code) {
        g.rs.f[idx].state = LS_F_BUSY;
        g.r_uploads++;
        snprintf(g.r_name, sizeof g.r_name, "%s", g.rs.f[idx].name);
        unique(g.rs.f[idx].name, dest, sizeof dest);
        snprintf(part, sizeof part, "%s.part", dest);
    }
    pthread_mutex_unlock(&g.mu);
    if (code) return reply(c, code, 0);
    mkdirs(part);
    upload u = {open(part, O_WRONLY | O_CREAT | O_EXCL | O_LS_LARGE | O_CLOEXEC, 0644), idx, EVP_MD_CTX_new(), 0};
    EVP_DigestInit_ex(u.md, EVP_sha256(), 0);
    int ok = u.fd >= 0 && !read_body(c, h, file_sink, &u);
    if (!ok) lg("upload: %s at %lld bytes (errno %d)", u.fd < 0 ? "no .part file" : "body ended", u.n, errno);
    if (u.fd >= 0 && close(u.fd)) ok = 0;
    unsigned char dg[32];
    unsigned dl = 32;
    EVP_DigestFinal_ex(u.md, dg, &dl);
    EVP_MD_CTX_free(u.md);
    pthread_mutex_lock(&g.mu);
    ls_file *f = &g.rs.f[idx];
    char hexd[65];
    for (int i = 0; i < 32; ++i) snprintf(hexd + 2 * i, 3, "%02x", dg[i]);
    int whole = ok && u.n == f->size, match = 1;
    if (f->sha[0]) {
        char lo[65];
        for (int i = 0; i < 64 && f->sha[i]; ++i) lo[i] = (char)(f->sha[i] >= 'A' && f->sha[i] <= 'F' ? f->sha[i] + 32 : f->sha[i]);
        lo[64] = 0;
        match = !strncmp(hexd, lo, 64);
    }
    if (whole && match && !rename(part, dest)) {
        f->state = LS_F_DONE;
        code = 200;
        char p[256];
        path(p, sizeof p, "received");
        int fd = open(p, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
        if (fd >= 0) {
            char line[LS_PATH + 224];
            int n = snprintf(line, sizeof line, "%s\n", dest);
            (void)!write(fd, line, n);
            close(fd);
        }
    } else {
        unlink(part);
        f->state = LS_F_FAIL;
        code = whole && !match ? 422 : 400;
    }
    g.r_files++;
    g.r_uploads--;
    int left = 0;
    for (int i = 0; i < g.rs.n; ++i) left += g.rs.f[i].state == LS_F_WANT || g.rs.f[i].state == LS_F_BUSY;
    if (!left && !strcmp(g.r_phase, "run")) {
        int failed = 0;
        for (int i = 0; i < g.rs.n; ++i) failed += g.rs.f[i].state == LS_F_FAIL;
        phase(g.r_phase, failed ? "failed" : "done");
    }
    g.dirty = 1;
    pthread_mutex_unlock(&g.mu);
    lg("upload %s: %d (%lld bytes)", dest, code, u.n);
    reply(c, code, 0);
}

static void on_cancel(conn *c, const ls_http *h) {
    char sid[LS_TOKEN + 4];
    pthread_mutex_lock(&g.mu);
    if (ls_query(h->query, "sessionId", sid, sizeof sid) > 0 && !strcmp(sid, g.rs.id) &&
        (!strcmp(g.r_phase, "ask") || !strcmp(g.r_phase, "run")))
        phase(g.r_phase, "cancelled");
    pthread_mutex_unlock(&g.mu);
    reply(c, 200, 0);
}

static void on_register(conn *c, const ls_http *h) {
    char b[8192];
    mem m = {b, 0, sizeof b};
    ls_info i;
    if (read_body(c, h, mem_sink, &m) || ls_info_text(b, m.n, &i)) return reply(c, 400, 0);
    if (c->fp[0]) snprintf(i.fp, sizeof i.fp, "%s", c->fp);
    pthread_mutex_lock(&g.mu);
    if (ls_peer_seen(g.peers, &g.npeers, &i, c->ip, now_ms(), g.me.fp) >= 0) g.dirty = 1;
    pthread_mutex_unlock(&g.mu);
    char me[512];
    ls_info_json(me, sizeof me, &g.me, 0, -1);
    reply(c, 200, me);
}

static void *serve(void *arg) {
    conn *c = arg;
    ls_http h;
    timeouts(c->fd, 10);
    c->ssl = SSL_new(g.srv);
    if (c->ssl && SSL_set_fd(c->ssl, c->fd) == 1 && SSL_accept(c->ssl) == 1) {
        X509 *x = SSL_get_peer_certificate(c->ssl);
        if (x) fp_of(x, c->fp), X509_free(x);
        timeouts(c->fd, IO_S);
        if (read_head(c, &h, 0))
            lg("%s: no request head (%d bytes)", c->ip, c->have);
        else {
            int post = !strcmp(h.method, "POST");
            if (!strcmp(h.path, "/api/localsend/v2/info")) {
                char me[512];
                ls_info_json(me, sizeof me, &g.me, 0, -1);
                reply(c, 200, me);
            } else if (post && !strcmp(h.path, "/api/localsend/v2/register"))
                on_register(c, &h);
            else if (post && !strcmp(h.path, "/api/localsend/v2/prepare-upload"))
                on_prepare(c, &h);
            else if (post && !strcmp(h.path, "/api/localsend/v2/upload"))
                on_upload(c, &h);
            else if (post && !strcmp(h.path, "/api/localsend/v2/cancel"))
                on_cancel(c, &h);
            else
                reply(c, 404, 0);
        }
    } else
        lg("%s: handshake failed", c->ip);
    conn_close(c);
    pthread_mutex_lock(&g.mu);
    g.conns--;
    pthread_mutex_unlock(&g.mu);
    return 0;
}

/* ---------------------------------------------------------------- the client side */

/* A TLS connection to ip:port, its certificate checked against fp when fp is given; 0 on failure. */
static conn *dial(const char *ip, int port, const char *fp, char *got) {
    struct sockaddr_in a = {AF_INET, htons((uint16_t)port), {0}, {0}};
    if (ip_parse(ip, &a.sin_addr.s_addr)) return 0;
    conn *c = calloc(1, sizeof *c);
    if (!c) return 0;
    c->fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    snprintf(c->ip, sizeof c->ip, "%s", ip);
    int fl = c->fd < 0 ? 0 : fcntl(c->fd, F_GETFL);
    if (c->fd < 0 || fcntl(c->fd, F_SETFL, fl | O_NONBLOCK)) goto fail;
    if (connect(c->fd, (void *)&a, sizeof a) && errno != EINPROGRESS) goto fail;
    struct pollfd p = {c->fd, POLLOUT, 0};
    int err = 0;
    socklen_t el = sizeof err;
    if (poll(&p, 1, 3000) != 1 || getsockopt(c->fd, SOL_SOCKET, SO_ERROR, &err, &el) || err) goto fail;
    fcntl(c->fd, F_SETFL, fl);
    timeouts(c->fd, IO_S);
    c->ssl = SSL_new(g.cli);
    if (!c->ssl || SSL_set_fd(c->ssl, c->fd) != 1 || SSL_connect(c->ssl) != 1) goto fail;
    X509 *x = SSL_get_peer_certificate(c->ssl);
    if (!x) goto fail;
    fp_of(x, c->fp);
    X509_free(x);
    if (got) snprintf(got, LS_FP, "%s", c->fp);
    if (fp && fp[0] && strcmp(fp, c->fp)) {
        lg("%s:%d is not %s (%s)", ip, port, fp, c->fp);
        goto fail;
    }
    return c;
fail:
    conn_close(c);
    return 0;
}

static int request(conn *c, const char *path_q, const char *ctype, long long len) {
    char h[1200];
    int n = snprintf(h, sizeof h, "POST %s HTTP/1.1\r\nHost: %s\r\nContent-Type: %s\r\nContent-Length: %lld\r\n"
                     "Connection: close\r\n\r\n", path_q, c->ip, ctype, len);
    return n < (int)sizeof h ? tls_write(c, h, n) : -1;
}

/* The answer's status and, into body, its text (cap bytes at most); waits up to ms, giving up early when
 * *cancel is set. */
static int answer(conn *c, char *body, int cap, uint32_t ms, int *cancel) {
    uint32_t until = now_ms() + ms;
    struct pollfd p = {c->fd, POLLIN, 0};
    while (!c->have && poll(&p, 1, 200) == 0)
        if ((cancel && *cancel) || g.stop || (int32_t)(now_ms() - until) > 0) return -1;
    ls_http h;
    if (read_head(c, &h, 1)) return -1;
    mem m = {body, 0, cap};
    if (body) body[0] = 0;
    if (body && h.length != 0 && read_body(c, &h, mem_sink, &m)) return -1;
    return h.status;
}

typedef struct {
    char ip[16], fp[LS_FP];
    int port;
} target;

static void *register_with(void *arg) {
    target *t = arg;
    conn *c = dial(t->ip, t->port, 0, 0);
    char me[512], body[4096];
    int n = ls_info_json(me, sizeof me, &g.me, 1, -1);
    ls_info i;
    if (c && !request(c, "/api/localsend/v2/register", "application/json", n) && !tls_write(c, me, n) &&
        answer(c, body, sizeof body, 5000, 0) == 200 && !ls_info_text(body, (int)strlen(body), &i)) {
        snprintf(i.fp, sizeof i.fp, "%s", c->fp);
        i.port = t->port;
        pthread_mutex_lock(&g.mu);
        if (ls_peer_seen(g.peers, &g.npeers, &i, t->ip, now_ms(), g.me.fp) >= 0) g.dirty = 1;
        pthread_mutex_unlock(&g.mu);
        lg("registered with %s at %s:%d", i.alias, t->ip, t->port);
    } else
        lg("register with %s:%d failed", t->ip, t->port);
    if (c) conn_close(c);
    free(t);
    return 0;
}

static const char *mime(const char *name) {
    const char *d = strrchr(name, '.');
    if (!d || strchr(d, '/')) return "application/octet-stream";
    if (!strcmp(d, ".txt") || !strcmp(d, ".log")) return "text/plain";
    if (!strcmp(d, ".flac")) return "audio/flac";
    if (!strcmp(d, ".mp3")) return "audio/mpeg";
    return "application/octet-stream";
}

/* The paths after the command's first line, as the session's files. */
typedef struct {
    char fp[LS_FP], paths[64][256];
    int n;
} job;

static void *send_job(void *arg) {
    job *j = arg;
    conn *c = 0;
    char *body = 0, res[4096];
    const char *end = "failed";
    pthread_mutex_lock(&g.mu);
    int k = ls_peer_find(g.peers, g.npeers, j->fp);
    target t = {{0}, {0}, 0};
    if (k >= 0) snprintf(t.ip, 16, "%s", g.peers[k].ip), t.port = g.peers[k].info.port,
                snprintf(g.s_alias, sizeof g.s_alias, "%s", g.peers[k].info.alias);
    ls_session_free(&g.ss);
    g.ss.f = calloc(j->n ? j->n : 1, sizeof *g.ss.f);
    g.s_done = 0, g.s_files = 0, g.s_name[0] = 0;
    phase(g.s_phase, "wait");
    pthread_mutex_unlock(&g.mu);
    if (k < 0 || !g.ss.f) goto out;
    for (int i = 0; i < j->n; ++i) {
        ls_file *f = &g.ss.f[g.ss.n];
        int fd = open(j->paths[i], O_RDONLY | O_LS_LARGE | O_CLOEXEC);
        if (fd < 0) continue;
        f->size = ls_lseek(fd, 0, SEEK_END);
        close(fd);
        const char *b = strrchr(j->paths[i], '/');
        snprintf(f->name, sizeof f->name, "%s", b ? b + 1 : j->paths[i]);
        snprintf(f->id, sizeof f->id, "q2f%d", i);
        snprintf(f->type, sizeof f->type, "%s", mime(f->name));
        f->state = LS_F_WANT;
        g.ss.total += f->size;
        g.ss.n++;
    }
    int cap = 2048 + g.ss.n * 1200;
    body = malloc(cap);
    int n = body ? ls_prepare_body(body, cap, &g.me, g.ss.f, g.ss.n) : -1;
    if (!g.ss.n || n < 0 || !(c = dial(t.ip, t.port, j->fp, 0)) ||
        request(c, "/api/localsend/v2/prepare-upload", "application/json", n) || tls_write(c, body, n))
        goto out;
    int st = answer(c, res, sizeof res, REPLY_MS, &g.s_cancel);
    conn_close(c), c = 0;
    if (g.s_cancel) {
        end = "cancelled";
        goto out;
    }
    lg("send to %s: prepare-upload %d", g.s_alias, st);
    if (st == 204) end = "done";
    if (st == 403) end = "declined";
    if (st == 409) end = "busy";
    if (st != 200 || ls_prepare_answer(res, (int)strlen(res), &g.ss)) goto out;
    pthread_mutex_lock(&g.mu);
    phase(g.s_phase, "run");
    pthread_mutex_unlock(&g.mu);
    for (int i = 0; i < g.ss.n; ++i) {
        ls_file *f = &g.ss.f[i];
        if (f->state != LS_F_WANT) continue;
        char q[512];
        snprintf(q, sizeof q, "/api/localsend/v2/upload?sessionId=%s&fileId=%s&token=%s", g.ss.id, f->id, f->token);
        int fd = open(j->paths[i], O_RDONLY | O_LS_LARGE | O_CLOEXEC);
        pthread_mutex_lock(&g.mu);
        snprintf(g.s_name, sizeof g.s_name, "%s", f->name);
        pthread_mutex_unlock(&g.mu);
        if (fd < 0 || !(c = dial(t.ip, t.port, j->fp, 0)) || request(c, q, "application/octet-stream", f->size)) {
            if (fd >= 0) close(fd);
            goto out;
        }
        long long sent = 0;
        for (int r; sent < f->size && (r = (int)read(fd, c->buf, BUF)) > 0; sent += r) {
            if (g.s_cancel || g.stop || tls_write(c, c->buf, r)) break;
            pthread_mutex_lock(&g.mu);
            g.s_done += r;
            g.dirty = 1;
            pthread_mutex_unlock(&g.mu);
        }
        close(fd);
        st = sent == f->size ? answer(c, 0, 0, 60000, &g.s_cancel) : -1;
        conn_close(c), c = 0;
        if (g.s_cancel) {
            end = "cancelled";
            goto out;
        }
        if (st != 200) {
            lg("upload %s: %d", f->name, st);
            goto out;
        }
        f->state = LS_F_DONE;
        pthread_mutex_lock(&g.mu);
        g.s_files++;
        g.dirty = 1;
        pthread_mutex_unlock(&g.mu);
    }
    end = "done";
out:
    if (!strcmp(end, "cancelled") && g.ss.id[0] && (c = dial(t.ip, t.port, j->fp, 0))) {
        char q[160];
        snprintf(q, sizeof q, "/api/localsend/v2/cancel?sessionId=%s", g.ss.id);
        if (!request(c, q, "application/json", 0)) answer(c, 0, 0, 3000, 0);
    }
    if (c) conn_close(c);
    free(body);
    pthread_mutex_lock(&g.mu);
    phase(g.s_phase, end);
    g.s_running = 0;
    pthread_mutex_unlock(&g.mu);
    lg("send to %s: %s", g.s_alias, end);
    free(j);
    return 0;
}

/* ---------------------------------------------------------------- the loop */

static void spawn(void *(*fn)(void *), void *arg) {
    pthread_t t;
    if (pthread_create(&t, 0, fn, arg)) {
        free(arg);
        return;
    }
    pthread_detach(t);
}

static void announce(int udp) {
    char msg[512];
    int n = ls_info_json(msg, sizeof msg, &g.me, 1, 1);
    struct sockaddr_in a = {AF_INET, htons(LS_PORT), {0}, {0}};
    ip_parse(LS_GROUP, &a.sin_addr.s_addr);
    if (n > 0 && sendto(udp, msg, n, 0, (void *)&a, sizeof a) != n) lg("announce failed");
}

static void heard(int udp) {
    char b[2048], ip[16];
    struct sockaddr_in from;
    socklen_t fl = sizeof from;
    int n = (int)recvfrom(udp, b, sizeof b - 1, 0, (void *)&from, &fl);
    ls_info i;
    if (n <= 0 || ls_info_text(b, n, &i) || !strcmp(i.fp, g.me.fp) || !i.https) return;
    ip_str(from.sin_addr.s_addr, ip);
    pthread_mutex_lock(&g.mu);
    uint32_t now = now_ms();
    if (i.announce == 1) {
        /* an announcement: register with it, at most once every 3 s a device (each sends three) */
        int k = 0, old = 0;
        for (; k < LS_MAX_PEERS && strcmp(g.asked[k].fp, i.fp); ++k)
            if ((int32_t)(g.asked[k].at - g.asked[old].at) < 0) old = k;
        if (k == LS_MAX_PEERS) k = old, snprintf(g.asked[k].fp, LS_FP, "%s", i.fp), g.asked[k].at = now - 10000;
        if (now - g.asked[k].at > 3000) {
            g.asked[k].at = now;
            target *t = calloc(1, sizeof *t);
            if (t) snprintf(t->ip, 16, "%s", ip), t->port = i.port, spawn(register_with, t);
        }
    } else if (ls_peer_seen(g.peers, &g.npeers, &i, ip, now, g.me.fp) >= 0)
        g.dirty = 1; /* an older app's answer by multicast */
    pthread_mutex_unlock(&g.mu);
}

static void write_state(void) {
    char b[6144], p[256];
    int n = snprintf(b, sizeof b, "me\t%s\t%s\t%d\n", g.me.fp, g.me.alias, g.me.port);
    for (int i = 0; i < g.npeers && n < (int)sizeof b - 300; ++i)
        n += snprintf(b + n, sizeof b - n, "peer\t%s\t%s\t%d\t%s\t%s\t%s\n", g.peers[i].info.fp, g.peers[i].ip,
                      g.peers[i].info.port, g.peers[i].info.type, g.peers[i].info.alias, g.peers[i].info.model);
    n += snprintf(b + n, sizeof b - n, "recv\t%s\t%d\t%d\t%lld\t%lld\t%s\t%s\t%d\n", g.r_phase[0] ? g.r_phase : "idle",
                  g.r_files, g.rs.want, g.r_done, g.rs.total, g.rs.from.alias, g.r_name, g.r_trusted);
    char fps[TRUST_MAX][LS_FP], names[TRUST_MAX][LS_ALIAS];
    for (int i = 0, t = trust_read(fps, names); i < t && n < (int)sizeof b - 200; ++i)
        n += snprintf(b + n, sizeof b - n, "trusted\t%s\t%s\n", fps[i], names[i]);
    n += snprintf(b + n, sizeof b - n, "send\t%s\t%d\t%d\t%lld\t%lld\t%s\t%s\n", g.s_phase[0] ? g.s_phase : "idle",
                  g.s_files, g.ss.n, g.s_done, g.ss.total, g.s_alias, g.s_name);
    path(p, sizeof p, "state");
    if (n < (int)sizeof b) put_file(p, b, n);
}

static void command(int udp, uint32_t *burst) {
    char p[256];
    path(p, sizeof p, "cmd");
    char *b = malloc(64 * 256 + 128);
    int n = b ? get_file(p, b, 64 * 256 + 128) : -1;
    if (n <= 0) return free(b);
    unlink(p);
    if (!strncmp(b, "scan", 4)) burst[0] = now_ms(), burst[1] = 1;
    else if (!strncmp(b, "stop", 4))
        g.stop = 1;
    else if (!strncmp(b, "untrust-all", 11)) {
        pthread_mutex_lock(&g.mu);
        trust_set(0, 0);
        pthread_mutex_unlock(&g.mu);
    } else if (!strncmp(b, "untrust\t", 8)) {
        char *nl = strchr(b, '\n');
        if (nl) *nl = 0;
        pthread_mutex_lock(&g.mu);
        if (strlen(b + 8) == 64) trust_set(b + 8, 0);
        pthread_mutex_unlock(&g.mu);
    } else if (!strncmp(b, "cancel", 6)) {
        pthread_mutex_lock(&g.mu);
        g.s_cancel = 1;
        if (!strcmp(g.r_phase, "ask") || !strcmp(g.r_phase, "run")) phase(g.r_phase, "cancelled");
        pthread_mutex_unlock(&g.mu);
    } else if (!strncmp(b, "send\t", 5)) {
        job *j = calloc(1, sizeof *j);
        char *line = b + 5, *nl = strchr(line, '\n');
        if (!j || !nl || nl - line >= LS_FP) return free(j), free(b);
        memcpy(j->fp, line, nl - line);
        for (line = nl + 1; *line && j->n < 64; line = nl + 1) {
            nl = strchr(line, '\n');
            if (!nl) nl = line + strlen(line);
            if (nl > line && nl - line < 256) memcpy(j->paths[j->n++], line, nl - line);
            if (!*nl) break;
        }
        pthread_mutex_lock(&g.mu);
        int busy = g.s_running;
        if (!busy) g.s_running = 1, g.s_cancel = 0;
        pthread_mutex_unlock(&g.mu);
        if (busy) free(j);
        else spawn(send_job, j);
    }
    free(b);
}

static void on_signal(int s) { g.stop = 1; }

static int arg(int argc, char **argv, int *i, const char *name, char *out, int cap) {
    if (strcmp(argv[*i], name) || *i + 1 >= argc) return 0;
    snprintf(out, cap, "%s", argv[++*i]);
    return 1;
}

int main(int argc, char **argv) {
    char port[8] = "53317", iface[16] = "";
    snprintf(g.run, sizeof g.run, "/tmp");
    snprintf(g.data, sizeof g.data, "/mnt/data/q2send");
    snprintf(g.recv, sizeof g.recv, "/mnt/mmc/Received");
    snprintf(g.me.alias, sizeof g.me.alias, "Vamp");
    snprintf(g.me.model, sizeof g.me.model, "Vamp");
    snprintf(g.me.type, sizeof g.me.type, "headless");
    for (int i = 1; i < argc; ++i)
        if (!arg(argc, argv, &i, "--run", g.run, sizeof g.run) && !arg(argc, argv, &i, "--data", g.data, sizeof g.data) &&
            !arg(argc, argv, &i, "--recv", g.recv, sizeof g.recv) && !arg(argc, argv, &i, "--port", port, sizeof port) &&
            !arg(argc, argv, &i, "--alias", g.me.alias, sizeof g.me.alias) &&
            !arg(argc, argv, &i, "--iface", iface, sizeof iface))
            return 2;
    g.me.port = (int)strtoul(port, 0, 10);
    if (iface[0] && ip_parse(iface, &g.iface)) return 2;
    char p[256];
    path(p, sizeof p, "log");
    g.logfd = open(p, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);
    uint32_t t0 = now_ms();
    if (identity() || !(g.srv = tls_ctx(1)) || !(g.cli = tls_ctx(0))) {
        lg("no TLS identity");
        return 3;
    }
    fp_of(g.cert, g.me.fp);
    g.me.https = 1;
    lg("q2send %s as %s (identity %u ms)", g.me.fp, g.me.alias, now_ms() - t0);
    int one = 1, tcp = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0), udp = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    struct sockaddr_in a = {AF_INET, htons((uint16_t)g.me.port), {INADDR_ANY}, {0}};
    setsockopt(tcp, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    if (tcp < 0 || bind(tcp, (void *)&a, sizeof a) || listen(tcp, 16)) {
        lg("port %d: bind failed", g.me.port);
        return 4;
    }
    struct sockaddr_in m = {AF_INET, htons(LS_PORT), {INADDR_ANY}, {0}};
    struct ip_mreq mr = {{0}, {g.iface}};
    ip_parse(LS_GROUP, &mr.imr_multiaddr.s_addr);
    setsockopt(udp, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    setsockopt(udp, SOL_SOCKET, SO_REUSEPORT, &one, sizeof one);
    if (udp < 0 || bind(udp, (void *)&m, sizeof m) || setsockopt(udp, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mr, sizeof mr))
        lg("multicast unavailable: found only by register"); /* still served: an app can scan the subnet */
    if (g.iface) setsockopt(udp, IPPROTO_IP, IP_MULTICAST_IF, &g.iface, sizeof g.iface);
    path(p, sizeof p, "pid");
    char pid[16];
    put_file(p, pid, snprintf(pid, sizeof pid, "%d\n", getpid()));
    path(p, sizeof p, "ask");
    unlink(p);
    g.dirty = 1;
    /* the announcement burst, as the app's: 100 ms, 500 ms and 2 s after the start or a scan */
    static const uint32_t at[] = {100, 500, 2000};
    uint32_t burst[2] = {now_ms(), 0}, tick = 0;
    while (!g.stop) {
        struct pollfd fds[2] = {{tcp, POLLIN, 0}, {udp, POLLIN, 0}};
        int r = poll(fds, udp >= 0 ? 2 : 1, 100);
        if (r > 0 && fds[0].revents & POLLIN) {
            struct sockaddr_in from;
            socklen_t fl = sizeof from;
            int fd = accept(tcp, (void *)&from, &fl);
            pthread_mutex_lock(&g.mu);
            int ok = fd >= 0 && g.conns < MAX_CONNS;
            if (ok) g.conns++;
            pthread_mutex_unlock(&g.mu);
            conn *c = ok ? calloc(1, sizeof *c) : 0;
            if (c) {
                c->fd = fd;
                ip_str(from.sin_addr.s_addr, c->ip);
                spawn(serve, c);
            } else if (fd >= 0) {
                close(fd);
                if (ok) {
                    pthread_mutex_lock(&g.mu);
                    g.conns--;
                    pthread_mutex_unlock(&g.mu);
                }
            }
        }
        if (r > 0 && udp >= 0 && fds[1].revents & POLLIN) heard(udp);
        uint32_t now = now_ms();
        while (burst[1] < 3 && now - burst[0] >= at[burst[1]]) announce(udp), burst[1]++;
        if (now - tick >= 250) {
            tick = now;
            command(udp, burst);
            pthread_mutex_lock(&g.mu);
            if (ls_peer_expire(g.peers, &g.npeers, now)) g.dirty = 1;
            if (g.dirty) write_state(), g.dirty = 0;
            pthread_mutex_unlock(&g.mu);
        }
    }
    lg("stopping");
    pthread_mutex_lock(&g.mu);
    if (!strcmp(g.r_phase, "ask") || !strcmp(g.r_phase, "run")) phase(g.r_phase, "cancelled");
    g.s_cancel = 1;
    pthread_mutex_unlock(&g.mu);
    for (int k = 0; k < 30 && (g.conns || g.s_running); ++k) poll(0, 0, 100); /* uploads see the cancel */
    for (const char *f = "state\0ask\0pid\0"; *f; f += strlen(f) + 1) {
        path(p, sizeof p, f);
        unlink(p);
    }
    return 0;
}
