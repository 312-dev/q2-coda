/* Find My Vamp (docs/internals.md#find-my-vamp): the Vamp app asks (coda-rc's Find, the shim's
 * "find" and "find stop", coda_rc_shim.c) and the Vamp plays findmy_tune.h's jingle on its internal
 * buzzer, over and over, with the screen lit and the toast "Find My Vamp" up, until any key, the
 * wheel or a touch on the Q2, a Stop from the phone, or FM_MAX_MS. The buzzer is not the
 * headphones' output, so it sounds with headphones in, and what plays goes on.
 *
 * The notes are timed on a worker thread of their own, never the UI thread. The buzzer is the MCU's
 * too (the Clicker's click, nav/input.c ringnav_buzzer), on the same PWM channel: while the jingle
 * plays the Clicker leaves the buzzer alone (findmy_on), and the worker waits out a click already
 * sounding before it takes the channel. At its end the pin is low and the channel released, so the
 * next click sounds. */
#include "navigation.h"

#define FM_MAX_MS 60000 /* the jingle stops by itself after a minute (the user's, 2026-11-25) */
#define FM_SLICE_MS 10  /* the worker's waits, in slices this long: a stop is heard within one */
#define FM_TICK_MS 250  /* the UI side's look: the screen kept lit, the toast down at the end */
#define FM_LIT_MS 1000  /* how often the screen's off timer is put back */
#define FM_TEXT "Find My Vamp"

#define FM_IOCTL(fd, request, arg) ioctl(fd, request, arg)
#define FM_WAIT(ms) fm_wait(ms)
static int fm_wait(unsigned ms);
#include "findmy_tune.h"

static struct {
    volatile int run;  /* the worker plays on while set; findmy_stop clears it */
    volatile int live; /* from the start until the worker has let the buzzer go */
    int fd, eat;       /* eat: the press that stopped it, its events dropped up to its release */
    unsigned at, lit_at, tick, show, toast;
} fm __attribute__((section(".scratch")));

int findmy_on(void) { return fm.live; }

static int fm_wait(unsigned ms) {
    for (;;) {
        if (!fm.run || (unsigned)time_now_ms() - fm.at >= FM_MAX_MS) return 1;
        if (!ms) return 0;
        unsigned slice = ms < FM_SLICE_MS ? ms : FM_SLICE_MS;
        usleep(slice * 1000);
        ms -= slice;
    }
}

static void *fm_worker(void *unused) {
    (void)unused;
    for (int i = 0; i < 50 && tone_busy; ++i)
        usleep(10000); /* a click sounding (nav/input.c tone_worker) ends first */
    int ch = fm_take(fm.fd);
    while (!fm_phrase(fm.fd, &ch)) {}
    fm_done(fm.fd, ch);
    close(fm.fd);
    fm.run = 0;
    fm.live = 0;
    loop_wake(); /* the UI side's tick sees the end on the next pass */
    return 0;
}

/* The toast, opened from a timer of its own: it is a modal dialog, which must not run inside a
 * command's pass or an input's dispatch. It lasts what is left of the minute; findmy_stop takes it
 * down sooner. */
static int fm_show(const void *info) {
    (void)info;
    fm.show = 0;
    unsigned left = FM_MAX_MS - ((unsigned)time_now_ms() - fm.at);
    if (fm.live && left <= FM_MAX_MS) {
        fm.toast = toast_next();
        toast_for(0, FM_TEXT, (int)left);
    }
    return 0;
}

/* While it plays: the screen kept lit, as Smart Awake keeps it. At the end, the toast down and this
 * timer gone. */
static int fm_tick(const void *info) {
    (void)info;
    unsigned now = (unsigned)time_now_ms();
    if (fm.live) {
        if (g_backlight_status && now - fm.lit_at >= FM_LIT_MS)
            fm.lit_at = now, reset_poweroptions_timer(1, 1, 1);
        return 8; /* RET_REPEAT */
    }
    toast_end(fm.toast);
    fm.tick = 0;
    return 0;
}

/* The jingle started, or its minute started again when it plays already. 0 when the buzzer's driver
 * cannot be opened or the worker cannot start. */
int findmy_start(void) {
    if (fm.live && fm.run) return fm.at = (unsigned)time_now_ms(), 1;
    if (fm.live) return 0; /* stopping: its worker still holds the buzzer */
    int fd = open("/dev/jz_pwm", 2 /* O_RDWR */);
    if (fd < 0) return 0;
    unsigned long t = 0;
    fm.fd = fd, fm.at = (unsigned)time_now_ms(), fm.lit_at = 0, fm.eat = 0;
    fm.run = fm.live = 1;
    if (pthread_create(&t, 0, fm_worker, 0)) {
        close(fd);
        fm.run = fm.live = 0;
        return 0;
    }
    pthread_detach(t);
    ulog('I', "findmy", "the jingle plays");
    if (!g_backlight_status) awake_screen();
    if (!fm.show) fm.show = timer_add(fm_show, 0, 0);
    if (!fm.tick) fm.tick = timer_add(fm_tick, 0, FM_TICK_MS);
    return 1;
}

/* The jingle stopped (the worker ends within FM_SLICE_MS) and its toast taken down. */
void findmy_stop(void) {
    if (!fm.run) return;
    fm.run = 0;
    ulog('I', "findmy", "stopped");
    toast_end(fm.toast);
}

/* ringnav_input, first: any key, a wheel tick or a touch while the jingle plays stops it, and that
 * press (its down, repeats and release) goes nowhere else. 1 drops the event. */
int findmy_gate(void *e) {
    unsigned type = (unsigned)I(e, EVENT_TYPE);
    int key = type == EVT_KEY_DOWN || type == EVT_KEY_LONG || type == EVT_KEY_UP;
    int touch = type >= EVT_POINTER_FIRST && type <= EVT_POINTER_ABORT;
    if (!key && !touch) return 0;
    if (fm.run && (type == EVT_KEY_DOWN || type == EVT_POINTER_FIRST)) {
        findmy_stop();
        fm.eat = 1;
        return 1;
    }
    if (!fm.eat) return 0;
    if (type == EVT_KEY_UP || type == EVT_POINTER_FIRST + 4 /* up */) fm.eat = 0;
    return 1;
}
