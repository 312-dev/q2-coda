#include "peq.h"
#include "offsets.inc"
#ifndef PEQ_HOST /* test/peq.py's shim builds a release's page */
#include "stock.h" /* Q2POD_DEV */
#endif

extern int stock_eq_trampoline(int mode);
int peq_chained __attribute__((section(".scratch"))); /* a peq_stock_eq since hciplayer connected: the filter is in */
/* All calls, including restored playback, instantiate the replacement even in bypass.
 * Afterwards the flag only drives the status-bar EQ icon (systembar_showface @0x541a70): on
 * while 3EQ or PEQ is. */
int peq_stock_eq(int mode) {
    peq_chained = 1;
    g_equalizer_flag = 1;
    int result = stock_eq_trampoline(1);
    eq_state s;
    eq_load(&s);
    g_equalizer_flag = s.mode != EQ_OFF;
    return result;
}

static double headroom(const peq_preset *p);
/* PEQ's Auto (or never picked) preamp as the cut for every rate, which the player plays as saved
 * (docs/internals.md#parametric-eq). 1 if that changed it. */
static int auto_level(eq_state *s) {
    if (s->peq.automatic == 2) return 0;
    double cut = headroom(&s->peq);
    int changed = s->peq.automatic != 1 || s->peq.preamp != cut;
    s->peq.automatic = 1, s->peq.preamp = cut;
    return changed;
}

/* Off's comparison preamp belongs to the open page, which cannot survive a restart; and an Auto saved
 * by another version (its 48 kHz cut) becomes the cut for every rate before anything plays. */
void peq_boot(void) {
    eq_state s;
    if (!eq_load(&s)) return;
    int changed = auto_level(&s);
    if (s.hold) s.hold = 0, changed = 1;
    if (changed && eq_save(&s) && peq_chained) peq_stock_eq(1);
}

/* Every EQ change goes through here: s saved, PEQ's Auto as the cut for every rate, the stock switch's
 * config key (boot instantiates the filter only when it is set) written when on/off changes, and the
 * player told. */
static int eq_commit(eq_state *s) {
    auto_level(s);
    if (!eq_save(s)) return 0;
    int on = s->mode != EQ_OFF;
    if (on != g_equalizer_flag) write_int_config(on, "PLAYSET", "EQFLAG");
    peq_stock_eq(1);
    return 1;
}

/* Control Center's EQ circle (control_center.c): off, or on as the EQ page's Enabled switch turns it
 * on (PEQ, 3EQ off); nothing if that cannot be saved. */
void eq_toggle(void) {
    eq_state s;
    eq_load(&s);
    s.mode = s.mode == EQ_OFF ? EQ_PEQ : EQ_OFF;
    s.hold = 0;
    eq_commit(&s);
}

/* EQ off but its filter in the chain, for a fade, Mono Audio or AirPlay's tap (crossfade.c
 * peq_chain): the switch's Off, so a mode saved on while the stock flag is clear stays silent;
 * nothing if that cannot be saved. */
void peq_attach(void) {
    eq_state s;
    eq_load(&s);
    if (s.mode == EQ_OFF || (s.mode = EQ_OFF, s.hold = 0, eq_save(&s))) peq_stock_eq(1);
}

enum { HOME, BAND, PRESETS, SAVES, CONFIRM, DELETES, PICK, ADJUST, STOCKS, RENAMES, NAMING, TYPES };
enum { BACK = 1000, APPLY, RESET, BYPASS, PER_OUTPUT, MENU, SAVE, ENABLE, TYPE, RAISE, LOWER, TYPED, CHANNEL, STEP,
       YES, CANCEL, DELETE, BALANCE, PREAMP, GAIN, /* these three open the picker, in picks[] order */
       FREQUENCY, QUALITY, /* and these two the value menu */ KEYBOARD, STOCK, FILE_PREV, FILE_NEXT, RENAME, NAMED };
#define FILE_PAGE 128
#define GRAPH_W (375 - 2 * PEQ_GRAPH_X)
#define ROW_X page_x()      /* the theme's text column, as coverflow.c's page rows */
#define TONE accent_tone(2) /* the light tone */
/* The page's two tabs: Simple, 3EQ's sliders (e3), and Advanced, the PEQ editor (ui.screen). */
enum { SIMPLE, ADVANCED };
static struct {
    void *page, *view, *edit, *graph, *bar; /* bar: the title, which shows the tabs on the top screens */
    unsigned timer, keyboard_timer;
    int screen, band, step, count, previous, rendered, dirty, pick, adjust, tries;
    int capacity, file_page;
    int tab, tabs; /* tabs: the wheel is on the tab bar, over Advanced's list */
    int from_simple; /* Advanced's Stock presets screen, opened from Simple's Presets */
    /* HOME's and BAND's row + 1 (0 none) and scroll, kept while a screen one of them opened shows,
     * so coming back lands on the row it was left at (docs/oled-design.md#selection-bar) */
    int kept[2][2];
    double typed;
    peq_preset draft, candidate, named; /* named: the preset applied under applied's name */
    char (*names)[256];
    char destination[600], status[160];
    char name[256], applied[256]; /* the draft's preset, and the active one's, when loaded from a file */
    float curve[2][GRAPH_W];      /* the left and right response, in graph rows */
} ui __attribute__((section(".scratch")));

#if Q2POD_DEV
static int q2eq_busy __attribute__((section(".scratch")));       /* q2eq_poll's change */
static char q2eq_said[160] __attribute__((section(".scratch"))); /* what its actions said */
#endif

static const int steps[] = {1, 10, 100, 1000};
/* A band's types (PEQ_*), as its row and the Type picker name them */
static const char *const types[PEQ_TYPES] = {"Peaking",   "Low shelf", "High shelf", "Low pass",
                                             "High pass", "Band pass", "Notch",      "All pass"};
/* Wheel pickers in 0.5 dB rows: row i is first + step * i. Preamp's row 0 is Auto (first is unused there). */
static const struct { double first, step; int rows; } picks[] = {{-12, 0.5, 49}, {12.5, -0.5, 74}, {24, -0.5, 97}};
/* Simple, 3EQ (docs/internals.md#3eq): Low, Mid and High, -6 to +6 dB in 0.5 dB steps, on a view
 * that e3_paint draws: the switch, Presets and Reset (R) under the tabs, then a slider per band over
 * a 0 dB line (under a set_switch theme the three are rows and the sliders the standard's,
 * slider_page.c eq_rows_paint). The wheel moves between the tabs and the six; Centre flips the switch, opens
 * Advanced's Presets screen, resets, or takes a slider, whose gain the wheel then turns until
 * Centre or Return. A touch on a slider drags it. Edits are saved at most every E3_SAVE_MS, and at
 * once when an adjustment ends; the player hears them only while 3EQ is on, which turns PEQ off.
 * A preset picked from Presets plays as PEQ at once; taking a slider then turns 3EQ back on. */
#define E3_MAX 6
#define E3_BUTTONS 52 /* the switch, Presets and Reset, 40px high */
#define E3_SLIDERS 104
#define E3_SAVE_MS 250
enum { E3_TABS, E3_SWITCH, E3_PRESETS, E3_RESET, E3_LOW, E3_HIGH = E3_LOW + 2 };
/* The three buttons' x and width: Enabled and Presets wide, Reset a small R box. */
static const short e3_button[3][2] = { { 12, 140 }, { 161, 140 }, { 310, 53 } };
static struct {
    void *view;
    unsigned save, press, press_at;
    int focus, adjusting, dragging, dirty, on, h;
    double gain[3];
    const char *status;
    int caption; /* status is eq_output_caption's: the first turn of the wheel clears it too */
} e3 __attribute__((section(".scratch")));

/* EQ per Output: PEQ_ACTIVE stays what plays, and owner names the output it was set on. When another
 * output takes over, the EQ goes to owner's file and the new output's comes back; a new output keeps
 * what plays. An open EQ page follows it (page_load). owner is on disk so an edit just before
 * power-off still goes to the right output. */
static char eq_owner[20] __attribute__((section(".scratch")));
static void eq_slot(char *path, unsigned n, const char *key) {
    snprintf(path, n, EQ_OUTPUTS "/%s", key);
}
static void owner_set(const char *key) {
    char file[sizeof eq_owner] = { 0 }, path[256];
    snprintf(file, sizeof file, "%s", key);
    memcpy(eq_owner, file, sizeof file);
    eq_slot(path, sizeof path, "owner");
    mkdir(EQ_OUTPUTS, 0700);
    peq_save_file(path, "Q2OWNER", file, sizeof file);
}
static void owner_read(void) {
    struct { char magic[8], key[sizeof eq_owner]; } file = { { 0 }, { 0 } };
    char path[256];
    eq_slot(path, sizeof path, "owner");
    void *f = fopen(path, "rb");
    if (!f) return;
    unsigned n = fread(&file, 1, sizeof file, f);
    fclose(f);
    if (n == sizeof file && !memcmp(file.magic, "Q2OWNER", 8) && !file.key[sizeof file.key - 1])
        memcpy(eq_owner, file.key, sizeof eq_owner);
}
/* Bluetooth slots kept, a MAC's 17 characters naming them. */
static int eq_devices(void) {
    int n = 0;
    void *dir = opendir(EQ_OUTPUTS);
    if (!dir) return 0;
    struct dirent *entry;
    while ((entry = readdir(dir))) n += strlen(entry->d_name) == 17;
    closedir(dir);
    return n;
}
/* The bar's message as the EQ page opens: which output's EQ this is. */
static const char *eq_output_caption(void) {
    if (!ringnav_eq_output()) return 0;
    if (!eq_owner[0]) owner_read();
    return !eq_owner[0]                     ? 0
           : !strcmp(eq_owner, "3.5mm")     ? "EQ for 3.5 mm"
           : !strcmp(eq_owner, "4.4mm")     ? "EQ for 4.4 mm"
           : !strcmp(eq_owner, "usb")       ? "EQ for USB"
                                            : "EQ for this Bluetooth device";
}
/* What plays, as now, into owner's file, if there is an owner. A PEQ_ACTIVE that exists but can't be
 * read isn't filed over a good slot. 0 if the file can't be saved. */
static int eq_file(eq_state *now) {
    char from[256];
    int readable = eq_load(now) || access(PEQ_ACTIVE, 0);
    now->hold = 0;
    if (!eq_owner[0] || !readable) return 1;
    /* ponytail: past EQ_DEVICES a new headset plays and edits the current EQ, unremembered;
     * a forgotten device's slot stays, so re-pairing brings its EQ back. Eviction would need a
     * last-used date per slot. */
    eq_slot(from, sizeof from, eq_owner);
    if (strlen(eq_owner) == 17 && access(from, 0) && eq_devices() >= EQ_DEVICES) return 1;
    mkdir(EQ_OUTPUTS, 0700);
    return eq_save_to(from, now);
}
/* Turned off: the output keeps its latest EQ, and what plays from here on belongs to none. Simple
 * can't hold unsaved gains here: the row is on Advanced, and tab_to won't leave them behind. */
void eq_output_forget(void) {
    char path[256];
    eq_state now;
    if (!eq_owner[0]) owner_read();
    eq_file(&now);
    eq_slot(path, sizeof path, "owner");
    unlink(path);
    eq_owner[0] = 0;
}
static void e3_done(void);
static void page_load(void);
static int render(const void *unused);
/* A keyboard's up (ui.edit): a render would close it, and those screens edit only the draft, so
 * nothing can be filed wrongly while the swap waits. ponytail: left up under another page, the old
 * output's EQ plays until it closes; the rebuild would have to keep the typing to do better. */
void eq_output(const char *key) {
    if (ui.edit) return;
    if (!eq_owner[0]) owner_read();
    if (!strcmp(key, eq_owner)) return;
    eq_state now, next;
    char to[256];
    e3_done();
    if (e3.dirty || !eq_file(&now)) return; /* kept for the next pass: nothing is lost */
    eq_slot(to, sizeof to, key);
    if (!eq_load_from(to, &next)) next = now;
    /* Off to Off is silent, so the filter stays out of the chain (peq_chain may still want it). */
    if (memcmp(&next, &now, sizeof next) &&
        !(now.mode == EQ_OFF && next.mode == EQ_OFF ? eq_save(&next) : eq_commit(&next)))
        return;
    /* ponytail: power lost before owner is saved files the new output's EQ under the old one, once.
     * A failed save is the same at the next boot; RAM moves on either way, as staying would file the
     * new EQ over the old slot at the next pass. */
    owner_set(key);
    if (ui.page) {
        page_load();
        if (!ui.timer) ui.timer = timer_add(render, 0, 1);
    }
}

static int action(void *ctx, void *event);

/* A row: the caption on the left, white or, for display-only rows and off bands (DIM), grey, and
 * the value, if any, in grey against the right edge, or left of stock's list_into chevron on a row
 * that opens a screen or picker (INTO: coverflow.c's row_into). Returns the last label, the
 * value's if any. Rows start under the graph where it heads the list. */
enum { DIM = 1, INTO = 2, SMALL = 4, LONG = 8 };
/* The curve's height in the list: whole rows of the theme's, so whole rows fill it at either
 * end. */
static int graph_head(void) {
    int rh = page_h(48);
    return (PEQ_GRAPH_H + rh - 1) / rh * rh;
}
/* A label with too long a text for the theme's row font: the row style's 20 px. Returns it. */
static void *fit(void *label) {
    widget_set_prop_int(label, "style:normal:font_size", 20);
    return label;
}
/* LONG: a value of any length (a file's name), in stock's right-aligned ellipsis label (navigation.c's
 * list_value), grey beside the chevron. It stops short of the caption at x 130, past "Presets" in
 * the widest row font at 27 px (ends at x 118). Stock's label keeps 2 px each side; none on
 * the right ends a name that fits where the row's other values do. Stock draws a cut one from the
 * label's left, so it can end up to a letter short of them, and scrolls it while the row is selected
 * (only_focus: the marquee's). ponytail: a fixed column, not the caption's measured width, so a theme
 * with a wider row font has to move it. */
static void *long_value(void *item, const char *value, int rh) {
    void *label = hscroll_label_create(item, 130, 0, 316 - 130, rh);
    widget_use_style(label, "s_scrlabel_white20r");
    widget_set_prop_int(label, "style:normal:text_color", (int)CF_GREY);
    widget_set_prop_int(label, "style:normal:margin_right", 0);
    hscroll_label_set_only_focus(label, 1);
    hscroll_label_set_ellipses(label, 1);
    widget_set_text_utf8(label, value);
    theme_font(label, ROLE_TITLE, FONT_ROW);
    return fit(label);
}
static void *row_dim(void *view, int index, const char *text, const char *value, int id, int flags) {
    int rh = page_h(48);
    void *item = list_item_create(view, 0, (ui.graph ? graph_head() : 0) + index * rh, 375, rh),
         *label = 0;
    widget_use_style(item, "s_listitem_black");
    for (int i = 0; i < 1 + (value && !(flags & LONG)); ++i) {
        label = label_create(item, ROW_X, 0, 375 - 2 * ROW_X, rh);
        widget_use_style(label, "s_label_white20l");
        if (i || flags & DIM || id < 0) widget_set_prop_int(label, "style:normal:text_color", (int)CF_GREY);
        if (i) widget_set_prop_str(label, "style:normal:text_align_h", "right");
        widget_set_text_utf8(label, i ? value : text);
        theme_font(label, ROLE_TITLE, FONT_ROW); /* by its text */
        if (!i && flags & SMALL) widget_set_prop_int(label, "style:normal:font_size", 16);
    }
    if (value && flags & LONG) label = long_value(item, value, rh);
    if (flags & INTO) row_into(item, rh);
    widget_on(item, EVT_CLICK, action, (void *)(long)id);
    return label;
}
#define row(view, index, text, id) row_dim(view, index, text, 0, id, 0)

static float graph_y(double db) {
    if (db > PEQ_GRAPH_DB) db = PEQ_GRAPH_DB;
    if (db < -PEQ_GRAPH_DB) db = -PEQ_GRAPH_DB;
    return (float)((PEQ_PLOT_TOP + PEQ_PLOT_BOTTOM) / 2.0 - db * (PEQ_PLOT_BOTTOM - PEQ_PLOT_TOP) / (2.0 * PEQ_GRAPH_DB));
}
static float graph_x(double f) { return (float)(PEQ_GRAPH_X + (GRAPH_W - 1) * log(f / 20) / log(1000)); }

/* The draft's response, one point a column, for peq_paint. */
static void curve(void) {
    peq_engine e;
    int ok = peq_compile(&ui.draft, 48000, &e);
    for (int x = 0; x < GRAPH_W; ++x) {
        double l = 1, r = 1;
        if (ok) peq_response(&e, 48000, 20 * pow(1000, x / (GRAPH_W - 1.0)), &l, &r);
        ui.curve[0][x] = graph_y(decibels(l));
        ui.curve[1][x] = graph_y(decibels(r));
    }
}

/* Auto, PEQ's and 3EQ's: one saved cut that covers every standard PCM rate's peak, so tracks at
 * different rates play at one level and the player works nothing out at a track's start. Kept until
 * a band changes, as preamp, balance and the switch move none: the bands are known again by two
 * FNV-1a hashes of them from two seeds, 8 bytes in place of a copy of the preset. ponytail: two
 * band sets with both hashes equal would share a cut (odds about 1 in 2^64); no bound for
 * nonstandard rates (37.8 kHz and the like), whose peak can sit between two of these. */
#define HEADROOM_SEED2 0x9e3779b9u
static double headroom(const peq_preset *p) {
    static const int rates[] = {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100,
                               48000, 64000, 88200, 96000, 176400, 192000, 352800, 384000};
    static unsigned cached[2];
    static int have_cache;
    static double gain;
    peq_preset key = *p;
    key.preamp = key.balance = 0, key.bypass = key.automatic = key.spare = 0;
    if (!peq_valid(&key)) return p->preamp;
    unsigned h[2] = {hash_bytes(FNV_SEED, (const unsigned char *)&key, sizeof key),
                     hash_bytes(HEADROOM_SEED2, (const unsigned char *)&key, sizeof key)};
    if (have_cache && h[0] == cached[0] && h[1] == cached[1]) return gain;
    gain = 0;
    for (unsigned i = 0; i < sizeof(rates) / sizeof(*rates); ++i) {
        double g = peq_auto(&key, rates[i]);
        if (g < gain) gain = g;
    }
    cached[0] = h[0], cached[1] = h[1];
    have_cache = 1;
    return gain;
}

static double *picked(void) {
    return (double *[]){&ui.draft.balance, &ui.draft.preamp, &ui.draft.bands[ui.band].gain}[ui.pick - BALANCE];
}

/* The value menu's edit closed with new text: apply it if it is a number (clamped like Raise/Lower), else restore it. */
static int typed(void *ctx, void *event) {
    if (!ui.edit) return 0;
    char s[16];
    if (widget_get_text_utf8(ui.edit, s, sizeof s)) s[0] = 0;
    if (peq_number(s, &ui.typed)) action((void *)(long)TYPED, 0);
    else if (!ui.timer) ui.timer = timer_add(render, 0, 1); /* show the value again */
    return 0;
}

/* The rename edit's Done (EVT_IM_ACTION, and EVT_VALUE_CHANGED, which stock sends only for a
 * changed text) renames, once: Done sends both. */
static int named(void *ctx, void *event) {
    if (ui.edit && ui.screen == NAMING) action((void *)(long)NAMED, 0);
    return 0;
}

/* Open T9's "123" page and select the old value so the first key replaces it.
 * The keyboard may open after focus; look for it every 16 ms, up to 20 times. It is the input
 * method's own, for this edit: a lookup by name could find the last one while it still closes. */
static int numbers(const void *unused) {
    void *im = input_method(),
         *kb = im && ui.edit && P(im, IM_WIDGET) == ui.edit ? P(im, IM_KEYBOARD) : 0;
    char s[16];
    if (kb) pages_set_active_by_name(widget_lookup(kb, "panel", 1), "symnum");
    if (kb && ui.edit && !widget_get_text_utf8(ui.edit, s, sizeof s)) edit_set_select(ui.edit, 0, strlen(s));
    if (kb || ++ui.tries >= 20) { ui.keyboard_timer = 0; return 7; }
    return 8; /* RET_REPEAT */
}

static int focused(void *ctx, void *event) {
    stop_timer(&ui.keyboard_timer);
    ui.tries = 0;
    ui.keyboard_timer = timer_add(numbers, 0, 16);
    return 0;
}

/* Auto unless the preamp was picked (2): imports and presets or states from before 1.3.6 take it. */
static int on_auto(void) { return ui.draft.automatic != 2; }

/* A draft on Auto takes the cut for every rate, which plays as saved. */
static void follow_auto(void) {
    if (on_auto()) ui.draft.automatic = 1, ui.draft.preamp = headroom(&ui.draft);
}

/* The Preamp row's and the picker's Auto. */
static void auto_text(char *s, unsigned n) {
    snprintf(s, n, "Auto (%.1f dB)", headroom(&ui.draft));
}

/* A Preamp pick sticks to the preset on the card it was loaded from, written out again (peq_export);
 * its bands stay as saved until Save. A file with lines the parser left out stays as it is: written
 * out, it would lose them for Equalizer APO, so its pick lasts until another load. ponytail: the
 * file's own comments go, and keyed on ui.name, a stock preset named like a saved one ("Rock")
 * writes to that file; keep the loaded path if that matters. */
static void remember_preamp(void) {
    char path[600];
    peq_preset p;
    peq_error e;
    snprintf(path, sizeof(path), PEQ_FOLDER "/%s.txt", ui.name);
    if (!ui.name[0] || !peq_import_file(path, &p, &e) || e.skipped) return; /* a stock preset, Reset or none */
    p.automatic = ui.draft.automatic, p.preamp = ui.draft.preamp;
    peq_export_file(path, &p, 1);
}

static int compare_names(const void *a, const void *b) { return strcmp(a, b); }

static void list_files(const char *folder, const char *extension) {
    ui.count = 0;
    void *dir = opendir(folder);
    if (!dir) { snprintf(ui.status, sizeof(ui.status), "Folder missing or unavailable"); return; }
    struct dirent *entry;
    while ((entry = readdir(dir))) {
        const char *name = entry->d_name;
        unsigned n = strlen(name), e = strlen(extension);
        if (entry->d_type != 8 && entry->d_type != 0) continue;
        if (n <= e || n >= 256 || strcasecmp(name + n - e, extension)) continue;
        if (ui.count == ui.capacity) {
            int capacity = ui.capacity ? ui.capacity * 2 : FILE_PAGE;
            void *names = calloc(capacity, 256);
            if (!names) {
                ui.count = 0;
                snprintf(ui.status, sizeof(ui.status), "Out of memory; cannot list presets");
                break;
            }
            if (ui.count) memcpy(names, ui.names, ui.count * 256);
            free(ui.names);
            ui.names = names;
            ui.capacity = capacity;
        }
        memcpy(ui.names[ui.count++], name, n + 1);
    }
    closedir(dir);
    if (ui.count) qsort(ui.names, ui.count, 256, compare_names);
    if (!ui.count && !ui.status[0]) snprintf(ui.status, sizeof(ui.status), "No presets found");
}

/* File name in ui.destination: sizeof skips PEQ_FOLDER and its "/". */
static const char *deleting(void) { return ui.destination + sizeof(PEQ_FOLDER); }

/* Earlier firmware's binary presets (PEQ_SAVED), written out to the card's folder once and kept
 * there for a downgrade. A text preset of the same name stays, unless it reads whole and differs: the
 * binary is then that file's import with a Preamp picked since, as remember_preamp keeps it. */
static void write_out(void) {
    char from[600], to[600];
    struct dirent *entry;
    void *dir;
    int ok = 1;
    if (!access(PEQ_SAVED "/.written", 0) || !card_mounted() || !(dir = opendir(PEQ_SAVED))) return;
    mkdir(PEQ_FOLDER, 0700);
    while ((entry = readdir(dir))) {
        unsigned n = strlen(entry->d_name);
        peq_preset p, text;
        peq_error e;
        if (n <= 4 || n >= 256 || strcmp(entry->d_name + n - 4, ".peq")) continue;
        snprintf(from, sizeof from, PEQ_SAVED "/%s", entry->d_name);
        snprintf(to, sizeof to, PEQ_FOLDER "/%.*s.txt", (int)n - 4, entry->d_name);
        if (!peq_load(from, &p)) continue;
        p.bypass = 0;
        if (p.automatic != 2) p.automatic = 1; /* never picked is Auto, as the editor takes it */
        if (!access(to, 0) && (!peq_import_file(to, &text, &e) || e.skipped || !memcmp(&text, &p, sizeof p))) continue;
        ok &= peq_export_file(to, &p, 1);
    }
    closedir(dir);
    if (ok) peq_save_file(PEQ_SAVED "/.written", 0, "", 0);
}

/* text as a preset's name: trailing spaces and dots dropped, as FAT drops them, and leading spaces;
 * 0 if that is empty, starts with a dot or holds a character the card's FAT cannot. */
static const char *preset_name(char *text) {
    unsigned n = strlen(text);
    while (n && (text[n - 1] == ' ' || text[n - 1] == '.')) text[--n] = 0;
    const char *t = text;
    while (*t == ' ') ++t;
    for (const char *c = t; *c; ++c)
        for (const char *bad = "/\\:*?\"<>|"; *bad; ++bad)
            if (*c == *bad) return 0;
    return *t && *t != '.' ? t : 0;
}

/* ui.destination (a saved preset's path) renamed to given, or with none to the edit's text, kept if
 * that is no name (preset_name), names another preset or is too long to read back (stock's
 * conversion leaves it empty, 0x72a180); an empty edit, as Return's cancel leaves it (search_key),
 * goes back to the list. The editor's and the active preset's names follow it. */
static void rename_preset(const char *given) {
    char text[256], to[600], old[256]; /* a whole name: list_files takes up to 255 bytes */
    const char *from = deleting();
    const unsigned *wide = ui.edit ? widget_get_text(ui.edit) : 0;
    if (given) snprintf(text, sizeof text, "%s", given);
    else if (!ui.edit || widget_get_text_utf8(ui.edit, text, sizeof text)) text[0] = 0;
    int typed = given || (wide && *wide);
    const char *t = preset_name(text);
    snprintf(old, sizeof old, "%.*s", (int)strlen(from) - 4, from);
    ui.screen = PRESETS;
    if (!typed) ui.screen = RENAMES;
    else if (!t)
        snprintf(ui.status, sizeof(ui.status), "Name not allowed; preset kept");
    else if (!strcmp(t, old)) return;
    else if (snprintf(to, sizeof to, PEQ_FOLDER "/%s.txt", t), strcasecmp(t, old) && !access(to, 0))
        snprintf(ui.status, sizeof(ui.status), "Name taken; preset kept");
    else if (rename(ui.destination, to))
        snprintf(ui.status, sizeof(ui.status), "Rename failed; preset kept");
    else {
        if (!strcmp(ui.name, old)) snprintf(ui.name, sizeof(ui.name), "%s", t);
        if (!strcmp(ui.applied, old)) snprintf(ui.applied, sizeof(ui.applied), "%s", t);
        snprintf(ui.status, sizeof(ui.status), "Renamed to %.100s", t);
    }
}

/* Stock's genre presets, built in: reset_user_eq's table (modes 4-11), in dB over the ten default
 * bands. Picking one loads it into the editor as a saved preset does; nothing is written. */
static const char stock_names[][10] = { "Pop", "Rock", "Dance", "Blues", "Metal", "Vocal", "Classical", "Jazz" };
static const signed char stock_gains[][10] = {
    { 3, 1, 0, -2, -4, -4, -2, 0, 1, 2 }, { -2, 0, 2, 4, -2, -2, 0, 0, 4, 4 },
    { -2, 3, 4, 1, -2, -2, 0, 0, 4, 4 },  { -2, 0, 2, 1, 0, 0, 0, 0, -2, -4 },
    { -6, 0, 0, 0, 0, 0, 2, 0, 2, 0 },    { -4, 0, 2, 1, 0, 0, 0, 0, -4, -6 },
    { 0, 3, 3, 2, 0, 0, 0, 0, 1, 1 },     { 0, 0, 0, 2, 2, 2, 0, 1, 2, 2 },
};
#define STOCK_N (int)(sizeof(stock_names) / sizeof(*stock_names))

/* Into the editor, not active: Apply is the activation step. */
static void load_draft(peq_preset *p, const char *name, int length) {
    p->bypass = ui.draft.bypass; /* ON/OFF is live state, not part of the edit */
    ui.draft = *p;
    follow_auto();
    ui.dirty = 1;
    snprintf(ui.name, sizeof(ui.name), "%.*s", length, name);
    ui.screen = HOME;
    snprintf(ui.status, sizeof(ui.status), "Preset loaded; choose Apply to activate");
}

/* A text box on item at y, h high, typed on the letter strip (keyboard.c), ending on its OK (the EQ page's sits in the
 * title's place, 2 px under the status row's foot: render_rows). */
void *edit_create(void *item, const char *input_type, int y, int h) {
    void *edit = widget_factory_create_widget(widget_factory(), "edit", item, 12, y, 351, h);
    static const char *const props[][2] = {{"keyboard", "kb_default_t9"}, {"input_type", 0}, {"action_text", "done"},
        {"bg_color", "#2B2B2B"}, {"border_color", "#2B2B2B00"}, {"text_color", "#FFFFFF"}, {"round_radius", 0},
        {"margin_left", "12"}, {"font_size", "22"}};
    static const char *const states[] = {"normal", "focused", "empty", "empty_focus", "changed", "error", "over", "empty_over"};
    char name[48], radius[12];
    snprintf(radius, sizeof(radius), "%d", h / 2);
    for (unsigned i = 0; i < sizeof(props) / sizeof(props[0]); ++i) {
        if (i < 3) { widget_set_prop_str(edit, props[i][0], i == 1 ? input_type : props[i][1]); continue; }
        for (unsigned j = 0; j < sizeof(states) / sizeof(states[0]); ++j) {
            snprintf(name, sizeof(name), "style:%s:%s", states[j], props[i][0]);
            widget_set_prop_str(edit, name, props[i][1] ? props[i][1] : radius);
        }
    }
    widget_set_name(edit, EDIT_NAME);
    return edit;
}

static void save_candidate(int replace) {
    mkdir(PEQ_FOLDER, 0700);
    int result = peq_export_file(ui.destination, &ui.candidate, replace);
    if (result == 2) { ui.previous = ui.screen; ui.screen = CONFIRM; }
    else snprintf(ui.status, sizeof(ui.status), result ? "Preset saved; active EQ unchanged" : "Save failed; settings unchanged");
}

/* The draft into the saved state under its name, playing if it shows On or on is set (Simple's
 * Presets; 3EQ goes off). 0 if it cannot be saved. */
static int apply(int on) {
    eq_state s;
    eq_load(&s);
    s.peq = ui.draft;
    if (on) s.peq.bypass = s.hold = 0;
    if (!s.peq.bypass) s.mode = EQ_PEQ; /* the draft shows On only while PEQ plays */
    if (!eq_commit(&s)) return 0;
    ui.draft.bypass = s.peq.bypass;
    ui.dirty = 0;
    memcpy(ui.applied, ui.name, sizeof(ui.name));
    ui.named = ui.draft;
    return 1;
}

static void load(eq_state *s);
static void e3_show(void);
static int action(void *ctx, void *event);

/* The Type picker's choice, as a click on the TYPES screen's row of it. The band's list comes back
 * as a kept one (render), its row count given: on a new view without it, ringnav takes the first
 * row once the panel has closed, not the Type row the list was left on. */
static void type_picked(int i) {
    if (ui.view && ui.screen == BAND) {
        ui.kept[BAND][0] = widget_get_prop_int(ui.view, "_ringnav_index", 0) + 1;
        ui.kept[BAND][1] = widget_get_prop_int(ui.view, "yoffset", 0);
        ui.rendered = -1;
    }
    ui.screen = TYPES;
    action((void *)(long)i, 0);
}

static int action(void *ctx, void *event) {
    int id = (int)(long)ctx;
    if (id < 0) return 0; /* display-only rows */
    int screen = ui.screen;
    /* The centre button on the value row; no render, which would destroy the edit and close the keyboard. */
    if (id == KEYBOARD) {
        widget_set_focused(ui.edit, 1);
        /* Done can close T9 while the edit stays focused; focus alone won't reopen it, nor send
         * EVT_FOCUS, so the number page and selection are asked for here too. */
        input_method_request(input_method(), ui.edit);
        focused(0, 0);
        return 0;
    }
    if (id == NAMED && ui.screen != NAMING) return 0; /* the row's click after the edit's blur renamed */
    peq_band *b = &ui.draft.bands[ui.band];
    ui.status[0] = 0;
    ui.tabs = 0;
    /* Band edits keep the preamp on Auto (just enough cut for the boosts) only if it was there. */
    int edit = (id >= ENABLE && id < STEP && id != TYPE) ||
               (id < 256 && (ui.screen == TYPES || (ui.screen == PICK && ui.pick == GAIN)));
    if (id == BACK) {
        if (ui.screen == HOME) { navigator_back(); return 0; }
        if (ui.screen == PICK) ui.screen = ui.pick == GAIN ? BAND : HOME;
        else if (ui.screen == ADJUST || ui.screen == TYPES) ui.screen = BAND;
        else if (ui.screen == NAMING) ui.screen = RENAMES;
        else ui.screen = ui.from_simple || ui.screen == BAND || ui.screen == PRESETS ? HOME : PRESETS;
    } else if (id == MENU) ui.screen = PRESETS;
    else if (id == FILE_PREV || id == FILE_NEXT) {
        ui.file_page += id == FILE_NEXT ? 1 : -1;
        ui.rendered = -1;
    }
    else if (id == STOCK) ui.screen = STOCKS;
    else if (id == DELETE) ui.screen = DELETES;
    else if (id == RENAME) ui.screen = RENAMES;
    else if (id == NAMED) rename_preset(0);
    else if (id >= BALANCE && id <= GAIN) { ui.pick = id; ui.screen = PICK; }
    else if (id == FREQUENCY || id == QUALITY) { ui.adjust = id; ui.screen = ADJUST; }
    else if (id == SAVE) ui.screen = SAVES;
    else if (id == BYPASS) {
        /* Takes effect at once and keeps unapplied band edits out of the active preset. On turns 3EQ off. */
        eq_state s;
        eq_load(&s);
        s.mode = ui.draft.bypass ? EQ_PEQ : EQ_OFF;
        s.hold = 0;
        if (eq_commit(&s)) ui.draft.bypass = s.mode != EQ_PEQ;
        else snprintf(ui.status, sizeof(ui.status), "Switch failed; EQ unchanged");
    }
    else if (id == PER_OUTPUT) ringnav_eq_output_toggle();
    else if (id == ENABLE) b->enabled = !b->enabled;
    /* The Type picker: the menu sheet's (docs/oled-design.md#menus), else, when the sheet can't
     * open, a screen of the types; either opens on the band's own. Under the sheet the band's list
     * is left as it is, no render, so a pick or Return finds it on the Type row. */
    else if (id == TYPE) {
        if (modal_pick((void *)0, types, PEQ_TYPES, b->type, type_picked)) return 0;
        ui.screen = TYPES;
    }
    else if (id == CHANNEL && b->enabled) b->enabled = b->enabled % 3 + 1; /* both, left, right */
    else if (id == STEP) ui.step = (ui.step + 1) % 4;
    else if (id == RAISE || id == LOWER || id == TYPED) {
        int k = ui.adjust == QUALITY;
        double *value = k ? &b->q : &b->frequency, step = k ? 0.05 : steps[ui.step];
        *value = id == TYPED ? ui.typed : *value + (id == RAISE ? step : -step);
        if (*value < (k ? 0.1 : 20)) *value = k ? 0.1 : 20;
        if (*value > (k ? PEQ_Q_MAX(b->type) : 20000)) *value = k ? PEQ_Q_MAX(b->type) : 20000;
    } else if (id == APPLY) {
        snprintf(ui.status, sizeof(ui.status), apply(0) ? "Applied" : "Apply failed; active EQ unchanged");
    } else if (id == RESET) {
        /* The draft returns to peq_default; On/Off is live state, not part of the edit. */
        int bypass = ui.draft.bypass;
        peq_default(&ui.draft);
        ui.draft.bypass = bypass;
        follow_auto();
        ui.name[0] = 0;
        ui.dirty = 1;
        snprintf(ui.status, sizeof(ui.status), "Reset; choose Apply to activate");
    } else if (id == YES) {
        ui.screen = ui.previous;
        if (ui.screen != DELETES) save_candidate(1);
        /* Saved presets are copies; the active EQ and the editor draft are untouched. */
        else if (unlink(ui.destination)) snprintf(ui.status, sizeof(ui.status), "Delete failed; preset kept");
        else snprintf(ui.status, sizeof(ui.status), "Deleted %.100s; active EQ unchanged", deleting());
    } else if (id == CANCEL) ui.screen = ui.previous;
    else if (id < 256) {
        int file = ui.file_page * FILE_PAGE + id;
        if (ui.screen == HOME && id < PEQ_BANDS) { ui.band = id; ui.screen = BAND; }
        else if (ui.screen == PICK && id < picks[ui.pick - BALANCE].rows) {
            /* Preamp's Auto row: the band-edit cut, which later band edits then keep up to date. */
            *picked() = ui.pick == PREAMP && !id
                            ? headroom(&ui.draft)
                            : picks[ui.pick - BALANCE].first + picks[ui.pick - BALANCE].step * id;
            if (ui.pick == PREAMP) ui.draft.automatic = id ? 2 : 1, remember_preamp();
            if (ui.pick == GAIN && !b->enabled) b->enabled = 1; /* picking a gain is using the band */
            ui.dirty = 1;
            ui.screen = ui.pick == GAIN ? BAND : HOME;
        }
        else if (ui.screen == TYPES && id < PEQ_TYPES) {
            b->type = id;
            if (b->q > PEQ_Q_MAX(id)) b->q = PEQ_Q_MAX(id); /* a notch's Q past the others' */
            ui.screen = BAND;
        }
        else if (ui.screen == STOCKS && id < STOCK_N) {
            peq_preset p;
            peq_default(&p);
            for (int k = 0; k < 10; ++k) p.bands[k].enabled = (p.bands[k].gain = stock_gains[id][k]) != 0;
            p.automatic = 1; /* load_draft sets the preamp */
            load_draft(&p, stock_names[id], sizeof(stock_names[id]));
        }
        else if (ui.screen == SAVES && id < 10) {
            snprintf(ui.destination, sizeof(ui.destination), PEQ_FOLDER "/Manual %02d.txt", id + 1);
            ui.candidate = ui.draft;
            save_candidate(0);
        } else if (file < ui.count && ui.screen == RENAMES) {
            snprintf(ui.destination, sizeof(ui.destination), PEQ_FOLDER "/%s", ui.names[file]);
            ui.screen = NAMING;
        } else if (file < ui.count && ui.screen == DELETES) {
            snprintf(ui.destination, sizeof(ui.destination), PEQ_FOLDER "/%s", ui.names[file]);
            ui.previous = DELETES;
            ui.screen = CONFIRM;
        } else if (file < ui.count && ui.screen == PRESETS) {
            char path[600];
            peq_preset p;
            peq_error error;
            snprintf(path, sizeof(path), PEQ_FOLDER "/%s", ui.names[file]);
            /* Each message fits OLED's toast, 43 characters or so (docs/oled-design.md#dialogs-and-toasts). */
            if (!peq_import_file(path, &p, &error)) /* line 0: the whole file's (GraphicEQ) */
                snprintf(ui.status, sizeof(ui.status), error.line ? "Line %u: %s" : "%.0u%s", error.line, error.reason);
            else {
                load_draft(&p, ui.names[file], (int)strlen(ui.names[file]) - 4);
                /* What it left out, by the first: "Loaded; skipped HP filter, 2 more" */
                if (error.skipped == 1) snprintf(ui.status, sizeof(ui.status), "Loaded; skipped %.17s", error.what);
                else if (error.skipped)
                    snprintf(ui.status, sizeof(ui.status), "Loaded; skipped %.17s, %u more", error.what, error.skipped - 1);
            }
        }
    }
    if (edit) { /* ENABLE, RAISE, LOWER, TYPED, CHANNEL, a picked type and a picked gain */
        ui.dirty = 1;
        follow_auto();
    }
    /* Simple's Presets: Back to Advanced's main screen returns to Simple, and so does a pick
     * (load_draft), which plays at once. */
    if (ui.from_simple && ui.screen == HOME) {
        int ok = id == BACK || apply(1);
        eq_state s;
        load(&s);
        ui.from_simple = 0;
        ui.tab = SIMPLE;
        ui.status[0] = 0;
        e3.focus = E3_PRESETS;
        e3.status = ok ? 0 : "Apply failed; active EQ unchanged";
    }
    if (screen != ui.screen) ui.file_page = 0;
    if (!ui.timer) ui.timer = timer_add(render, 0, 1);
    /* What an action did is a toast over the page, which keeps its title
     * (docs/oled-design.md#dialogs-and-toasts); the render runs under it. */
    if (ui.status[0]) {
        char said[sizeof ui.status];
        memcpy(said, ui.status, sizeof said);
        ui.status[0] = 0;
#if Q2POD_DEV
        if (q2eq_busy) memcpy(q2eq_said, said, sizeof said); /* the reply's, not a toast */
        else
#endif
        toast(said);
    }
    return 0;
}

static void *bar(void *page, int x, int w);
static int e3_touch(void *ctx, void *event);

static int render_rows(const void *unused);
/* Delete's and Replace's question, on the confirmation card (docs/oled-design.md#confirmation-card)
 * over the screen that asked: CONFIRM stays the state (q2eq answers it), the rows its previous
 * screen's; the card's answer is that screen's YES or CANCEL. */
static void eq_answer(void *ctx, int row, int toggle_on) {
    if (ui.page && ui.screen == CONFIRM) action((void *)(long)(row == 1 ? YES : CANCEL), 0);
}
static void eq_ask(void) {
    static const char *const del[] = { "Cancel", "Delete" }, *const rep[] = { "Cancel", "Replace" };
    int deletes = ui.previous == DELETES;
    confirm(&(confirm_t){ .title = deletes ? "Delete \"%s\"?" : "Replace \"%s\"?", .name = deleting(),
                          .body = deletes ? "The EQ playing now stays as it is." : "The preset of that name is saved over.",
                          .rows = deletes ? del : rep, .n_rows = 2, .kind = CONFIRM_DESTRUCTIVE, .back = -1,
                          .answer = eq_answer });
}
static int render(const void *unused) {
    if (ui.screen != CONFIRM) return render_rows(unused);
    ui.screen = ui.previous; /* the page that asked stays in sight under the card */
    int r = render_rows(unused);
    ui.screen = CONFIRM;
    if (ui.page && !confirm_up()) eq_ask();
    return r;
}
static int render_rows(const void *unused) {
    ui.timer = 0;
    if (!ui.page) return 7;
    stop_timer(&ui.keyboard_timer);
    /* Release T9 before replacing its editing target, as the search fields do. */
    if (ui.edit) {
        ui.edit = 0; /* Ignore blur callbacks from the field being replaced. */
        input_method_request(input_method(), 0);
    }
    if (ui.tab == SIMPLE) { /* the sliders' view, under the tabs */
        ui.view = ui.graph = ui.edit = 0;
        ui.rendered = -1;
        widget_destroy_children(ui.page);
        e3.view = widget_factory_create_widget(widget_factory(), "view", ui.page, 0, bar_foot(ui.page),
                                               375, e3.h);
        widget_on(e3.view, EVT_POINTER_DOWN, e3_touch, 0);
        ui.bar = bar(ui.page, 0, 375);
        e3_show();
        return 7;
    }
    e3.view = 0;
    int selection = 0, offset = 0, kept = 0;
    /* foot: the status row's in the page's window, which takes the whole screen
     * (docs/oled-design.md#full-bleed); every child starts there, and height is the rows under it. */
    int foot = bar_foot(ui.page), height = page_room(ui.page),
        graph = ui.screen == HOME || ui.screen == BAND;
    int head = graph * graph_head(), rh = page_h(48);
    /* shown: the whole rows in view, a set_switch theme's ending above the bottom corners' glass,
     * as its other lists do (list_end). rows, the list's box: on to the window's foot, its rows
     * past the list end fading into it (navigation.c list_fade), where the wheel's end leaves the
     * last row on the last whole row above the end (fit_top). */
    int shown = ((eq_rows() ? eq_list_end() : height) - 48) / rh * rh, rows = height - 48;
    if (ui.view && ui.rendered != ui.screen && (ui.rendered == HOME || ui.rendered == BAND)) {
        ui.kept[ui.rendered][0] = widget_get_prop_int(ui.view, "_ringnav_index", 0) + 1;
        ui.kept[ui.rendered][1] = widget_get_prop_int(ui.view, "yoffset", 0);
    }
    if (ui.screen == HOME) ui.kept[BAND][0] = 0; /* the next band opens at its top */
    if (ui.view && ui.rendered == ui.screen) {
        selection = widget_get_prop_int(ui.view, "_ringnav_index", 0);
        offset = widget_get_prop_int(ui.view, "yoffset", 0);
    } else if ((ui.screen == HOME || ui.screen == BAND) && ui.kept[ui.screen][0] > 0) {
        selection = ui.kept[ui.screen][0] - 1;
        offset = ui.kept[ui.screen][1];
        ui.kept[ui.screen][0] = 0;
        kept = 1;
    } else if (ui.screen == TYPES) { /* opens on the band's type, centred as a picker's value */
        selection = ui.draft.bands[ui.band].type;
        offset = selection * rh - (shown - rh) / 2;
        offset = offset < 0 ? 0 : offset > PEQ_TYPES * rh - shown ? PEQ_TYPES * rh - shown : offset;
        if (offset < 0) offset = 0; /* a list tall enough for all eight */
    } else if (ui.screen == PICK) { /* opens on the nearest row to the current value (Auto if on it), centred */
        int last = picks[ui.pick - BALANCE].rows - 1;
        double r = (*picked() - picks[ui.pick - BALANCE].first) / picks[ui.pick - BALANCE].step + 0.5;
        selection = r < 0 ? 0 : r > last ? last : (int)r;
        if (ui.pick == PREAMP && on_auto()) selection = 0;
        else if (ui.pick == PREAMP && !selection) selection = 1;
        offset = selection * rh - (shown - rh) / 2;
        offset = offset < 0 ? 0 : offset > (last + 1) * rh - shown ? (last + 1) * rh - shown : offset;
    }
    ui.rendered = ui.screen;
    ui.edit = 0;
    widget_destroy_children(ui.page);
    /* No item_height: stock's layout (0x5fd484) would give the graph a row's height; the rows are
     * the theme's high themselves. */
    void *list = list_view_create(ui.page, 0, foot + 48, 375, rows);
    /* The theme's default list_view is a light card; stock pages paint theirs black inline. */
    widget_set_prop_int(list, "style:normal:bg_color", (int)0xff000000u);
    widget_set_prop_int(list, "style:normal:border_color", 0);
    void *view = scroll_view_create(list, 0, 0, 375, rows);
    ui.view = view;
    widget_set_prop_int(view, "yslidable", 1);
    widget_set_prop_int(view, "xslidable", 0);
    widget_set_prop_int(view, "_ringnav_index", ui.tabs && ui.screen == HOME ? -1 : selection);
    /* The graph heads the list and scrolls off with it. It takes no click, so ringnav's rows, row 0
     * first, are the ones under it; peq_paint draws it from its own paint, inside the list's clip. */
    ui.graph = graph ? widget_factory_create_widget(widget_factory(), "view", view, 0, 0, 375, head) : 0;
    if (graph) curve();
    char text[160], value[32];
    int n = 0;
    /* No Back row: Return steps back on every screen (keyup). */
    if (ui.screen == HOME) {
        row(view, n++, ui.dirty ? "Apply changes" : "Nothing to apply", ui.dirty ? APPLY : -1);
        row(view, n++, "Reset to defaults", RESET);
        list_toggle(row(view, n++, "", BYPASS), "Enabled", !ui.draft.bypass);
        list_toggle(row(view, n++, "", PER_OUTPUT), "EQ per Output", ringnav_eq_output());
        /* Balance turns one side down: R 1.0 dB is the left 1 dB quieter. */
        snprintf(value, sizeof(value), __builtin_fabs(ui.draft.balance) >= 0.05 ? "%s %.1f dB" : "Center",
                 ui.draft.balance > 0 ? "R" : "L", __builtin_fabs(ui.draft.balance));
        row_dim(view, n++, "Balance", value, BALANCE, INTO);
        if (on_auto())
            auto_text(value, sizeof(value));
        else
            snprintf(value, sizeof(value), "%.1f dB", ui.draft.preamp);
        row_dim(view, n++, "Preamp", value, PREAMP, INTO);
        /* the draft's preset */
        row_dim(view, n++, "Presets", ui.name[0] ? ui.name : 0, MENU, INTO | LONG);
        /* 16px band captions leave room for the gain and chevron, even at 20000 Hz on one channel. */
        for (int i = 0; i < PEQ_BANDS && (i < 10 || i <= ui.draft.count); ++i) {
            peq_band *b = &ui.draft.bands[i];
            snprintf(text, sizeof(text), "%d  %s %.0f Hz%s", i + 1, types[b->type], b->frequency,
                     (const char *[]){"", "", " L", " R"}[b->enabled]);
            snprintf(value, sizeof(value), !b->enabled ? "Off" : PEQ_GAINED(b->type) ? "%+.1f dB" : "On", b->gain);
            fit(row_dim(view, n++, text, value, i, (b->enabled ? 0 : DIM) | INTO | SMALL));
        }
    } else if (ui.screen == BAND) {
        peq_band *b = &ui.draft.bands[ui.band];
        /* Imported missing bands already have valid defaults from peq_default. */
        if (ui.draft.count <= ui.band) ui.draft.count = ui.band + 1;
        list_toggle(row(view, n++, "", ENABLE), "Band", b->enabled);
        if (b->enabled) row_dim(view, n++, "Channels", (const char *[]){0, "Both", "Left", "Right"}[b->enabled], CHANNEL, 0);
        row_dim(view, n++, "Type", types[b->type], TYPE, INTO);
        snprintf(value, sizeof(value), "%.0f Hz", b->frequency); row_dim(view, n++, "Frequency", value, FREQUENCY, INTO);
        /* a pass, notch or all-pass has no gain to set */
        if (PEQ_GAINED(b->type)) {
            snprintf(value, sizeof(value), "%+.1f dB", b->gain);
            row_dim(view, n++, "Gain", value, GAIN, INTO);
        }
        snprintf(value, sizeof(value), "%.2f", b->q); row_dim(view, n++, "Q", value, QUALITY, INTO);
    } else if (ui.screen == ADJUST) {
        /* Row 0 names the value; it and the centre button open the keyboard on the edit over the
         * title (below). */
        snprintf(text, sizeof(text), ui.adjust == QUALITY ? "Band %d Q" : "Band %d Frequency (Hz)",
                 ui.band + 1);
        row(view, n++, text, KEYBOARD);
        if (ui.adjust == QUALITY) snprintf(value, sizeof(value), "0.05");
        else snprintf(value, sizeof(value), "%d Hz", steps[ui.step]);
        row_dim(view, n++, "Raise", value, RAISE, 0);
        row_dim(view, n++, "Lower", value, LOWER, 0);
        if (ui.adjust != QUALITY) row_dim(view, n++, "Step", value, STEP, 0);
    } else if (ui.screen == PICK) {
        for (int i = 0; i < picks[ui.pick - BALANCE].rows; ++i) {
            double v = picks[ui.pick - BALANCE].first + picks[ui.pick - BALANCE].step * i;
            if (ui.pick == BALANCE) /* L 12.0 dB to R 12.0 dB */
                snprintf(text, sizeof(text), i == 24 ? "Center" : "%s %.1f dB", v < 0 ? "L" : "R", __builtin_fabs(v));
            else if (ui.pick == PREAMP && !i)
                auto_text(text, sizeof(text));
            else snprintf(text, sizeof(text), "%+.1f dB", v);
            void *shown = row(view, n++, text, i);
            if (ui.pick == PREAMP && !i) fit(shown);
        }
    } else if (ui.screen == TYPES) {
        for (int i = 0; i < PEQ_TYPES; ++i) row(view, n++, types[i], i);
    } else if (ui.screen == NAMING) { /* the name in the edit over the title (below); the row is Done */
        row(view, n++, "Rename", NAMED);
    } else if (ui.screen == STOCKS) {
        for (int i = 0; i < STOCK_N; ++i) row(view, n++, stock_names[i], i);
    } else if (ui.screen == SAVES) {
        for (int i = 0; i < 10; ++i) {
            snprintf(text, sizeof(text), "Save as Manual %02d", i+1); row(view, n++, text, i);
        }
    } else {
        if (ui.screen == PRESETS) {
            row_dim(view, n++, "Stock presets", 0, STOCK, INTO);
            row_dim(view, n++, "Save editor preset", 0, SAVE, INTO);
            row_dim(view, n++, "Rename a preset", 0, RENAME, INTO);
            row_dim(view, n++, "Delete a preset", 0, DELETE, INTO);
        }
        write_out();
        list_files(PEQ_FOLDER, ".txt");
        int last = ui.count ? (ui.count - 1) / FILE_PAGE : 0;
        if (ui.file_page < 0) ui.file_page = 0;
        if (ui.file_page > last) { ui.file_page = last; selection = offset = 0; }
        if (ui.file_page) row(view, n++, "Previous page", FILE_PREV);
        if (ui.file_page < last) row(view, n++, "Next page", FILE_NEXT);
        for (int i = 0, start = ui.file_page * FILE_PAGE; i < FILE_PAGE && start + i < ui.count; ++i)
            row(view, n++, ui.names[start + i], i);
    }
    widget_set_prop_int(view, "virtual_h", head + n * rh);
    /* ringnav keeps the selection above on a view whose row count it already knows; on any other
     * new view it takes its own remembered row (navigation.c load) */
    if (ui.screen == PICK || ui.screen == TYPES || kept) widget_set_prop_int(view, "_ringnav_count", n);
    if (head + n * rh <
        rows) { /* short lists: shrink so the list's white background never shows below the rows */
        widget_resize(list, 375, head + n * rh);
        widget_resize(view, 375, head + n * rh);
    }
    scroll_view_set_offset(view, 0, offset);
    /* A message takes the title bar until the next action, so it never shrinks the list; on the
     * main screen the bar otherwise shows the tabs (peq_paint). */
    void *title = ui.screen == HOME ? bar(ui.page, 0, 375) : bar(ui.page, 8, 359);
    ui.bar = ui.screen == HOME ? title : 0;
    text[0] = 0;
    if (ui.status[0]) snprintf(text, sizeof(text), "%s", ui.status);
    else if (ui.screen == BAND)
        snprintf(text, sizeof(text), "Band %d", ui.band + 1);
    else if (ui.screen == PICK && ui.pick == GAIN) snprintf(text, sizeof(text), "Band %d Gain", ui.band + 1);
    else if (ui.screen == TYPES) snprintf(text, sizeof(text), "Band %d Type", ui.band + 1);
    else if (ui.screen == PICK)
        snprintf(text, sizeof(text), ui.pick == PREAMP ? "Preamp" : "Balance");
    else if ((ui.screen == PRESETS || ui.screen == DELETES || ui.screen == RENAMES) &&
             ui.count > FILE_PAGE)
        snprintf(text, sizeof(text), "Presets %d/%d", ui.file_page + 1, (ui.count - 1) / FILE_PAGE + 1);
    else if (ui.screen != HOME && ui.screen != ADJUST && ui.screen != NAMING)
        snprintf(text, sizeof(text), "Presets");
    widget_set_text_utf8(title, text);
    /* The value or name in an edit over the title, after it so a tap reaches the edit, as the
     * search fields': 2 px under the status row's foot, clear of the row's glyphs. The letter
     * strip's window starts at that foot (search_strip.c search_strip_box), so as it opens AWTK
     * lifts the page by the edit's last 28 px, which puts the edit just under the glyphs; the
     * strip types the text in its own Frequency, Q or Name row. Action text "done": stock edit_on_event acts on the keyboard's OK only for "done"
     * (close the keyboard, then EVT_VALUE_CHANGED) or "next". Text input: typed() validates on
     * Done, as stock's numeric edit rewrites unfinished decimals. */
    if (ui.screen == ADJUST || ui.screen == NAMING) {
        peq_band *b = &ui.draft.bands[ui.band];
        char s[256]; /* a whole name, as rename_preset reads it */
        ui.edit = edit_create(ui.page, "text", foot + 2, 26);
        if (ui.screen == NAMING)
            snprintf(s, sizeof s, "%.*s", (int)strlen(deleting()) - 4, deleting());
        else
            snprintf(s, sizeof s, ui.adjust == QUALITY ? "%.2f" : "%.0f",
                     ui.adjust == QUALITY ? b->q : b->frequency);
        widget_set_text_utf8(ui.edit, s);
        /* the letter strip types it in its row, after this label (search_strip.c search_strip_box) */
        widget_set_prop_str(ui.edit, "_strip_label", ui.screen == NAMING ? "Name" : ui.adjust == QUALITY ? "Q" : "Frequency");
        widget_set_prop_int(ui.edit, STRIP_RETURN, STRIP_SAVE); /* a name or a value saved: the check */
        widget_on(ui.edit, EVT_VALUE_CHANGED, ui.screen == NAMING ? named : typed, 0);
        if (ui.screen == ADJUST) widget_on(ui.edit, EVT_FOCUS, focused, 0);
        else {
            /* The name opens with the keyboard up and the cursor where stock's text setter leaves
             * it, at the end (0x627028). Stock's focus otherwise selects it all (an idle,
             * 0x6223f0), so a key replaced the name. Done confirms an unchanged name too. */
            widget_set_prop_int(ui.edit, "select_none_when_focused", 1);
            widget_on(ui.edit, EVT_IM_ACTION, named, 0);
            widget_set_focused(ui.edit, 1);
        }
    }
    widget_invalidate_force(ui.page, 0);
    return 7; /* RET_REMOVE */
}

/* 0xRRGGBB and an alpha to a color_t, whose bytes are r, g, b, a. */
unsigned rgba(unsigned c, unsigned a) { return a << 24 | (c & 255) << 16 | (c & 0xff00) | c >> 16; }

/* UTF-8 s centred in x, y, w, h (draw_centred); a stray continuation byte is drawn as itself. */
void caption(void *canvas, const char *s, int x, int y, int w, int h, unsigned px, unsigned color) {
    unsigned wide[32], n = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p && n < 32; ++n) {
        unsigned c = *p++;
        if (c >= 0xc0) {
            int k = c >= 0xf0 ? 3 : c >= 0xe0 ? 2 : 1;
            for (c &= 0x3f >> k; k-- && (*p & 0xc0) == 0x80;) c = c << 6 | (*p++ & 63);
        }
        wide[n] = c;
    }
    int r[4] = {x, y, w, h};
    draw_centred(canvas, wide, n, r, px, color);
}

static void path(void *vg, const float *y) {
    vgcanvas_begin_path(vg);
    vgcanvas_move_to(vg, PEQ_GRAPH_X, y[0]);
    for (int x = 1; x < GRAPH_W; ++x) vgcanvas_line_to(vg, PEQ_GRAPH_X + x, y[x]);
}

/* The graph view (ringnav_paint, after stock): a faint grid at 0, +-6 and +-12 dB and 100 Hz, 1 kHz
 * and 10 kHz, labelled in grey; the response as a 2px line in the accent's light tone,
 * fading to clear toward 0 dB, the other channel's dimmer when the two differ; a ring on each
 * band in use, the edited band's larger and filled. Off: the line alone, grey. */
static void e3_paint(void *canvas), tabs_paint(void *canvas);
void peq_paint(void *w, void *canvas) {
    if (w && w == e3.view && P(canvas, CANVAS_LCD)) e3_paint(canvas);
    if (w && w == ui.bar && P(canvas, CANVAS_LCD)) tabs_paint(canvas);
    if (!w || w != ui.graph || !P(canvas, CANVAS_LCD)) return;
    static const int lines[] = {0, 6, -6, 12, -12}, ticks[] = {100, 1000, 10000};
    static const char *const labels[] = {"100", "1k", "10k"};
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    unsigned tone = ui.draft.bypass ? 0x666666 : TONE;
    float zero = graph_y(0);
    for (int i = 0; i < 5; ++i) {
        canvas_set_fill_color(canvas, rgba(i ? 0x262626 : 0x4a4a4a, 255));
        canvas_fill_rect(canvas, PEQ_GRAPH_X, (int)graph_y(lines[i]), GRAPH_W, 1);
    }
    for (int i = 0; i < 3; ++i) {
        int x = (int)graph_x(ticks[i]);
        canvas_set_fill_color(canvas, rgba(0x262626, 255));
        canvas_fill_rect(canvas, x, PEQ_PLOT_TOP, 1, PEQ_PLOT_BOTTOM - PEQ_PLOT_TOP);
        caption(canvas, labels[i], x - 16, PEQ_PLOT_BOTTOM + 2, 32, PEQ_GRAPH_H - PEQ_PLOT_BOTTOM - 2, 12, CF_GREY);
    }
    stock_canvas_set_fill_color(canvas, fill);
    void *vg = canvas_get_vgcanvas(canvas);
    if (!vg) return;
    int split = 0;
    for (int x = 0; x < GRAPH_W; ++x) split |= __builtin_fabsf(ui.curve[0][x] - ui.curve[1][x]) > 0.25f;
    vgcanvas_save(vg);
    vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
    if (!ui.draft.bypass) /* one path, two fills: each gradient is clear beyond 0 dB on its side */
        for (int side = 0; side < 2; ++side) {
            path(vg, ui.curve[0]);
            vgcanvas_line_to(vg, PEQ_GRAPH_X + GRAPH_W - 1, zero);
            vgcanvas_line_to(vg, PEQ_GRAPH_X, zero);
            vgcanvas_close_path(vg);
            vgcanvas_set_fill_linear_gradient(vg, 0, side ? PEQ_PLOT_BOTTOM : PEQ_PLOT_TOP, 0, zero,
                                              rgba(tone, 0x90), rgba(tone, 0));
            vgcanvas_fill(vg);
        }
    vgcanvas_set_line_width(vg, 2);
    for (int ch = split; ch >= 0; --ch) {
        path(vg, ui.curve[ch]);
        vgcanvas_set_stroke_color(vg, rgba(tone, ch ? 0x80 : 0xff));
        vgcanvas_stroke(vg);
    }
    for (int i = 0; i < ui.draft.count; ++i) {
        const peq_band *b = &ui.draft.bands[i];
        int edited = ui.screen == BAND && i == ui.band;
        if (!b->enabled && !edited) continue;
        vgcanvas_begin_path(vg);
        vgcanvas_arc(vg, graph_x(b->frequency), graph_y(b->gain), edited ? 5 : 3.5f, 0, 6.2831853f, 0);
        vgcanvas_set_fill_color(vg, rgba(edited ? tone : 0, 255));
        vgcanvas_fill(vg);
        vgcanvas_set_stroke_color(vg, rgba(edited ? 0xffffff : tone, 255));
        vgcanvas_stroke(vg);
    }
    vgcanvas_restore(vg);
}

static void e3_done(void);
/* Replaces the stock page's on_common_keyup: Return steps back one screen; on Simple it ends an
 * adjustment, else leaves. */
static int keyup(void *ctx, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    if (ui.tab == ADVANCED) action((void *)(long)BACK, ctx);
    else if (e3.adjusting) {
        e3_done();
        e3_show();
    } else navigator_back();
    return 11; /* RET_STOP */
}

static int e3_flush(void);
/* Leaving saves what Simple has unsaved and lets go of its Off comparison's held preamp. */
static int closed(void *ctx, void *event) {
    stop_timer(&e3.press);
    e3_flush();
    eq_state s;
    if (eq_load(&s) && s.hold && (s.hold = 0, eq_save(&s))) peq_stock_eq(1);
    memset(&e3, 0, sizeof e3);
    if (ui.from_simple) ui.tab = SIMPLE, ui.from_simple = 0; /* reopens on Simple */
    ui.bar = 0;
    stop_timer(&ui.timer);
    stop_timer(&ui.keyboard_timer);
    ui.page = 0;
    if (ui.edit) {
        ui.edit = 0;
        input_method_request(input_method(), 0);
    }
    ui.view = 0;
    ui.graph = 0;
    free(ui.names);
    ui.names = 0;
    ui.capacity = ui.file_page = 0;
    ui.dirty = 0; /* unapplied edits go with the page; a draft q2eq made with it closed opens in it */
    return 0;
}

/* What plays, for both tabs: Simple's gains and switch, Advanced's switch. Boot instantiates the
 * filter only when the switch left PLAYSET/EQFLAG set; the flag then follows the mode
 * (peq_stock_eq), so a mode saved on with the flag clear is not playing. */
static void load(eq_state *s) {
    eq_load(s);
    e3.on = g_equalizer_flag && s->mode == EQ_3;
    for (int i = 0; i < 3; ++i) e3.gain[i] = s->eq3.bands[i].gain;
    ui.draft.bypass = !g_equalizer_flag || s->mode != EQ_PEQ;
}

/* The tab bar's tap, or the wheel or Previous or Next on it (eq_key): that tab, the wheel kept on the
 * bar. Neither changes what plays. */
static void tab_to(int tab) {
    if (tab == ui.tab) return;
    if (ui.tab == SIMPLE) {
        e3_done();
        if (e3.dirty) { e3_show(); return; } /* keep unsaved gains if saving failed */
    }
    eq_state s;
    load(&s);
    ui.tab = tab;
    ui.tabs = 1;
    ui.screen = HOME;
    ui.kept[HOME][0] = 0;
    ui.status[0] = 0;
    e3.focus = E3_TABS;
    e3.status = 0;
    if (!ui.timer) ui.timer = timer_add(render, 0, 1); /* not inside the press's own widget */
}

static int tab_touch(void *ctx, void *event) {
    int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
    widget_to_local(ui.page, xy);
    tab_to(xy[0] < 188 ? SIMPLE : ADVANCED);
    return 0;
}

/* The title label; the tabs' bar when the bar takes their taps. */
static void *bar(void *page, int x, int w) {
    void *title = label_create(page, x, bar_foot(page), w, 48);
    widget_use_style(title, "s_label_white20c");
    widget_set_prop_int(title, "line_wrap", 1);
    if (w == 375) widget_on(title, EVT_POINTER_DOWN, tab_touch, 0);
    return title;
}

/* The page from what plays, as it opens and when another output takes over, under the caption saying
 * whose EQ it is. Unapplied Advanced edits stay in the draft: Apply puts them on the output then
 * playing. */
static void page_load(void) {
    eq_state s;
    peq_preset edits = ui.draft;
    const char *output = eq_output_caption();
    eq_load(&s);
    ui.draft = s.peq;
    /* Auto's number follows the bands, as peq_boot saved it, so this finds nothing to apply. */
    follow_auto();
    load(&s);
    ui.named.bypass = ui.draft.bypass;
    if (memcmp(&ui.draft, &ui.named, sizeof(ui.named))) ui.applied[0] = 0; /* replaced since */
    if (ui.dirty) {
        edits.bypass = ui.draft.bypass; /* live state, as load_draft */
        ui.draft = edits;
    } else memcpy(ui.name, ui.applied, sizeof(ui.name));
    if (output) {
        e3.caption = 1;
        e3.status = output;
        snprintf(ui.status, sizeof(ui.status), "%s", output);
    }
}

/* Playback's EQ row: opens on the tab it was last on (ui.tab, zeroed to Simple at boot).
 * Opening changes nothing. ponytail: kept in RAM, so a reboot goes back to Simple; saving it in
 * eq_state would keep it, add when asked. */
int peq_page_init(void *page, void *context) {
    if (!page) return 16;
    ui.page = page;
    ui.view = 0;
    ui.screen = HOME;
    ui.kept[HOME][0] = 0;
    ui.status[0] = 0;
    ui.tabs = 0;
    page_load();
    e3.focus = E3_LOW;
    e3.h = page_room(page); /* the sliders' view, under the row */
    widget_on(page, EVT_DESTROY, closed, 0);
    widget_on(page, EVT_KEY_UP, keyup, 0);
    widget_on(page, EVT_POINTER_MOVE_BEFORE, e3_touch, 0); /* a slider's drag */
    widget_on(page, EVT_POINTER_UP_BEFORE, e3_touch, 0);
    render(0);
    return 0;
}

/* The sliders' travel, under their names and over their values, which stay clear of the iPod
 * glass's lower corners. */
static int e3_top(void) { return eq_rows() ? EQ_SLIDERS_TOP : E3_SLIDERS + 34; }
static int e3_bottom(void) { return eq_rows() ? EQ_SLIDERS_BOTTOM : e3.h - 60; }
static int e3_y(double gain) {
    return (e3_top() + e3_bottom()) / 2 - (int)(gain * (e3_bottom() - e3_top()) / (2 * E3_MAX));
}

static void e3_preset(peq_preset *p) {
    eq3_bands(p, e3.gain);
    p->preamp = headroom(p);
}

static void e3_show(void) {
    if (ui.bar && ui.tab == SIMPLE) widget_set_text_utf8(ui.bar, e3.status ? e3.status : "");
    if (ui.page) widget_invalidate_force(ui.page, 0);
}

/* The gains into the saved state, the mode as it is; the player told while it hears them. */
static int e3_flush(void) {
    stop_timer(&e3.save);
    if (!e3.dirty) return 1;
    eq_state s;
    eq_load(&s);
    e3_preset(&s.eq3);
    if (!eq_save(&s)) {
        e3.status = "Save failed; EQ unchanged"; /* kept dirty: the next edit tries again */
        return 0;
    }
    e3.dirty = 0;
    if (s.mode == EQ_3 || s.hold) peq_stock_eq(1);
    return 1;
}

static int e3_tick(const void *unused) {
    e3.save = 0;
    e3_flush();
    e3_show();
    return 7; /* RET_REMOVE */
}

static void e3_set(int band, double gain) {
    gain = gain > E3_MAX ? E3_MAX : gain < -E3_MAX ? -E3_MAX : gain;
    if (gain == e3.gain[band]) return;
    e3.gain[band] = gain;
    e3.dirty = 1;
    if (!e3.save) e3.save = timer_add(e3_tick, 0, E3_SAVE_MS);
}

static void e3_done(void) {
    e3.adjusting = e3.dragging = 0;
    e3_flush();
}

/* On selects 3EQ (PEQ goes off). Turning 3EQ off holds its preamp while the page is open, so
 * comparing against bypass is not suddenly louder; turning a playing PEQ off holds nothing. */
static void e3_switch(int on) {
    eq_state s;
    eq_load(&s);
    e3_preset(&s.eq3);
    s.mode = on ? EQ_3 : EQ_OFF;
    s.hold = !on && e3.on;
    if (!eq_commit(&s)) {
        e3.status = "Switch failed; EQ unchanged";
        return;
    }
    e3.on = on;
    e3.dirty = 0;
    ui.draft.bypass = 1; /* PEQ is off either way */
}

/* A slider taken, by Centre or touch; over a playing PEQ preset 3EQ takes over first. */
static void e3_take(void) {
    if (!ui.draft.bypass) e3_switch(1);
    if (ui.draft.bypass) e3.adjusting = 1; /* not over a PEQ the switch failed to stop */
}

/* Advanced's Stock presets screen, from Simple; unsaved gains keep Simple (as tab_to). */
static void e3_presets(void) {
    e3_done();
    if (e3.dirty) return;
    ui.tab = ADVANCED;
    ui.screen = STOCKS;
    ui.from_simple = 1;
    ui.tabs = ui.file_page = 0;
    ui.status[0] = 0;
    if (!ui.timer) ui.timer = timer_add(render, 0, 1); /* not inside the press's own widget */
}

static void e3_activate(void) {
    e3.status = 0;
    if (e3.focus == E3_TABS) e3.focus = E3_SWITCH; /* into the tab's rows */
    else if (e3.focus == E3_SWITCH) e3_switch(!e3.on && ui.draft.bypass); /* a playing PEQ shows on */
    else if (e3.focus == E3_PRESETS) e3_presets();
    else if (e3.focus == E3_RESET) {
        for (int i = 0; i < 3; ++i) e3_set(i, 0);
        e3_flush();
    } else e3_take();
    e3_show();
}

/* Advanced's tab bar left for its list's first row, as the bar's Centre or a turn down does. */
static void tabs_leave(void) {
    ui.tabs = 0;
    if (ui.view) widget_set_prop_int(ui.view, "_ringnav_index", 0); /* else its render's first row */
    e3_show();
}

static int e3_single(const void *unused) {
    e3.press = 0;
    if (ui.tab == ADVANCED) tabs_leave();
    else e3_activate();
    return 7;
}

/* navigation.c paint_selection: Advanced's list while its tab bar holds the wheel, whose row is
 * -1 then; ringnav would settle that on the first row in view (reconcile) and light it beside
 * the bar's segment. */
int eq_tabs_hold(void *view) {
    return view && view == ui.view && ui.tab == ADVANCED && ui.screen == HOME && ui.tabs;
}

/* ringnav(), the page on top: Simple's wheel and Centre, and the tab bar, which the wheel reaches past
 * either tab's first row (docs/oled-design.md#tabs). On the bar the wheel moves between the tabs,
 * each opening as the wheel reaches it, Simple then Advanced: a turn down past Advanced, or Centre,
 * goes into the open tab's rows, and a turn up on Simple stays. Previous and Next (the buttons, from
 * ringnav's page keys) open the other tab while the wheel is on the bar, as on Search's tabs. */
int eq_key(void *top, unsigned key) {
    static const unsigned release[EVENT_KEY / 4 + 1] = { [EVENT_KEY / 4] = KEY_CENTER };
    if (!ui.page || top != ui.page) return 0;
    int button = key == KEY_FWD_BTN || key == KEY_BACK_BTN;
    if (button) {
        if (ui.screen != HOME || e3.adjusting || (ui.tab == ADVANCED ? !ui.tabs : e3.focus != E3_TABS))
            return 0; /* stock's: off the bar, the track's */
        tab_to(ui.tab == ADVANCED ? SIMPLE : ADVANCED);
        return 1;
    }
    if (e3.caption && (key == KEY_NEXT || key == KEY_PREV)) {
        e3.caption = 0;
        e3.status = 0;
        ui.status[0] = 0;
        if (ui.bar) widget_set_text_utf8(ui.bar, "");
    }
    if (ui.tab == ADVANCED) {
        if (ui.screen != HOME || (!ui.view && !ui.tabs) ||
            (!ui.tabs && (key != KEY_PREV || widget_get_prop_int(ui.view, "_ringnav_index", -1) > 0)))
            return 0; /* the list's */
        if (key == KEY_PREV && ui.tabs) { /* up the bar: Simple */
            tab_to(SIMPLE);
            return 1;
        }
        if (key == KEY_NEXT) {
            tabs_leave();
            return 1;
        }
        if (key == KEY_PREV) { /* up from the first row: the bar */
            ui.tabs = 1;
            widget_set_prop_int(ui.view, "_ringnav_index", -1);
            e3_show();
            return 1;
        }
    }
    if (ui.tab == SIMPLE && key == KEY_NEXT && e3.focus == E3_TABS && !e3.adjusting) {
        tab_to(ADVANCED); /* down the bar: Advanced */
        return 1;
    }
    if (key == KEY_CENTER && e3.adjusting) {
        e3_done();
        e3_show();
        return 1;
    }
    if (key == KEY_CENTER) {
        int press = center_press(&e3.press, &e3.press_at, e3_single, 0);
        if (press == 2 && g_backlight_status) on_wm_keyup_fun(0, (void *)release);
        return press;
    }
    if (key != KEY_NEXT && key != KEY_PREV) return 0;
    int dir = key == KEY_NEXT ? 1 : -1;
    if (e3.adjusting) e3_set(e3.focus - E3_LOW, e3.gain[e3.focus - E3_LOW] + 0.5 * dir);
    else e3.focus = e3.focus + dir < E3_TABS ? E3_TABS : e3.focus + dir > E3_HIGH ? E3_HIGH : e3.focus + dir;
    e3_show();
    return 1;
}

/* A touch: a button (a set_switch theme's row) on the press; a slider's gain follows the finger,
 * in 0.5 dB steps, until it lifts. */
static int e3_touch(void *ctx, void *event) {
    int type = I(event, EVENT_TYPE), xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
    if (!e3.view || (type != EVT_POINTER_DOWN && !e3.dragging)) return 0;
    widget_to_local(e3.view, xy);
    if (type == EVT_POINTER_DOWN) {
        int rows = eq_rows(); /* a set_switch theme's rows (slider_page.c eq_rows_paint) */
        if (xy[1] < (rows ? EQ_ROWS_Y : E3_BUTTONS)) return 0;
        e3.status = 0;
        if (rows && xy[1] < EQ_ROWS_Y + 3 * EQ_ROW) {
            e3.focus = E3_SWITCH + (xy[1] - EQ_ROWS_Y) / EQ_ROW;
            e3_activate();
            return 0;
        }
        if (!rows && xy[1] < E3_SLIDERS) {
            e3.focus = xy[0] < e3_button[1][0] - 4 ? E3_SWITCH : xy[0] < e3_button[2][0] - 4 ? E3_PRESETS : E3_RESET;
            e3_activate();
            return 0;
        }
        e3.focus = E3_LOW + (xy[0] < 125 ? 0 : xy[0] < 250 ? 1 : 2);
        e3_take();
        e3.dragging = 1;
    }
    if (type == EVT_POINTER_UP_BEFORE) e3_done();
    else {
        double half = (e3_bottom() - e3_top()) / 2.0, g = ((e3_top() + e3_bottom()) / 2 - xy[1]) * E3_MAX / half;
        e3_set(e3.focus - E3_LOW, (int)(g * 2 + (g < 0 ? -0.5 : 0.5)) / 2.0);
    }
    e3_show();
    return 0;
}

static void e3_box(void *canvas, int x, int y, int w, int h, unsigned rgb) {
    canvas_set_fill_color(canvas, rgba(rgb, 255));
    canvas_fill_rect(canvas, x, y, w, h);
}

/* A set_switch theme's (OLED's) Simple tab is slider_page.c's eq_rows_paint, on the EQ_* rows.
 * Otherwise: the switch's bar and the gains' bars in the accent's light tone, grey
 * while 3EQ is off; the selected control on a lighter card; the slider being adjusted with a white
 * handle and value, the others grey. While PEQ plays the switch shows it on, under the applied
 * preset's name (Advanced unnamed; eq_rows_paint keeps the switch's Enabled and gives the name to
 * Presets as its value), and the sliders are dimmed: one takes over as 3EQ. */
static void e3_paint(void *canvas) {
    int peq = !ui.draft.bypass, zero = e3_y(0);
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), tone = e3.on ? TONE : peq ? 0x444444 : 0x666666;
    char text[16];
    /* The switch names the preset playing: 12 bytes at most, cut on a UTF-8 boundary, at 16px past 9 */
    char preset[16];
    int n = snprintf(preset, sizeof preset, "%s", ui.applied[0] ? ui.applied : "Advanced");
    if (n > 12) {
        n = 12;
        while (n && (preset[n] & 0xc0) == 0x80) --n; /* preset[12] continues a character: drop it */
        preset[n] = 0;
    }
    if (eq_rows()) { /* slider_page.c: rows and the standard's vertical sliders */
        eq_rows_t e = { peq ? preset : 0, e3.on || peq, e3.focus - E3_TABS, e3.adjusting,
                        { e3.gain[0], e3.gain[1], e3.gain[2] }, E3_MAX };
        eq_rows_paint(e3.view, canvas, &e);
        return;
    }
    const char *const names[] = { peq ? preset : e3.on ? "Enabled: On" : "Enabled: Off", "Presets", "R" };
    for (int i = 0; i < 3; ++i) {
        int x = e3_button[i][0], w = e3_button[i][1];
        e3_box(canvas, x, E3_BUTTONS, w, 40, e3.focus == E3_SWITCH + i ? 0x333333 : 0x1a1a1a);
        if (!i) e3_box(canvas, x, E3_BUTTONS + 36, w, 4, e3.on || peq ? TONE : 0x666666);
        caption(canvas, names[i], x, E3_BUTTONS, w, 40, !i && peq && n > 9 ? 16 : 20, 0xffffffffu);
    }
    for (int i = 0; i < 3; ++i) {
        static const char *const names[] = { "Low", "Mid", "High" };
        int x = 62 + 125 * i, y = e3_y(e3.gain[i]), focus = e3.focus == E3_LOW + i;
        if (focus) e3_box(canvas, x - 56, E3_SLIDERS, 112, e3_bottom() + 36 - E3_SLIDERS, 0x1a1a1a);
        caption(canvas, names[i], x - 56, E3_SLIDERS, 112, 28, 20, peq ? CF_GREY : 0xffffffffu);
        e3_box(canvas, x - 2, e3_top(), 4, e3_bottom() - e3_top(), 0x333333);
        e3_box(canvas, x - 3, y < zero ? y : zero, 6, y < zero ? zero - y : y - zero + 1, tone);
        e3_box(canvas, x - 20, y - 5, 40, 10, focus && e3.adjusting ? 0xffffff : peq ? 0x555555 : 0x8a8a8a);
        snprintf(text, sizeof text, e3.gain[i] ? "%+.1f dB" : "0 dB", e3.gain[i]);
        caption(canvas, text, x - 56, e3_bottom() + 8, 112, 24, 18, focus && e3.adjusting ? 0xffffffffu : CF_GREY);
    }
    e3_box(canvas, 12, zero, 351, 1, 0x8a8a8a); /* 0 dB */
    stock_canvas_set_fill_color(canvas, fill);
}

/* The tab bar, unless a message has it: the open tab white over an accent line, the other grey,
 * and while the wheel is on the bar, the other on the lighter card Centre would open; a set_switch
 * theme's is slider_page.c eq_tabs_paint's segmented control. */
static void tabs_paint(void *canvas) {
    int simple = ui.tab == SIMPLE, focus = simple ? e3.focus == E3_TABS : ui.tabs;
    if (simple ? e3.status != 0 : ui.status[0] != 0) return;
    if (eq_rows()) {
        eq_tabs_paint(ui.bar, canvas, simple, focus);
        return;
    }
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    for (int i = 0; i < 2; ++i) {
        int x = i ? 191 : 8, shown = i == !simple;
        if (focus && !shown) e3_box(canvas, x, 4, 176, 40, 0x333333);
        caption(canvas, i ? "Advanced" : "Simple", x, 4, 176, 40, 20, shown ? 0xffffffffu : CF_GREY);
        if (shown) e3_box(canvas, x + 40, 42, 96, 3, TONE);
    }
    stock_canvas_set_fill_color(canvas, fill);
}

#if Q2POD_DEV
#include "q2eq.h"
/* q2eq (q2eq.h, docs/adb.md#q2eq): a request from the helper, taken as the editor's own actions on
 * the draft and the switches, as if its rows were pressed, so the page, EQ per Output and the player
 * follow as they do for the wheel. Only a file that is root's alone is read. The page's screen is
 * kept, and an open page renders again; with it closed the draft is the page's next one. */

/* The action on screen's row id; what it said kept for the reply. */
static void q2eq_act(int screen, int id) {
    ui.screen = screen;
    action((void *)(long)id, 0);
}

/* The picker's row for text, as a pick on it: its 0.5 dB rows, Preamp's Auto row 0; -1 off them. */
static int q2eq_row(int pick, const char *text) {
    double v, r;
    ui.pick = pick;
    if (pick == PREAMP && !strcmp(text, "auto")) return 0;
    if (!peq_number(text, &v)) return -1;
    r = (v - picks[pick - BALANCE].first) / picks[pick - BALANCE].step;
    return r >= (pick == PREAMP) && r < picks[pick - BALANCE].rows && r == (int)r ? (int)r : -1;
}

/* name's row on screen, a list of the card's presets, pressed: 0 if there is none. */
static int q2eq_preset(int screen, const char *name) {
    unsigned n = strlen(name);
    list_files(PEQ_FOLDER, ".txt");
    for (int i = 0; i < ui.count; ++i)
        if (!strncmp(ui.names[i], name, n) && !strcasecmp(ui.names[i] + n, ".txt")) {
            ui.file_page = i / FILE_PAGE;
            q2eq_act(screen, i % FILE_PAGE);
            return 1;
        }
    return 0;
}

/* Stock's genre preset named name, or -1. */
static int q2eq_stock(const char *name) {
    for (int i = 0; i < STOCK_N; ++i)
        if (!strcasecmp(stock_names[i], name)) return i;
    return -1;
}

void q2eq_poll(void) {
    char text[PEQ_FILE_LIMIT]; /* on the stack: the scratch state's 128 KB is shared */
    q2eq_request rq;
    q2eq_reply rp;
    int st[40]; /* glibc 2.28 MIPS o32 stat: st_mode word 5, st_uid word 7 */
    if (__xstat(3, Q2EQ_REQUEST, st) || (st[5] & 0170077) != 0100000 || st[7]) return;
    void *f = fopen(Q2EQ_REQUEST, "rb");
    unsigned n = f ? fread(&rq, 1, sizeof rq, f) : 0;
    if (f) fclose(f);
    unlink(Q2EQ_REQUEST);
    if (n != sizeof rq) return;
    rq.output[sizeof rq.output - 1] = rq.name[255] = rq.text[255] = 0;
    int screen = ui.screen, band = ui.band, pick = ui.pick, adjust = ui.adjust, page = ui.file_page,
        previous = ui.previous, tabs = ui.tabs, k = rq.op == Q2EQ_SIMPLE_PRESET ? q2eq_stock(rq.name) : -1, b = rq.band;
    const char *fail = 0, *t;
    if (!ui.page) page_load(); /* the draft from what plays, or the one q2eq left */
    if (!eq_owner[0]) owner_read();
    q2eq_busy = 1;
    q2eq_said[0] = ui.status[0] = 0;
    e3.status = 0;
    peq_band *at = &ui.draft.bands[b < 0 || b >= PEQ_BANDS ? 0 : b];
    if (ui.edit) fail = "A keyboard is up on the EQ page";
    else if (rq.output[0] && (!ringnav_eq_output() || strcmp(rq.output, eq_owner)))
        fail = "Only the output playing can be changed";
    else if ((unsigned)rq.op >= Q2EQ_OPS) fail = "Unknown request";
    else if (rq.op >= Q2EQ_BAND && rq.op <= Q2EQ_GAIN) {
        /* The rows the main screen offers: ten, then one past the last used; opening one counts it. */
        if (b < 0 || b >= PEQ_BANDS || (b >= 10 && b > ui.draft.count)) fail = "No such band row";
        else if (ui.draft.count <= (ui.band = b)) ui.draft.count = b + 1;
    }
    if (fail) {
    } else if (rq.op == Q2EQ_LIST) {
        write_out();
        list_files(PEQ_FOLDER, ".txt");
        n = 0;
        for (int i = 0; i < ui.count && n < sizeof text - 257; ++i)
            n += snprintf(text + n, sizeof text - n, "%s\n", ui.names[i]);
        peq_save_file(Q2EQ_TEXT, 0, text, n);
    } else if (rq.op == Q2EQ_EXPORT) {
        n = peq_export(&ui.draft, text, sizeof text);
        if (!n || !peq_save_file(Q2EQ_TEXT, 0, text, n)) fail = "Export failed";
    } else if (rq.op == Q2EQ_ON) {
        eq_state s;
        if (rq.arg == ui.draft.bypass) q2eq_act(HOME, BYPASS); /* Advanced's Enabled */
        else if (!rq.arg && eq_load(&s) && s.mode != EQ_OFF)
            eq_toggle(), load(&s); /* 3EQ plays: off as Control Center's EQ circle turns it */
    } else if (rq.op == Q2EQ_PER_OUTPUT) {
        if (rq.arg != ringnav_eq_output()) q2eq_act(HOME, PER_OUTPUT);
    } else if (rq.op == Q2EQ_APPLY) {
        if (ui.dirty) q2eq_act(HOME, APPLY);
        else fail = "Nothing to apply";
    } else if (rq.op == Q2EQ_DISCARD) {
        ui.dirty = 0;
        page_load();
    } else if (rq.op == Q2EQ_RESET) q2eq_act(HOME, RESET);
    else if (rq.op == Q2EQ_PREAMP || rq.op == Q2EQ_BALANCE || rq.op == Q2EQ_GAIN) {
        int row = q2eq_row(rq.op == Q2EQ_GAIN ? GAIN : rq.op == Q2EQ_PREAMP ? PREAMP : BALANCE, rq.text);
        if (row < 0 || (rq.op == Q2EQ_GAIN && !PEQ_GAINED(at->type))) fail = "Not a value the picker offers";
        else q2eq_act(PICK, row);
    } else if (rq.op == Q2EQ_BAND) {
        if (!at->enabled != !rq.arg) q2eq_act(BAND, ENABLE);
    } else if (rq.op == Q2EQ_CHANNEL) {
        if (!at->enabled || rq.arg < 1 || rq.arg > 3) fail = "Turn the band on first";
        else
            while (at->enabled != rq.arg) q2eq_act(BAND, CHANNEL);
    } else if (rq.op == Q2EQ_TYPE) {
        if ((unsigned)rq.arg >= PEQ_TYPES) fail = "No such type";
        else q2eq_act(TYPES, rq.arg);
    } else if (rq.op == Q2EQ_FREQ || rq.op == Q2EQ_Q) {
        ui.adjust = rq.op == Q2EQ_Q ? QUALITY : FREQUENCY;
        if (!peq_number(rq.text, &ui.typed)) fail = "Not a number";
        else q2eq_act(ADJUST, TYPED);
    } else if (rq.op == Q2EQ_LOAD || rq.op == Q2EQ_DELETE || rq.op == Q2EQ_RENAME) {
        if (!q2eq_preset(rq.op == Q2EQ_LOAD ? PRESETS : rq.op == Q2EQ_DELETE ? DELETES : RENAMES, rq.name))
            fail = "No such preset";
        else if (rq.op == Q2EQ_DELETE) q2eq_act(CONFIRM, YES);
        else if (rq.op == Q2EQ_RENAME) rename_preset(rq.text);
    } else if (rq.op == Q2EQ_STOCK) {
        if ((k = q2eq_stock(rq.name)) < 0) fail = "No such stock preset";
        else q2eq_act(STOCKS, k);
    } else if (rq.op == Q2EQ_SAVE || rq.op == Q2EQ_SAVE_AS) {
        if (rq.op == Q2EQ_SAVE) {
            if ((unsigned)rq.arg < 10) q2eq_act(SAVES, rq.arg);
            else fail = "No such slot";
        } else if (!(t = preset_name(rq.name))) fail = "Name not allowed";
        else {
            ui.screen = SAVES;
            snprintf(ui.destination, sizeof(ui.destination), PEQ_FOLDER "/%s.txt", t);
            ui.candidate = ui.draft;
            save_candidate(0);
        }
        if (ui.screen == CONFIRM) {
            if (rq.flags & Q2EQ_REPLACE) q2eq_act(CONFIRM, YES);
            else fail = "A preset has that name";
        }
    } else if (rq.op == Q2EQ_IMPORT_AS) {
        peq_preset p;
        peq_error e;
        f = fopen(Q2EQ_IMPORT, "rb");
        n = f ? fread(text, 1, sizeof text, f) : 0;
        if (f) fclose(f);
        if (!(t = preset_name(rq.name))) fail = "Name not allowed";
        else if (!card_mounted()) fail = "No card";
        else if (!peq_import_file(Q2EQ_IMPORT, &p, &e))
            snprintf(ui.status, sizeof ui.status, "Line %u: %s", e.line, e.reason), fail = ui.status;
        else if (snprintf(ui.destination, sizeof ui.destination, PEQ_FOLDER "/%s.txt", t),
                 !(rq.flags & Q2EQ_REPLACE) && !access(ui.destination, 0))
            fail = "A preset has that name";
        else if (mkdir(PEQ_FOLDER, 0700), !peq_save_file(ui.destination, 0, text, n)) fail = "Save failed";
        else q2eq_preset(PRESETS, t);
        unlink(Q2EQ_IMPORT);
    } else if (rq.op == Q2EQ_SIMPLE) {
        if (!ui.page) e3.on = 0; /* Off holds its preamp only while the page compares */
        e3_switch(rq.arg);
    } else if (rq.op == Q2EQ_SIMPLE_GAIN) {
        double v;
        if ((unsigned)b > 2 || !peq_number(rq.text, &v)) fail = "Not a number";
        else if (e3_take(), !e3.adjusting) fail = e3.status ? e3.status : "Switch failed; EQ unchanged";
        else e3_set(b, v), e3_done();
    } else if (rq.op == Q2EQ_SIMPLE_RESET) {
        int focus = e3.focus;
        e3.focus = E3_RESET;
        e3_activate();
        e3.focus = focus;
    } else if (rq.op == Q2EQ_SIMPLE_PRESET) {
        if (k < 0) fail = "No such stock preset";
        else if (e3_presets(), ui.screen != STOCKS) fail = "Save failed; EQ unchanged";
        else q2eq_act(STOCKS, k);
    }
    if (!fail && (rq.flags & Q2EQ_NOW) && ui.dirty) q2eq_act(HOME, APPLY);
    q2eq_busy = 0;
    ui.screen = screen, ui.band = band, ui.pick = pick, ui.adjust = adjust, ui.file_page = page;
    ui.previous = previous, ui.tabs = tabs; /* the wheel stays where it was, on the tab bar too */
    if (ui.page && !ui.timer) ui.timer = timer_add(render, 0, 1);
    if (!ui.page) free(ui.names), ui.names = 0, ui.capacity = 0;
    memset(&rp, 0, sizeof rp);
    rp.nonce = rq.nonce;
    t = fail ? fail : q2eq_said[0] ? q2eq_said : ui.status[0] ? ui.status : e3.status ? e3.status : "";
    /* An action that failed says so in its message, as the editor's toast does. */
    rp.ok = !fail && !strstr(t, "failed") && !strstr(t, "not allowed") && !strstr(t, "taken") &&
            strncmp(t, "Line ", 5);
    snprintf(rp.message, sizeof rp.message, "%s", t);
    ui.status[0] = 0;
    rp.page = ui.page != 0, rp.flag = g_equalizer_flag, rp.per_output = ringnav_eq_output(), rp.dirty = ui.dirty;
    memcpy(rp.owner, eq_owner, sizeof rp.owner);
    memcpy(rp.name, ui.name, sizeof rp.name);
    memcpy(rp.applied, ui.applied, sizeof rp.applied);
    rp.automatic = headroom(&ui.draft);
    eq_load(&rp.state);
    rp.draft = ui.draft;
    peq_save_file(Q2EQ_REPLY, "Q2EQR01", &rp, sizeof rp);
}
#endif
