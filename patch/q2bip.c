/* q2-bip: the process (q2bip.h; docs/internals.md#bluetooth-cover-art). It listens on an L2CAP
 * channel in Enhanced Retransmission Mode, which OBEX over L2CAP requires, serves one controller at
 * a time through q2bip_core.c, and makes the 200 x 200 JPEG a controller asks for from the cover
 * demo's payload names in BT_COVER_FILE (patch/bt_cover.c), with stock's ffmpeg, the first time a
 * handle is asked for, each fetch a line of the unified log on its stderr (note). bluealsa2, stock's
 * Bluetooth launcher, starts it; it ends with that script
 * (PR_SET_PDEATHSIG), which ends when Bluetooth is turned off.
 *
 * One thread, asleep in accept() or recv() unless a car is fetching: nothing polls. */
#include "coda_rc_sys.h"
#include "q2bip.h"
#include "ulog_line.h"

#ifndef CODA_HOST
int fork(void), execl(const char *, const char *, ...), waitpid(int, int *, int), prctl(int, ...);
int getsockopt(int, int, int, void *, socklen_t *), nice(int);
ssize_t recv(int, void *, size_t, int);
__attribute__((noreturn)) void _exit(int);
#define WNOHANG 1
#define SIGKILL 9
#define SIGTERM 15
#define SIGPIPE 13
#define SIG_IGN ((void (*)(int))1)
#define PR_SET_PDEATHSIG 1
#define EADDRINUSE 125 /* MIPS */
#else
#include <sys/prctl.h>
#include <sys/wait.h>
#endif

#ifndef AF_BLUETOOTH
#define AF_BLUETOOTH 31
#endif
#define BTPROTO_L2CAP 0
#define SOL_L2CAP 6
#define L2CAP_OPTIONS 1
#define L2CAP_MODE_ERTM 3
#define SOL_BLUETOOTH 274
#define BT_SECURITY 4
#define BT_SECURITY_MEDIUM 2 /* an encrypted link: a paired device, as AVRCP's own channels need */
struct sockaddr_l2 {
    unsigned short family, psm; /* psm little-endian */
    unsigned char bdaddr[6];
    unsigned short cid;
    unsigned char bdaddr_type;
};
struct l2cap_options {
    unsigned short omtu, imtu, flush_to;
    unsigned char mode, fcs, max_tx;
    unsigned short txwin_size;
};

#define COVER_MAX 65536   /* a 200 x 200 JPEG is 5 to 20 KB */
#define FFMPEG_MS 10000   /* a run still going is killed: the car gets Not Found, and asks again */
#define NOW_MAX 2200      /* BT_COVER_FILE: a handle, a cover's path, a source and a song's path */
#define BIND_TRIES 20     /* a relaunched Bluetooth's old q2-bip may hold the PSM for a moment */

/* Where things are; the host tests move them (Q2BIP_NOW, Q2BIP_TMP, Q2BIP_FFMPEG) */
static const char *now_file = "/tmp/q2-bip.now", *tmp_file = "/tmp/q2-bip.jpg", *ffmpeg = "/usr/bin/ffmpeg";

static unsigned char cover[COVER_MAX];
static unsigned cover_len;
static char cover_handle[BIP_HANDLE + 1];

static unsigned read_file(const char *path, void *out, unsigned cap) {
    int fd = open(path, O_RDONLY), got = 0, r;
    if (fd < 0) return 0;
    while (got < (int)cap && (r = (int)read(fd, (char *)out + got, cap - got)) > 0) got += r;
    close(fd);
    return (unsigned)got;
}

/* src as a 200 x 200 baseline JPEG at tmp_file: fitted, centred on black where it is not square,
 * 4:2:2 as BIP's thumbnail is. Niced, so the encoder beside it never waits for it. */
static int scale(const char *src) {
    int pid = fork(), status = 0, waited = 0;
    if (pid < 0) return 0;
    if (!pid) {
        (void)nice(19);
        execl(ffmpeg, ffmpeg, "-nostdin", "-v", "quiet", "-xerror", "-i", src, "-frames:v", "1", "-vf",
              "scale=200:200:force_original_aspect_ratio=decrease,pad=200:200:(ow-iw)/2:(oh-ih)/2", "-pix_fmt",
              "yuvj422p", "-q:v", "4", "-f", "mjpeg", "-y", tmp_file, (char *)0);
        _exit(127);
    }
    while (waitpid(pid, &status, WNOHANG) == 0) {
        if ((waited += 50) > FFMPEG_MS) {
            kill(pid, SIGKILL);
            waitpid(pid, &status, 0);
            return 0;
        }
        poll(0, 0, 50);
    }
    return (status & 0x7f) == 0 && (status >> 8 & 0xff) == 0; /* its own exit, with 0 */
}

/* ms on the monotonic clock, the unified log's uptime */
static unsigned now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned)t.tv_sec * 1000u + (unsigned)(t.tv_nsec / 1000000);
}

/* One fetch as a bt line of the unified log on stderr (demo's stderr file on a Q2, which q2guard reads into the
 * unified and Bluetooth logs): the handle, what was asked, the song and where its cover came from (BT_COVER_FILE's
 * third and fourth lines, "?" in a file without them), the bytes and the time, and how it went. */
static void note(int kind, const char *handle, const char *track, const char *source, unsigned bytes, unsigned since,
                 const char *outcome, int warn) {
    char msg[700], line[800];
    const char *what = kind == BIP_PROPERTIES ? "properties" : kind == BIP_IMAGE ? "image" : "thumbnail";
    int n = snprintf(msg, sizeof msg, "cover %s %s for %s from %s: %u bytes in %u ms, %s", handle, what, track, source,
                     bytes, now_ms() - since, outcome);
    if (n < 0) return;
    if (n >= (int)sizeof msg) n = (int)sizeof msg - 1;
    n = ulog_line(line, sizeof line, time(0), now_ms(), warn ? 'W' : 'I', "bt", msg, n);
    if (write(2, line, (size_t)n) < 0) return; /* nowhere to say it */
}

/* BT_COVER_FILE's line at *at, cut at its newline, *at moved past it; "?" when there is none */
static const char *now_line(char **at) {
    char *s = *at, *end = s ? strchr(s, '\n') : 0;
    if (!end) return *at = 0, "?";
    *end = 0, *at = end + 1;
    return *s ? s : "?";
}

/* bip's image: the playing song's cover when handle is its handle, made once and kept until
 * another song's is made; each call logged (note). */
static int image(void *ctx, const char *handle, const unsigned char **data, unsigned *len) {
    static char kept_track[512], kept_source[16];
    char now[NOW_MAX];
    const struct bip *b = ctx;
    unsigned since = now_ms();
    if (cover_len && !strcmp(handle, cover_handle)) {
        note(b->kind, handle, kept_track, kept_source, cover_len, since, "kept", 0);
        *data = cover, *len = cover_len;
        return 1;
    }
    unsigned n = read_file(now_file, now, sizeof now - 1);
    now[n] = 0;
    char *at = n > BIP_HANDLE && now[BIP_HANDLE] == '\n' ? now + BIP_HANDLE + 1 : 0;
    const char *path = now_line(&at), *source = now_line(&at), *track = now_line(&at);
    if (n < BIP_HANDLE + 3 || now[BIP_HANDLE] != '\n' || strncmp(now, handle, BIP_HANDLE))
        return note(b->kind, handle, track, source, 0, since, "not the playing song's", 1), 0;
    if (!scale(path)) return unlink(tmp_file), note(b->kind, handle, track, source, 0, since, "ffmpeg failed", 1), 0;
    cover_len = 0; /* the cover kept so far gives way to this one */
    n = read_file(tmp_file, cover, COVER_MAX);
    unlink(tmp_file);
    if (n < 4 || n == COVER_MAX || cover[0] != 0xff || cover[1] != 0xd8) /* empty, cut, or no JPEG */
        return note(b->kind, handle, track, source, 0, since, "no JPEG from ffmpeg", 1), 0;
    cover_len = n;
    memcpy(cover_handle, handle, BIP_HANDLE + 1);
    snprintf(kept_track, sizeof kept_track, "%s", track);
    snprintf(kept_source, sizeof kept_source, "%s", source);
    note(b->kind, handle, track, source, cover_len, since, "made", 0);
    *data = cover, *len = cover_len;
    return 1;
}

/* One controller on fd, until it disconnects or the channel closes. omtu: the most it takes in one
 * L2CAP SDU, which an OBEX packet must fit. */
static void serve(int fd, unsigned omtu) {
    static unsigned char in[BIP_PKT + 1], out[BIP_PKT];
    struct bip b = { .image = image };
    b.ctx = &b; /* image() names the GET's kind in its line */
    for (;;) {
        int n = (int)recv(fd, in, sizeof in, 0);
        if (n <= 0) {
            if (n < 0 && errno == EINTR) continue;
            return;
        }
        unsigned m = bip_feed(&b, in, (unsigned)n, out);
        if (b.peer_max > omtu) b.peer_max = omtu;
        if (send(fd, out, m, MSG_NOSIGNAL) != (ssize_t)m || b.close) return;
    }
}

#ifdef CODA_HOST
#include <sys/un.h>
/* The tests' channel: a UNIX socket that keeps packets whole, as L2CAP does */
static int listen_unix(const char *path) {
    struct sockaddr_un a = { .sun_family = AF_UNIX };
    int s = socket(AF_UNIX, SOCK_SEQPACKET, 0);
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path);
    unlink(path);
    return s >= 0 && !bind(s, (void *)&a, sizeof a) && !listen(s, 1) ? s : -1;
}
#endif

static int listen_l2cap(void) {
    struct sockaddr_l2 a = { .family = AF_BLUETOOTH, .psm = BIP_PSM };
    struct l2cap_options o;
    struct {
        unsigned char level, key_size;
    } sec = { BT_SECURITY_MEDIUM, 0 };
    socklen_t len = sizeof o;
    int s = socket(AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP);
    if (s < 0) return -1;
    for (int i = 0; bind(s, (void *)&a, sizeof a); ++i) {
        if (errno != EADDRINUSE || i == BIND_TRIES) return close(s), -1;
        poll(0, 0, 500);
    }
    if (getsockopt(s, SOL_L2CAP, L2CAP_OPTIONS, &o, &len)) return close(s), -1;
    o.imtu = BIP_PKT, o.mode = L2CAP_MODE_ERTM;
    if (setsockopt(s, SOL_L2CAP, L2CAP_OPTIONS, &o, sizeof o) ||
        setsockopt(s, SOL_BLUETOOTH, BT_SECURITY, &sec, sizeof sec) || listen(s, 1))
        return close(s), -1;
    return s;
}

int main(int argc, char **argv) {
    int s, unix_ = 0;
    (void)argc, (void)argv;
    prctl(PR_SET_PDEATHSIG, SIGTERM); /* gone with the launcher: Bluetooth off, or relaunched */
    signal(SIGPIPE, SIG_IGN);
#ifdef CODA_HOST
    if (getenv("Q2BIP_NOW")) now_file = getenv("Q2BIP_NOW");
    if (getenv("Q2BIP_TMP")) tmp_file = getenv("Q2BIP_TMP");
    if (getenv("Q2BIP_FFMPEG")) ffmpeg = getenv("Q2BIP_FFMPEG");
    if (argc > 1) unix_ = 1;
    s = unix_ ? listen_unix(argv[1]) : listen_l2cap();
#else
    s = listen_l2cap();
#endif
    if (s < 0) return 1; /* no ERTM, or no Bluetooth: no covers, and nothing else changes */
    for (;;) {
        struct l2cap_options o = { .omtu = BIP_PKT };
        socklen_t len = sizeof o;
        int fd = accept(s, 0, 0);
        if (fd < 0) {
            if (errno == EINTR) continue;
            return 1;
        }
        if (!unix_ && getsockopt(fd, SOL_L2CAP, L2CAP_OPTIONS, &o, &len)) o.omtu = BIP_PKT_MIN;
        serve(fd, o.omtu < BIP_PKT_MIN ? BIP_PKT_MIN : o.omtu);
        close(fd);
    }
}
