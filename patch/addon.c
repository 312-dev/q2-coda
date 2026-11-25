/* Card apps: programs that run from the card beside Q2 Muse, installed by System
 * settings' Apps from their own GitHub releases (netupdate.c) or unzipped to the card by hand, as
 * Spotify's librespot is. Q2 Muse knows none of them by name: each is a card folder .<id> holding
 *   q2app    its manifest, key=value lines: name (its Streaming row's), search (the search box's
 *            hint), qr (1: its row turns it on and shows its QR code once it answers), scrobble
 *            (0: its songs stay out of the scrobble log, addon_scrobbles), stop (0: its page has no
 *            Stop row, and an error page's second row is Cancel) more (an op, then a space and
 *            a title: Now Playing's ⋯ opens its page there while its queue plays, addon_more),
 *            favdir (a card folder whose songs' hearts it is told of, addon_fav_path), lyrics
 *            (1: a song of its folder or its downloads folder, downloads=, has its lyrics asked of
 *            it, addon_lyrics_ask), covers (1: it is started for a cover the car asks it for,
 *            addon_cover_wake), resume (1: a stream of its songs restored after a restart is
 *            asked of it, addon_resume_ask) and hidden (1: no Streaming row, its page opened by
 *            id: addon_open_id, as AirPlay's is);
 *   run      its launcher, which copies the app to /tmp and runs it there in a restart loop, so
 *            the card is never kept busy, leaving the loop's pid in /tmp/q2-<id>;
 *   q2-<id>  the app;
 *   icon-40.png, icon.png
 *            its Streaming row's icon, 40 px (SET_ICON), drawn 1:1, else 52 px, scaled to fit;
 *            one over ART_MAX px either way counts as none; without them, the shared stream_app.
 * The firmware carries some apps too (tools/build.py BUNDLED_APPS), each in BUNDLED/<id> with the
 * same files and a version file, its launcher keeping the app's data in the card's .<id> (addon_dir
 * says which copy runs: docs/internals.md#bundled-apps).
 * Streaming lists those on the card and those carried (home.c ringnav_stream). A row starts its app and opens
 * addon_page, a list the app draws over Q2SPOT/1 (docs/librespot.md) on /tmp/q2-<id>.browse.sock:
 * each row's kind says what a press does (a local row plays a song or folder from the card,
 * library_lists.c's songs_open). Its status file /tmp/q2-<id>.state says whether to hold standby off
 * (hold=1: files moving, a phone using its page) and carries its web page's QR code, address and
 * PIN, which qr_open's page draws. A TERM to the loop's pid asks it to end. An app that plays as
 * well (q2-deezer's Stream mode) names a card folder of songs in /tmp/q2-<id>.play, which
 * stream_poll plays as it fills, or an .m3u of card paths, which it plays in order, waiting for
 * each, or its Q2URLS/1 queue of stream URLs, /tmp/q2-<id>.urls, which stock's Tidal player plays
 * (tidal.c app_urls_play), telling it in /tmp/q2-<id>.now which of them plays when the queue asks
 * (urls_now_poll). It asks for Update Local Music and its playlists' import in
 * /tmp/q2-<id>.library (addon_library.c), the result in .library.done. A list reply whose message
 * is "pages" and that has a cursor is followed by its next pages, appended to the same list
 * (page_next), up to ADDON_PAGES.
 *
 * Go to Album and Go to Artist on a song in an app's folder open the app's own page for them when
 * its q2app says goto=1 (addon_goto), and every song an app queues plays with its file's tags
 * (tags.c card_tags).
 *
 * An app's /tmp/q2-<id>.sleep asks for a sleep timer: playback pauses at a time, or as the track
 * playing ends (sleep_read, sleep_check).
 *
 * A card game (q2app game=1, then no run or Streaming row) is a row in Library's Games instead:
 * it runs over the whole screen, as Videos' player does, until it exits (addon_game_start). One
 * with audio=1 as well owns the sound: the player is stopped and the headphone output readied
 * before it starts, as for a video, it gets the output device (and a soft output's volume) as
 * its arguments, Play/Pause and the side buttons reach it, and the idle quit is its own.
 *
 * An app's first page may be sections (sec_take): one shows at a time, and a sideways swipe moves
 * to the next or the previous (swipe).
 *
 * Covers ("art" in the caps): a row's cover is a JPEG the app leaves at /tmp/q2-<id>.art/<key>.jpg,
 * key the row's URI, a tab and its context, hashed FNV-1a 32 (hash_bytes from FNV_SEED) and written
 * as 8 lowercase hex digits. A list with a cover for any of its rows is drawn as stock Tidal's
 * lists are (art_row): ART_ROW_H rows, the cover at the left, the title and subtitle beside it; a
 * row without one keeps the cover's room empty. The app sizes the covers: one over ART_MAX px
 * either way counts as none. The protocol is unchanged, so an app without covers, or firmware without "art", shows the plain rows.
 *
 * The card-app features this firmware has are words in /tmp/q2-addon.caps (ADDON_CAPS), written at
 * every Streaming scan. */
#include "offsets.inc"
#include "peq.h"
#include "playback.h"
#include "stock.h"

extern void ui_watch_step(const char *step); /* ui_watch.c: the step a long UI pass is in */

#define ADDON_POLL_MS 500
/* A level's drawing (artist_view): the rows as the app sent them, or an artist's page and the
 * pages its rows open, or an album's page (album_view) */
enum { LAY_PLAIN, LAY_ARTIST, LAY_ALBUMS, LAY_TOP, LAY_SIMILAR, LAY_ALBUM };
#define ADDON_START_MS 10000 /* no socket this long after the start: it isn't running */
#define ADDON_LIVE_MS 2000   /* a level whose reply says "refresh" is asked again this often */
#define ADDON_WAIT_MS 15000  /* a request's deadline, as Spotify's */
#define ADDON_DEPTH 6
#define ADDON_ROWS 50
#define PROGRESS_H 8            /* a progress row's bar: a flat strip under its text */
#define PROGRESS_TRACK 0x3a3a3a /* its unfilled part, dim against the black row */
/* ponytail: replies a level appends (page_next), 500 rows, under ringnav's MAX_ENTRIES (512), which
 * walks every row per wheel event; 2,000 needs MAX_ENTRIES 2048 after measuring, or a table_client
 * page. */
#define ADDON_PAGES 10
#define REPLY_MAX (64 << 10) /* Q2SPOT/1's limits */
#define REQ_MAX (8 << 10)
#define AWAY_MS 3000     /* USB storage waits at most this long for an app to let the card go */
#define SLOW_MS 3000     /* a page loading or starting this long offers to stop the app */
#define BUSY_MS 400      /* an action unanswered this long shows Working… on its row */
#define RESTART_MS 20000 /* Retry's restart starts the app by then, its old loop gone or not */
#define QUIT_MS 20000    /* the Apps page's worker waits at most this long for an app to end */
#define APP_ID 16        /* an id's bytes, its NUL included */
#define QR_RAW 1536      /* an app's status file's lines as kept (app_state) */
#define STATE_SCAN (16 << 10) /* and as far as it is read: a version-40 qr= (7,965 bytes) and more */
#define CARD "/mnt/mmc"
#define BUNDLED "/usr/lib/q2apps" /* the firmware's own copies (tools/build.py BUNDLED_DIR) */
#define TAGS CARD "/.q2/apps"      /* an App Store install's tag (netupdate.c app_file) */
#define CAPS_FILE "/tmp/q2-addon.caps"
/* This firmware's card-app features, space-separated; a feature adds its word. */
#define ADDON_CAPS                                                                                 \
    "playlist progress pages library goto sleep game audio sections art window urls sort field "   \
    "more fav now catalogue ratings likes artist album playlists storage signin libsource radio "  \
    "status holdmenu clip"
#define ADDON_SECTIONS 8  /* sections an app's first page may have */
#define SWIPE_CLAIM_PX 24 /* a sideways move this far, twice as far as up or down, is a swipe's */
#define SWIPE_PX 60       /* and this far at the lift, more than up or down, changes section */
#define ART_ROW_H 78 /* a row with a cover: stock Tidal's list_view default_item_height */
#define ART_PX 64    /* its cover, square, centred in the row's height */
#define ART_MAX 128  /* the most a cover's or an icon's file may be, either way; larger is none */
#define ART_KEEP 24  /* the rows' covers kept decoded around the list's scroll position */
#define ART_URL (APP_ID + 32) /* art_file's file:// URL, its NUL included */
#define QUEUE_KEEP 16  /* an app's .queue lines remembered as queued (queue_poll); it keeps 8 */
#define QUEUE_RAW 4096 /* and its .queue read this far: 8 lines of a seq, a word, a card path */

/* An app found on the card, kept for the life of demo in its slot. present: on the card at the
 * last scan; relaunch: stopped for USB storage, to start again; restart: stopped by Retry (its
 * socket gone), to start again once its loop is gone. */
typedef struct {
    char id[APP_ID], name[32], tips[32], bin[48], run[48], pid[32], state[40], browse[48], icon[56];
    char more[48]; /* q2app's more: an op, a space and a title (addon_more) */
    int qr, game, audio, hidden, present, launched, hold, relaunch, restart;
    int nostop; /* q2app's stop=0: no Stop row on its page */
    int radio;  /* q2app's radio=1: it plays a song's radio (addon_radio_start) */
    int lyrics; /* q2app's lyrics=1: it fetches a song's lyrics (addon_lyrics_ask) */
    int covers; /* q2app's covers=1: it fetches a cover the car asks for (addon_cover_wake) */
    int resume; /* q2app's resume=1: it resumes a restored stream (addon_resume_ask) */
    char dl[32]; /* q2app's downloads=: the card folder its synced songs are in, "" for none */
    int sync, sync_pm; /* its state file's sync=: addon_sync's kind, and a download's permille */
    unsigned polled, launch_at, restart_at, play; /* play: its .play as read last, hashed */
    int reach;       /* its state file's reach=down: its server out of reach (addon_offline) */
    char srverr[24]; /* its srverr= as read last: the server failure toasted last (reach_read) */
    int offline;     /* q2app's offline=1: it syncs a Library item offline (addon_offline_ask) */
    unsigned queue;  /* its .queue as read last, hashed (queue_poll) */
    unsigned taken[QUEUE_KEEP], next_taken; /* the .queue lines' seqs queued, hashed, a ring */
    int queue_seen;                         /* its .queue looked at once since demo started */
} app_t;

static struct {
    app_t a[APPS_MAX];
    int n, shown, order[APPS_MAX]; /* order: the slots on the card, by name */
    int games, game[APPS_MAX];     /* the card games among them, by name too */
} ap __attribute__((section(".scratch")));

/* An app's state file into raw as state_read lays it out (a newline first, NUL-terminated) and its
 * kept bytes' count, but line by line: a line longer than the room left in raw, a sign-in's qr=
 * (up to 7,965 bytes of hex, more than QR_RAW), is left out whole and the lines after it are
 * read, so a hold=, sync=, reach= or an ack written after the code is not lost. Read no further
 * than STATE_SCAN bytes of the file. The QR page reads the file whole itself (qr_refresh). */
static unsigned app_state(const char *path, char *raw, unsigned size) {
    void *f = fopen(path, "rb");
    unsigned n = 1, start = 1, seen = 0, got;
    int skip = 0;
    char chunk[256];
    raw[0] = '\n';
    while (f && seen < STATE_SCAN && (got = fread(chunk, 1, sizeof chunk, f)) > 0) {
        seen += got;
        for (unsigned k = 0; k < got; ++k) {
            char c = chunk[k];
            if (skip) { /* the rest of a line too long to keep */
                skip = c != '\n';
            } else if (n < size - 1) {
                raw[n++] = c;
                if (c == '\n') start = n;
            } else { /* no room for the line: take it back, and pass over what is left of it */
                n = start;
                skip = c != '\n';
            }
        }
    }
    if (f) fclose(f);
    raw[n] = 0;
    return n - 1;
}

/* An id goes into shell commands: lowercase letters, digits and '-' only. */
static int valid_id(const char *s) {
    unsigned k = 0;
    for (; s[k]; ++k)
        if (k + 1 >= APP_ID ||
            !((s[k] >= 'a' && s[k] <= 'z') || (s[k] >= '0' && s[k] <= '9') || s[k] == '-'))
            return 0;
    return k > 0;
}

/* id's slot, or 0 when no scan has found it an app. */
static app_t *known(const char *id) {
    for (int i = 0; i < ap.n; ++i)
        if (!strcmp(ap.a[i].id, id)) return &ap.a[i];
    return 0;
}
/* id's slot, made when it has none: only for a folder whose q2app was read (scan_one), so a
 * hidden folder a song's path names (.tidal, the user's own) takes none of the APPS_MAX. */
static app_t *slot(const char *id) {
    app_t *a = known(id);
    if (a) return a;
    if (ap.n == APPS_MAX) return 0;
    a = &ap.a[ap.n++];
    tk_snprintf(a->id, sizeof a->id, "%s", id);
    tk_snprintf(a->bin, sizeof a->bin, CARD "/.%s/q2-%s", id, id);
    tk_snprintf(a->run, sizeof a->run, "/bin/sh " CARD "/.%s/run", id);
    tk_snprintf(a->pid, sizeof a->pid, "/tmp/q2-%s", id);
    tk_snprintf(a->state, sizeof a->state, "/tmp/q2-%s.state", id);
    tk_snprintf(a->browse, sizeof a->browse, "/tmp/q2-%s.browse.sock", id);
    return a;
}

/* Where a version's numbers start: after its first 'v' followed by a digit ("q2-v0.9"), else at its
 * first digit ("1.10.2"). */
static const char *version_at(const char *s) {
    for (const char *p = s; *p; ++p)
        if (*p == 'v' && p[1] >= '0' && p[1] <= '9') return p + 1;
    while (*s && (*s < '0' || *s > '9')) ++s;
    return s;
}
/* A version's order against another's: "q2-v0.9", "v1.10" or "1.10.2", their dotted numbers
 * (version_at) compared in turn, a missing one as 0; anything after them is ignored. */
int addon_version_cmp(const char *x, const char *y) {
    x = version_at(x), y = version_at(y);
    while ((*x >= '0' && *x <= '9') || (*y >= '0' && *y <= '9')) {
        unsigned a = 0, b = 0;
        for (; *x >= '0' && *x <= '9'; ++x) a = a * 10 + (unsigned)(*x - '0');
        for (; *y >= '0' && *y <= '9'; ++y) b = b * 10 + (unsigned)(*y - '0');
        if (a != b) return a < b ? -1 : 1;
        x += *x == '.' && x[1] >= '0' && x[1] <= '9';
        y += *y == '.' && y[1] >= '0' && y[1] <= '9';
    }
    return 0;
}

/* Whether the firmware carries app id as its own alone (tools/build.py BUNDLED_APPS' builtin, its
 * BUNDLED/<id>/builtin file, which holds "1"): only its copy runs, and the App Store never lists it
 * (netupdate.c shelf_sources). A new version comes with a firmware update. */
int addon_builtin(const char *id) {
    char path[64], raw[8];
    tk_snprintf(path, sizeof path, BUNDLED "/%s/builtin", id);
    return state_read(path, raw, sizeof raw) && raw[1] == '1';
}

/* The folder of app id that runs, into dir (n bytes): the card's CARD/.<id> or the firmware's
 * BUNDLED/<id>; 1 for the firmware's. A builtin app (addon_builtin) runs the firmware's whatever the
 * card holds. Otherwise the card's runs when it holds the app (its q2-<id>) and either
 * the firmware carries none or the card's is newer: its App Store tag (TAGS/<id>.tag) against the
 * carried version file, by addon_version_cmp. A card copy with no tag was unzipped by hand, and
 * counts as newer: whoever put it there chose it. Anything else runs the firmware's, when it has
 * one; with neither, the card's folder (where an install would go). */
int addon_dir(const char *id, char *dir, unsigned n) {
    char path[64], card[24], mine[24];
    tk_snprintf(path, sizeof path, BUNDLED "/%s/q2-%s", id, id);
    int carried = !access(path, 0);
    tk_snprintf(path, sizeof path, CARD "/.%s/q2-%s", id, id);
    int on_card = !access(path, 0);
    if (carried && on_card && !addon_builtin(id)) {
        tk_snprintf(path, sizeof path, TAGS "/%s.tag", id);
        if (state_read(path, card, sizeof card)) {
            tk_snprintf(path, sizeof path, BUNDLED "/%s/version", id);
            carried = state_read(path, mine, sizeof mine) && addon_version_cmp(card, mine) <= 0;
        } else
            carried = 0;
    }
    if (carried) tk_snprintf(dir, n, BUNDLED "/%s", id);
    else tk_snprintf(dir, n, CARD "/.%s", id);
    return carried;
}
/* The q2app of app id that runs (addon_dir's folder), into path (n bytes). */
void addon_q2app(const char *id, char *path, unsigned n) {
    char dir[48];
    addon_dir(id, dir, sizeof dir);
    tk_snprintf(path, n, "%s/q2app", dir);
}

/* Each card app's folder, /mnt/mmc/.<id> with a valid id, and the q2app of the copy of it that
 * runs (addon_q2app) read into raw (512 bytes): each(id, raw, ctx) for each one that reads, until
 * it answers nonzero, which this answers then; 0 after the last. The one scan of the card's
 * manifests: Streaming's (addon_scan), the server library's (catalogue.c cat_scan), Storage's
 * (storage.c st_app) and a favourite's app (addon_fav_path). */
int addon_q2apps(int (*each)(const char *id, const char *raw, void *ctx), void *ctx) {
    char raw[512], path[64];
    int stop = 0;
    void *d = opendir(CARD);
    for (struct dirent *e; d && !stop && (e = readdir(d));) {
        if (e->d_name[0] != '.' || !valid_id(e->d_name + 1)) continue;
        addon_q2app(e->d_name + 1, path, sizeof path);
        if (state_read(path, raw, sizeof raw)) stop = each(e->d_name + 1, raw, ctx);
    }
    if (d) closedir(d);
    return stop;
}

/* One app's folder at the scan: read when it is the copy of its app that runs (addon_dir). */
static void scan_one(const char *id, const char *from, const char *raw) {
    char dir[48], path[64];
    app_t *a;
    addon_dir(id, dir, sizeof dir);
    if (strcmp(dir, from) || !(a = slot(id))) return;
    tk_snprintf(a->bin, sizeof a->bin, "%s/q2-%s", dir, id);
    tk_snprintf(a->run, sizeof a->run, "/bin/sh %s/run", dir);
    field(raw, "name", a->name, sizeof a->name);
    if (!a->name[0]) tk_snprintf(a->name, sizeof a->name, "%s", a->id);
    field(raw, "search", a->tips, sizeof a->tips);
    if (!a->tips[0]) tk_snprintf(a->tips, sizeof a->tips, "Search");
    a->qr = number(raw, "qr") == 1;
    a->game = number(raw, "game") == 1;
    a->audio = number(raw, "audio") == 1;
    a->hidden = number(raw, "hidden") == 1;
    char stop[4]; /* as text, as scrobble: number() is 0 for a missing key too */
    field(raw, "stop", stop, sizeof stop);
    a->nostop = !strcmp(stop, "0") || !strcmp(stop, "0\r");
    a->radio = number(raw, "radio") == 1;
    a->lyrics = number(raw, "lyrics") == 1;
    a->covers = number(raw, "covers") == 1;
    a->offline = number(raw, "offline") == 1;
    a->resume = number(raw, "resume") == 1;
    field(raw, "downloads", a->dl, sizeof a->dl);
    for (char *c = a->dl; *c; ++c) /* a Windows editor's line end; one folder, no path */
        if (*c == '\r' || *c == '/') *c = 0;
    if (!strcmp(a->dl, ".") || !strcmp(a->dl, "..")) a->dl[0] = 0;
    field(raw, "more", a->more, sizeof a->more);
    unsigned k = strlen(a->more);
    if (k && a->more[k - 1] == '\r') a->more[k - 1] = 0; /* a Windows editor's line end */
    a->present = !access(a->bin, 0);
    /* the row's size first, the other scaled to fit (navigation.c set_child), else stream_app
     * (one over ART_MAX px either way, or whose size can't be read, counts as none: demo
     * decodes it whole) */
    static const char *const icons[2] = { "icon-40.png", "icon.png" };
    tk_snprintf(a->icon, sizeof a->icon, "stream_app");
    for (int i = 1; i >= 0; --i) {
        int w = 0, h = 0;
        tk_snprintf(path, sizeof path, "%s/%s", dir, icons[i]);
        if (picture_size(path, &w, &h) && w && h && w <= ART_MAX && h <= ART_MAX)
            tk_snprintf(a->icon, sizeof a->icon, "file://%s", path);
    }
}

/* A builtin app's App Store tag and file list (netupdate.c app_file), from an install before the
 * firmware carried it, deleted: the App Store no longer has it. The card copy those name is left
 * as it is, never run (addon_dir): its folder is the app's data folder too, and a firmware
 * without the app still finds a working copy there. */
static void builtin_untag(const char *id) {
    static const char *const ext[2] = { ".tag", ".files" };
    char path[64];
    for (int i = 0; i < 2; ++i) {
        tk_snprintf(path, sizeof path, TAGS "/%s%s", id, ext[i]);
        if (!access(path, 0)) unlink(path);
    }
}

/* A card folder's app, for addon_scan: read when the card's copy is the one that runs. */
static int scan_card(const char *id, const char *raw, void *unused) {
    char dir[48];
    tk_snprintf(dir, sizeof dir, CARD "/.%s", id);
    scan_one(id, dir, raw);
    return 0;
}

/* Streaming's page (navigation.c): the apps on the card and those the firmware carries found again,
 * their names read; how many there are, by name for addon_name. A slot keeps its app (and its
 * running state) from scan to scan. */
int addon_scan(void) {
    char dir[48], raw[512], path[64];
    for (int i = 0; i < ap.n; ++i) ap.a[i].present = 0;
    addon_q2apps(scan_card, 0);
    void *d = opendir(BUNDLED);
    for (struct dirent *e; d && (e = readdir(d));) {
        if (!valid_id(e->d_name)) continue;
        tk_snprintf(dir, sizeof dir, BUNDLED "/%s", e->d_name);
        tk_snprintf(path, sizeof path, "%s/q2app", dir);
        if (state_read(path, raw, sizeof raw)) scan_one(e->d_name, dir, raw);
        if (addon_builtin(e->d_name)) builtin_untag(e->d_name);
    }
    if (d) closedir(d);
    ap.shown = ap.games = 0;
    for (int i = 0; i < ap.n; ++i) { /* by name: an insertion, eight at most */
        if (!ap.a[i].present || ap.a[i].hidden) continue;
        int *order = ap.a[i].game ? ap.game : ap.order, k = ap.a[i].game ? ap.games++ : ap.shown++;
        for (; k && strcasecmp(ap.a[order[k - 1]].name, ap.a[i].name) > 0; --k)
            order[k] = order[k - 1];
        order[k] = i;
    }
    /* libsource's state first: an app reading the caps finds it there (library_settings.c) */
    library_source_file();
    char caps[sizeof ADDON_CAPS + 16]; /* Download Ahead's words (stream_state.c ahead_caps) */
    int n = tk_snprintf(caps, sizeof caps, "%s%s\n", ADDON_CAPS, ahead_caps());
    blob_io(CAPS_FILE, CAPS_FILE ".tmp", caps, (unsigned)n, 1);
    return ap.shown;
}

const char *addon_name(int k) { return k >= 0 && k < ap.shown ? ap.a[ap.order[k]].name : 0; }
const char *addon_id(int k) { return k >= 0 && k < ap.shown ? ap.a[ap.order[k]].id : 0; }
const char *addon_icon(int k) { return k >= 0 && k < ap.shown ? ap.a[ap.order[k]].icon : 0; }

/* Whether an image's name is a card app's Streaming icon, file:///mnt/mmc/.<id>/icon*.png or a
 * carried app's, file:///usr/lib/q2apps/<id>/icon*.png (navigation.c ringnav_image_add: an
 * icons.addon_mono theme greys it as Streaming's own icons). */
int addon_is_icon(const char *name) {
    static const char *const ends[] = { "/icon.png", "/icon-40.png" };
    unsigned n = name ? strlen(name) : 0;
    if (!n || !(tk_str_start_with(name, "file://" CARD "/.") ||
                tk_str_start_with(name, "file://" BUNDLED "/")))
        return 0;
    for (unsigned i = 0; i < 2; ++i) {
        unsigned k = strlen(ends[i]);
        if (n > k && !strcmp(name + n - k, ends[i])) return 1;
    }
    return 0;
}

/* A pid file whose loop is gone (crashed, or killed) is removed, so no wait spends its limit on it.
 * ponytail: trusts the pid in the file; a loop that died without removing it, its pid since reused,
 * would TERM that process. A flock in run would close this. */
static void stop_cmd(const char *pid, char *cmd, unsigned n) {
    snprintf(cmd, n, "kill $(cat %s) 2>/dev/null || rm -f %s", pid, pid);
}

/* A launch under way: started, its launcher not yet at its pid file (it copies the app to /tmp
 * first), and ADDON_START_MS not past, when poll_one ends it. A second launch then would find no
 * app running too, and both copies would run (docs/internals.md#card-apps). ponytail: a wake that
 * finds a running app (addon_wake) restarts the count, so one that exits within ADDON_START_MS of
 * that waits out the rest before a wake starts it again; a flag set by launch alone would close it. */
static int starting(const app_t *a) {
    return a->launched && access(a->pid, 0) && time_now_ms() - a->launch_at < ADDON_START_MS;
}

/* The app's run script started in the background: system() waits only for its shell, not for the
 * script, which exits at once when the app runs already and otherwise copies itself and the app to
 * /tmp and forks its loop (about 210 ms on the emulator, which the UI pass waited for; 65 ms with
 * the "&", the shell's own start under qemu). Its loop is reparented when the shell ends, so no
 * child is left for demo to reap. */
static void launch(app_t *a) {
    const char *busy = shelf_busy();
    if (busy && !strcmp(busy + 1, a->id)) return; /* the Apps page is replacing its files */
    a->launched = 1;
    a->launch_at = time_now_ms();
    char cmd[sizeof a->run + 2];
    tk_snprintf(cmd, sizeof cmd, "%s &", a->run);
    system(cmd);
}

/* The server library's play hand-off (catalogue.c catalogue_load, docs/server-library.md): app id
 * started in the background when it isn't running, as its Streaming row starts it, so a .catplay
 * pressed on the Library is answered without the app opened since boot, or after its own idle
 * exit. The capability line is written first (addon_scan, as Streaming's page does, once the
 * app is found): the app reads it once, at its start. 0: it runs, and reads a catplay now; 1: started, and reads one once
 * its socket is there (addon_ready: it reads the catplay file at its start, as one answered, and
 * makes its socket after that); -1: it can't start (not on the card, or the Apps page replacing
 * its files). A socket a killed app left is removed first, so it is not taken for the new one's. */
int addon_wake(const char *id) {
    app_t *a = valid_id(id) ? known(id) : 0;
    if (valid_id(id) && (!a || !a->present)) addon_scan(), a = known(id);
    if (!a || !a->present) return -1;
    if (!access(a->pid, 0)) {
        if (!a->launched) a->launched = 1, a->launch_at = time_now_ms(); /* its .play polled */
        return 0;
    }
    if (starting(a)) return 1; /* boot's resume and the catalogue's autostart, one poll apart */
    unlink(a->browse);
    launch(a);
    return a->launched ? 1 : -1;
}
int addon_ready(const char *id) {
    app_t *a = valid_id(id) ? known(id) : 0;
    return a && !access(a->browse, 0);
}

/* Stream mode (q2-deezer's stream.go): an app's /tmp/q2-<id>.play holds "<seq> <folder>", a hidden
 * card folder it fills with a list's songs, named in play order ("01 - Title [id].flac"), as they
 * download. A new one is played from its first song once one is there (the folder's songs in name
 * order, as a local row's album is: songs_open), Now Playing opening as for a press. While the
 * queue is still that folder's, songs that come later (named after the last one queued) join its
 * end, as Add to Queue adds them (playback_queue_insert); the path of the one playing is left in
 * /tmp/q2-<id>.playing, so the app fetches only a few ahead and deletes the ones long played. Or
 * .play names an .m3u on the card (q2-subsonic's queue), one card path a line: its songs are queued
 * in its order, each waited for until it is there (m3u_songs), or with "grow" until enough of it
 * is (stream_startable); and while it plays the app is told the songs after the one playing in
 * /tmp/q2-<id>.ahead (stream_state.c, docs/server-library.md#download-ahead-and-the-streaming-state). */
#define M3U_MAX (256 << 10) /* a playlist file read this far: about 2,000 card paths */
#define M3U_BATCH 32        /* folders listed a poll at most, so a long list doesn't stall the UI */
static struct {
    app_t *app;        /* whose .play it is */
    char dir[160];     /* its folder */
    char last[192];    /* the name of the last of its songs queued */
    char playing[512]; /* the .playing path written last */
    int state;       /* S_WAIT for its first song, S_PLAY its queue playing, S_DONE another queue */
    unsigned timer;  /* the first song's start, on a timer (a window opens) */
    long seen;       /* the folder's mtime at the last scan that came well after it */
    int m3u;         /* sm.dir is a playlist file */
    int urls;        /* sm.dir is the app's URL queue (tidal.c app_urls_play) */
    int next;        /* its entries passed so far */
    int more;        /* a batch filled, so the next poll goes on */
    char first[512]; /* the session's first song */
    char want[512];  /* the entry waited for */
    unsigned loads;  /* playback_loads at its start, then once its queue was loaded */
    unsigned picks;  /* playback_picks at its start: a song the user moved to since ends its wait */
    char seq[24];    /* its .play's seq, as far as the first space (now_start) */
    unsigned skip;   /* playback_picks after a Next that waits for sm.want (addon_next_waits) */
    int keep;        /* its first entry is the song playing, which plays on (song_radio_keeps) */
    unsigned skip_at; /* ... the queue's place that song joins at, which then plays */
    int jump;         /* it is an Up Next pending row's: .playing names it (addon_pending_play) */
    int hold;   /* a resumed stream's song, paused: .playing waits for Play (addon_resume_ask) */
    int resume; /* its .play answers the resume request (rs.seq): its first song is kept */
} sm __attribute__((section(".scratch")));
enum { S_WAIT, S_PLAY, S_DONE };
/* The app whose .play started the queue playing, and playback_loads after that queue's load: the
 * queue is still the app's while no other has loaded since (addon_more). */
static struct {
    app_t *app;
    unsigned loads;
} qa;

/* ---- A streamed queue resumed after a restart (playback.c ringnav_memory,
 * docs/internals.md#memory-playback-and-a-streamed-queue). Memory Playback restores only the songs
 * whose files are on the card; a stream's others, not downloaded yet, are asked of their app (q2app
 * resume=1), which plays the queue on from the song restored: /tmp/q2-<id>.resume, written whole
 * once the app's socket is there (it takes a request there at its start as answered), "<seq>
 * <index> <seconds> paused|playing" and then every saved song's path a line, the queue's order,
 * index counting them from 0. Its answer is resumeack=<seq> <code> in its state file (0 resumed, 1
 * not its songs, 2 can't) and a .play named by that seq whose queue file starts with the song
 * restored, which is kept as a radio's seed is (no load, so paused stays paused). Meanwhile Up Next
 * lists the saved songs not restored as pending rows. Declined, unanswered, or another queue
 * played: the songs on the card are the queue, as before, and the log says why. */
#define RESUME_START_MS 30000 /* the app listening this long after the restore, else no resume */
#define RESUME_WAIT_MS 30000  /* its queue this long after the request, else no resume */
enum { R_NONE, R_ASK, R_SENT };
static struct {
    app_t *app;
    char *req;  /* the request, on the heap until it is written */
    char *rows; /* the saved songs after the current one not restored, each with its NUL */
    unsigned nrows, size; /* their count; the request's bytes */
    unsigned seq, at, loads; /* the request's seq; the restore's or request's time; its queue's */
    int state, woke;
    unsigned dead; /* a resume let go: its seq, whose .play never plays */
} rs __attribute__((section(".scratch")));
extern void ulog(char level, const char *src, const char *msg); /* ulog.c */
static int card_id(const char *path, char *id);

static void resume_end(const char *why) {
    char line[160];
    if (rs.state && why) {
        tk_snprintf(line, sizeof line, "%s: %s; the restored songs on the card play as they are",
                    rs.app->name, why);
        ulog('W', "resume", line);
        rs.dead = rs.seq;
    } else if (rs.state) {
        tk_snprintf(line, sizeof line, "%s resumed the stream, %u songs to come", rs.app->name,
                    rs.nrows);
        ulog('I', "resume", line);
    }
    free(rs.req), free(rs.rows);
    rs.req = rs.rows = 0, rs.nrows = rs.size = 0, rs.state = R_NONE;
}

/* A restored queue (ringnav_memory): paths, n of them, the saved queue's songs in order and then
 * the stream's songs not yet queued; in[i] whether path i was restored; at the index of the song
 * the restore made current, sec its seconds, playing whether it plays (car mode) or waits paused.
 * Its app is asked when one with resume=1 owns a saved song and a song after the current one wasn't
 * restored. 1 when asked (seq names the answer). */
int addon_resume_ask(const char *const *paths, const unsigned char *in, unsigned n, unsigned at,
                     int sec, int playing, unsigned seq) {
    app_t *a = 0;
    unsigned bytes = 64, rows = 0, missing = 0;
    int scanned = 0;
    resume_end("another restore");
    for (unsigned i = 0; i < n; ++i) {
        char id[APP_ID];
        if (strstr(paths[i], "\n") || strstr(paths[i], "\r")) return 0; /* a line each */
        unsigned k = strlen(paths[i]) + 1;
        bytes += k;
        if (i > at && !in[i]) ++missing, rows += k;
        app_t *b = a || !card_id(paths[i], id) ? 0 : known(id);
        if (!a && (!b || !b->present) && !scanned && card_id(paths[i], id))
            scanned = 1, addon_scan(), b = known(id);
        if (b && b->present && b->resume) a = b;
    }
    if (!a || !missing || at >= n) return 0;
    if (!(rs.req = calloc(1, bytes)) || !(rs.rows = calloc(1, rows))) {
        free(rs.req);
        rs.req = 0;
        return 0;
    }
    unsigned k = (unsigned)tk_snprintf(rs.req, 64, "%u %u %d %s\n", seq, at, sec,
                                       playing ? "playing" : "paused"),
             r = 0;
    for (unsigned i = 0; i < n; ++i) {
        unsigned len = strlen(paths[i]);
        memcpy(rs.req + k, paths[i], len);
        k += len, rs.req[k++] = '\n';
        if (i > at && !in[i]) memcpy(rs.rows + r, paths[i], len + 1), r += len + 1;
    }
    rs.app = a, rs.size = k, rs.nrows = missing, rs.seq = seq, rs.at = time_now_ms();
    rs.loads = playback_loads, rs.state = R_ASK, rs.woke = 0;
    char line[96];
    tk_snprintf(line, sizeof line, "asking %s to resume %u songs, %u not on the card", a->name, n,
                missing);
    ulog('I', "resume", line);
    return 1;
}
/* The restored queue loaded again by Now Playing's start (playback.c ringnav_load): still it. */
void addon_resume_follow(void) {
    if (rs.state) rs.loads = playback_loads;
}
/* addon_poll's: the app started, the request written once it listens, its answer read. */
static void resume_poll(void) {
    unsigned now = time_now_ms();
    if (!rs.state) return;
    if (playback_loads != rs.loads) {
        resume_end("another queue played");
        return;
    }
    if (rs.state == R_ASK) {
        if (!rs.woke && (rs.woke = 1) && addon_wake(rs.app->id) < 0) {
            resume_end("the app can't start");
            return;
        }
        if (addon_ready(rs.app->id)) {
            char path[48];
            tk_snprintf(path, sizeof path, "/tmp/q2-%s.resume", rs.app->id);
            if (!blob_io(path, "/tmp/q2-resume.tmp", rs.req, rs.size, 1)) {
                resume_end("the request couldn't be written");
                return;
            }
            free(rs.req);
            rs.req = 0, rs.state = R_SENT, rs.at = now;
        } else if (now - rs.at >= RESUME_START_MS)
            resume_end("the app didn't start");
        return;
    }
    char raw[QR_RAW], v[24], want[16], why[40];
    unsigned k = (unsigned)tk_snprintf(want, sizeof want, "%u ", rs.seq);
    if (app_state(rs.app->state, raw, sizeof raw)) {
        field(raw, "resumeack", v, sizeof v);
        if (!strncmp(v, want, k) && v[k] >= '1' && v[k] <= '9') {
            char code[2] = { v[k], 0 };
            tk_snprintf(why, sizeof why, "the app declined (%s)", code);
            resume_end(why);
            return;
        }
    }
    if (now - rs.at >= RESUME_WAIT_MS) resume_end("no queue from the app");
}
/* Whether the session's .play is named seq (sm.seq, as the app wrote it). */
static int seq_named(unsigned seq) {
    char s[16];
    tk_snprintf(s, sizeof s, "%u", seq);
    return seq && !strcmp(s, sm.seq);
}
/* m3u_songs', the resume's queue file's first song, first: whether it is the song restored, which
 * then plays on as it is (paused, at its place), the songs after it the app's. */
static int resume_keeps(const char *first) {
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned pos = (unsigned)I(MCL_POS, 0);
    if (!sm.resume || rs.state != R_SENT || !seq_named(rs.seq) || playback_loads != rs.loads ||
        !q || pos >= deque_size(q) || !catalogue_same_song(first, P(deque_at(q, pos), REC_PATH)))
        return 0;
    resume_end(0);
    sm.hold = 1;
    return 1;
}

/* Whether path changed since the last look that came well after its change.
 * ponytail: only once it changes (o32 stat, st_mtime word 16), not a scan every poll; a scan in the
 * second of a change is not trusted, as a file could land later in that second. */
static int changed(const char *path) {
    unsigned st[36];
    if (__xstat(3, path, st)) return 0;
    if ((long)st[16] == sm.seen) return 0;
    if ((long)st[16] < time((void *)0) - 1) sm.seen = (long)st[16];
    return 1;
}

/* A file in dir named after last ("": any), not a hidden one or a download's .part or .tmp; whether
 * it is a song, stock's listing says (app_album_songs). */
static int stream_newer(const char *dir, const char *last) {
    if (!changed(dir)) return 0;
    void *d = opendir(dir);
    int found = 0;
    for (struct dirent *e; d && !found && (e = readdir(d));) {
        const char *n = e->d_name, *dot = strrchr(n, '.');
        found = n[0] != '.' && !(dot && (!strcasecmp(dot, ".part") || !strcasecmp(dot, ".tmp"))) &&
                strcmp(n, last) > 0;
    }
    if (d) closedir(d);
    return found;
}

/* Whether path has a ".." segment: all of it, or one between slashes or at either end. A name with
 * an ellipsis ("Hello... (Live).flac") has none. */
static int dotdot(const char *path) {
    for (const char *s = path;; ++s) {
        if (s[0] == '.' && s[1] == '.' && (!s[2] || s[2] == '/')) return 1;
        if (!(s = strstr(s, "/"))) return 0;
    }
}
/* A path an app or its server names: on the card, with no ".." segment. */
int card_path(const char *path) { return tk_str_start_with(path, CARD "/") && !dotdot(path); }

/* The playlist's songs from entry sm.next on, in its order, a deque the caller destroys: up to the
 * first one not there yet (sm.want), its folders' listings M3U_BATCH at most (each song's folder
 * is listed unless the song before it was in the same one). An entry off the card, or there but
 * not a song in its folder's listing, is passed over, as is one too long to be a path here; one
 * missing from a listing taken for an earlier entry may have landed since, so the next poll lists
 * its folder again before it is passed over. Entries are counted, so the app may rewrite the lines
 * from sm.next on (the one waited for, say). A file longer than M3U_MAX is read that far, its cut
 * last line dropped, so it is never waited for.
 * ponytail: the file is read whole each time it changes or a waited song lands; fine to M3U_MAX. */
/* A stream's first start, timed (stream_start's log line): the ms its m3u_songs spent listing
 * folders and reading tags, counted by m3u_songs whenever it runs and zeroed by stream_start. */
static struct {
    unsigned list, tags;
} sst __attribute__((section(".scratch")));

static void *m3u_songs(void) {
    void *out = _create_deque("stSongInfo"), *songs = 0;
    /* state_read's leading newline and nul, and a byte past M3U_MAX that says the file goes on */
    char *raw = calloc(1, M3U_MAX + 3), dir[sizeof sm.want] = "", *line, *end;
    deque_init(out);
    if (raw && state_read(sm.dir, raw, M3U_MAX + 3) > M3U_MAX) strrchr(raw, '\n')[1] = 0;
    sm.want[0] = 0, sm.more = 0; /* at its end, unless a missing song or a full batch stops it */
    int entry = 0, lists = 0;
    for (line = raw ? raw + 1 : 0; line && *line; line = end) {
        if ((end = strstr(line, "\n"))) *end++ = 0;
        if (tk_str_start_with(line, "\xEF\xBB\xBF")) line += 3; /* a BOM */
        unsigned k = strlen(line);
        if (k && line[k - 1] == '\r') line[--k] = 0;
        if (!k || line[0] == '#' || entry++ < sm.next) continue;
        if (!sm.next && sm.state == S_WAIT && (song_radio_keeps(line) || resume_keeps(line))) {
            /* plays now, or a resumed stream's song restored: passed over */
            sm.keep = 1, ++sm.next;
            continue;
        }
        if (!card_path(line) || k >= sizeof sm.want) {
            ++sm.next;
            continue;
        }
        if (access(line, 0) || !stream_startable(sm.app ? sm.app->id : "", line)) {
            tk_snprintf(sm.want, sizeof sm.want, "%s", line); /* not there, or arriving (grow) */
            break;
        }
        char *slash = strrchr(line, '/');
        *slash = 0;
        int fresh = !songs || strcmp(line, dir); /* its folder listed after its access() */
        if (fresh) {
            if (lists++ == M3U_BATCH) { /* this entry first on the next poll */
                sm.more = 1;
                break;
            }
            if (songs) deque_destroy(songs);
            tk_snprintf(dir, sizeof dir, "%s", line);
            ui_watch_step("stream list");
            unsigned t = (unsigned)time_now_ms();
            songs = app_album_songs(dir);
            sst.list += (unsigned)time_now_ms() - t;
        }
        *slash = '/';
        int found = 0;
        for (unsigned i = 0, n = deque_size(songs); !found && i < n; ++i) {
            void *r = deque_at(songs, i);
            const char *p = P(r, REC_PATH);
            if ((found = p && !strcmp(p, line))) {
                ui_watch_step("stream tags");
                unsigned t = (unsigned)time_now_ms();
                card_record_tags(r); /* named by its tags (tags.c) */
                sst.tags += (unsigned)time_now_ms() - t;
                _deque_push_back(out, r);
            }
        }
        if (!found && !fresh) { /* landed since the listing an earlier entry took: listed again */
            sm.more = 1;
            break;
        }
        ++sm.next;
    }
    free(raw);
    if (songs) deque_destroy(songs);
    return out;
}

/* m3u mode's wake: a batch to go on with, the file rewritten, or the song waited for there. */
static int m3u_ready(void) {
    return sm.more || changed(sm.dir) ||
           (sm.want[0] && !access(sm.want, 0) && stream_startable(sm.app ? sm.app->id : "", sm.want));
}

/* A scan running, or its window still open: the user's own Update Local Music page
 * (updatemusic_page), whose OK starts one, or its dialog (updatemusic_dialog), which a library
 * request opens without the page and which stays up after the scan is done. */
static int scanning(void) {
    return library_scanning() || navigator_window_is_exist("updatemusic_page") ||
           navigator_window_is_exist("updatemusic_dialog");
}

/* Whether the user left a session still waiting for its first song: a playlist's by playing another
 * queue, any by moving to another song of the queue playing (Next or Previous on the one before:
 * the device, 2026-11-17, Shuffle's first song landed half way through the song Next had played,
 * and played over it). The song waited for is left on the card. */
static int moved_on(void) {
    return (sm.m3u && playback_loads != sm.loads) || playback_picks != sm.picks;
}

static int stream_start(const void *unused) {
    (void)unused;
    sm.timer = 0;
    if (moved_on()) sm.state = S_DONE; /* the user played a queue, or another song, since */
    if (sm.state != S_WAIT) return 0;
    if (scanning()) { /* no listing during a scan, no play over its dialog: looked at again after */
        sm.seen = 0;
        return 0;
    }
    unsigned t0 = (unsigned)time_now_ms(), play_ms = 0;
    sst.list = sst.tags = 0;
    ui_watch_step("stream songs");
    void *songs = sm.m3u ? m3u_songs() : app_album_songs(sm.dir);
    unsigned n = deque_size(songs), songs_ms = (unsigned)time_now_ms() - t0;
    if (sm.resume && !sm.keep) { /* a resume's queue not from the song restored: never played */
        sm.state = S_DONE;
        resume_end("its queue starts at another song");
    } else if (sm.keep) { /* an app's radio of the song playing (or a resume's song restored): it
                           * plays on, the rows after it replaced */
        void *q = P(mcl_pdeqplaylist, 0);
        sm.state = S_DONE;
        if (playback_queue_rest(songs)) {
            tk_snprintf(sm.first, sizeof sm.first, "%s", (const char *)P(deque_at(q, 0), REC_PATH));
            sm.state = S_PLAY, sm.loads = playback_loads; /* no load: the queue is the same one */
            qa.app = sm.app, qa.loads = playback_loads;
        }
    } else if (n) {
        if (sm.m3u)
            tk_snprintf(sm.first, sizeof sm.first, "%s",
                        (const char *)P(deque_at(songs, 0), REC_PATH));
        else
            tk_snprintf(sm.last, sizeof sm.last, "%s",
                        (const char *)P(deque_at(songs, n - 1), REC_NAME));
        sm.state = S_PLAY;
        press_natural = 1; /* the app's .play, from its poll: nobody here pressed */
        ui_watch_step("stream play");
        unsigned t = (unsigned)time_now_ms();
        play_folder(songs, 0, 0xf001); /* playing_page copies the queue at once */
        play_ms = (unsigned)time_now_ms() - t;
        press_natural = 0;
        sm.loads = playback_loads;     /* its load, which play_folder has done (playback_groups) */
        qa.app = sm.app, qa.loads = playback_loads;
    }
    deque_destroy(songs);
    if (sm.state == S_PLAY) { /* where a stream's start spent its time on the UI thread */
        char msg[96];
        tk_snprintf(msg, sizeof msg,
                    "stream start: %u songs in %u ms (list %u, tags %u), play %u ms", n, songs_ms,
                    sst.list, sst.tags, play_ms);
        ulog('I', "ui", msg);
    }
    return 0;
}

/* Now playing ("now" in the caps; docs/librespot.md): while the queue playing is a card app's
 * latest URL queue and its header said now=1 (tidal.c app_urls_at), /tmp/q2-<id>.now says which
 * of its songs plays and how: "<seq>\t<n>\t<playing|paused|stopped>\t<sec>\t<length>\n", seq its
 * .play's, n the song's place among the songs kept (as start= counts it). Written whole (blob_io,
 * tmpfs: cheap here on the UI thread) only when it changes: another song or state at once, the
 * position at once while paused or after a seek back, else once it moved NOW_STEP_S on. Never
 * the URL, which carries the app's token. Kept whether the app runs or not, so it reads the file
 * when it next does. Marked stopped, once, when another queue or player takes over, the player
 * stays stopped NOW_STOP_MS (not between tracks), another URL queue starts (any app's), or the apps
 * are stopped (addon_stop: Rockbox, Stop Streaming Services); then nothing until the queue plays
 * again. A restarted demo marks its last run's stopped (now_sweep). Nothing is written for a queue before it first plays. */
#define NOW_STEP_S 5
#define NOW_STOP_MS 3000
static struct {
    int owner;                /* the slot + 1 of the app whose URL queue started last; 0 none */
    char seq[24];             /* that queue's seq */
    int live;                 /* the file says playing or paused */
    int n, state, sec, total; /* as written last: state mclGetPlayStatus's, 1 stopped */
    unsigned stopped, polled; /* time_now_ms() the player was seen stopped since (0 not); paced */
    int swept;                /* now_sweep has run: this demo's .now files are its own */
} nw __attribute__((section(".scratch")));

static void now_write(int state, int sec, int total) {
    static const char *const names[] = { "stopped", "playing", "paused" }; /* status 1, 2, 3 */
    char path[48], line[96];
    int k = tk_snprintf(line, sizeof line, "%s\t%d\t%s\t%d\t%d\n", nw.seq, nw.n, names[state - 1],
                        sec, total);
    tk_snprintf(path, sizeof path, "/tmp/q2-%s.now", ap.a[nw.owner - 1].id);
    blob_io(path, "/tmp/q2-now.tmp", line, (unsigned)k, 1);
    nw.state = state, nw.sec = sec, nw.total = total, nw.live = state != 1;
}

/* a's URL queue started: the queue before it, if its file says it plays, stopped */
static void now_start(app_t *a) {
    if (nw.live) now_write(1, nw.sec, nw.total);
    nw.owner = (int)(a - ap.a) + 1, nw.n = -1, nw.stopped = 0;
    tk_snprintf(nw.seq, sizeof nw.seq, "%s", sm.seq);
}

/* demo started again with /tmp kept (a crash, the watchdog): a .now its last run left saying
 * playing or paused says stopped, at its last place, so an app never sees a play that never ends.
 * Once, at the first poll; the third field is the state. */
static void now_sweep(void) {
    nw.swept = 1;
    void *d = opendir("/tmp");
    for (struct dirent *e; d && (e = readdir(d));) {
        unsigned k = strlen(e->d_name);
        char id[APP_ID], path[48], raw[128], line[128];
        if (k < 8 || k - 7 >= APP_ID || !tk_str_start_with(e->d_name, "q2-") ||
            strcmp(e->d_name + k - 4, ".now"))
            continue;
        tk_snprintf(id, k - 6, "%s", e->d_name + 3); /* q2-<id>.now */
        if (!valid_id(id)) continue;
        tk_snprintf(path, sizeof path, "/tmp/%s", e->d_name);
        state_read(path, raw, sizeof raw); /* raw[0] is its newline */
        char *t = strstr(raw + 1, "\t"), *st = t ? strstr(t + 1, "\t") : 0;
        if (!st) continue;
        const char *rest = tk_str_start_with(st + 1, "playing\t")  ? st + 8
                           : tk_str_start_with(st + 1, "paused\t") ? st + 7
                                                                   : 0;
        if (!rest) continue;
        int n = tk_snprintf(line, sizeof line, "%.*sstopped%s", (int)(st - raw), raw + 1,
                            rest);
        if (n > 0 && n < (int)sizeof line) blob_io(path, "/tmp/q2-now.tmp", line, (unsigned)n, 1);
    }
    if (d) closedir(d);
}

/* addon_poll's, every ADDON_POLL_MS: which URL of a card app's queue plays, written to its .now
 * file, and what plays, as sleep_check reads it (now_source.c's now_poll is another thing: the
 * Bluetooth record and NP_PLAY_FILE) */
static void urls_now_poll(void) {
    unsigned ms = time_now_ms();
    if (!nw.owner || ms - nw.polled < ADDON_POLL_MS) return;
    nw.polled = ms;
    int n = media_owner() == 1 ? app_urls_at() : -1, status = n < 0 ? 1 : mclGetPlayStatus();
    if (n < 0) { /* another queue or player */
        nw.stopped = 0;
        if (nw.live) now_write(1, nw.sec, nw.total);
        return;
    }
    if (status == 1) { /* stopped, or between tracks for a moment */
        if (!nw.stopped) nw.stopped = ms | 1;
        if (nw.live && (int)(ms - nw.stopped) >= NOW_STOP_MS) now_write(1, nw.sec, nw.total);
        return;
    }
    nw.stopped = 0;
    int sec = -1, total = 0;
    player_playtime_and_length(&sec, &total);
    if ((status != 2 && status != 3) || sec < 0) return; /* between tracks: a later pass decides */
    if (!nw.live || n != nw.n || status != nw.state || total != nw.total ||
        (sec != nw.sec && (status == 3 || sec < nw.sec || sec >= nw.sec + NOW_STEP_S)))
        nw.n = n, now_write(status, sec, total);
}

/* A URL queue's start, on a timer as stream_start's (a window opens); then the session is done:
 * the queue is stock's, and the app hears back only through its .now (now_start). */
static int urls_start(const void *unused) {
    (void)unused;
    sm.timer = 0;
    if (playback_loads != sm.loads || playback_picks != sm.picks)
        sm.state = S_DONE; /* the user played a queue, or another song, since */
    if (sm.state != S_WAIT || scanning()) return 0; /* looked at again after the scan */
    press_natural = 1;
    if (app_urls_play(sm.dir)) {
        qa.app = sm.app, qa.loads = playback_loads;
        now_start(sm.app);
    }
    press_natural = 0;
    sm.state = S_DONE;
    return 0;
}

static void *stream_queue(void);
#define toolsFreeStSongInfo ((void (*)(void *))0x5d2e10u) /* build.py APP_URL_CALLS */
/* Now Playing on sm.want, the song a Next or an Up Next pick waits for, at its place at (of at + 1),
 * and that song played once it joins the queue there (stream_poll), unless the user moves again.
 * The record shown is kept here until the next: stock's deque copies it. */
static void want_show(unsigned at) {
    static unsigned rec[0x58 / 4] __attribute__((section(".scratch"))); /* stSongInfo */
    sm.skip = playback_picks, sm.skip_at = at;
    if (P(rec, REC_PATH)) toolsFreeStSongInfo(rec);
    memset(rec, 0, sizeof rec);
    const char *slash = strrchr(sm.want, '/');
    P(rec, REC_NAME) = strdup(slash ? slash + 1 : sm.want); /* before the path: fw tests read it */
    P(rec, REC_PATH) = strdup(sm.want);
    I(rec, REC_TYPE) = 8;
    card_record_tags(rec);
    catalogue_np_show(rec, at, at + 1);
}
/* A user's Next on the last song queued of a playlist session that waits for its next song
 * (sm.want, still to come or still arriving): 1, and Now Playing moves to that song at once (its
 * title, artist and cover from its server row or its file, card_record_tags; the song playing
 * stopped and the spinner turning while it loads: catalogue_np_show, as for a pressed server song),
 * and stream_poll plays it once m3u_songs queues it, unless the user moves to another song
 * meanwhile. That is once the app's download of it is whole for stock's player, which skips or
 * hangs on a file still arriving, and once START_MS of it is there with q2player, which waits at
 * the end of the bytes there are (stream_state.c stream_startable). Stock's Next on a queue's last
 * song would wrap to its first: a radio's seed, played again while the radio's next song
 * downloads. */
int addon_next_waits(void) {
    void *q = sm.state == S_PLAY && sm.m3u && sm.want[0] ? stream_queue() : 0;
    unsigned n = q ? deque_size(q) : 0;
    if (!q || (unsigned)I(MCL_POS, 0) + 1 != n) return 0;
    sm.jump = 0;
    want_show(n);
    return 1;
}

/* Whether the queue playing is the session's: its first song in its folder; or a playlist's first
 * song, and no queue loaded since its own (a Library play of the same album is the user's). */
static void *stream_queue(void) {
    void *q = P(mcl_pdeqplaylist, 0);
    const char *p = q && deque_size(q) ? P(deque_at(q, 0), REC_PATH) : 0;
    return p && (sm.m3u ? playback_loads == sm.loads && !strcmp(p, sm.first)
                        : tk_str_start_with(p, sm.dir) && p[strlen(sm.dir)] == '/')
               ? q
               : 0;
}

/* ---- Up Next's pending rows (upnext.c, docs/internals.md#pending-rows): a playlist session's
 * songs not yet queued, so Up Next shows the whole list, not only the songs fetched ahead. */

/* The playlist's entries from sm.next on that m3u_songs would queue (card paths that fit sm.want),
 * in order, each with its entry number, to each, until it returns 0. Lines are read as m3u_songs
 * reads them. */
static void pending_walk(int (*each)(void *ctx, const char *path, int entry), void *ctx) {
    char *raw = calloc(1, M3U_MAX + 3), *line, *end;
    if (raw && state_read(sm.dir, raw, M3U_MAX + 3) > M3U_MAX) strrchr(raw, '\n')[1] = 0;
    int entry = 0;
    for (line = raw ? raw + 1 : 0; line && *line; line = end) {
        if ((end = strstr(line, "\n"))) *end++ = 0;
        if (tk_str_start_with(line, "\xEF\xBB\xBF")) line += 3;
        unsigned k = strlen(line);
        if (k && line[k - 1] == '\r') line[--k] = 0;
        if (!k || line[0] == '#' || entry++ < sm.next) continue;
        if (card_path(line) && k < sizeof sm.want && !each(ctx, line, entry - 1)) break;
    }
    free(raw);
}

/* A record of path, a song not yet the player's, named as m3u_songs names one (card_record_tags:
 * its server row's title, artist and album, so a song not downloaded yet is named too), on the heap
 * (addon_pending_free). */
static void *pending_record(const char *path) {
    void *r = calloc(1, REC_SIZE);
    const char *slash = strrchr(path, '/');
    if (!r) return 0;
    P(r, REC_NAME) = strdup(slash ? slash + 1 : path);
    P(r, REC_PATH) = strdup(path);
    I(r, REC_TYPE) = 8;
    card_record_tags(r);
    return r;
}
/* A pending row's song again, a record of its own in a one-row array (addon_pending_free): Up
 * Next's menu on it keeps it while the rows are taken again (upnext.c un_menu_hold). */
void **addon_pending_one(const char *path) {
    void **rows = calloc(1, sizeof *rows);
    if (rows && !(rows[0] = pending_record(path))) free(rows), rows = 0;
    return rows;
}
void addon_pending_free(void **rows, unsigned n) {
    for (unsigned i = 0; rows && i < n; ++i)
        if (rows[i]) toolsFreeStSongInfo(rows[i]), free(rows[i]); /* its strings, then it */
    free(rows);
}

typedef struct {
    void **rows;
    unsigned n, max;
} pending_t;
static int pending_add(void *ctx, const char *path, int entry) {
    pending_t *p = ctx;
    (void)entry;
    if (p->n == p->max) return 0;
    if ((p->rows[p->n] = pending_record(path))) ++p->n;
    return 1;
}
/* While the queue playing is a playlist session's: its songs not yet queued, at most max, as
 * records (addon_pending_free), *n of them; 0 for none. */
void **addon_pending(unsigned max, unsigned *n) {
    pending_t p = { 0, 0, max };
    *n = 0;
    if (rs.state && playback_loads == rs.loads && max) { /* a resume asked: those not restored */
        if (!(p.rows = calloc(max, sizeof *p.rows))) return 0;
        const char *r = rs.rows;
        for (unsigned i = 0; i < rs.nrows && pending_add(&p, r, (int)i); ++i) r += strlen(r) + 1;
        if (!p.n) return free(p.rows), (void **)0;
        *n = p.n;
        return p.rows;
    }
    if (sm.state != S_PLAY || !sm.m3u || !max || !stream_queue() ||
        !(p.rows = calloc(max, sizeof *p.rows)))
        return 0;
    pending_walk(pending_add, &p);
    if (!p.n) return free(p.rows), (void **)0;
    *n = p.n;
    return p.rows;
}
/* What addon_pending's rows were taken from: the session, its entries passed, its playlist file's
 * size and time (o32 stat: st_size word 12, st_mtime word 16), and a resume's. */
unsigned addon_pending_sig(void) {
    unsigned st[36] = { 0 },
             v[6] = { (unsigned)sm.state, (unsigned)sm.next, 0, 0, (unsigned)rs.state, rs.seq };
    if (sm.m3u && sm.state == S_PLAY && !__xstat(3, sm.dir, st)) v[2] = st[12], v[3] = st[16];
    return hash_bytes(FNV_SEED, (const unsigned char *)v, sizeof v);
}

typedef struct {
    char *out;
    unsigned n, max, bytes, cap; /* cap: out's bytes, a file grown since its count stops short */
} paths_t;
static int paths_add(void *ctx, const char *path, int entry) {
    paths_t *p = ctx;
    unsigned k = strlen(path) + 1;
    (void)entry;
    if (p->n == p->max || (p->out && p->bytes + k > p->cap)) return 0;
    if (p->out) memcpy(p->out + p->bytes, path, k);
    ++p->n, p->bytes += k;
    return 1;
}
/* Up Next's pending songs (addon_pending's) as paths, for the snapshot (playback.c save_tail): at
 * most max, each with its NUL, in one heap block of *bytes the caller frees; *n of them, 0 for
 * none. */
char *addon_pending_paths(unsigned max, unsigned *n, unsigned *bytes) {
    paths_t p = { 0, 0, max, 0, 0 };
    *n = *bytes = 0;
    if (rs.state && playback_loads == rs.loads) {
        const char *r = rs.rows;
        for (unsigned i = 0; i < rs.nrows && paths_add(&p, r, 0); ++i) r += strlen(r) + 1;
        if (p.n && (p.out = calloc(1, p.bytes))) memcpy(p.out, rs.rows, p.cap = p.bytes);
    } else if (sm.state == S_PLAY && sm.m3u && max && stream_queue()) {
        pending_walk(paths_add, &p); /* counted, then copied */
        if (p.n && (p.out = calloc(1, p.bytes)))
            p.cap = p.bytes, p.n = p.bytes = 0, pending_walk(paths_add, &p);
    }
    if (p.out) *n = p.n, *bytes = p.bytes;
    return p.out;
}

typedef struct {
    const char *path;
    int entry;
} pending_at_t;
static int pending_find(void *ctx, const char *path, int entry) {
    pending_at_t *f = ctx;
    if (strcmp(path, f->path)) return 1;
    f->entry = entry;
    return 0;
}

/* Up Next's pending row of path tapped (upnext.c): as a Next to it (want_show), the session's songs
 * before it passed over (sm.next) and the songs queued after the one playing with them, so it is
 * the next to join the queue, at its end, and plays then; meanwhile .playing names it, so the app
 * fetches it first (stream_poll). 1 when path is one of the session's pending songs. */
int addon_pending_play(const char *path) {
    void *q = sm.state == S_PLAY && sm.m3u ? stream_queue() : 0;
    pending_at_t f = { path, -1 };
    if (!q || !path || strlen(path) >= sizeof sm.want) return 0;
    pending_walk(pending_find, &f);
    if (f.entry < 0) return 0;
    playback_pick(); /* the user moved: a Next waiting, or a session's first song, is let go */
    sm.next = f.entry, sm.more = 0, sm.jump = 1;
    tk_snprintf(sm.want, sizeof sm.want, "%s", path);
    want_show(deque_size(q));
    return 1;
}

static void stream_poll(app_t *a) {
    char path[48], raw[192];
    tk_snprintf(path, sizeof path, "/tmp/q2-%s.play", a->id);
    unsigned got = state_read(path, raw, sizeof raw);
    if (!got) return;
    unsigned h = hash_bytes(FNV_SEED, (const unsigned char *)raw, strlen(raw));
    /* a new session: this app's .play changed. Another app's, left in /tmp while it runs, never
     * takes the queue back. */
    if (h != a->play) {
        char *dir = strstr(raw, " ");
        a->play = h, sm.app = a;
        for (char *c = dir; c && *c; ++c)
            if (*c == '\n' || *c == '\r') *c = 0;
        tk_snprintf(sm.dir, sizeof sm.dir, "%s", dir ? dir + 1 : "");
        unsigned s = 0; /* raw[0] is state_read's newline; a seq is cut at 23 bytes or a control */
        for (; s + 1 < sizeof sm.seq && (unsigned char)raw[1 + s] > ' '; ++s)
            sm.seq[s] = raw[1 + s];
        sm.seq[s] = 0;
        sm.last[0] = sm.playing[0] = 0;
        sm.seen = 0;
        unsigned k = strlen(sm.dir);
        sm.m3u = k > 4 && !strcasecmp(sm.dir + k - 4, ".m3u");
        char own[40];
        tk_snprintf(own, sizeof own, "/tmp/q2-%s.urls", a->id);
        sm.urls = !strcmp(sm.dir, own); /* the app's own alone, in /tmp */
        sm.next = sm.more = sm.keep = 0, sm.first[0] = sm.want[0] = 0;
        sm.loads = playback_loads, sm.picks = playback_picks, sm.skip = sm.jump = 0;
        sm.hold = 0;
        /* a playlist on the card, or a hidden folder of it, as the app's own are, or its URL queue;
         * whole in raw and in sm.dir, as a cut path names another */
        sm.state = got < sizeof raw - 2 && dir && !dir[1 + k] && !dotdot(sm.dir) &&
                           (sm.urls || tk_str_start_with(sm.dir, sm.m3u ? CARD "/" : CARD "/."))
                       ? S_WAIT
                       : S_DONE;
        sm.resume = rs.state == R_SENT && seq_named(rs.seq);
        if (!sm.resume && seq_named(rs.dead)) sm.state = S_DONE; /* a resume let go: not played */
        stop_timer(&sm.timer);
    }
    if (sm.app != a) return;
    /* A song's folder listing (toolsLoadDirectory) refills tools_pdeq_directory, which a library
     * scan's thread fills too: none while one runs, the scan dialog's own loop included; and
     * play_folder would open Now Playing over the dialog, so nothing until it closes. */
    int scan = scanning();
    if (sm.state == S_WAIT && sm.urls) { /* played whole at once, by stock's Tidal player */
        if (playback_loads != sm.loads || playback_picks != sm.picks)
            sm.state = S_DONE; /* the user played a queue, or another song, before it started */
        else if (!sm.timer && !scan && !addon_game_audio()) /* not under a game's sound */
            sm.timer = timer_add(urls_start, 0, 0);
        return;
    }
    if (sm.state == S_WAIT) {
        if (moved_on())
            sm.state = S_DONE; /* the user played a queue, or another song, before its first came */
        else if (!sm.timer && !scan && !addon_game_audio() && /* the game's sound: the song waits */
                 (sm.m3u ? m3u_ready() : stream_newer(sm.dir, "")))
            sm.timer = timer_add(stream_start, 0, 0);
        return;
    }
    void *q = sm.state == S_PLAY ? stream_queue() : 0;
    if (!q) {
        sm.state = S_DONE; /* something else plays now: the session is left */
        return;
    }
    if (!scan && sm.m3u) {
        if (m3u_ready()) {
            void *add = m3u_songs();
            if (deque_size(add)) playback_queue_insert(q, add, 0);
            deque_destroy(add);
            /* a Next or an Up Next pick waited for this: it plays, unless the user moved since */
            if (sm.skip && sm.skip == playback_picks && sm.skip_at < deque_size(q))
                sm.skip = sm.jump = 0, playback_pick(), player_start_queue((int)sm.skip_at);
            else if (sm.skip != playback_picks)
                sm.skip = sm.jump = 0;
        }
        ahead_poll(a->id, sm.seq, q); /* Download Ahead: the songs after the one playing */
    } else if (!scan && stream_newer(sm.dir, sm.last)) {
        void *songs = app_album_songs(sm.dir), *add = _create_deque("stSongInfo");
        deque_init(add);
        for (unsigned i = 0, n = deque_size(songs); i < n; ++i) {
            void *r = deque_at(songs, i);
            if (strcmp(P(r, REC_NAME), sm.last) > 0) _deque_push_back(add, r);
        }
        if (deque_size(add)) {
            void *r = deque_at(add, deque_size(add) - 1);
            tk_snprintf(sm.last, sizeof sm.last, "%s", (const char *)P(r, REC_NAME));
            playback_queue_insert(q, add, 0);
        }
        deque_destroy(add);
        deque_destroy(songs);
    }
    unsigned pos = (unsigned)I(MCL_POS, 0);
    const char *now = pos < deque_size(q) ? P(deque_at(q, pos), REC_PATH) : 0;
    if (sm.jump && sm.skip == playback_picks) now = sm.want; /* an Up Next pick: the app's first */
    /* a resumed song waiting paused is not told as playing (the app reports what .playing names) */
    if (sm.hold && (mclGetPlayStatus() == 2 || !now || strcmp(now, sm.first))) sm.hold = 0;
    if (sm.hold) return;
    /* a path sm.playing can't hold is no use to the app cut, and tk_snprintf's count would pass
     * line's end */
    if (now && strlen(now) < sizeof sm.playing && strcmp(now, sm.playing)) {
        char line[sizeof sm.playing + 1];
        int n = tk_snprintf(line, sizeof line, "%s\n", now);
        tk_snprintf(sm.playing, sizeof sm.playing, "%s", now);
        tk_snprintf(path, sizeof path, "/tmp/q2-%s.playing", a->id);
        blob_io(path, "/tmp/q2-stream.tmp", line, (unsigned)n, 1);
    }
}

/* Play Next and Play Last from an app's own page (a song row's hold menu, "holdmenu" in the caps,
 * docs/internals.md#card-apps): the app fetches the song to the card, then names it in
 * /tmp/q2-<id>.queue, written whole, its latest requests a line each, "<seq> next <card path>" or
 * "<seq> last <card path>", seq a word of its own never reused. Each line is queued once, as the
 * hold menu's Play Next or Play Last queues a Library song (library_pages.c queue_add), and toasted
 * with its words; the seqs queued are kept (taken, QUEUE_KEEP of them), so a line the app keeps in
 * its next write is not queued again. What the file holds the first time it is looked at since demo
 * started was there before: none of it is queued. A song whose file is not there, or a queue that
 * takes none (a stream's), is "Queue unchanged". */
static int queue_taken(app_t *a, unsigned seq) {
    for (unsigned i = 0; i < QUEUE_KEEP; ++i)
        if (a->taken[i] == seq) return 1;
    a->taken[a->next_taken++ % QUEUE_KEEP] = seq;
    return 0;
}

/* The card song at path as a queue's record, named by its tags (card_record_tags), alone in a new
 * deque: empty when its folder's listing has no such song. */
static void *path_song(const char *path) {
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    const char *slash = strrchr(path, '/');
    char dir[512];
    if (!slash || (unsigned)(slash - path) >= sizeof dir) return out;
    tk_snprintf(dir, sizeof dir, "%.*s", (int)(slash - path), path);
    void *songs = app_album_songs(dir);
    for (unsigned i = 0, n = songs ? deque_size(songs) : 0; i < n; ++i) {
        void *r = deque_at(songs, i);
        if (P(r, REC_PATH) && !strcmp(P(r, REC_PATH), path)) {
            card_record_tags(r);
            _deque_push_back(out, r);
            break;
        }
    }
    if (songs) deque_destroy(songs);
    return out;
}

static void queue_poll(app_t *a) {
    char path[48], raw[QUEUE_RAW];
    tk_snprintf(path, sizeof path, "/tmp/q2-%s.queue", a->id);
    unsigned got = state_read(path, raw, sizeof raw);
    if (got >= sizeof raw - 2) strrchr(raw, '\n')[1] = 0; /* cut: its last line goes whole */
    unsigned h = got ? hash_bytes(FNV_SEED, (const unsigned char *)raw, strlen(raw)) : 0;
    int first = !a->queue_seen;
    if (!first && h == a->queue) return;
    a->queue = h, a->queue_seen = 1;
    const char *said = 0;
    for (char *line = got ? raw + 1 : 0, *end; line && *line; line = end) {
        if ((end = strstr(line, "\n"))) *end++ = 0;
        char *word = strstr(line, " "), *song = word ? strstr(word + 1, " ") : 0;
        if (!song || word == line) continue;
        *word++ = 0, *song++ = 0;
        int next = !strcmp(word, "next");
        if ((!next && strcmp(word, "last")) ||
            queue_taken(a, hash_bytes(FNV_SEED, (const unsigned char *)line, strlen(line))) ||
            first)
            continue;
        void *add = card_path(song) ? path_song(song) : (void *)0;
        int ok = add && deque_size(add) && queue_add(add, next ? 1 : 2);
        if (add) deque_destroy(add);
        said = !ok ? "Queue unchanged" : next ? "Playing Next" : "Playing Last";
    }
    if (said) toast(said); /* once all are queued: its loop runs the polls again */
}

/* Sleep timers (q2-podcasts' Sleep Timer): an app's /tmp/q2-<id>.sleep holds "until <time>"
 * (seconds since the epoch, at most SLEEP_MAX_S away) or "end" (the track playing now ends). Q2 Muse
 * keeps the timer itself, so it pauses whatever plays (media_set_playing) once the app has stopped
 * too. A time is kept as the time left on the boot clock (boot_s), which counts Standby and doesn't
 * move when Wi-Fi sets the clock; one the Q2 slept through by more than SLEEP_LATE_S is let go.
 * "end" pauses when the track ends by itself (playback.c auto_nexts) or leaves its last SLEEP_END_S
 * seconds for the next one, which then waits at its start, or starts over (repeat one); another
 * track picked, or a seek back, waits for that one's end instead (stock refills the same queue, so
 * a new queue looks the same), and playback stopped for SLEEP_STOP_MS (not between tracks) or
 * another player lets it go. Done or let go, the file is removed: the app's sign that no timer
 * runs. The app removes it to cancel. One timer: the latest asked, any app's. */
#define SLEEP_MAX_S (7 * 24 * 3600)
#define SLEEP_LATE_S 60
#define SLEEP_END_S 2
#define SLEEP_STOP_MS 3000
static struct {
    unsigned seen[APPS_MAX]; /* each app's .sleep as read last, hashed; 0 none */
    long at;                 /* boot_s() to pause at; 0 none */
    int end, owner;          /* end: pause when the local track ends; owner: the app's slot + 1 */
    unsigned pos, nexts;     /* the track "end" waits on, by its place in the queue; auto_nexts */
    int sec, total;          /* its elapsed and length as last seen, seconds; total 0 unknown */
    unsigned stopped;        /* time_now_ms() playback was seen stopped since; 0 not stopped */
} sl __attribute__((section(".scratch")));

static void sleep_path(char *out, unsigned size, int slot) {
    tk_snprintf(out, size, "/tmp/q2-%s.sleep", ap.a[slot].id);
}

static void sleep_done(void) {
    char path[48];
    if (sl.owner)
        sleep_path(path, sizeof path, sl.owner - 1), unlink(path), sl.seen[sl.owner - 1] = 0;
    sl.at = 0, sl.end = 0, sl.owner = 0;
}

/* Seconds since boot, Standby included. */
static long boot_s(void) {
    struct {
        long s, ns;
    } t = { 0 };                  /* struct timespec */
    clock_gettime(7, (void *)&t); /* CLOCK_BOOTTIME */
    return t.s;
}

/* An app's .sleep, read while it runs: a new request replaces the timer, its removal cancels it. */
static void sleep_read(app_t *a) {
    char path[48], raw[48];
    int slot = (int)(a - ap.a);
    sleep_path(path, sizeof path, slot);
    int have = state_read(path, raw, sizeof raw);
    unsigned h = have ? hash_bytes(FNV_SEED, (const unsigned char *)raw, strlen(raw)) | 1 : 0;
    if (h == sl.seen[slot]) return;
    sl.seen[slot] = h;
    if (!have) { /* the app cancelled its own */
        if (sl.owner == slot + 1) sl.at = 0, sl.end = 0, sl.owner = 0;
        return;
    }
    if (sl.owner != slot + 1) sleep_done(); /* another app's timer gives way, its file removed */
    sl.owner = slot + 1, sl.at = 0, sl.end = 0; /* and this app's earlier one */
    const char *req = raw + 1;                  /* state_read puts a newline first, for field() */
    if (tk_str_start_with(req, "end")) {
        sl.end =
            media_owner() == 1 && mclGetPlayStatus() != 1; /* the local player, playing or paused */
        sl.pos = (unsigned)I(MCL_POS, 0), sl.nexts = auto_nexts, sl.sec = -1, sl.total = 0;
        sl.stopped = 0;
        if (!sl.end) sleep_done();
    } else if (tk_str_start_with(req, "until ")) {
        long long at = 0, now = time((void *)0);
        const char *c = req + 6;
        for (; *c >= '0' && *c <= '9' && c < req + 6 + 11; ++c) at = at * 10 + (*c - '0');
        if (c == req + 6 || (*c && *c != '\n') || at - now > SLEEP_MAX_S) {
            sleep_done(); /* no time, or none this app could mean: dropped, nothing paused */
            return;
        }
        if (at <= now) { /* due; past by SLEEP_LATE_S (a stale file): let go, as sleep_check does */
            if (now - at < SLEEP_LATE_S) media_set_playing(0);
            sleep_done();
            return;
        }
        sl.at = boot_s() + (long)(at - now);
    } else
        sleep_done();
}

/* Every UI pass: a timer due, or the track "end" waited for over, pauses. */
static void sleep_check(void) {
    if (sl.at) {
        long late = boot_s() - sl.at;
        if (late >= SLEEP_LATE_S)
            sleep_done(); /* the Q2 slept through it */
        else if (late >= 0)
            media_set_playing(0), sleep_done();
    } else if (sl.end) {
        unsigned now = time_now_ms(), pos = (unsigned)I(MCL_POS, 0);
        if (mclGetPlayStatus() == 1) { /* stopped, or between tracks for a moment */
            if (!sl.stopped) sl.stopped = now | 1;
            if ((int)(now - sl.stopped) >= SLEEP_STOP_MS) sleep_done(); /* | 1: maybe now + 1 */
            return;
        }
        sl.stopped = 0;
        if (media_owner() != 1) {
            sleep_done(); /* another player: nothing to wait for */
            return;
        }
        int sec = -1, total = 0;
        player_playtime_and_length(&sec, &total);
        if (sec < 0) return; /* between tracks: a later pass decides */
        /* ended by itself, or at its end when last seen; a length unknown, any move on counts */
        int ended = auto_nexts != sl.nexts ||
                    (sl.sec >= 0 && (sl.total <= 0 || sl.sec >= sl.total - SLEEP_END_S));
        if (pos != sl.pos || sec + SLEEP_END_S < sl.sec) { /* moved on, or back */
            if (ended) {
                media_set_playing(0), sleep_done();
                return;
            }
            sl.pos = pos, sl.nexts = auto_nexts; /* another track picked, or a seek back */
        }
        sl.sec = sec, sl.total = total;
    }
}

/* ringnav_sleep, every UI loop pass, paced to ADDON_POLL_MS once its row started it: standby and
 * auto power-off held while the app asks (hold=1), as Spotify's poll does while it plays; the
 * screen still turns off. An app that stopped by itself (idle, or from the phone) is let go, and
 * one stopped for USB storage starts again once the card is back. */
static void sync_read(app_t *a, const char *raw);
static void reach_read(app_t *a, const char *raw);
static void offline_read(app_t *a, const char *raw);
static void poll_one(app_t *a) {
    unsigned now = time_now_ms();
    if ((!a->launched && !a->relaunch && !a->restart) || now - a->polled < ADDON_POLL_MS) return;
    a->polled = now;
    if (a->restart) { /* its loop gone (the launcher kills a frozen app 15 s after its TERM) */
        if (access(a->pid, 0) || now - a->restart_at > RESTART_MS) a->restart = 0, launch(a);
        return;
    }
    if (a->relaunch) {
        if (!navigator_window_is_exist("usbmode_page") && !access(a->bin, 0))
            a->relaunch = 0, launch(a);
        return;
    }
    /* hold= may come anywhere, after the long qr= too (app_state) */
    char raw[QR_RAW];
    app_state(a->state, raw, sizeof raw);
    a->hold = number(raw, "hold") == 1;
    if (a->hold) reset_poweroptions_timer(1, 1, 0); /* standby, auto power-off; not the screen's */
    sync_read(a, raw);
    reach_read(a, raw);
    offline_read(a, raw);
    stream_poll(a);
    queue_poll(a);
    sleep_read(a);
    if (access(a->pid, 0) && now - a->launch_at > ADDON_START_MS)
        a->launched = a->hold = a->sync = a->reach = 0;
}

/* The status row's sync (status_row.c, docs/internals.md#card-app-sync): what each running app's
 * state file says, sync=check while it checks its server or its metadata, sync=down DONE TOTAL
 * while it downloads songs (DONE of TOTAL, in any unit), no key while idle. Read at poll_one's
 * pace from the read it makes anyway (app_state, which keeps the lines after a sign-in's long
 * qr=). */
static unsigned long long digits(const char **c) {
    unsigned long long x = 0;
    while (**c == ' ') ++*c;
    for (; **c >= '0' && **c <= '9'; ++*c) x = x * 10 + (unsigned)(**c - '0');
    return x;
}
static void sync_read(app_t *a, const char *raw) {
    char v[48];
    field(raw, "sync", v, sizeof v);
    a->sync = 0;
    if (!strncmp(v, "check", 5)) a->sync = 1;
    if (strncmp(v, "down", 4)) return;
    const char *c = v + 4;
    unsigned long long done = digits(&c), total = digits(&c);
    while (total >> 20) done >>= 1, total >>= 1; /* 32-bit sums below: no 64-bit division here */
    a->sync = 2;
    a->sync_pm = !total ? 0 : done >= total ? 1000 : (int)((unsigned)done * 1000u / (unsigned)total);
}
int addon_sync(int *permille) {
    int kind = 0;
    for (int i = 0; i < ap.n; ++i) {
        app_t *a = &ap.a[i];
        if (!a->launched || a->sync < kind) continue;
        if (a->sync > kind && permille) *permille = a->sync_pm;
        kind = a->sync;
    }
    return kind;
}

/* A card app's server out of reach (docs/internals.md#card-app-reach): its state file's
 * reach=down <code> while the server can't be reached, until a request to it succeeds, which the
 * status row shows (status_row.c SLOT_OFFLINE, through addon_offline); and srverr=<unix> <code>,
 * each failure to say once as the icon toast (docs/oled-design.md#dialogs-and-toasts). <code> is
 * an HTTP status ("Server error 502"), none (no answer: "Server not answering") or dns (a name that
 * doesn't resolve: "Server not found"). A srverr is said once, when it is new to this demo and no
 * older than REACH_FRESH_S by the clock the app wrote it with (so a demo started beside a running
 * app doesn't say an old one), and only while Wi-Fi is connected: with none the missing Wi-Fi glyph
 * already says why, and the toast would blame the server. Read at poll_one's pace. */
#define REACH_FRESH_S 10
static void reach_read(app_t *a, const char *raw) {
    char v[sizeof a->srverr], text[40];
    field(raw, "reach", v, sizeof v);
    a->reach = !strncmp(v, "down", 4);
    field(raw, "srverr", v, sizeof v);
    if (!v[0] || !strcmp(v, a->srverr)) return;
    tk_snprintf(a->srverr, sizeof a->srverr, "%s", v);
    const char *code = v;
    long long at = (long long)digits(&code), now = time((void *)0);
    while (*code == ' ') ++code;
    if (at < now - REACH_FRESH_S || at > now + REACH_FRESH_S || !wifi_level()) return;
    if (*code >= '1' && *code <= '9')
        tk_snprintf(text, sizeof text, "Server error %s", code);
    else if (!strcmp(code, "none"))
        tk_snprintf(text, sizeof text, "Server not answering");
    else if (!strcmp(code, "dns"))
        tk_snprintf(text, sizeof text, "Server not found");
    else
        return;
    toast_later("srvtoast", text);
}
/* Sync Offline (docs/internals.md#sync-offline): offline.c's press on a Library item asks the
 * item's app, "<seq> <add|remove> <kind> <id>" in /tmp/q2-<id>.offline, written only once the app
 * listens (its browse socket there: an app reads a request present at its start as answered), the
 * app started first when it isn't running (addon_wake). Its answer, offlineack=<seq> <words> in its
 * state file, is toasted (offline_read); none in OFF_WAIT_MS is "<name> didn't answer". One request
 * at a time: a press while one waits replaces it. */
#define OFF_WAIT_MS 25000
static struct {
    char id[APP_ID], line[300];
    unsigned seq, at; /* at: when it was pressed; 0 with none waiting */
    int sent;
} off __attribute__((section(".scratch")));
static void offline_send(app_t *a) {
    if (!off.at || off.sent || strcmp(a->id, off.id) || access(a->browse, 0)) return;
    char path[48], text[sizeof off.line + 16];
    tk_snprintf(path, sizeof path, "/tmp/q2-%s.offline", a->id);
    int n = tk_snprintf(text, sizeof text, "%u %s\n", off.seq, off.line);
    off.sent = blob_io(path, "/tmp/q2-offline.tmp", text, (unsigned)n, 1);
}
/* 1 when id takes Sync Offline (its q2app's offline=1) */
int addon_offline_ok(const char *id) {
    app_t *a = valid_id(id) ? known(id) : 0;
    if (valid_id(id) && (!a || !a->present)) addon_scan(), a = known(id);
    return a && a->present && a->offline;
}
/* Asks id to sync key ("<kind> <id>") offline (add) or stop; 1 when the request is on its way. */
int addon_offline_ask(const char *id, int add, const char *key) {
    app_t *a = valid_id(id) ? known(id) : 0;
    if (!addon_offline_ok(id) || !key || !*key || addon_wake(id) < 0) return 0;
    tk_snprintf(off.id, sizeof off.id, "%s", id);
    tk_snprintf(off.line, sizeof off.line, "%s %s", add ? "add" : "remove", key);
    off.seq = (unsigned)time((void *)0) * 8u + (off.seq + 1) % 8u, off.at = time_now_ms() | 1,
    off.sent = 0;
    offline_send(a);
    return 1;
}
/* poll_one's part for Sync Offline: the waiting request sent once the app listens, and its answer
 * toasted. */
static void offline_read(app_t *a, const char *raw) {
    if (!off.at || strcmp(a->id, off.id)) return;
    offline_send(a);
    char v[160], seq[16];
    field(raw, "offlineack", v, sizeof v);
    int k = tk_snprintf(seq, sizeof seq, "%u ", off.seq);
    if (off.sent && !strncmp(v, seq, (unsigned)k) && v[k]) {
        off.at = 0;
        toast_later("", v + k);
    } else if (time_now_ms() - off.at > OFF_WAIT_MS) {
        char text[48];
        off.at = 0;
        tk_snprintf(text, sizeof text, "%s didn't answer", a->name[0] ? a->name : a->id);
        toast_later("", text);
    }
}

/* Whether a running app's server is out of reach and Wi-Fi is connected: the status row's
 * offline glyph. */
int addon_offline(void) {
    if (!wifi_level()) return 0;
    for (int i = 0; i < ap.n; ++i)
        if (ap.a[i].launched && ap.a[i].reach) return 1;
    return 0;
}

/* Card games: Library's Games lists them after its own (games.c); a row runs the game's binary
 * from its folder, with no launcher, over the whole screen while demo goes on without painting
 * (navigation.c ringnav_wm_paint) or input (ringnav_input), as Videos' q2video does. Each input
 * event reaches the game instead as a text datagram on /tmp/q2-<id>.input.sock:
 *   "t d X Y", "t m X Y", "t u X Y"   touch down, move, up (an abort too), in the landscape
 *                                     view's pixels (375 x 320), turned from the panel's
 *   "k d CODE", "k u CODE"            a key down, up (AWTK's codes: Return 170, Centre 218)
 *   "w N"                             the wheel's raw travel since the last (wheel_raw: 200 a turn,
 *                                     rising anticlockwise), sent once a UI loop pass
 * Play/Pause and the side buttons stay the music's, as stock has them under the Games page, unless
 * the game's q2app says audio=1: the sound is then the game's (now_hold and dac_take before it
 * starts, as video_play; the output device and, on Bluetooth or USB, the volume as its arguments,
 * as q2video gets them, and opened as q2video opens them, video.c sound_open; g_dacoff_time held
 * while it runs), those keys reach it as "k d|u 171", 222 and 223, a headset's buttons do nothing
 * (headset.c media_key, media_set_playing), and the idle quit below is left to the game, which
 * alone knows whether it is playing. The Library's song is held paused where it was while it runs and
 * loaded again paused there when it ends (now_release), as after a video. The game
 * ends itself (from its Return menu); Return held GAME_HOLD_MS ends it (TERM, and its release is
 * dropped so stock's Games stays), as does GAME_IDLE_MS without input unless Smart Awake is on;
 * one still there GAME_KILL_MS after a TERM is KILLed. demo repaints when it has gone. Stock makes
 * a held key's long press after this hook, so the hold is timed here, from the game's key-down.
 * ponytail: the screen stays on while it runs, as for a video, and the idle quit is what lets it
 * sleep without Smart Awake; a game that wants to sleep on screen needs the wake path to reach
 * stock under it. */
#define GAME_IDLE_MS 120000
#define GAME_HOLD_MS 700 /* navigation.c's KEY_HOLD_MS, Return's hold elsewhere */
#define GAME_KILL_MS 3000
static struct {
    int pid, sock, eat;  /* eat: the held Return's release, after the game it ended */
    int audio;           /* the running game's audio=1: the keys and the DAC are its */
    unsigned seen;       /* time_now_ms of the last input */
    unsigned held, term; /* when Return went down, when TERM went (0: neither) */
    char path[48];       /* the game's socket */
} gm __attribute__((section(".scratch")));

int addon_games(void) { return addon_scan(), ap.games; }
int addon_games_last(void) { return ap.games; }
const char *addon_game_name(int k) { return k >= 0 && k < ap.games ? ap.a[ap.game[k]].name : 0; }
int addon_game_on(void) { return gm.pid; }
/* a card game with audio=1 runs, or Caller Ten (game_callerten.c) holds the sound for a contest */
int addon_game_audio(void) { return (gm.pid && gm.audio) || callerten_sound(); }

static void game_kill(const char *how) {
    char cmd[24];
    tk_snprintf(cmd, sizeof cmd, "kill %s%d", how, gm.pid); /* demo imports no kill() */
    system(cmd);
}
static void game_end(void) {
    if (gm.term) return; /* game_poll KILLs it GAME_KILL_MS on */
    gm.term = (unsigned)time_now_ms() | 1;
    game_kill("");
}

/* netupdate.c's toast once app id is installed: a game is in Games, not Streaming */
int addon_is_game(const char *id) {
    addon_scan();
    for (int k = 0; k < ap.games; ++k)
        if (!strcmp(ap.a[ap.game[k]].id, id)) return 1;
    return 0;
}

/* Card game k started over the screen: 1 when it runs. */
int addon_game_start(int k) {
    app_t *a = k >= 0 && k < ap.games ? &ap.a[ap.game[k]] : 0;
    if (gm.pid || !a) return 0;
    const char *busy = shelf_busy();
    if (busy && !strcmp(busy + 1, a->id)) return toast("Wait until the App Store finishes"), 0;
    if (access(a->bin, 0)) return toast("It isn't on the card"), 0;
    char vol[5], pop[POPUP_ENV];
    const char *dev = 0;
    popup_env(pop, sizeof pop); /* the theme's pop-up, for its own menus */
    if (a->audio) { /* the sound is its: hciplayer off the PCM, the jack readied, as video_play */
        headset_manual();
        spot_yield();
        radio_stop();
        now_hold(); /* the Library paused where it was, not stopped and forgotten (now_source.c) */
        dev = output_device(vol);
        if (!dev) dac_take();
    }
    int pid = fork();
    if (!pid) {
        child_oom_first();
        for (int fd = 3; fd < 1024; ++fd) close(fd); /* demo's: the watchdog, the screen, sockets */
        /* the child's alone: demo's environment stays as it was. ponytail: a fork while another thread
         * holds libc's environment lock (stock's toolsSetTimeZone) hangs the child here; Return's hold
         * ends it */
        setenv("Q2_POPUP", pop, 1);
        if (a->audio) /* q2video's arguments: the device, then a soft output's volume */
            execl(a->bin, a->bin, dev ? dev : "plughw:0,0", dev ? vol : (char *)0, (char *)0);
        else
            execl(a->bin, a->bin, (char *)0);
        child_exit(127);
    }
    if (pid < 0) {
        if (a->audio) now_release(); /* no game: the Library's song back at once */
        return toast("It couldn't start"), 0;
    }
    gm.pid = pid;
    gm.audio = a->audio;
    gm.sock = socket(1, 1, 0); /* AF_UNIX, SOCK_DGRAM (MIPS numbering) */
    gm.seen = time_now_ms();
    gm.held = gm.term = gm.eat = 0;
    tk_snprintf(gm.path, sizeof gm.path, "/tmp/q2-%s.input.sock", a->id);
    __sync_lock_test_and_set(&wheel_travel, 0);
    wheel_raw = 1;
    return 1;
}

/* ringnav_input, every event: 1 when it is the game's (sent as its datagram) or the release of the
 * Return held to end it; 0 for stock's, a music key among them */
int addon_game_input(void *e) {
    if (!e) return gm.pid != 0;
    unsigned type = (unsigned)I(e, EVENT_TYPE), key = (unsigned)I(e, EVENT_KEY);
    int keyish = type == EVT_KEY_DOWN || type == EVT_KEY_UP || type == EVT_KEY_LONG;
    if (keyish && key == KEY_RETURN && gm.eat) /* the held Return's repeats and release */
        return gm.eat = type != EVT_KEY_UP, 1;
    if (!gm.pid) return 0;
    if (!gm.audio && keyish && (key == KEY_PLAY || key == KEY_BACK_BTN || key == KEY_FWD_BTN))
        return 0;
    if (keyish && key == KEY_RETURN)
        gm.held = type == EVT_KEY_UP ? 0
                  : type == EVT_KEY_DOWN && !gm.held
                      ? (unsigned)time_now_ms() | 1 /* not a repeat's */
                      : gm.held;
    char m[32];
    m[0] = 0;
    if (type == EVT_KEY_DOWN || type == EVT_KEY_UP)
        tk_snprintf(m, sizeof m, "k %s %u", type == EVT_KEY_UP ? "u" : "d", key);
    else if ((type >= EVT_POINTER_DOWN - 1 && type <= EVT_POINTER_UP_BEFORE) ||
             type == EVT_POINTER_ABORT) {
        const char *what = type == EVT_POINTER_ABORT || type >= EVT_POINTER_UP_BEFORE - 1 ? "u"
                           : type >= EVT_POINTER_MOVE_BEFORE - 1                          ? "m"
                                                                                          : "d";
        /* here, before the window manager turns it, a touch is the portrait panel's: the view
         * (375 x 320) is the panel turned anticlockwise, so its x is the panel's y */
        tk_snprintf(m, sizeof m, "t %s %d %d", what, I(e, EVENT_Y), 319 - I(e, EVENT_X));
    }
    gm.seen = time_now_ms();
    if (m[0]) dgram_send(&gm.sock, gm.path, m, strlen(m));
    return 1;
}

/* every UI loop pass: the wheel's travel, the screen and standby held, the idle quit, its end */
static void game_poll(void) {
    int status = 0;
    if (!gm.pid) return;
    if (waitpid(gm.pid, &status, 1)) { /* WNOHANG: it has ended */
        int audio = gm.audio;
        close(gm.sock);
        gm.pid = gm.audio = 0; /* first: no start while a game has the sound (ringnav_start_player) */
        /* the Library's song held at the start back, paused where the game found it; with none
         * held, a jack or USB change meanwhile ran stock's player_save_start(1): its start failed
         * (ringnav_start_player) but its pause stuck on nothing, so Play would do nothing */
        if (audio && !now_release() && mclGetPlayStatus() == 3) player_stop();
        wheel_raw = 0;
        widget_invalidate_force(window_manager(), 0);
        if (status == 127 << 8) toast("It couldn't start"); /* the child's execl failed */
        return;
    }
    unsigned now = (unsigned)time_now_ms();
    /* signed: a stamp can be just after now (| 1 rounds up, game_end reads the clock again) */
    if (gm.held && (int)(now - gm.held) >= GAME_HOLD_MS) gm.held = 0, gm.eat = 1, game_end();
    if (gm.term && (int)(now - gm.term) >= GAME_KILL_MS) gm.term = now | 1, game_kill("-9 ");
    int travel = __sync_lock_test_and_set(&wheel_travel, 0);
    if (travel) {
        char m[16];
        tk_snprintf(m, sizeof m, "w %d", travel);
        dgram_send(&gm.sock, gm.path, m, strlen(m));
        gm.seen = time_now_ms();
    }
    reset_poweroptions_timer(1, 1, 1);
    if (gm.audio) { /* the DAC kept on, as video_poll; the idle quit is the game's own */
        if (I(g_dacoff_time, 0) > 0) I(g_dacoff_time, 0) = 0;
        return;
    }
    if (time_now_ms() - gm.seen < GAME_IDLE_MS) return;
    if (!ringnav_smart_awake()) game_end();
    gm.seen = time_now_ms(); /* Smart Awake: asked again GAME_IDLE_MS on */
}

/* Smart Wi-Fi (smart_wifi.c smart_needed): an app started, or restarting after Retry, keeps the radio
 * on until it stops (by itself, Stop Streaming Services or Rockbox). One stopped for USB storage
 * counts once it starts again: the cable holds the radio meanwhile, and an app deleted from the
 * computer, which never starts again, holds nothing. */
int addon_running(void) {
    for (int i = 0; i < ap.n; ++i)
        if (ap.a[i].launched || ap.a[i].restart) return 1;
    return 0;
}

void addon_poll(void) {
    if (!nw.swept) now_sweep();
    game_poll();
    resume_poll(); /* first: a resume let go before its app's .play is read */
    for (int i = 0; i < ap.n; ++i) poll_one(&ap.a[i]);
    sleep_check();
    urls_now_poll();
    library_poll();
    catalogue_poll(); /* the server library's apps (catalogue.c), running or not */
}

/* An app asked to end through its loop, not its socket, so a frozen one stops too; restart: started
 * again once its loop is gone (poll_one). */
static void app_stop(app_t *a, int restart) {
    char cmd[96];
    stop_cmd(a->pid, cmd, sizeof cmd), system(cmd);
    a->launched = a->hold = a->relaunch = 0;
    a->restart = restart, a->restart_at = time_now_ms();
}

/* Streaming's Stop Streaming Services, and Home's Rockbox row: each asked to end (its downloads
 * or queue saved). */
void addon_stop(void) {
    char cmd[96];
    for (int i = 0; i < ap.n; ++i) {
        app_t *a = &ap.a[i];
        if (a->launched || !access(a->pid, 0)) stop_cmd(a->pid, cmd, sizeof cmd), system(cmd);
        a->launched = a->hold = a->relaunch = a->restart = 0;
    }
    /* Rockbox's row kills demo next, /tmp kept; Stop Streaming Services stops a URL queue too */
    if (nw.live) now_write(1, nw.sec, nw.total);
}

/* usbmode_page_init's hook (navigation.c), before stock unmounts the card for USB storage: every
 * app asked to end at once, then up to AWAY_MS in all for their loops to be gone (each removes its
 * pid file once the app has exited), so nothing of them holds the card; each starts again after
 * (addon_poll). On the UI thread, so it must stay short; addon_quit is the Apps page's worker's.
 * ponytail: an app still saving on its TERM after AWAY_MS can write to the card as USB takes it;
 * the real fix defers stock's USB-mode call until the apps are gone, without sleeping. */
void addon_card_away(void) {
    char cmd[96];
    int left = 0;
    catalogue_card_away(); /* the server library's file, attached to stock's connection */
    for (int i = 0; i < ap.n; ++i) {
        app_t *a = &ap.a[i];
        int running = a->launched || a->restart || !access(a->pid, 0);
        if (!access(a->pid, 0)) stop_cmd(a->pid, cmd, sizeof cmd), system(cmd), left = 1;
        a->launched = a->hold = a->restart = 0;
        a->relaunch = running;
    }
    for (unsigned start = time_now_ms(); left && time_now_ms() - start < AWAY_MS;) {
        sleep_ms(50);
        left = 0;
        for (int i = 0; i < ap.n; ++i) left |= !access(ap.a[i].pid, 0);
    }
}

/* The Apps page (netupdate.c) replaces or removes the files in dir (".<id>"): the app there, if
 * one, asked to end and waited for on the Apps page's worker, so its files are closed before they
 * move (up to QUIT_MS: an app still saving on its TERM after 3 s, a login, would write it back
 * into a folder just removed); then, once they moved or went, forgotten on the UI thread, so its row starts the new one
 * (or none). An update that failed or was cancelled before it stopped the app leaves it running and tracked. */
void addon_forget(const char *dir) {
    for (int i = 0; dir && dir[0] == '.' && i < ap.n; ++i)
        if (!strcmp(ap.a[i].id, dir + 1))
            ap.a[i].launched = ap.a[i].hold = ap.a[i].relaunch = ap.a[i].restart = 0;
}
void addon_quit(const char *dir) { /* addon_card_away's wait, libc's clock and sleep, one app */
    char pid[32], cmd[96];
    if (!dir || dir[0] != '.' || !valid_id(dir + 1)) return;
    snprintf(pid, sizeof pid, "/tmp/q2-%s", dir + 1);
    if (access(pid, 0)) return;
    stop_cmd(pid, cmd, sizeof cmd);
    system(cmd);
    for (unsigned long long end = now_ms() + QUIT_MS; !access(pid, 0) && now_ms() < end;)
        usleep(50000);
}

/* A Q2SPOT/1 request in flight on its pthread, as Spotify's browser's (spotify.c): written from
 * buf, the reply then read into it, one connection a request. */
static struct {
    unsigned long thread;
    int running, fail; /* fail: REQ_* */
    volatile int cancel, done;
    unsigned id, n, len; /* id: the latest request's, never reused */
    char *buf;
    const char *path; /* the app's browse socket */
    int field;        /* it sends a field row's text (field_sent): no answer is an error, not a wait */
} req __attribute__((section(".scratch")));
enum { REQ_OK, REQ_NOSOCK, REQ_FAIL };

/* Written, shut for writing, and read to EOF within ADDON_WAIT_MS, 100 ms at a time so a cancel is
 * joined at once. */
static void *worker(void *unused) {
    struct unix_addr to = { 1, { 0 } };
    tk_snprintf(to.path, sizeof to.path, "%s", req.path);
    unsigned long long end = now_ms() + ADDON_WAIT_MS;
    unsigned sent = 0;
    int fd = socket(1, 2, 0), k = 1; /* AF_UNIX, SOCK_STREAM (MIPS numbering) */
    req.fail = REQ_NOSOCK;
    if (fd >= 0 && fd < 1024 && !fcntl(fd, 4, 0x80) &&
        !connect(fd, &to, sizeof to)) { /* F_SETFL, O_NONBLOCK (MIPS) */
        req.fail = REQ_FAIL;
        while (!req.cancel && now_ms() < end) {
            unsigned set[32] = { 0 }; /* fd_set */
            int tv[2] = { 0, 100000 };
            set[fd / 32] = 1u << fd % 32;
            int writing = sent < req.len;
            if ((k = select(fd + 1, writing ? 0 : set, writing ? set : 0, 0, tv)) <= 0) {
                if (k) break; /* not a timeout */
                continue;
            }
            if (writing) {
                k = send(fd, req.buf + sent, req.len - sent, 0x4000); /* MSG_NOSIGNAL */
                if (k <= 0) break;
                if ((sent += (unsigned)k) == req.len) spot_shut(fd);
                continue;
            }
            if ((k = recv(fd, req.buf + req.n, REPLY_MAX + 1 - req.n, 0)) <= 0 ||
                (req.n += (unsigned)k) > REPLY_MAX) {
                req.fail = k ? REQ_FAIL : REQ_OK;
                break;
            }
        }
    }
    if (fd >= 0) close(fd);
    /* the request's bytes past the reply: none kept, a field row's text being one (field_sent) */
    if (req.n < req.len) memset(req.buf + req.n, 0, req.len - req.n);
    if (req.n <= REPLY_MAX) req.buf[req.n] = 0;
    req.done = 1;
    return 0;
}

/* The request in flight dropped: its pthread joined (within 100 ms) and its buffer freed. */
static void req_cancel(void) {
    unsigned none = 0;
    worker_stop(req.thread, &req.running, &req.cancel, &none);
    free(req.buf);
    req.buf = 0;
    req.done = 0;
}

/* op and tail (its arguments: "\t" and escaped each) to the app at path, under a new id; the one
 * before is dropped. When it can't start, req.done is set at once, so the tick reports it. Once
 * the thread runs, done is the worker's alone: it may have finished already. */
static void req_send(const char *path, const char *op, const char *tail) {
    req_cancel();
    ++req.id;
    req.cancel = 0;
    req.n = 0;
    req.fail = REQ_FAIL;
    req.path = path;
    int n = (req.buf = calloc(REPLY_MAX + 2, 1))
                ? tk_snprintf(req.buf, REQ_MAX, "Q2SPOT/1\n%u\t%s%s\n", req.id, op, tail)
                : 0;
    req.len = n > 0 && n < REQ_MAX ? (unsigned)n : 0;
    req.field = 0;
    req.running = req.len && !pthread_create(&req.thread, 0, worker, 0);
    /* no worker to clear what it held (a field's text): cleared here, a cut one's REQ_MAX too */
    if (!req.running && req.buf) memset(req.buf, 0, REQ_MAX);
    if (!req.running) req.done = 1;
}

/* A finished request's thread joined: 1 when its reply (or failure) is ready to take. */
static int req_done(void) {
    if (!req.done) return 0;
    if (req.running) pthread_join(req.thread, 0);
    req.running = req.done = 0;
    return 1;
}

/* The page: addon_page, for app slot pg.a, one page_list rebuilt per level.
 * A level is an op and its arguments; the top one's reply is the shown one, cut in place by
 * spot_reply into the header's 4 fields and 6 a row, and each level under it keeps its own (kept)
 * so Back shows it at once and asks for it again. A row's kind: list
 * opens the level uri (its context the argument), action sends uri with the context and toasts the
 * reply's message, search opens the text box (OK opens "search" for the text; a list's first one is
 * the title bar's search icon instead, take_find), sort opens as list does (caps word "sort"; one
 * just after a taken search is the title bar's sort icon instead, take_sort), remote opens the QR
 * code, local plays from the card (uri: a song, or a folder to list; context "album": in track
 * order), field opens the text box too, whose text goes to its uri, after its context, the reply
 * taking the level's place with no level pushed and nothing titled with the text (field_sent),
 * needslib is an action as action is (the server library is always on), info does nothing; its position,
 * when set, is the op a held Play/Pause sends. progress
 * (progress_row) draws a bar of its position, permille, under its title and subtitle; a press or a
 * held Play/Pause does nothing. page (caps word "window") shows the level's own list from the
 * cursor in its context, in the level's place rather than over it (page_to): uri "prev" slides it
 * in from the left and selects its last row, any other from the right, selecting its row after the
 * first (the app's Previous page row).
 * A reply whose message is "refresh" is asked again every ADDON_LIVE_MS while its level is on top,
 * and at once after an action on it (the level stays shown, no Loading…), and redrawn only when it
 * changed, its selected row kept; one with the same rows (same_rows) is written in place (relabel)
 * instead of rebuilt. One whose message is "pages" and that has a cursor
 * is followed by its next pages (page_next), appended to its list (append) under a Loading… row;
 * any other keeps More….
 * The first level's reply whose message is "sections" names the app's sections instead (sec_take):
 * each list row one, its uri the op that fills it, its title the page's, its context the op's
 * argument. The page shows one section at a time as its first level, the first to begin with, and
 * a sideways swipe on it (swipe) opens the next or the previous, its list sliding in from that
 * side; none past the ends. Only the first level swipes: a level opened from a section is a level
 * as any other, and Back returns to the section. The wheel never changes section. */
enum { K_LOAD, K_READY, K_ERROR, K_WAIT }; /* K_WAIT: no socket yet, the app starting */
enum { G_ROW, G_SHOW, G_SEARCH, G_SECTION, G_FIND, G_SORT, G_FIELD };
/* The request in flight (pg.act): a level's fetch (0), a row's action, whose reply is toasted, or a
 * row's hold op "hold", whose reply's rows are its menu (hold_send) */
enum { A_FETCH, A_ACTION, A_HOLD };
#define HOLD_OP "hold"
#define HOLD_ROWS 8 /* a hold menu's rows at most */

/* A level's page that can't show the app's rows (an error, or a load or start taking SLOW_MS) has a
 * Stop row whenever the app may be running, so a frozen one can always be ended; unless its q2app
 * says stop=0, when the error page's second row is Cancel (Return's) and a slow load has none. */
typedef struct {
    int state, n, sel, live, retry;
    int nosock, slow;  /* nosock: the error is no socket; slow: the wait's Stop row shown */
    int art;           /* its rows are drawn with covers (art_row): how many have one, 0 none */
    int find;          /* its first row, a search, is the title bar's search icon (take_find) */
    int sort;          /* and its next, a sort, the sort icon left of it (take_sort) */
    unsigned since;    /* when it began loading or waiting, 0 once done */
    unsigned base;     /* args' bytes before a More…'s cursor: the list's own */
    unsigned id, hash; /* id: the fetch whose reply this level takes; hash: its rows' bytes */
    char title[128], note[128], op[16], args[400]; /* args: "\t" and escaped each */
    signed char lay;  /* LAY_*: how show() draws its rows (artist_view) */
    signed char stop; /* LAY_ARTIST's or LAY_ALBUM's lit pair stop (artist_page.c pair_key), 0: its
                         rows' */
} lv_t;

/* A level under the top one's reply, kept for Back (pop): its pages, their fields (calloc'd, 4 + 6
 * a row) and its sort icon's row. f 0: none kept. */
typedef struct {
    char *buf[ADDON_PAGES], **f;
    int pages;
    char *sort[6];
} kept_t;

static struct {
    void *page, *view, *edit;
    unsigned timer, go, leave, again; /* again: when a waiting or live level asks again */
    int depth, act, next, arg, a,
        code; /* act: what the request in flight is for (A_*); a: the app's slot; code: its QR code
                 to show at the first answer */
    int arow, busy; /* the action's row, and whether it says Working… (busy_row) */
    int qrnext; /* an action's refresh in flight: a remote row under the cursor opens its page */
    unsigned acted; /* when the action was sent */
    char term[64], aop[16];
    lv_t *lv; /* ADDON_DEPTH of them, calloc'd, and one more: the level push() waits on (pend) */
    kept_t *kept; /* ADDON_DEPTH, calloc'd with lv: kept[i] level i's reply while one is over it */
    /* The shown reply, the top level's (no other keeps one): pages replies, each realloc'd to its
     * size, their fields in f (calloc'd with lv), every page's rows in turn; paging: a next page in
     * flight (page_next). */
    char *buf[ADDON_PAGES], **f;
    int pages, paging;
    signed char moved; /* the depth's change the next rows or error push in: 1 deeper, -1 back */
    lv_t *sec; /* the first page's sections, each the level it opens, calloc'd; nsec of them */
    signed char nsec, cur; /* cur: the one showing */
    /* a touch on the page: where it went down, and whether a sideways swipe has taken it (swipe) */
    int sx, sy;
    char sdown, sclaim;
    int art_lo, art_hi; /* the rows whose covers may be decoded, half open (art_window) */
    char *sort[6];      /* the sort icon's row (take_sort), its fields in the shown reply */
    int field;          /* the box open is field row field - 1's; 0, a search's */
    /* pend: a level's first reply awaited under the loading overlay (P_*: how it is taken), the
     * shown level and its rows left as they are (docs/oled-design.md#loading); the level waits in
     * lv[ADDON_DEPTH], its id the request's, never reused, so a reply to a cancelled one matches
     * nothing; pmoved: the way a level in the top's place slides in */
    char pend;
    signed char pmoved, psec; /* psec: the section a swipe's level is (P_SECTION) */
    /* hold: the top level asked for (again) in place under the loading overlay, what is shown left
     * as it is until its rows or its error are there, 1 + the state a cancel puts back (hold_load):
     * its first, with no page yet, over the window it was opened from (list_marks.c load_over), its
     * page made then (page_make); Retry; the reload after an action */
    char hold;
} pg __attribute__((section(".scratch")));

static void qr_open(const char *state, const char *title);
static int page_make(void);
void *page_in_use(void); /* smart_wifi.c: the top window past toasts and the volume dialog */
static app_t *app(void) { return &ap.a[pg.a]; }
static lv_t *top(void) { return &pg.lv[pg.depth - 1]; }
static const char *cell(int i, int k) { return pg.f[4 + 6 * i + k]; }

/* "\t" and s escaped appended to out (size bytes); 0 when it doesn't fit. */
static int put(char *out, unsigned size, const char *s) {
    unsigned k = strlen(out);
    return k + 1 < size && (out[k] = '\t', spot_escape(s, out + k + 1, size - k - 1) >= 0);
}

static void fetch(lv_t *l) {
    pg.act = 0;
    pg.paging = 0;
    req_send(app()->browse, l->op, l->args);
    l->id = req.id;
}

/* Row i's action op sent with its context, the one in flight dropped (its row's own text back).
 * go() sends none while one is in flight: the app has usually done it, and a second would drop its
 * reply, so a toggle pressed twice undid itself. */
static void busy_row(int on);
static void send_act(int i, const char *op, const char *ctx, int act) {
    char a[sizeof top()->args] = "";
    if (ctx[0] && !put(a, sizeof a, ctx)) return;
    busy_row(0);
    tk_snprintf(pg.aop, sizeof pg.aop, "%s", op);
    req_send(app()->browse, op, a);
    pg.act = act, pg.arow = i, pg.busy = 0, pg.acted = time_now_ms();
}
static void action(int i, const char *op, const char *ctx) { send_act(i, op, ctx, A_ACTION); }

static int clicked(void *ctx, void *event);

/* Whether row i opens a page (go()): a list (a sort row left as a row is one), the phone remote's
 * QR page or a folder on the card. Its row has a chevron. */
static int opens(int i) {
    const char *kind = cell(i, 0), *uri = cell(i, 1);
    return !strcmp(kind, "list") || !strcmp(kind, "sort") || !strcmp(kind, "remote") ||
           (!strcmp(kind, "local") && *uri && uri[strlen(uri) - 1] == '/');
}

/* Whether the app may be running, so a Stop row can end it; never for one whose q2app says stop=0,
 * whose error page ends in Cancel instead. */
static int stoppable(void) {
    return !app()->nostop && (app()->launched || app()->restart || !access(app()->pid, 0));
}

static void stop_row(int i) {
    char t[48];
    tk_snprintf(t, sizeof t, "Stop %s", app()->name);
    page_row_detail(pg.view, i, t, 0, clicked);
}

/* A progress row's bar's permille, clamped to 0 to 1000 for addon_paint. */
static void set_pml(void *bar, long permille) {
    widget_set_prop_int(bar, "_pml", permille < 0 ? 0 : permille > 1000 ? 1000 : (int)permille);
}

/* A progress row: page_row_detail's item re-dressed, its title and subtitle in the caption's box
 * (the theme's: an empty detail has moved the label), left and right on one line, over a bar
 * centred in the detail's, which addon_paint fills from the left: its _pml prop, permille, clamped
 * to 0 to 1000 (set_pml); strtol saturates what overflows, which atoi leaves undefined. */
static void progress_row(void *item, long permille) {
    const theme_t *T = theme();
    void *t = widget_get_child(item, 0), *s = widget_get_child(item, 1);
    int x = I(t, W_X), w = I(t, W_W), h = T->page_cap_h;
    int y = T->page_cap_y + (I(item, W_H) - page_h(64)) / 2; /* a cover list's rows are taller */
    int dx = I(s, W_X), dy = I(s, W_Y), dw = I(s, W_W), dh = I(s, W_H);
    widget_move_resize(t, x, y, w * 3 / 5, h);
    widget_use_style(s, "s_scrlabel_white20r"); /* the style drops the detail's size and grey */
    widget_set_prop_int(s, "style:normal:font_size", 16);
    widget_set_prop_int(s, "style:normal:text_color", (int)SPOT_GREY);
    widget_set_prop_int(s, "_font", -1); /* and its face */
    theme_font(s, ROLE_META, FONT_ROW);
    widget_move_resize(s, x + w * 3 / 5, y, w - w * 3 / 5, h);
    int bh = PROGRESS_H;
    void *bar = widget_factory_create_widget(widget_factory(), "view", item, dx, dy + (dh - bh) / 2,
                                             dw, bh);
    set_pml(bar, permille);
}

/* Row i's cover's file:// URL written to out (ART_URL bytes); no file is touched. */
static void art_url(int i, char *out) {
    unsigned key = hash_bytes(FNV_SEED, (const unsigned char *)cell(i, 1), strlen(cell(i, 1)));
    key = hash_bytes(key, (const unsigned char *)"\t", 1);
    key = hash_bytes(key, (const unsigned char *)cell(i, 4), strlen(cell(i, 4)));
    tk_snprintf(out, ART_URL, "file:///tmp/q2-%s.art/%08x.jpg", app()->id, key);
}

/* Row i's cover, its URL in out (art_url); whether the file is there, with a known size of no more
 * than ART_MAX px either way. */
static int art_file(int i, char *out) {
    int w = 0, h = 0;
    art_url(i, out);
    return picture_size(out + 7, &w, &h) && w && h && w <= ART_MAX && h <= ART_MAX;
}

/* Rows from.. to's covers, decoded or not, let go of the image manager. */
static void art_unload(int from, int to) {
    char url[ART_URL];
    for (; from < to; ++from) {
        art_url(from, url);
        image_manager_unload_bitmap_by_name(image_manager(), url);
    }
}

/* The range (art_window) unloaded and emptied; pg.f still names its rows. */
static void art_forget(void) {
    art_unload(pg.art_lo, pg.art_hi);
    pg.art_lo = pg.art_hi = 0;
}

/* The shown reply let go, and any page it waits for; its covers leave the image manager while
 * pg.f still names their rows, whether or not the files are still there. The rows painted since
 * the last tick count too. */
static void art_window(void);
static void album_covers_drop(void);
static void keep_sel(void);
static void drop(void) {
    art_window();
    art_forget();
    if (pg.depth && (top()->lay == LAY_ALBUMS || top()->lay == LAY_TOP))
        album_covers_drop(); /* the Albums or a songs page's covers */
    while (pg.pages) free(pg.buf[--pg.pages]);
    pg.paging = 0;
}

/* The list's own arguments in a (n bytes), and cursor c as Q2SPOT/1's last, none for an empty c (a
 * page row's for the list's start); 0 when they don't fit. */
static int cursor_args(const lv_t *l, char *a, unsigned n, const char *c) {
    tk_snprintf(a, n, "%.*s", (int)l->base, l->args);
    return !c[0] || put(a, n, c);
}

/* The level's next page asked for, as More… would, its reply appended by got(). */
static void page_next(lv_t *l) {
    char a[sizeof l->args] = "";
    pg.act = 0;
    if ((pg.paging = cursor_args(l, a, sizeof a, pg.f[3])))
        req_send(app()->browse, l->op, a), l->id = req.id;
}

/* How many of the first count rows have covers, so the list is drawn with them; 0 too with a
 * progress row among them, whose bar is page_row_detail's. */
static int art_rows(int count) {
    char url[ART_URL];
    int art = 0;
    for (int i = 0; i < count; ++i) {
        if (!strcmp(cell(i, 0), "progress")) return 0;
        art += art_file(i, url);
    }
    return art;
}

static int row_h(const lv_t *l) { return l->art ? ART_ROW_H : page_h(64); }

/* A stock Tidal list row, page_row_detail's item moved over: the cover (url, or room for one) at
 * the theme's left, the theme's label boxes beside it, centred in the taller row, an empty detail
 * centring the caption, and the chevron when it opens a page. The image goes before row_into, whose
 * chevron stays the last child, and is sized after it. */
static void *art_row(int index, const char *caption, const char *detail, const char *url,
                     int into) {
    int x = page_x(), tx = x + ART_PX + 12, w = 375 - x - tx, dy = (ART_ROW_H - page_h(64)) / 2;
    void *item = page_row_detail(pg.view, index, caption, detail, clicked), *img = 0;
    void *t = widget_get_child(item, 0), *s = widget_get_child(item, 1);
    widget_move_resize(item, 0, index * ART_ROW_H, 375, ART_ROW_H);
    widget_move_resize(t, tx, *detail ? I(t, W_Y) + dy : 0, w, *detail ? I(t, W_H) : ART_ROW_H);
    widget_move_resize(s, tx, I(s, W_Y) + dy, w, I(s, W_H));
    if (url) {
        img = image_create(item, x, (ART_ROW_H - ART_PX) / 2, ART_PX, ART_PX);
        image_set_draw_type(img, 4); /* scaled to fit, as Cover Flow's covers */
        image_manager_unload_bitmap_by_name(image_manager(), url); /* a file written since */
        image_base_set_image(img, url);
    }
    if (into) row_into(item, ART_ROW_H);
    if (img) widget_move_resize(img, x, (ART_ROW_H - ART_PX) / 2, ART_PX, ART_PX);
    return item;
}

/* view's rows one under the other, each at its own height (a one-line row's or a two-line one's),
 * and the view's content their sum: page_row_detail places row i at i rows of its own height, and
 * page_list's item_height would make every row its height, so the list keeps the rows' own. */
static void stack_rows(void *view) {
    int y = 0;
    widget_set_prop_int(P(view, W_PARENT), "item_height", 0);
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i);
        widget_move_resize(item, I(item, W_X), y, I(item, W_W), I(item, W_H));
        y += I(item, W_H);
    }
    widget_set_prop_int(view, "virtual_h", y);
}

/* Row i of the level's list; i == l->n: the trailing row, Loading… while a page is on its way (a
 * press does nothing), else More…. Without art a row with no subtitle is a one-line row, as Home's
 * (docs/oled-design.md#list-row), and only one with a subtitle two lines (stack_rows places them). */
static void row(lv_t *l, int i) {
    if (i == l->n) {
        if (l->art)
            art_row(i, pg.paging ? "Loading…" : "More…", "", 0, 0);
        else
            page_row_detail(pg.view, i, pg.paging ? "Loading…" : "More…", 0, clicked);
        return;
    }
    void *item;
    const char *sub = cell(i, 3);
    if (l->art) {
        char url[ART_URL];
        item = art_row(i, cell(i, 2), sub, art_file(i, url) ? url : 0, opens(i));
    } else /* a progress row keeps its detail label, empty or not: progress_row re-dresses both */
        item = (opens(i) ? page_row_detail_into : page_row_detail)(
            pg.view, i, cell(i, 2), *sub || !strcmp(cell(i, 0), "progress") ? sub : 0, clicked);
    if (!strcmp(cell(i, 0), "progress")) progress_row(item, strtol(cell(i, 5), 0, 10));
}

/* Stock never lets a row's decoded cover go (nothing calls image_manager_unload_unused), so one a
 * user scrolled past would stay until the list closes: at most ART_KEEP rows' stay decoded around
 * the list's real scroll position (its yoffset, so wheel, touch and fling alike), others unloaded
 * here, from show(), drop() and the tick; [pg.art_lo, pg.art_hi) names the rows that may be decoded,
 * first widened to all a move crossed since the last tick, so none is left behind, and the cost is
 * the rows crossing, not the list's length. An image draws by name and keeps no bitmap between
 * paints (internals.md Covers), so a row scrolled back loads again and nothing dangles.
 * ponytail: at most ART_KEEP covers (about 1.5 MB at ART_MAX px) plus what a fling paints in one
 * 250 ms tick; a cover scrolled back decodes again on the UI thread, a decode thread if a long
 * fling ever shows its fill-in. */
static void art_window(void) {
    const lv_t *l = top();
    if (!pg.view || !l->art || l->state != K_READY) return;
    int rh = row_h(l), y = widget_get_prop_int(pg.view, "yoffset", 0);
    if (y < 0) y = 0; /* a drag past the top: stock's rubber band reads to minus the view's height */
    /* a row either side of the view, which stock paints when it's within 4 px of the edge */
    int first = y / rh ? y / rh - 1 : 0, last = (y + I(pg.view, W_H) + rh - 1) / rh + 1;
    if (last > l->n) last = l->n;
    if (first > last) first = last;
    int lo = (first + last - ART_KEEP) / 2;
    if (lo > l->n - ART_KEEP) lo = l->n - ART_KEEP;
    if (lo < 0) lo = 0;
    int hi = lo + ART_KEEP < l->n ? lo + ART_KEEP : l->n;
    int a = pg.art_lo < pg.art_hi && pg.art_lo < first ? pg.art_lo : first;
    int b = pg.art_lo < pg.art_hi && pg.art_hi > last ? pg.art_hi : last;
    art_unload(a, b < lo ? b : lo);
    art_unload(a > hi ? a : hi, b);
    pg.art_lo = a > lo ? a : lo;
    pg.art_hi = b < hi ? b : hi;
}

static void show(void);

/* The first of reply f's *n rows out, the rest up one, if it is of this kind and not the sole row. */
static int take_row(char **f, int *n, const char *kind) {
    if (*n < 2 || strcmp(f[4], kind)) return 0;
    for (int i = 4; i < 4 + 6 * (*n - 1); ++i) f[i] = f[i + 6];
    --*n;
    return 1;
}

/* A first row of kind search leaves the list for a search icon at the title bar's right, as
 * Tidal's navbar has (s_img_search), where the theme has a title bar; under one without (the name
 * in the status bar) the row stays, as does a sole row, which would leave an empty list. The row's
 * title and subtitle aren't shown: the box's tips are the app's. Whether it did, from reply f's *n
 * rows. */
static int take_find(char **f, int *n) {
    return theme()->page_title_h && take_row(f, n, "search");
}

/* After a taken search, a first row of kind sort leaves the list too, for a sort icon left of the
 * search icon, its fields kept (pg.sort) for the tap, which opens it as a list row's press does.
 * A sole row stays, as a sole search. Whether it did, from reply f's *n rows. */
static int take_sort(char **f, int *n) {
    memcpy(pg.sort, f + 4, sizeof pg.sort); /* read only where l->sort says it was taken */
    return take_row(f, n, "sort");
}

static int bar_tapped(void *ctx, void *event);

/* A 50 px image at x in the title bar, h high from y, in stock style s: clickable (an image sends
 * no click unless it is, as Tidal's img_search), a tap queueing code. */
static void bar_icon(int x, int y, int h, const char *s, int code) {
    void *icon = image_create(pg.page, x, y, 50, h);
    widget_use_style(icon, s);
    widget_set_prop_int(icon, "clickable", 1);
    widget_on(icon, EVT_CLICK, bar_tapped, (void *)(long)code);
}

/* The search icon at the title bar's right, and the sort icon left of it when the level has one,
 * both in the same place on every such page. The title stays centred on the screen, in a box that
 * ends where the first icon's ink starts (the glass's box alone, 73 to 302, as before), so a longer
 * one scrolls clear of them.
 * ponytail: taps only, so the wheel and buttons don't reach search or sort on such a list; a key
 * bound to G_FIND or G_SORT if that is ever asked for. */
#define FIND_X 302 /* the rows' list_into chevron's box (row_into), so the icon lines up with them */
/* The sort icon's box: midway, by ink, between a centred 150 px title (a section's "Newly Added
 * 1/5" at 20 px, which ends at 262) and the glass. Stock's 50 px images (V1.36 images/xx, alpha
 * > 0): navbar_search's ink is columns 15 to 35, so 317 to 337 at FIND_X; song_order's
 * (s_img_order_navbar, the Sort of stock's Songs page) is columns 15 to 34, so 280 to 299 here:
 * 17 px after such a title's end and 17 px before the glass. */
#define SORT_X 265
#define TITLE_END (SORT_X + 15) /* 280: the sort's ink */
static void find_icon(int sort) {
    void *title = widget_get_child(pg.page, 0);
    int h = I(title, W_H), y = I(title, W_Y), end = sort ? TITLE_END : FIND_X;
    /* centred: 95 to 280, or 73 to 302 */
    widget_move_resize(title, 375 - end, y, 2 * end - 375, h);
    /* stock's sort style draws its image from the box's top left (draw type 0), where the glass's
     * centres it: a 50 px box centred on the title puts both at the same height. Made first: its
     * box overlaps the glass's (302 to 315), and a tap goes to the last child made, so the glass
     * keeps all of its own */
    if (sort) bar_icon(SORT_X, y + (h - 50) / 2, 50, "s_img_order_navbar", G_SORT);
    bar_icon(FIND_X, y, h, "s_img_search", G_FIND);
}

/* Rows from.. added to the shown list in place of its trailing row, the rest left as they are;
 * ringnav_select gives ringnav the new count, so it keeps the selection and the scroll. A reload's
 * row (l->sel) is selected once its page is there, unless the user moved off the trailing row; once
 * reached or left it is the selection, so no later page moves it.
 * ponytail: the new count makes ringnav drop a spin's speed-up and big letter (load) at each page;
 * a ringnav call that grows a list in place would keep them while pages arrive. */
static void append(lv_t *l, int from) {
    if (!pg.view) return; /* the search box over it: show() draws them all when it closes */
    if (l->lay) { /* an artist's page and its pages count and order the rows anew */
        keep_sel();
        show();
        return;
    }
    int cur = widget_get_prop_int(pg.view, "_ringnav_index", 0), n = l->n + (pg.f[3][0] != 0);
    if (cur == from && l->sel > from) cur = l->sel;
    if (n && cur >= n) cur = n - 1; /* past an empty last page: its last row */
    if (cur < n - 1 || !pg.paging || l->sel < cur) l->sel = cur; /* unless still on its way */
    if ((from + 1) * row_h(l) <=
        I(pg.view, W_H)) { /* page_list shrank a short first page: rebuilt */
        show();
        return;
    }
    widget_destroy(widget_get_child(pg.view, (unsigned)from));
    for (int i = from; i < n; ++i) row(l, i);
    if (l->art)
        widget_set_prop_int(pg.view, "virtual_h", n * row_h(l));
    else
        stack_rows(pg.view);
    ringnav_select(pg.view, cur, n);
}

/* A live level's new reply g (n rows) has the shown list's rows: as many, on one page, each of the
 * same kind, uri and context (so a press queued on one still does what it did) and with a detail
 * where it had one, More… shown or not as before, and as many covers. The shown reply's kinds, uris
 * and contexts are the new one's by then, so art_rows' answer is the new one's.
 * ponytail: covers are counted, not compared, so a cover swapped for another in one reply stays
 * until the next rebuild; a key per row if apps do that. */
static int same_rows(const lv_t *l, char **g, int n) {
    if (!pg.view || n != l->n || pg.pages != 1 || !g[3][0] != !pg.f[3][0]) return 0;
    for (int i = 0; i < n; ++i) {
        char **r = g + 4 + 6 * i;
        if (strcmp(r[0], cell(i, 0)) || strcmp(r[1], cell(i, 1)) || strcmp(r[4], cell(i, 4)) ||
            !r[3][0] != !cell(i, 3)[0])
            return 0;
    }
    return art_rows(n) == l->art;
}

/* Row item's label w, its title (k 2) or detail (3), says s, from its left in the face it needs;
 * a new title's marquee starts again at the next paint. */
static void retext(void *item, void *w, unsigned k, const char *s) {
    widget_set_text_utf8(w, s);
    theme_font(w, k == 2 ? ROLE_TALL : ROLE_META, FONT_ROW);
    widget_set_prop_int(w, "xoffset", 0);
    if (k == 2) marq_restart(item);
}

/* The shown rows' texts and progress bars written from the level's reply in place: a refresh that
 * only moves a percentage leaves the other rows, the selection and a long title's marquee as they
 * are, where a rebuild would start them over. A row's labels are its title then its detail, in
 * row()'s roles however it was dressed; one is set only when its text changed, read as UTF-8 (its
 * "text" prop is a wide string, which widget_get_prop_str doesn't return). A changed text starts
 * from its left, in the face it needs, and the selected row's new title has its marquee again at
 * the next paint (paint_selection), for its own width; a changed detail leaves the title's alone. */
static void relabel(const lv_t *l) {
    for (int i = 0; i < l->n; ++i) {
        void *item = widget_get_child(pg.view, (unsigned)i);
        for (unsigned c = 0, k = 2, m = widget_count_children(item); c < m; ++c) {
            void *w = widget_get_child(item, c);
            if (!tk_strcmp(widget_get_type(w), "hscroll_label")) {
                if (text_differs(w, cell(i, k))) retext(item, w, k, cell(i, k));
                ++k;
            } else if (widget_get_prop_int(w, "_pml", -1) >= 0) { /* a progress row's bar */
                set_pml(w, strtol(cell(i, 5), 0, 10));
                widget_invalidate_force(w, 0);
            }
        }
    }
}

/* The action's row says Working… (on) or its own text again, as the App Store's rows say
 * Installing…: its detail, or its title when it has none (an empty detail's label is under the
 * centred title). Its first two children are its labels (page_row_detail, art_row). */
static void busy_row(int on) {
    lv_t *l = top();
    int i = pg.arow;
    if (on == pg.busy || !pg.view || l->state != K_READY || i >= l->n || l->lay) return;
    int k = 2 + !!cell(i, 3)[0];
    void *item = widget_get_child(pg.view, (unsigned)i);
    retext(item, widget_get_child(item, (unsigned)k - 2), (unsigned)k,
           on ? "Working…" : cell(i, k));
    pg.busy = on;
}

/* An artist's page (docs/oled-design.md#artist-page) from the app's reply. A reply is an artist's when it has Shuffle
 * and Radio actions on an "artist:" context, as q2-subsonic's artist page does (Go to Artist's,
 * and its Artists rows'). Its other rows go where the design puts them: the album rows (list,
 * album) under an Albums row, Top Songs (list, top) as a row of its own, Add to Sync, Quality and
 * Sync Now in the Play/Pause hold menu (artist_hold), Play nowhere. The pages Top Songs and Albums
 * open are levels of their own drawn as cover rows (LAY_TOP, LAY_ALBUMS): Albums asks the artist's
 * page again and keeps its album rows, newest first.
 * An app that sees "artist" in the caps sends more (docs/librespot.md, the artist rows): an info
 * row "about" whose title is the header's counts and whose subtitle its genre; list rows
 * "singles", "similar" and "songs" (Singles & EPs, Similar Artists, All Songs), each only when it
 * has something, the first and last with their count leading their subtitle, opening album rows
 * (LAY_ALBUMS), artist rows (LAY_SIMILAR) and song rows (LAY_TOP); and song rows whose subtitle
 * is "<album> · <length> · <status>" and whose context ends in "album:<id>". What an older app
 * does not send is left out: the header then counts the album rows, Singles & EPs, Similar
 * Artists and All Songs are not offered, and a song row shows its title alone.
 * ponytail: counted and listed from this reply's rows: an artist whose rows run past one reply
 * (about 45 albums) counts and lists those of the pages that arrived (pages: page_next) or of its
 * first window only (window: its Previous and Next page rows are not drawn); the Albums page
 * keeping page rows would lift it. */
#define AR_ALBUMS (1 << 20) /* the Albums row's click context: no reply row's index */
#define AR_HOLD 4           /* the hold menu's rows at most */
static int ar_hold_row[AR_HOLD] __attribute__((section(".scratch")));
static void later(int next, int arg);

static int is_album(int i) {
    return !strcmp(cell(i, 0), "list") &&
           (!strcmp(cell(i, 1), "album") || !strcmp(cell(i, 1), "albumart"));
}
static int is_artist_act(int i, const char *op) {
    return !strcmp(cell(i, 0), "action") && !strcmp(cell(i, 1), op) &&
           !strncmp(cell(i, 4), "artist:", 7);
}
/* An album row's year: its subtitle's first four digits, else 0 */
static int album_year(int i, char *out) {
    const char *s = cell(i, 3);
    int y = 0;
    for (int k = 0; k < 4; ++k)
        if (s[k] < '0' || s[k] > '9') return out[0] = 0, 0;
    y = (s[0] - '0') * 1000 + (s[1] - '0') * 100 + (s[2] - '0') * 10 + (s[3] - '0');
    if (s[4] && s[4] != ' ') return out[0] = 0, 0;
    memcpy(out, s, 4), out[4] = 0;
    return y;
}
/* Row i's album id: its context's "album:<id>", at its start (an album row) or as its last word
 * (a song row's, "artist" in the caps); 0 when it has none. */
static const char *row_album(int i) {
    const char *c = cell(i, 4), *k = strstr(c, " album:");
    if (strncmp(c, "album:", 6)) c = k ? k + 1 : 0;
    if (!c || !c[6]) return 0;
    for (const char *e = c + 6; *e; ++e)
        if (*e == ' ' || *e == '/') return 0; /* one word, a name, never a path */
    return c + 6;
}
/* Row i's album's cover's URL: the app's catalogue cover for its album id
 * (docs/server-library.md#covers), there on the card or not; 0 for a row with no album. */
static int album_url(int i, char *url, unsigned size) {
    const char *id = row_album(i);
    if (!id) return 0;
    tk_snprintf(url, size, "file:///mnt/mmc/.%s/covers/%s.jpg", app()->id, id);
    return 1;
}
/* The same, for a cover about to be drawn: 0 too when the file is not there. */
static int album_cover(int i, char *url, unsigned size) {
    return album_url(i, url, size) && !access(url + 7, 0);
}
/* Every row's cover let go by name, with no look for its file: unloading a name that was never
 * loaded does nothing. */
static void album_covers_drop(void) {
    char url[96];
    for (int i = 0; i < top()->n; ++i)
        if (album_url(i, url, sizeof url)) image_manager_unload_bitmap_by_name(image_manager(), url);
}
/* A song row's subtitle's parts (each "" when not sent), as an app with "artist" or "album"
 * writes it, "<album or artist> · <length> · <status>": the length is the first part of digits
 * and colons with a colon in it, the album all before it (or the first part, with none). */
static void sub_parts(int i, char *album, unsigned an, char *len, unsigned ln) {
    static const char dot[] = " \xc2\xb7 ";
    const char *s = cell(i, 3), *p = s, *e;
    album[0] = len[0] = 0;
    for (;; p = e + sizeof dot - 1) {
        unsigned n = (e = strstr(p, dot)) ? (unsigned)(e - p) : strlen(p), k = 0, colon = 0;
        for (; k < n && ((p[k] >= '0' && p[k] <= '9') || p[k] == ':'); ++k) colon |= p[k] == ':';
        if (n && n < 9 && k == n && colon && p[0] != ':' && p[n - 1] != ':') {
            tk_snprintf(len, ln, "%.*s", (int)n, p);
            if (p > s) tk_snprintf(album, an, "%.*s", (int)(p - s - (sizeof dot - 1)), s);
            return;
        }
        if (!e) break;
    }
    e = strstr(s, dot);
    tk_snprintf(album, an, "%.*s", e ? (int)(e - s) : (int)strlen(s), s);
}
/* A song row's album and length, as sub_parts reads them. A row with no album id is an older
 * app's, whose subtitle names the artist: neither is read. */
static void song_parts(int i, char *album, unsigned an, char *len, unsigned ln) {
    album[0] = len[0] = 0;
    if (row_album(i)) sub_parts(i, album, an, len, ln);
}
/* The number a row's subtitle starts with ("12 songs"), as text in out; "" when none. */
static const char *lead_count(int i, char *out) {
    const char *s = cell(i, 3);
    unsigned k = 0;
    while (k < 7 && s[k] >= '0' && s[k] <= '9') out[k] = s[k], ++k;
    out[k] = 0;
    return out;
}
/* The level a drill-down row opens is drawn as: its list op's layout, 0 for none. */
static int drill_lay(const char *uri) {
    return !strcmp(uri, "top") || !strcmp(uri, "songs") ? LAY_TOP
           : !strcmp(uri, "singles")                    ? LAY_ALBUMS
           : !strcmp(uri, "similar")                    ? LAY_SIMILAR
                                                        : 0;
}

/* An album's page (docs/oled-design.md#album-page) from the app's reply: a reply is an album's when it has a Play action on "album:<id> 0", a Shuffle on
 * "album:<id>" and an info row "about", as q2-subsonic's album page has with "album" in the caps
 * (docs/librespot.md#album-rows). The header shows the level's title (the album), the about row's
 * title (its artist) and subtitle ("2015 · 18 songs · 1 hr 12 min") and the album's catalogue
 * cover (album_cover); Play and Shuffle are the pair, the page opening on Play as an artist's on
 * Shuffle; then the song rows as its tracks, numbered in the reply's order, each with the length
 * its subtitle carries ("<artist> · <length> · <status>"). Add to Sync, Quality and Sync Now are
 * the hold menu's (artist_hold). How many rows its list has, 0 when the reply is not one.
 * ponytail: tracks are numbered by place, so a second disc counts on from the first; the app
 * sending each song's track number would lift it. */
static int is_album_act(int i, const char *op) {
    return !strcmp(cell(i, 0), "action") && !strcmp(cell(i, 1), op) &&
           !strncmp(cell(i, 4), "album:", 6);
}
static int is_song(int i) {
    return !strcmp(cell(i, 0), "action") && !strcmp(cell(i, 1), "play") &&
           !strncmp(cell(i, 4), "song:", 5);
}
static int album_view(lv_t *l) {
    int play = -1, shuffle = -1, about = -1, n = 0;
    char url[96], artist[128], len[12];
    for (int i = 0; i < l->n; ++i) {
        if (is_album_act(i, "play")) play = i;
        if (is_album_act(i, "shuffle")) shuffle = i;
        if (!strcmp(cell(i, 0), "info") && !strcmp(cell(i, 1), "about")) about = i;
        n += is_song(i);
    }
    if (play < 0 || shuffle < 0 || about < 0 || !n) return 0;
    if (l->lay != LAY_ALBUM) l->lay = LAY_ALBUM, l->stop = 1; /* opens on Play */
    pg.view = page_list(pg.page, pg.page, 0, "", n, 48);
    drill_list(pg.view, n);
    for (int i = 0, k = 0; i < l->n; ++i)
        if (is_song(i)) {
            sub_parts(i, artist, sizeof artist, len, sizeof len);
            track_row(pg.view, k, k + 1, cell(i, 2), len, clicked, i), ++k;
        }
    static const char *const labels[2] = { "Play", "Shuffle" }, *const icons[2] = { "play",
                                                                                    "shuffle" };
    int ctx[2] = { play, shuffle };
    void *head = hero_open(pg.page, l->title, cell(about, 2), cell(about, 3));
    hero_cover(head, album_cover(shuffle, url, sizeof url) ? url : (const char *)0);
    pair_open(head, pg.view, labels, icons, ctx, clicked, &l->stop);
    return n;
}

/* The level's rows as an artist's page, its Albums (or Singles & EPs) page, a songs page (Top
 * Songs, All Songs), Similar Artists or an album's page (album_view); how many rows its list has,
 * 0 when the reply is not one (the level is then drawn plain from now on). */
static int artist_view(lv_t *l) {
    int n = 0;
    char url[96], year[8];
    if ((!l->lay || l->lay == LAY_ALBUM) && (n = album_view(l))) return n;
    if (l->lay == LAY_ALBUMS) {
        int k = 0, *order = calloc((unsigned)l->n + 1, sizeof *order);
        for (int i = 0; order && i < l->n; ++i)
            if (is_album(i)) { /* newest first, the undated after, each in the app's order */
                int y = album_year(i, year), j = k++;
                while (j && album_year(order[j - 1], url) < y) order[j] = order[j - 1], --j;
                order[j] = i;
            }
        if (!k) return free(order), l->lay = LAY_PLAIN, 0;
        pg.view = page_list(pg.page, pg.page, 0, l->title, k, 40);
        for (int j = 0; j < k; ++j) {
            int i = order[j];
            album_year(i, year);
            cover_row(pg.view, j, 40, 32, 0, cell(i, 2), year,
                      album_cover(i, url, sizeof url) ? url : (const char *)0, clicked, i);
        }
        free(order);
        return k;
    }
    if (l->lay == LAY_TOP) {
        char album[128], len[12];
        for (int i = 0; i < l->n; ++i)
            if (strcmp(cell(i, 0), "action") || strcmp(cell(i, 1), "play"))
                return l->lay = LAY_PLAIN, 0;
        if (!l->n) return l->lay = LAY_PLAIN, 0;
        pg.view = page_list(pg.page, pg.page, 0, l->title, l->n, 36);
        for (int i = 0; i < l->n; ++i) {
            song_parts(i, album, sizeof album, len, sizeof len);
            cover_after(cover_row(pg.view, i, 36, 28, AR_SONG_PX, cell(i, 2), len,
                                  album_cover(i, url, sizeof url) ? url : (const char *)0, clicked,
                                  i),
                        album);
        }
        return l->n;
    }
    if (l->lay == LAY_SIMILAR) {
        for (int i = 0; i < l->n; ++i)
            if (strcmp(cell(i, 0), "list") || strcmp(cell(i, 1), "artist"))
                return l->lay = LAY_PLAIN, 0;
        if (!l->n) return l->lay = LAY_PLAIN, 0;
        pg.view = page_list(pg.page, pg.page, 0, l->title, l->n, 40);
        for (int i = 0; i < l->n; ++i)
            cover_mono(cover_row(pg.view, i, 40, 32, 0, cell(i, 2), "", 0, clicked, i));
        return l->n;
    }
    int shuffle = -1, radio = -1, about = -1, albums = 0, drill[5] = { -1, -1, -1, -1, -1 };
    static const char *const ops[5] = { "top", "", "singles", "similar", "songs" };
    for (int i = 0; i < l->n; ++i) {
        if (is_artist_act(i, "shuffle")) shuffle = i;
        if (is_artist_act(i, "radio")) radio = i;
        if (!strcmp(cell(i, 0), "info") && !strcmp(cell(i, 1), "about")) about = i;
        for (int k = 0; k < 5 && !strcmp(cell(i, 0), "list"); ++k)
            if (*ops[k] && !strcmp(cell(i, 1), ops[k])) drill[k] = i;
        albums += is_album(i);
    }
    if (shuffle < 0 || radio < 0) return l->lay = LAY_PLAIN, 0;
    if (l->lay != LAY_ARTIST) l->lay = LAY_ARTIST, l->stop = 1; /* opens on Shuffle */
    if (albums) drill[1] = AR_ALBUMS;
    for (int k = 0; k < 5; ++k) n += drill[k] >= 0;
    pg.view = page_list(pg.page, pg.page, 0, "", n, 48);
    int h = drill_list(pg.view, n), r = 0;
    for (int k = 0; k < 5; ++k) { /* Top Songs, Albums, Singles & EPs, Similar Artists, All Songs */
        int i = drill[k];
        if (i < 0) continue;
        void *item = page_row_detail_into(pg.view, i, k == 1 ? "Albums" : cell(i, 2), 0, clicked);
        widget_move_resize(item, 0, r++ * h, 375, h);
        if (k == 1) tk_snprintf(year, sizeof year, "%d", albums);
        if (k == 1 || k == 2 || k == 4) drill_count(item, k == 1 ? year : lead_count(i, year));
    }
    char sub[24];
    tk_snprintf(sub, sizeof sub, albums == 1 ? "1 album" : "%d albums", albums);
    static const char *const labels[2] = { "Shuffle", "Radio" }, *const icons[2] = { "shuffle",
                                                                                     "radio" };
    int ctx[2] = { shuffle, radio };
    pair_open(hero_open(pg.page, l->title,
                        about >= 0 ? cell(about, 2)
                        : albums   ? sub
                                   : "",
                        about >= 0 ? cell(about, 3) : ""),
              pg.view, labels, icons, ctx, clicked, &l->stop);
    return n;
}

/* Play/Pause held on an artist's or album's page: the menu sheet of its sync rows (Add to Sync
 * or Remove from Sync, Quality, Sync Now), each doing what its row did. */
static void artist_pick(int k) {
    lv_t *l = top();
    int i = k >= 0 && k < AR_HOLD ? ar_hold_row[k] : -1;
    if (!pg.page || (l->lay != LAY_ARTIST && l->lay != LAY_ALBUM) || l->state != K_READY || i < 0 ||
        i >= l->n)
        return;
    later(G_ROW, i);
}
static int artist_hold(lv_t *l) {
    const char *names[AR_HOLD], *icons[AR_HOLD];
    int n = 0;
    for (int i = 0; i < l->n && n < AR_HOLD; ++i) {
        const char *kind = cell(i, 0), *uri = cell(i, 1);
        if (!strcmp(kind, "action") && !strcmp(uri, "toggle") &&
            (!strncmp(cell(i, 4), "artist:", 7) || !strncmp(cell(i, 4), "album:", 6)))
            icons[n] = !strncmp(cell(i, 2), "Remove", 6) ? "trash" : "cloud-arrow-down";
        else if (!strcmp(kind, "action") && !strcmp(uri, "quality"))
            icons[n] = "sliders-horizontal";
        else if (!strcmp(kind, "list") && !strcmp(uri, "sync"))
            icons[n] = "download-simple";
        else
            continue;
        names[n] = cell(i, 2);
        ar_hold_row[n++] = i;
    }
    if (n) return modal_actions(names, icons, n, artist_pick);
    for (int i = 0; i < l->n; ++i) /* Included via Entire Library: nothing to press */
        if (!strcmp(cell(i, 0), "info") && strcmp(cell(i, 1), "about") && i < 2)
            return toast(cell(i, 2)), 1;
    return 0;
}

/* A row's own menu ("holdmenu" in the caps, docs/internals.md#card-apps): a row whose position is
 * the op "hold" (q2-subsonic's song rows) has it sent with the row's context when Play/Pause is
 * held on the row (addon_hold), or Centre or a finger (addon_row_hold, from hold_menu.c's song row
 * hold), and the reply's action rows are its menu sheet, headed by the row's title and its
 * subtitle's first part (sub_parts: an album's artist, a song list's album). A row's symbol is its
 * op's: next and last as the hold menu's Play Next and Play Last, toggle as its Sync Offline
 * (cloud-slash for a row that takes the item out, "Stop ..."). A pick sends that row's op with its
 * context as an action, as a press of the row would, the reply toasted (got). A reply with no rows
 * is an action's. */
static struct {
    char *buf;                  /* the reply its rows point into */
    char *f[4 + 6 * HOLD_ROWS]; /* the reply cut (spot_reply) */
    int at[HOLD_ROWS], row;     /* the menu's rows' reply rows; the held row of the level */
} hm __attribute__((section(".scratch")));

static const char *hm_cell(int i, int k) { return hm.f[4 + 6 * i + k]; }

static void hold_pick(int k) {
    lv_t *l = pg.page ? top() : 0;
    if (!l || !hm.buf || k < 0 || k >= HOLD_ROWS || l->state != K_READY || pg.act || pg.pend ||
        pg.hold)
        return;
    action(hm.row, hm_cell(hm.at[k], 1), hm_cell(hm.at[k], 4)); /* both copied into the request */
    free(hm.buf);
    hm.buf = 0;
}

static int hold_menu(char *reply, char **f, int n) {
    const char *names[HOLD_ROWS], *icons[HOLD_ROWS];
    int k = 0;
    free(hm.buf);
    hm.buf = 0;
    memcpy(hm.f, f, (unsigned)(4 + 6 * n) * sizeof *f);
    for (int i = 0; i < n; ++i) {
        const char *op = hm_cell(i, 1), *title = hm_cell(i, 2);
        if (strcmp(hm_cell(i, 0), "action") || !*op || !*title) continue;
        icons[k] = !strcmp(op, "next")   ? "arrow-bend-down-right"
                   : !strcmp(op, "last") ? "queue"
                   : !strcmp(op, "toggle")
                       ? (!strncmp(title, "Stop", 4) ? "cloud-slash" : "cloud-arrow-down")
                       : (const char *)0;
        names[k] = title, hm.at[k++] = i;
    }
    if (!k || pg.arow < 0 || pg.arow >= top()->n) return 0;
    char sub[128], len[12];
    sub_parts(pg.arow, sub, sizeof sub, len, sizeof len);
    modal_head(cell(pg.arow, 2), sub, (void *)0);
    if (!modal_actions(names, icons, k, hold_pick)) return 0;
    hm.buf = reply, hm.row = pg.arow;
    return 1;
}

/* Row i's hold op sent, its reply the menu (got); 1 when it went. Not while a request is out: a
 * level's load, or an action the app may still be doing. */
static int hold_send(int i) {
    if (req.running || req.done || pg.act || pg.pend || pg.hold || strcmp(cell(i, 5), HOLD_OP))
        return 0;
    send_act(i, HOLD_OP, cell(i, 4), A_HOLD);
    return pg.act == A_HOLD;
}

/* A reply with nothing in it (an empty artist or album: its info row, "No albums", and the sync
 * toggle over nothing) under the menu sheet: no rows, the house empty note instead
 * (docs/oled-design.md#empty-and-offline), the info row's title over its subtitle as the hint, and
 * the toggle left out, as an action over nothing (audit2 F1). A reply with a next page (a cursor) is
 * not nothing: its rows stay, the More… row after them. 1 when it is one. */
static int empty_view(lv_t *l) {
    int info = -1;
    if (l->lay || pg.f[3][0]) return 0;
    for (int i = 0; i < l->n; ++i) {
        if (!strcmp(cell(i, 0), "info") && strcmp(cell(i, 1), "about")) {
            if (info < 0) info = i;
        } else if (strcmp(cell(i, 0), "action") || strcmp(cell(i, 1), "toggle") ||
                   (strncmp(cell(i, 4), "artist:", 7) && strncmp(cell(i, 4), "album:", 6)))
            return 0;
    }
    if (info < 0) return 0;
    pg.view = page_list_or(pg.page, pg.page, l->title, cell(info, 2), 0, 48);
    feedback_empty(pg.page, 0, cell(info, 2), cell(info, 3)[0] ? cell(info, 3) : (const char *)0);
    return 1;
}

/* A status page (docs/oled-design.md#sync, the caps word "status"): a reply whose first row is an
 * info row "status" is drawn as one centred stack, the empty state's form (feedback.c empty_state):
 * the row's context the glyph (tools/art.py EMPTY_GLYPHS), its title the title, its subtitle the
 * lines under it ('\n' apart), its position, when a number, a progress bar of that permille under
 * the title; the reply's first action row the button, which Centre or a tap presses as that row,
 * and an info row "foot" the footer. q2-subsonic's Sync draws its states so, iOS's Software Update's
 * way (the user's, 2026-11-19, mock_sync_apple.py). 1 when the reply is one. */
static int status_btn __attribute__((section(".scratch")));
static int status_press(void *ctx, void *event) { return clicked((void *)(long)status_btn, event); }
static int status_view(lv_t *l) {
    if (!l->n || strcmp(cell(0, 0), "info") || strcmp(cell(0, 1), "status")) return 0;
    int button = -1, foot = -1;
    for (int i = 1; i < l->n; ++i) {
        if (button < 0 && !strcmp(cell(i, 0), "action")) button = i;
        if (foot < 0 && !strcmp(cell(i, 0), "info") && !strcmp(cell(i, 1), "foot")) foot = i;
    }
    const char *bar = cell(0, 5);
    empty_t e = { cell(0, 4), cell(0, 2), cell(0, 3), button >= 0 ? cell(button, 2) : (const char *)0,
                  foot >= 0 ? cell(foot, 2) : (const char *)0,
                  *bar >= '0' && *bar <= '9' ? atoi(bar) + 1 : 0 };
    status_btn = button;
    pg.view = empty_list(pg.page, pg.page, l->title, &e, button >= 0 ? status_press : 0);
    return 1;
}

/* The top level: its rows, or what it waits for. */
static void show(void) {
    lv_t *l = top();
    int n = 0;
    /* a queued click names a row of the old list, so any rebuild drops it. ponytail: a short first
     * page that append() rebuilds drops it too, row kept or not; match the row by identity in go()
     * if that is ever pressed for. */
    if (pg.next == G_ROW) stop_timer(&pg.go);
    if (l->state == K_LOAD && !((req.running || req.done) && !pg.act && req.id == l->id)) fetch(l);
    if (pg.hold && (l->state == K_LOAD || l->state == K_WAIT)) return; /* under the overlay */
    if (pg.hold && !pg.page) { /* its first answer: the page opens over the window it held */
        if (!page_make()) return; /* none to be had: the overlay stays until Return */
        load_over(0, 0);
    }
    pg.hold = 0;
    pg.view = pg.edit = 0;
    pg.busy = 0; /* new rows, their own texts: the tick says Working… again */
    if (l->state == K_LOAD || l->state == K_WAIT) {
        char t[48];
        tk_snprintf(t, sizeof t, "Starting %s…", app()->name);
        if (!l->since) l->since = time_now_ms() | 1;
        l->slow = (int)(time_now_ms() - l->since) >= SLOW_MS && stoppable(); /* since: now + 1 */
        void *v =
            page_list(pg.page, pg.page, 0, l->state == K_WAIT ? t : "Loading…", n = l->slow, 48);
        if (n) pg.view = v, stop_row(0);
    } else if (l->state == K_ERROR) {
        n = 1 + (app()->nostop || stoppable());
        pg.view = page_list(pg.page, pg.page, 0, l->note, n, 48);
        page_row_detail(pg.view, 0,
                        l->retry ? "Retry" : "Install it again in System settings → App Store", 0,
                        clicked);
        if (app()->nostop)
            page_row_detail(pg.view, 1, "Cancel", 0, clicked);
        else if (n > 1)
            stop_row(1);
        page_push(pg.view, pg.moved), pg.moved = 0;
    } else {
        /* ponytail: the first page decides, as pages appended later (append) keep this drawing's
         * choice and Back or an action reloads from the first page; a list rebuilt whole with all
         * its pages there (a short first page, the search box closing) counts them */
        art_forget(); /* the rows are built anew: nothing of theirs decoded yet */
        if (status_view(l)) {
            l->art = 0, n = 0;
        } else if (empty_view(l)) {
            l->art = 0, n = 0;
        } else if ((n = artist_view(l))) {
            l->art = 0;
        } else {
            l->art = art_rows(l->n);
            pg.view = page_list(pg.page, pg.page, 0, l->title, n = l->n + (pg.f[3][0] != 0),
                                l->art ? ART_ROW_H : 64);
            for (int i = 0; i < n; ++i) row(l, i);
            if (!l->art) stack_rows(pg.view);
            if (l->find) find_icon(l->sort);
        }
        page_push(pg.view, pg.moved), pg.moved = 0;
    }
    if (n) ringnav_select(pg.view, l->state == K_ERROR ? 0 : l->sel < n ? l->sel : n - 1, n);
    art_window();
}

/* The selected row kept, from a page showing the level's own rows (not an error or a wait's). */
static void keep_sel(void) {
    if (pg.view && top()->state == K_READY)
        top()->sel = widget_get_prop_int(pg.view, "_ringnav_index", 0);
}

static void kept_free(kept_t *k) {
    while (k->pages) free(k->buf[--k->pages]);
    free(k->f);
    k->f = 0;
}

/* next over the top level, which keeps its reply for Back (kept; pop shows it at once and asks
 * again) when it shows its rows, else lets it go (Back asks for it again, its Loading… page
 * meanwhile). ponytail: every level under the top keeps its pages, up to ADDON_DEPTH - 1 of them
 * of ADDON_PAGES replies each, where letting the lower ones go would bound it at one level's. */
static void enter(const lv_t *next) {
    keep_sel();
    if (pg.depth == ADDON_DEPTH) { /* the oldest past the base forgotten */
        kept_free(&pg.kept[1]);
        for (int i = 1; i < ADDON_DEPTH - 1; ++i)
            pg.lv[i] = pg.lv[i + 1], pg.kept[i] = pg.kept[i + 1];
        memset(&pg.kept[ADDON_DEPTH - 1], 0, sizeof *pg.kept);
        --pg.depth;
    }
    lv_t *l = top();
    kept_t *k = &pg.kept[pg.depth - 1];
    unsigned n = 4 + 6 * (unsigned)l->n;
    kept_free(k);
    if (l->state == K_READY && pg.pages && (k->f = calloc(n, sizeof *k->f))) {
        memcpy(k->f, pg.f, n * sizeof *k->f);
        memcpy(k->buf, pg.buf, sizeof k->buf);
        memcpy(k->sort, pg.sort, sizeof k->sort);
        k->pages = pg.pages, pg.pages = 0; /* drop() frees none of them */
    }
    drop();
    if (!k->f) l->n = 0, l->state = K_LOAD;
    pg.lv[pg.depth++] = *next;
    pg.moved = 1;
}

/* The loading overlay let go: its request's thread joined and its reply, if any, dropped, the page
 * as it was (Return, or a tap off the spinner). */
static void load_cancel(void) {
    pg.pend = 0;
    req_cancel();
    if (pg.hold) /* the level as it was, still shown */
        top()->state = pg.hold - 1, pg.hold = 0;
    if (pg.page) widget_invalidate_force(pg.page, 0);
}

/* The top level l asked for again in place under the loading overlay, the page as it shows until
 * its answer is there (show); with nothing shown, its Loading… page. */
static void hold_load(lv_t *l) {
    if (pg.view) pg.hold = (char)(1 + l->state), widget_invalidate_force(pg.page, 0);
    l->state = K_LOAD;
    show();
}

/* How the loading overlay's level is taken once its reply comes (load_take): entered over the top
 * one (push), or in the top's place (page_to), or as the first level's section psec (sec_open). */
enum { P_PUSH = 1, P_PLACE, P_SECTION };

/* Whether the top level shows its rows, so a level can be asked for under the overlay over them. */
static int stageable(void) { return top()->state == K_READY && pg.view; }

/* next asked for under the loading overlay, to be taken how (P_*) once its reply comes (got): op
 * and args sent (0: next's own); the staged level, its id the request's. */
static lv_t *stage(const lv_t *next, int how, const char *op, const char *args) {
    lv_t *w = &pg.lv[ADDON_DEPTH];
    if (pg.act) busy_row(0); /* the action's reply dropped by this request: its row as it was */
    if (pg.paging) /* the next page let go: its Loading… row More… again, at the cursor it asked */
        pg.paging = 0, append(top(), top()->n);
    *w = *next;
    req_send(app()->browse, op ? op : w->op, op ? args : w->args);
    pg.act = 0;
    w->id = req.id;
    pg.pend = (char)how;
    widget_invalidate_force(pg.page, 0);
    return w;
}

/* The overlay's level, its reply come: entered, or in the top's place sliding in from pmoved's
 * side; got() then takes the reply as its own. */
static void load_take(void) {
    lv_t *w = &pg.lv[ADDON_DEPTH];
    int how = pg.pend;
    pg.pend = 0;
    if (how == P_PUSH)
        enter(w);
    else {
        if (how == P_SECTION) keep_sel(), pg.sec[pg.cur].sel = top()->sel, pg.cur = pg.psec;
        drop(), *top() = *w, pg.moved = pg.pmoved;
    }
    widget_invalidate_force(pg.page, 0);
}

/* A level over the top one; base: args' bytes that name its list, before a More…'s cursor. Over a
 * level showing its rows, it is asked for under the loading overlay and entered once its reply
 * comes (got); else at once, its Loading… page meanwhile. The level, to set its layout. */
static lv_t *push(const char *op, const char *args, unsigned base, const char *title) {
    lv_t next = { 0 };
    tk_snprintf(next.op, sizeof next.op, "%s", op);
    tk_snprintf(next.args, sizeof next.args, "%s", args);
    tk_snprintf(next.title, sizeof next.title, "%s", title);
    next.base = base < sizeof next.args ? base : 0;
    if (stageable()) return stage(&next, P_PUSH, 0, 0);
    enter(&next);
    show();
    return top();
}

/* Back: the level below shown at once from the reply it kept (enter), every page of it, and asked
 * again, its rows written anew only when the answer differs (got, as a live level's); one that
 * kept none is asked again under its Loading… page. ponytail: only the first page is compared, so
 * a later page that changed shows as it was until the level is asked again whole. */
static void pop(void) {
    req_cancel();
    drop();
    --pg.depth;
    lv_t *l = top();
    kept_t *k = &pg.kept[pg.depth - 1];
    pg.moved = -1;
    if (!k->f) {
        l->state = K_LOAD;
        show();
        return;
    }
    memcpy(pg.buf, k->buf, sizeof pg.buf);
    memcpy(pg.f, k->f, (4 + 6 * (unsigned)l->n) * sizeof *pg.f);
    memcpy(pg.sort, k->sort, sizeof pg.sort);
    pg.pages = k->pages, k->pages = 0;
    kept_free(k);
    show();
    fetch(l);
}

/* Section i shown as the first level: its op asked, its list sliding in from dir's side (1 from the
 * right, as the next; -1 from the left; 0 in place), the one it replaces keeping its selected row;
 * a swipe's under the loading overlay over the one shown, which it replaces once its reply comes
 * (load_take, P_SECTION). With two or more the title has the position, Albums 2/3. */
static void sec_open(int i, int dir) {
    lv_t *l = &pg.lv[0], next = pg.sec[i]; /* K_LOAD */
    if (pg.nsec > 1)
        tk_snprintf(next.title, sizeof next.title, "%s %d/%d", pg.sec[i].title, i + 1, pg.nsec);
    if (dir && stageable()) { /* a swipe: asked for under the overlay, the one shown staying */
        pg.pmoved = (signed char)dir, pg.psec = (signed char)i;
        stage(&next, P_SECTION, 0, 0);
        return;
    }
    req_cancel();
    keep_sel();
    pg.sec[pg.cur].sel = l->sel;
    drop();
    pg.cur = (signed char)i;
    *l = next;
    pg.moved = (signed char)dir;
    show();
}

/* A page row's press: the top level's list from cursor c, in the level's place, so Back leaves the
 * list instead of stepping back through its pages; dir: 1 a later page, -1 an earlier one. Asked
 * for under the loading overlay, the window on show staying until the new one comes (load_take). */
static void page_to(lv_t *l, const char *c, int dir) {
    char a[sizeof l->args] = "";
    if (!cursor_args(l, a, sizeof a, c)) return;
    /* the first content row: past Previous page, and past a Search and a Sort row kept as rows */
    int first = !strcmp(cell(0, 0), "search");
    first += first < l->n && !strcmp(cell(first, 0), "sort");
    ++first;
    if (stageable()) {
        lv_t next = *l;
        memcpy(next.args, a, sizeof a);
        next.n = 0, next.state = K_LOAD;
        next.sel = dir > 0 ? first : 1 << 20; /* show() clamps it to the last row */
        pg.pmoved = (signed char)dir;
        stage(&next, P_PLACE, 0, 0);
        return;
    }
    req_cancel();
    drop();
    memcpy(l->args, a, sizeof a);
    l->n = 0, l->state = K_LOAD;
    l->sel = dir > 0 ? first : 1 << 20; /* show() clamps it to the last row */
    pg.moved = (signed char)dir;
    show();
}

/* The first level's reply saying "sections": its list rows (n of g's) become the sections, and the
 * first opens. 0 when it has none to take. */
static int sec_take(char **g, int n) {
    if (!pg.sec && !(pg.sec = calloc(ADDON_SECTIONS, sizeof *pg.sec))) return 0;
    int k = 0;
    for (int i = 0; i < n && k < ADDON_SECTIONS; ++i) {
        char **c = g + 4 + 6 * i;
        if (strcmp(c[0], "list") || !c[1][0]) continue;
        lv_t *s = &pg.sec[k];
        tk_snprintf(s->op, sizeof s->op, "%s", c[1]);
        tk_snprintf(s->title, sizeof s->title, "%s", c[2]);
        s->args[0] = 0;
        if (c[4][0] && !put(s->args, sizeof s->args, c[4])) continue; /* too long: left out */
        s->base = strlen(s->args);
        ++k;
    }
    if (!k) return 0;
    pg.nsec = (signed char)k;
    sec_open(0, 0);
    return 1;
}

/* The finished request: an action's toast, or the top level's rows. */
static void got(void) {
    if (pg.pend && !pg.act && req.id == pg.lv[ADDON_DEPTH].id) load_take(); /* the overlay's */
    lv_t *l = top();
    char id[12], *head[4], *g[4 + 6 * ADDON_ROWS], *reply = req.buf, *fit;
    /* its own size, before spot_reply points into it: a full list keeps ADDON_PAGES of them */
    if (req.fail == REQ_OK && (fit = realloc(reply, req.n + 1))) reply = fit;
    /* The reply's bytes past its id, before spot_reply cuts them: the same as shown, nothing
     * redrawn. */
    unsigned skip = 9, hash = 0;
    while (req.fail == REQ_OK && skip < req.n && reply[skip] != '\t') ++skip;
    if (req.fail == REQ_OK && skip < req.n)
        hash = hash_bytes(FNV_SEED, (const unsigned char *)reply + skip, req.n - skip);
    /* an action's reply has no rows, a hold's its menu's, a level's its list's */
    int act = pg.act;
    char **f = act == A_ACTION ? head : g;
    int n = req.fail == REQ_OK ? spot_reply(reply, req.n, f,
                                            act == A_HOLD ? HOLD_ROWS
                                            : act         ? 0
                                                          : ADDON_ROWS)
                               : -1;
    int qrnext = !act && pg.qrnext; /* this reply is the refresh an action asked for */
    if (!act) pg.qrnext = 0;
    req.buf = 0;
    tk_snprintf(id, sizeof id, "%u", req.id);
    if (n >= 0 && strcmp(f[0], id)) n = -1;
    int ok = n >= 0 && !strcmp(f[1], "ok");
    unsigned now = time_now_ms();
    char why[48];
    tk_snprintf(why, sizeof why, req.fail == REQ_NOSOCK ? "%s isn't running" : "%s didn't answer",
                app()->name);
    if (act) busy_row(0); /* before the toast, whose loop can pop the level */
    pg.act = 0;
    if (act == A_HOLD && ok && n > 0 && l == top() && l->state == K_READY &&
        hold_menu(reply, f, n)) {
        reply = 0; /* the menu's rows point into it */
    } else if (act) {
        if (n >= 0 && f[2][0])
            toast(f[2]);
        else if (!ok)
            toast(why);
        if (ok) queue_poll(app()); /* an action that queued a song: queued and toasted at once */
        if (ok && !strcmp(pg.aop, "stop")) {
            app()->launched = app()->hold = 0;
            navigator_back();
        } else if (ok && l == top() && l->state == K_READY && l->live) {
            keep_sel(), fetch(l); /* a live level stays shown, asked again now: no Loading… page */
            pg.qrnext = 1;        /* one press: a sign-in's row turned remote opens the QR page */
        } else
            keep_sel(), hold_load(l); /* the level as the action left it */
    } else if (l->id != req.id) {
    } else if (pg.paging) { /* a next page appended, or the list ends at More… (rows kept) */
        int from = l->n;
        pg.paging = 0;
        if (ok) {
            pg.buf[pg.pages++] = reply, reply = 0;
            memcpy(pg.f + 4 + 6 * from, g + 4, 6 * (unsigned)n * sizeof *g);
            pg.f[3] = g[3], l->n += n;
            if (!strcmp(g[2], "pages") && g[3][0] && pg.pages < ADDON_PAGES) page_next(l);
        }
        append(l, from);
    } else if (req.fail == REQ_NOSOCK && !req.field && /* a field's text: its error, never resent */
               (app()->restart || now - app()->launch_at < ADDON_START_MS)) {
        if (l->state != K_WAIT && l->state != K_READY) l->state = K_WAIT, show();
        pg.again = now + 500;
    } else if (ok && pg.depth == 1 && !pg.nsec && !strcmp(f[2], "sections") && sec_take(g, n)) {
        /* sections: the first asked for (sec_open); this reply's rows were their names. Taken once:
         * a section that answers "sections" too is a plain list, not asked for again and again */
    } else if (!ok) {
        l->state = K_ERROR;
        l->retry = 1;
        l->nosock = req.fail == REQ_NOSOCK;
        l->since = l->slow = 0;
        tk_snprintf(l->note, sizeof l->note, "%s",
                    req.fail != REQ_NOSOCK && n >= 0 && f[2][0] ? f[2] : why);
        show();
    } else {
        l->since = l->slow = 0;
        l->live = !strcmp(f[2], "refresh");
        pg.again = now + ADDON_LIVE_MS;
        if (l->state != K_READY || hash != l->hash) {
            int find = take_find(g, &n), sort = find && take_sort(g, &n);
            int same = l->state == K_READY && l->live && !l->lay && find == l->find &&
                       sort == l->sort && same_rows(l, g, n);
            keep_sel();
            if (same)
                free(pg.buf[0]); /* same_rows: its one page, and the covers stay drawn */
            else
                drop();
            pg.buf[0] = reply, pg.pages = 1, reply = 0;
            memcpy(pg.f, f, sizeof g); /* pg.f holds ADDON_PAGES replies' rows, g one's */
            l->n = n;
            l->find = find;
            l->sort = sort;
            l->hash = hash;
            l->state = K_READY;
            /* an app opts in; older ones' lists keep More… */
            if (!strcmp(f[2], "pages") && f[3][0]) page_next(l);
            if (same)
                relabel(l); /* only texts and bars moved: the rows stay */
            else
                show();
        }
        if (pg.code) /* turned on by its row: its address at once, the menu behind it */
            pg.code = 0, qr_open(app()->state, app()->name);
        else if (qrnext && l->sel >= 0 && l->sel < l->n && !strcmp(cell(l->sel, 0), "remote"))
            qr_open(app()->state, app()->name); /* the action's row is the QR page's now */
    }
    free(reply);
}

static int go(const void *unused);
static int leave(const void *unused);

static void later(int next, int arg) {
    pg.next = next, pg.arg = arg;
    if (pg.page) rearm(&pg.go, go, 0); /* not once closed: the box's blur can come after */
}

static int clicked(void *ctx, void *event) {
    (void)event;
    /* Loading… does nothing: dropped here, as by go() its page may have put a row at its index */
    if (pg.paging && (int)(long)ctx == top()->n) return 0;
    later(G_ROW, (int)(long)ctx);
    return 0;
}

/* The box's text: one that doesn't fit pg.term whole is let go, cleared, and nothing is sent. Done sends
 * this twice (EVT_IM_ACTION, EVT_VALUE_CHANGED), so pg.field stays set here (field_sent and search_box
 * clear it) and the toast waits for no pending go: the second call must not toast again or send the cut text.
 * ponytail: stock's widget_get_text_utf8 writes nothing for a text over 255 bytes, which closes silently. */
static int searched(void *ctx, void *event) {
    char t[256] = "";
    (void)ctx, (void)event;
    if (widget_get_text_utf8(pg.edit, t, sizeof t)) t[0] = 0;
    if (strlen(t) >= sizeof pg.term) { /* none of it goes: the box closes, the level stays */
        memset(t, 0, sizeof t);
        if (!pg.go) toast("Too long"); /* its loop would run the go the first call armed, under this handler */
    }
    tk_snprintf(pg.term, sizeof pg.term, "%s", t);
    memset(t, 0, sizeof t);
    later(!pg.term[0] ? G_SHOW : pg.field ? G_FIELD : G_SEARCH, 0);
    return 0;
}

static int search_left(void *ctx, void *event) {
    (void)ctx, (void)event;
    if (!pg.go) later(G_SHOW, 0);
    return 0;
}

/* The page's touches (page_at binds them, before its children see them): a sideways swipe on the
 * first level of an app with sections moves to the next or the previous one, as Pull to search's
 * pull (navigation.c pull_event): once the move is a swipe's, the rows it pressed are told the
 * touch is gone, so the lift clicks nothing, and the moves and the lift are kept from the list. Up
 * and down stay the list's.
 * ponytail: touch only. The wheel moves the selection, as on every list, so the sections past the
 * first are reached by a swipe alone; bind a key here if that is ever asked for. */
static int swipe(void *ctx, void *event) {
    (void)ctx;
    int type = I(event, EVENT_TYPE);
    if ((pg.pend || pg.hold) && type != EVT_POINTER_ABORT) /* the overlay takes every touch */
        return load_touch(pg.page, event, load_cancel);
    if (type == EVT_POINTER_ABORT || pg.nsec < 2 || pg.depth != 1 || pg.edit) {
        pg.sdown = pg.sclaim = 0;
        return 0;
    }
    int x = I(event, EVENT_X), y = I(event, EVENT_Y);
    if (type == EVT_POINTER_DOWN) {
        pg.sdown = 1, pg.sclaim = 0, pg.sx = x, pg.sy = y;
        return 0;
    }
    if (!pg.sdown) return 0;
    int dx = x - pg.sx, dy = y - pg.sy, ax = dx < 0 ? -dx : dx, ay = dy < 0 ? -dy : dy;
    if (type == EVT_POINTER_UP_BEFORE) {
        int claimed = pg.sclaim;
        pg.sdown = pg.sclaim = 0;
        if (!claimed) return 0;
        if (ax >= SWIPE_PX && ax > ay) later(G_SECTION, dx < 0 ? 1 : -1); /* a swipe to the left: the next */
        return 11; /* RET_STOP */
    }
    if (type != EVT_POINTER_MOVE_BEFORE) return 0;
    if (!pg.sclaim) {
        if (ay >= SWIPE_CLAIM_PX) { /* the list's: it scrolls */
            pg.sdown = 0;
            return 0;
        }
        if (ax < SWIPE_CLAIM_PX || ax < 2 * ay) return 0;
        unsigned abort_event[12];
        pointer_event_init(abort_event, EVT_POINTER_ABORT, pg.page, pg.sx, pg.sy);
        widget_dispatch_event_to_target_recursive(pg.page, abort_event);
        pg.sdown = pg.sclaim = 1; /* after: the abort comes back here and clears them */
        if (pg.view) list_stop_scroll(pg.view); /* as pull_event: a fling under the claim ends */
    }
    return 11; /* RET_STOP */
}

/* Whether field row i takes a password, which the letter strip shows as dots (search_strip.c
 * ss_secret_box). Q2SPOT/1 has no word for one, so it is a field titled as one ("Password",
 * "Passcode": "pass" in any case) or one whose subtitle is asterisks alone, the app's mask. */
static int field_secret(int i) {
    const char *sub = cell(i, 3);
    if (strcasestr(cell(i, 2), "pass")) return 1;
    while (*sub == '*') ++sub;
    return *cell(i, 3) && !*sub;
}

/* peq_ui.c's text edit over the title, the letter strip open on it: a Search row's press, or its
 * icon's tap (field 0); or field row field - 1's press, its title the box's hint and its label on
 * the letter strip, marked _secret when it takes a password (field_secret). */
static void search_box(int field) {
    keep_sel();
    pg.field = field;
    widget_destroy_children(pg.page), pg.view = 0; /* the box alone, as Spotify's */
    pg.edit = edit_create(pg.page, "text", bar_foot(pg.page) + 2, 26);
    widget_set_prop_str(pg.edit, "tips", field ? cell(field - 1, 2) : app()->tips);
    if (field) widget_set_prop_str(pg.edit, "_strip_label", cell(field - 1, 2)); /* search_strip_box */
    widget_set_prop_int(pg.edit, STRIP_RETURN, field ? STRIP_GO : STRIP_SEARCH); /* its return mark */
    if (field && field_secret(field - 1)) widget_set_prop_int(pg.edit, "_secret", 1);
    widget_on(pg.edit, EVT_VALUE_CHANGED, searched, 0);
    widget_on(pg.edit, EVT_IM_ACTION, searched, 0);
    widget_on(pg.edit, EVT_BLUR, search_left, 0);
    widget_set_focused(pg.edit, 1);
}

/* A title bar icon's tap, ctx its G_ code, on a timer as a row's: what it opens replaces the page
 * the icon is in. */
static int bar_tapped(void *ctx, void *event) {
    (void)event;
    later((int)(long)ctx, 0);
    return 0;
}

/* A list row's press, or the sort icon's tap: level uri over the top one, its context the
 * argument, titled title. */
static lv_t *open_list(const char *uri, const char *ctx, const char *title) {
    char a[sizeof top()->args] = "";
    return !ctx[0] || put(a, sizeof a, ctx) ? push(uri, a, strlen(a), title) : (lv_t *)0;
}

/* A field row's text, typed in its box: sent to the row's uri after its context, and the reply
 * taken as the level's own (got), in its place: no level pushed, nothing titled with the text, the
 * box closed onto the level's rows with the loading overlay over them meanwhile (P_PLACE), and its
 * selected row kept. A failure is the level's error page,
 * whose Retry asks for the level's own op, so the text is never sent again. The text is cleared
 * once it is in the request, which the worker clears once it is sent: a password goes to no title,
 * log or cache. A row no longer a field (a reply landed under the box) sends nothing. */
static void field_sent(lv_t *l) {
    char a[sizeof l->args] = "";
    int i = pg.field - 1;
    pg.field = 0;
    if (l->state == K_READY && i >= 0 && i < l->n && !strcmp(cell(i, 0), "field") &&
        cell(i, 1)[0] && (!cell(i, 4)[0] || put(a, sizeof a, cell(i, 4))) &&
        put(a, sizeof a, pg.term)) {
        lv_t next = *l;
        next.n = 0, next.state = K_LOAD;
        show(); /* the box closed onto the rows, which keep the reply the uri points into */
        pg.pmoved = 0;
        stage(&next, P_PLACE, cell(i, 1), a);
        req.field = 1;
    } else
        show();
    memset(pg.term, 0, sizeof pg.term);
    memset(a, 0, sizeof a);
}

/* A row's choice, on a timer: a row's click may not rebuild the list it is in. */
static int go(const void *unused) {
    (void)unused;
    lv_t *l = top();
    int i = pg.arg;
    char a[sizeof l->args] = "";
    pg.go = 0;
    if (pg.pend || pg.hold) return 0; /* the wheel's Centre, a row or an icon while the overlay waits */
    if (pg.next == G_SHOW)
        show();
    else if (pg.next == G_SECTION) { /* a swipe: the next or previous section, none past the ends */
        int to = pg.cur + i;
        if (pg.depth == 1 && to >= 0 && to < pg.nsec) sec_open(to, i);
    } else if (pg.next == G_FIND) {
        if (l->state == K_READY && l->find) search_box(0);
    } else if (pg.next == G_SORT) { /* the sort icon, opening as its row would */
        if (l->state == K_READY && l->sort) open_list(pg.sort[1], pg.sort[4], pg.sort[2]);
    } else if (pg.next == G_SEARCH) { /* the box closed onto the rows, the search over them */
        if (put(a, sizeof a, pg.term)) show(), push("search", a, strlen(a), pg.term);
    } else if (pg.next == G_FIELD)
        field_sent(l);
    else if (l->state == K_ERROR && i == 1 && app()->nostop) { /* Cancel, as Return */
        leave(0);
    } else if (l->state == K_ERROR ? i == 1 : l->state != K_READY && l->slow) { /* Stop <name> */
        char t[48];
        tk_snprintf(t, sizeof t, "%s stopped", app()->name);
        app_stop(app(), 0);
        toast(t);
        navigator_back();
    } else if (l->state == K_ERROR) {
        if (l->retry) {
            if (l->nosock && !access(app()->pid, 0)) /* running, its socket gone: started again */
                app_stop(app(), 1);
            else if (!app()->launched || access(app()->pid, 0))
                launch(app());
            /* asked again under the loading overlay, the error page left as it is meanwhile */
            hold_load(l);
        }
    } else if (l->state != K_READY) {
    } else if (i == l->n) { /* More…, as a level of its own; Loading… does nothing */
        if (!pg.paging && cursor_args(l, a, sizeof a, pg.f[3]))
            push(l->op, a, l->base, pg.depth == 1 && pg.nsec ? pg.sec[pg.cur].title : l->title);
    } else if (i == AR_ALBUMS && l->lay == LAY_ARTIST) { /* the artist's page again, its albums */
        push(l->op, l->args, l->base, "Albums")->lay = LAY_ALBUMS;
    } else if (i < 0 || i >= l->n) {
    } else {
        const char *kind = cell(i, 0), *uri = cell(i, 1), *ctx = cell(i, 4);
        int lay = l->lay == LAY_ARTIST && !strcmp(kind, "list") ? drill_lay(uri) : 0;
        if (!strcmp(kind, "list") || !strcmp(kind, "sort")) { /* with remote and a folder: opens() */
            lv_t *to = open_list(uri, ctx, cell(i, 2)); /* may free the reply kind points into */
            if (lay && to && to != l) to->lay = (signed char)lay;
        } else if (!strcmp(kind, "page"))
            page_to(l, ctx, strcmp(uri, "prev") ? 1 : -1);
        /* one in flight: this press does nothing. needslib, an action that needed the server
         * library on when it was a setting, is an action: it is always on */
        else if ((!strcmp(kind, "action") || !strcmp(kind, "needslib")) && !pg.act)
            action(i, uri, ctx);
        else if (!strcmp(kind, "remote"))
            qr_open(app()->state, app()->name);
        else if (!strcmp(kind, "local")) { /* a song, or a folder of them, on the card alone */
            if (card_path(uri))
                songs_open(uri, cell(i, 2), !strcmp(ctx, "album"));
        } else if (!strcmp(kind, "search"))
            search_box(0);
        else if (!strcmp(kind, "field"))
            search_box(i + 1);
    }
    return 0;
}

/* The page's timer: a finished request taken; a starting app, or a live level on top, asked
 * again. */
static int tick(const void *unused) {
    (void)unused;
    lv_t *l = top();
    unsigned now = time_now_ms();
    if (req_done())
        got();
    else if (pg.act && (int)(now - pg.acted) >= BUSY_MS)
        busy_row(1);
    else if ((l->state == K_LOAD || l->state == K_WAIT) && !l->slow && l->since &&
             (int)(now - l->since) >= SLOW_MS && stoppable())
        show();
    else if (!req.running && (int)(now - pg.again) >= 0 && !pg.edit &&
             (l->state == K_WAIT || (l->state == K_READY && l->live && g_backlight_status &&
                                     window_manager_get_top_window(window_manager()) == pg.page)))
        fetch(l);
    art_window();
    return 8; /* RET_REPEAT */
}

static int leave(const void *unused) {
    (void)unused;
    pg.leave = 0;
    if (pg.pend || pg.hold) /* the load cancelled: the page stays */
        load_cancel();
    else if (pg.edit) /* the search box emptied, so no search; its blur shuts the keyboard, then the box
                  */
        widget_set_text_utf8(pg.edit, ""), widget_set_focused(pg.edit, 0);
    else if (pg.depth > 1)
        pop();
    else
        navigator_back();
    return 0;
}

static int keyup(void *ctx, void *event) {
    (void)ctx;
    return return_key(event, &pg.leave, leave);
}

static int closed(void *ctx, void *event) {
    (void)event;
    if (ctx != pg.page) return 0; /* a page addon_goto replaced, its state gone already */
    req_cancel();
    stop_timer(&pg.timer);
    stop_timer(&pg.go);
    stop_timer(&pg.leave);
    drop();
    for (int i = 0; pg.kept && i < ADDON_DEPTH; ++i) kept_free(&pg.kept[i]);
    free(pg.kept);
    free(pg.lv);
    free(pg.f);
    free(pg.sec);
    memset(&pg, 0, sizeof pg);
    free(hm.buf); /* a row's menu left unpicked */
    hm.buf = 0;
    return 0;
}

/* The page's window, its touches bound (swipe). 0 when none could be made. */
static int page_make(void) {
    static const unsigned short touches[] = { EVT_POINTER_DOWN, EVT_POINTER_MOVE_BEFORE,
                                              EVT_POINTER_UP_BEFORE, EVT_POINTER_ABORT };
    if (!(pg.page = page_open("addon_page", closed, keyup))) return 0;
    for (unsigned t = 0; t < sizeof touches / sizeof *touches; ++t)
        widget_on(pg.page, touches[t], swipe, 0);
    return 1;
}

/* The first open given up under the overlay (Return, a tap off the spinner, its window gone): no
 * page was made, and the app started for it runs on. */
static void held_cancel(void) { closed(0, 0); }

/* The page, or its first open under the overlay, let go: its state with it. */
static void page_drop(void) {
    void *old = pg.page;
    if (old)
        closed(old, 0), window_manager_close_window_force(window_manager(), old);
    else if (pg.lv)
        load_over(0, 0), closed(0, 0);
}

/* Slot k's page, its first level op (args: "\t" and escaped each) titled title: the app started if
 * it isn't (its QR code shown at the first answer when qr); gone from the card, the page says so.
 * The page opens once the app's first answer (its rows or its error) is there, the loading overlay
 * over the window it was opened from meanwhile, through the app's start (pg.hold); at once with no
 * such window. 1 when it opened, is on its way, or said why not. */
static int page_at(int k, const char *op, const char *args, const char *title, int qr) {
    const char *busy = shelf_busy(); /* a job on this app's folder */
    app_t *b = &ap.a[k];
    /* A page is refused when the folder is being deleted or moved into, and while an update
     * downloads for an app that is not running: launch() would be refused too, a dead page. */
    if (busy && !strcmp(busy + 1, b->id) &&
        (shelf_moving() || !b->launched || access(b->pid, 0)))
        return toast("Wait until the App Store finishes"), 1;
    void *from = page_in_use();
    if (pg.lv || !(pg.lv = calloc(ADDON_DEPTH + 1, sizeof *pg.lv))) return 0;
    int missing = access(b->bin, 0);
    if (!(pg.f = calloc(4 + 6 * ADDON_ROWS * ADDON_PAGES, sizeof *pg.f)) ||
        !(pg.kept = calloc(ADDON_DEPTH, sizeof *pg.kept)) || ((missing || !from) && !page_make()))
        return free(pg.lv), free(pg.f), free(pg.kept), pg.lv = 0, pg.f = 0, pg.kept = 0, 0;
    pg.a = k;
    app_t *a = app();
    if (!missing && (!a->launched || access(a->pid, 0))) {
        if (!starting(a)) launch(a); /* one under way since boot is the page's */
        pg.code = qr && a->qr;
    }
    pg.depth = 1;
    if (!pg.page) pg.hold = 1 + K_LOAD, load_over(from, held_cancel); /* the page once it answers */
    lv_t *l = top();
    tk_snprintf(l->op, sizeof l->op, "%s", op);
    tk_snprintf(l->args, sizeof l->args, "%s", args);
    l->base = strlen(l->args);
    tk_snprintf(l->title, sizeof l->title, "%s", title);
    if (missing) {
        l->state = K_ERROR;
        tk_snprintf(l->note, sizeof l->note, "%s isn't on the card", a->name);
    }
    pg.timer = timer_add(tick, 0, 250);
    show();
    return 1;
}

/* An app's Streaming row (home.c ringnav_stream; ctx: its place by name at the last scan). */
int addon_open(void *ctx, void *event) {
    (void)event;
    int k = (int)(long)ctx;
    if (smart_hold(addon_open, ctx, 0)) return 0; /* the app starts once Smart Wi-Fi's link is up */
    if (k >= 0 && k < ap.shown) page_at(ap.order[k], "menu", "", ap.a[ap.order[k]].name, 1);
    return 0;
}

/* The page of the app with this id, hidden or not, found on the card again first, over any app's
 * page left open under Now Playing, as addon_more's: at op with its one argument arg ("" for none),
 * titled title (0: the app's name), its QR code shown at the first answer for "menu". Settings >
 * Storage opens an app's Settings and a download's own page so (storage.c). 1 when it opened. */
int addon_open_at(const char *id, const char *op, const char *arg, const char *title) {
    char args[sizeof pg.lv->args] = "";
    addon_scan();
    for (int i = 0; i < ap.n; ++i)
        if (ap.a[i].present && !strcmp(ap.a[i].id, id)) {
            if (*arg && !put(args, sizeof args, arg)) return 0;
            page_drop();
            return page_at(i, op, args, title && *title ? title : ap.a[i].name,
                           !strcmp(op, "menu"));
        }
    return 0;
}
void addon_open_id(const char *id) { addon_open_at(id, "menu", "", 0); }

/* Whether path is in an app's folder, CARD/.<id>/, and id the app's. */
static int card_id(const char *path, char *id) {
    const char *p = path && tk_str_start_with(path, CARD "/.") ? path + sizeof CARD "/." - 1 : 0;
    unsigned k = 0;
    while (p && p[k] && p[k] != '/' && k < APP_ID - 1) ++k; /* an id fits id with its NUL */
    if (!p || p[k] != '/') return 0;
    tk_snprintf(id, k + 1, "%s", p);
    return valid_id(id);
}
/* The app whose folder holds path, when its q2app says goto=1; 0 for any other. */
static app_t *goto_app(const char *path) {
    char id[APP_ID], raw[512], file[64];
    if (catalogue_path(path) || !card_id(path, id)) return 0; /* a server row is the Library's */
    addon_q2app(id, file, sizeof file);
    if (!state_read(file, raw, sizeof raw) || number(raw, "goto") != 1) return 0;
    app_t *a = known(id);
    if (!a || !a->name[0]) addon_scan(), a = known(id); /* not listed since boot: its name read */
    return a && a->present ? a : 0;
}
/* The hold menu's: whether Go to on path opens a page, its app's (goto=1) or, outside an app's
 * folder, stock's. An app's folder is hidden, so the Library never holds its songs, but for a
 * server row (catalogue.c), whose path is in its app's folder and whose album and artist are the
 * Library's: stock's pages. */
int addon_goes(const char *path) {
    char id[APP_ID];
    return catalogue_path(path) || !card_id(path, id) || goto_app(path);
}
/* Whether a listen of path goes in the scrobble log (navigation.c scrobble): not when it is in an
 * app's folder and its q2app says scrobble=0, a line a Windows editor may end in \r. Compared as
 * text: number() is 0 for a missing key or a word too.
 * ponytail: by folder, not by who queued it: a song an app's .m3u or local row names outside its
 * own folder still logs. Knowing the app means keeping it per queue, if one ever needs that. */
int addon_scrobbles(const char *path) {
    char id[APP_ID], raw[512], file[64], v[4];
    if (!card_id(path, id)) return 1;
    addon_q2app(id, file, sizeof file);
    state_read(file, raw, sizeof raw);
    field(raw, "scrobble", v, sizeof v);
    return strcmp(v, "0") && strcmp(v, "0\r");
}

/* Hearts (docs/librespot.md): a press on an app's song goes to CARD/.<id>/fav.log as a line
 * "\n<kind>\t<0|1>\t<key>\n" appended, which the app takes and sends to its server, whether it runs
 * now or starts later: kind "item" for a streamed song (tidal.c app_fav, key its queue line's item),
 * "path" for a song in its favdir (key its path). The LF before it ends a line a cut write left, so
 * that never joins the next press; the app drops empty lines. One fwrite, no fsync, as
 * scrobble_append's listen: on the UI thread, and only ever appended to. 1 when the card took it. */
int addon_fav(const char *id, const char *kind, const char *key, int on) {
    char path[64], line[600];
    unsigned n = (unsigned)tk_snprintf(line, sizeof line, "\n%s\t%d\t%s\n", kind, on != 0, key);
    if (!valid_id(id) || n >= sizeof line) return 0; /* cut, it would name another song */
    tk_snprintf(path, sizeof path, CARD "/.%s/fav.log", id);
    void *f = fopen(path, "ab");
    int ok = f && fwrite(line, n, 1, f) == 1;
    if (f && fclose(f)) ok = 0;
    return ok;
}

/* A song's favourite changed (ratings.c): told to the card app whose q2app's favdir names the card
 * folder path is in, CARD/<favdir>/…, such as q2-jellyfin's synced Jellyfin. favdir is one folder
 * name, its line maybe ending in a Windows editor's \r. Every app's q2app is read at each change,
 * so no scan is needed first: a press is rare. 0 when that app's card didn't take it. */
struct fav_ask {
    const char *path;
    int on, ok;
};
static int fav_app(const char *id, const char *raw, void *ctx) {
    struct fav_ask *f = ctx;
    char dir[64];
    field(raw, "favdir", dir, sizeof dir);
    unsigned k = strlen(dir);
    if (k && dir[k - 1] == '\r') dir[--k] = 0;
    if (!k || strstr(dir, "/") || !strcmp(dir, ".") || !strcmp(dir, "..") ||
        strncmp(f->path + sizeof CARD, dir, k) || f->path[sizeof CARD + k] != '/')
        return 0;
    f->ok = addon_fav(id, "path", f->path, f->on);
    return 1;
}
int addon_fav_path(const char *path, int on) {
    struct fav_ask f = { path, on, 1 };
    if (path && card_path(path)) addon_q2apps(fav_app, &f);
    return f.ok;
}

/* Go to Album (album) or Go to Artist on a song in a card app's folder, /mnt/mmc/.<id>/…
 * (navigation.c qm_goto): when its manifest says goto=1, its page opens at op "goto" ("album" or
 * "artist", then the song's path), the level the app answers with, titled title; Back leaves it
 * for the page under it. Its page already open (the song played from it) is closed first, as
 * Coverflow's scoped page is: one page's state. 1 when it opened. */
int addon_goto(const char *path, const char *title, int album) {
    char args[sizeof pg.lv->args] = "";
    app_t *a = goto_app(path);
    if (!a || !put(args, sizeof args, album ? "album" : "artist") || !put(args, sizeof args, path))
        return 0;
    page_drop();
    return page_at((int)(a - ap.a), "goto", args, title && *title ? title : a->name, 0);
}

/* Now Playing's ⋯ (navigation.c: its img_more's click, the hold menu's More… too) while the queue
 * playing is the one an app's .play started (no queue loaded since) and its q2app says more=<op>
 * <title>: the app's page opens at op, with no arguments, titled title (the app's name without
 * one), instead of stock's ⋯ page, whose rows (Playlists, Quality Choice) mean nothing for an app's
 * queue. Its page already open is closed first, as for Go to. 1 when it opened.
 * ponytail: a stock reload of the same queue (a pick that loads it again) counts as another queue,
 * so ⋯ is stock's until the app plays a queue again; it errs toward stock's page. */
int addon_more(void) {
    app_t *a = qa.app;
    char op[sizeof pg.lv->op];
    if (!a || playback_loads != qa.loads || !a->more[0] || access(a->bin, 0)) return 0;
    const char *title = strstr(a->more, " ");
    unsigned k = title ? (unsigned)(title - a->more) : strlen(a->more);
    if (!k || k >= sizeof op) return 0; /* no op, or one too long for a level */
    tk_snprintf(op, k + 1, "%s", a->more);
    page_drop();
    return page_at((int)(a - ap.a), op, "", title && title[1] ? title + 1 : a->name, 0);
}

/* Now Playing's radio button (np_bar.c): 1 while the queue playing is an app's and its status file
 * says radio=<seed title>, as an app playing a radio writes it (Radio Mix's protocol,
 * docs/internals.md#now-playing-ipod). Read at most once a second. */
int addon_radio(void) {
    static unsigned at __attribute__((section(".scratch")));
    static int on __attribute__((section(".scratch")));
    unsigned now = (unsigned)time_now_ms();
    if (now - at < 1000 && at) return on;
    at = now ? now : 1;
    app_t *a = qa.app;
    char raw[512], seed[8];
    on = 0;
    if (a && playback_loads == qa.loads && app_state(a->state, raw, sizeof raw)) {
        field(raw, "radio", seed, sizeof seed);
        on = seed[0] && seed[0] != '\r';
    }
    return on;
}

/* A song's radio asked of the app that plays it (song_radio.c, docs/internals.md#radio): the app
 * whose folder holds path (a streamed song's file, a Library server row's path) when its q2app says
 * radio=1, else the playing queue's app when it says so. Its request is /tmp/q2-<id>.radio, one
 * line, "<seq> start <path>" or "<seq> stop", written only once the app's socket is there (it takes
 * a request already there at its start as answered, as a .catplay); it answers in its state file,
 * radioack=<seq> <code> (0 started or stopped, 1 not its song, 2 no radio, 3 signed out). */
static struct {
    app_t *app;     /* the app asked last */
    char line[600]; /* its request, while waiting for its socket */
    unsigned at;    /* when it was asked */
} rq __attribute__((section(".scratch")));

static void radio_send(void) {
    char path[48];
    if (!rq.line[0] || !rq.app || !addon_ready(rq.app->id)) return;
    tk_snprintf(path, sizeof path, "/tmp/q2-%s.radio", rq.app->id);
    blob_io(path, "/tmp/q2-radio.tmp", rq.line, strlen(rq.line), 1);
    rq.line[0] = 0;
}

static app_t *radio_app(const char *path) {
    char id[APP_ID];
    int app = path && card_id(path, id);
    app_t *a = app ? known(id) : 0;
    if (app && (!a || !a->present)) addon_scan(), a = known(id); /* a server row's, not listed yet */
    if ((!a || !a->present || !a->radio) && qa.app && playback_loads == qa.loads) a = qa.app;
    return a && a->present && a->radio ? a : 0;
}

/* 1 when an app takes the request for a radio of path's song (seq names its answer), started for
 * it when it doesn't run; 0 when no app plays radios of it. */
int addon_radio_start(const char *path, unsigned seq) {
    app_t *a = radio_app(path);
    if (!a || addon_wake(a->id) < 0) return 0;
    unsigned n = (unsigned)tk_snprintf(rq.line, sizeof rq.line, "%u start %s\n", seq, path);
    if (n >= sizeof rq.line) return rq.line[0] = 0, 0; /* cut, it would name another song */
    rq.app = a, rq.at = time_now_ms();
    radio_send();
    return 1;
}

/* The name of the app whose folder holds path (a streamed song's file, a Library server row's path)
 * when that app plays no song radios: its q2app has no radio=1, as a version from before them
 * (q2-subsonic before q2-v1.0). 0 for any other path, or an app that does. */
const char *addon_radio_missing(const char *path) {
    char id[APP_ID];
    int app = path && card_id(path, id);
    app_t *a = app ? known(id) : 0;
    if (app && (!a || !a->present)) addon_scan(), a = known(id);
    return a && a->present && !a->radio && a->name[0] ? a->name : 0;
}

/* A song's lyrics asked of the app whose song it is (lyrics.c lyrics_ask_poll,
 * docs/internals.md#server-lyrics): the app whose folder (CARD/.<id>/, its streamed and cached
 * songs) or downloads= folder (CARD/<downloads>/, its synced songs) holds path, when its q2app says
 * lyrics=1. Its request is /tmp/q2-<id>.lrcask, one line, "<seq> <path>", written only once the
 * app's socket is there (it takes a request already there at its start as answered); it answers in
 * its state file, lyricsack=<seq> <code>: 0 the .lrc is beside the song, 1 not its song, 2 its
 * server has none, 3 no answer. One request at a time; the caller passes the path again while it
 * waits for the socket, so none is kept here. */
static struct {
    app_t *app;   /* the app asked last */
    unsigned seq; /* its request's */
    int pending;  /* not written yet: its socket isn't there */
    int scanned;  /* addon_scan run once for a path no app listed so far held */
} lq __attribute__((section(".scratch")));

static void lyrics_send(const char *path) {
    char file[48], line[600];
    if (!lq.pending || !lq.app || !path || !addon_ready(lq.app->id)) return;
    unsigned n = (unsigned)tk_snprintf(line, sizeof line, "%u %s\n", lq.seq, path);
    tk_snprintf(file, sizeof file, "/tmp/q2-%s.lrcask", lq.app->id);
    if (n < sizeof line) blob_io(file, "/tmp/q2-lrcask.tmp", line, n, 1);
    lq.pending = 0;
}

/* The app with lyrics=1 whose folder or downloads folder holds path; 0 for none. */
static app_t *lyrics_app(const char *path) {
    char id[APP_ID];
    for (int pass = 0; pass < 2; ++pass) {
        app_t *a = card_id(path, id) ? known(id) : 0;
        if (a && !a->present) a = 0; /* not read yet, or gone from the card */
        for (int i = 0; !a && i < ap.n; ++i) {
            unsigned k = strlen(ap.a[i].dl);
            if (ap.a[i].present && k && !strncmp(path, CARD "/", sizeof CARD) &&
                !strncmp(path + sizeof CARD, ap.a[i].dl, k) && path[sizeof CARD + k] == '/')
                a = &ap.a[i];
        }
        if (a) return a->lyrics ? a : 0;
        if (lq.scanned) break; /* no app's: the user's own song */
        lq.scanned = 1;
        addon_scan(); /* apps not listed since boot: their folders read once */
    }
    return 0;
}

/* 1 when an app takes the request for path's lyrics (seq names its answer), started for it when it
 * doesn't run; 0 when no app fetches lyrics for it, or path is too long to name in one line. */
int addon_lyrics_ask(const char *path, unsigned seq) {
    app_t *a = path && strlen(path) < 580 ? lyrics_app(path) : 0;
    if (!a || addon_wake(a->id) < 0) return 0;
    lq.app = a, lq.seq = seq, lq.pending = 1;
    lyrics_send(path);
    return 1;
}

/* The app's answer to lyrics request seq, about path: its code, or -1 while there is none. Polled by
 * lyrics.c, which writes the request here once the app's socket is there. */
int addon_lyrics_answer(unsigned seq, const char *path) {
    char raw[QR_RAW], v[24], want[16];
    lyrics_send(path);
    if (!lq.app || lq.pending || !app_state(lq.app->state, raw, sizeof raw)) return -1;
    field(raw, "lyricsack", v, sizeof v);
    unsigned k = (unsigned)tk_snprintf(want, sizeof want, "%u ", seq);
    return strncmp(v, want, k) || v[k] < '0' || v[k] > '9' ? -1 : v[k] - '0';
}

/* Car Remote's daemon asked app id for a cover the car wants (coda_rc.c crc_cover_ask,
 * docs/internals.md#car-remote-covers): the app is started if it doesn't run, when its q2app says covers=1.
 * The daemon writes the request itself; an app reads one there at its start. */
void addon_cover_wake(const char *id) {
    char k[APP_ID];
    tk_snprintf(k, sizeof k, "%s", id);
    app_t *a = valid_id(k) ? known(k) : 0;
    if (valid_id(k) && (!a || !a->present)) addon_scan(), a = known(k);
    if (a && a->present && a->covers) addon_wake(k);
}

/* The playing queue's app's radio stopped; 1 when asked. */
int addon_radio_stop(unsigned seq) {
    app_t *a = qa.app && playback_loads == qa.loads && qa.app->radio ? qa.app : 0;
    if (!a) return 0;
    tk_snprintf(rq.line, sizeof rq.line, "%u stop\n", seq);
    rq.app = a, rq.at = time_now_ms();
    radio_send();
    return 1;
}

/* The app's answer to request seq: its code, or -1 while there is none. Polled by song_radio.c,
 * which sends a request waiting for its socket. */
int addon_radio_answer(unsigned seq) {
    char raw[QR_RAW], v[24], want[16];
    radio_send();
    if (!rq.app || !app_state(rq.app->state, raw, sizeof raw)) return -1;
    field(raw, "radioack", v, sizeof v);
    unsigned k = (unsigned)tk_snprintf(want, sizeof want, "%u ", seq);
    return strncmp(v, want, k) || v[k] < '0' || v[k] > '9' ? -1 : v[k] - '0';
}


/* ringnav_keylong: Play/Pause held on a row with a hold op (a list: all of it downloaded) sends it,
 * the op "hold" as the row's menu (hold_send); 1 when it was one. */
int addon_hold(void *w) {
    lv_t *l = pg.page && w == pg.page ? top() : 0;
    int i = l && l->state == K_READY && pg.view ? widget_get_prop_int(pg.view, "_ringnav_index", -1)
                                                : -1;
    if (i >= 0 && (l->lay == LAY_ARTIST || l->lay == LAY_ALBUM)) return artist_hold(l);
    if (i >= 0 && l->lay) /* a cover row: its reply row is its click's (cover_row) */
        i = widget_get_prop_int(widget_get_child(pg.view, (unsigned)i), "_ar_row", -1);
    if (i < 0 || i >= l->n || !cell(i, 5)[0] || !strcmp(cell(i, 0), "progress")) return 0;
    if (!strcmp(cell(i, 5), HOLD_OP)) return hold_send(i); /* its own menu */
    action(i, cell(i, 5), cell(i, 4));
    return 1;
}

/* hold_menu.c's song row hold, Centre held on the page or a finger on a row it has selected: the
 * selected row's own menu (hold_send) when its hold op is "hold"; 1 when it is on its way. Any
 * other row, and the lit pair of an artist's or album's page, keep what they did. */
int addon_row_hold(void *w) {
    lv_t *l = pg.page && w == pg.page ? top() : 0;
    int i = l && l->state == K_READY && pg.view ? widget_get_prop_int(pg.view, "_ringnav_index", -1)
                                                : -1;
    if (i < 0 || ((l->lay == LAY_ARTIST || l->lay == LAY_ALBUM) && l->stop)) return 0;
    if (l->lay) /* a cover row or a track: its reply row is its click's (cover_row, track_row) */
        i = widget_get_prop_int(widget_get_child(pg.view, (unsigned)i), "_ar_row", -1);
    return i >= 0 && i < l->n && hold_send(i);
}

/* The QR code page (qrcode_page), redrawn every half second from an app's status file:
 *  - its code, qr (qrsize rows of hex, the leftmost module the high bit) on a white plate centred
 *    at the top, the app's note under it ("Scan with your phone" without one: q2-subsonic's says
 *    "Scan with your phone to sign in"), and under that, dimmer, the address (addr) and PIN (pin)
 *    for a phone that types it; without an address (no Wi-Fi) or a file (not running), it says so;
 *  - or, once the file has a step, that step as a mode page (docs/oled-design.md): one solid circle
 *    with its glyph, the file's title and line under it, the stack centred under the status row.
 *    phone, check: the accent's (the phone has the form; the server is being asked), done: green,
 *    then the page closes itself after QR_DONE_MS so the app's page, signed in now, shows; fail
 *    (the server not reached) and error (anything else): red.
 * The status row names the app (addon_qr_title). While the page is open /tmp/q2-<id>.qrpage exists,
 * so the app knows the page was left by its going ("signin" in ADDON_CAPS), not by guessing from
 * the pauses in its page's refreshes, which the screen turning off makes too. The wheel does
 * nothing here, as on every page that is not a playback screen (volume.c vol_wm_keyup). */
/* The largest code the apps send: their encoder (q2-subsonic internal/qr) takes the smallest
 * version that holds the URL, up to 40, 177 modules, its rows QR_HEX bytes of hex; the status file
 * is read as far as QR_FILE, room for that and the page's other fields. Both are the page's, on
 * the heap while it is open. */
#define QR_MAX 177
#define QR_HEX ((QR_MAX + 3) / 4 * QR_MAX + 1)
#define QR_FILE (QR_HEX + 1024)
#define QR_PLATE                                                                                   \
    166 /* the code's white plate: a 29-module code's 5 px modules, 2 quiet each side */
#define QR_PLATE_Y 4  /* its top, on the page: under the status row */
#define QR_SCAN_Y 192 /* the note's centre, then the address line's */
#define QR_ADDR_Y 216
#define QR_SCAN_PX TYPE(17) /* Inter Medium 17 */
#define QR_ADDR_PX TYPE(15) /* Inter Regular 15 */
#define QR_DONE_MS 1500     /* Signed In shows this long before the app's page */
typedef struct {
    void *page, *view;
    unsigned timer, leave, done, seen; /* seen: the state file as last read, hashed */
    const char *state;
    char marker[48]; /* /tmp/q2-<id>.qrpage */
    char name[32];   /* the app's, the status row's title */
    int size, code;  /* code: the file has one to draw */
    char step[8], title[64], line[96], addr[64], pin[16], note[128];
    char hex[QR_HEX];  /* the code's rows */
    char raw[QR_FILE]; /* the status file as last read */
} qr_page;
static qr_page *qp __attribute__((section(".scratch"))); /* calloc'd with the page, 0 without one */

static int qr_done(const void *unused) {
    (void)unused;
    if (!qp) return 0;
    qp->done = 0;
    if (window_manager_get_top_window(window_manager()) == qp->page) navigator_back();
    return 0;
}

static void qr_refresh(void) {
    char *raw = qp->raw;
    int n = (int)state_read(qp->state, raw, QR_FILE);
    unsigned h = hash_bytes(FNV_SEED, (const unsigned char *)raw, (unsigned)n + 1);
    if (h == qp->seen) return;
    qp->seen = h;
    field(raw, "addr", qp->addr, sizeof qp->addr);
    field(raw, "pin", qp->pin, sizeof qp->pin);
    field(raw, "note", qp->note, sizeof qp->note);
    field(raw, "qr", qp->hex, QR_HEX);
    field(raw, "step", qp->step, sizeof qp->step);
    field(raw, "title", qp->title, sizeof qp->title);
    field(raw, "line", qp->line, sizeof qp->line);
    if (!n) qp->step[0] = 0; /* not running: say so, whatever the last step was */
    qp->size = (int)number(raw, "qrsize");
    int w = (qp->size + 3) / 4;
    qp->code = qp->addr[0] && qp->size >= 21 && qp->size <= QR_MAX &&
               (int)strlen(qp->hex) == w * qp->size;
    if (!qp->code) {
        tk_snprintf(qp->title, sizeof qp->title, "%s",
                    !n            ? "Not running"
                    : qp->addr[0] ? "No code yet"
                                  : "Connect the Q2 to Wi-Fi");
        tk_snprintf(qp->line, sizeof qp->line, "%s",
                    !n            ? "Open it again"
                    : qp->addr[0] ? "Wait a moment"
                                  : "Then come back here");
    }
    if (!strcmp(qp->step, "done") && !qp->done) rearm(&qp->done, qr_done, QR_DONE_MS);
    widget_invalidate_force(qp->view, 0);
}

static int qr_tick(const void *unused) {
    (void)unused;
    if (g_backlight_status && qp) qr_refresh();
    return 8; /* RET_REPEAT */
}

static int qr_leave(const void *unused) {
    (void)unused;
    qp->leave = 0;
    navigator_back();
    return 0;
}

static int qr_keyup(void *ctx, void *event) {
    (void)ctx;
    return return_key(event, &qp->leave, qr_leave);
}

static int qr_closed(void *ctx, void *event) {
    (void)ctx, (void)event;
    if (!qp) return 0;
    stop_timer(&qp->timer);
    stop_timer(&qp->leave);
    stop_timer(&qp->done);
    unlink(qp->marker);
    free(qp);
    qp = 0;
    return 0;
}

static void qr_open(const char *state, const char *title) {
    if (qp || !(qp = calloc(1, sizeof *qp))) return;
    if (!(qp->page = page_open("qrcode_page", qr_closed, qr_keyup))) {
        free(qp), qp = 0;
        return;
    }
    qp->state = state;
    tk_snprintf(qp->name, sizeof qp->name, "%s", title);
    /* /tmp/q2-<id>.state's marker: /tmp/q2-<id>.qrpage */
    unsigned k = strlen(state);
    k = k > 6 && !strcmp(state + k - 6, ".state") ? k - 6 : k;
    tk_snprintf(qp->marker, sizeof qp->marker, "%.*s.qrpage", (int)k, state);
    void *f = fopen(qp->marker, "wb");
    if (f) fclose(f);
    qp->view = widget_factory_create_widget(widget_factory(), "view", qp->page, 0,
                                            bar_foot(qp->page), 375, page_room(qp->page));
    qp->seen = 0; /* nothing read yet: no read hashes to 0 but by chance, and then only once */
    qr_refresh();
    qp->timer = timer_add(qr_tick, 0, 500);
}

/* The status row's title on the QR page: the app's name (navigation.c bar_title_of); 0 elsewhere.
 */
const char *addon_qr_title(void *win) { return qp && win == qp->page ? qp->name : 0; }

static void plate(void *canvas, int x, int y, int w, int h, unsigned color, unsigned radius) {
    int r[4] = { x, y, w, h };
    if (canvas_fill_rounded_rect(canvas, r, (void *)0, &color, radius)) {
        canvas_set_fill_color(canvas, color);
        canvas_fill_rect(canvas, x, y, w, h);
    }
}

/* s, UTF-8, centred on the line at y (the page's) in face at px, in a final 0xRRGGBB. */
static void qr_text(void *canvas, const char *s, int y, const char *face, unsigned px,
                    unsigned rgb) {
    unsigned raw = ui_ink_raw(1);
    ui_line(canvas, s, 8, 375 - 16, y, 1, face, (int)px, rgb);
    ui_ink_raw(raw);
}

/* The page as qr_refresh read it, on qp->view (its rows count from the status row's foot). */
static void qr_paint(void *w, void *canvas) {
    unsigned ink = tok(TOK_PRIMARY), ter = tok(TOK_TERTIARY);
    if (qp->code && !qp->step[0]) {
        /* a code past 162 modules: a plate of a pixel a module and its quiet zone */
        int n = qp->size, side = n + 4 > QR_PLATE ? n + 4 : QR_PLATE, x0 = (375 - side) / 2,
            y0 = QR_PLATE_Y, m = side / (n + 4), o = (side - m * n) / 2, digits = (n + 3) / 4;
        unsigned raw = ui_ink_raw(0);
        plate(canvas, x0, y0, side, side, 0xffffffffu, 12);
        canvas_set_fill_color(canvas, rgba(0x000000, 255));
        for (int y = 0; y < n; ++y)
            for (int x = 0; x < n;) {
                int run = 0;
                for (; x + run < n; ++run) {
                    int d = hex(qp->hex[y * digits + (x + run) / 4]);
                    if (d < 0 || !(d >> (3 - (x + run) % 4) & 1)) break;
                }
                if (run) canvas_fill_rect(canvas, x0 + o + x * m, y0 + o + y * m, run * m, m);
                x += run ? run : 1;
            }
        ui_ink_raw(raw);
        char a[112], spaced[16];
        if (strlen(qp->pin) == 6) /* 548 001 */
            memcpy(spaced, qp->pin, 3), spaced[3] = ' ', memcpy(spaced + 4, qp->pin + 3, 4);
        else
            tk_snprintf(spaced, sizeof spaced, "%s", qp->pin);
        tk_snprintf(a, sizeof a, spaced[0] ? "or open %s  \xc2\xb7  PIN %s" : "or open %s",
                    qp->addr, spaced);
        qr_text(canvas, qp->note[0] ? qp->note : "Scan with your phone", QR_SCAN_Y, "inter-medium",
                QR_SCAN_PX, ink);
        qr_text(canvas, a, QR_ADDR_Y, "inter", QR_ADDR_PX, ter);
        return;
    }
    const char *s = qp->step;
    unsigned ring = !strcmp(s, "done")                          ? RGB_POSITIVE
                    : !strcmp(s, "fail") || !strcmp(s, "error") ? RGB_NEGATIVE
                                                                : scheme()->tones[0] & 0xffffffu;
    int known = !strcmp(s, "phone") || !strcmp(s, "check") || !strcmp(s, "done") ||
                !strcmp(s, "fail") || !strcmp(s, "error");
    char glyph[24];
    tk_snprintf(glyph, sizeof glyph, "modepage_%s", s);
    mode_page_paint(w, canvas, widget_get_prop_int(qp->page, "y", 0),
                    &(mode_page_t){ known ? ring : MODE_BARE, known ? glyph : (const char *)0, 0,
                                    qp->title, qp->line, (const char *)0 });
}

/* ringnav_paint, every widget after stock painted it: a progress row's bar, a flat rectangle filled
 * position/1000 of its width from the left, square, with no knob (the accent's light tone, as
 * Spotify's bar, over a dim track; the scrubber's rows under a theme that has them); and the code,
 * dark modules on the white plate, a row's run of them one rectangle, inside a quiet zone of two
 * modules. */
void addon_paint(void *w, void *canvas) {
    if (pg.page && pg.depth && top()->lay && window_of(w) == pg.page) artist_paint(w, canvas);
    /* the height first: no prop lookup for the page's other widgets */
    int pml = pg.page && I(w, W_H) == PROGRESS_H && P(canvas, CANVAS_LCD)
                  ? widget_get_prop_int(w, "_pml", -1)
                  : -1;
    if (pml >= 0) {
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        int bw = I(w, W_W), h = I(w, W_H), x = bw * pml / 1000;
        canvas_set_fill_color(canvas, rgba(PROGRESS_TRACK, 255));
        canvas_fill_rect(canvas, 0, 0, bw, h);
        canvas_set_fill_color(canvas, rgba(accent_tone(2), 255));
        if (x) canvas_fill_rect(canvas, 0, 0, x, h);
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    if ((pg.pend || pg.hold) && w == pg.page) load_spinner_paint(w, canvas); /* over the page */
    if (!qp || w != qp->view || !P(canvas, CANVAS_LCD)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    qr_paint(w, canvas);
    stock_canvas_set_fill_color(canvas, fill);
}
