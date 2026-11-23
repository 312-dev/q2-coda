/* Download Ahead and the streaming state
 * (docs/server-library.md#download-ahead-and-the-streaming-state).
 *
 * Streaming > Configuration's Download Ahead (Q2POD AHEAD: 0 Off, 1-3 songs, 2 when missing) is
 * told to the card app whose m3u queue plays in /tmp/q2-<id>.ahead: the songs the queue holds after
 * the one playing, in play order (playback_upcoming), at most that many. The app answers in
 * /tmp/q2-<id>.stream how far each planned song has come:
 *   #Q2STREAM/1	seq=<the .play's seq>
 *   <wait|get|stall|done|fail>	<bytes on the card>	<total, 0 unknown>	<length ms, 0
 * unknown>	<card path> rewritten every 500 ms while a song arrives, so one left with a get line
 * for STALE_S has lost its app and reads as fail. With "grow" in the caps (q2player is the player:
 * its /tmp/q2clicks), the app writes a song in place and m3u_songs queues it once START_MS of it is
 * there (stream_startable); q2player waits at the end of the bytes there are and names the song in
 * /tmp/q2player.wait meanwhile. */
#include "navigation.h"
#include "playback.h"

#define AHEAD_DEFAULT 2
#define AHEAD_MAX 3
/* A song arriving starts once START_MS of it is there, at least START_MIN bytes: past what q2player's
 * open reads (the headers, a cover in them, and half a second of audio: source.c GROW_ANALYZE_US),
 * with time in hand for a download a little slower than the song. Measured in
 * docs/internals.md#streaming-configuration. */
#define START_MS 3000
#define START_MIN (256u << 10)
#define STALE_S 3                   /* a get line not rewritten this long: the app is gone */
#define SS_MAX (8 << 10)            /* .stream read this far: a dozen lines */
#define CLICKS_FILE "/tmp/q2clicks" /* player/main.c: there while q2player is the player */
#define WAIT_FILE "/tmp/q2player.wait"

static struct {
    int n, read;    /* Download Ahead, and whether it was read from config.ini */
    int grow;       /* "grow" in the caps written last */
    unsigned ahead; /* the .ahead written last, hashed with its app's id */
} ss __attribute__((section(".scratch")));

int ahead_n(void) {
    if (!ss.read) ss.n = config_value("Q2POD", "AHEAD", AHEAD_MAX + 1, AHEAD_DEFAULT), ss.read = 1;
    return ss.n;
}

/* Streaming > Configuration's row: Off, 1, 2, 3, Off... The next .ahead says so (ahead_poll). */
int ahead_step(void) {
    int n = (ahead_n() + 1) % (AHEAD_MAX + 1);
    write_int_config(n, "Q2POD", "AHEAD");
    ss.n = n;
    return n;
}

/* The caps words this file adds (addon.c's caps line), " ahead" and " grow" while q2player plays.
 */
const char *ahead_caps(void) {
    ss.grow = !access(CLICKS_FILE, 0);
    return ss.grow ? " ahead grow" : " ahead";
}

/* A decimal field, 0 for none; a value past 32 bits is held at their top. */
static unsigned ss_num(const char *s) {
    unsigned long long v = 0;
    for (; *s >= '0' && *s <= '9'; ++s)
        if ((v = v * 10 + (unsigned)(*s - '0')) > 0xffffffffu) v = 0xffffffffu;
    return (unsigned)v;
}

/* The app's .stream line for path (0: its first, the song playing), 1 and *l when there is one. A
 * get or stall line in a file not rewritten for STALE_S reads as fail. id "": none. */
int stream_line(const char *id, const char *path, sline_t *l) {
    char file[48];
    if (!id || !*id) return 0;
    tk_snprintf(file, sizeof file, "/tmp/q2-%s.stream", id);
    unsigned st[36];
    if (__xstat(3, file, st)) return 0;
    int stale = (long)st[16] < time((void *)0) - STALE_S;
    char *raw = calloc(1, SS_MAX + 2), *line, *end;
    if (!raw) return 0;
    int found = 0;
    state_read(file, raw, SS_MAX + 2);
    if (tk_str_start_with(raw + 1, "#Q2STREAM/1"))
        for (line = raw + 1; !found && line && *line; line = end) {
            if ((end = strstr(line, "\n"))) *end++ = 0;
            char *f[5];
            if (line[0] == '#' || tsv_split(line, f, 5) != 5 || (path && strcmp(f[4], path)))
                continue;
            static const char *const names[] = { "wait", "get", "stall", "done", "fail" };
            l->state = SL_NONE;
            for (int k = 0; k < 5; ++k)
                if (!strcmp(f[0], names[k])) l->state = SL_WAIT + k;
            if (l->state == SL_NONE) continue;
            if (stale && (l->state == SL_GET || l->state == SL_STALL)) l->state = SL_FAIL;
            l->have = ss_num(f[1]), l->total = ss_num(f[2]), l->ms = ss_num(f[3]);
            found = 1;
        }
    free(raw);
    return found;
}

/* The bytes a song needs before it starts: START_MS of it at its average rate, at least START_MIN,
 * never past its total; with no total or no length, all of it (~0u when even that is unknown). */
static unsigned start_bytes(const sline_t *l) {
    if (!l->total) return ~0u;
    if (!l->ms) return l->total;
    unsigned per_s = l->total / (l->ms / 1000 ? l->ms / 1000 : 1);
    unsigned need = per_s > ~0u / (START_MS / 1000) ? ~0u : per_s * (START_MS / 1000);
    if (need < START_MIN) need = START_MIN;
    return need < l->total ? need : l->total;
}

/* Whether m3u_songs may queue path, a file that is there: always, but with "grow" a song the app's
 * .stream says is still to come (wait) or arriving (get, stall) short of start_bytes. */
int stream_startable(const char *id, const char *path) {
    sline_t l;
    if (!ss.grow || !stream_line(id, path, &l)) return 1;
    if (l.state == SL_WAIT) return 0;
    if (l.state == SL_GET || l.state == SL_STALL) return l.have >= start_bytes(&l);
    return 1;
}

/* The .stream line of path, a song of an app's stream cache (its /stream/ folder) still arriving:
 * 1 and *l while the line is there and not done. */
static int arriving(const char *path, sline_t *l) {
    char id[16];
    return stream_app_of(path, id, sizeof id) &&
           tk_str_start_with(path + 10 + strlen(id), "/stream/") && stream_line(id, path, l) &&
           l->state != SL_DONE;
}

/* How sure the length of path is (peq.h stream_length). A song arriving in place reads short to the
 * player, a constant-bitrate MP3 without a Xing frame by its bytes so far, so its length is only
 * the server's: the line's own, else the catalogue's duration for that song. */
int stream_length(const char *path, unsigned *ms) {
    sline_t l;
    if (!arriving(path, &l)) return 0;
    int sec = l.ms ? 0 : catalogue_copy_seconds(path);
    if (!l.ms && sec <= 0) return 2;
    *ms = l.ms ? l.ms : (unsigned)sec * 1000u;
    return 1;
}

/* The last second of path a seek may go to while it arrives (peq.h stream_reach), -1 when it is
 * not arriving. total is the player's length of it, seconds. With the line's total bytes, the
 * share of them on the card of the song's length (the server's, else the player's); without, the
 * player's length, which is then measured on the bytes there are. Never past the player's length.
 */
int stream_reach(const char *path, int total) {
    sline_t l;
    if (!arriving(path, &l)) return -1;
    int sec = l.ms ? (int)(l.ms / 1000) : catalogue_copy_seconds(path), len = sec > 0 ? sec : total,
        arrived = total;
    if (l.total) { /* per mille, in 32 bits: the demo has no 64-bit division */
        unsigned have = l.have < l.total ? l.have : l.total, all = l.total;
        while (all >> 20) have >>= 1, all >>= 1;
        unsigned pm = all ? have * 1000u / all : 0;
        arrived = (int)((unsigned)len * pm / 1000u);
    }
    if (total > 0 && arrived > total) arrived = total;
    return arrived > 0 ? arrived - 1 : 0;
}

/* The card app's id in a path under its folder (/mnt/mmc/.<id>/...), into id; 0 for another path.
 */
int stream_app_of(const char *path, char *id, unsigned size) {
    if (!path || !tk_str_start_with(path, "/mnt/mmc/.")) return 0;
    const char *s = path + 10, *slash = strstr(s, "/");
    unsigned n = slash ? (unsigned)(slash - s) : 0;
    if (!n || n >= size) return 0;
    memcpy(id, s, n), id[n] = 0;
    return 1;
}

/* Whether q2player waits for more of path (its WAIT_FILE names it). */
int stream_player_waits(const char *path) {
    char raw[520];
    if (!path || !state_read(WAIT_FILE, raw, sizeof raw)) return 0;
    char *nl = strstr(raw + 1, "\n");
    if (nl) *nl = 0;
    return !strcmp(raw + 1, path);
}

static void ahead_write(const char *id, char *buf, int len) {
    char file[48], tmp[52];
    unsigned h = hash_bytes(fnv(FNV_SEED, (const unsigned char *)id), (const unsigned char *)buf,
                            (unsigned)len);
    if (h == ss.ahead) return;
    tk_snprintf(file, sizeof file, "/tmp/q2-%s.ahead", id);
    tk_snprintf(tmp, sizeof tmp, "%s.tmp", file);
    if (blob_io(file, tmp, buf, (unsigned)len, 1)) ss.ahead = h;
}

/* A list handed to app id (catalogue.c catplay_write, before its .catplay): the .ahead for its
 * seq, which the app's .play will carry, with n and no songs, as the queue holds none of the list
 * yet; the app takes the n after the first in the catplay's order itself. Without it the app ran
 * the list's start on its default of two ahead until ahead_poll's first write, Download Ahead Off
 * included (the seam test, 2026-11-22). */
void ahead_start(const char *id, unsigned seq) {
    char buf[64];
    ahead_write(id, buf, tk_snprintf(buf, sizeof buf, "#Q2AHEAD/1\tseq=%u\tn=%d\n", seq, ahead_n()));
}

/* While the queue playing is app id's m3u (addon.c stream_poll): its .ahead, the songs after
 * position in play order, rewritten only when they, n or the seq change. */
void ahead_poll(const char *id, const char *seq, void *queue) {
    char buf[2048];
    unsigned up[AHEAD_MAX], n = (unsigned)ahead_n(), k = n ? playback_upcoming(up, n) : 0;
    int len = tk_snprintf(buf, sizeof buf, "#Q2AHEAD/1\tseq=%s\tn=%u\n", seq, n);
    for (unsigned i = 0; i < k && queue && up[i] < deque_size(queue); ++i) {
        const char *p = P(deque_at(queue, up[i]), REC_PATH);
        if (!p || len + strlen(p) + 2 > sizeof buf) break;
        len += tk_snprintf(buf + len, sizeof buf - len, "%s\n", p);
    }
    ahead_write(id, buf, len);
}
