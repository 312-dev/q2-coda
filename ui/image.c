/* Pictures: PNG and JPEG decoded by stb_image (public domain, fetched by tools/ui.py as
 * stb_truetype is), kept as 0xAARRGGBB, and drawn scaled at a sub-pixel place with their edges
 * and their own alpha blended onto what is there. An icon is a picture whose alpha is inked in
 * the row's colour (tint), so one white glyph serves every ink. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_PNG
#define STBI_ONLY_JPEG
#define STBI_NO_STDIO
#define STBI_NO_LINEAR
#define STBI_NO_HDR
#define STB_IMAGE_STATIC
#include "stb_image.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include "px.h"
#include "q2ui.h"

image_t *image_mem(const uint8_t *data, size_t n) {
    int w, h, comp;
    uint8_t *rgba = stbi_load_from_memory(data, (int)n, &w, &h, &comp, 4);
    if (!rgba) return 0;
    image_t *im = malloc(sizeof *im);
    im->w = w, im->h = h, im->px = (uint32_t *)rgba;
    for (int i = 0; i < w * h; ++i) {
        const uint8_t *p = rgba + i * 4;
        im->px[i] = (uint32_t)p[3] << 24 | (uint32_t)p[0] << 16 | (uint32_t)p[1] << 8 | p[2];
    }
    return im;
}
image_t *image_load(const char *path) {
    struct stat st;
    int fd = open(path, O_RDONLY);
    if (fd < 0) return 0;
    image_t *im = 0;
    if (!fstat(fd, &st) && st.st_size > 0 && st.st_size < 32 << 20) {
        uint8_t *data = malloc((size_t)st.st_size);
        if (data && read(fd, data, (size_t)st.st_size) == st.st_size)
            im = image_mem(data, (size_t)st.st_size);
        free(data);
    }
    close(fd);
    return im;
}
image_t *image_new(int w, int h) {
    image_t *im = malloc(sizeof *im);
    im->w = w, im->h = h, im->px = calloc((size_t)(w * h), 4);
    return im;
}
void image_free(image_t *im) {
    if (!im) return;
    free(im->px);
    free(im);
}

/* src scaled to cover w x h and cropped about its middle, each pixel the mean of the source
 * pixels it covers: a cover brought down to a thumbnail keeps no shimmer. */
image_t *image_fit(const image_t *src, int w, int h) {
    image_t *im = malloc(sizeof *im);
    im->w = w, im->h = h, im->px = malloc((size_t)(w * h) * 4);
    float scale = (float)src->w / (float)w < (float)src->h / (float)h ? (float)src->w / (float)w
                                                                      : (float)src->h / (float)h;
    float ox = ((float)src->w - (float)w * scale) / 2, oy = ((float)src->h - (float)h * scale) / 2;
    for (int y = 0; y < h; ++y) {
        int y0 = (int)(oy + (float)y * scale), y1 = (int)(oy + (float)(y + 1) * scale);
        if (y1 <= y0) y1 = y0 + 1;
        if (y1 > src->h) y1 = src->h;
        for (int x = 0; x < w; ++x) {
            int x0 = (int)(ox + (float)x * scale), x1 = (int)(ox + (float)(x + 1) * scale);
            if (x1 <= x0) x1 = x0 + 1;
            if (x1 > src->w) x1 = src->w;
            unsigned a = 0, r = 0, g = 0, b = 0, n = 0;
            for (int j = y0; j < y1; ++j)
                for (int i = x0; i < x1; ++i, ++n) {
                    uint32_t p = src->px[j * src->w + i];
                    a += p >> 24, r += p >> 16 & 0xff, g += p >> 8 & 0xff, b += p & 0xff;
                }
            if (!n) n = 1;
            im->px[y * w + x] = (a / n) << 24 | (r / n) << 16 | (g / n) << 8 | b / n;
        }
    }
    return im;
}

/* im at (fx, fy) in its own pixels, each in 1/65536: the four round it weighed by how near each is */
static inline uint32_t sample(const image_t *im, int fx, int fy) {
    if (fx < 0) fx = 0;
    if (fy < 0) fy = 0;
    int x = fx >> 16, y = fy >> 16;
    if (x > im->w - 1) x = im->w - 1, fx = 0;
    if (y > im->h - 1) y = im->h - 1, fy = 0;
    unsigned wx = (unsigned)(fx >> 8 & 0xff), wy = (unsigned)(fy >> 8 & 0xff);
    const uint32_t *p = im->px + y * im->w + x;
    if (!wx && !wy) return *p;
    int x1 = x + 1 < im->w ? 1 : 0, y1 = y + 1 < im->h ? im->w : 0;
    uint32_t p00 = p[0], p10 = p[x1], p01 = p[y1], p11 = p[y1 + x1];
    /* two channels at a time: alpha and green, red and blue */
#define PAIR(c, shift, mask) ((uint32_t)(c) >> (shift) & (mask))
    uint32_t ag = ((PAIR(p00, 8, 0xff00ff) * (256 - wx) + PAIR(p10, 8, 0xff00ff) * wx) >> 8 & 0xff00ff) * (256 - wy) +
                  ((PAIR(p01, 8, 0xff00ff) * (256 - wx) + PAIR(p11, 8, 0xff00ff) * wx) >> 8 & 0xff00ff) * wy;
    uint32_t rb = ((PAIR(p00, 0, 0xff00ff) * (256 - wx) + PAIR(p10, 0, 0xff00ff) * wx) >> 8 & 0xff00ff) * (256 - wy) +
                  ((PAIR(p01, 0, 0xff00ff) * (256 - wx) + PAIR(p11, 0, 0xff00ff) * wx) >> 8 & 0xff00ff) * wy;
#undef PAIR
    return (ag & 0xff00ff00) | (rb >> 8 & 0xff00ff);
}

void image_draw(const image_t *im, rectf_t r, float radius, float tilt, unsigned alpha, long tint) {
    image_draw_part(im, (rectf_t){ 0, 0, im ? (float)im->w : 0, im ? (float)im->h : 0 }, r, radius, tilt, alpha, tint);
}

void image_draw_part(const image_t *im, rectf_t part, rectf_t r, float radius, float tilt, unsigned alpha, long tint) {
    float hw = r.w / 2, hh = r.h / 2, cx = r.x + hw, cy = r.y + hh;
    if (!im || hw <= 0 || hh <= 0 || !alpha) return;
    if (radius > hw) radius = hw;
    if (radius > hh) radius = hh;
    float st = sinf(tilt), ct = cosf(tilt), grow = PX_EYE / (PX_EYE - hh * fabsf(st));
    rect_t clip = clip_get(), c,
           box = rect_of((rectf_t){ cx - hw * grow, cy - hh * grow, 2 * hw * grow, 2 * hh * grow }, 1);
    if (!rect_meet(box, clip, &c)) return;
    float kx = part.w / r.w, ky = part.h / r.h;
    int end = c.x + c.w, hwq = (int)(hw * PX_ONE + 0.5f), hhq = (int)(hh * PX_ONE + 0.5f), rq = (int)(radius * PX_ONE + 0.5f);
    for (int y = c.y; y < c.y + c.h; ++y) {
        /* one row of the picture a scanline, at one scale (draw.c fill_round) */
        float dy = (float)y + 0.5f - cy, v = dy * PX_EYE / (PX_EYE * ct + dy * st);
        float s = PX_EYE / (PX_EYE - v * st), av = fabsf(v);
        int avq = (int)(av * PX_ONE + 0.5f), sq = (int)(s * 256 + 0.5f), inv = (int)(65536 / s);
        int mid = 128 - (((avq - hhq) * sq) >> 6);
        if (mid <= 0) continue;
        if (mid > 256) mid = 256;
        float inner = (hw - radius - 1) * s;
        int in0 = (int)ceilf(cx - inner), in1 = (int)floorf(cx + inner);
        if (in0 < c.x) in0 = c.x;
        if (in1 > end) in1 = end;
        if (in1 < in0) in0 = in1 = c.x;
        /* the picture's column at a screen column is a straight line of it */
        int fy = (int)((part.y + (v + hh) * ky - 0.5f) * 65536), step = (int)(kx / s * 65536);
        int fx = (int)((part.x + (((float)c.x + 0.5f - cx) / s + hw) * kx - 0.5f) * 65536);
        int u0 = (int)((((float)c.x - cx) / s) * PX_ONE);
        uint32_t *row = canvas + y * SCREEN_W;
        for (int x = c.x; x < end; ++x, fx += step) {
            unsigned cov = (unsigned)mid;
            if (x < in0 || x >= in1) {
                int u = (int)(((int64_t)(2 * (x - c.x) + 1) * inv) >> 11) + u0;
                int a = 128 - ((px_box_dist(u < 0 ? -u : u, avq, hwq, hhq, rq) * sq) >> 6);
                if (a <= 0) continue;
                cov = (unsigned)(a > 256 ? 256 : a);
            }
            uint32_t t = sample(im, fx, fy);
            unsigned a = t >> 24;
            a = ((a + (a >> 7)) * cov >> 8) * alpha >> 8;
            if (!a) continue;
            uint32_t ink = tint >= 0 ? (uint32_t)tint : px_picture(t, dither64[(y & 63) * 64 + (x & 63)]);
            row[x] = a >= 256 ? 0xff000000 | ink : px_mix(row[x], ink, a);
        }
    }
}

const char *icon_dir = "/release/assets/default/raw/images/xx";
const image_t *icon(const char *name) {
    static struct {
        char name[24];
        image_t *im;
    } kept[96]; /* the status row's sync arrows alone are 30 */
    static int n;
    for (int i = 0; i < n; ++i)
        if (!strcmp(kept[i].name, name)) return kept[i].im;
    if (n == 96 || strlen(name) >= sizeof kept[0].name) return 0;
    char path[320];
    /* a name with its own ending (mode_shuffle_on) is that file; a bare one is its white glyph */
    snprintf(path, sizeof path, strchr(name, '.') ? "%s/oled_%.*s.png" : "%s/oled_%.*s_w.png", icon_dir,
             strchr(name, '.') ? (int)(strchr(name, '.') - name) : (int)strlen(name), name);
    strcpy(kept[n].name, name);
    kept[n].im = image_load(path);
    return kept[n++].im;
}
