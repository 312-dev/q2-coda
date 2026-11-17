/* The overheat guardian's cards (docs/internals.md#overheat-guardian): q2guard decides, in a
 * process of its own, and says what to show in /tmp/q2guard.state; demo draws it and answers in
 * /tmp/q2guard.ack ("ack N" for a power-off it took over, "cancel N" for the load warning's
 * Cancel). A card that can't show yet (another is up) is tried again on the next poll. */
#include "navigation.h"

#define HG_POLL_MS 500
#define HG_STATE "/tmp/q2guard.state"
#define HG_ACK "/tmp/q2guard.ack"
#define HG_GRACE_MS 60000 /* after demo starts, before a silent q2guard counts as not running */
#define HG_STALE_MS 30000 /* its beat unchanged this long: not running */
#define HG_OFF_MS 10000   /* the power-off card's countdown, if q2guard gives no time left */

static struct {
    unsigned at, start, beat_at;
    int beat, seq_seen, seq_shown; /* the state's beat and seq; the last seq a card or toast took */
    int off;                       /* the power-off taken over: its seq */
    unsigned off_at, off_ms;
    int acked, cancel; /* the answers last written */
    int norun_shown;
} hg __attribute__((section(".scratch")));

/* The tests' seams (test/fw/test_heatguard.py): 0 on the Q2, so confirm and toast_later. */
int (*hg_confirm)(const confirm_t *c) __attribute__((section(".scratch")));
void (*hg_toast)(const char *glyph, const char *text) __attribute__((section(".scratch")));
static int hg_card(const confirm_t *c) { return (hg_confirm ? hg_confirm : confirm)(c); }

static int hg_field(const char *s, const char *key, char *out, int size) { /* key=value's value */
    int n = (int)strlen(key);
    for (const char *p = s; (p = strstr(p, key));) {
        if ((p == s || p[-1] == ' ') && p[n] == '=') {
            int i = 0;
            for (p += n + 1; *p && *p != ' ' && *p != '\n' && i < size - 1;) out[i++] = *p++;
            out[i] = 0;
            return 1;
        }
        p += n;
    }
    out[0] = 0;
    return 0;
}

static int hg_int(const char *v) { /* a decimal, "-1" as -1 */
    int neg = *v == '-', n = 0;
    for (v += neg; *v >= '0' && *v <= '9'; ++v) n = n * 10 + (*v - '0');
    return neg ? -n : n;
}

static void hg_answer(void) { /* both answers, rewritten whole */
    char b[48];
    int n = tk_snprintf(b, sizeof b, "ack %d\ncancel %d\n", hg.acked, hg.cancel);
    int fd = open(HG_ACK, 0x80301, 0644); /* O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC (MIPS) */
    if (fd < 0) return;
    write(fd, b, n);
    close(fd);
}

static void hg_load_answer(void *ctx, int row, int toggle_on) {
    if (row == 0)
        hg.cancel = (int)(long)ctx, hg_answer(); /* Cancel; the countdown's end is row 1 */
}
static void hg_off_answer(void *ctx, int row, int toggle_on) { navigator_to("poweroff_page"); }
static void hg_ok(void *ctx, int row, int toggle_on) {}

static const char *const hg_ok_row[] = { "OK" };
static const char *const hg_load_rows[] = { "Cancel", "Shut Down" };
static const char *const hg_off_row[] = { "Shutting Down" };

static int hg_info(const char *title, const char *body) {
    confirm_t c = { .title = title,
                    .body = body,
                    .rows = hg_ok_row,
                    .n_rows = 1,
                    .kind = CONFIRM_INFO,
                    .back = -1,
                    .answer = hg_ok };
    return hg_card(&c);
}

/* The power-off: answer, pause the audio, show the countdown, and at its end stock's own Shut Down
 * (poweroff_page: into_poweroff saves the queue). If the card can't show, the poll powers off at
 * the same time. q2guard powers off by itself at its own deadline whatever happens here. */
static void hg_power_off(int seq, int left, int load) {
    if (!hg.off) {
        hg.off = seq, hg.off_at = time_now_ms(), hg.off_ms = left > 0 ? left : HG_OFF_MS;
        hg.acked = seq, hg_answer();
        if (mclGetPlayStatus() == 2) player_play_pause();
    }
    unsigned gone = time_now_ms() - hg.off_at;
    if (gone >= hg.off_ms) {
        hg.off_ms = ~0u; /* once */
        navigator_to("poweroff_page");
        return;
    }
    if (hg.seq_shown == seq) return;
    confirm_t c = { .title = load ? "Shutting Down" : "Q2 is too hot",
                    .body = load ? "Q2 worked hard with no temperature sensor."
                                 : "Shutting down to cool off.",
                    .rows = hg_off_row,
                    .n_rows = 1,
                    .kind = CONFIRM_INFO,
                    .back = -1,
                    .timeout_ms = (int)(hg.off_ms - gone),
                    .timeout_row = 0,
                    .answer = hg_off_answer };
    if (hg_card(&c)) hg.seq_shown = seq;
}

void heatguard_poll(void) {
    unsigned now = time_now_ms();
    if (!hg.start) hg.start = now | !now, hg.beat_at = hg.start;
    if (now - hg.at < HG_POLL_MS && !hg.off) return;
    hg.at = now;
    char s[192], v[24];
    int fd = open(HG_STATE, 0x80000), n = 0; /* O_RDONLY | O_CLOEXEC */
    if (fd >= 0) n = (int)read(fd, s, sizeof s - 1), close(fd);
    s[n > 0 ? n : 0] = 0;
    int beat = hg_field(s, "beat", v, sizeof v) ? hg_int(v) : -1;
    if (beat != hg.beat) hg.beat = beat, hg.beat_at = now;
    if (!hg.norun_shown && now - hg.start >= HG_GRACE_MS && now - hg.beat_at >= HG_STALE_MS &&
        hg_info("Overheat protection isn't running",
                "Q2 can't watch for overheating\nuntil it restarts."))
        hg.norun_shown = 1;
    if (beat < 0) return;
    int seq = hg_field(s, "seq", v, sizeof v) ? hg_int(v) : 0;
    int left = hg_field(s, "left", v, sizeof v) ? hg_int(v) : 0;
    char card[16];
    hg_field(s, "card", card, sizeof card);
    hg_field(s, "state", v, sizeof v);
    if (!tk_strcmp(card, "off") || hg.off) {
        hg_power_off(hg.off ? hg.off : seq, left, !strncmp(v, "poweroff-load", 13));
        return;
    }
    if (seq == hg.seq_shown) return;
    int shown = 1;
    if (!tk_strcmp(card, "warn")) {
        (hg_toast ? hg_toast : toast_later)("chargetoast_weak", "Q2 is getting hot");
    } else if (!tk_strcmp(card, "nosensor")) {
        shown = hg_info("No Temperature Sensor",
                        "Q2 can't detect overheating; it will\nwatch CPU load instead.");
    } else if (!tk_strcmp(card, "loadwarn")) {
        confirm_t c = { .title = "Q2 has been working hard",
                        .body =
                            "2 minutes with no temperature sensor.\nShutting down to protect it.",
                        .rows = hg_load_rows,
                        .n_rows = 2,
                        .kind = CONFIRM_CONSEQUENTIAL,
                        .back = 0,
                        .timeout_ms = left > 0 ? left : 60000,
                        .timeout_row = 1,
                        .answer = hg_load_answer,
                        .ctx = (void *)(long)seq };
        shown = hg_card(&c);
    }
    if (shown) hg.seq_shown = seq;
}
