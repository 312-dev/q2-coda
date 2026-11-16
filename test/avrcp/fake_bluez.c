/* A stand-in bluetoothd for test/q2_avrcp.py: owns org.bluez on the bus DBUS_SYSTEM_BUS_ADDRESS names,
 * answers org.bluez.Media1.RegisterPlayer at /org/bluez/hci0 (refusing the first when argv[1] is "refuse"),
 * and prints each registration and each PropertiesChanged its caller sends, one line each, its values
 * written out ("REGISTER path {k: v, ...}", "CHANGED iface {...}"), then flushes. As BlueZ's media.c reads
 * a player: from the registration's dict, then its PropertiesChanged. The host's libdbus, declared here as
 * patch/q2_avrcp.c declares it (no development headers needed). */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct DBusConnection DBusConnection;
typedef struct DBusMessage DBusMessage;
typedef struct { const char *name, *message; unsigned dummy; void *pad; } DBusError;
typedef struct { void *p[4]; int i[16]; void *q[4]; } DBusMessageIter;
typedef uint32_t dbus_bool_t;
typedef int32_t dbus_int32_t;
typedef int64_t dbus_int64_t;
typedef int DBusHandlerResult;
void dbus_error_init(DBusError *);
DBusConnection *dbus_bus_get(int, DBusError *);
void dbus_bus_add_match(DBusConnection *, const char *, DBusError *);
int dbus_bus_request_name(DBusConnection *, const char *, unsigned, DBusError *);
int dbus_connection_add_filter(DBusConnection *, DBusHandlerResult (*)(DBusConnection *, DBusMessage *, void *), void *, void (*)(void *));
int dbus_connection_read_write_dispatch(DBusConnection *, int);
int dbus_connection_send(DBusConnection *, DBusMessage *, uint32_t *);
DBusMessage *dbus_message_new_method_return(DBusMessage *);
DBusMessage *dbus_message_new_error(DBusMessage *, const char *, const char *);
void dbus_message_unref(DBusMessage *);
int dbus_message_is_method_call(DBusMessage *, const char *, const char *);
int dbus_message_is_signal(DBusMessage *, const char *, const char *);
const char *dbus_message_get_path(DBusMessage *);
int dbus_message_iter_init(DBusMessage *, DBusMessageIter *);
int dbus_message_iter_get_arg_type(DBusMessageIter *);
int dbus_message_iter_get_element_type(DBusMessageIter *);
int dbus_message_iter_next(DBusMessageIter *);
void dbus_message_iter_recurse(DBusMessageIter *, DBusMessageIter *);
void dbus_message_iter_get_basic(DBusMessageIter *, void *);
#define DBUS_BUS_SYSTEM 1
#define DBUS_REQUEST_NAME_REPLY_PRIMARY_OWNER 1
#define DBUS_HANDLER_RESULT_HANDLED 0
#define DBUS_HANDLER_RESULT_NOT_YET_HANDLED 1
#define DBUS_TYPE_INVALID 0
#define DBUS_TYPE_STRING 's'
#define DBUS_TYPE_OBJECT_PATH 'o'
#define DBUS_TYPE_BOOLEAN 'b'
#define DBUS_TYPE_INT32 'i'
#define DBUS_TYPE_INT64 'x'
#define DBUS_TYPE_DOUBLE 'd'
#define DBUS_TYPE_VARIANT 'v'
#define DBUS_TYPE_DICT_ENTRY 'e'
#define DBUS_TYPE_ARRAY 'a'

static void dump(DBusMessageIter *it) {
    int first = 1;
    for (int t; (t = dbus_message_iter_get_arg_type(it)) != DBUS_TYPE_INVALID; dbus_message_iter_next(it)) {
        if (!first) fputs(", ", stdout);
        first = 0;
        DBusMessageIter sub;
        switch (t) {
        case DBUS_TYPE_STRING: case DBUS_TYPE_OBJECT_PATH: { const char *s; dbus_message_iter_get_basic(it, &s); printf("'%s'", s); break; }
        case DBUS_TYPE_BOOLEAN: { dbus_bool_t b; dbus_message_iter_get_basic(it, &b); fputs(b ? "true" : "false", stdout); break; }
        case DBUS_TYPE_INT32: { dbus_int32_t v; dbus_message_iter_get_basic(it, &v); printf("i%d", v); break; }
        case DBUS_TYPE_INT64: { dbus_int64_t v; dbus_message_iter_get_basic(it, &v); printf("x%lld", (long long)v); break; }
        case DBUS_TYPE_DOUBLE: { double v; dbus_message_iter_get_basic(it, &v); printf("d%g", v); break; }
        case DBUS_TYPE_VARIANT: dbus_message_iter_recurse(it, &sub); dump(&sub); break;
        case DBUS_TYPE_DICT_ENTRY: dbus_message_iter_recurse(it, &sub); dump(&sub); break;
        case DBUS_TYPE_ARRAY:
            dbus_message_iter_recurse(it, &sub);
            fputs(dbus_message_iter_get_element_type(it) == DBUS_TYPE_DICT_ENTRY ? "{" : "[", stdout);
            dump(&sub);
            fputs(dbus_message_iter_get_element_type(it) == DBUS_TYPE_DICT_ENTRY ? "}" : "]", stdout);
            break;
        default: printf("?%c", t);
        }
    }
}

static int refuse;

static DBusHandlerResult filter(DBusConnection *c, DBusMessage *m, void *unused) {
    (void)unused;
    DBusMessageIter it;
    if (dbus_message_is_method_call(m, "org.bluez.Media1", "RegisterPlayer") &&
        !strcmp(dbus_message_get_path(m), "/org/bluez/hci0")) {
        DBusMessage *r = refuse ? dbus_message_new_error(m, "org.bluez.Error.Failed", "busy") : dbus_message_new_method_return(m);
        printf(refuse ? "REFUSED " : "REGISTER ");
        refuse = 0;
        dbus_message_iter_init(m, &it);
        dump(&it);
        puts("");
        fflush(stdout);
        dbus_connection_send(c, r, 0);
        dbus_message_unref(r);
        return DBUS_HANDLER_RESULT_HANDLED;
    }
    if (dbus_message_is_signal(m, "org.freedesktop.DBus.Properties", "PropertiesChanged") &&
        !strcmp(dbus_message_get_path(m), "/org/q2pod/player")) {
        fputs("CHANGED ", stdout);
        dbus_message_iter_init(m, &it);
        dump(&it);
        puts("");
        fflush(stdout);
    }
    return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;
}

int main(int argc, char **argv) {
    DBusError e;
    dbus_error_init(&e);
    refuse = argc > 1 && !strcmp(argv[1], "refuse");
    DBusConnection *c = dbus_bus_get(DBUS_BUS_SYSTEM, &e);
    if (!c) return 1;
    dbus_bus_add_match(c, "type='signal',interface='org.freedesktop.DBus.Properties',path='/org/q2pod/player'", 0);
    dbus_connection_add_filter(c, filter, 0, 0);
    if (dbus_bus_request_name(c, "org.bluez", 0, &e) != DBUS_REQUEST_NAME_REPLY_PRIMARY_OWNER) return 2;
    puts("READY");
    fflush(stdout);
    while (dbus_connection_read_write_dispatch(c, -1)) {}
    return 0;
}
