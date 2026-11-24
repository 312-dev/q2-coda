/* The playback thread: takes demo's requests from ctl, plays one song after another and reports each one's end.
 *
 * What demo sees is stock's (docs/player.md#events): after set-songurl the status is "ready" until the song is heard,
 * media-info answers once the file is open, and a song ends with exactly one of
 *   {player-status\exit}      it played to its end
 *   {player-nextstatus\exit}  set-nexturl replaced it
 *   {player-stopstatus\exit}  mcl-stop
 *   {player-status\error}     it would not open or play, after /tmp/notfile or /tmp/notsupport is made for demo's
 *                             dialog; none for a song a waiting set-songurl or mcl-stop has already replaced
 * A song preloaded by set-gapless is opened on a thread of its own while the one before plays, and starts on the frame
 * after that one's last, in the same PCM when rate, channels and width are the same. A preload that a queue edit, a
 * stop or a skip cancelled is closed by its thread when its open returns, and never played. */
#include "q2player.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

struct ctl ctl = {.mu = PTHREAD_MUTEX_INITIALIZER, .wake = PTHREAD_COND_INITIALIZER};

static struct output *out;
static int out_way; /* the output way out was opened on */
static int out_cut; /* out was dropped for a skip: its tail is not to be played out */

enum { ENDED, NEXT, STOPPED, FAILED, MOVED /* reopen: opened for another output */ };
/* How often a song playing looks for the car's wish (car_way): an open of /tmp/q2-iap.audio, which fails while no car
 * asks. ponytail: 34 such opens in 10 s of a1 on the host, 0.7 ms of the 17.8 ms its syscalls took, made from the
 * playback loop, which is awake anyway, so none is a wake-up; an inotify watch on /tmp would wake for every file
 * written there (each app's .stream twice a second). A message from q2-iap would lift it. */
enum { CAR_LOOK_MS = 250 };

/* How a DSD file goes out on an output, as stock decides it at each file (0x4210d4-0x421a24): on Bluetooth as PCM;
 * on a Q2's own DAC (hardware type 17) as DoP whatever mcl-dsdmode says; elsewhere, a USB DAC, as mcl-dsdmode says. */
static int dsd_out(int way, int hw, int mode) {
    if (way == 1 || way == 3) return DSD_PCM;
    if (way == 0 && (hw == 17 || !hw)) return DSD_DOP; /* a preload's song has no type: the Q2's */
    return mode == 1 ? DSD_DOP : mode == 2 ? DSD_NATIVE : DSD_PCM;
}

/* The output demo's way stands for: the car's USB audio (way 3, output.c output_car) while the iPhone gadget's car
 * wants it, in place of the DAC's or Bluetooth's; a USB DAC (way 2) is never in the car. Every image's player
 * (docs/iphone-mode.md#car-audio). The car's own knob is its volume: way 3 plays at unity. */
static int car_way(int way) { return (way == 0 || way == 1) && output_car(NULL, 0, NULL) ? 3 : way; }

/* Stock's software volume, set at each song (0x4235a8): always on Bluetooth, on a USB DAC with mcl-softvolflag 1,
 * and otherwise unity, since stock builds each song's filters afresh; while a song plays an mcl-setvol reaches it on
 * either (0x42c820). */
enum { UNITY = 100 << 8 | 100 };
static void volume_at_start(int way) {
    pthread_mutex_lock(&ctl.mu);
    int v = ctl.volume, on = way == 1 || (way == 2 && ctl.softvol);
    ctl.volume_changed = 0;
    pthread_mutex_unlock(&ctl.mu);
    output_volume(out, on ? v : UNITY);
}

/* A song that would not open or play, with its path, in the unified log and so the Bluetooth event log
 * (docs/internals.md#bluetooth-event-log): demo shows a dialog or moves on, and the drive of 2026-11-19 left some
 * songs silent with no word of which. A URL's query is left out, as it carries the app's token. */
static void log_failed(const char *what, const struct song *s, int error) {
    char line[256];
    const char *u = s->url;
    const char *why = error == ERR_MISSING       ? "missing"
                      : error == ERR_UNSUPPORTED ? "unsupported"
                      : error == ERR_NONE        ? "the output would not open or take audio"
                                                 : "other";
    int n = snprintf(line, sizeof line, "q2player: %s (%s): %.*s", what, why,
                     (int)strcspn(u, strncmp(u, "http", 4) ? "|" : "|?"), u);
    plog('E', line, n < (int)sizeof line ? n : (int)sizeof line - 1);
}

static void touch(const char *path) {
    int fd = open(path, O_WRONLY | O_CREAT, 0644);
    if (fd >= 0) close(fd);
}

static int64_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000LL + t.tv_nsec / 1000000;
}

/* demo learns of a song's end only from the newest player-status it reads: a second exit inside one of its UI passes
 * (up to 222 ms) is lost, and with it the move to the next song, as when a preloaded file has no audio and ends as it
 * starts. So an exit goes no sooner than 300 ms after the one before, as in Q2 Muse's player
 * (docs/internals.md#a-bad-file-next). A song of 300 ms or more never waits. */
enum { EXIT_HOLD_MS = 300 };
static void send_exit(void) {
    static int64_t last;
    int64_t now = now_ms();
    if (last && now - last < EXIT_HOLD_MS) {
        usleep((useconds_t)(EXIT_HOLD_MS - (now - last)) * 1000);
        now = last + EXIT_HOLD_MS;
    }
    last = now;
    ipc_event("{player-status\\exit}");
}

/* ---- the preload: set-gapless's song, opened on its own thread so the song playing never waits on a slow open */

struct preload {
    struct song song;
    struct source *src; /* the open song, once done and if it opened */
    int error, done;
    int cancel; /* set under ctl.mu: nobody wants it; the thread closes what it opens and frees this */
};
static struct preload *pre; /* the newest set-gapless's; the playback thread's own */

static void *preload_thread(void *arg) {
    struct preload *p = arg;
    int error;
    struct source *s = source_open(&p->song, &error, &p->cancel);
    pthread_mutex_lock(&ctl.mu);
    if (p->cancel) {
        pthread_mutex_unlock(&ctl.mu);
        source_close(s);
        free(p);
        return NULL;
    }
    p->src = s, p->error = error, p->done = 1;
    pthread_cond_broadcast(&ctl.wake);
    pthread_mutex_unlock(&ctl.mu);
    return NULL;
}

static void preload_cancel(void) {
    struct preload *p = pre;
    if (!p) return;
    pre = NULL;
    pthread_mutex_lock(&ctl.mu);
    int done = p->done;
    if (!done) __atomic_store_n(&p->cancel, 1, __ATOMIC_RELAXED); /* the thread frees it; a read in its open stops */
    pthread_mutex_unlock(&ctl.mu);
    if (done) {
        source_close(p->src);
        free(p);
    }
}

static void preload_start(const struct song *song) {
    pthread_attr_t a;
    pthread_t t;
    preload_cancel();
    if (!(pre = calloc(1, sizeof *pre))) return;
    pre->song = *song;
    pre->song.dsd = dsd_out(car_way(ctl.outputway), song->hw, ctl.dsdmode);
    pthread_attr_init(&a);
    pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
    if (pthread_create(&t, &a, preload_thread, pre)) { /* no thread: the song's end opens it, as a cold start */
        pre->done = 1, pre->error = ERR_OTHER;
    }
    pthread_attr_destroy(&a);
}

/* ---- playing */

static void close_output(int drain) {
    if (!out) return;
    if (drain) output_drain(out);
    output_close(out);
    out = NULL;
}

/* mcl-dropcar taken (docs/player.md#releasing-the-car), c->mu held: the request cleared, and 1 when the car's PCM is
 * open, which the caller then closes (release_car) */
static int car_release_taken(struct ctl *c) {
    int r = c->car_release;
    c->car_release = 0;
    return r && out && out_way == 3;
}
/* The car's PCM closed at once, its unplayed frames dropped: the gadget's unbind waits for the last close of its card
 * while it holds the UDC's lock. The car's file is left to its owners: the next write opens on the way it says. */
static void release_car(void) {
    output_drop(out);
    close_output(0);
    static const char line[] = "q2player: the car's PCM released"; /* stamped: the firmware times the unbind by it */
    plog('I', line, sizeof line - 1);
}

/* ---- the Clicker's click with nothing playing (docs/internals.md#key-tone): a song paused keeps its PCM, whose
 * frames not yet heard are taken back (output_hold) and written again at the resume; with none, a PCM is opened for
 * the clicks, CLICK_RATE stereo S16 on the output demo chose, with the type of the last song's device. Either way
 * silence is written CLICK_TICK_MS apart a little ahead of what plays (output_idle_fill), each click mixed into it,
 * and the PCM is held so for CLICK_HOLD_MS after the last click, so a turn of the wheel does not start and stop an
 * A2DP stream at each tick; then the silence is dropped and a PCM of its own closed. Anything else asked of the
 * player (a song, a resume, a stop, a seek, a preload, another output) ends it at once. On the headphone DAC the line
 * is unmuted for it, its release waited for, and muted again after. A PCM that will not open is not tried again
 * for CLICK_RETRY_MS (Bluetooth chosen with no headset's PCM yet). Each session ends with one line in the unified log:
 * its clicks and the frames of silence written for them, a warning when none were, as nothing was then heard. */
enum { CLICK_HOLD_MS = 3000, CLICK_TICK_MS = 30, CLICK_RETRY_MS = 10000, CLICK_RATE = 48000 };
static int last_hw = 17; /* the type of the device the last song was opened on (a Q2's) */

static void click_session(int way, int kind, int paused) {
    struct ctl *c = &ctl;
    static int64_t failed_at;
    if (!paused) {
        int64_t now = now_ms();
        if (failed_at && now - failed_at < CLICK_RETRY_MS) return;
        struct pcm_format f = {CLICK_RATE, 2, 2, 0, DSD_PCM};
        dac_song(way, DSD_PCM); /* the DAC may be in DoP mode from the last song */
        dac_mute(way);
        if (!(out = output_open(&f, way, last_hw))) { failed_at = now; return; }
        out_way = way, failed_at = 0;
        output_volume(out, UNITY);
    } else {
        if (!out || out_way != way) return;
        output_hold(out);
    }
    dac_unmute(way, 1);
    int64_t last = now_ms();
    int clicks = 1;
    long frames = output_idle_click(out, kind);
    for (;;) {
        struct timespec until;
        clock_gettime(CLOCK_REALTIME, &until);
        until.tv_nsec += CLICK_TICK_MS * 1000000L;
        if (until.tv_nsec >= 1000000000L) until.tv_sec++, until.tv_nsec -= 1000000000L;
        pthread_mutex_lock(&c->mu);
        /* paused: a resume ends it; with nothing playing an mcl-pause changes nothing */
        int other = c->play || c->stop || c->seek || c->preload || c->close_preload || (paused && !c->paused) ||
                    car_way(c->outputway) != way;
        if (!c->click && !other) pthread_cond_timedwait(&c->wake, &c->mu, &until);
        int click = c->click;
        other = c->play || c->stop || c->seek || c->preload || c->close_preload || (paused && !c->paused) ||
                car_way(c->outputway) != way;
        c->click = 0;
        pthread_mutex_unlock(&c->mu);
        int64_t now = now_ms();
        if (click) output_click(out, click), last = now, clicks++;
        if (other || now - last >= CLICK_HOLD_MS) break;
        frames += output_idle_fill(out);
    }
    dac_mute(way);
    output_idle_end(out);
    if (!paused) close_output(0);
    char line[128];
    int n = snprintf(line, sizeof line, "q2player: %d click%s %s on way %d, %ld frames of silence written for them", clicks,
                     clicks == 1 ? "" : "s", paused ? "paused" : "with nothing playing", way, frames);
    plog(frames ? 'I' : 'W', line, n < (int)sizeof line ? n : (int)sizeof line - 1);
}

/* ---- a USB DAC unplugged (docs/player.md#usb-dacs): its card is not there to open, or went under a write. The song
 * is not reported as one that cannot play (each was, and demo's dialog ran through the queue, on 2026-11-22): it waits,
 * position held, while demo's own detach moves the output (check_otg_status: the way back to the DAC and the song
 * started again, paused) or the device comes back, looked for every GONE_LOOK_MS (output_present); the output's going
 * is logged once (output.c say_gone). A preload, a Next, a stop or a new song are taken meanwhile. -1 once an output is
 * open again (out, out_way and *way set), else how the song ends: NEXT, STOPPED, or FAILED for a song demo has
 * already replaced, which is not reported. */
enum { GONE_LOOK_MS = 250 };
static int output_back(const struct pcm_format *f, int *way, const struct song *song) {
    struct ctl *c = &ctl;
    for (;;) {
        struct timespec until;
        clock_gettime(CLOCK_REALTIME, &until);
        until.tv_nsec += GONE_LOOK_MS * 1000000L;
        if (until.tv_nsec >= 1000000000L) until.tv_sec++, until.tv_nsec -= 1000000000L;
        pthread_mutex_lock(&c->mu);
        if (!c->play && !c->stop && !c->preload && !c->close_preload) pthread_cond_timedwait(&c->wake, &c->mu, &until);
        int want = c->preload, unwant = c->close_preload, replaced = c->play && !c->next, w = car_way(c->outputway);
        struct song ps = c->preload_song;
        c->preload = c->close_preload = c->click = 0; /* a click has no output to sound in */
        if (c->play && c->next) { pthread_mutex_unlock(&c->mu); return NEXT; }
        if (c->stop) { c->stop = 0; pthread_mutex_unlock(&c->mu); return STOPPED; }
        pthread_mutex_unlock(&c->mu);
        if (unwant) preload_cancel();
        if (want) preload_start(&ps);
        if (replaced) return FAILED;
        if (!output_present(w)) continue;
        dac_song(w, f->dsd);
        dac_mute(w);
        if ((out = output_open(f, w, song->hw))) {
            out_way = *way = w;
            return -1;
        }
        if (!output_gone()) return FAILED; /* there, and refused: as any output that will not open */
    }
}

/* Plays s to its end or to the request that cuts it short, through its DSP chain (dsp.c). The PCM stays open on
 * return. */
static int play_through(struct source *s, const struct song *song, struct dsp *d, int untouched, struct stretch *sx);
static int play(struct source *s, const struct song *song) {
    struct ctl *c = &ctl;
    struct info info;
    source_info(s, &info);
    int untouched = !strcmp(info.codec, "DSF") || !strcmp(info.codec, "DSD") || !strcmp(info.codec, "ISO"); /* DSD skips every filter, as stock */
    pthread_mutex_lock(&c->mu);
    if (untouched && source_format(s)->dsd == DSD_DOP && c->outputway == 0)
        info.rate /= 2; /* stock's RATE for the DAC's DoP, whatever mcl-dsdmode says (captured) */
    /* until the song is heard its position reads 0, a start offset's too, as stock's does (fixtures/pod-start.txt) */
    c->info = info, c->has_info = 1, c->position = 0, c->state = OPENING;
    int rg = c->replaygain; /* taken at each file, as stock's */
    ipc_status_note(c);
    pthread_mutex_unlock(&c->mu);
    stream_playing(source_stream(s)); /* get_cache_buffer reports this song's cache, or 0 */
    struct dsp *d = dsp_open(source_format(s), untouched, untouched ? 1 : source_replaygain(s, rg), info.length);
    dsp_path(d, song->url); /* Smart Crossfade opens it again, ahead of its end */
    /* SCAN_FILE is there while the song playing can be scanned at speed (patch/seek.c looks at a hold's start) */
    struct stretch *sx = untouched ? NULL : stretch_open(source_format(s));
    if (sx) touch(SCAN_FILE);
    else unlink(SCAN_FILE);
    int why = play_through(s, song, d, untouched, sx);
    stretch_close(sx);
    dsp_close(d, why == ENDED);
    return why;
}

/* ---- the scan at speed (docs/player.md#scan): mcl-scan's hundredths of the song's speed, through the stretch
 * (stretch.c), forward as the song comes, or backward: whole seconds decoded from a seek, each played last frame
 * first, the one before it next, so the song runs backward without a break and stretch.c's search keeps its pitch.
 * While scanning only SCAN_LEAD_MS is written ahead, not the PCM's half second, so a change of speed and the
 * release are heard that soon. */
enum { SCAN_LEAD_MS = 150 };
struct rewind {
    int on, at;     /* backward; the whole second the next block ends at */
    int64_t origin; /* the song's frame the backward run began at */
    int64_t fed;    /* frames handed to the stretch since */
    uint8_t *buf;   /* the block, reversed: n frames, used of them handed on */
    int n, used, cap;
};

/* The second before r->at, decoded and reversed into r->buf: its frames, 0 at the song's start, below 0 when it would
 * not seek. */
static int rewind_block(struct source *s, struct rewind *r) {
    const struct pcm_format *f = source_format(s);
    int fb = f->channels * f->bytes, got = 0;
    if (r->at <= 0) return 0;
    if (r->cap < f->rate * fb) {
        uint8_t *b = realloc(r->buf, (size_t)f->rate * fb);
        if (!b) return -1;
        r->buf = b, r->cap = f->rate * fb;
    }
    if (source_seek(s, r->at - 1) < 0) return -1;
    while (got < f->rate) {
        const uint8_t *data;
        int n = source_read(s, &data);
        if (n <= 0) break;
        if (n > f->rate - got) n = f->rate - got;
        memcpy(r->buf + (size_t)got * fb, data, (size_t)n * fb);
        got += n;
    }
    uint8_t t[64]; /* a frame: 8 channels of 8 bytes at most */
    for (int i = 0, j = got - 1; i < j; ++i, --j) {
        memcpy(t, r->buf + (size_t)i * fb, fb);
        memcpy(r->buf + (size_t)i * fb, r->buf + (size_t)j * fb, fb);
        memcpy(r->buf + (size_t)j * fb, t, fb);
    }
    r->at--, r->n = got, r->used = 0;
    return got;
}

/* The song's frame being heard: forward, the frames decoded since the seek at origin (the song's frame offset), less
 * those the stretch holds and the PCM's backlog at the speed it was written; backward, the frames handed on since the
 * run began at rw->origin, counted down. */
static int64_t heard_at(struct source *s, struct stretch *sx, const struct rewind *rw, int64_t origin, int64_t offset) {
    double speed = stretch_rate(sx) > 0 ? stretch_rate(sx) : 1;
    int64_t back = stretch_behind(sx) + (int64_t)(output_delay(out) * speed);
    if (rw->on) {
        int64_t at = rw->origin - (rw->fed - back);
        return at < 0 ? 0 : at;
    }
    return offset + source_frames(s) - origin - back;
}

/* source_read on the car's way, timed for its trace: a decode, or a wait for a file's bytes (grow.c) */
static int timed_read(struct source *s, const uint8_t **data) {
    int64_t t = car_trace_now();
    int n = source_read(s, data);
    car_trace_read(car_trace_now() - t);
    return n;
}

static int play_through(struct source *s, const struct song *song, struct dsp *d, int untouched, struct stretch *sx) {
    struct ctl *c = &ctl;
    output_car_watch(&c->cut); /* a stop or a skip ends a write's wait for the car (output.c sink) */
    const struct pcm_format *f = source_format(s);
    static struct rewind rw; /* its block's buffer kept for the next song */
    rw.on = 0;
    int scan = 0; /* the mcl-scan in force */
    int fb = f->channels * f->bytes;

    pthread_mutex_lock(&c->mu);
    int way = car_way(c->outputway);
    pthread_mutex_unlock(&c->mu);
    /* mcl-setoutputway takes effect here, at a song's start, as stock's: a song playing goes on where it is (captured).
     * A DSD song opened for another output (a preload) is opened again for this one. */
    if (untouched && f->dsd != dsd_out(way, song->hw, c->dsdmode)) return MOVED;
    dac_song(way, f->dsd);
    int natural = 0; /* a preloaded change that reopens: the old song played out first, and nobody pressed */
    if (way == 3) output_car_song(f->rate, dsp_tail_held()); /* the car's rate for it (docs/iphone-mode.md#per-song-rate) */
    if (out && !output_take(out, f, way)) {
        if (!out_cut) output_drain(out), natural = 1;
        dac_mute(out_way);
        close_output(0);
    }
    if (!out) {
        dac_mute(way);
        if ((out = output_open(f, way, song->hw))) out_way = way;
        else if (!output_gone()) return FAILED;
        else {
            int r = output_back(f, &way, song); /* a USB DAC unplugged: the song waits for an output */
            if (r >= 0) return r;
        }
        if (song->hw) last_hw = song->hw;
    }
    out_cut = 0;
    volume_at_start(way);
    dac_unmute(way, natural || dac_stamp(1) || dac_stamp(0));
    int64_t origin = source_frames(s); /* frames of this song written to the PCM count from here */
    int64_t offset = origin;           /* and this is where in the song that was */
    int heard = 0, pausing = 0;
    int64_t resume_at = -1; /* a car PCM released while paused (mcl-dropcar): the frame the song goes on from */
    int64_t car_look = now_ms();       /* when the car's wish (car_way) was last looked at */

    for (;;) {
        pthread_mutex_lock(&c->mu);
        while (c->paused && !(c->play && c->next) && !c->stop && !c->seek && !c->preload && !c->close_preload && !c->click) {
            if (car_release_taken(c)) { /* paused on the car: its PCM closed now, a new one opened at the resume */
                if (!rw.on && !scan) resume_at = heard_at(s, sx, &rw, origin, offset) - output_held(out);
                pthread_mutex_unlock(&c->mu);
                release_car();
                pthread_mutex_lock(&c->mu);
                continue;
            }
            if (!pausing && out) dac_mute(way), output_pause(out, 1), pausing = 1;
            pthread_cond_wait(&c->wake, &c->mu);
        }
        c->car_release = 0; /* while the song plays its writes go on: the unbind's -ENODEV ends the car's way (sink) */
        /* set-nexturl cuts the song. set-songurl alone does not: stock only notes it and plays it when this song
         * has ended (captured: fixtures/stock-replace.txt). demo sends it only when it believes nothing plays. */
        if (c->play && c->next) { pthread_mutex_unlock(&c->mu); return NEXT; }
        if (c->stop) { c->stop = 0; pthread_mutex_unlock(&c->mu); return STOPPED; }
        int seek = c->seek, to = c->seek_to, want = c->preload, unwant = c->close_preload, metering = c->explay == 1;
        int eq = c->eq_changed, click = c->click, volume = c->volume_changed ? c->volume : -1, want_scan = sx ? c->scan : 0;
        c->volume_changed = 0;
        struct song ps = c->preload_song;
        c->seek = c->preload = c->close_preload = c->eq_changed = c->click = 0;
        int paused = c->paused;
        pthread_mutex_unlock(&c->mu);
        if (!out) { /* released while paused: a PCM for the way the car's file says now, the song where it was heard */
            way = car_way(c->outputway);
            if (way == 3) output_car_song(f->rate, 0);
            if (!(out = output_open(f, way, song->hw))) return FAILED;
            out_way = way, pausing = 0;
            volume_at_start(way);
            if (resume_at >= 0 && source_seek_frame(s, resume_at) < 0) return ENDED;
            resume_at = -1;
            if (paused) output_pause(out, 1), pausing = 1;
            else dac_unmute(way, 1);
            origin = offset = source_frames(s);
        }

        if (unwant) preload_cancel();
        if (want) preload_start(&ps);
        /* a turn between forward and backward starts the song again from the second heard, as a seek there */
        int turn = (want_scan < 0) != rw.on;
        if (seek || turn) {
            int64_t here = heard_at(s, sx, &rw, origin, offset);
            dac_mute(way);
            if (!paused) usleep(150000); /* stock's wait after the mute, before the drop */
            output_drop(out);
            if (sx) stretch_reset(sx);
            int target = seek ? to : (int)(here / f->rate);
            if (turn) rw.on = want_scan < 0;
            if (rw.on) /* the next block is the second before target */
                rw.at = target, rw.origin = (int64_t)target * f->rate, rw.fed = 0, rw.n = rw.used = 0;
            else if (source_seek(s, target) < 0)
                return ENDED;
            if (!paused) dac_unmute(way, dac_stamp(1)); /* paused, the resume unmutes */
            origin = offset = source_frames(s);
            /* paused, the position shows the seek only once playing again, as stock's; playing, it shows the target
             * at once, so a song still arriving shows it while the read waits for its bytes (grow.c) */
            if (seek && !paused) {
                pthread_mutex_lock(&c->mu);
                c->position = target;
                ipc_status_note(c);
                pthread_mutex_unlock(&c->mu);
            }
        }
        if (want_scan != scan) stretch_speed(sx, (scan = want_scan) < 0 ? -scan / 100.0 : scan / 100.0);
        if (volume >= 0 && (way == 1 || way == 2)) output_volume(out, volume);
        if (paused) { /* a preload or a seek taken while paused: back to waiting; a click sounds in silence first */
            if (click && pausing) click_session(way, click, 1);
            continue;
        }
        if (pausing) dac_unmute(way, 1), output_pause(out, 0), pausing = 0; /* a resume is always a person's */
        if (eq) dsp_eq_changed(d);
        if (click) output_click(out, click);

        const uint8_t *data;
        int n;
        if (rw.on) { /* backward: the block in hand, or the second before it; at the song's start, a wait */
            if (rw.used == rw.n && rewind_block(s, &rw) <= 0) {
                usleep(20000);
                continue;
            }
            n = rw.n - rw.used < 4096 ? rw.n - rw.used : 4096;
            data = rw.buf + (size_t)rw.used * fb;
            rw.used += n, rw.fed += n;
        } else if ((n = way == 3 ? timed_read(s, &data) : source_read(s, &data)) <= 0) {
            /* a song still arriving gives up its wait for a stop or a skip (grow.c): taken above */
            if (n < 0 && __atomic_load_n(&c->cut, __ATOMIC_RELAXED)) continue;
            return ENDED;
        }
        const uint8_t *read = data;
        int64_t at = source_frames(s) - n; /* the block's first frame, when it is the file's own (no scan) */
        if (sx) n = stretch_run(sx, data, n, &data);
        int uv[2] = {0, 0}, gone = 0;
        if (n > 0) {
            dsp_frame(d, !rw.on && data == read ? at : -1);
            n = dsp_run(d, data, n, (double)heard_at(s, sx, &rw, origin, offset) / f->rate, &data);
            if (metering) dsp_meter(f, untouched, data, n, uv);
            int w = output_write(out, data, n);
            if (w == OUTPUT_CAR_GONE) gone = 1, output_car_gone(); /* the car's file or its card gone: off its way at once */
            else if (w == OUTPUT_GONE) { /* a USB DAC unplugged: on from the frame heard once an output is back */
                int64_t at = rw.on || scan ? -1 : heard_at(s, sx, &rw, origin, offset);
                dac_mute(way);
                close_output(0);
                int r = output_back(f, &way, song);
                if (r >= 0) return r;
                volume_at_start(way);
                if (at >= 0 && source_seek_frame(s, at) < 0) return ENDED;
                if (sx) stretch_reset(sx);
                origin = offset = source_frames(s);
                pausing = 0;
                dac_unmute(way, 1);
                continue;
            } else if (w < 0) return FAILED;
        }
        if (way == 3 && !gone) car_trace_tick(out, 0); /* each second looked at while the car plays (docs/iphone-mode.md#car-trace) */
        /* The car asks for its audio, or stops, while a song plays: the song moves to the other output where it is,
         * as a seek moves (the PCM's unplayed frames, under half a second, are not heard), as the car changes source.
         * With nothing else to sound, demo then pauses it (patch/usb_car.c usb_car_gone). */
        int64_t now = now_ms();
        if (gone || now - car_look >= CAR_LOOK_MS) {
            car_look = now;
            pthread_mutex_lock(&c->mu);
            int want_way = car_way(c->outputway);
            pthread_mutex_unlock(&c->mu);
            /* the car's coming and going alone: demo's own mcl-setoutputway waits for the next song, as stock's */
            if ((want_way == 3) != (way == 3)) {
                dac_mute(way);
                close_output(0);
                way = want_way;
                if (way == 3) output_car_song(f->rate, 0);
                if (!(out = output_open(f, way, song->hw))) return FAILED;
                out_way = way;
                volume_at_start(way);
                dac_unmute(way, 1);
                origin = offset = source_frames(s);
                fprintf(stderr, "q2player: output way %d now (the car's audio %s)\n", way, way == 3 ? "asked for" : "gone");
            }
        }
        if (scan && output_delay(out) > (int64_t)f->rate * SCAN_LEAD_MS / 1000) usleep(5000);

        /* What is still in the PCM is this song's newest frames, and under a gapless change the tail of the song
         * before as well: the position stays at the start, and the status "ready", until that tail has played. */
        int64_t played = rw.on ? 1 : heard_at(s, sx, &rw, origin, offset) - offset;
        if (played > 0) heard = 1;
        pthread_mutex_lock(&c->mu);
        if (played > 0) c->position = (int)(heard_at(s, sx, &rw, origin, offset) / f->rate);
        if (way == 3) car_trace_position(c->position, played); /* the car's trace line names both */
        if (heard) c->state = PLAYING;
        if (metering) c->uv[0] = uv[0], c->uv[1] = uv[1]; /* the last levels stand otherwise, as stock's */
        ipc_status_note(c);
        pthread_mutex_unlock(&c->mu);
    }
}

/* The preload's own requests (set-gapless, mcl-closegapless) that came after the song's last block; with wait, then
 * also a wait while it is still opening, until it is done or demo asks for something else. */
static void settle_preload(int wait) {
    struct ctl *c = &ctl;
    for (;;) {
        pthread_mutex_lock(&c->mu);
        int want = c->preload, unwant = c->close_preload;
        struct song ps = c->preload_song;
        c->preload = c->close_preload = 0;
        if (!want && !unwant) {
            if (!wait || !pre || pre->done || c->play || c->stop) { pthread_mutex_unlock(&c->mu); return; }
            pthread_cond_wait(&c->wake, &c->mu);
            pthread_mutex_unlock(&c->mu);
            continue;
        }
        pthread_mutex_unlock(&c->mu);
        if (unwant) preload_cancel();
        if (want) preload_start(&ps);
    }
}

/* ---- another source's sound for the car (feed.c, docs/iphone-mode.md#other-sources-over-usb) ----
 * With nothing of its own playing and the car's way the output, the player writes what q2video sent to the feed to the
 * car's PCM, at FEED_RATE, unity: the same PCM rules as a song's (a dry ring is the silence the never-stopped PCM
 * plays, output.c alsa_sw), and the same close (its silence written out first). Only FEED_LEAD_MS is written ahead of
 * the car, as a scan's SCAN_LEAD_MS, not the PCM's half second: a video's sound then trails its picture by the FIFO,
 * the ring and this lead, under 0.3 s. A song, a click, another way or the feed quiet for FEED_END_MS (the source
 * ended, paused or gone) ends it. */
enum { FEED_END_MS = 2000, FEED_TICK = 1024, FEED_LEAD_MS = 150 };
static void feed_session(void) {
    struct ctl *c = &ctl;
    struct pcm_format f = {FEED_RATE, 2, 2, 0, DSD_PCM};
    static uint8_t buf[FEED_TICK * 4];
    output_car_song(FEED_RATE, 0);
    if (!(out = output_open(&f, 3, last_hw))) return;
    out_way = 3;
    output_volume(out, UNITY);
    fprintf(stderr, "q2player: the car plays another source\n");
    for (;;) {
        pthread_mutex_lock(&c->mu);
        c->car_release = 0; /* its writes go on: the unbind's -ENODEV ends the session (sink) */
        int other = c->play || c->click || car_way(c->outputway) != 3;
        pthread_mutex_unlock(&c->mu);
        if (other) break;
        if (output_delay(out) > (int64_t)FEED_RATE * FEED_LEAD_MS / 1000) { /* far enough ahead of the car */
            feed_pulling(); /* it is taking the frames: the source is paced, not dropped */
            usleep(5000);
            continue;
        }
        int n = feed_take(buf, FEED_TICK);
        if (n > 0) {
            int w = output_write(out, buf, n);
            if (w == OUTPUT_CAR_GONE) {
                output_car_gone();
                break;
            }
            if (w < 0) break;
            car_trace_tick(out, 0); /* the car's trace for another source too (docs/iphone-mode.md#car-trace) */
            continue;
        }
        if (feed_heard_ms() > FEED_END_MS) break;
        feed_pulling(); /* the car still takes it: the source is paced, not dropped */
        usleep(10000);
    }
    fprintf(stderr, "q2player: the car's other source ended\n");
    close_output(0);
}

void *engine_thread(void *unused) {
    struct ctl *c = &ctl;
    struct source *cur = NULL;
    struct song song;
    (void)unused;
    for (;;) {
        if (!cur) {
            pthread_mutex_lock(&c->mu);
            c->state = IDLE, c->has_info = 0;
            ipc_status_note(c);
            while (!c->play) {
                c->stop = c->stop_first = c->seek = c->preload = c->close_preload = 0; /* nothing is playing: stock answers none of these */
                if (car_way(c->outputway) == 3 && feed_frames() > 0) { /* another source for the car */
                    pthread_mutex_unlock(&c->mu);
                    if (out) output_drop(out);
                    close_output(0);
                    feed_session();
                    pthread_mutex_lock(&c->mu);
                    continue;
                }
                if (c->click) { /* the Clicker's, with nothing open: a PCM of its own for it */
                    int kind = c->click, way = car_way(c->outputway);
                    c->click = 0;
                    pthread_mutex_unlock(&c->mu);
                    if (out) dac_mute(out_way), output_drop(out); /* a skip's PCM kept for a next song that never came */
                    close_output(0);
                    click_session(way, kind, 0);
                    pthread_mutex_lock(&c->mu);
                    continue;
                }
                if (car_release_taken(c)) { /* a skip's car PCM kept open: closed now */
                    pthread_mutex_unlock(&c->mu);
                    release_car();
                    pthread_mutex_lock(&c->mu);
                    continue;
                }
                pthread_cond_wait(&c->wake, &c->mu);
            }
            /* An mcl-stop after the song's command (a set-songurl or set-nexturl clears an older one with no song
             * playing, and marks it stop_first with one: ipc.c) came before
             * this thread took the song: nothing plays, as stock's answers. demo sent the two that close when a press of
             * a server song stopped the queue stock's player_start played again; it sends neither now (patch/playback.c
             * playback_start_refused), and a stop is still held so. A stop that came before the song's command
             * (stop_first) was the song before's, which has ended: this song is taken. */
            if (c->stop && !c->stop_first) {
                c->play = c->next = c->stop = c->seek = c->paused = c->play_paused = c->cut = 0;
                pthread_mutex_unlock(&c->mu);
                if (out) dac_mute(out_way), output_drop(out);
                close_output(0);
                ipc_event("{player-stopstatus\\exit}");
                continue;
            }
            song = c->play_song;
            song.dsd = dsd_out(car_way(c->outputway), song.hw, c->dsdmode);
            c->paused = c->play_paused; /* a pause after the song's command holds for it (ipc.c) */
            c->play = c->next = c->stop = c->stop_first = c->seek = c->play_paused = c->cut = 0;
            c->state = OPENING;
            ipc_status_note(c);
            pthread_mutex_unlock(&c->mu);
            int error;
            preload_cancel();
            /* a stream's open waits for its cache's first fill: a stop or a Next meanwhile gives it up */
            if ((cur = source_open(&song, &error, &c->cut))) source_keep(cur);
            if (!cur) {
                close_output(0);
                pthread_mutex_lock(&c->mu);
                int stopped = c->stop, skipped = c->next, replaced = c->play;
                if (stopped && !c->stop_first) c->play = c->next = 0;
                if (stopped) c->stop = c->stop_first = 0; /* a song waiting behind a stop (stop_first) is taken next */
                pthread_mutex_unlock(&c->mu);
                if (stopped || skipped) { /* given up, not failed: answered as a song cut while it played */
                    ipc_event(stopped ? "{player-stopstatus\\exit}" : "{player-nextstatus\\exit}");
                    continue;
                }
                if (replaced) { /* a set-songurl came while it opened: that song is demo's now, as in FAILED below */
                    log_failed("open failed, already replaced", &song, error);
                    continue;
                }
                log_failed("open failed", &song, error);
                touch(error == ERR_MISSING ? PEQ_ROOT "/tmp/notfile" : PEQ_ROOT "/tmp/notsupport");
                ipc_event("{player-status\\error}");
                continue;
            }
        }
        int why = play(cur, &song);
        source_close(cur);
        cur = NULL;
        int sent = 0; /* the song's exit has gone */
        if (why == ENDED) {
            settle_preload(0);
            pthread_mutex_lock(&c->mu);
            int cancelled = c->play || c->stop;
            pthread_mutex_unlock(&c->mu);
            if (cancelled) preload_cancel();
            if (pre && !pre->done) {
                /* The song ended before its successor opened. demo hears of the end now, as from stock, whose main
                 * loop then opens that file itself; here the preload's own open goes on and this thread waits. */
                pthread_mutex_lock(&c->mu);
                c->has_info = 0, c->state = OPENING;
                ipc_status_note(c);
                pthread_mutex_unlock(&c->mu);
                send_exit(), sent = 1;
                settle_preload(1);
            }
        }
        pthread_mutex_lock(&c->mu);
        int skipped = c->next, waiting = c->play, stopping = c->stop;
        c->has_info = 0;
        /* a stop also forgets a song that was waiting, as stock's does, unless that song's command came after it */
        if (why == STOPPED && !c->stop_first) c->play = c->next = 0;
        if (why == STOPPED) c->stop_first = 0;
        ipc_status_note(c);
        pthread_mutex_unlock(&c->mu);
        switch (why) {
        case ENDED:
            /* a set-songurl that came while this song played goes next, not the preload; a stop leaves nothing */
            if (waiting || stopping) preload_cancel();
            if (pre && pre->src) { /* the preloaded song follows without a break; demo sends nothing more */
                cur = pre->src, song = pre->song;
                source_keep(cur);
                free(pre);
                pre = NULL;
                if (!sent) send_exit();
                break;
            }
            if (pre && pre->done) { /* it did not open: this thread opens it again, as stock's main loop does, and reports that */
                pthread_mutex_lock(&c->mu);
                if (!c->play) c->play_song = pre->song, c->play = 1;
                pthread_mutex_unlock(&c->mu);
                preload_cancel();
            }
            close_output(1);
            pthread_mutex_lock(&c->mu);
            c->state = IDLE;
            ipc_status_note(c);
            pthread_mutex_unlock(&c->mu);
            if (!sent) send_exit();
            break;
        case NEXT: /* the PCM stays open for the next song, which closes it if its format differs */
            if (out) dac_mute(out_way), output_drop(out), out_cut = 1;
            preload_cancel();
            if (skipped) ipc_event("{player-nextstatus\\exit}");
            break;
        case STOPPED:
            if (out) dac_mute(out_way), output_drop(out);
            close_output(0);
            preload_cancel();
            pthread_mutex_lock(&c->mu);
            c->state = IDLE;
            ipc_status_note(c);
            pthread_mutex_unlock(&c->mu);
            ipc_event("{player-stopstatus\\exit}");
            break;
        case MOVED: { /* a DSD song opened for another output than the one it starts on: opened again for this one */
            int error;
            pthread_mutex_lock(&c->mu);
            song.dsd = dsd_out(car_way(c->outputway), song.hw, c->dsdmode);
            pthread_mutex_unlock(&c->mu);
            if ((cur = source_open(&song, &error, NULL))) break;
            log_failed("open for the new output failed", &song, error);
            close_output(0);
            touch(PEQ_ROOT "/tmp/notsupport");
            ipc_event("{player-status\\error}");
            break;
        }
        case FAILED:
            log_failed(waiting || stopping ? "play failed, already replaced" : "play failed", &song, ERR_NONE); /* output_open or a write failed */
            close_output(0);
            preload_cancel();
            /* A song demo has already replaced (a set-songurl or a stop that came while this one's write was failing:
             * a Bluetooth headset lost mid-song, whose PCM bt_reopen waits up to 5 s for, while demo reloads the song
             * paused) is not reported: demo's error dialog would advance the queue and start the next song playing,
             * DAC powered, under a pause (docs/player-plan.md#a-failure-already-replaced). The waiting command is taken next. */
            if (waiting || stopping) break;
            touch(PEQ_ROOT "/tmp/notsupport");
            ipc_event("{player-status\\error}");
            break;
        }
    }
}
