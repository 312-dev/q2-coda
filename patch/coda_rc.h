/* coda-rc, Q2 Muse's own remote-control protocol (docs/remote-protocol.md, remote/coda-rc.proto).
 * The daemon is patch/coda_rc*.c, built as /usr/bin/coda-rc by tools/build.py (CODA_RC_SOURCES); it is not
 * part of demo's payload. coda_rc_core.c is the transport-agnostic part: frames, the protobuf subset, pairing,
 * authentication and sealing. coda_rc.c owns the transports and the data, coda_rc_ble.c the BLE GATT one.
 * CODA_HOST builds the same files against the host's libc for test/unit/test_coda_rc.py. */
#ifndef CODA_RC_H
#define CODA_RC_H
#include <stddef.h>
#include <stdint.h>

#define CRC_PROTO_VERSION 1
#define CRC_TCP_PORT 7720
#define CRC_HDR 6             /* u16 len | u8 type | u8 flags | u16 req_id, big-endian */
#define CRC_MAX_PAYLOAD 8192  /* the largest plaintext payload either side sends or accepts */
#define CRC_TAG 16
#define CRC_MAX_FRAME (CRC_HDR + CRC_MAX_PAYLOAD + CRC_TAG)
#define CRC_FLAG_SEALED 1

/* Frame types (remote/coda-rc.proto, "Frame types"). Never renumbered. */
enum {
    T_HELLO = 0x01, T_HELLO_RESP, T_PAIR_START, T_PAIR_CHALLENGE, T_PAIR_FINISH, T_PAIR_RESULT, T_AUTH, T_AUTH_RESP,
    T_ERROR = 0x0f,
    T_SUBSCRIBE = 0x10, T_STATE, T_GET_STATE,
    T_TRANSPORT = 0x20, T_ACK, T_SET_MODE, T_RATE = 0x24,
    T_BROWSE = 0x30, T_BROWSE_RESP, T_SEARCH, T_SEARCH_RESP,
    T_PLAY = 0x40, T_GET_QUEUE = 0x42, T_QUEUE_RESP, T_PLAY_QUEUE_ITEM, T_START_RADIO = 0x46,
    T_GET_COVER = 0x50, T_COVER,
    T_GET_REMOTE = 0x60, T_REMOTE_INFO, T_REGISTER_REMOTE,
    T_PING = 0x7e, T_PONG,
};

/* Error.code (remote/coda-rc.proto ErrorCode) */
enum {
    E_UNSUPPORTED = 1, E_UNAUTHORIZED, E_BAD_REQUEST, E_NOT_FOUND, E_BUSY, E_PAIRING_FAILED, E_PAIRING_LOCKED,
    E_TOO_LARGE, E_INTERNAL, E_VERSION, E_UNAVAILABLE, E_PAIRING_CANCELLED, E_PAIRING_FULL,
};

/* HelloResp.caps bits */
#define CAP_BROWSE 1u
#define CAP_SEARCH 2u
#define CAP_PLAY 4u
#define CAP_QUEUE 8u
#define CAP_COVERS 16u
#define CAP_SEEK 32u
#define CAP_SERVER 64u
#define CAP_MODES 128u      /* SetMode */
#define CAP_QUEUE_JUMP 256u /* PlayQueueItem */
#define CAP_RATE 512u       /* Rate, NowPlaying.hearts and .rateable */
#define CAP_RESUME 1024u    /* Play.resume, ACTION_RESUME */
#define CAP_FAVORITES 2048u /* the library's favorites node */
#define CAP_RECENT 4096u    /* the library's recent node (Recently Added) */
#define CAP_BUFFERING 8192u /* NowPlaying.buffering and its bytes */
#define CAP_RADIO 16384u    /* StartRadio, NowPlaying.radio */
#define CAP_NOTICE 32768u   /* NowPlaying.notice: the Q2 Muse's toast, for the car to show a moment */
#define CAP_REMOTE 65536u   /* GetRemote: q2-iroh's endpoint id and relay, to reach the Q2 Muse from another network */
#define CAP_IROH_ALLOW 131072u /* RegisterRemote: q2-iroh admits only paired phones' iroh keys */

/* ---- the protobuf subset: varints (wire type 0) and length-delimited fields (wire type 2) ---- */
typedef struct { uint8_t *p; size_t n, cap; int over; } pbw;
void pb_varint(pbw *w, unsigned field, uint64_t v);
void pb_bytes(pbw *w, unsigned field, const void *s, size_t n);
void pb_str(pbw *w, unsigned field, const char *s);
size_t pb_begin(pbw *w, unsigned field);  /* a nested message: pb_end patches its length */
void pb_end(pbw *w, size_t mark);

typedef struct { const uint8_t *p, *end; } pbr;
typedef struct { unsigned field, wire; uint64_t v; const uint8_t *s; size_t n; } pbf;
int pb_next(pbr *r, pbf *f);  /* 1 a field, 0 the end, -1 malformed */
/* the field's bytes as a NUL-terminated string in buf (truncated at cap - 1) */
void pb_cstr(const pbf *f, char *buf, size_t cap);

/* ---- sessions ---- */
#define CRC_PEER_ID 8
#define CRC_KEY 32
#define CRC_NAME 32
#define CRC_MAX_PEERS 8

typedef struct {
    uint8_t id[CRC_PEER_ID], ltk[CRC_KEY];
    char name[CRC_NAME];
    uint32_t created;
} crc_peer;

enum { S_NEW, S_HELLO, S_PAIRING, S_PAIRED, S_AUTHED, S_CLOSED };

typedef struct crc_session {
    int state, transport;  /* transport: platform's own tag */
    uint8_t sid[32];       /* client nonce then device nonce */
    uint8_t peer[CRC_PEER_ID], ltk[CRC_KEY], kc[CRC_KEY], kd[CRC_KEY];
    uint64_t rx_ctr, tx_ctr;
    uint8_t scalar[32], dpoint[32], gen[32];  /* the pairing in flight (S_PAIRING) */
    char client_name[CRC_NAME];
    int subscribed;
    uint8_t rx[CRC_MAX_FRAME];
    size_t rx_n;
    void *io;  /* the platform's connection */
    /* Through q2-iroh (iroh_via): iroh is the phone's endpoint key, as the sidecar named it, and iroh_ok says it is
     * a paired phone's (registered); a session with neither gets nothing before Auth but outside Pair a Phone's
     * code (docs/remote-protocol.md#across-networks-only-paired-phones). */
    uint8_t iroh[32];
    int iroh_via, iroh_ok;
} crc_session;

/* ---- what the platform provides (coda_rc.c; the host test's fakes) ---- */
void crc_random(void *buf, size_t n);
/* NowPlaying.buffered_ms: have of total bytes as a share of ms, 0 without a total or a length; in 32 bits */
uint32_t crc_buffered_ms(uint32_t have, uint32_t total, uint32_t ms);
uint32_t crc_now_ms(void);
uint32_t crc_now_s(void);  /* wall clock, for crc_peer.created */
int crc_write(crc_session *s, const uint8_t *buf, size_t n);  /* the whole frame, or -1 */
int crc_peers_load(crc_peer *peers, int cap);                  /* how many */
int crc_peers_save(const crc_peer *peers, int n);
void crc_pair_show(const char *code, const char *client_name); /* the Q2's pairing dialog */
void crc_pair_hide(int outcome);                               /* 1 paired, 0 failed, -1 timed out or cancelled */
int crc_pair_cancelled(void);                                  /* the user dismissed the dialog */
int crc_code_shown(char code[7]); /* 1 with the code Pair a Phone's QR code carries, 0 with none */
void crc_code_spent(void);        /* a pairing on that code ended: it is never taken again */
void crc_iroh_proved(crc_session *s); /* a session through q2-iroh proved its phone: its key is that phone's */
/* an authenticated request: answer with crc_send/crc_error; anything but 0 closes the session */
int crc_app(crc_session *s, int type, unsigned req_id, const uint8_t *p, size_t n);
extern uint8_t crc_device_id[8];

/* ---- the core (coda_rc_core.c) ---- */
void crc_open(crc_session *s, void *io, int transport);
/* bytes from the transport, in order; -1 when the session must close */
int crc_feed(crc_session *s, const uint8_t *p, size_t n);
int crc_send(crc_session *s, int type, unsigned req_id, const uint8_t *p, size_t n);
int crc_error(crc_session *s, unsigned req_id, int code, const char *message);
void crc_close(crc_session *s);
void crc_tick(void);  /* about once a second: expires a pairing code that was not used */
int crc_pair_busy(void); /* a pairing code is out (crc_tick still has to expire it) */
uint32_t crc_caps(void);
/* ---- the daemon's own parts (coda_rc.c, coda_rc_lib.c, coda_rc_ble.c) ---- */
const char *crc_path(char *buf, size_t cap, const char *path); /* path under --root */
int crc_write_file(const char *path, const void *buf, size_t n, unsigned mode);
void crc_log(const char *fmt, ...);
int crc_shim_alive(void);
int crc_shim_command(crc_session *s, unsigned req_id, const char *verb);
/* Browse, Search, GetQueue, GetCover and Play (coda_rc_lib.c); qgen and qpos: Now Playing's queue version and
 * place, newer than the queue file's */
int lib_request(crc_session *s, int type, unsigned req_id, const uint8_t *p, size_t n, int qgen, int qpos);
/* Play.resume's memory (coda_rc_lib.c): a list's Play was taken (ok) or not, its queue now qgen; Now Playing
 * now plays song id in queue qgen */
void lib_played(int ok, int qgen);
void lib_now(int qgen, int id);
void lib_art_of(const char *path, char *art, size_t cap); /* a song path's album art id */
/* the Browse nodes of a playing song's album and album artist (NowPlaying.album_id, artist_id), "" for none */
void lib_song_nodes(const char *track_id, char *album, size_t acap, char *artist, size_t rcap);
/* GetCover's answer (coda_rc_lib.c); hold: a server album with no cover on the card may be asked of its app first */
int lib_cover(crc_session *s, unsigned req_id, const char *art, unsigned offset, unsigned limit, unsigned max_px,
              int hold);
/* A car's cover the card lacks, asked of card app id (coda_rc.c, docs/internals.md#car-remote-covers): album's cover at
 * px (512 or 160). 1 when the daemon holds the GetCover's answer (it answers through lib_cover once the app has
 * answered, or after COVER_HOLD_MS); 0 when it doesn't, and NOT_FOUND goes now. */
int crc_cover_ask(crc_session *s, unsigned req_id, const char *art, unsigned limit, unsigned max_px, const char *id,
                  const char *album, unsigned px);
/* A streaming song's file (/mnt/mmc/.<app>/stream/.../<title> [<sid>].<suffix>) as the catalogue's server song */
typedef struct {
    int id, secs, track;
    char title[160], artist[160], album[160], art[48];
} lib_song_t;
int lib_stream_song(const char *path, lib_song_t *o);
int lib_song_rid(const char *id); /* "song:t12" -> 12; 0 when not a song id */
int lib_has_server(void);
int lib_queue_rid(unsigned at, int rid); /* PlayQueueItem: 0 for a queued stream download of server song rid */
/* BLE (coda_rc_ble.c, its own GATT server): ble_fds puts its sockets (at most BLE_FDS) into the loop's poll, and
 * ble_handle takes them back with their revents; ble_tick once a second, and when ble_due says the advertisement
 * is due (a phone gone: advertised again at once, at either of the loop's paces) */
#define BLE_FDS 3
struct pollfd;
void ble_start(void);
int ble_fds(struct pollfd *p);
void ble_handle(const struct pollfd *p, int n);
void ble_tick(uint32_t now_ms);
uint32_t ble_due(uint32_t now_ms); /* ms until the advertisement is due again (0: now), ~0 when none waits */
int ble_write(const uint8_t *p, size_t n);
void ble_disconnect(void);
void ble_stop(void);
#ifdef CODA_HOST
void ble_sim(const char *dir); /* --ble-sim: AF_UNIX sockets in dir stand in for the kernel's (the tests) */
#endif
void crc_ble_connected(void);
void crc_ble_bytes(const uint8_t *p, size_t n);
void crc_ble_gone(void);
void crc_ble_advertising(int on); /* /tmp/coda-rc.ble while the advertisement stands */
/* config.ini's [Q2POD] BTNAME when bt_name.c's bt_name_valid would take it, else "Q2 Muse"; out holds 31 */
void crc_bt_name(char *out);
int crc_tcp_port(void); /* the port TCP sessions are accepted on (CRC_TCP_PORT, or --port) */

/* ---- mDNS (coda_rc_mdns.c, docs/remote-protocol.md#discovery) ---- */
#define MDNS_TTL 120 /* seconds, every record */
enum { MD_PTR = 1, MD_SRV = 2, MD_TXT = 4, MD_A = 8, MD_ENUM = 16, MD_ALL = 31 };
typedef struct {
    uint8_t ip[4], id[8]; /* the address (network order) and crc_device_id */
    uint16_t port;
    char name[64]; /* the instance: the Bluetooth name */
    char host[64]; /* the host's label, before .local */
} mdns_self;
/* the records a packet asks for (MD_* bits), 0 none of ours or not a query, -1 malformed */
int mdns_query(const mdns_self *me, const uint8_t *p, size_t n);
/* a response with those records as answers (and what they lead to as additionals) at ttl into out; its size,
 * 0 when it does not fit */
size_t mdns_reply(const mdns_self *me, unsigned answers, uint32_t ttl, uint8_t *out, size_t cap);
void mdns_start(const char *ifname); /* answer on ifname's address whenever it has one */
int mdns_fd(void);                   /* the socket to poll for POLLIN, -1 with none */
void mdns_pace(int quiet);           /* the address looked at every 5 s while no phone is connected, else 1 s */
void mdns_poll(uint32_t now_ms);     /* every pass: the address and name followed, announcements sent */
uint32_t mdns_due(uint32_t now_ms);  /* ms until mdns_poll has something to do; 0xffffffff never */
void mdns_handle(void);              /* a packet waiting on mdns_fd */
void mdns_stop(void);                /* goodbye, and closed */

#define CRC_PAIR_TIMEOUT_MS 120000
#define CRC_PAIR_FAILS 3              /* wrong codes before pairing locks */
#define CRC_PAIR_LOCK_MS 300000
#define CRC_PAIR_SHOWS 4              /* new codes put up, at most, in any CRC_PAIR_SHOW_WINDOW_MS */
#define CRC_PAIR_SHOW_WINDOW_MS 60000
#define CRC_PING_PLAIN 64             /* the longest Ping echoed before Auth; sealed ones take CRC_MAX_PAYLOAD */

#endif
