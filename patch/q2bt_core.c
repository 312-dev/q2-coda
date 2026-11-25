/* q2bt's decisions, parsers and log line (docs/internals.md#bluetooth-link-monitor). No reads of
 * the device here: q2bt.c reads, these turn what it read into a line. */
#include "q2bt.h"

/* n counted over ms, a second's worth. A count over 4000000 is divided first: no 64-bit division
 * (the helper is linked without libgcc); a rate past 2000000000 is held there, not wrapped. */
int bt_rate(unsigned n, unsigned ms) {
    if (!ms) return 0;
    if (n <= 4000000u) return (int)(n * 1000u / ms);
    return n / ms > 2000000u ? 2000000000 : (int)(n / ms * 1000u);
}
int bt_ticks_pct(unsigned ticks, unsigned ms) {
    if (!ms) return 0;
    return (int)(ticks > 400000u ? ticks / ms * 10000u : ticks * 10000u / ms);
}

/* A stream starts on the first interval the controller was handed BT_START_BPS or more, and ends
 * after BT_QUIET_MS under it, so a stall (the radio taking nothing for seconds) is sampled through
 * and a pause ends it once bluealsa's keep-alive (5 s) has let the transport go. A count that went
 * backwards by more than a wrap would (the controller restarted) counts as nothing sent. */
int bt_detect_step(struct bt_detect *d, unsigned now, unsigned byte_tx) {
    unsigned ms = now - d->at, n = byte_tx - d->bytes;
    int first = !d->have;
    d->have = 1, d->at = now, d->bytes = byte_tx;
    if (first || !ms) return BT_EV_NONE;
    if (n > 16000000u) n = 0;
    int busy = bt_rate(n, ms) >= BT_START_BPS;
    if (!d->on) {
        if (!busy) return BT_EV_NONE;
        d->on = 1, d->quiet = 0;
        return BT_EV_START;
    }
    d->quiet = busy ? 0 : d->quiet + ms;
    if (d->quiet < BT_QUIET_MS) return BT_EV_NONE;
    d->on = 0;
    return BT_EV_STOP;
}

/* ---- hci_dev_info and hci_conn_info ----
 * include/net/bluetooth/hci_sock.h: dev_id, name[8], bdaddr[6], flags, type, features[8], pkt_type,
 * link_policy, link_mode, acl_mtu (44), acl_pkts (46), sco_mtu, sco_pkts, then hci_dev_stats (52):
 * err_rx, err_tx (56), cmd_tx, evt_rx, acl_tx (68), acl_rx, sco_tx, sco_rx, byte_rx (84), byte_tx (88).
 */
static unsigned u32_at(const unsigned char *p) {
    unsigned v;
    memcpy(&v, p, 4);
    return v;
}
static int u16_at(const unsigned char *p) {
    unsigned short v;
    memcpy(&v, p, 2);
    return v;
}
void bt_devinfo(const unsigned char *di, struct bt_counts *c) {
    c->acl_mtu = u16_at(di + 44), c->acl_pkts = u16_at(di + 46);
    c->err_tx = u32_at(di + 56), c->acl_tx = u32_at(di + 68), c->byte_tx = u32_at(di + 88);
    c->byte_rx = u32_at(di + 84);
}
/* hci_conn_list_req: dev_id, conn_num, then conn_num hci_conn_info of 16 bytes: handle, bdaddr[6],
 * type (8; ACL_LINK is 1), out, state, link_mode. */
int bt_acl_handle(const unsigned char *req, int max, unsigned char *bdaddr) {
    int n = u16_at(req + 2);
    for (int i = 0; i < n && i < max; ++i) {
        const unsigned char *c = req + 4 + 16 * i;
        if (c[8] != 1) continue;
        if (bdaddr) memcpy(bdaddr, c + 2, 6);
        return u16_at(c) & 0x0fff;
    }
    return -1;
}

/* ---- the commands ---- */
int bt_cmd(unsigned char *out, int op, int handle) {
    int plen = op == BT_OP_TPL ? 3 : 2;
    out[0] = 1; /* HCI_COMMAND_PKT */
    out[1] = (unsigned char)op, out[2] = (unsigned char)(op >> 8), out[3] = (unsigned char)plen;
    out[4] = (unsigned char)handle, out[5] = (unsigned char)(handle >> 8 & 0x0f);
    out[6] = 1; /* Read Transmit Power Level's type: the maximum */
    return 4 + plen;
}
int bt_handle_follow(struct bt_link *k, int listed) {
    if (listed == k->handle) return 0;
    k->handle = listed;
    return 1;
}
void bt_link_clear(struct bt_link *k, int handle) {
    k->handle = handle, k->answered = 0;
    k->rssi = k->afh = k->role = k->lsto = k->flush = k->txmax = BT_UNSET;
}
/* Command Complete (0x0e): ncmd, the opcode, then each command's return parameters: status, the
 * handle, its value (AFH: its mode, then the 10-byte map, channels 0-78; the two timeouts: 16 bits;
 * RSSI and the power: signed; the role: a byte). A refusal (status not 0) is answered, its value
 * left unset. */
int bt_event(struct bt_link *k, const unsigned char *p, int n) {
    if (n < 9 || p[0] != 4 || p[1] != 0x0e || n < 3 + p[2]) return 0;
    int op = p[4] | p[5] << 8, status = p[6], handle = (p[7] | p[8] << 8) & 0x0fff;
    if (op != BT_OP_RSSI && op != BT_OP_AFH && op != BT_OP_ROLE && op != BT_OP_LSTO && op != BT_OP_FLUSH &&
        op != BT_OP_TPL)
        return 0;
    if (handle != k->handle) return 0;
    ++k->answered;
    if (status) return 1;
    if (op == BT_OP_AFH) {
        if (n < 20) return 1;
        int used = 0;
        for (int ch = 0; ch < 79; ++ch) used += p[10 + ch / 8] >> (ch % 8) & 1;
        k->afh = used;
        return 1;
    }
    if (op == BT_OP_LSTO || op == BT_OP_FLUSH) {
        if (n >= 11) *(op == BT_OP_LSTO ? &k->lsto : &k->flush) = p[9] | p[10] << 8;
        return 1;
    }
    if (n < 10) return 1;
    if (op == BT_OP_RSSI)
        k->rssi = (signed char)p[9];
    else if (op == BT_OP_TPL)
        k->txmax = (signed char)p[9];
    else
        k->role = p[9];
    return 1;
}

/* Number Of Completed Packets: the number of handles, then each handle and its count (16 bits each)
 */
int bt_completed(const unsigned char *p, int n, int handle) {
    if (n < 4 || p[0] != 4 || p[1] != 0x13 || n < 3 + p[2] || n < 4 + 4 * p[3]) return 0;
    int done = 0;
    for (int i = 0; i < p[3]; ++i) {
        const unsigned char *e = p + 4 + 4 * i;
        if (((e[0] | e[1] << 8) & 0x0fff) == handle) done += e[2] | e[3] << 8;
    }
    return done;
}
int bt_fly_step(struct bt_fly *f, unsigned sent, unsigned done) {
    int d = (int)(sent - done);
    if (!f->have || d < f->low) f->have = 1, f->low = d;
    return d - f->low;
}

/* ---- EDR ---- */
static int hex_digit(int c) {
    return c >= '0' && c <= '9'   ? c - '0'
           : c >= 'A' && c <= 'F' ? c - 'A' + 10
           : c >= 'a' && c <= 'f' ? c - 'a' + 10
                                  : -1;
}
/* "dev_EC_66_D1_CF_59_3F" ends 17 characters before "/a2dpsrc" */
int bt_pcms_sink(const char *text, unsigned char *bdaddr) {
    const char *src = strstr(text, "/a2dpsrc");
    if (!src || src - text < 21 || strncmp(src - 21, "dev_", 4)) return 0;
    const char *a = src - 17;
    unsigned char b[6];
    for (int i = 0; i < 6; ++i) {
        int hi = hex_digit(a[3 * i]), lo = hex_digit(a[3 * i + 1]);
        if (hi < 0 || lo < 0 || (i < 5 && a[3 * i + 2] != '_')) return 0;
        b[5 - i] = (unsigned char)(hi << 4 | lo);
    }
    memcpy(bdaddr, b, 6);
    return 1;
}
int bt_dbus_bytes(const char *text, const char *after, const char *key, char *hex, int size) {
    const char *p = after ? strstr(text, after) : text;
    if (!p || !(p = strstr(p, key)) || !(p = strstr(p, "array of bytes"))) return 0;
    p += 14;
    while (*p == ' ') ++p;
    if (*p != '[') return 0; /* printed as a string */
    int n = 0, at = 0;
    for (++p; *p && *p != ']';) {
        int hi = hex_digit(p[0]), lo = hi < 0 ? -1 : hex_digit(p[1]);
        if (hi < 0 || lo < 0) {
            ++p;
            continue;
        }
        if (at + 2 < size) hex[at++] = (char)p[0] | 0x20, hex[at++] = (char)p[1] | 0x20, ++n;
        p += 2;
    }
    if (*p != ']') at = n = 0; /* cut short */
    if (size > 0) hex[at < size ? at : size - 1] = 0;
    return n;
}
int bt_acl_handle_of(const unsigned char *req, int max, const unsigned char *bdaddr) {
    int n = u16_at(req + 2);
    for (int i = 0; i < n && i < max; ++i) {
        const unsigned char *c = req + 4 + 16 * i;
        int same = c[8] == 1; /* ACL_LINK */
        for (int j = 0; j < 6; ++j) same &= c[2 + j] == bdaddr[j];
        if (same) return u16_at(c) & 0x0fff;
    }
    return -1;
}
void bt_edr_reset(struct bt_edr *e) { e->sent = -1; }
int bt_edr_step(struct bt_edr *e, int handle, unsigned char *out) {
    if (handle < 0 || handle == e->sent) return 0;
    e->sent = handle;
    out[0] = 1; /* HCI_COMMAND_PKT */
    out[1] = (unsigned char)BT_OP_CPT, out[2] = (unsigned char)(BT_OP_CPT >> 8), out[3] = 4;
    out[4] = (unsigned char)handle, out[5] = (unsigned char)(handle >> 8 & 0x0f);
    out[6] = (unsigned char)BT_PTYPE_EDR, out[7] = (unsigned char)(BT_PTYPE_EDR >> 8);
    return 8;
}
/* Connection Packet Type Changed: status, the handle, the packet types now allowed. Command Status:
 * status, ncmd, the opcode; the change follows it unless the status refuses. */
int bt_edr_event(struct bt_edr *e, const unsigned char *p, int n, char *note, int size) {
    int status, ptype = -1;
    if (e->sent < 0 || n < 3 || p[0] != 4 || n < 3 + p[2]) return 0;
    if (p[1] == 0x1d && p[2] >= 5) {
        if (((p[4] | p[5] << 8) & 0x0fff) != e->sent) return 0;
        status = p[3], ptype = p[6] | p[7] << 8;
    } else if (p[1] == 0x0f && p[2] >= 4) {
        if ((p[5] | p[6] << 8) != BT_OP_CPT || !p[3]) return 0;
        status = p[3];
    } else {
        return 0;
    }
    if (ptype < 0)
        snprintf(note, (size_t)size, "LINK ptype=- status=0x%02x h=%d", status, e->sent);
    else
        snprintf(note, (size_t)size, "LINK ptype=0x%04x status=0x%02x h=%d", ptype, status,
                 e->sent);
    if (e->said && e->said_ptype == ptype && e->said_status == status) return 1;
    e->said = 1, e->said_ptype = ptype, e->said_status = status;
    return 2;
}

/* Disconnection Complete (0x05): status, the handle, the reason, which names the side that ended
 * the link. */
int bt_down_event(const unsigned char *p, int n, int handle, const unsigned char *bdaddr, char *note,
                  int size) {
    if (handle < 0 || n < 7 || p[0] != 4 || p[1] != 0x05 || p[2] < 4 || p[3]) return 0;
    if (((p[4] | p[5] << 8) & 0x0fff) != handle) return 0;
    int reason = p[6];
    const char *who = reason >= 0x13 && reason <= 0x15 ? "headset"
                      : reason == 0x16                  ? "q2"
                      : reason == 0x08 || reason == 0x22 ? "timeout"
                                                         : "other";
    char dev[18] = "-";
    if (bdaddr) bt_addr_text(bdaddr, dev);
    snprintf(note, (size_t)size, "LINK down h=%d reason=0x%02x %s dev=%s", handle, reason, who, dev);
    return 1;
}

/* ---- the step-down ---- */
static const char *const codec_names[4] = { "", "SBC", "aptX", "aptX-HD" };
int bt_codec_rank(const char *codec) {
    for (int r = 1; r < 4; ++r)
        if (!strcmp(codec, codec_names[r])) return r;
    return 0;
}
const char *bt_codec_name(int rank) { return codec_names[rank > 0 && rank < 4 ? rank : 0]; }
int bt_codec_kbps(int rank, unsigned hz, int own) {
    static const int at48[4] = { 0, 259, 412, 614 };
    if (rank < 1 || rank > 3) return 0;
    int k = at48[rank];
    if (rank == 1 && own > 0) k = (k * (13 + 2 * own) + 39) / 79; /* frames of 13 + 2 x bitpool */
    return hz && hz < 400000 ? (int)(((unsigned)k * hz + 24000) / 48000) : k; /* no 64-bit math */
}
/* Short: the controller holding every buffer, something waiting, and what it delivered (kbps by
 * done of acl packets) under BT_STEP_SHORT % of the codec's rate, so the radio, not the encoder or
 * the player, is behind and the codec does not fit; a backlog with the rate whole is not. Down to
 * SBC at BT_STEP_STALL such seconds in a row (aptX is not worth a stop: where aptX HD fell short,
 * aptX did too). Back to where it started after BT_STEP_CLEAN clean seconds in a row: not short,
 * the AFH map at BT_STEP_AFH or more and SBC's bitpool at its own; and the wait since the last
 * switch, BT_STEP_UP_MS, doubled up to BT_STEP_UP_MAX_MS each time a step up is undone within
 * BT_STEP_UNDONE_MS. A quiet second (little handed, no buffer held) counts neither way. The setting
 * turned off while stepped down: back to where it started, once. Another Quality choice: left to
 * it (stock's change selects that codec), and where it started forgotten. */
int bt_step(struct bt_step *s, unsigned now, int allowed, int rank, const struct bt_step_line *l) {
    if (!rank || allowed < 0) { /* no codec known yet, or not one of the three: nothing to count */
        if (allowed < 0) s->top = rank;
        s->stall = s->clean = 0;
        return 0;
    }
    if (rank > s->top) s->top = rank;
    if (!allowed) {
        int back = rank < s->top ? s->top : 0;
        s->top = rank, s->stall = s->clean = 0; /* asked once: the next line sees it as the top */
        if (back) s->have = 1, s->last = now, s->raised = 0;
        return back;
    }
    if (s->have && now - s->last < BT_STEP_SETTLE_MS) {
        s->stall = s->clean = 0;
        return 0;
    }
    if (!s->up_ms) s->up_ms = BT_STEP_UP_MS;
    int full = l->fly >= 0 && l->bufs > 0 && l->fly >= l->bufs;
    if (!full && l->kbps >= 0 && l->kbps < BT_STEP_QUIET) return 0;
    /* delivered: kbps by done of acl packets, in 32 bits (each held under 65536) */
    unsigned kb = l->kbps > 0 ? (l->kbps < 65535 ? (unsigned)l->kbps : 65535u) : 0;
    unsigned done = l->done > 0 ? (l->done < 65535 ? (unsigned)l->done : 65535u) : 0;
    unsigned sent = l->acl > 0 ? kb * done / (unsigned)l->acl : 0;
    unsigned want = (unsigned)bt_codec_kbps(rank, l->hz, l->own) * BT_STEP_SHORT;
    int starved = full && l->wq > 0 && sent <= want / 100 && sent * 100 < want;
    s->stall = starved ? s->stall + 1 : 0;
    s->clean = !starved && l->fly >= 0 && l->afh >= BT_STEP_AFH && !l->lowered ? s->clean + 1 : 0;
    int to = 0;
    if (s->stall >= BT_STEP_STALL && rank > 1) {
        to = 1;
        if (s->raised && now - s->raised_at < BT_STEP_UNDONE_MS)
            s->up_ms = s->up_ms * 2 > BT_STEP_UP_MAX_MS ? BT_STEP_UP_MAX_MS : s->up_ms * 2;
        s->raised = 0;
    } else if (s->clean >= BT_STEP_CLEAN && rank < s->top &&
               (!s->have || now - s->last >= s->up_ms)) {
        to = s->top, s->raised = 1, s->raised_at = now;
    }
    if (to) s->have = 1, s->last = now, s->stall = s->clean = 0;
    return to;
}

/* ---- SBC's bitpool ----
 * The rungs: at 48 kHz joint stereo, 8 subbands and 16 blocks, 45 is about 309 kbit/s, 35 245, 29
 * 213, 23 177 and 19 153 (SBC's own; the link adds its headers). */
static const unsigned char sbc_rungs[] = { 45, 35, 29, 23, BT_SBC_FLOOR };
#define SBC_RUNGS ((int)sizeof sbc_rungs)
int bt_sbc_next(int pool, int own, int dir) {
    if (own <= 0) return -1;
    if (dir < 0) {
        int at = pool ? pool : own;
        for (int i = 0; i < SBC_RUNGS; ++i)
            if (sbc_rungs[i] < at) return sbc_rungs[i];
        return -1;
    }
    if (!pool) return -1;
    for (int i = SBC_RUNGS - 1; i >= 0; --i)
        if (sbc_rungs[i] > pool) return sbc_rungs[i] < own ? sbc_rungs[i] : 0;
    return 0;
}
/* bt_step's trigger and its clean seconds, a rung at a time each way: down after BT_STEP_STALL
 * congested seconds, BT_SBC_SETTLE_MS after a change for the queue to drain at the new rate; up
 * after BT_SBC_CLEAN clean ones and s->up_ms since the last change. A rung up undone within that
 * wait doubles it, so a link that carries one rung and not the next settles below. */
int bt_sbc_step(struct bt_sbc *s, unsigned now, int allowed, int rank, int own, int wq, int fly,
                int bufs) {
    if (allowed <= 0 || rank != 1) { /* another codec, or not SBC's to change: its own, once */
        int back = s->pool ? 0 : -1;
        s->pool = s->stall = s->clean = 0;
        return back;
    }
    if (own <= 0 || (s->have && now - s->last <= BT_SBC_SETTLE_MS)) {
        s->stall = s->clean = 0;
        return -1;
    }
    if (!s->up_ms) s->up_ms = BT_SBC_UP_MS;
    int full = fly >= 0 && bufs > 0 && fly >= bufs;
    s->stall = wq > BT_STEP_WQ && full ? s->stall + 1 : 0;
    s->clean = wq == 0 && fly >= 0 && !full ? s->clean + 1 : 0;
    int to = -1;
    if (s->stall >= BT_STEP_STALL && (to = bt_sbc_next(s->pool, own, -1)) >= 0) {
        if (s->raised && now - s->raised_at < s->up_ms)
            s->up_ms = s->up_ms * 2 > BT_SBC_UP_MAX_MS ? BT_SBC_UP_MAX_MS : s->up_ms * 2;
        s->raised = 0;
    } else if (s->clean >= BT_SBC_CLEAN && (!s->have || now - s->last >= s->up_ms) &&
               (to = bt_sbc_next(s->pool, own, 1)) >= 0) {
        s->raised = 1, s->raised_at = now;
    } else {
        return -1;
    }
    s->pool = to, s->have = 1, s->last = now, s->stall = s->clean = 0;
    return to;
}

/* config.ini's [section] key's value (up to its line's end), 0 when it has none */
static const char *ini_value(const char *ini, const char *section, const char *key) {
    int in = 0, k = (int)strlen(key), n = (int)strlen(section);
    for (const char *p = ini; p && *p;) {
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') ++p;
        if (*p == '[')
            in = !strncmp(p + 1, section, (size_t)n) && p[1 + n] == ']';
        else if (in && !strncmp(p, key, (size_t)k)) {
            const char *v = p + k;
            while (*v == ' ') ++v;
            if (*v == '=') {
                for (++v; *v == ' ';) ++v;
                return v;
            }
        }
        p = strchr(p, '\n');
    }
    return 0;
}
/* BTCODING's row when it is one of rows (n of them), else -1; BT_STEP_KEY's switch into *on */
static int coding_in(const char *ini, const int *rows, int n, int *on) {
    const char *coding = ini_value(ini, "SYSSET", "BTCODING"), *v;
    if (!coding || coding[0] < '0' || coding[0] > '9' || (coding[1] >= '0' && coding[1] <= '9'))
        return -1;
    v = ini_value(ini, "Q2POD", BT_STEP_KEY);
    *on = v && v[0] == '1' && !(v[1] >= '0' && v[1] <= '9'); /* off until turned on */
    for (int i = 0; i < n; ++i)
        if (coding[0] - '0' == rows[i]) return rows[i];
    return -1;
}
int bt_step_allowed(const char *ini) {
    static const int rows[] = { BT_LDAC_AUTO };
    int on;
    return coding_in(ini, rows, 1, &on) < 0 ? -1 : on;
}
int bt_sbc_allowed(const char *ini) {
    static const int rows[] = { BT_LDAC_AUTO, BT_SBC_HQ, BT_SBC_ROW };
    int on;
    return coding_in(ini, rows, 3, &on) < 0 ? -1 : on;
}
int bt_pcms_path(const char *text, char *path, int n) {
    const char *src = strstr(text, "/a2dpsrc"), *a = src, *b = src;
    if (!src) return 0;
    while (a > text && a[-1] != '"') --a;
    while (*b && *b != '"') ++b;
    if (a == text || *b != '"' || *a != '/' || b - a >= n) return 0;
    for (const char *c = a; c < b; ++c) /* it goes to a shell: only what an object path holds */
        if (!((*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') || (*c >= '0' && *c <= '9') ||
              *c == '_' || *c == '/'))
            return 0;
    memcpy(path, a, (size_t)(b - a));
    path[b - a] = 0;
    return 1;
}

/* ---- text ---- */
static const char *find_in(const char *p, const char *end, const char *needle) {
    const char *f = strstr(p, needle);
    return f && (!end || f < end) ? f : (const char *)0;
}
static const char *skip_space(const char *p) {
    while (*p == ' ' || *p == '\t') ++p;
    return p;
}
static const char *skip_word(const char *p) {
    while (*p && *p != ' ' && *p != '\t' && *p != '\n') ++p;
    return p;
}
static unsigned digits(const char *p) {
    unsigned v = 0;
    while (*p >= '0' && *p <= '9') v = v * 10 + (unsigned)(*p++ - '0');
    return v;
}

/* af_bluetooth.c bt_seq_show: a header naming the columns ("sk RefCnt Rmem Wmem User Inode Parent",
 * older kernels with Src and Dst), then a socket a line. The column is found by its name. */
int bt_l2cap_wmem(const char *t) {
    int col = -1, i = 0, best = -1;
    const char *p = skip_space(t);
    while (*p && *p != '\n') {
        if (!strncmp(p, "Wmem", 4) && (p[4] == ' ' || p[4] == '\t' || p[4] == '\n' || !p[4]))
            col = i;
        p = skip_space(skip_word(p)), ++i;
    }
    if (col < 0) return -1;
    while (*p == '\n') {
        p = skip_space(p + 1);
        for (i = 0; *p && *p != '\n'; p = skip_space(skip_word(p)), ++i)
            if (i == col && *p >= '0' && *p <= '9') {
                int v = (int)digits(p);
                if (v > best) best = v;
            }
    }
    return best < 0 ? 0 : best;
}

int bt_encoder_thread(const char *comm) { return !strncmp(comm, "ba-a2dp-", 8); }

/* A field of decimal nanoseconds as whole ms modulo 2^32: its last six digits dropped, no 64-bit
 * arithmetic. 0 when the field has no digits. */
static int ns_ms(const char **p, unsigned *ms) {
    const char *s = skip_space(*p), *e = s;
    while (*e >= '0' && *e <= '9') ++e;
    if (e == s) return 0;
    unsigned v = 0;
    for (const char *d = s; d < e - 6; ++d) v = v * 10 + (unsigned)(*d - '0');
    *ms = v, *p = e;
    return 1;
}
int bt_schedstat(const char *text, unsigned *run_ms, unsigned *wait_ms) {
    const char *p = text;
    return ns_ms(&p, run_ms) && ns_ms(&p, wait_ms) && *skip_space(p) >= '0' &&
           *skip_space(p) <= '9';
}

/* dbus-send --print-reply: each PCM an `object path "/org/bluealsa/hci0/dev_.../a2dpsrc/sink"`,
 * then its properties as dict entries, `string "Codec"` and on the next line `variant string
 * "aptX-HD"`. */
int bt_pcms(const char *text, char *codec, int n, unsigned *hz, unsigned *delay) {
    const char *pcm = strstr(text, "/a2dpsrc");
    if (!pcm || n < 1) return 0;
    const char *end = strstr(pcm, "object path \"/org/bluealsa"), *v;
    codec[0] = 0, *hz = *delay = 0;
    if ((v = find_in(pcm, end, "\"Codec\"")) && (v = find_in(v + 7, end, "string \""))) {
        int i = 0;
        for (v += 8; *v && *v != '"' && i < n - 1; ++v) codec[i++] = *v;
        codec[i] = 0;
    }
    if ((v = find_in(pcm, end, "\"Sampling\"")) && (v = find_in(v, end, "uint32 ")))
        *hz = digits(v + 7);
    if ((v = find_in(pcm, end, "\"Delay\"")) && (v = find_in(v, end, "uint16 ")))
        *delay = digits(v + 7);
    return codec[0] != 0;
}

/* ---- the line ---- */
static int put(char *b, int at, int size, const char *key, int v, int unset) {
    if (at >= size) return at;
    int n = v == unset ? snprintf(b + at, (size_t)(size - at), " %s=-", key)
                       : snprintf(b + at, (size_t)(size - at), " %s=%d", key, v);
    return n < 0 ? at : at + n;
}
static int put_tenths(char *b, int at, int size, const char *key, int v) { /* 90 as 9.0 */
    if (at >= size) return at;
    int n = v < 0 ? snprintf(b + at, (size_t)(size - at), " %s=-", key)
                  : snprintf(b + at, (size_t)(size - at), " %s=%d.%d", key, v / 10, v % 10);
    return n < 0 ? at : at + n;
}
/* " ue=fe/pe/brk/oe/bo", or " ue=0" when none moved (most seconds: the line stays short) */
static int put_ue(char *b, int at, int size, const int *ue, int ok) {
    if (at >= size) return at;
    int none = ok && !(ue[0] | ue[1] | ue[2] | ue[3] | ue[4]);
    int n = none ? snprintf(b + at, (size_t)(size - at), " ue=0")
            : ok ? snprintf(b + at, (size_t)(size - at), " ue=%d/%d/%d/%d/%d", ue[0], ue[1], ue[2], ue[3], ue[4])
               : snprintf(b + at, (size_t)(size - at), " ue=-");
    return n < 0 ? at : at + n;
}
/* "TIME up=S kbps= brx= acl= wq= rssi= afh= wtx= wrx= cpu= ba= own= h= er= done= fly= bp= pl= dk= gw=
 * ue=\n", "-" for not read, ba, own and pl as percent of one core (9.0). The fields after own come
 * last, so a reader that knows only those before them reads a line too. */
int bt_line(char *out, int size, const struct bt_sample *s) {
    char b[260];
    int at = snprintf(b, sizeof b, "up=%u", s->up);
    at = put(b, at, sizeof b, "kbps", s->kbps, -1);
    at = put(b, at, sizeof b, "brx", s->brx, -1);
    at = put(b, at, sizeof b, "acl", s->acl, -1);
    at = put(b, at, sizeof b, "wq", s->wq, -1);
    at = put(b, at, sizeof b, "rssi", s->rssi, BT_UNSET);
    at = put(b, at, sizeof b, "afh", s->afh, BT_UNSET);
    at = put(b, at, sizeof b, "wtx", s->wtx, -1);
    at = put(b, at, sizeof b, "wrx", s->wrx, -1);
    at = put(b, at, sizeof b, "cpu", s->cpu, -1);
    at = put_tenths(b, at, sizeof b, "ba", s->ba);
    at = put_tenths(b, at, sizeof b, "own", s->own);
    at = put(b, at, sizeof b, "h", s->h, -1);
    at = put(b, at, sizeof b, "er", s->er, -1);
    at = put(b, at, sizeof b, "done", s->done, -1);
    at = put(b, at, sizeof b, "fly", s->fly, -1);
    at = put(b, at, sizeof b, "bp", s->bp, -1);
    at = put_tenths(b, at, sizeof b, "pl", s->pl);
    at = put(b, at, sizeof b, "dk", s->dk, -1);
    at = put(b, at, sizeof b, "gw", s->gw, -1);
    put_ue(b, at, sizeof b, s->ue, s->ue_ok);
    return heat_note(out, size, s->wall, b);
}

/* ---- the recorder (docs/bluetooth.md's sampler) ---- */
int bt_acl_links(const unsigned char *req, int max, struct bt_acl *out, int n) {
    int got = 0, listed = u16_at(req + 2);
    for (int i = 0; i < listed && i < max && got < n; ++i) {
        const unsigned char *c = req + 4 + 16 * i;
        if (c[8] != 1) continue; /* ACL_LINK; SCO is 0, LE 0x80 */
        out[got].handle = u16_at(c) & 0x0fff;
        memcpy(out[got].addr, c + 2, 6);
        ++got;
    }
    return got;
}
static int put_ms(char *b, int at, int size, const char *key, int slots, const char *zero) {
    if (at >= size) return at;
    int n = slots == BT_UNSET ? snprintf(b + at, (size_t)(size - at), " %s=-", key)
            : !slots && zero  ? snprintf(b + at, (size_t)(size - at), " %s=%s", key, zero)
                              : snprintf(b + at, (size_t)(size - at), " %s=%dms", key, slots * 5 / 8);
    return n < 0 ? at : at + n;
}
int bt_acl_line(char *out, int size, long wall, const struct bt_link *k, const unsigned char *addr, int info) {
    char b[160], dev[18];
    bt_addr_text(addr, dev);
    int at = snprintf(b, sizeof b, "%s h=%d dev=%s", info ? "ACLINFO" : "ACL", k->handle, dev);
    if (!info) {
        at = put(b, at, sizeof b, "rssi", k->rssi, BT_UNSET);
        put(b, at, sizeof b, "afh", k->afh, BT_UNSET);
        return heat_note(out, size, wall, b);
    }
    if (at < (int)sizeof b) {
        int n = snprintf(b + at, sizeof b - (size_t)at, " role=%s",
                         k->role == 0 ? "central" : k->role == 1 ? "peripheral" : "-");
        at = n < 0 ? at : at + n;
    }
    at = put_ms(b, at, sizeof b, "lsto", k->lsto, 0);
    at = put_ms(b, at, sizeof b, "flush", k->flush, "none");
    put(b, at, sizeof b, "txmax", k->txmax, BT_UNSET);
    return heat_note(out, size, wall, b);
}
int bt_idle_line(char *out, int size, const struct bt_idle *s) {
    char b[160];
    int at = snprintf(b, sizeof b, "IDLE up=%u", s->up);
    at = put(b, at, sizeof b, "btx", s->btx, -1);
    at = put(b, at, sizeof b, "brx", s->brx, -1);
    at = put(b, at, sizeof b, "wtx", s->wtx, -1);
    at = put(b, at, sizeof b, "wrx", s->wrx, -1);
    at = put(b, at, sizeof b, "wq", s->wq, -1);
    put_ue(b, at, sizeof b, s->ue, s->ue_ok);
    return heat_note(out, size, s->wall, b);
}
/* The labels as rtl8733bs.ko prints them, matched at a line's start once its spaces are passed. TDMA
 * is the one whose label is a word of its own ("TDMA_Now" and the toggles' are other lines). */
static const char *const coex_keys[] = { "RSSI/ STA_Chnl", "Band/ BW/ Traffic", "Power Status",  "Rx_Tp/ Tx_Tp",
                                         "BT status/ rssi", "Profiles",          "Role/RoleSwCnt", "AFH MAP",
                                         "TDMA ",           "Coex_Mode",         "Null All",       "Cnt TDMA_Togg",
                                         "BT slot avg",     "0x770",             "0x774",          "Path Owner" };
int bt_coex_filter(const char *text, char *out, int size) {
    int at = 0, got = 0;
    if (size > 0) out[0] = 0;
    for (const char *l = text; l && *l; l = strchr(l, '\n'), l = l ? l + 1 : 0) {
        while (*l == ' ' || *l == '\t') ++l;
        int k = 0, nk = (int)(sizeof coex_keys / sizeof *coex_keys);
        while (k < nk && strncmp(l, coex_keys[k], strlen(coex_keys[k]))) ++k;
        if (k == nk) continue;
        if (got && at < size - 1) out[at++] = '\n';
        for (const char *c = l; *c && *c != '\n' && *c != '\r' && at < size - 1; ++c)
            if (!(*c == ' ' || *c == '\t') || (c[1] != ' ' && c[1] != '\t' && c[1] != '\n' && c[1])) /* runs of spaces as one */
                out[at++] = *c == '\t' ? ' ' : *c;
        out[at] = 0;
        ++got;
    }
    return got;
}
int bt_wpa_freq(const char *status, char *state, int n) {
    int freq = 0;
    if (n > 0) state[0] = 0;
    for (const char *l = status; l && *l; l = strchr(l, '\n'), l = l ? l + 1 : 0) {
        if (!strncmp(l, "freq=", 5))
            for (const char *d = l + 5; *d >= '0' && *d <= '9'; ++d) freq = freq * 10 + (*d - '0');
        if (!strncmp(l, "wpa_state=", 10)) {
            int i = 0;
            for (const char *d = l + 10; *d && *d != '\n' && *d != '\r' && i < n - 1; ++d) state[i++] = *d;
            if (n > 0) state[i] = 0;
        }
    }
    return freq;
}
int bt_wifi_line(char *out, int size, long wall, int freq, const char *state) {
    char b[96];
    const char *band = "-";
    int ch = -1;
    if (freq >= 2412 && freq <= 2484) band = "2.4", ch = freq == 2484 ? 14 : (freq - 2407) / 5;
    else if (freq >= 5955 && freq <= 7115) band = "6", ch = (freq - 5950) / 5;
    else if (freq >= 5000 && freq < 5955) band = "5", ch = (freq - 5000) / 5;
    int at = snprintf(b, sizeof b, "WIFI");
    at = put(b, at, sizeof b, "freq", freq ? freq : -1, -1);
    if (at < (int)sizeof b) {
        int k = snprintf(b + at, sizeof b - (size_t)at, " band=%s", band);
        at = k < 0 ? at : at + k;
    }
    at = put(b, at, sizeof b, "ch", ch, -1);
    if (at < (int)sizeof b) snprintf(b + at, sizeof b - (size_t)at, " state=%s", state && *state ? state : "-");
    return heat_note(out, size, wall, b);
}
int bt_pace(int streaming, int freq) { return streaming && freq >= 2400 && freq < 2500; }
int bt_pace_text(char *out, int size, int paced, int freq) {
    int n = snprintf(out, (size_t)size, "paced=%d rate=%d freq=%d\n", paced ? 1 : 0, paced ? BT_PACE_RATE : 0, freq);
    return n < 0 ? 0 : n;
}

/* ---- the player's side ---- */
static int number_at(const char *s, unsigned *v) {
    while (s && *s == ' ') ++s;
    if (!s || *s < '0' || *s > '9') return 0;
    unsigned n = 0;
    for (; *s >= '0' && *s <= '9'; ++s) n = n * 10 + (unsigned)(*s - '0');
    *v = n;
    return 1;
}
int bt_disk_busy(const char *text, const char *dev, unsigned *ms) {
    size_t n = strlen(dev);
    for (const char *p = text; p && *p; p = strchr(p, '\n'), p = p ? p + 1 : 0) {
        const char *s = p;
        for (int i = 0; i < 2; ++i) { /* major, minor */
            while (*s == ' ') ++s;
            while (*s >= '0' && *s <= '9') ++s;
        }
        while (*s == ' ') ++s;
        if (strncmp(s, dev, n) || s[n] != ' ') continue;
        s += n;
        unsigned v = 0;
        for (int i = 0; i < 10; ++i) { /* io_ticks is the tenth number after the name */
            while (*s == ' ') ++s;
            if (!number_at(s, &v)) return 0;
            while (*s >= '0' && *s <= '9') ++s;
        }
        *ms = v;
        return 1;
    }
    return 0;
}
int bt_song_path(const char *path) {
    return !strncmp(path, "/mnt/mmc/", 9) && strncmp(path, "/mnt/mmc/.q2muse/", 17);
}

/* ---- a wedged controller (docs/internals.md#bluetooth-controller-watchdog) ----
 * The H5 lines are Realtek's out-of-tree driver's (its "Error in H5 hdr checksum", not mainline's
 * "Invalid header checksum"); the timeout is hci_core's hci_cmd_timeout. */
int bt_kmsg_sign(const char *msg) {
    static const char *const h5[] = { "Peer acked invalid packet", "Error in H5 hdr checksum",
                                      "Short H5 packet" };
    for (unsigned i = 0; i < sizeof h5 / sizeof *h5; ++i)
        if (strstr(msg, h5[i])) return BT_SIGN_H5;
    if (strstr(msg, "command 0x") && strstr(msg, " tx timeout")) return BT_SIGN_TIMEOUT;
    return BT_SIGN_NONE;
}
/* The microseconds are past 2^32 after 72 minutes up: ms is every digit but the last three, each
 * taken modulo 2^32 (no 64-bit division) */
int bt_kmsg_split(const char *rec, unsigned *ms, const char **msg) {
    const char *s = rec;
    for (int i = 0; i < 2; ++i) { /* PRI, SEQ */
        while (*s >= '0' && *s <= '9') ++s;
        if (*s++ != ',') return 0;
    }
    const char *d = s;
    while (*s >= '0' && *s <= '9') ++s;
    if (s == d || *s != ',') return 0;
    unsigned v = 0;
    for (const char *p = d; p < s - 3; ++p) v = v * 10 + (unsigned)(*p - '0');
    const char *m = strchr(s, ';');
    if (!m) return 0;
    *ms = v, *msg = m + 1;
    return 1;
}
/* serial_core's uart_line_info writes a port a line, "0: uart:uart0 mmio:0x10030000 irq:55 tx:N rx:N", then fe:,
 * pe:, brk:, oe: and bo: each only when it is not 0, then the modem lines; the counts run from boot */
int bt_uart_counts(const char *text, int port, unsigned *v) {
    static const char *const key[BT_UART_N] = { "tx", "rx", "fe", "pe", "brk", "oe", "bo" };
    char head[16];
    int hn = snprintf(head, sizeof head, "%d: ", port);
    for (const char *l = text; l && *l; l = strchr(l, '\n'), l = l ? l + 1 : 0) {
        if (strncmp(l, head, (size_t)hn)) continue;
        const char *end = strchr(l, '\n'), *t = l;
        if (!end) end = l + strlen(l);
        int got = 0;
        memset(v, 0, BT_UART_N * sizeof *v);
        while (t < end) {
            while (t < end && *t == ' ') ++t;
            for (int k = 0; k < BT_UART_N; ++k) {
                size_t n = strlen(key[k]);
                if (!strncmp(t, key[k], n) && t[n] == ':' && t[n + 1] >= '0' && t[n + 1] <= '9') {
                    unsigned x = 0;
                    for (const char *d = t + n + 1; *d >= '0' && *d <= '9'; ++d) x = x * 10 + (unsigned)(*d - '0');
                    v[k] = x, got |= 1 << k;
                    break;
                }
            }
            while (t < end && *t != ' ') ++t;
        }
        return (got & 3) == 3; /* tx and rx: a port the driver counts */
    }
    return 0;
}
int bt_wifi_ips(const char *text, unsigned *enter, unsigned *leave) {
    static const char a[] = "IPS enter count:", b[] = ", IPS leave count:";
    const char *s = strstr(text, a);
    unsigned e = 0, l = 0;
    if (!s) return 0;
    s += sizeof a - 1;
    if (*s < '0' || *s > '9') return 0;
    for (; *s >= '0' && *s <= '9'; ++s) e = e * 10 + (unsigned)(*s - '0');
    if (strncmp(s, b, sizeof b - 1)) return 0;
    s += sizeof b - 1;
    if (*s < '0' || *s > '9') return 0;
    for (; *s >= '0' && *s <= '9'; ++s) l = l * 10 + (unsigned)(*s - '0');
    *enter = e, *leave = l;
    return 1;
}
int bt_h5dbg_kind(const char *msg) {
    static const char *const part[BT_H5D_KINDS] = { " retransmitting ", "Request for pkt ", "Received seqno ",
                                                    "Removing ", "Sending packet with seqno ",
                                                    "We request packet no " };
    for (int k = 0; k < BT_H5D_KINDS; ++k)
        if (strstr(msg, part[k])) return k;
    return -1;
}
int bt_h5dbg_line(struct bt_h5dbg *d, const char *msg) {
    int k = bt_h5dbg_kind(msg);
    if (k < 0 || d->lines >= BT_H5D_CAP || !strncmp(d->last[k], msg, BT_H5D_TEXT - 1)) return 0;
    snprintf(d->last[k], BT_H5D_TEXT, "%s", msg);
    ++d->lines;
    return 1;
}
int bt_h5dbg_ctl(char *b, int cap, int on) {
    static const char *const func[] = { "h5_timed_event", "h5_complete_rx_pkt", "h5_pkt_cull", "h5_prepare_pkt" };
    int n = 0;
    for (unsigned i = 0; i < sizeof func / sizeof *func; ++i) {
        int m = snprintf(b + n, (size_t)(cap - n), "module " BT_H5D_MODULE " func %s %cp\n", func[i], on ? '+' : '-');
        if (m < 0 || m >= cap - n) return 0;
        n += m;
    }
    return n;
}
static int settling(const struct bt_wedge *w, unsigned now) {
    return w->asked || (w->have_reset && now - w->reset_at < BT_RESET_SETTLE_MS);
}
int bt_wedge_line(struct bt_wedge *w, unsigned now, unsigned kms, const char *msg) {
    int sign = bt_kmsg_sign(msg), storm = 0;
    if (sign == BT_SIGN_NONE || settling(w, now)) return sign;
    if (sign == BT_SIGN_H5) {
        w->h5[w->h5_i] = kms, w->h5_i = (w->h5_i + 1) % BT_H5_STORM;
        if (w->h5_n < BT_H5_STORM) ++w->h5_n;
        /* the oldest of the last BT_H5_STORM, where the ring goes next */
        storm = w->h5_n == BT_H5_STORM && kms - w->h5[w->h5_i] <= BT_H5_WINDOW_MS;
        if (!storm) return sign;
    }
    if (!w->suspect) {
        w->suspect = storm ? 's' : 't', w->first = now, w->storm = w->timeout = 0;
        w->fails = 0, w->deferred = 0;
    }
    if (storm) w->storm = 1;
    else w->timeout = 1;
    w->last = now;
    return sign;
}
int bt_wedge_step(struct bt_wedge *w, unsigned now) {
    if (w->asked) {
        if (now - w->reset_at < BT_RESET_WAIT_MS) return BT_W_NONE;
        w->asked = 0; /* the relaunch counts as made: the backoff and the settle run from here */
        w->reset_at = now;
        return BT_W_LATE;
    }
    if (!w->suspect) return BT_W_NONE;
    if (!w->fails && now - w->last >= BT_SUSPECT_MS) {
        w->suspect = 0;
        return BT_W_NONE;
    }
    if (w->calm && (int)(now - w->calm_until) < 0) return BT_W_NONE;
    if (w->probed && now - w->probed_at < BT_PROBE_GAP_MS) return BT_W_NONE;
    return BT_W_PROBE;
}
static int defer(struct bt_wedge *w) {
    if (w->deferred) return BT_W_NONE;
    w->deferred = 1;
    return BT_W_DEFER;
}
int bt_wedge_probed(struct bt_wedge *w, unsigned now, int answered, int busy) {
    w->probed = 1, w->probed_at = now;
    if (answered < 0) return BT_W_NONE;
    if (answered) {
        int was = w->suspect || w->fails;
        w->suspect = 0, w->fails = 0, w->gave_up = 0;
        w->calm = 1, w->calm_until = now + BT_PROBE_CALM_MS;
        return was ? BT_W_HEALTHY : BT_W_NONE;
    }
    if (++w->fails < BT_PROBE_FAILS) return BT_W_NONE;
    if (w->gave_up) {
        w->calm = 1, w->calm_until = now + BT_PROBE_CALM_MS;
        return BT_W_NONE;
    }
    int in_row = w->have_reset && now - w->reset_at < BT_RESET_CURED_MS;
    int n = in_row ? w->resets + 1 : 1;
    if (n > BT_RESET_MAX) {
        w->gave_up = 1, w->calm = 1, w->calm_until = now + BT_PROBE_CALM_MS;
        return BT_W_GIVEUP;
    }
    if (in_row && now - w->reset_at < (unsigned)BT_RESET_GAP_MS << (w->resets - 1)) return defer(w);
    if (busy) return defer(w);
    w->resets = n, w->have_reset = 1, w->reset_at = now, w->asked = 1, ++w->req;
    w->suspect = 0, w->fails = 0, w->deferred = 0, w->h5_n = 0;
    return BT_W_RESET;
}
void bt_wedge_done(struct bt_wedge *w, unsigned now, int n) {
    if (!w->asked || n != w->req) return;
    w->asked = 0, w->reset_at = now, w->h5_n = 0;
}
int bt_probe_answer(const unsigned char *p, int n) {
    return n >= 6 && p[0] == 4 && p[1] == 0x0e && u16_at(p + 4) == BT_OP_VERSION;
}
int bt_dev_up(const unsigned char *di) { return (int)(u32_at(di + 16) & 1); }
int bt_answer_parse(const char *ack, int *off) {
    for (const char *p = ack; p && *p; p = strchr(p, '\n'), p = p ? p + 1 : 0) {
        unsigned n;
        if (strncmp(p, "bt ", 3) || !number_at(p + 3, &n)) continue;
        const char *s = p + 3;
        while (*s >= '0' && *s <= '9') ++s;
        *off = !strncmp(s, " off", 4);
        return (int)n;
    }
    *off = 0;
    return 0;
}

#ifdef Q2GUARD_HOST
int bt_wedge_size(void) { return (int)sizeof(struct bt_wedge); } /* the host test's layout check */
#endif

int bt_flush(card_log *l, const char *dir) {
    return log_flush(l, dir, "q2-bt", BT_FILE_MAX, BT_FILES);
}

/* ---- devices: the kernel's management events (docs/internals.md#bluetooth-devices) ----
 * Layouts from Linux 5.10's include/net/bluetooth/mgmt.h (the Q2 runs 5.10.21), all packed and
 * little-endian: struct mgmt_hdr is opcode, index, len (6 bytes), then the event's parameters;
 * struct mgmt_addr_info is bdaddr[6] and its type (7). mgmt_ev_device_connected: addr, flags (4),
 * eir_len (2), eir (13 on); mgmt_ev_device_disconnected: addr, reason; mgmt_ev_connect_failed and
 * mgmt_ev_auth_failed: addr, status (a mgmt status: net/bluetooth/mgmt.c mgmt_status() folds the
 * controller's HCI code into one); mgmt_ev_device_unpaired: addr; mgmt_ev_new_link_key:
 * store_hint, then mgmt_link_key_info, which starts with the addr (the key after it is never
 * read). */
void bt_addr_text(const unsigned char *bdaddr, char *out) {
    static const char hex[] = "0123456789ABCDEF";
    for (int i = 0; i < 6; ++i) {
        out[3 * i] = hex[bdaddr[5 - i] >> 4], out[3 * i + 1] = hex[bdaddr[5 - i] & 15];
        out[3 * i + 2] = i < 5 ? ':' : 0;
    }
}
/* EIR's structures: each a length (of what follows it), a type and its data; the complete name
 * (0x09) over a shortened one (0x08) */
static void eir_name(const unsigned char *eir, int n, char *name, int size) {
    name[0] = 0;
    for (int i = 0; i + 1 < n && eir[i];) {
        int len = eir[i], type = eir[i + 1];
        if (i + 1 + len > n) break;
        if ((type == 0x09 || (type == 0x08 && !name[0])) && len > 1) {
            int k = len - 1 < size - 1 ? len - 1 : size - 1;
            memcpy(name, eir + i + 2, (size_t)k), name[k] = 0;
        }
        i += 1 + len;
    }
}
int bt_mgmt_parse(const unsigned char *p, int n, struct bt_mgmt_ev *e) {
    if (n < 6) return 0;
    int op = u16_at(p), len = u16_at(p + 4), at = 0, need = 7;
    const unsigned char *q = p + 6;
    if (len > n - 6) return 0; /* cut short: a read can hold only part of a datagram */
    e->out = e->rssi = -1;
    if (op == BT_MGMT_EV_DISCOVERING) { /* type (a mask of address types), discovering */
        if (len < 2) return 0;
        memset(e->addr, 0, 6);
        e->op = op, e->type = q[0], e->code = q[1], e->name[0] = 0;
        return 1;
    }
    switch (op) {
    case BT_MGMT_EV_DEVICE_FOUND: /* addr, rssi (signed), flags (4), eir_len (2), eir (14 on) */
        need = 14;
        break;
    case BT_MGMT_EV_NEW_LINK_KEY:
        at = 1, need = 8;
        break;
    case BT_MGMT_EV_DEVICE_CONNECTED:
        need = 13;
        break;
    case BT_MGMT_EV_DEVICE_DISCONNECTED:
    case BT_MGMT_EV_CONNECT_FAILED:
    case BT_MGMT_EV_AUTH_FAILED:
        need = 8;
        break;
    case BT_MGMT_EV_DEVICE_UNPAIRED:
        break;
    default:
        return 0;
    }
    if (len < need) return 0;
    e->op = op;
    memcpy(e->addr, q + at, 6);
    e->type = q[at + 6];
    e->code = need == 8 && !at ? q[7] : -1;
    e->name[0] = 0;
    if (op == BT_MGMT_EV_DEVICE_FOUND) {
        int eir = u16_at(q + 12);
        e->rssi = (signed char)q[7];
        eir_name(q + 14, eir < len - 14 ? eir : len - 14, e->name, sizeof e->name);
    }
    if (op == BT_MGMT_EV_DEVICE_CONNECTED) {
        int eir = u16_at(q + 11);
        eir_name(q + 13, eir < len - 13 ? eir : len - 13, e->name, sizeof e->name);
    }
    return 1;
}
/* a discovery's type, a mask of address types (BDADDR_BREDR 0, BDADDR_LE_PUBLIC 1,
 * BDADDR_LE_RANDOM 2: hdev->discovery.type) */
static const char *disc_word(int t) {
    return t == 1 ? "BR/EDR" : t == 6 ? "LE" : t == 7 ? "BR/EDR+LE" : "other";
}
/* mgmt.h's MGMT_DEV_DISCONN_* */
static const char *const disconn_words[6] = { "unspecified",           "timeout or link loss",
                                              "this Q2 ended it",      "the device ended it",
                                              "authentication failed", "suspend" };
/* mgmt.h's MGMT_STATUS_*, the ones a connect or authentication failure can carry */
static const char *status_word(int s) {
    switch (s) {
    case 0x02:
        return "not connected";
    case 0x03:
        return "failed";
    case 0x04:
        return "no answer (page timeout)";
    case 0x05:
        return "authentication failed";
    case 0x06:
        return "not paired";
    case 0x07:
        return "no resources";
    case 0x08:
        return "timed out";
    case 0x09:
        return "already connected";
    case 0x0a:
        return "busy";
    case 0x0b:
        return "rejected";
    case 0x0c:
        return "not supported";
    case 0x0d:
        return "invalid parameters";
    case 0x0e:
        return "disconnected";
    case 0x10:
        return "cancelled";
    case 0x12:
        return "radio blocked";
    default:
        return "other";
    }
}
char bt_mgmt_line(const struct bt_mgmt_ev *e, const char *name, char *out, int size) {
    char a[18], who[80] = "";
    const char *what = "", *why = "";
    char code[16] = "", level = 'I';
    if (e->op == BT_MGMT_EV_DISCOVERING) {
        snprintf(out, (size_t)size, "discovery %s %s", e->code ? "started" : "stopped",
                 disc_word(e->type));
        return 'I';
    }
    bt_addr_text(e->addr, a);
    if (name && name[0]) snprintf(who, sizeof who, " \"%s\"", name);
    switch (e->op) {
    case BT_MGMT_EV_DEVICE_CONNECTED:
        what = "connected";
        if (e->out >= 0) snprintf(code, sizeof code, " by=%s", e->out ? "q2" : "device");
        break;
    case BT_MGMT_EV_DEVICE_FOUND:
        what = "found";
        snprintf(code, sizeof code, " rssi=%d", e->rssi);
        why = e->type ? "LE" : "BR/EDR";
        snprintf(out, (size_t)size, "%s %s%s%s %s", what, a, who, code, why);
        return 'I';
    case BT_MGMT_EV_DEVICE_DISCONNECTED:
        what = "disconnected";
        why = e->code >= 0 && e->code < 6 ? disconn_words[e->code] : "other";
        snprintf(code, sizeof code, " reason=%d", e->code);
        level = e->code == 2 ? 'I' : 'W';
        break;
    case BT_MGMT_EV_CONNECT_FAILED:
    case BT_MGMT_EV_AUTH_FAILED:
        what = e->op == BT_MGMT_EV_CONNECT_FAILED ? "connect failed" : "auth failed";
        why = status_word(e->code);
        snprintf(code, sizeof code, " status=0x%02x", e->code);
        level = 'W';
        break;
    case BT_MGMT_EV_DEVICE_UNPAIRED:
        what = "forgotten";
        break;
    case BT_MGMT_EV_NEW_LINK_KEY:
        what = "paired";
        break;
    default:
        return 0;
    }
    snprintf(out, (size_t)size, "%s %s%s%s%s%s%s", what, a, who, e->type ? " LE" : "", code,
             why[0] ? " " : "", why);
    return level;
}
int bt_info_name(const char *text, char *name, int size) {
    for (const char *p = text; p && *p; p = strchr(p, '\n'), p = p ? p + 1 : 0) {
        if (strncmp(p, "Name=", 5)) continue;
        int k = 0;
        for (p += 5; p[k] && p[k] != '\n' && p[k] != '\r' && k < size - 1; ++k) name[k] = p[k];
        name[k] = 0;
        return k > 0;
    }
    name[0] = 0;
    return 0;
}
const char *bt_names_get(const struct bt_names *c, const unsigned char *bdaddr) {
    for (int i = 0; i < BT_NAMES; ++i)
        if (c->name[i][0] && !memcmp(c->addr[i], bdaddr, 6)) return c->name[i];
    return 0;
}
void bt_names_put(struct bt_names *c, const unsigned char *bdaddr, const char *name) {
    if (!name || !name[0]) return;
    int i = 0;
    while (i < BT_NAMES && !(c->name[i][0] && !memcmp(c->addr[i], bdaddr, 6))) ++i;
    if (i == BT_NAMES) i = c->next, c->next = (c->next + 1) % BT_NAMES;
    memcpy(c->addr[i], bdaddr, 6);
    /* copied a byte at a time, not with snprintf: name is often this slot itself (bt_names_get's
     * answer put back), and glibc 2.28's snprintf empties its buffer before it reads "%s" */
    int k = 0;
    for (; name[k] && k < (int)sizeof c->name[i] - 1; ++k) c->name[i][k] = name[k];
    c->name[i][k] = 0;
}

void bt_found_reset(struct bt_found *f) { f->n = 0, f->capped = 0; }
int bt_found_first(struct bt_found *f, const unsigned char *bdaddr) {
    for (int i = 0; i < f->n; ++i)
        if (!memcmp(f->addr[i], bdaddr, 6)) return 0;
    if (f->n == BT_FOUND_MAX) return f->capped++ ? 0 : -1;
    memcpy(f->addr[f->n++], bdaddr, 6);
    return 1;
}
/* hci_conn_list_req's answer: who began the ACL link to bdaddr, the kernel's hci_conn->out
 * (hci_conn.c hci_acl_create_connection sets it for a Create Connection this host sent): 1 the Q2,
 * 0 the device, -1 not listed */
int bt_acl_out(const unsigned char *req, int max, const unsigned char *bdaddr) {
    int n = u16_at(req + 2);
    for (int i = 0; i < n && i < max; ++i) {
        const unsigned char *c = req + 4 + 16 * i;
        if (c[8] == 1 && !memcmp(c + 2, bdaddr, 6)) return c[9] != 0;
    }
    return -1;
}
