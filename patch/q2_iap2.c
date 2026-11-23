/* iAP2 with the Q2 in an iPhone's place, over the iPhone gadget's HID interface: milestone M1 of route 1
 * (docs/iphone-mode.md#iap2). q2_iap.c feeds it the car's output reports (SET_REPORT data) and sends its input
 * reports (q2_iap2.h); nothing here touches a file or the USB itself.
 *
 * A port of PodBox's firmware/usbstack/usb_iap2.c (the transport and link layer) and the M1 part of
 * usb_iap2_control.c (the control session up to identification, and the answers an iPhone gives right after),
 * Copyright the PodBox and Rockbox authors, under the GNU General Public License version 2 or (at your option) any
 * later version, here under version 3 as the rest of Q2 Muse is. PodBox takes its message IDs and packet layout from
 * lvalen91/carplayd (public domain) and its message order and timing from iPhones in real cars; the traps it found
 * are kept, each with its note. Apple's MFi specification was never consulted, here or there.
 *
 * In order: the HID transport (reports of [id][link control][data]: 00 whole, 02 first, 03 middle, 01 last); the link
 * layer (packets FF 5A, a 9-byte header, a checksummed payload; the car's SYN answered SYN+ACK with its own
 * parameters, the length capped); the control session (messages 40 40, length, id, parameters of length, id, data):
 * the Q2 asks for the car's certificate, challenges it, accepts whatever answers (the Q2 verifies nothing and holds no
 * key), asks it to identify, accepts that, then sends what an iPhone sends next. */
#include "q2_iap_sys.h"
#include "iap2.h"
#include "q2_iap2.h"
#include "iap2_names.h"
#include "iap2_hid.h"
#include "iap2_msg.h"
#include "iap2_xfer.h"
#include "iap2_library.h"

#define IAP2_SOP1 0xFF
#define IAP2_SOP2 0x5A
#define CTL_SYN 0x80
#define CTL_ACK 0x40
#define CTL_EAK 0x20
#define CTL_RST 0x10
#define SYN_FIXED 10   /* link parameters ahead of the session list */
#define RX_PACKET 8192  /* the longest it takes, offered in the SYN+ACK (an iPhone offers 0xFFFF) */
#define SLOTS 8
/* The link lost when the Q2 has packets or reports waiting and the car has sent nothing for this long (iap2_tick):
 * the BMW acknowledges within 22 ms and gives a packet up after 10 tries of 200 ms, so 10 s of quiet with something
 * waiting is a car that has stopped. q2muse-1125: the car stopped taking input reports, pacing then held every packet
 * back, the tries were never counted (they wait for a clear wire) and the link stayed up, silent, for 23 minutes. */
long link_quiet_ms = 10000;

const struct iap2_host *H;
const u8 iap2_probe[] = { 0xFF, 0x55, 0x02, 0x00, 0xEE, 0x10 };

/* ---- the HID transport ---- */

/* Input report ids and their sizes after the id (the report descriptor's counts), smallest first. PodBox sends no
 * report larger than 7 at high speed (95 bytes after the id), and the cars it runs in take that; so does this. */
static const u16 in_report_sizes[] = { 5, 9, 13, 17, 25, 49, 95 };
static long sent_at; /* the Q2's last input report handed over (link_health) */

static void send_bytes(const u8 *p, unsigned n) {
    unsigned off = 0;
    u8 r[1 + 95];
    do {
        unsigned left = n - off, id = 0;
        while (id + 1 < sizeof in_report_sizes / sizeof *in_report_sizes && in_report_sizes[id] < left + 1) ++id;
        unsigned size = in_report_sizes[id], take = size - 1 < left ? size - 1 : left;
        memset(r, 0, sizeof r);
        r[0] = id + 1;
        r[1] = (off ? 0x01 : 0) | (off + take < n ? 0x02 : 0); /* continues, more to follow */
        memcpy(r + 2, p + off, take);
        H->send(r, 1 + size);
        sent_at = H->now();
        off += take;
    } while (off < n);
}

/* ---- the link layer ---- */

int seen, link_up;
static int synced;
static u8 my_seq, syn_seq, peer_seq, last_ack;
u8 file_session;
u8 control_session;
static u8 syn_ack[48];
static unsigned syn_ack_len, max_sends, max_cacks, unacked;
unsigned window;
unsigned peer_max;
long rto;
static long cack, unacked_since; /* milliseconds; rto 0: never sent again */
static long wire_busy_at; /* the last time reports were still waiting for the wire */
static long heard_at, waiting_since; /* the car's last output report; since when something has waited (iap2_tick) */
/* acked_at: the car's last new acknowledgement; link_health_at: link_health's last line */
static long acked_at, link_health_at;
long health_ms = 5000;    /* --health-ms; 0 none */
static unsigned dropped;         /* packets dropped from a full queue, this link */
static int retries_held, drop_said;
int backlog(void) { return H && H->backlog ? H->backlog() : 0; }

struct slot {
    u8 packet[MAX_PACKET];
    unsigned len, tries;
    long sent;
    u8 quiet; /* a position update's: its first sending not logged */
};
static struct slot slots[SLOTS];
unsigned head, count;
static u8 rx[RX_PACKET + 256], cs[RX_PACKET + 256];
static unsigned rx_len, cs_len;
static int rx_partial;

static u8 cksum(const u8 *b, unsigned n) {
    u8 s = 0;
    while (n--) s += *b++;
    return (u8)-s;
}

static void put_header(u8 *p, unsigned len, u8 control, u8 seq, u8 session) {
    p[0] = IAP2_SOP1, p[1] = IAP2_SOP2, p[2] = len >> 8, p[3] = len, p[4] = control, p[5] = seq, p[6] = peer_seq;
    p[7] = session, p[8] = cksum(p, 8);
}

static void log_packet(int from_q2, const u8 *p, unsigned len, const char *note) {
    char m[200];
    snprintf(m, sizeof m, "pkt %s len %u ctl %02x%s%s%s%s seq %u ack %u session %u%s%s", from_q2 ? ">" : "<", len,
             p[4], p[4] & CTL_SYN ? " SYN" : "", p[4] & CTL_ACK ? " ACK" : "", p[4] & CTL_EAK ? " EAK" : "",
             p[4] & CTL_RST ? " RST" : "", p[5], p[6], p[7], note ? " " : "", note ? note : "");
    H->log(m);
}

/* The log's volume (car-bt-power.md #4: 870 B/s on the 2026-11-22 drive, 85 % of it the position update a song
 * plays with, twice a second, and its acknowledgement, five lines each): a position update's message, packet and
 * reports, and each bare acknowledgement either way, are counted, not logged (trace_quiet, read by q2_iap.c io_send
 * too), and the counts go in the link's health line. Sent again, a packet is logged. */
int msg_quiet;
int trace_quiet;
static unsigned quiet_pos, quiet_in, quiet_out;
/* An output report that is the car's bare acknowledgement whole: [id][00, a whole packet][FF 5A 00 09 40 ...] */
int iap2_bare_ack(const u8 *r, unsigned n) {
    return n >= 2 + HEADER_LEN && r[1] == 0 && r[2] == IAP2_SOP1 && r[3] == IAP2_SOP2 && r[4] == 0 &&
           r[5] == HEADER_LEN && r[6] == CTL_ACK;
}

static void send_ctl(u8 control, const u8 *payload, unsigned n, u8 seq) {
    u8 p[HEADER_LEN + 48 + 1];
    unsigned len = HEADER_LEN + (n ? n + 1 : 0);
    put_header(p, len, control, seq, 0);
    if (n) memcpy(p + HEADER_LEN, payload, n), p[HEADER_LEN + n] = cksum(payload, n);
    trace_quiet = control == CTL_ACK && !n;
    if (trace_quiet) ++quiet_out;
    else log_packet(1, p, len, 0);
    send_bytes(p, len);
    trace_quiet = 0;
    unacked = 0;
}

static void send_slot(struct slot *s) {
    s->packet[6] = peer_seq;
    s->packet[8] = cksum(s->packet, 8);
    s->tries++, s->sent = H->now();
    unacked = 0;
    trace_quiet = s->quiet && s->tries == 1;
    if (!trace_quiet) log_packet(1, s->packet, s->len, s->tries > 1 ? "(again)" : 0);
    send_bytes(s->packet, s->len);
    trace_quiet = 0;
}

/* Messages waiting for room in the car's window: with retransmission on (the BMW's SYN gives 10 retries), no more
 * than its window may be unacknowledged, and a cover is a dozen packets. Sent in order as acknowledgements free the
 * window (pump). */
#define QUEUE 96
static struct queued {
    u8 session, quiet;
    unsigned len;
    u8 b[MAX_PACKET - HEADER_LEN - 1];
} queue[QUEUE];
static unsigned q_head;
unsigned q_count;
static void pump(void);

static void iap2_control_reset(void);
static void iap2_control_link_up(void);
static void iap2_control_receive(const u8 *msg, unsigned len);

static void reset_link(void) {
    synced = link_up = 0;
    count = 0;
    q_head = q_count = 0;
    last_ack = my_seq;
    cs_len = unacked = 0;
    waiting_since = 0;
    dropped = 0, drop_said = retries_held = 0;
    iap2_control_reset();
}

/* The link given up on: said why, forgotten, and the host told (q2_iap.c io_link_lost: the iPhone shown again). */
void iap2_lost(const char *why) {
    H->log(why);
    reset_link();
    if (H->link_lost) H->link_lost();
}

/* The car's SYN: its link parameters, the sessions after them. Retransmitted SYNs get the same answer. */
/* The least maximum packet a SYN may offer: under it a file transfer's piece has no room (12 to 19 bytes spun the
 * loop for good at the car's go, car-bt-power.md #13) and most messages do not fit. No car offers so little (the
 * BMW 0xFFFF, PodBox's cars 1,024 or more). */
#define MIN_PEER_MAX 128
static void take_syn(u8 seq, const u8 *payload, unsigned n) {
    char h[200], m[300];
    if (n < SYN_FIXED || n > sizeof syn_ack || payload[0] != 1) {
        iap2_hex(h, sizeof h, payload, n);
        iap2_log("link: SYN not taken (not version 1, or its length):", h);
        return;
    }
    iap2_hex(h, sizeof h, payload, n);
    snprintf(m, sizeof m, "link parameters: version %u, window %u, max packet %u, retransmit %u ms, cack %u ms, "
             "retries %u, cacks %u:", payload[0], payload[1], payload[2] << 8 | payload[3], payload[4] << 8 | payload[5],
             payload[6] << 8 | payload[7], payload[8], payload[9]);
    iap2_log(m, h);
    unsigned want = payload[2] << 8 | payload[3];
    if (want < MIN_PEER_MAX) {
        snprintf(m, sizeof m, "link: SYN not taken (max packet %u bytes, under %u)", want, MIN_PEER_MAX);
        H->log(m);
        return;
    }
    memcpy(syn_ack, payload, n);
    syn_ack_len = n;
    peer_max = want > MAX_PACKET ? MAX_PACKET : want;
    if (want > RX_PACKET) syn_ack[2] = RX_PACKET >> 8, syn_ack[3] = RX_PACKET & 255;
    /* The Q2 holds nothing it receives, so takes any number of packets unacknowledged; an iPhone offers 0x7F to an
     * accessory that retransmits and echoes the window to one that does not. */
    if (payload[8]) syn_ack[1] = 0x7F;
    window = payload[1] < 1 ? 1 : payload[1] > SLOTS ? SLOTS : payload[1];
    /* Trap (PodBox): a car that gives 0 for the retransmission timeout and retries leaves packets unacknowledged for
     * seconds; an iPhone never sends one again, and a packet sent again stops that car reading. */
    max_sends = payload[8] + 1u;
    rto = payload[8] ? (payload[4] << 8 | payload[5]) : 0;
    if (rto && rto < 100) rto = 100;
    /* Trap (PodBox): a bare ACK only to a car whose SYN gives a cumulative acknowledgement timeout; one that gives 0
     * stops reading after a bare ACK. */
    cack = payload[6] << 8 | payload[7];
    max_cacks = payload[9];
    unacked = 0;
    control_session = file_session = 0;
    for (unsigned i = SYN_FIXED; i + 3 <= n; i += 3) {
        if (payload[i + 1] == 0) control_session = payload[i];
        else if (payload[i + 1] == 1) file_session = payload[i];
    }
    snprintf(m, sizeof m, "link: control session %u, file transfer session %u", control_session, file_session);
    H->log(m);
    syn_seq = my_seq;
    peer_seq = seq;
    synced = 1;
    send_ctl(CTL_SYN | CTL_ACK, syn_ack, syn_ack_len, syn_seq);
}

static void drop_acked(void) {
    while (count) {
        struct slot *s = &slots[head];
        if (!s->tries || (u8)(last_ack - s->packet[5]) >= 128) break;
        head = (head + 1) % SLOTS, --count;
    }
}

static void take_ack(u8 ack) {
    if (!link_up && ack == syn_seq) {
        link_up = 1;
        H->log("link: up");
        iap2_control_link_up();
    }
    if (ack != last_ack) acked_at = H->now();
    last_ack = ack;
    if (rto) drop_acked();
    pump();
}

static void take_control(const u8 *data, unsigned n) {
    if (cs_len + n > sizeof cs) cs_len = 0;
    memcpy(cs + cs_len, data, n);
    cs_len += n;
    unsigned off = 0;
    while (cs_len - off >= 6) {
        const u8 *m = cs + off;
        unsigned len = m[2] << 8 | m[3];
        if (m[0] != 0x40 || m[1] != 0x40 || len < 6 || len > sizeof cs) {
            H->log("control: bytes that are not a message, dropped");
            off = cs_len;
            break;
        }
        if (cs_len - off < len) break;
        iap2_control_receive(m, len);
        off += len;
    }
    memmove(cs, cs + off, cs_len - off);
    cs_len -= off;
}

static void take_packet(const u8 *p, unsigned len) {
    u8 control = p[4], seq = p[5], session = p[7];
    const u8 *payload = p + HEADER_LEN;
    unsigned n = len > HEADER_LEN ? len - HEADER_LEN - 1 : 0;
    if (n && cksum(payload, n + 1) != 0) {
        log_packet(0, p, len, "(bad payload checksum: dropped)");
        return;
    }
    if (control == CTL_ACK && len == HEADER_LEN) ++quiet_in; /* a bare acknowledgement: counted (link_health) */
    else log_packet(0, p, len, 0);
    if (control & CTL_RST) {
        reset_link();
        return;
    }
    if (control & CTL_SYN) {
        if (link_up) reset_link();
        take_syn(seq, payload, n);
        return;
    }
    if (!synced) return;
    if (control & CTL_ACK) take_ack(p[6]);
    if (control & CTL_EAK) {
        for (unsigned i = 0; i < n; ++i)
            for (unsigned j = 0; j < count; ++j) {
                struct slot *s = &slots[(head + j) % SLOTS];
                if (s->tries && s->packet[5] == payload[i]) send_slot(s);
            }
    } else if (n) {
        /* Out of order or repeated, a packet is dropped, and the Q2's next packet names the last one taken. */
        if (link_up && seq == (u8)(peer_seq + 1)) {
            peer_seq = seq;
            if (cack && !unacked++) unacked_since = H->now();
            if (session == control_session) take_control(payload, n);
            else if (file_session && session == file_session) iap2_file(payload, n);
            if (cack && max_cacks && unacked >= max_cacks) send_ctl(CTL_ACK, 0, 0, my_seq);
        } else if (link_up && cack && (u8)(peer_seq - seq) < 128) {
            send_ctl(CTL_ACK, 0, 0, my_seq);
        }
    }
}

static int opens_packet(const u8 *p, unsigned n) {
    return n >= HEADER_LEN && p[0] == IAP2_SOP1 && p[1] == IAP2_SOP2 && cksum(p, HEADER_LEN) == 0;
}

/* Whole packets off rx. What is left of a transfer's last report is padding, or a packet the car finishes in its next
 * transfer without marking it continued (PodBox). */
static void take_packets(int last) {
    unsigned off = 0;
    int waiting = 0;
    while (rx_len - off >= HEADER_LEN) {
        const u8 *p = rx + off;
        if (p[0] != IAP2_SOP1 || p[1] != IAP2_SOP2 || cksum(p, HEADER_LEN) != 0) {
            ++off;
            continue;
        }
        unsigned len = p[2] << 8 | p[3];
        if (len < HEADER_LEN) {
            ++off;
            continue;
        }
        if (len > RX_PACKET) {
            log_packet(0, p, len, "(longer than offered: dropped)");
            off = rx_len;
            break;
        }
        if (rx_len - off < len) {
            waiting = 1;
            break;
        }
        take_packet(p, len);
        off += len;
    }
    rx_partial = last && waiting;
    if (last && !waiting) rx_len = 0;
    else memmove(rx, rx + off, rx_len - off), rx_len -= off;
}

/* An output report from the car: [id][link control][data]. 1 if it was iAP2's. */
int iap2_report(const u8 *r, unsigned len) {
    if (len < 2) return 0;
    const u8 *data = r + 2;
    unsigned n = len - 2;
    int first = !(r[1] & 0x01);
    int is_probe = first && n >= sizeof iap2_probe && !memcmp(data, iap2_probe, sizeof iap2_probe);
    if (is_probe) {
        H->log(seen ? "detect again: echoed, link reset" : "detect: echoed");
        seen = 1;
        reset_link();
        rx_len = 0, rx_partial = 0;
        send_bytes(iap2_probe, sizeof iap2_probe);
        return 1;
    }
    if (!seen) return 0;
    heard_at = H->now();
    if (first && !(rx_partial && !opens_packet(data, n))) rx_len = 0;
    rx_partial = 0;
    if (n > sizeof rx - rx_len) n = sizeof rx - rx_len;
    memcpy(rx + rx_len, data, n);
    rx_len += n;
    take_packets(!(r[1] & 0x02));
    return 1;
}

/* A packet's payload to go: at once while the window has room, else in turn (pump). */
void enqueue(u8 session, const u8 *b, unsigned n) {
    /* a full queue's drops said once, then counted in link_health's line (q2muse-1125 logged 2,555 of them) */
    if (q_count == QUEUE) {
        if (!drop_said) H->log("link: the send queue is full; packets dropped (counted in link: health)"), drop_said = 1;
        ++dropped;
        return;
    }
    if (n > sizeof queue[0].b) {
        H->log("link: a packet too long; dropped");
        return;
    }
    drop_said = 0;
    struct queued *q = &queue[(q_head + q_count++) % QUEUE];
    q->session = session, q->len = n, q->quiet = (u8)msg_quiet;
    memcpy(q->b, b, n);
    pump();
}

static void pump(void) {
    while (q_count && link_up && (!rto || (count < window && count < SLOTS)) && backlog() <= BACKLOG_MAX) {
        struct queued *q = &queue[q_head];
        struct slot *s = &slots[(head + count) % SLOTS];
        s->len = HEADER_LEN + q->len + 1;
        put_header(s->packet, s->len, CTL_ACK, ++my_seq, q->session);
        memcpy(s->packet + HEADER_LEN, q->b, q->len);
        s->packet[HEADER_LEN + q->len] = cksum(q->b, q->len);
        s->tries = 0, s->quiet = q->quiet;
        if (rto) ++count; /* kept for sending again; with no retransmission, done with once sent */
        q_head = (q_head + 1) % QUEUE, --q_count;
        send_slot(s);
    }
}

/* ---- the control session: the conversation ---- */

static u16 car_receives[96];
static unsigned car_receives_n;
static int identified, now_playing, bt_component = -1;
int ever_identified; /* this cable: identified kept after the link is lost, for the summary */
static long auth_due, ident_due, availability_due, np_at; /* 0: not due */
static unsigned car_current;
static u32 media_mask, playback_mask, rnd;
static struct iap2_np np_last;
static int np_sent;
int audio_on;
char car_name[64]; /* the car's identification name, for the status file (q2_iap.c state_write) */

static void set_audio(int on) {
    if (on == audio_on) return;
    audio_on = on;
    H->log(on ? "audio: on (the player plays into the car)" : "audio: off");
    if (H->audio) H->audio(on);
}


#define MI_PERSISTENT_ID 0x00
#define MI_TITLE 0x01
#define MI_DURATION 0x04
#define MI_ALBUM 0x06
#define MI_ARTIST 0x0C
#define MI_LIKE_SUPPORTED 0x15 /* 0x15 to 0x18: like and ban, bools */
#define MI_BANNED 0x18
#define MI_ARTWORK 0x1A
#define MI_CHAPTER_COUNT 0x1B
#define PB_CHAPTER_INDEX 0x04
#define PB_LIBRARY 0x08
#define PB_STATE 0x00
#define PB_POSITION 0x01
#define PB_QUEUE_INDEX 0x02
#define PB_QUEUE_COUNT 0x03
#define PB_SHUFFLE 0x05
#define PB_REPEAT 0x06
#define PB_APP_NAME 0x07
#define PB_SPEED 0x0C
#define PB_SEEKABLE 0x0D
#define PB_QUEUE_LIST 0x0E    /* 1: the queue's list follows (ql_announce) */
#define PB_APP_BUNDLE 0x10
#define PB_QUEUE_LIST_ID 0x11 /* empty, with PB_QUEUE_LIST 1 */
#define DEFAULT_MEDIA (1u << MI_PERSISTENT_ID | 1u << MI_TITLE | 1u << MI_DURATION | 1u << MI_ALBUM | 1u << MI_ARTIST)
#define DEFAULT_PLAYBACK (1u << PB_STATE | 1u << PB_POSITION | 1u << PB_QUEUE_INDEX | 1u << PB_QUEUE_COUNT)

static long due_in(long ms) {
    long d = H->now() + ms;
    return d ? d : 1;
}

static void iap2_control_reset(void) {
    if (H) set_audio(0);
    art_going = 0, art_id = 0, n_controls = 0, held = 0, scanning = 0;
    lib_reset();
    identified = now_playing = 0;
    auth_due = ident_due = availability_due = 0;
    car_receives_n = 0, car_current = 0, bt_component = -1;
    np_sent = 0;
}

static int car_takes(u16 id) {
    for (unsigned i = 0; i < car_receives_n; ++i)
        if (car_receives[i] == id) return 1;
    return 0;
}

static void iap2_control_link_up(void) { send_empty(0xAA00); }

/* A 20-byte challenge to an MFi 2.0 certificate (about 900 bytes), 32 to a 3.0 one (about 600), as PodBox sends.
 * Nothing checks the answer. */
static void send_challenge(unsigned cert_len) {
    unsigned n = cert_len > 800 ? 20 : 32;
    u8 c[32];
    char t[96];
    if (!rnd) rnd = (u32)H->now() | 1;
    for (unsigned i = 0; i < n; ++i) rnd ^= rnd << 13, rnd ^= rnd >> 17, rnd ^= rnd << 5, c[i] = rnd;
    snprintf(t, sizeof t, "auth: the car's certificate is %u bytes; a %u-byte challenge", cert_len, n);
    H->log(t);
    msg_start(0xAA02);
    param(0, c, n);
    msg_send();
}

/* The car's identification: its names, and the message lists (6 it sends, 7 it takes) logged by name. */
static void take_identification(const u8 *p, unsigned len) {
    static const char *const fields[] = { "name", "model", "manufacturer", "serial", "firmware", "hardware" };
    char t[1800];
    unsigned n, o;
    for (unsigned f = 0; f < 6; ++f) {
        const u8 *v = find(p, len, f, &n);
        if (v && n && !v[n - 1]) {
            snprintf(t, sizeof t, "ident: %s \"%s\"", fields[f], (const char *)v);
            H->log(t);
            if (f == 0) snprintf(car_name, sizeof car_name, "%s", (const char *)v);
        }
    }
    for (unsigned which = 6; which <= 7; ++which) {
        const u8 *list = find(p, len, which, &n);
        o = (unsigned)snprintf(t, sizeof t, "ident: the car %s:", which == 6 ? "sends" : "takes");
        for (unsigned i = 0; list && i + 1 < n && o + 48 < sizeof t; i += 2) {
            unsigned id = list[i] << 8 | list[i + 1];
            o += (unsigned)snprintf(t + o, sizeof t - o, " %04x %s", id, iap2_name(id));
            if (which == 7 && car_receives_n < sizeof car_receives / sizeof *car_receives)
                car_receives[car_receives_n++] = id;
        }
        H->log(t);
    }
    const u8 *bt = find(p, len, 17, &n);
    bt_component = bt && (bt = find(bt, n, 0, &n)) && n == 2 ? (int)get_be(bt, 2) : -1;
    /* An iPhone accepts an identification 24 to 81 ms after it (PodBox, from iPhones' logs). */
    ident_due = due_in(30);
}

static void accept_identification(void) {
    send_empty(0x1D02);
    identified = ever_identified = 1;
    H->log("ident: accepted");
    /* What an iPhone sends next, in its order, a packet each; PodBox's car resets without the UUID. */
    if (car_takes(0x4E0C)) msg_start(0x4E0C), param_str(0, H->uuid), msg_send();
    if (car_takes(0x4E0A)) msg_start(0x4E0A), param_str(0, "en"), msg_send();
    if (car_takes(0x4E09)) msg_start(0x4E09), param_str(0, H->name), msg_send();
    /* The iPhone then starts pairing with the car's Bluetooth component and stops it at once, the stop carrying an
     * empty parameter 2. */
    if (bt_component >= 0 && car_takes(0x0B00) && car_takes(0x0B03)) {
        msg_start(0x0B00), param_u16(0, bt_component), msg_send();
        msg_start(0x0B03), param_u16(0, bt_component), param(2, 0, 0), msg_send();
    }
}

/* CarPlay is never available, as an iPhone with it switched off, so the car falls back to plain USB audio: groups 0
 * (wired) and 1 (wireless), each Available false in sub-parameter 0. */
static void send_carplay_availability(void) {
    availability_due = 0;
    msg_start(0x4300);
    group_start(0), param_u8(0, 0), group_end();
    group_start(1), param_u8(0, 0), group_end();
    msg_send();
}

/* The attribute ids a Start...Updates group lists, as a mask. */
static u32 wanted(const u8 *g, unsigned len, u32 fallback) {
    if (!g) return fallback;
    u32 mask = 0;
    for (unsigned off = 0; off + 4 <= len;) {
        unsigned plen = g[off] << 8 | g[off + 1], id = g[off + 2] << 8 | g[off + 3];
        if (plen < 4) break;
        if (id < 32) mask |= 1u << id;
        off += plen;
    }
    return mask;
}

/* Each power attribute asked for, a message each in an iPhone's order 4, 5, 0, 1, 6 (PodBox): 4 01; 5 the charge
 * state (2 charged: the Q2 on the car's power); 0 the current it takes, never more than the car offers; 1 01; 6 the
 * battery percentage, 100 as an iPhone at full charge sends. */
static void send_power(u32 mask) {
    static const u8 order[] = { 4, 5, 0, 1, 6 };
    for (unsigned i = 0; i < sizeof order; ++i) {
        unsigned a = order[i];
        if (!(mask & 1u << a)) continue;
        msg_start(0xAE01);
        if (a == 5) param_u8(a, 2);
        else if (a == 0) param_u16(a, car_current && car_current < 1500 ? car_current : 1500);
        else if (a == 6) param_u16(a, 100);
        else param_u8(a, 1);
        msg_send();
    }
}

/* The rate as an index into an iPhone's nine (8000 to 48000); an iPhone at 44100 sends 7, then two uint32s, both 0.
 * The rate is the player's for the song about to play (iap2_audio_rate, docs/iphone-mode.md#per-song-rate), sent at
 * StartUSBDeviceModeAudio and again at each song whose rate differs: 32, 44.1 and 48 kHz (6, 7, 8), the 48 kHz family
 * above it 8, anything else 44.1 kHz's 7. */
const unsigned iap2_rates[9] = { 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000 };
unsigned audio_hz = 44100;
static int audio_sent = -1;

int iap2_rate_index(unsigned hz) {
    if (hz == 32000) return 6;
    if (hz == 48000 || (hz > 48000 && hz % 48000 == 0)) return 8;
    return 7;
}

static void send_audio_rate(void) {
    char t[64];
    audio_sent = iap2_rate_index(audio_hz);
    snprintf(t, sizeof t, "audio: %u Hz announced (DA01 index %d)", iap2_rates[audio_sent], audio_sent);
    H->log(t);
    msg_start(0xDA01);
    param_u8(0, (u8)audio_sent);
    param_u32(1, 0);
    param_u32(2, 0);
    msg_send();
}

/* The player's rate for the song about to play (q2_iap.c rate_poll): told the car while it takes USB audio. */
void iap2_audio_rate(unsigned hz) {
    audio_hz = hz;
    if (audio_on && iap2_rate_index(hz) != audio_sent) send_audio_rate();
}

static u32 np_hash(const struct iap2_np *n) {
    u32 h = 2166136261u;
    for (const char *s = n->title; *s; ++s) h = (h ^ (u8)*s) * 16777619u;
    for (const char *s = n->artist; *s; ++s) h = (h ^ (u8)*s) * 16777619u;
    return h;
}

/* The song's attributes the car asked for (group 0) and its playback state (group 1). With nothing known, the
 * title is the Q2's name, stopped. A stream (live) has no duration or position, and an empty cover. */
static void send_now_playing(const struct iap2_np *n, int media) {
    msg_start(0x5001);
    if (media) {
        group_start(0);
        /* the library's id for a song in it (lib_item), so the car knows the song it plays; else a hash */
        if (media_mask & 1u << MI_PERSISTENT_ID)
            param_u64(MI_PERSISTENT_ID, n->id ? LIB_KEY : 0x51320000u, n->id ? n->id : np_hash(n));
        if (media_mask & 1u << MI_TITLE) param_str(MI_TITLE, n->title[0] ? n->title : H->name);
        if ((media_mask & 1u << MI_DURATION) && !n->live) param_u32(MI_DURATION, n->len_ms);
        if ((media_mask & 1u << MI_ALBUM) && n->album[0]) param_str(MI_ALBUM, n->album);
        if ((media_mask & 1u << MI_ARTIST) && n->artist[0]) param_str(MI_ARTIST, n->artist);
        for (unsigned a = MI_LIKE_SUPPORTED; a <= MI_BANNED; ++a)
            if (media_mask & 1u << a) param_u8(a, 0);
        if (media_mask & 1u << MI_CHAPTER_COUNT) param_u16(MI_CHAPTER_COUNT, 0);
        if (media_mask & 1u << MI_ARTWORK) { /* a stream's an empty transfer: no cover (art_start) */
            u8 id = art_start(n);
            if (id) param_u8(MI_ARTWORK, id);
        }
        group_end();
    }
    group_start(1);
    if (playback_mask & 1u << PB_STATE) param_u8(PB_STATE, n->state);
    if ((playback_mask & 1u << PB_POSITION) && !n->live) param_u32(PB_POSITION, n->pos_ms);
    if (playback_mask & 1u << PB_QUEUE_INDEX) param_u32(PB_QUEUE_INDEX, n->qpos);
    if (playback_mask & 1u << PB_QUEUE_COUNT) param_u32(PB_QUEUE_COUNT, n->qlen);
    if (playback_mask & 1u << PB_SHUFFLE) param_u8(PB_SHUFFLE, n->shuffle ? 1 : 0);
    if (playback_mask & 1u << PB_REPEAT) param_u8(PB_REPEAT, n->repeat);
    if (playback_mask & 1u << PB_CHAPTER_INDEX) param_u32(PB_CHAPTER_INDEX, 0);
    if (playback_mask & 1u << PB_LIBRARY) param_str(PB_LIBRARY, H->library_id);
    if (playback_mask & 1u << PB_APP_NAME) param_str(PB_APP_NAME, H->name);
    if (playback_mask & 1u << PB_SPEED) param_u16(PB_SPEED, 100);
    if (playback_mask & 1u << PB_SEEKABLE) param_u8(PB_SEEKABLE, 1);
    if (playback_mask & 1u << PB_APP_BUNDLE) param_str(PB_APP_BUNDLE, "org.q2muse.player");
    group_end();
    msg_send();
    if (media && art_id && art_going && !art_sent) art_setup();
}

/* Every half second while the car takes Now Playing: everything again on a new song or state, else the position
 * while playing (PodBox: without it the car's bar sits at 0:00), and paused when it moved (a seek); a stream's
 * none. */
/* ---- Up Next on iDrive: the playing queue as a list (docs/iphone-mode.md#the-library-on-idrive) ----
 * When the queue changes (the shim's qgen), once it has held still a second, an iPhone sends PB_QUEUE_LIST 1 and an
 * empty PB_QUEUE_LIST_ID, then the queue as a file transfer, [ID, 04, size, 00 03] and the songs' ids, 8 bytes each,
 * in the order they play (PodBox, from an iPhone in its car). A queue with a song outside the library (a card app's
 * stream), or longer than QLIST_MAX, is PB_QUEUE_LIST 0 and no list: the car then shows only the place in it. */

static void ql_announce(void) {
    static unsigned rids[QLIST_MAX];
    char t[96];
    int n = H->queue ? H->queue(rids, QLIST_MAX) : -1, whole = n > 0 && file_session;
    for (int i = 0; whole && i < n; ++i)
        if (!rids[i]) whole = 0;
    msg_start(0x5001), group_start(1), param_u8(PB_QUEUE_LIST, (u8)whole);
    if (whole) param(PB_QUEUE_LIST_ID, 0, 0);
    group_end(), msg_send();
    snprintf(t, sizeof t, "queue: %d songs, %s", n, whole ? "sent as a list" : "no list");
    H->log(t);
    if (!whole) return;
    if (ql_going && ql_id) file_send(ql_id, 0x02, 0, 0);
    ql_id = transfer_take();
    ql_len = 0;
    for (int i = 0; i < n; ++i) put_id8(ql + ql_len, LIB_KEY, rids[i]), ql_len += 8;
    ql_going = 1;
    u8 setup[10] = { 0, 0, 0, 0, ql_len >> 24, ql_len >> 16, ql_len >> 8, ql_len, 0, 3 };
    file_send(ql_id, 0x04, setup, sizeof setup);
}

/* A cover found since Now Playing went (q2_iap.c art_poll): all of it again, a new transfer with it. */
static void iap2_art_again(void) {
    if (now_playing) np_sent = 0, np_at = 0;
}

/* A new song's Now Playing waits for its cover's quick sources (Cover Flow's, the app's, one beside it: q2_iap.c
 * art_child), ART_HOLD_MS at most, so its first update names the cover; past that, or once the child goes on to an
 * ask or ffmpeg (iap2_art_slow), it goes with an empty one, and the cover follows (iap2_art_done). */
#define ART_HOLD_MS 1000
static long art_hold_at; /* since when Now Playing waits, 0 not */
void iap2_art_slow(void) {
    if (art_hold_at) np_at = 0; /* at the next tick, with no cover yet */
}
void iap2_art_done(int found) {
    if (art_hold_at || found) iap2_art_again();
}

static void poll_now_playing(long now) {
    struct iap2_np n;
    if (np_at && now - np_at < 500) return;
    np_at = now;
    memset(&n, 0, sizeof n);
    if (!H->now_playing || !H->now_playing(&n)) n.state = 0;
    int changed = !np_sent || np_hash(&n) != np_hash(&np_last) || n.state != np_last.state ||
                  n.len_ms != np_last.len_ms || n.live != np_last.live;
    if (changed && (media_mask & 1u << MI_ARTWORK) && !n.live && H->art_pending && H->art_pending(&n)) {
        if (!art_hold_at) art_hold_at = now | 1;
        if (now - art_hold_at < ART_HOLD_MS) {
            np_at = now - 450; /* looked at again in 50 ms */
            return;
        }
    }
    art_hold_at = 0;
    if (changed) send_now_playing(&n, 1);
    else if (!n.live && (n.state == 1 || n.pos_ms != np_last.pos_ms)) {
        msg_quiet = 1, ++quiet_pos;
        msg_start(0x5001), group_start(1), param_u32(PB_POSITION, n.pos_ms), group_end(), msg_send();
        msg_quiet = 0;
    }
    if ((playback_mask & 1u << PB_QUEUE_LIST) && n.qgen != np_last.qgen) ql_seen_at = now; /* the queue moved */
    np_last = n, np_sent = 1;
    if (ql_seen_at && now - ql_seen_at >= 1000) ql_seen_at = 0, ql_announce();
}

static void iap2_control_receive(const u8 *msg, unsigned len) {
    unsigned id = msg[4] << 8 | msg[5], plen = len - 6, n;
    const u8 *p = msg + 6, *v;
    /* whole: identification, and the car's Start*Updates, which list the attributes it wants (on 2026-11-22 the 160
     * bytes cut StartMediaLibraryUpdates' list of media item attributes, the question of a library item's cover) */
    log_msg(0, msg, len, id == 0x1D01 || id == 0x4C03 || id == 0x5000 ? 1100 : id == 0xAA01 || id == 0xAA03 ? 24 : 160);
    switch (id) {
    case 0xAA01:
        send_challenge(find(p, plen, 0, &n) ? n : 0);
        break;
    case 0xAA03:
        auth_due = due_in(20); /* an iPhone waits about 20 ms before accepting it (PodBox) */
        break;
    case 0xAA04:
        H->log("auth: the car says authentication failed");
        break;
    case 0x1D01:
        take_identification(p, plen);
        break;
    case 0x1D03:
        H->log("ident: the car rejected its own identification?");
        break;
    case 0xAE00:
        /* answered whatever the identification lists, as an iPhone does (PodBox's Lexus) */
        send_power(wanted(p, plen, 0));
        break;
    case 0xAE03:
        /* Trap (PodBox): CarPlayAvailability before this, or within a millisecond of it, and that car goes quiet
         * after StartHID; 50 ms after it, or at StartHID if that comes first, it goes on. */
        if ((v = find(p, plen, 0, &n)) && n == 2) car_current = get_be(v, 2);
        if (identified && car_takes(0x4300)) availability_due = due_in(50);
        break;
    case 0x4E03:
        /* an iPhone answers with the component and an empty parameter 1: no Bluetooth profile connected */
        if ((v = find(p, plen, 0, &n)) && n == 2) {
            msg_start(0x4E04), param_u16(0, get_be(v, 2)), param(1, 0, 0), msg_send();
        }
        break;
    case 0x4C00:
        send_library_info();
        break;
    case 0x4C03: {
        unsigned rn, wn;
        const u8 *rev = find(p, plen, 1, &rn), *w = find(p, plen, 2, &wn);
        send_library_updates(rev && rn && !rev[rn - 1] ? (const char *)rev : 0, wanted(w, wn, 0x0A0FFFFF));
        break;
    }
    case 0x4C05:
        ls.phase = LIB_IDLE, ls.x_state = 0;
        break;
    case 0x4C06:
        if (H->command) H->command("resume");
        break;
    case 0x4C07:
        play_items(p, plen);
        break;
    case 0x4C08:
        play_collection(p, plen);
        break;
    case 0x4C09:
        play_all();
        break;
    case 0xDA00:
        H->log("audio: the car asks for USB audio");
        if (H->rate) audio_hz = H->rate(); /* read only every RATE_IDLE_MS until now (q2_iap.c rate_poll) */
        send_audio_rate();
        set_audio(1);
        break;
    case 0xDA02:
        H->log("audio: the car stops USB audio");
        set_audio(0);
        break;
    case 0x5000:
        v = find(p, plen, 0, &n);
        media_mask = wanted(v, n, DEFAULT_MEDIA);
        v = find(p, plen, 1, &n);
        playback_mask = wanted(v, n, DEFAULT_PLAYBACK);
        now_playing = 1, np_sent = 0, np_at = 0;
        poll_now_playing(H->now());
        break;
    case 0x5002:
        now_playing = 0;
        break;
    case 0x5003:
        /* the car's seek: parameter 0, the position in ms */
        if ((v = find(p, plen, 0, &n)) && n == 4 && H->command) {
            char verb[32];
            snprintf(verb, sizeof verb, "seek %u", get_be(v, 4) / 1000);
            H->log(verb);
            H->command(verb);
        }
        break;
    case 0x6800: {
        char t[80];
        if ((v = find(p, plen, 4, &n))) parse_hid(v, n);
        snprintf(t, sizeof t, "hid: the car starts HID: %u buttons in its report descriptor", n_controls);
        H->log(t);
        memset(last_report, 0, sizeof last_report);
        if (availability_due) send_carplay_availability();
        break;
    }
    case 0x6802:
        if ((v = find(p, plen, 1, &n))) {
            char h[200];
            iap2_hex(h, sizeof h, v, n);
            iap2_log("hid: a button report", h);
            take_hid_report(v, n);
        }
        break;
    case 0x6803:
        n_controls = 0;
        break;
    default:
        break;
    }
}

/* ---- the clock ---- */

/* The link's health every health_ms while it is up (docs/iphone-mode.md#the-loop-s-steps), so a silence shows as it
 * grows: the reports the writer still holds, the packets queued and unacknowledged, how long since the Q2 sent, the
 * car sent anything and the car acknowledged something new, the packets dropped from a full queue, and the quiet
 * against its limit. */
static void link_health(long now, int bl) {
    char t[400];
    link_health_at = now;
    long quiet = waiting_since ? now - (heard_at > waiting_since ? heard_at : waiting_since) : 0;
    snprintf(t, sizeof t,
             "link: health: reports waiting %d, queued %u, unacknowledged %u, sent %ld ms ago, heard %ld ms ago, "
             "acknowledged %ld ms ago, dropped %u, quiet %ld of %ld ms; since the last, not logged: %u position updates,"
             " %u bare acks in, %u out",
             bl, q_count, rto ? count : 0, sent_at ? now - sent_at : -1L, heard_at ? now - heard_at : -1L,
             acked_at ? now - acked_at : -1L, dropped, quiet, link_quiet_ms, quiet_pos, quiet_in, quiet_out);
    H->log(t);
    quiet_pos = quiet_in = quiet_out = 0;
}

void iap2_tick(void) {
    long now = H->now();
    if (!seen) return;
    int bl = backlog();
    if (bl) wire_busy_at = now;
    /* The tries are counted only while the wire is clear: one held past every try's time is said once, as the reason
     * the count of tries cannot give the link up (q2muse-1125's 23 minutes) */
    if (link_up && count && rto && bl && slots[head].tries && !retries_held &&
        now - slots[head].sent > rto * (long)max_sends) {
        char t[160];
        snprintf(t, sizeof t, "link: retries held: packet %u unacknowledged %ld ms, %d reports still wait for the wire",
                 slots[head].packet[5], now - slots[head].sent, bl);
        H->log(t);
        retries_held = 1;
    }
    if (!bl) retries_held = 0;
    if (link_up && health_ms && now - link_health_at >= health_ms) link_health(now, bl);
    if (link_up && count && rto && !bl) {
        struct slot *s = &slots[head];
        long since = s->sent > wire_busy_at ? s->sent : wire_busy_at; /* when it can have reached the car */
        if (s->tries && now - since > rto) {
            if (s->tries >= max_sends) {
                iap2_lost("link: lost (a packet went unacknowledged through every retry)");
                return;
            }
            send_slot(s);
        }
    }
    /* something waiting (a packet queued or unacknowledged, or reports not yet taken) and nothing from the car since
     * it began, or since the car's last word, for link_quiet_ms (docs/iphone-mode.md#the-link-s-pace) */
    if (!link_up || !(q_count || (rto && count) || bl)) waiting_since = 0;
    else if (!waiting_since) waiting_since = now;
    if (waiting_since && now - (heard_at > waiting_since ? heard_at : waiting_since) >= link_quiet_ms) {
        iap2_lost("link: lost (nothing from the car with packets waiting)");
        return;
    }
    if (q_count) pump(); /* the wire has room again */
    if (link_up && unacked && cack && now - unacked_since >= cack) send_ctl(CTL_ACK, 0, 0, my_seq);
    if (auth_due && now >= auth_due) {
        auth_due = 0;
        send_empty(0xAA05);
        send_empty(0x1D00);
    }
    if (ident_due && now >= ident_due) {
        ident_due = 0;
        accept_identification();
    }
    if (availability_due && now >= availability_due) send_carplay_availability();
    scan_tick(now);
    if (ls.phase == LIB_READING) {
        int r = H->lib_ready();
        if (r < 0) ls.phase = LIB_IDLE, send_library_none();
        else if (r == 2) ls.phase = LIB_IDLE, library_unreadable();
        else if (r > 0) library_read();
    }
    lists_pump(now);
    lib_pump();
    if (identified && now_playing) poll_now_playing(now);
    pump();
}

/* The soonest anything here is due, in ms from now (0: at once), or -1 for nothing: what q2_iap.c's poll sleeps to,
 * besides the car's requests, the writer's reports gone and the children's pipes, which wake it themselves. Each
 * deadline is one iap2_tick tests: a packet's retry, the retries held, the cumulative ack, the quiet's limit, the
 * health line, authentication's and identification's answers, CarPlay's availability, a held button's scan, a list's
 * transfer the car never started, Now Playing's look and Up Next's announcement. */
static long due_min(long w, long now, long at) {
    long d = at - now;
    if (d < 0) d = 0;
    return w < 0 || d < w ? d : w;
}
long iap2_due(long now) {
    long w = -1;
    if (!seen) return -1;
    if (link_up && count && rto) {
        long since = slots[head].sent > wire_busy_at ? slots[head].sent : wire_busy_at;
        if (slots[head].tries) w = due_min(w, now, since + rto + 1);
        if (slots[head].tries && !retries_held) w = due_min(w, now, slots[head].sent + rto * (long)max_sends + 1);
    }
    if (waiting_since) w = due_min(w, now, (heard_at > waiting_since ? heard_at : waiting_since) + link_quiet_ms);
    if (link_up && health_ms) w = due_min(w, now, link_health_at + health_ms);
    if (link_up && unacked && cack) w = due_min(w, now, unacked_since + cack);
    if (auth_due) w = due_min(w, now, auth_due);
    if (ident_due) w = due_min(w, now, ident_due);
    if (availability_due) w = due_min(w, now, availability_due);
    if (held) w = due_min(w, now, scanning ? scan_at + SCAN_KEEP_MS : held_at + HOLD_MS);
    if (ls.phase == LIB_LISTS && ls.x_state == 1) w = due_min(w, now, ls.x_since + LIST_WAIT_MS + 1);
    if (identified && now_playing) w = due_min(w, now, np_at + 500);
    if (ql_seen_at) w = due_min(w, now, ql_seen_at + 1000);
    if (q_count && link_up && (!rto || count < window)) w = 0; /* the queue has room to go */
    return w;
}

/* A new connection: everything forgotten, the detect awaited. */
void iap2_connect(const struct iap2_host *h) {
    H = h;
    seen = 0;
    my_seq = 1, peer_seq = 0, head = 0;
    rx_len = 0, rx_partial = 0;
    reset_link();
}

/* A session's end (q2_iap.c: no ENABLE or RESUME within the gap): the link forgotten with it, the car's audio file
 * removed (iap2_control_reset), so a bus that comes back without the car's detect gets nothing of the old link. */
void iap2_session_end(void) {
    if (seen) H->log("link: forgotten with its session");
    iap2_connect(H);
}
