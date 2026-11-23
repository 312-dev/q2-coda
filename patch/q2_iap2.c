/* iAP2 with the Q2 in an iPhone's place, over the iPhone gadget's HID interface: milestone M1 of route 1
 * (docs/iphone-mode.md#iap2). Included by q2_iap.c, which feeds it the car's output reports (SET_REPORT data)
 * and sends its input reports; nothing here touches a file or the USB itself.
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

#define IAP2_SOP1 0xFF
#define IAP2_SOP2 0x5A
#define CTL_SYN 0x80
#define CTL_ACK 0x40
#define CTL_EAK 0x20
#define CTL_RST 0x10
#define HEADER_LEN 9
#define SYN_FIXED 10   /* link parameters ahead of the session list */
#define MAX_PACKET 1024 /* the longest the Q2 sends: the car's certificate, about 950 bytes, fits one */
#define RX_PACKET 8192  /* the longest it takes, offered in the SYN+ACK (an iPhone offers 0xFFFF) */
#define SLOTS 8
/* The wire's pace (docs/iphone-mode.md#the-link-s-pace): the BMW takes input reports from an interrupt endpoint, 64
 * bytes every 8 ms, so a 1,024-byte packet is about 170 ms on the wire and its window of 5 most of a second, longer
 * than its 200 ms retransmission timer and its 10 tries. So a packet goes to the wire only while no more than
 * BACKLOG_MAX reports wait for it (a bare acknowledgement always goes: one report), a packet is sent again only after
 * the wire has been idle for the retransmission time, and the bulk (library updates, covers, the queue list) goes in
 * packets of at most BULK_MAX bytes, so an acknowledgement waits behind no more than about 130 ms of it, well inside
 * the car's 200 ms. */
#define BACKLOG_MAX 4
#define BULK_MAX 768
/* The link lost when the Q2 has packets or reports waiting and the car has sent nothing for this long (iap2_tick):
 * the BMW acknowledges within 22 ms and gives a packet up after 10 tries of 200 ms, so 10 s of quiet with something
 * waiting is a car that has stopped. q2muse-1125: the car stopped taking input reports, pacing then held every packet
 * back, the tries were never counted (they wait for a clear wire) and the link stayed up, silent, for 23 minutes. */
static long link_quiet_ms = 10000;

#include "iap2.h"

static const struct iap2_host *H;
static const u8 iap2_probe[] = { 0xFF, 0x55, 0x02, 0x00, 0xEE, 0x10 };

/* ---- logging ---- */

static const struct {
    u16 id;
    const char *name;
} iap2_names[] = {
    { 0x0B00, "StartBluetoothPairing" }, { 0x0B01, "BluetoothPairingAccessoryInformation" },
    { 0x0B02, "BluetoothPairingStatus" }, { 0x0B03, "StopBluetoothPairing" },
    { 0x1D00, "StartIdentification" }, { 0x1D01, "IdentificationInformation" },
    { 0x1D02, "IdentificationAccepted" }, { 0x1D03, "IdentificationRejected" },
    { 0x1D05, "CancelIdentification" }, { 0x1D06, "IdentificationInformationUpdate" },
    { 0x4154, "StartCallStateUpdates" }, { 0x4156, "StopCallStateUpdates" },
    { 0x4157, "StartCommunicationsUpdates" }, { 0x4159, "StopCommunicationsUpdates" },
    { 0x4300, "CarPlayAvailability" }, { 0x4301, "CarPlayStartSession" },
    { 0x4C00, "StartMediaLibraryInformation" }, { 0x4C01, "MediaLibraryInformation" },
    { 0x4C02, "StopMediaLibraryInformation" }, { 0x4C03, "StartMediaLibraryUpdates" },
    { 0x4C04, "MediaLibraryUpdate" }, { 0x4C05, "StopMediaLibraryUpdates" },
    { 0x4C06, "PlayMediaLibraryCurrentSelection" }, { 0x4C07, "PlayMediaLibraryItems" },
    { 0x4C08, "PlayMediaLibraryCollection" }, { 0x4C09, "PlayMediaLibrarySpecial" },
    { 0x4E01, "BluetoothComponentInformation" }, { 0x4E03, "StartBluetoothConnectionUpdates" },
    { 0x4E04, "BluetoothConnectionUpdate" }, { 0x4E05, "StopBluetoothConnectionUpdates" },
    { 0x4E09, "DeviceInformationUpdate" }, { 0x4E0A, "DeviceLanguageUpdate" }, { 0x4E0B, "DeviceTimeUpdate" },
    { 0x4E0C, "DeviceUUIDUpdate" }, { 0x4E0E, "DeviceTransportIdentifierNotification" },
    { 0x5000, "StartNowPlayingUpdates" }, { 0x5001, "NowPlayingUpdate" }, { 0x5002, "StopNowPlayingUpdates" },
    { 0x5003, "SetNowPlayingInformation" }, { 0x5200, "StartRouteGuidanceUpdates" },
    { 0x5203, "StopRouteGuidanceUpdates" }, { 0x6800, "StartHID" }, { 0x6801, "DeviceHIDReport" },
    { 0x6802, "AccessoryHIDReport" }, { 0x6803, "StopHID" }, { 0x6806, "StartNativeHID" },
    { 0x6807, "HIDComponentUpdate" }, { 0xAA00, "RequestAuthenticationCertificate" },
    { 0xAA01, "AuthenticationCertificate" }, { 0xAA02, "RequestAuthenticationChallengeResponse" },
    { 0xAA03, "AuthenticationResponse" }, { 0xAA04, "AuthenticationFailed" },
    { 0xAA05, "AuthenticationSucceeded" }, { 0xAA06, "AccessoryAuthenticationSerialNumber" },
    { 0xAE00, "StartPowerUpdates" }, { 0xAE01, "PowerUpdate" }, { 0xAE02, "StopPowerUpdates" },
    { 0xAE03, "PowerSourceUpdate" }, { 0xAE05, "AccessoryPowerUpdate" },
    { 0xDA00, "StartUSBDeviceModeAudio" }, { 0xDA01, "USBDeviceModeAudioInformation" },
    { 0xDA02, "StopUSBDeviceModeAudio" }, { 0xEA00, "StartExternalAccessoryProtocolSession" },
    { 0xEA01, "StopExternalAccessoryProtocolSession" }, { 0xEA02, "RequestAppLaunch" },
    { 0xFFFA, "StartLocationInformation" }, { 0xFFFB, "LocationInformation" }, { 0xFFFC, "StopLocationInformation" },
};

static const char *iap2_name(unsigned id) {
    for (unsigned i = 0; i < sizeof iap2_names / sizeof *iap2_names; ++i)
        if (iap2_names[i].id == id) return iap2_names[i].name;
    return "?";
}

static void iap2_hex(char *out, unsigned cap, const u8 *p, unsigned n) {
    static const char d[] = "0123456789abcdef";
    unsigned o = 0, i = 0;
    for (; i < n && o + 6 < cap; ++i) {
        out[o++] = d[p[i] >> 4], out[o++] = d[p[i] & 15];
        if (i + 1 < n) out[o++] = ' ';
    }
    if (i < n) out[o++] = '.', out[o++] = '.'; /* cut: o + 6 < cap left room */
    out[o] = 0;
}

static void iap2_log(const char *fmt_label, const char *detail) {
    char line[2600];
    snprintf(line, sizeof line, "%s%s%s", fmt_label, detail && *detail ? " " : "", detail ? detail : "");
    H->log(line);
}

/* A message each way, its name and up to max bytes of its parameters in hex. */
static void log_msg(int from_q2, const u8 *m, unsigned len, unsigned max) {
    unsigned id = m[4] << 8 | m[5];
    char h[1200], head[160];
    iap2_hex(h, sizeof h, m + 6, len - 6 < max ? len - 6 : max);
    snprintf(head, sizeof head, "%s %04x %s (%u bytes)", from_q2 ? "msg >" : "msg <", id, iap2_name(id), len);
    iap2_log(head, h);
}

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

static int seen, synced, link_up;
static u8 my_seq, syn_seq, peer_seq, last_ack, control_session, file_session;
static u8 syn_ack[48];
static unsigned syn_ack_len, window, peer_max, max_sends, max_cacks, unacked;
static long rto, cack, unacked_since; /* milliseconds; rto 0: never sent again */
static long wire_busy_at; /* the last time reports were still waiting for the wire */
static long heard_at, waiting_since; /* the car's last output report; since when something has waited (iap2_tick) */
/* acked_at: the car's last new acknowledgement; link_health_at: link_health's last line. Named apart from q2_iap.c's
 * audio health_at: q2_iap.c includes this file, and two file-scope `static long health_at;` there are one variable,
 * which the audio check set every second, so no link line came on the 2026-11-22 drive (q2-iap with --state). */
static long acked_at, link_health_at;
static long health_ms = 5000;    /* --health-ms; 0 none */
static unsigned dropped;         /* packets dropped from a full queue, this link */
static int retries_held, drop_said;
static int backlog(void) { return H && H->backlog ? H->backlog() : 0; }

struct slot {
    u8 packet[MAX_PACKET];
    unsigned len, tries;
    long sent;
    u8 quiet; /* a position update's: its first sending not logged */
};
static struct slot slots[SLOTS];
static unsigned head, count;
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
static int msg_quiet, trace_quiet;
static unsigned quiet_pos, quiet_in, quiet_out;
/* An output report that is the car's bare acknowledgement whole: [id][00, a whole packet][FF 5A 00 09 40 ...] */
static int iap2_bare_ack(const u8 *r, unsigned n) {
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
static unsigned q_head, q_count;
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
static void iap2_lost(const char *why) {
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

static void iap2_file(const u8 *d, unsigned n);

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
static int iap2_report(const u8 *r, unsigned len) {
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

/* ---- the control session: building and reading messages ---- */

static struct {
    u8 b[MAX_PACKET];
    unsigned len, group;
    u16 id;
    int ok;
} m;

static void msg_start(u16 id) {
    m.b[0] = 0x40, m.b[1] = 0x40, m.b[4] = id >> 8, m.b[5] = id;
    m.len = 6, m.id = id, m.ok = 1;
}

static void param(u16 id, const void *data, unsigned n) {
    if (m.len + 4 + n > peer_max - HEADER_LEN - 1 || m.len + 4 + n > sizeof m.b) {
        m.ok = 0;
        return;
    }
    m.b[m.len] = (4 + n) >> 8, m.b[m.len + 1] = 4 + n, m.b[m.len + 2] = id >> 8, m.b[m.len + 3] = id;
    if (n) memcpy(m.b + m.len + 4, data, n);
    m.len += 4 + n;
}
static void param_u8(u16 id, u8 v) { param(id, &v, 1); }
static void param_u16(u16 id, u16 v) {
    u8 b[2] = { v >> 8, v };
    param(id, b, 2);
}
static void param_u32(u16 id, u32 v) {
    u8 b[4] = { v >> 24, v >> 16, v >> 8, v };
    param(id, b, 4);
}
static void param_u64(u16 id, u32 hi, u32 lo) {
    u8 b[8] = { hi >> 24, hi >> 16, hi >> 8, hi, lo >> 24, lo >> 16, lo >> 8, lo };
    param(id, b, 8);
}
/* NUL-terminated, cut at 200 bytes on a character boundary. */
static void param_str(u16 id, const char *s) {
    unsigned n = strlen(s);
    char t[201];
    if (n > 200) {
        n = 200;
        while (n && ((u8)s[n] & 0xC0) == 0x80) --n;
    }
    memcpy(t, s, n), t[n] = 0;
    param(id, t, n + 1);
}
static void group_start(u16 id) {
    m.group = m.len;
    param(id, 0, 0);
}
static void group_end(void) {
    unsigned n = m.len - m.group;
    m.b[m.group] = n >> 8, m.b[m.group + 1] = n;
}

/* A packet's payload to go: at once while the window has room, else in turn (pump). */
static void enqueue(u8 session, const u8 *b, unsigned n) {
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

/* One message to a packet: PodBox's car reads only the first message of a packet. */
static void msg_send(void) {
    if (!m.ok || !link_up) {
        char t[120];
        snprintf(t, sizeof t, "msg > %04x %s not sent (%s)", m.id, iap2_name(m.id), m.ok ? "no link" : "too long");
        H->log(t);
        return;
    }
    m.b[2] = m.len >> 8, m.b[3] = m.len;
    if (!msg_quiet) log_msg(1, m.b, m.len, 96);
    enqueue(control_session, m.b, m.len);
}

static void send_empty(u16 id) {
    msg_start(id);
    msg_send();
}

static const u8 *find(const u8 *data, unsigned len, u16 id, unsigned *n) {
    unsigned off = 0;
    while (off + 4 <= len) {
        unsigned plen = data[off] << 8 | data[off + 1];
        if (plen < 4 || off + plen > len) break;
        if ((data[off + 2] << 8 | data[off + 3]) == id) {
            *n = plen - 4;
            return data + off + 4;
        }
        off += plen;
    }
    return 0;
}

static u32 get_be(const u8 *p, unsigned n) {
    u32 v = 0;
    while (n--) v = v << 8 | *p++;
    return v;
}

/* ---- the control session: the conversation ---- */

static u16 car_receives[96];
static unsigned car_receives_n;
static int identified, now_playing, bt_component = -1;
static int ever_identified; /* this cable: identified kept after the link is lost, for the summary */
static long auth_due, ident_due, availability_due, np_at; /* 0: not due */
static unsigned car_current;
static u32 media_mask, playback_mask, rnd;
static struct iap2_np np_last;
static int np_sent, audio_on;
static char car_name[64]; /* the car's identification name, for the status file (q2_iap.c state_write) */

/* ---- the car's buttons: its StartHID report descriptor's consumer controls (PodBox parse_hid, one-bit ones) ---- */

#define MAX_CONTROLS 24
static struct {
    u8 report;  /* report id, or 0 */
    u16 bit;    /* from the report's data */
    u16 usage;
} controls[MAX_CONTROLS];
static unsigned n_controls, report_ids;
static u8 last_report[16];

static void parse_hid(const u8 *d, unsigned len) {
    u32 page = 0, size = 0, count_ = 0, id = 0, usages[16], bits[8] = { 0 };
    unsigned n_usages = 0;
    n_controls = report_ids = 0;
    for (unsigned i = 0; i < len;) {
        u8 prefix = d[i];
        if (prefix == 0xFE) { /* a long item */
            i += 3 + (i + 1 < len ? d[i + 1] : 0);
            continue;
        }
        unsigned n = (prefix & 3) == 3 ? 4 : prefix & 3;
        if (i + 1 + n > len) break;
        u32 v = n == 4 ? d[i + 1] | d[i + 2] << 8 | (u32)d[i + 3] << 16 | (u32)d[i + 4] << 24
              : n == 2 ? (u32)(d[i + 1] | d[i + 2] << 8)
              : n == 1 ? d[i + 1] : 0u;
        i += 1 + n;
        switch (prefix & 0xFC) {
        case 0x04: page = v; break;
        case 0x74: size = v; break;
        case 0x84: id = v, report_ids = 1; break;
        case 0x94: count_ = v; break;
        case 0x08:
            if (n_usages < 16) usages[n_usages++] = n == 4 ? v : page << 16 | v;
            break;
        case 0x80: { /* Input: each one-bit variable on the consumer page, as its usage */
            u32 *at = &bits[id & 7];
            if (!(v & 1) && (v & 2) && page == 0x0C && size == 1)
                for (u32 k = 0; k < count_ && k < n_usages && n_controls < MAX_CONTROLS; ++k)
                    if (usages[k] >> 16 == 0x0C)
                        controls[n_controls].report = id, controls[n_controls].bit = *at + k,
                        controls[n_controls++].usage = usages[k] & 0xFFFF;
            *at += size * count_;
            n_usages = 0; /* a main item clears the local items */
            break;
        }
        case 0x90: case 0xB0: case 0xA0: case 0xC0:
            n_usages = 0;
            break;
        }
    }
}

/* A control pressed: what the Q2's own remotes send (patch/coda_rc_shim.c cr_command's verbs). */
static void press(unsigned usage) {
    char v[32], t[96];
    struct iap2_np now;
    memset(&now, 0, sizeof now);
    if (H->now_playing) H->now_playing(&now); /* shuffle and repeat as they are now, not as last sent */
    const char *verb = usage == 0xB0 ? "resume" : usage == 0xB1 ? "pause" : usage == 0xCD ? "toggle"
                     : usage == 0xB5 ? "next" : usage == 0xB6 ? "previous" : 0;
    if (!verb && usage == 0xB9) snprintf(v, sizeof v, "mode %d %d", !now.shuffle, now.repeat), verb = v;
    if (!verb && usage == 0xBC) snprintf(v, sizeof v, "mode %d %d", now.shuffle, (now.repeat + 1) % 3), verb = v;
    snprintf(t, sizeof t, "hid: usage %02x pressed: %s", usage, verb ? verb : "not mapped");
    H->log(t);
    if (verb && H->command) H->command(verb);
}

static unsigned report_bit(const u8 *r, unsigned len, unsigned bit) { return bit / 8 < len && (r[bit / 8] >> (bit % 8) & 1); }

/* Hold to scan, as an iPod's wheel buttons: the BMW's descriptor has no fast-forward or rewind usage, only Next and
 * Previous. Next or Previous is acted on at its release: up within HOLD_MS, a skip as before (the car's taps were up
 * 13 to 120 ms after the press, 2026-11-20); held past it, the Q2's own scan of a held button (seek.c seek_car, heard
 * at 2, 4 and 8 times and then in growing jumps), started with the shim's "scan 1" or "scan -1", said again every
 * SCAN_KEEP_MS while the button stays down (seek.c lets a car's scan go after 2 s without it), and "scan 0" at the
 * release; the song plays on from where the scan reached. The first drive's scan, a seek every 500 ms, came out uneven
 * ("pretty inconsistent", 2026-11-20): each seek a mute, a drop and a fresh decode, read a loop pass apart. */
#define HOLD_MS 500
#define SCAN_KEEP_MS 500
static unsigned held; /* Next's or Previous's usage while it is down, else 0 */
static long held_at, scan_at;
static int scanning;

static void scan_say(long now, const char *verb) {
    char t[96];
    scan_at = now;
    snprintf(t, sizeof t, "hid: usage %02x held %ld ms: %s", held, now - held_at, verb);
    H->log(t);
    if (H->command) H->command(verb);
}

/* From iap2_tick: a hold that passed HOLD_MS starts the scan, and every SCAN_KEEP_MS says it is still held. */
static void scan_tick(long now) {
    if (!held || now - held_at < HOLD_MS) return;
    if (!scanning || now - scan_at >= SCAN_KEEP_MS) scanning = 1, scan_say(now, held == 0xB5 ? "scan 1" : "scan -1");
}

static void release(unsigned usage) {
    char t[96];
    if (usage != held) return;
    long ms = H->now() - held_at;
    if (!scanning) press(usage); /* a tap: the skip */
    else snprintf(t, sizeof t, "hid: usage %02x released after %ld ms: the scan ends", usage, ms), H->log(t),
                                                                                                  scan_say(H->now(), "scan 0");
    held = 0, scanning = 0;
}

/* Each control down now and up in the last report, and up now and down in it. */
static void take_hid_report(const u8 *r, unsigned len) {
    u8 id = 0;
    if (report_ids && len) id = *r++, --len;
    if (len > sizeof last_report) len = sizeof last_report;
    for (unsigned i = 0; i < n_controls; ++i) {
        if (controls[i].report != id) continue;
        unsigned now = report_bit(r, len, controls[i].bit), was = report_bit(last_report, len, controls[i].bit),
                 usage = controls[i].usage;
        if (now && !was && (usage == 0xB5 || usage == 0xB6)) {
            char t[64];
            held = usage, held_at = H->now(), scanning = 0;
            snprintf(t, sizeof t, "hid: usage %02x down", usage);
            H->log(t);
        } else if (now && !was) {
            press(usage);
        } else if (!now && was) {
            release(usage);
        }
    }
    memset(last_report, 0, sizeof last_report);
    memcpy(last_report, r, len);
}

static void set_audio(int on) {
    if (on == audio_on) return;
    audio_on = on;
    H->log(on ? "audio: on (the player plays into the car)" : "audio: off");
    if (H->audio) H->audio(on);
}

/* ---- artwork, on the file transfer session (PodBox): the song's attribute 26 names a transfer; the Q2 sets it
 * up as [ID, 04, an 8-byte size, 00 02 (artwork)]; the car answers [ID, 01] to go; the JPEG follows as [ID, op,
 * bytes], op C0 the only piece, else 80 the first, 00 the middle ones and 40 the last; [ID, 02] cancels either way.
 * An empty one (size 0) when there is no cover: PodBox's car waits otherwise. ---- */

#define ART_MAX 65536
static u8 art[ART_MAX];
static unsigned art_len, art_sent;
static u8 art_id, next_transfer = 0x80;
static int art_going;

/* Up Next's list on its way (ql_announce) */
#define QLIST_MAX 2000
static u8 ql[QLIST_MAX * 8], ql_id;
static unsigned ql_len, ql_going;
static long ql_seen_at;

static void file_send(u8 id, u8 op, const u8 *d, unsigned n) {
    u8 b[MAX_PACKET];
    if (!file_session || n + 2 > sizeof b) return;
    b[0] = id, b[1] = op;
    if (n) memcpy(b + 2, d, n);
    enqueue(file_session, b, n + 2);
}

/* A file transfer's pieces: the bytes a piece holds (the car's packet or BULK_MAX, less the header, its checksum and
 * the piece's id and op; MIN_PEER_MAX keeps it above 100), each piece's op (C0 the only, 80 the first, 00 one in the
 * middle, 40 the last), a new transfer's id (80 to FF, round), and a song's 8-byte id */
static unsigned piece_room(void) { return (peer_max < BULK_MAX ? peer_max : BULK_MAX) - HEADER_LEN - 1 - 2; }
static u8 piece_op(int first, int last) { return first && last ? 0xC0 : first ? 0x80 : last ? 0x40 : 0x00; }
static void file_pieces(u8 id, const u8 *d, unsigned len, unsigned *sent, unsigned room) {
    while (*sent < len) {
        unsigned k = len - *sent < room ? len - *sent : room;
        file_send(id, piece_op(*sent == 0, *sent + k == len), d + *sent, k);
        *sent += k;
    }
}
static u8 transfer_take(void) {
    u8 id = next_transfer;
    next_transfer = id == 0xFF ? 0x80 : id + 1;
    return id;
}
static void put_id8(u8 *p, u32 hi, u32 lo) {
    for (int b = 0; b < 4; ++b) p[b] = (u8)(hi >> (24 - 8 * b)), p[4 + b] = (u8)(lo >> (24 - 8 * b));
}

/* A baseline JPEG as PodBox's car needed it: each quantisation and Huffman table in a segment of its own, the bytes
 * otherwise as they are. 0 for anything else (a progressive JPEG, a PNG, a broken file). */
static unsigned jpeg_tables_apart(const u8 *in, unsigned n, u8 *out, unsigned cap) {
    unsigned i = 2, o = 2, baseline = 0;
    if (n < 4 || in[0] != 0xFF || in[1] != 0xD8 || cap < 2) return 0;
    out[0] = 0xFF, out[1] = 0xD8;
    while (i + 4 <= n) {
        if (in[i] != 0xFF) return 0;
        u8 mk = in[i + 1];
        unsigned seg = in[i + 2] << 8 | in[i + 3];
        if (seg < 2 || i + 2 + seg > n) return 0;
        if (mk == 0xC0 || mk == 0xC1) baseline = 1;
        if (mk == 0xC2) return 0;
        if (mk == 0xDB || mk == 0xC4) {
            unsigned at = i + 4, end = i + 2 + seg;
            while (at < end) {
                unsigned t = mk == 0xDB ? 1 + ((in[at] >> 4) ? 128 : 64) : 0;
                if (mk == 0xC4) {
                    if (at + 17 > end) return 0;
                    t = 17;
                    for (unsigned k = 1; k <= 16; ++k) t += in[at + k];
                }
                if (at + t > end || o + 4 + t > cap) return 0;
                out[o++] = 0xFF, out[o++] = mk, out[o++] = (t + 2) >> 8, out[o++] = t + 2;
                memcpy(out + o, in + at, t), o += t, at += t;
            }
        } else {
            if (o + 2 + seg > cap) return 0;
            memcpy(out + o, in + i, 2 + seg), o += 2 + seg;
        }
        i += 2 + seg;
        if (mk == 0xDA) { /* the scan, to the end */
            if (o + (n - i) > cap) return 0;
            memcpy(out + o, in + i, n - i), o += n - i;
            return baseline ? o : 0;
        }
    }
    return 0;
}

/* A new song's transfer, the last cancelled if it still went; its setup goes after the message naming it. */
static u8 art_start(const struct iap2_np *n) {
    static u8 raw[ART_MAX];
    char t[120];
    if (!file_session) return 0;
    if (art_going && art_id) file_send(art_id, 0x02, 0, 0);
    art_id = transfer_take();
    /* a stream has no cover, but is named one, empty: with none named the car keeps the last song's */
    unsigned got = H->artwork && !n->live ? H->artwork(n, raw, sizeof raw) : 0;
    art_len = got ? jpeg_tables_apart(raw, got, art, sizeof art) : 0;
    art_sent = 0, art_going = 1;
    snprintf(t, sizeof t, "art: transfer %02x, %u bytes (%u as found)", art_id, art_len, got);
    H->log(t);
    return art_id;
}

static void art_setup(void) {
    u8 setup[10] = { 0, 0, 0, 0, art_len >> 24, art_len >> 16, art_len >> 8, art_len, 0, 2 };
    file_send(art_id, 0x04, setup, sizeof setup);
}

static int list_transfer_word(const u8 *d);

/* The car's answers on the file transfer session. */
static void iap2_file(const u8 *d, unsigned n) {
    char t[96];
    if (n < 2) return;
    snprintf(t, sizeof t, "file < transfer %02x op %02x (%u bytes)", d[0], d[1], n);
    H->log(t);
    if (list_transfer_word(d)) return;
    if (d[0] == ql_id && ql_going && d[1] == 0x01) { /* the queue list: every piece */
        unsigned sent = 0;
        file_pieces(ql_id, ql, ql_len, &sent, piece_room() - piece_room() % 8);
        return;
    }
    if (d[0] == ql_id && (d[1] == 0x02 || d[1] == 0x05)) {
        ql_going = 0;
        return;
    }
    if (d[0] != art_id || !art_going) return;
    if (d[1] == 0x01) { /* go: every piece, as the window allows */
        if (!art_len) file_send(art_id, 0xC0, 0, 0);
        file_pieces(art_id, art, art_len, &art_sent, piece_room());
    } else if (d[1] == 0x02 || d[1] == 0x05) {
        art_going = 0;
        H->log(d[1] == 0x05 ? "art: the car took it" : "art: the car cancelled it");
    }
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

static void lib_reset(void);
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

/* The library: the Q2's own, by name and ID (its songs and lists follow in send_library_updates). */
static void send_library_info(void) {
    msg_start(0x4C01);
    group_start(0), param_str(0, H->name), param_str(1, H->library_id), param_u8(2, 0), group_end();
    msg_send();
}
/* ---- the library on iDrive (docs/iphone-mode.md#the-library-on-idrive) ----
 *
 * To StartMediaLibraryUpdates (4C03: 1 the revision the car holds, 2 the item properties it wants) an iPhone's
 * answers, as PodBox sends them: 8 00, 9 01 (it can play all songs), then MediaLibraryUpdates (4C04) with the songs
 * as items (parameter 2, a group each) and the lists as playlists (parameter 3), as many to a packet as it holds, the
 * first opening with 6 (a reset) when the car's copy is not built on; each carries a revision and the progress (7).
 * iDrive shows what an update with progress 100 completes (PodBox: its BMW reloads its library at each one, and reads
 * on while 100 is held back), and the whole library takes minutes on the BMW's wire (about 6 KB a second:
 * docs/iphone-mode.md#the-link-s-pace), so a first update goes ahead and completes within seconds: up to FIRST_MAX
 * songs (the queue's from the playing song on, then the lists' in order) and every list's record, each list's songs
 * then following as a file transfer (lists_pump). The whole push follows, completing a checkpoint every
 * CHECKPOINT_MS, and the last update the revision itself, 100, and the lists' records again (their songs are all in
 * the car by then). Until the last, the revision is a partial one naming where the push has reached ("q2p." for the
 * whole library, "q2d." for changes, then the revision and the songs gone so far): a car that comes back holding one
 * of this revision's resumes from there with no reset, so a 9,199-song library goes over several short plugs. A car
 * that holds the revision gets the lists alone; one that holds the revision this Q2 last sent it whole
 * (lib->sent_rev) gets only the songs added or changed since and, as 4 each, the ids of those gone, with no reset.
 * Updates go only while nothing else waits to be sent and the window has a slot to spare, so the car's buttons and
 * Now Playing are never behind the library. Each song's id is LIB_KEY's with its rid, and Now Playing sends the same
 * id, so the car knows the song it plays; an album's, an artist's, an album artist's and a genre's ids are their
 * names' hashes under their own high word, and a list's its id (stock's playlist id, or LIST_FAVORITES and
 * LIST_RECENT) under LIB_LIST. */
#define LIB_KEY 0x51320001u
#define LIB_ALBUM 0x5132000Au
#define LIB_ARTIST 0x5132000Bu
#define LIB_AARTIST 0x5132000Cu
#define LIB_GENRE 0x5132000Du
#define LIB_COMPOSER 0x5132000Eu
#define LIB_LIST 0x5132000Fu
#define FIRST_MAX 150            /* songs in the first update: about 7 s of the BMW's wire */
#define FIRST_QUEUE 50           /* of them, the queue's from the playing song on */
#define CHECKPOINT_FIRST_MS 5000 /* a push with no first update of its own completes one this soon */
#define CHECKPOINT_MS 60000      /* and then one a minute */
#define LISTS_MAX 128
#define LIST_WAIT_MS 5000 /* a list's transfer the car has not started in this long is given up */
enum { LIB_IDLE, LIB_FIRST, LIB_LISTS, LIB_ITEMS, LIB_GONE, LIB_FINAL, LIB_READING };
static struct {
    int phase, after, diff, reset, held; /* after: the phase once the lists' transfers end; held: the car has rev */
    unsigned next, gone, mask, items, updates, completes;
    unsigned nf, fi, rec, xi, x_sent; /* the first set's size and cursor; the next record; the list in transfer */
    unsigned first[FIRST_MAX], sorted[FIRST_MAX]; /* the first set's song indexes, in sending order and sorted */
    u8 x_id, x_state, list_ids[LISTS_MAX];         /* x_state: 0 none, 1 set up, 2 its songs going */
    long started, complete_at, x_since;
} ls;

static const char *lib_str(unsigned off) { return H->lib->pool + off; }

/* The update being built has no room for one more item (an item's group with its id, and the 5 bytes the update ends
 * with), in a packet of BULK_MAX or the car's maximum */
static int msg_full(void) {
    return !m.ok || m.len + 4 + 64 + 5 > BULK_MAX - HEADER_LEN - 1 || m.len + 4 + 64 + 5 > peer_max - HEADER_LEN - 1;
}

/* The first of n records (stride bytes apart, each starting with an unsigned key, sorted by it) whose key is not
 * below key: its index, n when there is none */
static unsigned lower_key(const void *base, unsigned n, unsigned stride, unsigned key) {
    unsigned lo = 0, hi = n;
    while (lo < hi) {
        unsigned mid = (lo + hi) / 2, k;
        memcpy(&k, (const u8 *)base + (size_t)mid * stride, sizeof k);
        if (k < key) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

static void lib_named(unsigned mask, unsigned id_param, u32 hi, u32 id, const char *name) {
    if (!*name) return;
    if (mask & 1u << id_param) param_u64(id_param, hi, id);
    if (mask & 1u << (id_param + 1)) param_str(id_param + 1, name);
}

static void lib_item(const struct iap2_track *t, unsigned mask) {
    group_start(2);
    param_u64(0, LIB_KEY, t->rid);
    if (mask & 1u << 1) param_str(1, lib_str(t->title));
    if (mask & 1u << 2) param_u8(2, 0);
    if ((mask & 1u << 4) && t->ms) param_u32(4, t->ms);
    lib_named(mask, 5, LIB_ALBUM, t->album_id, lib_str(t->album));
    if ((mask & 1u << 7) && t->track) param_u16(7, t->track);
    if ((mask & 1u << 9) && t->disc) param_u16(9, t->disc);
    lib_named(mask, 11, LIB_ARTIST, t->artist_id, lib_str(t->artist));
    lib_named(mask, 13, LIB_AARTIST, t->aartist_id, lib_str(t->aartist));
    lib_named(mask, 15, LIB_GENRE, t->genre_id, lib_str(t->genre));
    lib_named(mask, 17, LIB_COMPOSER, t->composer_id, lib_str(t->composer));
    group_end();
}

/* Is the car's copy of this song the one it was last sent (diff), so it needs nothing? */
static int lib_unchanged(const struct iap2_track *t) {
    const struct iap2_library *l = H->lib;
    unsigned lo = lower_key(l->sent, l->sent_n, sizeof *l->sent, t->rid);
    return lo < l->sent_n && l->sent[lo].rid == t->rid && l->sent[lo].hash == t->hash;
}

static int lib_index(unsigned rid) { /* a song's index in the library, or -1 */
    const struct iap2_library *l = H->lib;
    unsigned lo = lower_key(l->tracks, l->n, sizeof *l->tracks, rid);
    return lo < l->n && l->tracks[lo].rid == rid ? (int)lo : -1;
}
static int lib_kept(unsigned rid) { return lib_index(rid) >= 0; } /* still in the library */

static int in_first(unsigned i) { /* sent in this connection's first update */
    unsigned lo = lower_key(ls.sorted, ls.nf, sizeof *ls.sorted, i);
    return lo < ls.nf && ls.sorted[lo] == i;
}

/* Does the car hold this song as it is now: all of them, those the push cut off had reached, or (changes) those
 * unchanged since the revision last sent whole? */
static int car_has(unsigned i) {
    return ls.held || i < ls.next || (ls.diff && lib_unchanged(&H->lib->tracks[i]));
}

/* A song for the first update, by rid: one the library has, the car lacks and the set has not yet. */
static void first_add(unsigned rid) {
    int i = rid ? lib_index(rid) : -1;
    if (i < 0 || ls.nf >= FIRST_MAX || car_has((unsigned)i)) return;
    for (unsigned k = 0; k < ls.nf; ++k)
        if (ls.first[k] == (unsigned)i) return;
    ls.first[ls.nf++] = (unsigned)i;
}

/* The first update's songs: the queue's from the playing song on, then the lists' in their order. */
static void first_set(void) {
    static unsigned q[QLIST_MAX];
    const struct iap2_library *l = H->lib;
    struct iap2_np np;
    memset(&np, 0, sizeof np);
    int n = H->queue ? H->queue(q, QLIST_MAX) : -1;
    unsigned from = H->now_playing && H->now_playing(&np) && np.qpos < (unsigned)(n > 0 ? n : 0) ? np.qpos : 0;
    for (int k = (int)from; k < n && k < (int)from + FIRST_QUEUE; ++k) first_add(q[k]);
    for (unsigned j = 0; j < l->nlists && j < LISTS_MAX; ++j)
        for (unsigned k = 0; k < l->lists[j].n && ls.nf < FIRST_MAX; ++k) first_add(l->lrids[l->lists[j].at + k]);
    memcpy(ls.sorted, ls.first, ls.nf * sizeof *ls.first);
    for (unsigned i = 1; i < ls.nf; ++i) { /* insertion sort: a few hundred */
        unsigned x = ls.sorted[i], j = i;
        while (j && ls.sorted[j - 1] > x) ls.sorted[j] = ls.sorted[j - 1], --j;
        ls.sorted[j] = x;
    }
}

/* A list's record (parameter 3: 0 its id, 1 its name, 2 its folder, none, 3 not a Genius mix, 4 not a folder, 5 the
 * file transfer its songs' ids go in), a new transfer id each time it is sent. 0 when the packet is full. */
static int list_record(unsigned j) {
    const struct iap2_list *li = &H->lib->lists[j];
    unsigned mark = m.len;
    u8 id = next_transfer;
    group_start(3);
    param_u64(0, LIB_LIST, li->id);
    param_str(1, lib_str(li->name));
    param_u64(2, 0, 0);
    param_u8(3, 0), param_u8(4, 0), param_u8(5, id);
    group_end();
    if (msg_full()) {
        m.len = mark, m.ok = 1;
        return 0;
    }
    ls.list_ids[j] = transfer_take(); /* id, taken now that the record fits */
    return 1;
}

static unsigned lib_nlists(void) { return H->lib->nlists < LISTS_MAX ? H->lib->nlists : LISTS_MAX; }

/* An update while there is room: songs (or the ids of songs gone) or lists' records, as many as a packet holds. One
 * completes (progress 100, which iDrive loads, PodBox found) at the end of the first update, at the end of the push,
 * and in between at CHECKPOINT_FIRST_MS into a push that had no first update and then every CHECKPOINT_MS. */
static void lib_pump(void) {
    const struct iap2_library *l = H->lib;
    char t[200];
    while ((ls.phase == LIB_FIRST || ls.phase == LIB_ITEMS || ls.phase == LIB_GONE || ls.phase == LIB_FINAL) && link_up &&
           !q_count && (!rto || count + 1 < window) && backlog() <= BACKLOG_MAX) {
        int n = 0, complete = 0, final = 0;
        msg_start(0x4C04);
        param_str(0, H->library_id);
        if (ls.reset) param(6, 0, 0), ls.reset = 0;
        while (ls.phase == LIB_FIRST && ls.fi < ls.nf) {
            unsigned mark = m.len;
            lib_item(&l->tracks[ls.first[ls.fi]], ls.mask);
            if (msg_full()) {
                m.len = mark, m.ok = 1;
                if (!n) ++ls.fi; /* a song no packet holds */
                break;
            }
            ++ls.fi, ++n, ++ls.items;
        }
        while ((ls.phase == LIB_FIRST && ls.fi >= ls.nf) || ls.phase == LIB_FINAL) {
            if (ls.rec >= lib_nlists()) {
                complete = 1, final = ls.phase == LIB_FINAL;
                break;
            }
            if (!list_record(ls.rec)) {
                if (!n) ++ls.rec; /* a record no packet holds */
                break;
            }
            ++ls.rec, ++n;
        }
        while (ls.phase == LIB_ITEMS && ls.next < l->n) {
            const struct iap2_track *tr = &l->tracks[ls.next];
            if ((ls.diff && lib_unchanged(tr)) || in_first(ls.next)) {
                ++ls.next;
                continue;
            }
            unsigned mark = m.len;
            lib_item(tr, ls.mask);
            if (msg_full()) {
                m.len = mark, m.ok = 1;
                if (!n) ++ls.next; /* a song no packet holds */
                break;
            }
            ++ls.next, ++n, ++ls.items;
        }
        if (ls.phase == LIB_ITEMS && ls.next >= l->n) ls.phase = ls.diff ? LIB_GONE : LIB_FINAL, ls.rec = 0;
        while (ls.phase == LIB_GONE && ls.gone < l->sent_n) {
            unsigned rid = l->sent[ls.gone].rid;
            if (lib_kept(rid)) {
                ++ls.gone;
                continue;
            }
            if (m.len + 12 + 4 + 64 + 5 > BULK_MAX - HEADER_LEN - 1) break;
            param_u64(4, LIB_KEY, rid);
            ++ls.gone, ++n;
        }
        if (ls.phase == LIB_GONE && ls.gone >= l->sent_n) ls.phase = LIB_FINAL, ls.rec = 0;
        if (ls.phase == LIB_FINAL && !n && ls.rec >= lib_nlists()) complete = final = 1; /* no lists: the revision */
        if ((ls.phase == LIB_ITEMS || ls.phase == LIB_GONE) &&
            H->now() - ls.complete_at >= (ls.completes ? CHECKPOINT_MS : CHECKPOINT_FIRST_MS))
            complete = 1;
        char partial[64];
        snprintf(partial, sizeof partial, "%s.%s.%u", ls.diff ? "q2d" : "q2p", l->rev, ls.next);
        param_str(1, final || ls.held ? l->rev : partial);
        param_u8(7, complete ? 100 : (u8)(l->n ? ls.next * 99u / l->n : 0));
        msg_send();
        ++ls.updates;
        if (!complete) continue;
        ++ls.completes, ls.complete_at = H->now();
        snprintf(t, sizeof t, "library: %s complete, %u songs and %u lists so far, %ld ms",
                 final ? "the whole" : ls.phase == LIB_FIRST ? "the first update" : "a checkpoint", ls.items,
                 ls.phase == LIB_FIRST || final ? lib_nlists() : 0, H->now() - ls.started);
        H->log(t);
        if (ls.phase == LIB_FIRST) {
            ls.after = ls.held ? LIB_IDLE : LIB_ITEMS;
            ls.phase = LIB_LISTS, ls.xi = 0, ls.x_state = 0;
        } else if (final) {
            snprintf(t, sizeof t, "library: %s sent, %u songs of %u in %u updates, %ld ms", ls.diff ? "changes" : "whole",
                     ls.items, l->n, ls.updates, H->now() - ls.started);
            H->log(t);
            long at = H->now();
            if (H->lib_saved) H->lib_saved();
            snprintf(t, sizeof t, "library: its end taken in %ld ms (the record handed to its writer)", H->now() - at);
            H->log(t);
            ls.after = LIB_IDLE;
            ls.phase = LIB_LISTS, ls.xi = 0, ls.x_state = 0;
        }
    }
}

/* The lists' songs, a list at a time, each as a file transfer: [ID, 04, an 8-byte size, 00 04, the list's 8-byte id,
 * the library's id and a 0], then on the car's [ID, 01] the songs' ids, 8 bytes each in order (PodBox, from an iPhone
 * in its car), a piece at a time while little waits to be sent, and the next once the last piece is on its way (as
 * PodBox's), or once the car cancels it (02), or LIST_WAIT_MS after a setup it never answers. Then on to the push
 * (ls.after). */
static void lists_pump(long now) {
    const struct iap2_library *l = H->lib;
    if (ls.phase != LIB_LISTS) return;
    if (!file_session) ls.xi = lib_nlists();
    if (ls.x_state == 1 && now - ls.x_since > LIST_WAIT_MS) { /* never started: cancelled, and on */
        file_send(ls.x_id, 0x02, 0, 0);
        ls.x_state = 0, ++ls.xi;
    }
    if (!ls.x_state && ls.xi < lib_nlists() && !q_count) {
        const struct iap2_list *li = &l->lists[ls.xi];
        unsigned size = li->n * 8, k = 0;
        u8 setup[10 + 8 + 64];
        memset(setup, 0, 4);
        k = 4, setup[k++] = size >> 24, setup[k++] = size >> 16, setup[k++] = size >> 8, setup[k++] = size;
        setup[k++] = 0, setup[k++] = 4;
        put_id8(setup + k, LIB_LIST, li->id);
        k += 8;
        unsigned idn = strlen(H->library_id) + 1;
        if (idn > sizeof setup - k) idn = sizeof setup - k;
        memcpy(setup + k, H->library_id, idn), k += idn;
        ls.x_id = ls.list_ids[ls.xi], ls.x_state = 1, ls.x_since = now, ls.x_sent = 0;
        file_send(ls.x_id, 0x04, setup, k);
    }
    while (ls.x_state == 2 && q_count < 2) {
        const struct iap2_list *li = &l->lists[ls.xi];
        u8 piece[BULK_MAX];
        unsigned room = piece_room(), k = 0;
        unsigned first = ls.x_sent == 0;
        while (ls.x_sent < li->n && k + 8 <= room) put_id8(piece + k, LIB_KEY, l->lrids[li->at + ls.x_sent++]), k += 8;
        unsigned last = ls.x_sent >= li->n;
        file_send(ls.x_id, piece_op(first, last), piece, k);
        if (last) ls.x_state = 0, ++ls.xi; /* the next at once, as PodBox's does */
    }
    if (ls.xi >= lib_nlists() && !ls.x_state) {
        ls.phase = ls.after;
        if (ls.phase == LIB_ITEMS) ls.complete_at = now; /* the next checkpoint a minute on */
    }
}

/* The car's word on the list in transfer: 01 go, 02 cancelled. 1 when it was that list's. */
static int list_transfer_word(const u8 *d) {
    if (ls.phase != LIB_LISTS || !ls.x_state || d[0] != ls.x_id) return 0;
    if (d[1] == 0x01 && ls.x_state == 1) ls.x_state = 2;
    else if (d[1] == 0x02) ls.x_state = 0, ++ls.xi;
    return 1;
}

/* What the car asked at StartMediaLibraryUpdates, kept while the library is read (LIB_READING): the revision it
 * holds (a longer one than this is no revision this Q2 sent) and the item properties it wants. */
static struct {
    char rev[128];
    int has_rev;
    unsigned mask;
} lib_ask;

static void send_library_none(void) {
    msg_start(0x4C04), param_str(0, H->library_id), param_str(1, "1"), param_u8(7, 100), msg_send();
}

/* The car's StartMediaLibraryUpdates: its first two answers at once, then the library read off the loop (H->lib_load,
 * a child on the Q2) and the updates begun when it is in (library_read, from iap2_tick). */
static void send_library_updates(const char *car_rev, unsigned mask) {
    msg_start(0x4C04), param_str(0, H->library_id), param_u8(8, 0), msg_send();
    msg_start(0x4C04), param_str(0, H->library_id), param_u8(9, 1), msg_send();
    if (!H->lib || !H->lib_load || !H->lib_ready || H->lib_load() < 0) {
        send_library_none();
        return;
    }
    lib_ask.has_rev = car_rev != 0;
    snprintf(lib_ask.rev, sizeof lib_ask.rev, "%s", car_rev ? car_rev : "");
    lib_ask.mask = mask;
    ls.phase = LIB_READING, ls.x_state = 0;
}

/* The server's catalogue unreadable while the car was last sent server songs (q2_iap_lib.c lib_read's -2, the card
 * not mounted yet): not read as every server song gone (uac1-power-app.md #4). The car keeps what it holds: its own
 * revision back, complete, and nothing recorded as sent. */
static void library_unreadable(void) {
    char t[220];
    snprintf(t, sizeof t, "library: the catalogue cannot be read (the card not mounted?): the car keeps %s",
             lib_ask.has_rev ? lib_ask.rev : "nothing");
    H->log(t);
    if (!lib_ask.has_rev) {
        send_library_none();
        return;
    }
    msg_start(0x4C04), param_str(0, H->library_id), param_str(1, lib_ask.rev), param_u8(7, 100), msg_send();
}

static void library_read(void) {
    char t[300];
    const char *car_rev = lib_ask.has_rev ? lib_ask.rev : 0;
    unsigned mask = lib_ask.mask;
    const struct iap2_library *l = H->lib;
    memset(&ls, 0, sizeof ls);
    ls.mask = mask, ls.started = ls.complete_at = H->now();
    ls.held = car_rev && !strcmp(car_rev, l->rev);
    ls.diff = !ls.held && car_rev && l->sent_rev[0] && !strcmp(car_rev, l->sent_rev);
    ls.reset = !ls.held && !ls.diff;
    /* a push of this revision cut off: on from where it reached, with no reset (its kind from the prefix) */
    unsigned rn = strlen(l->rev);
    if (car_rev && (!strncmp(car_rev, "q2p.", 4) || !strncmp(car_rev, "q2d.", 4)) && !strncmp(car_rev + 4, l->rev, rn) &&
        car_rev[4 + rn] == '.') {
        unsigned at = (unsigned)atoi(car_rev + 5 + rn);
        ls.diff = car_rev[2] == 'd', ls.reset = 0, ls.next = at < l->n ? at : l->n;
    }
    first_set();
    if (ls.held && !lib_nlists()) {
        snprintf(t, sizeof t, "library: the car holds %s, %u songs: nothing to send", l->rev, l->n);
        H->log(t);
        msg_start(0x4C04), param_str(0, H->library_id), param_str(1, l->rev), param_u8(7, 100), msg_send();
        return;
    }
    ls.phase = ls.nf || lib_nlists() ? LIB_FIRST : LIB_ITEMS;
    snprintf(t, sizeof t, "library: the car holds %s; %u songs and %u lists first, then %s of %s (%u songs) from %u",
             car_rev ? car_rev : "nothing", ls.nf, lib_nlists(), ls.held ? "nothing" : ls.diff ? "the changes" : "the whole",
             l->rev, l->n, ls.next);
    H->log(t);
    /* every list's name, once: on 2026-11-22 iDrive showed a list like "Apple Music Radio" and the log cut the
     * updates' names off */
    char names[1600];
    unsigned at = (unsigned)snprintf(names, sizeof names, "library: its lists:");
    for (unsigned j = 0; j < lib_nlists() && at < sizeof names - 1; ++j)
        at += (unsigned)snprintf(names + at, sizeof names - at, "%s \"%s\"", j ? "," : "", lib_str(l->lists[j].name));
    H->log(names);
    lib_pump();
}

/* ---- playing from iDrive: 4C06 to 4C09 (parameters logged whole as they come, in iap2_control_receive) ---- */

static unsigned *play_rids(unsigned n) { return n ? malloc(n * sizeof(unsigned)) : 0; }

/* PlayMediaLibraryItems: 0 the songs' ids back to back, 8 bytes each, 1 the index to start at. */
static void play_items(const u8 *p, unsigned len) {
    unsigned n, sn;
    const u8 *v = find(p, len, 0, &n), *s = find(p, len, 1, &sn);
    unsigned at = s && sn == 4 ? get_be(s, 4) : 0, k = 0, *rids = play_rids(n / 8);
    for (unsigned i = 0; rids && v && i + 8 <= n; i += 8)
        if (get_be(v + i, 4) == LIB_KEY && get_be(v + i + 4, 4)) rids[k++] = get_be(v + i + 4, 4);
    if (k && H->play) H->play(rids, k, at, 0);
    free(rids);
}

/* PlayMediaLibraryCollection: 0 the collection's id, 1 its type (0 a playlist, 1 an artist, 2 an album, 3 an album
 * artist, 4 a genre, 5 a composer), 2 the index to start at. A playlist's songs in its order; the others' in the
 * library's order of an album: album artist, album, disc, track. */
static void play_collection(const u8 *p, unsigned len) {
    unsigned n, tn, sn;
    const u8 *v = find(p, len, 0, &n), *ty = find(p, len, 1, &tn), *s = find(p, len, 2, &sn);
    const struct iap2_library *l = H->lib;
    if (!v || n != 8 || !ty || !tn || !l || !l->n) return;
    unsigned id = get_be(v + 4, 4), type = ty[0], at = s && sn == 4 ? get_be(s, 4) : 0, k = 0;
    if (type == 0) {
        for (unsigned j = 0; j < l->nlists; ++j)
            if (get_be(v, 4) == LIB_LIST && l->lists[j].id == id && H->play) {
                char t[96];
                snprintf(t, sizeof t, "library: list %u: %u songs, from %u", id, l->lists[j].n, at);
                H->log(t);
                H->play(l->lrids + l->lists[j].at, l->lists[j].n, at, 0);
            }
        return;
    }
    unsigned *rids = play_rids(l->n);
    for (unsigned i = 0; rids && i < l->n; ++i) {
        const struct iap2_track *t = &l->tracks[i];
        unsigned of = type == 1   ? t->artist_id
                      : type == 2 ? t->album_id
                      : type == 3 ? t->aartist_id
                      : type == 4 ? t->genre_id
                      : type == 5 ? t->composer_id
                                  : 0;
        if (type >= 1 && type <= 5 && of == id) rids[k++] = i; /* indexes first, sorted below */
    }
    /* insertion sort by album artist, album, disc, track: a collection is an album or a few hundred songs */
    for (unsigned i = 1; i < k; ++i) {
        unsigned x = rids[i], j = i;
        const struct iap2_track *a = &l->tracks[x];
        while (j) {
            const struct iap2_track *b = &l->tracks[rids[j - 1]];
            int c = strcmp(lib_str(b->aartist), lib_str(a->aartist));
            if (!c) c = strcmp(lib_str(b->album), lib_str(a->album));
            if (!c) c = (int)b->disc - (int)a->disc;
            if (!c) c = (int)b->track - (int)a->track;
            if (c <= 0) break;
            rids[j] = rids[j - 1], --j;
        }
        rids[j] = x;
    }
    for (unsigned i = 0; i < k; ++i) rids[i] = l->tracks[rids[i]].rid;
    char t[96];
    snprintf(t, sizeof t, "library: collection %08x of type %u: %u songs, from %u", id, type, k, at);
    H->log(t);
    if (k && H->play) H->play(rids, k, at, 0);
    free(rids);
}

/* PlayMediaLibrarySpecial: every song (1, all songs), shuffled as the car's shuffle-all is. */
static void play_all(void) {
    const struct iap2_library *l = H->lib;
    unsigned *rids = l ? play_rids(l->n) : 0;
    for (unsigned i = 0; rids && i < l->n; ++i) rids[i] = l->tracks[i].rid;
    if (rids && H->play) H->play(rids, l->n, 0, 1);
    free(rids);
}

/* The rate as an index into an iPhone's nine (8000 to 48000); an iPhone at 44100 sends 7, then two uint32s, both 0.
 * The rate is the player's for the song about to play (iap2_audio_rate, docs/iphone-mode.md#per-song-rate), sent at
 * StartUSBDeviceModeAudio and again at each song whose rate differs: 32, 44.1 and 48 kHz (6, 7, 8), the 48 kHz family
 * above it 8, anything else 44.1 kHz's 7. */
static const unsigned iap2_rates[9] = { 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000 };
static unsigned audio_hz = 44100;
static int audio_sent = -1;

static int iap2_rate_index(unsigned hz) {
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
static void iap2_audio_rate(unsigned hz) {
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
static void iap2_art_slow(void) {
    if (art_hold_at) np_at = 0; /* at the next tick, with no cover yet */
}
static void iap2_art_done(int found) {
    if (art_hold_at || found) iap2_art_again();
}

static void lib_reset(void) { ls.phase = LIB_IDLE, ls.x_state = 0, ql_going = 0, ql_seen_at = 0; }

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

static void iap2_tick(void) {
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
static long iap2_due(long now) {
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
static void iap2_connect(const struct iap2_host *h) {
    H = h;
    seen = 0;
    my_seq = 1, peer_seq = 0, head = 0;
    rx_len = 0, rx_partial = 0;
    reset_link();
}

/* A session's end (q2_iap.c: no ENABLE or RESUME within the gap): the link forgotten with it, the car's audio file
 * removed (iap2_control_reset), so a bus that comes back without the car's detect gets nothing of the old link. */
static void iap2_session_end(void) {
    if (seen) H->log("link: forgotten with its session");
    iap2_connect(H);
}
