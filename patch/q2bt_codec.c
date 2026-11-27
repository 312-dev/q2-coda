/* q2bt-codec PCM CODEC: the step-down's switch (docs/internals.md#bluetooth-link-monitor). Asks
 * bluealsa for org.bluealsa.PCM1.SelectCodec(CODEC, {}) on the PCM at object path PCM (GetManagedObjects's
 * A2DP source PCM, q2bt_core.c bt_pcms_path), CODEC as bluealsa names it ("aptX-HD", "aptX",
 * "SBC"). dbus-send cannot send it: its empty a{sv} needs a variant inside a container. A program
 * of its own, forked by q2guard (q2bt.c) for each switch, so the overheat guardian links no libdbus
 * and waits on no reply. Exit 0 when bluealsa answered without an error; else 1, the error's name
 * and message on stdout, which q2guard notes. */

/* ---- the libdbus 1.12 API used here (the rootfs's libdbus-1.so.3; no headers on the Q2's side,
 * as q2_guest.c) ---- */
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
} DBusMessageIter; /* larger than libdbus's; only its size is ours */
void dbus_error_init(DBusError *);
void dbus_error_free(DBusError *);
int dbus_error_is_set(const DBusError *);
DBusConnection *dbus_bus_get(int, DBusError *);
DBusMessage *dbus_connection_send_with_reply_and_block(DBusConnection *, DBusMessage *, int,
                                                       DBusError *);
DBusMessage *dbus_message_new_method_call(const char *, const char *, const char *, const char *);
void dbus_message_unref(DBusMessage *);
void dbus_message_iter_init_append(DBusMessage *, DBusMessageIter *);
int dbus_message_iter_append_basic(DBusMessageIter *, int, const void *);
int dbus_message_iter_open_container(DBusMessageIter *, int, const char *, DBusMessageIter *);
int dbus_message_iter_close_container(DBusMessageIter *, DBusMessageIter *);
int write(int, const void *, unsigned);
unsigned strlen(const char *);
#define DBUS_BUS_SYSTEM 1
#define CALL_MS 10000 /* bluealsa answers once the headset has the new codec */

static void say(const char *s) {
    if (s) write(1, s, strlen(s));
}

int main(int argc, char **argv) {
    if (argc != 3) {
        say("usage: q2bt-codec PCM CODEC\n");
        return 2;
    }
    DBusError e;
    dbus_error_init(&e);
    DBusConnection *bus = dbus_bus_get(DBUS_BUS_SYSTEM, &e);
    DBusMessage *m = bus ? dbus_message_new_method_call("org.bluealsa", argv[1],
                                                        "org.bluealsa.PCM1", "SelectCodec")
                         : (DBusMessage *)0;
    DBusMessage *r = (DBusMessage *)0;
    if (m) {
        DBusMessageIter it, props;
        const char *codec = argv[2];
        dbus_message_iter_init_append(m, &it);
        dbus_message_iter_append_basic(&it, 's', &codec);
        dbus_message_iter_open_container(&it, 'a', "{sv}", &props);
        dbus_message_iter_close_container(&it, &props);
        r = dbus_connection_send_with_reply_and_block(bus, m, CALL_MS, &e);
        dbus_message_unref(m);
    }
    if (r) {
        dbus_message_unref(r);
        return 0;
    }
    say(dbus_error_is_set(&e) ? e.name : "no bus");
    if (dbus_error_is_set(&e) && e.message) say(" "), say(e.message);
    say("\n");
    dbus_error_free(&e);
    return 1;
}
