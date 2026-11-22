/* Now Playing, one answer for every reader (docs/internals.md#now-playing-source): who owns
 * playback (the Library's song through q2player, Radio's station through q2video -t, Internet
 * Radio's, Spotify's track through librespot, or a video), its words, whether it plays and where it
 * is.
 * - now_owner and now_status: the owner and its play state alone, cheap enough for a paint: Home's
 *   first row (home_np.c), Control Center (control_center.c), the status row's play glyph
 *   (lyrics_status_bar.c bar_status);
 * - now_song and now_time: the Library's song as Now Playing shows it and its second, for the pages
 *   that only ever show the Library (np_bar.c, nav/now_playing.c, lyrics.c) and for now_get; while
 *   Radio, Internet Radio, a video or a sound game has the output the Library is held paused where
 *   it was (now_hold, now_release);
 * - now_scrobbles: the one rule for what is scrobbled (nav/playback_power.c scrobble);
 * - now_get: all of it, for Car Remote and the iPhone cable (coda_rc_shim.c cr_now, which writes
 *   /tmp/coda-rc.now for coda-rc and q2-iap) and for now_poll, which hands it to the car over
 *   Bluetooth: an owner other than the Library as the song (a radio's title and artist from the
 *   stream's StreamTitle, tuner_song, with the station and its frequency as the album; Spotify's
 *   track; a video by name), no cover (bt_cover_hold), playing or paused as it is; the Library's
 *   song and status again after it, as Guest Play hands back; and NP_PLAY_FILE for bluetoothd's
 *   GetPlayStatus (tools/bt_meta.c): the position, the length and the monotonic time the position
 *   was read, written when any of them moves other than by the time passing, so bluetoothd adds the
 *   time since while playing.
 * A Library song's own words and status still reach bluetoothd as they did (tags.c's
 * sendBtHeadsetPlayInfo at each parse, stock's sendBtHeadsetPlayStatus at each play, pause and
 * stop). While Guest Play relays a guest's song, the guest's is the car's (q2_guest.c), and nothing
 * here speaks to bluetoothd. */
#include "navigation.h"

#define NP_PLAY_FILE "/tmp/q2-bt.play" /* BT_PLAY_FILE in tools/build.py */
#define NP_DRIFT_MS 1500 /* this far from where the time since put it: a seek, written */

/* libbtctl's track record: btctl_notify_track_changed's, as sendBtHeadsetPlayInfo builds it */
struct np_meta {
    int track, total, ms;
    const char *title, *artist, *album, *genre;
};

static struct {
    unsigned key; /* the other owner's item the car was last told of; 0: the Library's, stock's */
    int status;   /* that item's status as last sent, AVRCP's: 1 playing, 2 paused */
    int pos, len, state, locked;
    unsigned at, item; /* the stamp and the item NP_PLAY_FILE was written with */
    int held, held_sec,
        held_total; /* the Library held for another output (now_hold): its second and length */
    int start_sec;  /* where the Library's song last started, in its own seconds (now_starting) */
    unsigned
        start_key; /* ... that song's path hash and queue place, so another's start is not it */
} np __attribute__((section(".scratch")));

static unsigned np_ms(void) {
    struct {
        long sec, nsec;
    } t = { 0, 0 };
    clock_gettime(1 /* CLOCK_MONOTONIC: bluetoothd's clock too */, (void *)&t);
    return (unsigned)t.sec * 1000u + (unsigned)(t.nsec / 1000000);
}

/* The length of a server song shown before the player has it, in seconds: its server row's (a
 * pressed catalogue row, or the download a Next waits for), 0 unknown. */
static int pressed_len(const char *path) {
    int s = path ? catalogue_seconds(path) : -1;
    if (s <= 0 && path) s = catalogue_copy_seconds(path);
    return s > 0 ? s : 0;
}

/* A radio's words: the stream's song apart from its artist, else the station as the title; the
 * station (and its frequency) as the album. */
static void radio_words(now_info *n, const char *station, const char *freq, const char *raw) {
    char artist[128], song[128];
    tuner_song(raw, artist, song, sizeof song);
    tk_snprintf(n->station, sizeof n->station, "%s", station[0] ? station : freq);
    if (station[0] && freq[0])
        tk_snprintf(n->album, sizeof n->album, "%s %s", station, freq);
    else
        tk_snprintf(n->album, sizeof n->album, "%s", n->station);
    tk_snprintf(n->title, sizeof n->title, "%s",
                song[0]         ? song
                : n->station[0] ? n->station
                                : "Radio");
    tk_snprintf(n->artist, sizeof n->artist, "%s", song[0] ? artist : freq);
}

/* Who owns playback: Radio while its receiver runs (from its page's opening until the Library is
 * chosen again, its page shut or not), else headset.c media_owner's order, a video, Internet
 * Radio, Spotify, else the Library. */
int now_owner(void) {
    if (tuner_state()) return NOW_RADIO;
    return video_status()   ? NOW_VIDEO
           : radio_status() ? NOW_NETRADIO
           : spot_status()  ? NOW_SPOTIFY
                            : NOW_LIBRARY;
}

/* The owner's play state, AVRCP's and iAP2's order: 0 stopped (or nothing), 1 playing, 2 paused */
int now_status(void) {
    int s, sec, total, owner = now_owner();
    if (owner == NOW_RADIO) return tuner_state();
    s = owner == NOW_VIDEO      ? video_status()
        : owner == NOW_NETRADIO ? radio_status()
        : owner == NOW_SPOTIFY  ? spot_status()
                                : now_time(&sec, &total); /* 2 playing, 3 paused, as theirs */
    return s == 2 ? 1 : s == 3 ? 2 : 0;
}

/* What is scrobbled: the Library's songs alone, the card's files and the server songs its card
 * apps stream (a card app's own scrobble=0 aside: addon_scrobbles); never a radio, Spotify or a
 * video. */
int now_scrobbles(void) { return now_owner() == NOW_LIBRARY; }

/* The Library's song as Now Playing shows it: a server song pressed (or a Next waiting for its
 * download) from the press on (catalogue_np_now; *pressed 1), else the queue's at MCL_POS
 * (queue_now); *at and *n its place in that list. */
void *now_song(unsigned *at, unsigned *n, int *pressed) {
    void *r = catalogue_np_now(at, n);
    if (pressed) *pressed = r != 0;
    return r ? r : queue_now(at, n);
}

/* The playing song's path hash and queue place, 0 with none. */
static unsigned song_key(void) {
    unsigned at, n;
    void *r = queue_now(&at, &n);
    const char *path = r ? P(r, REC_PATH) : (const char *)0;
    return path ? fnv(FNV_SEED, (const unsigned char *)path) ^ (unsigned)MCL(MCL_POS) * 0x9e3779b1u
                : 0;
}

/* A start of the Library's player (chapters_streams_folders.c ringnav_start_player, before stock's
 * mclStartPlayer reads and clears MCL_START_SEC): where it starts, as the song's own seconds (a CUE
 * track's from its start). A song loaded paused there, as a boot resume, now_release or an output
 * change loads it, reads 0 from q2player until it is heard, as from stock's player
 * (player/engine.c play: resume_poll counts on it), and player_time answers this place until then.
 */
void now_starting(void) {
    unsigned at, n;
    void *r = queue_now(&at, &n);
    int ss = MCL(MCL_START_SEC), cue = r ? I(r, REC_CUE_START) : 0;
    np.start_sec = ss > cue ? ss - cue : 0;
    np.start_key = np.start_sec ? song_key() : 0;
}

/* player_playtime_and_length, but a song loaded paused at a place (now_starting) and not yet heard
 * reads that place, not the player's 0. */
static int player_time(int *sec, int *total) {
    int r = player_playtime_and_length(sec, total);
    if (!np.start_sec) return r;
    if (*sec > 0 || mclGetPlayStatus() != 3 || song_key() != np.start_key)
        np.start_sec = 0; /* heard, playing, stopped or another song: the player's own second */
    else
        *sec = np.start_sec;
    return r;
}

/* Stock's Now Playing update (playing_page_update, 0x53cf08) reads the state and the time through
 * these, where it called mclGetPlayStatus and player_playtime_and_length (tools/build.py
 * NP_TIME_CALLS): stopped, it writes 0 to the bar and both times. Held (now_hold) the player is
 * stopped, so they answer the held song paused at its second, and the bar and the elapsed time show
 * where Play will go on from. Otherwise they are stock's two calls. */
int now_page_status(void) { return np.held ? 3 : mclGetPlayStatus(); }
int now_page_time(int *sec, int *total) {
    if (!np.held) return player_time(sec, total);
    *sec = np.held_sec, *total = np.held_total;
    return 0;
}

/* Radio (radio_tuner.c tuner_open), an Internet Radio station (radio.c radio_play), a video
 * (videos.c video_play) or a card game with the sound (addon.c addon_game_start) takes the output:
 * the Library's song, playing or paused, is stopped with its place kept (player_save_stop; a paused
 * q2player would keep the PCM they need, and stock's player_stop, which video_start calls, forgets
 * the place), and now_time reads it paused there for as long as they have it, Radio's page shut or
 * not, and through a hand-over from one to another. The Library chosen again ends the hold: Play on
 * its Now Playing plays the song on from that place (now_take_back: radio_tuner.c tuner_key,
 * radio.c radio_take_back), and any start of its player, a pick among them, plays from the player's
 * own place (now_drop: radio_tuner.c tuner_yield and ringnav_start_player); a Radio page that found
 * no stations, shut, and a video's or a sound game's end load the song again paused there for the
 * user's Play (now_release), never playing on its own. A Library that was stopped is left as it is.
 */
void now_hold(void) {
    int status = mclGetPlayStatus();
    if (np.held || (status != 2 && status != 3)) return;
    np.held_sec = -1, np.held_total = 0;
    player_playtime_and_length(&np.held_sec, &np.held_total);
    np.held = 1;
    player_save_stop();
}
int now_release(void) {
    if (!np.held) return 0;
    np.held = 0;
    player_save_start(1);
    return 1;
}
/* The Library's player started (a pick: radio_tuner.c tuner_yield; any start:
 * ringnav_start_player): the held song is forgotten, the player's place is the one read. */
void now_drop(void) { np.held = 0; }
/* Radio or a station ended by the Library's Play (tuner_key, radio_take_back): the held song
 * playing on from its place, or, with none held (the Library was stopped when it started), stock's
 * Play. */
void now_take_back(void) {
    if (!np.held) {
        player_play_pause();
        return;
    }
    np.held = 0;
    player_save_start(0);
}

/* The Library's song's second and length as the player has them (-1 and 0 for none), and its play
 * state, mclGetPlayStatus's: 1 stopped, 2 playing, 3 paused; held (now_hold), paused where it was.
 */
int now_time(int *sec, int *total) {
    if (np.held) {
        *sec = np.held_sec, *total = np.held_total;
        return 3;
    }
    *sec = -1, *total = 0;
    player_time(sec, total);
    return mclGetPlayStatus();
}

void now_get(now_info *n) {
    char station[256], freq[256], raw[256]; /* tuner_now and radio_now fill each to one cap */
    memset(n, 0, sizeof *n);
    n->pos_ms = n->len_ms = -1;
    n->source = now_owner();
    n->state = now_status();
    if (n->source == NOW_RADIO) {
        tuner_now(station, freq, raw, sizeof station);
        radio_words(n, station, freq, raw);
        return;
    }
    if (n->source == NOW_NETRADIO) {
        radio_now(station, raw, sizeof station);
        radio_words(n, station, "", raw);
        return;
    }
    if (n->source == NOW_SPOTIFY) {
        unsigned pos = 0, len = 0;
        spot_now(n->title, n->artist, n->album, sizeof n->title, &pos, &len);
        n->pos_ms = (int)pos, n->len_ms = len ? (int)len : -1;
        tk_snprintf(n->station, sizeof n->station, "Spotify");
        return;
    }
    if (n->source == NOW_VIDEO) {
        tk_snprintf(n->title, sizeof n->title, "Video");
        return;
    }
    int pressed, sec, total;
    unsigned at, count;
    void *rec = now_song(&at, &count, &pressed);
    now_time(&sec, &total); /* its state is now_status's, above */
    if (pressed) sec = 0, total = pressed_len(P(rec, REC_PATH));
    unsigned lms = 0;
    /* a streamed song still arriving: the server's length, or 0 */
    int cert = np_stream_length(&lms);
    unsigned qat, qn;
    queue_now(&qat, &qn);
    n->rec = rec, n->pressed = pressed;
    n->pos_ms = sec > 0 ? sec * 1000 : 0;
    n->len_ms = cert == 1 ? (int)lms : cert == 2 ? 0 : total > 0 ? total * 1000 : 0;
    n->id = rec ? I(rec, REC_ID) : 0;
    n->qpos = qat, n->qlen = qn;
    const char *title = rec ? P(rec, REC_TITLE) : 0;
    if (rec && (!title || !*title)) title = P(rec, REC_NAME);
    tk_snprintf(n->title, sizeof n->title, "%s", title ? title : "");
    tk_snprintf(n->artist, sizeof n->artist, "%s",
                rec && P(rec, REC_ARTIST) ? P(rec, REC_ARTIST) : "");
    tk_snprintf(n->album, sizeof n->album, "%s", rec && P(rec, REC_ALBUM) ? P(rec, REC_ALBUM) : "");
    tk_snprintf(n->path, sizeof n->path, "%s", rec && P(rec, REC_PATH) ? P(rec, REC_PATH) : "");
}

/* An item's key other than the Library's: its source and words, never 0 */
static unsigned radio_key(const now_info *n) {
    unsigned h = hash_bytes(FNV_SEED, (const unsigned char *)&n->source, sizeof n->source);
    h = hash_bytes(h, (const unsigned char *)n->title, strlen(n->title));
    h = hash_bytes(h, (const unsigned char *)n->artist, strlen(n->artist));
    h = hash_bytes(h, (const unsigned char *)n->album, strlen(n->album));
    return h | 1;
}

/* bluetoothd's record and status: another owner's while it plays, the Library's again after */
static void tell_bluetooth(const now_info *n) {
    if (n->source == NOW_LIBRARY) {
        if (!np.key) return;
        np.key = 0, np.status = 0;
        bt_cover_hold(0);
        sendBtHeadsetPlayInfo(); /* the Library's song and status, as Guest Play hands back */
        sendBtHeadsetPlayStatus();
        return;
    }
    unsigned key = radio_key(n);
    if (key != np.key) {
        if (!np.key) bt_cover_hold(1); /* no cover but the Library's: set aside */
        /* -1: no track number or count, and no length for a stream or a video */
        struct np_meta m = { -1, -1, n->len_ms, n->title, n->artist, n->album, 0 };
        btctl_notify_track_changed(&m);
        if (!np.key) np.status = 0; /* from the Library: the new owner's status told once */
        np.key = key;
    }
    if (n->state != np.status) btctl_notify_playback_status_changed(n->state), np.status = n->state;
}

/* NP_PLAY_FILE: written when the item, the state or the length changes, when the position moves on
 * from a coarse stamp (the player's position is whole seconds: its first change after a write
 * stamps the second's start), and NP_DRIFT_MS from where the time since put it (a seek). */
static void tell_position(const now_info *n) {
    unsigned now = np_ms(),
             item = n->source == NOW_LIBRARY
                        ? (unsigned)n->id ^
                              hash_bytes(FNV_SEED, (const unsigned char *)n->path, strlen(n->path))
                        : radio_key(n);
    int pos = n->state ? n->pos_ms : -1, len = n->len_ms > 0 ? n->len_ms : -1;
    int expect = np.pos < 0 ? -1 : np.pos + (np.state == 1 ? (int)(now - np.at) : 0);
    int changed = item != np.item || n->state != np.state || len != np.len || !np.at;
    if (!changed && pos >= 0 && (pos - expect > NP_DRIFT_MS || expect - pos > NP_DRIFT_MS))
        changed = 1;
    else if (!changed && n->state == 1 && !np.locked && pos != np.pos)
        changed = np.locked = 1;
    else if (changed)
        np.locked = 0;
    if (!changed) return;
    char b[48];
    int k = tk_snprintf(b, sizeof b, "%d %d %u\n", pos, len, now);
    if (!blob_io(NP_PLAY_FILE, NP_PLAY_FILE ".tmp", b, (unsigned)k, 1))
        return; /* tried again next pass */
    np.item = item, np.state = n->state, np.pos = pos, np.len = len, np.at = now;
}

/* Every UI pass (ringnav_sleep) */
void now_poll(void) {
    if (guest_active()) return; /* the guest's song is the car's (q2_guest.c) */
    now_info n;
    now_get(&n);
    tell_bluetooth(&n);
    tell_position(&n);
}
