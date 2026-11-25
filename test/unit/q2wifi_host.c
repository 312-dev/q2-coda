/* test_q2wifi_rec.py's q2wifi.c: the device side built for this machine. Its files are real ones
 * under PEQ_ROOT (a fake /proc, /sys and /dev/kmsg the test writes), its wpa_supplicant a real Unix
 * datagram socket the test binds at WIFI_CTRL, and its clock this file's, moved by the test. Every
 * socket it makes and every command it sends is counted, so the test can say it never asks for
 * anything that changes Wi-Fi's state. q2guard.c's readers (slurp, read_int, now_ms) are played
 * here as q2guard.c has them. */
#include <errno.h>
#include <poll.h>
#include <sys/socket.h>
#include <time.h>

#include "../../patch/q2wifi.h"

static unsigned clock_ms = 1000;
static int sent_n, opens;
static char sent[64][32]; /* the commands sent to wpa_supplicant, in order */

static ssize_t count_send(int fd, const void *b, size_t n, int flags) {
    if (sent_n < 64)
        snprintf(sent[sent_n++], sizeof sent[0], "%.*s", (int)(n < 31 ? n : 31), (const char *)b);
    return send(fd, b, n, flags);
}
static int count_open(const char *path, int flags) { return ++opens, open(path, flags); }
#define send count_send
#define open(p, f, ...) count_open(p, f)
#include "../../patch/q2wifi.c"
#undef send
#undef open

int slurp(const char *path, char *buf, int size) {
    int fd = count_open(path, O_RDONLY | O_CLOEXEC), n = 0, got;
    if (fd < 0) return buf[0] = 0, -1;
    while (n < size - 1 && (got = (int)read(fd, buf + n, size - 1 - n)) > 0) n += got;
    close(fd);
    buf[n] = 0;
    return n;
}
int read_int(const char *path, int *v) {
    char b[32];
    if (slurp(path, b, sizeof b) <= 0 || ((b[0] < '0' || b[0] > '9') && b[0] != '-')) return -1;
    *v = atoi(b);
    return 0;
}
unsigned now_ms(void) { return clock_ms; }
static card_log ring, uring;
static int card_up;
int card_mounted(void) { return card_up; }
void set_card(int up) { card_up = up; }
int flush_to(const char *dir) { return wifi_flush(&ring, dir); }
volatile int guard_woke;

void start(const char *dir, int again) {
    static char a[256], b[256];
    snprintf(a, sizeof a, "%s/q2-wifi.0", dir), snprintf(b, sizeof b, "%s/q2-log.0", dir);
    wifi_init(card_log_at(&ring, a, 1 << 20), card_log_at(&uring, b, 1 << 20), again);
}
void set_clock(unsigned ms) { clock_ms = ms; }
void tick(unsigned ms, int temp, const char *heat) { clock_ms = ms, wifi_tick(ms, temp, heat); }
void drain(void) { wifi_drain(); }
int fd(void) { return wifi_fd(); }
int flush_due(void) { return wifi_flush_due(); }
const char *log_ram(void) { return ring.ring ? ring.ring : ""; }
unsigned log_used(void) { return ring.len; }
const char *ulog_ram(void) { return uring.ring ? uring.ring : ""; }
unsigned ulog_used(void) { return uring.len; }
int sent_count(void) { return sent_n; }
const char *sent_at(int i) { return sent[i]; }
int open_count(void) { return opens; }
