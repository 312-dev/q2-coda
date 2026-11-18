/* q2bt: the Bluetooth link monitor, a part of q2guard (docs/internals.md#bluetooth-link-monitor). While
 * an A2DP stream runs it writes one line a second to the card's q2-bt log: what the radio was sent,
 * what is waiting for it, what the controller says of the link, and what else was busy; it changes
 * nothing. The decisions, parsers and the line, with no reads of the device: q2bt_core.c, tested on
 * this machine by test/unit/test_q2bt.py. The device's side is q2bt.c; tools/q2bt.py reads the log. */
#ifndef Q2BT_H
#define Q2BT_H

#include "q2guard.h"

#define BT_TICK_MS 1000      /* a sample while a stream runs */
#define BT_START_BPS 4000    /* bytes a second handed to the controller that make a stream (SBC's least is ~25000) */
#define BT_QUIET_MS 30000    /* under BT_START_BPS this long: the stream ended (a stall is sampled through) */
#define BT_PCMS_MS 60000     /* bluealsa asked for the codec again this often while a stream runs */
#define BT_RAM 65536         /* the log's RAM ring while the card is away (about 9 minutes of lines) */
#define BT_FILE_MAX 1048576  /* q2-bt.0 moved to .1 at this size: the two hold about 4 hours of streaming */
#define BT_UNSET (-1000)     /* a signed reading not taken */

/* ---- is a stream running ----
 * From the controller's byte counter (hci_dev_info's stat.byte_tx), read at each call. */
enum { BT_EV_NONE, BT_EV_START, BT_EV_STOP };
struct bt_detect {
    int on, have;       /* a stream runs; a count has been read */
    unsigned at, bytes; /* the last call's time (ms) and count */
    unsigned quiet;     /* ms in a row under BT_START_BPS */
};
int bt_detect_step(struct bt_detect *d, unsigned now, unsigned byte_tx);

/* ---- hci_dev_info (HCIGETDEVINFO), as the kernel lays it out ---- */
#define BT_DEVINFO_SIZE 92
struct bt_counts {
    unsigned acl_tx, byte_tx, err_tx; /* since the controller came up */
    int acl_mtu, acl_pkts;            /* the controller's ACL buffers: their size, how many */
};
void bt_devinfo(const unsigned char *di, struct bt_counts *c);
/* hci_conn_list_req's answer (HCIGETCONNLIST): the first ACL link's handle, -1 none */
int bt_acl_handle(const unsigned char *req, int max);

/* ---- what the controller says of one link: four commands on a raw HCI socket ---- */
#define BT_OP_LQ 0x1403   /* Read Link Quality */
#define BT_OP_RSSI 0x1405 /* Read RSSI */
#define BT_OP_AFH 0x1406  /* Read AFH Channel Map */
#define BT_OP_TPL 0x0c2d  /* Read Transmit Power Level (current) */
struct bt_link {
    int handle;
    int rssi, lq, tpl, afh; /* dB, 0-255, dBm, channels in use of 79; BT_UNSET not answered */
    int answered;           /* how many of the four have */
};
int bt_cmd(unsigned char *out, int op, int handle); /* the command packet into out[8]; its length */
void bt_link_clear(struct bt_link *k, int handle);
/* An event read from the raw socket (its type byte first): 1 when it answered one of the four */
int bt_event(struct bt_link *k, const unsigned char *p, int n);

/* ---- the A2DP socket's queue ----
 * /proc/net/l2cap's largest Wmem (bytes an L2CAP socket has waiting: the kernel holds them only while
 * the controller's ACL buffers are full), or -1 with no such table. */
int bt_l2cap_wmem(const char *text);

/* ---- the codec, from bluealsa's GetPCMs as dbus-send prints it ----
 * The A2DP source PCM's Codec into codec (n bytes), Sampling in Hz and Delay in tenths of a ms; 0 for
 * none. */
int bt_pcms(const char *text, char *codec, int n, unsigned *hz, unsigned *delay);

/* ---- the log line ---- */
struct bt_sample {
    long wall;        /* seconds since 1970 */
    unsigned up;      /* seconds since boot */
    int kbps;         /* kbit/s handed to the controller; -1 not read */
    int acl;          /* ACL packets a second handed to it */
    int wq;           /* bytes waiting in the A2DP socket */
    int rssi, lq, tpl, afh; /* struct bt_link's; BT_UNSET none */
    int err;          /* transmit errors since the last line */
    int wtx, wrx;     /* wlan0 kB/s each way; -1 down */
    int cpu;          /* all cores' busy percent */
    int ba, own;      /* bluealsa's and q2guard's own CPU, tenths of a percent of one core; -1 not read */
};
int bt_line(char *out, int size, const struct bt_sample *s);
/* per-second rates over ms: n counted in ms, as a whole count a second (no 64-bit division) */
int bt_rate(unsigned n, unsigned ms);
/* clock ticks (100 a second) over ms as tenths of a percent of one core */
int bt_ticks_pct(unsigned ticks, unsigned ms);

/* The log: q2-bt.0 in dir, rotated into .1 at BT_FILE_MAX (log_flush, q2guard_core.c). */
int bt_flush(card_log *l, const char *dir);

/* ---- q2bt.c: the device's side ---- */
void bt_init(card_log *l);
/* Sleep ms; while a stream runs, wake each second to sample it. */
void bt_sleep(unsigned ms);
int bt_streaming(void);

#endif
