/* iAP2's control messages (docs/iphone-mode.md#iap2): one built at a time in m (40 40, its length, its id, then
 * parameters of length, id and data, and groups of them), sent a message to a packet, and a received one's parameters
 * found by id. Part of the PodBox port (iap2.h). */
#include "q2_iap_sys.h"
#include "iap2.h"
#include "iap2_link.h"
#include "iap2_names.h"
#include "iap2_msg.h"

struct iap2_msg m;

void msg_start(u16 id) {
    m.b[0] = 0x40, m.b[1] = 0x40, m.b[4] = id >> 8, m.b[5] = id;
    m.len = 6, m.id = id, m.ok = 1;
}

void param(u16 id, const void *data, unsigned n) {
    if (m.len + 4 + n > peer_max - HEADER_LEN - 1 || m.len + 4 + n > sizeof m.b) {
        m.ok = 0;
        return;
    }
    m.b[m.len] = (4 + n) >> 8, m.b[m.len + 1] = 4 + n, m.b[m.len + 2] = id >> 8, m.b[m.len + 3] = id;
    if (n) memcpy(m.b + m.len + 4, data, n);
    m.len += 4 + n;
}
void param_u8(u16 id, u8 v) { param(id, &v, 1); }
void param_u16(u16 id, u16 v) {
    u8 b[2] = { v >> 8, v };
    param(id, b, 2);
}
void param_u32(u16 id, u32 v) {
    u8 b[4] = { v >> 24, v >> 16, v >> 8, v };
    param(id, b, 4);
}
void param_u64(u16 id, u32 hi, u32 lo) {
    u8 b[8] = { hi >> 24, hi >> 16, hi >> 8, hi, lo >> 24, lo >> 16, lo >> 8, lo };
    param(id, b, 8);
}
/* NUL-terminated, cut at 200 bytes on a character boundary. */
void param_str(u16 id, const char *s) {
    unsigned n = strlen(s);
    char t[201];
    if (n > 200) {
        n = 200;
        while (n && ((u8)s[n] & 0xC0) == 0x80) --n;
    }
    memcpy(t, s, n), t[n] = 0;
    param(id, t, n + 1);
}
void group_start(u16 id) {
    m.group = m.len;
    param(id, 0, 0);
}
void group_end(void) {
    unsigned n = m.len - m.group;
    m.b[m.group] = n >> 8, m.b[m.group + 1] = n;
}

/* One message to a packet: PodBox's car reads only the first message of a packet. */
void msg_send(void) {
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

void send_empty(u16 id) {
    msg_start(id);
    msg_send();
}

const u8 *find(const u8 *data, unsigned len, u16 id, unsigned *n) {
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

u32 get_be(const u8 *p, unsigned n) {
    u32 v = 0;
    while (n--) v = v << 8 | *p++;
    return v;
}
