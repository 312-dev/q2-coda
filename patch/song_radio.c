/* Song radio (docs/internals.md#radio): Now Playing's Radio, and the car's StartRadio. A tap while
 * no radio plays starts one seeded by the playing song; a tap on the pill stops it, the songs
 * already queued playing on.
 *
 * A server song (a streamed file, a Library server row, a synced song of an app's queue) is its
 * app's radio: addon_radio_start asks the app (q2app radio=1), which plays the seed and the
 * server's similar songs and adds more as they run out (q2-subsonic's radio.go), and says radio= in
 * its state file while it plays (addon_radio); the seed is the song's path, which for a streamed
 * file carries its server id (stream_path.h). Any other song, one the app answers is not its own,
 * or one whose app is too old to play radios (addon_radio_missing), is this file's local radio:
 * the library's songs by the seed's artist, then of its genre, each shuffled, leaving out the last
 * SR_RECENT played or queued, SR_BATCH at a time, SR_LOW left before more join the queue's end. A
 * local radio plays in order (Now Playing's shuffle and repeat off, put back when it ends) and
 * ends when another queue loads.
 *
 * The song playing as the seed plays on untouched: the rows after it are replaced, nothing loads
 * (playback.c playback_queue_rest; an app's radio through addon.c, song_radio_keeps). A radio
 * whose queue loads with the seed first (an app's that names another song first) seeks the seed
 * to the place it had at the tap once that queue plays it (sr.seek). */
#include "navigation.h"
#include "playback.h"

#define SR_WAIT_MS 25000 /* an app's answer this long in coming: no radio */
#define SR_LOW 10        /* songs left after the one playing when a local radio adds more */
#define SR_BATCH 25      /* songs a local radio queues at a time */
#define SR_RECENT 128    /* songs a local radio leaves out: the last played or queued */
#define SR_SEEK_MS                                                                                 \
    30000 /* the radio's queue playing the seed this soon after the tap takes its place */
#define SR_RETRY_MS                                                                                \
    60000 /* a local radio that found nothing to add looks again this much later                   \
           */

static struct {
    int local;                  /* a local radio plays: its queue is the one loads names */
    unsigned loads;             /* playback_loads once its queue loaded */
    int shuf, rep;              /* Now Playing's modes before it, put back after */
    unsigned retry_at;          /* no look for more before (0: any time) */
    char artist[96], genre[64]; /* its seed's, folded to lower case */
    unsigned recent[SR_RECENT]; /* songs played or queued lately, by key, a ring */
    unsigned nrecent, last_id;  /* how many went in; the playing song as last noted */
    unsigned ask, ask_at;       /* an app's request waiting for its answer (its seq), and when */
    char path[512];             /* that request's seed, for the local radio when the app declines */
    unsigned id;                /* the seed's key */
    int sec;                    /* its place at the tap (-1: from its start, or played on) */
    unsigned seek_at;    /* the seed sought once the radio's queue plays it, until SR_SEEK_MS */
    unsigned seek_loads; /* playback_loads at the tap: the radio's queue is a later one */
} sr __attribute__((section(".scratch")));

/* A line in the system log (q2-log) for each radio decision: the drive of 2026-11-22 showed "Radio
 * isn't available for this song" on a second start after the car's stop, and nothing on the device
 * said what the app answered. */
static void say(const char *what, int n) {
    char line[96];
    tk_snprintf(line, sizeof line, "%s %d", what, n);
    ulog('I', "radio", line);
}
/* A time stamp, never 0 (0 is none) */
static unsigned stamp(unsigned ms) { return ms ? ms : 1; }

static void fold(char *out, unsigned n, const char *s) {
    unsigned k = 0;
    for (; s && s[k] && k + 1 < n; ++k) out[k] = s[k] >= 'A' && s[k] <= 'Z' ? s[k] + 32 : s[k];
    out[k] = 0;
}
static int same(const char *folded, const char *s) {
    char t[96];
    fold(t, sizeof t, s);
    return folded[0] && !strcmp(folded, t);
}

/* A song's key: its path's FNV-1a. Not REC_ID: a queue's records and the library's (staged) number
 * one song differently. 0 for none. */
static unsigned key(void *r) {
    const char *p = r ? P(r, REC_PATH) : 0;
    return p && *p ? hash_bytes(FNV_SEED, (const unsigned char *)p, strlen(p)) | 1 : 0;
}

static void heard(unsigned id) {
    if (id > 0) sr.recent[sr.nrecent++ % SR_RECENT] = id;
}
static int recent(unsigned id) {
    unsigned n = sr.nrecent < SR_RECENT ? sr.nrecent : SR_RECENT;
    for (unsigned i = 0; i < n; ++i)
        if (sr.recent[i] == id) return 1;
    return 0;
}

/* The playing song's record, 0 when none. */
static void *playing(void) {
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned pos = (unsigned)I(MCL_POS, 0);
    return q && pos < deque_size(q) ? deque_at(q, pos) : 0;
}

/* Up to want of all's songs like the seed into out: the artist's, then the genre's, each shuffled,
 * none of them the seed, a server row (its app's to stream), a video or a song played or queued
 * lately; each noted as queued. Their count. */
static unsigned picks(void *all, void *out, unsigned want) {
    unsigned m = all ? deque_size(all) : 0, got = 0;
    unsigned *ix = m ? calloc(m, sizeof *ix) : 0;
    for (int pass = 0; ix && pass < 2 && got < want; ++pass) {
        unsigned n = 0;
        for (unsigned i = 0; i < m; ++i) {
            void *r = deque_at(all, i);
            unsigned id = key(r);
            const char *path = P(r, REC_PATH);
            if (id == sr.id || recent(id) || !path || catalogue_path(path) || playback_video(r))
                continue;
            if (pass ? same(sr.genre, P(r, REC_STYLE)) && !same(sr.artist, P(r, REC_ARTIST))
                     : same(sr.artist, P(r, REC_ARTIST)))
                ix[n++] = i;
        }
        for (unsigned i = n; i > 1; --i) { /* Fisher-Yates */
            unsigned j = rand_below(i), t = ix[i - 1];
            ix[i - 1] = ix[j], ix[j] = t;
        }
        for (unsigned i = 0; i < n && got < want; ++i, ++got) {
            void *r = deque_at(all, ix[i]);
            heard(key(r));
            _deque_push_back(out, r);
        }
    }
    free(ix);
    return got;
}

static void local_end(void) {
    if (!sr.local) return;
    sr.local = 0;
    np_mode_set(sr.shuf, sr.rep);
}

/* A local radio of the song r, from its place sec (-1: from its start): the song playing (sec >= 0)
 * plays on and its queue's other rows become the radio's; any other is played as the first of a new
 * queue (its fields copied first: the queue it is in goes). With nothing like it, the toast says
 * so, or, when app is the name of the app the song is a server song of and that app is too old to
 * play radios, that it needs updating: the library rarely holds a server song's artist, and "No
 * similar songs" read as the server having none. 1 when it plays. */
static int local_start(void *r, int sec, const char *app) {
    sr.id = key(r);
    fold(sr.artist, sizeof sr.artist, P(r, REC_ARTIST));
    fold(sr.genre, sizeof sr.genre, P(r, REC_STYLE));
    heard(sr.id);
    int n = 0, keep = sec >= 0 && r == playing(); /* the seed plays on: the rows after it go */
    void *all = staged(songs_unsorted, (void *)1, &n), *dq = _create_deque("stSongInfo");
    deque_init(dq);
    if (!keep) _deque_push_back(dq, r);
    unsigned got = picks(all, dq, SR_BATCH);
    if (!got && sr.nrecent > 1) { /* all of it played lately: a new radio may play it again */
        sr.nrecent = 0;
        heard(sr.id);
        got = picks(all, dq, SR_BATCH);
    }
    if (all) deque_destroy(all);
    if (got) {
        if (!sr.local) sr.shuf = np_mode_shuffle(), sr.rep = np_mode_repeat();
        np_mode_set(0, 0); /* the radio picks the order */
        sr.seek_loads = playback_loads, sr.seek_at = 0, sr.sec = -1;
        if (keep) /* no load: no gap, no seek (out of memory: song_radio_poll's refill adds) */
            playback_queue_rest(dq);
        else {
            sr.seek_at = sec > 0 ? stamp(time_now_ms()) : 0, sr.sec = sec;
            play_folder(dq, 0, 0xf001);
        }
        sr.local = 1, sr.loads = playback_loads, sr.retry_at = 0;
    }
    deque_destroy(dq);
    if (!got && app) {
        char msg[64];
        tk_snprintf(msg, sizeof msg, "Update %s to use Radio", app);
        toast(msg);
    } else if (!got)
        toast("No similar songs on this Q2 Muse");
    return got != 0;
}

int song_radio_on(void) { return sr.local || addon_radio(); }

/* A radio of song r (the playing one when sec >= 0, its place then): its app's when it has one,
 * else local. 0 when nothing starts, 1 when it started or its app was asked. */
static int start(void *r, int sec) {
    const char *path = r ? P(r, REC_PATH) : 0;
    if (!path || !*path || sr.ask) return 0;
    unsigned seq = time_now_ms() | 1;
    if (addon_radio_start(path, seq)) {
        say("asked the app, seq", (int)seq);
        sr.ask = seq, sr.ask_at = time_now_ms();
        tk_snprintf(sr.path, sizeof sr.path, "%s", path);
        sr.id = key(r), sr.sec = sec;
        sr.seek_loads = playback_loads, sr.seek_at = 0;
        return 1;
    }
    int ok = local_start(r, sec, addon_radio_missing(path));
    say("a local radio, started", ok);
    return ok;
}

/* addon.c's, as a playlist session's first entry, first, comes: whether that session is the app's
 * radio of the song playing, asked for at the tap (sr.ask, or answered since and no queue loaded),
 * and first is that song (catalogue_same_song: the same path, or the same server song). Then the
 * session keeps the song playing and replaces only the songs after it (playback_queue_rest), so
 * the seed is never sought: no seek waits for it any more. */
int song_radio_keeps(const char *first) {
    void *r = playing();
    if (!r || sr.sec < 0 || (!sr.ask && !sr.seek_at) || playback_loads != sr.seek_loads ||
        key(r) != sr.id || !catalogue_same_song(first, P(r, REC_PATH)))
        return 0;
    sr.seek_at = 0, sr.sec = -1;
    return 1;
}

static void stop(void) {
    say("stopped; it was local", sr.local);
    if (sr.local) {
        local_end();
        return;
    }
    addon_radio_stop(time_now_ms() | 1);
}

/* Now Playing's Radio tapped (np_bar.c np_mode_click). */
int song_radio_tap(const void *unused) {
    (void)unused;
    if (song_radio_on()) {
        stop();
        toast("Radio Off");
        return 0;
    }
    void *r = playing();
    int sec = -1, total = 0;
    player_playtime_and_length(&sec, &total);
    if (!r)
        toast("Nothing is playing");
    else if (sr.ask)
        toast("Starting Radio…");
    else if (start(r, sec) && sr.ask)
        toast("Starting Radio…");
    return 0;
}

/* The car's StartRadio (coda_rc_shim.c): of the song with REC_ID id (0: the playing one, which
 * plays on), or stop (stop 1). 0 when it started or stopped, 1 when it didn't, 3 when no song has
 * that id. */
int song_radio_car(int id, int stop_it) {
    say(stop_it  ? "the car stops it, on"
        : sr.ask ? "the car starts one while an answer waits, id"
                 : "the car starts one, id",
        stop_it ? song_radio_on() : id);
    if (stop_it) {
        if (song_radio_on()) stop();
        return 0;
    }
    if (!id) {
        void *r = playing();
        int sec = -1, total = 0;
        player_playtime_and_length(&sec, &total);
        return r && start(r, sec) ? 0 : 1;
    }
    int n = 0, ok = 3;
    void *all = staged(songs_unsorted, (void *)1, &n);
    for (unsigned i = 0; all && i < deque_size(all) && ok == 3; ++i)
        if (I(deque_at(all, i), REC_ID) == id) ok = start(deque_at(all, i), -1) ? 0 : 1;
    if (all) deque_destroy(all);
    return ok;
}

/* Every UI pass (ringnav_sleep): an app's answer, the seed's place, and a local radio's next songs.
 */
void song_radio_poll(void) {
    unsigned now = time_now_ms();
    if (sr.ask) {
        int code = addon_radio_answer(sr.ask);
        if (code >= 0 || now - sr.ask_at >= SR_WAIT_MS)
            say("the app's answer (-1 none in time)", code);
        if (code == 0) {
            sr.ask = 0;
            sr.seek_at = sr.sec > 0 ? stamp(now) : 0;
        } else if (code ==
                   1) { /* not the app's song: the library's radio, if the song is still there */
            sr.ask = 0;
            void *r = playing();
            if (r && !strcmp(P(r, REC_PATH), sr.path)) {
                int sec = -1, total = 0;
                player_playtime_and_length(&sec, &total);
                local_start(r, sec, 0); /* it plays on from where it is now */
            }
        } else if (code == 3) { /* the app has no one signed in: no fallback, a local radio would
                                   hide why the server's is missing */
            sr.ask = 0;
            toast("Sign in to use Radio");
        } else if (code > 1 || now - sr.ask_at >= SR_WAIT_MS) {
            sr.ask = 0;
            toast("Radio isn't available for this song");
        }
    }
    void *r = playing();
    if (sr.seek_at && (now - sr.seek_at >= SR_SEEK_MS || !r)) sr.seek_at = 0;
    if (sr.seek_at && playback_loads != sr.seek_loads && media_playing()) {
        const char *p = P(r, REC_PATH);
        if (p && key(r) == sr.id) player_seek_time(sr.sec);
        sr.seek_at = 0; /* the radio's first song is the seed, or the seed is past */
    }
    if (r && key(r) != sr.last_id) sr.last_id = key(r), heard(key(r));
    if (!sr.local) return;
    if (playback_loads != sr.loads) { /* another queue: the radio's is gone */
        local_end();
        return;
    }
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned pos = (unsigned)I(MCL_POS, 0), size = q ? deque_size(q) : 0;
    if (!q || pos + 1 + SR_LOW < size || (sr.retry_at && now - sr.retry_at < SR_RETRY_MS)) return;
    int n = 0;
    void *all = staged(songs_unsorted, (void *)1, &n), *add = _create_deque("stSongInfo");
    deque_init(add);
    unsigned got = picks(all, add, SR_BATCH);
    if (all) deque_destroy(all);
    if (got) playback_queue_insert(q, add, 0);
    deque_destroy(add);
    sr.retry_at = got ? 0 : stamp(now);
    sr.loads = playback_loads; /* an insert is not another queue */
}
