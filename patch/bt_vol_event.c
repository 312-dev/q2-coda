/* A Bluetooth headset's own volume change, as BlueZ announces it
 * (docs/internals.md#headset-controls-and-volume). bluetoothd (BlueZ 5.66) emits PropertiesChanged
 * on org.bluez.MediaTransport1 when the headset's absolute volume moves, by its own keys or by our
 * write. A thread holds a connection of its own to the system bus with a match for that signal and
 * sleeps in recv until the bus sends something; each such signal counts, and bt_volume_poll
 * (playback_power.c), on the UI loop's next pass, sees the count change and reads the level at once
 * instead of waiting for its slow fallback. Nothing on the bus but those signals and the
 * connection's own replies wakes the thread.
 *
 * demo links libdbus-1 and gio but imports neither (and no dlsym), so the connection speaks the
 * wire protocol itself, the little of it this needs: SASL EXTERNAL with the process's uid, then
 * Hello and AddMatch, each a method call to the bus with no reply waited for; then every message
 * the bus sends is framed by its fixed header, and a signal whose header names the member
 * PropertiesChanged counts. The match also passes the transport's other properties (State, Delay):
 * each costs one read that finds the level unchanged. Any failure (no bus, a refused login, the
 * bus gone) ends the thread, and bt_volume_poll keeps its timed reads alone. */
#include "navigation.h"

#define BVE_SOCKET "/var/run/dbus/system_bus_socket"
#define BVE_RULE                                                                                   \
    "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.Properties',"                \
    "member='PropertiesChanged',arg0='org.bluez.MediaTransport1'"
#define BVE_BUF 2048 /* a transport's PropertiesChanged is a few hundred bytes */

static struct {
    int state; /* 0 not started, 2 connecting, 1 the thread waits on the bus, -1 no events */
    int fd;
    volatile unsigned seq; /* signals counted by the thread */
    unsigned seen;         /* the UI thread's last seq */
    unsigned len, skip;    /* bytes held in in; bytes of an oversized message still to drop */
    unsigned char in[BVE_BUF];
} bve __attribute__((section(".scratch")));

static int bve_send(const void *p, unsigned n) {
    const char *s = p;
    while (n) {
        int k = send(bve.fd, s, n, 0x4000 /* MSG_NOSIGNAL */);
        if (k <= 0) return 0;
        s += k, n -= (unsigned)k;
    }
    return 1;
}

/* One header field of a method call: its code, a one-letter signature ('s', 'o' or 'g') and its
 * string, at n in m (aligned to 8, as every field is). The new length. */
static unsigned bve_field(unsigned char *m, unsigned n, int code, char sig, const char *text) {
    unsigned len = strlen(text);
    while (n & 7) m[n++] = 0;
    m[n++] = (unsigned char)code, m[n++] = 1, m[n++] = (unsigned char)sig, m[n++] = 0;
    if (sig == 'g') {
        m[n++] = (unsigned char)len;
    } else {
        m[n++] = (unsigned char)len, m[n++] = (unsigned char)(len >> 8), m[n++] = 0, m[n++] = 0;
    }
    memcpy(m + n, text, len + 1);
    return n + len + 1;
}

/* A method call to the bus itself, member with one string argument (or none), little-endian. */
static int bve_call(const char *member, const char *arg, unsigned serial) {
    unsigned char m[400];
    unsigned n = 16;
    n = bve_field(m, n, 1, 'o', "/org/freedesktop/DBus");
    n = bve_field(m, n, 6, 's', "org.freedesktop.DBus");
    n = bve_field(m, n, 2, 's', "org.freedesktop.DBus");
    n = bve_field(m, n, 3, 's', member);
    if (arg) n = bve_field(m, n, 8, 'g', "s");
    unsigned fields = n - 16, body = arg ? 4 + strlen(arg) + 1 : 0;
    while (n & 7) m[n++] = 0;
    if (arg) {
        unsigned len = strlen(arg);
        m[n] = (unsigned char)len, m[n + 1] = (unsigned char)(len >> 8), m[n + 2] = 0, m[n + 3] = 0;
        memcpy(m + n + 4, arg, len + 1);
        n += body;
    }
    unsigned head[] = { 'l' | 1u << 8 | 1u << 24, body, serial, fields }; /* LE, METHOD_CALL, v1 */
    memcpy(m, head, sizeof head);
    return bve_send(m, n);
}

/* The bus, its login and the match, on the thread (the login waits on dbus-daemon). 0 without. */
int bt_vol_event_connect(void) {
    struct {
        unsigned short family;
        char path[108];
    } a = { 1 /* AF_UNIX */, BVE_SOCKET };
    bve.fd = socket(1 /* AF_UNIX */, 2 /* SOCK_STREAM (MIPS) */, 0);
    if (bve.fd < 0) return 0;
    char line[64] = "\0AUTH EXTERNAL ", hex[] = "0123456789abcdef";
    unsigned n = 15;
    char uid[12];
    unsigned u = getuid(), k = 0;
    do uid[k++] = (char)('0' + u % 10);
    while (u /= 10);
    while (k--) line[n++] = hex[uid[k] >> 4], line[n++] = hex[uid[k] & 15]; /* its digits, in hex */
    line[n++] = '\r', line[n++] = '\n';
    int got = 0;
    if (!connect(bve.fd, &a, sizeof a) && bve_send(line, n)) {
        while (got < (int)sizeof line - 1) { /* "OK <guid>\r\n" or a refusal, one line */
            int r = recv(bve.fd, line + got, sizeof line - 1 - (unsigned)got, 0);
            if (r <= 0) break;
            got += r;
            if (line[got - 1] == '\n') break;
        }
    }
    if (got < 3 || line[0] != 'O' || line[1] != 'K' || !bve_send("BEGIN\r\n", 7) ||
        !bve_call("Hello", 0, 1) || !bve_call("AddMatch", BVE_RULE, 2)) {
        close(bve.fd);
        bve.fd = -1;
        return 0;
    }
    bve.len = bve.skip = 0;
    bve.state = 1;
    return 1;
}

static unsigned bve_u32(const unsigned char *p, int big) {
    return big ? (unsigned)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]
               : (unsigned)p[3] << 24 | p[2] << 16 | p[1] << 8 | p[0];
}

/* Each whole message held: a signal (type 4) whose header fields hold the member's name counts. */
static void bve_frames(void) {
    static const char member[] = "PropertiesChanged";
    while (bve.len >= 16) {
        const unsigned char *m = bve.in;
        int big = m[0] == 'B';
        unsigned fields = bve_u32(m + 12, big), body = bve_u32(m + 4, big);
        unsigned whole = 16 + ((fields + 7) & ~7u) + body;
        if ((m[0] != 'l' && !big) || fields > BVE_BUF || body > 1u << 26) { /* not D-Bus: give up */
            bve.len = 0;
            return;
        }
        if (whole > BVE_BUF) { /* not ours (ours is small): dropped as it arrives */
            bve.skip = whole - bve.len;
            bve.len = 0;
            return;
        }
        if (bve.len < whole) return;
        if (m[1] == 4)
            for (unsigned i = 16; i + sizeof member <= 16 + fields; ++i)
                if (!memcmp(m + i, member, sizeof member)) {
                    __sync_synchronize();
                    ++bve.seq;
                    break;
                }
        bve.len -= whole;
        memmove(bve.in, bve.in + whole, bve.len);
    }
}

/* One wait: blocks until the bus sends something, then counts the signals it completed. 0 once
 * the connection is gone. */
int bt_vol_event_wait(void) {
    int n = recv(bve.fd, bve.in + bve.len, BVE_BUF - bve.len, 0);
    if (n <= 0) return 0;
    if (bve.skip) {
        unsigned drop = (unsigned)n < bve.skip ? (unsigned)n : bve.skip;
        bve.skip -= drop;
        memmove(bve.in + bve.len, bve.in + bve.len + drop, (unsigned)n - drop);
        n -= (int)drop;
    }
    bve.len += (unsigned)n;
    bve_frames();
    return 1;
}

static void *bt_vol_event_worker(void *unused) {
    if (bt_vol_event_connect()) {
        while (bt_vol_event_wait()) {}
        close(bve.fd);
    }
    bve.state = -1; /* the timed reads alone from here */
    return 0;
}

/* Once, from bt_volume_poll's first pass with a headset ready. */
void bt_vol_event_start(void) {
    if (bve.state) return;
    bve.state = 2;
    unsigned long t;
    if (pthread_create(&t, 0, bt_vol_event_worker, 0)) {
        bve.state = -1;
        return;
    }
    pthread_detach(t);
}

/* The UI thread's look: 1 when a signal came since the last look. */
int bt_vol_event_take(void) {
    unsigned seq = bve.seq;
    if (seq == bve.seen) return 0;
    bve.seen = seq;
    return 1;
}

/* 1 while the thread waits on the bus: bt_volume_poll's timed reads are only a fallback then. */
int bt_vol_event_live(void) { return bve.state == 1; }
