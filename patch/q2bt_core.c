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
 * err_rx, err_tx (56), cmd_tx, evt_rx, acl_tx (68), acl_rx, sco_tx, sco_rx, byte_rx, byte_tx (88).
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
}
/* hci_conn_list_req: dev_id, conn_num, then conn_num hci_conn_info of 16 bytes: handle, bdaddr[6],
 * type (8; ACL_LINK is 1), out, state, link_mode. */
int bt_acl_handle(const unsigned char *req, int max) {
    int n = u16_at(req + 2);
    for (int i = 0; i < n && i < max; ++i) {
        const unsigned char *c = req + 4 + 16 * i;
        if (c[8] == 1) return u16_at(c) & 0x0fff;
    }
    return -1;
}

/* ---- the five commands ---- */
int bt_cmd(unsigned char *out, int op, int handle) {
    int plen = op == BT_OP_TPL ? 3 : 2;
    out[0] = 1; /* HCI_COMMAND_PKT */
    out[1] = (unsigned char)op, out[2] = (unsigned char)(op >> 8), out[3] = (unsigned char)plen;
    out[4] = (unsigned char)handle, out[5] = (unsigned char)(handle >> 8 & 0x0f);
    out[6] = 0; /* Read Transmit Power Level's type: the current level */
    return 4 + plen;
}
int bt_handle_follow(struct bt_link *k, int listed) {
    if (listed == k->handle) return 0;
    k->handle = listed;
    return 1;
}
void bt_link_clear(struct bt_link *k, int handle) {
    k->handle = handle, k->answered = 0;
    k->rssi = k->lq = k->tpl = k->afh = k->fcc = BT_UNSET;
}
/* Command Complete (0x0e): ncmd, the opcode, then each of the five's return parameters: status, the
 * handle, its value (AFH: its mode, then the 10-byte map, channels 0-78; the counter: 16 bits). A
 * refusal (status not 0) is answered, its value left unset. */
int bt_event(struct bt_link *k, const unsigned char *p, int n) {
    if (n < 9 || p[0] != 4 || p[1] != 0x0e || n < 3 + p[2]) return 0;
    int op = p[4] | p[5] << 8, status = p[6], handle = (p[7] | p[8] << 8) & 0x0fff;
    if (op != BT_OP_RSSI && op != BT_OP_LQ && op != BT_OP_TPL && op != BT_OP_AFH && op != BT_OP_FCC)
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
    if (op == BT_OP_FCC) {
        if (n >= 11) k->fcc = p[9] | p[10] << 8;
        return 1;
    }
    if (n < 10) return 1;
    int v = op == BT_OP_LQ ? p[9] : (signed char)p[9];
    if (op == BT_OP_RSSI)
        k->rssi = v;
    else if (op == BT_OP_LQ)
        k->lq = v;
    else
        k->tpl = v;
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

/* ---- the step-down ---- */
static const char *const codec_names[4] = { "", "SBC", "aptX", "aptX-HD" };
int bt_codec_rank(const char *codec) {
    for (int r = 1; r < 4; ++r)
        if (!strcmp(codec, codec_names[r])) return r;
    return 0;
}
const char *bt_codec_name(int rank) { return codec_names[rank > 0 && rank < 4 ? rank : 0]; }
/* Congested: the socket queued past BT_STEP_WQ and the controller holding every buffer, so the
 * radio, not the encoder or the player, is behind. Down at BT_STEP_STALL such seconds in a row;
 * up one rank after BT_STEP_CLEAN clean ones and BT_STEP_UP_MS since the last switch. Off (not
 * LDAC Auto, or the setting off) while stepped down: back to where it started, once. */
int bt_step(struct bt_step *s, unsigned now, int allowed, int rank, int wq, int fly, int bufs) {
    if (!rank) {
        s->stall = s->clean = 0;
        return 0;
    }
    if (rank > s->top) s->top = rank;
    if (!allowed) {
        int back = rank < s->top ? s->top : 0;
        s->top = rank, s->stall = s->clean = 0; /* asked once: the next line sees it as the top */
        if (back) s->have = 1, s->last = now;
        return back;
    }
    if (s->have && now - s->last < BT_STEP_SETTLE_MS) {
        s->stall = s->clean = 0;
        return 0;
    }
    int full = fly >= 0 && bufs > 0 && fly >= bufs;
    s->stall = wq > BT_STEP_WQ && full ? s->stall + 1 : 0;
    s->clean = wq == 0 && fly >= 0 && !full ? s->clean + 1 : 0;
    int to = 0;
    if (s->stall >= BT_STEP_STALL && rank > 1)
        to = rank - 1;
    else if (s->clean >= BT_STEP_CLEAN && rank < s->top &&
             (!s->have || now - s->last >= BT_STEP_UP_MS))
        to = rank + 1;
    if (to) s->have = 1, s->last = now, s->stall = s->clean = 0;
    return to;
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
/* "TIME up=S kbps= acl= wq= rssi= lq= tpl= afh= err= wtx= wrx= cpu= ba= own= h= er= ew= fcc= done=
 * fly=\n", "-" for not read, ba and own as percent of one core (9.0). The fields after own come
 * last, so a reader that knows only those before them reads a line too. */
int bt_line(char *out, int size, const struct bt_sample *s) {
    char b[200];
    int at = snprintf(b, sizeof b, "up=%u", s->up);
    at = put(b, at, sizeof b, "kbps", s->kbps, -1);
    at = put(b, at, sizeof b, "acl", s->acl, -1);
    at = put(b, at, sizeof b, "wq", s->wq, -1);
    at = put(b, at, sizeof b, "rssi", s->rssi, BT_UNSET);
    at = put(b, at, sizeof b, "lq", s->lq, BT_UNSET);
    at = put(b, at, sizeof b, "tpl", s->tpl, BT_UNSET);
    at = put(b, at, sizeof b, "afh", s->afh, BT_UNSET);
    at = put(b, at, sizeof b, "err", s->err, -1);
    at = put(b, at, sizeof b, "wtx", s->wtx, -1);
    at = put(b, at, sizeof b, "wrx", s->wrx, -1);
    at = put(b, at, sizeof b, "cpu", s->cpu, -1);
    at = put_tenths(b, at, sizeof b, "ba", s->ba);
    at = put_tenths(b, at, sizeof b, "own", s->own);
    at = put(b, at, sizeof b, "h", s->h, -1);
    at = put(b, at, sizeof b, "er", s->er, -1);
    at = put(b, at, sizeof b, "ew", s->ew, -1);
    at = put(b, at, sizeof b, "fcc", s->fcc, BT_UNSET);
    at = put(b, at, sizeof b, "done", s->done, -1);
    put(b, at, sizeof b, "fly", s->fly, -1);
    return heat_note(out, size, s->wall, b);
}

int bt_flush(card_log *l, const char *dir) { return log_flush(l, dir, "q2-bt", BT_FILE_MAX); }
