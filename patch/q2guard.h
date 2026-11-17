/* q2guard: the heat recorder and overheat guardian (docs/internals.md#overheat-guardian). The
 * decisions, the log line and the log's storage, with no reads of the device: q2guard_core.c,
 * tested on this machine by test/unit/test_q2guard.py. The device's side is q2guard.c. */
#ifndef Q2GUARD_H
#define Q2GUARD_H

#ifdef Q2GUARD_HOST
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#else
/* No MIPS sysroot (as boot.c): glibc 2.28's declarations, with MIPS o32 open flags. */
typedef unsigned size_t;
typedef int ssize_t;
int open(const char *, int, ...), close(int), fsync(int), rename(const char *, const char *);
int mkdir(const char *, unsigned), snprintf(char *, size_t, const char *, ...);
ssize_t write(int, const void *, size_t);
long lseek(int, long, int);
void *memcpy(void *, const void *, size_t), *memmove(void *, const void *, size_t);
size_t strlen(const char *);
#define O_WRONLY 1
#define O_APPEND 0x8
#define O_CREAT 0x100
#define O_TRUNC 0x200
#define O_CLOEXEC 0x80000
#define SEEK_END 2
#endif

#define GUARD_NONE (-100000) /* a temperature not read */

/* Temperatures in tenths of a degree C, times in ms, CPU busy in percent of all cores. */
#define GUARD_WARN_DC 500     /* the warning toast */
#define GUARD_REARM_DC 450    /* the toast again only after falling below this */
#define GUARD_SHUT_DC 580     /* the protective power-off */
#define GUARD_OVER 3          /* readings in a row over a threshold before it counts */
#define GUARD_MIN_DC (-200)   /* a plausible reading lies in GUARD_MIN_DC..GUARD_MAX_DC */
#define GUARD_MAX_DC 900
#define GUARD_ERRORS 3        /* unreadable or implausible readings in a row: the sensor is down */
#define GUARD_VARY 60         /* readings (5 min at 5 s) a sensor has to vary in, or it is a constant */
#define GUARD_WIN 24          /* the load window: 2 min at 5 s */
#define GUARD_LOAD_PCT 85     /* the window's mean at or over this, with no sensor: the load warning */
#define GUARD_CALM_PCT 50     /* after a Cancel, the warning re-arms after GUARD_CALM samples under */
#define GUARD_CALM 12         /* 1 min at 5 s */
#define GUARD_LOAD_MS 60000   /* the load warning's countdown */
#define GUARD_ACK_MS 5000     /* demo's answer to a power-off, or q2guard pauses audio itself */
#define GUARD_CARD_MS 10000   /* the power-off card's countdown */
#define GUARD_DEADLINE_MS 30000 /* from the power-off's start, power off whatever the log is doing */

enum { SENSOR_PENDING, SENSOR_OK, SENSOR_DOWN };
enum { LOAD_ARMED, LOAD_COUNTING, LOAD_CANCELLED };

struct guard {
    int sensor, errors, seen, lo, hi;  /* the sensor's verdict, and what it has read so far */
    int warn_n, shut_n, warned;         /* readings in a row over each threshold; toast shown */
    int busy[GUARD_WIN], busy_n, busy_i; /* the load window */
    int load, calm;                     /* the load warning's state; samples under, cancelled */
    unsigned load_end;                  /* its countdown's end */
    int off;                            /* a power-off began (never undone) */
    unsigned off_at;                    /* when */
    int off_why;                        /* 'T' temperature, 'L' load */
    int killed, powered;                /* the player stopped by q2guard; power-off asked */
};

struct guard_in {
    unsigned now;
    int temp;   /* tenths of a degree C, or GUARD_NONE */
    int busy;   /* percent, or -1 not known */
    int cancel; /* demo's Cancel on the load warning */
    int acked;  /* demo answered the power-off */
    int saved;  /* the log flush after the power-off began has finished (written or given up) */
};

enum { EV_NONE, EV_SENSOR_DOWN, EV_SENSOR_OK, EV_WARN, EV_LOAD_WARN, EV_LOAD_CANCELLED, EV_OFF };
struct guard_out {
    int event;      /* what changed on this step: EV_* */
    int kill_player;/* demo never answered: stop the audio by stopping the player */
    int flush;      /* write the log out now */
    int power_off;  /* power off now */
};

void guard_step(struct guard *g, const struct guard_in *in, struct guard_out *out);
const char *guard_state(const struct guard *g); /* "pending", "ok", "nosensor", "warn", ... */

/* The log line: one sample. */
struct proc_top {
    char name[16];
    int pct; /* tenths of a percent of one core */
};
struct heat_sample {
    long wall;           /* seconds since 1970 (the Q2's clock, maybe never set) */
    unsigned up;         /* seconds since boot */
    int temp;            /* the sensor's, or GUARD_NONE */
    int raw_temp;        /* battery/temp as the driver says it (not a temperature on the Q2), or GUARD_NONE */
    int level, mv;       /* battery percent (CW2015), mV; -1 not read */
    char status[12];     /* battery/status's first word */
    int cable;           /* the charger supply's online, -1 not read */
    char udc[16];        /* the UDC's state */
    char gadget[24];     /* the configfs gadget bound to it, "-" none */
    int screen, bright;  /* backlight on, its brightness; -1 not read */
    int wifi, bt;        /* wlan0 up, hci0 up */
    int mhz;             /* CPU clock, -1 not read */
    int busy;            /* total CPU busy since the last line, percent; -1 first line */
    struct proc_top top[3];
    int demo_threads, procs;
    const char *state;   /* guard_state */
};
int heat_line(char *out, int size, const struct heat_sample *s);

/* The per-process CPU over an interval: ticks from /proc/[pid]/stat (utime + stime). */
#define TOP_PIDS 512
struct top_table {
    int n;
    int pid[TOP_PIDS];
    unsigned ticks[TOP_PIDS];
};
/* cur: this pass's pids, names, ticks (n of them), against prev; the three that used most,
 * as tenths of a percent of one core over span ticks of one core; prev becomes cur */
void top_three(struct top_table *prev, int n, const int *pid, const char (*name)[16],
               const unsigned *ticks, unsigned span, struct proc_top *top);

/* The log: lines held in RAM and written to the card in batches. */
#define HEAT_RAM 65536          /* held while the card is away; the oldest lines go first */
#define HEAT_FILE_MAX 262144    /* each of the two files */
struct heat_log {
    char ram[HEAT_RAM];
    int used;
    int dropped;     /* lines dropped from the RAM ring since the last write */
    int away;        /* the card was away (or a write failed) since the last write */
    long away_since; /* wall clock then */
};
void heat_add(struct heat_log *l, const char *line); /* one line, with its newline */
/* Write what is held to dir/q2-heat.0, moving .0 to .1 when it would pass HEAT_FILE_MAX; on any
 * failure keep it all. 0 written (or nothing held), -1 kept. now: for the gap's marker. */
int heat_flush(struct heat_log *l, const char *dir, long now);
void heat_card_away(struct heat_log *l, long now); /* the card is not available */

#endif
