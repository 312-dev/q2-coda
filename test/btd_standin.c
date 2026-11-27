/* test/btd.py's stand-in for BlueZ (org.bluez) and bluez-alsa (org.bluealsa): one adapter, hci0, and the made-up
 * devices its arguments give, on the bus DBUS_SYSTEM_BUS_ADDRESS names, each name owned by a connection of its own as
 * the two daemons would. It answers the calls q2btd makes in the shapes BlueZ 5.88 and bluez-alsa 5.0.0 use (their
 * doc/ folders), sends their signals, and prints a line for each thing done to it on stdout; lines on stdin are what a
 * radio and a headset would do (`drop ADDR`, `come ADDR`, `reach ADDR 0|1`, `hvol ADDR LEVEL`, `battery ADDR N`,
 * `button Play`, `pairin ADDR`, `quit`). Nothing here is BlueZ's or bluealsa's code.
 *
 *     btd_standin --dev ADDR,NAME,CLASS,PAIRED,REACHABLE,BATTERY,CODECS ... --found ADDR,NAME,CLASS ...
 *
 * CLASS in hex, CODECS bluealsa's names joined by '+', the first the one a connection starts on. */
#define _GNU_SOURCE
#include <dbus/dbus.h>
#include <poll.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define DEVS 16
static DBusConnection *bz, *ba;
static struct dev {
    int used, paired, connected, trusted, reachable, battery, found, shown, vol;
    unsigned cls;
    char addr[18], name[64], path[64], codecs[96], codec[24], pcm[128];
} devs[DEVS];
static int powered, discovering, discoverable;
static char agent_owner[64], agent_path[128], player_owner[64], player_path[128];

static void out(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void out(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    putchar('\n');
    fflush(stdout);
}
static struct dev *by(const char *key) { /* an address or an object path */
    for (int i = 0; i < DEVS; ++i)
        if (devs[i].used && (!strcasecmp(devs[i].addr, key) || !strcmp(devs[i].path, key))) return &devs[i];
    return 0;
}
static struct dev *by_pcm(const char *p) {
    for (int i = 0; i < DEVS; ++i)
        if (devs[i].used && devs[i].connected && !strcmp(devs[i].pcm, p)) return &devs[i];
    return 0;
}
static void add_dev(const char *spec, int found) {
    struct dev *d = 0;
    for (int i = 0; i < DEVS && !d; ++i)
        if (!devs[i].used) d = &devs[i];
    char buf[256], *f[7] = {0};
    snprintf(buf, sizeof buf, "%s", spec);
    f[0] = strtok(buf, ",");
    for (int i = 1; i < 7; ++i) f[i] = strtok(0, ",");
    *d = (struct dev){.used = 1, .found = found, .reachable = 1, .battery = -1, .vol = 127};
    snprintf(d->addr, sizeof d->addr, "%s", f[0]);
    snprintf(d->name, sizeof d->name, "%s", f[1] ? f[1] : "");
    d->cls = f[2] ? (unsigned)strtoul(f[2], 0, 16) : 0;
    d->paired = d->trusted = f[3] ? atoi(f[3]) : 0;
    if (f[4]) d->reachable = atoi(f[4]);
    if (f[5]) d->battery = atoi(f[5]);
    snprintf(d->codecs, sizeof d->codecs, "%s", f[6] ? f[6] : "SBC");
    snprintf(d->codec, sizeof d->codec, "%.*s", (int)strcspn(d->codecs, "+"), d->codecs);
    snprintf(d->path, sizeof d->path, "/org/bluez/hci0/dev_%s", d->addr);
    for (char *p = d->path; *p; ++p)
        if (*p == ':') *p = '_';
    snprintf(d->pcm, sizeof d->pcm, "/org/bluealsa/hci0/dev_%s/a2dpsrc/sink", d->path + strlen("/org/bluez/hci0/dev_"));
    d->shown = !found;
}

/* ---- building values ---- */

static void entry(DBusMessageIter *d, const char *k, const char *sig, int type, const void *v) {
    DBusMessageIter e, var;
    dbus_message_iter_open_container(d, DBUS_TYPE_DICT_ENTRY, 0, &e);
    dbus_message_iter_append_basic(&e, DBUS_TYPE_STRING, &k);
    dbus_message_iter_open_container(&e, DBUS_TYPE_VARIANT, sig, &var);
    dbus_message_iter_append_basic(&var, type, v);
    dbus_message_iter_close_container(&e, &var);
    dbus_message_iter_close_container(d, &e);
}
static void entry_b(DBusMessageIter *d, const char *k, int v) {
    dbus_bool_t b = v != 0;
    entry(d, k, "b", DBUS_TYPE_BOOLEAN, &b);
}
static void entry_s(DBusMessageIter *d, const char *k, const char *v) { entry(d, k, "s", DBUS_TYPE_STRING, &v); }
static void entry_vol(DBusMessageIter *d, int level) {
    DBusMessageIter e, var, a;
    const char *k = "Volume";
    unsigned char lr[2] = {(unsigned char)level, (unsigned char)level};
    const unsigned char *p = lr;
    dbus_message_iter_open_container(d, DBUS_TYPE_DICT_ENTRY, 0, &e);
    dbus_message_iter_append_basic(&e, DBUS_TYPE_STRING, &k);
    dbus_message_iter_open_container(&e, DBUS_TYPE_VARIANT, "ay", &var);
    dbus_message_iter_open_container(&var, DBUS_TYPE_ARRAY, "y", &a);
    dbus_message_iter_append_fixed_array(&a, DBUS_TYPE_BYTE, &p, 2);
    dbus_message_iter_close_container(&var, &a);
    dbus_message_iter_close_container(&e, &var);
    dbus_message_iter_close_container(d, &e);
}
static void adapter_props(DBusMessageIter *d) {
    entry_s(d, "Address", "00:11:22:33:44:55");
    entry_s(d, "Alias", "Vamp");
    entry_b(d, "Powered", powered);
    entry_b(d, "Discovering", discovering);
    entry_b(d, "Discoverable", discoverable);
}
static void dev_props(DBusMessageIter *d, const struct dev *v) {
    const char *adapter = "/org/bluez/hci0";
    entry_s(d, "Address", v->addr);
    entry_s(d, "Alias", v->name);
    entry_s(d, "Name", v->name);
    entry(d, "Class", "u", DBUS_TYPE_UINT32, &v->cls);
    entry_b(d, "Paired", v->paired);
    entry_b(d, "Bonded", v->paired);
    entry_b(d, "Trusted", v->trusted);
    entry_b(d, "Connected", v->connected);
    entry(d, "Adapter", "o", DBUS_TYPE_OBJECT_PATH, &adapter);
}
static void pcm_props(DBusMessageIter *d, const struct dev *v) {
    const char *dp = v->path;
    unsigned rate = !strcmp(v->codec, "LDAC") ? 96000 : 48000;
    entry(d, "Device", "o", DBUS_TYPE_OBJECT_PATH, &dp);
    entry_s(d, "Transport", "A2DP-source");
    entry_s(d, "Mode", "sink");
    entry_b(d, "Running", 0);
    entry(d, "Rate", "u", DBUS_TYPE_UINT32, &rate);
    entry_s(d, "Codec", v->codec);
    entry_b(d, "SoftVolume", 0);
    entry_vol(d, v->vol);
}
/* one object's a{sa{sv}}: kind 'a' the adapter, 'd' a device (with Battery1 while connected and reporting), 'p' its PCM */
static void interfaces(DBusMessageIter *it, int kind, const struct dev *v) {
    DBusMessageIter a, e, d;
    const char *names[2];
    int n = 0;
    names[n++] = kind == 'a' ? "org.bluez.Adapter1" : kind == 'd' ? "org.bluez.Device1" : "org.bluealsa.PCM1";
    if (kind == 'd' && v->connected && v->battery >= 0) names[n++] = "org.bluez.Battery1";
    dbus_message_iter_open_container(it, DBUS_TYPE_ARRAY, "{sa{sv}}", &a);
    for (int i = 0; i < n; ++i) {
        dbus_message_iter_open_container(&a, DBUS_TYPE_DICT_ENTRY, 0, &e);
        dbus_message_iter_append_basic(&e, DBUS_TYPE_STRING, &names[i]);
        dbus_message_iter_open_container(&e, DBUS_TYPE_ARRAY, "{sv}", &d);
        if (kind == 'a') adapter_props(&d);
        else if (kind == 'p') pcm_props(&d, v);
        else if (i == 0) dev_props(&d, v);
        else {
            unsigned char pct = (unsigned char)v->battery;
            entry(&d, "Percentage", "y", DBUS_TYPE_BYTE, &pct);
        }
        dbus_message_iter_close_container(&e, &d);
        dbus_message_iter_close_container(&a, &e);
    }
    dbus_message_iter_close_container(it, &a);
}
static void object(DBusMessageIter *objs, const char *p, int kind, const struct dev *v) {
    DBusMessageIter e;
    dbus_message_iter_open_container(objs, DBUS_TYPE_DICT_ENTRY, 0, &e);
    dbus_message_iter_append_basic(&e, DBUS_TYPE_OBJECT_PATH, &p);
    interfaces(&e, kind, v);
    dbus_message_iter_close_container(objs, &e);
}

/* ---- signals ---- */

static void added(DBusConnection *c, const char *p, int kind, const struct dev *v) {
    DBusMessage *s = dbus_message_new_signal("/", "org.freedesktop.DBus.ObjectManager", "InterfacesAdded");
    DBusMessageIter it;
    dbus_message_iter_init_append(s, &it);
    dbus_message_iter_append_basic(&it, DBUS_TYPE_OBJECT_PATH, &p);
    interfaces(&it, kind, v);
    if (c == ba) dbus_message_set_path(s, "/org/bluealsa");
    dbus_connection_send(c, s, 0), dbus_message_unref(s);
}
static void removed(DBusConnection *c, const char *p, const char *iface) {
    DBusMessage *s = dbus_message_new_signal(c == ba ? "/org/bluealsa" : "/", "org.freedesktop.DBus.ObjectManager", "InterfacesRemoved");
    DBusMessageIter it, a;
    dbus_message_iter_init_append(s, &it);
    dbus_message_iter_append_basic(&it, DBUS_TYPE_OBJECT_PATH, &p);
    dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "s", &a);
    dbus_message_iter_append_basic(&a, DBUS_TYPE_STRING, &iface);
    dbus_message_iter_close_container(&it, &a);
    dbus_connection_send(c, s, 0), dbus_message_unref(s);
}
/* PropertiesChanged of one property: 'b' a bool, 's' a string, 'v' the PCM's Volume, 'y' a byte */
static void changed(DBusConnection *c, const char *p, const char *iface, const char *k, int t, const void *v) {
    DBusMessage *s = dbus_message_new_signal(p, "org.freedesktop.DBus.Properties", "PropertiesChanged");
    DBusMessageIter it, d, inv;
    dbus_message_iter_init_append(s, &it);
    dbus_message_iter_append_basic(&it, DBUS_TYPE_STRING, &iface);
    dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{sv}", &d);
    if (t == 'b') entry_b(&d, k, *(const int *)v);
    else if (t == 's') entry_s(&d, k, v);
    else if (t == 'v') entry_vol(&d, *(const int *)v);
    else entry(&d, k, "y", DBUS_TYPE_BYTE, v);
    dbus_message_iter_close_container(&it, &d);
    dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "s", &inv);
    dbus_message_iter_close_container(&it, &inv);
    dbus_connection_send(c, s, 0), dbus_message_unref(s);
}

static void link_up(struct dev *d, const char *why) {
    d->connected = 1;
    changed(bz, d->path, "org.bluez.Device1", "Connected", 'b', &d->connected);
    if (d->battery >= 0) {
        unsigned char pct = (unsigned char)d->battery;
        DBusMessage *s = dbus_message_new_signal("/", "org.freedesktop.DBus.ObjectManager", "InterfacesAdded");
        DBusMessageIter it, a, e, p;
        const char *path = d->path, *name = "org.bluez.Battery1";
        dbus_message_iter_init_append(s, &it);
        dbus_message_iter_append_basic(&it, DBUS_TYPE_OBJECT_PATH, &path);
        dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{sa{sv}}", &a);
        dbus_message_iter_open_container(&a, DBUS_TYPE_DICT_ENTRY, 0, &e);
        dbus_message_iter_append_basic(&e, DBUS_TYPE_STRING, &name);
        dbus_message_iter_open_container(&e, DBUS_TYPE_ARRAY, "{sv}", &p);
        entry(&p, "Percentage", "y", DBUS_TYPE_BYTE, &pct);
        dbus_message_iter_close_container(&e, &p);
        dbus_message_iter_close_container(&a, &e);
        dbus_message_iter_close_container(&it, &a);
        dbus_connection_send(bz, s, 0), dbus_message_unref(s);
    }
    snprintf(d->codec, sizeof d->codec, "%.*s", (int)strcspn(d->codecs, "+"), d->codecs);
    added(ba, d->pcm, 'p', d);
    out("connected %s (%s)", d->addr, why);
}
static void link_down(struct dev *d, const char *why) {
    if (!d->connected) return;
    removed(ba, d->pcm, "org.bluealsa.PCM1");
    d->connected = 0;
    changed(bz, d->path, "org.bluez.Device1", "Connected", 'b', &d->connected);
    out("disconnected %s (%s)", d->addr, why);
}

/* the agent asked, waiting for its answer (a stand-in may block); 1 for a yes */
static int ask_agent(struct dev *d, const char *member) {
    if (!agent_owner[0]) return out("agent %s %s: no agent", member, d->addr), 0;
    DBusMessage *m = dbus_message_new_method_call(agent_owner, agent_path, "org.bluez.Agent1", member), *r;
    const char *p = d->path;
    unsigned passkey = 123456;
    if (!strcmp(member, "RequestConfirmation")) dbus_message_append_args(m, DBUS_TYPE_OBJECT_PATH, &p, DBUS_TYPE_UINT32, &passkey, DBUS_TYPE_INVALID);
    else dbus_message_append_args(m, DBUS_TYPE_OBJECT_PATH, &p, DBUS_TYPE_INVALID);
    DBusError err;
    dbus_error_init(&err);
    r = dbus_connection_send_with_reply_and_block(bz, m, 10000, &err);
    dbus_message_unref(m);
    out("agent %s %s: %s", member, d->addr, r ? "yes" : err.name);
    if (r) dbus_message_unref(r);
    dbus_error_free(&err);
    return r != 0;
}

/* ---- calls ---- */

/* GetAll of an interface; Get is not served (q2btd reads everything from GetManagedObjects and the signals) */
static DBusMessage *props_get(DBusMessage *m, const char *iface, const char *prop, int all, const struct dev *d) {
    if (!all) return dbus_message_new_error(m, "org.freedesktop.DBus.Error.NotSupported", prop);
    DBusMessage *r = dbus_message_new_method_return(m);
    DBusMessageIter it, dict;
    dbus_message_iter_init_append(r, &it);
    dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{sv}", &dict);
    if (!strcmp(iface, "org.bluez.Adapter1")) adapter_props(&dict);
    else if (!strcmp(iface, "org.bluez.Device1") && d) dev_props(&dict, d);
    else if (!strcmp(iface, "org.bluealsa.PCM1") && d) pcm_props(&dict, d);
    dbus_message_iter_close_container(&it, &dict);
    return r;
}
static int set_value(DBusMessage *m, const char **iface, const char **prop, long long *num, int *vol) {
    DBusMessageIter it, v, a;
    if (!dbus_message_iter_init(m, &it)) return 0;
    dbus_message_iter_get_basic(&it, iface), dbus_message_iter_next(&it);
    dbus_message_iter_get_basic(&it, prop), dbus_message_iter_next(&it);
    dbus_message_iter_recurse(&it, &v);
    if (dbus_message_iter_get_arg_type(&v) == DBUS_TYPE_BOOLEAN) {
        dbus_bool_t b;
        dbus_message_iter_get_basic(&v, &b);
        *num = b;
    } else if (dbus_message_iter_get_arg_type(&v) == DBUS_TYPE_ARRAY) {
        unsigned char *bytes;
        int n;
        dbus_message_iter_recurse(&v, &a);
        dbus_message_iter_get_fixed_array(&a, &bytes, &n);
        *vol = n ? bytes[0] : -1;
    }
    return 1;
}

static DBusHandlerResult on_bz(DBusConnection *c, DBusMessage *m, void *data) {
    (void)data;
    if (dbus_message_get_type(m) == DBUS_MESSAGE_TYPE_SIGNAL) {
        if (dbus_message_is_signal(m, "org.freedesktop.DBus.Properties", "PropertiesChanged") && player_owner[0] &&
            !strcmp(dbus_message_get_sender(m), player_owner)) { /* the player's track change, as BlueZ's AVRCP takes it */
            DBusMessageIter it, d, e, v, md, me, mv;
            char line[400] = "";
            dbus_message_iter_init(m, &it);
            dbus_message_iter_next(&it);
            for (dbus_message_iter_recurse(&it, &d); dbus_message_iter_get_arg_type(&d) == DBUS_TYPE_DICT_ENTRY; dbus_message_iter_next(&d)) {
                const char *k, *s;
                dbus_message_iter_recurse(&d, &e);
                dbus_message_iter_get_basic(&e, &k);
                dbus_message_iter_next(&e);
                dbus_message_iter_recurse(&e, &v);
                if (!strcmp(k, "PlaybackStatus")) dbus_message_iter_get_basic(&v, &s), snprintf(line + strlen(line), sizeof line - strlen(line), " status=%s", s);
                if (!strcmp(k, "Metadata"))
                    for (dbus_message_iter_recurse(&v, &md); dbus_message_iter_get_arg_type(&md) == DBUS_TYPE_DICT_ENTRY; dbus_message_iter_next(&md)) {
                        dbus_message_iter_recurse(&md, &me);
                        dbus_message_iter_get_basic(&me, &k);
                        dbus_message_iter_next(&me);
                        dbus_message_iter_recurse(&me, &mv);
                        if (!strcmp(k, "xesam:title")) dbus_message_iter_get_basic(&mv, &s), snprintf(line + strlen(line), sizeof line - strlen(line), " title=%s", s);
                    }
            }
            out("player-changed%s", line);
        }
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    }
    if (dbus_message_get_type(m) != DBUS_MESSAGE_TYPE_METHOD_CALL) return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    const char *p = dbus_message_get_path(m), *iface = dbus_message_get_interface(m), *member = dbus_message_get_member(m);
    struct dev *d = by(p);
    DBusMessage *r = 0;
    if (!iface) iface = "";
    if (!strcmp(member, "GetManagedObjects") && !strcmp(p, "/")) {
        DBusMessageIter it, objs;
        r = dbus_message_new_method_return(m);
        dbus_message_iter_init_append(r, &it);
        dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{oa{sa{sv}}}", &objs);
        object(&objs, "/org/bluez/hci0", 'a', 0);
        for (int i = 0; i < DEVS; ++i)
            if (devs[i].used && devs[i].shown) object(&objs, devs[i].path, 'd', &devs[i]);
        dbus_message_iter_close_container(&it, &objs);
    } else if (!strcmp(iface, "org.bluez.AgentManager1")) {
        const char *o = "", *io = "";
        dbus_message_get_args(m, 0, DBUS_TYPE_OBJECT_PATH, &o, DBUS_TYPE_STRING, &io, DBUS_TYPE_INVALID);
        if (!strcmp(member, "RegisterAgent")) {
            snprintf(agent_owner, sizeof agent_owner, "%s", dbus_message_get_sender(m));
            snprintf(agent_path, sizeof agent_path, "%s", o);
            out("agent registered %s %s", o, io);
        } else out("agent %s %s", member, o);
        r = dbus_message_new_method_return(m);
    } else if (!strcmp(iface, "org.bluez.Media1") && !strcmp(member, "RegisterPlayer")) {
        DBusMessageIter it;
        dbus_message_iter_init(m, &it);
        const char *o;
        dbus_message_iter_get_basic(&it, &o);
        snprintf(player_owner, sizeof player_owner, "%s", dbus_message_get_sender(m));
        snprintf(player_path, sizeof player_path, "%s", o);
        out("player registered %s", o);
        r = dbus_message_new_method_return(m);
    } else if (!strcmp(iface, "org.bluez.Adapter1")) {
        if (!strcmp(member, "StartDiscovery") || !strcmp(member, "StopDiscovery")) {
            discovering = member[2] == 'a';
            changed(bz, p, iface, "Discovering", 'b', &discovering);
            out("%s", member);
            if (discovering)
                for (int i = 0; i < DEVS; ++i)
                    if (devs[i].used && devs[i].found && !devs[i].shown) devs[i].shown = 1, added(bz, devs[i].path, 'd', &devs[i]);
            r = dbus_message_new_method_return(m);
        } else if (!strcmp(member, "RemoveDevice")) {
            const char *o = "";
            dbus_message_get_args(m, 0, DBUS_TYPE_OBJECT_PATH, &o, DBUS_TYPE_INVALID);
            struct dev *x = by(o);
            if (!x) r = dbus_message_new_error(m, "org.bluez.Error.DoesNotExist", o);
            else {
                link_down(x, "removed");
                x->shown = x->paired = x->trusted = 0, x->found = 1;
                removed(bz, x->path, "org.bluez.Device1");
                out("RemoveDevice %s", x->addr);
                r = dbus_message_new_method_return(m);
            }
        }
    } else if (!strcmp(iface, "org.bluez.Device1") && d) {
        if (!strcmp(member, "Connect")) {
            out("Connect %s", d->addr);
            if (!d->reachable || !powered) r = dbus_message_new_error(m, "org.bluez.Error.Failed", "Page Timeout");
            else {
                if (!d->connected) link_up(d, "asked");
                r = dbus_message_new_method_return(m);
            }
        } else if (!strcmp(member, "Disconnect")) {
            out("Disconnect %s", d->addr);
            link_down(d, "asked");
            r = dbus_message_new_method_return(m);
        } else if (!strcmp(member, "Pair")) {
            out("Pair %s", d->addr);
            if (d->paired) r = dbus_message_new_error(m, "org.bluez.Error.AlreadyExists", "Already Exists");
            else if (!ask_agent(d, "RequestConfirmation")) r = dbus_message_new_error(m, "org.bluez.Error.AuthenticationRejected", "rejected");
            else {
                d->paired = 1;
                changed(bz, d->path, iface, "Paired", 'b', &d->paired);
                r = dbus_message_new_method_return(m);
            }
        }
    } else if (!strcmp(iface, "org.freedesktop.DBus.Properties")) {
        const char *pi = "", *pp = "";
        if (!strcmp(member, "Set")) {
            long long num = 0;
            int vol = -1;
            set_value(m, &pi, &pp, &num, &vol);
            out("set %s %s %lld", d ? d->addr : p, pp, num);
            if (!strcmp(pp, "Powered")) powered = (int)num, changed(bz, p, pi, pp, 'b', &powered);
            else if (!strcmp(pp, "Discoverable")) discoverable = (int)num, changed(bz, p, pi, pp, 'b', &discoverable);
            else if (!strcmp(pp, "Trusted") && d) d->trusted = (int)num, changed(bz, p, pi, pp, 'b', &d->trusted);
            r = dbus_message_new_method_return(m);
        } else {
            dbus_message_get_args(m, 0, DBUS_TYPE_STRING, &pi, DBUS_TYPE_STRING, &pp, DBUS_TYPE_INVALID);
            r = props_get(m, pi, pp, !strcmp(member, "GetAll"), d);
        }
    }
    if (!r) r = dbus_message_new_error(m, "org.freedesktop.DBus.Error.UnknownMethod", member);
    dbus_connection_send(c, r, 0), dbus_message_unref(r);
    return DBUS_HANDLER_RESULT_HANDLED;
}

static DBusHandlerResult on_ba(DBusConnection *c, DBusMessage *m, void *data) {
    (void)data;
    if (dbus_message_get_type(m) != DBUS_MESSAGE_TYPE_METHOD_CALL) return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
    const char *p = dbus_message_get_path(m), *iface = dbus_message_get_interface(m), *member = dbus_message_get_member(m);
    struct dev *d = by_pcm(p);
    DBusMessage *r = 0;
    if (!iface) iface = "";
    if (!strcmp(member, "GetManagedObjects") && !strcmp(p, "/org/bluealsa")) {
        DBusMessageIter it, objs;
        r = dbus_message_new_method_return(m);
        dbus_message_iter_init_append(r, &it);
        dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{oa{sa{sv}}}", &objs);
        for (int i = 0; i < DEVS; ++i)
            if (devs[i].used && devs[i].connected) object(&objs, devs[i].pcm, 'p', &devs[i]);
        dbus_message_iter_close_container(&it, &objs);
    } else if (!strcmp(iface, "org.bluealsa.PCM1") && d && !strcmp(member, "GetCodecs")) {
        DBusMessageIter it, a, e, props;
        r = dbus_message_new_method_return(m);
        dbus_message_iter_init_append(r, &it);
        dbus_message_iter_open_container(&it, DBUS_TYPE_ARRAY, "{sa{sv}}", &a);
        char list[96];
        snprintf(list, sizeof list, "%s", d->codecs);
        for (char *t = strtok(list, "+"); t; t = strtok(0, "+")) {
            dbus_message_iter_open_container(&a, DBUS_TYPE_DICT_ENTRY, 0, &e);
            dbus_message_iter_append_basic(&e, DBUS_TYPE_STRING, &t);
            dbus_message_iter_open_container(&e, DBUS_TYPE_ARRAY, "{sv}", &props);
            dbus_message_iter_close_container(&e, &props);
            dbus_message_iter_close_container(&a, &e);
        }
        dbus_message_iter_close_container(&it, &a);
    } else if (!strcmp(iface, "org.bluealsa.PCM1") && d && !strcmp(member, "SelectCodec")) {
        const char *codec = "";
        DBusMessageIter it;
        dbus_message_iter_init(m, &it);
        dbus_message_iter_get_basic(&it, &codec);
        out("SelectCodec %s %s", d->addr, codec);
        char want[32];
        snprintf(want, sizeof want, "+%s+", codec);
        char have[100];
        snprintf(have, sizeof have, "+%s+", d->codecs);
        if (!strstr(have, want)) r = dbus_message_new_error(m, "org.bluealsa.Error.NotSupported", codec);
        else {
            snprintf(d->codec, sizeof d->codec, "%s", codec);
            changed(ba, d->pcm, "org.bluealsa.PCM1", "Codec", 's', d->codec);
            r = dbus_message_new_method_return(m);
        }
    } else if (!strcmp(iface, "org.freedesktop.DBus.Properties") && d && !strcmp(member, "Set")) {
        const char *pi = "", *pp = "";
        long long num = 0;
        int vol = -1;
        set_value(m, &pi, &pp, &num, &vol);
        out("set %s %s %d", d->addr, pp, vol);
        if (!strcmp(pp, "Volume") && vol >= 0) d->vol = vol & 0x7f, changed(ba, d->pcm, pi, pp, 'v', &d->vol);
        r = dbus_message_new_method_return(m);
    } else if (!strcmp(iface, "org.freedesktop.DBus.Properties") && d) {
        const char *pi = "", *pp = "";
        dbus_message_get_args(m, 0, DBUS_TYPE_STRING, &pi, DBUS_TYPE_STRING, &pp, DBUS_TYPE_INVALID);
        r = props_get(m, pi, pp, !strcmp(member, "GetAll"), d);
    }
    if (!r) r = dbus_message_new_error(m, "org.freedesktop.DBus.Error.UnknownMethod", member);
    dbus_connection_send(c, r, 0), dbus_message_unref(r);
    return DBUS_HANDLER_RESULT_HANDLED;
}

static void control(char *line) {
    char cmd[32] = "", a1[64] = "", a2[64] = "";
    sscanf(line, "%31s %63s %63s", cmd, a1, a2);
    struct dev *d = by(a1);
    if (!strcmp(cmd, "drop") && d) link_down(d, "link lost");
    else if (!strcmp(cmd, "come") && d) link_up(d, "by the headset");
    else if (!strcmp(cmd, "reach") && d) d->reachable = atoi(a2), out("reach %s %d", d->addr, d->reachable);
    else if (!strcmp(cmd, "hvol") && d) d->vol = atoi(a2), changed(ba, d->pcm, "org.bluealsa.PCM1", "Volume", 'v', &d->vol), out("hvol %s %d", d->addr, d->vol);
    else if (!strcmp(cmd, "battery") && d) {
        unsigned char pct = (unsigned char)(d->battery = atoi(a2));
        changed(bz, d->path, "org.bluez.Battery1", "Percentage", 'y', &pct);
        out("battery %s %d", d->addr, d->battery);
    } else if (!strcmp(cmd, "button") && player_owner[0]) {
        DBusMessage *m = dbus_message_new_method_call(player_owner, player_path, "org.mpris.MediaPlayer2.Player", a1), *r;
        DBusError err;
        dbus_error_init(&err);
        r = dbus_connection_send_with_reply_and_block(bz, m, 5000, &err);
        out("button %s: %s", a1, r ? "ok" : err.name);
        if (r) dbus_message_unref(r);
        dbus_message_unref(m), dbus_error_free(&err);
    } else if (!strcmp(cmd, "pairin") && d) {
        if (!d->shown) d->shown = 1, added(bz, d->path, 'd', d);
        if (ask_agent(d, "RequestConfirmation")) d->paired = 1, changed(bz, d->path, "org.bluez.Device1", "Paired", 'b', &d->paired);
    } else if (!strcmp(cmd, "quit")) exit(0);
    else out("? %s", line);
}

static DBusConnection *own(const char *name) {
    DBusError err;
    dbus_error_init(&err);
    DBusConnection *c = dbus_bus_get_private(DBUS_BUS_SYSTEM, &err);
    if (!c) fprintf(stderr, "btd_standin: %s\n", err.message), exit(1);
    dbus_connection_set_exit_on_disconnect(c, FALSE);
    if (dbus_bus_request_name(c, name, DBUS_NAME_FLAG_DO_NOT_QUEUE, &err) != DBUS_REQUEST_NAME_REPLY_PRIMARY_OWNER)
        fprintf(stderr, "btd_standin: %s not ours\n", name), exit(1);
    return c;
}

int main(int argc, char **argv) {
    for (int i = 1; i + 1 < argc; i += 2) add_dev(argv[i + 1], !strcmp(argv[i], "--found"));
    bz = own("org.bluez"), ba = own("org.bluealsa");
    dbus_connection_add_filter(bz, on_bz, 0, 0);
    dbus_connection_add_filter(ba, on_ba, 0, 0);
    dbus_bus_add_match(bz, "type='signal',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged',path='/vamp/q2btd/player'", 0);
    out("ready");
    char in[512];
    size_t n = 0;
    int fz, fa;
    dbus_connection_get_unix_fd(bz, &fz), dbus_connection_get_unix_fd(ba, &fa);
    for (;;) {
        struct pollfd p[3] = {{.fd = 0, .events = POLLIN}, {.fd = fz, .events = POLLIN}, {.fd = fa, .events = POLLIN}};
        dbus_connection_flush(bz), dbus_connection_flush(ba);
        poll(p, 3, 200);
        for (int k = 0; k < 2; ++k) {
            DBusConnection *c = k ? ba : bz;
            dbus_connection_read_write(c, 0);
            while (dbus_connection_dispatch(c) == DBUS_DISPATCH_DATA_REMAINS) {}
        }
        if (p[0].revents) {
            ssize_t got = read(0, in + n, sizeof in - 1 - n);
            if (got <= 0) return 0;
            n += (size_t)got;
            char *nl;
            while ((nl = memchr(in, '\n', n))) {
                *nl = 0;
                control(in);
                n -= (size_t)(nl + 1 - in);
                memmove(in, nl + 1, n);
            }
        }
    }
}
