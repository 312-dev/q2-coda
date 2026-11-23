/* q2dbus: BlueZ's A2DP transports as the system bus announces them, a part of q2guard
 * (docs/internals.md#bluetooth-event-log). A transport is one AVDTP stream to a device: BlueZ
 * makes its object (org.bluez.MediaTransport1) when the stream is configured, sets its State to
 * "pending" when the device sends AVDTP START, "active" once a start is accepted and audio may
 * flow, "idle" at a suspend or a start that failed, and removes it at the stream's close. q2guard
 * only listens: three match rules, no method called on BlueZ. The messages are parsed here with
 * no reads of the device (test/unit/test_q2dbus.py builds it on this machine, and runs the socket
 * side against a dbus-daemon of its own). */
#ifndef Q2DBUS_H
#define Q2DBUS_H

#include "q2guard.h"

#define DBUS_SOCK "/var/run/dbus/system_bus_socket" /* the Q2's /etc/dbus-1/system.conf */
#define DBUS_BUF 8192        /* the read buffer; a longer message is passed over */
#define DBUS_AUTH_MS 500     /* the bus's answer to the authentication is waited for this long */
#define DBUS_RETRY_MS 60000  /* a bus that would not open, or closed, is tried again after this */
#define DBUS_TRANSPORT "org.bluez.MediaTransport1"

/* ---- the messages, little-endian (the Q2's and this machine's) ---- */
/* The SASL line that opens a connection: a NUL, then AUTH EXTERNAL with uid in hex digits of its
 * decimal text, as libdbus sends it. Its length. */
int dbus_auth(char *out, int size, unsigned uid);
/* A method call to the bus itself (org.freedesktop.DBus, its member), with one string argument
 * or none (arg 0), serial its serial: its bytes into out, their count, or 0 when it does not fit. */
int dbus_call(unsigned char *out, int size, unsigned serial, const char *member, const char *arg);
/* The bytes the message starting at p takes, once its first 16 are there; 0 when fewer are; -1
 * for one that is not a version-1 message. */
int dbus_size(const unsigned char *p, int n);

enum { DBUS_TR_NONE, DBUS_TR_STATE, DBUS_TR_ADDED, DBUS_TR_REMOVED };
struct dbus_tr {
    int kind;               /* DBUS_TR_*: a State change, a transport made, a transport removed */
    char addr[18];          /* the device's address, from the object's path */
    unsigned char bdaddr[6]; /* the same, bdaddr_t order (the last byte first) */
    char tail[32];          /* the path after the device: "/sep1/fd0" */
    char state[16];         /* DBUS_TR_STATE: "idle", "pending", "active" */
};
/* One whole message (dbus_size's bytes): 1 when it is a transport's PropertiesChanged with a
 * State, or an InterfacesAdded or InterfacesRemoved naming DBUS_TRANSPORT, under /org/bluez/, into
 * t; 0 for anything else, a method's answer, or one that does not parse. */
int dbus_transport(const unsigned char *p, int n, struct dbus_tr *t);
/* Its unified-log message, name (or 0) quoted after the address: "transport 04:00:6E:D3:96:6D
 * "Pixel Buds" active: AVDTP started /sep1/fd0". Its level, I. */
char dbus_tr_line(const struct dbus_tr *t, const char *name, char *out, int size);

/* ---- the connection ---- */
struct bt_dbus {
    int fd; /* -1 none */
    int tried, warned;
    unsigned failed_at;
    int len;  /* bytes held in buf */
    int skip; /* bytes of a message too long for buf still to pass over */
    unsigned char buf[DBUS_BUF];
};
void dbus_init(struct bt_dbus *d);
/* Connected, or connecting now when it is time to try again (path the bus's socket): 1 when d->fd
 * can be polled. A connect authenticates (DBUS_AUTH_MS at most), then sends Hello and the three
 * match rules and returns without waiting for their answers. */
int dbus_ok(struct bt_dbus *d, const char *path, unsigned now);
/* The next transport event, reading what the socket holds (never waiting): 1 with t filled, 0 when
 * none is left; a closed connection is let go, and dbus_ok tries again later. */
int dbus_next(struct bt_dbus *d, struct dbus_tr *t, unsigned now);

#endif
