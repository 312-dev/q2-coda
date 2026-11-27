/* What plays, read from the file demo writes for Car Remote while demo owns the player
 * (/tmp/coda-rc.now, patch/coda_rc_shim.c cr_now: a key=value line each, rewritten when anything
 * in it changes, twice a second at most), and the playing song's cover as stock's cover thread
 * leaves it (/tmp/externpic.jpg). Nothing is polled: the folder is watched (inotify), so a change
 * wakes the loop and a page's step sees now_seq() move, and at rest nothing runs. When q2ui owns
 * the player this file reads its own state instead (docs/ui-rewrite.md#cutover). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/inotify.h>
#include <sys/stat.h>
#include <unistd.h>

#include "q2ui.h"

const char *now_file = "/tmp/coda-rc.now", *now_cover_file = "/tmp/externpic.jpg";
static ui_now now;
static unsigned seq, cover_seq;
static int watch = -1;
static image_t *cover;
static struct timespec cover_time;
static long cover_size = -1;

const ui_now *now_get(void) { return &now; }
unsigned now_seq(void) { return seq; }
int now_watched(void) { return watch >= 0; }

static void field(const char *line, const char *key, char *out, size_t room) {
    size_t k = strlen(key);
    if (strncmp(line, key, k) || line[k] != '=') return;
    snprintf(out, room, "%s", line + k + 1);
    out[strcspn(out, "\r\n")] = 0;
}
void now_read(void) {
    char line[700], num[16];
    ui_now was = now;
    FILE *f = fopen(now_file, "r");
    memset(&now, 0, sizeof now);
    while (f && fgets(line, sizeof line, f)) {
        *num = 0;
        field(line, "title", now.title, sizeof now.title);
        field(line, "artist", now.artist, sizeof now.artist);
        field(line, "album", now.album, sizeof now.album);
        field(line, "state", num, sizeof num);
        /* 0 stopped, 1 playing, 2 paused: AVRCP's order (patch/now_source.c now_status) */
        if (*num) now.playing = atoi(num) == 1, now.paused = atoi(num) == 2;
        *num = 0, field(line, "pos", num, sizeof num);
        if (*num) now.pos_ms = atoi(num);
        *num = 0, field(line, "len", num, sizeof num);
        if (*num) now.len_s = atoi(num);
        *num = 0, field(line, "volume", num, sizeof num);
        if (*num) now.volume = atoi(num);
        *num = 0, field(line, "id", num, sizeof num);
        if (*num) now.id = atoi(num);
        *num = 0, field(line, "shuffle", num, sizeof num);
        if (*num) now.shuffle = atoi(num);
        *num = 0, field(line, "repeat", num, sizeof num);
        if (*num) now.repeat = atoi(num);
        *num = 0, field(line, "hearts", num, sizeof num);
        if (*num) now.hearts = atoi(num);
    }
    if (f) fclose(f);
    now.read_us = mono_us();
    if (memcmp(&was, &now, offsetof(ui_now, read_us))) ++seq;
}

/* The cover, decoded again only when the file changed; 0 with none. */
const image_t *now_cover(void) {
    struct stat st;
    if (stat(now_cover_file, &st) || !st.st_size) {
        if (cover) image_free(cover), cover = 0, cover_size = -1, ++cover_seq;
        return 0;
    }
    if (st.st_size == cover_size && st.st_mtim.tv_sec == cover_time.tv_sec && st.st_mtim.tv_nsec == cover_time.tv_nsec)
        return cover;
    image_t *full = image_load(now_cover_file);
    image_free(cover);
    /* kept at twice the largest size it is shown, so a lean or a zoom never runs out of picture */
    cover = full ? image_fit(full, 360, 360) : 0;
    image_free(full);
    cover_size = st.st_size, cover_time = st.st_mtim, ++cover_seq;
    return cover;
}
unsigned now_cover_seq(void) { return cover_seq; }

static void changed(void) {
    char buf[1024];
    while (read(watch, buf, sizeof buf) > 0) {}
    now_read();
}
void now_open(void) {
    char dir[256];
    snprintf(dir, sizeof dir, "%s", now_file);
    char *slash = strrchr(dir, '/');
    if (slash) *slash = 0;
    watch = inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
    if (watch >= 0 && inotify_add_watch(watch, slash ? dir : ".", IN_MOVED_TO | IN_CLOSE_WRITE) >= 0) ui_watch(watch, changed);
    now_read();
}
