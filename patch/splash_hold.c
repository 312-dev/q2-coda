/* The boot animation plays to its last frame however soon demo is ready
 * (docs/boot.md#boot-animation). q2splash (patch/splash.c) holds a write lock on SPLASH_MARK while
 * it plays. If it does when demo makes its screen's lcd (lcd_linux_fb_create), the lcd's swap and
 * flush, both stock's copy of the frame to the hidden framebuffer page and its pan to that page,
 * return without either: demo loads and draws as it always does, but nothing reaches the screen.
 * Every UI loop pass (ringnav_sleep) tests the lock, and once it is free (q2splash drew its last
 * frame and exited, or was killed) gives stock's back and has the whole screen drawn again, so the
 * first frame shown is the current one, whole. SPLASH_HOLD_MS after the lcd was made demo stops
 * waiting: it removes SPLASH_MARK, which stops q2splash before its next frame, and shows its own
 * once the lock has gone, or SPLASH_STOP_MS later if q2splash still has it. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

#define SPLASH_MARK "/tmp/q2splash.run" /* splash.c's MARK */
#define SPLASH_HOLD_MS 10000            /* the animation is 7.4 s, and starts before demo */
#define SPLASH_STOP_MS 500              /* q2splash's next frame is due within 34 ms */
#define O_CLOEXEC 0x80000
#define F_SETLK 6 /* MIPS's, without large files */
#define EAGAIN 11
#define EACCES 13

void *stock_lcd_create_trampoline(const char *path);

static struct {
    void *lcd;                             /* the screen's, while its swap and flush are held */
    int (*swap)(void *), (*flush)(void *); /* stock's */
    int fd, told;                          /* SPLASH_MARK, open; told: removed, so q2splash stops */
    unsigned long long since;              /* now_ms when the lcd was made */
} sh __attribute__((section(".scratch")));

/* Whether q2splash still holds its lock: a read lock over the whole file (struct flock all 0, in
 * either layout) is refused only then. Taken, it goes with the fd's close. Any other failure is
 * not q2splash, so nothing is held up by it. */
static int held(int fd) {
    short lock[32] = { 0 };
    if (fcntl(fd, F_SETLK, lock) == 0) return 0;
    int e = *__errno_location();
    return e == EAGAIN || e == EACCES;
}

static int hold(void *lcd) {
    (void)lcd;
    return 0; /* RET_OK: end_frame goes on as after a frame shown */
}

/* lcd_linux_fb_create's hook: stock's lcd, its swap and flush held while q2splash plays. */
void *splash_lcd(const char *path) {
    void *lcd = stock_lcd_create_trampoline(path);
    if (!lcd || !P(lcd, LCD_SWAP) || !P(lcd, LCD_FLUSH) || sh.lcd) return lcd;
    int fd = open(SPLASH_MARK, O_CLOEXEC); /* O_RDONLY */
    if (fd < 0) return lcd;
    if (!held(fd)) {
        close(fd);
        return lcd;
    }
    sh.swap = (int (*)(void *))P(lcd, LCD_SWAP), sh.flush = (int (*)(void *))P(lcd, LCD_FLUSH);
    P(lcd, LCD_SWAP) = (void *)hold, P(lcd, LCD_FLUSH) = (void *)hold;
    sh.lcd = lcd, sh.fd = fd, sh.told = 0, sh.since = now_ms();
    return lcd;
}

/* ringnav_sleep, every UI loop pass: a flag test once shown. */
void splash_poll(void) {
    if (!sh.lcd) return;
    unsigned long long waited = now_ms() - sh.since;
    if (held(sh.fd)) {
        if (waited < SPLASH_HOLD_MS) return;
        if (!sh.told) unlink(SPLASH_MARK), sh.told = 1;
        if (waited < SPLASH_HOLD_MS + SPLASH_STOP_MS) return;
    }
    P(sh.lcd, LCD_SWAP) = (void *)sh.swap, P(sh.lcd, LCD_FLUSH) = (void *)sh.flush;
    close(sh.fd);
    sh.lcd = 0;
    widget_invalidate_force(window_manager(), 0); /* the next frame is the whole screen */
}
