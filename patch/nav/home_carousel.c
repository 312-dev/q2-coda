/* Cover Flow steps its slide_menu with one retargeted animator (home_step): stock scroll_to
 * (0x606290) starts a new animator per call and orphans the running one, so fast ticks leave
 * several fighting over the offset and committing the index twice.
 * ponytail: home_page is a carousel page here too. Home's slide_menu is hidden and takes no taps
 * (tools/ipod.py ipod_home), so nothing reaches it; the MIPS suite's slide tests
 * (test/patch_suite/01) run on Home's page, and moving them to Cover Flow's, whose own paths (the
 * thumb, the pooled glide) then apply, would let this name Cover Flow's page alone. */
static int carousel_page(void *top) {
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    return !tk_strcmp(name, "home_page") || !tk_strcmp(name, "coverflow_page");
}

static int is_home(void *top, void *w) { return w && kind(w) == 3 && carousel_page(top); }

/* iPod Home's lists (the main menu and its Settings list): Return goes back to the first row. */
static int home_menu(void *top) {
    return top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "home_page");
}

/* A list row: a list_item or table_row. */
static int is_row(void *w) {
    const char *t = w ? widget_get_type(w) : "";
    return !tk_strcmp(t, "list_item") || !tk_strcmp(t, "table_row");
}

/* A widget's own window: the top-level widget it sits in. */
void *window_of(void *w) {
    void *wm = window_manager();
    while (w && P(w, W_PARENT) != wm) w = P(w, W_PARENT);
    return w;
}

/* A page that opens and closes with stock's slide (tools/ipod.py SLIDE). Stock paints it and
 * the page under it into the animator's snapshots while it is the top window and before
 * window_manager_is_animating is set; nothing else paints the page under it. */
static int slides(void *win) {
    /* Now Playing with a hint holds it as close_anim_hint, which this doesn't read. */
    if (win && win == st.np_win && motion_row(MOTION_PLAYING)->hint) return 1;
    const char *hint = win ? widget_get_prop_str(win, "anim_hint", (void *)0) : (void *)0;
    return hint && *hint;
}

/* win's anim_hint becomes the theme's for kind (Reduce Motion's fade under that flag: ui_kit.c
 * motion_row), at load or open only. A theme with no hint for kind reads and writes nothing. Now
 * Playing takes it as close_anim_hint, its open stays immediate (opened from a cover, art_zoom.c's
 * zoom takes both). No kind reaches a dialog: a clear
 * one's snapshot would show black under its animation. */
static void motion_hint(void *win, int kind) {
    const char *hint = motion_row(kind)->hint;
    if (!hint) return;
    const char *prop = kind == MOTION_PLAYING ? "close_anim_hint" : "anim_hint";
    if (tk_strcmp(widget_get_prop_str(win, prop, ""), hint)) widget_set_prop_str(win, prop, hint);
}
/* A window as it loads. Themes with no window hints read nothing. */
static void motion_load(void *root, int np) {
    if (np) art_zoom_load(root); /* from a cover: it travels into the art (art_zoom.c) */
    if (!motion_row(MOTION_PAGE)->hint && !motion_row(MOTION_PLAYING)->hint) return;
    if (np && !window_hint(root, "open_anim_hint")[0])
        motion_hint(root, MOTION_PLAYING);
    else if (!np && slides(root))
        motion_hint(root, MOTION_PAGE);
}
/* page_open's page: Cover Flow's takes the theme's hint, if it has one. */
void motion_open(void *page, const char *name) {
    if (!tk_strcmp(name, "coverflow_page")) motion_hint(page, MOTION_COVERFLOW);
}

/* The animator's destination is the intended icon, even before stock commits its index.
 * Keep this widget-owned: touch and page recreation cannot leave a dangling animator here. */
static int slide_index(void *w) {
    void *a = P(w, SLIDE_ANIMATOR);
    int n = slide_count(w);
    if (!n) return -1;
    int id = I(w, SLIDE_INDEX);
    int stride = slide_menu_item_width(w) + I(w, SLIDE_SPACER);
    if (a && stride > 0) id -= I(a, ANIM_X_TO) / stride;
    id %= n;
    return id < 0 ? id + n : id;
}

static int home_done(void *w, void *event) {
    if (coverflow_slide(w)) { /* stock's end wraps the index by the children: Cover Flow's pool */
        int n = slide_count(w), stride = slide_menu_item_width(w) + I(w, SLIDE_SPACER);
        int off = I(w, SLIDE_OFFSET),
            q = stride > 0 ? (off < 0 ? off - stride / 2 : off + stride / 2) / stride : 0;
        int id = n ? (I(w, SLIDE_INDEX) - q) % n : 0;
        I(w, SLIDE_OFFSET) = 0;
        P(w, SLIDE_ANIMATOR) = (void *)0;
        slide_menu_set_value(w, id < 0 ? id + n : id); /* changed, laid out, invalidated */
    } else
        slide_menu_on_scroll_done(w, event);
    /* Stock skips focus restoration when a reversal returns to the original index. */
    widget_set_focused(slide_child(w, I(w, SLIDE_INDEX)), 1);
    return 7; /* RET_REMOVE: same one-shot lifetime as stock completion */
}

extern float coverflow_follow(float k);
extern int coverflow_follow_aim(int d, int tau, int elapsed);
/* w's one animator, the wheel's and Coverflow's touch's, from the live offset to goal in ms with
 * easing (AWTK's easing_type_t), made on first use; 0 if it cannot be, the move committed at once.
 */
int carousel_glide(void *w, int goal, unsigned ms, int easing) {
    void *a = P(w, SLIDE_ANIMATOR);
    if (a && !ms) { /* ms 0 is the immediate move, also over a glide still running */
        widget_animator_pause(a);
        widget_animator_destroy(a);
        a = (void *)0;
    }
    if (!a) {
        a = ms ? widget_animator_scroll_create(w, ms, 0, easing == EASE_FOLLOW ? 0 : easing)
               : (void *)0;
        if (a && !widget_animator_on(a, EVT_ANIM_END, home_done, w)) {
            widget_animator_destroy(a);
            a = (void *)0;
        }
        P(w, SLIDE_ANIMATOR) = a;
        if (!a) {
            int n = slide_count(w), stride = slide_menu_item_width(w) + I(w, SLIDE_SPACER);
            int id = n && stride > 0 ? (I(w, SLIDE_INDEX) - goal / stride) % n : 0;
            I(w, SLIDE_OFFSET) = 0;
            slide_menu_set_value(w, id < 0 ? id + n : id);
            widget_set_focused(slide_child(w, I(w, SLIDE_INDEX)), 1);
            return 0;
        }
        widget_set_focused(slide_child(w, I(w, SLIDE_INDEX)), 0);
    }
    widget_animator_pause(a);
    widget_animator_scroll_set_params(a, I(w, SLIDE_OFFSET), 0, goal, 0);
    if (easing == EASE_FOLLOW) /* ms is its time constant; the distance gives the run's length */
        ms = (unsigned)coverflow_follow_aim(
            goal - I(w, SLIDE_OFFSET), (int)ms,
            P(a, ANIM_EASING) == (void *)coverflow_follow ? I(a, ANIM_ELAPSED) : -1);
    I(a, ANIM_ELAPSED) = I(a, ANIM_START_TIME) = 0;
    I(a, ANIM_DURATION) = ms;
    /* scroll_create's own, so a reused one takes this run's */
    P(a, ANIM_EASING) = easing == EASE_FOLLOW ? (void *)coverflow_follow : easing_get(easing);
    widget_animator_start(a);
    return 1;
}

/* A tick's step of n albums (wheel_accel's rows: Cover Flow never jumps by letter), the covers
 * passing every album between; a bounded row (coverflow_bounded) stops on its end, and a step
 * from the end itself kicks out and springs back. */
static void home_step(void *w, int dir, unsigned now, int steps) {
    int n = slide_count(w);
    int stride = slide_menu_item_width(w) + I(w, SLIDE_SPACER);
    int fast =
        st.home_surface == w && st.home_dir == dir && now - st.last_home <= HOME_FAST_WINDOW_MS;
    void *a = P(w, SLIDE_ANIMATOR);
    int live = I(w, SLIDE_OFFSET);
    int goal = a ? I(a, ANIM_X_TO) : 0;
    /* Reversal discards the unfinished destination and heads to the adjacent card
     * behind the live position. Same-direction ticks extend the intended destination. */
    if (a && (goal - live) * dir > 0) {
        goal = (live / stride) * stride;
        if (dir > 0 ? goal >= live : goal <= live) goal -= dir * stride;
    } else
        goal -= dir * stride;
    goal -= dir * stride * (steps - 1);
    int to = I(w, SLIDE_INDEX) - goal / stride;
    if (coverflow_bounded(w) && (to < 0 || to >= n)) {
        int over = to < 0 ? -to : to - (n - 1);
        if (over < steps)
            goal += dir * stride * over; /* on the end, not past it */
        else {
            goal += dir * stride * steps;
            if (a && (goal - live) * dir < 0) return; /* still arriving at the end */
            I(w, SLIDE_OFFSET) = live = goal - (motion_reduced() ? 0 : dir * stride / CF_BOUNCE);
        }
    }
    const motion_t *glide =
        motion_row((coverflow_slide(w) ? MOTION_COVERFLOW_STEP : MOTION_STEP) + fast);
    if (!carousel_glide(w, goal, glide->ms, glide->easing)) {
        st.home_surface = (void *)0;
        return;
    }
    st.home_surface = w;
    st.last_home = now;
    st.home_dir = dir;
}

