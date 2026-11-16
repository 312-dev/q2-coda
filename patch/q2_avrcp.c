/* q2-avrcp: the Q2's Now Playing to a Bluetooth headset or car (docs/internals.md#track-metadata).
 *
 * BlueZ's AVRCP target answers a controller's GetElementAttributes and its TRACK_CHANGED and
 * PLAYBACK_STATUS_CHANGED notifications from a player an application registers with
 * org.bluez.Media1.RegisterPlayer: an object whose properties are MPRIS's (org.mpris.MediaPlayer2.Player:
 * PlaybackStatus, Position, Metadata with xesam:title, xesam:artist, xesam:album, mpris:length,
 * xesam:trackNumber), kept current with PropertiesChanged. Stock registers none, so a car showed no
 * title. demo writes Now Playing to /tmp/coda-rc.now while Bluetooth is on (patch/coda_rc_shim.c
 * cr_now, Car Remote's file); this daemon, started by demo, registers QA_PATH with BlueZ once
 * org.bluez is on the bus (again whenever bluetoothd comes back) and sends PropertiesChanged when
 * the file changes.
 *
 * CanControl is false: BlueZ then hands a controller's Play, Pause, Next and Previous to its uinput
 * device as before, which demo's headset worker reads (patch/headset.c), rather than to this
 * player's methods. */
#include "coda_rc_sys.h"

/* ---- the libdbus 1.12 API used here (no headers on the Q2's side) ---- */
typedef struct DBusConnection DBusConnection;
typedef struct DBusMessage DBusMessage;
typedef struct DBusPendingCall DBusPendingCall;
typedef struct { const char *name, *message; unsigned dummy; void *pad; } DBusError;
typedef struct { void *p[4]; int i[16]; void *q[4]; } DBusMessageIter;
typedef int (*dbus_msg_fn)(DBusConnection *, DBusMessage *, void *);
typedef struct { void (*unregister)(DBusConnection *, void *); dbus_msg_fn message; void (*pad[4])(void *); } DBusObjectPathVTable;
void dbus_error_init(DBusError *);
void dbus_error_free(DBusError *);
DBusConnection *dbus_bus_get(int, DBusError *);
void dbus_bus_add_match(DBusConnection *, const char *, DBusError *);
int dbus_bus_name_has_owner(DBusConnection *, const char *, DBusError *);
void dbus_connection_set_exit_on_disconnect(DBusConnection *, int);
int dbus_connection_register_object_path(DBusConnection *, const char *, const DBusObjectPathVTable *, void *);
int dbus_connection_add_filter(DBusConnection *, dbus_msg_fn, void *, void (*)(void *));
int dbus_connection_read_write(DBusConnection *, int);
int dbus_connection_dispatch(DBusConnection *);
int dbus_connection_send(DBusConnection *, DBusMessage *, uint32_t *);
void dbus_connection_flush(DBusConnection *);
int dbus_connection_send_with_reply(DBusConnection *, DBusMessage *, DBusPendingCall **, int);
int dbus_connection_get_is_connected(DBusConnection *);
void dbus_connection_unref(DBusConnection *);
int dbus_pending_call_get_completed(DBusPendingCall *);
DBusMessage *dbus_pending_call_steal_reply(DBusPendingCall *);
void dbus_pending_call_unref(DBusPendingCall *);
DBusMessage *dbus_message_new_method_call(const char *, const char *, const char *, const char *);
DBusMessage *dbus_message_new_method_return(DBusMessage *);
DBusMessage *dbus_message_new_error(DBusMessage *, const char *, const char *);
DBusMessage *dbus_message_new_signal(const char *, const char *, const char *);
void dbus_message_unref(DBusMessage *);
int dbus_message_get_type(DBusMessage *);
int dbus_message_is_method_call(DBusMessage *, const char *, const char *);
int dbus_message_is_signal(DBusMessage *, const char *, const char *);
int dbus_message_iter_init(DBusMessage *, DBusMessageIter *);
void dbus_message_iter_init_append(DBusMessage *, DBusMessageIter *);
int dbus_message_iter_get_arg_type(DBusMessageIter *);
int dbus_message_iter_next(DBusMessageIter *);
void dbus_message_iter_get_basic(DBusMessageIter *, void *);
int dbus_message_iter_append_basic(DBusMessageIter *, int, const void *);
int dbus_message_iter_open_container(DBusMessageIter *, int, const char *, DBusMessageIter *);
int dbus_message_iter_close_container(DBusMessageIter *, DBusMessageIter *);
#define DBUS_BUS_SYSTEM 1
#define DBUS_TYPE_ERROR_MSG 3
#define DBUS_HANDLED 0
#define DBUS_NOT_YET_HANDLED 1
#define DBUS_DATA_REMAINS 0
#define T_STR 's'
#define T_PATH 'o'
#define T_BOOL 'b'
#define T_I32 'i'
#define T_I64 'x'
#define T_DOUBLE 'd'
#define T_ARRAY 'a'
#define T_VARIANT 'v'
#define T_DICT 'e'

#define QA_PATH "/org/q2pod/player"
#define QA_IFACE "org.mpris.MediaPlayer2.Player"
#define QA_ADAPTER "/org/bluez/hci0"
#ifndef QA_NOW /* the tests name their own */
#define QA_NOW "/tmp/coda-rc.now"
#endif
#define QA_PID "/tmp/q2-avrcp.pid"
#define QA_POLL_MS 500
#define QA_RETRY_MS 5000 /* a refused registration is tried again this often */
#define QA_FIELD 256     /* a title, an artist or an album, at most (bytes) */

/* Now Playing as the file has it: state 1 playing, 2 paused, 0 stopped; pos and len in ms; qpos
 * the song's place in its queue (0 first). */
typedef struct {
    int state, pos, len, qpos, qlen;
    char title[QA_FIELD], artist[QA_FIELD], album[QA_FIELD];
} qa_now_t;

static struct {
    DBusConnection *bus;
    DBusPendingCall *pending; /* RegisterPlayer's, until BlueZ answers */
    int registered, bluez;    /* BlueZ took the player; org.bluez is on the bus */
    long retry_at;
    char raw[2048]; /* the file as last read */
    qa_now_t now;
} qa;

static long qa_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000L + t.tv_nsec / 1000000L;
}

/* The value of key in the file's text (lines key=value), into out (size n); 0 when absent. */
static int qa_field(const char *raw, const char *key, char *out, unsigned n) {
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

static int qa_int(const char *raw, const char *key) {
    char v[16];
    return qa_field(raw, key, v, sizeof v) ? atoi(v) : 0;
}

/* raw parsed into n. Exposed for the tests (CODA_HOST). */
void qa_parse(const char *raw, qa_now_t *n) {
    memset(n, 0, sizeof *n);
    n->state = qa_int(raw, "state"), n->pos = qa_int(raw, "pos"), n->len = qa_int(raw, "len");
    n->qpos = qa_int(raw, "qpos"), n->qlen = qa_int(raw, "qlen");
    qa_field(raw, "title", n->title, sizeof n->title);
    qa_field(raw, "artist", n->artist, sizeof n->artist);
    qa_field(raw, "album", n->album, sizeof n->album);
    if (n->state < 0 || n->state > 2) n->state = 0;
}

/* ---- the properties as D-Bus values ---- */

static void put_variant(DBusMessageIter *it, int type, const void *v) {
    DBusMessageIter var;
    char sig[2] = { (char)type, 0 };
    dbus_message_iter_open_container(it, T_VARIANT, sig, &var);
    dbus_message_iter_append_basic(&var, type, v);
    dbus_message_iter_close_container(it, &var);
}

static void put_entry(DBusMessageIter *dict, const char *key, int type, const void *v) {
    DBusMessageIter e;
    dbus_message_iter_open_container(dict, T_DICT, 0, &e);
    dbus_message_iter_append_basic(&e, T_STR, &key);
    put_variant(&e, type, v);
    dbus_message_iter_close_container(dict, &e);
}

/* key: a variant holding an array of one string (MPRIS's artist and genre are lists) */
static void put_strings(DBusMessageIter *dict, const char *key, const char *s) {
    DBusMessageIter e, var, arr;
    dbus_message_iter_open_container(dict, T_DICT, 0, &e);
    dbus_message_iter_append_basic(&e, T_STR, &key);
    dbus_message_iter_open_container(&e, T_VARIANT, "as", &var);
    dbus_message_iter_open_container(&var, T_ARRAY, "s", &arr);
    dbus_message_iter_append_basic(&arr, T_STR, &s);
    dbus_message_iter_close_container(&var, &arr);
    dbus_message_iter_close_container(&e, &var);
    dbus_message_iter_close_container(dict, &e);
}

static const char *qa_status(const qa_now_t *n) {
    return n->state == 1 ? "Playing" : n->state == 2 ? "Paused" : "Stopped";
}

/* Metadata: title, artist, album, length (us), track number; none while stopped with no title. */
static void put_metadata(DBusMessageIter *dict, const qa_now_t *n) {
    DBusMessageIter e, var, meta;
    const char *key = "Metadata";
    dbus_message_iter_open_container(dict, T_DICT, 0, &e);
    dbus_message_iter_append_basic(&e, T_STR, &key);
    dbus_message_iter_open_container(&e, T_VARIANT, "a{sv}", &var);
    dbus_message_iter_open_container(&var, T_ARRAY, "{sv}", &meta);
    if (*n->title) {
        const char *title = n->title, *album = n->album;
        int64_t length = (int64_t)n->len * 1000;
        int32_t track = n->qlen ? n->qpos + 1 : 0;
        put_entry(&meta, "xesam:title", T_STR, &title);
        put_strings(&meta, "xesam:artist", n->artist);
        put_entry(&meta, "xesam:album", T_STR, &album);
        if (length > 0) put_entry(&meta, "mpris:length", T_I64, &length);
        if (track > 0) put_entry(&meta, "xesam:trackNumber", T_I32, &track);
    }
    dbus_message_iter_close_container(&var, &meta);
    dbus_message_iter_close_container(&e, &var);
    dbus_message_iter_close_container(dict, &e);
}

/* The player's properties into dict: all of them (registering, GetAll), or what a change sends. */
static void put_props(DBusMessageIter *dict, const qa_now_t *n, int all) {
    const char *status = qa_status(n);
    int64_t position = (int64_t)n->pos * 1000;
    put_entry(dict, "PlaybackStatus", T_STR, &status);
    put_entry(dict, "Position", T_I64, &position);
    put_metadata(dict, n);
    if (!all) return;
    int f = 0;
    double rate = 1.0;
    const char *loop = "None", *identity = "Q2";
    put_entry(dict, "CanControl", T_BOOL, &f);
    put_entry(dict, "CanPlay", T_BOOL, &f);
    put_entry(dict, "CanPause", T_BOOL, &f);
    put_entry(dict, "CanGoNext", T_BOOL, &f);
    put_entry(dict, "CanGoPrevious", T_BOOL, &f);
    put_entry(dict, "CanSeek", T_BOOL, &f);
    put_entry(dict, "Shuffle", T_BOOL, &f);
    put_entry(dict, "LoopStatus", T_STR, &loop);
    put_entry(dict, "Rate", T_DOUBLE, &rate);
    put_entry(dict, "Identity", T_STR, &identity);
}

/* ---- BlueZ ---- */

static void qa_register(void) {
    DBusMessage *m = dbus_message_new_method_call("org.bluez", QA_ADAPTER, "org.bluez.Media1", "RegisterPlayer");
    DBusMessageIter it, dict;
    const char *path = QA_PATH;
    if (!m) return;
    dbus_message_iter_init_append(m, &it);
    dbus_message_iter_append_basic(&it, T_PATH, &path);
    dbus_message_iter_open_container(&it, T_ARRAY, "{sv}", &dict);
    put_props(&dict, &qa.now, 1);
    dbus_message_iter_close_container(&it, &dict);
    if (qa.pending) dbus_pending_call_unref(qa.pending), qa.pending = 0;
    dbus_connection_send_with_reply(qa.bus, m, &qa.pending, 5000);
    dbus_message_unref(m);
    qa.retry_at = qa_ms() + QA_RETRY_MS;
}

static void qa_changed(void) {
    DBusMessage *m = dbus_message_new_signal(QA_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged");
    DBusMessageIter it, dict, none;
    const char *iface = QA_IFACE;
    if (!m) return;
    dbus_message_iter_init_append(m, &it);
    dbus_message_iter_append_basic(&it, T_STR, &iface);
    dbus_message_iter_open_container(&it, T_ARRAY, "{sv}", &dict);
    put_props(&dict, &qa.now, 0);
    dbus_message_iter_close_container(&it, &dict);
    dbus_message_iter_open_container(&it, T_ARRAY, "s", &none);
    dbus_message_iter_close_container(&it, &none);
    dbus_connection_send(qa.bus, m, 0);
    dbus_message_unref(m);
}

/* The player's object: Properties.Get and GetAll answered, the rest refused. */
static int qa_object(DBusConnection *c, DBusMessage *m, void *unused) {
    (void)unused;
    DBusMessage *r;
    if (dbus_message_is_method_call(m, "org.freedesktop.DBus.Properties", "GetAll")) {
        DBusMessageIter it, dict;
        r = dbus_message_new_method_return(m);
        dbus_message_iter_init_append(r, &it);
        dbus_message_iter_open_container(&it, T_ARRAY, "{sv}", &dict);
        put_props(&dict, &qa.now, 1);
        dbus_message_iter_close_container(&it, &dict);
    } else {
        r = dbus_message_new_error(m, "org.freedesktop.DBus.Error.NotSupported", "Q2: properties only");
    }
    if (r) dbus_connection_send(c, r, 0), dbus_message_unref(r);
    return DBUS_HANDLED;
}

/* org.bluez coming or going (bluetoothd starts and stops with Bluetooth). */
static int qa_filter(DBusConnection *c, DBusMessage *m, void *unused) {
    (void)c, (void)unused;
    DBusMessageIter it;
    const char *name = "", *old = "", *now = "";
    if (!dbus_message_is_signal(m, "org.freedesktop.DBus", "NameOwnerChanged") || !dbus_message_iter_init(m, &it))
        return DBUS_NOT_YET_HANDLED;
    dbus_message_iter_get_basic(&it, &name);
    dbus_message_iter_next(&it);
    dbus_message_iter_get_basic(&it, &old);
    dbus_message_iter_next(&it);
    dbus_message_iter_get_basic(&it, &now);
    if (strcmp(name, "org.bluez")) return DBUS_NOT_YET_HANDLED;
    qa.bluez = *now != 0, qa.registered = 0, qa.retry_at = 0;
    return DBUS_NOT_YET_HANDLED;
}

static int qa_connect(void) {
    static const DBusObjectPathVTable vt = { 0, qa_object, { 0 } };
    DBusError e;
    dbus_error_init(&e);
    qa.bus = dbus_bus_get(DBUS_BUS_SYSTEM, &e);
    dbus_error_free(&e);
    if (!qa.bus) return 0;
    dbus_connection_set_exit_on_disconnect(qa.bus, 0);
    dbus_connection_register_object_path(qa.bus, QA_PATH, &vt, 0);
    dbus_connection_add_filter(qa.bus, qa_filter, 0, 0);
    dbus_bus_add_match(qa.bus, "type='signal',sender='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.bluez'", 0);
    dbus_error_init(&e);
    qa.bluez = dbus_bus_name_has_owner(qa.bus, "org.bluez", &e);
    dbus_error_free(&e);
    return 1;
}

/* The file again: 1 when it changed. */
static int qa_read(void) {
    char raw[sizeof qa.raw];
    int fd = open(QA_NOW, O_RDONLY | O_CLOEXEC), n = 0;
    if (fd >= 0) n = (int)read(fd, raw, sizeof raw - 1), close(fd);
    raw[n > 0 ? n : 0] = 0;
    if (!strcmp(raw, qa.raw)) return 0;
    memcpy(qa.raw, raw, sizeof raw);
    qa_parse(raw, &qa.now);
    return 1;
}

/* One pass: the file, BlueZ's answer, a registration due, then the bus for up to QA_POLL_MS. */
int qa_step(void) {
    if (!qa.bus && !qa_connect()) return 0;
    if (!dbus_connection_get_is_connected(qa.bus)) return 0;
    int changed = qa_read();
    if (qa.pending && dbus_pending_call_get_completed(qa.pending)) {
        DBusMessage *r = dbus_pending_call_steal_reply(qa.pending);
        qa.registered = r && dbus_message_get_type(r) != DBUS_TYPE_ERROR_MSG;
        if (r) dbus_message_unref(r);
        dbus_pending_call_unref(qa.pending), qa.pending = 0;
    }
    if (qa.bluez && !qa.registered && !qa.pending && qa_ms() >= qa.retry_at) qa_register();
    else if (changed && qa.registered) qa_changed();
    dbus_connection_flush(qa.bus);
    dbus_connection_read_write(qa.bus, QA_POLL_MS);
    while (dbus_connection_dispatch(qa.bus) == DBUS_DATA_REMAINS) {}
    return 1;
}

#ifndef CODA_HOST
int main(void) {
    char pid[16];
    int fd = open(QA_PID, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd >= 0) {
        int n = snprintf(pid, sizeof pid, "%d\n", getpid());
        write(fd, pid, (size_t)n), close(fd);
    }
    for (;;)
        if (!qa_step()) { /* no system bus yet, or it went: try again in a while */
            if (qa.bus) dbus_connection_unref(qa.bus), qa.bus = 0;
            qa.registered = 0, qa.pending = 0;
            poll(0, 0, 5000);
        }
}
#endif
