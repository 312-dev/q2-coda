/* q2-bip: the cover a car shows over Bluetooth (docs/internals.md#bluetooth-cover-art). AVRCP 1.6
 * names the playing song's cover by a 7-digit image handle (attribute 8, tools/bt_meta.c) and has the
 * controller fetch it from a BIP imaging responder over OBEX on an L2CAP channel of its own. This is
 * that responder: q2bip_core.c is OBEX and BIP with no reads of the device (tested on this machine by
 * test/unit/test_q2bip.py), q2bip.c the socket, the cover's file and the process. */
#ifndef Q2BIP_H
#define Q2BIP_H

#define BIP_PSM 0x1025 /* the L2CAP PSM in bluetoothd's AVRCP target record (BT_COVER_PSM, tools/build.py) */
/* The largest OBEX packet either way: with L2CAP's and ERTM's headers one packet is one of the
 * controller's 1021-byte ACL buffers, so a cover never holds more than one of the five beside the
 * A2DP stream. */
#define BIP_PKT 1000
#define BIP_PKT_MIN 255 /* OBEX's least */
#define BIP_HANDLE 7    /* an image handle's digits */
#define BIP_SIDE 200    /* AVRCP 1.6's thumbnail, the one version served: 200 x 200, JPEG */

/* OBEX opcodes and response codes (IrOBEX 1.5), the final bit set where it always is */
enum {
    OBEX_CONNECT = 0x80,
    OBEX_DISCONNECT = 0x81,
    OBEX_GET = 0x03,
    OBEX_FINAL = 0x80,
    OBEX_ABORT = 0xff,
    OBEX_CONTINUE = 0x90,
    OBEX_SUCCESS = 0xa0,
    OBEX_BAD_REQUEST = 0xc0,
    OBEX_NOT_FOUND = 0xc4,
    OBEX_NOT_ACCEPTABLE = 0xc6,
    OBEX_NOT_IMPLEMENTED = 0xd1,
    OBEX_UNAVAILABLE = 0xd3,
};
/* OBEX headers: the top two bits say how the value is carried */
enum {
    OBEX_H_HANDLE = 0x30, /* BIP's Img-Handle, UTF-16BE with its NUL */
    OBEX_H_TYPE = 0x42,
    OBEX_H_TARGET = 0x46,
    OBEX_H_BODY = 0x48,
    OBEX_H_END = 0x49,
    OBEX_H_WHO = 0x4a,
    OBEX_H_DESCRIPTOR = 0x71, /* BIP's Img-Descriptor, XML */
    OBEX_H_LENGTH = 0xc3,
    OBEX_H_CONNECTION = 0xcb,
};

enum { BIP_NONE, BIP_PROPERTIES, BIP_IMAGE, BIP_THUMBNAIL };

struct bip {
    /* The cover for handle (BIP_HANDLE digits, NUL-terminated): 1 with *data and *len, which stay
     * good until the next call; 0 for a handle that is not the playing song's or has no picture. */
    int (*image)(void *ctx, const char *handle, const unsigned char **data, unsigned *len);
    void *ctx;
    int connected, close; /* an OBEX session is up; the peer disconnected: close the channel */
    unsigned peer_max;    /* the peer's largest OBEX packet */
    /* the GET being asked (headers may come over several packets) or answered */
    int kind, sending, described; /* described: an image descriptor that this cover does not fit */
    char handle[BIP_HANDLE + 1];
    const unsigned char *body;
    unsigned body_len, body_at;
    char xml[160]; /* GetImageProperties' answer, the body while kind is BIP_PROPERTIES */
};

/* One OBEX request packet in (n bytes, whole), its response into out (cap at least BIP_PKT);
 * the response's length. */
unsigned bip_feed(struct bip *b, const unsigned char *in, unsigned n, unsigned char *out);
/* Whether an image descriptor's XML (n bytes) is met by a 200 x 200 JPEG */
int bip_descriptor_fits(const char *xml, unsigned n);
/* Cover Flow's key as an image handle: BIP_HANDLE decimal digits and a NUL */
void bip_handle(unsigned key, char *out);

#endif
