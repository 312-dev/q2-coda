/* q2btd: Vamp OS's Bluetooth service (docs/btd.md), the client of the image's own bluetoothd and bluealsa
 * (docs/vamp-os-base.md#bluetooth), with Q2BT/1 on /run/vamp/bt.sock (docs/platform/bt.md).
 *
 *   - BlueZ (org.bluez) and bluez-alsa (org.bluealsa) on the system bus: what they hold is read once with each one's
 *     GetManagedObjects and kept up to date from InterfacesAdded, InterfacesRemoved and PropertiesChanged; a daemon
 *     that goes away and comes back (NameOwnerChanged) is read again and given the agent and the player again.
 *   - The radio on and off (Adapter1.Powered), a scan (discovery, and the Q2 discoverable for as long), pairing,
 *     connecting, disconnecting and forgetting, the paired devices, the connected one's codec (bluealsa's PCM1) and
 *     battery (BlueZ's Battery1).
 *   - The pairing agent. A numeric comparison (RequestConfirmation) is the owner's to answer, on q2ui's pairing card
 *     through Q2UI/1 (docs/platform/notices.md), as today's Vamp's patch/bt_pair.c asks it: only Pair is a yes. With
 *     no Q2UI/1 to ask, and for the calls with nothing to show, the agent is a just-works fallback: it says yes to
 *     the device a `pair` command is pairing, and to a device pairing with the Q2 while a scan has it discoverable,
 *     and no to anything else.
 *   - Auto-reconnect: the last device connected is paged at the start, at power on and after a drop the Q2 did not
 *     ask for, on RECONNECT_AT's schedule.
 *   - Where q2playd's audio goes: a client of Q2PLAY/1 (docs/platform/playback.md), it says `output bluetooth` while
 *     a headset's A2DP PCM is up and `output dac` when it goes (a pause first, so a dropped headset does not move the
 *     song to the jack still playing). Headphones and headsets (by their Class) get absolute volume: q2playd's level
 *     is written to the PCM's Volume on the Even curve (127 v / 100) and a level the headset sets is sent back as
 *     `volume V`; anything else gets `output bluetooth soft`, the player's own gain.
 *   - The headset's buttons and its track display: an org.mpris.MediaPlayer2.Player registered with BlueZ's Media1,
 *     whose Play, Pause, Next and Previous become q2playd's verbs and whose Metadata is q2playd's `now`.
 *
 * Its state, /mnt/data/q2btd.state: power, the last device and the codec chosen, `key=value` lines. A unit with no
 * state takes the last device from stock's [SYSSET] BTLINKMAC in /mnt/data/config.ini (read, never written).
 *
 * --root prefixes its paths (the sockets, /mnt/data) for test/btd.py; the bus is the system bus, which
 * DBUS_SYSTEM_BUS_ADDRESS points elsewhere. */
#define _GNU_SOURCE
#include <ctype.h>
#include <dbus/dbus.h>
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define CLIENTS 8
#define DEVS 32
#define PCMS 4
#define SCAN_MS 60000          /* a scan's length, the Q2 discoverable for as long */
#define PLAY_RETRY_MS 2000     /* q2playd's socket tried again this often while it is not there */
#define CALL_MS 25000          /* a call to a daemon, at most (BlueZ's own page and connect timeouts are under it) */
#define PAIR_CALL_MS 65000     /* a Pair, which waits on the agent (BlueZ's REQUEST_TIMEOUT is 60 s) */
#define RECONNECT_FOR_MS 120000 /* auto-reconnect pages the last device for this long after a start, a power on or a drop */
static const unsigned RECONNECT_AT[] = {0, 3000, 8000, 15000, 30000, 60000, 90000}; /* ms after the start of a round */
#define AGENT_PATH "/vamp/q2btd/agent"
#define PLAYER_PATH "/vamp/q2btd/player"

static char root[256];
static DBusConnection *bus;

static char *path(const char *rel) {
    static char b[4][600];
    static int i;
    i = (i + 1) & 3;
    snprintf(b[i], sizeof b[i], "%s%s", root, rel);
    return b[i];
}
static unsigned long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned long long)t.tv_sec * 1000 + (unsigned long long)t.tv_nsec / 1000000;
}
static void say(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "q2btd: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

/* ---- the model: what BlueZ and bluealsa hold ---- */

static struct dev {
    int used, paired, connected, trusted, battery, announced, asked_off; /* asked_off: a disconnect the Q2 asked for */
    unsigned cls;
    char path[96], addr[18], name[128];
} devs[DEVS];
static struct pcm {
    int used, source, vol, vol_set, rate; /* source: the Q2 sends (A2DP-source), the headset's sink; vol_set: the level last written */
    char path[200], dev[96], codec[32], available[160];
} pcms[PCMS];
static struct {
    int have, powered, discovering;
    char path[64];
} adapter;
static unsigned seq; /* a change to anything a status line shows */

static struct dev *dev_by_path(const char *p, int make) {
    struct dev *free_one = 0;
    for (int i = 0; i < DEVS; ++i) {
        if (devs[i].used && !strcmp(devs[i].path, p)) return &devs[i];
        if (!devs[i].used && !free_one) free_one = &devs[i];
    }
    if (!make || !free_one) return 0;
    *free_one = (struct dev){.used = 1, .battery = -1};
    snprintf(free_one->path, sizeof free_one->path, "%s", p);
    return free_one;
}
static struct dev *dev_by_addr(const char *a) {
    for (int i = 0; i < DEVS; ++i)
        if (devs[i].used && !strcasecmp(devs[i].addr, a)) return &devs[i];
    return 0;
}
static struct pcm *pcm_by_path(const char *p, int make) {
    struct pcm *free_one = 0;
    for (int i = 0; i < PCMS; ++i) {
        if (pcms[i].used && !strcmp(pcms[i].path, p)) return &pcms[i];
        if (!pcms[i].used && !free_one) free_one = &pcms[i];
    }
    if (!make || !free_one) return 0;
    *free_one = (struct pcm){.used = 1, .vol = -1, .vol_set = -1};
    snprintf(free_one->path, sizeof free_one->path, "%s", p);
    return free_one;
}
/* the connected device the Q2 plays to: the one with an A2DP-source PCM, else any connected one */
static struct pcm *the_pcm(void) {
    for (int i = 0; i < PCMS; ++i)
        if (pcms[i].used && pcms[i].source) {
            struct dev *d = dev_by_path(pcms[i].dev, 0);
            if (d && d->connected) return &pcms[i];
        }
    return 0;
}
static struct dev *the_dev(void) {
    struct pcm *p = the_pcm();
    if (p) return dev_by_path(p->dev, 0);
    for (int i = 0; i < DEVS; ++i)
        if (devs[i].used && devs[i].connected) return &devs[i];
    return 0;
}

/* A device's kind from its Class of Device (Bluetooth Assigned Numbers: major class in bits 8-12, minor 2-7). */
static const char *kind(unsigned cls) {
    unsigned major = cls >> 8 & 0x1f, minor = cls >> 2 & 0x3f;
    if (major == 4) return minor == 6 ? "headphones" : minor == 1 || minor == 2 ? "headset" : minor == 5 ? "speaker"
                         : minor == 8 ? "car" : "audio";
    return major == 2 ? "phone" : major == 1 ? "computer" : "other";
}
/* Absolute volume (the headset's own level follows the Q2's) for headphones and headsets alone, today's Vamp's Link
 * Volume default (docs/internals.md#headset-controls-and-volume): a speaker's or a car's weak amplifier stage plays a
 * low level thin, and a device whose Class is not known yet gets the player's gain. */
static int absolute(const struct dev *d) {
    unsigned major = d ? d->cls >> 8 & 0x1f : 0, minor = d ? d->cls >> 2 & 0x3f : 0;
    return major == 4 && (minor == 1 || minor == 2 || minor == 6);
}

/* ---- the state file ---- */

static int power_wanted = 1;
static char last[18], codec_choice[32] = "auto";

static int is_addr(const char *a) {
    if (strlen(a) != 17) return 0;
    for (int i = 0; i < 17; ++i)
        if (i % 3 == 2 ? a[i] != ':' : !isxdigit((unsigned char)a[i])) return 0;
    return 1;
}
static void state_load(void) {
    FILE *f = fopen(path("/mnt/data/q2btd.state"), "r");
    char l[200];
    if (!f) { /* none yet: stock's last device, if it names one ([SYSSET] BTLINKMAC) */
        FILE *c = fopen(path("/mnt/data/config.ini"), "r");
        int in = 0;
        while (c && fgets(l, sizeof l, c)) {
            l[strcspn(l, "\r\n")] = 0;
            if (l[0] == '[') in = !strcmp(l, "[SYSSET]");
            else if (in && !strncmp(l, "BTLINKMAC=", 10) && is_addr(l + 10)) snprintf(last, sizeof last, "%.17s", l + 10);
        }
        if (c) fclose(c);
        return;
    }
    while (fgets(l, sizeof l, f)) {
        l[strcspn(l, "\r\n")] = 0;
        if (!strncmp(l, "power=", 6)) power_wanted = atoi(l + 6) != 0;
        else if (!strncmp(l, "last=", 5) && (is_addr(l + 5) || !l[5])) snprintf(last, sizeof last, "%.17s", l + 5);
        else if (!strncmp(l, "codec=", 6) && l[6]) snprintf(codec_choice, sizeof codec_choice, "%.31s", l + 6);
    }
    fclose(f);
}
static void state_save(void) {
    char tmp[620];
    snprintf(tmp, sizeof tmp, "%s.tmp", path("/mnt/data/q2btd.state"));
    FILE *f = fopen(tmp, "w");
    if (!f) return say("%s: %s", tmp, strerror(errno));
    fprintf(f, "power=%d\nlast=%s\ncodec=%s\n", power_wanted, last, codec_choice);
    if (fclose(f) || rename(tmp, path("/mnt/data/q2btd.state"))) say("the state not saved: %s", strerror(errno));
}

/* ---- percent-encoding, Vamp API 1's (docs/platform/README.md#the-common-shape) ---- */

static void enc(char *out, size_t room, const char *in) {
    static const char hex[] = "0123456789ABCDEF";
    size_t j = 0;
    for (const unsigned char *p = (const unsigned char *)in; *p && j + 4 < room; ++p)
        if (*p < 0x21 || *p == '%' || *p == '"' || *p == '=' || *p == 0x7f) out[j++] = '%', out[j++] = hex[*p >> 4], out[j++] = hex[*p & 15];
        else out[j++] = (char)*p;
    out[j] = 0;
}
static int hexv(char c) { return c >= '0' && c <= '9' ? c - '0' : (c | 32) >= 'a' && (c | 32) <= 'f' ? (c | 32) - 'a' + 10 : -1; }
static void dec(char *s) {
    char *o = s;
    for (; *s; ++s)
        if (*s == '%' && hexv(s[1]) >= 0 && hexv(s[2]) >= 0) *o++ = (char)(hexv(s[1]) << 4 | hexv(s[2])), s += 2;
        else *o++ = *s;
    *o = 0;
}

/* ---- reading D-Bus values ---- */

/* the value inside a variant (or the value itself) as text, a number or a bool; 0 when it is not that type */
static int iter_str(DBusMessageIter *it, char *out, size_t room) {
    DBusMessageIter v;
    if (dbus_message_iter_get_arg_type(it) == DBUS_TYPE_VARIANT) dbus_message_iter_recurse(it, &v), it = &v;
    int t = dbus_message_iter_get_arg_type(it);
    if (t != DBUS_TYPE_STRING && t != DBUS_TYPE_OBJECT_PATH) return 0;
    const char *s;
    dbus_message_iter_get_basic(it, &s);
    snprintf(out, room, "%s", s);
    return 1;
}
static int iter_num(DBusMessageIter *it, long long *out) {
    DBusMessageIter v;
    if (dbus_message_iter_get_arg_type(it) == DBUS_TYPE_VARIANT) dbus_message_iter_recurse(it, &v), it = &v;
    DBusBasicValue b;
    switch (dbus_message_iter_get_arg_type(it)) {
    case DBUS_TYPE_BOOLEAN: dbus_message_iter_get_basic(it, &b); *out = b.bool_val; return 1;
    case DBUS_TYPE_BYTE: dbus_message_iter_get_basic(it, &b); *out = b.byt; return 1;
    case DBUS_TYPE_INT16: dbus_message_iter_get_basic(it, &b); *out = b.i16; return 1;
    case DBUS_TYPE_UINT16: dbus_message_iter_get_basic(it, &b); *out = b.u16; return 1;
    case DBUS_TYPE_INT32: dbus_message_iter_get_basic(it, &b); *out = b.i32; return 1;
    case DBUS_TYPE_UINT32: dbus_message_iter_get_basic(it, &b); *out = b.u32; return 1;
    case DBUS_TYPE_INT64: dbus_message_iter_get_basic(it, &b); *out = (long long)b.i64; return 1;
    case DBUS_TYPE_UINT64: dbus_message_iter_get_basic(it, &b); *out = (long long)b.u64; return 1;
    }
    return 0;
}
/* bluealsa's PCM Volume, an array of bytes a channel (bit 7 mute, 0-127 the level): the first channel's level */
static int iter_volume(DBusMessageIter *it) {
    DBusMessageIter v, a;
    if (dbus_message_iter_get_arg_type(it) == DBUS_TYPE_VARIANT) dbus_message_iter_recurse(it, &v), it = &v;
    if (dbus_message_iter_get_arg_type(it) != DBUS_TYPE_ARRAY || dbus_message_iter_get_element_type(it) != DBUS_TYPE_BYTE) return -1;
    dbus_message_iter_recurse(it, &a);
    if (dbus_message_iter_get_arg_type(&a) != DBUS_TYPE_BYTE) return -1;
    unsigned char b;
    dbus_message_iter_get_basic(&a, &b);
    return b & 0x80 ? 0 : b & 0x7f;
}

static void pcm_added(struct pcm *p);
static void pcm_volume(struct pcm *p, int was);
static void dev_changed(struct dev *d, int was_connected);

/* one interface's properties (an a{sv} iterator) applied to the object at p */
static void props_apply(const char *p, const char *iface, DBusMessageIter *dict) {
    struct dev *d = 0;
    struct pcm *m = 0;
    int was = 0, new_pcm = 0, was_vol = -1;
    if (!strcmp(iface, "org.bluez.Adapter1")) {
        adapter.have = 1;
        snprintf(adapter.path, sizeof adapter.path, "%s", p);
    } else if (!strcmp(iface, "org.bluez.Device1") || !strcmp(iface, "org.bluez.Battery1")) {
        if (!(d = dev_by_path(p, 1))) return;
        was = d->connected;
    } else if (!strcmp(iface, "org.bluealsa.PCM1")) {
        new_pcm = !pcm_by_path(p, 0);
        if (!(m = pcm_by_path(p, 1))) return;
        was_vol = m->vol;
    } else return;
    for (; dbus_message_iter_get_arg_type(dict) == DBUS_TYPE_DICT_ENTRY; dbus_message_iter_next(dict)) {
        DBusMessageIter e;
        const char *k;
        char s[200];
        long long n;
        dbus_message_iter_recurse(dict, &e);
        dbus_message_iter_get_basic(&e, &k);
        dbus_message_iter_next(&e);
        if (d) {
            if (!strcmp(k, "Address")) iter_str(&e, d->addr, sizeof d->addr);
            else if (!strcmp(k, "Alias")) iter_str(&e, d->name, sizeof d->name);
            else if (!strcmp(k, "Class") && iter_num(&e, &n)) d->cls = (unsigned)n;
            else if (!strcmp(k, "Paired") && iter_num(&e, &n)) d->paired = (int)n;
            else if (!strcmp(k, "Connected") && iter_num(&e, &n)) d->connected = (int)n;
            else if (!strcmp(k, "Trusted") && iter_num(&e, &n)) d->trusted = (int)n;
            else if (!strcmp(k, "Percentage") && iter_num(&e, &n)) d->battery = (int)n;
        } else if (m) {
            if (!strcmp(k, "Device")) iter_str(&e, m->dev, sizeof m->dev);
            else if (!strcmp(k, "Transport") && iter_str(&e, s, sizeof s)) m->source = !strcmp(s, "A2DP-source");
            else if (!strcmp(k, "Codec")) iter_str(&e, m->codec, sizeof m->codec);
            else if (!strcmp(k, "Rate") && iter_num(&e, &n)) m->rate = (int)n;
            else if (!strcmp(k, "Volume")) m->vol = iter_volume(&e);
        } else {
            if (!strcmp(k, "Powered") && iter_num(&e, &n)) adapter.powered = (int)n;
            else if (!strcmp(k, "Discovering") && iter_num(&e, &n)) adapter.discovering = (int)n;
        }
    }
    ++seq;
    if (d) dev_changed(d, was);
    if (m && new_pcm) pcm_added(m);
    else if (m && m->vol != was_vol) pcm_volume(m, was_vol);
}

/* an object's interfaces and their properties (an a{sa{sv}} iterator) */
static void object_apply(const char *p, DBusMessageIter *ifaces) {
    for (; dbus_message_iter_get_arg_type(ifaces) == DBUS_TYPE_DICT_ENTRY; dbus_message_iter_next(ifaces)) {
        DBusMessageIter e, props;
        const char *iface;
        dbus_message_iter_recurse(ifaces, &e);
        dbus_message_iter_get_basic(&e, &iface);
        dbus_message_iter_next(&e);
        dbus_message_iter_recurse(&e, &props);
        props_apply(p, iface, &props);
    }
}
static void object_removed(const char *p, DBusMessageIter *names) {
    for (; dbus_message_iter_get_arg_type(names) == DBUS_TYPE_STRING; dbus_message_iter_next(names)) {
        const char *iface;
        dbus_message_iter_get_basic(names, &iface);
        struct dev *d = dev_by_path(p, 0);
        struct pcm *m = pcm_by_path(p, 0);
        if (!strcmp(iface, "org.bluez.Device1") && d) {
            int was = d->connected;
            d->connected = 0;
            dev_changed(d, was);
            d->used = 0;
        } else if (!strcmp(iface, "org.bluez.Battery1") && d) d->battery = -1;
        else if (!strcmp(iface, "org.bluealsa.PCM1") && m) m->used = 0;
        else if (!strcmp(iface, "org.bluez.Adapter1") && !strcmp(p, adapter.path)) adapter.have = adapter.powered = adapter.discovering = 0;
        ++seq;
    }
}

/* ---- calls, each answered later (a pending call) so the loop never waits on a daemon ---- */

typedef void (*reply_fn)(DBusMessage *reply, void *ctx); /* reply: a method return, an error, or 0 (no answer) */
struct pend { reply_fn fn; void *ctx; };

static void on_pending(DBusPendingCall *pc, void *data) {
    struct pend *pd = data;
    DBusMessage *r = dbus_pending_call_steal_reply(pc);
    if (pd->fn) pd->fn(r, pd->ctx);
    else free(pd->ctx);
    if (r) dbus_message_unref(r);
    dbus_pending_call_unref(pc);
}
static void free_pend(void *data) { free(data); }
/* m sent (and unref'd); fn called with its answer; ctx is fn's to free, or freed here when fn is 0 */
static void call(DBusMessage *m, int timeout, reply_fn fn, void *ctx) {
    DBusPendingCall *pc = 0;
    struct pend *pd = malloc(sizeof *pd);
    if (!m || !pd || !dbus_connection_send_with_reply(bus, m, &pc, timeout) || !pc) {
        if (fn) fn(0, ctx);
        else free(ctx);
        free(pd);
        if (m) dbus_message_unref(m);
        return;
    }
    *pd = (struct pend){fn, ctx};
    dbus_pending_call_set_notify(pc, on_pending, pd, free_pend);
    dbus_message_unref(m);
}
static DBusMessage *method(const char *dest, const char *p, const char *iface, const char *member) {
    return dbus_message_new_method_call(dest, p, iface, member);
}
static int failed(DBusMessage *r, char *why, size_t room) {
    if (r && dbus_message_get_type(r) != DBUS_MESSAGE_TYPE_ERROR) return 0;
    const char *name = r ? dbus_message_get_error_name(r) : 0, *dot = name ? strrchr(name, '.') : 0;
    if (why) snprintf(why, room, "%s", dot ? dot + 1 : name ? name : "NoReply");
    return 1;
}
/* Properties.Set of a bool or of bluealsa's two-channel Volume */
static DBusMessage *set_prop(const char *dest, const char *p, const char *iface, const char *prop, int type, int value) {
    DBusMessage *m = method(dest, p, "org.freedesktop.DBus.Properties", "Set");
    DBusMessageIter it, v, a;
    if (!m) return 0;
    dbus_message_iter_init_append(m, &it);
    dbus_message_iter_append_basic(&it, DBUS_TYPE_STRING, &iface);
    dbus_message_iter_append_basic(&it, DBUS_TYPE_STRING, &prop);
    if (type == DBUS_TYPE_BOOLEAN) {
        dbus_bool_t b = value != 0;
        dbus_message_iter_open_container(&it, DBUS_TYPE_VARIANT, "b", &v);
        dbus_message_iter_append_basic(&v, DBUS_TYPE_BOOLEAN, &b);
    } else {
        unsigned char lr[2] = {(unsigned char)value, (unsigned char)value};
        const unsigned char *p2 = lr;
        dbus_message_iter_open_container(&it, DBUS_TYPE_VARIANT, "ay", &v);
        dbus_message_iter_open_container(&v, DBUS_TYPE_ARRAY, "y", &a);
        dbus_message_iter_append_fixed_array(&a, DBUS_TYPE_BYTE, &p2, 2);
        dbus_message_iter_close_container(&v, &a);
    }
    dbus_message_iter_close_container(&it, &v);
    return m;
}
static void logged(DBusMessage *r, void *ctx) { /* a call no client waits on: its failure logged */
    char why[96];
    if (failed(r, why, sizeof why)) say("%s: %s", (char *)ctx, why);
    free(ctx);
}
static void call_logged(DBusMessage *m, int timeout, const char *what) { call(m, timeout, logged, strdup(what)); }

/* libdbus's timeouts (a pending call's), run by the loop: there are few, so a fixed table */
#define TIMEOUTS 64
static struct { DBusTimeout *t; unsigned long long due; } tmo[TIMEOUTS];
static dbus_bool_t tmo_add(DBusTimeout *t, void *data) {
    (void)data;
    for (int i = 0; i < TIMEOUTS; ++i)
        if (!tmo[i].t) {
            tmo[i].t = t, tmo[i].due = now_ms() + (unsigned)dbus_timeout_get_interval(t);
            return TRUE;
        }
    return FALSE;
}
static void tmo_remove(DBusTimeout *t, void *data) {
    (void)data;
    for (int i = 0; i < TIMEOUTS; ++i)
        if (tmo[i].t == t) tmo[i].t = 0;
}
static void tmo_toggled(DBusTimeout *t, void *data) {
    (void)data;
    for (int i = 0; i < TIMEOUTS; ++i)
        if (tmo[i].t == t) tmo[i].due = now_ms() + (unsigned)dbus_timeout_get_interval(t);
}
static void tmo_run(void) {
    unsigned long long t = now_ms();
    for (int i = 0; i < TIMEOUTS; ++i)
        if (tmo[i].t && dbus_timeout_get_enabled(tmo[i].t) && t >= tmo[i].due) {
            DBusTimeout *x = tmo[i].t;
            tmo[i].due = t + (unsigned)dbus_timeout_get_interval(x);
            dbus_timeout_handle(x);
        }
}
static int tmo_next(int cap) {
    unsigned long long t = now_ms();
    for (int i = 0; i < TIMEOUTS; ++i)
        if (tmo[i].t && dbus_timeout_get_enabled(tmo[i].t)) {
            long long left = (long long)tmo[i].due - (long long)t;
            if (left < cap) cap = left < 0 ? 0 : (int)left;
        }
    return cap;
}

/* ---- Q2PLAY/1: q2playd, where its audio goes and its volume ---- */

static int play_fd = -1;
static char play_in[4096];
static size_t play_n;
static unsigned long long play_tried;
static struct {
    int have, volume, state, id;
    long long pos, len;
    char title[256], artist[256], album[256];
    unsigned seq;
} np;
static char out_sent[24]; /* the output last asked of q2playd: "", "dac", "bluetooth", "bluetooth soft" */

static void play_say(const char *fmt, ...) {
    char line[600];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof line - 1, fmt, ap);
    va_end(ap);
    if (play_fd < 0 || n <= 0) return;
    line[n++] = '\n';
    if (send(play_fd, line, (size_t)n, MSG_NOSIGNAL | MSG_DONTWAIT) != n) close(play_fd), play_fd = -1, out_sent[0] = 0;
}
static void play_connect(void) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    play_tried = now_ms();
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/play.sock"));
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0 || connect(fd, (struct sockaddr *)&a, sizeof a)) {
        if (fd >= 0) close(fd);
        return;
    }
    play_fd = fd, play_n = 0, out_sent[0] = 0, np.have = 0;
    play_say("Q2PLAY/1");
    play_say("watch");
    play_say("now");
}
static void np_field(const char *line, const char *key, char *out, size_t room) {
    char k[24];
    snprintf(k, sizeof k, " %s=", key);
    const char *p = strstr(line, k);
    out[0] = 0;
    if (!p) return;
    p += strlen(k);
    snprintf(out, room, "%.*s", (int)strcspn(p, " "), p);
    dec(out);
}
static void route(void);
static void vol_sync(void);
static void player_changed(void);
static void play_line(const char *line) {
    if (strncmp(line, "now ", 4)) return; /* answers to what q2btd sent: ok, err */
    char v[256];
    int old_state = np.state, old_vol = np.volume, old_have = np.have;
    char old_title[256];
    snprintf(old_title, sizeof old_title, "%s", np.title);
    np_field(line, "title", np.title, sizeof np.title);
    np_field(line, "artist", np.artist, sizeof np.artist);
    np_field(line, "album", np.album, sizeof np.album);
    np_field(line, "state", v, sizeof v), np.state = atoi(v);
    np_field(line, "pos", v, sizeof v), np.pos = atoll(v);
    np_field(line, "len", v, sizeof v), np.len = atoll(v);
    np_field(line, "volume", v, sizeof v), np.volume = atoi(v);
    np_field(line, "id", v, sizeof v), np.id = atoi(v);
    np.have = 1, ++np.seq;
    if (!old_have || old_vol != np.volume) vol_sync();
    if (!old_have || old_state != np.state || strcmp(old_title, np.title)) player_changed();
}
static void play_read(void) {
    ssize_t got = recv(play_fd, play_in + play_n, sizeof play_in - 1 - play_n, 0);
    if (got <= 0) { close(play_fd), play_fd = -1, out_sent[0] = 0; return; }
    play_n += (size_t)got;
    char *p = play_in, *nl;
    while ((nl = memchr(p, '\n', play_n - (size_t)(p - play_in)))) {
        *nl = 0;
        play_line(p);
        p = nl + 1;
    }
    play_n -= (size_t)(p - play_in);
    memmove(play_in, p, play_n);
    if (play_n >= sizeof play_in - 1) play_n = 0;
}

/* The output q2playd should have: Bluetooth while a connected headset's A2DP PCM is up, else the DAC. */
static void route(void) {
    struct pcm *m = the_pcm();
    struct dev *d = m ? dev_by_path(m->dev, 0) : 0;
    const char *want = !m ? "dac" : absolute(d) ? "bluetooth" : "bluetooth soft";
    if (play_fd < 0 || !strcmp(out_sent, want)) return;
    if (!strcmp(want, "dac") && out_sent[0] && np.state == 1) play_say("pause"); /* the headset gone: not on to the jack */
    play_say("output %s", want);
    say("output %s", want);
    snprintf(out_sent, sizeof out_sent, "%s", want);
    ++seq;
    vol_sync();
}

/* Absolute volume on the Even curve (docs/internals.md#headset-controls-and-volume): 127 v / 100, at least 1 above 0 */
static int level_of(int v) { return v <= 0 ? 0 : (127 * v + 50) / 100 < 1 ? 1 : (127 * v + 50) / 100; }
static int volume_of(int level) { return (level * 100 + 63) / 127; }

static void vol_sync(void) {
    struct pcm *m = the_pcm();
    if (!m || !np.have || !absolute(dev_by_path(m->dev, 0)) || strcmp(out_sent, "bluetooth")) return;
    int want = level_of(np.volume);
    if (m->vol == want || m->vol_set == want) return;
    m->vol_set = want;
    call_logged(set_prop("org.bluealsa", m->path, "org.bluealsa.PCM1", "Volume", DBUS_TYPE_BYTE, want), CALL_MS, "Volume");
}
/* the PCM's Volume moved: by the Q2's own write (an echo), or by the headset's keys, which q2playd then follows */
static void pcm_volume(struct pcm *m, int was) {
    (void)was;
    if (m != the_pcm() || m->vol < 0 || !absolute(dev_by_path(m->dev, 0))) return;
    if (m->vol == m->vol_set) return;
    m->vol_set = m->vol;
    if (np.have && level_of(np.volume) == m->vol) return;
    say("the headset's volume: level %d, volume %d", m->vol, volume_of(m->vol));
    play_say("volume %d", volume_of(m->vol));
}

/* ---- Q2BT/1 ---- */

static struct client { int fd, hello, watch; unsigned gen; char in[512]; size_t n; } cl[CLIENTS];
static unsigned gens;
static int api = -1;

static void client_say(struct client *c, const char *fmt, ...) {
    char line[1200];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof line - 1, fmt, ap);
    va_end(ap);
    if (n < 0 || c->fd < 0) return;
    if (n > (int)sizeof line - 2) n = (int)sizeof line - 2;
    line[n++] = '\n';
    if (send(c->fd, line, (size_t)n, MSG_NOSIGNAL | MSG_DONTWAIT) != n) close(c->fd), c->fd = -1;
}
static void tell(const char *fmt, ...) {
    char line[1100];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    for (int i = 0; i < CLIENTS; ++i)
        if (cl[i].fd >= 0 && cl[i].hello && cl[i].watch) client_say(&cl[i], "%s", line);
}

/* a client waiting on a call's answer; it may have gone (its slot reused: the generation differs) */
struct waiter { int ci; unsigned gen; char what[24], path[200]; };
static struct waiter *waiter(struct client *c, const char *what, const char *p) {
    struct waiter *w = calloc(1, sizeof *w);
    if (!w) return 0;
    w->ci = (int)(c - cl), w->gen = c->gen;
    snprintf(w->what, sizeof w->what, "%s", what);
    snprintf(w->path, sizeof w->path, "%s", p ? p : "");
    return w;
}
static struct client *waiting(struct waiter *w) {
    struct client *c = &cl[w->ci];
    return c->fd >= 0 && c->gen == w->gen ? c : 0;
}
static void answered(DBusMessage *r, void *ctx) { /* "ok", or "err WHAT" with BlueZ's reason in the log */
    struct waiter *w = ctx;
    struct client *c = waiting(w);
    char why[96];
    if (failed(r, why, sizeof why)) {
        say("%s: %s", w->what, why);
        if (c) client_say(c, "err %s", w->what);
    } else if (c) client_say(c, "ok");
    free(w);
}

static char connecting[96]; /* the device a Connect is out for */
static const char *state_word(void) {
    struct dev *d = the_dev();
    if (!adapter.have) return "absent";
    if (!adapter.powered) return "off";
    if (d) return "connected";
    if (connecting[0]) return "connecting";
    if (adapter.discovering) return "scanning";
    return "on";
}
static void status_line(char *out, size_t room) {
    struct dev *d = the_dev();
    struct pcm *m = the_pcm();
    char name[400];
    enc(name, sizeof name, d ? d->name : "");
    snprintf(out, room, "status power=%d state=%s device=%s name=%s kind=%s codec=%s battery=%d output=%s", adapter.powered,
             state_word(), d ? d->addr : "", name, d ? kind(d->cls) : "", m ? m->codec : "", d ? d->battery : -1,
             !strncmp(out_sent, "bluetooth", 9) ? "bluetooth" : "dac");
}
static void device_line(struct client *c, const struct dev *d) {
    char name[400];
    enc(name, sizeof name, d->name);
    client_say(c, "device addr=%s name=%s kind=%s paired=%d connected=%d battery=%d", d->addr, name, kind(d->cls), d->paired,
               d->connected, d->battery);
}

/* ---- connecting, and the auto-reconnect ---- */

static struct { int on, step; unsigned long long from; } rc;
static void reconnect_round(const char *why) {
    if (!last[0] || !power_wanted) return;
    rc.on = 1, rc.step = 0, rc.from = now_ms();
    say("auto-reconnect to %s (%s)", last, why);
}
static void connected_cb(DBusMessage *r, void *ctx) {
    struct waiter *w = ctx;
    struct client *c = w->ci >= 0 ? waiting(w) : 0;
    char why[96];
    if (!strcmp(connecting, w->path)) connecting[0] = 0;
    ++seq;
    if (failed(r, why, sizeof why)) {
        say("connect %s: %s", w->path, why);
        if (c) client_say(c, "err connect");
    } else if (c) client_say(c, "ok");
    free(w);
}
/* Device1.Connect; c, when a client asked, gets its answer */
static void connect_dev(struct dev *d, struct client *c) {
    struct waiter *w = c ? waiter(c, "connect", d->path) : calloc(1, sizeof *w);
    if (!w) return;
    if (!c) w->ci = -1, snprintf(w->path, sizeof w->path, "%s", d->path);
    snprintf(connecting, sizeof connecting, "%s", d->path), ++seq;
    d->asked_off = 0;
    call(method("org.bluez", d->path, "org.bluez.Device1", "Connect"), CALL_MS, connected_cb, w);
}
static void reconnect_poll(void) {
    if (!rc.on) return;
    struct dev *d = last[0] ? dev_by_addr(last) : 0;
    unsigned long long t = now_ms();
    if (the_dev() || !power_wanted || !d || !d->paired || t > rc.from + RECONNECT_FOR_MS ||
        rc.step >= (int)(sizeof RECONNECT_AT / sizeof *RECONNECT_AT)) {
        rc.on = 0;
        return;
    }
    if (!adapter.powered || connecting[0] || t < rc.from + RECONNECT_AT[rc.step]) return;
    ++rc.step;
    say("auto-reconnect: paging %s, try %d", d->addr, rc.step);
    connect_dev(d, 0);
}

static void dev_changed(struct dev *d, int was) {
    if (!was && d->connected) {
        say("connected %s", d->addr);
        if (d->paired && d->addr[0] && strcasecmp(last, d->addr)) snprintf(last, sizeof last, "%s", d->addr), state_save();
        rc.on = 0;
    } else if (was && !d->connected) {
        say("disconnected %s%s", d->addr, d->asked_off ? " (asked)" : "");
        if (!d->asked_off && !strcasecmp(d->addr, last)) reconnect_round("dropped");
        d->asked_off = 0;
    }
    if (!d->announced && d->addr[0] && !d->paired && adapter.discovering) {
        char name[400];
        d->announced = 1;
        enc(name, sizeof name, d->name);
        tell("found addr=%s name=%s kind=%s", d->addr, name, kind(d->cls));
    }
    if (was != d->connected) route();
}

/* the codec a connected headset streams: bluealsa's choice (auto), or the one chosen when the headset offers it */
static void codecs_cb(DBusMessage *r, void *ctx) {
    char *p = ctx;
    struct pcm *m = pcm_by_path(p, 0);
    DBusMessageIter it, a, e;
    if (m && !failed(r, 0, 0) && dbus_message_iter_init(r, &it) && dbus_message_iter_get_arg_type(&it) == DBUS_TYPE_ARRAY) {
        m->available[0] = 0;
        for (dbus_message_iter_recurse(&it, &a); dbus_message_iter_get_arg_type(&a) == DBUS_TYPE_DICT_ENTRY; dbus_message_iter_next(&a)) {
            const char *name;
            dbus_message_iter_recurse(&a, &e);
            dbus_message_iter_get_basic(&e, &name);
            size_t n = strlen(m->available);
            snprintf(m->available + n, sizeof m->available - n, "%s%s", n ? "," : "", name);
        }
        ++seq;
        int offered = 0;
        for (char *t = m->available; *t;) {
            size_t k = strcspn(t, ",");
            if (k == strlen(codec_choice) && !strncasecmp(t, codec_choice, k)) offered = 1;
            t += k + (t[k] == ',');
        }
        if (strcmp(codec_choice, "auto") && offered && strcasecmp(m->codec, codec_choice)) {
            DBusMessage *s = method("org.bluealsa", m->path, "org.bluealsa.PCM1", "SelectCodec");
            DBusMessageIter si, sd;
            const char *cc = codec_choice;
            dbus_message_iter_init_append(s, &si);
            dbus_message_iter_append_basic(&si, DBUS_TYPE_STRING, &cc);
            dbus_message_iter_open_container(&si, DBUS_TYPE_ARRAY, "{sv}", &sd);
            dbus_message_iter_close_container(&si, &sd);
            say("codec %s chosen: asking for it", codec_choice);
            call_logged(s, CALL_MS, "SelectCodec");
        }
    }
    free(p);
}
static void pcm_added(struct pcm *m) {
    if (!m->source) return;
    call(method("org.bluealsa", m->path, "org.bluealsa.PCM1", "GetCodecs"), CALL_MS, codecs_cb, strdup(m->path));
    route();
}

/* ---- the pairing agent: the just-works fallback ---- */

static char pairing[96];               /* the device a `pair` is pairing */
static int power_asked = -1;           /* the Powered last asked of this bluetoothd, so the loop asks it once */
static unsigned long long scan_until;  /* while a scan runs the Q2 is discoverable, and a device pairing with it is let in */

static int agent_allows(const char *dev_path) {
    struct dev *d = dev_by_path(dev_path, 0);
    return (pairing[0] && !strcmp(pairing, dev_path)) || now_ms() < scan_until || (d && d->paired);
}
/* ---- the pairing card (docs/btd.md#the-pairing-card): a RequestConfirmation held while q2ui asks the owner ---- */

static int ui_fd = -1; /* Q2UI/1, connected when a card is asked and kept */
static char ui_in[512];
static size_t ui_n;
static struct {
    DBusMessage *m; /* the call waiting on the card, 0 none */
    char id[16], addr[18];
    unsigned passkey, n;
    int acked; /* the ask's own ok or err read */
} card;

static void ui_close(void) {
    if (ui_fd >= 0) close(ui_fd), ui_fd = -1;
    ui_n = 0;
}

/* a line to q2ui, connecting first; -1 when there is no Q2UI/1 to take it */
static int ui_send(const char *line) {
    if (ui_fd < 0) {
        struct sockaddr_un a = {.sun_family = AF_UNIX};
        snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/ui.sock"));
        ui_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (ui_fd < 0 || connect(ui_fd, (struct sockaddr *)&a, sizeof a) || send(ui_fd, "Q2UI/1\n", 7, MSG_NOSIGNAL) != 7)
            return ui_close(), -1;
    }
    char b[700];
    int n = snprintf(b, sizeof b, "%s\n", line);
    if (n < 0 || n >= (int)sizeof b || send(ui_fd, b, (size_t)n, MSG_NOSIGNAL) != n) return ui_close(), -1;
    return 0;
}

/* the held call answered: yes only from the card's Pair */
static void card_end(int yes, const char *why) {
    if (!card.m) return;
    DBusMessage *r = yes ? dbus_message_new_method_return(card.m) : dbus_message_new_error(card.m, "org.bluez.Error.Rejected", why);
    if (r) dbus_connection_send(bus, r, 0), dbus_message_unref(r);
    dbus_message_unref(card.m), card.m = 0;
    say("agent: the card for %s: %s", card.addr, yes ? "Pair" : why);
    tell("pair-request addr=%s passkey=%06u answer=%s", card.addr, card.passkey, yes ? "yes" : "no");
}

/* "Pair with NAME?" (the name cut whole at a character to TITLE's 40 bytes) over the code as two groups of three and
 * bt_pair.c's caption; a device BlueZ has no name for is "this device", its body "Unknown device" and the address
 * first. Cancel, then Pair: the consequential kind's order (docs/oled-design.md#pairing). 0 asked. */
static int card_ask(DBusMessage *m, const struct dev *d, const char *addr, unsigned passkey) {
    char name[30], title[48], body[200], et[150], eb[600], line[800];
    size_t k = d && d->name[0] ? strlen(d->name) : 0;
    if (k > sizeof name - 1) {
        k = sizeof name - 1;
        while (k && ((unsigned char)d->name[k] & 0xc0) == 0x80) --k; /* not inside a UTF-8 character */
    }
    snprintf(name, sizeof name, "%.*s", (int)k, k ? d->name : "");
    snprintf(title, sizeof title, "Pair with %s?", k ? name : "this device");
    snprintf(body, sizeof body, "%s%s%s%03u %03u\nMake sure both codes match", k ? "" : "Unknown device\n", k ? "" : addr,
             k ? "" : "\n", passkey / 1000 % 1000, passkey % 1000);
    snprintf(card.id, sizeof card.id, "pair-%u", ++card.n);
    enc(et, sizeof et, title), enc(eb, sizeof eb, body);
    snprintf(line, sizeof line, "ask %s %s %s Cancel Pair", card.id, et, eb);
    if (ui_send(line)) return -1;
    card.m = dbus_message_ref(m), card.acked = 0, card.passkey = passkey;
    snprintf(card.addr, sizeof card.addr, "%s", addr);
    say("agent: RequestConfirmation from %s: asked on the card (%s)", addr, card.id);
    return 0;
}

/* q2ui's lines: the ask's ok, or err (4 asks waiting, the text refused): no; then its answer, Pair (2) the one yes */
static void ui_read(void) {
    ssize_t got = recv(ui_fd, ui_in + ui_n, sizeof ui_in - 1 - ui_n, MSG_DONTWAIT);
    if (got <= 0) {
        if (got == 0 || (errno != EAGAIN && errno != EWOULDBLOCK)) ui_close(), card_end(0, "the UI went away");
        return;
    }
    ui_n += (size_t)got;
    ui_in[ui_n] = 0;
    for (char *nl; (nl = strchr(ui_in, '\n'));) {
        *nl = 0;
        char id[40], v[16];
        if (card.m && !card.acked && (!strcmp(ui_in, "ok") || !strncmp(ui_in, "err", 3))) {
            card.acked = 1;
            if (strcmp(ui_in, "ok")) card_end(0, "the card is busy");
        } else if (sscanf(ui_in, "answer %39s %15s", id, v) == 2 && card.m && !strcmp(id, card.id))
            card_end(!strcmp(v, "2"), "Cancel on the card");
        ui_n -= (size_t)(nl + 1 - ui_in);
        memmove(ui_in, nl + 1, ui_n + 1);
    }
    if (ui_n >= sizeof ui_in - 1) ui_n = 0, ui_in[0] = 0;
}

static DBusHandlerResult agent_msg(DBusConnection *c, DBusMessage *m, void *data) {
    (void)data;
    const char *member = dbus_message_get_member(m), *dev = "";
    if (!dbus_message_has_interface(m, "org.bluez.Agent1")) return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    DBusMessageIter it;
    if (dbus_message_iter_init(m, &it) && dbus_message_iter_get_arg_type(&it) == DBUS_TYPE_OBJECT_PATH) dbus_message_iter_get_basic(&it, &dev);
    struct dev *d = dev_by_path(dev, 0);
    const char *addr = d ? d->addr : dev;
    DBusMessage *r = 0;
    unsigned code = 0;
    if (!strcmp(member, "RequestConfirmation")) {
        DBusMessageIter at = it;
        if (dbus_message_iter_next(&at) && dbus_message_iter_get_arg_type(&at) == DBUS_TYPE_UINT32) dbus_message_iter_get_basic(&at, &code);
        if (card.m) { /* one card at a time: BlueZ asks one request of an agent, so a second is another device's */
            say("agent: RequestConfirmation from %s while %s's card is up: no", addr, card.addr);
            r = dbus_message_new_error(m, "org.bluez.Error.Rejected", "another pairing waits");
            dbus_connection_send(c, r, 0), dbus_message_unref(r);
            return DBUS_HANDLER_RESULT_HANDLED;
        }
        if (!card_ask(m, d, addr, code)) return DBUS_HANDLER_RESULT_HANDLED; /* answered from the card */
    }
    if (!strcmp(member, "RequestConfirmation") || !strcmp(member, "RequestAuthorization") || !strcmp(member, "AuthorizeService") ||
        !strcmp(member, "RequestPinCode")) {
        int yes = !strcmp(member, "AuthorizeService") ? d && (d->paired || agent_allows(dev)) : agent_allows(dev);
        unsigned passkey = 0;
        if (!strcmp(member, "RequestConfirmation") && dbus_message_iter_next(&it) && dbus_message_iter_get_arg_type(&it) == DBUS_TYPE_UINT32)
            dbus_message_iter_get_basic(&it, &passkey);
        say("agent: %s from %s: %s", member, addr, yes ? "yes" : "no");
        if (strcmp(member, "AuthorizeService")) tell("pair-request addr=%s passkey=%06u answer=%s", addr, passkey, yes ? "yes" : "no");
        if (!yes) r = dbus_message_new_error(m, "org.bluez.Error.Rejected", "not pairing");
        else if (!strcmp(member, "RequestPinCode")) { /* a device older than SSP: stock's "0000" */
            const char *pin = "0000";
            r = dbus_message_new_method_return(m);
            dbus_message_append_args(r, DBUS_TYPE_STRING, &pin, DBUS_TYPE_INVALID);
        } else r = dbus_message_new_method_return(m);
    } else if (!strcmp(member, "RequestPasskey")) { /* a passkey typed on the Q2: no keyboard (stock refuses it too) */
        say("agent: RequestPasskey from %s: refused", addr);
        r = dbus_message_new_error(m, "org.bluez.Error.Rejected", "no keyboard");
    } else if (!strcmp(member, "DisplayPasskey") || !strcmp(member, "DisplayPinCode") || !strcmp(member, "Release") ||
               !strcmp(member, "Cancel")) {
        say("agent: %s", member);
        if (!strcmp(member, "Cancel") && card.m) { /* BlueZ gave up: the card goes with no answer, as bt_pair_poll */
            char w[40];
            snprintf(w, sizeof w, "withdraw %s", card.id);
            ui_send(w);
            dbus_message_unref(card.m), card.m = 0;
            tell("pair-request addr=%s passkey=%06u answer=cancelled", card.addr, card.passkey);
        }
        r = dbus_message_new_method_return(m);
    } else return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    if (r) dbus_connection_send(c, r, 0), dbus_message_unref(r);
    return DBUS_HANDLER_RESULT_HANDLED;
}

/* ---- the player BlueZ's AVRCP target serves: the headset's buttons and its track display ---- */

static void dict_s(DBusMessageIter *d, const char *k, const char *sig, int type, const void *v) {
    DBusMessageIter e, var;
    dbus_message_iter_open_container(d, DBUS_TYPE_DICT_ENTRY, 0, &e);
    dbus_message_iter_append_basic(&e, DBUS_TYPE_STRING, &k);
    dbus_message_iter_open_container(&e, DBUS_TYPE_VARIANT, sig, &var);
    dbus_message_iter_append_basic(&var, type, v);
    dbus_message_iter_close_container(&e, &var);
    dbus_message_iter_close_container(d, &e);
}
/* Metadata's value, a variant holding the a{sv} MPRIS names: q2playd's `now` */
static void metadata_variant(DBusMessageIter *parent) {
    DBusMessageIter var, md, me, mv, arr;
    const char *t = np.title, *al = np.album, *ar = np.artist, *kar = "xesam:artist";
    char track[64];
    const char *tp = track;
    long long len = np.len * 1000000;
    snprintf(track, sizeof track, "/vamp/q2btd/track/%d", np.id > 0 ? np.id : 0);
    dbus_message_iter_open_container(parent, DBUS_TYPE_VARIANT, "a{sv}", &var);
    dbus_message_iter_open_container(&var, DBUS_TYPE_ARRAY, "{sv}", &md);
    dict_s(&md, "xesam:title", "s", DBUS_TYPE_STRING, &t);
    dict_s(&md, "xesam:album", "s", DBUS_TYPE_STRING, &al);
    dbus_message_iter_open_container(&md, DBUS_TYPE_DICT_ENTRY, 0, &me);
    dbus_message_iter_append_basic(&me, DBUS_TYPE_STRING, &kar);
    dbus_message_iter_open_container(&me, DBUS_TYPE_VARIANT, "as", &mv);
    dbus_message_iter_open_container(&mv, DBUS_TYPE_ARRAY, "s", &arr);
    dbus_message_iter_append_basic(&arr, DBUS_TYPE_STRING, &ar);
    dbus_message_iter_close_container(&mv, &arr);
    dbus_message_iter_close_container(&me, &mv);
    dbus_message_iter_close_container(&md, &me);
    dict_s(&md, "mpris:length", "x", DBUS_TYPE_INT64, &len);
    dict_s(&md, "mpris:trackid", "o", DBUS_TYPE_OBJECT_PATH, &tp);
    dbus_message_iter_close_container(&var, &md);
    dbus_message_iter_close_container(parent, &var);
}
static const char *PLAYER_PROPS[] = {"PlaybackStatus", "Metadata", "Position", "CanGoNext", "CanGoPrevious", "CanPlay", "CanPause",
                                     "CanControl"};
/* one property's value as a variant appended to it; 0 for a name the player does not have */
static int prop_variant(DBusMessageIter *it, const char *prop) {
    DBusMessageIter v;
    const char *st = np.state == 1 ? "Playing" : np.state == 2 ? "Paused" : "Stopped";
    long long pos = np.pos * 1000000;
    dbus_bool_t yes = TRUE;
    if (!strcmp(prop, "Metadata")) return metadata_variant(it), 1;
    if (!strcmp(prop, "PlaybackStatus")) dbus_message_iter_open_container(it, DBUS_TYPE_VARIANT, "s", &v), dbus_message_iter_append_basic(&v, DBUS_TYPE_STRING, &st);
    else if (!strcmp(prop, "Position")) dbus_message_iter_open_container(it, DBUS_TYPE_VARIANT, "x", &v), dbus_message_iter_append_basic(&v, DBUS_TYPE_INT64, &pos);
    else if (!strncmp(prop, "Can", 3)) dbus_message_iter_open_container(it, DBUS_TYPE_VARIANT, "b", &v), dbus_message_iter_append_basic(&v, DBUS_TYPE_BOOLEAN, &yes);
    else return 0;
    dbus_message_iter_close_container(it, &v);
    return 1;
}
/* the player's properties as dict entries: all of them, or what a track change moves */
static void player_props(DBusMessageIter *d, int all) {
    for (unsigned i = 0; i < (all ? sizeof PLAYER_PROPS / sizeof *PLAYER_PROPS : 2); ++i) {
        DBusMessageIter e;
        dbus_message_iter_open_container(d, DBUS_TYPE_DICT_ENTRY, 0, &e);
        dbus_message_iter_append_basic(&e, DBUS_TYPE_STRING, &PLAYER_PROPS[i]);
        prop_variant(&e, PLAYER_PROPS[i]);
        dbus_message_iter_close_container(d, &e);
    }
}
static int player_registered;
static void player_changed(void) {
    if (!player_registered) return;
    DBusMessage *s = dbus_message_new_signal(PLAYER_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged");
    DBusMessageIter it, d, inv;
    const char *iface = "org.mpris.MediaPlayer2.Player";
    if (!s) return;
    dbus_message_iter_init_append(s, &it);
    dbus_message_iter_append_basic(&it, DBUS_TYPE_STRING, &iface);
    dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{sv}", &d);
    player_props(&d, 0);
    dbus_message_iter_close_container(&it, &d);
    dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "s", &inv);
    dbus_message_iter_close_container(&it, &inv);
    dbus_connection_send(bus, s, 0);
    dbus_message_unref(s);
}
static DBusHandlerResult player_msg(DBusConnection *c, DBusMessage *m, void *data) {
    (void)data;
    const char *member = dbus_message_get_member(m);
    DBusMessage *r = 0;
    if (dbus_message_has_interface(m, "org.mpris.MediaPlayer2.Player")) {
        static const char *verbs[][2] = {{"Play", "resume"}, {"Pause", "pause"}, {"PlayPause", "toggle"}, {"Stop", "pause"},
                                         {"Next", "next"}, {"Previous", "previous"}};
        for (unsigned i = 0; i < sizeof verbs / sizeof *verbs; ++i)
            if (!strcmp(member, verbs[i][0])) {
                say("the headset's %s: %s", member, verbs[i][1]);
                play_say("%s", verbs[i][1]);
                r = dbus_message_new_method_return(m);
            }
        if (!r) r = dbus_message_new_error(m, "org.freedesktop.DBus.Error.NotSupported", member);
    } else if (dbus_message_has_interface(m, "org.freedesktop.DBus.Properties") && (!strcmp(member, "GetAll") || !strcmp(member, "Get"))) {
        const char *iface = "", *prop = "";
        dbus_message_get_args(m, 0, DBUS_TYPE_STRING, &iface, DBUS_TYPE_STRING, &prop, DBUS_TYPE_INVALID);
        r = dbus_message_new_method_return(m);
        DBusMessageIter it, d;
        dbus_message_iter_init_append(r, &it);
        if (!strcmp(member, "GetAll")) {
            dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{sv}", &d);
            player_props(&d, 1);
            dbus_message_iter_close_container(&it, &d);
        } else if (!prop_variant(&it, prop)) {
            dbus_message_unref(r);
            r = dbus_message_new_error(m, "org.freedesktop.DBus.Error.InvalidArgs", prop);
        }
    } else return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    dbus_connection_send(c, r, 0);
    dbus_message_unref(r);
    return DBUS_HANDLER_RESULT_HANDLED;
}

/* ---- what the daemons hold, read whole: at the start and each time one comes (back) on the bus ---- */

static void objects_cb(DBusMessage *r, void *ctx) {
    DBusMessageIter it, objs, e, ifaces;
    free(ctx);
    if (failed(r, 0, 0) || !dbus_message_iter_init(r, &it) || dbus_message_iter_get_arg_type(&it) != DBUS_TYPE_ARRAY) return;
    for (dbus_message_iter_recurse(&it, &objs); dbus_message_iter_get_arg_type(&objs) == DBUS_TYPE_DICT_ENTRY; dbus_message_iter_next(&objs)) {
        const char *p;
        dbus_message_iter_recurse(&objs, &e);
        dbus_message_iter_get_basic(&e, &p);
        dbus_message_iter_next(&e);
        dbus_message_iter_recurse(&e, &ifaces);
        object_apply(p, &ifaces);
    }
    route();
}
static void registered_cb(DBusMessage *r, void *ctx) {
    char why[96];
    if (failed(r, why, sizeof why)) say("%s: %s", (char *)ctx, why);
    else {
        say("%s", (char *)ctx);
        if (!strcmp(ctx, "player registered")) player_registered = 1;
    }
    free(ctx);
}
static void bluez_up(void) {
    const char *agent = AGENT_PATH, *io = "DisplayYesNo";
    DBusMessage *m = method("org.bluez", "/org/bluez", "org.bluez.AgentManager1", "RegisterAgent");
    dbus_message_append_args(m, DBUS_TYPE_OBJECT_PATH, &agent, DBUS_TYPE_STRING, &io, DBUS_TYPE_INVALID);
    call(m, CALL_MS, registered_cb, strdup("agent registered"));
    m = method("org.bluez", "/org/bluez", "org.bluez.AgentManager1", "RequestDefaultAgent");
    dbus_message_append_args(m, DBUS_TYPE_OBJECT_PATH, &agent, DBUS_TYPE_INVALID);
    call(m, CALL_MS, registered_cb, strdup("the default agent"));
    call(method("org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"), CALL_MS, objects_cb, 0);
}
/* the player, once the adapter is there (Media1 is the adapter's) */
static void player_register(void) {
    if (!adapter.have || player_registered) return;
    player_registered = -1; /* asked */
    const char *pp = PLAYER_PATH;
    DBusMessage *m = method("org.bluez", adapter.path, "org.bluez.Media1", "RegisterPlayer");
    DBusMessageIter it, d;
    dbus_message_iter_init_append(m, &it);
    dbus_message_iter_append_basic(&it, DBUS_TYPE_OBJECT_PATH, &pp);
    dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{sv}", &d);
    player_props(&d, 1);
    dbus_message_iter_close_container(&it, &d);
    call(m, CALL_MS, registered_cb, strdup("player registered"));
}
static void bluez_gone(void) {
    say("org.bluez gone");
    memset(devs, 0, sizeof devs);
    memset(&adapter, 0, sizeof adapter);
    connecting[0] = pairing[0] = 0;
    player_registered = 0;
    power_asked = -1; /* a bluetoothd started again starts unpowered: asked again */
    ++seq;
    route();
}
static void bluealsa_up(void) {
    call(method("org.bluealsa", "/org/bluealsa", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"), CALL_MS, objects_cb, 0);
}
static void bluealsa_gone(void) {
    say("org.bluealsa gone");
    memset(pcms, 0, sizeof pcms);
    ++seq;
    route();
}

static DBusHandlerResult on_signal(DBusConnection *c, DBusMessage *m, void *data) {
    (void)c, (void)data;
    if (dbus_message_get_type(m) != DBUS_MESSAGE_TYPE_SIGNAL) return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    const char *member = dbus_message_get_member(m), *p = dbus_message_get_path(m);
    DBusMessageIter it, sub;
    if (dbus_message_is_signal(m, "org.freedesktop.DBus", "NameOwnerChanged")) {
        const char *name, *old, *now;
        if (!dbus_message_get_args(m, 0, DBUS_TYPE_STRING, &name, DBUS_TYPE_STRING, &old, DBUS_TYPE_STRING, &now, DBUS_TYPE_INVALID))
            return DBUS_HANDLER_RESULT_HANDLED;
        int bz = !strcmp(name, "org.bluez"), ba = !strcmp(name, "org.bluealsa");
        if ((bz || ba) && *old) bz ? bluez_gone() : bluealsa_gone();
        if ((bz || ba) && *now) say("%s on the bus", name), bz ? bluez_up() : bluealsa_up();
        return DBUS_HANDLER_RESULT_HANDLED;
    }
    if (!dbus_message_iter_init(m, &it)) return DBUS_HANDLER_RESULT_HANDLED;
    if (!strcmp(member, "InterfacesAdded") || !strcmp(member, "InterfacesRemoved")) {
        const char *o;
        if (dbus_message_iter_get_arg_type(&it) != DBUS_TYPE_OBJECT_PATH) return DBUS_HANDLER_RESULT_HANDLED;
        dbus_message_iter_get_basic(&it, &o);
        dbus_message_iter_next(&it);
        dbus_message_iter_recurse(&it, &sub);
        if (member[10] == 'A') object_apply(o, &sub);
        else object_removed(o, &sub);
        route();
    } else if (!strcmp(member, "PropertiesChanged") && p) {
        const char *iface;
        if (dbus_message_iter_get_arg_type(&it) != DBUS_TYPE_STRING) return DBUS_HANDLER_RESULT_HANDLED;
        dbus_message_iter_get_basic(&it, &iface);
        dbus_message_iter_next(&it);
        if (dbus_message_iter_get_arg_type(&it) != DBUS_TYPE_ARRAY) return DBUS_HANDLER_RESULT_HANDLED;
        dbus_message_iter_recurse(&it, &sub);
        props_apply(p, iface, &sub);
    }
    return DBUS_HANDLER_RESULT_HANDLED;
}

/* ---- Q2BT/1's commands ---- */

static void power_cb(DBusMessage *r, void *ctx) {
    struct waiter *w = ctx;
    struct client *c = w->ci >= 0 ? waiting(w) : 0;
    char why[96];
    if (failed(r, why, sizeof why)) {
        say("power: %s", why);
        power_asked = -1;
        if (c) client_say(c, "err power");
    } else if (c) client_say(c, "ok");
    free(w);
}
static void power_set(int on, struct client *c) {
    struct waiter *w = c ? waiter(c, "power", 0) : calloc(1, sizeof *w);
    if (!w) return;
    if (!c) w->ci = -1;
    power_asked = on;
    say("power %s", on ? "on" : "off");
    call(set_prop("org.bluez", adapter.path, "org.bluez.Adapter1", "Powered", DBUS_TYPE_BOOLEAN, on), CALL_MS, power_cb, w);
}
static void scan_stop(void) {
    scan_until = 0;
    if (!adapter.have) return;
    call_logged(method("org.bluez", adapter.path, "org.bluez.Adapter1", "StopDiscovery"), CALL_MS, "StopDiscovery");
    call_logged(set_prop("org.bluez", adapter.path, "org.bluez.Adapter1", "Discoverable", DBUS_TYPE_BOOLEAN, 0), CALL_MS, "Discoverable");
}
static void paired_cb(DBusMessage *r, void *ctx) {
    struct waiter *w = ctx;
    struct client *c = waiting(w);
    struct dev *d = dev_by_path(w->path, 0);
    char why[96];
    if (!strcmp(pairing, w->path)) pairing[0] = 0;
    if (failed(r, why, sizeof why) && strcmp(why, "AlreadyExists")) {
        say("pair %s: %s", w->path, why);
        if (c) client_say(c, "err pair");
    } else {
        say("paired %s", d ? d->addr : w->path);
        call_logged(set_prop("org.bluez", w->path, "org.bluez.Device1", "Trusted", DBUS_TYPE_BOOLEAN, 1), CALL_MS, "Trusted");
        if (c) client_say(c, "ok");
        if (d && !d->connected) connect_dev(d, 0); /* paired: played to at once, as stock's page does */
    }
    free(w);
}

static void client_line(struct client *c, char *line) {
    char a[1200], b[64];
    if (!c->hello) {
        if (strcmp(line, "Q2BT/1")) { client_say(c, "err version"); close(c->fd), c->fd = -1; return; }
        c->hello = 1;
        client_say(c, "Q2BT/1 ok");
        return;
    }
    struct dev *d = 0;
    if (sscanf(line, "%*s %63s", b) == 1) dec(b), d = dev_by_addr(b);
    if (!strcmp(line, "caps")) { /* Vamp API 1's capability words (docs/platform/README.md#capabilities) */
        client_say(c, "caps status power scan devices pair connect disconnect forget codec watch");
    } else if (!strcmp(line, "status")) {
        status_line(a, sizeof a);
        client_say(c, "%s", a);
    } else if (!strcmp(line, "power on") || !strcmp(line, "power off")) {
        int on = line[7] == 'n';
        if (power_wanted != on) power_wanted = on, state_save();
        if (!adapter.have) client_say(c, "err no adapter");
        else if (adapter.powered == on) client_say(c, "ok");
        else power_set(on, c);
        if (on && !adapter.powered) rc.on = 0; /* the round starts when Powered comes */
    } else if (!strcmp(line, "scan")) {
        if (!adapter.powered) { client_say(c, "err power"); return; }
        scan_until = now_ms() + SCAN_MS;
        call_logged(set_prop("org.bluez", adapter.path, "org.bluez.Adapter1", "Discoverable", DBUS_TYPE_BOOLEAN, 1), CALL_MS, "Discoverable");
        for (int i = 0; i < DEVS; ++i) devs[i].announced = 0;
        call(method("org.bluez", adapter.path, "org.bluez.Adapter1", "StartDiscovery"), CALL_MS, answered, waiter(c, "scan", 0));
    } else if (!strcmp(line, "devices")) {
        for (int i = 0; i < DEVS; ++i)
            if (devs[i].used && devs[i].addr[0]) device_line(c, &devs[i]);
        client_say(c, "ok");
    } else if (!strncmp(line, "pair ", 5)) {
        if (!d) { client_say(c, "err no such device"); return; }
        if (pairing[0]) { client_say(c, "err busy"); return; }
        snprintf(pairing, sizeof pairing, "%s", d->path);
        call(method("org.bluez", d->path, "org.bluez.Device1", "Pair"), PAIR_CALL_MS, paired_cb, waiter(c, "pair", d->path));
    } else if (!strncmp(line, "connect ", 8)) {
        if (!d) client_say(c, "err no such device");
        else if (!d->paired) client_say(c, "err not paired");
        else if (d->connected) client_say(c, "ok");
        else connect_dev(d, c);
    } else if (!strcmp(line, "disconnect") || !strncmp(line, "disconnect ", 11)) {
        if (line[10] != ' ') d = the_dev();
        if (!d || !d->connected) { client_say(c, "err not connected"); return; }
        d->asked_off = 1, rc.on = 0;
        call(method("org.bluez", d->path, "org.bluez.Device1", "Disconnect"), CALL_MS, answered, waiter(c, "disconnect", d->path));
    } else if (!strncmp(line, "forget ", 7)) {
        if (!d) { client_say(c, "err no such device"); return; }
        if (!strcasecmp(d->addr, last)) last[0] = 0, rc.on = 0, state_save();
        const char *dp = d->path;
        d->asked_off = 1;
        DBusMessage *m = method("org.bluez", adapter.path, "org.bluez.Adapter1", "RemoveDevice");
        dbus_message_append_args(m, DBUS_TYPE_OBJECT_PATH, &dp, DBUS_TYPE_INVALID);
        call(m, CALL_MS, answered, waiter(c, "forget", dp));
    } else if (!strcmp(line, "codec")) {
        struct pcm *m = the_pcm();
        client_say(c, "codec name=%s rate=%d available=%s choice=%s", m ? m->codec : "", m ? m->rate : 0, m ? m->available : "", codec_choice);
    } else if (!strncmp(line, "codec ", 6)) {
        snprintf(b, sizeof b, "%s", line + 6), dec(b);
        struct pcm *m = the_pcm();
        int offered = !strcmp(b, "auto");
        for (char *t = m ? m->available : ""; *t && !offered;) {
            size_t k = strcspn(t, ",");
            offered = k == strlen(b) && !strncasecmp(t, b, k);
            t += k + (t[k] == ',');
        }
        if (m && !offered) { client_say(c, "err codec"); return; }
        if (strcmp(codec_choice, b)) snprintf(codec_choice, sizeof codec_choice, "%s", b), state_save();
        if (!m || !strcmp(b, "auto") || !strcasecmp(m->codec, b)) { client_say(c, "ok"); return; }
        DBusMessage *s = method("org.bluealsa", m->path, "org.bluealsa.PCM1", "SelectCodec");
        DBusMessageIter si, sd;
        const char *cc = b;
        dbus_message_iter_init_append(s, &si);
        dbus_message_iter_append_basic(&si, DBUS_TYPE_STRING, &cc);
        dbus_message_iter_open_container(&si, DBUS_TYPE_ARRAY, "{sv}", &sd);
        dbus_message_iter_close_container(&si, &sd);
        call(s, CALL_MS, answered, waiter(c, "codec", m->path));
    } else if (!strcmp(line, "watch")) {
        c->watch = 1;
        client_say(c, "ok");
    } else {
        client_say(c, "err %s", line);
    }
}
static void client_read(struct client *c) {
    ssize_t got = recv(c->fd, c->in + c->n, sizeof c->in - 1 - c->n, 0);
    if (got <= 0) { close(c->fd), c->fd = -1; return; }
    c->n += (size_t)got;
    char *p = c->in, *nl;
    while (c->fd >= 0 && (nl = memchr(p, '\n', c->n - (size_t)(p - c->in)))) {
        *nl = 0;
        if (nl > p && nl[-1] == '\r') nl[-1] = 0;
        client_line(c, p);
        p = nl + 1;
    }
    if (c->fd < 0) return;
    c->n -= (size_t)(p - c->in);
    memmove(c->in, p, c->n);
    if (c->n >= sizeof c->in - 1) close(c->fd), c->fd = -1;
}

static volatile sig_atomic_t stopping;
static void on_term(int s) { (void)s, stopping = 1; }

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else return fprintf(stderr, "usage: q2btd [--root DIR]\n"), 2;
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    for (int i = 0; i < CLIENTS; ++i) cl[i].fd = -1;
    state_load();
    DBusError err;
    dbus_error_init(&err);
    bus = dbus_bus_get_private(DBUS_BUS_SYSTEM, &err);
    if (!bus) return say("no system bus: %s", err.message), 1;
    dbus_connection_set_exit_on_disconnect(bus, FALSE);
    dbus_connection_set_timeout_functions(bus, tmo_add, tmo_remove, tmo_toggled, 0, 0);
    static const DBusObjectPathVTable agent_vt = {.message_function = agent_msg}, player_vt = {.message_function = player_msg};
    dbus_connection_register_object_path(bus, AGENT_PATH, &agent_vt, 0);
    dbus_connection_register_object_path(bus, PLAYER_PATH, &player_vt, 0);
    dbus_connection_add_filter(bus, on_signal, 0, 0);
    static const char *rules[] = {
        "type='signal',sender='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.bluez'",
        "type='signal',sender='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.bluealsa'",
        "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.ObjectManager'",
        "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'",
        "type='signal',sender='org.bluealsa',interface='org.freedesktop.DBus.ObjectManager'",
        "type='signal',sender='org.bluealsa',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'",
    };
    for (unsigned i = 0; i < sizeof rules / sizeof *rules; ++i) dbus_bus_add_match(bus, rules[i], 0);
    if (dbus_bus_name_has_owner(bus, "org.bluez", 0)) bluez_up();
    else say("no org.bluez yet");
    if (dbus_bus_name_has_owner(bus, "org.bluealsa", 0)) bluealsa_up();
    else say("no org.bluealsa yet");

    mkdir(path("/run"), 0755), mkdir(path("/run/vamp"), 0755);
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    if (strlen(path("/run/vamp/bt.sock")) >= sizeof a.sun_path) return say("--root too long"), 2;
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/bt.sock"));
    unlink(a.sun_path);
    api = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (api < 0 || bind(api, (struct sockaddr *)&a, sizeof a) || listen(api, CLIENTS)) say("%s: %s", a.sun_path, strerror(errno));
    chmod(a.sun_path, 0600);
    say("start: power %s, last device %s, codec %s", power_wanted ? "on" : "off", last[0] ? last : "none", codec_choice);
    int dfd = -1;
    dbus_connection_get_unix_fd(bus, &dfd);
    unsigned seen = ~0u;
    int was_powered = 0, started = 0;
    while (!stopping) {
        struct pollfd p[4 + CLIENTS];
        int n = 0;
        p[n++] = (struct pollfd){.fd = dfd, .events = POLLIN | (dbus_connection_has_messages_to_send(bus) ? POLLOUT : 0)};
        p[n++] = (struct pollfd){.fd = api, .events = POLLIN};
        p[n++] = (struct pollfd){.fd = play_fd, .events = POLLIN};
        p[n++] = (struct pollfd){.fd = ui_fd, .events = POLLIN};
        for (int i = 0; i < CLIENTS; ++i) p[n++] = (struct pollfd){.fd = cl[i].fd, .events = POLLIN};
        poll(p, (nfds_t)n, tmo_next(500));
        dbus_connection_read_write(bus, 0);
        while (dbus_connection_dispatch(bus) == DBUS_DISPATCH_DATA_REMAINS) {}
        tmo_run();
        while (dbus_connection_dispatch(bus) == DBUS_DISPATCH_DATA_REMAINS) {}
        if (!dbus_connection_get_is_connected(bus)) return say("the system bus went away"), 1;
        if (p[1].revents & POLLIN) {
            int fd = accept4(api, 0, 0, SOCK_CLOEXEC), i = 0;
            while (fd >= 0 && i < CLIENTS && cl[i].fd >= 0) ++i;
            if (fd >= 0 && i == CLIENTS) close(fd);
            else if (fd >= 0) cl[i] = (struct client){.fd = fd, .gen = ++gens};
        }
        if (play_fd >= 0 && p[2].fd == play_fd && p[2].revents) play_read();
        if (ui_fd >= 0 && p[3].fd == ui_fd && p[3].revents) ui_read();
        for (int i = 0; i < CLIENTS; ++i)
            if (cl[i].fd >= 0 && p[4 + i].fd == cl[i].fd && p[4 + i].revents) client_read(&cl[i]);
        unsigned long long t = now_ms();
        if (play_fd < 0 && t >= play_tried + PLAY_RETRY_MS) play_connect(), route();
        if (adapter.have && adapter.powered != power_wanted && power_asked != power_wanted) power_set(power_wanted, 0);
        if (adapter.have) player_register();
        if (adapter.powered && !was_powered) reconnect_round(started ? "powered on" : "start"), started = 1;
        was_powered = adapter.powered;
        if (scan_until && t >= scan_until) scan_stop();
        reconnect_poll();
        if (seq != seen) {
            char s[1200];
            status_line(s, sizeof s);
            static char told[1200];
            if (strcmp(s, told)) tell("%s", s), snprintf(told, sizeof told, "%s", s);
            seen = seq;
        }
    }
    unlink(path("/run/vamp/bt.sock"));
    return 0;
}
