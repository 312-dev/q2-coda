/* An SACD image (docs/player.md#sacd-iso): its stereo area's DSD, presented to libavformat as a DSDIFF file, so that
 * FFmpeg's iff demuxer and DSD decoders play it as any .dff, DoP, Native and D2P alike. The image is read as the
 * Scarletbook says and sacd-ripper's libsacdread reads it (scarletbook.h, scarletbook_read.c): the master TOC at
 * sector 510 names the stereo area's TOC, which gives its frame format, channels and first and last sectors; each
 * audio sector is a header byte, its packets' lengths, the time codes of the frames that start in it, then the
 * packets, and a frame is the audio packets from one marked as a frame's start to the next. Plain DSD alone, as stock:
 * its demuxer refuses an area in DST (captured, frame format 0). Plain DSD comes three frames to each 14 or 16
 * sectors, so frame k is found from sector (k / 3) x 14 or 16 of the area without reading what is before it. */
#include "q2player.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <libavformat/avformat.h>

enum { SECTOR = 2048, MASTER_TOC = 510, FRAME_BYTES = 4704 /* a channel's, 1/75 s at DSD64 */, IO_BUF = 32768 };

struct sacd {
    FILE *f;
    AVIOContext *io;
    uint32_t first, last; /* the area's audio sectors */
    int channels, per_group, frames;
    int frame_size;       /* channels x FRAME_BYTES */
    uint8_t head[192];    /* the DSDIFF header before the DSD chunk's data */
    int head_len;
    int64_t pos;          /* where libavformat reads in the DSDIFF file */
    int64_t have;         /* the frame in buf, -1 for none */
    uint8_t *buf, sector[SECTOR];
};

static uint32_t be32(const uint8_t *p) { return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }

static int read_sector(struct sacd *s, uint32_t lsn, uint8_t *to) {
    return fseeko(s->f, (off_t)lsn * SECTOR, SEEK_SET) == 0 && fread(to, 1, SECTOR, s->f) == SECTOR ? 0 : -1;
}

/* Frame k into buf: from the first sector of its group of three, the frames' audio packets gathered in order. */
static int load_frame(struct sacd *s, int64_t k) {
    uint32_t lsn = s->first + (uint32_t)(k / 3) * s->per_group;
    int64_t want = k % 3, at = -1; /* at: which frame of the group is being gathered, -1 before the first start */
    int size = 0;
    for (; lsn <= s->last; lsn++) {
        if (read_sector(s, lsn, s->sector)) return -1;
        const uint8_t *p = s->sector;
        int packets = p[0] >> 5, starts = (p[0] >> 2) & 7, dst = p[0] & 1;
        if (dst) return -1;
        const uint8_t *info = p + 1, *data = info + 2 * packets + 3 * starts;
        for (int i = 0; i < packets; i++, info += 2) {
            int start = info[0] >> 7, type = (info[0] >> 3) & 7, n = (info[0] & 7) << 8 | info[1];
            if (data + n > s->sector + SECTOR) return -1;
            if (type == 2 && start) {
                if (at == want) return size == s->frame_size ? 0 : -1;
                at++, size = 0;
            }
            if (type == 2 && at == want) {
                if (size + n > s->frame_size) return -1;
                memcpy(s->buf + size, data, n), size += n;
            }
            data += n;
        }
        if (at == want && size == s->frame_size && lsn == s->last) return 0;
    }
    return at == want && size == s->frame_size ? 0 : -1;
}

static int sacd_read(void *opaque, uint8_t *to, int n) {
    struct sacd *s = opaque;
    int done = 0;
    while (done < n) {
        if (s->pos < s->head_len) {
            int c = (int)(s->head_len - s->pos) < n - done ? (int)(s->head_len - s->pos) : n - done;
            memcpy(to + done, s->head + s->pos, c), s->pos += c, done += c;
            continue;
        }
        int64_t k = (s->pos - s->head_len) / s->frame_size, off = (s->pos - s->head_len) % s->frame_size;
        if (k >= s->frames) break;
        if (k != s->have) {
            if (load_frame(s, k)) {
                fprintf(stderr, "q2player: SACD frame %lld unreadable\n", (long long)k);
                break;
            }
            s->have = k;
        }
        int c = (int)(s->frame_size - off) < n - done ? (int)(s->frame_size - off) : n - done;
        memcpy(to + done, s->buf + off, c), s->pos += c, done += c;
    }
    return done ? done : AVERROR_EOF;
}

static int64_t sacd_seek(void *opaque, int64_t offset, int whence) {
    struct sacd *s = opaque;
    int64_t size = s->head_len + (int64_t)s->frames * s->frame_size;
    if (whence & AVSEEK_SIZE) return size;
    whence &= ~AVSEEK_FORCE;
    if (whence == SEEK_CUR) offset += s->pos;
    else if (whence == SEEK_END) offset += size;
    else if (whence != SEEK_SET) return AVERROR(EINVAL);
    if (offset < 0) return AVERROR(EINVAL);
    return s->pos = offset;
}

static uint8_t *chunk(uint8_t *p, const char *id, uint64_t size) {
    memcpy(p, id, 4);
    for (int i = 0; i < 8; i++) p[4 + i] = (uint8_t)(size >> (56 - 8 * i));
    return p + 12;
}

int sacd_is_image(const char *path) {
    size_t n = strlen(path);
    return n > 4 && !strcasecmp(path + n - 4, ".iso");
}

struct sacd *sacd_open(const char *path, int *error) {
    struct sacd *s = calloc(1, sizeof *s);
    uint8_t toc[SECTOR];
    uint8_t *buf = NULL;
    *error = ERR_MISSING;
    if (!s || !(s->f = fopen(path, "rb"))) goto fail;
    *error = ERR_UNSUPPORTED;
    if (read_sector(s, MASTER_TOC, toc) || memcmp(toc, "SACDMTOC", 8)) goto fail;
    uint32_t area = be32(toc + 64); /* the stereo area's TOC */
    if (!area || read_sector(s, area, toc) || memcmp(toc, "TWOCHTOC", 8)) goto fail;
    int format = toc[21] & 15;
    s->channels = toc[32], s->first = be32(toc + 72), s->last = be32(toc + 76);
    s->per_group = format == 2 ? 14 : format == 3 ? 16 : 0;
    if (!s->per_group || toc[20] != 4 || s->channels < 1 || s->channels > 6 || s->last < s->first) {
        fprintf(stderr, "q2player: SACD area: frame format %d, %d channels, rate code %d: not plain DSD64\n", format, s->channels, toc[20]);
        goto fail;
    }
    s->frames = (toc[64] * 60 + toc[65]) * 75 + toc[66]; /* the area's play time */
    s->frame_size = s->channels * FRAME_BYTES;
    s->have = -1;
    if (!(s->buf = malloc(s->frame_size))) goto fail;
    /* FRM8 { DSD, FVER, PROP { SND, FS, CHNL, CMPR }, DSD } as a .dff of the area's DSD would begin */
    static const char *const names[] = {"SLFT", "SRGT", "C   ", "LFE ", "LS  ", "RS  "};
    uint8_t prop[128], *q = prop;
    memcpy(q, "SND ", 4), q += 4;
    q = chunk(q, "FS  ", 4), q[0] = 0, q[1] = 0x2b, q[2] = 0x11, q[3] = 0, q += 4; /* 2,822,400 */
    q = chunk(q, "CHNL", 2 + 4 * s->channels), q[0] = 0, q[1] = (uint8_t)s->channels, q += 2;
    for (int c = 0; c < s->channels; c++) memcpy(q, names[c], 4), q += 4;
    q = chunk(q, "CMPR", 4 + 1 + 14 + 1), memcpy(q, "DSD \x0enot compressed\0", 20), q += 20;
    int64_t data = (int64_t)s->frames * s->frame_size;
    uint8_t *h = s->head;
    h = chunk(h, "FRM8", 4 + 16 + 12 + (q - prop) + 12 + data), memcpy(h, "DSD ", 4), h += 4;
    h = chunk(h, "FVER", 4), memcpy(h, "\x01\x05\x00\x00", 4), h += 4;
    h = chunk(h, "PROP", q - prop), memcpy(h, prop, q - prop), h += q - prop;
    h = chunk(h, "DSD ", data);
    s->head_len = (int)(h - s->head);
    if (!(buf = av_malloc(IO_BUF)) || !(s->io = avio_alloc_context(buf, IO_BUF, 0, s, sacd_read, NULL, sacd_seek))) {
        av_free(buf);
        goto fail;
    }
    *error = ERR_NONE;
    return s;
fail:
    sacd_close(s);
    return NULL;
}

struct AVIOContext *sacd_avio(struct sacd *s) { return s->io; }

void sacd_close(struct sacd *s) {
    if (!s) return;
    if (s->io) av_freep(&s->io->buffer), avio_context_free(&s->io);
    if (s->f) fclose(s->f);
    free(s->buf);
    free(s);
}
