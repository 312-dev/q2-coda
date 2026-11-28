/* The settings q2ui takes from /mnt/data/config.ini (docs/internals.md#configini), which demo owns
 * and writes while it owns the settings pages: read here, never written. [IPOD] LIGHT (0 dark, 1
 * Light Mode, 2 Auto: light from 7:00 to 19:00, patch/theme_engine.c light_day; Auto when absent), REDUCEMOTION
 * (1 on), and the song rows' SONGARTIST (0 Inline, 1 Selected Row, 2 Right, 3 Off) and SONGLENGTH (0 Albums, 1 All
 * Lists, 2 Off), a value outside its range read as its default, 0, as demo reads them (theme_engine.c settings_read), and the accent,
 * UI_ACCENT_KEY (absent, or a value the theme does not offer, reads as UI_ACCENT_DEFAULT: Arterial). Read at the start and whenever demo writes the file (a watch of its folder), and Auto
 * again when the minute turns (status.c). --light and --reduce-motion on the command line hold
 * their setting on whatever the file says. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/inotify.h>
#include <time.h>
#include <unistd.h>

#include "../patch/night_scale.h"
#include "q2ui.h"

#define LIGHT_FROM 7
#define LIGHT_UNTIL 19

const char *config_file = "/mnt/data/config.ini";
int config_light_forced, config_motion_forced;
static int light_setting, motion_setting, night_setting, night_k = NS_DEFAULT, watch = -1;
int config_light_setting(void) { return light_setting; }
int config_night_setting(void) { return night_setting; }
int config_night_kelvin(void) { return night_k; }
int config_motion_setting(void) { return motion_setting; }
static int song_artist_setting, song_length_setting;
int config_song_artist(void) { return song_artist_setting; }
int config_song_length(void) { return song_length_setting; }

/* [section] key=value's number; def when the file, the section or the key is not there */
int config_int(const char *section, const char *key, int def) {
    FILE *f = fopen(config_file, "r");
    char line[256];
    int in = 0, v = def;
    size_t k = strlen(key);
    while (f && fgets(line, sizeof line, f)) {
        char *s = line + strspn(line, " \t");
        if (*s == '[') {
            char *end = strchr(s, ']');
            in = end && (size_t)(end - s - 1) == strlen(section) && !strncasecmp(s + 1, section, strlen(section));
        } else if (in && !strncasecmp(s, key, k) && (s[k] == '=' || s[k] == ' ')) {
            char *eq = strchr(s, '=');
            if (eq) v = atoi(eq + 1);
        }
    }
    if (f) fclose(f);
    return v;
}

/* Light Mode as the setting and the hour have it; a change draws everything again */
void config_light_now(void) {
    time_t t = (time_t)status_time();
    int hour = localtime(&t)->tm_hour;
    int light = config_light_forced || light_setting == 1 || (light_setting == 2 && hour >= LIGHT_FROM && hour < LIGHT_UNTIL);
    /* Night Shift: on, or Auto in Light Mode's Auto's dark hours (patch/nightshift.c lit) */
    int night = night_setting == 1 || (night_setting == 2 && !(hour >= LIGHT_FROM && hour < LIGHT_UNTIL));
    static int warm = -1;
    if ((night ? night_k : 0) != warm) warm = night ? night_k : 0, fb_warm(warm), ui_mark_all();
    if (light == ui_light) return;
    ui_light = light;
    list_rows_changed();
    ui_mark_all();
}

void config_read(void) {
    light_setting = config_int("IPOD", "LIGHT", 2); /* Auto unless chosen (the owner's, 2026-11-27) */
    motion_setting = config_int("IPOD", "REDUCEMOTION", 0) == 1;
    night_setting = config_int("Q2POD", "NIGHTSHIFT", 0);
    night_setting = night_setting >= 0 && night_setting <= 2 ? night_setting : 0;
    int k = config_int("Q2POD", "NIGHTK", 0); /* patch/nightshift.c settings: a temperature off its steps is the default */
    night_k = k >= NS_WARM && k <= NS_COOL && !(k % NS_STEP) ? k : NS_DEFAULT;
    song_artist_setting = config_int("IPOD", "SONGARTIST", 0);
    song_artist_setting = song_artist_setting >= 0 && song_artist_setting <= SONG_ARTIST_OFF ? song_artist_setting : 0;
    song_length_setting = config_int("IPOD", "SONGLENGTH", 0);
    song_length_setting = song_length_setting >= 0 && song_length_setting <= SONG_LENGTH_OFF ? song_length_setting : 0;
    if (accent_set_value(config_int("IPOD", UI_ACCENT_KEY, UI_ACCENT_DEFAULT))) ui_mark_all();
    list_rows_changed(); /* a page may show a setting (page_display.c), and song rows are drawn by two */
    int reduce = config_motion_forced || motion_setting;
    if (reduce != ui_reduce_motion) ui_reduce_motion = reduce, ui_mark_all();
    config_light_now();
    if (api_vamp) status_told_changed(); /* the status states take Brightness, Max Volume and the rest from here */
}

static void changed(void) {
    char buf[1024] __attribute__((aligned(__alignof__(struct inotify_event))));
    int ours = 0;
    const char *name = strrchr(config_file, '/') ? strrchr(config_file, '/') + 1 : config_file;
    for (ssize_t n; (n = read(watch, buf, sizeof buf)) > 0;)
        for (char *at = buf; at < buf + n;) {
            const struct inotify_event *e = (const struct inotify_event *)at;
            ours |= e->len && !strcmp(e->name, name);
            at += sizeof *e + e->len;
        }
    if (ours) config_read();
}

void config_open(void) {
    char dir[256];
    snprintf(dir, sizeof dir, "%.*s", strrchr(config_file, '/') ? (int)(strrchr(config_file, '/') - config_file) : 1,
             strrchr(config_file, '/') ? config_file : ".");
    watch = inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
    if (watch >= 0 && inotify_add_watch(watch, *dir ? dir : "/", IN_MOVED_TO | IN_CLOSE_WRITE) >= 0) ui_watch(watch, changed);
    config_read();
}
