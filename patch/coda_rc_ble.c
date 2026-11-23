/* coda-rc's Bluetooth LE transport (docs/remote-protocol.md#bluetooth-le-primary): its own GATT server, as
 * SyncLink's libSL.so has. Stock's bluetoothd offers no GattManager1 or LEAdvertisingManager1 (its adapter
 * never makes a GATT database or an advertising manager), so the daemon listens on the LE ATT fixed channel
 * (L2CAP CID 4) itself, answers the few ATT requests a phone's GATT client sends, and advertises through the
 * kernel's management socket (MGMT_OP_ADD_ADVERTISING), as libSL's ble_mgmt_adv does.
 *
 * The frame stream rides the control characteristic's writes in and the event characteristic's notifications
 * out, cut at the link's MTU and joined again by the frames' lengths. Nothing asks for encryption or a bond:
 * coda-rc's own pairing and sealing protect the session, and a bonded phone could take the Q2 for an audio
 * device. One phone at a time; advertising stops while it is connected and starts again when it goes.
 *
 * The phone's frames reach the session one at a time, each once the answer before it has left (feed), so a
 * phone that asks for many covers at once gets them one by one and the link never holds more than one answer:
 * LE shares the radio with A2DP. The connection interval is the kernel's: on a peripheral link outside its
 * 30-50 ms default it asks the phone for that range (l2cap_le_conn_ready).
 *
 * While SyncLink is on (config.ini SYSSET SYNCLINK, off unless the user turns it on) the LE side stays off:
 * its libSL listens on the same channel. With Bluetooth off there is no hci0: the daemon waits for the kernel's
 * Index Added and advertises 2 s after it. Each failure is logged once, not at every try, and TCP goes on
 * meanwhile. /tmp/coda-rc.ble exists while the advertisement stands (crc_ble_advertising), for the Pair a
 * Phone page.
 *
 * The host build takes --ble-sim DIR: the ATT channel is a SOCK_SEQPACKET listener at DIR/att and the
 * management socket a SOCK_SEQPACKET connection to DIR/mgmt, so test/unit/test_coda_rc_ble.py plays the
 * phone's GATT client and the kernel. */
#include "coda_rc_sys.h"
#include "coda_rc.h"

#define SERVICE_UUID "b67cefa7-194f-4988-8820-4974e36704c3"
#define CONTROL_UUID "1666f4ad-18d6-4692-b723-b5ae83ce5ef3"
#define EVENT_UUID "5645283b-18bb-419e-b338-f089cfc8dced"
#define INFO_UUID "c7f73d6b-93b1-46b5-8892-1ba59bdb7513"
#define INFO_VALUE "coda-rc 1"
#define RETRY_MS 30000
#define INDEX_WAIT_MS 2000 /* after hci0 appears, for bluetoothd to power it */
#define CONFIG_MS 5000     /* how often config.ini is read for SyncLink and the name */
#define CONFIG "/mnt/data/config.ini"

/* ---- Bluetooth sockets: Linux's ABI (no headers on the Q2's side) ---- */
#define AF_BT 31
#define BTPROTO_L2CAP 0
#define BTPROTO_HCI 1
#define HCI_DEV_NONE 0xffff
#define HCI_CHANNEL_CONTROL 3
#define ATT_CID 4
#define BDADDR_LE_PUBLIC 1
#define HCI_INDEX 0 /* hci0 */

/* the management protocol (BlueZ doc/mgmt-api.txt): little-endian, a 6-byte header, then the parameters */
#define MGMT_OP_DISCONNECT 0x0014
#define MGMT_OP_ADD_ADVERTISING 0x003e
#define MGMT_OP_REMOVE_ADVERTISING 0x003f
#define MGMT_EV_CMD_COMPLETE 0x0001
#define MGMT_EV_CMD_STATUS 0x0002
#define MGMT_EV_INDEX_ADDED 0x0004
#define MGMT_EV_INDEX_REMOVED 0x0005
#define MGMT_STATUS_INVALID_INDEX 0x11
#define ADV_INSTANCE 2           /* libSL's is 1 */
#define ADV_CONNECTABLE 1u       /* MGMT_ADV_FLAG_CONNECTABLE */
#define ADV_DISCOV 2u            /* MGMT_ADV_FLAG_DISCOV: the kernel adds the Flags field */

/* ---- ATT (Core 5.x Vol 3 Part F) ---- */
#define ATT_MTU_MIN 23
#define ATT_MTU 247 /* what this side takes; the remote app asks for 247 */
enum {
    ERR_RSP = 0x01, MTU_REQ = 0x02, MTU_RSP = 0x03, FIND_INFO_REQ = 0x04, FIND_INFO_RSP = 0x05, FIND_TYPE_REQ = 0x06,
    FIND_TYPE_RSP = 0x07, READ_TYPE_REQ = 0x08, READ_TYPE_RSP = 0x09, READ_REQ = 0x0a, READ_RSP = 0x0b,
    READ_BLOB_REQ = 0x0c, READ_BLOB_RSP = 0x0d, READ_GROUP_REQ = 0x10, READ_GROUP_RSP = 0x11, WRITE_REQ = 0x12,
    WRITE_RSP = 0x13, NOTIFY = 0x1b, CONFIRM = 0x1e, WRITE_CMD = 0x52, CMD_BIT = 0x40,
};
enum {
    E_HANDLE = 0x01, E_READ = 0x02, E_WRITE = 0x03, E_PDU = 0x04, E_REQ = 0x06, E_OFFSET = 0x07, E_NONE = 0x0a,
    E_LEN = 0x0d, E_GROUP = 0x10,
};

/* the attribute table: GAP's service (the name a scanner shows), then coda-rc's */
enum {
    H_GAP = 1, H_NAME_DECL, H_NAME, H_LOOK_DECL, H_LOOK, H_SVC, H_CONTROL_DECL, H_CONTROL, H_EVENT_DECL, H_EVENT,
    H_CCCD, H_INFO_DECL, H_INFO, H_LAST = H_INFO
};
#define PRIMARY 0x2800
#define CHAR_DECL 0x2803
#define CCCD 0x2902
#define GAP 0x1800
#define DEVICE_NAME 0x2a00
#define APPEARANCE 0x2a01
#define P_READ 0x02
#define P_WRITE_CMD 0x04
#define P_WRITE 0x08
#define P_NOTIFY 0x10

#define IN_CAP (2 * CRC_MAX_FRAME)  /* the phone's bytes not yet framed or held for the answer before them */
#define OUT_CAP (3 * CRC_MAX_FRAME) /* one answer and the pushes made while it leaves */

static struct {
    int mfd, lfd, cfd;           /* the management socket, the ATT listener, the phone's ATT channel */
    int adv;                     /* ADV_* below */
    int absent;                  /* no hci0 (Bluetooth off): advertise on its Index Added */
    int synclink;                /* SyncLink on at the last look: everything here is closed */
    uint32_t failed_at, add_at;  /* the last refusal; when the next ADD_ADVERTISING may go */
    uint32_t config_at;
    uint16_t mtu, cccd;
    uint8_t peer[7];             /* the phone's address and its type, for MGMT_OP_DISCONNECT */
    int has_peer, dead;          /* dead: a send failed; the link ends at ble_handle's end, outside the session */
    uint8_t in[IN_CAP], out[OUT_CAP], rsp[ATT_MTU];
    size_t in_n, out_off, out_n, rsp_n;
    char name[32], adv_name[32]; /* the Bluetooth name, and the one advertised */
    char logged[96];             /* the last failure logged, so a failure that repeats is logged once */
#ifdef CODA_HOST
    char sim[160];
#endif
} b = {.mfd = -1, .lfd = -1, .cfd = -1};
enum { ADV_OFF, ADV_ASKED, ADV_ON };

static uint8_t u_service[16], u_control[16], u_event[16], u_info[16];

#ifdef CODA_HOST
void ble_sim(const char *dir) { snprintf(b.sim, sizeof b.sim, "%s", dir); }
#endif

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

static void put16(uint8_t *p, unsigned v) { p[0] = v, p[1] = v >> 8; }
static unsigned get16(const uint8_t *p) { return p[0] | p[1] << 8; }

/* "b67cefa7-194f-..." as ATT sends it: 16 bytes, least significant first */
static void uuid_le(const char *s, uint8_t *out) {
    int n = 15;
    for (; *s && n >= 0; ++s) {
        if (*s == '-') continue;
        unsigned hi = *s <= '9' ? *s - '0' : (*s | 32) - 'a' + 10;
        ++s;
        unsigned lo = *s <= '9' ? *s - '0' : (*s | 32) - 'a' + 10;
        out[n--] = hi << 4 | lo;
    }
}

static void nonblock(int fd) {
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
    fcntl(fd, F_SETFD, FD_CLOEXEC);
}

/* ---- config.ini: SyncLink's switch and the Bluetooth name ---- */

/* [SYSSET] SYNCLINK above 0: SyncLink's Bluetooth (1) or Wi-Fi (2), whose LE side libSL restarts too */
static int synclink_on(const char *ini) {
    const char *q = strstr(ini, "[SYSSET]"), *k = q ? strstr(q, "\nSYNCLINK=") : 0, *next = q ? strstr(q + 1, "\n[") : 0;
    if (!k || (next && k > next)) return 0;
    return atoi(k + 10) > 0;
}

static void config_read(void) {
    char p[256], ini[8192];
    int fd = open(crc_path(p, sizeof p, CONFIG), O_RDONLY | O_CLOEXEC), n = 0;
    if (fd >= 0) {
        n = (int)read(fd, ini, sizeof ini - 1);
        close(fd);
    }
    ini[n > 0 ? n : 0] = 0;
    b.synclink = synclink_on(ini);
    crc_bt_name(b.name);
}

/* ---- the management socket ---- */

static int mgmt_send(unsigned op, const uint8_t *p, unsigned n) {
    uint8_t m[6 + 64];
    if (b.mfd < 0 || n > sizeof m - 6) return -1;
    put16(m, op), put16(m + 2, HCI_INDEX), put16(m + 4, n);
    memcpy(m + 6, p, n);
    return write(b.mfd, m, 6 + n) < 0 ? -1 : 0; /* Linux 5.10 answers a management command's write with 0 */
}

static void set_adv(int state) {
    int was = b.adv == ADV_ON;
    b.adv = state;
    if (was != (state == ADV_ON)) crc_ble_advertising(state == ADV_ON);
}

/* The advertisement: the service's UUID, the kernel's Flags field (ADV_DISCOV), and the Bluetooth name in the
 * scan response, which has its own 31 bytes; a 30-character name is cut to 29 and marked shortened. */
static void adv_add(void) {
    uint8_t m[11 + 31 + 31];
    size_t name = strlen(b.name), cut = name > 29 ? 29 : name;
    m[0] = ADV_INSTANCE;
    m[1] = ADV_CONNECTABLE | ADV_DISCOV, m[2] = m[3] = m[4] = 0;
    put16(m + 5, 0), put16(m + 7, 0); /* duration and timeout: none */
    m[9] = 18, m[10] = 2 + cut;
    m[11] = 17, m[12] = 0x07; /* Complete List of 128-bit Service UUIDs */
    memcpy(m + 13, u_service, 16);
    m[29] = 1 + cut, m[30] = cut < name ? 0x08 : 0x09; /* Shortened or Complete Local Name */
    memcpy(m + 31, b.name, cut);
    if (mgmt_send(MGMT_OP_ADD_ADVERTISING, m, 11 + 18 + 2 + cut)) return;
    snprintf(b.adv_name, sizeof b.adv_name, "%s", b.name);
    set_adv(ADV_ASKED);
}

static void adv_remove(void) {
    uint8_t i = ADV_INSTANCE;
    mgmt_send(MGMT_OP_REMOVE_ADVERTISING, &i, 1);
    set_adv(ADV_OFF);
}

static void mgmt_event(const uint8_t *m, size_t n) {
    if (n < 6) return;
    unsigned ev = get16(m), index = get16(m + 2);
    if (ev == MGMT_EV_INDEX_ADDED && index == HCI_INDEX) {
        b.absent = 0;
        log_once(0);
        crc_log("ble: Bluetooth on; advertising in 2 s");
        b.add_at = crc_now_ms() + INDEX_WAIT_MS;
    } else if (ev == MGMT_EV_INDEX_REMOVED && index == HCI_INDEX) {
        set_adv(ADV_OFF);
        b.absent = 1;
        log_once("Bluetooth is off; waiting for it");
    } else if ((ev == MGMT_EV_CMD_COMPLETE || ev == MGMT_EV_CMD_STATUS) && n >= 9 &&
               get16(m + 6) == MGMT_OP_ADD_ADVERTISING && b.adv == ADV_ASKED) {
        unsigned status = m[8];
        if (!status) {
            if (ev == MGMT_EV_CMD_STATUS) return; /* pending: its Command Complete follows */
            set_adv(ADV_ON);
            log_once(0);
            crc_log("ble: advertising");
            return;
        }
        char what[96];
        if (status == MGMT_STATUS_INVALID_INDEX) {
            b.absent = 1;
            snprintf(what, sizeof what, "Bluetooth is off; waiting for it");
        } else {
            snprintf(what, sizeof what, "advertising refused: status 0x%02x", status);
        }
        log_once(what);
        b.failed_at = crc_now_ms();
        b.add_at = b.failed_at + RETRY_MS;
        set_adv(ADV_OFF);
    }
}

static int mgmt_open(void) {
#ifdef CODA_HOST
    if (b.sim[0]) {
        struct sockaddr_un a = {.sun_family = AF_UNIX};
        snprintf(a.sun_path, sizeof a.sun_path, "%s/mgmt", b.sim);
        int fd = socket(AF_UNIX, SOCK_SEQPACKET, 0);
        if (fd >= 0 && connect(fd, (void *)&a, sizeof a) < 0) close(fd), fd = -1;
        return fd;
    }
#endif
    uint8_t a[6];
    put16(a, AF_BT), put16(a + 2, HCI_DEV_NONE), put16(a + 4, HCI_CHANNEL_CONTROL);
    int fd = socket(AF_BT, SOCK_RAW, BTPROTO_HCI);
    if (fd >= 0 && bind(fd, (void *)a, sizeof a) < 0) close(fd), fd = -1;
    return fd;
}

/* ---- the ATT listener ---- */

static int att_listen(void) {
#ifdef CODA_HOST
    if (b.sim[0]) {
        struct sockaddr_un a = {.sun_family = AF_UNIX};
        snprintf(a.sun_path, sizeof a.sun_path, "%s/att", b.sim);
        unlink(a.sun_path);
        int fd = socket(AF_UNIX, SOCK_SEQPACKET, 0);
        if (fd >= 0 && (bind(fd, (void *)&a, sizeof a) < 0 || listen(fd, 1) < 0)) close(fd), fd = -1;
        return fd;
    }
#endif
    /* struct sockaddr_l2: family, psm, bdaddr (any), cid, bdaddr type */
    uint8_t a[14] = {0};
    put16(a, AF_BT), put16(a + 10, ATT_CID);
    a[12] = BDADDR_LE_PUBLIC;
    int fd = socket(AF_BT, SOCK_SEQPACKET, BTPROTO_L2CAP);
    if (fd >= 0 && (bind(fd, (void *)a, sizeof a) < 0 || listen(fd, 1) < 0)) close(fd), fd = -1;
    return fd;
}

/* ---- the phone's link ---- */

static void link_reset(void) {
    if (b.cfd >= 0) close(b.cfd);
    b.cfd = -1;
    b.has_peer = b.dead = 0;
    b.in_n = b.out_off = b.out_n = b.rsp_n = 0;
    b.cccd = 0;
    b.mtu = ATT_MTU_MIN;
}

/* the phone has gone (or was sent away): its session ends, and the Q2 can be found again */
static void link_gone(void) {
    if (b.cfd < 0) return;
    link_reset();
    crc_ble_gone();
    crc_log("ble: disconnected");
    b.add_at = crc_now_ms();
}

static void link_accept(void) {
    int fd = accept(b.lfd, 0, 0);
    if (fd < 0) return;
    if (b.cfd >= 0) { /* one phone at a time */
        close(fd);
        return;
    }
    nonblock(fd);
    int sndbuf = 4096; /* the kernel holds a few notifications, the rest waits here (feed) */
    setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof sndbuf);
    link_reset();
    b.cfd = fd;
    uint8_t a[16] = {0};
    socklen_t al = sizeof a;
    if (!getpeername(fd, (void *)a, &al) && get16(a) == AF_BT) {
        memcpy(b.peer, a + 4, 6);
        b.peer[6] = a[12];
        b.has_peer = 1;
    }
    adv_remove();
    crc_log("ble: connected");
    crc_ble_connected();
}

/* the next ATT reply goes before any notification still waiting */
static void reply(const uint8_t *p, size_t n) {
    memcpy(b.rsp, p, n);
    b.rsp_n = n;
}

static void error_rsp(unsigned op, unsigned handle, unsigned code) {
    uint8_t e[5] = {ERR_RSP, (uint8_t)op};
    put16(e + 2, handle);
    e[4] = code;
    reply(e, 5);
}

/* the type of handle h as ATT carries it (2 or 16 bytes, little-endian) into t; its length */
static int att_type(unsigned h, uint8_t *t) {
    static const uint16_t types[] = {0, PRIMARY, CHAR_DECL, DEVICE_NAME, CHAR_DECL, APPEARANCE, PRIMARY, CHAR_DECL,
                                     0, CHAR_DECL, 0, CCCD, CHAR_DECL, 0};
    if (h == H_CONTROL) return memcpy(t, u_control, 16), 16;
    if (h == H_EVENT) return memcpy(t, u_event, 16), 16;
    if (h == H_INFO) return memcpy(t, u_info, 16), 16;
    put16(t, types[h]);
    return 2;
}

static int decl(uint8_t *v, unsigned props, unsigned value, const uint8_t *uuid, int n) {
    v[0] = props;
    put16(v + 1, value);
    memcpy(v + 3, uuid, n);
    return 3 + n;
}

/* the value of handle h into v (at most 32 bytes); -1 when it cannot be read */
static int att_value(unsigned h, uint8_t *v) {
    uint8_t u[2];
    switch (h) {
    case H_GAP: return put16(v, GAP), 2;
    case H_NAME_DECL: return put16(u, DEVICE_NAME), decl(v, P_READ, H_NAME, u, 2);
    case H_NAME: return memcpy(v, b.name, strlen(b.name)), (int)strlen(b.name);
    case H_LOOK_DECL: return put16(u, APPEARANCE), decl(v, P_READ, H_LOOK, u, 2);
    case H_LOOK: return put16(v, 0), 2; /* Unknown */
    case H_SVC: return memcpy(v, u_service, 16), 16;
    case H_CONTROL_DECL: return decl(v, P_WRITE | P_WRITE_CMD, H_CONTROL, u_control, 16);
    case H_EVENT_DECL: return decl(v, P_NOTIFY, H_EVENT, u_event, 16);
    case H_CCCD: return put16(v, b.cccd), 2;
    case H_INFO_DECL: return decl(v, P_READ, H_INFO, u_info, 16);
    case H_INFO: return memcpy(v, INFO_VALUE, sizeof INFO_VALUE - 1), (int)sizeof INFO_VALUE - 1;
    }
    return -1;
}

static unsigned group_end(unsigned h) { return h == H_GAP ? H_LOOK : H_LAST; }

/* a write to handle h; 0, or the ATT error */
static int att_write(unsigned h, const uint8_t *p, size_t n) {
    if (h == H_CONTROL) {
        if (b.in_n + n > IN_CAP) { /* far more than requests come to: a phone not waiting for answers is let go */
            b.dead = 1;
            return E_LEN;
        }
        memcpy(b.in + b.in_n, p, n);
        b.in_n += n;
        return 0;
    }
    if (h == H_CCCD) {
        if (n != 2) return E_LEN;
        b.cccd = get16(p) & 1; /* notifications only: event does not indicate */
        return 0;
    }
    return h && h <= H_LAST ? E_WRITE : E_HANDLE;
}

/* one PDU from the phone; the answer, if any, into b.rsp */
static void att_pdu(const uint8_t *p, size_t n) {
    uint8_t r[ATT_MTU], t[16], v[32];
    unsigned op = p[0], start = n >= 5 ? get16(p + 1) : 0, end = n >= 5 ? get16(p + 3) : 0;
    size_t room = b.mtu, k = 0;
    switch (op) {
    case MTU_REQ: {
        if (n != 3) break;
        unsigned want = get16(p + 1);
        b.mtu = want < ATT_MTU_MIN ? ATT_MTU_MIN : want > ATT_MTU ? ATT_MTU : want;
        r[0] = MTU_RSP;
        put16(r + 1, ATT_MTU);
        return reply(r, 3);
    }
    case FIND_INFO_REQ:
    case READ_TYPE_REQ:
    case READ_GROUP_REQ:
    case FIND_TYPE_REQ: {
        if (n < 5 || (op == READ_TYPE_REQ || op == READ_GROUP_REQ ? n != 7 && n != 21 : op == FIND_INFO_REQ ? n != 5 : n < 7))
            break;
        if (!start || start > end) return error_rsp(op, start, E_HANDLE);
        const uint8_t *want = p + 5;
        int want_n = op == FIND_TYPE_REQ ? 2 : (int)n - 5, each = 0;
        if (op == READ_GROUP_REQ && !(want_n == 2 && get16(want) == PRIMARY))
            return error_rsp(op, start, want_n == 2 && get16(want) == 0x2801 ? E_NONE : E_GROUP);
        r[0] = op + 1;
        k = op == FIND_INFO_REQ || op == READ_TYPE_REQ || op == READ_GROUP_REQ ? 2 : 1;
        for (unsigned h = start; h <= end && h <= H_LAST; ++h) {
            int tn = att_type(h, t), vn, e;
            if (op != FIND_INFO_REQ && (tn != want_n || memcmp(t, want, tn))) continue;
            if (op == FIND_INFO_REQ) { /* handle and type, all of one size */
                e = 2 + tn;
                if (each && e != each) break;
                if (k + e > room) break;
                r[1] = tn == 2 ? 1 : 2;
                put16(r + k, h), memcpy(r + k + 2, t, tn);
            } else if (op == FIND_TYPE_REQ) { /* the services whose value is the one asked for */
                vn = att_value(h, v);
                if (vn != (int)n - 7 || memcmp(v, p + 7, vn)) continue;
                e = 4;
                if (k + e > room) break;
                put16(r + k, h), put16(r + k + 2, get16(t) == PRIMARY ? group_end(h) : h);
            } else {
                vn = att_value(h, v);
                if (vn < 0) {
                    if (!each) return error_rsp(op, h, E_READ);
                    break;
                }
                e = (op == READ_GROUP_REQ ? 4 : 2) + vn;
                if ((each && e != each) || k + e > room) break;
                put16(r + k, h);
                if (op == READ_GROUP_REQ) put16(r + k + 2, group_end(h));
                memcpy(r + k + e - vn, v, vn);
                r[1] = e;
            }
            k += e;
            each = e;
            if (op == READ_GROUP_REQ) h = group_end(h);
        }
        if (!each) return error_rsp(op, start, E_NONE);
        return reply(r, k);
    }
    case READ_REQ:
    case READ_BLOB_REQ: {
        if (n != (op == READ_REQ ? 3u : 5u)) break;
        unsigned h = get16(p + 1), off = op == READ_BLOB_REQ ? get16(p + 3) : 0;
        if (!h || h > H_LAST) return error_rsp(op, h, E_HANDLE);
        int vn = att_value(h, v);
        if (vn < 0) return error_rsp(op, h, E_READ);
        if ((int)off > vn) return error_rsp(op, h, E_OFFSET);
        r[0] = op + 1;
        k = (size_t)vn - off < room - 1 ? (size_t)vn - off : room - 1;
        memcpy(r + 1, v + off, k);
        return reply(r, 1 + k);
    }
    case WRITE_REQ:
    case WRITE_CMD: {
        if (n < 3) break;
        int e = att_write(get16(p + 1), p + 3, n - 3);
        if (op == WRITE_CMD) return;
        if (e) return error_rsp(op, get16(p + 1), e);
        r[0] = WRITE_RSP;
        return reply(r, 1);
    }
    case CONFIRM:
        return;
    default:
        if (op & CMD_BIT) return; /* a command this side does not know: ignored, as ATT says */
        return error_rsp(op, 0, E_REQ);
    }
    if (!(op & CMD_BIT)) error_rsp(op, 0, E_PDU);
}

/* the reply, then the frame bytes as notifications on event, at most MTU - 3 each, until the socket is full */
static void flush(void) {
    if (b.dead) return;
    if (b.cfd >= 0 && b.rsp_n) {
        if (send(b.cfd, b.rsp, b.rsp_n, MSG_NOSIGNAL) < 0) {
            if (errno != EAGAIN && errno != EINTR) b.dead = 1;
            return;
        }
        b.rsp_n = 0;
    }
    while (b.cfd >= 0 && b.out_off < b.out_n) {
        uint8_t pdu[ATT_MTU];
        size_t k = b.out_n - b.out_off, max = b.mtu - 3;
        if (k > max) k = max;
        pdu[0] = NOTIFY;
        put16(pdu + 1, H_EVENT);
        memcpy(pdu + 3, b.out + b.out_off, k);
        if (send(b.cfd, pdu, 3 + k, MSG_NOSIGNAL) < 0) {
            if (errno != EAGAIN && errno != EINTR) b.dead = 1;
            return;
        }
        b.out_off += k;
    }
    if (b.out_off == b.out_n) b.out_off = b.out_n = 0;
}

/* The phone's frames to the session, one at a time, each only once everything before it has gone out. A
 * length out of range goes as its two bytes alone, which the session refuses (crc_feed), closing it and the
 * link; what follows them is dropped. b.in holds up to two frames, so nothing longer than f is ever copied. */
static void feed(void) {
    static uint8_t f[CRC_MAX_FRAME];
    while (b.cfd >= 0 && !b.dead && b.out_n == 0 && !b.rsp_n && b.in_n >= 2) {
        size_t len = b.in[0] << 8 | b.in[1], take = 2 + len;
        if (len < 4 || take > CRC_MAX_FRAME) take = 2;
        if (take > b.in_n) return;
        memcpy(f, b.in, take);
        memmove(b.in, b.in + take, b.in_n - take);
        b.in_n -= take;
        crc_ble_bytes(f, take); /* may answer (ble_write), or close the link (ble_disconnect) */
    }
}

static void att_read(void) {
    uint8_t p[ATT_MTU + 8];
    for (;;) {
        ssize_t n = read(b.cfd, p, sizeof p);
        if (n < 0 && (errno == EAGAIN || errno == EINTR)) return;
        if (n <= 0) return link_gone();
        att_pdu(p, n);
        flush();
        if (b.cfd < 0 || b.rsp_n) return; /* the reply is still to go: no more requests until it has */
    }
}

/* ---- the daemon's side (coda_rc.h) ---- */

static void open_all(uint32_t now) {
    config_read();
    b.config_at = now;
    if (b.synclink) {
        log_once("off while SyncLink is on");
        return;
    }
    if (b.mfd < 0 && (b.mfd = mgmt_open()) >= 0) nonblock(b.mfd);
    if (b.lfd < 0 && (b.lfd = att_listen()) >= 0) nonblock(b.lfd);
    if (b.mfd < 0 || b.lfd < 0) {
        char what[96];
        snprintf(what, sizeof what, "no %s (%d)", b.mfd < 0 ? "management socket" : "LE ATT channel", errno);
        log_once(what);
        b.failed_at = now;
        return;
    }
    b.add_at = now;
}

static void close_all(void) {
    ble_disconnect();
    if (b.mfd >= 0 && b.adv != ADV_OFF) adv_remove();
    set_adv(ADV_OFF);
    if (b.lfd >= 0) close(b.lfd);
    if (b.mfd >= 0) close(b.mfd);
    b.lfd = b.mfd = -1;
}

void ble_start(void) {
    if (b.mfd >= 0 || b.lfd >= 0) return;
    uuid_le(SERVICE_UUID, u_service), uuid_le(CONTROL_UUID, u_control);
    uuid_le(EVENT_UUID, u_event), uuid_le(INFO_UUID, u_info);
    b.mtu = ATT_MTU_MIN;
    open_all(crc_now_ms());
    ble_tick(crc_now_ms());
}

int ble_fds(struct pollfd *p) {
    int n = 0;
    if (b.mfd >= 0) p[n++] = (struct pollfd){b.mfd, POLLIN, 0};
    if (b.lfd >= 0) p[n++] = (struct pollfd){b.lfd, POLLIN, 0};
    if (b.cfd >= 0) p[n++] = (struct pollfd){b.cfd, (short)(POLLIN | (b.rsp_n || b.out_off < b.out_n ? POLLOUT : 0)), 0};
    return n;
}

void ble_handle(const struct pollfd *p, int n) {
    for (int i = 0; i < n; ++i) {
        if (!p[i].revents) continue;
        if (p[i].fd == b.mfd) {
            uint8_t m[512];
            ssize_t k;
            while (b.mfd >= 0 && (k = read(b.mfd, m, sizeof m)) > 0) mgmt_event(m, k);
        } else if (p[i].fd == b.lfd) {
            link_accept();
        } else if (p[i].fd == b.cfd) {
            att_read();
        }
    }
    flush();
    feed();
    if (b.dead) link_gone();
}

/* once a second: SyncLink and the name every CONFIG_MS, a socket refused tried again after RETRY_MS, and the
 * advertisement made when it is due */
void ble_tick(uint32_t now) {
    if (now - b.config_at >= CONFIG_MS) {
        int was = b.synclink;
        config_read();
        b.config_at = now;
        if (b.synclink && !was) {
            close_all();
            log_once("off while SyncLink is on");
        } else if (!b.synclink && was) {
            log_once(0);
            open_all(now);
        } else if (b.adv != ADV_OFF && strcmp(b.name, b.adv_name)) {
            adv_add(); /* renamed: the same instance, replaced */
        }
    }
    if (b.synclink) return;
    if ((b.mfd < 0 || b.lfd < 0) && now - b.failed_at >= RETRY_MS) open_all(now);
    if (b.mfd >= 0 && b.lfd >= 0 && b.cfd < 0 && b.adv == ADV_OFF && !b.absent && (int32_t)(now - b.add_at) >= 0) adv_add();
}

uint32_t ble_due(uint32_t now) {
    if (b.synclink || b.mfd < 0 || b.lfd < 0 || b.cfd >= 0 || b.adv != ADV_OFF || b.absent) return 0xffffffffu;
    uint32_t d = b.add_at - now;
    return d >= 0x80000000u ? 0 : d;
}

/* the frame bytes out, once the phone has asked for notifications */
int ble_write(const uint8_t *p, size_t n) {
    if (b.cfd < 0 || b.dead || !b.cccd) return -1;
    if (b.out_n + n > OUT_CAP) {
        memmove(b.out, b.out + b.out_off, b.out_n - b.out_off);
        b.out_n -= b.out_off;
        b.out_off = 0;
        if (b.out_n + n > OUT_CAP) return -1;
    }
    memcpy(b.out + b.out_n, p, n);
    b.out_n += n;
    flush();
    return 0;
}

/* the session ended on this side (a bad frame, a forgotten phone): drop the phone's link too */
void ble_disconnect(void) {
    if (b.cfd < 0) return;
    if (b.has_peer) mgmt_send(MGMT_OP_DISCONNECT, b.peer, 7);
    link_reset();
    crc_log("ble: disconnected");
    b.add_at = crc_now_ms();
}

void ble_stop(void) { close_all(); }
