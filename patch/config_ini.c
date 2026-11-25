/* config.ini's sections each start a line (docs/internals.md#config-ini). The config.ini stock
 * copies in at first boot or a reset ends "NAVHIDE=0" with no newline, and toolsWriteConfig appends
 * a section it does not find as "[%s]\n%s=%s\n", so the first section written after a reset ran on
 * from that line ("NAVHIDE=0[Q2POD]"). toolsReadConfig takes a section only at a line's start, so
 * it never saw that section or its keys, and the next write of one of them appended the section
 * again. Every toolsWriteConfig puts the file right first. */
#include "navigation.h"

#define INI_MAX 16384 /* a Q2's config.ini is about 1.5 KB; a larger file is left as it is */
/* The sections the payload adds; stock's own two, PLAYSET and SYSSET, are in the file it copies. */
static const char *const ini_added[] = { "[Q2POD]", "[IPOD]" };

/* The section header the line [s, e) ends with after a key's text, or 0. */
static const char *ini_run_on(const char *b, int s, int e) {
    if (b[s] == '[') return 0;
    for (unsigned i = 0; i < sizeof ini_added / sizeof *ini_added; i++) {
        int n = (int)strlen(ini_added[i]);
        if (e - s > n && !memcmp(b + e - n, ini_added[i], n)) return ini_added[i];
    }
    return 0;
}

static int ini_line_end(const char *b, int n, int i) {
    while (i < n && b[i] != '\n') i++;
    return i;
}

/* Whether a section headed h after offset i holds the key k (n bytes). */
static int ini_later(const char *b, int len, int i, const char *h, const char *k, int n) {
    int in = 0, hn = (int)strlen(h);
    for (int e; i < len; i = e + 1) {
        e = ini_line_end(b, len, i);
        if (b[i] == '[')
            in = e - i == hn && !strncasecmp(b + i, h, hn);
        else if (in && e - i > n && b[i + n] == '=' && !strncasecmp(b + i, k, n))
            return 1;
    }
    return 0;
}

/* A line that runs into a section's header is split before it, and every line ends in a newline.
 * Under a header so split, a key that a later section of the same name also holds is dropped: stock
 * reads and rewrites a key's first line in any section of that name, and the later one is the line
 * it has been reading and writing since. The keys only the split section holds come back. */
static void ini_heal(const char *path) {
    char *in = calloc(1, 3 * INI_MAX + 4), *out = in + INI_MAX + 1;
    void *f = in ? fopen(path, "rb") : 0;
    int n = f ? (int)fread(in, 1, INI_MAX + 1, f) : 0, o = 0;
    if (f) fclose(f);
    const char *h = 0; /* while in a split section, its header */
    for (int s = 0, e; n <= INI_MAX && s < n; s = e + 1) {
        e = ini_line_end(in, n, s);
        const char *run = ini_run_on(in, s, e);
        int k = s;
        while (k < e && in[k] != '=') k++;
        if (in[s] == '[') h = 0;
        if (run) {
            int hn = (int)strlen(run);
            memcpy(out + o, in + s, e - hn - s), o += e - hn - s, out[o++] = '\n';
            memcpy(out + o, run, hn), o += hn, out[o++] = '\n';
            h = run;
        } else if (!(h && k < e && k > s && ini_later(in, n, e + 1, h, in + s, k - s))) {
            memcpy(out + o, in + s, e - s), o += e - s, out[o++] = '\n';
        }
    }
    if (n > 0 && n <= INI_MAX && (o != n || memcmp(in, out, n))) peq_save_file(path, 0, out, o);
    free(in);
}

/* toolsWriteConfig's hook: stock's every write and the payload's, the file healed before it. */
int ringnav_ini_write(const char *path, const char *section, const char *key, const char *value) {
    ini_heal(path);
    autozone_config(section, key); /* a zone picked by hand turns Automatic Time Zone off */
    return stock_ini_write_trampoline(path, section, key, value);
}

/* Settled writes (docs/internals.md#where-vamp-writes): the keys a wheel steps or a tap toggles,
 * each change a whole rewrite of config.ini on the flash (60-180 ms on the UI thread in the
 * emulator), are held by write_int_config's hook (ringnav_write_config) and written once their
 * delay after their last change has passed, from power_poll's pass, one key a pass (step "config settle"
 * for the UI pass watchdog), so a turn of ten steps is one write and a tap's own pass writes nothing. The
 * wheel's keys wait CONFIG_SETTLE_MS; the play mode's, which Now Playing's Shuffle and Repeat and
 * stock's play mode setting write (stock's config_playmode through the same hook, and
 * np_bar.c's own two), MODE_SETTLE_MS. config_settle(1) writes what is held at once: before the
 * payload reads one of them back (config_value, ringnav_output), at Standby and at a clean end
 * (power-off, Restart, an update, Rockbox). ponytail: a forced restart or a flat battery within
 * the delay of the last change loses that change, the value before it coming back at boot. */
#define MODE_SETTLE_MS 1000
static const struct {
    const char *section, *key;
    unsigned ms;
} settle_keys[] = {
    { "PLAYSET", "VOLUME", CONFIG_SETTLE_MS },     { "PLAYSET", "BT_VOLUME", CONFIG_SETTLE_MS },
    { "Q2POD", "AIRVOL", CONFIG_SETTLE_MS },       { "PLAYSET", "MAXVOLUME", CONFIG_SETTLE_MS },
    { "PLAYSET", "BOOTVOLUME", CONFIG_SETTLE_MS }, { "PLAYSET", "BALANCE", CONFIG_SETTLE_MS },
    { "SYSSET", "LIGHTNESS", CONFIG_SETTLE_MS },   { "PLAYSET", "PLAYMODE", MODE_SETTLE_MS },
    { "IPOD", "NPSHUFONE", MODE_SETTLE_MS },       { "IPOD", "NPREPOFF", MODE_SETTLE_MS },
};
#define SETTLE_N (sizeof settle_keys / sizeof *settle_keys)
static struct {
    int value[SETTLE_N];
    unsigned at[SETTLE_N]; /* time_now_ms of the last change; 0 nothing held */
} settle __attribute__((section(".scratch")));

int config_hold(int value, const char *section, const char *key) {
    for (unsigned i = 0; i < SETTLE_N; i++)
        if (!tk_strcmp(section, settle_keys[i].section) && !tk_strcmp(key, settle_keys[i].key)) {
            unsigned now = (unsigned)time_now_ms();
            settle.value[i] = value;
            settle.at[i] = now | !now;
            return 1;
        }
    return 0;
}

void config_settle(int now_too) {
    unsigned now = (unsigned)time_now_ms();
    for (unsigned i = 0; i < SETTLE_N; i++)
        if (settle.at[i] && (now_too || now - settle.at[i] >= settle_keys[i].ms)) {
            settle.at[i] = 0;
            ui_watch_step("config settle");
            stock_write_config_trampoline(settle.value[i], settle_keys[i].section,
                                          settle_keys[i].key);
            if (!now_too) return; /* one rewrite a pass: a tap's two keys go on two passes */
        }
}
