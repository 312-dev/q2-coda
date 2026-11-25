/* q2-guest: Guest Play's system side (docs/internals.md#guest-play).
 *
 * A guest's phone streams to the Q2 (A2DP sink, a second bluealsa daemon, "org.bluealsa.sink"), and
 * bluealsa-aplay plays it on to the car or headset the Q2 plays to, or the jack. This daemon
 * decides who may: it asks the owner (through the state file the UI reads, answered by commands on
 * a FIFO), keeps the Trusted Guests, opens Let a Guest Connect (discoverable under the Guest Play
 * name, with its own pairing agent), forwards the guest's track information to the car through
 * bluetoothd's shanling.bluez service and the car's buttons to the guest's phone. demo's payload
 * (patch/guest.c) starts it and does what only demo can: pause and resume the owner's music.
 *
 * One thread: poll() on the bus, the FIFO and a 250 ms tick. Calls to BlueZ and bluealsa block for
 * at most QG_CALL_MS. */
#include "coda_rc_sys.h"

#ifndef CODA_HOST
int fork(void), dup2(int, int), execv(const char *, char *const[]), waitpid(int, int *, int),
    setsid(void);
int mkfifo(const char *, unsigned), system(const char *);
__attribute__((noreturn)) void _exit(int);
#define WNOHANG 1
#define SIGKILL 9
#define AF_UNIX 1
#define SOCK_DGRAM 1 /* MIPS swaps STREAM and DGRAM */
#define MSG_DONTWAIT 0x40 /* Linux's on every architecture; 0x80 is MSG_EOR */
struct sockaddr_un {
    unsigned short sun_family;
    char sun_path[108];
};
ssize_t sendto(int, const void *, size_t, int, const void *, socklen_t);
#else
#include <sys/un.h>
#include <sys/wait.h>
#endif

/* ---- the libdbus 1.12 API used here (no headers on the Q2's side) ---- */
typedef struct DBusConnection DBusConnection;
typedef struct DBusMessage DBusMessage;
typedef struct {
    const char *name, *message;
    unsigned dummy;
    void *pad;
} DBusError;
typedef struct {
    void *p[4];
    int i[16];
    void *q[4];
} DBusMessageIter;
typedef int (*dbus_msg_fn)(DBusConnection *, DBusMessage *, void *);
typedef struct {
    void (*unregister)(DBusConnection *, void *);
    dbus_msg_fn message;
    void (*pad[4])(void *);
} DBusObjectPathVTable;
void dbus_error_init(DBusError *);
void dbus_error_free(DBusError *);
DBusConnection *dbus_bus_get(int, DBusError *);
void dbus_bus_add_match(DBusConnection *, const char *, DBusError *);
void dbus_connection_set_exit_on_disconnect(DBusConnection *, int);
int dbus_connection_get_unix_fd(DBusConnection *, int *);
int dbus_connection_register_object_path(DBusConnection *, const char *,
                                         const DBusObjectPathVTable *, void *);
int dbus_connection_add_filter(DBusConnection *, dbus_msg_fn, void *, void (*)(void *));
int dbus_connection_read_write(DBusConnection *, int);
int dbus_connection_dispatch(DBusConnection *);
int dbus_connection_send(DBusConnection *, DBusMessage *, uint32_t *);
void dbus_connection_flush(DBusConnection *);
DBusMessage *dbus_connection_send_with_reply_and_block(DBusConnection *, DBusMessage *, int,
                                                       DBusError *);
int dbus_connection_get_is_connected(DBusConnection *);
DBusMessage *dbus_message_new_method_call(const char *, const char *, const char *, const char *);
DBusMessage *dbus_message_new_method_return(DBusMessage *);
DBusMessage *dbus_message_new_error(DBusMessage *, const char *, const char *);
DBusMessage *dbus_message_ref(DBusMessage *);
void dbus_message_unref(DBusMessage *);
void dbus_message_set_no_reply(DBusMessage *, int);
int dbus_message_is_method_call(DBusMessage *, const char *, const char *);
int dbus_message_is_signal(DBusMessage *, const char *, const char *);
const char *dbus_message_get_path(DBusMessage *);
const char *dbus_message_get_sender(DBusMessage *);
const char *dbus_message_get_interface(DBusMessage *);
int dbus_message_append_args(DBusMessage *, int, ...);
int dbus_message_iter_init(DBusMessage *, DBusMessageIter *);
void dbus_message_iter_init_append(DBusMessage *, DBusMessageIter *);
int dbus_message_iter_get_arg_type(DBusMessageIter *);
void dbus_message_iter_recurse(DBusMessageIter *, DBusMessageIter *);
int dbus_message_iter_next(DBusMessageIter *);
void dbus_message_iter_get_basic(DBusMessageIter *, void *);
void dbus_message_iter_get_fixed_array(DBusMessageIter *, void *, int *);
int dbus_message_iter_append_basic(DBusMessageIter *, int, const void *);
int dbus_message_iter_append_fixed_array(DBusMessageIter *, int, const void *, int);
int dbus_message_iter_open_container(DBusMessageIter *, int, const char *, DBusMessageIter *);
int dbus_message_iter_close_container(DBusMessageIter *, DBusMessageIter *);
#define DBUS_BUS_SYSTEM 1
#define DBUS_HANDLED 0
#define DBUS_NOT_YET_HANDLED 1
#define DBUS_DATA_REMAINS 0
#define T_END 0
#define T_STR 's'
#define T_PATH 'o'
#define T_BOOL 'b'
#define T_I32 'i'
#define T_U32 'u'
#define T_U16 'q'
#define T_BYTE 'y'
#define T_ARRAY 'a'
#define T_VARIANT 'v'
#define T_DICT 'e'

/* ---- where things are (the tests name their own) ---- */
#ifndef QG_RUN
#define QG_RUN "/tmp/q2-guest"
#endif
#ifndef QG_DATA
#define QG_DATA "/mnt/data/q2-guest"
#endif
#ifndef QG_BIN
#define QG_BIN "/usr/bin/"
#endif
#define QG_STATE QG_RUN ".state"
#define QG_CMD QG_RUN ".cmd"
#define QG_PID QG_RUN ".pid"
#define QG_CONFIG QG_DATA "/config"
#define QG_TRUSTED QG_DATA "/trusted"
#ifndef QG_SPOT /* librespot's status file and control socket (docs/internals.md#spotify) */
#define QG_SPOT "/tmp/q2-librespot"
#endif

/* the timings; the tests shorten some */
#define QG_TICK_MS 250
#define QG_CALL_MS 2000
#ifndef QG_ASK_S
#define QG_ASK_S 30 /* a prompt answers itself (Decline, Keep) after this */
#endif
#ifndef QG_WINDOW_S
#define QG_WINDOW_S 120 /* Let a Guest Connect */
#endif
#ifndef QG_GIVEUP_MS
#define QG_GIVEUP_MS 300000 /* a guest paused this long: the owner's music again */
#endif
#ifndef QG_ARRIVE_MS
#define QG_ARRIVE_MS 30000 /* a guest allowed while pairing must start its stream by then */
#endif
#ifndef QG_OUT_WAIT_MS
#define QG_OUT_WAIT_MS                                                                             \
    8000 /* the sink daemon waits this long for the car or headset to connect first */
#endif
#ifndef QG_HOTSPOT
#define QG_HOTSPOT QG_BIN "q2-hotspot"
#endif
#define QG_HOTSPOT_IP "192.168.43.1" /* q2-hotspot's NET */
#ifndef QG_HOTSPOT_CHECK_MS
#define QG_HOTSPOT_CHECK_MS 10000
#endif
#define QG_RESEND_MS 1500  /* the guest's song again after the owner's player said it paused */
#define QG_RESPAWN_MS 1000 /* a child that ended is started again no sooner */
#define QG_TRUSTED_MAX 16
#define QG_NAME 64
#define QG_FIELD 256

#define BLUEZ "org.bluez"
#define SINK_SRV "org.bluealsa.sink"
#define MAIN_SRV "org.bluealsa"
#define ADAPTER "/org/bluez/hci0"
#define AGENT_PATH "/org/q2pod/guest_agent"
#define A2DP_SINK_UUID "0000110b-0000-1000-8000-00805f9b34fb"

/* The main bluealsa daemon (demo's launcher, /usr/bin/bluealsa2, picks its role from the saved
 * switch): restarted when it does not match the switch, only while bluetoothd runs. */
#define QG_MAIN_LOOP(kill_when)                                                                    \
    "pidof bluetoothd >/dev/null || exit 0; R=; for p in $(pidof bluealsa); do "                   \
    "a=\"$(tr '\\0' ' ' </proc/$p/cmdline) \"; case \"$a\" in *' -B '*) continue;; esac; "         \
    "case \"$a\" in " kill_when "; esac; done; [ \"$R\" ] && (" QG_BIN "bluealsa2 &); exit 0"
#ifndef QG_MAIN_SOURCE /* the tests name their own */
#define QG_MAIN_SOURCE QG_MAIN_LOOP("*a2dp-source*) ;; *) kill $p; R=1;;")
#define QG_MAIN_STOCK QG_MAIN_LOOP("*a2dp-source*) kill $p; R=1;;")
#endif

enum { ASK_NONE, ASK_PAIR, ASK_STREAM };

typedef struct {
    char mac[48], name[QG_NAME]; /* a network guest's mac is KIND:ID
                                    (docs/internals.md#guest-play-receivers) */
    int ask;
    long last;
} qg_trusted_t;

typedef struct {
    char path[160];              /* its PCM on bluealsa */
    char mac[48], name[QG_NAME]; /* a network guest's mac is KIND:ID
                                    (docs/internals.md#guest-play-receivers) */
    int on_main;                 /* the PCM is the main daemon's (no -B for aplay) */
    char codec[48];
    char source[12]; /* "" Bluetooth, else the receiver: airplay, spotify, dlna */
} qg_dev_t;

static struct {
    DBusConnection *bus;
    int fifo, fifo_keep, quit;
    char line[256];
    unsigned line_n;
    /* the settings */
    int enabled, trusted_only, stay;
    int hotspot, hotspot_up, hotspot_clients; /* switched on; running; phones joined */
    char hotspot_pass[64];
    long hotspot_at; /* the last check */
    char name[QG_NAME];
    qg_trusted_t trusted[QG_TRUSTED_MAX];
    int n_trusted;
    /* the bus */
    char sink_owner[64];
    int bluez_up, main_up;
    long main_up_at;
    char reconnect[18]; /* the car to connect again once the main daemon is back */
    /* Let a Guest Connect */
    long window_end;
    int agent;
    char saved_alias[QG_NAME * 2];
    /* the prompt */
    int ask;
    long ask_end;
    DBusMessage *pending; /* the agent request it answers */
    qg_dev_t asking;
    char code[8];
    /* the session */
    int playing;
    qg_dev_t guest;
    int guest_trusted;
    long session_at, arrive_by, paused_since, resend_at;
    char approved[18]; /* allowed while pairing: not asked again when its stream starts */
    /* the second guest */
    int replace;
    long replace_end;
    DBusMessage *rep_pending;
    qg_dev_t newcomer;
    /* where it plays */
    qg_dev_t out;
    /* the guest's song */
    char player[200];
    char title[QG_FIELD], artist[QG_FIELD], album[QG_FIELD], genre[QG_FIELD], status[24];
    unsigned track, tracks, duration, pos;
    long pos_at;
    unsigned sent_hash;
    int sent_status;
    /* children */
    int sinkd, aplay;
    long sinkd_at, aplay_at;
    /* the state file */
    unsigned seq, event_seq, netdeny_seq;
    char netdeny[12]; /* the receiver the payload is to stop, counted by netdeny_seq */
    char event[16], event_name[QG_NAME];
    char last[4096];
    long written_at;
} qg;

static long qg_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000L + t.tv_nsec / 1000000L;
}

static void copy(char *dst, const char *src, unsigned n) {
    unsigned i = 0;
    for (; src && src[i] && i + 1 < n; ++i)
        dst[i] = src[i] == '\n' || src[i] == '\r' || src[i] == '\t' ? ' ' : src[i];
    dst[i] = 0;
}

/* ---- files ---- */

static int write_whole(const char *path, const char *data, unsigned n, int sync) {
    char tmp[128];
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return 0;
    int ok = write(fd, data, n) == (ssize_t)n;
    if (sync) fsync(fd);
    close(fd);
    return ok && !rename(tmp, path);
}

static int read_whole(const char *path, char *buf, unsigned n) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return 0;
    ssize_t got = read(fd, buf, n - 1);
    close(fd);
    buf[got > 0 ? got : 0] = 0;
    return got > 0;
}

/* The value of key in text of key=value lines, into out (size n); 0 when absent. Exposed for the
 * tests. */
int qg_field(const char *raw, const char *key, char *out, unsigned n) {
    unsigned k = strlen(key);
    for (const char *line = raw; *line;) {
        const char *end = strchr(line, '\n');
        unsigned len = end ? (unsigned)(end - line) : strlen(line);
        if (len > k && !strncmp(line, key, k) && line[k] == '=') {
            unsigned v = len - k - 1 < n - 1 ? len - k - 1 : n - 1;
            memcpy(out, line + k + 1, v);
            out[v] = 0;
            return 1;
        }
        line += len + (end != 0);
    }
    if (n) out[0] = 0;
    return 0;
}

static void config_load(void) {
    char raw[512], v[QG_NAME];
    raw[0] = 0;
    read_whole(QG_CONFIG, raw, sizeof raw);
    qg.enabled = qg_field(raw, "enabled", v, sizeof v) && atoi(v) == 1;
    qg.trusted_only = qg_field(raw, "allow", v, sizeof v) && !strcmp(v, "trusted");
    qg.stay = qg_field(raw, "leave", v, sizeof v) && !strcmp(v, "stay");
    qg_field(raw, "name", qg.name, sizeof qg.name);
    qg.hotspot = qg_field(raw, "hotspot", v, sizeof v) && atoi(v) == 1;
    qg_field(raw, "hotspot_pass", qg.hotspot_pass, sizeof qg.hotspot_pass);
}

static void config_save(void) {
    char b[320];
    int n = snprintf(b, sizeof b,
                     "enabled=%d\nname=%s\nallow=%s\nleave=%s\nhotspot=%d\nhotspot_pass=%s\n",
                     qg.enabled, qg.name, qg.trusted_only ? "trusted" : "ask",
                     qg.stay ? "stay" : "resume", qg.hotspot, qg.hotspot_pass);
    mkdir(QG_DATA, 0755);
    write_whole(QG_CONFIG, b, (unsigned)n, 1);
}

static void trusted_load(void) {
    char raw[QG_TRUSTED_MAX * 128 + 1];
    qg.n_trusted = 0;
    if (!read_whole(QG_TRUSTED, raw, sizeof raw)) return;
    for (char *line = raw; *line && qg.n_trusted < QG_TRUSTED_MAX;) {
        char *end = strchr(line, '\n');
        if (end) *end = 0;
        char *f[4] = { line, 0, 0, 0 };
        for (int i = 1; i < 4 && f[i - 1]; ++i) {
            char *tab = strchr(f[i - 1], '\t');
            if (tab) *tab = 0, f[i] = tab + 1;
        }
        if (f[3] && *f[0] && strlen(f[0]) < sizeof qg.trusted[0].mac) {
            qg_trusted_t *t = &qg.trusted[qg.n_trusted++];
            copy(t->mac, f[0], sizeof t->mac);
            t->ask = atoi(f[1]) != 0;
            t->last = atoi(f[2]);
            copy(t->name, f[3], sizeof t->name);
        }
        if (!end) break;
        line = end + 1;
    }
}

static void trusted_save(void) {
    char b[QG_TRUSTED_MAX * 128];
    unsigned n = 0;
    for (int i = 0; i < qg.n_trusted; ++i)
        n += (unsigned)snprintf(b + n, sizeof b - n, "%s\t%d\t%ld\t%s\n", qg.trusted[i].mac,
                                qg.trusted[i].ask, qg.trusted[i].last, qg.trusted[i].name);
    mkdir(QG_DATA, 0755);
    write_whole(QG_TRUSTED, b, n, 1);
}

static qg_trusted_t *trusted_find(const char *mac) {
    for (int i = 0; i < qg.n_trusted; ++i)
        if (!strcmp(qg.trusted[i].mac, mac)) return &qg.trusted[i];
    return 0;
}

/* A new Trusted Guest, or the one there seen again; the least recently played makes room. */
static void trusted_add(const qg_dev_t *d) {
    qg_trusted_t *t = trusted_find(d->mac);
    if (!t) {
        if (qg.n_trusted == QG_TRUSTED_MAX) {
            int old = 0;
            for (int i = 1; i < qg.n_trusted; ++i)
                if (qg.trusted[i].last < qg.trusted[old].last) old = i;
            qg.trusted[old] = qg.trusted[--qg.n_trusted];
        }
        t = &qg.trusted[qg.n_trusted++];
        copy(t->mac, d->mac, sizeof t->mac);
        copy(t->name, d->name, sizeof t->name);
        t->ask = 0;
    }
    t->last = time(0);
    trusted_save();
}

/* ---- the bus ---- */

static DBusMessage *call_va(const char *dest, const char *path, const char *iface,
                            const char *method, int first, va_list ap) {
    DBusMessage *m = dbus_message_new_method_call(dest, path, iface, method);
    if (!m) return 0;
    /* dbus_message_append_args takes (type, pointer) pairs to T_END; forwarded by hand as the
     * va_list can't be */
    for (int type = first; type != T_END; type = va_arg(ap, int)) {
        void *v = va_arg(ap, void *);
        DBusMessageIter it;
        dbus_message_iter_init_append(m, &it);
        dbus_message_iter_append_basic(&it, type, v);
    }
    DBusError e;
    dbus_error_init(&e);
    DBusMessage *r = dbus_connection_send_with_reply_and_block(qg.bus, m, QG_CALL_MS, &e);
    dbus_error_free(&e);
    dbus_message_unref(m);
    return r;
}

/* A method call with basic arguments (type, pointer, ..., T_END); its reply, or 0 for an error. */
static DBusMessage *call(const char *dest, const char *path, const char *iface, const char *method,
                         int first, ...) {
    va_list ap;
    va_start(ap, first);
    DBusMessage *r = call_va(dest, path, iface, method, first, ap);
    va_end(ap);
    return r;
}

/* The same, its reply not waited for. */
static void send_only(const char *dest, const char *path, const char *iface, const char *method,
                      int first, ...) {
    DBusMessage *m = dbus_message_new_method_call(dest, path, iface, method);
    if (!m) return;
    va_list ap;
    va_start(ap, first);
    for (int type = first; type != T_END; type = va_arg(ap, int)) {
        void *v = va_arg(ap, void *);
        DBusMessageIter it;
        dbus_message_iter_init_append(m, &it);
        dbus_message_iter_append_basic(&it, type, v);
    }
    va_end(ap);
    dbus_message_set_no_reply(m, 1);
    dbus_connection_send(qg.bus, m, 0);
    dbus_message_unref(m);
}

static void set_prop(const char *path, const char *iface, const char *prop, int type,
                     const void *v) {
    DBusMessage *m =
        dbus_message_new_method_call(BLUEZ, path, "org.freedesktop.DBus.Properties", "Set");
    if (!m) return;
    DBusMessageIter it, var;
    char sig[2] = { (char)type, 0 };
    dbus_message_iter_init_append(m, &it);
    dbus_message_iter_append_basic(&it, T_STR, &iface);
    dbus_message_iter_append_basic(&it, T_STR, &prop);
    dbus_message_iter_open_container(&it, T_VARIANT, sig, &var);
    dbus_message_iter_append_basic(&var, type, v);
    dbus_message_iter_close_container(&it, &var);
    DBusError e;
    dbus_error_init(&e);
    DBusMessage *r = dbus_connection_send_with_reply_and_block(qg.bus, m, QG_CALL_MS, &e);
    dbus_error_free(&e);
    if (r) dbus_message_unref(r);
    dbus_message_unref(m);
}

/* A string property of a BlueZ object into out; 0 when it has none. */
static int get_str(const char *path, const char *iface, const char *prop, char *out, unsigned n) {
    DBusMessage *r = call(BLUEZ, path, "org.freedesktop.DBus.Properties", "Get", T_STR, &iface,
                          T_STR, &prop, T_END);
    DBusMessageIter it, var;
    const char *s = 0;
    if (r && dbus_message_iter_init(r, &it) && dbus_message_iter_get_arg_type(&it) == T_VARIANT) {
        dbus_message_iter_recurse(&it, &var);
        if (dbus_message_iter_get_arg_type(&var) == T_STR) dbus_message_iter_get_basic(&var, &s);
    }
    copy(out, s ? s : "", n);
    if (r) dbus_message_unref(r);
    return s != 0;
}

/* /org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF (or anything below it) to AA:BB:CC:DD:EE:FF. */
static int path_mac(const char *path, char *mac) {
    const char *d = path ? strstr(path, "/dev_") : 0;
    if (!d || strlen(d) < 22) return 0;
    for (int i = 0; i < 17; ++i) mac[i] = d[5 + i] == '_' ? ':' : d[5 + i];
    mac[17] = 0;
    return 1;
}

static void dev_path(const char *mac, char *out, unsigned n) {
    char m[18];
    copy(m, mac, sizeof m);
    for (char *c = m; *c; ++c)
        if (*c == ':') *c = '_';
    snprintf(out, n, ADAPTER "/dev_%s", m);
}

static void dev_name(qg_dev_t *d) {
    char path[64];
    dev_path(d->mac, path, sizeof path);
    if (!get_str(path, "org.bluez.Device1", "Alias", d->name, sizeof d->name))
        copy(d->name, d->mac, sizeof d->name);
}

static void dev_call(const char *mac, const char *method) {
    char path[64];
    if (strlen(mac) != 17) return; /* a network guest's KIND:ID */
    dev_path(mac, path, sizeof path);
    send_only(BLUEZ, path, "org.bluez.Device1", method, T_END);
}

/* ---- codec lines ---- */

/* "CODEC RATE KBPS" from a bluealsa PCM's Codec, Sampling and CodecConfiguration (the A2DP
 * capability octets: SBC's mode, blocks, subbands and bit pool give its bit rate; AAC carries its
 * own). Exposed for the tests. */
void qg_codec_line(const char *codec, unsigned rate, const unsigned char *cfg, int n, char *out,
                   unsigned size) {
    unsigned kbps = 0;
    if (codec && !strcmp(codec, "SBC") && n >= 4 && rate) {
        unsigned mode = cfg[0] & 15, blocks = cfg[1] >> 4, sb = cfg[1] & 0x0c, pool = cfg[3];
        blocks = blocks & 8 ? 4 : blocks & 4 ? 8 : blocks & 2 ? 12 : 16;
        sb = sb & 8 ? 4 : 8;
        unsigned ch = mode & 8 ? 1 : 2, len = 4 + 4 * sb * ch / 8;
        if (mode & 0x0c) /* mono or dual channel: a bit pool for each channel */
            len += (blocks * ch * pool + 7) / 8;
        else
            len += ((mode & 1 ? sb : 0) + blocks * pool + 7) / 8;
        kbps = (8u * len * rate / (sb * blocks) + 500) /
               1000; /* under 2^32: len < 600, rate <= 48000 */
    } else if (codec && !strcmp(codec, "AAC") && n >= 6)
        kbps = (((cfg[3] & 0x7fu) << 16 | cfg[4] << 8 | cfg[5]) + 500) / 1000;
    snprintf(out, size, "%s %u %u", codec && *codec ? codec : "?", rate, kbps);
}

/* A PCM's properties (a{sv}) into d (its codec line) and its transport's direction (1: the Q2 is
 * the sink). */
static int pcm_props(DBusMessageIter *dict, qg_dev_t *d) {
    const char *codec = "", *device = 0, *transport = "";
    unsigned rate = 0;
    const unsigned char *cfg = 0;
    int n = 0;
    DBusMessageIter e, v;
    while (dbus_message_iter_get_arg_type(dict) == T_DICT) {
        const char *key;
        dbus_message_iter_recurse(dict, &e);
        dbus_message_iter_get_basic(&e, &key);
        dbus_message_iter_next(&e);
        dbus_message_iter_recurse(&e, &v);
        int t = dbus_message_iter_get_arg_type(&v);
        if (!strcmp(key, "Codec") && t == T_STR) dbus_message_iter_get_basic(&v, &codec);
        if (!strcmp(key, "Device") && t == T_PATH) dbus_message_iter_get_basic(&v, &device);
        if (!strcmp(key, "Transport") && t == T_STR) dbus_message_iter_get_basic(&v, &transport);
        if (!strcmp(key, "Sampling") && t == T_U32) dbus_message_iter_get_basic(&v, &rate);
        if (!strcmp(key, "CodecConfiguration") && t == T_ARRAY) {
            DBusMessageIter a;
            dbus_message_iter_recurse(&v, &a);
            dbus_message_iter_get_fixed_array(&a, &cfg, &n);
        }
        dbus_message_iter_next(dict);
    }
    if (!path_mac(device, d->mac)) return -1;
    qg_codec_line(codec, rate, cfg, n, d->codec, sizeof d->codec);
    return !strcmp(transport, "A2DP-sink");
}

/* ---- children ---- */

static int spawn(char *const argv[]) {
    int pid = fork();
    if (pid) return pid > 0 ? pid : 0;
    setsid();
    int null = open("/dev/null", O_RDWR);
    if (null >= 0) dup2(null, 0), dup2(null, 1), dup2(null, 2);
    execv(argv[0], argv);
    _exit(127);
}

static void stop_child(int *pid) {
    if (*pid > 0) kill(*pid, SIGTERM), waitpid(*pid, 0, 0);
    *pid = 0;
}

static void sinkd_start(void) {
    char *argv[] = { QG_BIN "bluealsa", "-B", "sink", "-p", "a2dp-sink", "-c", "AAC", 0 };
    qg.sinkd = spawn(argv);
    qg.sinkd_at = qg_ms();
}

static void aplay_start(void) {
    char dev[96];
    if (*qg.out.mac)
        snprintf(dev, sizeof dev, "bluealsa:DEV=%s,PROFILE=a2dp", qg.out.mac);
    else
        copy(dev, "plug:hw:0", sizeof dev);
    char *with_b[] = { QG_BIN "bluealsa-aplay", "-B", "sink", "-D", dev, qg.guest.mac, 0 };
    char *main[] = { QG_BIN "bluealsa-aplay", "-D", dev, qg.guest.mac, 0 };
    qg.aplay = spawn(qg.guest.on_main ? main : with_b);
    qg.aplay_at = qg_ms();
}

static void reap(void) {
    int pid;
    while ((pid = waitpid(-1, 0, WNOHANG)) > 0) {
        if (pid == qg.sinkd) qg.sinkd = 0;
        if (pid == qg.aplay) qg.aplay = 0;
    }
}

/* ---- the hotspot (docs/internals.md#guest-play-hotspot): q2-hotspot does the work ---- */

/* The hotspot's name: Guest Play's, or the Q2's default Bluetooth name (bt_name.c). */
static void hotspot_ssid(char *out) { copy(out, *qg.name ? qg.name : "Vamp", QG_NAME); }

/* Wi-Fi's join QR code text: WIFI:T:WPA;S:name;P:password;; with \ ; , : and " escaped. Exposed for
 * the tests. */
void qg_wifi_qr(const char *ssid, const char *pass, char *out, unsigned n) {
    unsigned k = 0;
    const char *part[2] = { ssid, pass }, *head[2] = { "WIFI:T:WPA;S:", ";P:" };
    for (int i = 0; i < 2; ++i) {
        for (const char *h = head[i]; *h && k + 1 < n; ++h) out[k++] = *h;
        for (const char *c = part[i]; *c && k + 2 < n; ++c) {
            if (strchr("\\;,:\"", *c)) out[k++] = '\\';
            out[k++] = *c;
        }
    }
    for (const char *h = ";;"; *h && k + 1 < n; ++h) out[k++] = *h;
    out[k] = 0;
}

static void hotspot_qr(const char *ssid, char *out, unsigned n) {
    qg_wifi_qr(ssid, qg.hotspot_pass, out, n);
}

/* A WPA2 passphrase q2-hotspot can pass on as it is: 8 to 63 printable characters, no quote or
 * backslash. Exposed for the tests. */
int qg_pass_ok(const char *s) {
    unsigned n = strlen(s);
    for (const char *c = s; *c; ++c)
        if (*c < 0x20 || *c > 0x7e || strchr("\"'\\`", *c)) return 0;
    return n >= 8 && n <= 63;
}

/* The generated default: two short words and two digits, easy to read out (tulip-river-42). */
static void hotspot_new_pass(void) {
    static const char *const words[] = { "tulip",  "river",  "maple",  "comet", "otter",  "cedar",
                                         "lemon",  "harbor", "pixel",  "amber", "willow", "ember",
                                         "falcon", "meadow", "violet", "canyon" };
    unsigned char r[4] = { 0 };
    int fd = open("/dev/urandom", O_RDONLY);
    if (fd >= 0) read(fd, r, sizeof r), close(fd);
    snprintf(qg.hotspot_pass, sizeof qg.hotspot_pass, "%s-%s-%02u", words[r[0] & 15],
             words[r[1] & 15], r[2] % 100u);
}

static void hotspot_apply(void) {
    char cmd[400], ssid[QG_NAME], hex[2 * QG_NAME + 1];
    int want = qg.enabled && qg.hotspot && qg_pass_ok(qg.hotspot_pass);
    if (!want) {
        if (qg.hotspot_up) system(QG_HOTSPOT " stop");
        qg.hotspot_up = qg.hotspot_clients = 0;
        return;
    }
    hotspot_ssid(ssid);
    for (unsigned i = 0; ssid[i]; ++i) snprintf(hex + 2 * i, 3, "%02x", (unsigned char)ssid[i]);
    snprintf(cmd, sizeof cmd, QG_HOTSPOT " start %s '%s'", *ssid ? hex : "5132", qg.hotspot_pass);
    qg.hotspot_up = system(cmd) == 0;
    qg.hotspot_at = qg_ms();
}

/* Every QG_HOTSPOT_CHECK_MS while it runs: on wlan0's channel still, and how many phones have
 * joined. */
static void hotspot_check(void) {
    char out[16] = "";
    qg.hotspot_at = qg_ms();
    system(QG_HOTSPOT " check > " QG_RUN ".hotspot 2>/dev/null");
    read_whole(QG_RUN ".hotspot", out, sizeof out);
    qg.hotspot_clients = atoi(out);
}

/* ---- the state file ---- */

static int countdown(long end) {
    long left = end - qg_ms();
    return left <= 0 ? 0 : (int)((left + 999) / 1000);
}

static const char *state_name(void) {
    if (!qg.enabled) return "off";
    if (qg.replace) return "replace";
    if (qg.ask) return "asking";
    if (qg.playing)
        return !strcmp(qg.status, "paused") || !strcmp(qg.status, "stopped") ? "paused" : "playing";
    return qg.window_end ? "open" : "idle";
}

static unsigned position(void) {
    unsigned p = qg.pos;
    if (!strcmp(qg.status, "playing") && qg.pos_at) p += (unsigned)(qg_ms() - qg.pos_at);
    return qg.duration && p > qg.duration ? qg.duration : p;
}

/* Written whole when anything in it changed. */
static void state_write(void) {
    static char b[4096];
    const qg_dev_t *g = qg.ask ? &qg.asking : &qg.guest;
    int shown = qg.ask || qg.playing;
    int n = snprintf(b, sizeof b,
                     "state=%s\nenabled=%d\nname=%s\nallow=%s\nleave=%s\nwindow=%d\nguest=%s\nmac=%"
                     "s\ntrusted=%d\ncode=%s\n"
                     "countdown=%d\nnew=%s\nnewmac=%s\nin=%s\nout=%s\noutmac=%s\noutcodec=%"
                     "s\ntitle=%s\nartist=%s\nalbum=%s\n"
                     "genre=%s\ntrack=%u\ntracks=%u\nduration=%u\nposition=%u\nstatus=%s\nevent=%"
                     "s\nevent_seq=%u\neventname=%s\n",
                     state_name(), qg.enabled, qg.name, qg.trusted_only ? "trusted" : "ask",
                     qg.stay ? "stay" : "resume", countdown(qg.window_end), shown ? g->name : "",
                     shown ? g->mac : "", shown && trusted_find(g->mac) != 0, qg.ask ? qg.code : "",
                     qg.ask       ? countdown(qg.ask_end)
                     : qg.replace ? countdown(qg.replace_end)
                                  : 0,
                     qg.replace ? qg.newcomer.name : "", qg.replace ? qg.newcomer.mac : "",
                     qg.playing ? qg.guest.codec : "", *qg.out.mac ? qg.out.name : "jack",
                     qg.out.mac, *qg.out.mac ? qg.out.codec : "", qg.playing ? qg.title : "",
                     qg.playing ? qg.artist : "", qg.playing ? qg.album : "",
                     qg.playing ? qg.genre : "", qg.track, qg.tracks, qg.duration,
                     qg.playing ? position() / 1000 * 1000 : 0, qg.playing ? qg.status : "",
                     qg.event, qg.event_seq, qg.event_name);
    if (n < 0 || n >= (int)sizeof b) n = (int)sizeof b - 1;
    char ssid[QG_NAME], qr[2 * QG_NAME + 160];
    hotspot_ssid(ssid);
    hotspot_qr(ssid, qr, sizeof qr);
    int h = snprintf(b + n, sizeof b - n,
                     "hotspot=%d\nhotspot_up=%d\nhotspot_name=%s\nhotspot_pass=%s\nhotspot_ip=%s\n"
                     "hotspot_clients=%d\nhotspot_qr=%s\n",
                     qg.hotspot, qg.hotspot_up, ssid, qg.hotspot_pass,
                     qg.hotspot_up ? QG_HOTSPOT_IP : "", qg.hotspot_clients, qr);
    if (h > 0 && n + h < (int)sizeof b) n += h;
    h = snprintf(b + n, sizeof b - n, "source=%s\nnewsource=%s\nnetdeny=%s\nnetdeny_seq=%u\n",
                 qg.ask       ? qg.asking.source
                 : qg.playing ? qg.guest.source
                              : "",
                 qg.replace ? qg.newcomer.source : "", qg.netdeny, qg.netdeny_seq);
    if (h > 0 && n + h < (int)sizeof b) n += h;
    if (!strcmp(b, qg.last)) return;
    memcpy(qg.last, b, (unsigned)n + 1);
    char out[4096 + 24];
    int m = snprintf(out, sizeof out, "seq=%u\n%s", ++qg.seq, b);
    write_whole(QG_STATE, out, (unsigned)m, 0);
    qg.written_at = qg_ms();
}

static void event(const char *what, const char *name) {
    copy(qg.event, what, sizeof qg.event);
    copy(qg.event_name, name, sizeof qg.event_name);
    ++qg.event_seq;
}

/* ---- the car's side: the guest's song through bluetoothd's shanling.bluez
 * (docs/internals.md#guest-play) ---- */

static void notify(int ev, const unsigned char *data, int n) {
    DBusMessage *m = dbus_message_new_method_call("shanling.bluez", "/shanling/bluez",
                                                  "shanling.bluez", "NotifyChanged");
    if (!m) return;
    DBusMessageIter it, arr;
    dbus_message_iter_init_append(m, &it);
    dbus_message_iter_append_basic(&it, T_I32, &ev);
    dbus_message_iter_open_container(&it, T_ARRAY, "y", &arr);
    dbus_message_iter_append_fixed_array(&arr, T_BYTE, &data, n);
    dbus_message_iter_close_container(&it, &arr);
    dbus_message_set_no_reply(m, 1);
    dbus_connection_send(qg.bus, m, 0);
    dbus_message_unref(m);
}

/* libbtctl's btctl_notify_track_changed record (event 0x108): track, count, ms, attributes, then
 * the title (16-bit id 1, 16-bit length with its NUL) and the artist (2), album (3) and genre (6)
 * present, each an 8-bit id, 0, a 16-bit little-endian length and the text with its NUL. Exposed
 * for the tests. */
int qg_track_record(unsigned track, unsigned tracks, unsigned ms, const char *title,
                    const char *artist, const char *album, const char *genre, unsigned char *out,
                    int cap) {
    const char *text[4] = { title, artist, album, genre };
    static const unsigned char id[4] = { 1, 2, 3, 6 };
    unsigned w[4] = { track, tracks, ms, 1 };
    int n = 16;
    for (int i = 1; i < 4; ++i) w[3] += text[i] && *text[i];
    if (cap < 16) return 0;
    memcpy(out, w, 16);
    for (int i = 0; i < 4; ++i) {
        if (i && !(text[i] && *text[i])) continue;
        const char *s = text[i] ? text[i] : "";
        unsigned len = strlen(s) + 1;
        if (n + 4 + (int)len > cap) return 0;
        if (!i) {
            uint16_t h[2] = { 1, (uint16_t)len };
            memcpy(out + n, h, 4);
        } else
            out[n] = id[i], out[n + 1] = 0, out[n + 2] = len & 255, out[n + 3] = len >> 8;
        memcpy(out + n + 4, s, len);
        n += 4 + (int)len;
    }
    return n;
}

static int status_byte(void) {
    return !strcmp(qg.status, "playing") ? 1 : !strcmp(qg.status, "paused") ? 2 : 0;
}

static void notify_car(int force) {
    if (!qg.playing || !*qg.guest.path) return; /* nothing of the guest's before its stream */
    if (!strcmp(qg.guest.source, "dlna"))
        return; /* hciplayer plays it: demo sends its song itself */
    unsigned char rec[16 + 4 * (QG_FIELD + 4)];
    int n = qg_track_record(qg.track, qg.tracks, qg.duration, qg.title, qg.artist, qg.album,
                            qg.genre, rec, sizeof rec);
    unsigned h = 2166136261u;
    for (int i = 0; i < n; ++i) h = (h ^ rec[i]) * 16777619u;
    if (n && (force || h != qg.sent_hash)) notify(0x108, rec, n), qg.sent_hash = h;
    unsigned char s = (unsigned char)status_byte();
    if (force || s != qg.sent_status) notify(0x107, &s, 1), qg.sent_status = s;
}

/* ---- the guest's player (org.bluez.MediaPlayer1) ---- */

static void player_props(DBusMessageIter *dict) {
    DBusMessageIter e, v;
    while (dbus_message_iter_get_arg_type(dict) == T_DICT) {
        const char *key, *s;
        unsigned u;
        dbus_message_iter_recurse(dict, &e);
        dbus_message_iter_get_basic(&e, &key);
        dbus_message_iter_next(&e);
        dbus_message_iter_recurse(&e, &v);
        int t = dbus_message_iter_get_arg_type(&v);
        if (!strcmp(key, "Status") && t == T_STR) {
            dbus_message_iter_get_basic(&v, &s);
            copy(qg.status, s, sizeof qg.status);
            qg.pos = position(), qg.pos_at = qg_ms();
        } else if (!strcmp(key, "Position") && t == T_U32) {
            dbus_message_iter_get_basic(&v, &u);
            qg.pos = u, qg.pos_at = qg_ms();
        } else if (!strcmp(key, "Track") && t == T_ARRAY) {
            DBusMessageIter tr, te, tv;
            dbus_message_iter_recurse(&v, &tr);
            qg.title[0] = qg.artist[0] = qg.album[0] = qg.genre[0] = 0;
            qg.track = qg.tracks = qg.duration = 0;
            while (dbus_message_iter_get_arg_type(&tr) == T_DICT) {
                const char *k;
                dbus_message_iter_recurse(&tr, &te);
                dbus_message_iter_get_basic(&te, &k);
                dbus_message_iter_next(&te);
                dbus_message_iter_recurse(&te, &tv);
                int tt = dbus_message_iter_get_arg_type(&tv);
                if (tt == T_STR) {
                    dbus_message_iter_get_basic(&tv, &s);
                    if (!strcmp(k, "Title")) copy(qg.title, s, sizeof qg.title);
                    if (!strcmp(k, "Artist")) copy(qg.artist, s, sizeof qg.artist);
                    if (!strcmp(k, "Album")) copy(qg.album, s, sizeof qg.album);
                    if (!strcmp(k, "Genre")) copy(qg.genre, s, sizeof qg.genre);
                } else if (tt == T_U32) {
                    dbus_message_iter_get_basic(&tv, &u);
                    if (!strcmp(k, "TrackNumber")) qg.track = u;
                    if (!strcmp(k, "NumberOfTracks")) qg.tracks = u;
                    if (!strcmp(k, "Duration")) qg.duration = u;
                }
                dbus_message_iter_next(&tr);
            }
        }
        dbus_message_iter_next(dict);
    }
}

static int under(const char *path, const char *mac) {
    char dev[64];
    dev_path(mac, dev, sizeof dev);
    unsigned n = strlen(dev);
    return path && !strncmp(path, dev, n) && path[n] == '/';
}

static void player_read(const char *path) {
    copy(qg.player, path, sizeof qg.player);
    const char *iface = "org.bluez.MediaPlayer1";
    DBusMessage *r =
        call(BLUEZ, path, "org.freedesktop.DBus.Properties", "GetAll", T_STR, &iface, T_END);
    DBusMessageIter it, dict;
    if (r && dbus_message_iter_init(r, &it) && dbus_message_iter_get_arg_type(&it) == T_ARRAY) {
        dbus_message_iter_recurse(&it, &dict);
        player_props(&dict);
    }
    if (r) dbus_message_unref(r);
}

/* The guest's MediaPlayer1, if BlueZ has one for it yet. */
static void player_find(void) {
    DBusMessage *r =
        call(BLUEZ, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", T_END);
    DBusMessageIter it, objs, ent, ifs, ife;
    char found[200] = { 0 };
    if (r && dbus_message_iter_init(r, &it) && dbus_message_iter_get_arg_type(&it) == T_ARRAY) {
        dbus_message_iter_recurse(&it, &objs);
        for (; dbus_message_iter_get_arg_type(&objs) == T_DICT && !*found;
             dbus_message_iter_next(&objs)) {
            const char *path, *iface;
            dbus_message_iter_recurse(&objs, &ent);
            dbus_message_iter_get_basic(&ent, &path);
            if (!under(path, qg.guest.mac)) continue;
            dbus_message_iter_next(&ent);
            dbus_message_iter_recurse(&ent, &ifs);
            for (; dbus_message_iter_get_arg_type(&ifs) == T_DICT; dbus_message_iter_next(&ifs)) {
                dbus_message_iter_recurse(&ifs, &ife);
                dbus_message_iter_get_basic(&ife, &iface);
                if (!strcmp(iface, "org.bluez.MediaPlayer1")) copy(found, path, sizeof found);
            }
        }
    }
    if (r) dbus_message_unref(r);
    if (*found) player_read(found);
}

static void spot_send(char c);

static void player_key(const char *key) {
    if (qg.playing && !strcmp(qg.guest.source, "spotify")) { /* librespot's own letters */
        char c = !strcmp(key, "play")     ? 'P'
                 : !strcmp(key, "pause")  ? 's'
                 : !strcmp(key, "next")   ? 'n'
                 : !strcmp(key, "prev")   ? 'b'
                 : !strcmp(key, "toggle") ? 'p'
                                          : 0;
        if (c) spot_send(c);
        return;
    }
    if (!qg.playing || !*qg.player) return;
    const char *method = !strcmp(key, "play")     ? "Play"
                         : !strcmp(key, "pause")  ? "Pause"
                         : !strcmp(key, "next")   ? "Next"
                         : !strcmp(key, "prev")   ? "Previous"
                         : !strcmp(key, "toggle") ? (status_byte() == 1 ? "Pause" : "Play")
                                                  : 0;
    if (method) send_only(BLUEZ, qg.player, "org.bluez.MediaPlayer1", method, T_END);
}

/* ---- a Spotify Connect guest: librespot's status file and control socket
 * (docs/internals.md#spotify) ---- */

static void session_end(const char *why, int disconnect);

/* librespot's song and state, as the guest's while a Spotify Connect guest plays. */
static void spot_read(void) {
    char raw[2048], v[QG_FIELD];
    if (!read_whole(QG_SPOT ".state", raw, sizeof raw)) return;
    qg_field(raw, "state", v, sizeof v);
    if (!strcmp(v, "none")) { /* the phone chose another device */
        session_end("left", 0);
        return;
    }
    copy(qg.status, v, sizeof qg.status);
    qg_field(raw, "title", qg.title, sizeof qg.title);
    qg_field(raw, "artist", qg.artist, sizeof qg.artist);
    qg_field(raw, "album", qg.album, sizeof qg.album);
    qg.duration = (unsigned)atoi((qg_field(raw, "duration", v, sizeof v), v));
    qg.pos = (unsigned)atoi((qg_field(raw, "position", v, sizeof v), v));
    qg.pos_at =
        atoi((qg_field(raw, "at", v, sizeof v), v)); /* librespot's CLOCK_MONOTONIC ms, as qg_ms */
}

static void spot_send(char c) {
    struct sockaddr_un a;
    memset(&a, 0, sizeof a);
    a.sun_family = AF_UNIX;
    copy(a.sun_path, QG_SPOT ".sock", sizeof a.sun_path);
    int s = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (s < 0) return;
    sendto(s, &c, 1, MSG_DONTWAIT, (const void *)&a, sizeof a);
    close(s);
}

/* ---- Let a Guest Connect ---- */

static int agent_reply(DBusMessage *req, int ok) {
    if (!req) return 0;
    DBusMessage *r =
        ok ? dbus_message_new_method_return(req)
           : dbus_message_new_error(req, "org.bluez.Error.Rejected", "Declined on the Q2");
    if (r) dbus_connection_send(qg.bus, r, 0), dbus_message_unref(r);
    dbus_message_unref(req);
    return 1;
}

static void window_close(void) {
    if (!qg.window_end) return;
    qg.window_end = 0;
    int no = 0;
    set_prop(ADAPTER, "org.bluez.Adapter1", "Discoverable", T_BOOL, &no);
    if (*qg.name) {
        const char *alias = qg.saved_alias;
        set_prop(ADAPTER, "org.bluez.Adapter1", "Alias", T_STR, &alias);
    }
    if (qg.agent) {
        const char *p = AGENT_PATH;
        DBusMessage *r = call(BLUEZ, "/org/bluez", "org.bluez.AgentManager1", "UnregisterAgent",
                              T_PATH, &p, T_END);
        if (r) dbus_message_unref(r);
        qg.agent = 0;
    }
}

static void window_open(void) {
    if (!qg.bluez_up || !qg.enabled) return;
    if (!qg.window_end) {
        get_str(ADAPTER, "org.bluez.Adapter1", "Alias", qg.saved_alias, sizeof qg.saved_alias);
        const char *p = AGENT_PATH, *cap = "DisplayYesNo";
        DBusMessage *r = call(BLUEZ, "/org/bluez", "org.bluez.AgentManager1", "RegisterAgent",
                              T_PATH, &p, T_STR, &cap, T_END);
        qg.agent = r != 0;
        if (r) dbus_message_unref(r);
        r = call(BLUEZ, "/org/bluez", "org.bluez.AgentManager1", "RequestDefaultAgent", T_PATH, &p,
                 T_END);
        if (r) dbus_message_unref(r);
        if (*qg.name) {
            const char *alias = qg.name;
            set_prop(ADAPTER, "org.bluez.Adapter1", "Alias", T_STR, &alias);
        }
        int yes = 1;
        set_prop(ADAPTER, "org.bluez.Adapter1", "Pairable", T_BOOL, &yes);
        set_prop(ADAPTER, "org.bluez.Adapter1", "Discoverable", T_BOOL, &yes);
    }
    qg.window_end = qg_ms() + QG_WINDOW_S * 1000L;
}

/* ---- the session ---- */

static void out_find(void);

/* A network guest's receiver to stop (the payload does it: docs/internals.md#guest-play-receivers).
 */
static void net_deny(const char *source) {
    copy(qg.netdeny, source, sizeof qg.netdeny);
    ++qg.netdeny_seq;
}

/* A guest turned away or ended: a phone disconnected, a receiver stopped. */
static void refuse(const qg_dev_t *d) {
    if (*d->source)
        net_deny(d->source);
    else
        dev_call(d->mac, "Disconnect");
}

static void session_start(const qg_dev_t *d) {
    qg.guest = *d;
    qg.playing = 1;
    qg.session_at = qg_ms();
    qg.arrive_by = *d->path ? 0 : qg.session_at + QG_ARRIVE_MS;
    qg.paused_since = 0;
    qg.resend_at = qg.session_at + QG_RESEND_MS;
    qg.title[0] = qg.artist[0] = qg.album[0] = qg.genre[0] = qg.player[0] = 0;
    qg.track = qg.tracks = qg.duration = qg.pos = 0;
    qg.sent_hash = 0, qg.sent_status = -1;
    copy(qg.status, *d->path ? "playing" : "", sizeof qg.status);
    qg_trusted_t *t = trusted_find(d->mac);
    if (t) t->last = time(0), trusted_save();
    window_close();
    out_find();
    if (*d->path && !*d->source) aplay_start(), player_find();
    if (!strcmp(d->source, "airplay"))
        copy(qg.title, "AirPlay", sizeof qg.title); /* shairport tells none */
}

/* The guest's stream is here (its PCM): play it. */
static void session_arrived(const qg_dev_t *d) {
    copy(qg.guest.path, d->path, sizeof qg.guest.path);
    copy(qg.guest.codec, d->codec, sizeof qg.guest.codec);
    qg.guest.on_main = d->on_main;
    qg.arrive_by = 0;
    qg.resend_at = qg_ms() + QG_RESEND_MS;
    if (!*qg.status) copy(qg.status, "playing", sizeof qg.status);
    if (*qg.guest.source) return; /* a receiver plays by itself */
    if (!qg.aplay) aplay_start();
    player_find();
}

static void session_end(const char *why, int disconnect) {
    if (!qg.playing) return;
    stop_child(&qg.aplay);
    if (disconnect) refuse(&qg.guest);
    event(why, qg.guest.name);
    qg.playing = 0;
    qg.approved[0] = 0;
    memset(&qg.guest, 0, sizeof qg.guest);
    qg.player[0] = qg.status[0] = 0;
}

static void ask_start(int kind, const qg_dev_t *d, DBusMessage *req, unsigned code) {
    qg.ask = kind;
    qg.asking = *d;
    qg.pending = req ? dbus_message_ref(req) : 0;
    if (code < 1000000 && kind == ASK_PAIR && req &&
        dbus_message_is_method_call(req, "org.bluez.Agent1", "RequestConfirmation"))
        snprintf(qg.code, sizeof qg.code, "%06u", code);
    else
        qg.code[0] = 0;
    qg.ask_end = qg_ms() + QG_ASK_S * 1000L;
}

static void ask_end(int allow, int always, const char *why) {
    if (!qg.ask) return;
    qg_dev_t d = qg.asking;
    int kind = qg.ask;
    qg.ask = 0;
    qg.code[0] = 0;
    agent_reply(qg.pending, allow);
    qg.pending = 0;
    if (!allow) {
        if (kind == ASK_STREAM) refuse(&d);
        event(why, d.name);
        return;
    }
    if (always) trusted_add(&d);
    copy(qg.approved, d.mac, sizeof qg.approved);
    session_start(&d);
}

static void replace_start(const qg_dev_t *d, DBusMessage *req) {
    qg.replace = 1;
    qg.newcomer = *d;
    qg.rep_pending = req ? dbus_message_ref(req) : 0;
    qg.replace_end = qg_ms() + QG_ASK_S * 1000L;
}

static void replace_end(int replace) {
    if (!qg.replace) return;
    qg.replace = 0;
    qg_dev_t d = qg.newcomer;
    int paired = agent_reply(qg.rep_pending, replace);
    qg.rep_pending = 0;
    if (!replace) {
        if (!paired) refuse(&d);
        event("kept", d.name);
        return;
    }
    session_end("replaced", 1);
    copy(qg.approved, d.mac, sizeof qg.approved);
    session_start(&d);
}

/* Someone wants to play: while pairing (req, the agent's call) or by starting a stream (d.path). */
static void wants(qg_dev_t *d, DBusMessage *req, unsigned code) {
    if (!qg.enabled) {
        agent_reply(req ? dbus_message_ref(req) : 0, 0);
        return;
    }
    if (qg.playing &&
        !strcmp(d->mac, qg.guest.mac)) { /* the guest allowed while pairing, its stream now */
        if (*d->path) session_arrived(d);
        agent_reply(req ? dbus_message_ref(req) : 0, 1);
        return;
    }
    if (!*d->name) dev_name(d);
    if (qg.ask || qg.replace) { /* one prompt at a time */
        agent_reply(req ? dbus_message_ref(req) : 0, 0);
        if (!req) refuse(d);
        event("refused", d->name);
        return;
    }
    if (qg.playing) {
        replace_start(d, req);
        return;
    }
    qg_trusted_t *t = trusted_find(d->mac);
    if (!req && (!strcmp(qg.approved, d->mac) || (t && !t->ask))) {
        session_start(d);
        return;
    }
    if (!req && qg.trusted_only && !t) {
        refuse(d);
        event("refused", d->name);
        return;
    }
    ask_start(req ? ASK_PAIR : ASK_STREAM, d, req, code);
}

/* A PCM appeared on a bluealsa daemon. */
static void pcm_added(const char *path, DBusMessageIter *dict, const char *sender) {
    qg_dev_t d;
    memset(&d, 0, sizeof d);
    int sink = pcm_props(dict, &d);
    if (sink < 0) return;
    copy(d.path, path, sizeof d.path);
    if (!sink) { /* the car or headset: where the guest plays */
        if (strstr(path, "/a2dp")) qg.out = d, dev_name(&qg.out);
        return;
    }
    d.on_main = !*qg.sink_owner || !sender || strcmp(sender, qg.sink_owner);
    wants(&d, 0, 0);
}

static void pcm_removed(const char *path) {
    if (qg.playing && !strcmp(path, qg.guest.path))
        session_end(!strcmp(qg.status, "playing") ? "lost" : "left", 0);
    else if (qg.ask == ASK_STREAM && !strcmp(path, qg.asking.path)) {
        qg.ask = 0;
        event("left", qg.asking.name);
    } else if (qg.replace && !strcmp(path, qg.newcomer.path)) {
        qg.replace = 0;
        event("left", qg.newcomer.name);
    } else if (!strcmp(path, qg.out.path))
        memset(&qg.out, 0, sizeof qg.out);
}

static void pcms_list(const char *srv) {
    DBusMessage *r = call(srv, "/org/bluealsa", "org.bluealsa.Manager1", "GetPCMs", T_END);
    DBusMessageIter it, objs, ent, dict;
    if (r && dbus_message_iter_init(r, &it) && dbus_message_iter_get_arg_type(&it) == T_ARRAY) {
        dbus_message_iter_recurse(&it, &objs);
        for (; dbus_message_iter_get_arg_type(&objs) == T_DICT; dbus_message_iter_next(&objs)) {
            const char *path;
            dbus_message_iter_recurse(&objs, &ent);
            dbus_message_iter_get_basic(&ent, &path);
            dbus_message_iter_next(&ent);
            dbus_message_iter_recurse(&ent, &dict);
            pcm_added(path, &dict, !strcmp(srv, SINK_SRV) ? qg.sink_owner : "");
        }
    }
    if (r) dbus_message_unref(r);
}

static void out_find(void) {
    if (!*qg.out.mac && qg.main_up) pcms_list(MAIN_SRV);
}

/* ---- the switch ---- */

static void main_fix(void) { system(qg.enabled ? QG_MAIN_SOURCE : QG_MAIN_STOCK); }

static void set_enabled(int on) {
    if (on == qg.enabled) return;
    qg.enabled = on;
    config_save();
    if (!on) {
        if (qg.ask) ask_end(0, 0, "declined");
        if (qg.replace) replace_end(0);
        session_end("stopped", 1);
        window_close();
        stop_child(&qg.sinkd);
    }
    copy(qg.reconnect, qg.out.mac, sizeof qg.reconnect);
    main_fix();
    hotspot_apply();
}

/* ---- commands ---- */

static void command(char *line) {
    char *arg = strchr(line, ' ');
    if (arg) *arg++ = 0;
    if (!strcmp(line, "on") || !strcmp(line, "off"))
        set_enabled(!strcmp(line, "on"));
    else if (!strcmp(line, "open"))
        window_open();
    else if (!strcmp(line, "close"))
        window_close();
    else if (!strcmp(line, "allow-once") || !strcmp(line, "allow-always"))
        ask_end(1, !strcmp(line, "allow-always"), "");
    else if (!strcmp(line, "decline"))
        ask_end(0, 0, "declined");
    else if (!strcmp(line, "keep") || !strcmp(line, "replace"))
        replace_end(!strcmp(line, "replace"));
    else if (!strcmp(line, "stop"))
        session_end("stopped", 1);
    else if (!strcmp(line, "key") && arg)
        player_key(arg);
    else if (!strcmp(line, "net") && arg) { /* net KIND ID NAME: a receiver's session started */
        char *id = strchr(arg, ' '), *name = id ? strchr(id + 1, ' ') : 0;
        if (!name) return;
        *id++ = 0, *name++ = 0;
        qg_dev_t d;
        memset(&d, 0, sizeof d);
        copy(d.source, arg, sizeof d.source);
        snprintf(d.mac, sizeof d.mac, "%s:%s", arg, id);
        copy(d.name, name, sizeof d.name);
        copy(d.path, "net", sizeof d.path); /* here already: no stream to wait for */
        wants(&d, 0, 0);
    } else if (!strcmp(line, "net-end") && arg) {
        if (qg.playing && !strcmp(qg.guest.source, arg)) session_end("left", 0);
        if (qg.ask && !strcmp(qg.asking.source, arg)) qg.ask = 0, event("left", qg.asking.name);
        if (qg.replace && !strcmp(qg.newcomer.source, arg))
            qg.replace = 0, event("left", qg.newcomer.name);
    } else if (!strcmp(line, "hotspot") && arg) {
        qg.hotspot = !strcmp(arg, "on");
        config_save();
        hotspot_apply();
    } else if (!strcmp(line, "hotspot-pass") && arg) {
        if (!qg_pass_ok(arg)) return;
        copy(qg.hotspot_pass, arg, sizeof qg.hotspot_pass);
        config_save();
        if (qg.hotspot_up) hotspot_apply();
    } else if (!strcmp(line, "name") && arg) {
        copy(qg.name, arg, sizeof qg.name);
        config_save();
        if (qg.hotspot_up) hotspot_apply();
    } else if (!strcmp(line, "allow") && arg) {
        qg.trusted_only = !strcmp(arg, "trusted");
        config_save();
    } else if (!strcmp(line, "leave") && arg) {
        qg.stay = !strcmp(arg, "stay");
        config_save();
    } else if ((!strcmp(line, "forget") || !strcmp(line, "ask") || !strcmp(line, "rename")) &&
               arg) {
        char *rest = strchr(arg, ' ');
        if (rest) *rest++ = 0;
        qg_trusted_t *t = trusted_find(arg);
        if (!t) return;
        if (!strcmp(line, "ask") && rest)
            t->ask = atoi(rest) != 0;
        else if (!strcmp(line, "rename") && rest && *rest)
            copy(t->name, rest, sizeof t->name);
        else if (!strcmp(line, "forget")) {
            char path[64];
            const char *p = path;
            if (qg.playing && !strcmp(qg.guest.mac, arg)) session_end("stopped", 1);
            dev_path(arg, path, sizeof path);
            send_only(BLUEZ, ADAPTER, "org.bluez.Adapter1", "RemoveDevice", T_PATH, &p, T_END);
            *t = qg.trusted[--qg.n_trusted];
        }
        trusted_save();
    }
}

static void fifo_read(void) {
    char buf[256];
    ssize_t got;
    while ((got = read(qg.fifo, buf, sizeof buf)) > 0)
        for (ssize_t i = 0; i < got; ++i) {
            if (buf[i] == '\n') {
                qg.line[qg.line_n] = 0;
                if (qg.line_n) command(qg.line);
                qg.line_n = 0;
            } else if (qg.line_n + 1 < sizeof qg.line)
                qg.line[qg.line_n++] = buf[i];
        }
}

/* ---- the agent (Let a Guest Connect's pairing) ---- */

static int agent_msg(DBusConnection *c, DBusMessage *m, void *u) {
    (void)c, (void)u;
    const char *iface = dbus_message_get_interface(m), *dev = 0;
    if (!iface || strcmp(iface, "org.bluez.Agent1"))
        return DBUS_NOT_YET_HANDLED; /* Introspect and the like */
    unsigned code = 1000000;
    DBusMessageIter it;
    if (dbus_message_iter_init(m, &it) && dbus_message_iter_get_arg_type(&it) == T_PATH) {
        dbus_message_iter_get_basic(&it, &dev);
        if (dbus_message_iter_next(&it) && dbus_message_iter_get_arg_type(&it) == T_U32)
            dbus_message_iter_get_basic(&it, &code);
    }
    if (dbus_message_is_method_call(m, "org.bluez.Agent1", "RequestConfirmation") ||
        dbus_message_is_method_call(m, "org.bluez.Agent1", "RequestAuthorization")) {
        qg_dev_t d;
        memset(&d, 0, sizeof d);
        if (!path_mac(dev, d.mac)) return agent_reply(dbus_message_ref(m), 0), DBUS_HANDLED;
        wants(&d, m, code);
    } else if (dbus_message_is_method_call(m, "org.bluez.Agent1", "AuthorizeService") ||
               dbus_message_is_method_call(m, "org.bluez.Agent1", "Release") ||
               dbus_message_is_method_call(m, "org.bluez.Agent1", "DisplayPasskey") ||
               dbus_message_is_method_call(m, "org.bluez.Agent1", "DisplayPinCode"))
        agent_reply(dbus_message_ref(m), 1);
    else if (dbus_message_is_method_call(m, "org.bluez.Agent1", "Cancel")) {
        agent_reply(dbus_message_ref(m), 1);
        if (qg.ask == ASK_PAIR) {
            if (qg.pending) dbus_message_unref(qg.pending), qg.pending = 0;
            qg.ask = 0;
            qg.code[0] = 0;
            event("failed", qg.asking.name);
        }
    } else /* RequestPinCode, RequestPasskey: a guest pairs with a code it shows or none */
        agent_reply(dbus_message_ref(m), 0);
    return DBUS_HANDLED;
}

/* ---- signals ---- */

static int filter(DBusConnection *c, DBusMessage *m, void *u) {
    (void)c, (void)u;
    DBusMessageIter it;
    const char *path = dbus_message_get_path(m);
    if (dbus_message_is_signal(m, "org.freedesktop.DBus", "NameOwnerChanged")) {
        const char *name, *old, *now;
        if (!dbus_message_iter_init(m, &it)) return DBUS_NOT_YET_HANDLED;
        dbus_message_iter_get_basic(&it, &name);
        dbus_message_iter_next(&it);
        dbus_message_iter_get_basic(&it, &old);
        dbus_message_iter_next(&it);
        dbus_message_iter_get_basic(&it, &now);
        if (!strcmp(name, SINK_SRV)) {
            copy(qg.sink_owner, now, sizeof qg.sink_owner);
            if (*now) pcms_list(SINK_SRV);
        } else if (!strcmp(name, MAIN_SRV)) {
            qg.main_up = *now != 0;
            qg.main_up_at = qg_ms();
            if (!*now) memset(&qg.out, 0, sizeof qg.out);
            if (*now && *qg.reconnect) { /* the car first, then the sink role
                                            (docs/internals.md#the-relay) */
                char p[64];
                const char *uuid = A2DP_SINK_UUID;
                dev_path(qg.reconnect, p, sizeof p);
                send_only(BLUEZ, p, "org.bluez.Device1", "ConnectProfile", T_STR, &uuid, T_END);
                qg.reconnect[0] = 0;
            }
            if (*now) pcms_list(MAIN_SRV);
        } else if (!strcmp(name, BLUEZ)) {
            qg.bluez_up = *now != 0;
            if (!*now) { /* Bluetooth off: everything with it */
                if (qg.ask) ask_end(0, 0, "failed");
                if (qg.replace) replace_end(0);
                session_end("lost", 0);
                qg.window_end = 0;
                qg.agent = 0;
                stop_child(&qg.sinkd);
            } else if (qg.enabled)
                main_fix();
        }
        return DBUS_NOT_YET_HANDLED;
    }
    if (dbus_message_is_signal(m, "org.bluealsa.Manager1", "PCMAdded")) {
        const char *p;
        DBusMessageIter dict;
        if (dbus_message_iter_init(m, &it) && dbus_message_iter_get_arg_type(&it) == T_PATH) {
            dbus_message_iter_get_basic(&it, &p);
            dbus_message_iter_next(&it);
            dbus_message_iter_recurse(&it, &dict);
            pcm_added(p, &dict, dbus_message_get_sender(m));
        }
    } else if (dbus_message_is_signal(m, "org.bluealsa.Manager1", "PCMRemoved")) {
        const char *p;
        if (dbus_message_iter_init(m, &it) && dbus_message_iter_get_arg_type(&it) == T_PATH)
            dbus_message_iter_get_basic(&it, &p), pcm_removed(p);
    } else if (qg.playing &&
               dbus_message_is_signal(m, "org.freedesktop.DBus.Properties", "PropertiesChanged") &&
               under(path, qg.guest.mac)) {
        const char *iface;
        DBusMessageIter dict;
        if (dbus_message_iter_init(m, &it)) {
            dbus_message_iter_get_basic(&it, &iface);
            if (!strcmp(iface, "org.bluez.MediaPlayer1")) {
                if (strcmp(path, qg.player)) copy(qg.player, path, sizeof qg.player);
                dbus_message_iter_next(&it);
                dbus_message_iter_recurse(&it, &dict);
                player_props(&dict);
            }
        }
    } else if (qg.playing &&
               dbus_message_is_signal(m, "org.freedesktop.DBus.ObjectManager", "InterfacesAdded")) {
        const char *p;
        if (dbus_message_iter_init(m, &it) && dbus_message_iter_get_arg_type(&it) == T_PATH) {
            dbus_message_iter_get_basic(&it, &p);
            if (under(p, qg.guest.mac) && strstr(p, "/player")) player_read(p);
        }
    }
    return DBUS_NOT_YET_HANDLED;
}

/* ---- the loop ---- */

static void tick(void) {
    long now = qg_ms();
    reap();
    if (qg.window_end && now >= qg.window_end) window_close();
    if (qg.ask && now >= qg.ask_end) ask_end(0, 0, "timeout");
    if (qg.replace && now >= qg.replace_end) replace_end(0);
    if (qg.playing && qg.arrive_by && now >= qg.arrive_by) session_end("failed", 1);
    if (qg.playing) {
        int stopped = !strcmp(qg.status, "paused") || !strcmp(qg.status, "stopped");
        if (!stopped)
            qg.paused_since = 0;
        else if (!qg.paused_since)
            qg.paused_since = now;
        else if (now - qg.paused_since >= QG_GIVEUP_MS)
            session_end("gaveup", 1);
    }
    if (qg.playing && *qg.guest.path && !*qg.guest.source && !qg.aplay &&
        now - qg.aplay_at >= QG_RESPAWN_MS)
        aplay_start();
    if (qg.playing && !strcmp(qg.guest.source, "spotify")) spot_read();
    /* the sink role once the car or headset had its chance to connect first */
    if (qg.enabled && qg.bluez_up && qg.main_up && !qg.sinkd && !*qg.reconnect &&
        now - qg.sinkd_at >= QG_RESPAWN_MS &&
        (*qg.out.mac || now - qg.main_up_at >= QG_OUT_WAIT_MS))
        sinkd_start();
    if (qg.hotspot_up && now - qg.hotspot_at >= QG_HOTSPOT_CHECK_MS) hotspot_check();
    if (qg.playing && qg.resend_at && now >= qg.resend_at) qg.resend_at = 0, notify_car(1);
    notify_car(0);
    state_write();
}

static int name_has_owner(const char *name, char *owner, unsigned n) {
    DBusMessage *r = call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "GetNameOwner", T_STR, &name, T_END);
    const char *s = "";
    DBusMessageIter it;
    if (r && dbus_message_iter_init(r, &it) && dbus_message_iter_get_arg_type(&it) == T_STR)
        dbus_message_iter_get_basic(&it, &s);
    if (owner) copy(owner, s, n);
    if (r) dbus_message_unref(r);
    return *s != 0;
}

static void on_term(int sig) { (void)sig, qg.quit = 1; }

int main(void) {
    char pid[16];
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_term);
    signal(SIGHUP, on_term);
    int n = snprintf(pid, sizeof pid, "%d\n", getpid());
    write_whole(QG_PID, pid, (unsigned)n, 0);
    config_load();
    trusted_load();
    if (!qg_pass_ok(qg.hotspot_pass)) hotspot_new_pass(), config_save();
    unlink(QG_CMD);
    mkfifo(QG_CMD, 0600);
    qg.fifo = open(QG_CMD, O_RDONLY | O_NONBLOCK);
    qg.fifo_keep = open(QG_CMD, O_WRONLY | O_NONBLOCK); /* so the FIFO never reads as ended */
    DBusError e;
    dbus_error_init(&e);
    qg.bus = dbus_bus_get(DBUS_BUS_SYSTEM, &e);
    dbus_error_free(&e);
    if (!qg.bus || qg.fifo < 0) return 1;
    dbus_connection_set_exit_on_disconnect(qg.bus, 0);
    static const DBusObjectPathVTable vt = { 0, agent_msg, { 0 } };
    dbus_connection_register_object_path(qg.bus, AGENT_PATH, &vt, 0);
    dbus_connection_add_filter(qg.bus, filter, 0, 0);
    static const char *const match[] = {
        "type='signal',sender='org.freedesktop.DBus',member='NameOwnerChanged'",
        "type='signal',interface='org.bluealsa.Manager1'",
        "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.Properties',member='"
        "PropertiesChanged'",
        "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.ObjectManager',member='"
        "InterfacesAdded'",
    };
    for (unsigned i = 0; i < sizeof match / sizeof *match; ++i)
        dbus_bus_add_match(qg.bus, match[i], 0);
    qg.bluez_up = name_has_owner(BLUEZ, 0, 0);
    qg.main_up = name_has_owner(MAIN_SRV, 0, 0);
    qg.main_up_at = qg_ms();
    if (name_has_owner(SINK_SRV, qg.sink_owner, sizeof qg.sink_owner)) pcms_list(SINK_SRV);
    if (qg.main_up) pcms_list(MAIN_SRV);
    if (qg.enabled && qg.bluez_up) main_fix();
    hotspot_apply();
    int bus_fd = -1;
    dbus_connection_get_unix_fd(qg.bus, &bus_fd);
    while (!qg.quit && dbus_connection_get_is_connected(qg.bus)) {
        struct pollfd p[2] = { { bus_fd, POLLIN, 0 }, { qg.fifo, POLLIN, 0 } };
        poll(p, 2, QG_TICK_MS);
        dbus_connection_read_write(qg.bus, 0);
        while (dbus_connection_dispatch(qg.bus) == DBUS_DATA_REMAINS) {}
        fifo_read();
        tick();
        dbus_connection_flush(qg.bus);
    }
    if (qg.ask) ask_end(0, 0, "failed");
    session_end("stopped", 0);
    window_close();
    stop_child(&qg.sinkd);
    qg.enabled = 0;
    hotspot_apply();
    unlink(QG_STATE);
    unlink(QG_PID);
    unlink(QG_CMD);
    return 0;
}
