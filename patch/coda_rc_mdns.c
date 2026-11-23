/* coda-rc's mDNS responder (docs/remote-protocol.md#discovery): while Car Remote is on and Wi-Fi is joined, the
 * Q2 answers multicast DNS (RFC 6762, IPv4, UDP 5353) for one DNS-SD service (RFC 6763), so Q2 Muse Remote's
 * Pair a Q2 Muse lists it without an address typed:
 *
 *   _q2muse._tcp.local                 PTR  <name>._q2muse._tcp.local      <name>: the Bluetooth name
 *   <name>._q2muse._tcp.local          SRV  0 0 7720 q2muse-<6 hex>.local  (config.ini BTNAME, else Q2 Muse)
 *   <name>._q2muse._tcp.local          TXT  id=<device id, 16 hex> v=1
 *   q2muse-<6 hex>.local               A    wlan0's address
 *   _services._dns-sd._udp.local       PTR  _q2muse._tcp.local             (service type enumeration)
 *
 * It answers only those names, sends two announcements a second apart when it starts and whenever the address
 * or the name changes, and a goodbye (TTL 0) for the old records first; and a goodbye when the daemon stops
 * (Car Remote off) or the address goes (Wi-Fi dropped). Nothing else is sent: no periodic announcements, no
 * queries. The socket is part of coda_rc.c's poll loop, not a thread.
 *
 * Stock's only other responder is shairport's built-in one (AirPlay receiver, in-process, no registration
 * API), and librespot on the card brings its own; each binds 5353 with SO_REUSEADDR, as this does, and Linux
 * hands every one of them each multicast packet.
 *
 * The packet half (mdns_query, mdns_reply) is pure and bounds-checked, for test/unit/test_coda_rc_mdns.py.
 * ponytail: no probing or conflict resolution (RFC 6762 section 9): two Q2s with the same Bluetooth name on
 * one network claim one instance name, and the phone lists one of them; renaming either fixes it. No answer
 * to legacy unicast queries (a source port other than 5353), which no DNS-SD browser sends. */
#include "coda_rc_sys.h"
#include "coda_rc.h"

#define MDNS_PORT 5353
#define MDNS_GROUP 0xe00000fbu /* 224.0.0.251 */
#define MDNS_CHECK_MS 1000     /* the address looked at this often */
#define MDNS_CHECK_IDLE_MS 5000 /* and this often while no phone is connected (mdns_pace) */
#define MDNS_NAME_MS 5000      /* the Bluetooth name this often */
#define MDNS_SPACING_MS 1000   /* between the two announcements, and the least between two sends of a record */
#define MDNS_RETRY_MS 30000    /* a socket refused on an address is tried again this much later (or on a new one) */
#define MDNS_PKT 1500

enum { DNS_A = 1, DNS_PTR = 12, DNS_TXT = 16, DNS_SRV = 33, DNS_ANY = 255, DNS_IN = 1, DNS_FLUSH = 0x8000 };

static const uint8_t SVC[] = "\x07_q2muse\x04_tcp\x05local";
static const uint8_t ENUM_NAME[] = "\x09_services\x07_dns-sd\x04_udp\x05local";
static const uint8_t LOCAL[] = "\x05local";

/* ---- names in wire form: length-prefixed labels, ending in the zero label ---- */

static size_t wire_len(const uint8_t *w) {
    size_t i = 0;
    while (w[i]) i += 1 + w[i];
    return i + 1;
}

/* a label then a suffix (wire form) into out (at least 256 bytes); 0 when the label is empty or too long */
static size_t wire_join(uint8_t *out, const char *label, const uint8_t *suffix) {
    size_t l = strlen(label), s = wire_len(suffix);
    if (!l || l > 63 || 1 + l + s > 255) return 0;
    out[0] = (uint8_t)l;
    memcpy(out + 1, label, l);
    memcpy(out + 1 + l, suffix, s);
    return 1 + l + s;
}

static int lower(int c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }

/* two wire names the same, ASCII letters in either case (RFC 6762 section 16); length bytes are under 64 */
static int wire_eq(const uint8_t *a, const uint8_t *b) {
    size_t n = wire_len(a);
    if (n != wire_len(b)) return 0;
    for (size_t i = 0; i < n; ++i)
        if (lower(a[i]) != lower(b[i])) return 0;
    return 1;
}

/* The name at p[*at] (a packet of n bytes) into out (256 bytes), uncompressed, and *at past it. Compression
 * pointers must point back, before the label that holds them, so a loop cannot be built; 0 when malformed. */
static int name_read(const uint8_t *p, size_t n, size_t *at, uint8_t *out) {
    size_t i = *at, o = 0;
    int jumped = 0;
    for (;;) {
        if (i >= n) return 0;
        unsigned l = p[i];
        if ((l & 0xc0) == 0xc0) {
            if (i + 1 >= n) return 0;
            size_t to = (l & 0x3f) << 8 | p[i + 1];
            if (to >= i) return 0;
            if (!jumped) *at = i + 2;
            jumped = 1;
            i = to;
            continue;
        }
        if (l & 0xc0) return 0; /* 0x40 and 0x80: no such label types */
        if (i + 1 + l > n || o + 1 + l > 255) return 0;
        out[o++] = (uint8_t)l;
        memcpy(out + o, p + i + 1, l);
        o += l;
        i += 1 + l;
        if (!l) {
            if (!jumped) *at = i;
            return 1;
        }
    }
}

/* ---- the packet half ---- */

typedef struct {
    uint8_t inst[256], host[256];
} names;

static int self_names(const mdns_self *me, names *nm) {
    return wire_join(nm->inst, me->name, SVC) && wire_join(nm->host, me->host, LOCAL);
}

static unsigned u16(const uint8_t *p) { return p[0] << 8 | p[1]; }

int mdns_query(const mdns_self *me, const uint8_t *p, size_t n) {
    names nm;
    uint8_t q[256], rd[256];
    if (n < 12) return -1;
    if (p[2] & 0x80 || (p[2] >> 3 & 15)) return 0; /* a response, or not a standard query: nothing to answer */
    if (!self_names(me, &nm)) return 0;
    unsigned qd = u16(p + 4), an = u16(p + 6), want = 0, known = 0;
    size_t at = 12;
    for (unsigned k = 0; k < qd; ++k) {
        if (!name_read(p, n, &at, q) || at + 4 > n) return -1;
        unsigned type = u16(p + at), cls = u16(p + at + 2) & 0x7fff; /* the top bit asks for a unicast answer */
        at += 4;
        if (cls != DNS_IN && cls != DNS_ANY) continue;
        int any = type == DNS_ANY;
        if (wire_eq(q, SVC) && (any || type == DNS_PTR)) want |= MD_PTR;
        else if (wire_eq(q, nm.inst)) want |= (any || type == DNS_SRV ? MD_SRV : 0) | (any || type == DNS_TXT ? MD_TXT : 0);
        else if (wire_eq(q, nm.host) && (any || type == DNS_A)) want |= MD_A;
        else if (wire_eq(q, ENUM_NAME) && (any || type == DNS_PTR)) want |= MD_ENUM;
    }
    /* Known answers (RFC 6762 section 7.1): a PTR the asker holds with at least half its TTL left is not sent */
    for (unsigned k = 0; k < an; ++k) {
        if (!name_read(p, n, &at, q) || at + 10 > n) return -1;
        unsigned type = u16(p + at), rdlen = u16(p + at + 8);
        uint32_t ttl = (uint32_t)p[at + 4] << 24 | p[at + 5] << 16 | p[at + 6] << 8 | p[at + 7];
        at += 10;
        if (at + rdlen > n) return -1;
        size_t r = at;
        if (type == DNS_PTR && ttl >= MDNS_TTL / 2 && name_read(p, at + rdlen, &r, rd)) {
            if (wire_eq(q, SVC) && wire_eq(rd, nm.inst)) known |= MD_PTR;
            if (wire_eq(q, ENUM_NAME) && wire_eq(rd, SVC)) known |= MD_ENUM;
        }
        at += rdlen;
    }
    return (int)(want & ~known);
}

typedef struct {
    uint8_t *p;
    size_t n, cap;
    int over;
} wbuf;

static void put(wbuf *w, const void *s, size_t n) {
    if (w->over || n > w->cap - w->n) {
        w->over = 1;
        return;
    }
    memcpy(w->p + w->n, s, n);
    w->n += n;
}

static void put16(wbuf *w, unsigned v) {
    uint8_t b[2] = {(uint8_t)(v >> 8), (uint8_t)v};
    put(w, b, 2);
}

static void record(wbuf *w, const uint8_t *name, unsigned type, unsigned cls, uint32_t ttl, const void *rd, size_t rdn) {
    uint8_t t[4] = {(uint8_t)(ttl >> 24), (uint8_t)(ttl >> 16), (uint8_t)(ttl >> 8), (uint8_t)ttl};
    put(w, name, wire_len(name));
    put16(w, type);
    put16(w, cls);
    put(w, t, 4);
    put16(w, (unsigned)rdn);
    put(w, rd, rdn);
}

static void one(wbuf *w, const mdns_self *me, const names *nm, unsigned which, uint32_t ttl) {
    static const char hex[] = "0123456789abcdef";
    uint8_t rd[300];
    size_t n;
    switch (which) {
    case MD_PTR:
        record(w, SVC, DNS_PTR, DNS_IN, ttl, nm->inst, wire_len(nm->inst));
        break;
    case MD_SRV: /* priority 0, weight 0, the port, the host */
        rd[0] = rd[1] = rd[2] = rd[3] = 0;
        rd[4] = (uint8_t)(me->port >> 8), rd[5] = (uint8_t)me->port;
        n = wire_len(nm->host);
        memcpy(rd + 6, nm->host, n);
        record(w, nm->inst, DNS_SRV, DNS_IN | DNS_FLUSH, ttl, rd, 6 + n);
        break;
    case MD_TXT: /* "id=<16 hex>" "v=1", each string after its length */
        rd[0] = 19;
        memcpy(rd + 1, "id=", 3);
        for (int i = 0; i < 8; ++i) rd[4 + 2 * i] = hex[me->id[i] >> 4], rd[5 + 2 * i] = hex[me->id[i] & 15];
        rd[20] = 3;
        memcpy(rd + 21, "v=1", 3);
        record(w, nm->inst, DNS_TXT, DNS_IN | DNS_FLUSH, ttl, rd, 24);
        break;
    case MD_A:
        record(w, nm->host, DNS_A, DNS_IN | DNS_FLUSH, ttl, me->ip, 4);
        break;
    case MD_ENUM:
        record(w, ENUM_NAME, DNS_PTR, DNS_IN, ttl, SVC, wire_len(SVC));
        break;
    }
}

static unsigned bits(unsigned v) {
    unsigned c = 0;
    for (; v; v &= v - 1) ++c;
    return c;
}

size_t mdns_reply(const mdns_self *me, unsigned answers, uint32_t ttl, uint8_t *out, size_t cap) {
    names nm;
    answers &= MD_ALL;
    if (!answers || !self_names(me, &nm)) return 0;
    /* what the asker will want next (RFC 6763 section 12): the SRV, TXT and A for a PTR, the A for an SRV */
    unsigned extra = (answers & MD_PTR ? MD_SRV | MD_TXT | MD_A : 0) | (answers & MD_SRV ? MD_A : 0);
    extra &= ~answers;
    wbuf w = {out, 0, cap, 0};
    uint8_t h[12] = {0, 0, 0x84, 0, 0, 0, 0, (uint8_t)bits(answers), 0, 0, 0, (uint8_t)bits(extra)}; /* QR, AA */
    put(&w, h, 12);
    for (unsigned b = 1; b <= MD_ALL; b <<= 1)
        if (answers & b) one(&w, me, &nm, b, ttl);
    for (unsigned b = 1; b <= MD_ALL; b <<= 1)
        if (extra & b) one(&w, me, &nm, b, ttl);
    return w.over ? 0 : w.n;
}

/* ---- the daemon's half ---- */

#define CONFIG "/mnt/data/config.ini"
#define NAME_DEFAULT "Q2 Muse"
#define NAME_MAX 30 /* bt_name.c BT_NAME_MAX */

static struct {
    int fd, on;
    char ifname[16];
    mdns_self me; /* as announced, while fd is open */
    uint32_t checked, named, next_announce;
    int announces;           /* still to send */
    uint32_t sent[5];        /* when each record (MD_* bit) was last multicast */
    uint8_t refused[4];      /* the address a socket was last refused on, and when */
    uint32_t refused_at;
    char name[NAME_MAX + 1]; /* the Bluetooth name as last read */
} md = {.fd = -1};

/* config.ini's [Q2POD] BTNAME into out when it is one bt_name.c's bt_name_valid takes, else NAME_DEFAULT (and
 * coda_rc_ble.c's advertised name) */
void crc_bt_name(char *out) {
    char p[256], b[8192];
    int fd = open(crc_path(p, sizeof p, CONFIG), O_RDONLY | O_CLOEXEC), n = 0;
    snprintf(out, NAME_MAX + 1, "%s", NAME_DEFAULT);
    if (fd < 0) return;
    n = (int)read(fd, b, sizeof b - 1);
    close(fd);
    if (n <= 0) return;
    b[n] = 0;
    const char *q = strstr(b, "[Q2POD]"), *k = q ? strstr(q, "\nBTNAME=") : 0, *next = q ? strstr(q + 1, "\n[") : 0;
    if (!k || (next && k > next)) return;
    k += 8;
    size_t l = 0;
    while (k[l] && k[l] != '\n' && k[l] != '\r') ++l;
    if (!l || l > NAME_MAX || k[0] == ' ' || k[l - 1] == ' ') return;
    for (size_t i = 0; i < l; ++i)
        if (k[i] < ' ' || k[i] > '~' || k[i] == '\'' || k[i] == '"' || k[i] == '\\' || k[i] == '`') return;
    memcpy(out, k, l);
    out[l] = 0;
}

/* the interface's IPv4 address into ip, 0 with none (Wi-Fi off or not joined) */
static int if_addr(const char *ifname, uint8_t ip[4]) {
    unsigned char ifr[40]; /* struct ifreq: the name, then a sockaddr_in (the address at 20) */
    memset(ifr, 0, sizeof ifr);
    snprintf((char *)ifr, 16, "%s", ifname);
    int fd = socket(AF_INET, SOCK_DGRAM, 0), ok = 0;
    if (fd >= 0) {
        ok = ioctl(fd, SIOCGIFADDR, ifr) == 0 && (ifr[20] | ifr[21] | ifr[22] | ifr[23]);
        close(fd);
    }
    if (ok) memcpy(ip, ifr + 20, 4);
    return ok;
}

static void send_records(unsigned answers, uint32_t ttl) {
    uint8_t pkt[MDNS_PKT];
    size_t n = mdns_reply(&md.me, answers, ttl, pkt, sizeof pkt);
    if (!n || md.fd < 0) return;
    struct sockaddr_in to;
    memset(&to, 0, sizeof to);
    to.sin_family = AF_INET;
    to.sin_port = htons(MDNS_PORT);
    to.sin_addr.s_addr = htonl(MDNS_GROUP);
    if (sendto(md.fd, pkt, n, 0, (void *)&to, sizeof to) < 0) crc_log("mdns: send failed (%d)", errno);
    uint32_t now = crc_now_ms();
    for (int b = 0; b < 5; ++b)
        if (answers & 1u << b) md.sent[b] = now;
}

/* the records said goodbye to (TTL 0) and the socket closed */
static void md_down(void) {
    if (md.fd < 0) return;
    send_records(MD_PTR | MD_SRV | MD_TXT | MD_A, 0);
    close(md.fd);
    md.fd = -1;
    md.announces = 0;
    crc_log("mdns: off");
}

/* a socket on 5353 in 224.0.0.251 on the address ip, sending from it; 0 when one could not be opened */
static int md_up(const uint8_t ip[4]) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0), one = 1, ttl = 255;
    if (fd < 0) return 0;
    struct sockaddr_in a;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons(MDNS_PORT);
    a.sin_addr.s_addr = INADDR_ANY;
    struct ip_mreq m;
    memcpy(&m.imr_interface.s_addr, ip, 4);
    m.imr_multiaddr.s_addr = htonl(MDNS_GROUP);
    const char *step = 0;
    if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one)) step = "SO_REUSEADDR";
    else if (bind(fd, (void *)&a, sizeof a)) step = "bind";
    else if (setsockopt(fd, IPPROTO_IP, IP_ADD_MEMBERSHIP, &m, sizeof m)) step = "IP_ADD_MEMBERSHIP";
    else if (setsockopt(fd, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof ttl)) step = "IP_MULTICAST_TTL";
    /* Sends leave by this address's interface. Not fatal: without it they follow the route, wlan0 on a Q2.
     * qemu-user 8.2 (the emulator) refuses every form of it with EADDRNOTAVAIL. */
    if (!step && setsockopt(fd, IPPROTO_IP, IP_MULTICAST_IF, &m.imr_interface, sizeof m.imr_interface))
        crc_log("mdns: IP_MULTICAST_IF refused (errno %d); sending by the route", errno);
    if (step) {
        crc_log("mdns: %s refused on %u.%u.%u.%u (errno %d)", step, ip[0], ip[1], ip[2], ip[3], errno);
        close(fd);
        return 0;
    }
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
    md.fd = fd;
    memcpy(md.me.ip, ip, 4);
    snprintf(md.me.name, sizeof md.me.name, "%s", md.name);
    snprintf(md.me.host, sizeof md.me.host, "q2muse-%02x%02x%02x", crc_device_id[0], crc_device_id[1], crc_device_id[2]);
    memcpy(md.me.id, crc_device_id, 8);
    md.me.port = (uint16_t)crc_tcp_port();
    memset(md.sent, 0, sizeof md.sent);
    md.announces = 2; /* RFC 6762 section 8.3: at least two, a second apart */
    md.next_announce = crc_now_ms();
    crc_log("mdns: %s on %u.%u.%u.%u", md.me.name, ip[0], ip[1], ip[2], ip[3]);
    return 1;
}

void mdns_start(const char *ifname) {
    snprintf(md.ifname, sizeof md.ifname, "%s", ifname);
    md.on = 1;
    md.checked = crc_now_ms() - MDNS_CHECK_IDLE_MS; /* the first look at once, at either pace */
    md.named = crc_now_ms() - MDNS_NAME_MS;
}

int mdns_fd(void) { return md.fd; }

/* The address looked at every MDNS_CHECK_IDLE_MS while the daemon is quiet (no phone connected: coda_rc.c quiet),
 * else every MDNS_CHECK_MS: a new address is announced up to 5 s late then, and queries are answered at once. */
static uint32_t check_ms = MDNS_CHECK_MS;
void mdns_pace(int quiet) { check_ms = quiet ? MDNS_CHECK_IDLE_MS : MDNS_CHECK_MS; }

void mdns_poll(uint32_t now) {
    if (!md.on) return;
    if (now - md.checked >= check_ms) {
        md.checked = now;
        int renamed = 0;
        if (now - md.named >= MDNS_NAME_MS) {
            char was[sizeof md.name];
            memcpy(was, md.name, sizeof was);
            md.named = now;
            crc_bt_name(md.name);
            renamed = strcmp(was, md.name) != 0;
        }
        uint8_t ip[4];
        int have = if_addr(md.ifname, ip);
        if (md.fd >= 0 && (!have || memcmp(ip, md.me.ip, 4) || renamed)) md_down();
        if (have && md.fd < 0 && (memcmp(ip, md.refused, 4) || now - md.refused_at >= MDNS_RETRY_MS) && !md_up(ip))
            memcpy(md.refused, ip, 4), md.refused_at = now;
    }
    if (md.fd >= 0 && md.announces && now - md.next_announce < 0x80000000u) {
        send_records(MD_PTR | MD_SRV | MD_TXT | MD_A, MDNS_TTL);
        --md.announces;
        md.next_announce = now + MDNS_SPACING_MS;
    }
}

uint32_t mdns_due(uint32_t now) {
    if (!md.on) return 0xffffffffu;
    uint32_t due = md.checked + check_ms - now;
    if (due > check_ms) due = 0; /* already due */
    if (md.fd >= 0 && md.announces) {
        uint32_t a = md.next_announce - now;
        if (a >= 0x80000000u) a = 0;
        if (a < due) due = a;
    }
    return due;
}

void mdns_handle(void) {
    uint8_t pkt[MDNS_PKT];
    struct sockaddr_in from;
    socklen_t fl = sizeof from;
    ssize_t r = recvfrom(md.fd, pkt, sizeof pkt, 0, (void *)&from, &fl);
    if (r <= 0 || fl < sizeof from || from.sin_port != htons(MDNS_PORT)) return;
    int want = mdns_query(&md.me, pkt, (size_t)r);
    if (want <= 0) return;
    uint32_t now = crc_now_ms();
    for (int b = 0; b < 5; ++b) /* RFC 6762 section 6: a record multicast at most once a second */
        if (want & 1 << b && md.sent[b] && now - md.sent[b] < MDNS_SPACING_MS) want &= ~(1 << b);
    if (want) send_records((unsigned)want, MDNS_TTL);
}

void mdns_stop(void) {
    md_down();
    md.on = 0;
}
