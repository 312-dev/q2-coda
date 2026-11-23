/* The unified log, a part of q2guard (docs/internals.md#unified-log): one rotating log on the card,
 * /mnt/mmc/.q2muse/logs/q2-log.0 to .3, every line as ulog_line.h writes it. q2guard is its only
 * writer: it reads demo's stderr file (demo's ulog lines, and the player's and stock's helpers'
 * own), the kernel's warnings and worse (/dev/kmsg) and its own notes, holds the lines in
 * card_store.c's RAM ring and writes them in batches. The parsers, the guesses, the rotation and
 * the snapshot, with no reads of the device but the files they are given: q2log_core.c, tested on
 * this machine by test/unit/test_q2log.py. The device's side is q2guard.c. */
#ifndef Q2LOG_H
#define Q2LOG_H

#include "q2guard.h"
#include "ulog_line.h"

#define ULOG_RAM 65536       /* the ring's bytes while the card is away or between flushes */
#define ULOG_FILE_MAX 262144 /* q2-log.0 is moved on when a flush finds it this big */
#define ULOG_FILES 4         /* q2-log.0 to .3: about 1 MB in all */
#define ULOG_READ_MAX 16384  /* the most read of a feed in one tick; the rest waits for the next */
#define ULOG_PART 512        /* a line read without its end yet is held up to this, then cut */

/* The Bluetooth event log (docs/internals.md#bluetooth-event-log): q2-btev.0 to .3 beside it, every
 * Bluetooth line of the unified log copied as it was, so a day's connects, drops and refused
 * starts outlast the unified log's churn and a reboot. */
#define BTEV_RAM 32768       /* its ring's bytes while the card is away or between flushes */
#define BTEV_FILE_MAX 131072 /* q2-btev.0 is moved on when a flush finds it this big */
#define BTEV_FILES 4         /* q2-btev.0 to .3: about 512 KB in all */
#define BTEV_FLUSH_MS 60000  /* lines held in its ring are written at least this often */
#define BTEV_HOLD_MS 10000   /* its newest lines this young wait for the next flush, unless final: two ticks */
/* From here on each line into from (the unified ring) that ulog_bt_wanted keeps is also put in to */
void ulog_bt_to(card_log *from, card_log *to);
/* Lines go into that ring by their uptime, not as read (a late one carried back past later ones).
 * While q2guard's writer holds the ring's first bytes (dropped: the ring's drop count then), none
 * goes in before them; 0 bytes lifts it. */
void ulog_bt_freeze(unsigned bytes, unsigned dropped);
/* The bytes at l's start whose lines have an uptime of before_ms or less: what a flush may write
 * while a line read late could still belong after them. */
unsigned ulog_settled(const card_log *l, unsigned before_ms);
/* 1 for a Bluetooth line: source bt; a boot, snapshot or shutdown line; the player's errors; or
 * one that names bluealsa, BlueZ, Bluetooth, A2DP, AVDTP, rtk_hci, hci0 or L2CAP, but the player's
 * parse_bluealsa_status_msg and the kernel's debugfs notes */
int ulog_bt_wanted(char level, const char *src, const char *s, int n);

/* A level and source for a line that came without them (the player's, stock's helpers'), from
 * its words: E for an error or failure, W for a warning, else I; the source by who it names, else
 * ui. */
char ulog_guess(const char *s, int n, const char **src);
/* One line into the ring: s (n bytes) kept as it is when it is already a ulog line, else stamped
 * with wall and up_ms and guessed. Its level; 0 for a blank line, which is dropped, or a repeat of
 * the line before, which is counted (ulog_repeats). */
char ulog_add_raw(card_log *l, long wall, unsigned up_ms, const char *s, int n);
/* One line of q2guard's own (src "guard", or any other) into the ring, stamped. */
void ulog_add(card_log *l, long wall, unsigned up_ms, char level, const char *src, const char *msg);
/* A run of one line repeated is that line once and then a count (every add above): the count
 * written now, for a flush or a snapshot to carry it. */
void ulog_repeats(card_log *l);

/* A file another process appends to (demo's stderr), read from where the last call stopped. The
 * shell's roll (tools/build.py BOOT_HOOK) copies it to path.1 and empties it: a file found shorter
 * than the offset was rolled, so the rest of path.1 from the offset is read first. */
struct ulog_tail {
    const char *path;
    long off; /* bytes of path already read */
    int plen; /* a line's start held for its end */
    char part[ULOG_PART];
    const char *src; /* every raw line's source (a card app's id), its level guessed; 0: guessed */
};
/* t aimed at path, from its start or (q2guard started again) its present end */
void ulog_tail_at(struct ulog_tail *t, const char *path, int from_end);
/* What path gained, as lines into l (ULOG_READ_MAX at most): 1 when a line went in, | 2 when one
 * was E or W. */
int ulog_tail(struct ulog_tail *t, card_log *l, long wall, unsigned up_ms);

/* The card apps' own logs: an app's launcher leaves its loop's pid in dir/q2-<id> while it runs
 * (addon.c), and an app logs to dir/q2-<id>.log (q2-subsonic's SetupLog: Go's log, 256 KB, then a
 * rename to .1). Each call finds the running apps' logs (a q2-<id>.log beside its q2-<id>), at
 * most ULOG_APPS of them, and reads what each gained into l, its lines' source the id, the date
 * Go's log puts first left out (the stamp has the time); an app whose pid file has gone is read
 * once more, then let go. from_end (q2guard started again) starts a log first seen at its end. */
#define ULOG_APPS 4
#define ULOG_APP_ID 24
#define ULOG_APP_PATH 160 /* dir/q2-<id>.log */
struct ulog_apps {
    struct ulog_tail t[ULOG_APPS];
    char id[ULOG_APPS][ULOG_APP_ID];
    char path[ULOG_APPS][ULOG_APP_PATH];
    int from_end;
};
/* 1 when a line went in, | 2 when one was E or W, as ulog_tail */
int ulog_apps(struct ulog_apps *a, card_log *l, const char *dir, long wall, unsigned up_ms);

/* One /dev/kmsg record ("PRI,SEQ,USEC,FLAGS;message\n" and its continuation lines): a kernel line
 * of warning or worse into l, stamped with the record's own uptime. Its level, or 0 for one not
 * kept (info, debug, a program's line written to /dev/kmsg, or a repeat, counted). */
char ulog_kmsg(card_log *l, long wall, const char *rec, int n);

/* dir/name.0 rotated once it has reached max (.0 to .1 and so on, the oldest of files dropped),
 * then the ring appended to it in order. 0 when nothing is left held, -1 when the card refused. */
int ulog_flush(card_log *l, const char *dir, const char *name, long max, int files);

/* A snapshot, why it was taken (power-off, restart, update, crash, heat, ...), at level: a marker
 * line, then what the Q2 was doing, each a line of its own (meminfo's totals, loadavg, the heat
 * line's state without its time; any of them 0 to leave it out). */
void ulog_snapshot(card_log *l, long wall, unsigned up_ms, char level, const char *why,
                   const char *meminfo, const char *loadavg, const char *state);

#endif
