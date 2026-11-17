/* For test_coda_rc_ble.py: patch/coda_rc_ble.c built for this machine against a fake libdbus and fake daemon hooks,
 * enough to see what it asks bluetoothd for and when. Every RegisterApplication or RegisterAdvertisement is
 * answered at once with fake_error (NULL: accepted); fake_owner sends the NameOwnerChanged bluetoothd's arrival or
 * departure would; crc_log's lines and crc_ble_advertising's state are kept for the test to read. */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct DBusConnection DBusConnection;
typedef struct DBusPendingCall DBusPendingCall;
typedef struct DBusMessage {
    int type; /* 1 call, 2 return, 3 error, 4 signal */
    char iface[64], member[64], error[96];
    const char *args[3];
    int nargs;
} DBusMessage;
typedef struct { const char *name, *message; unsigned dummy; void *pad; } DBusError;
typedef struct { void *p[4]; int i[16]; void *q[4]; } DBusMessageIter;
typedef int (*dbus_msg_fn)(DBusConnection *, DBusMessage *, void *);
typedef struct { void (*unregister)(DBusConnection *, void *); dbus_msg_fn message; void (*pad[4])(void *); } DBusObjectPathVTable;

static int conn_token;
static dbus_msg_fn filter;
const char *fake_error;  /* the registrations' answer: an error name, or NULL for success */
int fake_calls;          /* RegisterApplication and RegisterAdvertisement calls made */
int fake_advertising;    /* crc_ble_advertising's last word */
uint32_t fake_now;       /* crc_now_ms */
char fake_log[8192];

void crc_log(const char *fmt, ...) {
    va_list ap;
    size_t n = strlen(fake_log);
    va_start(ap, fmt);
    vsnprintf(fake_log + n, sizeof fake_log - n, fmt, ap);
    va_end(ap);
    n = strlen(fake_log);
    if (n + 1 < sizeof fake_log) fake_log[n] = '\n', fake_log[n + 1] = 0;
}
uint32_t crc_now_ms(void) { return fake_now; }
void crc_ble_connected(void) {}
void crc_ble_bytes(const uint8_t *p, size_t n) { (void)p, (void)n; }
void crc_ble_gone(void) {}
void crc_ble_advertising(int on) { fake_advertising = on; }

static DBusMessage *msg(int type, const char *iface, const char *member) {
    DBusMessage *m = calloc(1, sizeof *m);
    m->type = type;
    snprintf(m->iface, sizeof m->iface, "%s", iface ? iface : "");
    snprintf(m->member, sizeof m->member, "%s", member ? member : "");
    return m;
}

/* bluetoothd taking org.bluez (owner non-empty) or leaving it (owner "") */
void fake_owner(const char *owner) {
    DBusMessage *m = msg(4, "org.freedesktop.DBus", "NameOwnerChanged");
    m->args[0] = "org.bluez", m->args[1] = "", m->args[2] = owner, m->nargs = 3;
    filter((DBusConnection *)&conn_token, m, 0);
    free(m);
}

void dbus_error_init(DBusError *e) { memset(e, 0, sizeof *e); }
void dbus_error_free(DBusError *e) { (void)e; }
DBusConnection *dbus_bus_get(int t, DBusError *e) { (void)t, (void)e; return (DBusConnection *)&conn_token; }
void dbus_bus_add_match(DBusConnection *c, const char *r, DBusError *e) { (void)c, (void)r, (void)e; }
void dbus_connection_set_exit_on_disconnect(DBusConnection *c, int v) { (void)c, (void)v; }
int dbus_connection_get_unix_fd(DBusConnection *c, int *fd) { (void)c; *fd = 99; return 1; }
int dbus_connection_register_object_path(DBusConnection *c, const char *p, const DBusObjectPathVTable *v, void *d) {
    (void)c, (void)p, (void)v, (void)d;
    return 1;
}
int dbus_connection_add_filter(DBusConnection *c, dbus_msg_fn f, void *d, void (*fr)(void *)) {
    (void)c, (void)d, (void)fr;
    filter = f;
    return 1;
}
int dbus_connection_read_write(DBusConnection *c, int t) { (void)c, (void)t; return 1; }
int dbus_connection_dispatch(DBusConnection *c) { (void)c; return 2; /* DBUS_DISPATCH_COMPLETE */ }
int dbus_connection_send(DBusConnection *c, DBusMessage *m, uint32_t *s) { (void)c, (void)m, (void)s; return 1; }
void dbus_connection_flush(DBusConnection *c) { (void)c; }
int dbus_connection_send_with_reply(DBusConnection *c, DBusMessage *m, DBusPendingCall **pc, int t) {
    (void)c, (void)t;
    DBusMessage *r = fake_error ? msg(3, 0, 0) : msg(2, 0, 0);
    if (fake_error) snprintf(r->error, sizeof r->error, "%s", fake_error);
    if (!strcmp(m->member, "RegisterApplication") || !strcmp(m->member, "RegisterAdvertisement")) ++fake_calls;
    *pc = (DBusPendingCall *)r; /* the pending call is its reply, complete at once */
    return 1;
}
int dbus_connection_get_is_connected(DBusConnection *c) { (void)c; return 1; }
void dbus_connection_unref(DBusConnection *c) { (void)c; }
int dbus_pending_call_get_completed(DBusPendingCall *pc) { (void)pc; return 1; }
DBusMessage *dbus_pending_call_steal_reply(DBusPendingCall *pc) { return (DBusMessage *)pc; }
void dbus_pending_call_unref(DBusPendingCall *pc) { (void)pc; }
DBusMessage *dbus_message_new_method_call(const char *d, const char *p, const char *i, const char *m) {
    (void)d, (void)p;
    return msg(1, i, m);
}
DBusMessage *dbus_message_new_method_return(DBusMessage *m) { (void)m; return msg(2, 0, 0); }
DBusMessage *dbus_message_new_error(DBusMessage *m, const char *n, const char *t) { (void)m, (void)n, (void)t; return msg(3, 0, 0); }
DBusMessage *dbus_message_new_signal(const char *p, const char *i, const char *m) { (void)p; return msg(4, i, m); }
void dbus_message_unref(DBusMessage *m) { free(m); }
int dbus_message_get_type(DBusMessage *m) { return m->type; }
const char *dbus_message_get_error_name(DBusMessage *m) { return m->type == 3 ? m->error : 0; }
int dbus_message_is_method_call(DBusMessage *m, const char *i, const char *n) {
    return m->type == 1 && !strcmp(m->iface, i) && !strcmp(m->member, n);
}
int dbus_message_is_signal(DBusMessage *m, const char *i, const char *n) {
    return m->type == 4 && !strcmp(m->iface, i) && !strcmp(m->member, n);
}
const char *dbus_message_get_path(DBusMessage *m) { (void)m; return 0; }
int dbus_message_iter_init(DBusMessage *m, DBusMessageIter *it) {
    it->p[0] = m, it->i[0] = 0;
    return m->nargs > 0;
}
void dbus_message_iter_init_append(DBusMessage *m, DBusMessageIter *it) { it->p[0] = m, it->i[0] = 0; }
int dbus_message_iter_get_arg_type(DBusMessageIter *it) {
    DBusMessage *m = it->p[0];
    return m && it->i[0] < m->nargs ? 's' : 0;
}
void dbus_message_iter_recurse(DBusMessageIter *it, DBusMessageIter *sub) { (void)it; memset(sub, 0, sizeof *sub); }
int dbus_message_iter_next(DBusMessageIter *it) {
    DBusMessage *m = it->p[0];
    return m && ++it->i[0] < m->nargs;
}
void dbus_message_iter_get_basic(DBusMessageIter *it, void *out) {
    DBusMessage *m = it->p[0];
    *(const char **)out = m->args[it->i[0]];
}
void dbus_message_iter_get_fixed_array(DBusMessageIter *it, void *out, int *n) { (void)it, (void)out; *n = 0; }
int dbus_message_iter_append_basic(DBusMessageIter *it, int t, const void *v) { (void)it, (void)t, (void)v; return 1; }
int dbus_message_iter_append_fixed_array(DBusMessageIter *it, int t, const void *v, int n) {
    (void)it, (void)t, (void)v, (void)n;
    return 1;
}
int dbus_message_iter_open_container(DBusMessageIter *it, int t, const char *s, DBusMessageIter *sub) {
    (void)it, (void)t, (void)s;
    memset(sub, 0, sizeof *sub);
    return 1;
}
int dbus_message_iter_close_container(DBusMessageIter *it, DBusMessageIter *sub) { (void)it, (void)sub; return 1; }
