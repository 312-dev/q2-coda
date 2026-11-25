/* q2-usbtrace: a read-only recorder of the USB port, --dev builds only (docs/internals.md#usb-tracer). It
 * watches PD17, PD18 and PD19, the kernel's uevents, the UDC's state, the PMIC and the USB registers, writes one
 * line per event to the card's q2-usbtrace log, and feeds every event to the USB state machine (usb_fsm.c) in
 * shadow, logging what it would have done beside what stock did. It writes nothing to any device.
 *
 * q2usbtrace_core.c is the recorder's logic with no device I/O: lines, samples, the shadow and the plug cycles,
 * tested on this machine by test/unit/test_q2usbtrace.py; q2usbtrace.c is the device side, whose every device
 * call goes through the t_* functions below, so the same test runs it over a simulated device and checks what it
 * opened and asked (Q2USBTRACE_HOST: test/unit/q2usbtrace_host.c gives them). */
#ifndef Q2USBTRACE_H
#define Q2USBTRACE_H

#if defined(Q2USBTRACE_HOST) && !defined(Q2GUARD_HOST)
#define Q2GUARD_HOST
#elif !defined(Q2USBTRACE_HOST) && !defined(Q2GUARD_DEVICE)
#define Q2GUARD_DEVICE
#endif
#include "q2guard.h" /* libc's declarations (MIPS o32 on the device), card_store.h */
#include "usb_fsm.h"

#ifdef Q2USBTRACE_HOST
#include <poll.h>
#include <sys/socket.h>
#include <time.h>
#ifndef CLOCK_BOOTTIME
#define CLOCK_BOOTTIME 7
#endif
#else
void *mmap(void *, size_t, int, int, int, long);
ssize_t recv(int, void *, size_t, int);
int mount(const char *, const char *, const char *, unsigned long, const void *);
int nice(int);
#endif

/* ---- the device's numbers ---- */
#define UT_GPIOCHIP "/dev/gpiochip3" /* GPD, base 96 (/sys/kernel/debug/gpio: gpio-113 is PD17) */
#define UT_PD17 17                    /* VBUS sense, held by demo's usb_event.c (q2-usb-cable): never asked for */
#define UT_PD18 18                    /* the CC chip's ID ("attached as source"), gpio-114, unclaimed */
#define UT_PD19 19                    /* beside them, a pull-up, never read by stock, gpio-115, unclaimed */
/* PD14 to PD16: unclaimed in software, PD16 read 0 with a source attached (the CC chip's "attached as sink" output, if
 * the second USB study's guess holds). Read from the PIN register only: a line request would make each a GPIO input,
 * and what the board has them doing is not known. */
#define UT_PD14 14
/* linux/gpio.h v1: GPIO_GET_LINEEVENT_IOCTL _IOWR(0xB4, 0x04, struct gpioevent_request), 48 bytes; the only
 * request this program makes. A line handle (GPIO_GET_LINEHANDLE_IOCTL), which could drive a pin, is never asked. */
#define UT_GPIO_GET_LINEEVENT 0xc030b404u
#define UT_GPIOHANDLE_REQUEST_INPUT 1u
#define UT_GPIOEVENT_BOTH_EDGES 3u
struct ut_event_request {
    unsigned lineoffset, handleflags, eventflags;
    char consumer_label[32];
    int fd;
};
struct ut_event_data {             /* struct gpioevent_data: 16 bytes */
    unsigned long long timestamp;  /* CLOCK_MONOTONIC ns (Linux 5.7 and later) */
    unsigned id, pad;              /* 1 rising, 2 falling */
};
#define UT_EDGE_RISING 1u
#define UT_EDGE_FALLING 2u

/* Registers, read through /dev/mem opened O_RDONLY and mapped PROT_READ: no store can reach them. */
#define UT_MEM "/dev/mem"
#define UT_PROT_READ 1
#define UT_MAP_SHARED 1
#define UT_O_SYNC 0x4010 /* MIPS o32 O_SYNC: uncached, as the registers need */
#define UT_CPM_PAGE 0x10000000u
#define UT_USBPCR 0x3c /* the PHY's control: bits 29:28 the ID pull-up (phy-ingenic-usb.c) */
#define UT_USBRDT 0x40 /* bit 24 IDDIG_EN, 23 IDDIG_REG: the controller's role from a register */
#define UT_GPIO_PAGE 0x10010000u
#define UT_PORT_C 0x200 /* each port 0x100 apart; PIN at +0x00 is the pin's level, INT at +0x10 */
#define UT_PORT_D 0x300
#define UT_PORT_E 0x400
#define UT_PIN 0x00
#define UT_INT 0x10
#define UT_PC20 20 /* bus power out, written only by demo (check_otg_status) */
#define UT_PE22 22 /* the charger off while host, written only by demo */

/* The AXP2101 (I2C 6, 0x34, axp20x-i2c bound to it, so I2C_SLAVE_FORCE): SMBus byte reads only, of its status
 * (00h: bit 5 VBUS good), IRQ enable (40h to 42h) and IRQ status (48h to 4Ah) registers. 49h bits 7 and 6 are VBUS
 * insert and remove, latched with the IRQ off and each cleared by the other event; a status bit is cleared by writing
 * 1, which this never does. */
#define UT_I2C "/dev/i2c-6"
#define UT_I2C_ADDR 0x34
#define UT_I2C_SLAVE_FORCE 0x0706
#define UT_I2C_SMBUS 0x0720
#define UT_I2C_SMBUS_READ 1
#define UT_I2C_SMBUS_BYTE_DATA 2
struct ut_smbus_args { /* struct i2c_smbus_ioctl_data */
    char read_write;
    unsigned char command;
    unsigned size;
    unsigned char *data;
};
#define UT_AXP_REGS 7
extern const unsigned char ut_axp_regs[UT_AXP_REGS]; /* 00h, 40h, 41h, 42h, 48h, 49h, 4Ah */

/* The rest: sysfs and debugfs files read, a uevent socket, demo's forwarded PD17 edges. */
#define UT_ONLINE "/sys/class/power_supply/usb/online"
#define UT_OTG "/sys/bus/platform/devices/13500000.otg" /* id_status, dwc2_mode, connected */
#define UT_UDC_STATE "/sys/class/udc/13500000.otg/state"
#define UT_HOSTDEV "/sys/bus/usb/devices/1-1/idVendor" /* a device on the host port */
#define UT_CARDS "/proc/asound/cards"
#define UT_DEBUGFS "/sys/kernel/debug"
#define UT_REGDUMP "/sys/kernel/debug/usb/13500000.otg/regdump"
#define UT_EDGES_NAME "q2-usbtrace-pd17" /* abstract AF_UNIX datagram name demo's usb_event.c sends PD17's edges to */
#define UT_AF_NETLINK 16
#define UT_NETLINK_KOBJECT_UEVENT 15
#define UT_SOCK_DGRAM 1 /* MIPS's */
#define UT_POLLPRI 2
#define UT_POLLERR 8

/* Files of its own: the trace on the card beside the other logs, its summaries in demo's --dev console log
 * (/tmp/q2-demo.log, which q2guard passes into the unified log as it is), and the flags that stop it. */
#define UT_LOGS "/mnt/mmc/.q2muse/logs"
#define UT_LOG_NAME "q2-usbtrace"
#define UT_FILE_MAX 262144 /* q2-usbtrace.0 moves to .1 once a flush finds it this big: 2 x 256 KB */
#define UT_RAM 65536       /* the ring that holds lines between flushes, and while the card is away */
#define UT_ULOG "/tmp/q2-demo.log"
#define UT_STOP_FLASH "/mnt/data/q2-usbtrace.off"
#define UT_STOP_CARD "/mnt/mmc/.q2muse/usbtrace.off"
#define UT_CARD_HOLD "/tmp/mmc_hold" /* demo letting the card go (card_release.c) */

/* ---- timing ---- */
#define UT_IDLE_MS 1000     /* a sample a second while nothing moves */
#define UT_BURST_MS 50      /* 20 a second ... */
#define UT_BURST_FOR_MS 30000 /* ... for 30 s after an edge, a uevent of the port's, or a change in a sample */
#define UT_SLOW_EVERY 5     /* the PMIC's registers and the regdump at an edge, 50 ms on, and every 5th burst
                            * sample (4 a second) */
#define UT_ONLINE_AT1_MS 50 /* online read again this long after an edge, and ... */
#define UT_ONLINE_AT2_MS 250 /* ... this long (usb_fsm.c UF_ONLINE_MS) */
#define UT_FLUSH_MS 60000   /* lines held at most this long before a write to the card */
#define UT_FLUSH_BYTES 16384 /* or once this many are held */
#define UT_CLOCK_MS 600000  /* a clock line (the wall time against the uptime) every 10 minutes */
#define UT_AGREE_MS 15000   /* a grant by one side and the other's within this are the same decision */
#define UT_WAKE_MS 500      /* CLOCK_BOOTTIME moved this much past CLOCK_MONOTONIC: a Standby */

/* One reading of the port: -1 for a value that could not be read. */
struct ut_sample {
    int pd17, pd18, pd19, pc20, pe22; /* the pins' levels (GPIO PIN registers) */
    int pd14, pd15, pd16;             /* read only, beside them */
    int online;                       /* power_supply/usb/online */
    char id[8], mode[8], conn[12];    /* the dwc2's id_status, dwc2_mode, connected, first word */
    int dev;                          /* /sys/bus/usb/devices/1-1 there */
    int snd;                          /* a second sound card in /proc/asound/cards */
    long pcr, rdt;                    /* USBPCR, USBRDT; -1 not mapped */
};
struct ut_slow {                      /* read at edges and every UT_SLOW_EVERY burst sample */
    int axp[UT_AXP_REGS];             /* -1 not read */
    long gotgctl, gintsts, hprt0;     /* the regdump's words, -1 none */
};

/* Which of the fed levels the shadow last saw (usb_fsm's events), so the same change from two sources (an edge
 * and a sample) is fed once. */
enum { UT_FED_VBUS, UT_FED_IDLOW, UT_FED_POWER, UT_FED_HOSTDEV, UT_FED_PCM, UT_FED_UDC, UT_FED_N };

/* A plug cycle: from the first movement after quiet to UT_BURST_FOR_MS with none. Times are ns, UF_NEVER unset. */
struct ut_cycle {
    int open, n;
    long long start, last;
    long long vbus_on, vbus_off, id_low, id_high, online1, addr, conf, dev_add, dev_rm, card_add, pcm_add;
    long long stock_on, stock_off, shadow_on, shadow_off, shadow_replug, shadow_charger, shadow_host;
    int stock_grants, shadow_grants, disagreed, unsafe, residue;
    char start_state[16];
};

struct trace {
    struct usb_fsm fsm;
    int fed[UT_FED_N];
    struct ut_sample last;  /* the last sample written (all -1 before the first) */
    struct ut_slow slow;
    int have_last, have_slow;
    char udc[20];
    long long now, burst_until, next_sample, next_flush, next_clock, online_at1, online_at2, fsm_due;
    long long fsm_last; /* the shadow's last step: its time never goes back */
    unsigned samples_in_burst;
    struct ut_cycle cyc;
    card_log log;
    unsigned long long lines, dropped_lines;
    char path0[200], path1[200]; /* q2-usbtrace.0 and .1 under the log folder */
    long wall_at_start;
};

/* Writers the core calls, given by q2usbtrace.c (and the host test's harness). */
void ut_ulog(struct trace *tr, char level, const char *msg); /* a line for the unified log */

void trace_init(struct trace *tr, const char *logs_dir, long long now);
/* A line "<uptime s.us> <text>\n" held for the card (t_ns its time). */
void trace_line(struct trace *tr, long long t_ns, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
/* An edge of PD17, PD18 or PD19 at its kernel time; how ("" requested, "fwd" demo's). */
void trace_edge(struct trace *tr, int pin, int level, long long t_ns, const char *how);
/* One uevent (the kernel's NUL-separated text, n bytes) received at t_ns. */
void trace_uevent(struct trace *tr, const char *msg, int n, long long t_ns);
/* The UDC's state file as read (its first word). */
void trace_udc(struct trace *tr, const char *state, long long t_ns);
/* A sample: the changes written, the shadow fed, a burst started on a change. */
void trace_sample(struct trace *tr, const struct ut_sample *s, long long t_ns);
/* An online read made for an edge (at it, 50 and 250 ms on): always written and fed. */
void trace_online(struct trace *tr, int online, long long t_ns);
void trace_slow(struct trace *tr, const struct ut_slow *s, long long t_ns);
/* Back from a Standby that lasted slept_ms. */
void trace_wake(struct trace *tr, long long slept_ms, long long t_ns);
/* The shadow's timers due by t_ns, and a cycle that has gone quiet closed. */
void trace_tick(struct trace *tr, long long t_ns);
/* When the main loop must next wake (ns); the next sample's time in tr->next_sample. */
long long trace_next_wake(const struct trace *tr);
/* A burst begins (an edge or a port uevent): 20 samples a second for UT_BURST_FOR_MS, a cycle opened. */
void trace_burst(struct trace *tr, long long t_ns);
/* What a flush should write, and its rotation: 1 when name.0 must first move to name.1 (size its bytes). */
int trace_rotate_needed(long size);

/* n / d, its remainder in *rem (may be 0): the helpers link libc alone, which has no 64-bit division (libgcc's
 * __udivdi3), so the uptimes are divided bit by bit. */
unsigned long long ut_udiv(unsigned long long n, unsigned d, unsigned *rem);

/* Parsers of what the device gives, tested here. */
int ut_udc_addressed(const char *state); /* "addressed" or "configured" */
int ut_regdump_word(const char *text, const char *name, long *v); /* "GOTGCTL = 0x000d00fc" */
int ut_cards_second(const char *text); /* a card numbered 1 in /proc/asound/cards */
/* A line event request for a line: input, both edges, nothing else. */
void ut_event_request(struct ut_event_request *r, unsigned line);
/* A read of one AXP2101 register as the SMBus ioctl takes it: a read, one byte. */
void ut_smbus_read(struct ut_smbus_args *a, unsigned char reg, unsigned char *data);

/* ---- the device's calls (q2usbtrace.c), each one place; the host test gives its own ---- */
int t_open(const char *path, int flags);
int t_close(int fd);
int t_read(int fd, void *buf, int n);
int t_reread(int fd, void *buf, int n); /* a sysfs attribute again from its start (which re-arms its poll) */
int t_ioctl(int fd, unsigned long req, void *arg);
int t_poll(struct pollfd *p, int n, int timeout_ms);
void *t_mmap(unsigned len, int fd, unsigned long off); /* PROT_READ, MAP_SHARED: the only mapping made */
int t_socket(int domain, int type, int protocol);
int t_bind(int fd, const void *addr, unsigned len);
int t_recv(int fd, void *buf, int n);
int t_mount_debugfs(void);
long long t_now(int clock); /* ns: CLOCK_MONOTONIC (1) or CLOCK_BOOTTIME (7) */
long t_wall(void);
int t_exists(const char *path);
int t_append(const char *path, const char *s, int n); /* the unified log's line, appended to an existing file */
int t_card_ready(void); /* the card mounted read-write and the Q2's own (not lent to a computer, not being let go) */
int q2usbtrace_main(int argc, char **argv);

#endif
