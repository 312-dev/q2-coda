/* iAP2 with the Q2 in an iPhone's place, over the iPhone gadget's HID interface: milestone M1 of route 1
 * (docs/iphone-mode.md#iap2). q2_iap.c feeds it the car's output reports (SET_REPORT data) and sends its input
 * reports (iap2_link.h); nothing in the iAP2 files touches a file or the USB itself.
 *
 * In order: the HID transport (reports of [id][link control][data]: 00 whole, 02 first, 03 middle, 01 last); the link
 * layer (packets FF 5A, a 9-byte header, a checksummed payload; the car's SYN answered SYN+ACK with its own
 * parameters, the length capped) and its send queue; and the clock, iAP2's tick each pass of q2_iap.c's loop and its
 * next deadline, and a connection's start and end. The control session over it is iap2_session.c's. Part of the
 * PodBox port (iap2.h). */
#include "q2_iap_sys.h"
#include "iap2.h"
#include "iap2_link.h"
#include "iap2_names.h"
#include "iap2_hid.h"
#include "iap2_msg.h"
#include "iap2_xfer.h"
#include "iap2_library.h"
#include "iap2_nowplaying.h"
#include "iap2_session.h"

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
static unsigned quiet_in, quiet_out;
unsigned quiet_pos;
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
