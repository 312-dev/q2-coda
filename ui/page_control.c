/* Control Center (docs/oled-design.md#control-center), q2ui's own while it has the screen: opened
 * by holding Return on any page (main.c), closed by Return. Drawn as demo's sheet is
 * (patch/control_center.c cc_paint, the numbers its CC_* are): under the status row, a block of
 * six circles two by three, each named under it (Wi-Fi, Bluetooth / AirPlay, Car / Smart Wi-Fi,
 * EQ); at the right Now Playing (the cover, title and artist over the transport), and under it the
 * Brightness and Volume pills; while a cable is in, the USB row under both; the grab bar on the
 * foot. Landing open, the blocks overshoot and settle (springs[], demo's cc_springs), still under
 * Reduce Motion.
 *
 * The circles are lit as demo says its switches stand (ui/status.c status_told_value, from
 * control_center.c q2ui_cc_state) and flipped down q2ui's own pipe to demo ("set NAME 0|1",
 * ui/player.c player_set, control_center.c q2ui_cc_set): a circle turns at once and keeps its new
 * state until demo's word says the same, or WANT_HOLD_US after the flip if it never does. Bluetooth
 * flipped while its stack is still starting (about 3 s after it is turned on) is kept by demo and done
 * once the stack is up; demo says btbusy=1 till then, and the circle holds its flip all that time. The Wi-Fi circle is the link as the status row reads
 * it (status_wifi_bars), lit while joined whatever the switch says, as demo's cc_lit has it.
 *
 * The wheel walks the stops in demo's order (the toggles by rows, Now Playing while something is
 * playing or paused, Brightness, Volume, the USB row while a cable is in), a white ring round the
 * one it is on. The ring is not there on opening: the first tick or Centre shows it without moving
 * or acting, and it goes RING_US after the last tick or key. Centre acts on the stop: a switch
 * flips; AirPlay and the USB row close the sheet and open demo's page for them (the gate,
 * ui_demo_page); Now Playing closes it and opens q2ui's Now Playing; on a slider Centre gives it
 * the wheel (an accent ring) and Centre or Return gives it back. A grabbed slider steps as a list's
 * inline slider does (wheel_accel, up to SLIDER_STEPS a tick): Brightness down the pipe ("set
 * bright N"), Volume down the player's queue ("volume N", as Now Playing sets it) up to demo's top
 * ("volmax"), each shown at once and kept until demo's word says the same, LEVEL_HOLD_US at most.
 * Centre held on a toggle (the toolkit's KEY_HOLD_MS) closes the sheet and opens its settings, as
 * demo's cc_hold: Wi-Fi and Smart Wi-Fi q2ui's Wi-Fi page, Bluetooth q2ui's Bluetooth page, and
 * demo's for AirPlay (Network services), Car Remote and the EQ.
 * ponytail: names are drawn at Inter's advances without demo's caption tracking (text.c has none),
 * and none scrolls as a marquee (the six fit); the screen has no taps here, so the transport is
 * drawn and not pressed. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "q2ui.h"
#include "../patch/wheel_accel.h"

/* the layout, as control_center.c's CC_* */
#define X0 20
#define X1 355
#define Y0 38
#define GAP 10
#define PAD 10
#define PITCH 66
#define BLOCK_W 158
#define BLOCK_H 210
#define BLOCK_R 22
#define RIGHT_X (X0 + BLOCK_W + GAP)
#define RIGHT_W (X1 - RIGHT_X)
#define NOW_H 114
#define CIRCLE_R 20
#define ICON 22
#define LABEL_PX TYPE(13)
#define LABEL_DY 29
#define ART 52
#define ART_R 9
#define ART_IN 12
#define TEXT_GAP 10
#define TRANSPORT_Y 87
#define TRANSPORT_DX 46
#define SLIDER_Y (Y0 + NOW_H + GAP)
#define SLIDER_H 38
#define USB_Y (Y0 + BLOCK_H + GAP)
#define USB_ICON 20
#define USB_ICON_X 22
#define USB_TEXT_X 40
#define USB_VALUE_END 30
#define USB_CHEV 13
#define USB_CHEV_X 18
#define USB_RING 5
#define RING 3 /* the ring's outer edge past the stop, 2 px wide */
#define GRAB_W 36
#define GRAB_H 5
#define GRAB_Y (SCREEN_H - 6 - GRAB_H)
/* the sliders' fill and the grab bar: demo's greys, and their Light Mode halves as its flip makes them */
#define FILL_RGB 0xe5e5ea
#define FILL_RGB_L 0x1a1a15
#define GRAB_RGB 0x5a5a5e
#define GRAB_RGB_L 0xa5a5a1
#define RING_US 5000000u
#define SPRING_MS 280
#define SPRING_STEP 20
#define SLIDER_STEPS 10
#define WANT_HOLD_US 2000000u
#define LEVEL_HOLD_US 1500000u
#define LEVEL_EASE 0.06f /* the fills' spring, as Now Playing's bar eases (VOL_EASE_MS) */

enum { WIFI, BT, AIRPLAY, CAR, SMART, EQ, NOW, BRIGHT, VOLUME, USB, STOPS, PREV = STOPS, PLAY, NEXT };
static const struct {
    const char *name, *told, *set, *glyph; /* told: demo's word on it; set: the verb's name */
} stops[STOPS] = { [WIFI] = { "Wi-Fi", "wifi", "wifi", 0 },     [BT] = { "Bluetooth", "bton", "bt", "bluetooth" },
                   [AIRPLAY] = { "AirPlay", "airplay", 0, "airplay" },
                   [CAR] = { "Car", "car", "car", "car" },     [SMART] = { "Smart Wi-Fi", "smart", "smart", "leaf" },
                   [EQ] = { "EQ", "eq", "eq", "eq" },          [NOW] = { 0, 0, 0, 0 },
                   [BRIGHT] = { 0, "bright", 0, "sun" },       [VOLUME] = { 0, "vol", 0, "speaker" },
                   [USB] = { 0, "usb", 0, "usb" } };
/* the landing's offset from rest in quarter pixels every SPRING_STEP ms (control_center.c cc_springs):
 * 8 px past rest at about 50 ms, back through it at 140, 2 px short at about 190, still at SPRING_MS */
static const signed char springs[SPRING_MS / SPRING_STEP + 1] = { 0, 21, 31, 31, 26, 17, 8, 0, -5, -8, -8, -6, -4, -2, 0 };

struct cc_st {
    int focus, grab;
    uint64_t seen;    /* the last tick or key, 0 none: the ring shows RING_US after it */
    int ring_drawn;   /* whether the ring was last drawn */
    uint64_t landed;  /* when the sheet came to rest open, 0 not yet */
    int dy;           /* the blocks' offset, as last drawn */
    int want[STOPS];  /* a switch flipped or a level set here: shown until demo says so too; -1 none */
    uint64_t set_at[STOPS];
    spring_t fill[2]; /* Brightness's and Volume's fills, 0 to 1 */
    wheel_accel_t accel;
    int wifi, cable, loaded; /* as last drawn: the Wi-Fi glyph, a cable in, something loaded */
    unsigned now_seen, cover_seen;
    uint64_t t;
    run_t names[EQ + 1], title, artist, usb, usb_value;
    char usb_s[24];
};

static int told_int(const char *key, int def) {
    char v[16];
    return status_told_value(key, v, sizeof v) && *v ? atoi(v) : def;
}
static int ring_on(const struct cc_st *s) { return s->seen && ui_time() - s->seen < RING_US; }
static int loaded(void) { return now_get()->playing || now_get()->paused; }
static int cable(void) { return told_int("cable", 0) == 1; }

/* stop i's box: a circle's square, the Now Playing block, a slider, the USB row, a transport glyph's */
static rect_t box(int i) {
    if (i <= EQ) {
        int cx = X0 + BLOCK_W / 2 - PITCH / 2 + (i % 2) * PITCH, cy = Y0 + PAD + CIRCLE_R + (i / 2) * PITCH;
        return (rect_t){ cx - CIRCLE_R, cy - CIRCLE_R, 2 * CIRCLE_R, 2 * CIRCLE_R };
    }
    if (i == NOW) return (rect_t){ RIGHT_X, Y0, RIGHT_W, NOW_H };
    if (i == BRIGHT || i == VOLUME) return (rect_t){ RIGHT_X, SLIDER_Y + (i - BRIGHT) * (SLIDER_H + GAP), RIGHT_W, SLIDER_H };
    if (i == USB) return (rect_t){ X0, USB_Y, X1 - X0, SLIDER_H };
    int cx = RIGHT_X + RIGHT_W / 2 + (i - PLAY) * TRANSPORT_DX, cy = Y0 + TRANSPORT_Y, px = i == PLAY ? 30 : ICON;
    return (rect_t){ cx - px / 2, cy - px / 2, px, px };
}

/* The Wi-Fi circle's glyph, as demo's cc_wifi: -1 off (wifi-slash), 0 on but not joined, 1 to 3
 * joined by the link's bars, 4 joined with no internet (wifi-x) */
static int wifi_glyph(void) {
    int bars = status_wifi_bars();
    return bars ? bars : told_int("wifi", 0) == 1 ? 0 : -1;
}
static int switch_on(const struct cc_st *s, int i) { return s->want[i] >= 0 ? s->want[i] : told_int(stops[i].told, 0) == 1; }
static int lit(const struct cc_st *s, int i) {
    if (i == WIFI) return switch_on(s, i) || status_wifi_bars() > 0;
    return switch_on(s, i);
}
static int level(struct cc_st *s, int i) {
    int told = told_int(stops[i].told, 0);
    told = told < 0 ? 0 : told > 100 ? 100 : told;
    if (s->want[i] >= 0 && (told == s->want[i] || ui_time() - s->set_at[i] >= LEVEL_HOLD_US)) s->want[i] = -1;
    return s->want[i] >= 0 ? s->want[i] : told;
}

static void *cc_open(void *arg) {
    (void)arg;
    struct cc_st *s = calloc(1, sizeof *s);
    for (int i = 0; i < STOPS; ++i) s->want[i] = -1;
    s->grab = -1, s->t = ui_time(), s->wifi = wifi_glyph(), s->cable = cable(), s->loaded = loaded();
    spring_set(&s->fill[0], level(s, BRIGHT) / 100.0), spring_set(&s->fill[1], level(s, VOLUME) / 100.0);
    s->now_seen = now_seq(), s->cover_seen = now_cover_seq();
    ui_again();
    return s;
}
static void close_for(void) { ui_pop(); }

static void act(struct cc_st *s, int i) {
    if (i == AIRPLAY || i == USB) { /* demo's pages: the sheet goes first, as demo's dialog_quit */
        close_for();
        ui_demo_page(i == AIRPLAY ? "systemset/netservice_page" : "usbpick_page");
    } else if (i == NOW) {
        if (!loaded()) return;
        close_for();
        ui_push(&page_now, 0);
    } else if (i <= EQ) {
        int on = !switch_on(s, i);
        if (player_set(stops[i].set, on)) return; /* not sent (no pipe): the circle stays as demo has it */
        s->want[i] = on, s->set_at[i] = ui_time();
        ui_mark_all();
    }
}
static void hold(int i) {
    close_for();
    if (i == WIFI || i == SMART) wifi_push();
    else if (i == BT) bluetooth_push();
    else if (i == AIRPLAY) ui_demo_page("systemset/netservice_page");
    else if (i == CAR) ui_demo_page("carremote_page");
    else if (i == EQ) ui_demo_page("playset/equalizer_page");
}
/* a grabbed slider's level moved by steps, sent, and shown at once */
static void adjust(struct cc_st *s, int i, int steps) {
    int from = level(s, i), to = from + steps, top = i == VOLUME ? told_int("volmax", 100) : 100;
    to = to < 0 ? 0 : to > top ? top : to;
    if (to == from) return;
    char verb[24];
    snprintf(verb, sizeof verb, "volume %d", to);
    if (i == BRIGHT ? player_set("bright", to) : player_send(verb)) return; /* not sent: as demo has it */
    s->want[i] = to, s->set_at[i] = ui_time();
}
/* a tick or key: 1 when the ring was not showing, which the input only brings up */
static int wake(struct cc_st *s) {
    int was = ring_on(s);
    s->seen = ui_time();
    ui_mark_all();
    ui_wake_ms(RING_US / 1000u);
    return !was;
}

static void cc_event(void *st, const input_t *e) {
    struct cc_st *s = st;
    if (e->type == IN_KEY_UP && e->value == KEY_UI_RETURN) {
        if (s->grab < 0) {
            ui_pop();
            return;
        }
        s->grab = -1, wake(s);
    } else if (e->type == IN_TICK) {
        ui_click(0); /* every tick, as on demo's sheet, a dialog whose keys keep stock's click (ringnav_keydown) */
        if (wake(s)) return;
        if (s->grab >= 0) {
            int step = wheel_accel(&s->accel, s, e->value, e->ms, 0);
            adjust(s, s->grab, e->value * (step < 1 ? 1 : step > SLIDER_STEPS ? SLIDER_STEPS : step)); /* clockwise is more, as demo's cc_wheel */
            return;
        }
        int next = s->focus + e->value;
        if (next == NOW && !loaded()) next += e->value;
        if (next < 0 || next >= STOPS || (next == USB && !cable())) return; /* the ends are kept */
        s->focus = next;
    } else if (e->type == IN_KEY_UP && e->value == KEY_UI_CENTRE) {
        if (wake(s)) return;
        if (s->focus == BRIGHT || s->focus == VOLUME)
            s->grab = s->grab >= 0 ? -1 : s->focus;
        else
            act(s, s->focus);
    } else if (e->type == IN_KEY_HOLD && e->value == KEY_UI_CENTRE) {
        if (wake(s)) return;
        if (s->grab < 0 && s->focus <= EQ) hold(s->focus);
    }
}

static int spring_dy(const struct cc_st *s) {
    if (!s->landed || ui_reduce_motion || !list_motion) return 0;
    uint64_t t = (ui_time() - s->landed) / 1000u;
    if (t >= SPRING_MS) return 0;
    unsigned k = (unsigned)t / SPRING_STEP, f = (unsigned)t % SPRING_STEP;
    int q = (springs[k] * (int)(SPRING_STEP - f) + springs[k + 1] * (int)f) / SPRING_STEP;
    return (q + (q < 0 ? -2 : 2)) / 4;
}
static void cc_step(void *st) {
    struct cc_st *s = st;
    float dt = (float)(ui_time() - s->t) / 1e6f;
    s->t = ui_time();
    if (!s->landed && !ui_sliding()) s->landed = ui_time(); /* at rest open: the landing starts */
    int dy = spring_dy(s), landing = s->landed && ui_time() - s->landed < SPRING_MS * 1000u && !ui_reduce_motion && list_motion;
    if (dy != s->dy) /* the blocks' band, from 2 px short of rest to the foot */
        s->dy = dy, ui_mark((rect_t){ 0, Y0 - RING - 2, SCREEN_W, SCREEN_H - (Y0 - RING - 2) });
    if (landing) ui_again();
    for (int i = 0; i < STOPS; ++i) {
        if (i > EQ || s->want[i] < 0) continue;
        /* Bluetooth's stack starting (btbusy=1): demo has the flip kept and does it once the stack is up */
        int busy = i == BT && told_int("btbusy", 0) == 1;
        if ((told_int(stops[i].told, 0) == 1) == s->want[i] || (!busy && ui_time() - s->set_at[i] >= WANT_HOLD_US))
            s->want[i] = -1, ui_mark_all(); /* demo has caught up, or never will */
        else if (busy)
            s->set_at[i] = ui_time(), ui_wake_ms(WANT_HOLD_US / 1000u); /* held from demo's last busy word */
        else
            ui_wake_ms((unsigned)((s->set_at[i] + WANT_HOLD_US - ui_time()) / 1000u) + 1);
    }
    for (int k = 0; k < 2; ++k) {
        s->fill[k].to = level(s, BRIGHT + k) / 100.0;
        if (spring_moving(&s->fill[k])) {
            rect_t r = box(BRIGHT + k);
            spring_step(&s->fill[k], dt, LEVEL_EASE, 1);
            ui_mark((rect_t){ r.x - RING, r.y - RING + s->dy, r.w + 2 * RING, r.h + 2 * RING }), ui_again();
        }
        if (s->want[BRIGHT + k] >= 0) /* let go then, if demo never says the same */
            ui_wake_ms((unsigned)((s->set_at[BRIGHT + k] + LEVEL_HOLD_US - ui_time()) / 1000u) + 1);
    }
    int w = wifi_glyph(), c = cable(), l = loaded();
    ui_wake_ms(1000); /* the link polled once a second while the sheet is open (status_wifi_bars) */
    if (w != s->wifi) s->wifi = w, ui_mark_all();
    if (c != s->cable) { /* a cable came or went: the USB row with it, the wheel back on Volume if it was on the row */
        s->cable = c, ui_mark_all();
        if (!c && s->focus == USB) s->focus = VOLUME;
    }
    if (l != s->loaded) {
        s->loaded = l, ui_mark_all();
        if (!l && s->focus == NOW) s->focus = BRIGHT;
    }
    if (now_seq() != s->now_seen) s->now_seen = now_seq(), s->title.n = s->artist.n = 0, ui_mark(box(NOW));
    now_cover();
    if (now_cover_seq() != s->cover_seen) s->cover_seen = now_cover_seq(), ui_mark(box(NOW));
    if (ring_on(s) != s->ring_drawn) s->ring_drawn = ring_on(s), ui_mark_all(); /* the ring's time is up: drawn without it */
}

/* r, round by radius, in rgb, ringed when the wheel is on it: white, or the accent on a slider
 * that has the wheel, a gap of `behind` between */
static void shape(rect_t r, float radius, uint32_t rgb, int ring, uint32_t behind) {
    rectf_t f = { (float)r.x, (float)r.y, (float)r.w, (float)r.h };
    if (ring) {
        fill_round((rectf_t){ f.x - RING, f.y - RING, f.w + 2 * RING, f.h + 2 * RING }, radius + RING, 0,
                   ring == 2 ? RGB_ACCENT : tok(TOK_PRIMARY), 1, 0);
        fill_round((rectf_t){ f.x - 1, f.y - 1, f.w + 2, f.h + 2 }, radius + 1, 0, behind, 1, 0);
    }
    fill_round(f, radius, 0, rgb, 1, 0);
}
/* a glyph of the theme's, centred on cx, cy in a px box (tools/art.py CC_ICONS); Light Mode's
 * ink (CC_LIGHT, `_l`) but on a lit circle, whose white stays */
static void glyph(const char *stem, int light, int cx, int cy, int px) {
    char name[24];
    snprintf(name, sizeof name, "cc_%s%s.", stem, light ? "_l" : "");
    const image_t *im = icon(name);
    if (im) image_draw(im, (rectf_t){ (float)(cx - px / 2), (float)(cy - px / 2), (float)im->w, (float)im->h }, 0, 0, 256, -1);
}
static void text_at(run_t *run, int face, int px, const char *str, int max, float x, int cy, uint32_t rgb, int right) {
    if (!run->n || run_stale(run)) run_make(run, face, px, str, max);
    run_draw(run, right ? x - run->w : x, (float)cy - (float)px / 2, rgb, 256);
}

static void cc_draw(void *st, rect_t r) {
    struct cc_st *s = st;
    int shown = ring_on(s), dy = s->dy, light = ui_light != 0;
    uint32_t block = tok(TOK_RAISED), off = tok(TOK_OFF), canvas_rgb = tok(TOK_CANVAS);
    fill(r, canvas_rgb);
    rect_t b = { X0, Y0 + dy, BLOCK_W, BLOCK_H };
    fill_round((rectf_t){ (float)b.x, (float)b.y, (float)b.w, (float)b.h }, BLOCK_R, 0, block, 1, 0);
    rect_t np = box(NOW);
    np.y += dy;
    shape(np, BLOCK_R, block, s->focus == NOW && shown, canvas_rgb);
    for (int i = 0; i < STOPS; ++i) {
        rect_t q = box(i);
        q.y += dy;
        int ring = i == s->focus && shown ? 1 + (s->grab == i) : 0;
        if (i == NOW) continue; /* drawn under the rest, above */
        if (i == USB) {
            if (!cable()) continue;
            int cy = q.y + q.h / 2, x1 = q.x + q.w;
            if (ring) { /* the mock's: 2 px wide, USB_RING out */
                rectf_t o = { (float)(q.x - USB_RING), (float)(q.y - USB_RING), (float)(q.w + 2 * USB_RING), (float)(q.h + 2 * USB_RING) };
                fill_round(o, o.h / 2, 0, tok(TOK_PRIMARY), 1, 0);
                fill_round((rectf_t){ o.x + 2, o.y + 2, o.w - 4, o.h - 4 }, (o.h - 4) / 2, 0, canvas_rgb, 1, 0);
            }
            fill_round((rectf_t){ (float)q.x, (float)q.y, (float)q.w, (float)q.h }, (float)q.h / 2, 0, block, 1, 0);
            glyph("usb", light, q.x + USB_ICON_X, cy, USB_ICON);
            text_at(&s->usb, FACE_MEDIUM, TYPE(15), "USB", 120, (float)(q.x + USB_TEXT_X), cy, tok(TOK_PRIMARY), 0);
            char v[24];
            status_told_value("usb", v, sizeof v);
            if (strcmp(v, s->usb_s)) snprintf(s->usb_s, sizeof s->usb_s, "%s", v), s->usb_value.n = 0;
            if (*v) text_at(&s->usb_value, FACE_REGULAR, TYPE(15), s->usb_s, 200, (float)(x1 - USB_VALUE_END), cy, tok(TOK_SECONDARY), 1);
            glyph("chev", light, x1 - USB_CHEV_X, cy, USB_CHEV);
            continue;
        }
        if (i == BRIGHT || i == VOLUME) {
            shape(q, (float)q.h / 2, off, ring, canvas_rgb);
            float fw = (float)q.w * clampf((float)s->fill[i - BRIGHT].x, 0, 1);
            fill_round((rectf_t){ (float)q.x, (float)q.y, fw > q.h ? fw : (float)q.h, (float)q.h }, (float)q.h / 2, 0,
                       light ? FILL_RGB_L : FILL_RGB, 1, 0);
            glyph(stops[i].glyph, light, q.x + q.h / 2 + 2, q.y + q.h / 2, 18);
            continue;
        }
        int on = lit(s, i), cx = q.x + q.w / 2, cy = q.y + q.h / 2;
        if (ring) {
            fill_disc((float)cx, (float)cy, CIRCLE_R + RING, tok(TOK_PRIMARY), 1);
            fill_disc((float)cx, (float)cy, CIRCLE_R + 1, block, 1);
        }
        fill_disc((float)cx, (float)cy, CIRCLE_R, on ? RGB_ACCENT : off, 1);
        char wifi[16];
        if (i == WIFI) snprintf(wifi, sizeof wifi, s->wifi < 0 ? "wifi_slash" : s->wifi == 4 ? "wifi_x" : "wifi_%d", s->wifi);
        glyph(i == WIFI ? wifi : stops[i].glyph, light && !on, cx, cy, ICON);
        run_t *name = &s->names[i];
        if (!name->n || run_stale(name)) run_make(name, FACE_MEDIUM, LABEL_PX, stops[i].name, -1);
        run_draw(name, (float)cx - name->w / 2, (float)(cy + LABEL_DY) - (float)LABEL_PX / 2, tok(on ? TOK_PRIMARY : TOK_SECONDARY), 256);
    }
    /* Now Playing: the cover as Home's pane finds it (demo's cover=, ui/now.c), else the grey square */
    const ui_now *n = now_get();
    rectf_t art = { (float)(np.x + ART_IN), (float)(np.y + ART_IN), ART, ART };
    const image_t *cover = loaded() ? now_cover() : 0;
    if (cover) {
        float side = (float)(cover->w < cover->h ? cover->w : cover->h);
        image_draw_part(cover, (rectf_t){ (cover->w - side) / 2, (cover->h - side) / 2, side, side }, art, ART_R, 0, 256, -1);
    } else
        fill_round(art, ART_R, 0, off, 1, 0);
    int tx = np.x + ART_IN + ART + TEXT_GAP, tw = np.x + np.w - ART_IN - tx, mid = np.y + ART_IN + ART / 2;
    text_at(&s->title, FACE_MEDIUM, TYPE(16), loaded() && *n->title ? n->title : "Not Playing", tw, (float)tx, mid - 10, tok(TOK_PRIMARY), 0);
    if (loaded() && *n->artist) text_at(&s->artist, FACE_REGULAR, TYPE(13), n->artist, tw, (float)tx, mid + 10, tok(TOK_SECONDARY), 0);
    for (int i = PREV; i <= NEXT; ++i) {
        rect_t q = box(i);
        glyph(i == PREV ? "back" : i == NEXT ? "forward" : n->playing ? "pause" : "play", light, q.x + q.w / 2, q.y + dy + q.h / 2,
              i == PLAY ? 30 : ICON);
    }
    fill_round((rectf_t){ SCREEN_W / 2 - GRAB_W / 2, GRAB_Y + dy, GRAB_W, GRAB_H }, 2.5f, 0, light ? GRAB_RGB_L : GRAB_RGB, 1, 0);
}
static void cc_close(void *st) { free(st); }
static void cc_shown(void *st) {
    (void)st;
    ui_mark_all();
}
static int cc_where(void *st, char *name, size_t room) {
    snprintf(name, room, "control");
    return ((struct cc_st *)st)->focus;
}
static void cc_select(void *st, int sel) {
    if (sel >= 0 && sel < STOPS) ((struct cc_st *)st)->focus = sel;
}
static int cc_trace(void *st, char *out, size_t room) {
    struct cc_st *s = st;
    return snprintf(out, room, "sel=%d ring=%d grab=%d land=%d", s->focus, ring_on(s), s->grab >= 0, s->dy);
}
const ui_page page_control = { "control", cc_open, cc_event, cc_step, cc_draw, cc_close, cc_shown, cc_where, cc_select, cc_trace };

void control_push(void) { ui_push(&page_control, 0); }
