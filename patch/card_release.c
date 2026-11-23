/* The card let go of before it loses power: Standby, power-off and restart
 * (docs/internals.md#the-card-across-standby). Stock's Standby runs a plain `umount /mnt/mmc` and
 * then unloads the card's driver whatever that umount did, and on the owner's Q2 (2026-11-23) it
 * failed: coda-rc held the server catalogue open (its library's connection, coda_rc_lib.c), so the
 * driver went away under a live mount, the wake mounted the card again on top of it, and the first
 * mount stayed in /proc/mounts with coda-rc still reading through it. Power-off and restart go
 * through init's `umount -a -r` with every one of those holders still running.
 *
 * So, at stock's umount in device_standby (ringnav_card_umount: its system("umount /mnt/mmc") call)
 * and at its system("poweroff") and system("reboot") (ringnav_card_down):
 * - everything of ours that holds the card asked to let go (card_release): CARD_HOLD written with
 * demo's pid, which q2guard (no log write to the card, the lines kept in RAM) and coda-rc (no
 * catalogue opened) read; the card apps asked to end, Get Album Lyrics, playlist art's worker and
 * the song durations' pass stopped and the server catalogue detached from stock's connection, as
 * for USB storage (addon_card_away); coda-rc's library connection closed (a SIGUSR1); then the
 * processes that still hold a file, a working folder or their program on the card (card_holders)
 * waited for, CARD_FREE_MS at most, and named in the log if any are left;
 * - `sync`, then the umount, verified in /proc/mounts and tried again with the holders named, every
 * mount of the card taken off (a stale one under it too);
 * - as a last resort, a card that will not unmount remounted read-only and synced, then detached
 * (`umount -l`), so its power-off cannot be a write cut short and the next mount is the only one;
 * the log says which. After Standby's wake, card_back (standby_usb.c, once the card is mounted
 * again or given up on) removes the hold, logs how many times the card is mounted and asks q2guard
 * to write out what it held. */
#include "navigation.h"
#include "playlist_art.h"

#define CARD_HOLD                                                                                  \
    "/tmp/mmc_hold" /* demo's pid while the card is let go of (q2guard_core.c, coda_rc_lib.c) */
#define CARD_MOUNTS "/proc/mounts"
#define CARD_DIR "/mnt/mmc"
#define CARD_FREE_MS 3000 /* the most the holders are waited for, once asked */
#define CARD_LOOK_MS 100
#define CARD_TRIES 3  /* umounts of one mount that failed before the last resort */
#define CARD_LAYERS 4 /* mounts of the card taken off at most */
#define CARD_RETRY_MS 500
#define CODA_RC_RELEASE                                                                            \
    "kill -USR1 $(cat /tmp/coda-rc.pid) 2>/dev/null" /* coda_rc.c: its library let go */

void ulog_request_flush(void); /* ulog.c */

static struct {
    int held; /* CARD_HOLD written: card_back removes it */
} cr __attribute__((section(".scratch")));

/* How many times the card is mounted: lines of /proc/mounts on CARD_DIR. */
int card_mounts(void) {
    char m[4096]; /* ponytail: the Q2's mounts fit (705 bytes measured with the ADB gadget up,
                     2026-11-22) */
    void *f = fopen(CARD_MOUNTS, "rb");
    if (!f) return 0;
    unsigned n = fread(m, 1, sizeof m - 1, f);
    fclose(f);
    m[n] = 0;
    int k = 0;
    for (char *p = m; (p = strstr(p, " " CARD_DIR " ")); p += sizeof CARD_DIR) ++k;
    return k;
}

static int on_card(const char *path) {
    return !strncmp(path, CARD_DIR, sizeof CARD_DIR - 1) &&
           (!path[sizeof CARD_DIR - 1] || path[sizeof CARD_DIR - 1] == '/');
}

/* link's target on the card: its path into to (cap bytes), 1. */
static int link_on_card(const char *link, char *to, unsigned cap) {
    int n = readlink(link, to, cap - 1);
    if (n <= 0) return 0;
    to[n] = 0;
    return on_card(to);
}

/* The processes with a file open, their working folder or their program on the card: how many, and
 * up to cap bytes of "pid name path" for each, ", " between (out may be 0). */
int card_holders(char *out, unsigned cap) {
    void *proc = opendir("/proc");
    if (out && cap) out[0] = 0;
    if (!proc) return 0;
    int count = 0;
    unsigned used = 0;
    for (struct dirent *e; (e = readdir(proc));) {
        const char *pid = e->d_name;
        if (pid[0] < '1' || pid[0] > '9') continue;
        char p[64], to[160], what[200];
        int found = 0;
        tk_snprintf(p, sizeof p, "/proc/%s/cwd", pid);
        found = link_on_card(p, to, sizeof to);
        if (!found)
            tk_snprintf(p, sizeof p, "/proc/%s/exe", pid), found = link_on_card(p, to, sizeof to);
        if (!found) {
            tk_snprintf(p, sizeof p, "/proc/%s/fd", pid);
            void *fds = opendir(p);
            for (struct dirent *fd; fds && !found && (fd = readdir(fds));) {
                if (fd->d_name[0] < '0' || fd->d_name[0] > '9') continue;
                tk_snprintf(p, sizeof p, "/proc/%s/fd/%s", pid, fd->d_name);
                found = link_on_card(p, to, sizeof to);
            }
            if (fds) closedir(fds);
        }
        if (!found) continue;
        ++count;
        if (!out || used + 2 >= cap) continue;
        char name[20] = "?";
        tk_snprintf(p, sizeof p, "/proc/%s/comm", pid);
        void *f = fopen(p, "rb");
        unsigned n = f ? fread(name, 1, sizeof name - 1, f) : 0;
        if (f) fclose(f);
        if (n) name[n] = 0;
        for (unsigned i = 0; name[i]; ++i)
            if (name[i] == '\n') name[i] = 0;
        int w = tk_snprintf(what, sizeof what, "%s%s %s %s", count > 1 ? ", " : "", pid, name, to);
        if (w > 0 && used + (unsigned)w < cap)
            memcpy(out + used, what, (unsigned)w + 1), used += (unsigned)w;
    }
    closedir(proc);
    return count;
}

static void card_say(char level, const char *what, const char *why, const char *detail) {
    char line[480];
    tk_snprintf(line, sizeof line, "card: %s for %s%s%s", what, why,
                detail && detail[0] ? ": " : "", detail ? detail : "");
    ulog(level, "ui", line);
}

/* Everything of ours that holds the card asked to let go, and waited for (the top of this file). */
static void card_release(const char *why) {
    char pid[16];
    int n = tk_snprintf(pid, sizeof pid, "%d\n", getpid());
    void *f = fopen(CARD_HOLD, "w");
    if (f) fwrite(pid, 1, (unsigned)n, f), fclose(f), cr.held = 1;
    addon_card_away(); /* the card apps, and the server catalogue on stock's connection */
    album_lyrics_stop();
    playlist_art_stop();
    durations_shutdown(1);
    system(CODA_RC_RELEASE);
    char held[400];
    unsigned start = (unsigned)time_now_ms();
    while (card_holders(0, 0) && (unsigned)time_now_ms() - start < CARD_FREE_MS)
        usleep(CARD_LOOK_MS * 1000);
    if (card_holders(held, sizeof held)) card_say('W', "still held after 3 s", why, held);
}

/* The card off before it loses power (the top of this file): 1 when every mount came off by umount,
 * 0 when one had to be detached. cmd is the umount to run (stock's own string at device_standby's
 * call). */
static int card_unmount(const char *cmd, const char *why) {
    int mounts = card_mounts();
    if (!mounts) return 1;
    card_release(why);
    system("sync");
    char held[400], line[480];
    int fails = 0, layers = 0;
    while (card_mounts() && layers < CARD_LAYERS && fails < CARD_TRIES) {
        int before = card_mounts();
        system(cmd);
        if (card_mounts() < before) {
            ++layers, fails = 0;
            continue;
        }
        ++fails;
        card_holders(held, sizeof held);
        tk_snprintf(line, sizeof line, "umount failed (try %d), held by %s", fails,
                    held[0] ? held : "nothing seen");
        card_say('W', line, why, 0);
        if (fails < CARD_TRIES) usleep(CARD_RETRY_MS * 1000);
    }
    if (!card_mounts()) {
        tk_snprintf(line, sizeof line, "unmounted (%d mount%s)", mounts, mounts == 1 ? "" : "s");
        card_say('I', line, why, 0);
        return 1;
    }
    card_holders(held, sizeof held);
    int ro = !system("mount -o remount,ro " CARD_DIR);
    system("sync");
    for (int k = 0; card_mounts() && k < CARD_LAYERS; ++k) system("umount -l " CARD_DIR);
    card_say('E',
             ro ? "would not unmount: remounted read-only, synced and detached"
                : "would not unmount or go read-only: synced and detached read-write",
             why, held);
    return 0;
}

/* After Standby's wake (standby_usb.c), once the card is mounted again or given up on: the hold
 * gone, the mounts counted, q2guard asked to write out the lines it kept. */
void card_back(void) {
    if (!cr.held) return;
    cr.held = 0;
    unlink(CARD_HOLD);
    int n = card_mounts();
    char line[64];
    tk_snprintf(line, sizeof line,
                n == 1 ? "card: back, mounted once" : "card: back, mounted %d times", n);
    ulog(n == 1 ? 'I' : n ? 'E' : 'W', "ui", n ? line : "card: not back after the wake");
    ulog_request_flush();
}

/* device_standby's system("umount /mnt/mmc") (tools/build.py CARD_UMOUNT_CALL), once the player,
 * Wi-Fi and Bluetooth are stopped and before it unloads the card's driver. */
int ringnav_card_umount(const char *cmd) {
    card_unmount(cmd, "standby");
    return 0;
}

/* device_poweroff's system("poweroff") and device_reboot's system("reboot") (tools/build.py
 * CARD_DOWN_CALLS), after the queue and the unified log are saved: the card off first, then cmd. */
int ringnav_card_down(const char *cmd) {
    card_unmount("umount " CARD_DIR, cmd);
    return system(cmd);
}
