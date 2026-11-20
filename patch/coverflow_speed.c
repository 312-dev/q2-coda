/* Cover Flow's covers at speed (docs/internals.md#coverflow-thumb), as the depth renderer draws
 * them: the arch (arch_pose) and, for a theme with no arch, the row whose facing point leads the
 * covers (draw_row). coverflow.c's coverflow_render draws them, with its poses and columns. */
#include "coverflow.h"

/* A cover at xc, depth zc, seen from eye (1/16 px), at brightness bright, turned deg16 degrees
 * (16.16, up to 90) as a right-hand cover; negative turns it the other way, as a left-hand one.
 * CF_ANGLE itself takes its own table's end, as pose() does at that tilt. */
static void pose_at(int xc, int zc, int deg16, int eye, int bright, pose_t *p) {
    int a = deg16 < 0 ? -deg16 : deg16, other = a != CF_ANGLE << 16;
    turn(other ? a / 90 : CF_ONE, other, p);
    p->xc = xc, p->zc = zc, p->eye = eye, p->bright = bright;
    if (deg16 < 0) p->s = -p->s;
}

/* The shelf's cover t (16.16) albums out, in the arch as far as it has formed (th.arch; after
 * #329's Parameter Lab): at speed every cover within arch_span albums of the middle is back at the
 * side covers' depth, the facing one too, so none swings towards the eye, and turned by smoothstep
 * of its share of the span, so the row bends in to the middle. Its place across and its brightness
 * stay the shelf's; unformed, or arch_span out, it is pose()'s to the byte. */
void arch_pose(int t, pose_t *p) {
    float f = th.on ? th.arch : 0, a = (float)t / CF_ONE, span = cfs.arch_span / 100.f;
    pose(t, p);
    if (f <= 0 || a >= span) return;
    float u = a < 1 ? a : 1, deg = cfs.tilt * (u + (cf_smooth(a / span) - u) * f);
    int side = 16 * cfs.z1 + (a > 1 ? (int)(16 * cfs.zs * (a - 1)) : 0);
    pose_at(p->xc, p->zc + (int)((float)(side - p->zc) * f), (int)(deg * CF_ONE), p->eye, p->bright,
            p);
}

#ifdef PEQ_HOST
/* For test/coverflow.py: cover t's pose in the arch formed arch: its depth, its near and far edges'
 * screen x, its sine and its place across. */
void cf_arch_pose(int t, float arch, int *out) {
    pose_t p;
    cf_thumb_t was = th;
    th.on = 1, th.arch = arch;
    arch_pose(t, &p);
    th = was;
    out[0] = p.zc, out[1] = project(&p, CF_HALF16), out[2] = project(&p, -CF_HALF16);
    out[3] = p.s, out[4] = p.xc;
}
#endif

typedef struct {
    int xl, xr, hl, hr, bright; /* draw_quad's: its edges' x (1/16 px) and heights (1/256 px) */
} cf_quad_t;

/* How far the row at lead has formed (0 to 1), past row_from */
static float row_formed(float lead) {
    float ld = lead < 0 ? -lead : lead;
    return cf_smooth((ld * 100 - cfs.row_from) / (cfs.row_full - cfs.row_from));
}
/* The shelf with its facing point lead albums on from frac, its cover a albums from frac's centre:
 * its t is its distance from that point, on that side, and the whole shelf slides lead * row_shift
 * px. Past row_from the row forms: shrunk, no receding past row_flat from the focus, only spacing,
 * each cover away from the focus thinner and with less keystone. At lead 0 this is
 * coverflow_render's own path, which draws then. Its brightness: a cover is whole for as long as
 * any of it shows, the window it is drawn from reaching past the screen. */
static int row_quad(int frac, float lead, int a, cf_quad_t *q) {
    float at = (float)frac / CF_ONE + lead, shift = lead * cfs.row_shift;
    float b = row_formed(lead);
    float sh = 1 - row_formed(lead) * cfs.row_shrink / 100, flat = cfs.row_flat / 100.f;
    float d = (float)a - at, t = d < 0 ? -d : d;
    float tz = t > flat ? t - (t - flat) * b : t, e = b * cf_smooth(t * 100 / cfs.row_edge);
    pose_t p;
    pose((int)(tz * CF_ONE), &p);
    int near = project(&p, CF_HALF16), inner = project(&p, -CF_HALF16);
    float hn = ART_SIZE * 256.f * p.eye / (p.eye + p.zc - (CF_HALF16 * p.s >> 16));
    float hi = ART_SIZE * 256.f * p.eye / (p.eye + p.zc + (CF_HALF16 * p.s >> 16));
    float out = near + (t - tz) * cfs.row_out * 16;
    float width = (near - inner) * (1 - e * cfs.row_width / 100);
    float hf = (hn + (hi - hn) * (1 - e * cfs.row_key / 100)) * sh, right = d >= 0;
    hn *= sh;
    float xl = (right ? out - width : -out) + shift * 16;
    float xr = (right ? out : width - out) + shift * 16;
    q->xl = (int)(xl * sh), q->xr = (int)(xr * sh);
    q->hl = (int)(right ? hf : hn), q->hr = (int)(right ? hn : hf);
    return q->bright = tz < 2 ? p.bright : cfs.bright2;
}

/* The row at speed at frac, its facing point lead albums on, leaning against it */
typedef struct {
    int frac, lean, focus, after;
    float lead, at;
} cf_row_t;

/* The row's window's middle (CF_WIDE albums), from frac's centre: round the facing cover */
static int row_window(const cf_row_t *n) { return cf_round(n->lead); }

/* The row's covers, nearest the facing point first, into q, with each one's album from frac's
 * centre in album and the window's middle in *centre; their count. */
static int row_quads(const cf_row_t *n, int *centre, cf_quad_t *q, int *album) {
    int k = 0;
    *centre = row_window(n);
    for (int d = 0; d < 2 * CF_WIDE; ++d)
        for (int i = 0; i < (d ? 2 : 1); ++i) {
            int a = (i == 0) == n->after ? n->focus - d : n->focus + d;
            if (a < *centre - CF_WIDE_REACH || a > *centre + CF_WIDE_REACH) continue;
            row_quad(n->frac, n->lead, a, &q[k]);
            album[k++] = a;
        }
    return k;
}

/* The row at frac: at speed, its facing point lead albums on, leaning against it */
static void row_at(cf_row_t *n, int frac) {
    n->frac = frac, n->lead = th.lead, n->lean = th.lead < 0 ? 1 : -1;
    n->at = (float)frac / CF_ONE + th.lead;
    n->focus = cf_round(n->at), n->after = n->at < (float)n->focus;
}

/* The window's middle (coverflow_paint's, fx_window's) of the row at frac as it is drawn now */
int coverflow_window(int frac) {
    cf_row_t n;
    row_at(&n, frac);
    return row_window(&n);
}

/* The row's covers, nearest the facing point first: all of its window. ring[j]: the cover
 * j - CF_REACH from the window's middle (coverflow_paint's). */
void draw_row(shelf_t *f, int frac, const unsigned *const *ring) {
    cf_quad_t q[CF_WIDE];
    cf_row_t n;
    int album[CF_WIDE], centre;
    row_at(&n, frac);
    for (int i = 0, k = row_quads(&n, &centre, q, album); i < k; ++i) {
        const unsigned *tex = ring[album[i] - centre + CF_REACH];
        if (tex && q[i].bright > 0)
            draw_quad(f, q[i].xl, q[i].xr, q[i].hl, q[i].hr, tex, q[i].bright);
    }
}

#ifdef PEQ_HOST
/* For test/coverflow.py: the covers of the frame at frac as drawn now (the row at speed, or at
 * rest), each its album from frac's centre, xl, xr, hl, hr, bright; their count. */
int cf_frame_quads(int frac, int (*out)[6]) {
    cf_quad_t q[CF_WIDE];
    int album[CF_WIDE], n = 0, centre;
    cf_restyle();
    if (th.on && th.lead) {
        cf_row_t e;
        row_at(&e, frac);
        n = row_quads(&e, &centre, q, album);
    } else
        for (int o = -CF_WIDE_REACH; o <= CF_WIDE_REACH; ++o, ++n)
            album[n] = o, row_quad(frac, 0, o, &q[n]);
    for (int i = 0; i < n; ++i)
        out[i][0] = album[i], out[i][1] = q[i].xl, out[i][2] = q[i].xr, out[i][3] = q[i].hl,
        out[i][4] = q[i].hr, out[i][5] = q[i].bright;
    return n;
}
/* For test/coverflow.py: any one cover a (from frac's centre) of the frame at frac as drawn now, in
 * or out of its window: a, xl, xr, hl, hr, bright. */
void cf_frame_quad(int frac, int a, int *out) {
    cf_quad_t q;
    cf_restyle();
    row_quad(frac, th.on ? th.lead : 0, a, &q);
    out[0] = a, out[1] = q.xl, out[2] = q.xr, out[3] = q.hl, out[4] = q.hr, out[5] = q.bright;
}
/* For test/coverflow.py: the window's middle, from the album at the screen's middle, of the frame
 * at frac as drawn now (coverflow_paint's). */
int cf_frame_window(int frac) {
    cf_restyle();
    return !th.on || !th.lead ? 0 : coverflow_window(frac);
}
#endif
