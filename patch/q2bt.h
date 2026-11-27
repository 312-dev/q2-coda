/* q2bt: the Bluetooth link monitor, a part of q2guard (docs/internals.md#bluetooth-link-monitor).
 * While an A2DP stream runs it writes one line a second to the card's q2-bt log: what the radio was
 * sent, what is waiting for it, what the controller says of the link, and what else was busy. While
 * any classic link is up, streaming or idle, it records it too (the recorder, docs/bluetooth.md's
 * sampler): each link's RSSI and AFH map every 10 s and what the controller agreed for it once, the
 * UART's error counts, byte counters and the A2DP socket's queue each second, and every minute the
 * Wi-Fi band and the coex lines that move; and it writes the pacing state downloads obey while A2DP
 * streams with Wi-Fi on 2.4 GHz (BT_PACE_FILE). Two
 * things it changes: the headset's link is allowed EDR packets (bt_edr_step), once a handle; and
 * under Auto, aptX HD or aptX is stepped down to SBC while the radio cannot keep up
 * (bt_step), and SBC's bitpool or AAC's peak lowered (bt_sbc_step, bt_aac_step), all with Adapt to
 * Signal on. The
 * decisions, parsers and the line, with no reads of the device: q2bt_core.c, tested on this machine
 * by test/unit/test_q2bt.py. The device's side is q2bt.c; tools/q2bt.py reads the log. The
 * controller watchdog (bt_wedge, test/unit/test_q2bt_wedge.py) lives here too, and the device
 * events read off the kernel's management channel (bt_mgmt_*, test/unit/test_q2bt_devices.py). */
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
#define BT_FILE_MAX 1048576 /* q2-bt.0 moved on at this size: a megabyte is about 2 hours at 1 Hz  \
                             */
#define BT_FILES                                                                                   \
    4 /* q2-bt.0 to .3: after a move 3 MB, about 6 hours, and never past 4 MB and a flush */
#define BT_UNSET (-1000) /* a signed reading not taken */
#define BT_LINK_MS 10000 /* each classic link's RSSI and AFH map, streaming or idle */
#define BT_COEX_MS 60000 /* the Wi-Fi band and the coex lines, while a link is up */
#define BT_LINKS 4       /* links recorded at once: HCIGETCONNLIST is asked for four */

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
    unsigned byte_rx;                 /* bytes the controller handed the host */
};
void bt_devinfo(const unsigned char *di, struct bt_counts *c);
/* hci_conn_list_req's answer (HCIGETCONNLIST): the first ACL link's handle, -1 none; its address
 * into bdaddr[6] (bdaddr_t order) unless bdaddr is 0 */
int bt_acl_handle(const unsigned char *req, int max, unsigned char *bdaddr);

/* ---- what the controller says of one link: read-only commands on a raw HCI socket ----
 * RSSI and the AFH map each second while it streams and every BT_LINK_MS while idle; the other four
 * once a link (its handle). Link Quality, the current transmit power and the Failed Contact Counter
 * are not asked: in 16,390 lines (2026-11-21 to 25) they never moved (docs/bluetooth.md). */
#define BT_OP_RSSI 0x1405  /* Read RSSI */
#define BT_OP_AFH 0x1406   /* Read AFH Channel Map */
#define BT_OP_ROLE 0x0809  /* Role Discovery */
#define BT_OP_LSTO 0x0c36  /* Read Link Supervision Timeout */
#define BT_OP_FLUSH 0x0c27 /* Read Automatic Flush Timeout */
#define BT_OP_TPL 0x0c2d   /* Read Transmit Power Level, asked for the maximum */
struct bt_link {
    int handle;
    int rssi, afh; /* dB, channels in use of 79; BT_UNSET not answered */
    int role;      /* Role Discovery's: 0 the Q2 central, 1 peripheral; BT_UNSET */
    int lsto;      /* supervision timeout, slots of 0.625 ms; BT_UNSET */
    int flush;     /* automatic flush timeout, slots of 0.625 ms, 0 none; BT_UNSET */
    int txmax;     /* the maximum transmit power, dBm; BT_UNSET */
    int answered;  /* how many of the commands sent have */
};
int bt_cmd(unsigned char *out, int op, int handle); /* the command packet into out[8]; its length */
/* listed (HCIGETCONNLIST's ACL handle, -1 none) taken as k's handle: 1 when it changed. A codec
 * change reconnects the headset on a new handle while the stream runs on, so it is looked up each
 * line. */
int bt_handle_follow(struct bt_link *k, int listed);
void bt_link_clear(struct bt_link *k, int handle);
/* An event read from the raw socket (its type byte first): 1 when it answered one of the six */
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
/* The A2DP sink's address, from bluealsa's a2dpsrc PCM ("/dev_EC_66_D1_CF_59_3F/a2dpsrc"), into
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
/* An event read from the raw socket: Disconnection Complete (0x05) for handle, the link followed.
 * Its note into note, "LINK down h=5 reason=0x13 headset dev=EC:66:D1:CF:59:3F": the reason as the
 * controller gave it and the side it names, headset (0x13 to 0x15: it ended the link, or ran low or
 * switched off), q2 (0x16: this host, so bluetoothd, the kernel or stock's own reconnect), timeout
 * (0x08 the supervision timeout, 0x22 an LMP answer that never came: the radio) or other, and the
 * link's address (bdaddr, from the connection list while it was up; 0 writes dev=-). 1 when it was
 * one, 0 for any other event, another handle's, or one whose status says the link stayed. */
int bt_down_event(const unsigned char *p, int n, int handle, const unsigned char *bdaddr, char *note,
                  int size);

/* ---- the step-down (docs/internals.md#bluetooth-link-monitor) ----
 * Under Auto with Quality's Adapt to Signal on, a link the radio cannot keep up with is moved
 * from aptX HD or aptX straight to SBC, whose bitpool then adapts with no gap (bt_sbc_step), and
 * back to where it started once the link is clean and the band clear. */
#define BT_STEP_WQ 4096         /* bt_sbc_step: bytes waiting in the A2DP socket over this, */
#define BT_STEP_SHORT 90        /* bt_step: a second short, delivered under this % of the rate, */
#define BT_STEP_STALL 2         /* either with every ACL buffer held, this many seconds in a row */
#define BT_STEP_QUIET 50        /* kbit/s: a second under it, no buffer held, counts neither way */
#define BT_STEP_CLEAN 60        /* seconds in a row not short, the band clear, before a step up */
#define BT_STEP_AFH 60          /* the band clear: at least this many channels in the AFH map */
#define BT_STEP_SETTLE_MS 10000 /* after a switch: the reconfigure's gap, not counted either way */
#define BT_STEP_UP_MS 180000    /* a step up no sooner than this after the last switch, */
/* doubled each time a step up is undone within BT_STEP_UNDONE_MS, to BT_STEP_UP_MAX_MS */
#define BT_STEP_UNDONE_MS 300000
#define BT_STEP_UP_MAX_MS 1440000
/* "aptX-HD" 3, "aptX" 2, "SBC" 1, any other (LDAC, AAC, none) 0; and back */
int bt_codec_rank(const char *codec);
const char *bt_codec_name(int rank);
/* A rank's rate on the link in kbit/s, its headers in, as the q2-bt log's kbps shows it: aptX HD
 * 614 and aptX 412 at 48 kHz, SBC by its frame at bitpool own (33 when 0); at hz (48 kHz when 0).
 * 0 for another rank. */
int bt_codec_kbps(int rank, unsigned hz, int own);
struct bt_step {
    int top;          /* the rank the stream was on before any step: never stepped above */
    int stall, clean; /* seconds in a row short, clean */
    int have;         /* last is set */
    unsigned last;    /* ms of the last switch */
    unsigned up_ms;   /* the wait before a step up, 0 BT_STEP_UP_MS */
    int raised;       /* the last switch was up, at raised_at (ms) */
    unsigned raised_at;
};
/* What bt_step reads of a line (bt_sample's, bufs START's), -1 not read */
struct bt_step_line {
    int kbps, acl, done; /* kbit/s handed to the controller, its packets, those it completed */
    int wq, fly, bufs;   /* bytes waiting in the A2DP socket, packets in flight, its ACL buffers */
    int afh;             /* channels in the AFH map */
    int own;             /* SBC: the encoder thread's own bitpool, 0 not known */
    int lowered;         /* SBC: its bitpool below its own (bt_sbc_step's) */
    unsigned hz;         /* the codec's sampling rate, 0 not known */
};
/* One line: allowed (bt_step_allowed), the codec's rank now and the line. The rank to select now,
 * else 0. */
int bt_step(struct bt_step *s, unsigned now, int allowed, int rank, const struct bt_step_line *l);
#define BT_LDAC_AUTO 2           /* [SYSSET] BTCODING's Auto (g_btcoding's row) */
#define BT_STEP_KEY "BTSTEPDOWN" /* [Q2POD]: Adapt to Signal, on only at 1 (patch/bt_ui.c) */
/* config.ini's text: 1 Auto with the setting on, 0 Auto with it off, -1 another choice
 * (or none saved) */
int bt_step_allowed(const char *ini);
/* bluealsa's PCMs (as bt_pcms reads them): the A2DP source PCM's object path, which SelectCodec is sent to,
 * into path (n bytes); 0 none, or one holding anything but letters, digits, _ and / */
int bt_pcms_path(const char *text, char *path, int n);
/* dbus-send --print-reply's text: the first byte array after key (itself the first after after, when after is not 0)
 * as lower-case hex into hex (size bytes, NUL-terminated): its byte count; 0 for none, or one dbus-send printed as a
 * string (every byte printable). The CODEC note's cfg= (BlueZ's MediaTransport1 Configuration, the bytes the stream
 * was set up with) and cap= (bluealsa's GetCodecs Capabilities for the codec: the sink's, filtered by the Q2's own)
 * (docs/internals.md#bluetooth-link-monitor). */
int bt_dbus_bytes(const char *text, const char *after, const char *key, char *hex, int size);

/* ---- SBC's bitpool (docs/internals.md#bluetooth-link-monitor) ----
 * bluealsa's SBC encoder takes its bitpool from tools/bt_silence.S's sbc_pool byte at each frame,
 * as the lesser of it and the bitpool the encoder thread started with (0 or 1: that one), which
 * libsbc applies from the next frame: no renegotiation, no gap. The three bytes at BT_SBC_POOL in
 * bluealsa (tools/build.py's BT_SBC_POOL): the bitpool asked for (q2guard writes it through
 * /proc/PID/mem), the thread's own (written by its first frame) and the last one encoded with. */
#define BT_SBC_POOL 0x450600
#define BT_SBC_FLOOR 19         /* the lowest rung: about 150 kbit/s at 48 kHz joint stereo */
#define BT_SBC_SETTLE_MS 2000   /* after a change: the queue drains at the new rate, not counted */
#define BT_SBC_CLEAN 30         /* seconds in a row with nothing waiting before a rung up, */
#define BT_SBC_UP_MS 60000      /* and no sooner than this after the last change; */
#define BT_SBC_UP_MAX_MS 480000 /* doubled, to this, each time a rung up stalls again within it */
/* A ladder's place: SBC's bitpool, or AAC's peak (bt_aac_step, with the same timing) */
struct bt_rung {
    int rung;         /* the rung asked for (a bitpool, or kbit/s), 0 the stream's own */
    int stall, clean; /* seconds in a row congested, clean */
    int have;         /* last is set */
    unsigned last;    /* ms of the last change */
    unsigned up_ms;   /* the wait before a rung up, 0 BT_SBC_UP_MS */
    int raised;       /* the last change was up, at raised_at (ms) */
    unsigned raised_at;
};
/* The rung after pool (0 the thread's own, own) going down (dir -1) or up (1): down below own and
 * not under BT_SBC_FLOOR, up to 0 once the next rung reaches own; -1 none (or own not known, 0). */
int bt_sbc_next(int pool, int own, int dir);
/* One line: allowed (bt_sbc_allowed), the codec's rank (bt_codec_rank), own, wq, fly (-1 not read)
 * and the controller's buffer count, as bt_step's. The bitpool to ask for now (0 the thread's own),
 * else -1. Another codec, or the switch turned off, asks for 0 once. */
int bt_sbc_step(struct bt_rung *s, unsigned now, int allowed, int rank, int own, int wq, int fly,
                int bufs);
#define BT_SBC_HQ 6 /* [SYSSET] BTCODING's SBC HQ and SBC rows (g_btcoding) */
#define BT_SBC_ROW 7
/* config.ini's text: 1 Auto, SBC HQ or SBC with BT_STEP_KEY's switch on, 0 those with it off,
 * -1 another choice (or none saved) */
int bt_sbc_allowed(const char *ini);

/* ---- AAC's peak (docs/internals.md#bluetooth-link-monitor, AAC peak) ----
 * bluealsa's AAC encoder sets fdk-aac's peak bitrate (a VBR stream: every frame's header stays as it
 * was, only the frames shrink) or its bitrate (CBR) to tools/bt_silence.S's aac_ask byte, in kbit/s,
 * before its next frame, when that is below the stream's own, and back to its own at 0. The four bytes
 * at BT_AAC_RATE in bluealsa (tools/build.py's BT_AAC_RATE): the rate asked for (q2guard writes it
 * through /proc/PID/mem), the stream's own (its configured bitrate, 240 as the build sets it; 0 not
 * known, or a set that failed), the one in use (0 its own) and fdk-aac's bitrate mode (0 CBR). */
#define BT_AAC_RATE 0x450604
#define BT_AAC_FLOOR 128 /* the lowest rung: every frame at most 341 bytes at 48 kHz, a packet that fits a 2-DH3 */
/* As bt_sbc_next, on AAC's rungs in kbit/s (192, 160, BT_AAC_FLOOR) below the stream's own. */
int bt_aac_next(int rate, int own, int dir);
/* As bt_sbc_step, on AAC under Auto: allowed bt_step_allowed's (Auto with the switch on; AAC chosen
 * by hand is left alone, as Adapt to Signal's caption names Auto and SBC only), aac 1 while the
 * stream is AAC, own its rate in kbit/s (0 not known). The rate to ask for now (0 the stream's
 * own), else -1. */
int bt_aac_step(struct bt_rung *s, unsigned now, int allowed, int aac, int own, int wq, int fly,
                int bufs);

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

/* ---- the codec, from bluealsa's PCMs as dbus-send prints them ----
 * text is org.freedesktop.DBus.ObjectManager's GetManagedObjects on /org/bluealsa (q2bt.c PCMS_CMD), which
 * stock's bluealsa 4.0.0 and Vamp's 5.0.0 both answer (docs/bluealsa.md#the-callers); 4.0.0's GetPCMs, its one
 * level of nesting fewer, reads the same. The A2DP source PCM's Codec into codec (n bytes), its rate in Hz (4.0.0
 * calls it Sampling, 5.0.0 Rate) and Delay in tenths of a ms; 0 for none. */
int bt_pcms(const char *text, char *codec, int n, unsigned *hz, unsigned *delay);

/* ---- the log line ---- */
struct bt_sample {
    long wall;     /* seconds since 1970 */
    unsigned up;   /* seconds since boot */
    int kbps, brx; /* kbit/s handed to the controller, and handed back by it; -1 not read */
    int acl;       /* ACL packets a second handed to it */
    int wq;        /* bytes waiting in the A2DP socket */
    int rssi, afh; /* struct bt_link's; BT_UNSET none */
    int wtx, wrx;  /* wlan0 kB/s each way; -1 down */
    int cpu;       /* all cores' busy percent */
    /* bluealsa's and q2guard's own CPU in tenths of a percent of one core (90 is 9.0%); -1 not read
     */
    int ba, own;
    int h;    /* the ACL handle asked about; -1 none */
    int er;   /* bluealsa's encoder thread, ms a second on a CPU; -1 not read */
    int done; /* packets a second the controller delivered (Number Of Completed Packets); -1 */
    int fly;  /* packets in the controller now (bt_fly_step); -1 */
    int bp; /* SBC's bitpool on the last frame (BT_SBC_POOL's third byte); -1 not SBC or not read */
    /* the player's side, for the encoder fed short with the radio idle: the player's (hciplayer)
     * CPU in tenths of a percent of one core; the microSD's busy ms in the second (io_ticks); 1
     * while it waits for a song still arriving (grow.c's wait file), 0 not; -1 each not read */
    int pl, dk, gw;
    /* the H5 UART's framing, parity, break and the two overrun counts since the last line, when
     * ue_ok (bt_uart_counts' fe, pe, brk, oe, bo) */
    int ue[5], ue_ok;
};
int bt_line(char *out, int size, const struct bt_sample *s);
/* /proc/diskstats' io_ticks (ms the device was busy, modulo 2^32) for the device named dev; 0 none */
int bt_disk_busy(const char *text, const char *dev, unsigned *ms);
/* 1 when a path the player holds open is a song: a file on the card, not under .q2muse (logs) */
int bt_song_path(const char *path);
/* per-second rates over ms: n counted in ms, as a whole count a second (no 64-bit division) */
int bt_rate(unsigned n, unsigned ms);
/* clock ticks (100 a second) over ms as tenths of a percent of one core */
int bt_ticks_pct(unsigned ticks, unsigned ms);

/* ---- the recorder: every classic link, streaming or idle (docs/bluetooth.md's sampler) ---- */
struct bt_acl {
    int handle;
    unsigned char addr[6]; /* bdaddr_t: the last byte first */
};
/* HCIGETCONNLIST's answer: its classic ACL links (not SCO, not LE) into out, n at most; how many */
int bt_acl_links(const unsigned char *req, int max, struct bt_acl *out, int n);
/* "TIME ACL h= dev= rssi= afh=\n" (info 0), or once a link "TIME ACLINFO h= dev= role=central|
 * peripheral lsto=MSms flush=MSms|none txmax=DBM\n" (info 1); "-" for an answer not had */
int bt_acl_line(char *out, int size, long wall, const struct bt_link *k, const unsigned char *addr, int info);
/* An idle link's second, no radio traffic: bytes a second to and from the controller, Wi-Fi's kB/s,
 * the A2DP socket's queue, the UART's error counts since the last (as bt_sample's ue) */
struct bt_idle {
    long wall;
    unsigned up;
    int btx, brx, wtx, wrx, wq; /* -1 not read */
    int ue[5], ue_ok;
};
/* "TIME IDLE up= btx= brx= wtx= wrx= wq= ue=fe/pe/brk/oe/bo\n" */
int bt_idle_line(char *out, int size, const struct bt_idle *s);
/* Realtek's coex dump (BT_COEX_PROC): the sixteen lines that move (the study's filter: Wi-Fi channel,
 * band and traffic, power state, throughput, BT status, profiles, role, the BT side's AFH map, TDMA,
 * coex mode, null frames, TDMA toggles, BT slots, the PTA counts, path owner), each with its runs of
 * spaces made one and its leading ones gone, joined by newlines into out; how many */
#define BT_COEX_PROC "/proc/net/rtl8733bs/wlan0/btcoex"
int bt_coex_filter(const char *text, char *out, int size);
/* wpa_supplicant's STATUS answer: freq= in MHz (0 none: not joined), wpa_state= into state */
int bt_wpa_freq(const char *status, char *state, int n);
/* "TIME WIFI freq= band=2.4|5|6 ch= state=\n", "-" for a freq of 0 */
int bt_wifi_line(char *out, int size, long wall, int freq, const char *state);

/* ---- pacing (docs/internals.md#bluetooth-coexistence-pacing) ----
 * While A2DP streams and Wi-Fi is joined on 2.4 GHz, the Q2's own downloads take airtime the headset's
 * link needs (the stall cluster of 2026-11-24: Wi-Fi receiving 1.5 to 2 MB/s). q2guard writes
 * BT_PACE_FILE, "paced=1 rate=BYTES freq=MHZ", at each change and at its start; the firmware's
 * downloads (netupdate.c) and the card apps that read it hold their background transfers to rate
 * bytes a second while paced=1. */
#define BT_PACE_FILE "/tmp/q2-bt-coex"
#define BT_PACE_RATE 204800 /* 200 KB/s: about a tenth of what the stall cluster received */
int bt_pace(int streaming, int freq);                             /* 1 to pace */
int bt_pace_text(char *out, int size, int paced, int freq); /* the file's line; its length */

/* ---- a wedged controller (docs/internals.md#bluetooth-controller-watchdog) ----
 * Realtek's H5 UART driver logs each bad packet and carries on, and the kernel logs an HCI command
 * left unanswered ("hci0: command 0x0401 tx timeout") and sends the next: a controller whose link
 * has wedged leaves Bluetooth dead until the stack is relaunched. A sign (a storm of H5 errors, or
 * a command timeout) makes the controller suspect; Read Local Version left unanswered
 * BT_PROBE_FAILS times in a row confirms it; demo then relaunches the stack as stock's Bluetooth
 * switch does (heatguard.c), asked through q2guard's state file. */
#define BT_H5_STORM 20            /* H5 error lines within BT_H5_WINDOW_MS that make a storm */
#define BT_H5_WINDOW_MS 10000     /* (the kernel's own times; one bad packet is a single line) */
#define BT_SUSPECT_MS 60000       /* a suspicion with no new sign this long, no probe failed: dropped */
#define BT_OP_VERSION 0x1001      /* the probe: Read Local Version Information, no side effects */
#define BT_PROBE_MS 2500          /* a probe unanswered this long failed (the kernel gives up at 2 s) */
#define BT_PROBE_GAP_MS 5000      /* probes while suspect no closer than this (q2guard's tick) */
#define BT_PROBE_FAILS 2          /* in a row: the controller is wedged */
#define BT_PROBE_CALM_MS 60000    /* after a probe answered, or once given up: the next no sooner */
#define BT_RESET_GAP_MS 120000    /* a relaunch no sooner than this after the last, doubled for each */
#define BT_RESET_CURED_MS 600000  /* in a row: a wedge this soon after the last relaunch is in a row */
#define BT_RESET_MAX 3            /* relaunches in a row, none curing, before giving up */
#define BT_RESET_SETTLE_MS 60000  /* after a relaunch: the stack starting, its lines not counted */
#define BT_RESET_WAIT_MS 60000    /* demo's answer waited for this long */
enum { BT_SIGN_NONE, BT_SIGN_H5, BT_SIGN_TIMEOUT };
/* A kernel message (/dev/kmsg's text after the ';'): BT_SIGN_H5 for the H5 driver's error lines,
 * BT_SIGN_TIMEOUT for "hci0: command 0x.... tx timeout", else BT_SIGN_NONE */
int bt_kmsg_sign(const char *msg);
/* /dev/kmsg's record "PRI,SEQ,USEC,FLAGS;message": the time in ms since boot (modulo 2^32) and the
 * message; 0 when it is not one */
int bt_kmsg_split(const char *rec, unsigned *ms, const char **msg);
/* The H5 UART's counters since boot from /proc/tty/driver's text (serial_core): v[] is tx, rx, fe, pe, brk, oe, bo
 * (framing, parity, break, FIFO overrun, flip buffer overrun), the ones the line leaves out 0. 1 when port's line has
 * tx and rx, else 0. The Q2's controller is on ttyS0 (stock's rtk_hciattach), the file "INGENIC serial". */
enum { BT_UART_N = 7, BT_UART_PORT = 0 };
#define BT_UART_PROC "/proc/tty/driver/INGENIC serial"
int bt_uart_counts(const char *text, int port, unsigned *v);
/* The Wi-Fi driver's inactive power save counts from its ps_info (" IPS enter count:N, IPS leave count:N"): how often
 * it has powered the Wi-Fi side of the RTL8733BS down and up since it loaded. 1 when the line is there, else 0. */
#define BT_WIFI_PS_PROC "/proc/net/rtl8733bs/wlan0/ps_info"
int bt_wifi_ips(const char *text, unsigned *enter, unsigned *leave);
/* The H5 driver's own debug lines, which the kernel's dynamic debug prints once q2guard asks for them at an H5 error
 * line (docs/internals.md#bluetooth-controller-watchdog): the sequence and acknowledgement numbers each side holds,
 * which the error lines leave out. The module's pr_debug formats, by kind:
 *   0 "hu %p retransmitting %u pkts"            h5_timed_event      the Q2 sends its unacknowledged packets again
 *   1 "Request for pkt %u from card"            h5_complete_rx_pkt  the controller's acknowledgement number
 *   2 "Received seqno %u from card"             h5_complete_rx_pkt  a reliable packet from the controller, in order
 *   3 "Removing %u pkts out of %u, up to seqno %u"  h5_pkt_cull     what that acknowledgement took off the Q2's queue
 *   4 "Sending packet with seqno %u"            h5_prepare_pkt      the Q2's sequence number
 *   5 "We request packet no %u to card"         h5_prepare_pkt      the Q2's acknowledgement number */
enum { BT_H5D_KINDS = 6, BT_H5D_TEXT = 56, BT_H5D_CAP = 240 };
#define BT_H5D_MS 20000     /* the debug lines stay on this long after the last H5 error line; one later is a new event */
#define BT_H5D_MAX_MS 60000 /* and no longer than this in all, however long a storm's error lines keep coming */
#define BT_H5D_MODULE "hci_uart_h5_kernel_5_10_21"
#define BT_H5D_CTL "/proc/dynamic_debug/control"
#define BT_H5D_CTL_DEBUGFS "/sys/kernel/debug/dynamic_debug/control"
struct bt_h5dbg {
    char last[BT_H5D_KINDS][BT_H5D_TEXT]; /* the last line logged of each kind, cut to fit */
    int lines;                            /* logged since it was zeroed */
};
/* A kernel message's kind as above, or -1 */
int bt_h5dbg_kind(const char *msg);
/* 1 to log msg: a debug line that differs from the last logged of its kind, under BT_H5D_CAP lines. A wedge repeats
 * the same few lines, so each state is logged once and each change of a number once more. */
int bt_h5dbg_line(struct bt_h5dbg *d, const char *msg);
/* The text for dynamic debug's control file, a query a line, turning those four functions' lines on or off; its
 * length, or 0 when cap is too small */
int bt_h5dbg_ctl(char *b, int cap, int on);
enum { BT_W_NONE, BT_W_PROBE, BT_W_RESET, BT_W_HEALTHY, BT_W_GIVEUP, BT_W_DEFER, BT_W_LATE };
struct bt_wedge {
    unsigned h5[BT_H5_STORM]; /* the kernel times of the last H5 error lines, a ring at h5_i */
    int h5_n, h5_i;
    int suspect;          /* a sign seen and not cleared: 's' a storm first, 't' a timeout first */
    int storm, timeout;   /* which signs this suspicion has had */
    unsigned first, last; /* now (q2guard's clock) of its first and latest sign */
    int fails;            /* probes in a row unanswered */
    int probed;           /* probed_at is set */
    unsigned probed_at, calm_until;
    int calm;             /* calm_until is set */
    int resets;           /* relaunches in a row, none curing */
    int have_reset, gave_up, deferred;
    unsigned reset_at;    /* the last relaunch asked for */
    int asked;            /* a relaunch asked for and not yet answered (its number is req) */
    int req;              /* relaunches asked for since q2guard started: what the state file says */
};
/* One kernel message, now (q2guard's clock) and its own time kms; its sign */
int bt_wedge_line(struct bt_wedge *w, unsigned now, unsigned kms, const char *msg);
/* Each tick: BT_W_PROBE when a probe is due, BT_W_LATE when demo has not answered a relaunch in
 * BT_RESET_WAIT_MS (given up waiting; counted as a relaunch), else BT_W_NONE */
int bt_wedge_step(struct bt_wedge *w, unsigned now);
/* A probe's outcome (1 answered, 0 not, -1 no verdict: hci0 down, or no socket). BT_W_RESET: ask
 * for a relaunch now (w->req is its number, w->first when the trouble began); BT_W_DEFER: one is
 * due but busy (a codec switch running) or too soon after the last; BT_W_GIVEUP, once, when
 * BT_RESET_MAX in a row did not cure it; BT_W_HEALTHY when an answer cleared a suspicion; else
 * BT_W_NONE. busy: a codec switch is running. */
int bt_wedge_probed(struct bt_wedge *w, unsigned now, int answered, int busy);
/* demo's answer to relaunch n: the settle starts */
void bt_wedge_done(struct bt_wedge *w, unsigned now, int n);
/* The probe's answer: 1 for a Command Complete to Read Local Version (an event read off a raw HCI
 * socket, its type byte first) */
int bt_probe_answer(const unsigned char *p, int n);
/* hci_dev_info's flags: 1 while hci0 is up (HCI_UP) */
int bt_dev_up(const unsigned char *di);
/* the q2guard state file's answer line "bt N done" or "bt N off" (heatguard.c): N, 0 none; *off 1
 * for "off" (Bluetooth was off: nothing relaunched) */
int bt_answer_parse(const char *ack, int *off);

/* ---- devices: the kernel's management events (docs/internals.md#bluetooth-devices) ----
 * Connects (and who began them), disconnects, failed connects and authentications, pairings and
 * Forgets, each a line of the unified log naming the device, whether a stream runs or not; and
 * discovery: each search's start and stop, and the first answer from each device in it. Opcodes
 * from Linux 5.10's include/net/bluetooth/mgmt.h. */
#define BT_MGMT_EV_NEW_LINK_KEY 0x0009
#define BT_MGMT_EV_DEVICE_CONNECTED 0x000B
#define BT_MGMT_EV_DEVICE_DISCONNECTED 0x000C
#define BT_MGMT_EV_CONNECT_FAILED 0x000D
#define BT_MGMT_EV_AUTH_FAILED 0x0011
#define BT_MGMT_EV_DEVICE_FOUND 0x0012
#define BT_MGMT_EV_DISCOVERING 0x0013
#define BT_MGMT_EV_DEVICE_UNPAIRED 0x0016
struct bt_mgmt_ev {
    int op;                /* one of the eight above */
    unsigned char addr[6]; /* bdaddr_t: the last byte first; zero for Discovering */
    int type;              /* the address's: 0 BR/EDR, 1 LE public, 2 LE random; Discovering's
                            * type, a mask of them (1 BR/EDR, 6 LE, 7 both) */
    int code;              /* a disconnect's reason, a failure's mgmt status, Discovering's 1 started
                            * or 0 stopped; -1 none */
    char name[64];         /* the connected or found event's EIR name, cut to fit; "" none */
    int rssi;              /* Device Found's, dBm; -1 for the rest */
    int out;               /* Device Connected: who began the link (bt_acl_out: 1 the Q2, 0 the
                            * device), filled in by the caller; -1 not known */
};
/* One event as read off the control channel (struct mgmt_hdr first, n bytes): 1 when it was one
 * of the eight, whole, into e; 0 for any other event or one cut short. */
int bt_mgmt_parse(const unsigned char *p, int n, struct bt_mgmt_ev *e);
/* Its unified-log message into out, name (or 0) quoted after the address: "connected
 * 04:00:6E:D3:96:6D "Pixel Buds" by=q2", "disconnected ... reason=1 timeout or link loss", "connect
 * failed ... status=0x04 no answer (page timeout)", "auth failed ...", "forgotten ...", "paired
 * ...", "found ... rssi=-52 BR/EDR", "discovery started BR/EDR+LE"; " LE" after the name for an LE
 * address. Its level: W for a disconnect the Q2 did not ask for and for a failure, else I; 0 for an
 * event that is none of the eight. */
char bt_mgmt_line(const struct bt_mgmt_ev *e, const char *name, char *out, int size);
/* Device Found comes for each inquiry or advertising answer, many a second: only each address's
 * first in a discovery is logged, BT_FOUND_MAX addresses at most. */
#define BT_FOUND_MAX 32
struct bt_found {
    unsigned char addr[BT_FOUND_MAX][6];
    int n, capped;
};
void bt_found_reset(struct bt_found *f); /* at each discovery's start */
/* 1 bdaddr's first in this discovery (log it); 0 seen, or past the cap; -1 the first past the
 * cap (log that the rest are not) */
int bt_found_first(struct bt_found *f, const unsigned char *bdaddr);
/* HCIGETCONNLIST's answer: who began the ACL link to bdaddr, the kernel's hci_conn->out: 1 the Q2,
 * 0 the device, -1 not listed */
int bt_acl_out(const unsigned char *req, int max, const unsigned char *bdaddr);
/* BlueZ's device info file's text: its Name= line's value into name (cut to size), 1 when found */
int bt_info_name(const char *text, char *name, int size);
void bt_addr_text(const unsigned char *bdaddr, char *out); /* "04:00:6E:D3:96:6D" into out[18] */
/* The names last seen, so a Forget, whose info file BlueZ has already removed, still names the
 * device: the oldest replaced */
#define BT_NAMES 8
struct bt_names {
    unsigned char addr[BT_NAMES][6];
    char name[BT_NAMES][64];
    int next;
};
const char *bt_names_get(const struct bt_names *c, const unsigned char *bdaddr); /* 0 none */
/* name may be bt_names_get's answer for the same address: the slot it points into keeps it */
void bt_names_put(struct bt_names *c, const unsigned char *bdaddr, const char *name);

/* The log: q2-bt.0 in dir, moved on at BT_FILE_MAX through BT_FILES files (log_flush,
 * q2guard_core.c). */
int bt_flush(card_log *l, const char *dir);

/* ---- q2bt.c: the device's side ---- */
void bt_init(card_log *l, card_log *u); /* the q2-bt log, and the unified log (EDR, STEP) */
/* Sleep ms; while a stream runs, wake each second to sample it. A signal that sets guard_woke
 * (q2guard.c: a request, a SIGTERM) ends it early. */
void bt_sleep(unsigned ms);
/* One more socket bt_sleep waits on, drained as it wakes: the Wi-Fi recorder's monitor (q2wifi.c wifi_fd,
 * wifi_drain); fd answers -1 while there is none */
void bt_also_wait(int (*fd)(void), void (*drain)(void));
int bt_streaming(void);
int bt_flush_due(void); /* once after a stream's START or STOP: its lines to the card at once */
int bt_dev_warned(void); /* once after a device line at W (a drop, a failed connect): flush soon */
void bt_kmsg(const char *rec, int n); /* each /dev/kmsg record q2guard reads: the wedge's signs */
/* Each tick: the wedge watched (probes, relaunches asked for); the relaunch number for the state
 * file's bt= (0 none yet). ack: the ack file's text, demo's answer in it. */
int bt_watch(unsigned now, const char *ack);

#endif
