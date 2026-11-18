/* Stock's two cover threads wait for work instead of polling for it
 * (docs/internals.md#cover-threads). parse_albumcovertask_thd (the Library's row and grid art)
 * slept 50 ms a round and player_parsecover_thd (Now Playing's art) 100 ms, whether or not a cover
 * was asked for: 30 wakeups a second on every lit screen. Their sleeps (tools/build.py COVER_WAITS)
 * come here: a thread with work keeps stock's pace between its tasks; one with none parks on a
 * futex until a request wakes it, or COVER_WAIT_S passes.
 *
 * What wakes each, for every way stock V1.36 gives it work (read from its GOT: no other slot or
 * page reference reaches these globals). player_parsecover_thd's work is g_play_cover_info's start
 * flag, which only player_set_coverinfo sets: player_get_id3info on a track change,
 * get_onlineid3info through tidal.c app_coverinfo, and two stock page handlers (0x52c7c8,
 * 0x5360f8); player_get_coverinfo clears it. That entry is hooked (HOOKS), and cover_set_info wakes
 * the thread.
 *
 * parse_albumcovertask_thd's work is plist_albumtasklist not empty while albumcover_taskstate is
 * not 2 (paused), or a state of 3 to 6 (a cache to clear). About 35 stores to the state in 25 stock
 * functions (a producer pushes, then stores 1; a page's scroll stores 2 and its end the saved
 * state) and the pushes have no one function to hook, so the UI loop's pass (ringnav_sleep) looks,
 * while the thread is parked, and wakes it: a request made on the UI thread is seen the same pass,
 * one from another thread (loadclasspthread, a Tidal list) by the next.
 * ponytail: a request from another thread while the UI loop is stalled waits for the loop or the
 * timeout; COVER_WAIT_S is that bound. */
#include "navigation.h"

#define COVER_WAIT_S 5 /* the longest park, should a wake ever be missed */
#define FUTEX_WAIT_PRIVATE 128
#define FUTEX_WAKE_PRIVATE 129
enum { COVER_ALBUM, COVER_PLAY };

/* futex(word, op, val, timeout), the o32 syscall: demo imports no futex wrapper. */
__asm__(".globl cover_futex\ncover_futex:\nli $v0, 4238\nsyscall\njr $ra\nnop\n");
extern int cover_futex(volatile int *word, int op, int val, const void *timeout);
extern int stock_set_coverinfo_trampoline(const char *cover, const char *name, int type);

static volatile int cover_gen[2];    /* moved by every wake: what a parked thread waits on */
static volatile int cover_parked[2]; /* the thread is in, or about to enter, its futex wait */
static const long cover_wait_ts[2] = { COVER_WAIT_S, 0 };

static int album_work(void) {
    int state = *(volatile int *)albumcover_taskstate;
    if (state >= 3 && state <= 6) return 1;
    void *tasks = *(void *volatile *)plist_albumtasklist;
    if (state == 2 || !tasks) return 0;
    pthread_mutex_lock((void *)actasklist_mutex);
    int queued = list_size(tasks) > 0;
    pthread_mutex_unlock((void *)actasklist_mutex);
    return queued;
}

static int play_work(void) { return *(volatile unsigned char *)g_play_cover_info != 0; }

/* The waker moves the generation before it looks at parked, and the thread marks itself parked
 * before its last look at its work, so a request lands either in that look or in a wake: never
 * between. */
static int cover_wait(int t, unsigned us, int (*work)(void)) {
    int gen = __atomic_load_n(&cover_gen[t], __ATOMIC_SEQ_CST);
    if (work()) return usleep(us); /* still busy: stock's pace between tasks */
    __atomic_store_n(&cover_parked[t], 1, __ATOMIC_SEQ_CST);
    if (!work()) cover_futex(&cover_gen[t], FUTEX_WAIT_PRIVATE, gen, cover_wait_ts);
    __atomic_store_n(&cover_parked[t], 0, __ATOMIC_SEQ_CST);
    return 0;
}

static void cover_wake(int t) {
    __atomic_add_fetch(&cover_gen[t], 1, __ATOMIC_SEQ_CST);
    if (__atomic_exchange_n(&cover_parked[t], 0, __ATOMIC_SEQ_CST))
        cover_futex(&cover_gen[t], FUTEX_WAKE_PRIVATE, 1, 0);
}

/* parse_albumcovertask_thd's usleep(50000) and player_parsecover_thd's usleep(100000). */
int ringnav_album_cover_wait(unsigned us) { return cover_wait(COVER_ALBUM, us, album_work); }
int ringnav_play_cover_wait(unsigned us) { return cover_wait(COVER_PLAY, us, play_work); }

/* player_set_coverinfo's entry: stock's, then the player's cover thread woken for it. */
int cover_set_info(const char *cover, const char *name, int type) {
    int r = stock_set_coverinfo_trampoline(cover, name, type);
    cover_wake(COVER_PLAY);
    return r;
}

/* Each UI loop pass (ringnav_sleep): the album thread woken when it is parked and has work. */
void cover_wake_poll(void) {
    if (__atomic_load_n(&cover_parked[COVER_ALBUM], __ATOMIC_SEQ_CST) && album_work())
        cover_wake(COVER_ALBUM);
}
