/* What plays, read from the file demo writes for Car Remote while demo owns the player
 * (/tmp/coda-rc.now, patch/coda_rc_shim.c cr_now: a key=value line each, rewritten when anything
 * in it changes, twice a second at most), and the playing song's cover from the file demo names for
 * it (cover= in /tmp/q2ui.status, patch/q2ui_gate.c tell: the player's embedded picture, a folder
 * image or download, or a card app's cover). Nothing is polled: the folder is watched (inotify), so a change
 * wakes the loop and a page's step sees now_seq() move, and at rest nothing runs. The same watch
 * tells the status row when demo's file of the states only it knows changes (ui/status.c
 * status_told), and the Wi-Fi and Bluetooth pages when demo's files of the networks and devices do (ui/page_wifi.c,
 * ui/page_bluetooth.c). On Vamp OS, Q2PLAY/1's now line and rating are laid over the file's (ui/api.c
 * api_now_overlay; docs/ui-rewrite.md#cutover). */
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
static char cover_path[600]; /* the file cover was decoded from */

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
    now.liked = -1;
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
        *num = 0, field(line, "liked", num, sizeof num);
        if (*num) now.liked = atoi(num);
        *num = 0, field(line, "radio", num, sizeof num);
        if (*num) now.radio = atoi(num);
    }
    if (f) fclose(f);
    now.read_us = mono_us();
    api_now_overlay(&now); /* on Vamp OS, Q2PLAY/1's word over the file's (ui/api.c) */
    if (memcmp(&was, &now, offsetof(ui_now, read_us))) ++seq;
}

/* The cover, decoded again only when its file or the file's bytes changed; 0 with none. The file is
 * the one demo names (cover=, empty for none), as the player writes an embedded picture and a folder
 * image to two files and leaves the last song's behind; now_cover_file while demo names none. */
const image_t *now_cover(void) {
    char named[sizeof cover_path];
    struct stat st;
    const char *file = status_told_value("cover", named, sizeof named) ? named : now_cover_file;
    if (!*file || stat(file, &st) || !st.st_size) {
        if (cover) image_free(cover), cover = 0, cover_size = -1, ++cover_seq;
        return 0;
    }
    if (st.st_size == cover_size && st.st_mtim.tv_sec == cover_time.tv_sec && st.st_mtim.tv_nsec == cover_time.tv_nsec &&
        !strcmp(file, cover_path))
        return cover;
    snprintf(cover_path, sizeof cover_path, "%s", file);
    image_t *full = image_load(file);
    image_free(cover);
    /* kept at twice the largest size it is shown, so a lean or a zoom never runs out of picture */
    cover = full ? image_fit(full, 360, 360) : 0;
    image_free(full);
    cover_size = st.st_size, cover_time = st.st_mtim, ++cover_seq;
    return cover;
}
unsigned now_cover_seq(void) { return cover_seq; }

static const char *base(const char *path) { return strrchr(path, '/') ? strrchr(path, '/') + 1 : path; }
static void changed(void) {
    char buf[1024] __attribute__((aligned(__alignof__(struct inotify_event))));
    for (ssize_t n; (n = read(watch, buf, sizeof buf)) > 0;)
        for (char *at = buf; at < buf + n;) {
            const struct inotify_event *e = (const struct inotify_event *)at;
            if (e->len && !strcmp(e->name, base(status_told))) status_told_changed();
            if (e->len && !strcmp(e->name, base(wifi_networks_file))) wifi_networks_changed();
            if (e->len && !strcmp(e->name, base(bt_devices_file))) bt_devices_changed();
            at += sizeof *e + e->len;
        }
    now_read();
}
/* path's folder into dir: "." for a bare name */
static const char *folder(const char *path, char *dir, size_t room) {
    snprintf(dir, room, "%.*s", strrchr(path, '/') ? (int)(strrchr(path, '/') - path) : 0, path);
    return *dir ? dir : strrchr(path, '/') ? "/" : ".";
}
void now_open(void) {
    char dir[256], other[256];
    watch = inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
    int ok = watch >= 0 && inotify_add_watch(watch, folder(now_file, dir, sizeof dir), IN_MOVED_TO | IN_CLOSE_WRITE) >= 0;
    /* demo's status file, when a test puts it in a folder of its own */
    if (ok && strcmp(folder(status_told, other, sizeof other), folder(now_file, dir, sizeof dir)))
        inotify_add_watch(watch, other, IN_MOVED_TO | IN_CLOSE_WRITE);
    /* and its file of the networks (a folder already watched is not watched twice) */
    if (ok) inotify_add_watch(watch, folder(wifi_networks_file, other, sizeof other), IN_MOVED_TO | IN_CLOSE_WRITE);
    if (ok) inotify_add_watch(watch, folder(bt_devices_file, other, sizeof other), IN_MOVED_TO | IN_CLOSE_WRITE);
    if (ok) ui_watch(watch, changed);
    now_read();
}
