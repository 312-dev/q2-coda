/* One file or stream, demuxed and decoded by FFmpeg and handed out as interleaved little-endian PCM.
 *
 * Nothing here changes a sample, and the width is the one stock opens its output with for the same file
 * (docs/player.md#output): 16-bit integers as S16, wider integers as S32, left justified as the decoder gives
 * them, and a float decoder's output as float. MPEG audio comes from FFmpeg's integer decoder, 16 bits as stock's
 * libmad. Encoder delay and padding (LAME's tag, iTunSMPB, an edit list, Opus pre-skip, Vorbis granules) are cut
 * by libavcodec from the side data libavformat attaches. */
#include "q2player.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/replaygain.h>
#include <math.h>

enum { AAC_FRAME = 1024 }; /* AAC LC's samples a packet; padding is less */
/* The most audio avformat_find_stream_info reads of a song still arriving (docs/internals.md#streaming-configuration).
 * Its default, 5 s, is reached whenever a stream's parameters stay unknown, and an embedded cover's always do here:
 * this FFmpeg has no JPEG or PNG decoder to size it (tools/player.py DECODERS). A whole file's 5 s is read at once,
 * but an arriving one's waits for them to come, so the song started only once 5 s of audio and the cover had. The
 * audio's own parameters are known from its first frames; at 100 ms the open reads what it would at none, its
 * parser's look ahead (about 1.3 s of a FLAC after its headers, measured with FFmpeg's flac demuxer, 2026-11-22). */
enum { GROW_ANALYZE_US = 100000 };

struct source {
    AVFormatContext *fmt;
    AVCodecContext *dec;
    AVPacket *pkt, *ahead; /* ahead: the packet after a short one, read to see whether the short one was the last */
    int have_ahead;
    int64_t end_pts; /* AAC in MP4: where the last packet's duration says the audio ends; AV_NOPTS_VALUE for none */
    AVFrame *frame;
    int stream, draining, done;
    struct pcm_format out;
    uint8_t *buf;
    size_t cap;
    int64_t frames;     /* handed out since the start or the last seek */
    int64_t base;       /* the frame the last seek aimed at */
    int64_t drop_until; /* after a seek: the first frame to hand out, by the stream's own count; -1 for none */
    const int *cancel;  /* nonzero: whoever opened this has given up on it, and a read in progress stops */
    /* An MP3's own frame count (mp3_count): where its audio ends, in frames from the stream's start, and the file
     * position after the newest packet decoded, where what follows the count is judged. limit -1: no count. */
    int64_t limit, next_pos;
    FILE *file;
    int aac_mp4; /* AAC from the mov demuxer, in a time base of one sample */
    /* DSD as it is, DoP or Native (dsd.c): packets are not decoded. drop_until is then in bytes of a channel, and
     * byte_rate a channel's bytes a second (the DSD rate over 8) */
    int raw, planar, lsb_first, byte_rate;
    struct dsd_pack pack;
    struct stream *io; /* a URL's or QQ Music's bytes (stream.c); NULL for a file libavformat opens itself */
    struct sacd *iso;  /* an SACD image's stereo area, as a DSDIFF file (sacd.c) */
    struct grow *grow; /* a card app's song still arriving (grow.c) */
};

static int interrupted(void *opaque) {
    const struct source *s = opaque;
    return s->cancel && __atomic_load_n(s->cancel, __ATOMIC_RELAXED);
}

void source_keep(struct source *s) {
    s->cancel = NULL;
    if (s->io) stream_keep(s->io);
}

struct stream *source_stream(const struct source *s) { return s->io; }

/* MPEG audio Layer III frame header at h: its length in bytes and samples, and its rate; 0 if it is not one. */
static int mp3_header(const uint8_t *h, int *bytes, int *samples, int *rate) {
    static const int rates[3] = {44100, 48000, 32000};
    static const short kbps[2][15] = {{0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320},
                                      {0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160}};
    if (h[0] != 0xff || (h[1] & 0xe0) != 0xe0) return 0;
    int version = (h[1] >> 3) & 3, layer = (h[1] >> 1) & 3, br = h[2] >> 4, sr = (h[2] >> 2) & 3;
    if (version == 1 || layer != 1 || br == 0 || br == 15 || sr == 3) return 0; /* reserved, not Layer III, free format */
    int mpeg1 = version == 3;
    *rate = rates[sr] >> (mpeg1 ? 0 : version == 2 ? 1 : 2);
    *samples = mpeg1 ? 1152 : 576;
    *bytes = (mpeg1 ? 144000 : 72000) * kbps[!mpeg1][br] / *rate + ((h[2] >> 1) & 1);
    return 1;
}

/* Two frames that chain at offset at, after an ID3v2 tag if one is there, with at least 4 KB of file left: real audio
 * (a stale count, or two files joined), not tags or bytes that only look like a sync now and then. */
static int frames_follow(FILE *f, int64_t at) {
    uint8_t h[10];
    int b1, s1, r1, b2, s2, r2;
    if (fseeko(f, at, SEEK_SET) || fread(h, 1, 10, f) != 10) return 0;
    if (!memcmp(h, "ID3", 3)) at += 10 + ((h[6] & 0x7f) << 21 | (h[7] & 0x7f) << 14 | (h[8] & 0x7f) << 7 | (h[9] & 0x7f)) + (h[5] & 0x10 ? 10 : 0);
    if (fseeko(f, 0, SEEK_END) || ftello(f) - at < 4096) return 0;
    if (fseeko(f, at, SEEK_SET) || fread(h, 1, 4, f) != 4 || !mp3_header(h, &b1, &s1, &r1)) return 0;
    if (fseeko(f, at + b1, SEEK_SET) || fread(h + 4, 1, 4, f) != 4 || !mp3_header(h + 4, &b2, &s2, &r2)) return 0;
    return r1 == r2 && ((h[1] ^ h[5]) & 0x1e) == 0; /* the same version, layer and rate */
}

/* The number of frames an MP3's Xing or Info header says it holds, less the encoder delay and padding its LAME tag
 * gives: the length FFmpeg cuts it to when it trusts the header. -1 when the first frame has no frame count.
 * FFmpeg stops trusting the count when the file is much longer than the header says ("invalid concatenated file"), and
 * then decodes whatever follows the audio: tags, a cover, junk (docs/internals.md#forced-eof). */
static int64_t mp3_count(FILE *f) {
    uint8_t b[512];
    int64_t at = 0;
    int bytes, samples, rate;
    if (fread(b, 1, 10, f) != 10) return -1;
    if (!memcmp(b, "ID3", 3)) at = 10 + ((b[6] & 0x7f) << 21 | (b[7] & 0x7f) << 14 | (b[8] & 0x7f) << 7 | (b[9] & 0x7f)) + (b[5] & 0x10 ? 10 : 0);
    if (fseeko(f, at, SEEK_SET) || fread(b, 1, sizeof b, f) != sizeof b || !mp3_header(b, &bytes, &samples, &rate)) return -1;
    int mpeg1 = ((b[1] >> 3) & 3) == 3, mono = (b[3] >> 6) == 3;
    int x = 4 + (mpeg1 ? (mono ? 17 : 32) : (mono ? 9 : 17));
    if (memcmp(b + x, "Xing", 4) && memcmp(b + x, "Info", 4)) return -1;
    uint32_t flags = (uint32_t)b[x + 4] << 24 | b[x + 5] << 16 | b[x + 6] << 8 | b[x + 7];
    if (!(flags & 1)) return -1;
    int64_t frames = (int64_t)b[x + 8] << 24 | b[x + 9] << 16 | b[x + 10] << 8 | b[x + 11];
    int lame = x + 12 + (flags & 2 ? 4 : 0) + (flags & 4 ? 100 : 0) + (flags & 8 ? 4 : 0);
    int delay = 0, padding = 0;
    if (lame + 24 <= (int)sizeof b && (!memcmp(b + lame, "LAME", 4) || !memcmp(b + lame, "Lavf", 4) || !memcmp(b + lame, "Lavc", 4))) {
        delay = b[lame + 21] << 4 | b[lame + 22] >> 4;
        padding = (b[lame + 22] & 15) << 8 | b[lame + 23];
    }
    return frames * samples - delay - padding;
}

/* The names stock's media-info uses (its table at 0xafb100), by codec and container. */
static const char *codec_name(const struct source *s) {
    const char *container = s->fmt->iformat->name;
    if (s->iso) return "ISO";
    switch (s->dec->codec_id) {
    case AV_CODEC_ID_MP2: case AV_CODEC_ID_MP1: return "MP2";
    case AV_CODEC_ID_MP3: return "MP3";
    case AV_CODEC_ID_AAC: return "AAC";
    case AV_CODEC_ID_APE: return "APE";
    case AV_CODEC_ID_VORBIS: return "OGG";
    case AV_CODEC_ID_ALAC: return "ALAC";
    case AV_CODEC_ID_DTS: return "DTS";
    case AV_CODEC_ID_AC3: return "AC2"; /* stock's table names AC-3 so (captured) */
    case AV_CODEC_ID_WAVPACK: return "WMA";                    /* stock names WavPack so (captured); its table has no entry */
    case AV_CODEC_ID_FLAC: return "FLAC";
    case AV_CODEC_ID_TAK: return "TAK";
    case AV_CODEC_ID_OPUS: return "OPUS";
    case AV_CODEC_ID_WMAV1: case AV_CODEC_ID_WMAV2: case AV_CODEC_ID_WMAPRO: case AV_CODEC_ID_WMALOSSLESS: return "WMA";
    default: break;
    }
    if (!strcmp(container, "aiff")) return "AIFF";
    if (!strcmp(container, "wav")) return "WAV";
    if (!strcmp(container, "dsf")) return "DSF";
    if (!strcmp(container, "iff")) return "DSD";
    return "UNK";
}

struct source *source_open(const struct song *song, int *error, const int *cancel) {
    struct source *s = calloc(1, sizeof *s);
    int r;
    *error = ERR_OTHER;
    if (!s) return NULL;
    s->drop_until = s->limit = -1;
    s->cancel = cancel;
    if (!(s->fmt = avformat_alloc_context())) goto fail;
    s->fmt->interrupt_callback = (AVIOInterruptCB){interrupted, s};
    if (stream_is_url(song->url) || strstr(song->url, "qm_songid=")) {
        if (!(s->io = stream_open(song, cancel, error))) goto fail;
        s->fmt->pb = stream_avio(s->io);
        s->fmt->flags |= AVFMT_FLAG_CUSTOM_IO;
    } else if (grow_arriving(song->url)) {
        if (!(s->grow = grow_open(song->url, error))) goto fail;
        s->fmt->pb = grow_avio(s->grow);
        s->fmt->flags |= AVFMT_FLAG_CUSTOM_IO;
        s->fmt->max_analyze_duration = GROW_ANALYZE_US;
    } else if (sacd_is_image(song->url)) {
        if (!(s->iso = sacd_open(song->url, error))) goto fail;
        s->fmt->pb = sacd_avio(s->iso);
        s->fmt->flags |= AVFMT_FLAG_CUSTOM_IO;
    }
    if ((r = avformat_open_input(&s->fmt, song->url, NULL, NULL)) < 0) {
        *error = r == AVERROR(ENOENT) ? ERR_MISSING : ERR_UNSUPPORTED;
        goto fail;
    }
    *error = ERR_UNSUPPORTED;
    if (avformat_find_stream_info(s->fmt, NULL) < 0) goto fail;
    const AVCodec *codec = NULL;
    s->stream = av_find_best_stream(s->fmt, AVMEDIA_TYPE_AUDIO, -1, -1, &codec, 0);
    if (s->stream < 0 || !codec) goto fail;
    AVStream *st = s->fmt->streams[s->stream];
    for (unsigned i = 0; i < s->fmt->nb_streams; i++)
        if ((int)i != s->stream) s->fmt->streams[i]->discard = AVDISCARD_ALL; /* cover art, chapters */
    if (!(s->dec = avcodec_alloc_context3(codec)) || avcodec_parameters_to_context(s->dec, st->codecpar) < 0) goto fail;
    s->dec->pkt_timebase = st->time_base; /* the decoder cuts delay and padding in these units */
    if (avcodec_open2(s->dec, codec, NULL) < 0) goto fail;
    if (!(s->pkt = av_packet_alloc()) || !(s->ahead = av_packet_alloc()) || !(s->frame = av_frame_alloc())) goto fail;
    s->end_pts = AV_NOPTS_VALUE;
    if (s->dec->sample_rate <= 0 || s->dec->ch_layout.nb_channels <= 0) goto fail;
    s->aac_mp4 = codec->id == AV_CODEC_ID_AAC && !strncmp(s->fmt->iformat->name, "mov,", 4) && st->time_base.num == 1 && st->time_base.den == s->dec->sample_rate;
    /* not an MP3 still arriving: its frames are counted from the whole file */
    if (codec->id == AV_CODEC_ID_MP3 && !s->grow && !strcmp(s->fmt->iformat->name, "mp3") && (s->file = fopen(song->url, "rb"))) {
        s->limit = mp3_count(s->file);
        if (s->limit < 0) fclose(s->file), s->file = NULL;
    }
    s->out.rate = s->dec->sample_rate;
    s->out.channels = s->dec->ch_layout.nb_channels;
    switch (av_get_packed_sample_fmt(s->dec->sample_fmt)) {
    case AV_SAMPLE_FMT_S32: s->out.bytes = 4; break;
    case AV_SAMPLE_FMT_U8: case AV_SAMPLE_FMT_S16: s->out.bytes = 2; break;
    case AV_SAMPLE_FMT_FLT: case AV_SAMPLE_FMT_DBL: s->out.bytes = 4, s->out.is_float = 1; break;
    default: goto fail;
    }
    enum AVCodecID id = codec->id;
    if (song->dsd && (id == AV_CODEC_ID_DSD_LSBF || id == AV_CODEC_ID_DSD_MSBF || id == AV_CODEC_ID_DSD_LSBF_PLANAR || id == AV_CODEC_ID_DSD_MSBF_PLANAR)) {
        /* DoP or Native: the decoder's rate is a channel's bytes a second; frames hold 2 or 4 of them */
        s->raw = 1, s->planar = id == AV_CODEC_ID_DSD_LSBF_PLANAR || id == AV_CODEC_ID_DSD_MSBF_PLANAR;
        s->lsb_first = id == AV_CODEC_ID_DSD_LSBF || id == AV_CODEC_ID_DSD_LSBF_PLANAR;
        s->byte_rate = s->dec->sample_rate;
        s->pack.mode = song->dsd, s->pack.channels = s->out.channels;
        if (s->out.channels > DSD_CHANNELS) goto fail;
        s->out.rate = s->byte_rate / dsd_unit(song->dsd), s->out.bytes = song->dsd == DSD_DOP ? 3 : 4, s->out.is_float = 0, s->out.dsd = song->dsd;
    }
    if (song->start > 0 && source_seek(s, song->start) < 0) goto fail;
    if (s->grow) grow_playing(s->grow); /* the open's own reads go on at any byte; a stall after waits for a refill */
    *error = ERR_NONE;
    return s;
fail:
    source_close(s);
    return NULL;
}

void source_close(struct source *s) {
    if (!s) return;
    av_frame_free(&s->frame);
    av_packet_free(&s->pkt);
    av_packet_free(&s->ahead);
    avcodec_free_context(&s->dec);
    avformat_close_input(&s->fmt); /* a stream's AVIOContext is its own, closed with it */
    stream_close(s->io);
    sacd_close(s->iso);
    grow_close(s->grow);
    if (s->file) fclose(s->file);
    free(s->buf);
    free(s->pack.buf);
    free(s);
}

const struct pcm_format *source_format(const struct source *s) { return &s->out; }
int64_t source_frames(const struct source *s) { return s->base + s->frames; }

void source_info(const struct source *s, struct info *i) {
    /* Stock reports whole bytes a second, times 8: the file's size over its length for what MPlayer's own demuxer
     * reads (MP3, FLAC, WAV), and the stream's declared rate for what libavformat reads (a Vorbis file's nominal
     * rate, not its average). */
    enum AVCodecID id = s->dec->codec_id;
    int own = id == AV_CODEC_ID_MP3 || id == AV_CODEC_ID_MP2 || id == AV_CODEC_ID_FLAC;
    int64_t rate = !own && s->dec->bit_rate > 0 ? s->dec->bit_rate : s->fmt->bit_rate > 0 ? s->fmt->bit_rate : s->dec->bit_rate;
    int64_t size = s->fmt->pb ? avio_size(s->fmt->pb) : -1;
    if (own && s->limit > 0 && size > 0) rate = size * 8 * s->out.rate / s->limit; /* an MP3 by its frame count: all of the file */
    memset(i, 0, sizeof *i);
    i->bitrate = (int)(rate / 8) * 8;
    i->rate = s->out.rate;
    i->length = s->limit >= 0 ? (int)(s->limit / s->out.rate) : s->fmt->duration > 0 ? (int)(s->fmt->duration / AV_TIME_BASE) : 0;
    i->bits = s->out.bytes == 2 ? 16 : !s->out.is_float && s->dec->bits_per_raw_sample > 0 ? s->dec->bits_per_raw_sample : 32;
    snprintf(i->codec, sizeof i->codec, "%s", codec_name(s));
    if (id == AV_CODEC_ID_DSD_LSBF || id == AV_CODEC_ID_DSD_MSBF || id == AV_CODEC_ID_DSD_LSBF_PLANAR || id == AV_CODEC_ID_DSD_MSBF_PLANAR) {
        /* as stock's (captured): BITS 1, RATE the DSD rate, BITRATE twice it sent as it is and four times as PCM. The
         * DAC's DoP halves RATE in stock; play() does that, knowing the output */
        int dsd = s->dec->sample_rate * 8;
        i->bits = 1, i->rate = dsd, i->bitrate = s->raw ? 2 * dsd : 4 * dsd;
    }
}

/* mcl-replaygain's scale for this file, as hciplayer's replaygain filter sets it up at each file (0x4282c0): mode 1
 * the track's gain, or the album's when there is no track gain; mode 2 the album's; any other mode none. The gain
 * in dB becomes a factor that is never above 1 / peak, a peak of 0 (none given) counting as 1, so without a peak a
 * gain only ever cuts; and a gain of exactly 1 dB is ignored, as stock compares it with 100000 (a quirk kept for
 * parity). The gains are FFmpeg's, from the REPLAYGAIN_* tags it reads (docs/internals.md#mp3-replaygain): only
 * when a tag is there, since FFmpeg also exports an MP3's LAME header gain, which stock never read. */
float source_replaygain(const struct source *s, int mode) {
    const AVStream *st = s->fmt->streams[s->stream];
    const AVPacketSideData *sd = av_packet_side_data_get(st->codecpar->coded_side_data, st->codecpar->nb_coded_side_data, AV_PKT_DATA_REPLAYGAIN);
    int tagged = 0;
    for (const char *const *k = (const char *const[]){"REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_ALBUM_GAIN", NULL}; *k; k++)
        tagged |= av_dict_get(s->fmt->metadata, *k, NULL, 0) || av_dict_get(st->metadata, *k, NULL, 0);
    if ((mode != 1 && mode != 2) || !sd || sd->size < sizeof(AVReplayGain) || !tagged) return 1;
    const AVReplayGain *rg = (const AVReplayGain *)sd->data;
    int32_t gain = mode == 1 && rg->track_gain != INT32_MIN ? rg->track_gain : rg->album_gain;
    uint32_t peak = mode == 1 && rg->track_gain != INT32_MIN ? rg->track_peak : rg->album_peak;
    if (gain == INT32_MIN || gain == 100000) return 1;
    double scale = exp(gain * 1e-5 * 0.05 * 2.302585092994046), most = 1 / ((peak ? peak : 100000) * 1e-5);
    return (float)(scale < most ? scale : most);
}

/* frame, from sample `skip` on, into buf, interleaved. */
static int convert(struct source *s, int skip) {
    const AVFrame *f = s->frame;
    int n = f->nb_samples - skip, ch = s->out.channels, planar = av_sample_fmt_is_planar(f->format);
    size_t need = (size_t)n * ch * s->out.bytes;
    if (n <= 0) return 0;
    if (need > s->cap) {
        uint8_t *b = realloc(s->buf, need);
        if (!b) return AVERROR(ENOMEM);
        s->buf = b, s->cap = need;
    }
#define EACH(TYPE, EXPR)                                                                    \
    for (int c = 0; c < ch; c++) {                                                          \
        const TYPE *in = (const TYPE *)f->extended_data[planar ? c : 0] + (planar ? skip : skip * ch + c); \
        for (int i = 0; i < n; i++) {                                                       \
            TYPE v = in[planar ? i : i * ch];                                               \
            out[i * ch + c] = EXPR;                                                         \
        }                                                                                   \
    }
    switch (av_get_packed_sample_fmt(f->format)) {
    case AV_SAMPLE_FMT_S16: { int16_t *out = (int16_t *)s->buf; EACH(int16_t, v) break; }
    case AV_SAMPLE_FMT_S32: { int32_t *out = (int32_t *)s->buf; EACH(int32_t, v) break; }
    case AV_SAMPLE_FMT_U8:  { int16_t *out = (int16_t *)s->buf; EACH(uint8_t, (int16_t)((v - 128) << 8)) break; }
    case AV_SAMPLE_FMT_FLT: { float *out = (float *)s->buf; EACH(float, v) break; }
    case AV_SAMPLE_FMT_DBL: { float *out = (float *)s->buf; EACH(double, (float)v) break; }
    default: return AVERROR(EINVAL);
    }
#undef EACH
    return n;
}

/* DoP or Native: the next packet's DSD made into frames (dsd.c), from the byte a seek aimed at */
static int raw_read(struct source *s, const uint8_t **data) {
    for (;;) {
        if (s->done || av_read_frame(s->fmt, s->pkt) < 0) return s->done = 1, 0;
        if (s->pkt->stream_index != s->stream) { av_packet_unref(s->pkt); continue; }
        int skip = 0, per = s->pkt->size / s->out.channels;
        if (s->drop_until >= 0) {
            AVStream *st = s->fmt->streams[s->stream];
            int64_t at = s->pkt->pts == AV_NOPTS_VALUE ? s->drop_until : av_rescale_q(s->pkt->pts, st->time_base, (AVRational){1, s->byte_rate});
            if (at + per <= s->drop_until) { av_packet_unref(s->pkt); continue; }
            if (at < s->drop_until) skip = (int)(s->drop_until - at);
            s->drop_until = -1;
        }
        int n = dsd_pack(&s->pack, s->pkt->data, s->pkt->size, s->planar, s->lsb_first, skip, data);
        av_packet_unref(s->pkt);
        if (n < 0) return n;
        if (n == 0) continue;
        s->frames += n;
        return n;
    }
}

int source_read(struct source *s, const uint8_t **data) {
    if (s->raw) return raw_read(s, data);
    for (;;) {
        int r = s->done ? AVERROR_EOF : avcodec_receive_frame(s->dec, s->frame);
        if (r == AVERROR_EOF) return s->done = 1, 0;
        if (r == AVERROR(EAGAIN)) {
            if (s->have_ahead) av_packet_move_ref(s->pkt, s->ahead), s->have_ahead = 0, r = 0;
            else r = av_read_frame(s->fmt, s->pkt);
            if (r < 0) { /* the end, or an error that reads as one: what was decoded still comes out */
                if (!s->draining) avcodec_send_packet(s->dec, NULL), s->draining = 1;
                else s->done = 1;
                continue;
            }
            if (s->pkt->stream_index != s->stream) { av_packet_unref(s->pkt); continue; }
            /* AAC in MP4 says its padding as the last packet's shorter duration (FFmpeg's muxer in stts, iTunes in
             * the iTunSMPB tag FFmpeg reads), and the decoder still gives the whole frame: the end is believed only
             * when no packet follows, as in Vamp's player (docs/internals.md#lossy-gapless-playback). */
            if (s->aac_mp4 && s->pkt->duration > 0 && s->pkt->duration < AAC_FRAME && s->pkt->pts != AV_NOPTS_VALUE) {
                int a;
                while ((a = av_read_frame(s->fmt, s->ahead)) >= 0 && s->ahead->stream_index != s->stream) av_packet_unref(s->ahead);
                if (a >= 0) s->have_ahead = 1;
                else s->end_pts = s->pkt->pts + s->pkt->duration;
            }
            if (avcodec_send_packet(s->dec, s->pkt) >= 0 && s->pkt->pos >= 0)
                s->next_pos = s->pkt->pos + s->pkt->size; /* a bad packet is skipped */
            av_packet_unref(s->pkt);
            continue;
        }
        if (r < 0) continue; /* a frame that would not decode: go on with the next */
        int skip = 0;
        if (s->drop_until >= 0) { /* a seek lands on a packet at or before its target: drop up to the target */
            int64_t at = s->frame->pts == AV_NOPTS_VALUE ? s->drop_until
                       : av_rescale_q(s->frame->pts, s->fmt->streams[s->stream]->time_base, (AVRational){1, s->out.rate});
            if (at + s->frame->nb_samples <= s->drop_until) { av_frame_unref(s->frame); continue; }
            if (at < s->drop_until) skip = (int)(s->drop_until - at);
            s->drop_until = -1;
        }
        if (s->end_pts != AV_NOPTS_VALUE && s->frame->pts != AV_NOPTS_VALUE && s->frame->pts + s->frame->nb_samples > s->end_pts) {
            int64_t keep = s->end_pts - s->frame->pts;
            if (keep <= skip) { av_frame_unref(s->frame); continue; }
            s->frame->nb_samples = (int)keep;
        }
        if (s->limit >= 0 && source_frames(s) + s->frame->nb_samples - skip > s->limit) {
            /* The frame count ends here unless more frames follow it, as in two files joined or a stale count:
             * then the rest plays and nothing is counted again. */
            if (frames_follow(s->file, s->next_pos)) s->limit = -1;
            else if (source_frames(s) >= s->limit) { av_frame_unref(s->frame); return s->done = 1, 0; }
            else s->frame->nb_samples = (int)(s->limit - source_frames(s)) + skip;
        }
        int n = convert(s, skip);
        av_frame_unref(s->frame);
        if (n < 0) return n;
        if (n == 0) continue;
        s->frames += n;
        *data = s->buf;
        return n;
    }
}

int source_seek(struct source *s, int seconds) { return source_seek_frame(s, (int64_t)seconds * s->out.rate); }

/* To frame target of the output's rate: the decode resumes at the packet before it and drops up to it. */
int source_seek_frame(struct source *s, int64_t target) {
    AVStream *st = s->fmt->streams[s->stream];
    int64_t ts = av_rescale_q(target, (AVRational){1, s->out.rate}, st->time_base);
    if (st->start_time != AV_NOPTS_VALUE) ts += st->start_time;
    /* a song still arriving: the search sees only the bytes there are, and a target past them is read up to (grow.c) */
    if (s->grow) grow_seeking(s->grow, 1);
    int r = av_seek_frame(s->fmt, s->stream, ts, AVSEEK_FLAG_BACKWARD);
    if (s->grow) grow_seeking(s->grow, 0);
    if (r < 0) return -1;
    avcodec_flush_buffers(s->dec);
    av_packet_unref(s->ahead);
    s->draining = s->done = s->have_ahead = 0;
    s->end_pts = AV_NOPTS_VALUE;
    s->frames = 0;
    s->base = target;
    s->drop_until = st->start_time != AV_NOPTS_VALUE ? target + av_rescale_q(st->start_time, st->time_base, (AVRational){1, s->out.rate}) : target;
    if (s->raw) s->drop_until = target * s->byte_rate / s->out.rate, dsd_reset(&s->pack); /* in a channel's bytes */
    return 0;
}
