/* coda-rc's Bluetooth LE transport (docs/remote-protocol.md#bluetooth-le-primary): a GATT application and an
 * advertisement registered with bluetoothd over D-Bus (org.bluez.GattManager1, LEAdvertisingManager1), so the
 * Q2 is a peripheral a phone connects to. The frame stream rides the control characteristic's writes in and the
 * event characteristic's notifications out, cut at the link's MTU and joined again by the frames' lengths.
 *
 * bluetoothd is stock's BlueZ 5.66, started by demo when Bluetooth is on. While it is off the bus (Bluetooth off)
 * nothing is tried: its NameOwnerChanged, when it comes, starts the registration 2 s later. A refusal while it is
 * on is tried again every 30 s. Each failure is logged once, not at every try, and TCP goes on meanwhile.
 * /tmp/coda-rc.ble exists while both registrations stand, for the Pair a Phone page (crc_ble_advertising). Proven
 * only on a real Q2: the emulator has no Bluetooth (docs/remote-protocol.md#testing-on-a-q2). */
#include "coda_rc_sys.h"
#include "coda_rc.h"

/* ---- the libdbus 1.12 API used here (the rootfs's libdbus-1.so.3; no headers on this side either) ---- */
typedef struct DBusConnection DBusConnection;
typedef struct DBusMessage DBusMessage;
typedef struct DBusPendingCall DBusPendingCall;
typedef struct { const char *name, *message; unsigned dummy; void *pad; } DBusError;
typedef struct { void *p[4]; int i[16]; void *q[4]; } DBusMessageIter; /* larger than libdbus's; only its size is ours */
typedef int (*dbus_msg_fn)(DBusConnection *, DBusMessage *, void *);
typedef struct { void (*unregister)(DBusConnection *, void *); dbus_msg_fn message; void (*pad[4])(void *); } DBusObjectPathVTable;
void dbus_error_init(DBusError *);
void dbus_error_free(DBusError *);
DBusConnection *dbus_bus_get(int, DBusError *);
void dbus_bus_add_match(DBusConnection *, const char *, DBusError *);
void dbus_connection_set_exit_on_disconnect(DBusConnection *, int);
int dbus_connection_get_unix_fd(DBusConnection *, int *);
int dbus_connection_register_object_path(DBusConnection *, const char *, const DBusObjectPathVTable *, void *);
int dbus_connection_add_filter(DBusConnection *, dbus_msg_fn, void *, void (*)(void *));
int dbus_connection_read_write(DBusConnection *, int);
int dbus_connection_dispatch(DBusConnection *);
int dbus_connection_get_dispatch_status(DBusConnection *);
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
const char *dbus_message_get_error_name(DBusMessage *);
int dbus_message_is_method_call(DBusMessage *, const char *, const char *);
int dbus_message_is_signal(DBusMessage *, const char *, const char *);
const char *dbus_message_get_path(DBusMessage *);
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
#define DBUS_TYPE_ERROR_MSG 3
#define DBUS_HANDLED 0
#define DBUS_NOT_YET_HANDLED 1
#define DBUS_DATA_REMAINS 0
#define T_ARRAY 'a'
#define T_BYTE 'y'
#define T_BOOL 'b'
#define T_STR 's'
#define T_PATH 'o'
#define T_VARIANT 'v'
#define T_DICT 'e'
#define T_U16 'q'

#define SERVICE_UUID "b67cefa7-194f-4988-8820-4974e36704c3"
#define CONTROL_UUID "1666f4ad-18d6-4692-b723-b5ae83ce5ef3"
#define EVENT_UUID "5645283b-18bb-419e-b338-f089cfc8dced"
#define INFO_UUID "c7f73d6b-93b1-46b5-8892-1ba59bdb7513"
#define ADAPTER "/org/bluez/hci0"
#define APP "/coda"
#define SVC APP "/service0"
#define CH_CONTROL SVC "/char0"
#define CH_EVENT SVC "/char1"
#define CH_INFO SVC "/char2"
#define ADV APP "/adv0"
#define RETRY_MS 30000
#define INFO_VALUE "coda-rc 1"

static struct {
    DBusConnection *bus;
    int fd, registered, notifying, mtu;
    int absent;  /* bluetoothd is not on the bus: wait for its NameOwnerChanged, try nothing */
    DBusPendingCall *app_call, *adv_call;
    uint32_t failed_at;
    char logged[96];  /* the last failure logged, so a failure that repeats is logged once */
    char device[96];  /* the connected phone's object path, from its writes */
} b;

/* ---- building replies ---- */

/* what (a failure) logged unless it is the one logged last; 0 forgets it, so the next is logged */
static void log_once(const char *what) {
    if (!what) {
        b.logged[0] = 0;
        return;
    }
    if (!strcmp(what, b.logged)) return;
    snprintf(b.logged, sizeof b.logged, "%s", what);
    crc_log("ble: %s", what);
}

/* registered is both registrations' bits; the page's file follows whether both stand */
static void set_registered(int bits) {
    int was = b.registered == 3;
    b.registered = bits;
    if (was != (bits == 3)) crc_ble_advertising(bits == 3);
}

static void add_str(DBusMessageIter *it, int type, const char *s) { dbus_message_iter_append_basic(it, type, &s); }

static void variant_begin(DBusMessageIter *dict, DBusMessageIter *entry, DBusMessageIter *var, const char *key, const char *sig) {
    dbus_message_iter_open_container(dict, T_DICT, 0, entry);
    add_str(entry, T_STR, key);
    dbus_message_iter_open_container(entry, T_VARIANT, sig, var);
}

static void variant_end(DBusMessageIter *dict, DBusMessageIter *entry, DBusMessageIter *var) {
    dbus_message_iter_close_container(entry, var);
    dbus_message_iter_close_container(dict, entry);
}

static void prop_str(DBusMessageIter *dict, const char *key, int type, const char *v) {
    DBusMessageIter e, var;
    char sig[2] = {(char)type, 0};
    variant_begin(dict, &e, &var, key, sig);
    add_str(&var, type, v);
    variant_end(dict, &e, &var);
}

static void prop_bool(DBusMessageIter *dict, const char *key, int v) {
    DBusMessageIter e, var;
    uint32_t x = v != 0;
    variant_begin(dict, &e, &var, key, "b");
    dbus_message_iter_append_basic(&var, T_BOOL, &x);
    variant_end(dict, &e, &var);
}

static void prop_strs(DBusMessageIter *dict, const char *key, const char *const *v) {
    DBusMessageIter e, var, arr;
    variant_begin(dict, &e, &var, key, "as");
    dbus_message_iter_open_container(&var, T_ARRAY, "s", &arr);
    for (; *v; ++v) add_str(&arr, T_STR, *v);
    dbus_message_iter_close_container(&var, &arr);
    variant_end(dict, &e, &var);
}

static void prop_bytes(DBusMessageIter *dict, const char *key, const uint8_t *p, int n) {
    DBusMessageIter e, var, arr;
    variant_begin(dict, &e, &var, key, "ay");
    dbus_message_iter_open_container(&var, T_ARRAY, "y", &arr);
    dbus_message_iter_append_fixed_array(&arr, T_BYTE, &p, n);
    dbus_message_iter_close_container(&var, &arr);
    variant_end(dict, &e, &var);
}

/* ---- the objects: one service, three characteristics, one advertisement ---- */

static const char *const F_CONTROL[] = {"write", "write-without-response", "encrypt-write", 0};
static const char *const F_EVENT[] = {"notify", 0};
static const char *const F_INFO[] = {"read", 0};
static const struct { const char *path, *uuid; const char *const *flags; } chars[] = {
    {CH_CONTROL, CONTROL_UUID, F_CONTROL}, {CH_EVENT, EVENT_UUID, F_EVENT}, {CH_INFO, INFO_UUID, F_INFO}};

/* an object's interface and its properties, as a{sv} into dict; 0 when the path has no such interface */
static int props(const char *path, const char *iface, DBusMessageIter *dict) {
    if (!strcmp(path, SVC) && !strcmp(iface, "org.bluez.GattService1")) {
        prop_str(dict, "UUID", T_STR, SERVICE_UUID);
        prop_bool(dict, "Primary", 1);
        return 1;
    }
    for (unsigned i = 0; i < 3; ++i)
        if (!strcmp(path, chars[i].path) && !strcmp(iface, "org.bluez.GattCharacteristic1")) {
            prop_str(dict, "UUID", T_STR, chars[i].uuid);
            prop_str(dict, "Service", T_PATH, SVC);
            prop_strs(dict, "Flags", chars[i].flags);
            if (i == 1) prop_bool(dict, "Notifying", b.notifying);
            return 1;
        }
    if (!strcmp(path, ADV) && !strcmp(iface, "org.bluez.LEAdvertisement1")) {
        static const char *const uuids[] = {SERVICE_UUID, 0};
        prop_str(dict, "Type", T_STR, "peripheral");
        prop_strs(dict, "ServiceUUIDs", uuids);
        /* the Q2's default Bluetooth name (bt_name.c BT_NAME_DEFAULT); fixed, not the user's: with the
         * 128-bit service UUID (18 bytes) and the flags (3) 10 of the 31 are left, and "Q2 Muse" takes
         * 9. The remote app finds the Q2 by the service UUID, not this name. */
        prop_str(dict, "LocalName", T_STR, "Q2 Muse");
        return 1;
    }
    return 0;
}

static const char *iface_of(const char *path) {
    if (!strcmp(path, SVC)) return "org.bluez.GattService1";
    if (!strcmp(path, ADV)) return "org.bluez.LEAdvertisement1";
    return "org.bluez.GattCharacteristic1";
}

static void reply(DBusMessage *m) {
    if (!m) return;
    dbus_connection_send(b.bus, m, 0);
    dbus_message_unref(m);
}

static void managed_objects(DBusMessage *call) {
    DBusMessage *r = dbus_message_new_method_return(call);
    DBusMessageIter it, objs, obj, ifs, ife, dict;
    static const char *const paths[] = {SVC, CH_CONTROL, CH_EVENT, CH_INFO};
    dbus_message_iter_init_append(r, &it);
    dbus_message_iter_open_container(&it, T_ARRAY, "{oa{sa{sv}}}", &objs);
    for (unsigned i = 0; i < 4; ++i) {
        dbus_message_iter_open_container(&objs, T_DICT, 0, &obj);
        add_str(&obj, T_PATH, paths[i]);
        dbus_message_iter_open_container(&obj, T_ARRAY, "{sa{sv}}", &ifs);
        dbus_message_iter_open_container(&ifs, T_DICT, 0, &ife);
        add_str(&ife, T_STR, iface_of(paths[i]));
        dbus_message_iter_open_container(&ife, T_ARRAY, "{sv}", &dict);
        props(paths[i], iface_of(paths[i]), &dict);
        dbus_message_iter_close_container(&ife, &dict);
        dbus_message_iter_close_container(&ifs, &ife);
        dbus_message_iter_close_container(&obj, &ifs);
        dbus_message_iter_close_container(&objs, &obj);
    }
    dbus_message_iter_close_container(&it, &objs);
    reply(r);
}

static void get_all(DBusMessage *call, const char *path) {
    DBusMessageIter it, dict;
    const char *iface = "";
    if (dbus_message_iter_init(call, &it) && dbus_message_iter_get_arg_type(&it) == T_STR)
        dbus_message_iter_get_basic(&it, &iface);
    DBusMessage *r = dbus_message_new_method_return(call);
    dbus_message_iter_init_append(r, &it);
    dbus_message_iter_open_container(&it, T_ARRAY, "{sv}", &dict);
    props(path, iface, &dict);
    dbus_message_iter_close_container(&it, &dict);
    reply(r);
}

/* WriteValue(ay value, a{sv} options): the bytes, and the writer's device and MTU */
static void write_value(DBusMessage *call) {
    DBusMessageIter it, arr, opts, e, v;
    const uint8_t *data = 0;
    int n = 0;
    const char *device = 0;
    uint16_t mtu = 0;
    if (dbus_message_iter_init(call, &it) && dbus_message_iter_get_arg_type(&it) == T_ARRAY) {
        dbus_message_iter_recurse(&it, &arr);
        dbus_message_iter_get_fixed_array(&arr, &data, &n);
        if (dbus_message_iter_next(&it) && dbus_message_iter_get_arg_type(&it) == T_ARRAY) {
            for (dbus_message_iter_recurse(&it, &opts); dbus_message_iter_get_arg_type(&opts) == T_DICT;
                 dbus_message_iter_next(&opts)) {
                const char *key;
                dbus_message_iter_recurse(&opts, &e);
                dbus_message_iter_get_basic(&e, &key);
                dbus_message_iter_next(&e);
                dbus_message_iter_recurse(&e, &v);
                if (!strcmp(key, "device") && dbus_message_iter_get_arg_type(&v) == T_PATH) dbus_message_iter_get_basic(&v, &device);
                if (!strcmp(key, "mtu") && dbus_message_iter_get_arg_type(&v) == T_U16) dbus_message_iter_get_basic(&v, &mtu);
            }
        }
    }
    reply(dbus_message_new_method_return(call));
    if (mtu >= 23) b.mtu = mtu;
    if (device && strcmp(device, b.device)) {  /* a new phone: a new session */
        snprintf(b.device, sizeof b.device, "%s", device);
        crc_ble_connected();
    }
    if (n > 0) crc_ble_bytes(data, n);
}

static void read_value(DBusMessage *call) {
    DBusMessage *r = dbus_message_new_method_return(call);
    DBusMessageIter it, arr;
    const uint8_t *p = (const uint8_t *)INFO_VALUE;
    dbus_message_iter_init_append(r, &it);
    dbus_message_iter_open_container(&it, T_ARRAY, "y", &arr);
    dbus_message_iter_append_fixed_array(&arr, T_BYTE, &p, (int)sizeof INFO_VALUE - 1);
    dbus_message_iter_close_container(&it, &arr);
    reply(r);
}

static int on_message(DBusConnection *c, DBusMessage *m, void *data) {
    const char *path = dbus_message_get_path(m);
    if (!path) return DBUS_NOT_YET_HANDLED;
    if (!strcmp(path, APP) && dbus_message_is_method_call(m, "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")) {
        managed_objects(m);
    } else if (dbus_message_is_method_call(m, "org.freedesktop.DBus.Properties", "GetAll")) {
        get_all(m, path);
    } else if (!strcmp(path, CH_CONTROL) && dbus_message_is_method_call(m, "org.bluez.GattCharacteristic1", "WriteValue")) {
        write_value(m);
    } else if (!strcmp(path, CH_INFO) && dbus_message_is_method_call(m, "org.bluez.GattCharacteristic1", "ReadValue")) {
        read_value(m);
    } else if (!strcmp(path, CH_EVENT) && dbus_message_is_method_call(m, "org.bluez.GattCharacteristic1", "StartNotify")) {
        b.notifying = 1;
        reply(dbus_message_new_method_return(m));
    } else if (!strcmp(path, CH_EVENT) && dbus_message_is_method_call(m, "org.bluez.GattCharacteristic1", "StopNotify")) {
        /* the last subscriber gone: its link is down */
        b.notifying = 0;
        b.device[0] = 0;
        crc_ble_gone();
        reply(dbus_message_new_method_return(m));
    } else if (!strcmp(path, ADV) && dbus_message_is_method_call(m, "org.bluez.LEAdvertisement1", "Release")) {
        reply(dbus_message_new_method_return(m));
    } else {
        reply(dbus_message_new_error(m, "org.bluez.Error.NotSupported", "not supported"));
    }
    return DBUS_HANDLED;
}

/* bluetoothd leaving the bus, or coming back: either way the registration is to be made again */
static int on_signal(DBusConnection *c, DBusMessage *m, void *data) {
    if (dbus_message_is_signal(m, "org.freedesktop.DBus", "NameOwnerChanged")) {
        DBusMessageIter it;
        const char *name = "", *owner = "";
        if (dbus_message_iter_init(m, &it) && dbus_message_iter_get_arg_type(&it) == T_STR) {
            dbus_message_iter_get_basic(&it, &name);
            /* name, old owner, new owner: an empty new owner is bluetoothd gone */
            if (dbus_message_iter_next(&it) && dbus_message_iter_next(&it) && dbus_message_iter_get_arg_type(&it) == T_STR)
                dbus_message_iter_get_basic(&it, &owner);
        }
        if (!strcmp(name, "org.bluez")) {
            set_registered(0);
            b.notifying = 0;
            b.device[0] = 0;
            crc_ble_gone();
            b.absent = !*owner;
            log_once(0);
            crc_log("ble: bluetoothd %s", b.absent ? "left the bus; waiting for it" : "started; registering in 2 s");
            if (b.absent) snprintf(b.logged, sizeof b.logged, "bluetoothd is not running");
            b.failed_at = crc_now_ms() - RETRY_MS + 2000;  /* in 2 s, once it has its objects up */
        }
    }
    return DBUS_NOT_YET_HANDLED;
}

static DBusPendingCall *call_register(const char *iface, const char *method, const char *object) {
    DBusMessage *m = dbus_message_new_method_call("org.bluez", ADAPTER, iface, method);
    DBusMessageIter it, dict;
    DBusPendingCall *pc = 0;
    if (!m) return 0;
    dbus_message_iter_init_append(m, &it);
    add_str(&it, T_PATH, object);
    dbus_message_iter_open_container(&it, T_ARRAY, "{sv}", &dict);
    dbus_message_iter_close_container(&it, &dict);
    /* not a blocking call: bluetoothd asks this side for GetManagedObjects before it answers */
    dbus_connection_send_with_reply(b.bus, m, &pc, 10000);
    dbus_message_unref(m);
    return pc;
}

/* the registrations' answers, once they come */
static void pending(void) {
    DBusPendingCall **calls[] = {&b.app_call, &b.adv_call};
    for (int i = 0; i < 2; ++i) {
        DBusPendingCall *pc = *calls[i];
        if (!pc || !dbus_pending_call_get_completed(pc)) continue;
        DBusMessage *r = dbus_pending_call_steal_reply(pc);
        dbus_pending_call_unref(pc);
        *calls[i] = 0;
        if (r && dbus_message_get_type(r) == DBUS_TYPE_ERROR_MSG) {
            const char *e = dbus_message_get_error_name(r);
            char what[96];
            e = e ? e : "?";
            /* nobody owns org.bluez: Bluetooth is off. Its NameOwnerChanged starts the next try (on_signal). */
            if (!strcmp(e, "org.freedesktop.DBus.Error.ServiceUnknown") || !strcmp(e, "org.freedesktop.DBus.Error.NameHasNoOwner")) {
                b.absent = 1;
                snprintf(what, sizeof what, "bluetoothd is not running");
            } else {
                snprintf(what, sizeof what, "registration refused: %s", e);
            }
            log_once(what);
            b.failed_at = crc_now_ms();
            set_registered(0);
        } else if (r) {
            set_registered(b.registered | 1 << i);
            if (b.registered == 3) {
                log_once(0);
                crc_log("ble: registered");
            }
        }
        if (r) dbus_message_unref(r);
    }
}

static int start_registration(void) {
    set_registered(0);
    b.app_call = call_register("org.bluez.GattManager1", "RegisterApplication", APP);
    b.adv_call = call_register("org.bluez.LEAdvertisingManager1", "RegisterAdvertisement", ADV);
    dbus_connection_flush(b.bus);
    return b.app_call && b.adv_call ? 0 : -1;
}

int ble_start(void) {
    if (b.bus) return b.fd;
    DBusError e;
    dbus_error_init(&e);
    b.failed_at = crc_now_ms();
    b.bus = dbus_bus_get(DBUS_BUS_SYSTEM, &e);
    if (!b.bus) {
        char what[96];
        snprintf(what, sizeof what, "no system bus: %s", e.message ? e.message : "?");
        log_once(what);
        dbus_error_free(&e);
        return -1;
    }
    dbus_connection_set_exit_on_disconnect(b.bus, 0);
    static const DBusObjectPathVTable vt = {0, on_message, {0, 0, 0, 0}};
    static const char *const paths[] = {APP, SVC, CH_CONTROL, CH_EVENT, CH_INFO, ADV};
    for (unsigned i = 0; i < 6; ++i) dbus_connection_register_object_path(b.bus, paths[i], &vt, 0);
    dbus_connection_add_filter(b.bus, on_signal, 0, 0);
    dbus_bus_add_match(b.bus, "type='signal',sender='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.bluez'", 0);
    b.mtu = 23;
    if (!dbus_connection_get_unix_fd(b.bus, &b.fd)) b.fd = -1;
    start_registration();
    return b.fd;
}

int ble_events(void) { return POLLIN; }
/* libdbus holds messages it read while sending (a flush reads too): the loop's next poll must not wait */
int ble_pending(void) { return b.bus && dbus_connection_get_dispatch_status(b.bus) == DBUS_DATA_REMAINS; }

void ble_handle(void) {
    if (!b.bus) return;
    dbus_connection_read_write(b.bus, 0);
    while (dbus_connection_dispatch(b.bus) == DBUS_DATA_REMAINS) {}
    pending();
    dbus_connection_flush(b.bus);
}

/* called once a second from the loop: try the registration again 30 s after a refusal, never while bluetoothd is
 * off the bus (on_signal ends that) */
int ble_retry_due(uint32_t now) {
    if (!b.bus) return now - b.failed_at >= RETRY_MS;
    if (!dbus_connection_get_is_connected(b.bus)) return 0;
    if (b.registered != 3 && !b.absent && !b.app_call && !b.adv_call && now - b.failed_at >= RETRY_MS) {
        b.failed_at = now;
        start_registration();
    }
    pending();
    return 0;
}

/* the frame bytes out as notifications on the event characteristic, at most MTU - 3 each */
int ble_write(const uint8_t *p, size_t n) {
    if (!b.bus || !b.notifying) return -1;
    int chunk = b.mtu - 3;
    while (n) {
        int k = n < (size_t)chunk ? (int)n : chunk;
        DBusMessage *sig = dbus_message_new_signal(CH_EVENT, "org.freedesktop.DBus.Properties", "PropertiesChanged");
        DBusMessageIter it, dict, inv;
        if (!sig) return -1;
        dbus_message_iter_init_append(sig, &it);
        add_str(&it, T_STR, "org.bluez.GattCharacteristic1");
        dbus_message_iter_open_container(&it, T_ARRAY, "{sv}", &dict);
        prop_bytes(&dict, "Value", p, k);
        dbus_message_iter_close_container(&it, &dict);
        dbus_message_iter_open_container(&it, T_ARRAY, "s", &inv);
        dbus_message_iter_close_container(&it, &inv);
        dbus_connection_send(b.bus, sig, 0);
        dbus_message_unref(sig);
        p += k;
        n -= k;
    }
    dbus_connection_flush(b.bus);
    return 0;
}

/* the session ended on this side (a bad frame, a forgotten phone): drop the phone's link too */
void ble_disconnect(void) {
    if (!b.bus || !b.device[0]) return;
    DBusMessage *m = dbus_message_new_method_call("org.bluez", b.device, "org.bluez.Device1", "Disconnect");
    if (m) {
        dbus_connection_send(b.bus, m, 0);
        dbus_message_unref(m);
        dbus_connection_flush(b.bus);
    }
    b.device[0] = 0;
}

void ble_stop(void) {
    if (!b.bus) return;
    if (b.registered & 2) {
        DBusMessage *m = dbus_message_new_method_call("org.bluez", ADAPTER, "org.bluez.LEAdvertisingManager1", "UnregisterAdvertisement");
        DBusMessageIter it;
        if (m) {
            dbus_message_iter_init_append(m, &it);
            add_str(&it, T_PATH, ADV);
            dbus_connection_send(b.bus, m, 0);
            dbus_message_unref(m);
        }
    }
    if (b.registered & 1) {
        DBusMessage *m = dbus_message_new_method_call("org.bluez", ADAPTER, "org.bluez.GattManager1", "UnregisterApplication");
        DBusMessageIter it;
        if (m) {
            dbus_message_iter_init_append(m, &it);
            add_str(&it, T_PATH, APP);
            dbus_connection_send(b.bus, m, 0);
            dbus_message_unref(m);
        }
    }
    dbus_connection_flush(b.bus);
    set_registered(0);
}
