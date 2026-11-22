/* q2dbus: the system bus's A2DP transport signals, parsed and read (docs/internals.md#bluetooth-event-log).
 * See q2dbus.h. Only the wire format's parts these messages use: little-endian, the header's fields,
 * strings, object paths, signatures, arrays, dict entries and variants. */
#include "q2dbus.h"

#ifdef Q2GUARD_HOST
#include <errno.h>
#include <poll.h>
#include <sys/socket.h>
#define ERRNO errno
#else
#define ERRNO (*__errno_location())
#endif
#define DBUS_EAGAIN 11
#define DBUS_EINTR 4

/* ---- writing ---- */
struct w {
    unsigned char *p;
    int n, size;
};
static void put(struct w *w, const void *s, int k) {
    if (w->n + k <= w->size) memcpy(w->p + w->n, s, (size_t)k);
    w->n += k;
}
static void pad(struct w *w, int a) {
    static const unsigned char z[8];
    put(w, z, (a - w->n % a) % a);
}
static void put_u32(struct w *w, unsigned v) {
    pad(w, 4);
    unsigned char b[4] = { (unsigned char)v, (unsigned char)(v >> 8), (unsigned char)(v >> 16),
                           (unsigned char)(v >> 24) };
    put(w, b, 4);
}
static void put_str(struct w *w, const char *s) { /* s or o */
    int k = (int)strlen(s);
    put_u32(w, (unsigned)k), put(w, s, k + 1);
}
/* a header field: its code, then a variant of one basic type */
static void field(struct w *w, int code, char type, const char *v) {
    unsigned char h[4] = { (unsigned char)code, 1, (unsigned char)type, 0 };
    pad(w, 8), put(w, h, 4);
    if (type == 'g') {
        unsigned char k = (unsigned char)strlen(v);
        put(w, &k, 1), put(w, v, k + 1);
    } else
        put_str(w, v);
}

int dbus_auth(char *out, int size, unsigned uid) {
    char dec[12], hex[25];
    int k = snprintf(dec, sizeof dec, "%u", uid);
    for (int i = 0; i < k; ++i) snprintf(hex + 2 * i, 3, "%02x", (unsigned char)dec[i]);
    hex[2 * k] = 0;
    out[0] = 0;
    return 1 + snprintf(out + 1, (size_t)size - 1, "AUTH EXTERNAL %s\r\n", hex);
}

int dbus_call(unsigned char *out, int size, unsigned serial, const char *member, const char *arg) {
    struct w w = { out, 0, size };
    unsigned char h[4] = { 'l', 1 /* a method call */, 0, 1 };
    put(&w, h, 4);
    put_u32(&w, 0); /* the body's length, below */
    put_u32(&w, serial);
    put_u32(&w, 0); /* the fields' length, below */
    int fields = w.n;
    field(&w, 1, 'o', "/org/freedesktop/DBus");
    field(&w, 6, 's', "org.freedesktop.DBus");
    field(&w, 2, 's', "org.freedesktop.DBus");
    field(&w, 3, 's', member);
    if (arg) field(&w, 8, 'g', "s");
    unsigned flen = (unsigned)(w.n - fields);
    pad(&w, 8);
    int body = w.n;
    if (arg) put_str(&w, arg);
    if (w.n > size) return 0;
    unsigned blen = (unsigned)(w.n - body);
    memcpy(out + 4, &blen, 4), memcpy(out + 12, &flen, 4); /* little-endian, as the host's and the Q2's */
    return w.n;
}

/* ---- reading ---- */
struct r {
    const unsigned char *p;
    int n, i, bad;
};
static unsigned u32_at(const unsigned char *p) {
    return (unsigned)p[0] | (unsigned)p[1] << 8 | (unsigned)p[2] << 16 | (unsigned)p[3] << 24;
}
static void align(struct r *r, int a) {
    r->i += (a - r->i % a) % a;
    if (r->i > r->n) r->bad = 1;
}
static unsigned get_u32(struct r *r) {
    align(r, 4);
    if (r->bad || r->i + 4 > r->n) return r->bad = 1, 0;
    unsigned v = u32_at(r->p + r->i);
    r->i += 4;
    return v;
}
/* a string or object path: its text in place (NUL ended in the message), 0 bad */
static const char *get_str(struct r *r) {
    unsigned k = get_u32(r);
    if (r->bad || k > (unsigned)(r->n - r->i - 1) || r->p[r->i + k]) return r->bad = 1, (const char *)0;
    const char *s = (const char *)r->p + r->i;
    r->i += (int)k + 1;
    return s;
}
static const char *get_sig(struct r *r) {
    if (r->i >= r->n) return r->bad = 1, (const char *)0;
    int k = r->p[r->i];
    if (r->i + 1 + k >= r->n || r->p[r->i + 1 + k]) return r->bad = 1, (const char *)0;
    const char *s = (const char *)r->p + r->i + 1;
    r->i += k + 2;
    return s;
}
static int alignment(char c) {
    switch (c) {
    case 'y': case 'g': case 'v': return 1;
    case 'n': case 'q': return 2;
    case 'x': case 't': case 'd': case '(': case '{': return 8;
    default: return 4;
    }
}
/* the end of the one complete type at sig, 0 when it is not one */
static const char *type_end(const char *s) {
    if (!*s) return 0;
    if (*s == 'a') return type_end(s + 1);
    if (*s == '(' || *s == '{') {
        char close = *s == '(' ? ')' : '}';
        ++s;
        while (*s && *s != close)
            if (!(s = type_end(s))) return 0;
        return *s ? s + 1 : 0;
    }
    return s + 1;
}
/* one value of the type at sig passed over; the depth bounds a hostile nesting */
static void skip(struct r *r, const char *sig, int depth) {
    if (r->bad || depth > 32) return (void)(r->bad = 1);
    switch (*sig) {
    case 'y': r->i += 1; break;
    case 'n': case 'q': align(r, 2), r->i += 2; break;
    case 'b': case 'i': case 'u': case 'h': align(r, 4), r->i += 4; break;
    case 'x': case 't': case 'd': align(r, 8), r->i += 8; break;
    case 's': case 'o': get_str(r); break;
    case 'g': get_sig(r); break;
    case 'v': {
        const char *s = get_sig(r);
        if (s && type_end(s) && !*type_end(s)) skip(r, s, depth + 1);
        else r->bad = 1;
        break;
    }
    case 'a': {
        unsigned k = get_u32(r);
        align(r, alignment(sig[1]));
        if (k > (unsigned)(r->n - r->i)) r->bad = 1;
        else r->i += (int)k;
        break;
    }
    case '(': case '{': {
        align(r, 8);
        const char *e = type_end(sig);
        if (!e) return (void)(r->bad = 1);
        for (const char *s = sig + 1; s < e - 1 && !r->bad; s = type_end(s)) skip(r, s, depth + 1);
        break;
    }
    default: r->bad = 1;
    }
    if (r->i > r->n) r->bad = 1;
}

int dbus_size(const unsigned char *p, int n) {
    if (n < 16) return 0;
    if ((p[0] != 'l' && p[0] != 'B') || p[3] != 1) return -1;
    unsigned flen = u32_at(p + 12), blen = u32_at(p + 4);
    if (p[0] == 'B') /* big-endian: only its size is read, to pass it over */
        flen = (unsigned)p[12] << 24 | (unsigned)p[13] << 16 | (unsigned)p[14] << 8 | p[15],
        blen = (unsigned)p[4] << 24 | (unsigned)p[5] << 16 | (unsigned)p[6] << 8 | p[7];
    if (flen > 1 << 26 || blen > 1 << 26) return -1;
    return (int)((16 + flen + 7) / 8 * 8 + blen);
}

static int starts(const char *s, const char *w) { return !strncmp(s, w, strlen(w)); }

/* the device and the rest from /org/bluez/hciN/dev_04_00_6E_D3_96_6D/sep1/fd0 */
static int path_device(const char *path, struct dbus_tr *t) {
    if (!starts(path, "/org/bluez/")) return 0;
    const char *d = strstr(path, "/dev_");
    if (!d || strlen(d) < 22) return 0;
    d += 5;
    for (int i = 0; i < 17; ++i) {
        char c = d[i];
        if (i % 3 == 2) {
            if (c != '_') return 0;
            t->addr[i] = ':';
        } else {
            int hex = (c >= '0' && c <= '9') || (c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f');
            if (!hex) return 0;
            t->addr[i] = c >= 'a' ? (char)(c - 32) : c;
        }
    }
    t->addr[17] = 0;
    for (int b = 0; b < 6; ++b) {
        int v = 0;
        for (int k = 0; k < 2; ++k) {
            char c = t->addr[3 * b + k];
            v = v * 16 + (c <= '9' ? c - '0' : c - 'A' + 10);
        }
        t->bdaddr[5 - b] = (unsigned char)v;
    }
    snprintf(t->tail, sizeof t->tail, "%s", d + 17);
    return 1;
}

int dbus_transport(const unsigned char *p, int n, struct dbus_tr *t) {
    memset(t, 0, sizeof *t);
    int size = dbus_size(p, n);
    if (size <= 0 || size > n || p[0] != 'l' || p[1] != 4 /* a signal */) return 0;
    struct r r = { p, size, 16, 0 };
    int fend = 16 + (int)u32_at(p + 12);
    const char *path = 0, *iface = 0, *member = 0, *sig = "";
    while (r.i < fend && !r.bad) {
        align(&r, 8);
        if (r.i >= fend) break;
        int code = r.p[r.i++];
        const char *vs = get_sig(&r);
        if (!vs || !vs[0] || vs[1]) return 0;
        if (*vs == 's' || *vs == 'o') {
            const char *v = get_str(&r);
            if (code == 1) path = v;
            if (code == 2) iface = v;
            if (code == 3) member = v;
        } else if (*vs == 'g') {
            const char *v = get_sig(&r);
            if (code == 8 && v) sig = v;
        } else
            skip(&r, vs, 0);
    }
    if (r.bad || !path || !iface || !member) return 0;
    r.n = size; /* the body, from its 8-byte boundary */
    r.i = fend;
    align(&r, 8);
    if (!strcmp(iface, "org.freedesktop.DBus.Properties") && !strcmp(member, "PropertiesChanged") &&
        !strcmp(sig, "sa{sv}as")) {
        const char *in = get_str(&r);
        if (!in || strcmp(in, DBUS_TRANSPORT) || !path_device(path, t)) return 0;
        unsigned k = get_u32(&r);
        align(&r, 8);
        int end = r.i + (int)k;
        if (r.bad || end > r.n) return 0;
        while (r.i < end && !r.bad) {
            align(&r, 8);
            const char *key = get_str(&r), *vs = get_sig(&r);
            if (!key || !vs) return 0;
            if (!strcmp(key, "State") && !strcmp(vs, "s")) {
                const char *v = get_str(&r);
                if (!v) return 0;
                snprintf(t->state, sizeof t->state, "%s", v);
                t->kind = DBUS_TR_STATE;
            } else if (type_end(vs) && !*type_end(vs))
                skip(&r, vs, 0);
            else
                return 0;
        }
        return !r.bad && t->kind == DBUS_TR_STATE;
    }
    if (!strcmp(iface, "org.freedesktop.DBus.ObjectManager") &&
        ((!strcmp(member, "InterfacesAdded") && !strcmp(sig, "oa{sa{sv}}")) ||
         (!strcmp(member, "InterfacesRemoved") && !strcmp(sig, "oas")))) {
        int added = member[10] == 'A'; /* InterfacesAdded */
        const char *obj = get_str(&r);
        if (!obj || !path_device(obj, t)) return 0;
        unsigned k = get_u32(&r);
        align(&r, added ? 8 : 4);
        int end = r.i + (int)k;
        if (r.bad || end > r.n) return 0;
        while (r.i < end && !r.bad) {
            if (added) align(&r, 8);
            const char *name = get_str(&r);
            if (!name) return 0;
            if (!strcmp(name, DBUS_TRANSPORT)) t->kind = added ? DBUS_TR_ADDED : DBUS_TR_REMOVED;
            if (added) skip(&r, "a{sv}", 0);
        }
        return !r.bad && t->kind != DBUS_TR_NONE;
    }
    return 0;
}

char dbus_tr_line(const struct dbus_tr *t, const char *name, char *out, int size) {
    char who[96];
    if (name && *name)
        snprintf(who, sizeof who, "%s \"%s\"", t->addr, name);
    else
        snprintf(who, sizeof who, "%s", t->addr);
    const char *what = "";
    if (t->kind == DBUS_TR_ADDED) what = "opened: A2DP stream configured";
    if (t->kind == DBUS_TR_REMOVED) what = "closed";
    if (t->kind == DBUS_TR_STATE)
        what = !strcmp(t->state, "active")    ? "active: AVDTP started"
               : !strcmp(t->state, "pending") ? "pending: the device asked to start, not yet acquired"
               : !strcmp(t->state, "idle")    ? "idle: AVDTP suspended or not started"
                                              : t->state;
    snprintf(out, (size_t)size, "transport %s %s %s", who, what, t->tail);
    return 'I';
}

/* ---- the connection ---- */
#ifndef AF_UNIX
#define AF_UNIX 1
#endif
struct un_addr { /* sockaddr_un */
    unsigned short family;
    char path[108];
};

void dbus_init(struct bt_dbus *d) {
    memset(d, 0, sizeof *d);
    d->fd = -1;
}

static int send_all(int fd, const void *b, int n) {
    return (int)send(fd, b, (size_t)n, MSG_NOSIGNAL) == n;
}

static int dbus_open(const char *path) {
    struct un_addr a = { AF_UNIX, { 0 } };
    snprintf(a.path, sizeof a.path, "%s", path);
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0) return -1;
    char b[160];
    int n = dbus_auth(b, sizeof b, (unsigned)getuid());
    if (connect(fd, (const void *)&a, sizeof a) || !send_all(fd, b, n)) return close(fd), -1;
    /* "OK <guid>\r\n", waited for */
    int got = 0;
    for (int waited = 0; got < (int)sizeof b - 1 && waited < DBUS_AUTH_MS; waited += 50) {
        struct pollfd p = { fd, POLLIN, 0 };
        if (poll(&p, 1, 50) <= 0) continue;
        int k = (int)read(fd, b + got, sizeof b - 1 - (size_t)got);
        if (k <= 0) return close(fd), -1;
        got += k, b[got] = 0;
        if (strstr(b, "\r\n")) break;
    }
    if (got < 3 || strncmp(b, "OK ", 3)) return close(fd), -1;
    static const char *const rules[] = {
        "type='signal',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged',"
        "arg0='" DBUS_TRANSPORT "'",
        "type='signal',interface='org.freedesktop.DBus.ObjectManager',member='InterfacesAdded',"
        "arg0path='/org/bluez/'",
        "type='signal',interface='org.freedesktop.DBus.ObjectManager',member='InterfacesRemoved',"
        "arg0path='/org/bluez/'",
    };
    unsigned char m[1024];
    n = 0;
    memcpy(m, "BEGIN\r\n", 7), n = 7;
    n += dbus_call(m + n, (int)sizeof m - n, 1, "Hello", 0);
    for (int i = 0; i < 3; ++i) n += dbus_call(m + n, (int)sizeof m - n, (unsigned)i + 2, "AddMatch", rules[i]);
    if (!send_all(fd, m, n)) return close(fd), -1;
    return fd;
}

int dbus_ok(struct bt_dbus *d, const char *path, unsigned now) {
    if (d->fd >= 0) return 1;
    if (d->tried && now - d->failed_at < DBUS_RETRY_MS) return 0;
    d->tried = 1;
    d->len = d->skip = 0;
    if ((d->fd = dbus_open(path)) < 0) d->failed_at = now;
    return d->fd >= 0;
}

int dbus_next(struct bt_dbus *d, struct dbus_tr *t, unsigned now) {
    for (;;) {
        /* whole messages already held */
        while (d->len) {
            if (d->skip) { /* the rest of one too long to hold */
                int k = d->skip < d->len ? d->skip : d->len;
                memmove(d->buf, d->buf + k, (size_t)(d->len - k));
                d->len -= k, d->skip -= k;
                continue;
            }
            int size = dbus_size(d->buf, d->len);
            if (size < 0) { /* lost the framing: start again */
                close(d->fd), d->fd = -1, d->failed_at = now, d->len = 0;
                return 0;
            }
            if (!size) break;
            if (size > DBUS_BUF) {
                d->skip = size;
                continue;
            }
            if (size > d->len) break;
            int got = dbus_transport(d->buf, size, t);
            memmove(d->buf, d->buf + size, (size_t)(d->len - size));
            d->len -= size;
            if (got) return 1;
        }
        if (d->fd < 0) return 0;
        int k = (int)read(d->fd, d->buf + d->len, (size_t)(DBUS_BUF - d->len));
        if (k < 0 && (ERRNO == DBUS_EAGAIN || ERRNO == DBUS_EINTR)) return 0;
        if (k <= 0) { /* the bus went (dbus-daemon restarted) */
            close(d->fd), d->fd = -1, d->failed_at = now, d->len = d->skip = 0;
            return 0;
        }
        d->len += k;
    }
}
