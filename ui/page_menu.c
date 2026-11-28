/* A menu sheet over the page (docs/oled-design.md#menus, patch/menu_modal.c): actions on the highlighted item. The
 * page behind recedes, drawn at 94% of its size about the screen's middle and dimmed half way to black (Light Mode:
 * 35%), its status row with it; the sheet rises over it from y 40 to the screen's foot, its top corners 40 px, a grab
 * bar at y 46. The sheet's material is the receded page blurred (a quarter-size grid, three box passes, about a 20 px
 * gaussian), its colour raised 1.6 times and 28% of its brightness kept under a 6% white tint (Light Mode: white at
 * 75% over it), made once as it opens. A header (the item's 52 px cover, its name in Inter Medium 20 and a line in 15)
 * and the rows, a list on the house grid from y 122, each with its symbol at the right column in Text Secondary.
 *
 * The sheet rises over SH_RISE_US, cubic out, and falls over SH_FALL_US when Return closes it or Centre picks a row; a
 * pick is acted on once it has gone, on the page under it. Under Reduce Motion it stands and goes at once.
 *
 * ponytail: the header stays while the rows scroll (demo's scrolls away with them) and a menu has no destructive
 * rows, gaps or settings yet; the material is made once, so a page that changes under the sheet shows as it was. */
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "q2ui.h"

#define SH_TOP 40
#define SH_H (SCREEN_H - SH_TOP)
#define SH_R 40
#define SH_GRAB_Y 46
#define SH_ART 52
#define SH_ART_Y 56
#define SH_NAME_X 88
#define SH_ROWS_Y 122
#define SH_ROWS_END 282
#define SH_SHRINK 0.94f
#define SH_KEEP_PAGE 0.5f /* the receding page kept: half way to black, */
#define SH_KEEP_PAGE_L 0.65f /* in Light Mode black at 35% */
#define SH_QW 94          /* the blur's quarter-size grid */
#define SH_QH 80
#define SH_BLUR 5 /* its box radius, three passes each way */
#define SH_SAT 1.6f
#define SH_KEEP 0.28f
#define SH_TINT 0.06f
#define SH_TINT_L 0.75f
#define SH_RISE_US 260000u
#define SH_FALL_US 180000u

static struct {
    menu_t m;
    uint32_t *snap;   /* the screen as the sheet opened over it */
    uint32_t *recede; /* ... receded, at the sheet's rest */
    uint32_t *mat;    /* the sheet's material, its SH_H rows */
    uint32_t *still;  /* the sheet whole with its contents, for its rise and fall */
    float edge[SH_R]; /* the top corners' inset by row */
    ui_list *list;
    uint64_t at;
    int phase; /* 0 rising, 1 up, 2 falling */
    int picked;
    run_t name, line;
} S;

static float progress(void) {
    if (S.phase == 1 || !list_motion || ui_reduce_motion) return S.phase == 2 ? 0 : 1;
    float t = clampf((float)(ui_time() - S.at) / (float)(S.phase ? SH_FALL_US : SH_RISE_US), 0, 1);
    return S.phase ? 1 - ease_cubic_in(t) : ease_cubic_out(t);
}

static uint32_t mix(uint32_t a, uint32_t b, float t) { return rgb_mix(a, b, t) | 0xff000000u; }
/* the page's pixel at a sub-pixel place, bilinear, clamped to the screen */
static uint32_t sample(const uint32_t *px, float x, float y) {
    x = clampf(x, 0, SCREEN_W - 1.001f), y = clampf(y, 0, SCREEN_H - 1.001f);
    int x0 = (int)x, y0 = (int)y;
    float fx = x - (float)x0, fy = y - (float)y0;
    const uint32_t *p = px + y0 * SCREEN_W + x0;
    return mix(mix(p[0], p[1], fx), mix(p[SCREEN_W], p[SCREEN_W + 1], fx), fy);
}
/* the page behind at share p of its recession, row y from x0 to x1 (made: the receded page at rest, kept) */
static void recede_row(uint32_t *out, int y, int x0, int x1, float p, int made) {
    float scale = 1 - (1 - SH_SHRINK) * p, keep = 1 - (1 - (ui_light ? SH_KEEP_PAGE_L : SH_KEEP_PAGE)) * p;
    unsigned k = (unsigned)(keep * 256);
    for (int x = x0; x < x1; ++x) {
        if (made) {
            out[x] = S.recede[y * SCREEN_W + x];
            continue;
        }
        uint32_t c = sample(S.snap, ((float)x + 0.5f - SCREEN_W / 2.0f) / scale + SCREEN_W / 2.0f - 0.5f,
                            ((float)y + 0.5f - SCREEN_H / 2.0f) / scale + SCREEN_H / 2.0f - 0.5f);
        out[x] = 0xff000000u | ((c >> 16 & 255) * k >> 8) << 16 | ((c >> 8 & 255) * k >> 8) << 8 | ((c & 255) * k >> 8);
    }
}

static void box(float *v, int n, int stride, float *tmp) {
    for (int i = 0; i < n; ++i) tmp[i] = v[i * stride];
    for (int i = 0; i < n; ++i) {
        float sum = 0;
        for (int k = -SH_BLUR; k <= SH_BLUR; ++k) sum += tmp[i + k < 0 ? 0 : i + k >= n ? n - 1 : i + k];
        v[i * stride] = sum / (2 * SH_BLUR + 1);
    }
}
/* The receded page and the material made from it, once */
static int build(void) {
    S.recede = malloc(SCREEN_W * SCREEN_H * sizeof *S.recede);
    S.mat = malloc(SCREEN_W * SH_H * sizeof *S.mat);
    S.still = malloc(SCREEN_W * SCREEN_H * sizeof *S.still);
    float *q = malloc((3 * SH_QW * SH_QH + SH_QW + SH_QH) * sizeof *q);
    if (!S.recede || !S.mat || !S.still || !q) {
        free(q);
        return -1;
    }
    for (int y = 0; y < SCREEN_H; ++y) recede_row(S.recede + y * SCREEN_W, y, 0, SCREEN_W, 1, 0);
    float *pl[3] = { q, q + SH_QW * SH_QH, q + 2 * SH_QW * SH_QH }, *tmp = q + 3 * SH_QW * SH_QH;
    for (int qy = 0; qy < SH_QH; ++qy)
        for (int qx = 0; qx < SH_QW; ++qx) {
            float sum[3] = { 0, 0, 0 };
            for (int j = 0; j < 4; ++j)
                for (int i = 0; i < 4; ++i) {
                    int x = 4 * qx + i < SCREEN_W ? 4 * qx + i : SCREEN_W - 1;
                    uint32_t c = S.recede[(4 * qy + j) * SCREEN_W + x];
                    sum[0] += (float)(c >> 16 & 255), sum[1] += (float)(c >> 8 & 255), sum[2] += (float)(c & 255);
                }
            for (int p = 0; p < 3; ++p) pl[p][qy * SH_QW + qx] = sum[p] / 16;
        }
    for (int p = 0; p < 3; ++p)
        for (int pass = 0; pass < 3; ++pass) {
            for (int qy = 0; qy < SH_QH; ++qy) box(pl[p] + qy * SH_QW, SH_QW, 1, tmp);
            for (int qx = 0; qx < SH_QW; ++qx) box(pl[p] + qx, SH_QH, SH_QW, tmp);
        }
    for (int y = SH_TOP; y < SCREEN_H; ++y) {
        float gy = clampf((float)y / 4 - 0.375f, 0, SH_QH - 1.001f);
        int y0 = (int)gy;
        float fy = gy - (float)y0;
        for (int x = 0; x < SCREEN_W; ++x) {
            float gx = clampf((float)x / 4 - 0.375f, 0, SH_QW - 1.001f), c[3];
            int x0 = (int)gx;
            float fx = gx - (float)x0;
            for (int p = 0; p < 3; ++p) {
                const float *v = pl[p] + y0 * SH_QW + x0;
                c[p] = lerpf(lerpf(v[0], v[1], fx), lerpf(v[SH_QW], v[SH_QW + 1], fx), fy);
            }
            float luma = 0.30f * c[0] + 0.59f * c[1] + 0.11f * c[2];
            uint32_t out = 0xff000000u;
            for (int p = 0; p < 3; ++p) {
                float v = clampf(luma + (c[p] - luma) * SH_SAT, 0, 255);
                v = ui_light ? v * (1 - SH_TINT_L) + 255 * SH_TINT_L : v * SH_KEEP * (1 - SH_TINT) + 255 * SH_TINT;
                out |= (uint32_t)(v + 0.5f) << (16 - 8 * p);
            }
            S.mat[(y - SH_TOP) * SCREEN_W + x] = out;
        }
    }
    free(q);
    for (int i = 0; i < SH_R; ++i) { /* the corner's inset at row i's middle */
        float d = (float)SH_R - (float)i - 0.5f;
        S.edge[i] = (float)SH_R - sqrtf((float)(SH_R * SH_R) - d * d);
    }
    return 0;
}

/* Sheet row sy (0 its top) at screen row y from rows (SH_H of them, SCREEN_W wide), over what out has, its corners
 * smoothed */
static void sheet_row(uint32_t *out, const uint32_t *rows, int sy, int x0, int x1) {
    const uint32_t *in = rows + sy * SCREEN_W;
    float inset = sy < SH_R ? S.edge[sy] : 0;
    for (int x = x0; x < x1; ++x) {
        float d = fminf((float)x + 1 - inset, (float)(SCREEN_W - x) - inset); /* how much of the pixel is in */
        if (d >= 1) out[x] = in[x];
        else if (d > 0) out[x] = mix(out[x], in[x], d);
    }
}

/* The sheet's contents at rest: the grab bar, the header and the rows */
static void contents(rect_t r) {
    fill_round((rectf_t){ (SCREEN_W - 36) / 2.0f, SH_GRAB_Y, 36, 5 }, 2.5f, 0, ui_light ? 0xa1a1a6 : 0x5a5a5e, 1, 0);
    if (S.m.name) {
        rectf_t art = { 24, SH_ART_Y, SH_ART, SH_ART };
        if (S.m.cover) image_draw(S.m.cover, art, 6, 0, 256, -1);
        else fill_round(art, 6, 0, tok(TOK_RAISED_2), 1, 0);
        if (!S.name.n || run_stale(&S.name)) run_make(&S.name, FACE_MEDIUM, TYPE(20), S.m.name, SCREEN_W - SH_NAME_X - 24);
        if (S.m.line && (!S.line.n || run_stale(&S.line)))
            run_make(&S.line, FACE_REGULAR, TYPE(15), S.m.line, SCREEN_W - SH_NAME_X - 24);
        run_draw(&S.name, SH_NAME_X, SH_ART_Y + 4, tok(TOK_PRIMARY), 256);
        if (S.m.line) run_draw(&S.line, SH_NAME_X, SH_ART_Y + 4 + TYPE(20) + 2, tok(TOK_SECONDARY), 256);
    }
    list_draw(S.list, r);
}

static int count(void *ctx) {
    (void)ctx;
    return S.m.n;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    out->title = S.m.rows[i].title, out->flags = ROW_SOFT;
    out->marks[0] = S.m.rows[i].icon;
}
static void fall(int picked) {
    if (S.phase == 2) return;
    S.picked = picked;
    S.phase = 2, S.at = ui_time();
    /* the sheet whole as it goes, for the frames of its fall */
    uint32_t *screen = canvas_swap(S.still);
    rect_t all = { 0, SH_TOP, SCREEN_W, SH_H };
    ui_painting = 1;
    clip_set(all);
    for (int y = SH_TOP; y < SCREEN_H; ++y) memcpy(S.still + y * SCREEN_W, S.mat + (y - SH_TOP) * SCREEN_W, SCREEN_W * sizeof *S.still);
    contents(all);
    ui_painting = 0;
    canvas_swap(screen);
    ui_mark_all(), ui_again();
}
static void chosen(void *ctx, int i) {
    (void)ctx;
    fall(i);
}

static void *m_open(void *arg) {
    S.m = *(const menu_t *)arg;
    S.snap = ui_snapshot_screen();
    S.name.n = S.line.n = 0;
    ui_source src = { .count = count, .row = row, .chosen = chosen, .bare = 1,
                      .box = { 0, SH_ROWS_Y, SCREEN_W, SH_ROWS_END - SH_ROWS_Y } };
    S.list = list_new(&src);
    S.phase = 0, S.at = ui_time(), S.picked = -1;
    if (!S.snap || build()) S.phase = 1; /* no memory for the motion: it stands at once, over black */
    else {
        fall(-1); /* the sheet whole, for its rise */
        S.phase = 0, S.at = ui_time();
    }
    ui_status_hidden = S.snap != 0;
    ui_mark_all(), ui_again();
    return &S;
}
static void m_event(void *st, const input_t *e) {
    (void)st;
    if (S.phase != 1) return; /* rising or falling: no input */
    if (e->type == IN_KEY_UP && e->value == KEY_UI_RETURN) {
        fall(-1);
        return;
    }
    list_event(S.list, e);
}
static void m_step(void *st) {
    (void)st;
    list_step(S.list);
    if (S.phase == 1) return;
    float p = progress();
    if (S.phase == 0 && p >= 1) S.phase = 1;
    if (S.phase == 2 && p <= 0) {
        int picked = S.picked;
        menu_t m = S.m;
        ui_pop_still();
        if (picked >= 0 && m.picked) m.picked(m.ctx, picked);
        return;
    }
    ui_mark_all(), ui_again();
}
static void m_draw(void *st, rect_t r) {
    (void)st;
    float p = progress();
    if (!S.recede) { /* with no memory for the material: a plain sheet over black */
        fill(r, 0);
        fill_round((rectf_t){ 0, SH_TOP, SCREEN_W, SH_H + SH_R }, SH_R, 0, tok(TOK_RAISED), 1, 0);
        contents(r);
        return;
    }
    int top = SH_TOP + (int)((1 - p) * SH_H + 0.5f);
    for (int y = r.y; y < r.y + r.h; ++y) {
        uint32_t *out = canvas + y * SCREEN_W;
        recede_row(out, y, r.x, r.x + r.w, p, p >= 1);
        if (y < top) continue;
        if (S.phase == 1) sheet_row(out, S.mat, y - top, r.x, r.x + r.w);
        else sheet_row(out, S.still + SH_TOP * SCREEN_W, y - top, r.x, r.x + r.w);
    }
    if (S.phase == 1) contents(r);
}
static void m_close(void *st) {
    (void)st;
    list_free(S.list);
    free(S.snap), free(S.recede), free(S.mat), free(S.still);
    S.snap = S.recede = S.mat = S.still = 0, S.list = 0;
    ui_status_hidden = 0;
}
static const ui_page page_menu = { "menu", m_open, m_event, m_step, m_draw, m_close, 0, 0, 0 };

void menu_push(const menu_t *m) { ui_push_still(&page_menu, (void *)m); }
