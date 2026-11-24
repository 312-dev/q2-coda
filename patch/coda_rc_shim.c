/* Car Remote's side in demo (docs/internals.md#car-remote): the little the coda-rc daemon (patch/coda_rc.c)
 * cannot do from outside. Every UI pass (ringnav_sleep), paced to CR_POLL_MS, while Settings > Car Remote
 * is on, it:
 * - starts the daemon (at boot and when the switch goes on; again if it dies) and stops it when the switch
 *   goes off: nothing listens while it is off; and beside it q2-iroh, the iroh sidecar that lets a phone on
 *   another network reach the daemon (docs/remote.md#car-remote-across-networks), on and off with it;
 * - writes Now Playing for it to /tmp/coda-rc.now, now_source.c's (radio=1 while a song radio plays; another
 *   owner as source=, a Radio or Internet Radio station live=1 with station=; a pressed server song from the
 *   press on, as Now Playing shows it; the last toast for CR_NOTE_MS), and the queue's song ids to /tmp/coda-rc.queue (a stream download's
 *   path, title and artist beside its id 0);
 * - takes its commands from the queue coda_rc_cmdq.h describes (the FIFO /tmp/coda-rc.cmdq, read by a thread of
 *   its own, which wakes the UI loop for each), or from the single file /tmp/coda-rc.cmd that a sender with no
 *   queue writes: play a list of song ids from one of them, or shuffled (ids in
 *   /tmp/coda-rc.list), Play, Pause, Play/Pause, Next, Previous, seek, scan (Car Audio's held Next or Previous: seek.c seek_car), set the volume, set shuffle and
 *   repeat, rate the playing song, play a song of the queue, start or stop a radio (song_radio.c); and
 *   answers each in /tmp/coda-rc.ack (the last CR_ACKS answers, newest first), a Pause or Play once the player
 *   shows it;
 * - starts a card app the daemon named in /tmp/coda-rc.wake, for a cover the car asked for (addon_cover_wake);
 * - shows the pairing code while /tmp/coda-rc.pair exists, and writes /tmp/coda-rc.pair-cancel when the
 *   user closes it.
 * Settings > Car Remote is carremote_page.c's, polled from here.
 * The daemon tells the shim when it has written or removed one of those files, over a pipe whose write end it
 * is handed at its start (--tell-fd): a byte, nothing else, so the files stay the one channel and nothing new is
 * reachable by anyone else. A thread waits on the read end and wakes the UI loop, and the files are read on the
 * pass after, else every CR_FILES_MS; without the pipe (a daemon this demo did not start, the pipe refused or
 * closed) they are read every CR_POLL_MS (docs/internals.md#car-remote-tell).
 * While Car Audio's iPhone is on the cable (usb_car.c) Now Playing is written and the commands taken with the switch
 * off too, for q2-iap, which reads Now Playing and sends the car's buttons the same way: queued ones at once, the
 * single file every CR_POLL_MS; the daemon is not started for it.
 * Everything the protocol needs is in the daemon; here only a few words of state. */
#include "navigation.h"
#include "coda_rc_cmdq.h"

#define CR_POLL_MS 500
#define CR_FILES_MS 5000 /* with the daemon's pipe: the command files read at least this often */
#define CR_RESTART_MS 30000 /* a daemon or q2-iroh that died is started again at most this often */
#define CR_NET_IDLE_MS 600000 /* Car Remote holds Smart Wi-Fi's radio this long after its last use (coda_rc_wants_wifi) */
#define CR_IROH_DOWN_MS 60000 /* q2-iroh runs on this long after Wi-Fi's link went, for a link that comes back */
#define CR_SETTLE_MS 3000   /* a Pause or Play the player has not shown by then is refused (under coda_rc.c ACK_MS) */
#define CR_DAEMON "/usr/bin/coda-rc"
#define CR_PID "/tmp/coda-rc.pid"
/* q2-iroh (remote/iroh-sidecar): its key beside coda-rc's, the relays to use instead of n0's public ones one URL a
 * line (none: n0's), the endpoint id and home relay it keeps in CR_IROH_INFO for the daemon and Pair a Phone, and
 * its log, which it keeps to 64 KB (--log); each stream goes to coda-rc's q2-iroh port named by its phone's key, and
 * only the keys coda-rc lists (paired phones') come in, anyone else only while Pair a Phone shows its code
 * (docs/remote-protocol.md#across-networks-only-paired-phones) */
#define CR_IROH "/usr/bin/q2-iroh"
#define CR_IROH_PID "/tmp/q2-iroh.pid"
#define CR_IROH_INFO "/tmp/coda-rc.iroh"
#define CR_IROH_ARGS                                                                                                   \
    " serve --key /mnt/data/coda-rc/iroh.key --relay-file /mnt/data/coda-rc/relay --info " CR_IROH_INFO               \
    " --pid " CR_IROH_PID " --nice 19 --log /tmp/q2-iroh.log --forward-named 127.0.0.1:7721"                       \
    " --allow-file /tmp/coda-rc.iroh-allow --open-while /tmp/coda-rc.qr"
#define CR_CMDQ CRC_CMDQ /* coda_rc_cmdq.h: the FIFO the daemon and q2-iap send commands down */
#define CR_QN 4          /* lines held between the reader thread and the UI pass; the FIFO holds the rest */
#define CR_LINE CRC_CMDQ_LINE
#define CR_ACKS 4 /* answers kept in CR_ACK, so two acked between the daemon's reads both reach it */
#define CR_NOW "/tmp/coda-rc.now"
#define CR_QUEUE "/tmp/coda-rc.queue"
#define CR_CMD "/tmp/coda-rc.cmd"
#define CR_LIST "/tmp/coda-rc.list"
#define CR_ACK "/tmp/coda-rc.ack"
#define CR_PAIR "/tmp/coda-rc.pair"
#define CR_PAIR_END "/tmp/coda-rc.pair-end"
#define CR_PAIR_CANCEL "/tmp/coda-rc.pair-cancel"
#define CR_ONLINE "/tmp/coda-rc.online" /* coda_rc.c online_poll, read by carremote_page.c */
#define CR_WAKE "/tmp/coda-rc.wake"     /* coda_rc.c crc_cover_ask: a card app to start for a car's cover */
#define CR_QUEUE_MAX 5000 /* ponytail: a longer queue lists its first 5000 to the phone */
#define CR_LIST_MAX 20000 /* coda_rc_lib.c PLAY_MAX */
#define CR_HEARTS_MS 5000 /* the playing song's stars read again at least this often */
#define CR_NOTE_MS 4000   /* a toast stays in Now Playing for the car this long (NowPlaying.notice) */

static struct {
    int checked, on, radio_said; /* radio_said: the radio flag cr_now last logged */
    unsigned at, started_at, seq, now_hash, now_n, q_hash, q_gen;
    unsigned *q_rows, q_listed, q_place; /* a shuffle's listing (cr_queue): its queue indices, the playing one's place */
    unsigned play_timer, toast_timer;
    int want; /* a Pause (1) or Play (2) waiting for the player; 0 none */
    unsigned want_seq, want_at;
    void *pair_page;   /* the code's Dialog */
    char pair_code[8]; /* the code the Dialog shows */
    char pair_name[32]; /* the phone asking, for the toast */
    int toast_what;
    int hearts; /* the playing song's stars, as rate_now; read at hearts_at for hearts_id */
    unsigned hearts_at, hearts_id;
    char note[96]; /* the toast shown last, for the car, until note_at + CR_NOTE_MS; note_n counts them */
    unsigned note_at, note_n;
    volatile int tell_live;   /* the tell thread waits on the daemon's pipe */
    volatile unsigned told;   /* bytes the daemon sent, counted by that thread */
    unsigned told_seen, files_at;
    /* The command queue (cr_cmdq_read): lines the reader thread took from CR_CMDQ, q_head written by it alone,
     * q_tail by the UI thread alone; each line as state_read leaves one, '\n' first. q_live: the thread reads;
     * q_tried: the last time making the FIFO failed. play_line: the play or radio command its timer runs. */
    char q[CR_QN][CR_LINE];
    unsigned q_head, q_tail, q_tried;
    int q_live;
    char play_line[CR_LINE];
    unsigned acks[CR_ACKS][2]; /* the last commands answered, newest first: seq, code (cr_ack) */
    /* online: a phone has an authenticated session (CR_ONLINE not empty, read on the files' pass); net_at: Car
     * Remote's last use (on, a session, a pairing); link_at: Wi-Fi's link last seen up; iroh_at: q2-iroh's last
     * start, 0 while it is stopped for want of a link */
    int online;
    unsigned net_at, link_at, iroh_at;
} cr __attribute__((section(".scratch")));

int coda_rc_enabled(void) {
    if (!cr.checked) cr.checked = 1, cr.on = config_value("Q2POD", "CARREMOTE", 2, 0);
    return cr.on;
}

/* A toast the Q2 Muse shows (library_pages.c toast): the car shows it too, for CR_NOTE_MS, in place of its
 * artist (NowPlaying.notice), as Android Auto draws no phone toast. */
void coda_rc_note(const char *text) {
    if (!coda_rc_enabled() || !text || !*text) return;
    tk_snprintf(cr.note, sizeof cr.note, "%s", text);
    cr.note_at = time_now_ms() | 1;
    ++cr.note_n;
}

/* ---- the daemon ---- */

/* The process pid_file names still runs */
static int cr_alive(const char *pid_file) {
    char raw[24], proc[24];
    if (!state_read(pid_file, raw, sizeof raw)) return 0;
    snprintf(proc, sizeof proc, "/proc/%d", atoi(raw + 1));
    return atoi(raw + 1) > 1 && !access(proc, 0);
}

static int cr_running(void) { return cr_alive(CR_PID); }

/* q2-iroh, unless it runs or the image has none; its id file is the new one's, never a dead one's */
static void cr_iroh_start(unsigned now) {
    cr.iroh_at = now | !now;
    if (cr_alive(CR_IROH_PID) || access(CR_IROH, 0)) return;
    unlink(CR_IROH_INFO);
    system("( " CR_IROH CR_IROH_ARGS " >/dev/null 2>&1 & )");
}

/* q2-iroh stopped, Wi-Fi's link gone for CR_IROH_DOWN_MS: with no network it reaches no one, and its relay's
 * retries were the idle Q2's largest CPU user (2026-11-22, docs/internals.md#car-remote-iroh). Pair a Phone's QR
 * code goes with its id file. */
static void cr_iroh_stop(void) {
    cr.iroh_at = 0;
    system("kill $(cat " CR_IROH_PID " 2>/dev/null) 2>/dev/null");
    unlink(CR_IROH_PID);
    unlink(CR_IROH_INFO);
}

/* q2-iroh while Wi-Fi has a link, or had one within CR_IROH_DOWN_MS; started again CR_RESTART_MS after a start if
 * it died. Every CR_POLL_MS while Car Remote is on. */
static void cr_iroh_poll(unsigned now) {
    if (networkGetConnectStatus() > 0) cr.link_at = now | !now;
    if (!cr.link_at || now - cr.link_at >= CR_IROH_DOWN_MS) {
        if (cr.iroh_at) cr_iroh_stop();
        cr.link_at = 0;
    } else if (!cr.iroh_at || now - cr.iroh_at >= CR_RESTART_MS) {
        cr_iroh_start(now);
    }
}

/* Smart Wi-Fi's question (smart_wifi.c smart_needed): does Car Remote need the radio on? While the Q2 has a Wi-Fi
 * link and Car Remote was used within CR_NET_IDLE_MS (turned on or started at boot, a phone connected by any way,
 * a pairing card up; net_at, kept by coda_rc_poll), so a phone reaches it on that network or through q2-iroh. Not
 * with no link (no saved network in range: the radio would scan for one for nothing, and the phone in the car comes
 * over Bluetooth, which needs no Wi-Fi), nor once nothing has used it for CR_NET_IDLE_MS. Pair a Phone wakes the
 * radio as a network page (smart_wifi.c net_page_open). */
int coda_rc_wants_wifi(void) {
    return coda_rc_enabled() && cr.net_at && time_now_ms() - cr.net_at < CR_NET_IDLE_MS &&
           networkGetConnectStatus() > 0;
}

/* A phone has an authenticated session (playback_power.c dark_slow_ok: it shows the position). */
int coda_rc_online(void) { return coda_rc_enabled() && cr.online; }

/* The o32 syscall nr(a, b, c): its result, or -1 when the kernel reports an error ($a3). */
#ifndef PEQ_HOST
__asm__(".globl cr_syscall\ncr_syscall:\n.set push\n.set noreorder\nmove $v0, $a0\nmove $a0, $a1\n"
        "move $a1, $a2\nmove $a2, $a3\nsyscall\nbnez $a3, 1f\nnop\njr $ra\nnop\n1:\njr $ra\n"
        "li $v0, -1\n.set pop\n");
extern int cr_syscall(int nr, long a, long b, long c);
#else
__attribute__((weak)) int cr_syscall(int nr, long a, long b, long c) { return -1; }
#endif
#define SYS_PIPE2 4328
#define CR_CLOEXEC 0x80000 /* O_CLOEXEC on MIPS */

/* The daemon's tells: each wakes the UI loop, whose next pass reads the files (coda_rc_poll). It ends when the
 * daemon has gone (its write end closed) or the read fails. */
static void *cr_tell(void *arg) {
    int fd = (int)(long)arg;
    char b[64];
    while (read(fd, b, sizeof b) > 0) {
        ++cr.told;
        loop_wake();
    }
    cr.tell_live = 0;
    close(fd);
    return 0;
}

#define SYS_MKNOD 4014
#define CR_FIFO_MODE 010600 /* S_IFIFO, 0600 */

/* The command queue's thread (coda_rc_cmdq.h): each whole line read from the FIFO goes into cr.q, and the UI
 * loop is woken to run it. With cr.q full it waits for the UI pass to take one, and the FIFO holds what comes
 * meanwhile. The FIFO is open for writing too (O_RDWR), so a sender closing it is no end of file and the read
 * waits; it ends only if the read fails. */
static void *cr_cmdq_read(void *arg) {
    int fd = (int)(long)arg;
    char b[2 * CR_LINE];
    unsigned n = 0;
    for (int r; (r = (int)read(fd, b + n, sizeof b - n)) > 0;) {
        n += (unsigned)r;
        unsigned start = 0;
        for (unsigned i = 0; i < n; ++i) {
            if (b[i] != '\n') continue;
            unsigned len = i - start;
            if (len && len + 2 < CR_LINE) {
                while (cr.q_head - __atomic_load_n(&cr.q_tail, __ATOMIC_ACQUIRE) >= CR_QN) usleep(10000);
                char *slot = cr.q[cr.q_head % CR_QN];
                slot[0] = '\n';
                memcpy(slot + 1, b + start, len);
                slot[len + 1] = 0;
                __atomic_store_n(&cr.q_head, cr.q_head + 1, __ATOMIC_RELEASE);
                loop_wake();
            }
            start = i + 1;
        }
        if (start) memmove(b, b + start, n - start), n -= start;
        else if (n == sizeof b) n = 0; /* a line too long for any command: dropped */
    }
    cr.q_live = 0;
    close(fd);
    return 0;
}

/* The FIFO made and its thread started, once (again CR_RESTART_MS after a failure): a file of that name another
 * demo left goes first. Without it senders write CR_CMD, read as before. */
static void cr_cmdq_start(void) {
    unsigned now = time_now_ms();
    if (cr.q_live || (cr.q_tried && now - cr.q_tried < CR_RESTART_MS)) return;
    cr.q_tried = now | !now;
    unlink(CR_CMDQ);
    int fd = cr_syscall(SYS_MKNOD, (long)CR_CMDQ, CR_FIFO_MODE, 0) ? -1 : open(CR_CMDQ, 2 | CR_CLOEXEC); /* O_RDWR */
    unsigned long t;
    if (fd < 0) return;
    cr.q_live = 1;
    if (pthread_create(&t, 0, cr_cmdq_read, (void *)(long)fd) || !t) {
        cr.q_live = 0;
        close(fd);
        unlink(CR_CMDQ);
        return;
    }
    pthread_detach(t);
}

/* The next queued line into raw (CR_LINE bytes), as state_read leaves one: 1, or 0 with none. */
static int cr_cmdq_take(char *raw) {
    unsigned tail = cr.q_tail;
    if (__atomic_load_n(&cr.q_head, __ATOMIC_ACQUIRE) == tail) return 0;
    memcpy(raw, cr.q[tail % CR_QN], CR_LINE);
    __atomic_store_n(&cr.q_tail, tail + 1, __ATOMIC_RELEASE);
    return 1;
}

static int cr_cmdq_waiting(void) { return __atomic_load_n(&cr.q_head, __ATOMIC_ACQUIRE) != cr.q_tail; }

/* The daemon, handed the write end of a pipe whose read end cr_tell waits on. The write end is inheritable only
 * for the system() that starts it; the read end never is. ponytail: another thread's fork in that moment would
 * hold the write end too, and the read end then ends only with that child: tells still come, from the daemon. */
static void cr_start(void) {
    cr.started_at = time_now_ms() | 1;
    if (cr_running()) return;
    int fds[2] = { 0, 0 };
    unsigned long t;
    if (!cr.tell_live && !cr_syscall(SYS_PIPE2, (long)fds, CR_CLOEXEC, 0) && fds[0] > 2 && fds[1] > 2) {
        char cmd[96];
        fcntl(fds[1], 2 /* F_SETFD */, 0);
        tk_snprintf(cmd, sizeof cmd, "( " CR_DAEMON " --tell-fd %d >/dev/null 2>&1 & )", fds[1]);
        system(cmd);
        close(fds[1]);
        cr.tell_live = 1;
        if (pthread_create(&t, 0, cr_tell, (void *)(long)fds[0]) || !t) {
            cr.tell_live = 0;
            close(fds[0]);
        } else {
            pthread_detach(t);
        }
        return;
    }
    system("( " CR_DAEMON " >/dev/null 2>&1 & )");
}

static void cr_stop(void) {
    system("kill $(cat " CR_PID " " CR_IROH_PID " 2>/dev/null) 2>/dev/null");
    cr.iroh_at = cr.link_at = 0;
    cr.online = 0;
    unlink(CR_IROH_PID);
    unlink(CR_IROH_INFO);
    unlink(CR_NOW);
    unlink(CR_QUEUE);
    free(cr.q_rows);
    cr.q_rows = 0, cr.q_hash = 0;
    unlink(CR_CMD);
    unlink(CR_PAIR);
    unlink(CR_ONLINE);
    if (cr.pair_page) window_close(cr.pair_page);
}

/* ---- Now Playing out ---- */

static void put(char *b, unsigned cap, unsigned *n, const char *key, const char *v) {
    for (const char *s = key; *s && *n + 1 < cap; ++s) b[(*n)++] = *s;
    if (*n + 1 < cap) b[(*n)++] = '=';
    for (const char *s = v ? v : ""; *s && *n + 1 < cap; ++s) b[(*n)++] = *s == '\n' || *s == '\r' ? ' ' : *s;
    if (*n < cap) b[(*n)++] = '\n';
}

static void put_int(char *b, unsigned cap, unsigned *n, const char *key, int v) {
    char s[12];
    snprintf(s, sizeof s, "%d", v);
    put(b, cap, n, key, s);
}

/* A queue line's text field: no tab or line break, which separate the fields and the lines. */
static unsigned q_text(char *b, unsigned cap, const char *v) {
    unsigned n = 0;
    for (const char *s = v ? v : ""; *s && n + 1 < cap; ++s) b[n++] = *s == '\t' || *s == '\n' || *s == '\r' ? ' ' : *s;
    return n;
}

static unsigned q_len(const char *v) { return v ? (unsigned)strlen(v) : 0; }

/* The playing queue, when it changes: "gen pos total" then a line a song, its library id; a song with no
 * library record (id 0: a card app's stream download, as its playlist sessions and radios queue them) also
 * its path, title and artist after tabs, as its record has them (tags.c card_record_tags: its server row's),
 * so the daemon names it rather than calling it gone from the library. Under a shuffle the lines are the play
 * order Up Next shows (upnext.c): the songs played (playback_played, at most half the lines), the one playing,
 * then what plays after it (playback_upcoming), and pos is the playing one's line (q_place, which Now Playing's
 * qpos says too); a Next moves only that place, as those three run on into each other. Otherwise the queue's own
 * order, pos its index. */
static void cr_queue(void *q, unsigned pos) {
    unsigned n = q ? deque_size(q) : 0, h = hash_bytes(FNV_SEED, (const unsigned char *)&q, sizeof q);
    unsigned *rows = n && pos < n && playback_option(0) ? calloc(CR_QUEUE_MAX, sizeof *rows) : 0, listed;
    if (rows) {
        unsigned k = playback_played(rows, CR_QUEUE_MAX / 2);
        rows[k] = pos;
        listed = k + 1 + playback_upcoming(rows + k + 1, CR_QUEUE_MAX - k - 1);
        cr.q_place = k;
        h = hash_bytes(h, (const unsigned char *)rows, listed * sizeof *rows);
    } else
        listed = n < CR_QUEUE_MAX ? n : CR_QUEUE_MAX, cr.q_place = pos;
    h = hash_bytes(h, (const unsigned char *)&n, sizeof n);
    h = hash_bytes(h, (const unsigned char *)&playback_loads, sizeof playback_loads);
    if (h == cr.q_hash) return free(rows);
    unsigned len = 0, cap = 40 + listed * 12;
    for (unsigned i = 0; i < listed; ++i) {
        void *r = deque_at(q, rows ? rows[i] : i);
        if (I(r, REC_ID) <= 0) cap += 3 + q_len(P(r, REC_PATH)) + q_len(P(r, REC_TITLE)) + q_len(P(r, REC_NAME)) +
                                      q_len(P(r, REC_ARTIST));
    }
    char *b = calloc(1, cap);
    if (!b) return free(rows);
    cr.q_hash = h;
    ++cr.q_gen;
    free(cr.q_rows);
    cr.q_rows = rows, cr.q_listed = listed;
    len = (unsigned)snprintf(b, cap, "%u %u %u\n", cr.q_gen, cr.q_place, rows ? listed : n);
    for (unsigned i = 0; i < listed; ++i) {
        void *r = deque_at(q, rows ? rows[i] : i);
        int id = I(r, REC_ID);
        len += (unsigned)snprintf(b + len, cap - len, "%d", id);
        if (id <= 0 && P(r, REC_PATH)) {
            const char *title = P(r, REC_TITLE);
            if (!title || !*title) title = P(r, REC_NAME);
            const char *field[] = { P(r, REC_PATH), title, P(r, REC_ARTIST) };
            for (unsigned k = 0; k < 3; ++k) b[len++] = '\t', len += q_text(b + len, cap - len, field[k]);
        }
        b[len++] = '\n';
    }
    blob_io(CR_QUEUE, CR_QUEUE ".tmp", b, len, 1);
    free(b);
}

/* The playing song's stars (the protocol's hearts): again on a new song, after a Rate from here or
 * every CR_HEARTS_MS, as the rating page and the server change them without telling. The heart, the
 * like, is not the rating and is not sent. */
static int cr_hearts(unsigned id) {
    unsigned now = time_now_ms();
    if (!cr.hearts_at || id != cr.hearts_id || now - cr.hearts_at >= CR_HEARTS_MS)
        cr.hearts = rate_now(), cr.hearts_id = id, cr.hearts_at = now | 1;
    return cr.hearts;
}

/* Shuffle (0 Off, 1 on) and repeat (0 Off, 1 One, 2 All) as the phone has them: the hold menu's extended play
 * while it runs (playback.c: 3 repeats the song, 4 and 5 wrap the queue, 4 within an album or folder), else Now
 * Playing's buttons (np_bar.c), which stock's play mode and [IPOD] NPSHUFONE / NPREPOFF hold. */
static void cr_modes(int *shuf, int *rep) {
    if (playback_active()) {
        int r = playback_option(1);
        *shuf = playback_option(0) != 0, *rep = r == 3 ? 1 : r >= 4 ? 2 : 0;
    } else {
        int r = np_mode_repeat();
        *shuf = np_mode_shuffle(), *rep = r == 2 ? 1 : r == 1 ? 2 : 0;
    }
}

/* Now Playing as now_source.c has it (now_get: the Library's song as the Q2 Muse's own page shows it, a
 * pressed server song from the press on, or another owner's), with the queue, the modes and the hearts beside
 * it. A pressed song is at 0 with no hearts until it plays, as the Q2's rating works only on the song
 * playing. Another owner is source=radio (Radio), netradio (Internet Radio), spotify or video, with no hearts
 * and no path, so nothing here names a cover for it; a radio is live=1, with no length or position, its
 * station in station=. */
static void cr_now(void) {
    now_info now;
    now_get(&now);
    void *q = P(mcl_pdeqplaylist, 0);
    cr_queue(q, (unsigned)I(MCL_POS, 0));
    int live = now.source == NOW_RADIO || now.source == NOW_NETRADIO, other = now.source != NOW_LIBRARY;
    /* the hold menu's extended play while it runs, else Now Playing's own pair (cr_modes) */
    int shuf, rep;
    cr_modes(&shuf, &rep);
    char b[1400];
    unsigned n = 0;
    put_int(b, sizeof b, &n, "state", now.state);
    put_int(b, sizeof b, &n, "pos", now.pos_ms > 0 ? now.pos_ms : 0);
    put_int(b, sizeof b, &n, "len", now.len_ms > 0 ? now.len_ms / 1000 : 0);
    put_int(b, sizeof b, &n, "id", now.id);
    put_int(b, sizeof b, &n, "shuffle", shuf);
    /* playback.c: 3 repeats the song, 4 and 5 wrap the queue (4 within an album or folder) */
    put_int(b, sizeof b, &n, "repeat", rep);
    put_int(b, sizeof b, &n, "volume", g_volume);
    put_int(b, sizeof b, &n, "hearts", now.pressed || other ? -1 : cr_hearts((unsigned)now.id));
    /* a shuffle's: the playing song's line in the listing and its lines (cr_queue), as the car lists them */
    int listing = cr.q_rows && !now.pressed && !other;
    put_int(b, sizeof b, &n, "qpos", listing ? (int)cr.q_place : (int)now.qpos);
    put_int(b, sizeof b, &n, "qlen", listing ? (int)cr.q_listed : (int)now.qlen);
    put_int(b, sizeof b, &n, "qgen", (int)cr.q_gen);
    unsigned have = 0, btotal = 0, bms = 0; /* a server song loading (np_stream.c), NowPlaying.buffering */
    put_int(b, sizeof b, &n, "buf", other ? 0 : np_stream_loading(&have, &btotal, &bms));
    put_int(b, sizeof b, &n, "bhave", (int)have);
    put_int(b, sizeof b, &n, "btotal", (int)btotal);
    put_int(b, sizeof b, &n, "bms", (int)bms);
    int radio = song_radio_on();
    if (radio != cr.radio_said) { /* its changes in the system log, as the phone's buttons follow it */
        cr.radio_said = radio;
        ulog('I', "remote", radio ? "Now Playing: a radio plays" : "Now Playing: no radio");
    }
    put_int(b, sizeof b, &n, "radio", radio);
    static const char *const sources[] = { "", "library", "radio", "netradio", "spotify", "video" };
    put(b, sizeof b, &n, "source", sources[now.source]);
    put_int(b, sizeof b, &n, "live", live);
    put(b, sizeof b, &n, "station", now.station);
    if (cr.note_at && time_now_ms() - cr.note_at >= CR_NOTE_MS) cr.note_at = 0, cr.note[0] = 0;
    put_int(b, sizeof b, &n, "noten", (int)cr.note_n);
    put(b, sizeof b, &n, "note", cr.note);
    put(b, sizeof b, &n, "path", now.path);
    put(b, sizeof b, &n, "title", now.title);
    put(b, sizeof b, &n, "artist", now.artist);
    put(b, sizeof b, &n, "album", now.album);
    unsigned h = hash_bytes(FNV_SEED, (const unsigned char *)b, n);
    if (h == cr.now_hash) return;
    char seq[16];
    unsigned k = (unsigned)snprintf(seq, sizeof seq, "seq=%u\n", ++cr.now_n);
    if (k + n > sizeof b) n = sizeof b - k;
    memmove(b + k, b, n);
    memcpy(b, seq, k);
    if (blob_io(CR_NOW, CR_NOW ".tmp", b, n + k, 1)) cr.now_hash = h;
}

/* ---- commands in ---- */

/* A command answered: CR_ACK holds this answer and the CR_ACKS - 1 before it, a "seq code" line each, newest
 * first, so commands queued close together are each answered though the daemon reads the file once. */
static void cr_ack(unsigned seq, int code) {
    char b[CR_ACKS * 24];
    unsigned n = 0;
    memmove(cr.acks[1], cr.acks[0], sizeof cr.acks - sizeof cr.acks[0]);
    cr.acks[0][0] = seq, cr.acks[0][1] = (unsigned)code;
    for (unsigned i = 0; i < CR_ACKS && (i == 0 || cr.acks[i][0]); ++i)
        n += (unsigned)snprintf(b + n, sizeof b - n, "%u %d\n", cr.acks[i][0], (int)cr.acks[i][1]);
    blob_io(CR_ACK, CR_ACK ".tmp", b, n, 1);
}

/* A command in the system log as it is taken: its words (verb, up to its line's end), who sent it (q2-iap's numbers
 * start at 0x51000000, q2_iap.c io_command; coda-rc's, the phone's, are below), what owned playback said before it
 * (status, media_status's: 2 playing, 3 paused) and, with done, what came of it. The drive of 2026-11-23 could
 * not tell a phone's pick from a song's end. */
static void cr_said(unsigned seq, const char *verb, int status, const char *done) {
    char line[112];
    int n = 0;
    while (verb[n] && verb[n] != '\n' && n < 48) ++n;
    tk_snprintf(line, sizeof line, "%.*s from the %s: the player %d%s%s", n, verb,
                seq >= 0x51000000u ? "car" : "phone", status, done ? ", " : "", done ? done : "");
    ulog('I', "remote", line);
}

typedef struct {
    int id;
    unsigned at;
} cr_ix;

static int ix_order(const void *a, const void *b) {
    int x = ((const cr_ix *)a)->id, y = ((const cr_ix *)b)->id;
    return x < y ? -1 : x > y;
}

/* The listed ids as the library's records, in the list's order: the whole library's (with server songs
 * when a server is signed in) sorted by id once, then each id looked up. */
static void *cr_songs(int want, int *at) {
    int n = 0;
    void *all = staged(songs_unsorted, (void *)1, &n), *out = _create_deque("stSongInfo");
    deque_init(out);
    unsigned m = all ? deque_size(all) : 0;
    char *raw = calloc(1, CR_LIST_MAX * 11 + 3);
    cr_ix *ix = calloc(m + 1, sizeof *ix);
    if (raw && ix && state_read(CR_LIST, raw, CR_LIST_MAX * 11 + 3)) {
        for (unsigned i = 0; i < m; ++i) ix[i].id = I(deque_at(all, i), REC_ID), ix[i].at = i;
        qsort(ix, m, sizeof *ix, ix_order);
        int line = 0;
        for (char *c = raw + 1; *c && line < CR_LIST_MAX; ++line) {
            int id = atoi(c);
            unsigned lo = 0, hi = m;
            while (lo < hi) {
                unsigned mid = lo + (hi - lo) / 2;
                if (ix[mid].id < id) lo = mid + 1;
                else hi = mid;
            }
            if (lo < m && ix[lo].id == id) {
                if (line == want) *at = (int)deque_size(out);
                _deque_push_back(out, deque_at(all, ix[lo].at));
            }
            char *nl = strstr(c, "\n");
            if (!nl) break;
            c = nl + 1;
        }
    }
    free(raw);
    free(ix);
    if (all) deque_destroy(all);
    return out;
}

/* Play's work, from a timer: play_folder opens Now Playing, a window, which a poll may not do. */
static int cr_play(const void *unused) {
    char *raw = cr.play_line;
    cr.play_timer = 0;
    unsigned seq = (unsigned)atoi(raw + 1);
    int at = 0, shuffle = 0, count = 0, from = 0;
    char *verb = strstr(raw + 1, " ");
    if (!verb || __isoc99_sscanf(verb, " play %d %d %d", &at, &shuffle, &count) != 3) return 0;
    if (library_scanning() || addon_game_audio() || navigator_window_is_exist("usbmode_page")) {
        cr_said(seq, verb + 1, media_status(), "busy");
        cr_ack(seq, 2); /* busy: the daemon answers UNAVAILABLE */
        return 0;
    }
    void *dq = cr_songs(at, &from);
    int size = (int)deque_size(dq);
    char done[32];
    tk_snprintf(done, sizeof done, "%d songs from %d", size, from);
    cr_said(seq, verb + 1, media_status(), size ? done : "no songs");
    if (size) {
        press_natural = 0; /* the phone's pick is the user's: the same row resumes, as a tap does */
        if (shuffle)
            shuffle_play(dq);
        else
            play_folder(dq, from, 0xf001);
    }
    deque_destroy(dq);
    cr_now(); /* the new queue's version before the ack: the daemon's resume notes it (coda_rc_lib.c) */
    cr_ack(seq, size ? 0 : 1);
    return 0;
}

/* StartRadio's work, from a timer as Play's (a radio's queue opens Now Playing): "radio <id>", a radio of
 * the song with that record id, 0 the playing one, or "radio stop" (song_radio.c song_radio_car). */
static int cr_radio(const void *unused) {
    char *raw = cr.play_line;
    int id = 0;
    cr.play_timer = 0;
    unsigned seq = (unsigned)atoi(raw + 1);
    char *verb = strstr(raw + 1, " ");
    int stop = verb && !strncmp(verb, " radio stop", 11);
    if (!verb || (!stop && __isoc99_sscanf(verb, " radio %d", &id) != 1)) return 0;
    if (!stop && (library_scanning() || addon_game_audio() || navigator_window_is_exist("usbmode_page"))) {
        cr_said(seq, verb + 1, media_status(), "busy");
        cr_ack(seq, 2);
        return 0;
    }
    cr_said(seq, verb + 1, media_status(), 0);
    int code = song_radio_car(id, stop);
    cr_now();
    cr_ack(seq, code);
    return 0;
}

/* A Pause or Play waiting for the player: ack 0 once it plays or is paused as asked, 1 (the phone gets
 * UNAVAILABLE) if it is not within CR_SETTLE_MS, as when a card game's sound holds stock's player off. */
static void cr_settle(void) {
    if (!cr.want) return;
    int ok = media_playing() == cr.want - 1;
    if (!ok && time_now_ms() - cr.want_at < CR_SETTLE_MS) return;
    cr_now(); /* Now Playing first, so a GetState after the ack sees what it acked */
    cr_ack(cr.want_seq, !ok);
    cr.want = 0;
}

/* SetMode's shuffle (0 Off, 1 on) and repeat (0 Off, 1 One, 2 All; -1 leave either). While the hold menu's
 * extended play runs, as its choices (navigation.c qm_run): on keeps a shuffle by album; Off keeps Play One
 * Song or Album, else Play All; All keeps Repeat Album, else Repeat All. Otherwise as Now Playing's buttons
 * (np_bar.c np_mode_set), so they show it. 1 when set. */
static int cr_mode(int shuffle, int repeat) {
    if (!playback_active()) {
        int on = shuffle >= 0 ? shuffle : np_mode_shuffle();
        np_mode_set(on, repeat == 1 ? 2 : repeat == 2 ? 1 : repeat == 0 ? 0 : np_mode_repeat());
        return 1;
    }
    int s = playback_option(0), r = playback_option(1), ok = 1;
    if (shuffle >= 0) ok = playback_set(0, !shuffle ? 0 : s ? s : 1);
    if (ok && repeat >= 0) ok = playback_set(1, repeat == 1 ? 3 : repeat == 2 ? (r == 4 ? 4 : 5) : r < 3 ? r : 2);
    return ok;
}

/* The queue's song at index, if it is still song id (else 3: the phone's queue is stale), as a View Queue
 * row plays it (upnext.c un_play): the playing one plays on, another starts unless Headphones required
 * forbids it (1). */
static int cr_jump(unsigned at, int id) {
    void *q = P(mcl_pdeqplaylist, 0);
    if (cr.q_rows) at = at < cr.q_listed ? cr.q_rows[at] : ~0u; /* a line of a shuffle's listing (cr_queue) */
    if (!q || at >= deque_size(q) || I(deque_at(q, at), REC_ID) != id) return 3;
    if (at == (unsigned)I(MCL_POS, 0) && playback_keeps(q, (int)at)) {
        if (!media_playing()) media_key(KEY_PLAY);
        return 0;
    }
    if (check_forbid_play_no_headset(0)) return 1;
    playback_pick();
    player_start_queue((int)at);
    return 0;
}

/* One command, raw as state_read leaves a line ('\n' first). A play or a radio is kept for its timer, which runs it
 * on the next pass; cr_command takes no other until then. */
static void cr_run(const char *raw) {
    unsigned seq = (unsigned)atoi(raw + 1);
    const char *verb = strstr(raw + 1, " ");
    int v = 0, w = 0;
    cr.want = 0; /* a newer command: the daemon has answered the one waiting, and its ack would be stale */
    if (verb && (!strncmp(verb, " play ", 6) || !strncmp(verb, " radio ", 7))) {
        memcpy(cr.play_line, raw, CR_LINE);
        cr.play_timer = timer_add(verb[1] == 'p' ? cr_play : cr_radio, 0, 0);
        /* taken: its work can hold the loop for seconds (a shuffled list's start, 9 s on a loaded emulator), and the
         * daemon then waits longer for the answer the timer writes (coda_rc_cmdq.h CRC_ACK_TAKEN) */
        if (cr.play_timer) cr_ack(seq, CRC_ACK_TAKEN);
        return;
    }
    char word[12] = "";
    if (verb) __isoc99_sscanf(verb, " %11s", word);
    /* Play/Pause, Next and Previous as a headset's buttons (headset.c media_key): to whatever plays,
     * past any window's keys. A press of the Q2's own button is swallowed while a toast is up
     * (msginfo_dialog_on_keyup) or Shuffle's confirmation is (feedback.c conf_key); this is not. */
    int want = !strcmp(word, "resume")   ? 1
               : !strcmp(word, "pause")  ? 0
               : !strcmp(word, "toggle") ? !media_playing()
                                         : -1;
    if (want >= 0) {
        int status = media_status(), press = (status == 2) != want;
        /* the drive of 2026-11-22: a resume, and the player paused 0.4 s later */
        cr_said(seq, verb + 1, status, press ? "Play/Pause pressed" : "nothing pressed");
        if (press) media_key(KEY_PLAY);
        cr.want = want + 1; /* acked once the player shows it (cr_settle) */
        cr.want_seq = seq;
        cr.want_at = time_now_ms();
        cr_settle();
    } else if (!strcmp(word, "next") || !strcmp(word, "previous")) {
        cr_said(seq, verb + 1, media_status(), 0);
        media_key(word[0] == 'n' ? KEY_FWD_BTN : KEY_BACK_BTN);
        cr_ack(seq, 0);
    } else if (verb && __isoc99_sscanf(verb, " scan %d", &v) == 1 && v >= -1 && v <= 1) {
        cr_said(seq, verb + 1, media_status(), 0);
        seek_car(v); /* Car Audio's held Next or Previous (iap2_hid.c), and its release */
        cr_ack(seq, 0);
    } else if (verb && __isoc99_sscanf(verb, " seek %d", &v) == 1 && v >= 0) {
        cr_said(seq, verb + 1, media_status(), 0);
        player_seek_time(v);
        cr_ack(seq, 0);
    } else if (verb && __isoc99_sscanf(verb, " mode %d %d", &v, &w) == 2) {
        int ok = cr_mode(v, w);
        /* the drive of 2026-11-22: repeat from the car seen not to reach the Q2 after a radio */
        cr_said(seq, verb + 1, media_status(), ok ? "set" : "refused");
        cr_now(); /* the new mode before the ack, as cr_settle */
        cr_ack(seq, !ok);
    } else if (verb && __isoc99_sscanf(verb, " rate %d %d", &v, &w) == 2) {
        unsigned at, n;
        void *r = queue_now(&at, &n);
        int ok = r && I(r, REC_ID) == w ? rate_now_set(v) : -1;
        cr_said(seq, verb + 1, media_status(), ok < 0 ? "not the song playing" : ok ? "set" : "refused");
        cr.hearts_at = 0; /* the new stars: read them now */
        cr_now();
        cr_ack(seq, ok < 0 ? 3 : !ok);
    } else if (verb && __isoc99_sscanf(verb, " jump %d %d", &v, &w) == 2 && v >= 0) {
        int before = media_status(), code = cr_jump((unsigned)v, w);
        cr_said(seq, verb + 1, before, code == 3 ? "not that song now" : code ? "refused" : 0);
        cr_ack(seq, code);
    } else if (verb && __isoc99_sscanf(verb, " volume %d", &v) == 1 && v >= 0 && v <= 100) {
        /* as the AirPlay app sets it (airplay.c): up to Volume Limit, saved for the output in use */
        cr_said(seq, verb + 1, media_status(), 0);
        if (v > g_maxvolume) v = g_maxvolume;
        int from = g_volume;
        g_volume = (unsigned char)v;
        device_set_volume(v, 1);
        write_int_config(v, "PLAYSET", mclGetOutputWay() == 1 ? "BT_VOLUME" : "VOLUME");
        if (v != from) vol_show(from); /* on the Q2's screen too, as a headset's change (volume.c) */
        cr_ack(seq, 0);
    } else {
        cr_said(seq, verb ? verb + 1 : "?", media_status(), "not known");
        cr_ack(seq, 1);
    }
}

/* The commands waiting, in order: the single file's (CR_CMD, from a sender that found no queue: a q2-iap or daemon
 * from before coda_rc_cmdq.h, or one whose FIFO open failed), then the queue's; none while a play's or radio's
 * timer has yet to run. */
static void cr_command(void) {
    char raw[CR_LINE];
    if (cr.play_timer) return;
    if (state_read(CR_CMD, raw, sizeof raw)) {
        unlink(CR_CMD);
        cr_run(raw);
    }
    while (!cr.play_timer && cr_cmdq_take(raw)) cr_run(raw);
}

/* ---- the pairing code ---- */

static int cr_toast(const void *unused) {
    cr.toast_timer = 0;
    if (cr.toast_what > 0)
        carremote_paired(cr.pair_name); /* carremote_page.c: "Pixel 10 Pro paired" */
    else if (cr.toast_what < 0)
        toast("Pairing failed");
    cr.toast_what = 0;
    return 0;
}

static int pair_closed(void *ctx, void *event) {
    cr.pair_page = 0;
    return 0;
}

/* Cancel, Centre on it, or Return: the daemon is told, and ends the pairing */
static int pair_cancel(void *ctx, void *event) {
    blob_io(CR_PAIR_CANCEL, CR_PAIR_CANCEL ".tmp", "1\n", 2, 1);
    unlink(CR_PAIR);
    if (cr.pair_page) window_close(cr.pair_page);
    return 0;
}

/* The card's Cancel, Return, or its timeout (the daemon's CRC_PAIR_TIMEOUT_MS): the card is
 * closing itself. */
static void pair_answer(void *ctx, int row, int toggle_on) {
    cr.pair_page = 0;
    pair_cancel(ctx, 0);
}

/* The pairing card (pair_prompt.c, docs/oled-design.md#pairing): the phone named in its title, the
 * code in two groups of three, where to enter it, Cancel, and the pairing's 120 s as its bar
 * (docs/remote-protocol.md#pairing-on-the-q2). */
static void pair_show(const char *code, const char *name) {
    static const char *const rows[] = { "Cancel" };
    reset_poweroptions_timer(1, 1, 1); /* the screen on and kept on, as a key press would */
    cr.pair_page = pair_prompt(name, code, "Enter this code in Q2 Muse Remote", rows, 1, 0,
                               120000, 0, pair_answer, 0);
    if (cr.pair_page) widget_on(cr.pair_page, EVT_DESTROY, pair_closed, 0);
}

static void cr_pairing(void) {
    char raw[80];
    if (state_read(CR_PAIR, raw, sizeof raw) >= 7) {
        char *code = raw + 1, *name = strstr(code, "\n");
        if (!name || name - code != 6) return;
        *name++ = 0;
        if (cr.pair_page) {
            /* a code the daemon ended and replaced between two polls: the old one would be refused */
            if (strcmp(code, cr.pair_code)) window_close(cr.pair_page);
            return; /* the next poll shows the new one, once the old Dialog is gone */
        }
        char *end = strstr(name, "\n");
        if (end) *end = 0;
        memcpy(cr.pair_code, code, 7);
        tk_snprintf(cr.pair_name, sizeof cr.pair_name, "%s", *name ? name : "Phone");
        pair_show(code, *name ? name : "Phone");
        return;
    }
    if (cr.pair_page) window_close(cr.pair_page); /* paired, failed or given up: the daemon removed it */
    if (state_read(CR_PAIR_END, raw, sizeof raw)) {
        unlink(CR_PAIR_END);
        int outcome = atoi(raw + 1);
        if (outcome >= 0) {
            cr.toast_what = outcome ? 1 : -1;
            if (!cr.toast_timer) cr.toast_timer = timer_add(cr_toast, 0, 0);
        }
    }
}

/* The daemon asked a card app that doesn't run for a cover the car wants (coda_rc.c crc_cover_ask): it is started
 * (addon_cover_wake, only an app whose q2app says covers=1). */
static void cr_wake(void) {
    char raw[24];
    if (!state_read(CR_WAKE, raw, sizeof raw)) return;
    unlink(CR_WAKE);
    for (char *c = raw + 1; *c; ++c)
        if (*c == '\n') *c = 0;
    addon_cover_wake(raw + 1);
}

/* Every UI pass (ringnav_sleep): the daemon, Now Playing and the pages every CR_POLL_MS; the daemon's files then
 * too, unless its pipe is live, when they are read on the pass after a tell, every CR_POLL_MS while a play waits
 * for its timer or the pairing card is up (their next step is a poll's, not a tell's), else every CR_FILES_MS. A
 * queued command (cr_cmdq_read) runs on the pass its line woke, whatever the pace. */
void coda_rc_poll(void) {
    unsigned now = time_now_ms();
    int told = cr.told != cr.told_seen, queued = cr_cmdq_waiting();
    if (!told && !queued && cr.at && now - cr.at < CR_POLL_MS) return;
    if (!coda_rc_enabled() && !usb_car_on()) {
        char drop[CR_LINE];
        while (cr_cmdq_take(drop)) {} /* a command with neither to take it: dropped, as the file's was */
        if (cr.at && now - cr.at < CR_POLL_MS) return;
    }
    if (!cr.at || now - cr.at >= CR_POLL_MS) {
        cr.at = now | !now;
        carremote_poll(); /* Settings > Car Remote's pages while open */
        if (!coda_rc_enabled()) {
            /* Car Audio's q2-iap reads Now Playing and sends the car's buttons the same way, with Car Remote on or
             * off (docs/iphone-mode.md#car-audio-in-the-image); no daemon is started for it */
            if (usb_car_on()) cr_cmdq_start(), cr_now(), cr_settle(), cr_command();
            return;
        }
        cr_cmdq_start();
        if (!cr.started_at) cr.net_at = now | !now; /* the first start: Car Remote's use begins (coda_rc_wants_wifi) */
        if (!cr.started_at || (now - cr.started_at >= CR_RESTART_MS && !cr_running())) cr_start();
        cr_iroh_poll(now);
        if (cr.online || cr.pair_page) cr.net_at = now | !now;
        cr_now();
        cr_settle();
        /* A q2-iap from before the queue writes the car's buttons in CR_CMD and never tells, and the car's held Next
         * or Previous says "scan" every 500 ms against seek.c's SEEK_CAR_LOST_MS: while its iPhone is on the cable,
         * every CR_POLL_MS (q2muse-1126: read every 5 s, the car's scan ran in bursts) */
        if (!told && !queued && cr.tell_live && !cr.play_timer && !cr.pair_page && !usb_car_on() &&
            now - cr.files_at < CR_FILES_MS)
            return;
    } else if (!coda_rc_enabled()) {
        if (queued) cr_command(); /* Car Audio's button, on the pass its line woke */
        return;
    }
    cr.told_seen = cr.told;
    cr.files_at = now;
    cr_command();
    cr_wake();
    cr_pairing();
    char raw[8];
    cr.online = state_read(CR_ONLINE, raw, sizeof raw) > 0;
}

/* Car Remote on or off, its daemon started or stopped: the page's switch, and Control Center's
 * circle (control_center.c). */
void coda_rc_toggle(void) {
    cr.on = !coda_rc_enabled();
    write_int_config(cr.on, "Q2POD", "CARREMOTE");
    if (cr.on) {
        unsigned now = time_now_ms();
        cr.net_at = now | !now; /* turned on: Car Remote's use begins (coda_rc_wants_wifi) */
        cr_start();
    } else {
        cr_stop();
    }
}
