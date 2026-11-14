/* DSD sent as it is, for a DAC that takes it (docs/player.md#output): DoP, the bits carried in 24-bit PCM frames,
 * or Native, in one of ALSA's DSD formats. D2P, DSD made PCM, is FFmpeg's DSD decoder in source.c and not here.
 *
 * The packets are FFmpeg's, as its demuxers give them: a DSF's planar and least significant bit first (a block of
 * each channel in turn), a DSDIFF's interleaved a byte at a time and most significant bit first. Either becomes each
 * channel's bytes in the order they are heard, first bit in the most significant place, and then frames:
 *   DoP     a frame a channel holds 16 DSD bits under a marker byte, 0x05 and 0xFA in turn from frame to frame
 *           (DoP 1.1), as S24_3LE, as stock writes it (captured): the second byte heard, the first, the marker;
 *           frames at the DSD rate over 16
 *   Native  DSD_U32_BE: 32 bits a channel a frame, the first heard in the first byte: frames at the DSD rate over 32
 * A packet need not hold whole frames: what is left of each channel waits for the next. */
#include "q2player.h"
#include <stdlib.h>
#include <string.h>

static uint8_t reverse(uint8_t b) {
    b = (uint8_t)((b & 0xf0) >> 4 | (b & 0x0f) << 4);
    b = (uint8_t)((b & 0xcc) >> 2 | (b & 0x33) << 2);
    return (uint8_t)((b & 0xaa) >> 1 | (b & 0x55) << 1);
}

int dsd_unit(int mode) { return mode == DSD_DOP ? 2 : 4; }

void dsd_reset(struct dsd_pack *p) { p->left = 0, p->frame = 0; }

int dsd_pack(struct dsd_pack *p, const uint8_t *data, int bytes, int planar, int lsb_first, int skip, const uint8_t **out) {
    int ch = p->channels, unit = dsd_unit(p->mode);
    if (ch <= 0 || ch > DSD_CHANNELS) return 0;
    int per = bytes / ch;                 /* this packet's bytes of each channel */
    if (skip > per) skip = per;
    int have = p->left + per - skip;      /* with what waited from the packet before */
    int frames = have / unit;
    int size = p->mode == DSD_DOP ? 3 : 4; /* bytes a sample */
    size_t need = (size_t)frames * ch * size;
    if (need > p->cap) {
        uint8_t *b = realloc(p->buf, need);
        if (!b) return -1;
        p->buf = b, p->cap = need;
    }
    for (int c = 0; c < ch; ++c) {
        /* channel c's byte k of this packet, in time order */
#define BYTE(k) (planar ? data[c * per + (k)] : data[(k) * ch + c])
        int k = skip;
        for (int f = 0; f < frames; ++f) {
            uint8_t b[4];
            for (int j = 0; j < unit; ++j) {
                int at = f * unit + j; /* of what this channel has, the waiting bytes first */
                uint8_t v = at < p->left ? p->wait[c][at] : BYTE(k++);
                b[j] = lsb_first && at >= p->left ? reverse(v) : v;
            }
            uint8_t *o = p->buf + ((size_t)f * ch + c) * size;
            if (p->mode == DSD_DOP) o[0] = b[1], o[1] = b[0], o[2] = (p->frame + f) & 1 ? 0xfa : 0x05;
            else memcpy(o, b, 4); /* DSD_U32_BE: the first heard first in memory */
        }
        /* what is left of channel c waits, in time order */
        int rest = have - frames * unit, kept = 0;
        uint8_t keep[4];
        for (int at = frames * unit; at < have; ++at) {
            uint8_t v = at < p->left ? p->wait[c][at] : BYTE(k++);
            keep[kept++] = lsb_first && at >= p->left ? reverse(v) : v;
        }
        memcpy(p->wait[c], keep, (size_t)rest);
#undef BYTE
    }
    p->left = have - frames * unit;
    p->frame += frames;
    *out = p->buf;
    return frames;
}
