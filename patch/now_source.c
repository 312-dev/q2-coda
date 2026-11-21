/* Now Playing, one answer for every reader outside the Q2's own pages
 * (docs/internals.md#now-playing-source): what plays (the Library's song through q2player, Radio's
 * station through q2video -t, or Internet Radio's), its words, whether it plays and where it is.
 * now_get is that answer; Car Remote and the iPhone cable read it (coda_rc_shim.c cr_now, which
 * writes /tmp/coda-rc.now for coda-rc and q2-iap), and now_poll hands it to the car over Bluetooth:
 * - a radio as the song: its title and artist from the stream's StreamTitle (tuner_song), the station
 *   and its frequency as the album, no length, no cover (bt_cover_hold), playing or paused as the
 *   radio is; the Library's song and status again when the radio goes, as Guest Play hands back;
 * - NP_PLAY_FILE for bluetoothd's GetPlayStatus (tools/bt_meta.c): the position, the length and the
 *   monotonic time the position was read, written when any of them moves other than by the time
 *   passing, so bluetoothd adds the time since while playing.
 * A Library song's own words and status still reach bluetoothd as they did (tags.c's
 * sendBtHeadsetPlayInfo at each parse, stock's sendBtHeadsetPlayStatus at each play, pause and stop).
 * While Guest Play relays a guest's song, the guest's is the car's (q2_guest.c), and nothing here
 * speaks to bluetoothd. */
#include "navigation.h"

#define NP_PLAY_FILE "/tmp/q2-bt.play" /* BT_PLAY_FILE in tools/build.py */
#define NP_DRIFT_MS 1500 /* a position this far from where the time since put it: a seek, written */

/* libbtctl's track record as btctl_notify_track_changed takes it (sendBtHeadsetPlayInfo builds one) */
struct np_meta {
    int track, total, ms;
    const char *title, *artist, *album, *genre;
};

static struct {
    unsigned key;      /* the radio item the car was last told of (0: the Library's song, as stock tells it) */
    int status;        /* the radio's status as last sent, AVRCP's: 1 playing, 2 paused */
    int pos, len, state, locked;
    unsigned at, item; /* the stamp and the item NP_PLAY_FILE was written with */
} np __attribute__((section(".scratch")));

static unsigned np_ms(void) {
    struct {
        long sec, nsec;
    } t = { 0, 0 };
    clock_gettime(1 /* CLOCK_MONOTONIC: bluetoothd's clock too */, (void *)&t);
    return (unsigned)t.sec * 1000u + (unsigned)(t.nsec / 1000000);
}

/* The length of a server song shown before the player has it, in seconds: its server row's (a pressed
 * catalogue row, or the download a Next waits for), 0 unknown. */
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
    tk_snprintf(n->title, sizeof n->title, "%s", song[0] ? song : n->station[0] ? n->station : "Radio");
    tk_snprintf(n->artist, sizeof n->artist, "%s", song[0] ? artist : freq);
}

void now_get(now_info *n) {
    char station[64], freq[16], raw[256];
    memset(n, 0, sizeof *n);
    n->pos_ms = n->len_ms = -1;
    int t = tuner_now(station, freq, raw, sizeof station < sizeof raw ? sizeof raw : sizeof station);
    if (t) {
        n->source = NOW_RADIO, n->state = t;
        radio_words(n, station, freq, raw);
        return;
    }
    int r = radio_now(station, raw, sizeof station);
    if (r) {
        n->source = NOW_NETRADIO, n->state = r == 2 ? 1 : 2;
        radio_words(n, station, "", raw);
        return;
    }
    /* the Library: a server song pressed (or a Next waiting for its download) from the press on, as Now
     * Playing shows it (catalogue_np_now), else the queue's song at MCL_POS */
    void *q = P(mcl_pdeqplaylist, 0), *rec = 0;
    unsigned pos = (unsigned)I(MCL_POS, 0), pat, pn;
    if (q && pos < deque_size(q)) rec = deque_at(q, pos);
    void *pressed = catalogue_np_now(&pat, &pn);
    if (pressed) rec = pressed;
    int status = mclGetPlayStatus(), sec = -1, total = 0;
    player_playtime_and_length(&sec, &total);
    if (pressed) sec = 0, total = pressed_len(P(pressed, REC_PATH));
    unsigned lms = 0;
    int cert = np_stream_length(&lms); /* a streamed song still arriving: the server's length, or 0 */
    n->source = NOW_LIBRARY, n->rec = rec, n->pressed = pressed != 0;
    n->state = status == 2 ? 1 : status == 3 ? 2 : 0;
    n->pos_ms = sec > 0 ? sec * 1000 : 0;
    n->len_ms = cert == 1 ? (int)lms : cert == 2 ? 0 : total > 0 ? total * 1000 : 0;
    n->id = rec ? I(rec, REC_ID) : 0;
    n->qpos = pos, n->qlen = q ? deque_size(q) : 0;
    const char *title = rec ? P(rec, REC_TITLE) : 0;
    if (rec && (!title || !*title)) title = P(rec, REC_NAME);
    tk_snprintf(n->title, sizeof n->title, "%s", title ? title : "");
    tk_snprintf(n->artist, sizeof n->artist, "%s", rec && P(rec, REC_ARTIST) ? P(rec, REC_ARTIST) : "");
    tk_snprintf(n->album, sizeof n->album, "%s", rec && P(rec, REC_ALBUM) ? P(rec, REC_ALBUM) : "");
    tk_snprintf(n->path, sizeof n->path, "%s", rec && P(rec, REC_PATH) ? P(rec, REC_PATH) : "");
}

/* A radio item's key: its source and words, never 0 */
static unsigned radio_key(const now_info *n) {
    unsigned h = hash_bytes(FNV_SEED, (const unsigned char *)&n->source, sizeof n->source);
    h = hash_bytes(h, (const unsigned char *)n->title, strlen(n->title));
    h = hash_bytes(h, (const unsigned char *)n->artist, strlen(n->artist));
    h = hash_bytes(h, (const unsigned char *)n->album, strlen(n->album));
    return h | 1;
}

/* bluetoothd's record and status: a radio's while one is the source, the Library's again after */
static void tell_bluetooth(const now_info *n) {
    int radio = n->source == NOW_RADIO || n->source == NOW_NETRADIO;
    if (!radio) {
        if (!np.key) return;
        np.key = 0, np.status = 0;
        bt_cover_hold(0);
        sendBtHeadsetPlayInfo(); /* the Library's song and status, as Guest Play hands back */
        sendBtHeadsetPlayStatus();
        return;
    }
    unsigned key = radio_key(n);
    if (key != np.key) {
        if (!np.key) bt_cover_hold(1); /* no cover for a stream: the Library's set aside */
        struct np_meta m = { -1, -1, -1, n->title, n->artist, n->album, 0 }; /* -1: no number, no length */
        btctl_notify_track_changed(&m);
        if (!np.key) np.status = 0; /* from the Library: the radio's status told once */
        np.key = key;
    }
    if (n->state != np.status) btctl_notify_playback_status_changed(n->state), np.status = n->state;
}

/* NP_PLAY_FILE: written when the item, the state or the length changes, when the position moves on
 * from a coarse stamp (the player's position is whole seconds: its first change after a write
 * stamps the second's start), and when it is NP_DRIFT_MS from where the time since put it (a seek). */
static void tell_position(const now_info *n) {
    unsigned now = np_ms(), item = n->source == NOW_LIBRARY ? (unsigned)n->id ^ hash_bytes(FNV_SEED,
        (const unsigned char *)n->path, strlen(n->path)) : radio_key(n);
    int pos = n->state ? n->pos_ms : -1, len = n->len_ms > 0 ? n->len_ms : -1;
    int expect = np.pos < 0 ? -1 : np.pos + (np.state == 1 ? (int)(now - np.at) : 0);
    int changed = item != np.item || n->state != np.state || len != np.len || !np.at;
    if (!changed && pos >= 0 && (pos - expect > NP_DRIFT_MS || expect - pos > NP_DRIFT_MS)) changed = 1;
    else if (!changed && n->state == 1 && !np.locked && pos != np.pos) changed = np.locked = 1;
    else if (changed) np.locked = 0;
    if (!changed) return;
    char b[48];
    int k = tk_snprintf(b, sizeof b, "%d %d %u\n", pos, len, now);
    if (!blob_io(NP_PLAY_FILE, NP_PLAY_FILE ".tmp", b, (unsigned)k, 1)) return; /* tried again next pass */
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
