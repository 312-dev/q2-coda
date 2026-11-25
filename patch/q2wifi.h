/* q2wifi: the Wi-Fi fault recorder, a part of q2guard (docs/internals.md#wi-fi-fault-recorder).
 * It records and never recovers: nothing here restarts wpa_supplicant, reloads the driver, toggles
 * the interface or asks for a scan; what it sends wpa_supplicant is ATTACH (its events, as
 * wpa_cli's monitor) and the read-only STATUS, SCAN_RESULTS, LIST_NETWORKS and SIGNAL_POLL. Into
 * its own log, q2-wifi.0 to .3 beside the heat log, it writes:
 * - once a boot, 30 s after q2guard starts (S90play starts it just before demo): whether wlan0 is
 *   there; if not, the kernel's lines about the driver, the SDIO bus and allocation failures, the
 *   memory and vmalloc state, the driver's lsmod line, the temperature and uptime;
 * - a deaf radio: 60 s of scans with no network while not joined, or 120 s not joined
 * (DISCONNECTED, SCANNING or a try that failed) while the scans hold a saved network: a snapshot of
 * wpa_supplicant's state, the scan's counts by band, the driver's state files, the SDIO interrupt
 * count and the memory; again every 5 min while it lasts, and once more at its end with how long it
 * lasted;
 * - each association and disassociation, with wpa_supplicant's reason code and the driver's own
 *   last disconnect reason.
 * No network name is ever written; access points' and devices' addresses are written as a hash
 * salted with the boot's id (wifi_hash), the same address the same hash within a boot. The
 * decisions, parsers and lines, with no reads of the device: q2wifi_core.c, tested on this machine
 * by test/unit/test_q2wifi.py; the device's side is q2wifi.c, tested there with a fake /proc, /sys
 * and wpa_supplicant (test/unit/test_q2wifi_rec.py); tools/q2wifi.py reads the log back. */
#ifndef Q2WIFI_H
#define Q2WIFI_H

#include "q2guard.h"

#define WIFI_RAM 65536 /* the log's RAM ring while the card is away: a boot snapshot fits twice */
#define WIFI_FILE_MAX 524288 /* q2-wifi.0 moved on at this size */
#define WIFI_FILES 4         /* q2-wifi.0 to .3: 1.5 MB after a move, never past 2 MB and a flush */
#define WIFI_BOOT_MS 30000   /* from q2guard's start: is wlan0 there */
#define WIFI_EMPTY_MS 60000  /* not joined and no network in the scans this long: deaf */
#define WIFI_NOJOIN_MS 120000 /* not joined this long with a saved network in the scans: deaf */
#define WIFI_REPEAT_MS 300000 /* a snapshot this often while deaf */
#define WIFI_JOINED_MS 60000  /* joined: STATUS this often, in case an event was missed */
#define WIFI_SCAN_MS 30000    /* not joined, no scan event since: SCAN_RESULTS this often anyway */
#define WIFI_RETRY_MS 30000   /* a control socket that would not attach: tried again after this */
#define WIFI_PROC_MAX 2048    /* each driver file's bytes in a snapshot */
#define WIFI_KMSG_MAX 16384   /* the boot snapshot's kernel lines */
#define WIFI_KMSG_PLAIN 6144  /* of them, lines naming only the driver or the bus */
#define WIFI_KMSG_AFTER 80    /* records kept after a failure's line: its stack and memory dump */

/* ---- privacy ---- */
/* An address as "#" and six hex digits: FNV-1a of the salt and its hex digits, case folded */
void wifi_hash(unsigned salt, const char *mac, char *out); /* out: 8 bytes */
unsigned wifi_salt(const char *boot_id); /* /proc/sys/kernel/random/boot_id's text */
/* text into out with every address hashed and every line naming an SSID dropped; out's length */
int wifi_scrub(const char *text, unsigned salt, char *out, int size);

/* ---- wpa_supplicant ---- */
enum { WS_NONE, WS_DISABLED, WS_INACTIVE, WS_SCANNING, WS_DISCONNECTED, WS_JOINING, WS_COMPLETED };
/* STATUS's wpa_state= as WS_*, its text into state (WS_NONE and "" without one) */
int wifi_state(const char *status, char *state, int n);
/* STATUS's fields that say nothing private, "state=DISCONNECTED freq=5240 ap=#1a2b3c ..." */
int wifi_status_line(const char *status, unsigned salt, char *out, int size);
struct wifi_scan {
    int n;       /* networks in SCAN_RESULTS, -1 not read */
    int band[3]; /* of them on 2.4, 5 and 6 GHz */
    int best[3]; /* the strongest signal on each, dBm; 0 none */
    int known;   /* of them with a saved, enabled network's SSID (compared here, never written) */
};
/* SCAN_RESULTS's text, and LIST_NETWORKS's (0 not read: known is 0) */
void wifi_scan_count(const char *results, const char *networks, struct wifi_scan *s);
int wifi_scan_text(const struct wifi_scan *s, char *out,
                   int size); /* "nets=14 2.4=9/-61 5=5/-70 6=0 known=1" */

enum {
    WE_NONE,
    WE_CONNECTED,
    WE_DISCONNECTED,
    WE_TERMINATING,
    WE_SCAN_STARTED,
    WE_SCAN_RESULTS,
    WE_SCAN_FAILED,
    WE_LOG
};
/* One event from the monitor ("<3>CTRL-EVENT-..."): WE_*, and for those that are logged the line's
 * text into out, private fields dropped and addresses hashed; *reason the disconnect's reason code
 * (-1 none), *local 1 when wpa_supplicant or the driver began it */
int wifi_event(const char *msg, unsigned salt, char *out, int size, int *reason, int *local);

/* ---- the deaf radio ---- */
enum { WIFI_DEAF_NONE, WIFI_DEAF_EMPTY, WIFI_DEAF_NOJOIN };
enum { WA_NONE, WA_START, WA_AGAIN, WA_EMPTY, WA_END };
enum { WIFI_END_SCAN = 1, WIFI_END_ASSOC, WIFI_END_OFF };
struct wifi_watch {
    unsigned empty_since, nojoin_since; /* when each condition began; 0 not now */
    int deaf; /* WIFI_DEAF_*: what started it, WIFI_DEAF_EMPTY once its scans emptied */
    unsigned deaf_at, snap_at; /* when it started, and its last snapshot */
    int snaps;                 /* snapshots taken in it */
    int end;                   /* WA_END's cause: WIFI_END_* */
    unsigned lasted;           /* WA_END's: ms from its start */
};
struct wifi_obs {
    unsigned now;
    int state; /* WS_*; WS_NONE: no wpa_supplicant (Wi-Fi off, Smart Wi-Fi's radio off) */
    int nets;  /* in its scan results, -1 not read this time (the last count holds) */
    int known; /* of them saved */
};
/* One observation: what to record (WA_*). A start, WIFI_DEAF_EMPTY on and a repeat take a snapshot;
 * WA_END stamps the end (w->end, w->lasted) and the watch starts again. */
int wifi_step(struct wifi_watch *w, const struct wifi_obs *o);

/* ---- the boot's evidence ---- */
/* A kernel record's text worth keeping: 2 a failure's (its next WIFI_KMSG_AFTER records are kept
 * too), 1 one that names the driver, the SDIO bus or mmc1, 0 neither */
int wifi_kmsg_keep(const char *msg);
struct wifi_vm {
    int areas;
    unsigned long used, vmalloc, ioremap; /* bytes */
    unsigned long gap, lo,
        hi; /* the largest hole between two areas; the lowest and highest address */
};
void wifi_vmalloc_sum(
    const char *text,
    struct wifi_vm *v); /* /proc/vmallocinfo, sorted by address as the kernel lists it */
int wifi_vm_text(const struct wifi_vm *v, char *out, int size);
/* /proc/meminfo's MemFree, MemAvailable, Cached, Slab, VmallocUsed, as "MemFree=1234 ..." (kB) */
int wifi_mem_brief(const char *meminfo, char *out, int size);
/* The lines of text that hold any of the words (a NUL-separated list ended by an empty one), runs
 * of spaces made one, joined by " | " into out; how many */
int wifi_lines_with(const char *text, const char *words, char *out, int size);

/* "TIME up=S what\n" into out; its length */
int wifi_line(char *out, int size, long wall, unsigned up_s, const char *what);
/* The log: q2-wifi.0 in dir, moved on at WIFI_FILE_MAX through WIFI_FILES (log_flush) */
int wifi_flush(card_log *l, const char *dir);

/* ---- q2wifi.c: the device's side ---- */
void wifi_init(card_log *l, card_log *u,
               int again); /* again: a q2guard started again (no boot check) */
/* Each q2guard tick: the boot check, and while wpa_supplicant runs and is not joined its state and
 * scans. temp: q2guard's reading (tenths of a degree, GUARD_NONE none); heat: its last heat line or
 * "" */
void wifi_tick(unsigned now, int temp, const char *heat);
int wifi_fd(void);        /* the monitor's socket while attached, else -1: bt_sleep waits on it */
void wifi_drain(void);    /* its events, read as they come */
int wifi_flush_due(void); /* once after a snapshot or an association line: to the card soon */

#endif
