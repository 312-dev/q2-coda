/* The theme engine: Light Mode's flip, the themes table, the settings it reads, accent(), theme(),
 * scheme() and the accent's tones. */
#include "navigation.h"

/* A setting under section in the stock config.ini, a decimal 0 to n - 1 (write_int_config writes
 * them so), else def (a missing key too): toolsReadConfig(path, section, key, out, default) copies
 * the value, or the default. */
int config_value(const char *section, const char *key, int n, int def) {
    char s[256] = "", d[12];
    tk_snprintf(d, sizeof d, "%d", def);
    config_settle(1); /* a held key reads back as last set */
    toolsReadConfig("/mnt/data/config.ini", section, key, s, d);
    int v = 0, i = 0;
    for (; i < 3 && s[i] >= '0' && s[i] <= '9'; ++i) v = v * 10 + s[i] - '0';
    return i && !s[i] && v < n ? v : def;
}

#define ROCKBOX_BIN "/mnt/mmc/.rockbox/rockbox"
/* glibc 2.28 MIPS o32 stat v3: st_mode at byte 20, as stock's fs stat reads it. */
static int bin_available(const char *path) {
    unsigned info[36];
    return !__xstat(3, path, info) && (info[5] & 0170000) == 0100000;
}
int rockbox_available(void) { return bin_available(ROCKBOX_BIN); }

/* Light Mode (docs/ipod.md#light-mode): a neutral colour, its channels within LIGHT_SPREAD, flipped
 * per channel from black to the light half's paper and white to its ink (LIGHT_PAPER and LIGHT_INK
 * unless the scheme sets them); any other hue, and the alpha byte, as they are. Either byte order
 * (color_t or 0xRRGGBB): the paper and ink are grey. */
int neutral(unsigned c) {
    unsigned hi = 0, lo = 255;
    for (int s = 0; s < 24; s += 8) {
        unsigned v = c >> s & 255;
        hi = v > hi ? v : hi;
        lo = v < lo ? v : lo;
    }
    return hi - lo <= LIGHT_SPREAD;
}
unsigned flip_v(unsigned v, unsigned paper, unsigned ink) {
    return paper - v * (paper - ink) / 255;
}
static const scheme_t *light_row(void);
unsigned flip(unsigned c) {
    if (!neutral(c)) return c;
    /* greys, so either byte order */
    const scheme_t *S = light_row();
    unsigned paper = S->paper & 255, ink = S->ink & 255;
    unsigned out = c & 0xff000000u;
    for (int s = 0; s < 24; s += 8) out |= flip_v(c >> s & 255, paper, ink) << s;
    return out;
}
unsigned flip_if(int on, unsigned c) { return on ? flip(c) : c; }
/* The grey whose flip is v, by the half's paper and ink; v itself when dark. */
unsigned unflip(unsigned v) {
    if (!ipod_light()) return v;
    const scheme_t *S = light_row();
    unsigned paper = S->paper & 255, ink = S->ink & 255;
    return v < ink ? 255 : v > paper ? 0 : (paper - v) * 255 / (paper - ink);
}

/* Every colour the payload draws passes here (peq.h wraps the canvas setters), as stock's pass
 * the style and image hooks: flipped in Light Mode. A clear colour stays 0: stock tests for it
 * (Now Playing's slider draws no dragger under a #00000000 border). */
unsigned ui_ink(unsigned c) { return !st.raw_ink && ipod_light() && c >> 24 ? flip(c) : c; }
/* Sets whether ui_ink leaves colours alone (final colours follow); the last setting, to restore. */
unsigned ui_ink_raw(unsigned raw) {
    unsigned old = st.raw_ink;
    st.raw_ink = raw;
    return old;
}

/* The 0xRRGGBB j/n of the way from one color to another, per channel. */
unsigned mix(unsigned from, unsigned to, int j, int n) {
    unsigned c = 0;
    for (int s = 0; s < 24; s += 8)
        c |= (unsigned)(((int)(from >> s & 255) * (n - j) + (int)(to >> s & 255) * j) / n) << s;
    return c;
}

/* The Accent, Home and Battery settings (docs/ipod.md#display-settings). */
static const unsigned accents[][5] = { ACCENTS };
_Static_assert(sizeof accents / sizeof *accents == ACCENT_N, "one accent per ACCENTS row");
const char *const accent_names[] = { "Accent: Graphite",  "Accent: Crimson", "Accent: Tidal",
                                     "Accent: Champagne", "Accent: Cobalt",  "Accent: Tangerine",
                                     "Accent: Plum",      "Accent: Ivory",   "Accent: Green" };
_Static_assert(sizeof accent_names / sizeof *accent_names == ACCENT_N, "one name per ACCENTS row");
_Static_assert(sizeof st.setting_label / sizeof *st.setting_label == SETTING_OWN + THEME_KEYS,
               "one label per Display row");
#include "themes.inc" /* tools/theme.py, from assets/themes/<id>/theme.xml */
_Static_assert(DRIFT_TEXELS >= 4 * (375 - HOME_SPLIT_W) * 320, /* a pane to the top edge */
               "Home drift frame fits every set");
#if __SIZEOF_POINTER__ == 4 /* the Q2's layout; test/fw's 64-bit build has its own */
_Static_assert(__builtin_offsetof(theme_t, motion) == THEME_MOTION_OFF && sizeof(motion_t) == 8 &&
                   __builtin_offsetof(theme_t, recall_ms) == THEME_MOTION_OFF + MOTION_N * 8,
               "test/patch.py's set_motion and set_recall");
_Static_assert(__builtin_offsetof(scheme_t, mode) == SCHEME_MODE_OFF, "test/patch.py patches it");
#endif
/* A value of the theme's geometry set (tools/ipod.py Geometry) that is not an immediate: V_BAR, ... */
int geom_val(int v) { return geom_vals[v]; }
int client_h(void) { return 320 - geom_val(V_BAR); }
/* The row in w where the status row ends: V_BAR in a page's window, which takes the whole screen
 * (tools/build.py FULL_BLEED, docs/oled-design.md#full-bleed), and in a view at its top; 0 in a
 * view of it that starts at the row's foot. A layout that counts from the row's foot adds it. */
int bar_foot(void *w) { return geom_val(V_BAR) - (w ? I(w, W_Y) : 0); }
/* The rows of w under the status row: from bar_foot to w's own foot (client_h in a page's window).
 */
int page_room(void *w) { return w ? I(w, W_H) - bar_foot(w) : client_h(); }
_Static_assert(V_SPLIT_ART == V_SPLIT_ROW + SPLIT_ART &&
                   V_SPLIT_ART_UP == V_SPLIT_ROW + SPLIT_ART_UP &&
                   V_SPLIT_INSET == V_SPLIT_ROW + SPLIT_INSET,
               "tools/ipod.py GEOM_VALS keeps split_* in SPLIT_* order");
int split_geom(int k) { return geom_val(V_SPLIT_ROW + k); }
/* The lists end their whole rows by this screen row (282: the grid's bar and list_end). */
int list_fit_end(void) { return geom_val(V_BAR) + geom_val(V_LIST_END); }
/* The Display rows' settings, by ringnav_display's index: 0 Accent, 1 Home (0 Split, 1 Full; any
 * other stored value reads as Split), 2 Battery, 3 Light Mode, 4 Smart Awake, 5 the Clicker's
 * output (no row of its own: Controls' Key Tone row shows it, clicker.c), 6 Reduce Motion; 7 on:
 * the theme's own options, SETTING_OWN + k for theme_keys[k]. IPOD SHORTCUT is not a key here and
 * is never read. */
static const char *const setting_keys[] = { "ACCENT",  "HOME",    "BATTERY",     "LIGHT",
                                            "KEEPLIT", "CLICKER", "REDUCEMOTION" };
const int setting_counts[] = {
    SETTING_N_ACCENT, SETTING_N_HOME, SETTING_N_BATTERY, SETTING_N_LIGHT, 2, 3, 2
};
int *const setting_values[] = { &st.accent,   &st.home,    &st.battery,      &st.light,
                                &st.keep_lit, &st.clicker, &st.reduce_motion };
/* An option's value and config key: the accent's are the theme's own, an own option's st.own[] and
 * theme_keys[], the rest setting_values'. */
int *option_slot(const theme_t *T, int setting) {
    return setting == SETTING_ACCENT ? T->accent_slot
           : setting >= SETTING_OWN  ? &st.own[setting - SETTING_OWN]
                                     : setting_values[setting];
}
const char *option_key(const theme_t *T, int setting) {
    return setting == SETTING_ACCENT ? T->accent_key
           : setting >= SETTING_OWN  ? theme_keys[setting - SETTING_OWN].key
                                     : setting_keys[setting];
}
/* The option bound to setting, or NULL. */
const option_t *option_of(const theme_t *T, int setting) {
    for (int k = 0; k < T->n_opts; ++k)
        if (T->opts[k].setting == setting) return &T->opts[k];
    return (void *)0;
}
/* The local time now into buf (struct tm: tm_sec @0, tm_min @4, tm_hour @8), or 0 when the clock
 * cannot be read. */
const int *clock_now(int *buf) {
    long long wall = wall_time();
    long now = clock_back(wall);
    return wall == -1 ? (void *)0 : localtime_r(&now, buf);
}
/* Light Mode's Auto (LIGHT=2) is light from 7:00 to 19:00 by the clock. clock_show applies it each
 * minute: when Auto crosses either time it drops the cached images (ringnav_image_add bakes the
 * mode into them) and repaints, as setting_click does. */
#define LIGHT_FROM 7
#define LIGHT_UNTIL 19
int light_day(const int *tm) { return tm && tm[2] >= LIGHT_FROM && tm[2] < LIGHT_UNTIL; }
/* Night Shift's Auto (nightshift.c) is on while Light Mode's would be dark, 19:00 to 7:00, and
 * off while the clock cannot be read. */
int clock_night(void) {
    int buf[11];
    const int *tm = clock_now(buf);
    return tm && !light_day(tm);
}
void light_apply(const int *tm) {
    if (st.light != 2 || light_day(tm) == st.light_day) return;
    st.light_day = !st.light_day;
    np_fill(0);
    text_inks(window_manager());
    image_manager_unload_all(image_manager());
    widget_invalidate_force(window_manager(), (void *)0);
}
/* accent()'s first call: the [IPOD] keys. Its own function, so accent()'s every later call (each
 * colour Light Mode flips asks twice: ipod_light(), scheme()) skips this frame. */
__attribute__((noinline)) static void settings_read(void) {
    for (int i = 0; i < 3; ++i)
        *setting_values[i] = config_value("IPOD", setting_keys[i], setting_counts[i], 0);
    st.light = config_value("IPOD", "LIGHT", 3, 2); /* Auto unless chosen (the owner's, 2026-11-27); 0 dark, 1 light */
    /* Smart Awake on unless chosen (tools/build.py DEFAULT_IPOD writes it so after a reset) */
    st.keep_lit = config_value("IPOD", "KEEPLIT", 2, 1);
    st.clicker = config_value("IPOD", "CLICKER", 3, 0);      /* Both unless chosen (clicker.c) */
    st.reduce_motion = config_value("IPOD", "REDUCEMOTION", 2, 0); /* full motion unless chosen */
    /* the theme's own keys, after the payload's, in table order */
    for (int k = 0; k < THEME_KEYS; ++k)
        st.own[k] = config_value("IPOD", theme_keys[k].key, theme_keys[k].n, theme_keys[k].def);
    /* A stored value outside an option's values reads as its default, the key untouched. */
    for (int k = 0; k < themes->n_opts; ++k) {
        const option_t *o = &themes->opts[k];
        int *v = option_slot(themes, o->setting);
        if (o->values && o->def >= 0 && !(o->values >> *v & 1)) *v = o->def;
    }
    int buf[11];
    st.light_day = light_day(clock_now(buf));
    st.settings_read = 1;
}
int accent(void) {
    if (!st.settings_read) settings_read();
    return *themes->accent_slot;
}
/* THEME_N is 1: OLED, the one built-in theme, is themes[0]. */
_Static_assert(THEME_N == 1, "theme() returns the one row");
const theme_t *theme(void) {
    accent();
    return themes;
}
/* 1 for scheme v's light half: its mode, else Light Mode (Auto: light_day), which reads 0 where the
 * theme does not declare it (setting()'s rule). The dark row's mode: both carry it. */
static int light_on(const theme_t *T, int v) {
    int m = T->schemes[v].mode;
    return m == SCHEME_LIGHT || (m == SCHEME_DERIVE && !(T->undeclared >> SETTING_LIGHT & 1) &&
                                 (st.light == 2 ? st.light_day : st.light));
}
/* The half Light Mode picks; a theme stating no final value has one row for both halves. */
const scheme_t *scheme(void) {
    int v = accent();
    const theme_t *T = themes;
    return &(light_on(T, v) ? T->light : T->schemes)[v];
}
/* scheme() for code that runs only in the light half (flip, red_map's light term), so after
 * ipod_light()'s accent(): the light row without a second accent() and light_on() on these
 * per-colour paths (a folder paint can set ~470 colours). */
static const scheme_t *light_row(void) { return &themes->light[*themes->accent_slot]; }
/* An option the theme does not declare reads 0, its stored key untouched (ponytail: 0 is every
 * hidden option's value today; a theme hiding Home or Battery needs a value per bit). */
int setting(int i) {
    accent();
    return themes->undeclared >> i & 1 ? 0 : *setting_values[i];
}
/* A theme's own choice (OWN_*): its stored value while the theme offers it, else 0. */
static int theme_own(int k) { return option_of(theme(), SETTING_OWN + k) ? st.own[k] : 0; }
/* OLED's song rows (song_rows.c): Song Artist's and Song Length's values, SONG_ARTIST_* and
 * SONG_LENGTH_* (navigation.h) in theme.xml's order */
int song_artist(void) { return theme_own(OWN_SONGARTIST); }
int song_length(void) { return theme_own(OWN_SONGLENGTH); }
/* OLED's Track Time (np_bar.c np_sync): 1 when Now Playing's right-hand time is the track's length,
 * 0 for what is left of it */
int np_time_total(void) { return theme_own(OWN_NPTIME); }
/* A tap on that time (np_time_tap): the other one, saved. 0 under a theme without the option. */
int np_time_toggle(void) {
    if (!option_of(theme(), SETTING_OWN + OWN_NPTIME)) return 0;
    st.own[OWN_NPTIME] = !st.own[OWN_NPTIME];
    write_int_config(st.own[OWN_NPTIME], "IPOD", theme_keys[OWN_NPTIME].key);
    return 1;
}
/* Home's layout, HOME's Split or Full; Split shows as Full while Edit Home is open
 * (library_edit.c), the editor's hint and grips across the screen. */
int ipod_home(void) {
    accent();
    return !st.home && lr_home_editing() ? 1 : st.home;
}
int ipod_light(void) {
    int v = accent();
    return light_on(themes, v);
}
/* Podcasts, Audiobooks and Videos (docs/internals.md#podcasts-and-audiobooks): the card's top-level
 * folders of these names, case aside, browsed in folder_page from there. */
const char *const MEDIA[] = { "Podcasts", "Audiobooks", "Videos" };
/* Light Mode keeps a coloured accent's tones; a neutral one (Graphite, Ivory) takes its silver red
 * tone for both, which flips dark. Before flip. */
unsigned tone_of(const unsigned *a, int tone) {
    return ipod_light() && neutral(a[0]) ? a[TONE_RED] : a[tone];
}
unsigned accent_tone(int tone) { return tone_of(scheme()->tones, tone); }

/* A color_t (bytes r, g, b, a) that is stock red blended with a neutral, t * red + k * white per
 * channel within RED_TOLERANCE, becomes the same blend of one of the preset's tones (TONE_RED for
 * text and images, TONE_LIGHT for every other color): flat reds,
 * pressed tints and anti-aliased edges follow the accent, alpha is kept, and greys and other hues
 * are unchanged. t comes from least squares against red with the mean removed, in 1/4096. The white
 * part (k) becomes that share of glyph: white keeps a blend's light part as it is. With another
 * glyph, neutral pixels (the glyph itself) map too, so pass it only for an image that holds red.
 * Crimson is the identity. Light Mode (light) puts the red share over paper instead: a tint over
 * black becomes the same tint of tone over the light half's paper, its grey part flipped. */
unsigned red_map(unsigned c, unsigned tone, unsigned glyph, int light) {
    int paper = light ? (int)(light_row()->paper & 255) : 0,
        ink = light ? (int)(light_row()->ink & 255) : 0;
    static const int red[3] = { STOCK_RED >> 16, STOCK_RED >> 8 & 255, STOCK_RED & 255 };
    const int sum = red[0] + red[1] + red[2];
    int ch[3] = { c & 255, c >> 8 & 255, c >> 16 & 255 }, s = ch[0] + ch[1] + ch[2], d = 0, dd = 0;
    for (int i = 0; i < 3; ++i) {
        d += (3 * ch[i] - s) * (3 * red[i] - sum);
        dd += (3 * red[i] - sum) * (3 * red[i] - sum);
    }
    int t = d * 4096 / dd;
    if (t < 256 && glyph != 0xffffff) t = 0; /* a neutral glyph pixel takes the glyph colour too */
    int k = (s * 4096 - t * sum) / (3 * 4096);
    if ((t && t < 256) || t > 4096 + 256 || k < -RED_TOLERANCE) return c;
    unsigned out = c & 0xff000000u;
    for (int i = 0; i < 3; ++i) {
        int miss = ch[i] - (t * red[i] / 4096 + k);
        if (miss > RED_TOLERANCE || miss < -RED_TOLERANCE) return c;
        int v = t * (int)(tone >> (16 - 8 * i) & 255) / 4096 +
                (light ? (t < 4096 ? 4096 - t : 0) * paper / 4096 - k * (paper - ink) / 255
                       : k * (int)(glyph >> (16 - 8 * i) & 255) / 255);
        out |= (unsigned)(v < 0 ? 0 : v > 255 ? 255 : v) << 8 * i;
    }
    return out;
}
unsigned accent_map(unsigned c, int preset, int tone) {
    const theme_t *T = theme(); /* once: this runs per colour */
    const unsigned *a = T->schemes[preset].tones;
    if (ipod_light())
        return !(c >> 24) || neutral(c) ? ui_ink(c)
                                        : red_map(c, flip(tone_of(a, tone)), 0xffffff, 1);
    return red_map(c, a[tone], 0xffffff, 0);
}
