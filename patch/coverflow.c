/* Coverflow (docs/internals.md): the stock Local Music albums on a stock slide_menu, the way
 * PictureFlow reads Rockbox's database. No tag reads: albums and tracks come from the stock library
 * queries, and the only thing Coverflow owns is a thumbnail cache, built on a modal screen by one
 * pthread that touches only files, the two stock art locks and the volatile counters below. */
#include "coverflow.h"
#include "playlist_art.h"

#define LAST_ALBUM ART_DIR "/album"  /* the centre album's key, so a reboot opens on it */
#define BUILDING ART_DIR "/building" /* the key and track of the album the worker is on */
#define CRASH_LOG PEQ_ROOT "/mnt/mmc/q2-crash.log"
#define MIN_FREE_MB 16 /* no new cache files below this much free space on the card */
#define DATA_ROOM_MB 8 /* /mnt/data free for library_scan's three larger indexes */
#define ART_NEAR 3     /* real art only this many covers either side, like PictureFlow's cache */
/* Home's art with no cover, at 290px (stock's: 110) */
#define HOME_PLACEHOLDER "default_album_home"

extern int stock_home_trampoline(void *win, void *ctx), stock_scan_all_trampoline(void *, void *),
    stock_scan_folder_trampoline(void *, void *), stock_delete_song_trampoline(void *, void *),
    stock_file_created_trampoline(const char *),
    stock_album_cover_trampoline(const char *, const char *, int, int);

extern void storage_scanned(void); /* storage.c: a whole scan's end, saved */

enum { PREPARING, COVERS, TRACKS };
cf_page_t cf __attribute__((section(".scratch")));

static void pick(int i);
static int pick_child(void *ctx, void *event);

/* The album list is kept across opens until songtable changes. Only these three stock functions
 * write it; each moves the generation before and after it runs, so a list queried meanwhile is
 * never kept. The scans run on stock's scan thread, and first walk the card for folder markers. */
volatile unsigned library_gen __attribute__((section(".scratch"))); /* also Most Played's */
static int library_write(int (*stock)(void *, void *), void *a, void *b) {
    ++library_gen;
    int result = stock(a, b);
    ++library_gen;
    return result;
}
/* Stock's songtable has only its primary key, so an album's songs (getMusicByAlbum's like, on the
 * UI thread at each album open), a file's row (deleteMusicFromMusicDb, the folder scan's check and
 * refresh) and Recently Added each read the whole table. Update Local Music drops the table, and
 * its indexes with it, so a scan ends by adding them: on its own thread and stock's shared handle,
 * while the library still reads as scanning. A library no scan has run on since has none and reads
 * as before. A reader on another connection makes the create fail (after the 500 ms the shared
 * connection waits since playlist art, stock_db_open), until the next scan. time_create is desc:
 * an index keeps equal keys in rowid order, as stock's sort did,
 * so songs of one second (a CUE image's tracks) keep their order newest first (Recently Added,
 * getAllMusic(3)); oldest first (getAllMusic(4)) library_query adds id for it. ponytail: the album
 * groupings (getAllAlbum, an artist's albums; ALBUM_SQL on songtable_group) walk their index too, a
 * row lookup each in album order: over twice a scan's page reads (docs/internals.md). A full scan
 * (drops): toolsInitDatabase drops songtable and creates it again on the connection the tools*
 * functions share, which waits only 500 ms for a reader (stock_db_open). A count still reading
 * songtable on its own connection made the drop fail, then the create, and stock skipped the update
 * without a word. So the scan's thread waits for the counts first (library_counting), outside
 * SQLite; Back ends the wait and the scan. */
static int library_scan(int (*stock)(void *, void *), void *a, void *b, int drops) {
    if (drops) {
        while (library_counting() && !toolsGetUserStop()) usleep(100000);
        if (toolsGetUserStop()) return 1; /* as wait_scan skips a stopped scan: nothing changes */
    }
    library_scope_scan();
    int result = library_write(stock, a, b);
    /* ALBUM_GROUP's three terms (as its text: SQLite matches an index's expression to the query's),
     * and the Unknown rows' album="" and album-artist="" checks (docs/internals.md#coverflow,
     * Indexes): 1.7 MB at 20,000 songs, so only with DATA_ROOM_MB free, and only once a full scan
     * has dropped the table. SQLite takes the newest of two indexes that serve a query equally, so
     * songtable_group, made before songtable_album, leaves getAllAlbum and the other album
     * groupings on songtable_album, each group's row as before. */
    if (drops && card_room("/mnt/data", DATA_ROOM_MB, 0))
        toolsUpdateDbTable(
            "/mnt/data/database.db",
            "create index if not exists songtable_group on songtable(album collate nocase,"
            "ifnull(albumsonger,'') collate nocase,case ifnull(albumsonger,'') when '' then "
            "rtrim(fileurl,replace(fileurl,'/','')) end);"
            "create index if not exists songtable_album_exact on songtable(album);"
            "create index if not exists songtable_artist on "
            "songtable(ifnull(nullif(albumsonger,''),songer))");
    toolsUpdateDbTable(
        "/mnt/data/database.db",
        "create index if not exists songtable_album on songtable(album collate nocase);"
        "create index if not exists songtable_filename on songtable(filename);"
        "create index if not exists songtable_time_create on songtable(time_create desc)");
    /* Scan Song Durations' pass after a whole scan: a stopped one leaves part of the library */
    if (!toolsGetUserStop()) durations_scanned();
    if (drops && !toolsGetUserStop()) storage_scanned(); /* Storage's Update Library: last updated */
    return result;
}
int coverflow_scan_all(void *a, void *b) {
    return library_scan(stock_scan_all_trampoline, a, b, 1);
}
/* toolsGetFileCreateTime (0x5d0954) dates a song (time_create, Recently Added's order) by stat's
 * st_ctime, on FAT32 the directory entry's creation time, but by st_atime on exFAT cards, where
 * stat has no creation time; every read moves that, so a full update dated the whole library to
 * its own day (#170, #188). statx reports the creation stamp exFAT keeps (stock's kernel has the
 * mainline driver); the o32 syscall itself, as demo imports no statx. docs/internals.md has the
 * rest. ponytail: a file whose filesystem reports no stamp takes stock's date and drifts as
 * before. */
#ifndef PEQ_HOST
__asm__(".globl ringnav_statx\nringnav_statx:\nli $v0, 4366\nsyscall\njr $ra\nnop\n");
int ringnav_file_created(const char *path) {
    unsigned sx[64]; /* struct statx: stx_mask first, stx_btime.tv_sec at byte 80 */
    if (ringnav_statx(-100, path, 0, 0x800, sx) == 0 && (sx[0] & 0x800) && sx[20])
        return (int)sx[20];
    return stock_file_created_trampoline(path);
}
#endif
int coverflow_scan_folder(void *a, void *b) {
    return library_scan(stock_scan_folder_trampoline, a, b, 0);
}
int coverflow_delete_song(void *a, void *b) {
    return library_write(stock_delete_song_trampoline, a, b);
}

/* The queue menu resolves the live track list again after its dialog closes. */
void *coverflow_tracks(void *page) {
    return page == cf.page && cf.screen == TRACKS ? cf.tracks : 0;
}

/* The live cover at the centre, an album's or a playlist's: never a Sort card, nor with a screen
 * change due */
static void *centred(void *page, int playlists) {
    if (page != cf.page || cf.screen != COVERS || !cf.slide || cf.timer ||
        (cf.scoped == PLAYLISTS) != playlists)
        return 0;
    unsigned i = (unsigned)I(cf.slide, SLIDE_INDEX);
    return cf.albums && i < deque_size(cf.albums) ? deque_at(cf.albums, i) : 0;
}
void *coverflow_album(void *page) { return centred(page, 0); }
/* The playlist a hold is on (navigation.c), its container record, a copy of the list's */
void *coverflow_playlist(void *page) { return centred(page, 1); }

unsigned fnv(unsigned h, const unsigned char *s) {
    while (s && *s) h = hash_bytes(h, s++, 1);
    return h * 16777619u; /* a separator, so "ab"+"c" and "a"+"bc" differ */
}

unsigned tags_key(const char *artist, const char *album) {
    return fnv(fnv(FNV_SEED, (const unsigned char *)artist), (const unsigned char *)album);
}

unsigned album_key(void *r) { return tags_key(P(r, REC_ARTIST), P(r, REC_ALBUM)); }

/* An album's artist as Artists sets it: with Album Artist (artist_type 1), its album artist tag
 * when set, so a compilation reads "Various Artists" or its compiler, not its last track's artist.
 */
static const char *cover_artist(void *r) {
    const char *aa = P(r, REC_ALBUM_ARTIST);
    return I(artist_type, 0) == 1 && aa && *aa ? aa : P(r, REC_ARTIST);
}

/* Track selection uses ringnav's existing position memory, keyed by this album; a playlist's has
 * none. */
unsigned coverflow_scope(void *page) {
    return coverflow_tracks(page) && cf.scoped != PLAYLISTS
               ? album_key(deque_at(cf.albums, (unsigned)cf.album))
               : 0;
}

/* ART_DIR/<key>.jpg<suffix>; out holds at least 512 bytes. */
char *art_path(char *out, unsigned key, const char *suffix) {
    tk_snprintf(out, 512, ART_DIR "/%08x.jpg%s", key, suffix);
    return out;
}
/* Asks the image cache for a cover only once its file exists: a load made before the worker wrote
 * it stays a failure, and the placeholder would stay until a reboot. url is "file://" + the path.
 */
static int art_exists(const char *url) { return !access(url + 7, 0); }

/* path's size, -1 when it can't be opened */
long file_size(const char *path) {
    void *f = fopen(path, "rb");
    long size = f && !fseek(f, 0, 2) ? ftell(f) : -1;
    if (f) fclose(f);
    return size;
}

/* Runs that never reached ffmpeg's own exit, from any thread (counted atomically, so none is
 * lost); build_art and playlist_art.c compare before and after. */
volatile unsigned ff_kills __attribute__((section(".scratch")));
/* The wait for ffmpeg's child pid, writing tmp, which is renamed onto dst on success; shared with
 * playlist_art.c's 2x2. */
int ff_wait(int pid, const char *tmp, const char *dst, int *exited) {
    int status = -1;
    while (pid > 0 && waitpid(pid, &status, 0) < 0 &&
           *__errno_location() == 4) { /* EINTR: reap it */
    }
    /* WIFEXITED, less execl's failure (127: out of memory, as ffmpeg is stock's); never for -1 */
    *exited = !(status & 0x7f) && ((status >> 8) & 0xff) != 127;
    if (!*exited) __atomic_fetch_add(&ff_kills, 1, __ATOMIC_RELAXED);
    if (!status && file_size(tmp) > 0 && !rename(tmp, dst)) return 1;
    unlink(tmp);
    return 0;
}

/* Stock ffmpeg's first picture of src (an image, or an audio file's embedded one) fitted into w x h
 * as a JPEG at dst: what stock's decoder refuses (WebP, a PNG over 1 MB, a JPEG over 6 MB). Uses no
 * lock: it writes only dst.ff, then renames it onto dst. ffmpeg creates its output before decoding,
 * so a picture it can't decode leaves an empty file, even on exit 0, and a killed run a partial
 * one; either is removed and dst is left alone, as stock's caches take any file for art.
 * -xerror stops at a picture's first decode error rather than demuxing the rest of the file.
 * *exited: ffmpeg ran to its own exit (no refused fork, no kill), so a failure is the picture's.
 * ponytail: a process per cover; a resident decoder if first builds drag. */
static int ff_run(const char *src, const char *dst, int w, int h, int *exited) {
    char vf[80], tmp[1040];
    tk_snprintf(vf, sizeof(vf), "scale=%d:%d:force_original_aspect_ratio=decrease", w, h);
    tk_snprintf(tmp, sizeof(tmp), "%s.ff", dst);
    int pid = fork();
    if (!pid) {
        child_oom_first(); /* its one picture can need tens of MB (#169) */
        execl(ART_FFMPEG, ART_FFMPEG, "-nostdin", "-v", "quiet", "-xerror", "-i", src, "-frames:v",
              "1", "-vf", vf, "-pix_fmt", "yuvj420p", "-q:v", "3", "-f", "mjpeg", "-y", tmp,
              (char *)0);
        child_exit(127);
    }
    return ff_wait(pid, tmp, dst, exited);
}

/* shared with playlist_art.c, for a playlist's own picture, which stock's decoder never sees */
int ff_thumb(const char *src, const char *dst, int w, int h) {
    int exited;
    return ff_run(src, dst, w, h, &exited);
}

static unsigned be16(const unsigned char *p) { return p[0] << 8 | p[1]; }

#define PICTURE_PNG 1
#define PICTURE_JPEG 0xd8 /* a JPEG whose frame header wasn't found: its size is unknown */

/* path's picture size in *w and *h, read from its header; 0 when it isn't a picture we know, else
 * PICTURE_PNG, a JPEG's frame marker (0xc2: progressive) or PICTURE_JPEG. A PNG's size past 16 bits
 * reads as 65536. Magic bytes as toolsGetPictureInfo tells them; the JPEG's frame header is looked
 * for in its first 64 segments, a fill byte (0xff before a marker) counting as one. Shared with
 * addon.c. */
int picture_size(const char *path, int *w, int *h) {
    unsigned char b[24];
    void *f = fopen(path, "rb");
    int got = f && fread(b, 1, sizeof b, f) == sizeof b, kind = 0;
    if (got && b[0] == 0x89 && b[1] == 'P' && !memcmp(b + 12, "IHDR", 4)) { /* its width, height */
        kind = PICTURE_PNG;
        *w = be16(b + 16) ? 65536 : (int)be16(b + 18);
        *h = be16(b + 20) ? 65536 : (int)be16(b + 22);
    } else if (got && b[0] == 0xff && b[1] == 0xd8) {
        kind = PICTURE_JPEG;
        for (long at = 2, n = 0; n < 64 && !fseek(f, at, 0) && fread(b, 1, 9, f) == 9 &&
                                 b[0] == 0xff && b[1] != 0xda; /* image data follows */
             ++n, at += b[1] == 0xff ? 1 : 2 + be16(b + 2))
            if (b[1] >> 4 == 0xc && b[1] != 0xc4 && b[1] != 0xc8 && b[1] != 0xcc) {
                /* a frame header, SOF0 to SOF15: SOF2 is the Huffman progressive one */
                kind = b[1], *h = (int)be16(b + 5), *w = (int)be16(b + 7);
                break;
            }
    }
    if (f) fclose(f);
    return kind;
}

/* Whether stock's decoder would hold path's whole picture in demo, which the OOM killer would then
 * take (#216): a PNG over 1024 px either way (its reader, 0x5c91bc, expands the image with
 * png_read_png before its own 1024 px test) or a progressive JPEG over 1024 px (libjpeg keeps a
 * full-size coefficient buffer at any scale), and a JPEG it can't size, which may be one
 * (GHSA-w54x-mvfw-c7v3).
 * ponytail: only SOF2, as ffmpeg 4.2 decodes no arithmetic (SOF10) or hierarchical frames; a
 * progressive cover ffmpeg's -xerror rejects (cut short) no longer falls back to stock. */
static int big_picture(const char *path) {
    int w = 0, h = 0, kind = picture_size(path, &w, &h);
    return kind == PICTURE_JPEG ||
           ((kind == PICTURE_PNG || kind == 0xc2) && (w > 1024 || h > 1024));
}

/* stock's thumbnailer, under the lock every stock caller holds, else ffmpeg. Shared (peq.h).
 * A picture stock would decode whole in demo goes to ffmpeg alone, whose child the kernel kills
 * first. */
int thumb(const char *src, const char *dst, int w, int h) {
    int ok = 0;
    if (!big_picture(src)) {
        pthread_mutex_lock((void *)parse_cover_mutex);
        ok = toolsThumbSpecCover(src, dst, w, h) == 1;
        pthread_mutex_unlock((void *)parse_cover_mutex);
    }
    return ok || (!access(src, 4) && ff_thumb(src, dst, w, h)); /* none for a missing file */
}

/* Which albums share a folder with another album (job_t.shared), one byte per album; 0 without
 * memory. Albums with a path are sorted by folder (its FNV hash, then the folder itself, then the
 * album key), so each folder's albums sit together, and a folder holding two keys marks all of
 * them. A scan of every album per album here was albums squared on the UI thread, the likely
 * (inferred, not reproduced) cause of a big library's first open stalling past the watchdog. */
typedef struct {
    unsigned hash, len, key, idx;
    const char *path;
} folder_t;
static int folder_before(const void *a, const void *b) {
    const folder_t *x = a, *y = b;
    int c = x->hash != y->hash ? (x->hash < y->hash ? -1 : 1)
            : x->len != y->len ? (x->len < y->len ? -1 : 1)
                               : memcmp(x->path, y->path, x->len);
    return c ? c : (x->key > y->key) - (x->key < y->key);
}
/* noinline: test/patch.py calls it */
__attribute__((noinline)) static unsigned char *folders_shared(void *albums, unsigned count) {
    folder_t *v = calloc(count + 1, sizeof *v);
    unsigned char *shared = calloc(count + 1, 1);
    unsigned n = 0;
    for (unsigned i = 0; v && shared && i < count; ++i) {
        void *r = deque_at(albums, i);
        const char *p = P(r, REC_PATH), *s = p ? strrchr(p, '/') : 0;
        if (s)
            v[n++] = (folder_t){ hash_bytes(FNV_SEED, (const unsigned char *)p, s - p), s - p,
                                 album_key(r), i, p };
    }
    if (n) qsort(v, n, sizeof *v, folder_before);
    for (unsigned a = 0, b; a < n; a = b) {
        for (b = a + 1; b < n && v[b].hash == v[a].hash && v[b].len == v[a].len &&
                        !memcmp(v[b].path, v[a].path, v[a].len);
             ++b);
        for (unsigned k = a; v[a].key != v[b - 1].key && k < b; ++k) shared[v[k].idx] = 1;
    }
    if (!v) free(shared), shared = 0;
    free(v);
    return shared;
}

/* The cover and folder images beside track through fit (none when shared: a folder shared with
 * other albums, whose cover.jpg would be theirs too). */
static int beside_art(const char *track, const char *dst, int w, int h, int shared,
                      int (*fit)(const char *, const char *, int, int)) {
    char src[1024];
    const char *slash = strrchr(track, '/');
    int folder = slash ? (int)(slash - track) : 0, ok = 0;
    static const char *const names[] = { "cover.jpg",  "folder.jpg", "cover.png",
                                         "folder.png", "cover.webp", "folder.webp" };
    for (unsigned i = 0; i < sizeof(names) / sizeof(*names) && !ok && !shared; ++i) {
        if (snprintf(src, sizeof(src), "%.*s/%s", folder, track, names[i]) >= (int)sizeof src)
            continue; /* cut: a prefix of the name, another file's at best */
        ok = !access(src, 4) && fit(src, dst, w, h);
    }
    return ok;
}

/* Whether Coverflow marked the album of the track the player parsed (g_play_id3_info, keyed as
 * Home's art is) empty: its build found no art for the album's track. */
static int marked_empty(const char *track) {
    const char *id3 = (const char *)g_play_id3_info;
    if (tk_strcmp(id3, track)) return 0;
    char art[512];
    return !file_size(art_path(art, tags_key(id3 + ID3_ARTIST, id3 + ID3_ALBUM), ""));
}

/* Now Playing's art for a server song is its app's cover of the album through ffmpeg, a fork of
 * demo, under g_playcover_mutex, where stock's in-process thumbnailer cannot go: it takes
 * parse_cover_mutex, which Coverflow holds before g_playcover_mutex. So the fit is kept: the last
 * cover fitted, its size and the JPEG ffmpeg made of it in NP_COVER_FILE, copied to the next
 * track's dst of the same album and size without a process. One album, so the album played
 * through costs one ffmpeg run, not one a track. ponytail: one album kept, and a cover the app
 * writes again under the same path shows its old fit until another album plays or a reboot; key
 * on the cover's size and time too if an app replaces covers. */
#define NP_COVER_FILE "/tmp/q2-npcover.jpg"
static struct {
    unsigned key[2]; /* the cover's path, hashed from two seeds; 0 none kept */
    int w, h;
} np_fit __attribute__((section(".scratch")));
/* src's bytes into dst, through dst.np renamed over it; 1 when whole. */
static int file_copy(const char *src, const char *dst) {
    char tmp[1040], buf[4096];
    tk_snprintf(tmp, sizeof tmp, "%s.np", dst);
    void *in = fopen(src, "rb"), *out = in ? fopen(tmp, "wb") : 0;
    unsigned long n, total = 0;
    int ok = in && out;
    while (ok && (n = fread(buf, 1, sizeof buf, in)) > 0)
        ok = fwrite(buf, 1, n, out) == n, total += n;
    if (in) fclose(in);
    if (out) ok = !fclose(out) && ok && total;
    if (ok && !rename(tmp, dst)) return 1;
    unlink(tmp);
    return 0;
}
static int np_cover(const char *cover, const char *dst, int w, int h) {
    unsigned n = (unsigned)strlen(cover),
             key[2] = { hash_bytes(FNV_SEED, (const unsigned char *)cover, n) | 1,
                        hash_bytes(0x9e3779b9u, (const unsigned char *)cover, n) };
    if (np_fit.key[0] == key[0] && np_fit.key[1] == key[1] && np_fit.w == w && np_fit.h == h &&
        file_copy(NP_COVER_FILE, dst))
        return 1;
    np_fit.key[0] = 0;
    if (!ff_thumb(cover, dst, w, h)) return 0;
    if (file_copy(dst, NP_COVER_FILE))
        np_fit.key[0] = key[0], np_fit.key[1] = key[1], np_fit.w = w, np_fit.h = h;
    return 1;
}

/* player_parsecover_thd's folder-image call, after its embedded art failed, under
 * g_playcover_mutex: what stock refuses (a PNG over 1 MB or palette/greyscale, WebP) goes to
 * ffmpeg, so Now Playing gets the art Coverflow shows. ff_thumb takes no lock, so Coverflow's
 * parse-then-play order cannot deadlock with it; a server song's app cover goes through np_cover,
 * one ffmpeg run an album. The folder images cost a process only when one
 * exists; the embedded probe, a process for every track without art, is skipped for an album
 * Coverflow marked empty. ponytail: the marker is one track's verdict, so another disc's or a
 * same-named album's embedded-only WebP or large PNG shows no art here; a per-track probe cache
 * if that matters. */
int coverflow_player_cover(const char *track, const char *dst, int w, int h) {
    char cover[512]; /* a server song's offline copy (catalogue.c): its app's cover of the album */
    return toolsGetExternCover(track, dst, w, h) == 1 ||
           beside_art(track, dst, w, h, 0, ff_thumb) ||
           (catalogue_cover(track, cover, sizeof cover) && np_cover(cover, dst, w, h)) ||
           (!marked_empty(track) && ff_thumb(track, dst, w, h));
}

/* parse_albumcovertask_thd's embedded-art calls (Albums grids; Folder view's folder and track
 * rows), made on stock's one cover thread under parse_cover_mutex: stock reads no Ogg picture, so
 * an .opus or .ogg track's goes to ffmpeg. The lock is dropped for ffmpeg, as the UI thread takes
 * it for Library's online thumbnails and Spotify's art: the call's paths are the thread's own, and
 * stock's fallback after it runs with the lock held again. The Albums grid's call (album) reads the
 * folder image first, which stock would only after it, so an Ogg album whose art is a cover.jpg
 * costs no process; that also orders Ogg art as Coverflow does. Stock caches only a hit, so a track
 * ffmpeg found no picture in is remembered (not one gone with the card, nor a refused fork or a
 * killed ffmpeg). ponytail: 1024 misses until a reboot, oldest overwritten; a picture since added
 * to a track shows after a reboot. */
static int grid_cover(const char *track, const char *dst, int w, int h, int album) {
    static unsigned *miss __attribute__((section(".scratch"))),
        n __attribute__((section(".scratch")));
    const char *dot = strrchr(track, '.');
    char cover[512];
    /* a server row (catalogue.c): no file, its app's cover of the album, a small baseline JPEG */
    if (catalogue_path(track))
        return catalogue_cover(track, cover, sizeof cover) &&
               toolsThumbSpecCover(cover, dst, w, h) == 1;
    if (toolsGetAlbumCover(track, dst, w, h) == 1) return 1;
    if (!dot || (strcasecmp(dot, ".opus") && strcasecmp(dot, ".ogg"))) return 0;
    unsigned key = fnv(FNV_SEED, (const unsigned char *)track);
    if (!miss) miss = calloc(1024, sizeof(*miss)); /* heap, as the scratch page is nearly full */
    for (unsigned i = 0; miss && i < n && i < 1024; ++i)
        if (miss[i] == key) return 0;
    if (album && toolsGetExternCover(track, dst, w, h) == 1) return 1;
    pthread_mutex_unlock((void *)parse_cover_mutex);
    int exited, ok = ff_run(track, dst, w, h, &exited);
    pthread_mutex_lock((void *)parse_cover_mutex);
    if (!ok && exited && miss && !access(track, 4)) miss[n++ % 1024] = key;
    return ok;
}

/* toolsGetAlbumCover's entry, so every embedded-art read: player_parsecover_thd's Now Playing art,
 * the GRID_COVER calls and build_art. libtag_c's taglib_album_picture gives an .ogg to
 * get_oggfile_albumpic, XiphComment::pictureList after TagLib's whole parse, about 4 times the
 * comment header (docs/internals.md#oversized-ogg-tags). One over OGG_TAGS_MAX gets none from
 * stock, so its caller's fallback (coverflow_player_cover, grid_cover, build_art) hands it to
 * ffmpeg, which the kernel kills before demo. Stock reads no .opus or .oga picture, so those are
 * not measured. */
#ifndef PEQ_HOST
int ringnav_album_cover(const char *track, const char *dst, int w, int h) {
    const char *dot = strrchr(track, '.');
    if (dot && !strcasecmp(dot, ".ogg") && ogg_tags(track, 0)) return 0;
    return stock_album_cover_trampoline(track, dst, w, h);
}
#endif

int coverflow_grid_cover(const char *track, const char *dst, int w, int h) {
    return grid_cover(track, dst, w, h, 0);
}

int coverflow_album_cover(const char *track, const char *dst, int w, int h) {
    return grid_cover(track, dst, w, h, 1);
}

/* Folder view's folder row: stock tries the folder's songs in directory order, the order they
 * were copied to the card, and keeps the first one's embedded art, so an album copied out of
 * order showed a later song's cover. The folder's first song by name, as Folder view lists it,
 * is tried first (not a dot file: a Mac's ._ copies sort first). Stock calls once a song until
 * one has art, so the folder's first song, and whether it had none, are kept for the walk's
 * next call; a hit ends the walk, and the next one lists the folder again. ponytail: only songs
 * with the extension of stock's are compared, by strcasecmp rather than the list's own
 * comparator; a folder with no art at all keeps its listing until another folder is shown. */
int coverflow_folder_cover(const char *track, const char *dst, int w, int h) {
    static struct {
        char dir[512], first[512];
        int bare; /* first has no art */
    } *f __attribute__((section(".scratch")));
    const char *slash = strrchr(track, '/'), *ext = strrchr(track, '.');
    unsigned n = slash ? (unsigned)(slash - track) : 0;
    if (!f) f = calloc(1, sizeof *f); /* heap, as the scratch page is nearly full */
    if (!f || !n || n >= sizeof f->dir || !ext || ext < slash)
        return grid_cover(track, dst, w, h, 0);
    if (memcmp(f->dir, track, n) || f->dir[n]) {
        char low[256] = "";
        memcpy(f->dir, track, n), f->dir[n] = 0, f->first[0] = 0, f->bare = 0;
        void *d = opendir(f->dir);
        for (struct dirent *e; d && (e = readdir(d));) {
            const char *x = strrchr(e->d_name, '.');
            if ((e->d_type == 8 || !e->d_type) && e->d_name[0] != '.' && x && !strcasecmp(x, ext) &&
                (!low[0] || strcasecmp(e->d_name, low) < 0))
                tk_snprintf(low, sizeof low, "%s", e->d_name);
        }
        if (d) closedir(d);
        if (low[0] &&
            tk_snprintf(f->first, sizeof f->first, "%s/%s", f->dir, low) >= (int)sizeof f->first)
            f->first[0] = 0; /* too long: stock's song alone */
    }
    if (f->first[0] && !f->bare && grid_cover(f->first, dst, w, h, 0)) return f->dir[0] = 0, 1;
    f->bare = 1;
    if (!strcmp(f->first, track) || !grid_cover(track, dst, w, h, 0)) return 0;
    return f->dir[0] = 0, 1;
}

/* A track's album art at dst, ART_SIZE: embedded art, then beside_art, then ffmpeg's read of the
 * embedded art, last as it costs a process for every album without art; shared with
 * playlist_art.c. */
int album_art(const char *track, const char *dst, int shared) {
    /* toolsGetAlbumCover goes through the shared /tmp/.tmp_picture, which stock guards with
     * either lock (parse_albumcovertask_thd, player_parsecover_thd); hold both. Stock never
     * nests them, so this order cannot deadlock. */
    pthread_mutex_lock((void *)parse_cover_mutex);
    pthread_mutex_lock((void *)g_playcover_mutex);
    int ok = toolsGetAlbumCover(track, dst, ART_SIZE, ART_SIZE) == 1;
    pthread_mutex_unlock((void *)g_playcover_mutex);
    pthread_mutex_unlock((void *)parse_cover_mutex);
    return ok || beside_art(track, dst, ART_SIZE, ART_SIZE, shared, thumb) ||
           ff_thumb(track, dst, ART_SIZE, ART_SIZE);
}

/* One album's art, written to .tmp and renamed. An empty file marks "no art", so the placeholder
 * shows and the album is not retried until Refresh Coverflow; a run ffmpeg did not finish leaves
 * none. ponytail: an album whose ffmpeg is killed every time costs one kill per open. */
static void build_art(const job_t *j) {
    char dst[512], tmp[512];
    /* A server row (catalogue.c) has no file: its app's cover of the album, which load_albums
     * queues only once the app has fetched it; one that won't decode is "no art". */
    char cover[512];
    if (catalogue_path(j->track)) {
        art_path(dst, j->key, "");
        art_path(tmp, j->key, ".tmp");
        if (catalogue_cover(j->track, cover, sizeof cover) &&
            thumb(cover, tmp, ART_SIZE, ART_SIZE) && !rename(tmp, dst))
            return;
        unlink(tmp);
        if (access(cover, 0)) return; /* gone meanwhile (Sign Out): the next open asks again */
        void *f = fopen(dst, "w");
        if (f) fclose(f);
        return;
    }
    if (access(j->track, 0)) return; /* storage gone: no marker, the next open retries */
    art_path(dst, j->key, "");
    art_path(tmp, j->key, ".tmp");
    unsigned kills = ff_kills;
    if (album_art(j->track, tmp, j->shared) && !rename(tmp, dst)) return;
    unlink(tmp);
    /* No marker when an ffmpeg run meanwhile never reached its own exit (a refused fork, or killed:
     * the kernel takes it first when memory runs out, #169; another thread's run counts too, a
     * retry at worst) or storage is gone: the next open retries. */
    if (kills != ff_kills || access(j->track, 0)) return;
    void *f = fopen(dst, "w");
    if (f) fclose(f);
}

/* note: the key and track of the album a worker is on, struck at its next start if this build
 * never ends; synced, see art_strike. Shared with playlist_art.c. */
void art_note(const char *note, unsigned key, const char *track) {
    void *f = fopen(note, "wb");
    if (f) {
        fwrite(&key, sizeof key, 1, f);
        fwrite(track, strlen(track), 1, f);
        fflush(f), fsync(fileno(f)), fclose(f);
    }
}

static unsigned art_counts[2]; /* crash_note's, every 50th album */
static void *worker(void *unused) {
    while (cf.done < cf.total && !cf.cancel) {
        crash_note(cf.jobs[cf.done].track, "/tmp/q2-art-file", "/tmp/q2-art.log", 50, art_counts);
        art_note(BUILDING, cf.jobs[cf.done].key, cf.jobs[cf.done].track);
        if (cf.cancel) break; /* a clean end since the loop's head: its note goes with the exit */
        build_art(&cf.jobs[cf.done]);
        ++cf.done;
    }
    unlink(BUILDING);
    return 0;
}

/* The album the last build never finished, the key in file (BUILDING, or playlist_art.c's
 * PL_BUILDING): demo died on it, or hung on it until the watchdog restarted the Q2, and nothing
 * marked it, so every open would try it again and end the same way. It takes the "no art" marker
 * (unless its cover is there: a death between the rename and the next note) until Refresh Cover
 * Flow. A clean end mid-build removes the note (coverflow_clean_end), as a power-off is no album's
 * fault. The note is synced in place, a sync per album, and not through a rename, whose directory
 * write a reset could lose: a tester's log (2026-11-09) showed the restart it must outlive is the
 * watchdog's, demo still alive and nothing flushed, with no "last art" line, so the note carries
 * the track and the strike appends it to the card's q2-crash.log, that restart's own by now (the
 * boot hook moved it there). A torn note reads short and strikes nothing, or names part of a path.
 * ponytail: the battery running out mid-build strikes an innocent album; Refresh puts it back. */
void art_strike(const char *file) {
    struct {
        unsigned key;
        char track[512];
    } note = { 0 };
    char path[512], line[560];
    void *f = fopen(file, "rb");
    unsigned got = f ? fread(&note, 1, sizeof note - 1, f) : 0;
    if (f) fclose(f);
    if (got >= sizeof note.key && access(art_path(path, note.key, ""), 0)) {
        if ((f = fopen(path, "w"))) fclose(f);
        int n = snprintf(line, sizeof line, "== cover flow skipped\n%s\n", note.track);
        if ((f = fopen(CRASH_LOG, "a")))
            fwrite(line, 1, n, f), fflush(f), fsync(fileno(f)), fclose(f);
    }
    unlink(file);
}
/* A clean end (power-off, Restart, an update, Rockbox): the worker stops after the album it is on,
 * writing no note for the next, which the kill that follows would leave for art_strike, and this
 * one's goes; playlist_art.c's worker too. */
void coverflow_clean_end(void) {
    cf.cancel = 1;
    unlink(BUILDING);
    playlist_art_clean_end();
}

/* A running worker cancelled and joined, and its poll timer stopped; 1 when one ran.
 * Shared (peq.h). */
int worker_stop(unsigned long thread, int *running, volatile int *cancel, unsigned *timer) {
    int ran = *running;
    if (ran) {
        *cancel = 1;
        pthread_join(thread, 0);
        *running = 0;
    }
    stop_timer(timer);
    return ran;
}

/* Whether dir's file system has mb MB free and pct percent of its size free (statfs, MIPS o32
 * layout: f_bsize is word 1, f_blocks word 3, f_bavail word 7). */
int card_room(const char *dir, unsigned mb, unsigned pct) {
    unsigned fs[32] = { 0 };
    if (statfs(dir, fs)) return 0;
    unsigned long long room = (unsigned long long)fs[7] * fs[1];
    return room >= (unsigned long long)mb << 20 &&
           room * 100 >= (unsigned long long)fs[3] * fs[1] * pct;
}
/* Whether dir's file system has MIN_FREE_MB free. Shared (peq.h). */
int card_space(const char *dir) { return card_room(dir, MIN_FREE_MB, 0); }

/* A warm open (docs/internals.md#coverflow): once a look over the library's albums found each
 * one's art cached (or a build made it), the next opens of the same albums (cf.stock, the
 * library_gen it was queried at, its size) show the covers at once, with no file asked for per
 * album on the UI thread, which cost 6,000 albums 20-28 ms on the emulator: a thread looks again
 * behind them (verify), and an album found without art makes the next open a cold one, which
 * builds it with Preparing artwork as before. The cover drawn meanwhile is the placeholder, as for
 * any album whose art file is missing (art_url). The first album is found from where it was last
 * (centre_of), so a reopen passes over the albums only when the queue's album moved. */
static struct {
    void *clean;                 /* cf.stock when every album's art was found, else 0 */
    unsigned clean_gen, clean_n; /* and its library_gen and size then */
    void *albums;                /* the list now_at and saved_at index */
    int now_at, saved_at;        /* the queue's album and the saved one there, + 1; 0 unknown */
    unsigned long thread;        /* verify's */
    int running;                 /* it was started and not joined yet */
    volatile int found, halt;    /* an album without art seen; the UI thread wants it to end */
} aw __attribute__((section(".scratch")));
static void art_cleaned(void) {
    aw.clean = cf.stock, aw.clean_gen = library_gen,
    aw.clean_n = cf.stock ? deque_size(cf.stock) : 0;
}
static int art_clean(void) {
    return aw.clean && aw.clean == cf.stock && aw.clean_gen == library_gen &&
           aw.clean_n == deque_size(cf.stock);
}
/* Album r's art: 0 cached, 1 not cached and a build could make it (load_albums' job test), 2 not
 * cached and nothing to make it from: a server album whose app has no cover file, drawn as the
 * placeholder, no job. */
static int art_state(void *r, char *path, unsigned size) {
    const char *p = P(r, REC_PATH);
    if (!p || !access(art_path(path, album_key(r), ""), 0)) return 0;
    return !catalogue_path(p) || catalogue_cover(p, path, size) ? 1 : 2;
}
static int art_missing(void *r, char *path, unsigned size) { return art_state(r, path, size) == 1; }
/* The thread: files and the albums' records only, read-only; every change to the list joins it
 * first (verify_join). */
static void *verify(void *albums) {
    char path[512];
    for (unsigned i = 0, n = deque_size(albums); i < n && !aw.halt && !aw.found; ++i)
        if (art_missing(deque_at(albums, i), path, sizeof path)) aw.found = 1;
    return albums;
}
static void verify_start(void) {
    aw.found = aw.halt = 0;
    aw.running = !pthread_create(&aw.thread, 0, verify, cf.albums);
}
/* verify stopped and joined, its finding kept: shared by every change to cf.albums or cf.stock. */
static void verify_join(void) {
    if (aw.running) {
        aw.halt = 1;
        pthread_join(aw.thread, 0);
        aw.running = 0;
    }
    if (aw.found) aw.clean = 0, aw.found = 0;
}
/* The album the covers open on: the queue's song's, else the saved one (cf.saved_album), else the
 * first, as load_albums' pass finds them; each looked for first where it was last. */
static int centre_of(void *now, unsigned count) {
    if (aw.albums != cf.albums) aw.albums = cf.albums, aw.now_at = aw.saved_at = 0;
    if (now) {
        int k = aw.now_at - 1;
        if (k >= 0 && (unsigned)k < count && !album_cmp(deque_at(cf.albums, (unsigned)k), now))
            return k;
        for (unsigned i = 0; i < count; ++i)
            if (!album_cmp(deque_at(cf.albums, i), now)) return aw.now_at = (int)i + 1, (int)i;
    }
    int k = aw.saved_at - 1;
    if (k >= 0 && (unsigned)k < count &&
        album_key(deque_at(cf.albums, (unsigned)k)) == cf.saved_album)
        return k;
    for (unsigned i = count; i-- > 0;) /* the pass's last match */
        if (album_key(deque_at(cf.albums, i)) == cf.saved_album)
            return aw.saved_at = (int)i + 1, (int)i;
    return 0;
}
/* changed(): album c is the one saved now, at c in cf.albums. */
static void centred_at(unsigned c) { aw.albums = cf.albums, aw.saved_at = (int)c + 1; }

/* A cold open's look over its albums' art, made on the open's step thread (OPEN_ART) while Home's
 * spinner turns, for load_albums to take: the art jobs of cf.albums as they were at library_gen. */
static struct {
    void *albums;
    unsigned gen, count;
    job_t *jobs; /* 0: none made (or no memory) */
    int total;
    volatile unsigned looked, bare; /* albums looked at so far; of them, art_state's 2 (the log) */
} aj __attribute__((section(".scratch")));
static void art_found_drop(void) {
    for (int i = 0; i < aj.total; ++i) free(aj.jobs[i].track);
    free(aj.jobs);
    memset(&aj, 0, sizeof aj);
}
/* cf.albums' jobs into aj, as load_albums' own pass makes them. Files and the albums' records only.
 */
static int open_cancelled(void);
static void cw_refused(const char *what);
static void art_find(void) {
    char path[512];
    unsigned count = deque_size(cf.albums);
    art_found_drop();
    art_strike(BUILDING); /* first: the album it strikes takes its marker, and no job */
    if (!(aj.jobs = calloc(count, sizeof(job_t)))) {
        cw_refused("the art look's jobs");
        return;
    }
    aj.albums = cf.albums, aj.gen = library_gen, aj.count = count;
    unsigned char *shared = folders_shared(cf.albums, count);
    for (unsigned i = 0; i < count && !open_cancelled(); ++i) { /* a given-up open's look stops */
        void *r = deque_at(cf.albums, i);
        job_t *j = &aj.jobs[aj.total];
        int state = art_state(r, path, sizeof path);
        aj.bare += state == 2, aj.looked = i + 1;
        if (state == 1 && (j->track = strdup(P(r, REC_PATH)))) {
            j->key = album_key(r);
            j->shared = shared ? shared[i] : 1; /* no memory: no cover.jpg that may be another's */
            ++aj.total;
        }
    }
    free(shared);
}

/* Cancel stops after the current album; finished thumbnails stay, so the next open resumes. */
static void stop(void) {
    worker_stop(cf.thread, &cf.running, &cf.cancel, &cf.timer);
    verify_join();
    for (int i = 0; i < cf.total; ++i) free(cf.jobs[i].track);
    free(cf.jobs);
    cf.jobs = 0;
    cf.total = cf.done = 0;
}

static void fx_close(void);

static void drop(void) {
    stop();
    fx_close();
    if (cf.tracks) deque_destroy(cf.tracks);
    cf.tracks = 0;
}

/* The Year sort's years, 0 undated, album by album as years_of (the library's sorted() list) has
 * them. */
static unsigned short *years;
static void *years_of;
static void drop_albums(void) {
    verify_join(); /* before the albums it reads go */
    art_found_drop();
    if (cf.albums && cf.albums != cf.stock) deque_destroy(cf.albums);
    if (cf.stock) deque_destroy(cf.stock);
    cf.albums = cf.stock = 0;
    free(years), years = 0, years_of = 0; /* they go with their list */
}

/* A stock library query run into the caller's deque, the staging pointer on it so it leaves no
 * trace. No copies: two of a 47,000-song library's rows got demo OOM-killed, with 128 MB and no
 * swap. Stock writes the pointer only at init and reads it afresh at each use; every caller is on
 * the UI thread, on a Cover Flow open's step thread or on Search's load thread, and a call on the
 * UI thread while a step runs gives that open up first (open_yield), and one while Search's load
 * runs waits for it (search_strip.c search_yield), as an overlap could leave the pointer on a deque
 * its owner destroyed. */
static void open_yield(void);
void search_yield(void); /* search_strip.c */
int staged_into(void *into, int (*query)(void *), void *arg) {
    open_yield();
    search_yield();
    void *dir = P(tools_pdeq_directory, 0);
    P(tools_pdeq_directory, 0) = into;
    int n = query(arg);
    P(tools_pdeq_directory, 0) = dir;
    return n;
}

/* Its rows (*count its result) in a deque of their own; shared with navigation.c's queue menu. */
void *staged(int (*query)(void *), void *arg, int *count) {
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    *count = staged_into(out, query, arg);
    return out;
}

/* getAllAlbum, as load_localclass_list 0xf003 runs it, or the album's getMusicByAlbum, as its row
 * opens it. Stock order, including the trailing "Unknown Album" (id -1) row. */
static void *cf_staged(int (*query)(void *), void *arg, int *count);
static int albums(void *album) {
    return album ? getMusicByAlbum(I(album, REC_ID) == -1 ? (const char *)0 : P(album, REC_ALBUM))
                 : getAllAlbum();
}

/* p's folder length; with disc, a CD1, Disc 2 or Disk_03 folder (cd/disc/disk, case aside, an
 * optional space, _ or -, digits) is its parent's, so a multi-disc album is one. */
unsigned album_dir(const char *p, int disc) {
    const char *e = strrchr(p, '/');
    unsigned n = e ? (unsigned)(e - p) : 0, i = n;
    while (disc && i && p[i - 1] >= '0' && p[i - 1] <= '9') --i;
    if (i == n) return n;
    i -= i && (p[i - 1] == ' ' || p[i - 1] == '_' || p[i - 1] == '-');
    if (i >= 3 && p[i - 3] == '/' && !strncasecmp(p + i - 2, "cd", 2)) return i - 3;
    if (i >= 5 && p[i - 5] == '/' && !strncasecmp(p + i - 4, "dis", 3) &&
        ((p[i - 1] | 32) == 'c' || (p[i - 1] | 32) == 'k'))
        return i - 5;
    return n;
}
static int dir_cmp(void *a, void *b, int disc) {
    const char *pa = P(a, REC_PATH) ? P(a, REC_PATH) : "",
               *pb = P(b, REC_PATH) ? P(b, REC_PATH) : "";
    unsigned na = album_dir(pa, disc), nb = album_dir(pb, disc);
    int d = memcmp(pa, pb, na < nb ? na : nb);
    return d ? d : na < nb ? -1 : na != nb;
}
/* Albums apart, as split groups them: the name, then the album artist, else the folder (disc
 * folders as one), ASCII case aside but the folder's, so a compilation stays one album; tagged
 * names first. Shuffle Folders' folder_cmp keeps disc folders apart. Both shared with playback.c.
 */
int folder_cmp(void *a, void *b) { return dir_cmp(a, b, 0); }
int album_cmp(void *a, void *b) {
    const char *x = P(a, REC_ALBUM), *y = P(b, REC_ALBUM);
    int tx = x && *x, d = (y && *y) - tx;
    if (d || (tx && (d = strcasecmp(x, y)))) return d;
    x = P(a, REC_ALBUM_ARTIST), y = P(b, REC_ALBUM_ARTIST);
    int ax = x && *x, ay = y && *y;
    if (tx && (ax || ay)) return ay != ax ? ay - ax : strcasecmp(x, y);
    return dir_cmp(a, b, 1);
}
/* r's album as album_cmp tells it apart, hashed for ratings.c's album ratings: the name and the
 * album artist, else the folder (disc folders as one), ASCII case aside but the folder's; the
 * Unknown card is one album. Never 0. */
static unsigned fnv_lower(unsigned h, const char *s) {
    for (; s && *s; ++s) {
        unsigned char c = (unsigned char)*s;
        c += (unsigned char)((unsigned)(c - 'A') < 26u) * 32;
        h = hash_bytes(h, &c, 1);
    }
    return h * 16777619u;
}
/* The same from the album, album artist and path alone; shared with playlist_art.c. */
unsigned album_ident(const char *x, const char *y, const char *p) {
    unsigned h = fnv_lower(FNV_SEED, x);
    p = p ? p : "";
    h = x && *x && y && *y ? fnv_lower(h, y)
                           : hash_bytes(h, (const unsigned char *)p, album_dir(p, 1)) * 16777619u;
    return h | !h;
}
unsigned album_id(void *r) {
    unsigned unknown = fnv(FNV_SEED, (const unsigned char *)"?");
    if (I(r, REC_ID) == -1) return unknown | !unknown;
    return album_ident(P(r, REC_ALBUM), P(r, REC_ALBUM_ARTIST), P(r, REC_PATH));
}

/* getAllAlbum ends with an "Unknown Album" row (id -1) whenever any album exists, even when every
 * song has an album tag. It goes when the query its card opens, getMusicByAlbum(NULL), finds no
 * song, as navigation.c's ringnav_localclass drops it from the Albums list. */
static void drop_unknown(void) {
    unsigned n = deque_size(cf.stock);
    void *last = n ? deque_at(cf.stock, n - 1) : 0;
    if (!last || I(last, REC_ID) != -1) return;
    int found;
    void *songs = cf_staged(albums, last, &found);
    if (!deque_size(songs)) deque_pop_back(cf.stock);
    deque_destroy(songs);
}

/* Sort, Display settings' Coverflow Sort and the rated albums' card
 * (docs/internals.md#coverflow-sort): Album is the stock order;
 * Artist sorts by artist without a leading article, as every library list does (ringnav_sort_key),
 * then year, then album; Year in Playback settings' Year order (year_sql), undated last; Recently
 * Added by the newest song's time_create, a tie by file time (added_places); Most Played by the
 * album's listens (library_lists.c album_plays); Random by playback.c's rand_below, reshuffled
 * whenever the list is rebuilt. Other ties keep the stock order, and the Unknown row stays last.
 * Ranks come from ALBUM_SQL with another ORDER BY (toolsQueryDbTable without its name sort),
 * matched back to the rows by album_cmp. */
enum { SORT_ALBUM, SORT_ARTIST, SORT_ADDED, SORT_PLAYED, SORT_RANDOM, SORT_RATING, SORT_YEAR };
#define SORT_FILE PEQ_ROOT "/mnt/data/ringnav-coversort"
/* The rated albums' Sort: most hearts first, Album, Artist, Year, then Most Played. */
#define RATED_SORT_FILE PEQ_ROOT "/mnt/data/ringnav-ratedsort"
static const unsigned char rated_sorts[] = { SORT_RATING, SORT_ALBUM, SORT_ARTIST, SORT_YEAR,
                                             SORT_PLAYED };
/* The library's Sort: Year after Artist; saved values are the enum's. */
static const unsigned char all_sorts[] = { SORT_ALBUM, SORT_ARTIST, SORT_YEAR,
                                           SORT_ADDED, SORT_PLAYED, SORT_RANDOM };
#define ALBUM_SQL "select id,album,songer," ALBUM_TRACK ",albumsonger from songtable" ALBUM_GROUP
#define MUSIC_ALBUM_SQL                                                                            \
    "select case when ifnull(album,'')='' then -1 else id end,"                                    \
    "case when ifnull(album,'')='' then 'Unknown Album' else album end,"                           \
    "songer," ALBUM_TRACK ",albumsonger from songtable where " MUSIC_WHERE ALBUM_GROUP
#define SORT_SQL ALBUM_SQL " order by "
extern int ringnav_year_old(void);
/* ALBUM_SQL with each album's year after (split_row's REC_YEAR), by year, undated last. */
#define YEAR_SQL                                                                                   \
    "select id,album,songer," ALBUM_TRACK ",albumsonger," ALBUM_YEAR " from songtable" ALBUM_GROUP \
    " order by " ALBUM_YEAR "=0," ALBUM_YEAR
/* Albums by year, Playback settings' Year order (navigation.c): newest or oldest first, undated
 * last. */
static const char *year_sql(void) {
    return ringnav_year_old() ? YEAR_SQL ",album COLLATE NOCASE"
                              : YEAR_SQL " desc,album COLLATE NOCASE";
}
static const char *const sort_names[] = {
    "Sort: Album",  "Sort: Artist", "Sort: Recently Added", "Sort: Most Played", "Sort: Random",
    "Sort: Rating", "Sort: Year"
};
/* The order step after cf.sort (dir 1), or cf.sort itself (0), in the page's own orders; an
 * order not among them is the first (Album, or the rated albums' Rating). */
static int sort_next(int dir) {
    const unsigned char *o = cf.scoped ? rated_sorts : all_sorts;
    unsigned n = cf.scoped ? sizeof rated_sorts : sizeof all_sorts, i = 0;
    while (i < n && o[i] != cf.sort) ++i;
    return i == n ? o[0] : o[(i + (unsigned)dir) % n];
}
extern void album_plays(int (*album_of)(void *), unsigned *sum);
extern void ringnav_sort_key(char *s);
#define ARTIST_KEY 96

/* Unicode 16.0.0 number, punctuation and separator ranges, as 16 bits: those of the BMP, then
 * those of plane 1 less 0x10000 (none is above it). */
static const unsigned short initial_misc[][2] = {
    { 0xa0, 0xa1 }, { 0xa7, 0xa7 }, { 0xab, 0xab }, { 0xb2, 0xb3 },
    { 0xb6, 0xb7 }, { 0xb9, 0xb9 }, { 0xbb, 0xbf }, { 0x37e, 0x37e },
    { 0x387, 0x387 }, { 0x55a, 0x55f }, { 0x589, 0x58a }, { 0x5be, 0x5be },
    { 0x5c0, 0x5c0 }, { 0x5c3, 0x5c3 }, { 0x5c6, 0x5c6 }, { 0x5f3, 0x5f4 },
    { 0x609, 0x60a }, { 0x60c, 0x60d }, { 0x61b, 0x61b }, { 0x61d, 0x61f },
    { 0x660, 0x66d }, { 0x6d4, 0x6d4 }, { 0x6f0, 0x6f9 }, { 0x700, 0x70d },
    { 0x7c0, 0x7c9 }, { 0x7f7, 0x7f9 }, { 0x830, 0x83e }, { 0x85e, 0x85e },
    { 0x964, 0x970 }, { 0x9e6, 0x9ef }, { 0x9f4, 0x9f9 }, { 0x9fd, 0x9fd },
    { 0xa66, 0xa6f }, { 0xa76, 0xa76 }, { 0xae6, 0xaf0 }, { 0xb66, 0xb6f },
    { 0xb72, 0xb77 }, { 0xbe6, 0xbf2 }, { 0xc66, 0xc6f }, { 0xc77, 0xc7e },
    { 0xc84, 0xc84 }, { 0xce6, 0xcef }, { 0xd58, 0xd5e }, { 0xd66, 0xd78 },
    { 0xde6, 0xdef }, { 0xdf4, 0xdf4 }, { 0xe4f, 0xe5b }, { 0xed0, 0xed9 },
    { 0xf04, 0xf12 }, { 0xf14, 0xf14 }, { 0xf20, 0xf33 }, { 0xf3a, 0xf3d },
    { 0xf85, 0xf85 }, { 0xfd0, 0xfd4 }, { 0xfd9, 0xfda }, { 0x1040, 0x104f },
    { 0x1090, 0x1099 }, { 0x10fb, 0x10fb }, { 0x1360, 0x137c }, { 0x1400, 0x1400 },
    { 0x166e, 0x166e }, { 0x1680, 0x1680 }, { 0x169b, 0x169c }, { 0x16eb, 0x16f0 },
    { 0x1735, 0x1736 }, { 0x17d4, 0x17d6 }, { 0x17d8, 0x17da }, { 0x17e0, 0x17e9 },
    { 0x17f0, 0x17f9 }, { 0x1800, 0x180a }, { 0x1810, 0x1819 }, { 0x1944, 0x194f },
    { 0x19d0, 0x19da }, { 0x1a1e, 0x1a1f }, { 0x1a80, 0x1a89 }, { 0x1a90, 0x1a99 },
    { 0x1aa0, 0x1aa6 }, { 0x1aa8, 0x1aad }, { 0x1b4e, 0x1b60 }, { 0x1b7d, 0x1b7f },
    { 0x1bb0, 0x1bb9 }, { 0x1bfc, 0x1bff }, { 0x1c3b, 0x1c49 }, { 0x1c50, 0x1c59 },
    { 0x1c7e, 0x1c7f }, { 0x1cc0, 0x1cc7 }, { 0x1cd3, 0x1cd3 }, { 0x2000, 0x200a },
    { 0x2010, 0x2029 }, { 0x202f, 0x2043 }, { 0x2045, 0x2051 }, { 0x2053, 0x205f },
    { 0x2070, 0x2070 }, { 0x2074, 0x2079 }, { 0x207d, 0x207e }, { 0x2080, 0x2089 },
    { 0x208d, 0x208e }, { 0x2150, 0x2182 }, { 0x2185, 0x2189 }, { 0x2308, 0x230b },
    { 0x2329, 0x232a }, { 0x2460, 0x249b }, { 0x24ea, 0x24ff }, { 0x2768, 0x2793 },
    { 0x27c5, 0x27c6 }, { 0x27e6, 0x27ef }, { 0x2983, 0x2998 }, { 0x29d8, 0x29db },
    { 0x29fc, 0x29fd }, { 0x2cf9, 0x2cff }, { 0x2d70, 0x2d70 }, { 0x2e00, 0x2e2e },
    { 0x2e30, 0x2e4f }, { 0x2e52, 0x2e5d }, { 0x3000, 0x3003 }, { 0x3007, 0x3011 },
    { 0x3014, 0x301f }, { 0x3021, 0x3029 }, { 0x3030, 0x3030 }, { 0x3038, 0x303a },
    { 0x303d, 0x303d }, { 0x30a0, 0x30a0 }, { 0x30fb, 0x30fb }, { 0x3192, 0x3195 },
    { 0x3220, 0x3229 }, { 0x3248, 0x324f }, { 0x3251, 0x325f }, { 0x3280, 0x3289 },
    { 0x32b1, 0x32bf }, { 0xa4fe, 0xa4ff }, { 0xa60d, 0xa60f }, { 0xa620, 0xa629 },
    { 0xa673, 0xa673 }, { 0xa67e, 0xa67e }, { 0xa6e6, 0xa6ef }, { 0xa6f2, 0xa6f7 },
    { 0xa830, 0xa835 }, { 0xa874, 0xa877 }, { 0xa8ce, 0xa8d9 }, { 0xa8f8, 0xa8fa },
    { 0xa8fc, 0xa8fc }, { 0xa900, 0xa909 }, { 0xa92e, 0xa92f }, { 0xa95f, 0xa95f },
    { 0xa9c1, 0xa9cd }, { 0xa9d0, 0xa9d9 }, { 0xa9de, 0xa9df }, { 0xa9f0, 0xa9f9 },
    { 0xaa50, 0xaa59 }, { 0xaa5c, 0xaa5f }, { 0xaade, 0xaadf }, { 0xaaf0, 0xaaf1 },
    { 0xabeb, 0xabeb }, { 0xabf0, 0xabf9 }, { 0xfd3e, 0xfd3f }, { 0xfe10, 0xfe19 },
    { 0xfe30, 0xfe52 }, { 0xfe54, 0xfe61 }, { 0xfe63, 0xfe63 }, { 0xfe68, 0xfe68 },
    { 0xfe6a, 0xfe6b }, { 0xff01, 0xff03 }, { 0xff05, 0xff0a }, { 0xff0c, 0xff1b },
    { 0xff1f, 0xff20 }, { 0xff3b, 0xff3d }, { 0xff3f, 0xff3f }, { 0xff5b, 0xff5b },
    { 0xff5d, 0xff5d }, { 0xff5f, 0xff65 },
};
static const unsigned short initial_misc1[][2] = {
    { 0x100, 0x102 }, { 0x107, 0x133 }, { 0x140, 0x178 }, { 0x18a, 0x18b },
    { 0x2e1, 0x2fb }, { 0x320, 0x323 }, { 0x341, 0x341 }, { 0x34a, 0x34a },
    { 0x39f, 0x39f }, { 0x3d0, 0x3d5 }, { 0x4a0, 0x4a9 }, { 0x56f, 0x56f },
    { 0x857, 0x85f }, { 0x879, 0x87f }, { 0x8a7, 0x8af }, { 0x8fb, 0x8ff },
    { 0x916, 0x91b }, { 0x91f, 0x91f }, { 0x93f, 0x93f }, { 0x9bc, 0x9bd },
    { 0x9c0, 0x9cf }, { 0x9d2, 0x9ff }, { 0xa40, 0xa48 }, { 0xa50, 0xa58 },
    { 0xa7d, 0xa7f }, { 0xa9d, 0xa9f }, { 0xaeb, 0xaf6 }, { 0xb39, 0xb3f },
    { 0xb58, 0xb5f }, { 0xb78, 0xb7f }, { 0xb99, 0xb9c }, { 0xba9, 0xbaf },
    { 0xcfa, 0xcff }, { 0xd30, 0xd39 }, { 0xd40, 0xd49 }, { 0xd6e, 0xd6e },
    { 0xe60, 0xe7e }, { 0xead, 0xead }, { 0xf1d, 0xf26 }, { 0xf51, 0xf59 },
    { 0xf86, 0xf89 }, { 0xfc5, 0xfcb }, { 0x1047, 0x104d }, { 0x1052, 0x106f },
    { 0x10bb, 0x10bc }, { 0x10be, 0x10c1 }, { 0x10f0, 0x10f9 }, { 0x1136, 0x1143 },
    { 0x1174, 0x1175 }, { 0x11c5, 0x11c8 }, { 0x11cd, 0x11cd }, { 0x11d0, 0x11d9 },
    { 0x11db, 0x11db }, { 0x11dd, 0x11df }, { 0x11e1, 0x11f4 }, { 0x1238, 0x123d },
    { 0x12a9, 0x12a9 }, { 0x12f0, 0x12f9 }, { 0x13d4, 0x13d5 }, { 0x13d7, 0x13d8 },
    { 0x144b, 0x145b }, { 0x145d, 0x145d }, { 0x14c6, 0x14c6 }, { 0x14d0, 0x14d9 },
    { 0x15c1, 0x15d7 }, { 0x1641, 0x1643 }, { 0x1650, 0x1659 }, { 0x1660, 0x166c },
    { 0x16b9, 0x16b9 }, { 0x16c0, 0x16c9 }, { 0x16d0, 0x16e3 }, { 0x1730, 0x173e },
    { 0x183b, 0x183b }, { 0x18e0, 0x18f2 }, { 0x1944, 0x1946 }, { 0x1950, 0x1959 },
    { 0x19e2, 0x19e2 }, { 0x1a3f, 0x1a46 }, { 0x1a9a, 0x1a9c }, { 0x1a9e, 0x1aa2 },
    { 0x1b00, 0x1b09 }, { 0x1be1, 0x1be1 }, { 0x1bf0, 0x1bf9 }, { 0x1c41, 0x1c45 },
    { 0x1c50, 0x1c6c }, { 0x1c70, 0x1c71 }, { 0x1d50, 0x1d59 }, { 0x1da0, 0x1da9 },
    { 0x1ef7, 0x1ef8 }, { 0x1f43, 0x1f59 }, { 0x1fc0, 0x1fd4 }, { 0x1fff, 0x1fff },
    { 0x2400, 0x246e }, { 0x2470, 0x2474 }, { 0x2ff1, 0x2ff2 }, { 0x6130, 0x6139 },
    { 0x6a60, 0x6a69 }, { 0x6a6e, 0x6a6f }, { 0x6ac0, 0x6ac9 }, { 0x6af5, 0x6af5 },
    { 0x6b37, 0x6b3b }, { 0x6b44, 0x6b44 }, { 0x6b50, 0x6b59 }, { 0x6b5b, 0x6b61 },
    { 0x6d6d, 0x6d79 }, { 0x6e80, 0x6e9a }, { 0x6fe2, 0x6fe2 }, { 0xbc9f, 0xbc9f },
    { 0xccf0, 0xccf9 }, { 0xd2c0, 0xd2d3 }, { 0xd2e0, 0xd2f3 }, { 0xd360, 0xd378 },
    { 0xd7ce, 0xd7ff }, { 0xda87, 0xda8b }, { 0xe140, 0xe149 }, { 0xe2f0, 0xe2f9 },
    { 0xe4f0, 0xe4f9 }, { 0xe5f1, 0xe5fa }, { 0xe5ff, 0xe5ff }, { 0xe8c7, 0xe8cf },
    { 0xe950, 0xe959 }, { 0xe95e, 0xe95f }, { 0xec71, 0xecab }, { 0xecad, 0xecaf },
    { 0xecb1, 0xecb4 }, { 0xed01, 0xed2d }, { 0xed2f, 0xed3d }, { 0xf100, 0xf10c },
    { 0xfbf0, 0xfbf9 },
};

/* Initial of the active alphabetic key; UTF-8 is decoded without losing non-ASCII names. */
static unsigned initial(int i) {
    void *r = deque_at(cf.albums, (unsigned)i);
    if (I(r, REC_ID) == -1) return '#';
    const char *name = cf.sort == SORT_ARTIST ? cover_artist(r) : P(r, REC_ALBUM);
    char key[ARTIST_KEY];
    snprintf(key, sizeof key, "%s", name ? name : "");
    ringnav_sort_key(key);
    const unsigned char *p = (const unsigned char *)key;
    unsigned c = *p++;
    if (c >= 0xc2 && c <= 0xf4) {
        unsigned n = c < 0xe0 ? 1 : c < 0xf0 ? 2 : 3;
        c &= (1u << (6 - n)) - 1;
        for (unsigned j = 0; j < n; ++j) {
            if ((*p & 0xc0) != 0x80) return '#';
            c = (c << 6) | (*p++ & 63);
        }
    }
    if (c >= 'a' && c <= 'z') c -= 32;
    if (c < 128) return c >= 'A' && c <= 'Z' ? c : '#';
    const unsigned short(*t)[2] = c < 0x10000 ? initial_misc : initial_misc1;
    unsigned n = c < 0x10000 ? sizeof initial_misc / sizeof *initial_misc
                             : c < 0x20000 ? sizeof initial_misc1 / sizeof *initial_misc1 : 0;
    for (unsigned i = 0, low = c & 0xffff; i < n; ++i)
        if (low >= t[i][0] && low <= t[i][1]) return '#';
    return c;
}

/* Album i's group: its initial, or in Year its year, CF_YEAR alone when undated. */
static unsigned group(int i) {
    if (cf.sort != SORT_YEAR) return initial(i);
    return CF_YEAR | (years && cf.albums == years_of ? years[i] : 0u);
}

/* Letters mean something on w: the whole library's covers, sorted by album or artist; years, by
 * year, with Fast Navigation on. */
int lettered(void *w) {
    return w && w == cf.slide && cf.screen == COVERS &&
           (cf.sort <= SORT_ARTIST || (cf.sort == SORT_YEAR && ringnav_big_library())) &&
           !cf.scoped;
}

/* Utility cards and wrap edges remain individual stops. Reverse lands at a group's start. */
int coverflow_jump(void *w, int from, int dir, unsigned *letter) {
    int n = cf.albums ? (int)deque_size(cf.albums) : 0;
    *letter = 0;
    if (!lettered(w) || from < 0 || from >= n) return 1;
    unsigned c = group(from);
    int to = from + dir;
    if (dir > 0) {
        while (to < n && group(to) == c) ++to;
        if (to < n) *letter = group(to);
    } else {
        while (to >= 0 && group(to) == c) --to;
        if (to < 0) return from + 1; /* the last album, across the wrap */
        c = group(to);
        while (to > 0 && group(to - 1) == c) --to;
        *letter = c;
    }
    return dir > 0 ? to - from : from - to;
}

/* The first album of album i's letter, or the last letter's from past them, the letter in *letter:
 * where the thumb's letters land round the seam, the first letter's after the last and the last's
 * before the first. */
int coverflow_letter_first(int i, unsigned *letter) {
    int n = cf.albums ? (int)deque_size(cf.albums) : 0;
    *letter = 0;
    if (n < 1) return i;
    i = i < n ? i : n - 1;
    unsigned c = *letter = group(i);
    while (i > 0 && group(i - 1) == c) --i;
    return i;
}

/* The first album of the letter after album from's (dir 1) or before it (-1), of n, round the seam
 * as the albums wrap; its letter in *letter. */
int coverflow_letter_next(void *w, int from, int dir, int n, unsigned *letter) {
    int to = from + dir * coverflow_jump(w, from, dir, letter);
    return *letter ? to : coverflow_letter_first(dir > 0 ? 0 : n - 1, letter);
}

typedef struct {
    void *r;
    const char *artist; /* Artist: the artist's sort key, without its article */
    unsigned rank;
    int idx;
    unsigned short year; /* Year: the query's, 0 undated or unranked */
} order_t;

static const char *album_name(void *r) {
    const char *s = P(r, REC_ALBUM);
    return s ? s : "";
}
/* Rows sorted to look up by binary search, ties in their list's order. */
typedef struct {
    void *a;
    unsigned j;
} row_t;
static int name_cmp(void *a, void *b) { return strcasecmp(album_name(a), album_name(b)); }
static int tie(int d, const row_t *p, const row_t *q) {
    return d ? d : (p->j > q->j) - (p->j < q->j);
}
static int by_name(const void *x, const void *y) {
    const row_t *p = x, *q = y;
    return tie(name_cmp(p->a, q->a), p, q);
}
static int by_album(const void *x, const void *y) {
    const row_t *p = x, *q = y;
    return tie(album_cmp(p->a, q->a), p, q);
}
/* The first of v's n rows not before r by cmp. */
static unsigned lower(row_t *v, unsigned n, void *r, int (*cmp)(void *, void *)) {
    unsigned lo = 0;
    while (n) {
        unsigned h = n / 2;
        if (cmp(v[lo + h].a, r) < 0)
            lo += h + 1, n -= h + 1;
        else
            n = h;
    }
    return lo;
}
/* list's rows sorted by by, *m of them, Unknown (REC_ID -1) only if all; 0 out of memory. */
static row_t *album_rows(void *list, unsigned *m, int (*by)(const void *, const void *), int all) {
    row_t *v = calloc(deque_size(list) + 1, sizeof *v);
    *m = 0;
    for (unsigned i = 0; v && i < deque_size(list); ++i)
        if (all || I(deque_at(list, i), REC_ID) != -1) v[*m].a = deque_at(list, i), v[(*m)++].j = i;
    if (v) qsort(v, *m, sizeof *v, by);
    return v;
}
/* The list row of r's album among album_rows' v, or -1: the Unknown row never matches. */
static int album_in(row_t *v, unsigned m, void *r) {
    unsigned k = lower(v, m, r, album_cmp);
    return k < m && !album_cmp(v[k].a, r) ? (int)v[k].j : -1;
}
/* cf.stock's album_rows while sorted() ranks it. */
static row_t *stock_rows;
static unsigned stock_n;
static int album_index(void *r) { return album_in(stock_rows, stock_n, r); }
/* album_row on the first four columns, then the fifth, the album artist, on the row it added, and
 * YEAR_SQL's sixth, the year; the stSongInfo copy (0x5c65ac) duplicates +0x24 as it does the other
 * strings. */
static int split_row(void *a, int n, char **v, char **c) {
    void *dir = P(tools_pdeq_directory, 0);
    unsigned k = deque_size(dir);
    album_row(a, 4, v, c);
    if (deque_size(dir) <= k) return 0;
    if (v[4]) P(deque_at(dir, k), REC_ALBUM_ARTIST) = strdup(v[4]);
    if (n > 5) I(deque_at(dir, k), REC_YEAR) = v[5] ? atoi(v[5]) : 0;
    return 0;
}
static int rank_query(void *sql) {
    return toolsQueryDbTable("/mnt/data/database.db", sql, split_row, 0);
}
/* staged, reading a signed-in server's songs too (staged_music sets library_music, so
 * navigation.c library_query reads songtable's union with servertable, which catalogue.c
 * catalogue_rows supplies): Cover Flow's albums, their ranks and the rated albums, as the Library's
 * Albums list reads them. */
extern void *staged_music(int (*query)(void *), void *arg, int *count);
static void *cf_staged(int (*query)(void *), void *arg, int *count) {
    return catalogue_has() ? staged_music(query, arg, count) : staged(query, arg, count);
}

/* list's albums with music, in a new deque (list goes), using a music track for their art even when
 * tags are shared with an audiobook. The card's files stay untouched. Out of memory,
 * audiobooks stay. */
static void *music_of(void *list, void *rows);
static void *music_albums(void *list) {
    int n;
    return music_of(list, cf_staged(rank_query, MUSIC_ALBUM_SQL, &n));
}
/* music_albums with MUSIC_ALBUM_SQL's rows, which go too. */
static void *music_of(void *list, void *rows) {
    void *out;
    unsigned m;
    row_t *v = album_rows(rows, &m, by_album, 0);
    if (!v) return deque_destroy(rows), list;
    out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned i = 0; i < deque_size(list); ++i) {
        void *r = deque_at(list, i);
        int k = I(r, REC_ID) == -1 ? -1 : album_in(v, m, r);
        if (k >= 0)
            _deque_push_back(out, deque_at(rows, (unsigned)k));
        else if (I(r, REC_ID) == -1 && m < deque_size(rows))
            _deque_push_back(out, r);
    }
    free(v);
    deque_destroy(rows);
    deque_destroy(list);
    return out;
}

/* getAllAlbum groups by name alone, as stock's Albums list does: each of its rows becomes that
 * name's ALBUM_SQL rows, one per album_cmp album (the SQL keeps disc folders apart), in its place;
 * a row the query missed stays. Out of memory, the rows stay unsplit. ponytail: a scan of the
 * name's albums so far per row, its albums squared; fine unless one name has thousands. */
static void *split_by(void *stock, void *rows);
static void *split(void *stock) {
    int n;
    void *rows = cf_staged(rank_query, ALBUM_SQL, &n), *out = split_by(stock, rows);
    deque_destroy(rows);
    return out;
}
/* split with the grouping's rows, which stay the caller's. Their Unknown rows (MUSIC_ALBUM_SQL's,
 * REC_ID -1) are no name's: stock's Unknown row stays as it is. */
static void *split_by(void *stock, void *rows) {
    void *out;
    unsigned m;
    row_t *v = album_rows(rows, &m, by_name, 0);
    if (!v) return stock;
    out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned i = 0; i < deque_size(stock); ++i) {
        void *r = deque_at(stock, i);
        unsigned from = deque_size(out), k;
        for (unsigned j = I(r, REC_ID) == -1 ? m : lower(v, m, r, name_cmp);
             j < m && !name_cmp(v[j].a, r); ++j) {
            void *a = v[j].a;
            for (k = from; k < deque_size(out) && album_cmp(deque_at(out, k), a); ++k) {}
            if (k == deque_size(out)) _deque_push_back(out, a);
        }
        if (from == deque_size(out)) _deque_push_back(out, r);
    }
    free(v);
    deque_destroy(stock);
    return out;
}
/* navigation.c's Albums list (load_localclass_list 0xf003), split in place as Coverflow's: its
 * size. */
int coverflow_split(void *list) {
    void *copy = _create_deque("stSongInfo"), *out;
    deque_init_copy(copy, list);
    out = split(copy);
    deque_clear(list);
    deque_assign(list, out);
    deque_destroy(out);
    return (int)deque_size(list);
}
/* list down to r's album (album_cmp), in order and in place, unless none is; then at's index. An
 * untagged r keeps every untagged song the card lists (spoken and scoped-out ones left out, as
 * MUSIC_WHERE, which stock's getMusicByAlbum(NULL) skips at resume), and r: the Unknown Album card
 * is one album, whatever its folders. navigation.c's album pages and playback.c's stock resume. */
/* ponytail: keeps the list's (stock's) order, not the card's album_before order; sort here if
 * Next/Previous after a restart shows it. */
int album_only(void *list, void *r, int at) {
    void *out = _create_deque("stSongInfo");
    const char *name = P(r, REC_ALBUM);
    int k = at, unknown = !name || !*name;
    deque_init(out);
    for (unsigned i = 0; i < deque_size(list); ++i) {
        void *x = deque_at(list, i);
        const char *a = P(x, REC_ALBUM), *path = P(x, REC_PATH);
        if (unknown ? x == r || ((!a || !*a) && !spoken(path) && !library_scope_hidden(path))
                    : !album_cmp(x, r)) {
            if ((int)i == at) k = (int)deque_size(out);
            _deque_push_back(out, x);
        }
    }
    if (deque_size(out)) deque_clear(list), deque_assign(list, out);
    deque_destroy(out);
    return k;
}
/* Recently Added: ALBUM_SQL with each album's newest time_create after (split_row's REC_YEAR), by
 * it, newest first. */
#define ADDED_SQL                                                                                  \
    "select id,album,songer," ALBUM_TRACK                                                          \
    ",albumsonger,max(time_create) from songtable" ALBUM_GROUP " order by max(time_create) desc"
typedef struct {
    unsigned j;
    int mtime;
    const char *path;
} tie_t;
static int by_newer(const void *a, const void *b) {
    const tie_t *x = a, *y = b;
    if (x->mtime != y->mtime) return x->mtime > y->mtime ? -1 : 1;
    int d = strcmp(x->path, y->path);
    return d ? d : x->j < y->j ? -1 : 1;
}
/* ADDED_SQL's rows' places: each row's own, but albums whose newest song has one time_create
 * (files copied in one go share a second, and a card copied whole can date every album alike) go
 * newest file first, by the modification time each album's art track (ALBUM_TRACK) keeps from
 * before the copy, then by that track's path. SQLite hands a tie in the grouping's order, the
 * Album order, so Recently Added looked as if it had not applied. 0 out of memory: the rows' order. ponytail: the art track stands
 * for its album, one stat an album in a tie; stat every track if albums tagged over time show up
 * out of order. */
static unsigned *added_places(void *rows) {
    unsigned n = deque_size(rows), *place = calloc(n + 1, sizeof *place);
    tie_t *t = calloc(n + 1, sizeof *t);
    for (unsigned j = 0; place && t && j < n;) {
        unsigned k = j;
        int when = I(deque_at(rows, j), REC_YEAR);
        while (k < n && I(deque_at(rows, k), REC_YEAR) == when) {
            const char *path = P(deque_at(rows, k), REC_PATH);
            t[k - j] = (tie_t){ k, 0, path ? path : "" };
            place[k] = k, ++k;
        }
        for (unsigned m = 0; k - j > 1 && m < k - j; ++m) {
            unsigned st[36]; /* o32 stat: st_mtime word 16 */
            if (*t[m].path && !__xstat(3, t[m].path, st)) t[m].mtime = (int)st[16];
        }
        if (k - j > 1) {
            qsort(t, k - j, sizeof *t, by_newer);
            for (unsigned m = 0; m < k - j; ++m) place[t[m].j] = j + m;
        }
        j = k;
    }
    if (!t) free(place), place = 0;
    free(t);
    return place;
}
/* v's ranks, the places of sql's rows: ADDED_SQL's with its ties ordered (added_places). */
static void rank_by(order_t *v, const char *sql, int added) {
    int n;
    void *rows = cf_staged(rank_query, (void *)sql, &n);
    unsigned *place = added ? added_places(rows) : 0;
    for (unsigned j = 0; j < deque_size(rows); ++j) {
        int i = album_index(deque_at(rows, j));
        int y = I(deque_at(rows, j), REC_YEAR);
        if (i >= 0 && (v[i].rank == ~0u || (place && place[j] < v[i].rank)))
            /* a year past 1-9999 is no year: undated */
            v[i].rank = place ? place[j] : j,
            v[i].year = y > 0 && y < 10000 ? (unsigned short)y : 0;
    }
    free(place);
    deque_destroy(rows);
}
static int by_rank(const void *a, const void *b) {
    const order_t *x = a, *y = b;
    int unknown = (I(x->r, REC_ID) == -1) - (I(y->r, REC_ID) == -1); /* the Unknown card last */
    if (unknown) return unknown;
    return x->rank != y->rank ? (x->rank < y->rank ? -1 : 1) : x->idx - y->idx;
}
static int by_artist(const void *a, const void *b) {
    const order_t *x = a, *y = b;
    int d = (I(x->r, REC_ID) == -1) - (I(y->r, REC_ID) == -1);
    if (!d) d = strcasecmp(x->artist, y->artist);
    return d ? d : by_rank(a, b);
}

/* cf.stock in the Sort's order: cf.stock itself for Album. Out of memory, the Sort falls back to
 * Album too, so its card says what is shown. */
static void *sorted(void) {
    unsigned n = deque_size(cf.stock);
    int artist = cf.sort == SORT_ARTIST, played = cf.sort == SORT_PLAYED;
    order_t *v = cf.sort == SORT_ALBUM ? 0 : calloc(n + 1, sizeof *v);
    if (!cf.scoped) /* the rated page's sort is its own */
        cf.year_old = ringnav_year_old(), cf.artists = I(artist_type, 0);
    unsigned *sum = v && played ? calloc(n + 1, sizeof *sum) : 0;
    char *keys = v && artist ? calloc(n + 1, ARTIST_KEY) : 0;
    int ranked = cf.sort != SORT_RATING && cf.sort != SORT_RANDOM; /* uses album_index */
    stock_rows = v && ranked ? album_rows(cf.stock, &stock_n, by_album, 0) : 0;
    if (!v || (ranked && !stock_rows) || (played && !sum) || (artist && !keys)) {
        free(v), free(sum), free(keys), free(stock_rows), stock_rows = 0;
        cf.sort = SORT_ALBUM;
        return cf.stock;
    }
    for (unsigned i = 0; i < n; ++i) {
        void *r = v[i].r = deque_at(cf.stock, i);
        v[i].rank = ~0u, v[i].idx = (int)i;
        if (keys) {
            const char *a = cover_artist(r);
            snprintf(keys + i * ARTIST_KEY, ARTIST_KEY, "%s", a ? a : "");
            ringnav_sort_key(keys + i * ARTIST_KEY);
            v[i].artist = keys + i * ARTIST_KEY;
        }
    }
    if (cf.sort == SORT_RATING)
        for (unsigned i = 0; i < n; ++i) v[i].rank = 5u - (unsigned)cf.hearts(v[i].r);
    else if (sum) {
        album_plays(album_index, sum);
        for (unsigned i = 0; i < n; ++i)
            if (sum[i]) v[i].rank = ~sum[i]; /* most first; never played after, in stock order */
    } else if (cf.sort == SORT_RANDOM)
        for (unsigned i = 0; i < n; ++i) v[i].rank = rand_below(~0u);
    else
        rank_by(v, cf.sort == SORT_ADDED ? ADDED_SQL : year_sql(), cf.sort == SORT_ADDED);
    qsort(v, n, sizeof *v, artist ? by_artist : by_rank);
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned i = 0; i < n; ++i) _deque_push_back(out, v[i].r);
    if (!cf.scoped && cf.sort == SORT_YEAR) {
        free(years);
        years_of = out;
        years = calloc(n + 1, sizeof *years); /* out of memory, all undated: one group */
        for (unsigned i = 0; years && i < n; ++i) years[i] = v[i].year;
    }
    free(v), free(sum), free(keys), free(stock_rows), stock_rows = 0;
    return out;
}
extern int ringnav_artist_az(void);
/* albums (split's rows, or songs standing for their albums) in place in sql's order (an
 * ALBUM_SQL), each with its year in REC_YEAR when sql is a YEAR_SQL (years): the query's order,
 * then the albums it missed, then the Unknown album. Out of memory they keep their order. */
static void album_order(void *albums, const char *sql, int years) {
    unsigned n = deque_size(albums), m;
    char *taken = calloc(n + 1, 1);
    row_t *v = taken ? album_rows(albums, &m, by_album, 0) : 0;
    int found;
    if (!v) {
        free(taken);
        return;
    }
    void *rows = cf_staged(rank_query, (void *)sql, &found), *out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned j = 0; j < deque_size(rows); ++j) {
        int i = album_in(v, m, deque_at(rows, j));
        if (i < 0 || taken[i]) continue;
        taken[i] = 1;
        if (years) I(deque_at(albums, (unsigned)i), REC_YEAR) = I(deque_at(rows, j), REC_YEAR);
        _deque_push_back(out, deque_at(albums, (unsigned)i));
    }
    deque_destroy(rows);
    for (int unknown = 0; unknown < 2; ++unknown)
        for (unsigned i = 0; i < n; ++i)
            if (!taken[i] && (I(deque_at(albums, i), REC_ID) == -1) == unknown) {
                if (years) I(deque_at(albums, i), REC_YEAR) = 0; /* undated */
                _deque_push_back(out, deque_at(albums, i));
            }
    free(taken), free(v);
    deque_clear(albums);
    deque_assign(albums, out);
    deque_destroy(out);
}
/* navigation.c's artist pages: by year, as year_sql, or by name (Playback's Artist album order
 * A-Z). */
void coverflow_by_year(void *albums) {
    album_order(albums, ringnav_artist_az() ? SORT_SQL "album COLLATE NOCASE" : year_sql(), 0);
}
/* library_artist.c's Albums: newest first, undated last, each with its year (REC_YEAR). */
void coverflow_newest(void *albums) {
    album_order(albums, YEAR_SQL " desc,album COLLATE NOCASE", 1);
}

/* w's text isn't s. AWTK keeps a widget's text as UTF-32, which widget_get_prop_str(w, "text")
 * never returns, so a compare through it always differs and every set repaints. Shared (peq.h). */
int text_differs(void *w, const char *s) {
    char cur[512] = ""; /* stock writes nothing when it doesn't fit */
    return widget_get_text_utf8(w, cur, sizeof cur) || strcmp(cur, s);
}

/* A label for the pages' text. Shared (peq.h). */
void *text(void *parent, int x, int y, int w, int h) {
    void *label = hscroll_label_create(parent, x, y, w, h);
    widget_use_style(label, "s_scrlabel_white20c");
    set_hscroll_label_attribute(label);
    widget_set_prop_int(label, "loop", 1);
    return label;
}

/* A black page named name, with its destroy (given the page) and Return handlers; 0 when none.
 * Full-bleed, the whole screen as every page's window (docs/oled-design.md#full-bleed): its
 * content rests from bar_foot(page), under the status row. Shared (peq.h). */
void *page_open(const char *name, int (*closed)(void *, void *), int (*keyup)(void *, void *)) {
    void *page = window_create(0, 0, 0, 0, 0);
    if (!page) return 0;
    widget_set_name(page, name);
    widget_set_prop_int(page, "style:normal:bg_color", (int)0xff000000u);
    widget_on(page, EVT_DESTROY, closed, page);
    widget_on(page, EVT_KEY_UP, keyup, 0);
    motion_open(page, name);
    return page;
}

#define TITLE_ON_SHOW 0 /* page_title's n when the caller writes the title again */

/* A page's title row, the theme's height. 0: the bar holds the caption, and this hidden label is
 * what it reads, unless the page also needs it as a first line, a row high: with no rows (n 0),
 * when it's all the page says, or when it doesn't fit the bar's box (bar_title_fits; a status
 * row's holds any, ending a long one in an ellipsis, so the rows keep their place under it).
 * Shared (peq.h). */
void *page_title(void *body, const char *caption, int n) {
    const theme_t *T = theme();
    int x = T->page_x, w = 375 - 2 * x, top = bar_foot(body);
    void *title = text(body, x, top, w, T->page_title_h);
    widget_set_text_utf8(title, caption);
    if (T->page_title_h) return title;
    widget_set_name(title, PAGE_TITLE);
    if (n && bar_title_fits(title)) {
        widget_set_visible(title, 0, 0);
    } else {
        widget_use_style(title, "s_scrlabel_white20l");
        widget_move_resize(title, x, top, w, T->page_row);
        theme_font(title, ROLE_TITLE, FONT_ROW);
    }
    return title;
}

/* A list view in parent from y, w wide and at most h high, for n item_h rows; its scroll view.
 * Shared with feedback.c's empty_state. */
void *rows_view(void *parent, int y, int w, int h, int n, int item_h) {
    int rows = n * item_h < h ? n * item_h : h;
    void *lv = list_view_create(parent, 0, y, w, rows);
    widget_set_prop_int(lv, "item_height", item_h);
    /* The theme's default list_view is a light card; stock pages paint theirs black inline. */
    widget_set_prop_int(lv, "style:normal:bg_color", (int)0xff000000u);
    widget_set_prop_int(lv, "style:normal:border_color", 0);
    void *view = scroll_view_create(lv, 0, 0, w, rows);
    widget_set_prop_int(view, "yslidable", 1);
    widget_set_prop_int(view, "xslidable", 0);
    widget_set_prop_int(view, "virtual_h", n * item_h);
    return view;
}

/* The peq_ui.c page: a title bar (*title, unless 0: its caller writes it again, so it stays on
 * show) over n item_h rows (page_h) in body, down to the page's bottom (the last row's selection
 * meets the screen's edge) or shrunk so the list's white background never shows below a short list;
 * its rows past the list end fade (navigation.c list_fade). Shared (peq.h). */
void *page_list(void *page, void *body, void **title, const char *caption, int n, int item_h) {
    int h = page_room(body);
    item_h = page_h(item_h);
    widget_destroy_children(body);
    widget_set_visible(body, 1, 0);
    void *t = page_title(body, caption, title ? TITLE_ON_SHOW : n);
    if (title) *title = t;
    void *view = rows_view(body, I(t, W_Y) + I(t, W_H), 375, h - I(t, W_H), n, item_h);
    if (!theme()->page_title_h) bar_title_sync(page);
    widget_invalidate_force(page, 0);
    return view;
}

/* page_list for a page with a name of its own and a message for no rows (page_empty): the bar still
 * reads the name. */
void *page_list_or(void *page, void *body, const char *name, const char *none, int n, int item_h) {
    void *view = page_list(page, body, 0, n ? name : none, n, item_h);
    if (!n) page_empty(body, name);
    return view;
}

/* The stock page_list heights, 48 a row and 64 a row with a detail, as the theme's. */
int page_h(int h) {
    const theme_t *T = theme();
    return h == 64 ? T->page_detail : h == 48 ? T->page_row : h;
}

/* The theme's page rows' text column. */
int page_x(void) { return theme()->page_x; }

/* A page_list caption written again, and the bar's when it holds it. */
void page_retitle(void *page, void *title, const char *caption) {
    widget_set_text_utf8(title, caption);
    if (!theme()->page_title_h) bar_title_sync(page);
}

static void *list(const char *title, int n) {
    return page_list(cf.page, cf.body, &cf.title, title, n, 48);
}

/* One row of a page_list (48px, the theme's page_h) or, with a detail (library_lists.c's Most
 * Played), a taller one: the caption over the detail in 16px #AAAAAA, stock's s_scrlabel_gray24l
 * grey, in the theme's boxes and fonts. iPod left-aligns them, as every stock list. Returns the
 * row. Shared (peq.h). */
void *page_row_detail(void *view, int index, const char *caption, const char *detail,
                      int (*click)(void *, void *)) {
    const theme_t *T = theme();
    int h = page_h(detail ? 64 : 48), x = T->page_x, w = 375 - 2 * x;
    void *item = list_item_create(view, 0, index * h, 375, h);
    widget_use_style(item, "s_listitem_black");
    void *label = text(item, x, !detail ? 0 : T->page_cap_y, w, !detail ? h : T->page_cap_h);
    widget_use_style(label, "s_scrlabel_white20l");
    widget_set_text_utf8(label, caption);
    theme_font(label, detail ? ROLE_TALL : ROLE_TITLE, FONT_ROW);
    if (detail) {
        label = text(item, x, T->page_det_y, w, T->page_det_h);
        widget_use_style(label, "s_scrlabel_white20l");
        widget_set_prop_int(label, "style:normal:text_color", (int)0xffaaaaaau);
        widget_set_prop_int(label, "style:normal:font_size", 16);
        widget_set_text_utf8(label, detail);
        theme_font(label, ROLE_META, FONT_ROW);
    }
    widget_on(item, EVT_CLICK, click, (void *)(long)index);
    return item;
}

/* Stock's list_into chevron at the right of a full-width row h high, as the Library's (its glyph
 * at x 322 to 332), the row's labels narrowed to end before it. Shared with peq_ui.c. */
void row_into(void *item, int h) {
    for (unsigned i = 0, n = widget_count_children(item); i < n; ++i) {
        void *label = widget_get_child(item, i);
        widget_move_resize(label, I(label, W_X), I(label, W_Y), 316 - I(label, W_X), I(label, W_H));
    }
    void *into = image_create(item, 302, 0, 50, h);
    image_base_set_image(into, "list_into");
}

/* A page_row_detail row that opens another page, with row_into's chevron. An empty detail centres
 * the caption in its 64px row, beside the chevron. */
void *page_row_detail_into(void *view, int index, const char *caption, const char *detail,
                           int (*click)(void *, void *)) {
    void *item = page_row_detail(view, index, caption, detail, click);
    const theme_t *T = theme();
    int h = page_h(detail ? 64 : 48);
    if (detail && !*detail)
        widget_move_resize(widget_get_child(item, 0), T->page_x, 0, 375 - 2 * T->page_x, h);
    row_into(item, h);
    return item;
}

void page_row_into(void *view, int index, const char *caption, int (*click)(void *, void *)) {
    page_row_detail_into(view, index, caption, 0, click);
}

/* clip: the canvas clip, read into old, narrowed to x, y, w, h; false when nothing shows. Shared
 * with navigation.c. */
int clip_within(void *canvas, int *old, int *clip, int x, int y, int w, int h) {
    canvas_get_clip_rect(canvas, old);
    clip[0] = old[0] > x ? old[0] : x;
    clip[1] = old[1] > y ? old[1] : y;
    clip[2] = (old[0] + old[2] < x + w ? old[0] + old[2] : x + w) - clip[0];
    clip[3] = (old[1] + old[3] < y + h ? old[1] + old[3] : y + h) - clip[1];
    if (clip[2] < 0) clip[2] = 0;
    if (clip[3] < 0) clip[3] = 0;
    return clip[2] && clip[3];
}

/* Video places, the last album, Boot to, games and the rest: each file is written whole, to a .tmp,
 * synced and renamed. Shared with navigation.c and videos.c; card_store.c does the same for the
 * records kept on the card. */
int blob_io(const char *path, const char *tmp, void *buf, unsigned size, int write) {
    void *f = fopen(write ? tmp : path, write ? "wb" : "rb");
    if (!f) return 0;
    int ok = write ? fwrite(buf, size, 1, f) == 1 : fread(buf, size, 1, f) == 1;
    if (write && (fflush(f) || fsync(fileno(f)))) ok = 0;
    if (fclose(f) || !ok) {
        if (!write) memset(buf, 0, size);
        return 0;
    }
    return !write || !rename(tmp, path);
}

/* Stock pattern (album rows, Now Playing): load the file, set it, drop the load's reference, so the
 * next paint decodes the file again rather than a stale cached copy. Returns 0 when the load fails,
 * as for the empty "no art" marker. size, if given, gets the image's width and height (bitmap_t
 * w @0, h @4). Shared with spotify.c. */
int image_show(void *img, const char *url, unsigned *size) {
    unsigned bitmap[64]; /* bitmap_t */
    if (widget_load_image(img, url, bitmap)) return 0;
    if (size) size[0] = bitmap[0], size[1] = bitmap[1];
    image_base_set_image(img, url);
    widget_unload_image(img, bitmap);
    widget_invalidate_force(img, 0); /* A reused file URL still contains new pixels. */
    return 1;
}

/* Depth (docs/internals.md#coverflow-depth), after PictureFlow's renderer: every cover within reach
 * of the visual position is projected column by column into one frame bitmap, farthest first, with
 * its reflection, then the frame is drawn over the slide_menu, whose own children stay empty. All
 * integer: 16.16 turns and rotations, geometry in 1/16 px, row spans in 1/256 px. */
#define CF_RING (2 * CF_REACH + 1)
/* the active theme's Cover Flow values, copied at page open (cf_restyle) */
cf_style_t cfs __attribute__((section(".scratch")));
/* with them, cf_restyle's: a side cover's sine, and its near edge's screen x (1/16 px), at t = 1;
 * the horizon's row (1/256 px) */
static int cf_s1 __attribute__((section(".scratch")));
static int cf_rest16 __attribute__((section(".scratch")));
static int cf_top256 __attribute__((section(".scratch")));
#define CF_SWING_T (CF_ONE * 45 / 100)
/* the horizon every cover shares, and a cover h high (1/256 px)'s top row on it */
#define CF_TOP256(h) (cf_top256 - (h) * cfs.horizon / 100)

int cf_floor(float x) {
    int i = (int)x;
    return i - (x < (float)i);
}
int cf_round(float x) { return cf_floor(x + .5f); }
float cf_smooth(float x) {
    x = x < 0 ? 0 : x > 1 ? 1 : x;
    return x * x * (3 - 2 * x);
}

/* cos and sin of 0 to CF_ANGLE (60) degrees in 16 steps, 16.16 */
static const int cf_cos[17] = { 65536, 65396, 64975, 64277, 63303, 62058, 60547, 58777, 56756,
                                54491, 51993, 49273, 46341, 43211, 39896, 36410, 32768 };
static const int cf_sin[17] = { 0,     4286,  8554,  12785, 16962, 21066, 25080, 28986, 32768,
                                36410, 39896, 43211, 46341, 49273, 51993, 54491, 56756 };
/* the same of 0 to 90 degrees, for a theme's other tilt */
const int cf_cos90[17] = { 65536, 65220, 64277, 62714, 60547, 57798, 54491, 50660, 46341,
                           41576, 36410, 30893, 25080, 19024, 12785, 6424,  0 };
static const int cf_sin90[17] = { 0,     6424,  12785, 19024, 25080, 30893, 36410, 41576, 46341,
                                  50660, 54491, 57798, 60547, 62714, 64277, 65220, 65536 };

/* p's c and s at a (16.16) of the way to CF_ANGLE, or of the way to 90 degrees if other. */
void turn(int a, int other, pose_t *p) {
    const int *cs = other ? cf_cos90 : cf_cos, *sn = other ? cf_sin90 : cf_sin;
    int i = a >> 12, f = a & 4095;
    p->c = cs[i] + (i < 16 ? (cs[i + 1] - cs[i]) * f >> 12 : 0);
    p->s = sn[i] + (i < 16 ? (sn[i + 1] - sn[i]) * f >> 12 : 0);
}

void pose(int t, pose_t *p) {
    int a = t < CF_ONE ? t : CF_ONE, other = cfs.tilt != CF_ANGLE;
    p->eye = CF_EYE16;
    turn(other ? a * cfs.tilt / 90 : a, other, p);
    if (t < CF_ONE) {
        /* xc = A t + B t^2: x1 at t = 1, arriving at the stack's xs slope, and quick at
         * first, so the covers turning in and out mid-step keep clear of each other. */
        int sq = (int)((unsigned)t * (unsigned)t >> 16);
        p->xc = (16 * (2 * cfs.x1 - cfs.xs) * t + 16 * (cfs.xs - cfs.x1) * sq) >> 16;
        p->zc = 16 * cfs.z1 * t >> 16;
        if (cfs.swing == 2) p->xc = 16 * cfs.x1 * t >> 16; /* straight out, as it turns */
        if (cfs.swing == 1) {
            /* Depth follows the turn, less a small swell of lift px mid-way. The near edge's screen
             * x is two parabolas meeting flat at 45% of the way, 3 px past its rest place, and xc
             * follows from it, so the far edge goes on swinging out with no corner in the path.
             * t = 1 lands on the rest pose below. */
            p->zc = 16 * cfs.z1 * (p->s >> 4) / (cf_s1 >> 4) - (16 * cfs.lift * (t - sq) >> 16);
            int den = CF_EYE16 + p->zc - (CF_HALF16 * p->s >> 16);
            int peak = cf_rest16 + 48, rising = t < CF_SWING_T;
            int q =
                ((rising ? CF_SWING_T - t : t - CF_SWING_T) << 12) /
                (rising ? CF_SWING_T : CF_ONE - CF_SWING_T); /* 0 at the peak, 4096 at each end */
            int near = peak - ((rising ? peak - CF_HALF16 : 48) * (q * q >> 12) >> 12);
            p->xc = near * den / CF_EYE16 - (CF_HALF16 * p->c >> 16);
            /* The eye draws back mid-turn, by eye_turn (1 - t^4), so the far edge is less steep.
             * Depth goes with it, which keeps the near edge's place and height; at t = 0 and 1 no
             * pixel moves. */
            p->eye += 16 * cfs.eye_turn * (CF_ONE - (int)((unsigned)sq * (unsigned)sq >> 16)) >> 16;
            p->zc = p->eye * den / CF_EYE16 - p->eye + (CF_HALF16 * p->s >> 16);
        }
        p->bright = 256 - ((256 - cfs.bright1) * t >> 16);
    } else {
        int e = t - CF_ONE;
        p->xc = 16 * cfs.x1 + (cfs.xs * e >> 12);
        p->zc = 16 * cfs.z1 + (cfs.zs * e >> 12);
        if (e < CF_ONE)
            p->bright = cfs.bright1 - ((cfs.bright1 - cfs.bright2) * e >> 16);
        else if (cfs.third) /* whole from the second on; with no thumb, seven covers, gone half a
                             * step past the third, where the ring's reach ends */
            p->bright = cfs.thumb || e < 2 * CF_ONE ? cfs.bright2
                        : e < 5 * CF_ONE / 2        ? cfs.bright2 * (5 * CF_ONE / 2 - e) >> 15
                                                    : 0;
        else
            p->bright = e < 2 * CF_ONE ? cfs.bright2 * (2 * CF_ONE - e) >> 16 : 0;
    }
}

/* Screen x (1/16 px right of the centre line) of the cover point u (1/16 px from its middle). */
int project(const pose_t *p, int u) {
    return (p->xc + (u * p->c >> 16)) * p->eye / (p->eye + p->zc - (u * p->s >> 16));
}
#ifdef PEQ_HOST
/* For test/coverflow.py: the follower's state, and a lead (< 0: none) to render at. */
void cf_thumb(float *out) {
    out[0] = th.pos, out[1] = th.vel, out[2] = th.goal, out[3] = th.lead, out[4] = th.rate;
    out[5] = (float)th.on, out[6] = (float)th.jump, out[7] = (float)th.lean;
    out[8] = (float)th.fold, out[9] = th.enter, out[10] = th.fold_off, out[11] = th.fold_vel;
    out[12] = th.arch, out[13] = (float)th.rebased;
}
void cf_thumb_lead(float lead) { th.on = lead >= 0, th.lead = lead >= 0 ? lead : 0; }
/* For test/coverflow.py: the arch formed so far (0: none), to render at. */
void cf_thumb_arch(float arch) { th.on = arch > 0, th.arch = arch; }
/* For test/coverflow.py: going by letters, the row leaning lean (0: not). */
void cf_thumb_lean(int lean) { th.on = th.jump = lean != 0, th.lean = lean; }
/* For test/coverflow.py: folding out of a row leaning lean since at (0: not). */
void cf_thumb_fold(int lean, unsigned at) {
    th.on = th.fold = lean != 0, th.jump = 0, th.lean = lean, th.fold_at = at;
    th.fold_off = th.fold_vel = 0;
}
/* For test/coverflow.py: a cover's brightness at rest, t (16.16) albums out. */
int cf_pose_bright(int t) {
    pose_t p;
    pose(t, &p);
    return p.bright;
}
/* For test/coverflow.py: an edge's path, finer than a frame's pixels. */
int cf_path_x(int t, int u) {
    pose_t p;
    pose(t, &p);
    return project(&p, u);
}
#endif

/* The cover point under screen x dx (1/16 px right of the centre line), the inverse of project;
 * 0 when the column misses the cover. *h gets the cover's projected height there (1/256 px). */
int unproject(const pose_t *p, int dx, int *u, int *h) {
    int den = (p->eye * p->c + dx * p->s) >> 16;
    if (den <= 0) return 0;
    *u = (dx * (p->eye + p->zc) - p->eye * p->xc) / den;
    if (*u < -CF_HALF16 || *u >= CF_HALF16) return 0;
    *h = ART_SIZE * 256 * p->eye / (p->eye + p->zc - (*u * p->s >> 16));
    return 1;
}

#define HOT static inline __attribute__((always_inline)) /* -Oz would call these per pixel */

/* p scaled by b (of 256), per 8-bit channel; alpha is left 0. */
HOT unsigned shade(unsigned p, unsigned b) {
    return ((p & 0xff00ffu) * b >> 8 & 0xff00ffu) | ((p & 0xff00u) * b >> 8 & 0xff00u);
}
/* A texel (RGBA8888, red low) as the screen's BGRA8888, opaque; both ways, but for the alpha. */
HOT unsigned bgra(unsigned p) { /* R and B from p turned half way round (one rotr) */
    return 0xff000000u | (p & 0xff00u) | ((p >> 16 | p << 16) & 0xff00ffu);
}

/* colour (alpha ignored) under what is already at px, covering cov (of 256) of the pixel */
HOT void put(unsigned *px, unsigned colour, unsigned cov) {
    unsigned a = *px >> 24, have = a + (a >> 7), w = cov * (256 - have) >> 8;
    if (!a && cov == 256) {
        *px = colour | 0xff000000u;
        return;
    }
    if (!w) return;
    have += w;
    *px = ((*px & 0xffffffu) + shade(colour, w)) | (have > 255 ? 255u : have) << 24;
}

/* What dimming washes towards: black, or Light Mode's paper (set per frame). */
static unsigned cf_paper;

/* The covers' corners (cf_corners): their radius in texels and its coverage table (corner_cover.c),
 * cf_r rows of cf_r from the outermost; 0 for square. */
static int cf_r;
static const unsigned char *cf_cover;
/* By texel column: the corner's rows from either end it covers in part, and of those the ones it
 * leaves clear; 0 off the corners (cf_corners) */
static unsigned char cf_part[ART_SIZE], cf_clear[ART_SIZE];

/* One texture column (texels ART_SIZE apart) h high (1/256 px) on the horizon at frame column col,
 * its partial end rows by coverage, then its reflection fading out below: a column off the
 * corners, the hot path. */
static void plain_column(shelf_t *f, int col, const unsigned *tex, int h, int bright) {
    if (h < 16) return;
    unsigned *d = f->d + col;
    int top = CF_TOP256(h), bottom = top + h, step = (ART_SIZE << 20) / (h >> 4);
    int ins = 0, clipped_top = top, clipped_bottom = bottom;
    int solid_top = f->solid_top[col], solid_bottom = f->solid_bottom[col];
    int y = clipped_top >> 8, end = (clipped_bottom + 255) >> 8,
        last = end < cfs.view_h ? end : cfs.view_h;
    int first = y, final = end - 1; /* the rows the edges cross */
    int v = (((y << 8) + 128 - top) >> 4) * step >> 4, pitch = f->pitch; /* v: 16.16 texel row */
    if (y < 0) v -= y * step, y = 0;
    unsigned *px = d + y * pitch, wash = shade(cf_paper, 256u - (unsigned)bright);
    int whole = bright >= 256; /* the texel as it is: shade(t, 256) is its colour, and no wash */
    while (y < last) {
        if (y >= solid_top && y < solid_bottom) {
            int skip = solid_bottom - y;
            v += skip * step, px += skip * pitch, y = solid_bottom;
            continue;
        }
        int run = y < solid_top && solid_top < last ? solid_top : last; /* up to the opaque span */
        for (; y < run; ++y, v += step, px += pitch) {
            unsigned cov = 256, t;
            if (y == first || y == final) { /* only the edge rows' centres can fall off the art */
                int from = y << 8 > clipped_top ? y << 8 : clipped_top,
                    to = (y + 1) << 8 < clipped_bottom ? (y + 1) << 8 : clipped_bottom;
                cov = (unsigned)(to - from);
                t = tex[(v < 0 ? 0 : v >> 16 >= ART_SIZE ? ART_SIZE - 1 : v >> 16) * ART_SIZE];
            } else
                t = tex[(v >> 16) * ART_SIZE];
            put(px, whole ? t : shade(t, (unsigned)bright) + wash, cov); /* put ignores t's alpha */
        }
    }
    /* The reflection starts where the body ends, sharing the row the body only partly covers. */
    int reflect = h * cfs.reflect / ART_SIZE, stop = (bottom + reflect + 255) >> 8;
    int fade = (256 << 16) / reflect; /* fading per 1/256 px, 16.16 */
    unsigned dim = (unsigned)bright * cfs.reflect_top >> 8;
    wash = shade(cf_paper, 256u - dim);
    if (stop > cfs.view_h) stop = cfs.view_h;
    for (y = (bottom + ins) >> 8, px = d + y * pitch; y < stop; ++y, px += pitch) {
        int from = y << 8 > bottom + ins ? y << 8 : bottom + ins,
            dist = ((from + ((y + 1) << 8)) >> 1) - bottom;
        if (dist >= reflect) break;
        if (y < 0 || (y >= solid_top && y < solid_bottom)) continue;
        int mirrored = dist * step >> 8 >> 16; /* texel rows up from the bottom edge */
        int row = ART_SIZE - 1 - (mirrored >= ART_SIZE ? ART_SIZE - 1 : mirrored);
        unsigned cov =
            (unsigned)(((y + 1) << 8) - from) * (256u - (unsigned)(dist * fade >> 16)) >> 8;
        put(px, shade(tex[row * ART_SIZE], dim) + wash, cov);
    }
    /* The rows this body covers whole are opaque now: join them to the known span, or keep the
     * longer of the two. */
    int s0 = (clipped_top + 255) >> 8, s1 = clipped_bottom >> 8;
    s0 = s0 < 0 ? 0 : s0, s1 = s1 > cfs.view_h ? cfs.view_h : s1;
    if (s1 <= s0) return;
    if (solid_bottom > solid_top && s0 <= solid_bottom && s1 >= solid_top) {
        s0 = s0 < solid_top ? s0 : solid_top;
        s1 = s1 > solid_bottom ? s1 : solid_bottom;
    } else if (s1 - s0 <= solid_bottom - solid_top)
        return;
    f->solid_top[col] = (short)s0, f->solid_bottom[col] = (short)s1;
}

/* The same in a corner's column, texel k across the art: each end row the corner covers in part
 * takes the corner pixel's coverage at its texel too (docs/internals.md#rounded-corners), so the
 * arc is anti-aliased where it runs steep as well as where it runs flat; its reflection mirrors it.
 * Only the corner's bands (the rows from either end whose texel row is under part) go a row at a
 * time; the rows between them are whole and go as plain_column's do, and the rows the corner leaves
 * wholly clear are not visited. */
static void corner_column(shelf_t *f, int col, const unsigned *tex, int h, int bright, int k) {
    unsigned *d = f->d + col;
    int part = cf_part[k], clear = cf_clear[k], R = cf_r;
    const unsigned char *corner = cf_cover + (k < R ? k : ART_SIZE - 1 - k);
    int top = CF_TOP256(h), bottom = top + h, step = (ART_SIZE << 20) / (h >> 4);
    int ins = h * part / ART_SIZE, out = h * clear / ART_SIZE;
    int solid_top = f->solid_top[col], solid_bottom = f->solid_bottom[col], pitch = f->pitch;
    int y = (top + out) >> 8, end = (bottom - out + 255) >> 8, last = end < cfs.view_h ? end : cfs.view_h;
    /* [b1, b2): the rows whose centres lie past both bands, whole and of no corner */
    int b1 = (top + ins + 255) >> 8, b2 = (bottom - ins) >> 8;
    int v = (((y << 8) + 128 - top) >> 4) * step >> 4; /* 16.16 texel row */
    if (y < 0) v -= y * step, y = 0;
    unsigned *px = d + y * pitch, wash = shade(cf_paper, 256u - (unsigned)bright);
    int whole = bright >= 256;
    while (y < last) {
        if (y >= solid_top && y < solid_bottom) {
            int skip = solid_bottom - y;
            v += skip * step, px += skip * pitch, y = solid_bottom;
            continue;
        }
        if (y >= b1 && y < b2) {
            int run = b2 < last ? b2 : last;
            if (y < solid_top && solid_top < run) run = solid_top;
            for (; y < run; ++y, v += step, px += pitch) {
                unsigned t = tex[(v >> 16) * ART_SIZE];
                put(px, whole ? t : shade(t, (unsigned)bright) + wash, 256);
            }
            continue;
        }
        int row = v < 0 ? 0 : v >> 16 >= ART_SIZE ? ART_SIZE - 1 : v >> 16;
        int cr = row < ART_SIZE / 2 ? row : ART_SIZE - 1 - row; /* corner row */
        int from = y << 8 > top ? y << 8 : top, to = (y + 1) << 8 < bottom ? (y + 1) << 8 : bottom;
        unsigned cov = (unsigned)(to - from), c = cr < part ? corner[cr * R] : 255;
        cov = cov * (c + (c >> 7)) >> 8; /* of 256, no divide */
        if (cov) put(px, whole ? tex[row * ART_SIZE] : shade(tex[row * ART_SIZE], (unsigned)bright) + wash, cov);
        ++y, v += step, px += pitch;
    }
    int reflect = h * cfs.reflect / ART_SIZE, stop = (bottom + reflect + 255) >> 8;
    int fade = (256 << 16) / reflect;
    unsigned dim = (unsigned)bright * cfs.reflect_top >> 8;
    wash = shade(cf_paper, 256u - dim);
    if (stop > cfs.view_h) stop = cfs.view_h;
    for (y = (bottom + out) >> 8; y < stop; ++y) {
        int from = y << 8 > bottom ? y << 8 : bottom, dist = ((from + ((y + 1) << 8)) >> 1) - bottom;
        if (dist >= reflect) break;
        if (y < 0 || (y >= solid_top && y < solid_bottom)) continue;
        int mirrored = dist * step >> 8 >> 16; /* texel rows up from the bottom edge */
        int row = ART_SIZE - 1 - (mirrored >= ART_SIZE ? ART_SIZE - 1 : mirrored);
        unsigned cov = (unsigned)(((y + 1) << 8) - from) * (256u - (unsigned)(dist * fade >> 16)) >> 8;
        unsigned c = mirrored < part ? corner[mirrored * R] : 255;
        put(d + y * f->pitch, shade(tex[row * ART_SIZE], dim) + wash, cov * (c + (c >> 7)) >> 8);
    }
    /* the rows it covers whole join the opaque span, as plain_column's do */
    int s0 = (top + ins + 255) >> 8, s1 = (bottom - ins) >> 8;
    s0 = s0 < 0 ? 0 : s0, s1 = s1 > cfs.view_h ? cfs.view_h : s1;
    if (s1 <= s0) return;
    if (solid_bottom > solid_top && s0 <= solid_bottom && s1 >= solid_top) {
        s0 = s0 < solid_top ? s0 : solid_top;
        s1 = s1 > solid_bottom ? s1 : solid_bottom;
    } else if (s1 - s0 <= solid_bottom - solid_top)
        return;
    f->solid_top[col] = (short)s0, f->solid_bottom[col] = (short)s1;
}

/* A column, k its texel across the art or -1: a corner's to corner_column, else the plain one. */
static void column(shelf_t *f, int col, const unsigned *tex, int h, int bright, int k) {
    if (h < 16) return;
    if (k >= 0 && cf_part[k])
        corner_column(f, col, tex, h, bright, k);
    else
        plain_column(f, col, tex, h, bright);
}

#ifdef PEQ_HOST
static void cf_corners(void);
/* test/coverflow.py's column_fuzz: column() alone, on the caller's frame and solid span (solid[0]
 * its top, solid[1] its bottom, kept), the open page's values and corners, the light half's paper
 * as the render takes it; k the column's texel, or -1. */
void cf_column(unsigned *d, int pitch, short *solid, int col, const unsigned *tex, int h,
               int bright, int k) {
    static shelf_t f;
    cf_corners();
    cf_paper = ipod_light() ? scheme()->paper : 0;
    f.d = d, f.pitch = pitch, f.solid_top[col] = solid[0], f.solid_bottom[col] = solid[1];
    column(&f, col, tex, h, bright, k);
    solid[0] = f.solid_top[col], solid[1] = f.solid_bottom[col];
}
#endif

/* The cover t (16.16, signed) turns from the visual position, under what is drawn. */
static unsigned short cf_inset[ART_SIZE]
    __attribute__((section(".scratch"))); /* 1/256 texel, symmetric */

/* Copy the theme's Cover Flow values to cfs. */
void cf_restyle(void) {
    pose_t rest;
    cfs = theme()->cf;
    pose(CF_ONE, &rest);
    cf_s1 = rest.s, cf_rest16 = project(&rest, CF_HALF16);
    cf_top256 = 256 * cfs.top + 40960 * cfs.horizon / 100;
}
/* cf_inset (coverflow_hit's), cf_r and cf_cover (column's) follow the theme's radius. */
static void cf_corners(void) {
    static short filled __attribute__((section(".scratch")));
    int radius = cfs.radius;
    if (radius == filled && (cf_cover || !cf_r)) return;
    filled = (short)radius;
    int r = ART_SIZE * radius / 166;
    cf_r = r, cf_cover = corner_cover(r);
    memset(cf_part, 0, sizeof cf_part), memset(cf_clear, 0, sizeof cf_clear);
    for (int k = 0; cf_cover && k < r; ++k) {
        int n = 0, z = 0;
        while (n < r && cf_cover[n * r + k] < 255) z += !cf_cover[n * r + k], ++n;
        cf_part[k] = cf_part[ART_SIZE - 1 - k] = (unsigned char)n;
        cf_clear[k] = cf_clear[ART_SIZE - 1 - k] = (unsigned char)z;
    }
    memset(cf_inset, 0, sizeof cf_inset);
    for (int k = 0; k < r; ++k)
        cf_inset[k] = cf_inset[ART_SIZE - 1 - k] = (unsigned short)(arc_out(r, k) * 16);
}

/* One mapping of the art across a cover, at rest (draw_cover) and in the row (draw_quad): the texel
 * (0 at the far edge) at x of n from the far edge, the near half q (of 256) times as wide as the
 * far half; 256 spaces them evenly. Unsigned, x q stays under 2^32 / ART_SIZE. */
static int cf_texel(int x, int n, int q) {
    unsigned k = (unsigned)x * (unsigned)q * ART_SIZE / (unsigned)((n - x) * 256 + x * q);
    return k >= ART_SIZE ? ART_SIZE - 1 : (int)k;
}
/* A cover's q, near and far heights hn and hf: on the screen (per hf), r, from 1 at
 * coverflow.shelf.art_persp 0 to the plane's own hn / hf at 100; over the plane's own spacing
 * (per hn), r / (hn / hf), so 100 leaves it exactly as it is. */
static int cf_art_q(int hn, int hf, int per) {
    return ((hf + (hn - hf) * cfs.art_persp / 100) << 8) / per;
}

/* A right-hand cover's pose p, mirrored to the left when side < 0. */
void draw_posed(shelf_t *f, const pose_t *p, int side, const unsigned *tex) {
    if (p->bright <= 0 || !tex) return;
    int a = project(p, -CF_HALF16), b = project(p, CF_HALF16);
    int lo = (16 * CF_CX + (a < b ? a : b)) >> 4, hi = (16 * CF_CX + (a < b ? b : a)) >> 4;
    /* the edges' depths: far over near is the near edge's height over the far edge's */
    int dn = p->eye + p->zc - (CF_HALF16 * p->s >> 16), df = dn + 2 * (CF_HALF16 * p->s >> 16);
    int q = cf_art_q(df, dn, df);
    for (int v = lo; v <= hi; ++v) { /* v: the column as if the cover were on the right */
        int col = side > 0 ? v : 2 * CF_CX - 1 - v, u, h;
        if (col < 0 || col >= CF_VIEW_W || !unproject(p, (2 * v + 1 - 2 * CF_CX) * 8, &u, &h))
            continue;
        int k = cf_texel(u + CF_HALF16, 2 * CF_HALF16, q);
        column(f, col, tex + (side > 0 ? k : ART_SIZE - 1 - k), h, p->bright, k);
    }
}

static void draw_cover(shelf_t *f, int t, const unsigned *tex) {
    pose_t p;
    int side = t < 0 ? -1 : 1;
    arch_pose(t * side, &p);
    draw_posed(f, &p, side, tex);
}

/* A cover whose near and far edges have left its pose, between xl and xr (1/16 px right of the
 * centre line), its height hl to hr (1/256 px) straight across; the shorter edge is the far one. */
void draw_quad(shelf_t *f, int xl, int xr, int hl, int hr, const unsigned *tex, int bright) {
    int lo = (16 * CF_CX + xl) >> 4, hi = (16 * CF_CX + xr) >> 4;
    int left = hr < hl, hf = left ? hr : hl, q = hf > 0 ? cf_art_q(hl + hr - hf, hf, hf) : 256;
    lo = lo < 0 ? 0 : lo, hi = hi >= CF_VIEW_W ? CF_VIEW_W - 1 : hi;
    for (int col = lo; xr > xl && col <= hi; ++col) {
        int dx = (2 * col + 1 - 2 * CF_CX) * 8 - xl;
        if (dx < 0 || dx >= xr - xl) continue;
        float p = (float)dx / (float)(xr - xl);
        int k = cf_texel(left ? xr - xl - dx : dx, xr - xl, q);
        k = left ? ART_SIZE - 1 - k : k;
        column(f, col, tex + k, (int)((float)hl + (float)(hr - hl) * p), bright, k);
    }
}

/* Covers decoded either side: more under a thumb theme, whose row shows about 11. */
static int fx_reach(void) { return cfs.thumb ? CF_WIDE_REACH : CF_REACH; }

/* Whether the shelf draws its cover t (16.16) out. Still (nothing moving: cf_still, set only for
 * coverflow_paint's and flip_sides' own renders), the seven it always has. Moving, all of the
 * ring, fx_reach each side: the cover coming in at either edge is whole from its first pixel, its
 * reflection too, and the ring's ends add nothing to the frame. */
static int cf_still, fx_still; /* fx_still: the frame's, drawn */
#ifdef PEQ_HOST
void cf_shelf_still(int still) { cf_still = still; }
#endif
static int shelf_draws(int t) { return !cf_still || ABS(t) <= CF_REACH * CF_ONE; }

/* The ring slot of the k-th nearest pair's nearer (i 0) or farther (i 1) cover at frac: the side
 * the position is moving away from is farther. */
static int slot(int frac, int k, int i) {
    return (i == 0) == (frac < 0) ? CF_REACH - k : CF_REACH + k;
}

/* The shelf at frac (16.16, -CF_ONE/2 to under CF_ONE/2) past its centre cover: ring[j] is the
 * texture of the cover j - CF_REACH from the centre, or 0 to leave it out, j from CF_REACH -
 * fx_reach to CF_REACH + fx_reach. Nearest first; nearer covers and their reflections overlap
 * farther ones. */
void coverflow_render(unsigned *d, int pitch, int frac, const unsigned *const *ring) {
    if (!cfs.eye) cf_restyle(); /* once, for callers which render without opening the page */
    cf_corners();
    cf_paper = ipod_light() ? scheme()->paper : 0;
    shelf_t f;
    f.d = d, f.pitch = pitch;
    for (int x = 0; x < CF_VIEW_W; ++x) f.solid_top[x] = f.solid_bottom[x] = 0;
    for (int y = 0; y < cfs.view_h; ++y) memset(d + y * pitch, 0, CF_VIEW_W * 4);
    if (th.on && th.fold)
        draw_fold(&f, ring, fold_t16(), th.lean < 0 ? -1 : 1, (int)(th.fold_off * CF_ONE));
    else if (th.on && (th.jump || th.lead))
        draw_row(&f, frac, ring);
    else
        for (int k = 0; k <= fx_reach(); ++k)
            for (int i = 0; i < (k ? 2 : 1); ++i) {
                int j = slot(frac, k, i), t = (j - CF_REACH) * CF_ONE - frac;
                if (shelf_draws(t)) draw_cover(&f, t, ring[j]);
            }
    /* Over black, or Light Mode's paper as the page's black flips to it; then in the screen's
     * BGRA8888, opaque, as stock copies it (opaque_frame): stock's blends and Night Shift read the
     * alpha it leaves on the screen. */
    for (int y = 0; y < cfs.view_h; ++y) {
        unsigned *p = d + y * pitch;
        int x = CF_VIEW_W; /* count-down loops, which -Oz keeps tight */
        if (cf_paper) {
            do put(p, cf_paper, 256), *p = bgra(*p), ++p;
            while (--x);
        } else {
            do *p = bgra(*p), ++p;
            while (--x);
        }
    }
}

#define CF_MISS 99

/* The ring offset of the frontmost cover body (not its reflection) drawn at frame pixel x, y at
 * frac, or CF_MISS; nearest first, in the drawing order, rounded corners (iPod) left out, and only
 * the covers the frame on the screen draws (a still one, at rest, the seven). With no thumb, a
 * cover fading out at the edge, under a quarter bright, is no target. */
int coverflow_hit(int frac, int x, int y) {
    for (int k = 0; k <= (fx_still ? CF_REACH : fx_reach()); ++k)
        for (int i = 0; i < (k ? 2 : 1); ++i) {
            int j = slot(frac, k, i), t = (j - CF_REACH) * CF_ONE - frac, side = t < 0 ? -1 : 1;
            int v = side > 0 ? x : 2 * CF_CX - 1 - x, u, h;
            pose_t p;
            pose(t * side, &p);
            if (p.bright < 64 || !unproject(&p, (2 * v + 1 - 2 * CF_CX) * 8, &u, &h)) continue;
            int ins = h * cf_inset[(u + CF_HALF16) >> 4] / (ART_SIZE * 256), top = CF_TOP256(h);
            if ((y << 8) + 128 >= top + ins &&
                (y << 8) + 128 < top + h - ins) /* column()'s corners */
                return j - CF_REACH;
        }
    return CF_MISS;
}

/* The texture pool: fx_reach's either side (fx_base), then the placeholder; going by letters, up to
 * FX_POOL with fx.more's. */
int fx_base(void) { return 2 * fx_reach() + 1; }
static int fx_slots(void) { return fx.more ? FX_POOL : fx_base(); }
cf_depth_t fx __attribute__((section(".scratch")));
unsigned *fx_buf(int i) { /* slot i's texture */
    return i < fx_base() ? fx.tex + i * CF_TEXELS : fx.more + (i - fx_base()) * CF_TEXELS;
}

/* A decoded image copied into a texture: fitted whole and centred, nearest sampled to ART_SIZE,
 * over black, so a non-square cover shows uncropped between black bars, as Now Playing shows it.
 * Only 32-bit formats (BITMAP_RGBA_AT); stock decodes covers to RGBA8888 with straight alpha,
 * composited over the page (black or Light Mode's paper). The image manager's copy is only read,
 * and a cover's load is dropped again at once, as stock does. */
int cf_decode(const char *url, unsigned *out, int unload) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    unsigned bitmap[64]; /* bitmap_t */
    if (widget_load_image(cf.page, url, bitmap)) return 0;
    unsigned w = bitmap[0], h = bitmap[1], format = ((unsigned short *)bitmap)[7] - 1u;
    const unsigned char *data =
        format < 4 && w && h && w <= 4096 && h <= 4096 ? bitmap_lock_buffer_for_read(bitmap) : 0;
    if (data) {
        const unsigned char *o = at[format];
        unsigned stride = bitmap_get_line_length(bitmap), side = w > h ? w : h, xoff[ART_SIZE];
        unsigned paper = ipod_light() ? scheme()->paper & 255 : 0;
        for (unsigned x = 0; x < ART_SIZE; ++x) { /* ~0u: a bar; left of the image wraps to one */
            unsigned sx = x * side / ART_SIZE - (side - w) / 2;
            xoff[x] = sx < w ? sx * 4 : ~0u;
        }
        for (unsigned y = 0; y < ART_SIZE; ++y) {
            unsigned sy = y * side / ART_SIZE - (side - h) / 2; /* wraps above the image */
            for (unsigned x = 0; x < ART_SIZE; ++x) {
                if (sy >= h || xoff[x] == ~0u) {
                    *out++ = 0xff000000u;
                    continue;
                }
                const unsigned char *px = data + sy * stride + xoff[x];
                unsigned a = px[o[3]], r = px[o[0]], g = px[o[1]], b = px[o[2]], k = 255 - a;
                /* over the page: black, or Light Mode's paper (the placeholder's corners) */
                if (a < 255)
                    r = (r * a + paper * k) / 255, g = (g * a + paper * k) / 255,
                    b = (b * a + paper * k) / 255;
                *out++ = 0xff000000u | r | g << 8 | b << 16;
            }
        }
        bitmap_unlock_buffer(bitmap);
    }
    if (unload) widget_unload_image(cf.page, bitmap);
    return data != 0;
}

static void fx_close(void) {
    ld_quit();
    if (fx.frame) bitmap_destroy(fx.frame);
    free(fx.tex);
    free(fx.more);
    memset(&fx, 0, sizeof(fx));
}

static void fx_placeholder(void) {
    unsigned *placeholder = FX_PLACEHOLDER;
    if (!cf_decode(PLACEHOLDER, placeholder, 0)) /* a theme image: the manager keeps it */
        for (int i = 0; i < CF_TEXELS; ++i) placeholder[i] = 0xff3a3a3au;
    fx.light = ipod_light();
}
/* A w x h bitmap of format (1 RGBA8888, or LCD_BGRA8888) the renderers fill whole, so flagged
 * opaque; 0 when out of memory. Stock's draw_image copies an opaque 1:1 image of the screen's own
 * format a row at a time (image_copy, 0x688f18) and blends any other, at 12 times the cost. */
static void *opaque_frame(int w, int h, int format) {
    void *b = bitmap_create_ex(w, h, w * 4, format);
    if (b) *(unsigned short *)((char *)b + 0xc) |= 1; /* BITMAP_FLAG_OPAQUE */
    return b;
}

/* The frame and every texture at once, or neither: the flat covers then stand in. Under a thumb
 * theme 16 slots, 1.6 MB. */
static int fx_open(void) {
    fx.frame = opaque_frame(CF_VIEW_W, cfs.view_h, LCD_BGRA8888);
    fx.tex = fx.frame ? calloc((unsigned)(fx_base() + 1), CF_TEXELS * 4) : 0;
    if (!fx.tex) {
        fx_close();
        return 0;
    }
    fx_placeholder();
    return 1;
}

/* Album a's art as a file:// url once its file exists (its cache file, a playlist's from
 * playlist_art.c, which queues a build until then), else 0: a card, or no art yet. */
static int art_url(char *url, unsigned a) {
    void *r = a < deque_size(cf.albums) ? deque_at(cf.albums, a) : 0;
    if (!r) return 0;
    if (cf.scoped == PLAYLISTS) return playlist_art(I(r, REC_DISC), P(r, REC_TITLE), 0, url, 600);
    art_path(url + 7, album_key(r), "");
    return art_exists(url);
}

/* Album a's art into pool slot i: its half copy when half will do and it has one (never a
 * playlist's: they have none), else its art_url decoded, else the placeholder (no art, or a
 * card). */
void fx_fill(int i, int a, int half) {
    unsigned *buf = fx_buf(i);
    char url[600] = "file://";
    fx.art[i] = FX_PLACEHOLDER, fx.half[i] = 0;
    if ((unsigned)a >= deque_size(cf.albums)) return;
    if (half && cf.scoped != PLAYLISTS &&
        half_read(album_key(deque_at(cf.albums, (unsigned)a)), buf))
        fx.art[i] = buf, fx.half[i] = 1;
    else if (art_url(url, (unsigned)a) && cf_decode(url, buf, 1))
        fx.art[i] = buf;
}

/* want's k albums + 1 and album a (of n, round the ring) if it isn't one, while the pool has room;
 * fx_around: the window r either side of album c, nearest first. */
static int fx_want(int *want, int k, int a, int n) {
    a = (a % n + n) % n + 1;
    for (int i = 0; i < k; ++i)
        if (want[i] == a) return k;
    if (k < fx_slots()) want[k++] = a;
    return k;
}
static int fx_around(int *want, int k, int c, int n, int r) {
    for (int m = 0; m <= 2 * r; ++m) k = fx_want(want, k, c + (m & 1 ? (m + 1) / 2 : -m / 2), n);
    return k;
}
/* album + 1's pool slot, or a free one taken for it; -1 with none free */
int fx_slot(int album) {
    int i = 0, free = -1;
    for (; i < fx_slots() && fx.album[i] != album; ++i)
        if (!fx.album[i] && free < 0) free = i;
    if (i < fx_slots()) return i;
    if (free >= 0) fx.album[free] = album, fx.art[free] = 0, fx.half[free] = 0;
    return free;
}

/* With Fast Navigation on a frame's loads stop at FX_BUDGET_MS, FX_ENTER_MS in the entry and
 * FX_LETTERS_MS going by letters, or FX_SLACK_MS in a frame FX_LATE_MS or more after the last
 * (docs/internals.md#coverflow-thumb); off, 1.7.6's FX_DECODES a frame. */
#define FX_DECODES 2
#define FX_BUDGET_MS 8
#define FX_ENTER_MS 2   /* the entry's budget */
#define FX_LETTERS_MS 1 /* going by letters' */
#define FX_SLACK_MS 10  /* a late frame's going by letters */
#define FX_LATE_MS 30   /* two refreshes of 16.7 ms, less some jitter */
/* album a within r albums of album c, round the ring of n */
static int fx_near(int a, int c, int n, int r) {
    int d = ((a - c) % n + n) % n;
    return d <= r || n - d <= r;
}
/* Letters over (any landing on album c, painted or not, or a touch): the loader asked for nothing
 * more, so it parks after the read in hand and the UI thread never waits on it; the covers kept
 * away from c are dropped, so c's window, in fx.more's slots or not, takes the base slots, those in
 * fx.more's move to the free ones before it, and fx.more goes. ponytail: the thread and its stage
 * (LD_SLOTS half copies, 0.8 MB) stay until the page closes (fx_close joins it); join here once no
 * request is LD_READ if that memory is wanted back. */
void fx_unletter(int c) {
    if (fx.ld_on > 0)
        ld_ask(0, 0);
    else if (fx.ld_on < 0)
        ld_quit(); /* a start that failed: tried again by the next letters */
    if (!fx.more && !fx.no_more) return;
    int n = cf.albums ? (int)deque_size(cf.albums) : 0;
    for (int i = 0; n && i < FX_POOL; ++i)
        if (fx.album[i] && !fx_near(fx.album[i] - 1, c, n, fx_reach())) fx.album[i] = 0;
    for (int i = fx_base(), j = 0; fx.more && i < FX_POOL; ++i) {
        if (!fx.album[i]) continue;
        while (j < fx_base() && fx.album[j]) ++j;
        if (j < fx_base()) {
            fx.album[j] = fx.album[i], fx.half[j] = fx.half[i], fx.art[j] = fx.art[i];
            if (fx.art[i] == fx_buf(i)) fx.art[j] = memcpy(fx_buf(j), fx_buf(i), CF_TEXELS * 4);
        }
        fx.album[i] = 0;
    }
    free(fx.more);
    fx.more = 0, fx.no_more = 0;
}
/* The covers wanted around album c of n stay loaded, others are dropped as movement crosses albums;
 * ring gets each ring slot's texture: ring[j] the cover j - CF_REACH from the centre, so a thumb
 * theme's goes either side of the seven (the callers' wide). A small library repeats albums round
 * the ring, as the slide_menu wraps. Wanted: the window on the screen and, going by letters, the
 * window of the letter gone to, the albums ahead towards it and the letters' either side; half
 * copies from the loader (ld_ask) going by letters, the rest decoded, within a frame's budget, the
 * order and the rules in docs/internals.md#coverflow-thumb. A cover not loaded shows the
 * placeholder until a later frame (fx.todo) loads it. At rest every cover on the screen is decoded
 * at once, but for the covers' first frames (fx_fresh): there the centre alone, the rest within
 * the budget, so an open shows the covers a frame's decodes sooner (seven, 40 ms on the emulator).
 */
static int fx_fresh __attribute__((section(".scratch")));
static void fx_window(int c, int n, const unsigned **ring) {
    int want[FX_POOL], r = fx_reach(), k = 0, at = 0, sh = 0, to = 0;
    unsigned t0 = (unsigned)time_now_ms();
    int lean = th.on && th.jump && th.enter <= 0, late = (int)(t0 - fx.paint_at) >= FX_LATE_MS;
    int half = th.on && (th.jump || th.fold), full = !th.on || (lean && th.pos == th.goal), any = 0;
    fx.paint_at = t0;
    /* With no room for the letters' windows, the screen's alone, which always fits. The calloc is
     * an mmap the kernel's overcommit grants whatever is free, so short of MemAvailable for it and
     * MP4_SPARE_KB more it is not tried: else its pages, filled, would bring the OOM killer. */
    if (half && !fx.more && !fx.no_more) {
        unsigned size = (unsigned)(FX_POOL - fx_base()) * CF_TEXELS * 4;
        fx.no_more = mem_available_kb() < size / 1024 + MP4_SPARE_KB ||
                     !(fx.more = calloc((unsigned)(FX_POOL - fx_base()), CF_TEXELS * 4));
    }
    if (half && !fx.more) lean = 0;
    if (lean) { /* each letter's window sh on from its first album, as it is with the row there */
        at = I(cf.slide, SLIDE_INDEX), sh = coverflow_window(0), to = at + cf_round(th.goal);
        k = fx_around(want, 0, to + sh, n, r);
    }
    k = fx_around(want, k, c, n, r);
    if (lean) {
        int go = cf_round(th.goal) - cf_round(th.pos), dir = go < 0 ? -1 : 1;
        for (int d = r + 1; d < ABS(go) - r && d <= r + CF_WIDE; ++d)
            k = fx_want(want, k, c + dir * d, n);
        to = (to % n + n) % n;
        if (fx.letters_at != to + 1) {
            unsigned letter;
            fx.letters_at = to + 1;
            fx.next = coverflow_letter_next(cf.slide, to, 1, n, &letter);
            fx.prev = coverflow_letter_next(cf.slide, to, -1, n, &letter);
        }
        k = fx_around(want, fx_around(want, k, fx.next + sh, n, r), fx.prev + sh, n, r);
    }
    for (int i = 0; i < fx_slots(); ++i) {
        int keep = 0;
        for (int j = 0; j < k; ++j) keep |= fx.album[i] == want[j];
        if (!keep) fx.album[i] = 0;
    }
    int budget = !lean  ? th.on && th.jump ? FX_ENTER_MS : FX_BUDGET_MS
                 : late ? FX_SLACK_MS
                        : FX_LETTERS_MS;
    int big = ringnav_big_library(), decodes = FX_DECODES;
    fx.todo = 0;
    if (half || fx.ld_on > 0) ld_ask(want, half ? k : 0);
    if (!half) fx_unletter(c);
    for (int up = 0; up < 2; ++up)
        for (int m = 0; m < k; ++m) {
            int i = fx_slot(want[m]), h = half && !up,
                s = h && fx.ld_on > 0 ? ld_find(want[m]) : -1;
            int near = fx_near(want[m] - 1, c, n, r);
            if (up ? !(near && full && fx.half[i]) : fx.art[i] != 0) continue;
            if (h && fx.ld_on > 0 && (s < 0 || ld_state(s) != LD_READY)) { /* still on the card */
                fx.todo = 1;
                continue;
            }
            unsigned t = (unsigned)time_now_ms();
            int spent = (int)(t - t0) + (h && (s < 0 || fx.ld[s].ok) ? fx.half_ms : fx.full_ms);
            if ((!th.on && !up && m <= 2 * CF_REACH && (!fx_fresh || !m)) ||
                (big ? !any || spent <= budget : decodes-- > 0)) {
                if (s >= 0)
                    ld_take(s, i, want[m] - 1);
                else
                    fx_fill(i, want[m] - 1, h);
                if (fx.art[i] != FX_PLACEHOLDER) /* a copy wanted and none there: a decode's */
                    *(fx.half[i] ? &fx.half_ms : &fx.full_ms) = (int)((unsigned)time_now_ms() - t);
                any = 1;
                if (near) fx.drawn = 0;
            } else {
                fx.todo = 1;
            }
        }
    for (int j = -r; j <= r; ++j) {
        int i = fx_slot(((c + j) % n + n) % n + 1);
        ring[j + CF_REACH] = i >= 0 && fx.art[i] ? fx.art[i] : FX_PLACEHOLDER;
    }
    if (!fx.todo) fx_fresh = 0;
}

#ifdef PEQ_HOST
/* For test/coverflow.py: album a's art in the pool: 0 none, 1 its half copy, 2 its own, 3 the
 * placeholder. */
int cf_fx_has(int a) {
    for (int i = 0; i < fx_slots(); ++i)
        if (fx.album[i] == a + 1 && fx.art[i])
            return fx.art[i] == FX_PLACEHOLDER ? 3 : fx.half[i] ? 1 : 2;
    return 0;
}
unsigned cf_album_key(int a) { return album_key(deque_at(cf.albums, (unsigned)a)); }
/* What letters hold: 1 fx.more, 2 the loader's stage, 4 its thread; 8 a slot past the pool's end
 * kept, or art in none of its own texture, the placeholder or nothing. */
int cf_fx_letters(void) {
    int bad = 0;
    for (int i = 0; i < FX_POOL; ++i)
        bad |= fx.album[i] && (i >= fx_slots() || (fx.art[i] && fx.art[i] != FX_PLACEHOLDER &&
                                                   fx.art[i] != fx_buf(i)));
    return (fx.more != 0) | (fx.stage != 0) << 1 | (fx.ld_on > 0) << 2 | bad << 3;
}
#endif

static int floor_div(int a, int b) { /* floor division, b > 0 */ return a / b - (a % b < 0); }

/* The visual position from the slide_menu's index and live offset (the wheel's animator): album c
 * (of n) at the centre and frac past it. Stock completion commits index - offset / stride. */
int visual(void *s, int n, int *c, int *frac) { /* shared with coverflow_position.c */
    int stride = slide_menu_item_width(s) + I(s, SLIDE_SPACER), d = -I(s, SLIDE_OFFSET);
    if (n <= 0 || stride <= 0) return 0;
    int q = floor_div(2 * d + stride, 2 * stride);
    *frac = (d - q * stride) * CF_ONE / stride;
    *c = ((I(s, SLIDE_INDEX) + q) % n + n) % n;
    return stride;
}

/* The playlists stop at both ends (home_step bounces there): a ring of one or two would otherwise
 * repeat round the shelf. */
int coverflow_bounded(void *w) { return w && w == cf.slide && cf.scoped == PLAYLISTS; }
int coverflow_slide(void *w) { return w && w == cf.slide; }

/* What a slide_menu stands for, by index (docs/internals.md#coverflow-depth): Cover Flow's with
 * depth holds only CF_POOL children and SLIDE_INDEX is the album's, of cf.count (the albums and
 * cards); the flat fallback and Home's hold one child per index. Stock reads its children through
 * slide_menu_fix_index (the index modulo the children) and never wraps the index itself, but for
 * its scroll end (slide_menu_on_scroll_done), which Cover Flow's glides end in their own
 * (nav/home_carousel.c home_done). */
int slide_count(void *w) { return w && w == cf.slide ? cf.count : (int)widget_count_children(w); }
/* The child that stands for index i: the pool's slot i modulo its size. */
void *slide_child(void *w, int i) {
    int k = (int)widget_count_children(w);
    return k ? widget_get_child(w, (unsigned)((i % k + k) % k)) : (void *)0;
}
/* The index child j stands for now: of the indexes that use it, the one nearest the centre
 * (SLIDE_INDEX), round the ring of slide_count. */
int slide_child_index(void *w, int j) {
    int k = (int)widget_count_children(w), n = slide_count(w), c = I(w, SLIDE_INDEX);
    if (k <= 0 || n <= 0) return j;
    int d = ((j - c) % k + k) % k;
    if (2 * d > k) d -= k;
    return ((c + d) % n + n) % n;
}

static int retitle(const void *unused);

/* The covers round album c of n for this frame, in Light Mode's colours. */
static void fx_ring(int c, int n, const unsigned **ring) {
    if (fx.light != ipod_light()) {
        fx_placeholder();
        memset(fx.album, 0, sizeof fx.album); /* decoded art may have transparent corners too */
        fx.drawn = 0;
    }
    fx_window(c, n, ring);
    if (coverflow_bounded(cf.slide)) /* no wrap: only the real neighbours, never repeats */
        for (int j = -fx_reach(); j <= fx_reach(); ++j)
            if ((unsigned)(c + j) >= (unsigned)n) ring[j + CF_REACH] = 0;
}

#if Q2POD_DEV
/* --dev builds only (docs/internals.md#coverflow-step): each frame of a run in RAM, a store a
 * frame. gaps' 1,032 bytes of scratch are a --dev build's alone, so its scratch_free_bytes reads
 * that much under a release build's (docs/architecture.md#the-payload). They go to
 * /tmp/q2-cf-gaps.log at the first paint after the run, if /tmp/q2-cf-gaps.on
 * exists. */
#define GAP_FRAMES 128
#define GAP_MAX_BYTES 262144
static struct {
    unsigned n, last;
    struct {
        unsigned short gap, decode, render;
        short offset;
    } row[GAP_FRAMES];
} gaps __attribute__((section(".scratch")));
static void gap_note(void *w, unsigned t0, unsigned t1, unsigned t2) {
    unsigned i = gaps.n++ % GAP_FRAMES;
    gaps.row[i].gap = (unsigned short)(t0 - gaps.last);
    gaps.row[i].decode = (unsigned short)(t1 - t0);
    gaps.row[i].render = (unsigned short)(t2 - t1);
    gaps.row[i].offset = (short)I(w, SLIDE_OFFSET);
    gaps.last = t0;
    if (P(w, SLIDE_ANIMATOR) || th.on) return;
    void *on = gaps.n > 1 ? fopen("/tmp/q2-cf-gaps.on", "rb") : 0;
    void *f = on ? fopen("/tmp/q2-cf-gaps.log", "ab") : 0;
    if (on) fclose(on);
    if (f && !fseek(f, 0, 2) && ftell(f) < GAP_MAX_BYTES) {
        char s[64];
        tk_snprintf(s, sizeof s, "== run at %u ms: %u frames\n", t0, gaps.n);
        fputs(s, f);
        for (i = gaps.n > GAP_FRAMES ? gaps.n - GAP_FRAMES : 0; i < gaps.n; ++i) {
            unsigned k = i % GAP_FRAMES;
            tk_snprintf(s, sizeof s, "gap=%u decode=%u render=%u offset=%d\n", gaps.row[k].gap,
                        gaps.row[k].decode, gaps.row[k].render, gaps.row[k].offset);
            fputs(s, f);
        }
    }
    if (f) fclose(f);
    gaps.n = 0;
}
#endif
static int touched(void);
static int album_offline(unsigned c);
static void caption_mark(void *w, void *canvas);

/* ringnav_paint (the border hook, after stock painted the slide_menu's empty children) calls this
 * for every widget: over Coverflow's slide_menu it draws the frame, rendered again only when the
 * position or a texture has changed. */
void coverflow_paint(void *w, void *canvas) {
    coverflow_position_paint(w, canvas);
    caption_mark(w, canvas);
    int c, frac, n;
    if (!w || w != cf.slide || !fx.frame || cf.screen != COVERS ||
        !visual(w, n = slide_count(w), &c, &frac))
        return;
    int lead = (int)(th.lead * CF_ONE), arch = (int)(th.arch * CF_ONE), sh = 0,
        mode = !th.on || (!th.fold && !th.jump) ? FX_SHELF
               : th.fold                        ? FX_FOLD
               : th.enter > 0                   ? FX_ENTRY
               : th.lean < 0                    ? FX_HELD_LEFT
                                                : FX_HELD;
    if (th.on) { /* finer than the offset's whole px */
        int q = cf_round(th.pos);
        frac = (int)((th.pos - (float)q) * CF_ONE);
        c = ((I(w, SLIDE_INDEX) + q) % n + n) % n;
        /* draw_row's: the window round the covers that show; by letters it goes on with the row */
        if (mode != FX_FOLD && (mode || th.lead)) sh = coverflow_window(frac);
    }
    const unsigned *wide[CF_WIDE], **ring = wide + CF_WIDE_REACH - CF_REACH;
#if Q2POD_DEV
    unsigned gap_t0 = (unsigned)time_now_ms();
#endif
    fx_ring(c + sh, n, ring);
#if Q2POD_DEV
    unsigned gap_t1 = (unsigned)time_now_ms();
#endif
    int still = !th.on && !P(w, SLIDE_ANIMATOR) && !touched();
    if (!fx.drawn || c != fx.c || frac != fx.frac || lead != fx.lead || arch != fx.arch ||
        mode != fx.mode || still != fx_still || mode == FX_FOLD ||
        mode == FX_ENTRY) { /* the fold: the clock, the entry: passes */
        unsigned *d = (unsigned *)bitmap_lock_buffer_for_write(fx.frame);
        if (!d) return;
        cf_still = still;
        coverflow_render(d, (int)(bitmap_get_line_length(fx.frame) / 4), frac, ring);
        cf_still = 0;
        bitmap_unlock_buffer(fx.frame);
        fx.c = c, fx.frac = frac, fx.lead = lead, fx.arch = arch, fx.mode = mode, fx.drawn = 1,
        fx_still = still;
    }
#if Q2POD_DEV
    gap_note(w, gap_t0, gap_t1, (unsigned)time_now_ms());
#endif
    if (!th.on && (c + 1 != cf.titled || album_offline((unsigned)c) != cf.offline) &&
        !cf.title_timer)
        cf.title_timer = timer_add(retitle, 0, 0);
    int r[4] = { 0, 0, CF_VIEW_W, cfs.view_h };
    canvas_draw_image(canvas, fx.frame, r, r);
}

/* Touch (docs/internals.md#coverflow-touch). The slide_menu stays insensitive: the covers' view
 * takes the touch and moves the offset itself, so stock's drag and throw never run. A drag follows
 * the finger, CF_DRAG_PX an album; the release glides on at the finger's speed, decelerating,
 * through the wheel's own animator (carousel_glide), to the cover that speed reaches; a tap brings
 * a side cover to the centre or opens the centre one. A touch while the covers move stops them.
 * The press is the covers' view's; the moves and the release are the page's, so a release off the
 * covers still ends the touch, and one never seen ends at the next key (coverflow_lost_touch). */
#define CF_SAMPLES 8

/* EASE_FOLLOW (docs/internals.md#coverflow-step): a step follows its goal, critically damped, so a
 * tick moves the goal and never the speed. Over s time constants from the live offset, d px short
 * of the goal with speed v, what is left is (d + (d - v tau) s) e^-s, and the animator's share is
 * 1 - (1 + a s) e^-s with a = 1 - v tau / d. Run by stock's animator, as a float easing. */
static struct {
    int d, tau;
    float a, span; /* span: the run's length, in time constants */
} ff __attribute__((section(".scratch")));

/* e^-s, s under 256: (e^(-s/256))^256, smooth and falling, about .1% over at s = 7. */
float ff_decay(float s) {
    float x = 1 - s / 256 + s * s / 131072;
    for (int i = 0; i < 8; ++i) x *= x;
    return x;
}
float coverflow_follow(float k) {
    if (k >= 1) return 1;
    float s = k * ff.span;
    return 1 - (1 + ff.a * s) * ff_decay(s);
}
/* A run d px long at time constant tau ms, picking up the speed elapsed ms into the last one (< 0:
 * from rest). Returns its ms: until within half a px of the goal, as stock's update truncates, so
 * the share's jump to 1 is one whole px at most. A goal right ahead of a cover too fast to stop for
 * it (a < 0) gives up the speed it can't use. */
int coverflow_follow_aim(int d, int tau, int elapsed) {
    float a = 1, e = 1, s = 0, size = (float)ABS(d);
    if (elapsed >= 0 && d) {
        s = (float)elapsed / (float)ff.tau;
        float v = (float)ff.d * (1 - ff.a + ff.a * s) * ff_decay(s) / (float)ff.tau;
        a = 1 - v * (float)tau / (float)d;
        if (a < 0) a = 0;
    }
    s = 0;
    do {
        s += .25f;
        e *= .7788008f; /* e^-1/4 */
    } while (size * (1 + a * s) * e > .5f);
    ff.d = d, ff.tau = tau, ff.a = a, ff.span = s;
    return (int)(s * (float)tau);
}
static struct {
    int down, drag, caught, x, y, from, n; /* from: the offset at the press, or the drag's start */
    int sx[CF_SAMPLES];
    unsigned st[CF_SAMPLES];
} tc __attribute__((section(".scratch")));

/* A finger on the covers: they move with it, or are held where it caught them */
static int touched(void) { return tc.down; }

int stride_of(void *s) { return slide_menu_item_width(s) + I(s, SLIDE_SPACER); }
static int round_stride(int off, int stride) {
    return floor_div(2 * off + stride, 2 * stride) * stride;
}

/* The playlists give past an end as iOS's rubber band, x * 0.55 / (1 + x * 0.55 / D) with
 * D 2/5 of a stride, so the centre never reaches past half a cover (visual() would wrap there);
 * not rubber: they stop at the end. */
static int bound(int off, int stride, int rubber) {
    void *s = cf.slide;
    if (!coverflow_bounded(s)) return off;
    int i = I(s, SLIDE_INDEX), hi = i * stride, lo = (i + 1 - slide_count(s)) * stride;
    int edge = off > hi ? hi : off < lo ? lo : off, x = off - edge, a = ABS(x), d = 2 * stride / 5;
    if (!x || !rubber) return edge;
    a = a * 11 * d / (a * 11 + 20 * d);
    return edge + (x < 0 ? -a : a);
}

/* bound's inverse past an end: a touch catching the rubber band (or a wheel bounce) drags on from
 * where it shows, not from a band stretched again. */
static int unbound(int off, int stride) {
    void *s = cf.slide;
    if (!coverflow_bounded(s)) return off;
    int i = I(s, SLIDE_INDEX), hi = i * stride, lo = (i + 1 - slide_count(s)) * stride;
    int edge = off > hi ? hi : off < lo ? lo : off, x = off - edge, a = ABS(x), d = 2 * stride / 5;
    if (!x || a >= d) return off;
    a = 20 * d * a / (11 * (d - a));
    return edge + (x < 0 ? -a : a);
}

/* px/s over the last 100 ms of moves; 0 if the finger rested CF_STILL_MS before lifting. */
static int velocity(unsigned now) {
    int last = (tc.n - 1) % CF_SAMPLES, first = last;
    if (!tc.n || now - tc.st[last] > CF_STILL_MS) return 0;
    for (int k = 1; k < (tc.n < CF_SAMPLES ? tc.n : CF_SAMPLES); ++k) {
        int j = (tc.n - 1 - k) % CF_SAMPLES;
        if (tc.st[last] - tc.st[j] > 100) break;
        first = j;
    }
    int dt = (int)(tc.st[last] - tc.st[first]),
        v = dt ? (tc.sx[last] - tc.sx[first]) * 1000 / dt : 0;
    return v > CF_VMAX ? CF_VMAX : v < -CF_VMAX ? -CF_VMAX : v; /* a burst of queued moves */
}

static void sample(int x, unsigned t) {
    tc.sx[tc.n % CF_SAMPLES] = x, tc.st[tc.n % CF_SAMPLES] = t;
    ++tc.n;
}

/* When the event was posted: moves queued behind a frame are dispatched together, but keep their
 * own times. 0 (unstamped) takes the dispatch time. */
static unsigned stamp(void *event) {
    unsigned t = (unsigned)I(event, EVENT_TIME);
    return t ? t : time_now_ms();
}

/* The covers glide on from the release (v px/s, 0 to settle) to a whole cover: where the speed
 * carries them in CF_PROJECT_MS, never back against a flick, starting at the finger's speed. */
static void release(int v) {
    void *s = cf.slide;
    int stride = stride_of(s), live = I(s, SLIDE_OFFSET);
    if (stride <= 0) return;
    int vo = v * stride / CF_DRAG_PX, goal = round_stride(live + vo * CF_PROJECT_MS / 1000, stride);
    int flick = ABS(v) >= CF_FLICK;
    if (flick && v > 0 && goal < -floor_div(-live, stride) * stride)
        goal = -floor_div(-live, stride) * stride;
    if (flick && v < 0 && goal > floor_div(live, stride) * stride)
        goal = floor_div(live, stride) * stride;
    goal = bound(goal, stride, 0);
    if (goal == live && !live && !P(s, SLIDE_ANIMATOR)) { /* at rest already: the still frame */
        widget_invalidate_force(s, 0);
        return;
    }
    int d = ABS(goal - live), a = ABS(vo), ahead = (goal > live && v > 0) || (goal < live && v < 0);
    unsigned ms = ahead ? (unsigned)(3000 * d / a) : CF_SETTLE_MS; /* cubic out: 3 d / v */
    carousel_glide(s, goal,
                   ms < CF_SETTLE_MS   ? CF_SETTLE_MS
                   : ms > CF_GLIDE_MAX ? CF_GLIDE_MAX
                                       : ms,
                   EASE_CUBIC_OUT);
}

/* A tap at rest: the cover drawn under it comes to the centre, or opens if it is there. */
static void tap(int x, int y) {
    void *s = cf.slide;
    int n = slide_count(s), c, frac, xy[2] = { x, y };
    int stride = visual(s, n, &c, &frac);
    if (!stride) return;
    widget_to_local(s, xy);
    int j = fx.frame ? coverflow_hit(frac, xy[0], xy[1])
            : ABS(xy[0] - CF_CX) < ART_SIZE / 2 && xy[1] >= 0 && xy[1] < ART_SIZE
                ? 0
                : CF_MISS; /* the flat fallback: the centre cover only */
    if (j == CF_MISS || (coverflow_bounded(s) && (unsigned)(c + j) >= (unsigned)n)) return;
    if (!j && !frac)
        pick(c);
    else
        carousel_glide(s, round_stride(I(s, SLIDE_OFFSET), stride) - j * stride, CF_TAP_MS,
                       SLIDE_EASING);
}

int covers_live(void) { return cf.slide && cf.screen == COVERS && !cf.timer; }

/* The touch ends at now: a drag glides on (thrown unless aborted), a stopped glide settles, a
 * still touch is a tap. Off the covers (a screen change mid-touch) they still settle on a whole
 * cover. */
static void lift(int aborted, unsigned now) {
    if (!tc.down) return;
    tc.down = 0;
    if (!cf.slide) return;
    if (tc.drag || tc.caught || !covers_live())
        release(tc.drag && !aborted && covers_live() ? velocity(now) : 0);
    else if (!aborted)
        tap(tc.x, tc.y);
}

static int touch(void *ctx, void *event) {
    int type = I(event, EVENT_TYPE), x = I(event, EVENT_X), y = I(event, EVENT_Y);
    if (type == EVT_POINTER_DOWN) {
        lift(1, 0);                            /* a release the input queue dropped */
        if (!covers_live() || th.on) return 0; /* the thumb's covers settle first */
        void *a = P(cf.slide, SLIDE_ANIMATOR);
        if (a) widget_animator_pause(a); /* caught where it is */
        tc = (typeof(tc)){ .down = 1,
                           .caught = a != 0,
                           .x = x,
                           .y = y,
                           .from = unbound(I(cf.slide, SLIDE_OFFSET), stride_of(cf.slide)) };
        return 0;
    }
    if (!tc.down) return 0;
    if (type == EVT_POINTER_UP_BEFORE || type == EVT_POINTER_ABORT) {
        lift(type == EVT_POINTER_ABORT, stamp(event));
        return 0;
    }
    if (type != EVT_POINTER_MOVE_BEFORE || !covers_live()) return 0;
    int dx = x - tc.x, dy = y - tc.y;
    if (!tc.drag) {
        if (ABS(dx) < CF_SLOP && ABS(dy) < CF_SLOP) return 0;
        if (ABS(dy) > ABS(dx)) { /* up or down: stock's (the Quick Settings pull-down) */
            lift(1, 0);
            return 0;
        }
        tc.drag = 1, tc.x = x, dx = 0; /* from here, so the covers don't jump the slop */
    }
    sample(x, stamp(event));
    int stride = stride_of(cf.slide);
    I(cf.slide, SLIDE_OFFSET) = bound(tc.from + dx * stride / CF_DRAG_PX, stride, 1);
    widget_invalidate_force(cf.slide, 0);
    return 0;
}

/* ringnav, at a key with no finger down: a touch whose release went unseen ends, so the wheel
 * and Centre act on the cover the covers settle on. */
void coverflow_lost_touch(void) { lift(1, 0); }

static void cover(void *img, unsigned i, int near) {
    char url[600] = "file://";
    near = near && art_url(url, i);
    if (tk_strcmp(widget_get_prop_str(img, "image", ""), near ? url : PLACEHOLDER) &&
        (!near || !image_show(img, url, 0)))
        image_base_set_image(img, PLACEHOLDER);
}

/* A playlist's count: "1 song", "N songs"; buf holds 24. */
static const char *songs_text(char *buf, unsigned k) {
    tk_snprintf(buf, 24, "%u song%s", k, k == 1 ? "" : "s");
    return buf;
}

/* Whether album c can't play now (catalogue.c: a server album offline); a card's or a playlist's
 * always can. */
static int album_offline(unsigned c) {
    void *r = c + CARDS < (unsigned)cf.count && cf.scoped != PLAYLISTS ? deque_at(cf.albums, c)
                                                                       : (void *)0;
    return r && catalogue_album_unavailable(r);
}
/* The captions of an album that can't play now are greyed, as a Library row is
 * (docs/server-library.md#grayed-rows), the name narrowed CF_MARK_W a side, centred still, for
 * the offline mark at its right (caption_mark); its own width again once it can. */
#define CF_MARK_W 28
static void caption_offline(int off) {
    int x = CF_EDGE + (off ? CF_MARK_W : 0);
    if (off != cf.offline) widget_invalidate_force(cf.artist, 0);
    cf.offline = off;
    catalogue_grey(cf.name, off), catalogue_grey(cf.artist, off);
    if (I(cf.name, W_X) != x)
        widget_move_resize(cf.name, x, I(cf.name, W_Y), 375 - 2 * x, I(cf.name, W_H));
}
static void caption_mark(void *w, void *canvas) {
    unsigned ink;
    if (w && w == cf.covers && cf.offline && cf.name && catalogue_row_ink(cf.name, &ink))
        catalogue_mark_paint(canvas, I(cf.name, W_X) + I(cf.name, W_W) + CF_MARK_W / 2,
                             I(cf.name, W_Y) + I(cf.name, W_H) / 2, ink);
}

/* A caption's new text, its marquee from the start: a running scroll keeps its own clock, so the
 * xoffset alone came back on its next step and the next album's long name went on from the last
 * one's place; stopped, the next paint starts it again with the hold at its start
 * (ringnav_hscroll_start). Stock draws even a centred caption at -xoffset, so a fitting one is set
 * back to 0 too. */
static void caption_text(void *label, const char *s) {
    int fresh = text_differs(label, s);
    if (fresh) hscroll_label_stop(label);
    widget_set_text_utf8(label, s);
    if (fresh) widget_set_prop_int(label, "xoffset", 0);
}
/* The captions name album c (or a card's), once each time it changes (or whether it can play
 * does); a playlist's are its name and its count. */
void titles(unsigned c) {
    unsigned n = (unsigned)cf.count;
    int off = album_offline(c);
    if ((int)c + 1 == cf.titled && off == cf.offline) return;
    cf.titled = (int)c + 1;
    caption_offline(off);
    void *r = c + CARDS < n ? deque_at(cf.albums, c) : (void *)0;
    const char *name = r ? P(r, REC_ALBUM) : 0, *by = r ? cover_artist(r) : "";
    char songs[24];
    if (r && cf.scoped == PLAYLISTS)
        name = P(r, REC_TITLE), by = songs_text(songs, playlist_songs(I(r, REC_DISC)));
    caption_text(cf.name, r ? name ?: "" : sort_names[cf.sort]);
    if (by) caption_text(cf.artist, by); /* NULL leaves the old one */
}

static int changed(void *ctx, void *event) {
    unsigned n = (unsigned)cf.count, c = (unsigned)I(cf.slide, SLIDE_INDEX);
    for (unsigned i = 0; !fx.frame && i < n; ++i) { /* the flat fallback's own images */
        unsigned d = i > c ? i - c : c - i;
        cover(widget_get_child(cf.slide, i), i, d <= ART_NEAR || n - d <= ART_NEAR);
    }
    void *r = c + CARDS < n ? deque_at(cf.albums, c) : (void *)0;
    if (r && !cf.scoped) cf.saved_album = album_key(r), centred_at(c);
    cf.titled = 0; /* a commit always writes them */
    titles(c);
    return 0;
}

/* The centre cover the frame last drew, named outside the paint. */
static int retitle(const void *unused) {
    cf.title_timer = 0;
    if (cf.slide && cf.screen == COVERS && !th.on) titles((unsigned)fx.c);
    return 0;
}

/* An index per album plus the cards (CARDS), moved by the wheel and by touch on the covers' view
 * (touch); the slide_menu itself takes no touch. With depth it spans the frame, so every step
 * repaints all of it, and its cfs.view_h square items with a negative spacer move one album per
 * the theme's stride in px; its children stay empty under the frame, so CF_POOL of them stand for
 * every album (slide_count, slide_child_index): an open makes no widget per album, which cost
 * 6,000 albums 25 ms and their layout as much again. The flat fallback is the stock images, 160 px,
 * as before, one child per album. ponytail: the flat fallback (no memory for the frame) still makes
 * a child per album; a pool there would rebind the images round the visual centre mid-glide. */
static void cw_shown(const char *what);
static void cw_check(void);
static void covers(void) {
    cf.screen = COVERS;
    widget_set_visible(cf.body, 0, 0);
    if (!cf.covers) {
        coverflow_position_reset();
        void *f = widget_factory();
        int depth = fx_open();
        if (!depth) cw_refused("the covers' frame, drawn flat");
        fx_fresh = depth;
        cf.covers =
            widget_factory_create_widget(f, "view", cf.page, 0, bar_foot(cf.page), 375, client_h());
        cf.slide = widget_factory_create_widget(f, "slide_menu", cf.covers, 0, depth ? 0 : 24, 375,
                                                depth ? cfs.view_h : ART_SIZE);
        if (depth) slide_menu_set_spacer(cf.slide, cfs.stride - cfs.view_h);
        widget_set_sensitive(cf.slide, 0);
        cf.count = (int)deque_size(cf.albums) + CARDS;
        for (int i = 0; i < (depth && cf.count > CF_POOL ? CF_POOL : cf.count); ++i) {
            void *img = image_create(cf.slide, 0, 0, 0, 0);
            image_set_draw_type(img, 4); /* scale_auto, as the stock cover rows */
            if (!depth) image_base_set_image(img, PLACEHOLDER);
            widget_set_prop_int(img, "clickable", 1);
            widget_on(img, EVT_CLICK, pick_child, (void *)(long)i);
        }
        /* Album over artist under the frame, white and larger, then Text Secondary (the Artist
         * role, docs/oled-design.md#color), clear of the rounded glass (docs/ipod.md#coverflow).
         * Not stock's #AAAAAA: ringnav_style_color paints that as the quieter Text Tertiary. */
        cf.name = text(cf.covers, CF_EDGE, cfs.text_y, 375 - 2 * CF_EDGE, CF_NAME_H);
        widget_set_prop_int(cf.name, "style:normal:font_size", cfs.name_px);
        cf.artist =
            text(cf.covers, CF_EDGE, cfs.text_y + cfs.artist_y, 375 - 2 * CF_EDGE, CF_ARTIST_H);
        widget_set_prop_int(cf.artist, "style:normal:font_size", cfs.artist_px);
        widget_set_prop_int(cf.artist, "style:normal:text_color",
                            (int)(0xff000000u | tok(TOK_SECONDARY)));
        slide_menu_set_value(cf.slide, cf.album);
        widget_on(cf.slide, EVT_VALUE_CHANGED, changed, 0);
        tc.down = th.on = 0; /* a touch or thumb on the covers these replace */
        changed(0, 0);
        widget_on(cf.covers, EVT_POINTER_DOWN, touch, 0); /* the rest: open_page */
    }
    widget_set_visible(cf.covers, 1, 0);
    widget_set_sensitive(cf.covers, 1);
    widget_invalidate_force(cf.page, 0);
    cw_shown("covers");
}

static int playlists_again(const void *unused);
static unsigned pl_again __attribute__((section(".scratch"))); /* playlists_again's own */
/* Album tracks are one-line song rows (song_rows.c); with the pages' titles in the bar, to_tracks
 * lists Cover Flow's tracks that way. */
static int song_tracks(void) { return !theme()->page_title_h; }

/* The bar's title on Cover Flow's page (navigation.c bar_title_of): 0 on song_tracks' tracks, whose
 * hidden caption names the album; none over the covers, so the status row shows the clock as
 * Home's does; else, and over no albums (cf_empty.c's empty state), "Cover Flow". */
const char *coverflow_bar_title(void) {
    if (cf.screen == TRACKS && song_tracks()) return (const char *)0;
    if (cf.screen == COVERS && !empty_shown(cf.body)) return ""; /* no albums: the page's name */
    return "Cover Flow";
}

/* The bar looks for its title again: the page's stays the bar's source until it goes. */
static void retitle_bar(void) {
    title_gone(0, 0);
    bar_title_sync(cf.page);
}

static int to_covers(const void *unused) {
    cf.timer = 0;
    stop();
    covers();
    retitle_bar();
    if (cf.scoped == PLAYLISTS) rearm(&pl_again, playlists_again, 0); /* edits from the panel */
    return 0;
}

static int cancel_row(void *ctx, void *event) {
    rearm(&cf.timer, to_covers, 0);
    return 0;
}

static int poll(const void *unused) {
    cf.timer = 0;
    if (cf.done == cf.total) { /* stop() joins the thread's last steps */
        if (!cf.scoped) art_cleaned();
        return to_covers(0);
    }
    cw_check();
    char progress[64];
    tk_snprintf(progress, sizeof(progress), "Preparing artwork\xe2\x80\xa6 %d/%d", cf.done,
                cf.total);
    page_retitle(cf.page, cf.title, progress);
    cf.timer = timer_add(poll, 0, 250);
    return 0;
}

/* A cold open's progress (docs/internals.md#watchdog): a line per phase, synced to /mnt/data and
 * removed when load() ends, so one left at boot names the phase a stalled open reached. Only an
 * open that queries the albums or finds albums without art writes it (cf_cold); a warm one, the
 * usual, writes nothing. A few lines per open: flash, never per album. */
#define CF_TRACE PEQ_ROOT "/mnt/data/q2-cf-trace"
static int cf_traced; /* this open is a cold one */
static void cf_trace(unsigned t0, const char *what, unsigned a, unsigned b) {
    if (!cf_traced) return;
    char line[96];
    int n = b ? snprintf(line, sizeof line, "%s%u/%u %u ms\n", what, a, b, time_now_ms() - t0)
              : snprintf(line, sizeof line, "%s%u %u ms\n", what, a, time_now_ms() - t0);
    void *f = fopen(CF_TRACE, "a");
    if (!f) return;
    fwrite(line, 1, n, f);
    fflush(f);
    fsync(fileno(f));
    fclose(f);
}
/* From here this open traces, from a fresh file's "start". */
static void cf_cold(unsigned t0) {
    if (cf_traced) return;
    cf_traced = 1;
    unlink(CF_TRACE);
    cf_trace(t0, "start albums=", cf.stock ? deque_size(cf.stock) : 0, 0);
}

/* On open: the albums, then art for the ones with no cache file (PictureFlow's first-launch build;
 * later opens resume). The albums are queried again only after the library changed (or Refresh
 * Coverflow): stock's sort converts both names to pinyin on every comparison. */
static void load_albums(unsigned t0);
static void load(void);
/* The library's empty page waits for it: a scan, an import of a server's albums (each moves
 * library_gen) loads the covers again. The timer is the page's (empty_page),
 * so it goes with it; one at a time. */
static unsigned empty_gen __attribute__((section(".scratch")));
static void *empty_page __attribute__((section(".scratch")));
static int empty_poll(const void *info) {
    if (cf.page != empty_page) return 7; /* RET_REMOVE: another page's */
    if (library_scanning() || empty_gen == library_gen) return 8; /* RET_REPEAT */
    empty_page = 0;
    load(); /* may build the empty page again, which arms another */
    return 7;
}
static void empty_wait(void) {
    empty_gen = library_gen;
    if (empty_page != cf.page) empty_page = cf.page, widget_add_timer(cf.page, empty_poll, 1000);
}
static void load(void) {
    unsigned t0 = time_now_ms();
    cf_traced = 0;
    load_albums(t0);
    if (cf_traced) unlink(CF_TRACE);
}
/* The albums' query in steps, so that an open waiting on it shows it is busy between them
 * (open_step): 0 getAllAlbum, 1 MUSIC_ALBUM_SQL's grouping, 2 split and music_albums on its rows;
 * each returns 1 once cf.stock is the list. One grouping serves both: a name's ALBUM_SQL rows are
 * its music rows and more (the WHERE drops songs, not groups), and music_albums drops the more; a
 * name with no music keeps its stock row, which music_albums drops too. library_gen is taken before
 * the first step, so a scan meanwhile has the next open query again. */
static struct {
    void *stock, *rows;
    int n;
    unsigned gen;
} cq __attribute__((section(".scratch")));
static void query_drop(void) {
    if (cq.stock) deque_destroy(cq.stock);
    if (cq.rows) deque_destroy(cq.rows);
    cq.stock = cq.rows = 0;
}
static int query_step(int k, unsigned t0) {
    if (k == 0) {
        cf_cold(t0);
        cq.gen = library_gen;
        cq.stock = cf_staged(albums, 0, &cq.n);
        cf_trace(t0, "staged albums=", cq.n, 0);
        return 0;
    }
    if (k == 1 && cq.n > 0) {
        cq.rows = cf_staged(rank_query, MUSIC_ALBUM_SQL, &cq.n);
        return 0;
    }
    if (cq.rows) {
        cq.stock = music_of(split_by(cq.stock, cq.rows), cq.rows);
        cq.rows = 0;
        cf_trace(t0, "music_split albums=", deque_size(cq.stock), 0);
    }
    cf.stock = cq.stock, cq.stock = 0;
    cf.albums_gen = cq.gen;
    drop_unknown();
    cf_trace(t0, "drop_unknown albums=", deque_size(cf.stock), 0);
    return 1;
}
/* The kept order goes once what made it changed: Display > Cover Flow's Sort (its order read
 * again), Year order, or Artists for Artist's names. The library's covers alone; a scoped page's
 * order is its own. */
static void sort_check(void) {
    if (cf.scoped) return;
    verify_join(); /* before the albums it reads go */
    if (cf.albums &&
        (cf.resort || ((cf.sort == SORT_YEAR || cf.sort == SORT_ARTIST) &&
                       (cf.year_old != ringnav_year_old() ||
                        (cf.sort == SORT_ARTIST && cf.artists != I(artist_type, 0)))))) {
        if (cf.albums != cf.stock) deque_destroy(cf.albums);
        cf.albums = 0;
    }
    if (cf.resort) cf.resort = cf.sort_read = 0;
}
/* Whether an open has the kept albums to put in the Sort's order (sort_albums) before its page. */
static int sort_due(void) { return cf.stock && deque_size(cf.stock) && !cf.albums; }
/* cf.stock in the Sort's order, as cf.albums. Its ranking queries the library (Artist, Year and
 * Recently Added; Most Played reads every song's count), seconds on a large one, so an open runs it
 * on a thread of its own as it does the albums' query (open_step). */
static void sort_albums(unsigned t0) {
    if (!cf.sort_read) BLOB_IO(SORT_FILE, cf.sort, 0), cf.sort_read = 1;
    cf.sort = sort_next(0);
    cf.albums = sorted();
    cf_trace(t0, "sorted albums=", deque_size(cf.albums), 0);
}
void *queue_now(unsigned *pos, unsigned *n);
static void art_build(void);
static void load_albums(unsigned t0) {
    int on_sort = cf.on_sort; /* taken whatever this load shows */
    cf.on_sort = 0;
    sort_check();
    drop();
    coverflow_position_reset();
    widget_destroy_children(cf.page);
    cf.covers = cf.slide = cf.name = cf.artist = 0; /* destroyed with the page's children */
    cf.offline = 0;
    if (cf.scoped != PLAYLISTS) cf.album = 0; /* the playlists' read again keep their place */
    cf.body = widget_factory_create_widget(widget_factory(), "view", cf.page, 0, bar_foot(cf.page),
                                           375, client_h());
    int n = 0;
    unsigned gen = library_gen;
    if (cf.albums_gen != gen && !cf.scoped) drop_albums();
    if (cf.scoped)
        n = (int)deque_size(cf.scoped == RATED_ALBUMS ? cf.stock : cf.albums);
    else if (cf.stock)
        n = (int)deque_size(cf.stock);
    else if (!library_scanning()) {
        for (int k = 0; !query_step(k, t0); ++k) {}
        n = (int)deque_size(cf.stock);
    }
    if (n > 0 && !cf.albums) sort_albums(t0); /* an open has sorted already (open_step) */
    if (n <= 0) {
        cw_shown("no albums");
        drop_albums();      /* only a real list is kept */
        cf.screen = COVERS; /* Return goes Home */
        if (cf.scoped || !cf_empty(cf.page, cf.body)) /* the house's empty page (cf_empty.c) */
            list("Update Local Music first", 0);
        else
            empty_wait();
        return;
    }
    unsigned count = deque_size(cf.albums);
    if (cf.scoped == PLAYLISTS) {
        covers(); /* playlist_art.c builds a playlist's art */
        return;
    }
    char path[512];
    if (!cf.saved_album) BLOB_IO(LAST_ALBUM, cf.saved_album, 0); /* outlives a reboot */
    /* The covers open on the queue's song's album, playing or not, when the list has it (#350),
     * else on the album they last centred. ponytail: a song with no album tag (a folder play's
     * records carry none) is never looked for, so those open on the last centre. */
    unsigned at, queued;
    void *now = P(mcl_pdeqplaylist, 0) && !on_sort && !cf.scoped ? queue_now(&at, &queued) : 0;
    if (now && !(P(now, REC_ALBUM) && *(const char *)P(now, REC_ALBUM))) now = 0;
    if (!cf.scoped && art_clean()) { /* warm: the covers at once, the art checked behind them */
        cf.album = on_sort ? (int)count : centre_of(now, count);
        cf_trace(t0, "warm albums=", count, 0);
        to_covers(0);
        verify_start();
        return;
    }
    if (aj.jobs && aj.albums == cf.albums && aj.count == count && aj.gen == library_gen &&
        !cf.scoped) { /* the open's thread looked already (OPEN_ART) */
        cf.album = on_sort ? (int)count : centre_of(now, count);
        cf.jobs = aj.jobs, cf.total = aj.total; /* the build's now: stop() frees them */
        aj.jobs = 0, aj.total = 0;
        art_found_drop();
        if (cf.total)
            cf_cold(t0);
        else
            art_cleaned(); /* every album's art is there */
        cf_trace(t0, "found jobs=", cf.total, count);
        art_build();
        return;
    }
    art_found_drop();
    int playing = 0;
    art_strike(BUILDING);
    if (!(cf.jobs = calloc(count, sizeof(job_t)))) cw_refused("the art build's jobs");
    unsigned char *shared = folders_shared(cf.albums, count);
    for (unsigned i = 0; i < count; ++i) {
        if (i && !(i % 1000)) cf_trace(t0, "jobs ", i, count);
        void *r = deque_at(cf.albums, i);
        unsigned key = album_key(r);
        if (now && !album_cmp(r, now))
            cf.album = (int)i, playing = 1, now = 0;
        else if (key == cf.saved_album && !on_sort && !cf.scoped && !playing)
            cf.album = (int)i;
        job_t *j = cf.jobs ? &cf.jobs[cf.total] : 0;
        if (j && art_missing(r, path, sizeof path) && (j->track = strdup(P(r, REC_PATH)))) {
            cf_cold(t0);
            j->key = key;
            j->shared = shared ? shared[i] : 1; /* no memory: no cover.jpg that may be another's */
            ++cf.total;
        }
    }
    free(shared);
    if (!cf.total && cf.jobs && !cf.scoped) art_cleaned(); /* every album's art is there */
    cf_trace(t0, "done jobs=", cf.total, count);
    if (on_sort) cf.album = (int)count; /* the Sort card, pressed again and again */
    art_build();
}
/* The jobs found (cf.jobs) built on the worker with Preparing artwork, or the covers when none. */
static void art_build(void) {
    mkdir(ART_DIR, 0755);
    cf.done = cf.cancel = 0;
    if (cf.total && card_space(ART_DIR) && !pthread_create(&cf.thread, 0, worker, 0)) {
        cf.running = 1;
        cf.screen = PREPARING;
        page_row_detail(list("", 1), 0, "Cancel", 0, cancel_row);
        poll(0);
    } else
        to_covers(0);
}

/* Album queues use stock's album class (0xff10), never folder class 1: Folder Skip only acts on
 * class 1 and would load the next directory's untagged files. Stock resume rebuilds 0xff10 with
 * getMusicByAlbum from the resume record's album, which player_load_songlist fills for stock lists.
 */
void album_memory(void *r) {
    const char *album = P(r, REC_ALBUM);
    unsigned char *m = (unsigned char *)g_memory_info;
    m[0x60d] = !album || !*album; /* Unknown album */
    tk_snprintf((char *)m + 0x611, 0x100, "%s", album ? album : "");
}

/* Start a queue at idx with its playback class; playing_page copies dq synchronously. Stock's
 * navigator_to_with_context opens a new playing_page even over one, and then both pages' timers
 * run stock's update (0x53cf08), which writes the title and artist only while g_autochange_flag or
 * g_forcerefresh_flag is set and clears them: the page under may take the flag, and the one on top
 * keeps the last song's title through every Next (the device, 2026-11-17: a card app's first song
 * started over the pressed server song's Now Playing, addon.c stream_start). Now Playing on top is
 * closed first, so there is one, and Return leaves for the page under it. */
void play_folder(void *dq, int idx, int cls) {
    /* ringnav_nav_to's question, asked before Now Playing on top closes or an album's name goes
     * into the resume record (docs/internals.md#no-playback-without-an-output) */
    if (cls != 0xff && check_forbid_play_no_headset(0)) return;
    if (cls == 0xff10) album_memory(deque_at(dq, (unsigned)idx));
    struct {
        void *dq;
        int idx, cls, mode;
    } context = { dq, idx, cls, 2 };
    void *top = window_manager_get_top_window(window_manager());
    if (top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "playing_page"))
        window_manager_close_window_force(window_manager(), top);
    navigator_to_with_context("playing_page", &context);
}

static unsigned cover_load __attribute__((section(".scratch"))); /* playback_loads at its play */
static int play(void *ctx, void *event) {
    int i = (int)(long)ctx;
    void *t = deque_at(cf.tracks, (unsigned)i);
    /* a server row has no file: catalogue_load plays it, or says why it can't */
    if (!t || catalogue_missing(P(t, REC_PATH))) {
        page_retitle(cf.page, cf.title, "Storage unavailable");
        return 0;
    }
    /* a playlist's songs play as a song list: 0xff10 would resume the first song's album, and its
     * own list's 0xff13 needs stock's playlist set (docs/internals.md, Cover Flow scope) */
    play_folder(cf.tracks, i, cf.scoped == PLAYLISTS ? 0xf001 : 0xff10);
    if (cf.scoped == PLAYLISTS) cover_load = playback_loads; /* its load, play_folder's */
    return 0;
}
/* The queue is a playlist's songs played from its cover, no queue loaded since: Now Playing's More
 * drops Delete (onlinematch.c), whose 0xf001 delete takes the song out of the library, not the
 * playlist. ponytail: RAM only, so after a restart the resumed queue's Delete shows and removes
 * from the library, as any song list's; a flag in the queue's snapshot would keep the mark. */
int coverflow_playlist_queue(void) { return cover_load && cover_load == playback_loads; }

/* Album order: disc, then track, then path, so an untagged album keeps its file-name order and a
 * CUE image's tracks (one path) their start times. */
int album_before(const void *pa, const void *pb) {
    void *a = *(void *const *)pa, *b = *(void *const *)pb;
    int d = I(a, REC_DISC) - I(b, REC_DISC);
    if (!d) d = I(a, REC_TRACK) - I(b, REC_TRACK);
    if (!d) d = strcmp(P(a, REC_PATH), P(b, REC_PATH));
    return d ? d : I(a, REC_CUE_START) - I(b, REC_CUE_START);
}

/* album's tracks (getMusicByAlbum finds every album of its name) in album order, in a new deque;
 * all of them in stock's name order when out of memory. */
static void *in_order(void *tracks, void *album) {
    unsigned n = 0;
    void **v = calloc(deque_size(tracks) + 1, sizeof *v);
    if (!v) return tracks;
    for (unsigned i = 0; i < deque_size(tracks); ++i)
        if (I(album, REC_ID) == -1 || !album_cmp(deque_at(tracks, i), album))
            v[n++] = deque_at(tracks, i);
    qsort(v, n, sizeof *v, album_before);
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned i = 0; i < n; ++i) _deque_push_back(out, v[i]);
    free(v);
    deque_destroy(tracks);
    return out;
}

/* A track's tagged title, else its file name without the extension; CUE names stay whole.
 * Shared with library_lists.c's Most Played and navigation.c's scrobbler. */
const char *track_name(char *buf, unsigned size, void *t) {
    const char *title = P(t, REC_TITLE);
    if (title && *title) return title;
    const char *name = P(t, REC_NAME), *path = P(t, REC_PATH), *ext = 0;
    if (!name || !path) return name;
    for (; *path; ++path)
        if (*path == '.')
            ext = path;
        else if (*path == '/')
            ext = 0;
    if (!ext) return name;
    unsigned n = strlen(name), e = strlen(ext);
    if (n <= e || strcmp(name + n - e, ext)) return name;
    snprintf(buf, size, "%.*s", (int)(n - e), name);
    return buf;
}

extern void *staged_music(int (*query)(void *), void *arg, int *count);
void *coverflow_album_tracks(void *r) {
    int n;
    return in_order(staged_music(albums, r, &n), r);
}

static void *page_under(void);
static int to_tracks(const void *unused) {
    cf.timer = 0;
    int n;
    char name[512];
    void *r = deque_at(cf.albums, (unsigned)cf.album), *under;
    if (cf.tracks) deque_destroy(cf.tracks), cf.tracks = 0;
    if (cf.scoped == PLAYLISTS) {
        /* Centre opens the playlist's own list over the covers, as its row on the Playlists page
         * under them does: Return comes back to them, as from an album's */
        if ((under = page_under())) playlist_open(under, I(r, REC_DISC));
        return 0;
    }
    if (!cf.scoped) {
        cf.saved_album = album_key(r);
        BLOB_IO(LAST_ALBUM, cf.saved_album, 1); /* a power-off on the tracks keeps it too */
    }
    cf.tracks = coverflow_album_tracks(r);
    n = (int)deque_size(cf.tracks);
    cf.screen = TRACKS;
    if (cf.covers) { /* none in the text list; stock's hit test skips insensitive, not hidden,
                      * widgets, and the covers' view over the list would take its taps and swipes
                      */
        widget_set_visible(cf.covers, 0, 0);
        widget_set_sensitive(cf.covers, 0);
    }
    /* one row height a list, as stock's layouter forces item_height on every row: 64 px with a
     * length's line under each title once any track has one, an unknown's left empty */
    int timed = 0;
    for (int i = 0; i < n && !timed; ++i)
        timed = duration_seconds(deque_at(cf.tracks, (unsigned)i)) > 0;
    const char *title = P(r, cf.scoped == PLAYLISTS ? REC_TITLE : REC_ALBUM);
    if (song_tracks()) {
        /* one-line song rows, as an album opened from Albums: song_rows.c draws each track's
         * number and length (song_list knows these by coverflow_tracks), and the album names the
         * page in the bar (coverflow_bar_title), never a row of its own above the tracks,
         * whatever its length */
        void *view = page_list(cf.page, cf.body, 0, title, n, 48);
        cf.title = widget_get_child(cf.body, 0); /* page_title's label, for page_retitle */
        if (widget_get_visible(cf.title)) {      /* wider than the bar's box: hidden all the same */
            void *lv = P(view, W_PARENT);
            int all = n * page_h(48), page = page_room(cf.body), h = all < page ? all : page;
            widget_set_visible(cf.title, 0, 0);
            widget_move_resize(lv, 0, 0, I(lv, W_W), h);
            widget_move_resize(view, 0, 0, I(view, W_W), h);
        }
        for (int i = 0; i < n; ++i) {
            void *row = page_row_detail(
                view, i, track_name(name, sizeof name, deque_at(cf.tracks, (unsigned)i)), 0, play);
            /* as stock's rows: a long title rests on its ellipsis, scrolling only while selected
             * (navigation.c's marquee), where song_row's narrower box would make text()'s loop
             * scroll it on every row */
            void *label = widget_get_child(row, 0);
            widget_set_prop_int(label, "loop", 0);
            hscroll_label_set_only_focus(label, 1);
            hscroll_label_set_ellipses(label, 1);
        }
        retitle_bar();
        page_push(view, 0);
        return 0;
    }
    void *view = timed ? page_list(cf.page, cf.body, &cf.title, title, n, 64) : list(title, n);
    /* the album over its tracks, all centred, but under a theme's own rows */
    int centred = theme()->page_title_h;
    if (centred) widget_set_prop_int(cf.title, "style:normal:font_size", cfs.name_px);
    for (int i = 0; i < n; ++i) {
        char duration[24];
        void *song = deque_at(cf.tracks, (unsigned)i);
        duration_text(duration, sizeof duration, song);
        void *row = page_row_detail(view, i, track_name(name, sizeof name, song),
                                    timed ? duration : 0, play);
        if (centred) widget_use_style(widget_get_child(row, 0), "s_scrlabel_white20c");
        if (centred && timed)
            widget_set_prop_str(widget_get_child(row, 1), "style:normal:text_align_h", "center");
    }
    page_push(view, 0);
    return 0;
}

/* Display settings' Refresh Coverflow (navigation.c): the art cache emptied and the albums queried
 * again, both at Coverflow's next open (a page open under Settings keeps what it shows). Stock's
 * Library covers (.sldp, made once per album name, which only a settings reset clears) go too,
 * and AWTK's cached bitmaps with them, as the cover thread writes them again at the same names as
 * rows show. The library generation's step is a songtable write's, which Most Played reads
 * again too. */
void coverflow_refresh_art(void) {
    static const char *const dirs[] = { ART_DIR, PEQ_ROOT "/mnt/mmc/.sldp" };
    /* Playlists open under Settings: its build stopped first, or a 2x2 whose cover goes here would
     * fail and mark the playlist as having none */
    playlist_art_stop();
    for (unsigned i = 0; i < 2; ++i) {
        void *dir = opendir(dirs[i]);
        for (struct dirent *e; dir && (e = readdir(dir));) {
            char path[600];
            snprintf(path, sizeof(path), "%s/%s", dirs[i], e->d_name);
            unlink(path); /* "." and ".." fail harmlessly */
        }
        if (dir) closedir(dir);
    }
    image_manager_unload_all(image_manager());
    ++library_gen;
}

/* The library's order as SORT_FILE holds it, Album when it holds none of all_sorts. */
static int sort_saved(void) {
    int sort = SORT_ALBUM;
    BLOB_IO(SORT_FILE, sort, 0);
    for (unsigned i = 0; i < sizeof all_sorts; ++i)
        if (all_sorts[i] == sort) return sort;
    return SORT_ALBUM;
}

/* Display > Cover Flow's Sort: its label ("Sort: Album"), and its picker's choices, all_sorts in
 * order, each its name past "Sort: "; a pick is saved, and Coverflow takes it at its next open
 * (sort_check). */
const char *coverflow_sort_name(void) { return sort_names[sort_saved()]; }
int coverflow_sort_count(void) { return (int)sizeof all_sorts; }
const char *coverflow_sort_at(int i) { return sort_names[all_sorts[i]] + 6; }
int coverflow_sort_now(void) {
    int sort = sort_saved(), i = 0;
    while (all_sorts[i] != sort) ++i;
    return i;
}
void coverflow_sort_set(int i) {
    int sort = all_sorts[i];
    BLOB_IO(SORT_FILE, sort, 1);
    cf.resort = 1;
}

/* The rated albums' Sort card: the next order, saved, and the covers again in it, still on the Sort
 * card. The stock list is kept, so nothing is queried but the order's own ranking. */
static int resort(const void *unused) {
    cf.timer = 0;
    cf.sort = sort_next(1);
    verify_join(); /* before the albums it reads go */
    if (cf.scoped)
        BLOB_IO(RATED_SORT_FILE, cf.sort, 1);
    else
        BLOB_IO(SORT_FILE, cf.sort, 1);
    if (cf.albums != cf.stock) deque_destroy(cf.albums);
    cf.albums = 0;
    cf.on_sort = 1;
    load();
    return 0;
}

/* Album i, or the card past the albums, opened. Only scheduled: a screen change never destroys
 * the widget whose click is running. */
static void pick(int i) {
    if (i == (int)deque_size(cf.albums))
        rearm(&cf.timer, resort, 0);
    else {
        cf.album = i;
        rearm(&cf.timer, to_tracks, 0);
    }
}
/* A click on the slide_menu's child ctx (Centre's, confirm_center): the album it stands for. */
static int pick_child(void *ctx, void *event) {
    if (cf.slide) pick(slide_child_index(cf.slide, (int)(long)ctx));
    return 0;
}

/* The page right under the playlists' covers, Playlists, which they close with them (keyup,
 * coverflow_playlists), or 0. */
static void *page_under(void) {
    void *wm = window_manager(), *under = 0;
    for (unsigned i = 1, n = cf.scoped == PLAYLISTS ? widget_count_children(wm) : 0; i < n; ++i)
        if (widget_get_child(wm, i) == cf.page) under = widget_get_child(wm, i - 1);
    if (under && tk_strcmp(widget_get_prop_str(under, "name", ""), "playlist_page")) under = 0;
    return under;
}

/* Return: tracks -> covers (the slide_menu kept its album), preparing -> cancel and covers,
 * covers or the message -> Home, or back out of a scoped page (one album's tracks too). */
static int keyup(void *ctx, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    if ((cf.screen == TRACKS && cf.scoped != ALBUM_TRACKS) || cf.screen == PREPARING)
        rearm(&cf.timer, to_covers, 0);
    else if (cf.scoped) {
        /* The playlists' covers: the unseen Playlists page under them closes first, unanimated
         * (its own Return is navigator_back too; its cleanup runs on EVT_WINDOW_CLOSE), so Return
         * lands on Library. */
        void *under = page_under();
        if (under) window_manager_close_window_force(window_manager(), under);
        navigator_back();
    } else
        navigator_back_to_home();
    return 11; /* RET_STOP */
}

/* A playlist's art made, or its songs read again: its covers decode again and the captions name
 * its count again. The rows under them, hidden, ask again as the page comes back to the front. */
static void playlist_ready(int cd_num) {
    if (!cf.slide || cf.scoped != PLAYLISTS) return;
    cf.titled = 0; /* the next frame, or changed below, names the centre's count again */
    for (int i = 0; i < fx_slots(); ++i) {
        void *r = fx.album[i] ? deque_at(cf.albums, (unsigned)fx.album[i] - 1) : 0;
        if (r && I(r, REC_DISC) == cd_num) fx.art[i] = 0, fx.drawn = 0;
    }
    if (!fx.frame) changed(0, 0); /* the flat covers' images, by name */
    widget_invalidate_force(cf.page, 0);
}

static void cw_closed(void);
static int closed(void *ctx, void *event) {
    if (ctx != cf.page) return 0; /* a page scoped_open closed already */
    cw_closed();
    coverflow_position_reset();
    stop_timer(&cf.title_timer);
    stop_timer(&pl_again);
    tc.down = 0;
    th.on = 0; /* the poll kept the album named (the page may be part-destroyed here) */
    if (cf.saved_album) BLOB_IO(LAST_ALBUM, cf.saved_album, 1);
    drop();
    cf.page = cf.body = cf.covers = cf.slide = 0;
    if (cf.scoped == RATED_ALBUMS) {
        if (cf.albums != cf.stock) deque_destroy(cf.albums);
        deque_destroy(cf.stock);
        cf.stock = cf.all_stock;
        cf.sort = cf.all_sort;
    } else if (cf.scoped)
        deque_destroy(cf.albums);
    if (cf.scoped) cf.albums = cf.all_albums;
    if (cf.scoped == PLAYLISTS && playlist_art_ready == playlist_ready) playlist_art_ready = 0;
    cf.scoped = 0;
    return 0;
}

/* A Coverflow page: the covers' moves and release are taken here (touch). */
static void *open_page(void) {
    cf_restyle();
    void *page = page_open("coverflow_page", closed, keyup);
    static const unsigned short kinds[] = { EVT_POINTER_MOVE_BEFORE, EVT_POINTER_UP_BEFORE,
                                            EVT_POINTER_ABORT };
    for (unsigned k = 0; page && k < sizeof kinds / sizeof *kinds; ++k)
        widget_on(page, kinds[k], touch, 0);
    return page;
}

/* An open that has to query the albums first (query_step), or put kept ones in a new Sort's order
 * (sort_albums: its ranking is another query of the library), runs that with Home up and live
 * (docs/internals.md#coverflow): the query's steps, then the Sort's (OPEN_SORT) when the list has no
 * order yet, then the look for art (OPEN_ART), each on a thread of its own (open_start). The UI thread
 * waits for them for the open's first OPEN_SPIN_MS / 2, so a warm open, or a cold one of a small
 * library, opens as it is pressed and never shows a spinner; past that it goes back to the main loop
 * and a timer (open_step, every STEP_TICK_MS) collects each step as it ends, Home's Cover Flow row
 * showing the spinner for its chevron (coverflow_spinner) meanwhile. Home stays live: the wheel moves
 * its rows, Return gives the open up, and any other key or touch, another window on top or a library
 * query of the payload's own on the UI thread (staged_into) gives it up too, waiting for the step to
 * stop first (open_stop), so nothing on the UI thread meets a step's use of stock's staging pointer or
 * shared connection. A step stops within a few ms: every connection stock_db_open opens has
 * coverflow_query_stop as its progress handler, which ends the step's statement once cancelled, and
 * art_find looks at its cancel between albums. A step given up leaves nothing: its rows (cq) go, and
 * a Sort cut short is dropped (open_work). ponytail: a step's own sorting and splitting (stock's name
 * sort inside getAllAlbum, music_of, the Sort's ranking sort) runs to its end once begun, so a cancel
 * can wait for that, a few hundred ms on a large library; a stock library call on the UI thread outside
 * the payload's paths (none from Home is known) would not wait. */
#define OPEN_SPIN_MS 150
#define OPEN_SORT 3 /* the step after query_step's last: sort_albums */
#define OPEN_ART 4  /* then the look for the albums' art (art_find), unless it is known */
#define OPEN_DONE 5
#define STEP_TICK_MS 16 /* the timer's look at a running step: MO_FRAME_MS */
#define STEP_POLL_MS 2  /* the UI thread's look while it still waits, within OPEN_SPIN_MS / 2 */
#define QUERY_STOP_OPS 1000 /* SQLite VM steps between the progress handler's looks */
static struct {
    unsigned at, timer;
    void *home; /* the window it opens over: gone, the open is given up */
    int waiting, step, spin, running;
    unsigned long thread;       /* the running step's */
    volatile int done, ret;     /* the step's thread: finished, and open_work's answer */
    volatile int cancel;        /* the step is given up: its statement and art_find stop */
} op __attribute__((section(".scratch")));
/* stock_db_open's progress handler (playlists.c), on every connection it opens: nonzero stops the
 * statement running, the running step's once its open is given up, nothing else's. */
int coverflow_query_stop(void *unused) {
    return op.cancel && op.running && pthread_self() == op.thread;
}
void coverflow_query_watch(void *db) { sqlite3_progress_handler(db, QUERY_STOP_OPS, coverflow_query_stop, 0); }
/* The running step stopped and joined; the caller drops what it leaves. */
static void open_stop(void) {
    if (!op.running) return;
    op.cancel = 1;
    pthread_join(op.thread, 0);
    op.running = 0;
}
static void cw_given_up(void);
static void open_end(void) {
    if (op.step != OPEN_DONE) cw_given_up(); /* before its step's state goes */
    open_stop();
    if (op.timer) timer_remove(op.timer);
    query_drop();
    if (cf_traced) unlink(CF_TRACE), cf_traced = 0;
    if (op.spin) widget_invalidate_force(op.home, 0); /* the row's chevron again */
    op.waiting = op.timer = op.spin = op.cancel = 0;
}
/* Step k: query_step's, OPEN_SORT's sort_albums or OPEN_ART's art_find; 1 once it is the last. A
 * Sort cut short has ranked only some albums: dropped, so the next open sorts again. */
static int open_work(int k) {
    if (k == OPEN_ART) return art_find(), 1;
    if (k != OPEN_SORT) return query_step(k, op.at);
    sort_albums(op.at);
    if (op.cancel && cf.albums) {
        if (cf.albums != cf.stock) deque_destroy(cf.albums);
        cf.albums = 0;
    }
    return 1;
}
/* art_find's look between albums: the open it runs for is given up. */
static int open_cancelled(void) { return op.cancel && op.running && pthread_self() == op.thread; }
/* staged_into on the UI thread while a step runs on its own: the open is given up first. */
static void open_yield(void) {
    if (op.running && pthread_self() != op.thread) open_end(), art_found_drop();
}
void coverflow_yield(void) { open_yield(); }
/* Whether an open's albums want OPEN_ART: listed and in order, their art not known. */
static int art_due(void) { return cf.albums && deque_size(cf.albums) && !art_clean(); }
static void *step_thread(void *k) {
    op.thread = pthread_self(); /* before any statement: coverflow_query_stop compares it */
    op.ret = open_work((int)(long)k);
    __sync_synchronize();
    op.done = 1;
    return k;
}
/* Step op.step on a thread of its own; in place when no thread starts (a started one has a nonzero
 * id), as before steps had threads. */
static void open_start(void) {
    unsigned long t = 0;
    op.done = op.cancel = 0;
    op.running = 1;
    if (pthread_create(&t, 0, step_thread, (void *)(long)op.step) || !t) {
        op.running = 0;
        op.ret = open_work(op.step);
        op.done = 1;
        return;
    }
    op.thread = t;
}
/* The step after k, whose open_work answered last (1: the query's last). */
static int open_next(int k, int last) {
    return !last                      ? k + 1
           : k < OPEN_SORT && sort_due() ? OPEN_SORT
           : k < OPEN_ART && art_due()   ? OPEN_ART
                                         : OPEN_DONE;
}
/* The open's driver: from coverflow_open, then its timer. Collects a step that ended and starts the
 * next, waiting in place while the open is younger than OPEN_SPIN_MS / 2; past that it returns to
 * the main loop and looks again in STEP_TICK_MS, the spinner up. */
static int open_step(const void *unused) {
    op.timer = 0;
    for (;;) {
        if (window_manager_get_top_window(window_manager()) != op.home)
            return open_end(), art_found_drop(), 0; /* given up: the look's jobs are no open's */
        if (op.running && !op.done) {
            if ((unsigned)time_now_ms() - op.at < OPEN_SPIN_MS / 2) {
                usleep(STEP_POLL_MS * 1000);
                continue;
            }
            if (!op.spin) op.spin = 1, widget_invalidate_force(op.home, 0); /* the row's spinner */
            cw_check();
            op.timer = timer_add(open_step, 0, STEP_TICK_MS);
            return 0;
        }
        if (op.running) pthread_join(op.thread, 0), op.running = 0;
        if (op.done) op.step = open_next(op.step, op.ret), op.done = 0;
        if (op.step == OPEN_DONE) break;
        open_start();
    }
    open_end();
    if ((cf.page = open_page())) load();
    return 0;
}
/* Whether Home's Cover Flow row shows the spinner for its chevron: an open waits on its query
 * (nav/row_paint.c paint_chevrons, list_marks.c spinner_icon). */
int coverflow_spinner(void) { return op.spin; }
/* ringnav_input, while an open waits: the wheel's ticks go to Home (0); Return gives the open up
 * at its release and goes nowhere (1); any other key or a touch gives it up, the step stopped
 * first, and goes on to Home (0), so a press into another page finds the library free. */
int coverflow_wait_input(void *e) {
    if (!op.waiting) return 0;
    unsigned type = (unsigned)I(e, EVENT_TYPE), key = (unsigned)I(e, EVENT_KEY);
    int keyed = type == EVT_KEY_DOWN || type == EVT_KEY_UP || type == EVT_KEY_LONG;
    if (keyed && (key == KEY_NEXT || key == KEY_PREV)) return 0;
    if (keyed && key == KEY_RETURN) {
        if (type == EVT_KEY_UP) open_end(), art_found_drop();
        return 1;
    }
    if (keyed || type == EVT_POINTER_DOWN) open_end(), art_found_drop();
    return 0;
}
/* Cover Flow's open in the unified log (docs/internals.md#cover-flow-log): an I line as Home's
 * Cover Flow opens, one when its covers (or its no-albums page) first show, with the time since,
 * and, while nothing shows, a W line CW_FIRST_MS after the open and every CW_AGAIN_MS after that
 * naming the phase it waits in, looked for by the ticks that wait (open_step's and Preparing
 * artwork's poll); an I line too if the open is given up or the page closed first. A few lines an
 * open, never one a frame or an album. The trace file (cf_trace) reaches the card only after a
 * forced restart; these go with every flush of the log. */
#define CW_FIRST_MS 5000
#define CW_AGAIN_MS 60000
void ulog(char level, const char *src, const char *msg);
static struct {
    unsigned at, next;   /* the open's time (0: no open watched), the next W line's */
    const char *refused; /* the memory last refused this open, or 0 */
} cw __attribute__((section(".scratch")));
#define cw_say(level, ...)                                                                         \
    do {                                                                                           \
        char line_[160];                                                                           \
        snprintf(line_, sizeof line_, __VA_ARGS__);                                                \
        ulog(level, "cf", line_);                                                                  \
    } while (0)
static void cw_refused(const char *what) { cw.refused = what; }
/* Where the open waits now, into buf. */
static const char *cw_phase(char *buf, unsigned size) {
    int n;
    if (op.waiting && op.step <= 2)
        n = snprintf(buf, size, "the albums' query (step %d of 3)", op.step + 1);
    else if (op.waiting && op.step == OPEN_SORT)
        n = snprintf(buf, size, "the Sort's order");
    else if (op.waiting && op.step == OPEN_ART)
        n = snprintf(buf, size,
                     "the cover lookup, %u of %u albums looked at, %u without a cover file",
                     aj.looked, cf.albums ? deque_size(cf.albums) : 0, aj.bare);
    else if (op.waiting)
        n = snprintf(buf, size, "the open's last step");
    else if (!cf.page)
        n = snprintf(buf, size, "no page");
    else if (cf.screen == PREPARING)
        n = snprintf(buf, size, "the thumbnail build, %d of %d done", cf.done, cf.total);
    else
        n = snprintf(buf, size, "its page, the covers not shown");
    if (cw.refused && n > 0 && (unsigned)n < size)
        snprintf(buf + n, size - (unsigned)n, "; no memory for %s", cw.refused);
    return buf;
}
static unsigned cw_ms(void) { return (unsigned)time_now_ms() - cw.at; }
static void cw_end(void) { cw.at = 0; }
/* A waiting tick's look: the W line once it is due. */
static void cw_check(void) {
    unsigned now = (unsigned)time_now_ms();
    if (!cw.at || (int)(now - cw.next) < 0) return;
    char phase[128];
    cw_say('W', "cover flow not shown %u s after its open: %s", cw_ms() / 1000,
           cw_phase(phase, sizeof phase));
    cw.next = now + CW_AGAIN_MS;
}
static void cw_open(void) {
    cw.at = (unsigned)time_now_ms() | 1, cw.refused = 0;
    cw.next = cw.at + CW_FIRST_MS;
    if (cf.stock)
        cw_say('I', "cover flow open: %u albums kept, %s", deque_size(cf.stock),
               art_clean() ? "their art known" : "their art to look for");
    else
        cw_say('I', "cover flow open: the albums to query");
}
static void cw_shown(const char *what) {
    if (!cw.at) return;
    cw_say('I', "cover flow shown: %s, %u albums, %u ms after its open%s%s", what,
           cf.albums ? deque_size(cf.albums) : 0, cw_ms(), cw.refused ? "; no memory for " : "",
           cw.refused ? cw.refused : "");
    cw_end();
}
static void cw_given_up(void) {
    if (!cw.at || cf.page) return;
    char phase[128];
    cw_say('I', "cover flow open given up after %u ms, in %s", cw_ms(),
           cw_phase(phase, sizeof phase));
    cw_end();
}
static void cw_closed(void) {
    if (!cw.at) return;
    char phase[128];
    cw_say('I', "cover flow closed %u ms after its open, not shown: %s", cw_ms(),
           cw_phase(phase, sizeof phase));
    cw_end();
}

static int coverflow_open(void *ctx, void *event) {
    if (cf.page || op.waiting) return 0;
    cw_open();
    if (cf.albums_gen != library_gen) drop_albums();
    sort_check();
    if ((cf.stock && !sort_due() && !art_due()) || library_scanning()) { /* nothing to wait for */
        if ((cf.page = open_page())) load();
        return 0;
    }
    op.at = (unsigned)time_now_ms(), op.waiting = 1, op.done = 0;
    op.home = window_manager_get_top_window(window_manager());
    /* kept albums: only their new order, or their art looked for */
    op.step = !cf.stock ? 0 : sort_due() ? OPEN_SORT : OPEN_ART;
    return open_step(0);
}

/* A scoped page: a Coverflow already open (played from, under Now Playing, issue #73) closes once
 * the new one is up, with the page under it (page_under): there is one page's state. ponytail: Return then skips it; stack the state if that is missed. */
static int scoped_open(void) {
    void *old = cf.page, *under = page_under(), *page = open_page();
    if (!page) return 0; /* the old page and its state stay */
    if (old) {
        closed(old, 0);
        window_manager_close_window_force(window_manager(), old);
        if (under) window_manager_close_window_force(window_manager(), under);
    }
    cf.page = page;
    return 1;
}

/* Go to Album from a Coverflow song (navigation.c's qm_goto) and Era's albums: r's tracks alone, no
 * covers under them, so Return closes the page; 0 when it has none. */
int coverflow_goto_album(void *r) {
    void *t = coverflow_album_tracks(r), *one;
    unsigned n = deque_size(t);
    deque_destroy(t);
    if (!n || !(one = _create_deque("stSongInfo"))) return 0;
    deque_init(one);
    _deque_push_back(one, r);
    if (!scoped_open()) {
        deque_destroy(one);
        return 0;
    }
    cf.all_albums = cf.albums;
    cf.albums = one;
    cf.scoped = ALBUM_TRACKS;
    cf.covers = cf.slide = cf.name = cf.artist = 0;
    cf.album = 0;
    cf.body = widget_factory_create_widget(widget_factory(), "view", cf.page, 0, bar_foot(cf.page),
                                           375, client_h());
    to_tracks(0);
    return 1;
}

/* The open page shows r's album alone (coverflow_goto_album): Go to Album has nowhere new to go. */
int coverflow_shows_album(void *r) {
    return cf.page && cf.scoped == ALBUM_TRACKS && !album_cmp(deque_at(cf.albums, 0), r);
}

/* Library Ratings' Albums (ratings.c): the albums hearts gives 1 to 5 hearts, as covers with the
 * rated Sort card alone; 0 when none is. */
int coverflow_rated(int (*hearts)(void *)) {
    int n;
    void *all = cf_staged(albums, 0, &n), *rated = _create_deque("stSongInfo");
    if (n > 0) all = split(all);
    all = music_albums(all);
    deque_init(rated);
    for (unsigned i = 0; i < deque_size(all); ++i)
        if (hearts(deque_at(all, i))) _deque_push_back(rated, deque_at(all, i));
    deque_destroy(all);
    if (!deque_size(rated) || !scoped_open()) {
        deque_destroy(rated);
        return 0;
    }
    if (!cf.sort_read) BLOB_IO(SORT_FILE, cf.sort, 0), cf.sort_read = 1;
    cf.all_sort = cf.sort, cf.all_stock = cf.stock, cf.all_albums = cf.albums;
    cf.sort = SORT_RATING; /* unless saved */
    BLOB_IO(RATED_SORT_FILE, cf.sort, 0);
    cf.stock = rated, cf.albums = 0, cf.hearts = hearts;
    cf.scoped = RATED_ALBUMS;
    load();
    return 1;
}

/* The playlists read again (stock's getAllPlayList, the page under them keeping its own list) as
 * the covers come back to the front or from a playlist's songs, as after More's Rename or Delete,
 * Edit Playlist or Add to Playlist made from them: changed, the covers again, the centre on its
 * playlist or, deleted, the one in its place; none left, the covers close for the list. Their
 * songs too, by the art's worker: a changed count or art names and decodes its cover again
 * (playlist_ready). ponytail: a header query and a songs' read per return to the covers; a hook on
 * every playlist write would save them. */
static int all_playlists(void *unused) { return getAllPlayList(); }
static int playlists_again(const void *unused) {
    pl_again = 0;
    if (cf.scoped != PLAYLISTS || cf.screen != COVERS || !cf.slide || cf.timer) return 0;
    int n;
    void *now = staged(all_playlists, 0, &n), *was = centred(cf.page, 1);
    unsigned k = deque_size(now), c = (unsigned)I(cf.slide, SLIDE_INDEX),
             same = k == deque_size(cf.albums);
    for (unsigned i = 0; same && i < k; ++i) {
        void *a = deque_at(now, i), *b = deque_at(cf.albums, i);
        same = I(a, REC_DISC) == I(b, REC_DISC) && !tk_strcmp(P(a, REC_TITLE), P(b, REC_TITLE));
    }
    if (same || n < 0) {
        deque_destroy(now);
    } else if (!k) {
        deque_destroy(now);
        window_manager_close_window_force(window_manager(), cf.page);
    } else {
        unsigned at = 0;
        while (was && at < k && I(deque_at(now, at), REC_DISC) != I(was, REC_DISC)) ++at;
        cf.album = (int)(at < k ? at : c < k ? c : k - 1);
        deque_destroy(cf.albums);
        cf.albums = now;
        load();
    }
    if (cf.scoped != PLAYLISTS) return 0; /* closed */
    /* covers left on the placeholder by a stop of the art's builds under them (USB Storage,
     * Refresh Cover Flow) ask again: a build, or the known none from playlist_art's table */
    for (int i = 0; fx.tex && i < fx_slots(); ++i)
        if (fx.art[i] == FX_PLACEHOLDER) fx.art[i] = 0, fx.drawn = 0;
    if (!fx.frame) changed(0, 0); /* the flat covers' images, by name */
    widget_invalidate_force(cf.page, 0);
    playlist_art_refresh();
    return 0;
}
static int playlists_front(void *ctx, void *event) {
    if (cf.scoped == PLAYLISTS) rearm(&pl_again, playlists_again, 0);
    return 0;
}

/* Playlists, when Display's Playlist View is Cover Flow (playlists.c opens them over the Playlists
 * page): lists (p_deque_playlist), the containers, as covers, which the page copies; their art
 * playlist_art.c's, each cover filled in as its file is made. 0 when there are none. */
int coverflow_playlists(void *lists) {
    void *mine = lists && deque_size(lists) ? _create_deque("stSongInfo") : 0;
    if (!mine) return 0;
    deque_init_copy(mine, lists);
    if (!scoped_open()) {
        deque_destroy(mine);
        return 0;
    }
    cf.all_albums = cf.albums;
    cf.albums = mine;
    cf.scoped = PLAYLISTS;
    cf.album = 0;
    widget_on(cf.page, EVT_WINDOW_FOREGROUND, playlists_front, 0);
    playlist_art_ready = playlist_ready;
    load(); /* the songs' hashes and counts: playlists.c asked for them as the list was built */
    return 1;
}

/* The playlists' covers closed, unanimated, for their hold's New Playlist:
 * the Playlists page under them, or 0 when they aren't up. */
void *coverflow_playlists_close(void) {
    void *under = cf.scoped == PLAYLISTS ? page_under() : 0;
    if (under) window_manager_close_window_force(window_manager(), cf.page);
    return under;
}

/* The queue's playing record, or 0; *pos and *n get its index and the queue length. */
void *queue_now(unsigned *pos, unsigned *n) {
    void *queue = P(mcl_pdeqplaylist, 0);
    *pos = *(volatile unsigned *)MCL_POS;
    *n = queue ? deque_size(queue) : 0;
    return *pos < *n ? deque_at(queue, *pos) : (void *)0;
}

/* r's REC_ALBUM, REC_ARTIST, REC_ALBUM_ARTIST or REC_TITLE as the player parsed it from the file,
 * once it has parsed r's; else the record's own. Folder plays' records, and those of the next
 * folder, which stock queues when a folder play ends (on_player_autochange), carry no tags (issue
 * #7). */
const char *now_tag(void *r, int field) {
    if (!r) return (void *)0;
    if (!tk_strcmp((const char *)g_play_id3_info, P(r, REC_PATH)))
        return (const char *)g_play_id3_info + (field == REC_ALBUM          ? ID3_ALBUM
                                                : field == REC_TITLE        ? ID3_TITLE
                                                : field == REC_ALBUM_ARTIST ? ID3_ALBUM_ARTIST
                                                                            : ID3_ARTIST);
    return P(r, field);
}

/* Split's art pane (docs/ipod.md#home): from the set's split_art to the screen's right edge, from
 * split_art_up rows above the status row's foot, under the row, to the screen's bottom. Home's
 * window is the whole screen (docs/oled-design.md#full-bleed), so pane_y is the pane's top in it
 * and on the screen alike. HOME_PW is the widest, as no set's split_art is left of HOME_SPLIT_W
 * (tools/theme.py). */
#define HOME_PW (375 - HOME_SPLIT_W)
static int pane_x(void) { return split_geom(SPLIT_ART); }
static int pane_w(void) { return 375 - pane_x(); }
static int pane_up(void) { return split_geom(SPLIT_ART_UP); }
static int pane_h(void) { return client_h() + pane_up(); }
static int pane_y(void) { return 320 - pane_h(); }
/* One cover in Split's pane (docs/ipod.md#home): tex its zoomed copy inside a border (slide_load),
 * tw x th texels, the border's not counted (tw 0: none); lib 0 the playing track's, which pans
 * from the old crop u0 v0 across ranges rx ry, an eased ping-pong (drift_at) for as long as it
 * shows; lib 1 a library cover of the slideshow, whose window moves once over its life from
 * kb[0] to kb[1] (its top left in 1/256 texel and its scale in 1/65536 texel a pixel); lib
 * SLIDE_STILL the frame as it stood when play started mid-fade (frame_hold), tex its pixels as
 * they are, tw x th, no border; ms the time it has shown, the pan's phase. */
typedef struct {
    unsigned *tex;
    int tw, th, u0, v0, rx, ry, lib;
    int kb[2][3];
    unsigned ms;
} slide_t;
enum { SLIDE_STILL = 2 };
enum {
    SHOW_NOW,
    SHOW_LIB,
    SHOW_RADIO
}; /* what the pane shows (art_source): the playing cover, the library's, Radio's (radio_pane.c) */
/* iPod Home (docs/ipod.md): the playing track's art, or the library's in turn, beside the list. */
static struct {
    void *win, *art, *list, *sets; /* sets: the Settings list, in the list's place while open */
    void *lib;             /* the Library's rows the same way: only a set with home_lib has it */
    void *stream;          /* and Streaming's, which the Settings list's row opens */
    void *carousel[3];     /* Home: Carousel's slide_menu and its img_left, img_right */
    unsigned key, repaint; /* repaint: the timer of home_repaint */
    /* drift: home_drift's timer, tick its period; drift_last: its last step */
    unsigned drift, drift_last, tick;
    /* sl[0] the cover showing (tw 0: the still stock image, which never moves), sl[1] the next
     * one fading in over it for fade ms (0: none); the panel's frame, drawn from them as drawn
     * says (frame_stamp, 0 for none yet); ring: home_render's rows */
    slide_t sl[2];
    void *frame;
    unsigned fade, drawn;
    int show;      /* SHOW_* */
    unsigned seed; /* kb_rand's */
    void *row;     /* the highlighted row and what it shows in the pane (PANE_*) */
    int kind;
    unsigned pane_timer;
    unsigned ring[4][HOME_PW];
    int clip[4]; /* the canvas clip while the art paints, restored after */
    int clipped;
    /* the bar whose border paint inks the pane's rows (coverflow_home_bar), 0: none due; ground:
     * the bar's background there (BGRA); held: the row's ink, a byte a pane column (home_ink) */
    void *ink_bar;
    unsigned ground;
    unsigned char held[HOME_PW];
    /* np: Now Playing's row shows (np_loaded); np_stop: when play was first seen stopped, | 1, 0
     * while something is loaded; np_timer: np_due's; folders: Folders' rule at the last layout */
    int np, folders;
    unsigned np_stop, np_timer;
    unsigned rtick; /* radio_tick's timer, while Radio's pane moves */
} home __attribute__((section(".scratch")));
extern int ipod_home(void), home_shortcut_open(void *, void *);
extern void ipod_shortcut_seen(void), home_sets_bind(void *win); /* theme_engine.c, navigation.c */
extern void home_np_bind(void *win), home_np_sync(void); /* home_np.c: Home's first row */
extern void library_rows(void *list), stream_rows(void *list);
/* home.c: Home's overflow */
extern void home_overflow(void *list, int bottom), home_pad(void *view),
    home_fades(void *canvas, void *list, int list_y, int cols, int split, int shift);
extern int library_search_open(void *ctx, void *event);
extern int games_open(void *ctx, void *event); /* games.c: Home's Games row */
extern int tuner_open(void *ctx, void *event); /* radio_tuner.c: Home's Radio row */
/* library_edit.c: Home's rows in the user's order and Edit Home; home.c: Folders' rule */
extern void lr_home_tag(void *view), lr_home_apply(void *view, int np),
    lr_edit_labels(void *view, int on);
extern int lr_home_editing(void), home_folders(void);
extern int library_count(const char *sql), msg_counting(void);
extern const char *ipod_home_shortcut(void);
/* Split's menu width; the art fills the rest. */

/* player_parsecover_thd writes the playing track's cover and then sets g_playcover_type, as Now
 * Playing reads it: 1 embedded, 2 folder image, 4 downloaded; 0 while parsing or stopped, 3 none.
 * Tidal's (5) is keyed by its online URL, never a queue path, so Home leaves it out. */
static const char *const player_covers[] = { 0, "file://" PEQ_ROOT "/tmp/coverpic.jpg",
                                             "file://" PEQ_ROOT "/tmp/externpic.jpg", 0,
                                             "file://" PEQ_ROOT "/tmp/externpic.jpg" };

/* The player's file (now_cover's) as last written: its size and modification time, 0 for none. The
 * art's keys take it: a card app's URL queue has no path and a cover by URL leaves g_lastcover_url
 * empty, so type and path are the same for every song, and stock clears its request before the
 * download, so only the file tells one song's cover from the last, even one downloaded after the
 * next song's request. The cover thread waits 100 ms between covers, so two writes never share a
 * time. o32 stat: st_size word 12, st_mtime word 16, its nanoseconds 17.
 * ponytail: a stat of tmpfs each time a key is made. */
unsigned now_cover_written(const char *file) {
    unsigned st[36];
    if (!file || __xstat(3, file + 7, st)) return 0; /* past "file://" */
    return hash_bytes(hash_bytes(FNV_SEED, (const unsigned char *)&st[12], 4),
                      (const unsigned char *)&st[16], 8);
}

/* The player's cover of r: its type once the cover thread has parsed r (g_lastcover_url is its
 * path), else 0, and *file that type's file, or 0. */
unsigned char now_cover(void *r, const char **file) {
    const char *path = r ? P(r, REC_PATH) : (void *)0;
    unsigned char type =
        path && !tk_strcmp((const char *)g_lastcover_url, path) ? g_playcover_type : 0;
    *file = type < sizeof(player_covers) / sizeof(*player_covers) ? player_covers[type] : 0;
    return type;
}

/* AWTK's bitmaps of the player's cover files let go of: every song's cover is written to the same
 * file, so a list showing one (album_cover.c) takes the file again, not the last song's bitmap. */
void now_cover_forget(void) {
    for (unsigned i = 0; i < sizeof(player_covers) / sizeof(*player_covers); ++i)
        if (player_covers[i]) image_manager_unload_bitmap_by_name(image_manager(), player_covers[i]);
}

/* r's cover as a file:// URL in url (size bytes, a cover's path cut short counted as none), as Home's pane finds it: the player's file once
 * the cover thread has parsed r (now_cover: embedded, folder image or downloaded; for a server
 * song's stream download, its app's cover first, catalogue.c cat_copy_of), else the app's cover of
 * a server row's album (catalogue_cover), else Cover Flow's cache of the album by its tags
 * (now_tag: the playing file's own). 0, url empty, for none: the caller's placeholder. Control
 * Center and the modal header take it. */
int song_cover_url(void *r, char *url, unsigned size) {
    const char *file = (const char *)0;
    char art[512];
    *url = 0;
    if (!r || size < 8) return 0;
    if (now_cover(r, &file) && file && *file) {
        tk_snprintf(url, size, "%s", file); /* a file:// URL already */
        return 1;
    }
    tk_snprintf(url, 8, "file://");
    if (catalogue_cover(P(r, REC_PATH), url + 7, size - 7)) return 1;
    art_path(art, tags_key(now_tag(r, REC_ARTIST), now_tag(r, REC_ALBUM)), "");
    if (file_size(art) > 0 && strlen(art) < size - 7) {
        tk_snprintf(url + 7, size - 7, "%s", art);
        return 1;
    }
    *url = 0;
    return 0;
}

/* Repaint every window once the paint that changed the art is over: AWTK drops what a paint
 * invalidates when the frame ends, so the new art would reach only regions repainted later. */
static int home_repaint(const void *unused) {
    home.repaint = 0;
    widget_invalidate_force(window_manager(), 0);
    return 0; /* RET_REMOVE */
}

/* One axis of the pan (an iPod classic's menu art): 0 to range px and back over 2 x period ms, in
 * 1/256 px, at ms shown, eased in and out at each end (smoothstep, 3s^2 - 2s^3), so it slows to a
 * stop and turns with no jolt; its speed peaks at 1.5 times the average halfway. s is in 1/65536
 * and e, unrounded so it never steps back, in 1/2^48 (64 bits); range is a tenth of a cover within
 * DRIFT_TEXELS (under 100 px), so the result fits 32. */
static int drift_at(int range, unsigned period, unsigned ms) {
    unsigned t = ms % (2 * period), s = ((t < period ? t : 2 * period - t) << 16) / period;
    unsigned long long e = (unsigned long long)s * s * (3 * 65536u - 2 * s);
    return (int)((unsigned long long)range * 256 * (e >> 24) >> 24);
}

/* A library cover's whole life on the pane: its fade in, its hold and its fade out. */
#define SLIDE_LIFE_MS (SLIDE_HOLD_MS + 2 * SLIDE_FADE_MS)
/* Where s's window is now: its top left in the zoomed cover in 1/256 texel (u v) and its scale in
 * 1/65536 texel a pixel (k). The playing cover: the old crop, then the pan, a texel a pixel. A
 * library cover: kb[0] to kb[1] at an even pace over SLIDE_LIFE_MS, then still (the Ken Burns
 * look: one slow zoom and drift a cover); ms is at most SLIDE_LIFE_MS here, so p fits 32 bits. A
 * still never moves. */
static void slide_at(const slide_t *s, int *u, int *v, int *k) {
    if (!s->lib || s->lib == SLIDE_STILL) {
        *u = s->u0 * 256 + drift_at(s->rx, DRIFT_X_MS, s->ms);
        *v = s->v0 * 256 + drift_at(s->ry, DRIFT_Y_MS, s->ms);
        *k = 65536;
        return;
    }
    unsigned p = ((s->ms < SLIDE_LIFE_MS ? s->ms : SLIDE_LIFE_MS) << 16) / SLIDE_LIFE_MS;
    int *at[3] = { u, v, k };
    for (int i = 0; i < 3; ++i)
        *at[i] = s->kb[0][i] + (int)((long long)(s->kb[1][i] - s->kb[0][i]) * p >> 16);
}

/* The incoming slide's share of the frame in 1/256, by the fade's time: 1 to 255. */
static unsigned fade_k(void) {
    unsigned k = home.fade * 256 / SLIDE_FADE_MS;
    return k < 1 ? 1 : k > 255 ? 255 : k;
}

/* What the frame shows now, never 0: the slides' windows and the fade's share. */
static unsigned frame_stamp(void) {
    int at[7] = { 0 };
    slide_at(&home.sl[0], &at[0], &at[1], &at[2]);
    if (home.fade) slide_at(&home.sl[1], &at[3], &at[4], &at[5]), at[6] = (int)fade_k();
    return hash_bytes(FNV_SEED, (const unsigned char *)at, sizeof at) | 1;
}

/* A step repaints only the panel, once the pan has moved; coverflow_home_clip draws it. */
static void pane_invalidate(void) {
    int pane[4] = { pane_x(), pane_y(), pane_w(), pane_h() }; /* rect_t, in the parent */
    widget_invalidate_force(P(home.art, W_PARENT), pane);
}
static void drift_place(void) {
    if (frame_stamp() != home.drawn) pane_invalidate();
}

/* What a highlighted row shows in Split's pane: Now Playing's row the playing cover, every other
 * row the library's (the slideshow). */
enum { PANE_NOW, PANE_LIBRARY };

/* The pan runs only while it shows: Home on top and not sliding (its snapshot would not move), the
 * screen lit, on a row that shows a cover (Full never paints the art), and either a cover that
 * moves (the placeholder does not) or a fade, or the slideshow, which fetches the next cover. */
static int drift_live(void) {
    void *wm = window_manager();
    const slide_t *s = &home.sl[0];
    int moves = s->tw && (s->lib || s->rx || s->ry || home.fade);
    return (moves || home.show == SHOW_LIB) && widget_get_visible(home.art) && g_backlight_status &&
           !g_lockscreen_pageflag && window_manager_get_top_window(wm) == home.win &&
           !window_manager_is_animating(wm);
}

/* The faded-in slide is the one showing, the other's texture freed. */
static void fade_end(void) {
    free(home.sl[0].tex);
    home.sl[0] = home.sl[1];
    memset(&home.sl[1], 0, sizeof home.sl[1]);
    home.fade = 0, home.drawn = 0;
}
/* A slide's time on: the playing cover's modulo a whole number of both axes' cycles, so it never
 * wraps mid-cycle; a library cover's held at its life's end once past it. */
static void slide_step(slide_t *s, unsigned step) {
    s->ms = s->lib ? (s->ms + step < SLIDE_LIFE_MS ? s->ms + step : SLIDE_LIFE_MS)
                   : (s->ms + step) % (2u * DRIFT_X_MS * DRIFT_Y_MS);
}
static void slide_next(void);

/* The pan's step; once it no longer shows the timer goes, and the next Home paint arms it again
 * at the same phase. Steps are capped, so a stalled loop never jumps. A fade runs to its end, the
 * slideshow asks for its next cover, and the timer's period is SLIDE_TICK_MS while a fade runs,
 * else DRIFT_TICK_MS: a new timer takes over when it changes. */
static int home_drift(const void *unused) {
    if (!drift_live()) return (int)(home.drift = 0); /* RET_REMOVE */
    unsigned now = time_now_ms(), step = now - home.drift_last;
    home.drift_last = now;
    step = step < DRIFT_MAX_STEP_MS ? step : DRIFT_MAX_STEP_MS;
    slide_step(&home.sl[0], step);
    if (home.fade) {
        slide_step(&home.sl[1], step);
        home.fade += step;
        if (home.fade >= SLIDE_FADE_MS) fade_end();
    }
    /* the slideshow's next cover, or at once over a playing cover still fading in (the wheel
     * back on a library row) */
    if (home.show == SHOW_LIB && (!home.fade || !home.sl[1].lib)) slide_next();
    drift_place();
    unsigned tick = home.fade ? SLIDE_TICK_MS : DRIFT_TICK_MS;
    if (tick == home.tick) return 8; /* RET_REPEAT */
    home.tick = tick;
    home.drift = timer_add(home_drift, (void *)0, tick);
    return 0; /* RET_REMOVE: the new timer goes on */
}

/* A w x h image's size just covering the panel, ph high, at its proportions; unknown sizes fill
 * it. */
static void home_fit(unsigned w, unsigned h, int ph, int *fw, int *fh) {
    int pw = pane_w();
    *fw = pw, *fh = ph;
    if (w && h && w <= 8192 && h <= 8192) {
        if ((unsigned)pw * h > (unsigned)ph * w)
            *fh = (int)(((unsigned)pw * h + w - 1) / w);
        else
            *fw = (int)(((unsigned)ph * w + h - 1) / h);
    }
}

/* [-1,6,-1]/4 along n opaque texels step apart, in place, rounded and clamped a channel; the ends
 * are their own neighbours. Bilinear kept a whole-pixel frame's texels and mixed a half-pixel
 * one's 50/50, so the pan's sharpness pulsed as its phase turned. home_render's cubic B-spline
 * blurs every phase about alike but is soft on its own; this brings the detail back, about twice
 * the old [1,2,1]/4 and bilinear's, and the pulse stays small (a tenth of the sharpness on a
 * photo, a fifth on lettering).
 * ponytail: the frame costs about 2.3x the bilinear's, five a second while Home is lit; an exact
 * cubic-spline prefilter (an IIR each way) sharpens no more than this does here. */
void home_sharpen(unsigned *t, int n, int step) { /* test/coverflow.py */
    unsigned prev = t[0];
    for (int i = 0; i < n; ++i) {
        unsigned c = t[i * step], next = i + 1 < n ? t[(i + 1) * step] : c, o = 0xff000000u;
        for (int s = 0; s < 24; s += 8) {
            int v = 6 * (int)(c >> s & 255) - (int)(prev >> s & 255) - (int)(next >> s & 255);
            v = (v + 2) >> 2;
            o |= (unsigned)(v < 0 ? 0 : v > 255 ? 255 : v) << s;
        }
        prev = c;
        t[i * step] = o;
    }
}

/* The decoded image (data, stride, channel order o, w x h) box filtered to tw x th texels at out,
 * ostride texels a row, over the grey paper (a byte). Alpha-weighted sums, so only the average
 * divides; at most 4 x 4 samples a texel, so a huge embedded cover filters as quickly as a small
 * one. */
static void box_filter(const unsigned char *data, unsigned stride, const unsigned char *o,
                       unsigned w, unsigned h, unsigned *out, int ostride, int tw, int th,
                       unsigned paper) {
    for (int y = 0; y < th; ++y) {
        unsigned *px_out = out + y * ostride;
        unsigned y0 = (unsigned)y * h / (unsigned)th, y1 = (unsigned)(y + 1) * h / (unsigned)th;
        y1 += y1 == y0;
        for (int x = 0; x < tw; ++x) {
            unsigned x0 = (unsigned)x * w / (unsigned)tw, x1 = (unsigned)(x + 1) * w / (unsigned)tw;
            x1 += x1 == x0;
            unsigned r = 0, g = 0, b = 0, a = 0, n = 0, xs = (x1 - x0 + 3) / 4 * 4,
                     ys = (y1 - y0 + 3) / 4;
            for (unsigned sy = y0; sy < y1; sy += ys)
                for (const unsigned char *px = data + sy * stride + x0 * 4,
                                         *end = data + sy * stride + x1 * 4;
                     px < end; px += xs, n += 255) {
                    unsigned k = px[o[3]];
                    r += px[o[0]] * k, g += px[o[1]] * k, b += px[o[2]] * k, a += k;
                }
            unsigned bg = paper * (n - a);
            *px_out++ = 0xff000000u | (r + bg) / n | (g + bg) / n << 8 | (b + bg) / n << 16;
        }
    }
}

/* One axis's pan in the zoomed cover, t texels long, for a panel p long: from the old crop of the
 * cover's fit f (half its overhang in), across the zoom's margin t - f, start *c0 and range *r.
 * A set's split_inset keeps that percent of t clear at each edge too, so a cover's own frame (many
 * have a thin white border) never shows; with no room left, the zoom's middle, still. */
static void drift_axis(int f, int t, int p, int *c0, int *r) {
    int lo = (f - p) / 2, hi = lo + t - f, ins = t * split_geom(SPLIT_INSET) / 100;
    if (lo < ins) lo = ins;
    if (hi > t - p - ins) hi = t - p - ins;
    if (hi < lo) lo = hi = (t - p) / 2;
    *c0 = lo, *r = hi - lo;
}

/* kb_ends' random numbers: xorshift32, seeded from the clock on first use. */
static unsigned kb_rand(void) {
    unsigned x = home.seed ? home.seed : time_now_ms() | 1;
    x ^= x << 13, x ^= x >> 17, x ^= x << 5;
    return home.seed = x;
}
/* x within lo..hi, else halfway between them (a window wider than the room: centred). */
static int kb_clamp(int x, int lo, int hi) {
    return hi < lo ? (lo + hi) / 2 : x < lo ? lo : x > hi ? hi : x;
}
/* A library cover's Ken Burns: a focus at random in the middle 40 % of the zoomed cover each way,
 * and a drift of up to KB_DRIFT px each way about it; the window starts half the drift before the
 * focus and ends half after, zooming in (scale 1 to KB_NEAR) or out, at random. Each end is kept
 * within the cover less split_inset at each edge, as the pan is (drift_axis). */
static void kb_ends(slide_t *s) {
    int pw = pane_w(), ph = pane_h(), in = (int)(kb_rand() & 1), inset = split_geom(SPLIT_INSET);
    int ix = s->tw * inset / 100 * 256, iy = s->th * inset / 100 * 256;
    int fx = s->tw * 256 / 100 * (30 + (int)(kb_rand() % 41)),
        fy = s->th * 256 / 100 * (30 + (int)(kb_rand() % 41)),
        dx = (int)(kb_rand() % (2 * KB_DRIFT + 1)) - KB_DRIFT,
        dy = (int)(kb_rand() % (2 * KB_DRIFT + 1)) - KB_DRIFT;
    for (int e = 0; e < 2; ++e) {
        int k = e == in ? 65536 : KB_NEAR, side = e ? 1 : -1, w = pw * (k >> 8), h = ph * (k >> 8);
        s->kb[e][0] = kb_clamp(fx + side * dx * 128 - w / 2, ix, s->tw * 256 - ix - w);
        s->kb[e][1] = kb_clamp(fy + side * dy * 128 - h / 2, iy, s->th * 256 - iy - h);
        s->kb[e][2] = k;
    }
}

/* The cover at url into s, box filtered once to its fit zoomed DRIFT_ZOOM_PCT (s->tex, tw x th)
 * and composited over the page as Coverflow's decode does, for the pan to draw from: the playing
 * cover's (lib 0, at phase ms) across the zoom's margin only (rx ry), from the old crop (u0 v0), so
 * its edges never show; a library cover's (lib 1) as kb_ends moves it. 1 when it moves, -1 when
 * nothing loads, 0 for a 16-bit decode or a cover too long for DRIFT_TEXELS, which the stock fill
 * shows still; s is emptied first, and stays empty but for a 1. s->tex holds the cover inside a
 * border of its edges, a texel left and above and two right and below (tw + 3 texels a row):
 * home_render reads that far past the panel.
 * ponytail: Light Mode's paper behind a transparent cover changes with the next cover. */
static int slide_load(slide_t *s, const char *url, int lib, unsigned ms) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    unsigned bitmap[64]; /* bitmap_t */
    free(s->tex);
    memset(s, 0, sizeof *s);
    if (widget_load_image(home.art, url, bitmap)) return -1;
    unsigned w = bitmap[0], h = bitmap[1], format = ((unsigned short *)bitmap)[7] - 1u;
    int fw, fh;
    home_fit(w, h, pane_h(), &fw, &fh);
    int tw = fw * DRIFT_ZOOM_PCT / 100, th = fh * DRIFT_ZOOM_PCT / 100;
    const unsigned char *data = 0;
    if (!home.frame) home.frame = opaque_frame(pane_w(), pane_h(), 1);
    if (home.frame && format < 4 && w && h && w <= 8192 && h <= 8192 && tw <= DRIFT_TEXELS / th) {
        s->tex = calloc((unsigned)(th + 3) * (unsigned)(tw + 3), 4);
        data = s->tex ? bitmap_lock_buffer_for_read(bitmap) : 0;
    }
    if (data) {
        unsigned stride = bitmap_get_line_length(bitmap),
                 paper = ipod_light() ? scheme()->paper & 255 : 0;
        int ts = tw + 3;                 /* s->tex's row, its border included */
        unsigned *tex = s->tex + ts + 1; /* texel 0, 0 */
        box_filter(data, stride, at[format], w, h, tex, ts, tw, th, paper);
        bitmap_unlock_buffer(bitmap);
        for (int y = 0; y < th; ++y) home_sharpen(tex + y * ts, tw, 1);
        for (int x = 0; x < tw; ++x) home_sharpen(tex + x, th, ts);
        for (int y = 0; y < th; ++y) {
            unsigned *row = tex + y * ts;
            row[-1] = row[0], row[tw] = row[tw + 1] = row[tw - 1];
        }
        /* then rows -1, th and th + 1, from the edge rows and their border */
        memcpy(s->tex, s->tex + ts, (unsigned)ts * 4);
        for (int y = th + 1; y < th + 3; ++y)
            memcpy(s->tex + y * ts, s->tex + th * ts, (unsigned)ts * 4);
        s->tw = tw, s->th = th, s->lib = lib, s->ms = ms;
        drift_axis(fw, tw, pane_w(), &s->u0, &s->rx);
        drift_axis(fh, th, pane_h(), &s->v0, &s->ry);
        if (lib) kb_ends(s);
    } else {
        free(s->tex);
        s->tex = 0;
    }
    widget_unload_image(home.art, bitmap);
    return data != 0;
}

/* Cubic B-spline weights at phase t/256, in 1/256: all >= 0, summing to 256. */
static void bspline_w(unsigned t, unsigned *w) {
    unsigned s = 256 - t;
    w[0] = (s * s * s + 196608) / 393216;
    w[3] = (t * t * t + 196608) / 393216;
    w[1] = (3 * t * t * t + 4 * 16777216u - 6 * 256 * t * t + 196608) / 393216;
    w[2] = 256 - w[0] - w[1] - w[3];
}

/* Four opaque texels weighted w, rounded. Weights >= 0 summing to 256 keep each channel within
 * its 16 bits, so red and blue blend in one word. */
HOT unsigned tap4(unsigned a, unsigned b, unsigned c, unsigned e, const unsigned *w) {
    unsigned rb = (a & 0xff00ffu) * w[0] + (b & 0xff00ffu) * w[1] + (c & 0xff00ffu) * w[2] +
                  (e & 0xff00ffu) * w[3] + 0x800080u,
             g = (a & 0xff00u) * w[0] + (b & 0xff00u) * w[1] + (c & 0xff00u) * w[2] +
                 (e & 0xff00u) * w[3] + 0x8000u;
    return 0xff000000u | (rb >> 8 & 0xff00ffu) | (g >> 8 & 0xff00u);
}

/* a to b by k of 256, per channel */
HOT unsigned lerp(unsigned a, unsigned b, unsigned k) {
    const unsigned w[4] = { 256 - k, k, 0, 0 };
    return tap4(a, b, 0, 0, w);
}

/* Split's vignette (docs/ipod.md#home, approved as V4 on 2026-11-15): the pane's top VIG_TOP and
 * bottom VIG_BOT rows on the screen darkened toward 25% brightness, 0.25 + 0.75 t^1.6 of each row's
 * pixels, t its distance from the edge over the fade's height; in 256ths, as tables. The status
 * row's glyphs are inked from these darkened pixels (home_ink reads the frame), so a bright cover
 * gets white ones where its own top would have asked for black. */
#define VIG_TOP 50
#define VIG_BOT 40
static const unsigned char vig_top[VIG_TOP] = {
    64,  64,  65,  66,  67,  69,  70,  72,  74,  76,  79,  81,  84,  86,  89,  92,  95,
    98,  101, 105, 108, 112, 116, 119, 123, 127, 131, 136, 140, 144, 149, 153, 158, 163,
    168, 173, 178, 183, 188, 193, 198, 204, 209, 215, 220, 226, 232, 238, 244, 250
};
static const unsigned char vig_bot[VIG_BOT] = { 64,  65,  66,  67,  69,  71,  73,  76,  79,  82,
                                                85,  88,  92,  96,  100, 104, 108, 113, 118, 122,
                                                127, 132, 138, 143, 149, 155, 160, 166, 173, 179,
                                                185, 192, 198, 205, 212, 219, 226, 233, 241, 248 };
/* The vignette's 256ths for the frame's row y (the screen's row y + 320 - pane_h()), 256 outside. */
static unsigned vig_k(int y) {
    y += 320 - pane_h();
    return y < VIG_TOP ? vig_top[y < 0 ? 0 : y] : y >= 320 - VIG_BOT ? vig_bot[319 - y] : 256;
}
/* c (RGBA8888) at k 256ths of its brightness, its alpha kept. */
static unsigned vig(unsigned c, unsigned k) {
    return (c & 0xff000000u) | ((c & 0xff00ffu) * k >> 8 & 0xff00ffu) | ((c & 0xff00u) * k >> 8 & 0xff00u);
}

/* Slide s's window at u, v (1/256 texel) and scale k (1/65536 texel a pixel) into the panel, so
 * it moves by fractions of a pixel: in whole pixels its 1.5 a second jerked. A separable cubic
 * B-spline over 4 x 4 texels: each cover row the panel needs is filtered across once into
 * home.ring (by its row, tag), and each frame row down from the four it falls among. At scale 1
 * (the playing cover) every column takes the same weights, as one row of texels a pixel; a
 * library cover's zoom gives each column its own. blend 0 writes the frame; else the slide is laid
 * over what d holds at blend/256 (a cross-fade's incoming cover), each under the vignette (vig_k).
 * A still (only ever the one showing, so blend 0) is copied as it is: frame_hold made it from a
 * frame the vignette had darkened already. */
static void home_render(unsigned *d, int pitch, const slide_t *s, int u, int v, int k,
                        unsigned blend) {
    if (s->lib == SLIDE_STILL) {
        for (int y = 0; y < s->th; ++y, d += pitch)
            memcpy(d, s->tex + y * s->tw, (unsigned)s->tw * 4);
        return;
    }
    unsigned wx[HOME_PW][4], wy[4];
    int ix[HOME_PW], tag[4] = { -1, -1, -1, -1 }, ts = s->tw + 3, pw = pane_w(), one = k == 65536;
    for (int x = 0; x < (one ? 1 : pw); ++x) {
        unsigned at = ((unsigned)u << 8) + (unsigned)x * (unsigned)k; /* 1/65536 texel */
        ix[x] = (int)(at >> 16), bspline_w(at >> 8 & 255, wx[x]);     /* texel -1, bordered at 0 */
    }
    for (int y = 0, h = pane_h(); y < h; ++y, d += pitch) {
        unsigned at = ((unsigned)v << 8) + (unsigned)y * (unsigned)k;
        int r = (int)(at >> 16);
        for (int j = r; j < r + 4; ++j) {
            if (tag[j & 3] == j) continue;
            unsigned *o = home.ring[j & 3];
            const unsigned *t = s->tex + j * ts;
            tag[j & 3] = j;
            if (one)
                for (int x = 0; x < pw; ++x, ++t)
                    o[x] = tap4(t[*ix], t[*ix + 1], t[*ix + 2], t[*ix + 3], *wx);
            else
                for (int x = 0, i; x < pw; ++x)
                    i = ix[x], o[x] = tap4(t[i], t[i + 1], t[i + 2], t[i + 3], wx[x]);
        }
        bspline_w(at >> 8 & 255, wy);
        const unsigned *r0 = home.ring[r & 3], *r1 = home.ring[(r + 1) & 3],
                       *r2 = home.ring[(r + 2) & 3], *r3 = home.ring[(r + 3) & 3];
        unsigned dim = vig_k(y);
        for (int x = 0; x < pw; ++x) {
            unsigned c = tap4(r0[x], r1[x], r2[x], r3[x], wy);
            if (dim < 256) c = vig(c, dim);
            d[x] = blend ? lerp(d[x], c, blend) : c;
        }
    }
}

/* The frame, rendered again only once the slides moved or the fade stepped: 0 when it cannot be. */
static int home_frame(void) {
    unsigned stamp = frame_stamp();
    if (stamp != home.drawn) {
        unsigned *d = (unsigned *)bitmap_lock_buffer_for_write(home.frame);
        if (!d) return 0;
        int pitch = (int)(bitmap_get_line_length(home.frame) / 4), u, v, k;
        slide_at(&home.sl[0], &u, &v, &k);
        home_render(d, pitch, &home.sl[0], u, v, k, 0);
        if (home.fade) {
            slide_at(&home.sl[1], &u, &v, &k);
            home_render(d, pitch, &home.sl[1], u, v, k, fade_k());
        }
        bitmap_unlock_buffer(home.frame);
        home.drawn = stamp;
    }
    return 1;
}

/* The frame over the art (its image is empty), the whole pane. */
static void home_paint(void *canvas) {
    int src[4] = { 0, 0, pane_w(), pane_h() }, dst[4] = { 0, 0, src[2], src[3] };
    if (home_frame()) canvas_draw_image(canvas, home.frame, src, dst);
}

/* A paint can't invalidate (AWTK drops it with the frame), so the frame after. */
static int pane_after(const void *unused) {
    home.pane_timer = 0;
    pane_invalidate();
    return 0; /* RET_REMOVE */
}

/* The row now highlighted on Home's surface: the pane follows it. On a wheel move (painting 0) the
 * pane joins the row's frame; a paint catches every other way a row changes (Return's first row, a
 * tap, a list sliding in). Names are compared only when the row changes. */
void coverflow_home_row(void *surface, void *row, int id, int painting) {
    if (row == home.row || !home.art || ipod_home()) return;
    home.row = row;
    const char *name = widget_get_prop_str(row, "name", "");
    /* as an iPod classic's menu shows the library's art on Music */
    int kind = tk_strcmp(name, "img_playing") ? PANE_LIBRARY : PANE_NOW;
    if (kind == home.kind) return;
    home.kind = kind;
    if (painting)
        rearm(&home.pane_timer, pane_after, 0);
    else
        pane_invalidate();
}

/* The pane as the frame (its image empty, home_paint draws it), at the panel's size. */
static void art_frame(void) {
    image_base_set_image(home.art, "");
    widget_move_resize(home.art, pane_x(), pane_y(), pane_w(), pane_h());
    rearm(&home.repaint, home_repaint, 0);
}
/* No slide: the still stock image or the theme's pane shows. */
static void slides_drop(void) {
    free(home.sl[0].tex), free(home.sl[1].tex);
    memset(home.sl, 0, sizeof home.sl);
    home.fade = 0, home.drawn = 0;
}
/* The frame as it stands, both slides blended, as the one showing: a still (SLIDE_STILL) the next
 * cover fades in over, both slides freed. 0, the slides as they were, with no memory for it. */
static int frame_hold(void) {
    int pw = pane_w(), h = pane_h();
    unsigned *t = home_frame() ? calloc((unsigned)(pw * h), 4) : 0;
    const unsigned *f = t ? (const unsigned *)bitmap_lock_buffer_for_read(home.frame) : 0;
    if (!f) return free(t), 0;
    int pitch = (int)(bitmap_get_line_length(home.frame) / 4);
    for (int y = 0; y < h; ++y) memcpy(t + y * pw, f + y * pitch, (unsigned)pw * 4);
    bitmap_unlock_buffer(home.frame);
    slides_drop();
    home.sl[0].tex = t, home.sl[0].tw = pw, home.sl[0].th = h, home.sl[0].lib = SLIDE_STILL;
    return 1;
}
/* The cover at url as the next slide (slide_load's answer): with one showing it fades in over it,
 * else, or with cut (the same cover again after a layout, on its new paper), it shows at once. A
 * playing cover that comes while another fades in takes that one's place in the fade (the player's
 * own after the Coverflow thumbnail that stood in for it, or a quick skip's next), so nothing
 * jumps. Any other cover that comes mid-fade, the playing one while a library cover fades in (play
 * starting, or the wheel onto Now Playing) or a library one while the playing cover fades in (the
 * wheel back off it), fades in over the frame as it stands (frame_hold), the two covers there still
 * from then on; a cut ends the fade first. The playing cover takes the one showing's phase when
 * that was the playing cover too, so a new track's pan goes on where it was. */
static int slide_show(const char *url, int lib, int cut) {
    if (home.fade && !cut && !lib && !home.sl[1].lib) {
        int moves = slide_load(&home.sl[1], url, 0, home.sl[1].ms);
        if (moves <= 0) home.fade = 0; /* none to fade in: the one showing stays */
        home.drawn = 0;
        return moves;
    }
    if (home.fade && (cut || !frame_hold())) fade_end();
    int over = !cut && home.sl[0].tw;
    unsigned ms = !lib && !home.sl[0].lib ? home.sl[0].ms : 0;
    int moves = slide_load(&home.sl[over], url, lib, ms);
    if (moves <= 0) return moves;
    if (over)
        home.fade = 1;
    else
        art_frame();
    home.drawn = 0;
    return 1;
}
extern int slide_pick(char *url, int take); /* slideshow.c */
/* The slideshow's next cover, once the one showing is due to fade out (at once if it is not a
 * library cover, or the playing cover fades in over it), fetched SLIDE_AHEAD_MS before. */
static void slide_next(void) {
    const slide_t *s = &home.sl[0];
    unsigned end = SLIDE_FADE_MS + SLIDE_HOLD_MS;
    int due = !s->tw || !s->lib || s->ms >= end || (home.fade && !home.sl[1].lib);
    char url[600];
    if ((due || s->ms + SLIDE_AHEAD_MS >= end) && slide_pick(url, due)) {
        if (!strcmp(url + 7, PEQ_ROOT "/tmp/q2-slide.jpg"))
            image_manager_unload_bitmap_by_name(image_manager(), url); /* the same file each time */
        slide_show(url, 1, 0);
    }
}

/* What the pane shows (docs/ipod.md#home), as an iPod classic's main menu: the playing cover on a
 * Now Playing row, playing or paused, the library's covers in turn on every other row. */
/* Radio the owner of Now Playing, playing or paused (radio_tuner.c tuner_state): its pane
 * (radio_pane.c, approved 2026-11-20, design/radio-tuner/radio-now-playing/pane-v6, K1); else the
 * row's: the playing cover, or the library's in turn. */
static int art_source(void) {
    return tuner_state() ? SHOW_RADIO : home.kind == PANE_LIBRARY ? SHOW_LIB : SHOW_NOW;
}
/* Radio's pane may move: Home on top and not sliding, the pane shown, the screen lit. */
static int radio_live(void) {
    void *wm = window_manager();
    return home.show == SHOW_RADIO && widget_get_visible(home.art) && g_backlight_status &&
           !g_lockscreen_pageflag && window_manager_get_top_window(wm) == home.win &&
           !window_manager_is_animating(wm);
}
/* Radio's pane's frame tick (RADIO_TICK_MS) while it moves (radio_pane.c rp_moving: the way in, or
 * rings leaving while Radio plays) and shows: a repaint of the pane alone; then it stops, and
 * coverflow_home_art arms it again. */
#define RADIO_TICK_MS 33
static int radio_tick(const void *unused) {
    if (!radio_live() || !rp_moving()) return (int)(home.rtick = 0); /* RET_REMOVE */
    pane_invalidate();
    return 8; /* RET_REPEAT */
}
/* The pane's frame for Radio, drawn into the frame bitmap, then the tower and the frequency. */
static void home_radio_paint(void *canvas) {
    if (!home.frame) home.frame = opaque_frame(pane_w(), pane_h(), 1);
    unsigned *d = home.frame ? (unsigned *)bitmap_lock_buffer_for_write(home.frame) : 0;
    if (!d) return;
    rp_words_t k;
    int ok = rp_frame(d, (int)(bitmap_get_line_length(home.frame) / 4), pane_w(), pane_h(),
                      pane_y(), (unsigned)time_now_ms(), &k);
    bitmap_unlock_buffer(home.frame);
    home.drawn = 0; /* the covers' frame is gone: drawn again when they come back */
    if (!ok) return;
    int src[4] = { 0, 0, pane_w(), pane_h() }, dst[4] = { 0, 0, src[2], src[3] };
    canvas_draw_image(canvas, home.frame, src, dst);
    rp_words(home.art, canvas, &k);
}

/* The player's cover, else the Coverflow cache of the track's album (now_tag), else the theme's
 * pane or the placeholder; or under the slideshow (SHOW_LIB) the library's covers, which
 * home_drift brings in turn, that track's art showing until the first comes. The player's files
 * belong to the track whose path it copies to g_lastcover_url after writing them, so right after
 * a track change they count only once that is this track. Runs whenever a window or the status
 * bar paints (at least once a second), for Home in Split, and reloads only when what shows, the
 * track, the cover it can use or its parsed tags change: a new cover that moves fades in over the
 * one showing, and a still one cuts; home_repaint then shows the new art whole. */
void coverflow_home_art(void *top) {
    if (!home.art || top != home.win || !widget_get_visible(home.art)) return;
    int show = art_source(), changed = show != home.show, was = home.show;
    home.show = show;
    if (show == SHOW_RADIO) { /* no covers, no pan: Radio's pane, sliding in over the last cover */
        unsigned key = (unsigned)tuner_dial() ^ (unsigned)tuner_state() << 20;
        if (changed) {
            stop_timer(&home.drift);
            const unsigned *cover = home.frame && home.drawn
                                        ? (const unsigned *)bitmap_lock_buffer_for_read(home.frame)
                                        : 0;
            rp_enter(cover, pane_w(), pane_h());
            if (cover) bitmap_unlock_buffer(home.frame);
            slides_drop(), art_frame();
        }
        if (!home.rtick && radio_live() && rp_moving())
            home.rtick = timer_add(radio_tick, (void *)0, RADIO_TICK_MS);
        if (changed || key != home.key) home.key = key, pane_invalidate();
        return;
    }
    if (was == SHOW_RADIO) stop_timer(&home.rtick), rp_free(); /* the Library's again */
    if (!home.drift && drift_live()) {
        home.drift_last = time_now_ms();
        home.tick = DRIFT_TICK_MS;
        home.drift = timer_add(home_drift, (void *)0, DRIFT_TICK_MS);
    }
    unsigned pos, n;
    void *r = queue_now(&pos, &n);
    const char *path = r ? P(r, REC_PATH) : (void *)0;
    const char *cover;
    unsigned char type = now_cover(r, &cover);
    unsigned album = tags_key(now_tag(r, REC_ARTIST), now_tag(r, REC_ALBUM));
    unsigned key = hash_bytes(hash_bytes(fnv(FNV_SEED, (const unsigned char *)path), &type, 1),
                              (const unsigned char *)&album, sizeof(album)) ^
                   now_cover_written(cover);
    if (key == home.key && !changed) return;
    int cut = home.key == ~0u; /* coverflow_home_layout's: the same cover, laid out again */
    home.key = key;
    /* the show goes on over a track change, and takes over from any cover that moves */
    if (show == SHOW_LIB && home.sl[0].tw) return;
    char url[600] = "file://";
    if (r) art_path(url + 7, album, "");
    unsigned size[2] = { 0, 0 };
    int ch = client_h(), fw = pane_w(), fh = ch;
    const char *src = cover;
    /* the player writes every track's cover to the same file: drop AWTK's cached bitmap of the
     * last track's, or the new track shows it (Enlarged Art does the same) */
    if (cover) image_manager_unload_bitmap_by_name(image_manager(), cover);
    int moves = cover ? slide_show(cover, 0, cut) : -1;
    if (moves < 0 && r) moves = slide_show(url, 0, cut), src = url;
    /* no cover: the placeholder as the pane's slide, so it runs up under the status row as a cover
     * does */
    if (moves < 0) moves = slide_show(HOME_PLACEHOLDER, 0, cut), src = HOME_PLACEHOLDER;
    if (moves > 0) return;
    slides_drop();
    int shown = moves == 0 && image_show(home.art, src, size);
    /* still, sized to the image's fit and centred under the status row, so the native fill draws it
     * whole and the clip crops it evenly */
    if (!shown && !image_show(home.art, HOME_PLACEHOLDER, size))
        image_base_set_image(home.art, HOME_PLACEHOLDER);
    home_fit(size[0], size[1], ch, &fw, &fh);
    widget_move_resize(home.art, pane_x() + (pane_w() - fw) / 2, 320 - ch + (ch - fh) / 2, fw, fh);
    rearm(&home.repaint, home_repaint, 0);
}

/* The status row's glyphs over Split's art (docs/ipod.md#home), as Scrolly's feed-end line inks
 * its own: each glyph in one ink, white or black, whichever has the higher WCAG contrast against
 * the art's relative luminance L under it, averaged over the glyph and weighted by its coverage.
 * White wins while (L + 0.05)^2 <= 1.05 x 0.05, so up to an L of sqrt(0.0525) - 0.05. srgb_lin
 * is each sRGB byte's linear value in 1/65535; L is summed in 1/(10000 x 65535), so INK_WHITE_MAX
 * is that bound at the same scale. */
static const unsigned short srgb_lin[256] = {
    0,     20,    40,    60,    80,    99,    119,   139,   159,   179,   199,   219,   241,
    264,   288,   313,   340,   367,   396,   427,   458,   491,   526,   562,   599,   637,
    677,   718,   761,   805,   851,   898,   947,   997,   1048,  1101,  1156,  1212,  1270,
    1330,  1391,  1453,  1517,  1583,  1651,  1720,  1790,  1863,  1937,  2013,  2090,  2170,
    2250,  2333,  2418,  2504,  2592,  2681,  2773,  2866,  2961,  3058,  3157,  3258,  3360,
    3464,  3570,  3678,  3788,  3900,  4014,  4129,  4247,  4366,  4488,  4611,  4736,  4864,
    4993,  5124,  5257,  5392,  5530,  5669,  5810,  5953,  6099,  6246,  6395,  6547,  6700,
    6856,  7014,  7174,  7335,  7500,  7666,  7834,  8004,  8177,  8352,  8528,  8708,  8889,
    9072,  9258,  9445,  9635,  9828,  10022, 10219, 10417, 10619, 10822, 11028, 11235, 11446,
    11658, 11873, 12090, 12309, 12530, 12754, 12980, 13209, 13440, 13673, 13909, 14146, 14387,
    14629, 14874, 15122, 15371, 15623, 15878, 16135, 16394, 16656, 16920, 17187, 17456, 17727,
    18001, 18277, 18556, 18837, 19121, 19407, 19696, 19987, 20281, 20577, 20876, 21177, 21481,
    21787, 22096, 22407, 22721, 23038, 23357, 23678, 24002, 24329, 24658, 24990, 25325, 25662,
    26001, 26344, 26688, 27036, 27386, 27739, 28094, 28452, 28813, 29176, 29542, 29911, 30282,
    30656, 31033, 31412, 31794, 32179, 32567, 32957, 33350, 33745, 34143, 34544, 34948, 35355,
    35764, 36176, 36591, 37008, 37429, 37852, 38278, 38706, 39138, 39572, 40009, 40449, 40891,
    41337, 41785, 42236, 42690, 43147, 43606, 44069, 44534, 45002, 45473, 45947, 46423, 46903,
    47385, 47871, 48359, 48850, 49344, 49841, 50341, 50844, 51349, 51858, 52369, 52884, 53401,
    53921, 54445, 54971, 55500, 56032, 56567, 57105, 57646, 58190, 58737, 59287, 59840, 60396,
    60955, 61517, 62082, 62650, 63221, 63795, 64372, 64952, 65535
};
#define INK_WHITE_MAX 117392049u
/* The row keeps its ink until the mean crosses the bound by 0.03 of L, so a pan easing over a
 * mean near it doesn't flicker (docs/ipod.md#home has the measure); between L 0.149 and 0.209
 * either ink keeps at least 3.98:1. */
#define INK_HOLD 19660500u
/* Glyphs are the bar's columns with ink, parted by at least INK_GAP empty ones (a pause's bars,
 * a battery's body and nub, are one glyph; status_row.c SR_GAP and more part two): each finds its
 * own status colour, though the row takes one ink. */
#define INK_GAP 4
/* The most glyphs a row holds: the pane is at most 179 columns, a glyph and its gap 5. */
#define INK_GLYPHS 40
/* 1: a coloured pixel (the slim battery's charging green or low red) keeps its own colour over
 * the art, and only the glyph's grey part is inked; 0: it is inked with the rest. */
#define INK_KEEP_STATUS_COLOURS 1
/* A colour's spread (greatest channel less least) that marks it a status colour, not grey. */
#define INK_HUE_MIN 48
#define INK_CUT_LEFT 1
#define INK_CUT_RIGHT 2
#define INK_CUT_ROWS 4

/* Whether p is not the ground's colour: its share (ink_share) is 0 just where it is. One compare,
 * not the share's three channels, for most of the bar's rows, the bare art (test/patch.py's ink
 * count). */
#define INKED(p, ground) ((((p) ^ (ground)) & 0xffffffu) != 0)
/* p's greatest channel's distance from ground, and its spread. */
static unsigned ink_share(unsigned p, unsigned ground, unsigned *spread) {
    unsigned c = 0, hi = 0, lo = 255;
    for (int s = 0; s < 24; s += 8) {
        int d = (int)(p >> s & 255) - (int)(ground >> s & 255);
        unsigned e = (unsigned)(d < 0 ? -d : d), v = p >> s & 255;
        c = e > c ? e : c;
        hi = v > hi ? v : hi, lo = v < lo ? v : lo;
    }
    *spread = hi - lo;
    return c;
}

/* A glyph: its columns [x, end), its status colour's pixel, spread and share (spread 0: none). */
typedef struct {
    int x, end;
    unsigned hue, hue_s, hue_c;
} ink_glyph_t;

/* The bar's rows over the pane, w x h at fb (the screen's BGRA8888, pitch words a row), as its
 * background and glyphs left them on the flat ground (BGRA): each pixel becomes the art's (art,
 * the frame's RGBA8888, apitch words a row), and every glyph, the clock's and the status slots'
 * alike, is laid over it in one contrast ink for the whole row, as iOS's status bar takes one
 * style: white or black by the art's mean L under all of their inked pixels together, each pixel
 * weighed by its share, its greatest channel's distance from the ground (an antialiased edge, or
 * the slim battery's dim outline, a part), so each keeps its shape and smooth edges. held has a
 * byte a column, the row's ink when it was last picked (1 white, 2 black, 0 none): it is kept
 * until INK_HOLD past the bound. cut (INK_CUT_*) says the canvas's clip, not the pane, ends the
 * rows there: a repaint of part of the row (the play state's 8 frames a second, a battery's
 * level) shows too little of it to pick from, so with an ink held it keeps it. A status colour
 * stays itself (INK_KEEP_STATUS_COLOURS), its share read against its own full colour, its
 * glyph's most saturated pixel. test/fw/test_coverflow.py */
void home_ink(unsigned *fb, int pitch, const unsigned *art, int apitch, int w, int h,
              unsigned ground, unsigned char *held, int cut) {
    ink_glyph_t g[INK_GLYPHS];
    int n = 0;
    unsigned sp;
    for (int x = 0; x < w;) { /* the glyphs: each on to INK_GAP empty columns */
        int lit = 0, b = x, gap = 0;
        for (int y = 0; y < h && !lit; ++y) lit = INKED(fb[y * pitch + x], ground);
        if (!lit) {
            ++x;
            continue;
        }
        for (b = x + 1; b < w && (gap < INK_GAP || n == INK_GLYPHS - 1); ++b) {
            int on = 0;
            for (int y = 0; y < h && !on; ++y) on = INKED(fb[y * pitch + b], ground);
            gap = on ? 0 : gap + 1;
        }
        ink_glyph_t *k = &g[n++];
        k->x = x, k->end = b - gap, k->hue = k->hue_s = k->hue_c = 0;
        for (int y = 0; INK_KEEP_STATUS_COLOURS && y < h; ++y) /* the most saturated pixel */
            for (int i = x; i < k->end; ++i) {
                unsigned p = fb[y * pitch + i], c = ink_share(p, ground, &sp);
                if (sp > k->hue_s) k->hue = p, k->hue_s = sp, k->hue_c = c;
            }
        if (k->hue_s < INK_HUE_MIN) k->hue_s = 0;
        x = k->end;
    }
    /* the art's L under every glyph's inked share, less its status colour's */
    unsigned long long sum = 0;
    unsigned weight = 0; /* at most 255 a pixel */
    for (ink_glyph_t *k = g; k < g + n; ++k)
        for (int y = 0; y < h; ++y)
            for (int i = k->x; i < k->end; ++i) {
                if (!INKED(fb[y * pitch + i], ground)) continue; /* no share: adds nothing */
                unsigned c = ink_share(fb[y * pitch + i], ground, &sp), a = art[y * apitch + i];
                if (k->hue_s) {
                    unsigned q = sp >= k->hue_s ? 256 : (sp << 8) / k->hue_s, t = k->hue_c * q >> 8;
                    c = c > t ? c - t : 0;
                }
                sum += (unsigned long long)c *
                       (722u * srgb_lin[a >> 16 & 255] + 7152u * srgb_lin[a >> 8 & 255] +
                        2126u * srgb_lin[a & 255]);
                weight += c;
            }
    unsigned was = 0, ink = 0;
    for (int i = 0; i < w && !was; ++i) was = held[i];
    if (was && cut) /* part of the row: it keeps the ink it has */
        ink = was;
    else if (weight) {
        unsigned bound = was == 1   ? INK_WHITE_MAX + INK_HOLD
                         : was == 2 ? INK_WHITE_MAX - INK_HOLD
                                    : INK_WHITE_MAX;
        ink = sum <= (unsigned long long)weight * bound ? 1 : 2;
    }
    if (ink)
        for (int i = 0; i < w; ++i) held[i] = (unsigned char)ink;
    for (int y = 0; y < h; ++y) /* the art, and the glyphs on it */
        for (int i = 0, k = 0; i < w; ++i) {
            while (k < n && i >= g[k].end) ++k;
            unsigned *p = fb + y * pitch + i, a = bgra(art[y * apitch + i]),
                     c = k < n && i >= g[k].x && INKED(*p, ground) ? ink_share(*p, ground, &sp) : 0;
            if (c && g[k].hue_s) {
                unsigned q = sp >= g[k].hue_s ? 256 : (sp << 8) / g[k].hue_s,
                         t = g[k].hue_c * q >> 8;
                a = lerp(a, g[k].hue, q);
                c = c > t ? c - t : 0;
            }
            if (c && ink) a = lerp(a, ink == 1 ? 0xffffffffu : 0xff000000u, c + (c >> 7));
            *p = a;
        }
}

/* The screen's buffer while canvas draws straight into it (nightshift.c's warm reads it the same
 * way: 375 words a row, BGRA8888), else 0. */
static unsigned *screen_fb(void *canvas) {
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd || I(lcd, LCD_DRAW_MODE) != LCD_DRAW_NORMAL || I(lcd, LCD_FORMAT) != LCD_BGRA8888 ||
        I(lcd, LCD_W) != 375 || I(lcd, LCD_H) != 320 || I(lcd, LCD_LINE_LENGTH) != 375 * 4)
        return 0;
    return P(lcd, LCD_OFFLINE_FB);
}

/* The pane's rows in the bar that canvas's clip lets through, x y w h on screen; 0 for none. */
static int bar_rows(void *canvas, int *r) {
    int old[4];
    return clip_within(canvas, old, r, pane_x(), pane_y(), pane_w(), pane_up());
}

/* Whether top is Home in Split, its art beside the list (1), and 2 when the art runs up under the
 * status row (a split_art_up and a slide that fills the pane: a cover, or the placeholder with
 * none), where the bar's glyphs are inked
 * over it; navigation.c's clock_place puts the clock at the row's left, over the list, either way. */
int coverflow_home_split(void *top) {
    if (!home.art || top != home.win || !widget_get_visible(home.art)) return 0;
    return pane_up() && home.sl[0].tw ? 2 : 1;
}

/* page_parallax.c: the page under a window's slide */
extern void *parallax_slide(float *percent);

/* Whether Home in Split is the page under a window's slide (page_parallax.c), its band, the rows
 * under the status row, in sight left of the sliding page: *open the columns from the left that
 * the sliding page does not cover yet, else the screen's width. The bar's title then shows only
 * over the sliding page (theme_titles_paint.c bar_title_fades). */
int coverflow_home_slide(int *open) {
    float percent = 0;
    *open = 375;
    void *under = window_manager_is_animating(window_manager()) ? parallax_slide(&percent) : 0;
    if (!under || under != home.win) return 0;
    int w = I(under, W_W);
    *open = w - (int)(w * percent + 0.5f);
    return *open > 0 && coverflow_home_split(under);
}

/* The bar's background (ringnav_paint_bg), with Home on top and still: the pane's rows under the
 * status row, which Home's own paint has drawn, laid with the bar's flat ground (BGRA) again
 * where the canvas draws on the screen itself, so the bar's glyphs paint on a known ground and its
 * border paint (coverflow_home_clip) puts the art back there with them inked by contrast
 * (home_ink). A set with no split_art_up has no such rows. Elsewhere, under a window's slide too,
 * the art stays as Home painted it (ponytail: the glyphs keep the bar's ink, not home_ink's, until
 * Home settles). */
void coverflow_home_bar(void *canvas, void *top, unsigned ground) {
    int r[4];
    void *lcd = P(canvas, CANVAS_LCD);
    home.ink_bar = 0;
    if (top != home.win || window_manager_is_animating(window_manager()) || !lcd ||
        coverflow_home_split(top) != 2 || !home_frame())
        return;
    unsigned *fb = screen_fb(canvas), fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    if (!fb || !bar_rows(canvas, r)) return;
    stock_canvas_set_fill_color(canvas, ground);
    canvas_fill_rect(canvas, r[0] - I(canvas, CANVAS_X), r[1] - I(canvas, CANVAS_Y), r[2], r[3]);
    stock_canvas_set_fill_color(canvas, fill);
    home.ground = fb[r[1] * 375 + r[0]]; /* as the screen holds it, no glyph yet */
    home.ink_bar = *(void *const *)system_bar;
}

/* The bar's border paint, its glyphs drawn: the art into the rows coverflow_home_bar left, the
 * glyphs over it inked by contrast. Only the pixels inside the clip, at most the pane's rows. */
static void home_ink_bar(void *canvas) {
    int r[4], top = pane_y(), pw = pane_w();
    unsigned *fb = screen_fb(canvas);
    home.ink_bar = 0;
    if (!fb || !bar_rows(canvas, r)) return;
    const unsigned *art = (const unsigned *)bitmap_lock_buffer_for_read(home.frame);
    if (!art) return;
    int cut = (r[0] > pane_x() ? INK_CUT_LEFT : 0) | (r[0] + r[2] < 375 ? INK_CUT_RIGHT : 0) |
              (r[3] < pane_up() ? INK_CUT_ROWS : 0);
    home_ink(fb + r[1] * 375 + r[0], 375, art + (r[1] - top) * pw + (r[0] - pane_x()), pw, r[2],
             r[3], home.ground, home.held + (r[0] - pane_x()), cut);
    bitmap_unlock_buffer(home.frame);
}

/* The art paints only inside its panel and the lists only left of it, so a sliding list
 * never covers the art: ringnav_paint_bg narrows the canvas clip (screen coordinates; the canvas
 * origin is the widget's) before stock draws it, and ringnav_paint puts the old clip back after its
 * children, the pan's frame painted inside it first. */
void coverflow_home_clip(void *w, void *canvas, int begin) {
    if (w == home.ink_bar && !begin && w) {
        home_ink_bar(canvas);
        return;
    }
    if (!w ||
        (w != home.art && w != home.list && w != home.sets && w != home.lib && w != home.stream))
        return;
    if (!begin) {
        if (w == home.art && home.show == SHOW_RADIO)
            home_radio_paint(canvas);
        else if (w == home.art && home.sl[0].tw)
            home_paint(canvas);
        if (w == home.list) {
            int split = widget_get_visible(home.art);
            home_fades(canvas, w, I(canvas, CANVAS_Y), split ? pane_x() : 375, split, 0);
        }
        if (home.clipped) canvas_set_clip_rect(canvas, home.clip);
        home.clipped = 0;
        return;
    }
    if (w == home.list && widget_count_children(w)) home_pad(widget_get_child(w, 0));
    int clip[4], art = w == home.art, x = pane_x(), pane = widget_get_visible(home.art) ? x : 375;
    clip_within(canvas, home.clip, clip, I(canvas, CANVAS_X) - I(w, W_X) + (art ? x : 0),
                I(canvas, CANVAS_Y) - I(w, W_Y) + (art ? pane_y() : I(w, W_Y)), art ? 375 - x : pane,
                art ? pane_h() : I(w, W_H));
    canvas_set_clip_rect(canvas, clip);
    home.clipped = 1;
}

/* Whether w is in one of Home's menus. */
int coverflow_home_owns(void *w) {
    for (; w && home.list; w = P(w, W_PARENT))
        if (w == home.list || w == home.sets || w == home.lib || w == home.stream) return 1;
    return 0;
}

/* The list open on Home, 0: its main list. */
static void *home_open(void) {
    return home.lib && widget_get_visible(home.lib)         ? home.lib
           : home.stream && widget_get_visible(home.stream) ? home.stream
           : home.sets && widget_get_visible(home.sets)     ? home.sets
                                                            : (void *)0;
}

/* Home's main list's scroll view (home.c home_rest), 0 before Home is made. */
void *coverflow_home_view(void) {
    return home.list && widget_count_children(home.list) ? widget_get_child(home.list, 0)
                                                         : (void *)0;
}

/* Home's main list's view when top is Home showing it (not a list its rows open, not Carousel):
 * what Edit Home edits (library_edit.c), else 0. */
void *coverflow_home_edit_view(void *top) {
    return top && top == home.win && !home_open() && ipod_home() != 2 &&
                   widget_get_visible(home.list)
               ? coverflow_home_view()
               : (void *)0;
}

/* Split under a home_lists_full theme (OLED's): the lists Home's rows open take the whole width,
 * as a page does, and the art is the main menu's alone. Without it, an iPod's submenu: the list
 * in the main menu's place beside the art. */
static int sub_full(void) { return theme()->home_lists_full && !ipod_home(); }

/* The art beside shown, the list now on Home, under sub_full: there only beside the main list. */
static void art_beside(void *shown) {
    if (sub_full()) widget_set_visible(home.art, shown == home.list, 0);
}

/* Both menus' lists span outer, their rows and tap targets inner; labels start at x and end end
 * before the row's edge. */
/* Split's list top on the screen where the art runs up under the status row (a split_art_up):
 * the list rises to just under the clock at the status row's left (navigation.c clock_place), its
 * top rows under the row's overlay. */
#define HOME_SPLIT_TOP 40
/* w, one of Home's lists, and its rows: outer wide, the rows inner, their labels from x to end
 * short of the row; the list home.rows.top under the status row's foot, Split's main menu at
 * HOME_SPLIT_TOP. Home's window is the screen (docs/oled-design.md#full-bleed). */
static void home_width(void *w, int outer, int inner, int x, int end, int depth) {
    int top = w == home.list && pane_up() && !ipod_home() ? HOME_SPLIT_TOP
                                                          : 320 - client_h() + theme()->home_top;
    widget_move_resize(w, I(w, W_X), depth ? I(w, W_Y) : top, depth < 2 ? outer : inner, I(w, W_H));
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *child = widget_get_child(w, i);
        if (!tk_strcmp(widget_get_type(child), "hscroll_label")) {
            widget_move_resize(child, x, I(child, W_Y), inner - x - end, I(child, W_H));
            theme_font(child, ROLE_HOME, -1);
        } else
            home_width(child, outer, inner, x, end, depth + 1);
    }
}

/* Shown, or hidden and taking no taps: hidden widgets keep theirs. */
static void home_show(void *w, int on) {
    widget_set_visible(w, on, 0);
    widget_set_sensitive(w, on);
}

/* Home's Now Playing row (docs/ipod.md#home), as an iPod's main menu has it: there while
 * something is loaded, playing or paused, and gone once play has stopped for NP_GONE_MS, so the
 * moment between two tracks keeps it. Once stopped with the row shown, np_due looks again when
 * that time is up, as nothing may paint then. */
#define NP_GONE_MS 3000
void coverflow_home_np(void), coverflow_home_layout(void);
static int np_due(const void *unused) {
    home.np_timer = 0;
    coverflow_home_np();
    return 0; /* RET_REMOVE */
}
static int np_loaded(void) {
    int s = media_status();
    if (s == 2 || s == 3) return (int)(home.np_stop = 0) + 1;
    unsigned now = time_now_ms();
    if (!home.np_stop) home.np_stop = now | 1;
    int left = NP_GONE_MS - (int)(now - home.np_stop); /* np_stop may be now + 1 */
    if (home.np && left > 0 && !home.np_timer)
        home.np_timer = timer_add(np_due, (void *)0, (unsigned)left);
    return home.np && left > 0;
}
/* Every bar paint (navigation.c bar_sync): Home's first row follows the play state (home_np.c),
 * and Home is laid out again once Now Playing's row is to come or go, or, with Home on top,
 * Folders' rule changes (home.c home_folders). */
void coverflow_home_np(void) {
    home_np_sync(); /* its first row's state (home_np.c) */
    if (!home.list) return;
    int top = window_manager_get_top_window(window_manager()) == home.win;
    if (np_loaded() != home.np || (top && home_folders() != home.folders)) coverflow_home_layout();
}
/* Stored HOME=0/1/2 is Split/Full/Carousel in every theme: Split halves the screen, the art in the
 * right panel; Full hides the art and widens the list, its rows to the theme's home_full_row;
 * Carousel hides the art and shows stock's cards (tools/ipod.py ipod_home) in the
 * lists' place, and leaving it, the main list. The main list's rows go in the user's order and
 * show as the user set them (library_edit.c lr_home_apply: Now Playing only with something loaded,
 * Folders by default only while the card has folders); hidden rows are restacked last because the
 * native layout includes hidden rows, and made insensitive, as hidden rows keep their taps. Edit
 * Home lays Split out as Full (theme_engine.c ipod_home). Carousel keeps its Now Playing card, as
 * stock's. */
void coverflow_home_layout(void) {
    int mode = ipod_home(), full = mode == 1, carousel = mode == 2;
    const theme_t *T = theme();
    /* The art goes back to the panel; Split's next paint resumes the pan. */
    stop_timer(&home.drift);
    if (!home.list) return;
    int np = np_loaded();
    home.folders = home_folders();
    lr_home_apply(coverflow_home_view(), np); /* the user's order and rows, Now Playing's with np */
    home.np = np;
    /* ponytail: the Shortcut is the carousel's card alone (Home's list has none); the card stays
     * when its target is gone (Shortcut: Rockbox, no Rockbox on the card; its tap says so), as the
     * slide_menu lays out hidden cards too; removing it would mean re-adding it once the card has
     * Rockbox */
    ipod_shortcut_seen();
    if (home.carousel[0])
        widget_set_text_utf8(widget_lookup(home.carousel[0], "label_rockbox", 1),
                             ipod_home_shortcut());
    int split = split_geom(SPLIT_ROW), outer = full ? 375 : split,
        inner = !full ? split : T->home_full_row, x = T->home_text_x, end = T->home_label_end[full];
    home_width(home.list, outer, inner, x, end, 0);
    lr_edit_labels(coverflow_home_view(), lr_home_editing()); /* Edit Home's words, past circles */
    home_overflow(home.list, 320);
    if (home.sets)
        home_width(home.sets, sub_full() ? 375 : outer, sub_full() ? T->home_full_row : inner, x,
                   T->home_label_end[full || sub_full()], 0);
    widget_move_resize(home.art, pane_x(), pane_y(), pane_w(), pane_h());
    widget_set_visible(home.art, !mode && !(sub_full() && home_open()), 0);
    for (int i = 0; mode && i < 2; ++i) { /* Split's alone */
        void *open = i ? home.stream : home.lib;
        if (!open || !widget_get_visible(open)) continue;
        home_show(open, 0);
        home_show(home.list, 1);
    }
    if (home.carousel[0] && carousel != widget_get_visible(home.carousel[0])) {
        for (int i = 0; i < 3; ++i) home_show(home.carousel[i], carousel);
        home_show(home.list, !carousel);
        if (home.sets) home_show(home.sets, 0);
    }
    home.key = ~0u; /* Split shows the current cover again, next frame if laid out mid-paint */
    rearm(&home.repaint, home_repaint, 0);
}

/* w from x+dx to its x over m's ms and easing. Returns 0 without touching w when m->ms is 0;
 * returns 0 with w back at its x when no animator can be made; else 1. */
static int push_x(void *w, int dx, const motion_t *m) {
    if (!m->ms) return 0;
    int x = I(w, W_X);
    widget_move_resize(w, x + dx, I(w, W_Y), I(w, W_W), I(w, W_H));
    void *a = widget_animator_prop_create(w, m->ms, 0, m->easing, "x");
    if (!a) {
        widget_move_resize(w, x, I(w, W_Y), I(w, W_W), I(w, W_H));
        return 0;
    }
    widget_animator_prop_set_params(a, x + dx, x);
    widget_animator_start(a);
    return 1;
}

/* After page_list rebuilt a level: its list (view's list_view parent; the title is the bar's)
 * slides in from the right (dir 1, deeper) or the left (-1, back) over the theme's inplace motion.
 * No window animates, so keys and the wheel act on the new list at once. 0 for a null view or dir
 * 0, when the theme keeps levels still (push_x touches nothing), or when no animator could be made:
 * the list is at x 0. */
int page_push(void *view, int dir) {
    if (!motion_row(MOTION_INPLACE)->ms) return 0;
    void *w = view ? P(view, W_PARENT) : (void *)0;
    if (!w || !dir) return 0;
    /* A kept level (a folder's table) still sliding goes to its rest first. */
    if (I(w, W_X)) widget_move_resize(w, 0, I(w, W_Y), I(w, W_W), I(w, W_H));
    return push_x(w, dir * I(w, W_W), motion_row(MOTION_INPLACE));
}

/* Home's Settings row, as an iPod's submenu: the Settings list (stock's Playback and System
 * setting rows, which stock binds) slides in from the right over the list's place, the art staying
 * (under sub_full the list is a page's width and the art goes); Return (coverflow_home_back) slides
 * the main list back in from the left. Each list keeps its row. */
static void home_swap(void *from, void *to, int dx) {
    home_show(from, 0); /* only the shown list takes taps */
    /* A list still sliding goes to its rest first: push_x's goal, and where no push leaves it. */
    if (I(to, W_X)) widget_move_resize(to, 0, I(to, W_Y), I(to, W_W), I(to, W_H));
    push_x(to, dx, motion_row(MOTION_SWAP));
    home_show(to, 1);
    art_beside(to);
    bar_title_sync(home.win); /* the bar's text follows the list at once, not at its next second */
}

static int home_settings(void *ctx, void *event) {
    if (home.sets && !widget_get_visible(home.sets))
        home_swap(home.list, home.sets, I(home.sets, W_W));
    return 0;
}

/* A list its page's constructor just filled, slid in over from in Home's rows' shape (under
 * sub_full, Full's rows across the screen). service: Streaming's, whose 70 px rows take Home's row
 * height, and the build's icons are decoded now, not on the wheel's path (coverflow_home_row). */
static void home_fill(void *from, void *list, int service) {
    const theme_t *T = theme();
    int full = sub_full(), split = full ? T->home_full_row : split_geom(SPLIT_ROW);
    home_width(list, full ? 375 : split, split, T->home_text_x, T->home_label_end[full], 0);
    void *view = widget_count_children(list) ? widget_get_child(list, 0) : (void *)0;
    void *first = service ? widget_get_child(widget_get_child(home.list, 0), 0) : (void *)0;
    for (unsigned i = 0, n = view ? widget_count_children(view) : 0; i < n; ++i) {
        void *row = widget_get_child(view, i);
        void *b = widget_count_children(row) ? widget_get_child(row, 0) : (void *)0;
        if (!b) continue;
        int h = service ? I(first, W_H) : I(b, W_H);
        widget_move_resize(b, 0, service ? 0 : I(b, W_Y), split, h);
    }
    widget_layout(list);
    home_swap(from, list, I(list, W_W));
}

/* Home's Library row in Split (a set with home_lib): its rows slide in as Settings' do, each filled
 * by the page's own constructor (home.c library_rows) in Home's rows' shape, and open what
 * they open from the page. Full and Carousel keep the page. */
static int home_library(void *ctx, void *event) {
    if (ipod_home() || !home.lib) return on_home_music_onclick(ctx, event);
    if (widget_get_visible(home.lib)) return 0;
    library_rows(home.lib);
    home_fill(home.list, home.lib, 0);
    return 0;
}

/* The Settings list's Streaming row (after Playback, tools/ipod.py HOME_SETS), the same way over
 * the Settings list (home.c stream_rows); Return goes back to Settings (coverflow_home_back). The
 * rebuild frees the rows, so the highlighted row goes first. Full and Carousel keep the page. */
static int home_streaming(void *ctx, void *event) {
    if (ipod_home() || !home.stream) return on_home_stream_onclick(ctx, event);
    if (widget_get_visible(home.stream)) return 0;
    home.row = (void *)0;
    stream_rows(home.stream);
    home_fill(home.sets, home.stream, 1);
    return 0;
}

/* Return on Home with a list open: 1 when it went back to the main list. A reopen_top theme's list
 * opens at its top next time (navigation.c forget_rows), as a page left by Return does. */
int coverflow_home_back(void *top) {
    void *open = top == home.win ? home_open() : (void *)0;
    if (!open) return 0;
    if (theme()->reopen_top) forget_rows(open);
    void *back = open == home.stream && home.sets ? home.sets : home.list; /* Streaming: Settings' */
    home_swap(open, back, -I(back, W_W));
    return 1;
}

/* The list one of Home's rows opened while top is Home, which Return leaves (swipe_back.c); else 0. */
void *coverflow_home_sublist(void *top) { return top && top == home.win ? home_open() : (void *)0; }
/* Home's art pane, which shows the selected row's cover (art_zoom.c: Now Playing grows from it) */
void *coverflow_home_pane(void) { return home.art; }

/* For art_zoom.c's card (docs/internals.md#now-playing-motion): the playing cover the pane shows,
 * its texture (RGBA8888, texel 0, 0 at *tex + tw + 4, rows tw + 3 words, a texel of border round
 * it, as slide_load makes it), its size and the pane's window now (1/256 texel, a texel a pixel); 0 when
 * the pane shows no playing cover of its own (a library cover, the still, a fade). */
int coverflow_home_cover(const unsigned **tex, int *tw, int *th, int *u, int *v) {
    const slide_t *s = &home.sl[0];
    int k;
    if (!s->tex || !s->tw || s->lib || home.fade) return 0;
    slide_at(s, u, v, &k);
    *tex = s->tex, *tw = s->tw, *th = s->th;
    return 1;
}

/* The pane's vignette (vig_top, vig_bot) for row y of a card h px tall, in 256ths: the pane's own
 * rows at the pane's height, its top and bottom fades kept at their heights on a shorter card. */
unsigned coverflow_home_vig(int y, int h) {
    unsigned k = 256;
    if (y >= 0 && y < VIG_TOP) k = vig_top[y];
    int b = h - 1 - y;
    if (b >= 0 && b < VIG_BOT && vig_bot[b] < k) k = vig_bot[b];
    return k;
}

/* 1 while Home shows a list of its rows' (the Library's, Streaming's, Settings'), not its own. */
int coverflow_home_sub(void) { return home_open() != (void *)0; }

/* The bar's title on Home (navigation.c bar_title_sync). */
const char *coverflow_home_title(void) {
    void *open = home_open();
    return !open                 ? "Q2 Muse"
           : open == home.lib    ? "Library"
           : open == home.stream ? "Streaming"
                                 : "Settings";
}

/* home_page_init: stock binds the name-matched img_* cards, then the Coverflow card binds here,
 * on its image as stock does, whatever stock returned. */
int coverflow_home(void *win, void *ctx) {
    int result = stock_home_trampoline(win, ctx);
    widget_on(widget_lookup(win, "img_cover_flow", 1), EVT_CLICK, coverflow_open, 0);
    widget_on(widget_lookup(win, "img_settings", 1), EVT_CLICK, home_settings, 0);
    widget_on(widget_lookup(win, "img_home_search", 1), EVT_CLICK, library_search_open, 0);
    home_np_bind(win); /* its first row's click and glyph (home_np.c) */
    home_sets_bind(win);
    home.win = win;
    void *art = widget_lookup(win, "img_homeart", 1);
    home.art = art;
    home.clipped = 0;
    /* The fitted cover reaches under the list; taps there must still find the rows. */
    if (art) widget_set_sensitive(art, 0);
    void *list = widget_lookup(win, "list_view_home", 1);
    home.list = list;                   /* a new Home window's own, so still the asset's width */
    lr_home_tag(coverflow_home_view()); /* its rows' letters, for the user's order */
    home.sets = widget_lookup(win, "list_view_homeset", 1);
    if (home.sets) widget_set_sensitive(home.sets, 0);
    home.lib = widget_lookup(win, "list_view_homelib", 1);
    if (home.lib) {
        widget_set_sensitive(home.lib, 0);
        widget_on(widget_lookup(win, "img_library", 1), EVT_CLICK, home_library, win);
    }
    home.stream = widget_lookup(win, "list_view_homestream", 1);
    if (home.stream) {
        widget_set_sensitive(home.stream, 0);
        widget_on(widget_lookup(win, "img_set_stream", 1), EVT_CLICK, home_streaming, win);
    } else /* Full's sets: stock's Streaming page */
        widget_on(widget_lookup(win, "img_set_stream", 1), EVT_CLICK, on_home_stream_onclick, win);
    widget_on(widget_lookup(win, "img_games", 1), EVT_CLICK, games_open, 0);
    widget_on(widget_lookup(win, "img_radio", 1), EVT_CLICK, tuner_open, 0); /* radio_tuner.c */
    static const char *const carousel[] = { "slide_menu", "img_left", "img_right" };
    for (int i = 0; i < 3; ++i) home.carousel[i] = widget_lookup(win, carousel[i], 1);
    /* The carousel's own Coverflow and Shortcut cards; the lists' come first in the window. */
    widget_on(widget_lookup(home.carousel[0], "img_cover_flow", 1), EVT_CLICK, coverflow_open, 0);
    widget_on(widget_lookup(home.carousel[0], "img_rockbox", 1), EVT_CLICK, home_shortcut_open, 0);
    coverflow_home_layout();
    return result;
}

/* A library scan is running: its thread has started and not finished. Card apps (addon.c) wait for
 * it as Coverflow does, since the scan rewrites the records and directory lists they would read. */
int library_scanning(void) { return *(volatile int *)SCAN_THREAD && !*(volatile int *)SCAN_DONE; }
/* A song count reads songtable on its own connection: the scan dialog's msg_count (scan_page.c).
 * Its flag is set on the UI thread before its thread starts, as the dialog loads, before stock's
 * init starts the scan's thread. So a scan that finds none can drop the table. */
int library_counting(void) { return msg_counting(); }
