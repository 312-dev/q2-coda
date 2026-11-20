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

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

/* What the Q2 is playing, for NowPlayingUpdate (the shim's /tmp/coda-rc.now, read by the caller). */
struct iap2_np {
    int state; /* 0 stopped, 1 playing, 2 paused */
    unsigned pos_ms, len_ms, qpos, qlen;
    int shuffle, repeat; /* repeat 0 off, 1 one, 2 all */
    char title[160], artist[160], album[160], path[400];
};

struct iap2_host {
    void (*send)(const u8 *report, unsigned len); /* an input report: [id][link control][data, padded] */
    long (*now)(void);                             /* milliseconds */
    void (*log)(const char *msg);
    int (*now_playing)(struct iap2_np *np);       /* 1 and np filled, or 0: nothing known */
    const char *name, *uuid, *library_id;
    void (*audio)(int on);                         /* the car wants USB audio (1) or no longer (0) */
    void (*command)(const char *verb);             /* a button or a seek: the shim's verbs (coda_rc_shim.c cr_command) */
    unsigned (*artwork)(const struct iap2_np *np, u8 *out, unsigned cap); /* the song's cover, a JPEG, or 0 */
};

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
        off += take;
    } while (off < n);
}

/* ---- the link layer ---- */

static int seen, synced, link_up;
static u8 my_seq, syn_seq, peer_seq, last_ack, control_session, file_session;
static u8 syn_ack[48];
static unsigned syn_ack_len, window, peer_max, max_sends, max_cacks, unacked;
static long rto, cack, unacked_since; /* milliseconds; rto 0: never sent again */

struct slot {
    u8 packet[MAX_PACKET];
    unsigned len, tries;
    long sent;
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

static void send_ctl(u8 control, const u8 *payload, unsigned n, u8 seq) {
    u8 p[HEADER_LEN + 48 + 1];
    unsigned len = HEADER_LEN + (n ? n + 1 : 0);
    put_header(p, len, control, seq, 0);
    if (n) memcpy(p + HEADER_LEN, payload, n), p[HEADER_LEN + n] = cksum(payload, n);
    log_packet(1, p, len, 0);
    send_bytes(p, len);
    unacked = 0;
}

static void send_slot(struct slot *s) {
    s->packet[6] = peer_seq;
    s->packet[8] = cksum(s->packet, 8);
    s->tries++, s->sent = H->now();
    unacked = 0;
    log_packet(1, s->packet, s->len, s->tries > 1 ? "(again)" : 0);
    send_bytes(s->packet, s->len);
}

/* Messages waiting for room in the car's window: with retransmission on (the BMW's SYN gives 10 retries), no more
 * than its window may be unacknowledged, and a cover is a dozen packets. Sent in order as acknowledgements free the
 * window (pump). */
#define QUEUE 96
static struct queued {
    u8 session;
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
    iap2_control_reset();
}

/* The car's SYN: its link parameters, the sessions after them. Retransmitted SYNs get the same answer. */
static void take_syn(u8 seq, const u8 *payload, unsigned n) {
    char h[200], m[300];
    iap2_hex(h, sizeof h, payload, n);
    snprintf(m, sizeof m, "link parameters: version %u, window %u, max packet %u, retransmit %u ms, cack %u ms, "
             "retries %u, cacks %u:", payload[0], payload[1], payload[2] << 8 | payload[3], payload[4] << 8 | payload[5],
             payload[6] << 8 | payload[7], payload[8], payload[9]);
    iap2_log(m, h);
    if (n < SYN_FIXED || n > sizeof syn_ack || payload[0] != 1) {
        H->log("link: SYN not taken (not version 1, or its length)");
        return;
    }
    memcpy(syn_ack, payload, n);
    syn_ack_len = n;
    unsigned want = payload[2] << 8 | payload[3];
    peer_max = want < HEADER_LEN + 2 ? HEADER_LEN + 2 : want > MAX_PACKET ? MAX_PACKET : want;
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
    log_packet(0, p, len, 0);
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
    if (q_count == QUEUE || n > sizeof queue[0].b) {
        H->log(q_count == QUEUE ? "link: the send queue is full; a packet dropped" : "link: a packet too long; dropped");
        return;
    }
    struct queued *q = &queue[(q_head + q_count++) % QUEUE];
    q->session = session, q->len = n;
    memcpy(q->b, b, n);
    pump();
}

static void pump(void) {
    while (q_count && link_up && (!rto || (count < window && count < SLOTS))) {
        struct queued *q = &queue[q_head];
        struct slot *s = &slots[(head + count) % SLOTS];
        s->len = HEADER_LEN + q->len + 1;
        put_header(s->packet, s->len, CTL_ACK, ++my_seq, q->session);
        memcpy(s->packet + HEADER_LEN, q->b, q->len);
        s->packet[HEADER_LEN + q->len] = cksum(q->b, q->len);
        s->tries = 0;
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
    log_msg(1, m.b, m.len, 96);
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
 * 13 to 120 ms after the press, 2026-11-20); held past it, the song moves through itself from where it was, a seek
 * every SCAN_STEP_MS (the shim's command file is read every 500 ms with Car Remote off: a faster seek would only
 * replace the one before it), SCAN_SLOW seconds a step for the first SCAN_SLOW_MS (4 times as fast as it plays),
 * then SCAN_FAST (8 times), then SCAN_FASTEST (16 times), until the release, and the song plays on from there. */
#define HOLD_MS 500
#define SCAN_STEP_MS 500
#define SCAN_SLOW 2
#define SCAN_FAST 4
#define SCAN_FASTEST 8
#define SCAN_SLOW_MS 2000
#define SCAN_FAST_MS 5000
static unsigned held; /* Next's or Previous's usage while it is down, else 0 */
static long held_at, scan_at;
static int scanning;
static long scan_pos_ms, scan_len_ms;

static void scan_step(long now) {
    char v[32], t[96];
    long in = now - held_at - HOLD_MS, step = in < SCAN_SLOW_MS ? SCAN_SLOW : in < SCAN_FAST_MS ? SCAN_FAST : SCAN_FASTEST;
    scan_pos_ms += (held == 0xB5 ? step : -step) * 1000L;
    if (scan_pos_ms < 0) scan_pos_ms = 0;
    if (scan_len_ms > 1000 && scan_pos_ms > scan_len_ms - 1000) scan_pos_ms = scan_len_ms - 1000;
    scan_at = now;
    snprintf(v, sizeof v, "seek %ld", scan_pos_ms / 1000);
    snprintf(t, sizeof t, "hid: usage %02x held %ld ms: %s", held, now - held_at, v);
    H->log(t);
    if (H->command) H->command(v);
}

/* From iap2_tick: a hold that passed HOLD_MS starts the scan, and each SCAN_STEP_MS moves it. */
static void scan_tick(long now) {
    if (!held || now - held_at < HOLD_MS) return;
    if (!scanning) {
        struct iap2_np np;
        memset(&np, 0, sizeof np);
        if (H->now_playing) H->now_playing(&np);
        scanning = 1, scan_pos_ms = np.pos_ms, scan_len_ms = np.len_ms;
        scan_step(now);
    } else if (now - scan_at >= SCAN_STEP_MS) {
        scan_step(now);
    }
}

static void release(unsigned usage) {
    char t[96];
    if (usage != held) return;
    long ms = H->now() - held_at;
    if (!scanning) press(usage); /* a tap: the skip */
    else snprintf(t, sizeof t, "hid: usage %02x released after %ld ms: the scan ends", usage, ms), H->log(t);
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

static void file_send(u8 id, u8 op, const u8 *d, unsigned n) {
    u8 b[MAX_PACKET];
    if (!file_session || n + 2 > sizeof b) return;
    b[0] = id, b[1] = op;
    if (n) memcpy(b + 2, d, n);
    enqueue(file_session, b, n + 2);
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
    art_id = next_transfer, next_transfer = next_transfer == 0xFF ? 0x80 : next_transfer + 1;
    unsigned got = H->artwork ? H->artwork(n, raw, sizeof raw) : 0;
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

/* The car's answers on the file transfer session. */
static void iap2_file(const u8 *d, unsigned n) {
    char t[96];
    if (n < 2) return;
    snprintf(t, sizeof t, "file < transfer %02x op %02x (%u bytes)", d[0], d[1], n);
    H->log(t);
    if (d[0] != art_id || !art_going) return;
    if (d[1] == 0x01) { /* go: every piece, as the window allows */
        unsigned room = peer_max - HEADER_LEN - 1 - 2;
        if (room > MAX_PACKET - HEADER_LEN - 1 - 2) room = MAX_PACKET - HEADER_LEN - 1 - 2;
        if (!art_len) file_send(art_id, 0xC0, 0, 0);
        while (art_sent < art_len) {
            unsigned k = art_len - art_sent < room ? art_len - art_sent : room;
            u8 op = art_sent == 0 && art_sent + k == art_len ? 0xC0 : art_sent == 0 ? 0x80 : art_sent + k == art_len ? 0x40 : 0x00;
            file_send(art_id, op, art + art_sent, k);
            art_sent += k;
        }
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
#define PB_APP_BUNDLE 0x10
#define DEFAULT_MEDIA (1u << MI_PERSISTENT_ID | 1u << MI_TITLE | 1u << MI_DURATION | 1u << MI_ALBUM | 1u << MI_ARTIST)
#define DEFAULT_PLAYBACK (1u << PB_STATE | 1u << PB_POSITION | 1u << PB_QUEUE_INDEX | 1u << PB_QUEUE_COUNT)

static long due_in(long ms) {
    long d = H->now() + ms;
    return d ? d : 1;
}

static void iap2_control_reset(void) {
    if (H) set_audio(0);
    art_going = 0, art_id = 0, n_controls = 0, held = 0, scanning = 0;
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
    identified = 1;
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

/* The library: the Q2's own, by name and ID, and an update with no items (the user's decision: the car's browser
 * starts empty, music is chosen on the Q2 and in the app). ponytail: PodBox found its Mazda reads nothing more after
 * an empty library; if iDrive stalls here, send the playing track as the one item, as PodBox does. */
static void send_library_info(void) {
    msg_start(0x4C01);
    group_start(0), param_str(0, H->name), param_str(1, H->library_id), param_u8(2, 0), group_end();
    msg_send();
}
static void send_library_updates(void) {
    msg_start(0x4C04), param_str(0, H->library_id), param_u8(8, 0), msg_send();
    msg_start(0x4C04), param_str(0, H->library_id), param_u8(9, 1), msg_send();
    msg_start(0x4C04), param_str(0, H->library_id), param_str(1, "1"), param_u8(7, 100), msg_send();
}

/* The rate as an index into an iPhone's nine (8000 to 48000); an iPhone at 44100 sends 7, then two uint32s, both 0. */
static void send_audio_rate(void) {
    msg_start(0xDA01);
    param_u8(0, 7);
    param_u32(1, 0);
    param_u32(2, 0);
    msg_send();
}

static u32 np_hash(const struct iap2_np *n) {
    u32 h = 2166136261u;
    for (const char *s = n->title; *s; ++s) h = (h ^ (u8)*s) * 16777619u;
    for (const char *s = n->artist; *s; ++s) h = (h ^ (u8)*s) * 16777619u;
    return h;
}

/* The song's attributes the car asked for (group 0) and its playback state (group 1). With nothing known, the
 * title is the Q2's name, stopped. Artwork (M6) is not offered. */
static void send_now_playing(const struct iap2_np *n, int media) {
    msg_start(0x5001);
    if (media) {
        group_start(0);
        if (media_mask & 1u << MI_PERSISTENT_ID) param_u64(MI_PERSISTENT_ID, 0x51320000u, np_hash(n));
        if (media_mask & 1u << MI_TITLE) param_str(MI_TITLE, n->title[0] ? n->title : H->name);
        if (media_mask & 1u << MI_DURATION) param_u32(MI_DURATION, n->len_ms);
        if ((media_mask & 1u << MI_ALBUM) && n->album[0]) param_str(MI_ALBUM, n->album);
        if ((media_mask & 1u << MI_ARTIST) && n->artist[0]) param_str(MI_ARTIST, n->artist);
        for (unsigned a = MI_LIKE_SUPPORTED; a <= MI_BANNED; ++a)
            if (media_mask & 1u << a) param_u8(a, 0);
        if (media_mask & 1u << MI_CHAPTER_COUNT) param_u16(MI_CHAPTER_COUNT, 0);
        if (media_mask & 1u << MI_ARTWORK) {
            u8 id = art_start(n);
            if (id) param_u8(MI_ARTWORK, id);
        }
        group_end();
    }
    group_start(1);
    if (playback_mask & 1u << PB_STATE) param_u8(PB_STATE, n->state);
    if (playback_mask & 1u << PB_POSITION) param_u32(PB_POSITION, n->pos_ms);
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
 * while playing (PodBox: without it the car's bar sits at 0:00). */
static void poll_now_playing(long now) {
    struct iap2_np n;
    if (np_at && now - np_at < 500) return;
    np_at = now;
    memset(&n, 0, sizeof n);
    if (!H->now_playing || !H->now_playing(&n)) n.state = 0;
    int changed = !np_sent || np_hash(&n) != np_hash(&np_last) || n.state != np_last.state || n.len_ms != np_last.len_ms;
    if (changed) send_now_playing(&n, 1);
    else if (n.state == 1) {
        msg_start(0x5001), group_start(1), param_u32(PB_POSITION, n.pos_ms), group_end(), msg_send();
    }
    np_last = n, np_sent = 1;
}

static void iap2_control_receive(const u8 *msg, unsigned len) {
    unsigned id = msg[4] << 8 | msg[5], plen = len - 6, n;
    const u8 *p = msg + 6, *v;
    log_msg(0, msg, len, id == 0x1D01 ? 1100 : id == 0xAA01 || id == 0xAA03 ? 24 : 160);
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
    case 0x4C03:
        send_library_updates();
        break;
    case 0xDA00:
        H->log("audio: the car asks for USB audio (44.1 kHz announced; M3 plays into it)");
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

static void iap2_tick(void) {
    long now = H->now();
    if (!seen) return;
    if (link_up && count && rto) {
        struct slot *s = &slots[head];
        if (s->tries && now - s->sent > rto) {
            if (s->tries >= max_sends) {
                H->log("link: lost (a packet went unacknowledged through every retry)");
                reset_link();
                return;
            }
            send_slot(s);
        }
    }
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
    if (identified && now_playing) poll_now_playing(now);
    pump();
}

/* A new connection: everything forgotten, the detect awaited. */
static void iap2_connect(const struct iap2_host *h) {
    H = h;
    seen = 0;
    my_seq = 1, peer_seq = 0, head = 0;
    rx_len = 0, rx_partial = 0;
    reset_link();
}
