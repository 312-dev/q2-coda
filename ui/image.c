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

/* im at (fx, fy) in its own pixels, the four round it weighed by how near each is */
static inline uint32_t sample(const image_t *im, float fx, float fy) {
    if (fx < 0) fx = 0;
    if (fy < 0) fy = 0;
    int x = (int)fx, y = (int)fy;
    if (x > im->w - 1) x = im->w - 1;
    if (y > im->h - 1) y = im->h - 1;
    unsigned wx = (unsigned)((fx - (float)x) * 256), wy = (unsigned)((fy - (float)y) * 256);
    int x1 = x + 1 < im->w ? x + 1 : x, y1 = y + 1 < im->h ? y + 1 : y;
    uint32_t p00 = im->px[y * im->w + x], p10 = im->px[y * im->w + x1];
    uint32_t p01 = im->px[y1 * im->w + x], p11 = im->px[y1 * im->w + x1];
    if (!wx && !wy) return p00;
#define LERP(a, b, w, shift) \
    ((((a) >> (shift) & 0xff) * (256 - (w)) + ((b) >> (shift) & 0xff) * (w)) >> 8)
    uint32_t out = 0;
    for (unsigned shift = 0; shift < 32; shift += 8) {
        unsigned top = LERP(p00, p10, wx, shift), bottom = LERP(p01, p11, wx, shift);
        out |= ((top * (256 - wy) + bottom * wy) >> 8) << shift;
    }
#undef LERP
    return out;
}

void image_draw(const image_t *im, rectf_t r, float radius, float tilt, unsigned alpha, long tint) {
    float hw = r.w / 2, hh = r.h / 2, cx = r.x + hw, cy = r.y + hh;
    if (!im || hw <= 0 || hh <= 0 || !alpha) return;
    if (radius > hw) radius = hw;
    if (radius > hh) radius = hh;
    float st = sinf(tilt), ct = cosf(tilt), grow = PX_EYE / (PX_EYE - hh * fabsf(st));
    rect_t clip = clip_get(), c,
           box = rect_of((rectf_t){ cx - hw * grow, cy - hh * grow, 2 * hw * grow, 2 * hh * grow }, 1);
    if (!rect_meet(box, clip, &c)) return;
    float kx = (float)im->w / r.w, ky = (float)im->h / r.h;
    for (int y = c.y; y < c.y + c.h; ++y) {
        float dy = (float)y + 0.5f - cy, v = dy * PX_EYE / (PX_EYE * ct + dy * st);
        float s = PX_EYE / (PX_EYE - v * st), av = fabsf(v);
        if ((av - hh) * s >= 0.5f) continue;
        float sy = (v + hh) * ky - 0.5f;
        uint32_t *p = canvas + y * SCREEN_W + c.x;
        for (int x = c.x; x < c.x + c.w; ++x, ++p) {
            float u = ((float)x + 0.5f - cx) / s;
            float cov = clampf(0.5f - px_box_dist(fabsf(u), av, hw, hh, radius) * s, 0, 1);
            if (cov <= 0) continue;
            uint32_t t = sample(im, (u + hw) * kx - 0.5f, sy);
            unsigned a = (unsigned)((float)(t >> 24) * cov + 0.5f);
            a = (a + (a >> 7)) * alpha >> 8;
            if (!a) continue;
            uint32_t ink = tint >= 0 ? (uint32_t)tint : t & 0xffffff;
            *p = a >= 256 ? 0xff000000 | ink : px_mix(*p, ink, a);
        }
    }
}

const char *icon_dir = "/release/assets/default/raw/images/xx";
const image_t *icon(const char *name) {
    static struct {
        char name[24];
        image_t *im;
    } kept[48];
    static int n;
    for (int i = 0; i < n; ++i)
        if (!strcmp(kept[i].name, name)) return kept[i].im;
    if (n == 48 || strlen(name) >= sizeof kept[0].name) return 0;
    char path[320];
    snprintf(path, sizeof path, "%s/oled_%s_w.png", icon_dir, name);
    strcpy(kept[n].name, name);
    kept[n].im = image_load(path);
    return kept[n++].im;
}
