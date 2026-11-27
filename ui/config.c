/* The settings q2ui takes from /mnt/data/config.ini (docs/internals.md#configini), which demo owns
 * and writes while it owns the settings pages: read here, never written. [IPOD] LIGHT (0 dark, 1
 * Light Mode, 2 Auto: light from 7:00 to 19:00, patch/theme_engine.c light_day; Auto when absent) and REDUCEMOTION
 * (1 on). Read at the start and whenever demo writes the file (a watch of its folder), and Auto
 * again when the minute turns (status.c). --light and --reduce-motion on the command line hold
 * their setting on whatever the file says. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/inotify.h>
#include <time.h>
#include <unistd.h>

#include "q2ui.h"

#define LIGHT_FROM 7
#define LIGHT_UNTIL 19

const char *config_file = "/mnt/data/config.ini";
int config_light_forced, config_motion_forced;
static int light_setting, motion_setting, watch = -1;
int config_light_setting(void) { return light_setting; }
int config_motion_setting(void) { return motion_setting; }

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
    if (light == ui_light) return;
    ui_light = light;
    list_rows_changed();
    ui_mark_all();
}

void config_read(void) {
    light_setting = config_int("IPOD", "LIGHT", 2); /* Auto unless chosen (the owner's, 2026-11-27) */
    motion_setting = config_int("IPOD", "REDUCEMOTION", 0) == 1;
    list_rows_changed(); /* a page may show a setting (page_display.c) */
    int reduce = config_motion_forced || motion_setting;
    if (reduce != ui_reduce_motion) ui_reduce_motion = reduce, ui_mark_all();
    config_light_now();
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
