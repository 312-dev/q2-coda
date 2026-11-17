/* Car Remote's side in demo (docs/internals.md#car-remote): the little the coda-rc daemon (patch/coda_rc.c)
 * cannot do from outside. Every UI pass (ringnav_sleep), paced to CR_POLL_MS, while Settings > Car Remote
 * is on, it:
 * - starts the daemon (at boot and when the switch goes on; again if it dies) and stops it when the switch
 *   goes off: nothing listens while it is off;
 * - writes Now Playing for it to /tmp/coda-rc.now (radio=1 while a radio plays), and the queue's song ids
 *   to /tmp/coda-rc.queue;
 * - takes its commands from /tmp/coda-rc.cmd: play a list of song ids from one of them, or shuffled (ids in
 *   /tmp/coda-rc.list), Play, Pause, Play/Pause, Next, Previous, seek, set the volume, set shuffle and
 *   repeat, rate the playing song, play a song of the queue, start or stop a radio (song_radio.c); and
 *   answers each in /tmp/coda-rc.ack, a Pause or Play once the player shows it;
 * - shows the pairing code while /tmp/coda-rc.pair exists, and writes /tmp/coda-rc.pair-cancel when the
 *   user closes it.
 * Settings > Car Remote is carremote_page.c's, polled from here.
 * Everything the protocol needs is in the daemon; here only a few words of state. */
#include "navigation.h"

#define CR_POLL_MS 500
#define CR_RESTART_MS 30000 /* a daemon that died is started again at most this often */
#define CR_SETTLE_MS 3000   /* a Pause or Play the player has not shown by then is refused (under coda_rc.c ACK_MS) */
#define CR_DAEMON "/usr/bin/coda-rc"
#define CR_PID "/tmp/coda-rc.pid"
#define CR_NOW "/tmp/coda-rc.now"
#define CR_QUEUE "/tmp/coda-rc.queue"
#define CR_CMD "/tmp/coda-rc.cmd"
#define CR_LIST "/tmp/coda-rc.list"
#define CR_ACK "/tmp/coda-rc.ack"
#define CR_PAIR "/tmp/coda-rc.pair"
#define CR_PAIR_END "/tmp/coda-rc.pair-end"
#define CR_PAIR_CANCEL "/tmp/coda-rc.pair-cancel"
#define CR_ONLINE "/tmp/coda-rc.online" /* coda_rc.c online_poll, read by carremote_page.c */
#define CR_QUEUE_MAX 5000 /* ponytail: a longer queue lists its first 5000 to the phone */
#define CR_LIST_MAX 20000 /* coda_rc_lib.c PLAY_MAX */
#define CR_HEARTS_MS 5000 /* the playing song's hearts read again at least this often */

static struct {
    int checked, on;
    unsigned at, started_at, seq, now_hash, now_n, q_hash, q_gen;
    unsigned play_timer, toast_timer;
    int want; /* a Pause (1) or Play (2) waiting for the player; 0 none */
    unsigned want_seq, want_at;
    void *pair_page;   /* the code's Dialog */
    char pair_code[8]; /* the code the Dialog shows */
    char pair_name[32]; /* the phone asking, for the toast */
    int toast_what;
    int hearts; /* the playing song's, as rate_car_hearts; read at hearts_at for hearts_id, hearts_gen */
    unsigned hearts_at, hearts_id, hearts_gen;
} cr __attribute__((section(".scratch")));

int coda_rc_enabled(void) {
    if (!cr.checked) cr.checked = 1, cr.on = config_value("Q2POD", "CARREMOTE", 2, 0);
    return cr.on;
}

/* ---- the daemon ---- */

static int cr_running(void) {
    char raw[24], proc[24];
    if (!state_read(CR_PID, raw, sizeof raw)) return 0;
    snprintf(proc, sizeof proc, "/proc/%d", atoi(raw + 1));
    return atoi(raw + 1) > 1 && !access(proc, 0);
}

static void cr_start(void) {
    cr.started_at = time_now_ms() | 1;
    if (!cr_running()) system("( " CR_DAEMON " >/dev/null 2>&1 & )");
}

static void cr_stop(void) {
    system("kill $(cat " CR_PID " 2>/dev/null) 2>/dev/null");
    unlink(CR_NOW);
    unlink(CR_QUEUE);
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

/* The playing queue as song ids, when it changes: "gen pos total" then an id a line. */
static void cr_queue(void *q, unsigned pos) {
    unsigned n = q ? deque_size(q) : 0, h = hash_bytes(FNV_SEED, (const unsigned char *)&q, sizeof q);
    h = hash_bytes(h, (const unsigned char *)&n, sizeof n);
    h = hash_bytes(h, (const unsigned char *)&playback_loads, sizeof playback_loads);
    if (h == cr.q_hash) return;
    unsigned listed = n < CR_QUEUE_MAX ? n : CR_QUEUE_MAX, len = 0, cap = 40 + listed * 12;
    char *b = calloc(1, cap);
    if (!b) return;
    cr.q_hash = h;
    ++cr.q_gen;
    len = (unsigned)snprintf(b, cap, "%u %u %u\n", cr.q_gen, pos, n);
    for (unsigned i = 0; i < listed; ++i)
        len += (unsigned)snprintf(b + len, cap - len, "%d\n", I(deque_at(q, i), REC_ID));
    blob_io(CR_QUEUE, CR_QUEUE ".tmp", b, len, 1);
    free(b);
}

/* The playing song's hearts: again on a new song, a My Fav change made here (catalogue_fav_gen) or
 * every CR_HEARTS_MS, as stock's own lists change My Fav without telling. */
static int cr_hearts(unsigned id) {
    unsigned now = time_now_ms(), gen = catalogue_fav_gen();
    if (!cr.hearts_at || id != cr.hearts_id || gen != cr.hearts_gen || now - cr.hearts_at >= CR_HEARTS_MS)
        cr.hearts = rate_car_hearts(), cr.hearts_id = id, cr.hearts_gen = gen, cr.hearts_at = now | 1;
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

static void cr_now(void) {
    void *q = P(mcl_pdeqplaylist, 0), *r = 0;
    unsigned pos = (unsigned)I(MCL_POS, 0);
    if (q && pos < deque_size(q)) r = deque_at(q, pos);
    cr_queue(q, pos);
    int status = mclGetPlayStatus(), sec = -1, total = 0;
    player_playtime_and_length(&sec, &total);
    /* the hold menu's extended play while it runs, else Now Playing's own pair (cr_modes) */
    int shuf, rep;
    cr_modes(&shuf, &rep);
    char b[1400];
    unsigned n = 0;
    const char *title = r ? P(r, REC_TITLE) : 0;
    if (r && (!title || !*title)) title = P(r, REC_NAME);
    put_int(b, sizeof b, &n, "state", status == 2 ? 1 : status == 3 ? 2 : 0);
    put_int(b, sizeof b, &n, "pos", sec > 0 ? sec * 1000 : 0);
    put_int(b, sizeof b, &n, "len", total > 0 ? total : 0);
    put_int(b, sizeof b, &n, "id", r ? I(r, REC_ID) : 0);
    put_int(b, sizeof b, &n, "shuffle", shuf);
    /* playback.c: 3 repeats the song, 4 and 5 wrap the queue (4 within an album or folder) */
    put_int(b, sizeof b, &n, "repeat", rep);
    put_int(b, sizeof b, &n, "volume", g_volume);
    put_int(b, sizeof b, &n, "hearts", cr_hearts(r ? (unsigned)I(r, REC_ID) : 0));
    put_int(b, sizeof b, &n, "qpos", (int)pos);
    put_int(b, sizeof b, &n, "qlen", q ? (int)deque_size(q) : 0);
    put_int(b, sizeof b, &n, "qgen", (int)cr.q_gen);
    put_int(b, sizeof b, &n, "radio", song_radio_on());
    put(b, sizeof b, &n, "path", r ? P(r, REC_PATH) : "");
    put(b, sizeof b, &n, "title", title);
    put(b, sizeof b, &n, "artist", r ? P(r, REC_ARTIST) : "");
    put(b, sizeof b, &n, "album", r ? P(r, REC_ALBUM) : "");
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

static void cr_ack(unsigned seq, int code) {
    char b[24];
    int n = snprintf(b, sizeof b, "%u %d\n", seq, code);
    blob_io(CR_ACK, CR_ACK ".tmp", b, (unsigned)n, 1);
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
 * when Local + Server is on) sorted by id once, then each id looked up. */
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
    char raw[64];
    cr.play_timer = 0;
    if (!state_read(CR_CMD, raw, sizeof raw)) return 0;
    unsigned seq = (unsigned)atoi(raw + 1);
    int at = 0, shuffle = 0, count = 0, from = 0;
    char *verb = strstr(raw + 1, " ");
    if (!verb || __isoc99_sscanf(verb, " play %d %d %d", &at, &shuffle, &count) != 3) return 0;
    unlink(CR_CMD);
    if (library_scanning() || addon_game_audio() || navigator_window_is_exist("usbmode_page")) {
        cr_ack(seq, 2); /* busy: the daemon answers UNAVAILABLE */
        return 0;
    }
    void *dq = cr_songs(at, &from);
    int size = (int)deque_size(dq);
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
    char raw[64];
    int id = 0;
    cr.play_timer = 0;
    if (!state_read(CR_CMD, raw, sizeof raw)) return 0;
    unsigned seq = (unsigned)atoi(raw + 1);
    char *verb = strstr(raw + 1, " ");
    int stop = verb && !strncmp(verb, " radio stop", 11);
    if (!verb || (!stop && __isoc99_sscanf(verb, " radio %d", &id) != 1)) return 0;
    unlink(CR_CMD);
    if (!stop && (library_scanning() || addon_game_audio() || navigator_window_is_exist("usbmode_page"))) {
        cr_ack(seq, 2);
        return 0;
    }
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
    if (!q || at >= deque_size(q) || I(deque_at(q, at), REC_ID) != id) return 3;
    if (at == (unsigned)I(MCL_POS, 0) && playback_keeps(q, (int)at)) {
        if (!media_playing()) media_key(KEY_PLAY);
        return 0;
    }
    if (check_forbid_play_no_headset(0)) return 1;
    press_stamp();
    player_start_queue((int)at);
    return 0;
}

static void cr_command(void) {
    char raw[64];
    if (cr.play_timer || !state_read(CR_CMD, raw, sizeof raw)) return;
    unsigned seq = (unsigned)atoi(raw + 1);
    char *verb = strstr(raw + 1, " ");
    int v = 0, w = 0;
    cr.want = 0; /* a newer command: the daemon has answered the one waiting, and its ack would be stale */
    if (verb && (!strncmp(verb, " play ", 6) || !strncmp(verb, " radio ", 7))) {
        cr.play_timer = timer_add(verb[1] == 'p' ? cr_play : cr_radio, 0, 0);
        return;
    }
    unlink(CR_CMD);
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
        if (media_playing() != want) media_key(KEY_PLAY);
        cr.want = want + 1; /* acked once the player shows it (cr_settle) */
        cr.want_seq = seq;
        cr.want_at = time_now_ms();
        cr_settle();
    } else if (!strcmp(word, "next") || !strcmp(word, "previous")) {
        media_key(word[0] == 'n' ? KEY_FWD_BTN : KEY_BACK_BTN);
        cr_ack(seq, 0);
    } else if (verb && __isoc99_sscanf(verb, " seek %d", &v) == 1 && v >= 0) {
        player_seek_time(v);
        cr_ack(seq, 0);
    } else if (verb && __isoc99_sscanf(verb, " mode %d %d", &v, &w) == 2) {
        int ok = cr_mode(v, w);
        cr_now(); /* the new mode before the ack, as cr_settle */
        cr_ack(seq, !ok);
    } else if (verb && __isoc99_sscanf(verb, " rate %d %d", &v, &w) == 2) {
        unsigned at, n;
        void *r = queue_now(&at, &n);
        int ok = r && I(r, REC_ID) == w ? rate_car(v) : -1;
        cr.hearts_at = 0; /* 1 to 4 hearts move no My Fav generation: read them now */
        cr_now();
        cr_ack(seq, ok < 0 ? 3 : !ok);
    } else if (verb && __isoc99_sscanf(verb, " jump %d %d", &v, &w) == 2 && v >= 0) {
        cr_ack(seq, cr_jump((unsigned)v, w));
    } else if (verb && __isoc99_sscanf(verb, " volume %d", &v) == 1 && v >= 0 && v <= 100) {
        /* as the AirPlay app sets it (airplay.c): up to Volume Limit, saved for the output in use */
        if (v > g_maxvolume) v = g_maxvolume;
        g_volume = (unsigned char)v;
        device_set_volume(v, 1);
        write_int_config(v, "PLAYSET", mclGetOutputWay() == 1 ? "BT_VOLUME" : "VOLUME");
        cr_ack(seq, 0);
    } else {
        cr_ack(seq, 1);
    }
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

static int pair_keyup(void *ctx, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return page_keyup(ctx, event);
    pair_cancel(ctx, event);
    return STOP;
}

/* The Dialog: the question as its title, the code in two groups of three as Wi-Fi transfer's PIN, a hint,
 * and Cancel (docs/remote-protocol.md#pairing-on-the-q2). */
static void pair_show(const char *code, const char *name) {
    char title[64], digits[8];
    snprintf(title, sizeof title, "Pair with \"%s\"?", name);
    snprintf(digits, sizeof digits, "%.3s %.3s", code, code + 3);
    reset_poweroptions_timer(1, 1, 1); /* the screen on and kept on, as a key press would */
    if (!(cr.pair_page = page_open("carpair_page", pair_closed, pair_keyup))) return;
    void *view = page_list(cr.pair_page, cr.pair_page, 0, title, 1, 48);
    page_row_detail(view, 0, "Cancel", 0, pair_cancel);
    ringnav_select(view, 0, 1);
    const scheme_t *c = scheme();
    widget_set_text_utf8(label(cr.pair_page, 0, 120, 375, 64, "s_scrlabel_white20c", 56, 0xff000000u | c->ink), digits);
    widget_set_text_utf8(label(cr.pair_page, 0, 196, 375, 24, "s_scrlabel_white20c", 16, 0xff000000u | c->muted),
                         "Enter this code in Q2 Muse Remote");
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

/* Every UI pass (ringnav_sleep). */
void coda_rc_poll(void) {
    unsigned now = time_now_ms();
    if (cr.at && now - cr.at < CR_POLL_MS) return;
    cr.at = now | !now;
    carremote_poll(); /* Settings > Car Remote's pages while open */
    if (!coda_rc_enabled()) return;
    if (!cr.started_at || (now - cr.started_at >= CR_RESTART_MS && !cr_running())) cr_start();
    cr_now();
    cr_settle();
    cr_command();
    cr_pairing();
}

/* Car Remote on or off, its daemon started or stopped: the page's switch, and Control Center's
 * circle (control_center.c). */
void coda_rc_toggle(void) {
    cr.on = !coda_rc_enabled();
    write_int_config(cr.on, "Q2POD", "CARREMOTE");
    if (cr.on)
        cr_start();
    else
        cr_stop();
}
