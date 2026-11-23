/* Now Playing on iDrive (docs/iphone-mode.md#iap2): the song's attributes and the playback state the car asked for at
 * StartNowPlayingUpdates, looked at every half second, the cover's transfer named with them, and Up Next's list when
 * the queue changes. Part of the PodBox port (iap2.h). */
#include "q2_iap_sys.h"
#include "iap2.h"
#include "iap2_link.h"
#include "iap2_session.h"
#include "iap2_msg.h"
#include "iap2_xfer.h"
#include "iap2_library.h"
#include "iap2_nowplaying.h"

static struct iap2_np np_last;

static u32 np_hash(const struct iap2_np *n) {
    u32 h = 2166136261u;
    for (const char *s = n->title; *s; ++s) h = (h ^ (u8)*s) * 16777619u;
    for (const char *s = n->artist; *s; ++s) h = (h ^ (u8)*s) * 16777619u;
    return h;
}

/* The song's attributes the car asked for (group 0) and its playback state (group 1). With nothing known, the
 * title is the Q2's name, stopped. A stream (live) has no duration or position, and an empty cover. */
static void send_now_playing(const struct iap2_np *n, int media) {
    msg_start(0x5001);
    if (media) {
        group_start(0);
        /* the library's id for a song in it (lib_item), so the car knows the song it plays; else a hash */
        if (media_mask & 1u << MI_PERSISTENT_ID)
            param_u64(MI_PERSISTENT_ID, n->id ? LIB_KEY : 0x51320000u, n->id ? n->id : np_hash(n));
        if (media_mask & 1u << MI_TITLE) param_str(MI_TITLE, n->title[0] ? n->title : H->name);
        if ((media_mask & 1u << MI_DURATION) && !n->live) param_u32(MI_DURATION, n->len_ms);
        if ((media_mask & 1u << MI_ALBUM) && n->album[0]) param_str(MI_ALBUM, n->album);
        if ((media_mask & 1u << MI_ARTIST) && n->artist[0]) param_str(MI_ARTIST, n->artist);
        for (unsigned a = MI_LIKE_SUPPORTED; a <= MI_BANNED; ++a)
            if (media_mask & 1u << a) param_u8(a, 0);
        if (media_mask & 1u << MI_CHAPTER_COUNT) param_u16(MI_CHAPTER_COUNT, 0);
        if (media_mask & 1u << MI_ARTWORK) { /* a stream's an empty transfer: no cover (art_start) */
            u8 id = art_start(n);
            if (id) param_u8(MI_ARTWORK, id);
        }
        group_end();
    }
    group_start(1);
    if (playback_mask & 1u << PB_STATE) param_u8(PB_STATE, n->state);
    if ((playback_mask & 1u << PB_POSITION) && !n->live) param_u32(PB_POSITION, n->pos_ms);
    if (playback_mask & 1u << PB_QUEUE_INDEX) param_u32(PB_QUEUE_INDEX, n->qpos);
    if (playback_mask & 1u << PB_QUEUE_COUNT) param_u32(PB_QUEUE_COUNT, n->qlen);
    if (playback_mask & 1u << PB_SHUFFLE) param_u8(PB_SHUFFLE, n->shuffle ? 1 : 0);
    if (playback_mask & 1u << PB_REPEAT) param_u8(PB_REPEAT, n->repeat);
    if (playback_mask & 1u << PB_CHAPTER_INDEX) param_u32(PB_CHAPTER_INDEX, 0);
    if (playback_mask & 1u << PB_LIBRARY) param_str(PB_LIBRARY, H->library_id);
    if (playback_mask & 1u << PB_APP_NAME) param_str(PB_APP_NAME, H->name);
    if (playback_mask & 1u << PB_SPEED) param_u16(PB_SPEED, 100);
    if (playback_mask & 1u << PB_SEEKABLE) param_u8(PB_SEEKABLE, 1);
    if (playback_mask & 1u << PB_APP_BUNDLE) param_str(PB_APP_BUNDLE, "org.q2muse.player");
    group_end();
    msg_send();
    if (media && art_id && art_going && !art_sent) art_setup();
}

/* Every half second while the car takes Now Playing: everything again on a new song or state, else the position
 * while playing (PodBox: without it the car's bar sits at 0:00), and paused when it moved (a seek); a stream's
 * none. */
/* ---- Up Next on iDrive: the playing queue as a list (docs/iphone-mode.md#the-library-on-idrive) ----
 * When the queue changes (the shim's qgen), once it has held still a second, an iPhone sends PB_QUEUE_LIST 1 and an
 * empty PB_QUEUE_LIST_ID, then the queue as a file transfer, [ID, 04, size, 00 03] and the songs' ids, 8 bytes each,
 * in the order they play (PodBox, from an iPhone in its car). A queue with a song outside the library (a card app's
 * stream), or longer than QLIST_MAX, is PB_QUEUE_LIST 0 and no list: the car then shows only the place in it. */

static void ql_announce(void) {
    static unsigned rids[QLIST_MAX];
    char t[96];
    int n = H->queue ? H->queue(rids, QLIST_MAX) : -1, whole = n > 0 && file_session;
    for (int i = 0; whole && i < n; ++i)
        if (!rids[i]) whole = 0;
    msg_start(0x5001), group_start(1), param_u8(PB_QUEUE_LIST, (u8)whole);
    if (whole) param(PB_QUEUE_LIST_ID, 0, 0);
    group_end(), msg_send();
    snprintf(t, sizeof t, "queue: %d songs, %s", n, whole ? "sent as a list" : "no list");
    H->log(t);
    if (!whole) return;
    if (ql_going && ql_id) file_send(ql_id, 0x02, 0, 0);
    ql_id = transfer_take();
    ql_len = 0;
    for (int i = 0; i < n; ++i) put_id8(ql + ql_len, LIB_KEY, rids[i]), ql_len += 8;
    ql_going = 1;
    u8 setup[10] = { 0, 0, 0, 0, ql_len >> 24, ql_len >> 16, ql_len >> 8, ql_len, 0, 3 };
    file_send(ql_id, 0x04, setup, sizeof setup);
}

/* A cover found since Now Playing went (q2_iap.c art_poll): all of it again, a new transfer with it. */
static void iap2_art_again(void) {
    if (now_playing) np_sent = 0, np_at = 0;
}

/* A new song's Now Playing waits for its cover's quick sources (Cover Flow's, the app's, one beside it: q2_iap.c
 * art_child), ART_HOLD_MS at most, so its first update names the cover; past that, or once the child goes on to an
 * ask or ffmpeg (iap2_art_slow), it goes with an empty one, and the cover follows (iap2_art_done). */
#define ART_HOLD_MS 1000
static long art_hold_at; /* since when Now Playing waits, 0 not */
void iap2_art_slow(void) {
    if (art_hold_at) np_at = 0; /* at the next tick, with no cover yet */
}
void iap2_art_done(int found) {
    if (art_hold_at || found) iap2_art_again();
}

void poll_now_playing(long now) {
    struct iap2_np n;
    if (np_at && now - np_at < 500) return;
    np_at = now;
    memset(&n, 0, sizeof n);
    if (!H->now_playing || !H->now_playing(&n)) n.state = 0;
    int changed = !np_sent || np_hash(&n) != np_hash(&np_last) || n.state != np_last.state ||
                  n.len_ms != np_last.len_ms || n.live != np_last.live;
    if (changed && (media_mask & 1u << MI_ARTWORK) && !n.live && H->art_pending && H->art_pending(&n)) {
        if (!art_hold_at) art_hold_at = now | 1;
        if (now - art_hold_at < ART_HOLD_MS) {
            np_at = now - 450; /* looked at again in 50 ms */
            return;
        }
    }
    art_hold_at = 0;
    if (changed) send_now_playing(&n, 1);
    else if (!n.live && (n.state == 1 || n.pos_ms != np_last.pos_ms)) {
        msg_quiet = 1, ++quiet_pos;
        msg_start(0x5001), group_start(1), param_u32(PB_POSITION, n.pos_ms), group_end(), msg_send();
        msg_quiet = 0;
    }
    if ((playback_mask & 1u << PB_QUEUE_LIST) && n.qgen != np_last.qgen) ql_seen_at = now; /* the queue moved */
    np_last = n, np_sent = 1;
    if (ql_seen_at && now - ql_seen_at >= 1000) ql_seen_at = 0, ql_announce();
}
