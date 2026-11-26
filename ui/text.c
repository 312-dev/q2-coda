/* Text: the theme's faces rasterised by stb_truetype and kept as coverage bitmaps, one per face,
 * size, code point and quarter-pixel phase; a string is laid out once into a run (its glyphs and
 * their pens), and a row keeps its runs while it is in sight, so a repaint of a row measures
 * nothing and reads no clock. A size is AWTK's: the face scaled so that its ascent less its
 * descent is the size (stbtt_ScaleForPixelHeight), which is what the theme's sizes mean
 * (assets/themes).
 * Pens are the face's own advances, unrounded: a glyph whose pen falls between pixels is
 * rasterised at the nearest quarter pixel left of it (PHASES), so a word's spacing is the
 * face's and not a column grid's. text_whole lays text out as AWTK does instead, every advance
 * rounded to a pixel, for comparing a screen with demo's. A run drawn at a place between pixels
 * (a row gliding) is filtered there by the blit (draw.c blit_a8).
 * A code point the face lacks comes from the fallback face. The cache holds GLYPHS glyphs; full,
 * it drops those no frame has drawn since the last time it filled, and every run is laid out
 * again (run_stale). */
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#define STB_TRUETYPE_IMPLEMENTATION
#define STBTT_STATIC
#include "stb_truetype.h"

#include "q2ui.h"

#define GLYPHS 2048 /* a power of two */
#define PHASES 4    /* sub-pixel pen places a glyph is kept at */

int text_whole;

struct glyph {
    uint32_t key;   /* face << 30 | phase << 28 | px << 21 | code point, 0 an empty slot */
    uint32_t used;  /* the frame that last drew it */
    int16_t x0, y0; /* the bitmap's corner from the pen, on the baseline */
    uint16_t w, h;
    int16_t advance; /* rounded to a pixel, text_whole's */
    float adv;       /* the face's */
    uint8_t *bits;
};

static struct {
    stbtt_fontinfo info;
    int ok;
    float rise; /* the baseline's share of the line box, from its top */
} faces[FACES];
static struct glyph cache[GLYPHS];
static uint32_t frame = 1, generation = 1, filled_at;
struct text_stats text_stats;

static int face_open(int face, const char *path) {
    struct stat st;
    int fd = path ? open(path, O_RDONLY) : -1;
    if (fd < 0 || fstat(fd, &st) < 0) return -1;
    /* mapped, not read: the fallback face is megabytes of glyphs of which a list shows dozens */
    void *data = mmap(0, (size_t)st.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (data == MAP_FAILED) return -1;
    faces[face].ok = stbtt_InitFont(&faces[face].info, data, stbtt_GetFontOffsetForIndex(data, 0));
    if (faces[face].ok) {
        int ascent, descent, gap;
        stbtt_GetFontVMetrics(&faces[face].info, &ascent, &descent, &gap);
        faces[face].rise = (float)ascent / (float)(ascent - descent);
    }
    return faces[face].ok ? 0 : -1;
}
int text_open(const char *regular, const char *medium, const char *fallback) {
    if (face_open(FACE_REGULAR, regular)) return -1;
    if (face_open(FACE_MEDIUM, medium)) faces[FACE_MEDIUM] = faces[FACE_REGULAR];
    face_open(FACE_FALLBACK, fallback);
    return 0;
}
void text_frame(void) { ++frame; }

/* The cache is full: every glyph no frame drew since it last filled goes, and the runs with it. */
static void evict(void) {
    for (int i = 0; i < GLYPHS; ++i) {
        struct glyph *g = &cache[i];
        if (!g->key || g->used > filled_at) continue;
        text_stats.bytes -= (unsigned)(g->w * g->h);
        free(g->bits);
        memset(g, 0, sizeof *g);
        --text_stats.glyphs, ++text_stats.evictions;
    }
    /* open addressing: what stays is put back where a lookup finds it */
    static struct glyph kept[GLYPHS];
    memcpy(kept, cache, sizeof cache);
    memset(cache, 0, sizeof cache);
    for (int i = 0; i < GLYPHS; ++i) {
        if (!kept[i].key) continue;
        unsigned at = kept[i].key * 2654435761u >> 21 & (GLYPHS - 1);
        while (cache[at].key) at = (at + 1) & (GLYPHS - 1);
        cache[at] = kept[i];
    }
    filled_at = frame;
    ++generation;
}

static const struct glyph *glyph_get(int face, int px, unsigned cp, int phase) {
    if (face != FACE_FALLBACK && faces[FACE_FALLBACK].ok &&
        !stbtt_FindGlyphIndex(&faces[face].info, (int)cp))
        face = FACE_FALLBACK;
    uint32_t key = (uint32_t)face << 30 | (uint32_t)phase << 28 | (uint32_t)px << 21 | (cp & 0x1fffff);
    unsigned at = key * 2654435761u >> 21 & (GLYPHS - 1);
    for (;; at = (at + 1) & (GLYPHS - 1)) {
        if (cache[at].key == key) return &cache[at];
        if (!cache[at].key) break;
    }
    if (text_stats.glyphs >= GLYPHS * 3 / 4) {
        evict();
        if (text_stats.glyphs >= GLYPHS * 3 / 4) { /* a frame that needs them all: start over */
            filled_at = frame + 1;
            evict();
        }
        for (at = key * 2654435761u >> 21 & (GLYPHS - 1); cache[at].key;
             at = (at + 1) & (GLYPHS - 1));
    }
    struct glyph *g = &cache[at];
    stbtt_fontinfo *f = &faces[face].info;
    float scale = stbtt_ScaleForPixelHeight(f, (float)px);
    int adv, lsb, w = 0, h = 0, x0 = 0, y0 = 0;
    stbtt_GetCodepointHMetrics(f, (int)cp, &adv, &lsb);
    g->bits = stbtt_GetCodepointBitmapSubpixel(f, scale, scale, (float)phase / PHASES, 0, (int)cp,
                                               &w, &h, &x0, &y0);
    g->key = key, g->w = (uint16_t)w, g->h = (uint16_t)h, g->x0 = (int16_t)x0, g->y0 = (int16_t)y0;
    /* a whole number of pixels, rounded, as AWTK keeps a glyph's advance: with it every glyph of a
     * Songs row lands on the column demo draws it in (docs/ui-rewrite.md#measurements) */
    g->advance = (int16_t)((float)adv * scale + 0.5f);
    g->adv = (float)adv * scale;
    ++text_stats.glyphs, ++text_stats.made;
    text_stats.bytes += (unsigned)(w * h);
    return g;
}

static unsigned utf8(const char **s) {
    const unsigned char *p = (const unsigned char *)*s;
    unsigned c = *p++, n = c < 0x80 ? 0 : c < 0xe0 ? 1 : c < 0xf0 ? 2 : 3;
    if (n) c &= 0x3f >> n;
    while (n-- && (*p & 0xc0) == 0x80) c = c << 6 | (*p++ & 0x3f);
    *s = (const char *)p;
    return c;
}

static inline float advance_of(const struct glyph *g) {
    return text_whole ? (float)g->advance : g->adv;
}
static inline int phase_of(float pen) {
    return text_whole ? 0 : (int)((pen - (float)(int)pen) * PHASES);
}

void run_make(run_t *r, int face, int px, const char *s, int max) {
    const char *start = s;
    if (px > 127) px = 127;
    for (int tries = 0;; ++tries) {
        float pen = 0, cut_pen = 0;
        int cut = 0; /* glyphs that fit with the ellipsis after them */
        s = start, r->n = 0, r->cut = 0, r->made = generation;
        float dots = max >= 0 ? advance_of(glyph_get(face, px, 0x2026, 0)) : 0;
        while (*s && r->n < sizeof r->at / sizeof *r->at - 1) {
            const struct glyph *g = glyph_get(face, px, utf8(&s), phase_of(pen));
            if (max >= 0 && pen + dots <= (float)max) cut = r->n, cut_pen = pen;
            r->at[r->n].g = g, r->at[r->n].x = (int16_t)pen;
            ++r->n, pen += advance_of(g);
            if (max >= 0 && pen > (float)max) {
                r->n = (uint16_t)cut, pen = cut_pen, r->cut = 1;
                r->at[r->n].g = glyph_get(face, px, 0x2026, phase_of(pen)), r->at[r->n].x = (int16_t)pen;
                ++r->n, pen += dots;
                break;
            }
        }
        r->w = pen;
        /* the cache turned over while this run was laid out: its first glyphs may be gone */
        if (r->made == generation || tries) break;
    }
    r->made = generation;
}
int run_stale(const run_t *r) { return r->made != generation; }

float text_width(int face, int px, const char *s) {
    run_t r;
    run_make(&r, face, px, s, -1);
    return r.w;
}

void run_draw(const run_t *r, float x, float y, uint32_t rgb, unsigned alpha) {
    if (!r->n || !alpha) return;
    /* the baseline: the line box is px high, the face's ascent from its top, on a whole row of
     * the box so text at rest is as sharp as its glyphs were made */
    const struct glyph *first = r->at[0].g;
    int face = (int)(first->key >> 30), px = (int)(first->key >> 21 & 0x7f);
    float base = y + (float)(int)(faces[face == FACE_FALLBACK ? FACE_REGULAR : face].rise * (float)px + 0.5f);
    rect_t c = clip_get();
    for (int i = 0; i < r->n; ++i) {
        struct glyph *g = (struct glyph *)r->at[i].g;
        float gx = x + (float)(r->at[i].x + g->x0);
        g->used = frame;
        if (gx >= (float)(c.x + c.w)) break;
        if (gx + (float)g->w + 1 <= (float)c.x || !g->bits) continue;
        blit_a8(g->bits, g->w, g->h, gx, base + (float)g->y0, rgb, alpha);
    }
}
