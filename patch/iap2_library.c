/* The library on iDrive and playing from it (docs/iphone-mode.md#the-library-on-idrive): the Q2's library by name and
 * ID, the push of its songs and lists at StartMediaLibraryUpdates, and PlayMediaLibrary's four ways in. Part of the
 * PodBox port (iap2.h). */
#include "q2_iap_sys.h"
#include "iap2.h"
#include "iap2_link.h"
#include "iap2_msg.h"
#include "iap2_xfer.h"
#include "iap2_library.h"

/* The library: the Q2's own, by name and ID (its songs and lists follow in send_library_updates). */
void send_library_info(void) {
    msg_start(0x4C01);
    group_start(0), param_str(0, H->name), param_str(1, H->library_id), param_u8(2, 0), group_end();
    msg_send();
}
/* ---- the library on iDrive (docs/iphone-mode.md#the-library-on-idrive) ----
 *
 * To StartMediaLibraryUpdates (4C03: 1 the revision the car holds, 2 the item properties it wants) an iPhone's
 * answers, as PodBox sends them: 8 00, 9 01 (it can play all songs), then MediaLibraryUpdates (4C04) with the songs
 * as items (parameter 2, a group each) and the lists as playlists (parameter 3), as many to a packet as it holds, the
 * first opening with 6 (a reset) when the car's copy is not built on; each carries a revision and the progress (7).
 * iDrive shows what an update with progress 100 completes (PodBox: its BMW reloads its library at each one, and reads
 * on while 100 is held back), and the whole library takes minutes on the BMW's wire (about 6 KB a second:
 * docs/iphone-mode.md#the-link-s-pace), so a first update goes ahead and completes within seconds: up to FIRST_MAX
 * songs (the queue's from the playing song on, then the lists' in order) and every list's record, each list's songs
 * then following as a file transfer (lists_pump). The whole push follows, completing a checkpoint every
 * CHECKPOINT_MS, and the last update the revision itself, 100, and the lists' records again (their songs are all in
 * the car by then). Until the last, the revision is a partial one naming where the push has reached ("q2p." for the
 * whole library, "q2d." for changes, then the revision and the songs gone so far): a car that comes back holding one
 * of this revision's resumes from there with no reset, so a 9,199-song library goes over several short plugs. A car
 * that holds the revision gets the lists alone; one that holds the revision this Q2 last sent it whole
 * (lib->sent_rev) gets only the songs added or changed since and, as 4 each, the ids of those gone, with no reset.
 * Updates go only while nothing else waits to be sent and the window has a slot to spare, so the car's buttons and
 * Now Playing are never behind the library. Each song's id is LIB_KEY's with its rid, and Now Playing sends the same
 * id, so the car knows the song it plays; an album's, an artist's, an album artist's and a genre's ids are their
 * names' hashes under their own high word, and a list's its id (stock's playlist id, or LIST_FAVORITES and
 * LIST_RECENT) under LIB_LIST. */
#define LIB_ALBUM 0x5132000Au
#define LIB_ARTIST 0x5132000Bu
#define LIB_AARTIST 0x5132000Cu
#define LIB_GENRE 0x5132000Du
#define LIB_COMPOSER 0x5132000Eu
#define LIB_LIST 0x5132000Fu
#define FIRST_QUEUE 50           /* of them, the queue's from the playing song on */
#define CHECKPOINT_FIRST_MS 5000 /* a push with no first update of its own completes one this soon */
#define CHECKPOINT_MS 60000      /* and then one a minute */

struct iap2_lib_push ls;

static const char *lib_str(unsigned off) { return H->lib->pool + off; }

/* The update being built has no room for one more item (an item's group with its id, and the 5 bytes the update ends
 * with), in a packet of BULK_MAX or the car's maximum */
static int msg_full(void) {
    return !m.ok || m.len + 4 + 64 + 5 > BULK_MAX - HEADER_LEN - 1 || m.len + 4 + 64 + 5 > peer_max - HEADER_LEN - 1;
}

/* The first of n records (stride bytes apart, each starting with an unsigned key, sorted by it) whose key is not
 * below key: its index, n when there is none */
static unsigned lower_key(const void *base, unsigned n, unsigned stride, unsigned key) {
    unsigned lo = 0, hi = n;
    while (lo < hi) {
        unsigned mid = (lo + hi) / 2, k;
        memcpy(&k, (const u8 *)base + (size_t)mid * stride, sizeof k);
        if (k < key) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

static void lib_named(unsigned mask, unsigned id_param, u32 hi, u32 id, const char *name) {
    if (!*name) return;
    if (mask & 1u << id_param) param_u64(id_param, hi, id);
    if (mask & 1u << (id_param + 1)) param_str(id_param + 1, name);
}

static void lib_item(const struct iap2_track *t, unsigned mask) {
    group_start(2);
    param_u64(0, LIB_KEY, t->rid);
    if (mask & 1u << 1) param_str(1, lib_str(t->title));
    if (mask & 1u << 2) param_u8(2, 0);
    if ((mask & 1u << 4) && t->ms) param_u32(4, t->ms);
    lib_named(mask, 5, LIB_ALBUM, t->album_id, lib_str(t->album));
    if ((mask & 1u << 7) && t->track) param_u16(7, t->track);
    if ((mask & 1u << 9) && t->disc) param_u16(9, t->disc);
    lib_named(mask, 11, LIB_ARTIST, t->artist_id, lib_str(t->artist));
    lib_named(mask, 13, LIB_AARTIST, t->aartist_id, lib_str(t->aartist));
    lib_named(mask, 15, LIB_GENRE, t->genre_id, lib_str(t->genre));
    lib_named(mask, 17, LIB_COMPOSER, t->composer_id, lib_str(t->composer));
    group_end();
}

/* Is the car's copy of this song the one it was last sent (diff), so it needs nothing? */
static int lib_unchanged(const struct iap2_track *t) {
    const struct iap2_library *l = H->lib;
    unsigned lo = lower_key(l->sent, l->sent_n, sizeof *l->sent, t->rid);
    return lo < l->sent_n && l->sent[lo].rid == t->rid && l->sent[lo].hash == t->hash;
}

static int lib_index(unsigned rid) { /* a song's index in the library, or -1 */
    const struct iap2_library *l = H->lib;
    unsigned lo = lower_key(l->tracks, l->n, sizeof *l->tracks, rid);
    return lo < l->n && l->tracks[lo].rid == rid ? (int)lo : -1;
}
static int lib_kept(unsigned rid) { return lib_index(rid) >= 0; } /* still in the library */

static int in_first(unsigned i) { /* sent in this connection's first update */
    unsigned lo = lower_key(ls.sorted, ls.nf, sizeof *ls.sorted, i);
    return lo < ls.nf && ls.sorted[lo] == i;
}

/* Does the car hold this song as it is now: all of them, those the push cut off had reached, or (changes) those
 * unchanged since the revision last sent whole? */
static int car_has(unsigned i) {
    return ls.held || i < ls.next || (ls.diff && lib_unchanged(&H->lib->tracks[i]));
}

/* A song for the first update, by rid: one the library has, the car lacks and the set has not yet. */
static void first_add(unsigned rid) {
    int i = rid ? lib_index(rid) : -1;
    if (i < 0 || ls.nf >= FIRST_MAX || car_has((unsigned)i)) return;
    for (unsigned k = 0; k < ls.nf; ++k)
        if (ls.first[k] == (unsigned)i) return;
    ls.first[ls.nf++] = (unsigned)i;
}

/* The first update's songs: the queue's from the playing song on, then the lists' in their order. */
static void first_set(void) {
    static unsigned q[QLIST_MAX];
    const struct iap2_library *l = H->lib;
    struct iap2_np np;
    memset(&np, 0, sizeof np);
    int n = H->queue ? H->queue(q, QLIST_MAX) : -1;
    unsigned from = H->now_playing && H->now_playing(&np) && np.qpos < (unsigned)(n > 0 ? n : 0) ? np.qpos : 0;
    for (int k = (int)from; k < n && k < (int)from + FIRST_QUEUE; ++k) first_add(q[k]);
    for (unsigned j = 0; j < l->nlists && j < LISTS_MAX; ++j)
        for (unsigned k = 0; k < l->lists[j].n && ls.nf < FIRST_MAX; ++k) first_add(l->lrids[l->lists[j].at + k]);
    memcpy(ls.sorted, ls.first, ls.nf * sizeof *ls.first);
    for (unsigned i = 1; i < ls.nf; ++i) { /* insertion sort: a few hundred */
        unsigned x = ls.sorted[i], j = i;
        while (j && ls.sorted[j - 1] > x) ls.sorted[j] = ls.sorted[j - 1], --j;
        ls.sorted[j] = x;
    }
}

/* A list's record (parameter 3: 0 its id, 1 its name, 2 its folder, none, 3 not a Genius mix, 4 not a folder, 5 the
 * file transfer its songs' ids go in), a new transfer id each time it is sent. 0 when the packet is full. */
static int list_record(unsigned j) {
    const struct iap2_list *li = &H->lib->lists[j];
    unsigned mark = m.len;
    u8 id = next_transfer;
    group_start(3);
    param_u64(0, LIB_LIST, li->id);
    param_str(1, lib_str(li->name));
    param_u64(2, 0, 0);
    param_u8(3, 0), param_u8(4, 0), param_u8(5, id);
    group_end();
    if (msg_full()) {
        m.len = mark, m.ok = 1;
        return 0;
    }
    ls.list_ids[j] = transfer_take(); /* id, taken now that the record fits */
    return 1;
}

static unsigned lib_nlists(void) { return H->lib->nlists < LISTS_MAX ? H->lib->nlists : LISTS_MAX; }

/* An update while there is room: songs (or the ids of songs gone) or lists' records, as many as a packet holds. One
 * completes (progress 100, which iDrive loads, PodBox found) at the end of the first update, at the end of the push,
 * and in between at CHECKPOINT_FIRST_MS into a push that had no first update and then every CHECKPOINT_MS. */
void lib_pump(void) {
    const struct iap2_library *l = H->lib;
    char t[200];
    while ((ls.phase == LIB_FIRST || ls.phase == LIB_ITEMS || ls.phase == LIB_GONE || ls.phase == LIB_FINAL) && link_up &&
           !q_count && (!rto || count + 1 < window) && backlog() <= BACKLOG_MAX) {
        int n = 0, complete = 0, final = 0;
        msg_start(0x4C04);
        param_str(0, H->library_id);
        if (ls.reset) param(6, 0, 0), ls.reset = 0;
        while (ls.phase == LIB_FIRST && ls.fi < ls.nf) {
            unsigned mark = m.len;
            lib_item(&l->tracks[ls.first[ls.fi]], ls.mask);
            if (msg_full()) {
                m.len = mark, m.ok = 1;
                if (!n) ++ls.fi; /* a song no packet holds */
                break;
            }
            ++ls.fi, ++n, ++ls.items;
        }
        while ((ls.phase == LIB_FIRST && ls.fi >= ls.nf) || ls.phase == LIB_FINAL) {
            if (ls.rec >= lib_nlists()) {
                complete = 1, final = ls.phase == LIB_FINAL;
                break;
            }
            if (!list_record(ls.rec)) {
                if (!n) ++ls.rec; /* a record no packet holds */
                break;
            }
            ++ls.rec, ++n;
        }
        while (ls.phase == LIB_ITEMS && ls.next < l->n) {
            const struct iap2_track *tr = &l->tracks[ls.next];
            if ((ls.diff && lib_unchanged(tr)) || in_first(ls.next)) {
                ++ls.next;
                continue;
            }
            unsigned mark = m.len;
            lib_item(tr, ls.mask);
            if (msg_full()) {
                m.len = mark, m.ok = 1;
                if (!n) ++ls.next; /* a song no packet holds */
                break;
            }
            ++ls.next, ++n, ++ls.items;
        }
        if (ls.phase == LIB_ITEMS && ls.next >= l->n) ls.phase = ls.diff ? LIB_GONE : LIB_FINAL, ls.rec = 0;
        while (ls.phase == LIB_GONE && ls.gone < l->sent_n) {
            unsigned rid = l->sent[ls.gone].rid;
            if (lib_kept(rid)) {
                ++ls.gone;
                continue;
            }
            if (m.len + 12 + 4 + 64 + 5 > BULK_MAX - HEADER_LEN - 1) break;
            param_u64(4, LIB_KEY, rid);
            ++ls.gone, ++n;
        }
        if (ls.phase == LIB_GONE && ls.gone >= l->sent_n) ls.phase = LIB_FINAL, ls.rec = 0;
        if (ls.phase == LIB_FINAL && !n && ls.rec >= lib_nlists()) complete = final = 1; /* no lists: the revision */
        if ((ls.phase == LIB_ITEMS || ls.phase == LIB_GONE) &&
            H->now() - ls.complete_at >= (ls.completes ? CHECKPOINT_MS : CHECKPOINT_FIRST_MS))
            complete = 1;
        char partial[64];
        snprintf(partial, sizeof partial, "%s.%s.%u", ls.diff ? "q2d" : "q2p", l->rev, ls.next);
        param_str(1, final || ls.held ? l->rev : partial);
        param_u8(7, complete ? 100 : (u8)(l->n ? ls.next * 99u / l->n : 0));
        msg_send();
        ++ls.updates;
        if (!complete) continue;
        ++ls.completes, ls.complete_at = H->now();
        snprintf(t, sizeof t, "library: %s complete, %u songs and %u lists so far, %ld ms",
                 final ? "the whole" : ls.phase == LIB_FIRST ? "the first update" : "a checkpoint", ls.items,
                 ls.phase == LIB_FIRST || final ? lib_nlists() : 0, H->now() - ls.started);
        H->log(t);
        if (ls.phase == LIB_FIRST) {
            ls.after = ls.held ? LIB_IDLE : LIB_ITEMS;
            ls.phase = LIB_LISTS, ls.xi = 0, ls.x_state = 0;
        } else if (final) {
            snprintf(t, sizeof t, "library: %s sent, %u songs of %u in %u updates, %ld ms", ls.diff ? "changes" : "whole",
                     ls.items, l->n, ls.updates, H->now() - ls.started);
            H->log(t);
            long at = H->now();
            if (H->lib_saved) H->lib_saved();
            snprintf(t, sizeof t, "library: its end taken in %ld ms (the record handed to its writer)", H->now() - at);
            H->log(t);
            ls.after = LIB_IDLE;
            ls.phase = LIB_LISTS, ls.xi = 0, ls.x_state = 0;
        }
    }
}

/* The lists' songs, a list at a time, each as a file transfer: [ID, 04, an 8-byte size, 00 04, the list's 8-byte id,
 * the library's id and a 0], then on the car's [ID, 01] the songs' ids, 8 bytes each in order (PodBox, from an iPhone
 * in its car), a piece at a time while little waits to be sent, and the next once the last piece is on its way (as
 * PodBox's), or once the car cancels it (02), or LIST_WAIT_MS after a setup it never answers. Then on to the push
 * (ls.after). */
void lists_pump(long now) {
    const struct iap2_library *l = H->lib;
    if (ls.phase != LIB_LISTS) return;
    if (!file_session) ls.xi = lib_nlists();
    if (ls.x_state == 1 && now - ls.x_since > LIST_WAIT_MS) { /* never started: cancelled, and on */
        file_send(ls.x_id, 0x02, 0, 0);
        ls.x_state = 0, ++ls.xi;
    }
    if (!ls.x_state && ls.xi < lib_nlists() && !q_count) {
        const struct iap2_list *li = &l->lists[ls.xi];
        unsigned size = li->n * 8, k = 0;
        u8 setup[10 + 8 + 64];
        memset(setup, 0, 4);
        k = 4, setup[k++] = size >> 24, setup[k++] = size >> 16, setup[k++] = size >> 8, setup[k++] = size;
        setup[k++] = 0, setup[k++] = 4;
        put_id8(setup + k, LIB_LIST, li->id);
        k += 8;
        unsigned idn = strlen(H->library_id) + 1;
        if (idn > sizeof setup - k) idn = sizeof setup - k;
        memcpy(setup + k, H->library_id, idn), k += idn;
        ls.x_id = ls.list_ids[ls.xi], ls.x_state = 1, ls.x_since = now, ls.x_sent = 0;
        file_send(ls.x_id, 0x04, setup, k);
    }
    while (ls.x_state == 2 && q_count < 2) {
        const struct iap2_list *li = &l->lists[ls.xi];
        u8 piece[BULK_MAX];
        unsigned room = piece_room(), k = 0;
        unsigned first = ls.x_sent == 0;
        while (ls.x_sent < li->n && k + 8 <= room) put_id8(piece + k, LIB_KEY, l->lrids[li->at + ls.x_sent++]), k += 8;
        unsigned last = ls.x_sent >= li->n;
        file_send(ls.x_id, piece_op(first, last), piece, k);
        if (last) ls.x_state = 0, ++ls.xi; /* the next at once, as PodBox's does */
    }
    if (ls.xi >= lib_nlists() && !ls.x_state) {
        ls.phase = ls.after;
        if (ls.phase == LIB_ITEMS) ls.complete_at = now; /* the next checkpoint a minute on */
    }
}

/* The car's word on the list in transfer: 01 go, 02 cancelled. 1 when it was that list's. */
int list_transfer_word(const u8 *d) {
    if (ls.phase != LIB_LISTS || !ls.x_state || d[0] != ls.x_id) return 0;
    if (d[1] == 0x01 && ls.x_state == 1) ls.x_state = 2;
    else if (d[1] == 0x02) ls.x_state = 0, ++ls.xi;
    return 1;
}

/* What the car asked at StartMediaLibraryUpdates, kept while the library is read (LIB_READING): the revision it
 * holds (a longer one than this is no revision this Q2 sent) and the item properties it wants. */
static struct {
    char rev[128];
    int has_rev;
    unsigned mask;
} lib_ask;

void send_library_none(void) {
    msg_start(0x4C04), param_str(0, H->library_id), param_str(1, "1"), param_u8(7, 100), msg_send();
}

/* The car's StartMediaLibraryUpdates: its first two answers at once, then the library read off the loop (H->lib_load,
 * a child on the Q2) and the updates begun when it is in (library_read, from iap2_tick). */
void send_library_updates(const char *car_rev, unsigned mask) {
    msg_start(0x4C04), param_str(0, H->library_id), param_u8(8, 0), msg_send();
    msg_start(0x4C04), param_str(0, H->library_id), param_u8(9, 1), msg_send();
    if (!H->lib || !H->lib_load || !H->lib_ready || H->lib_load() < 0) {
        send_library_none();
        return;
    }
    lib_ask.has_rev = car_rev != 0;
    snprintf(lib_ask.rev, sizeof lib_ask.rev, "%s", car_rev ? car_rev : "");
    lib_ask.mask = mask;
    ls.phase = LIB_READING, ls.x_state = 0;
}

/* The server's catalogue unreadable while the car was last sent server songs (q2_iap_lib.c lib_read's -2, the card
 * not mounted yet): not read as every server song gone (uac1-power-app.md #4). The car keeps what it holds: its own
 * revision back, complete, and nothing recorded as sent. */
void library_unreadable(void) {
    char t[220];
    snprintf(t, sizeof t, "library: the catalogue cannot be read (the card not mounted?): the car keeps %s",
             lib_ask.has_rev ? lib_ask.rev : "nothing");
    H->log(t);
    if (!lib_ask.has_rev) {
        send_library_none();
        return;
    }
    msg_start(0x4C04), param_str(0, H->library_id), param_str(1, lib_ask.rev), param_u8(7, 100), msg_send();
}

void library_read(void) {
    char t[300];
    const char *car_rev = lib_ask.has_rev ? lib_ask.rev : 0;
    unsigned mask = lib_ask.mask;
    const struct iap2_library *l = H->lib;
    memset(&ls, 0, sizeof ls);
    ls.mask = mask, ls.started = ls.complete_at = H->now();
    ls.held = car_rev && !strcmp(car_rev, l->rev);
    ls.diff = !ls.held && car_rev && l->sent_rev[0] && !strcmp(car_rev, l->sent_rev);
    ls.reset = !ls.held && !ls.diff;
    /* a push of this revision cut off: on from where it reached, with no reset (its kind from the prefix) */
    unsigned rn = strlen(l->rev);
    if (car_rev && (!strncmp(car_rev, "q2p.", 4) || !strncmp(car_rev, "q2d.", 4)) && !strncmp(car_rev + 4, l->rev, rn) &&
        car_rev[4 + rn] == '.') {
        unsigned at = (unsigned)atoi(car_rev + 5 + rn);
        ls.diff = car_rev[2] == 'd', ls.reset = 0, ls.next = at < l->n ? at : l->n;
    }
    first_set();
    if (ls.held && !lib_nlists()) {
        snprintf(t, sizeof t, "library: the car holds %s, %u songs: nothing to send", l->rev, l->n);
        H->log(t);
        msg_start(0x4C04), param_str(0, H->library_id), param_str(1, l->rev), param_u8(7, 100), msg_send();
        return;
    }
    ls.phase = ls.nf || lib_nlists() ? LIB_FIRST : LIB_ITEMS;
    snprintf(t, sizeof t, "library: the car holds %s; %u songs and %u lists first, then %s of %s (%u songs) from %u",
             car_rev ? car_rev : "nothing", ls.nf, lib_nlists(), ls.held ? "nothing" : ls.diff ? "the changes" : "the whole",
             l->rev, l->n, ls.next);
    H->log(t);
    /* every list's name, once: on 2026-11-22 iDrive showed a list like "Apple Music Radio" and the log cut the
     * updates' names off */
    char names[1600];
    unsigned at = (unsigned)snprintf(names, sizeof names, "library: its lists:");
    for (unsigned j = 0; j < lib_nlists() && at < sizeof names - 1; ++j)
        at += (unsigned)snprintf(names + at, sizeof names - at, "%s \"%s\"", j ? "," : "", lib_str(l->lists[j].name));
    H->log(names);
    lib_pump();
}

/* ---- playing from iDrive: 4C06 to 4C09 (parameters logged whole as they come, in iap2_control_receive) ---- */

static unsigned *play_rids(unsigned n) { return n ? malloc(n * sizeof(unsigned)) : 0; }

/* PlayMediaLibraryItems: 0 the songs' ids back to back, 8 bytes each, 1 the index to start at. */
void play_items(const u8 *p, unsigned len) {
    unsigned n, sn;
    const u8 *v = find(p, len, 0, &n), *s = find(p, len, 1, &sn);
    unsigned at = s && sn == 4 ? get_be(s, 4) : 0, k = 0, *rids = play_rids(n / 8);
    for (unsigned i = 0; rids && v && i + 8 <= n; i += 8)
        if (get_be(v + i, 4) == LIB_KEY && get_be(v + i + 4, 4)) rids[k++] = get_be(v + i + 4, 4);
    if (k && H->play) H->play(rids, k, at, 0);
    free(rids);
}

/* PlayMediaLibraryCollection: 0 the collection's id, 1 its type (0 a playlist, 1 an artist, 2 an album, 3 an album
 * artist, 4 a genre, 5 a composer), 2 the index to start at. A playlist's songs in its order; the others' in the
 * library's order of an album: album artist, album, disc, track. */
void play_collection(const u8 *p, unsigned len) {
    unsigned n, tn, sn;
    const u8 *v = find(p, len, 0, &n), *ty = find(p, len, 1, &tn), *s = find(p, len, 2, &sn);
    const struct iap2_library *l = H->lib;
    if (!v || n != 8 || !ty || !tn || !l || !l->n) return;
    unsigned id = get_be(v + 4, 4), type = ty[0], at = s && sn == 4 ? get_be(s, 4) : 0, k = 0;
    if (type == 0) {
        for (unsigned j = 0; j < l->nlists; ++j)
            if (get_be(v, 4) == LIB_LIST && l->lists[j].id == id && H->play) {
                char t[96];
                snprintf(t, sizeof t, "library: list %u: %u songs, from %u", id, l->lists[j].n, at);
                H->log(t);
                H->play(l->lrids + l->lists[j].at, l->lists[j].n, at, 0);
            }
        return;
    }
    unsigned *rids = play_rids(l->n);
    for (unsigned i = 0; rids && i < l->n; ++i) {
        const struct iap2_track *t = &l->tracks[i];
        unsigned of = type == 1   ? t->artist_id
                      : type == 2 ? t->album_id
                      : type == 3 ? t->aartist_id
                      : type == 4 ? t->genre_id
                      : type == 5 ? t->composer_id
                                  : 0;
        if (type >= 1 && type <= 5 && of == id) rids[k++] = i; /* indexes first, sorted below */
    }
    /* insertion sort by album artist, album, disc, track: a collection is an album or a few hundred songs */
    for (unsigned i = 1; i < k; ++i) {
        unsigned x = rids[i], j = i;
        const struct iap2_track *a = &l->tracks[x];
        while (j) {
            const struct iap2_track *b = &l->tracks[rids[j - 1]];
            int c = strcmp(lib_str(b->aartist), lib_str(a->aartist));
            if (!c) c = strcmp(lib_str(b->album), lib_str(a->album));
            if (!c) c = (int)b->disc - (int)a->disc;
            if (!c) c = (int)b->track - (int)a->track;
            if (c <= 0) break;
            rids[j] = rids[j - 1], --j;
        }
        rids[j] = x;
    }
    for (unsigned i = 0; i < k; ++i) rids[i] = l->tracks[rids[i]].rid;
    char t[96];
    snprintf(t, sizeof t, "library: collection %08x of type %u: %u songs, from %u", id, type, k, at);
    H->log(t);
    if (k && H->play) H->play(rids, k, at, 0);
    free(rids);
}

/* PlayMediaLibrarySpecial: every song (1, all songs), shuffled as the car's shuffle-all is. */
void play_all(void) {
    const struct iap2_library *l = H->lib;
    unsigned *rids = l ? play_rids(l->n) : 0;
    for (unsigned i = 0; rids && i < l->n; ++i) rids[i] = l->tracks[i].rid;
    if (rids && H->play) H->play(rids, l->n, 0, 1);
    free(rids);
}

void lib_reset(void) { ls.phase = LIB_IDLE, ls.x_state = 0, ql_going = 0, ql_seen_at = 0; }
