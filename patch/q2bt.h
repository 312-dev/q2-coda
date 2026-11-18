/* q2bt: the Bluetooth link monitor, a part of q2guard (docs/internals.md#bluetooth-link-monitor).
 * While an A2DP stream runs it writes one line a second to the card's q2-bt log: what the radio was
 * sent, what is waiting for it, what the controller says of the link, and what else was busy. The
 * one thing it changes: the headset's link is allowed EDR packets (bt_edr_step), once a handle. The
 * decisions, parsers and the line, with no reads of the device: q2bt_core.c, tested on this machine
 * by test/unit/test_q2bt.py. The device's side is q2bt.c; tools/q2bt.py reads the log. */
#ifndef Q2BT_H
#define Q2BT_H

#include "q2guard.h"

#define BT_TICK_MS 1000 /* a sample while a stream runs */
#define BT_START_BPS                                                                               \
    4000 /* bytes a second handed to the controller that make a stream (SBC's least is ~25000) */
#define BT_QUIET_MS                                                                                \
    30000 /* under BT_START_BPS this long: the stream ended (a stall is sampled through) */
#define BT_PCMS_MS 60000 /* bluealsa asked for the codec again this often while a stream runs */
#define BT_RAM 65536     /* the log's RAM ring while the card is away (about 9 minutes of lines) */
#define BT_FILE_MAX                                                                                \
    1048576 /* q2-bt.0 moved to .1 at this size: the two hold about 4 hours of streaming */
#define BT_UNSET (-1000) /* a signed reading not taken */

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

/* ---- what the controller says of one link: five commands on a raw HCI socket ---- */
#define BT_OP_FCC 0x1401  /* Read Failed Contact Counter */
#define BT_OP_LQ 0x1403   /* Read Link Quality */
#define BT_OP_RSSI 0x1405 /* Read RSSI */
#define BT_OP_AFH 0x1406  /* Read AFH Channel Map */
#define BT_OP_TPL 0x0c2d  /* Read Transmit Power Level (current) */
struct bt_link {
    int handle;
    int rssi, lq, tpl, afh; /* dB, 0-255, dBm, channels in use of 79; BT_UNSET not answered */
    int fcc;                /* the failed contact counter (flush timeouts in a row); BT_UNSET */
    int answered;           /* how many of the five have */
};
int bt_cmd(unsigned char *out, int op, int handle); /* the command packet into out[8]; its length */
/* listed (HCIGETCONNLIST's ACL handle, -1 none) taken as k's handle: 1 when it changed. A codec
 * change reconnects the headset on a new handle while the stream runs on, so it is looked up each
 * line. */
int bt_handle_follow(struct bt_link *k, int listed);
void bt_link_clear(struct bt_link *k, int handle);
/* An event read from the raw socket (its type byte first): 1 when it answered one of the five */
int bt_event(struct bt_link *k, const unsigned char *p, int n);
/* Number Of Completed Packets (event 0x13): the packets the controller says it delivered on handle
 */
int bt_completed(const unsigned char *p, int n, int handle);
/* Packets in the controller now: sent (acl_tx) less done (completions counted since the socket
 * opened), from the least difference seen, as the count at the opening is not known (the buffers
 * empty whenever the stream pauses or the link keeps up). Both counts modulo 2^32. */
struct bt_fly {
    int have, low;
};
int bt_fly_step(struct bt_fly *f, unsigned sent, unsigned done);

/* ---- EDR on the A2DP sink's link (docs/internals.md#bluetooth-link-monitor) ----
 * Change Connection Packet Type, once to each of the sink's ACL handles while a stream runs:
 * DM1|DH1 with the EDR "shall not use" bits (0x3306) clear, as `hcitool cpt MAC DM1,DH1`, which
 * moved the Px7's aptX HD link off multi-slot basic rate. The kernel's default mask (0xcc18) does
 * not. */
#define BT_OP_CPT 0x040f    /* Change Connection Packet Type (OGF 1, OCF 0x000F) */
#define BT_PTYPE_EDR 0x0018 /* DM1|DH1; the 2-DHx and 3-DHx bits left clear: EDR allowed */
/* The A2DP sink's address, from GetPCMs's a2dpsrc PCM ("/dev_EC_66_D1_CF_59_3F/a2dpsrc"), into
 * bdaddr[6] in the kernel's order (bdaddr_t: the last byte first); 0 none (no headset, or the Q2 is
 * the sink: a phone streaming to it is an a2dpsnk PCM). */
int bt_pcms_sink(const char *text, unsigned char *bdaddr);
/* HCIGETCONNLIST's answer: the classic ACL link to bdaddr, its handle; -1 none (LE never) */
int bt_acl_handle_of(const unsigned char *req, int max, const unsigned char *bdaddr);
struct bt_edr {
    int sent;                          /* the handle the command went to, -1 none since the reset */
    int said, said_ptype, said_status; /* an outcome has gone to the unified log, and which */
};
void bt_edr_reset(struct bt_edr *e); /* at the start and at a stream's STOP */
/* handle: the sink's link now, -1 none listed. The command into out[8] when that handle has not had
 * it since the reset; its length, else 0. A handle not listed for a line is not forgotten. */
int bt_edr_step(struct bt_edr *e, int handle, unsigned char *out);
/* An event read from the raw socket: Connection Packet Type Changed (0x1D) for the handle sent to,
 * or Command Status (0x0F) refusing the command. Its note ("LINK ptype=0x0018 status=0x00 h=2",
 * ptype "-" for a refusal) into note: 2 when the unified log should have it too (the first outcome,
 * or one unlike the last it had), 1 for the q2-bt log only, 0 not an answer to it. */
int bt_edr_event(struct bt_edr *e, const unsigned char *p, int n, char *note, int size);

/* ---- the step-down (docs/internals.md#bluetooth-link-monitor) ----
 * Under LDAC Auto with Quality's "Lower quality when the signal is weak" on, a link the radio
 * cannot keep up with is moved down a codec, aptX HD to aptX to SBC, and back up one at a time once
 * clean. */
#define BT_STEP_WQ 4096         /* bytes waiting in the A2DP socket over this, */
#define BT_STEP_STALL 2         /* with the controller's buffers full, this many seconds in a row */
#define BT_STEP_CLEAN 60        /* seconds in a row with nothing waiting before a step up */
#define BT_STEP_SETTLE_MS 10000 /* after a switch: the reconnect's gap, not counted either way */
#define BT_STEP_UP_MS 180000    /* a step up no sooner than this after the last switch */
/* "aptX-HD" 3, "aptX" 2, "SBC" 1, any other (LDAC, AAC, none) 0; and back */
int bt_codec_rank(const char *codec);
const char *bt_codec_name(int rank);
struct bt_step {
    int top;          /* the rank the stream was on before any step: never stepped above */
    int stall, clean; /* seconds in a row congested, clean */
    int have;         /* last is set */
    unsigned last;    /* ms of the last switch */
};
/* One line: allowed (LDAC Auto and the setting on), the codec's rank now, wq, fly (-1 not read)
 * and the controller's buffer count. The rank to select now, else 0. */
int bt_step(struct bt_step *s, unsigned now, int allowed, int rank, int wq, int fly, int bufs);

/* ---- the A2DP socket's queue ----
 * /proc/net/l2cap's largest Wmem (bytes an L2CAP socket has waiting: the kernel holds them only
 * while the controller's ACL buffers are full), or -1 with no such table. */
int bt_l2cap_wmem(const char *text);

/* ---- bluealsa's encoder thread ----
 * Named by bluealsa ("ba-a2dp-sbc", "ba-a2dp-aptx-hd", ...): 1 for such a comm. Its schedstat
 * ("run_ns wait_ns slices": on a CPU, waiting for one) as whole ms, modulo 2^32; 0 when not that.
 */
int bt_encoder_thread(const char *comm);
int bt_schedstat(const char *text, unsigned *run_ms, unsigned *wait_ms);

/* ---- the codec, from bluealsa's GetPCMs as dbus-send prints it ----
 * The A2DP source PCM's Codec into codec (n bytes), Sampling in Hz and Delay in tenths of a ms; 0
 * for none. */
int bt_pcms(const char *text, char *codec, int n, unsigned *hz, unsigned *delay);

/* ---- the log line ---- */
struct bt_sample {
    long wall;              /* seconds since 1970 */
    unsigned up;            /* seconds since boot */
    int kbps;               /* kbit/s handed to the controller; -1 not read */
    int acl;                /* ACL packets a second handed to it */
    int wq;                 /* bytes waiting in the A2DP socket */
    int rssi, lq, tpl, afh; /* struct bt_link's; BT_UNSET none */
    int err;                /* transmit errors since the last line */
    int wtx, wrx;           /* wlan0 kB/s each way; -1 down */
    int cpu;                /* all cores' busy percent */
    /* bluealsa's and q2guard's own CPU in tenths of a percent of one core (90 is 9.0%); -1 not read
     */
    int ba, own;
    int h;      /* the ACL handle asked about; -1 none */
    int er, ew; /* bluealsa's encoder thread, per second: ms on a CPU, ms waiting for one; -1 not
                   read */
    int fcc;    /* struct bt_link's; BT_UNSET none */
    int done;   /* packets a second the controller delivered (Number Of Completed Packets); -1 */
    int fly;    /* packets in the controller now (bt_fly_step); -1 */
};
int bt_line(char *out, int size, const struct bt_sample *s);
/* per-second rates over ms: n counted in ms, as a whole count a second (no 64-bit division) */
int bt_rate(unsigned n, unsigned ms);
/* clock ticks (100 a second) over ms as tenths of a percent of one core */
int bt_ticks_pct(unsigned ticks, unsigned ms);

/* The log: q2-bt.0 in dir, rotated into .1 at BT_FILE_MAX (log_flush, q2guard_core.c). */
int bt_flush(card_log *l, const char *dir);

/* ---- q2bt.c: the device's side ---- */
void bt_init(card_log *l, card_log *u); /* the q2-bt log, and the unified log for the EDR outcome */
/* Sleep ms; while a stream runs, wake each second to sample it. A signal that sets guard_woke
 * (q2guard.c: a request, a SIGTERM) ends it early. */
void bt_sleep(unsigned ms);
int bt_streaming(void);
int bt_flush_due(void); /* once after a stream's START or STOP: its lines to the card at once */

#endif
