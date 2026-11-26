/* Text: the theme's faces rasterised by stb_truetype and kept as coverage bitmaps, one per face,
 * size and code point; a string is laid out once into a run (its glyphs and their pens), and a
 * row keeps its runs while it is in sight, so a repaint of a row measures nothing and reads no
 * clock. A size is AWTK's: the face scaled so that its ascent less its descent is the size
 * (stbtt_ScaleForPixelHeight), which is what the theme's sizes mean (assets/themes).
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

#define GLYPHS 1024 /* a power of two */

struct glyph {
    uint32_t key;   /* face << 28 | px << 21 | code point, 0 an empty slot */
    uint32_t used;  /* the frame that last drew it */
    int16_t x0, y0; /* the bitmap's corner from the pen, on the baseline */
    uint16_t w, h;
    int16_t advance;
    uint8_t *bits;
};

static struct {
    stbtt_fontinfo info;
    int ok;
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
        unsigned at = kept[i].key * 2654435761u >> 22 & (GLYPHS - 1);
        while (cache[at].key) at = (at + 1) & (GLYPHS - 1);
        cache[at] = kept[i];
    }
    filled_at = frame;
    ++generation;
}

static const struct glyph *glyph_get(int face, int px, unsigned cp) {
    if (face != FACE_FALLBACK && faces[FACE_FALLBACK].ok &&
        !stbtt_FindGlyphIndex(&faces[face].info, (int)cp))
        face = FACE_FALLBACK;
    uint32_t key = (uint32_t)face << 28 | (uint32_t)px << 21 | (cp & 0x1fffff);
    unsigned at = key * 2654435761u >> 22 & (GLYPHS - 1);
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
        for (at = key * 2654435761u >> 22 & (GLYPHS - 1); cache[at].key;
             at = (at + 1) & (GLYPHS - 1));
    }
    struct glyph *g = &cache[at];
    stbtt_fontinfo *f = &faces[face].info;
    float scale = stbtt_ScaleForPixelHeight(f, (float)px);
    int adv, lsb, w = 0, h = 0, x0 = 0, y0 = 0;
    stbtt_GetCodepointHMetrics(f, (int)cp, &adv, &lsb);
    g->bits = stbtt_GetCodepointBitmap(f, scale, scale, (int)cp, &w, &h, &x0, &y0);
    g->key = key, g->w = (uint16_t)w, g->h = (uint16_t)h, g->x0 = (int16_t)x0, g->y0 = (int16_t)y0;
    /* a whole number of pixels, rounded, as AWTK keeps a glyph's advance: with it every glyph of a
     * Songs row lands on the column demo draws it in (docs/ui-rewrite.md#measurements) */
    g->advance = (int16_t)((float)adv * scale + 0.5f);
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

void run_make(run_t *r, int face, int px, const char *s, int max) {
    const char *start = s;
    for (int tries = 0;; ++tries) {
        int pen = 0, cut_pen = 0;
        int cut = 0; /* glyphs that fit with the ellipsis after them */
        s = start, r->n = 0, r->made = generation;
        const struct glyph *dots = max >= 0 ? glyph_get(face, px, 0x2026) : 0;
        while (*s && r->n < sizeof r->at / sizeof *r->at - 1) {
            const struct glyph *g = glyph_get(face, px, utf8(&s));
            if (dots && pen + dots->advance <= max) cut = r->n, cut_pen = pen;
            r->at[r->n].g = g, r->at[r->n].x = (int16_t)pen;
            ++r->n, pen += g->advance;
            if (dots && pen > max) {
                r->n = (uint16_t)cut, pen = cut_pen;
                r->at[r->n].g = dots, r->at[r->n].x = (int16_t)pen;
                ++r->n, pen += dots->advance;
                break;
            }
        }
        r->w = (int16_t)pen;
        /* the cache turned over while this run was laid out: its first glyphs may be gone */
        if (r->made == generation || tries) break;
    }
    r->made = generation;
}
int run_stale(const run_t *r) { return r->made != generation; }

void run_draw(const run_t *r, int x, int y, uint32_t rgb) {
    if (!r->n) return;
    /* the baseline: the line box is px high, the face's ascent from its top */
    const struct glyph *first = r->at[0].g;
    int face = (int)(first->key >> 28), px = (int)(first->key >> 21 & 0x7f), ascent, descent, gap;
    stbtt_GetFontVMetrics(&faces[face == FACE_FALLBACK ? FACE_REGULAR : face].info, &ascent,
                          &descent, &gap);
    int base = y + (int)((float)ascent * (float)px / (float)(ascent - descent) + 0.5f);
    rect_t c = clip_get();
    for (int i = 0; i < r->n; ++i) {
        struct glyph *g = (struct glyph *)r->at[i].g;
        int gx = x + r->at[i].x + g->x0;
        g->used = frame;
        if (gx >= c.x + c.w) break;
        if (gx + g->w <= c.x || !g->bits) continue;
        blit_a8(g->bits, g->w, g->h, gx, base + g->y0, rgb);
    }
}
