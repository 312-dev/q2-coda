/* q2guard: the heat recorder and overheat guardian (docs/internals.md#overheat-guardian). The
 * decisions, the log line and the log's rotation, with no reads of the device: q2guard_core.c,
 * tested on this machine by test/unit/test_q2guard.py, as is the PMIC's die temperature reader,
 * q2guard_pmic.c, over a bus it is given. The device's side is q2guard.c. */
#ifndef Q2GUARD_H
#define Q2GUARD_H

#ifdef Q2GUARD_HOST
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <stdlib.h>
#include <unistd.h>
#include <dirent.h>
#include <signal.h>
#else
/* No MIPS sysroot (as boot.c): glibc 2.28's declarations, with MIPS o32 open flags. */
typedef unsigned size_t;
typedef int ssize_t;
int open(const char *, int, ...), close(int), fsync(int), rename(const char *, const char *);
int mkdir(const char *, unsigned), snprintf(char *, size_t, const char *, ...);
ssize_t write(int, const void *, size_t);
long lseek(int, long, int);
/* what card_store.c uses (a FILE is a void *, as there) */
void *fopen(const char *, const char *), *calloc(size_t, size_t);
size_t fread(void *, size_t, size_t, void *), fwrite(const void *, size_t, size_t, void *);
int fclose(void *), fflush(void *), fseek(void *, long, int), fileno(void *);
long ftell(void *);
void free(void *);
void *memcpy(void *, const void *, size_t), *memmove(void *, const void *, size_t);
void *memset(void *, int, size_t);
int memcmp(const void *, const void *, size_t);
size_t strlen(const char *);
#define O_WRONLY 1
#define O_APPEND 0x8
#define O_CREAT 0x100
#define O_TRUNC 0x200
#define O_CLOEXEC 0x80000
#define SEEK_END 2
/* q2guard.c's and q2bt.c's */
struct timespec {
    long tv_sec, tv_nsec;
};
struct dirent { /* glibc's, MIPS o32, no LFS */
    unsigned d_ino;
    long d_off;
    unsigned short d_reclen;
    unsigned char d_type;
    char d_name[256];
};
typedef int pid_t;
ssize_t read(int, void *, size_t);
int clock_gettime(int, struct timespec *), usleep(unsigned), kill(int, int), reboot(int);
pid_t fork(void), waitpid(pid_t, int *, int);
void _exit(int), sync(void);
long time(long *);
void *opendir(const char *);
struct dirent *readdir(void *);
int flock(int, int), closedir(void *), strcmp(const char *, const char *), strncmp(const char *, const char *, size_t);
char *strstr(const char *, const char *), *strchr(const char *, int);
int atoi(const char *);
ssize_t readlink(const char *, char *, size_t); /* q2bt.c's: the player's open songs */
int access(const char *, int);
#define F_OK 0
#define O_RDONLY 0
#define O_RDWR 2
#define O_NONBLOCK 0x80
#define SEEK_SET 0
/* q2log's: the unified log's feeds and demo's requests (q2guard.c) */
int unlink(const char *), ftruncate(int, long), getpid(void), *__errno_location(void);
void (*signal(int, void (*)(int)))(int);
#define SIGTERM 15
#define SIGUSR1 16 /* MIPS's numbers */
#define EPIPE 32
#define WNOHANG 1
#define CLOCK_MONOTONIC 1
#define RB_POWER_OFF 0x4321fedc
#define LOCK_EX 2
#define LOCK_NB 4
/* q2bt.c's: the HCI socket, MIPS o32's numbers (_IOR on MIPS is 2 << 29) */
struct pollfd {
    int fd;
    short events, revents;
};
int socket(int, int, int), bind(int, const void *, unsigned), setsockopt(int, int, int, const void *, unsigned);
int ioctl(int, unsigned long, ...), poll(struct pollfd *, unsigned long, int);
int execl(const char *, const char *, ...);
#define AF_BLUETOOTH 31
#define BTPROTO_HCI 1
#define SOCK_RAW 3
#define SOCK_CLOEXEC O_CLOEXEC
#define SOCK_NONBLOCK O_NONBLOCK
#define POLLIN 1
#define HCIGETDEVINFO 0x400448d3u
#define HCIGETCONNLIST 0x400448d4u
/* q2dbus.c's: the system bus's socket */
int connect(int, const void *, unsigned);
ssize_t send(int, const void *, size_t, int);
unsigned getuid(void);
#define AF_UNIX 1
#define SOCK_STREAM 2 /* MIPS's (SOCK_DGRAM is 1 there) */
#define MSG_NOSIGNAL 0x4000
#endif

#ifndef PEQ_ROOT
#define PEQ_ROOT "" /* card_store.h's CARD_STORE_DIR is on the card itself (host tests give a scratch one) */
#endif
#include "card_store.h"
int card_mounted(void); /* card_store.c asks: q2guard.c's (the writing child's), the host test's */

#define GUARD_NONE (-100000) /* a temperature not read */

/* Temperatures in tenths of a degree C, times in ms, CPU busy in percent of all cores. The thresholds are
 * for the AXP2101's die, which runs hotter than the battery beside it (docs/internals.md#overheat-guardian). */
#define GUARD_WARN_DC 550     /* the warning toast */
#define GUARD_REARM_DC 500    /* the toast again only after falling below this */
#define GUARD_SHUT_DC 620     /* the protective power-off */
#define GUARD_OVER 3          /* readings in a row over a threshold before it counts */
#define GUARD_MIN_DC 0        /* a plausible reading lies in GUARD_MIN_DC..GUARD_MAX_DC */
#define GUARD_MAX_DC 1000
#define GUARD_JUMP_DC 30      /* a reading further than this from the last good one is not believed */
#define GUARD_STEP_DC 60      /* the most a reading may move at a charger change (GUARD_SETTLE readings) */
#define GUARD_SETTLE 2        /* readings from a charger change that may step: the die can be read before the cable */
#define GUARD_ERRORS 3        /* unreadable or implausible readings in a row: the sensor is down */
#define GUARD_VARY 60         /* readings (5 min at 5 s) a sensor has to vary in, or it is a constant */
#define GUARD_SWING 40        /* a trusted sensor stuck GUARD_VARY readings while the load swung this much: down */
#define GUARD_WIN 24          /* the load window: 2 min at 5 s */
#define GUARD_LOAD_PCT 85     /* the window's mean at or over this, with no sensor: the load warning */
#define GUARD_CALM_PCT 50     /* after a Cancel, the warning re-arms after GUARD_CALM samples under */
#define GUARD_CALM 12         /* 1 min at 5 s */
#define GUARD_LOAD_MS 60000   /* the load warning's countdown */
#define GUARD_ACK_MS 5000     /* demo's answer to a power-off, or q2guard pauses audio itself */
#define GUARD_CARD_MS 10000   /* the power-off card's countdown */
#define GUARD_DEADLINE_MS 30000 /* from the power-off's start, power off whatever the log is doing */

enum { SENSOR_PENDING, SENSOR_OK, SENSOR_DOWN };
/* why a sensor went down (guard_why's words): none read, out of range, a jump, a constant, stuck under load */
enum { GUARD_WHY_NONE = 'N', GUARD_WHY_RANGE = 'R', GUARD_WHY_JUMP = 'J', GUARD_WHY_CONST = 'C', GUARD_WHY_STUCK = 'S' };
enum { LOAD_ARMED, LOAD_COUNTING, LOAD_CANCELLED };
/* guard_in's charge, bits: the charger's supply online, the battery charging */
enum { GUARD_CHARGE_CABLE = 1, GUARD_CHARGE_CHARGING = 2 };

struct guard {
    int sensor, errors, seen, lo, hi;  /* the sensor's verdict, and what it has read so far */
    int last, same, busy_lo, busy_hi;  /* the last good reading, how many in a row read it, the load meanwhile */
    int why;                            /* why the sensor went down: GUARD_WHY_* */
    int charge, settle;                 /* the charger's last state; readings left that may step */
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
    int charge; /* the charger's state, GUARD_CHARGE_*: a change steps the die's reading (q2guard_core.c) */
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
const char *guard_why(const struct guard *g);   /* why the sensor is down, for the log */

/* The AXP2101's die temperature (q2guard_pmic.c), over a bus q2guard.c gives: I2C 6, address 0x34. */
#define PMIC_ADC_STOCK 0x03 /* 0x30 as stock leaves it: the VBAT and TS channels */
#define PMIC_ADC_DIE 0x13   /* and the die temperature channel (bit 4) */
#define PMIC_WINDOW_MS 1000 /* each sample's window in ADC mode 00 before the result is read */
#define PMIC_POLL_MS 50     /* the first window's reads, to log when the first conversion landed */
#define PMIC_ERRORS 3       /* I2C errors in a row, over samples, before the reader turns off */
struct pmic_bus {
    int (*rd)(void *ctx, int reg);          /* the byte, or -1 on an I2C error */
    int (*wr)(void *ctx, int reg, int val); /* 0, or -1 on an I2C error */
    void (*sleep_ms)(void *ctx, int ms);
    void *ctx;
};
enum { PMIC_START, PMIC_ON, PMIC_OFF };                     /* struct pmic's state; PMIC_OFF is for good */
enum { PMIC_OK, PMIC_IO = -1, PMIC_REFUSED = -2, PMIC_TORN = -3 }; /* what a step did */
struct pmic {
    int state, errors;
    int measured, first_ms; /* the first window ran (1; 2 once logged); ms to its first conversion, -1 none */
    char note[96];          /* a log note this sample made (the channel on, the reader off and why), or "" */
};
/* One sample: the die temperature in tenths of a degree, or GUARD_NONE (*raw the ADC count, or -1). */
int pmic_sample(const struct pmic_bus *b, struct pmic *p, int *raw);
int pmic_deci(int raw);

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
    int die, adc;        /* the PMIC's die temperature (tenths) and its ADC count, or GUARD_NONE and -1 */
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
/* "<time> what\n": a note of its own in the log (BOOT, WARN, POWEROFF, ...) */
int heat_note(char *out, int size, long wall, const char *what);

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

/* The log: lines held in card_store.c's RAM ring and written to the card in batches. */
#define HEAT_RAM 65536          /* the ring's bytes while the card is away; the oldest lines go first */
#define HEAT_FILE_MAX 262144    /* a file is moved on when a flush finds it this big */
#define HEAT_FILES 4            /* q2-heat.0 to .3: about four hours of 5 s lines after a move */
void heat_add(card_log *l, const char *line); /* one line, with its newline */
/* Rotate dir/q2-heat.0 on if it is full, then append the ring to it in order (card_log_flush, which
 * also notes lines dropped for room). 0 when nothing is left held, -1 when the card refused. */
int heat_flush(card_log *l, const char *dir);
/* 1 while path names a live process (demo's pid in /tmp/mmc_hold while it lets the card go before Standby, a
 * power-off or a restart, card_release.c): nothing is written to the card meanwhile, the lines kept in RAM. A file
 * naming a process gone (demo restarted) holds nothing. */
int card_hold_live(const char *path);
/* The same for any log: dir/name.0, moved on once it has reached max bytes, .1 to .2 and so on up to
 * name.(files-1), the oldest dropped */
int log_flush(card_log *l, const char *dir, const char *name, long max, int files);
/* How often q2guard.c sends the RAM lines to the card: what a sudden power loss can lose */
#define FLUSH_MS 300000     /* idle: the heat log's 30 s lines, ten at most */
#define FLUSH_FAST_MS 30000 /* a stream running, or warm, busy or plugged in */
int flush_every(int fast, int streaming);

/* set by q2guard.c's signal handler (demo's or S90play's request, a SIGTERM): bt_sleep returns early */
extern volatile int guard_woke;

/* q2guard.c's readers, which q2bt.c shares */
int slurp(const char *path, char *buf, int size); /* the file's start, NUL-terminated: its length, -1 none */
int read_int(const char *path, int *v);           /* 0 when read */
unsigned now_ms(void);                            /* CLOCK_MONOTONIC */
struct cpu {
    unsigned long long all, idle;
};
int busy_since(struct cpu *c, int *cores); /* all cores' busy percent since the last call, -1 the first */

#endif
