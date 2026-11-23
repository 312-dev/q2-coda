/* A device pairing with the Q2 by numeric comparison (docs/internals.md#bluetooth-pairing-card):
 * the pairing card (docs/oled-design.md#pairing), naming the device, closed the moment the request
 * ends.
 *
 * BlueZ asks the pairing agent libbtctl registers for demo. Of its Agent1 calls, only
 * RequestConfirmation reaches here as a question: libbtctl hands each call to demo's prompt
 * callback as (type, passkey text), and this is that callback in place of stock's (0x4f9d20).
 * Type 5 is RequestConfirmation, its passkey "%06u"; libbtctl keeps the call as its pending
 * request (BTCTL_PENDING) until an answer (btctl_reply), and answers every other type itself when
 * the callback returns 0, as stock's did: RequestPinCode "0000", RequestPasskey refused,
 * RequestAuthorization and AuthorizeService allowed; DisplayPinCode and DisplayPasskey are answered
 * before the callback and need nothing.
 *
 * BlueZ (5.66) ends a request it gave up on with Agent1.Cancel: the peer cancelled or failed the
 * pairing, its link dropped, or the agent did not answer in REQUEST_TIMEOUT (60 s, src/agent.c).
 * libbtctl's Cancel drops its pending request and tells demo nothing, so stock's dialog stayed up
 * until someone answered it (once for six and a half hours, refusing a software update meanwhile).
 * Here the UI loop watches libbtctl's pending request (bt_pair_poll) and closes the card when it
 * is no longer the one shown; and after BTP_TIMEOUT_MS the card refuses by itself, should no
 * Cancel come. Nothing closes the card with a yes.
 *
 * The device: the callback gets no name or address, but the pending request is the Agent1 call
 * itself, a DBusMessage whose body is (device object path, passkey). Its path is read from the
 * message (msg_path), checked against the passkey the callback was handed, and BlueZ is asked the
 * device's Name over D-Bus (a shell, bt_ui.c bt_shell) until it has one. The card waits for the
 * first answer (BTP_NAME_MS at most); with no name yet it asks "Pair with this device?" over
 * "Unknown device" and the address, and changes to the name in place when BlueZ has it. A path
 * that does not check out is no device: "Unknown device" alone. */
#include "navigation.h"

#define BTP_CONFIRM 5        /* libbtctl's prompt type for Agent1.RequestConfirmation */
#define BTP_TIMEOUT_MS 60000 /* BlueZ 5.66's REQUEST_TIMEOUT for an agent's answer */
#define BTP_NAME_MS 1000     /* the device's Name asked again this often till it has one */
#define BTP_NAME_FILE "/tmp/q2-btpair-name"
#define BTP_UNKNOWN "this device" /* the title's name for a device whose Name is not known */
#define BTP_CAPTION "Make sure both codes match"
#define BTP_CANCEL 0 /* the rows: Cancel first and lit (a consequential question) */
#define BTP_PAIR 1

/* libbtctl.so (stock V1.36's, fixed by tools/build.py ZIP_SHA): its agent's pending request, the
 * DBusMessage agent_response answers and the Cancel method clears, at BTCTL_PENDING from where
 * it is loaded; BTCTL_CHECK holds agent_response's `lw $4, 0x5900($17)` of it, checked before
 * the word is trusted. */
#define BTCTL_PENDING 0x45900u
#define BTCTL_CHECK 0xe3b0u
#define BTCTL_CHECK_WORD 0x8e245900u
/* libdbus 1.12.24's DBusMessage (/usr/lib/libdbus-1.so.3.19.15, the same pin), as far as its body:
 * the refcount, the header (its bytes, a DBusString, then ten field offsets and a bitfield word)
 * and the body's DBusString, at +64 on the Q2 (dbus_message_iter_init's &message->body). */
typedef struct {
    const unsigned char *str;
    int len, allocated;
    unsigned flags;
} dbm_string;
typedef struct {
    int refcount;
    dbm_string header;
    int fields[10];
    unsigned bits;
    dbm_string body;
} dbm_message;
#ifdef __mips__
_Static_assert(__builtin_offsetof(dbm_message, body) == 64, "libdbus 1.12's DBusMessage body");
#endif

static struct {
    /* written by the callback on libbtctl's thread, seq last */
    volatile unsigned seq; /* RequestConfirmations taken */
    void *msg;             /* ... the last one's DBusMessage, libbtctl's pending request then */
    char code[8];          /* ... its six digits */
    char path[64];         /* ... its device's object path, "" when unread */
    /* the UI loop's */
    unsigned long base; /* libbtctl's load address, 0 unknown */
    unsigned taken;     /* the request the card is for */
    int live;           /* ... not answered nor ended */
    void *dialog;       /* ... its card while up */
    void *want;         /* ... its DBusMessage */
    unsigned at;        /* ... when it was taken (time_now_ms) */
    char name[64], body[112], mac[18];
    bt_job job;                     /* the Name's ask */
    unsigned seen, asked_at, first; /* its runs read; the last asked; the count at the request */
    int named;
} bp __attribute__((section(".scratch")));

static unsigned rd32(const unsigned char *p) {
    return p[0] | (unsigned)p[1] << 8 | (unsigned)p[2] << 16 | (unsigned)p[3] << 24;
}

/* The device's object path from m, RequestConfirmation's DBusMessage, into path (size bytes):
 * a little-endian method call whose body is exactly (an object path under /org/bluez/hci, passkey).
 * 1 when it is; 0, path untouched, for anything else. */
int bt_pair_msg_path(const void *msg, unsigned passkey, char *path, unsigned size) {
    const dbm_message *m = msg;
    if (!m) return 0;
    const unsigned char *h = m->header.str, *b = m->body.str;
    unsigned len = (unsigned)m->body.len, n, at;
    if (!h || !b || h[0] != 'l' || h[1] != 1 || h[3] != 1 || rd32(h + 4) != len || len < 8)
        return 0;
    n = rd32(b);
    at = (4 + n + 1 + 3) & ~3u;
    if (n >= size || n > len || at + 4 != len || b[4 + n] || rd32(b + at) != passkey ||
        strncmp((const char *)b + 4, "/org/bluez/hci", 14))
        return 0;
    memcpy(path, b + 4, n + 1);
    return 1;
}

/* libbtctl's load address from /proc/self/maps (its first mapping, offset 0), once its
 * agent_response is the one BTCTL_PENDING was read from; else 0. */
static unsigned long btctl_base(void) {
    char buf[1024];
    int fd = open("/proc/self/maps", 0), n, keep = 0;
    unsigned long base = 0;
    if (fd < 0) return 0;
    while (!base && (n = (int)read(fd, buf + keep, sizeof buf - 1 - keep)) > 0) {
        n += keep, buf[n] = 0;
        char *line = buf, *end;
        while (!base && (end = strstr(line, "\n"))) {
            *end = 0;
            if (strstr(line, "/libbtctl.so") && strstr(line, " 00000000 ")) {
                for (const char *p = line;; ++p) {
                    int d = *p >= '0' && *p <= '9'   ? *p - '0'
                            : *p >= 'a' && *p <= 'f' ? *p - 'a' + 10
                                                     : -1;
                    if (d < 0) break;
                    base = base << 4 | (unsigned)d;
                }
            }
            line = end + 1;
        }
        keep = (int)(buf + n - line);
        if (keep >= (int)sizeof buf - 1) keep = 0; /* a line longer than the buffer: dropped */
        memmove(buf, line, (unsigned)keep);
    }
    close(fd);
    if (base && *(const unsigned *)(base + BTCTL_CHECK) != BTCTL_CHECK_WORD) base = 0;
    return base;
}

/* libbtctl's pending request now. */
static void *pending(void) { return *(void *volatile *)(bp.base + BTCTL_PENDING); }

/* headset.c headset_bt_init, before libbtctl's thread starts: libbtctl found. */
void bt_pair_init(void) { bp.base = btctl_base(); }

/* libbtctl's prompt callback (its thread). 1 holds a RequestConfirmation for the card; 0 leaves
 * any other type to libbtctl's own answer. */
int bt_pair_prompt(int type, const char *passkey, void *unused) {
    (void)unused;
    if (type != BTP_CONFIRM || !passkey) return 0;
    unsigned key = 0;
    for (const char *p = passkey; *p >= '0' && *p <= '9'; ++p)
        key = key * 10 + (unsigned)(*p - '0');
    bp.msg = bp.base ? pending() : (void *)0;
    tk_snprintf(bp.code, sizeof bp.code, "%s", passkey);
    if (!bp.msg || !bt_pair_msg_path(bp.msg, key, bp.path, sizeof bp.path)) bp.path[0] = 0;
    __atomic_add_fetch(&bp.seq, 1, __ATOMIC_RELEASE);
    return 1;
}

/* The request the card is for is still libbtctl's to answer (libbtctl unknown: assumed). */
static int asked(void) { return bp.live && (!bp.base || pending() == bp.want); }

/* The request answered: yes pairs, anything else refuses. Once, and only while it is asked. */
static void reply(int yes) {
    if (asked()) btctl_reply(BTP_CONFIRM, yes ? "yes" : "no");
    bp.live = 0;
}

/* The card closed with no answer, when it is ours and up. */
static void card_drop(void) {
    if (bp.dialog && confirm_dialog() == bp.dialog) confirm_close();
    bp.dialog = (void *)0;
}

static void answered(void *ctx, int row, int toggle_on) {
    (void)toggle_on;
    if ((unsigned)(long)ctx != bp.taken) return; /* an earlier request's card */
    bp.dialog = (void *)0;
    reply(row == BTP_PAIR);
}

/* The title's name and the body: the device's Name over the caption; without one, "this device"
 * and the body "Unknown device", the address when it is known, then the caption. */
static const char *card_words(void) {
    if (bp.named)
        tk_snprintf(bp.body, sizeof bp.body, BTP_CAPTION);
    else
        tk_snprintf(bp.body, sizeof bp.body, "Unknown device\n%s%s" BTP_CAPTION, bp.mac,
                    bp.mac[0] ? "\n" : "");
    return bp.named ? bp.name : BTP_UNKNOWN;
}

static void card_open(unsigned now) {
    static const char *const rows[] = { "Cancel", "Pair" };
    int left = BTP_TIMEOUT_MS - (int)(now - bp.at);
    if (left <= 0 || confirm_up()) return; /* another card first: this one after it */
    awake_screen();
    const char *name = card_words();
    bp.dialog = pair_prompt(name, bp.code, bp.body, rows, 2, BTP_CANCEL, left, BTP_CANCEL, answered,
                            (void *)(long)bp.taken);
}

/* A new request: the last one's card goes (BlueZ asks one at a time, so it has ended). */
static void take(unsigned seq, unsigned now) {
    card_drop();
    bp.taken = seq, bp.live = 1, bp.at = now, bp.want = bp.msg;
    bp.named = 0, bp.name[0] = bp.mac[0] = 0, bp.asked_at = now - BTP_NAME_MS;
    bp.seen = bp.first = bt_job_done(&bp.job);
    const char *dev = strstr(bp.path, "/dev_");
    if (dev && strlen(dev + 5) == 17) {
        tk_snprintf(bp.mac, sizeof bp.mac, "%s", dev + 5);
        for (char *p = bp.mac; *p; ++p)
            if (*p == '_') *p = ':';
    }
}

/* BlueZ's Name for the device, asked till it has one; the card follows it. */
static void name_follow(unsigned now) {
    if (!bp.mac[0] || bp.named) return;
    unsigned done = bt_job_done(&bp.job);
    if (done != bp.seen) {
        char raw[sizeof bp.path + sizeof bp.name + 4];
        unsigned n = strlen(bp.path);
        bp.seen = done;
        state_read(BTP_NAME_FILE, raw, sizeof raw);
        const char *name = raw + 2 + n;
        if (!strncmp(raw + 1, bp.path, n) && raw[1 + n] == ' ' && *name) {
            unsigned k = 0;
            while (name[k] && name[k] != '\n' && k < sizeof bp.name - 1) ++k;
            tk_snprintf(bp.name, sizeof bp.name, "%.*s", (int)k, name);
            bp.named = 1;
            if (bp.dialog && confirm_dialog() == bp.dialog) confirm_words(card_words(), bp.body);
            return;
        }
    }
    if (now - bp.asked_at < BTP_NAME_MS) return;
    char cmd[400];
    tk_snprintf(cmd, sizeof cmd,
                "(f=" BTP_NAME_FILE "; n=$(dbus-send --system --print-reply --dest=org.bluez %s "
                "org.freedesktop.DBus.Properties.Get string:org.bluez.Device1 string:Name "
                "2>/dev/null | sed -n 's/.*string \"\\(.*\\)\"$/\\1/p'); [ -n \"$n\" ] && "
                "echo \"%s $n\" > $f.new && mv -f $f.new $f) >/dev/null 2>&1",
                bp.path, bp.path);
    if (bt_shell(&bp.job, cmd)) bp.asked_at = now;
}

/* Every UI loop pass (bt_ui.c bt_ui_poll). */
void bt_pair_poll(void) {
    unsigned seq = __atomic_load_n(&bp.seq, __ATOMIC_ACQUIRE), now = (unsigned)time_now_ms();
    if (seq != bp.taken) take(seq, now);
    if (!bp.live) return;
    if (bp.base && pending() != bp.want) { /* BlueZ's Cancel: the peer gave up, or the link went */
        bp.live = 0;
        card_drop();
        return;
    }
    if (now - bp.at >= BTP_TIMEOUT_MS) { /* no Cancel came: refused */
        reply(0);
        card_drop();
        return;
    }
    if (bp.dialog && confirm_dialog() != bp.dialog) bp.dialog = (void *)0; /* closed by another */
    name_follow(now);
    /* the card waits for BlueZ's first answer on the Name (BTP_NAME_MS at most), so a device BlueZ
     * knows opens named rather than changing under the reader a moment later */
    if (!bp.dialog && (bp.named || !bp.mac[0] || bp.seen != bp.first || now - bp.at >= BTP_NAME_MS))
        card_open(now);
}
