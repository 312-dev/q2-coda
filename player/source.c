/* One file or stream, demuxed and decoded by FFmpeg and handed out as interleaved little-endian PCM.
 *
 * Nothing here changes a sample: integer decoders' output is copied (16-bit as S16, anything wider as S32, left
 * justified as the decoder gives it), and a float decoder's is rounded to 16 bits the way `ffmpeg -f s16le` does,
 * which is the width stock's lossy decoders put out. Encoder delay and padding (LAME's tag, iTunSMPB, an edit
 * list, Opus pre-skip, Vorbis granules) are cut by libavcodec from the side data libavformat attaches. */
#include "q2player.h"
#include <errno.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>

struct source {
    AVFormatContext *fmt;
    AVCodecContext *dec;
    AVPacket *pkt;
    AVFrame *frame;
    int stream, draining, done;
    struct pcm_format out;
    uint8_t *buf;
    size_t cap;
    int64_t frames;     /* handed out since the start or the last seek */
    int64_t base;       /* the frame the last seek aimed at */
    int64_t drop_until; /* after a seek: the first frame to hand out, by the stream's own count; -1 for none */
};

/* The names stock's media-info uses (its table at 0xafb100), by codec and container. */
static const char *codec_name(const struct source *s) {
    const char *container = s->fmt->iformat->name;
    switch (s->dec->codec_id) {
    case AV_CODEC_ID_MP2: case AV_CODEC_ID_MP1: return "MP2";
    case AV_CODEC_ID_MP3: return "MP3";
    case AV_CODEC_ID_AAC: case AV_CODEC_ID_AAC_LATM: return "AAC";
    case AV_CODEC_ID_APE: return "APE";
    case AV_CODEC_ID_VORBIS: return "OGG";
    case AV_CODEC_ID_ALAC: return "ALAC";
    case AV_CODEC_ID_DTS: return "DTS";
    case AV_CODEC_ID_FLAC: return "FLAC";
    case AV_CODEC_ID_TAK: return "TAK";
    case AV_CODEC_ID_OPUS: return "OPUS";
    case AV_CODEC_ID_WMAV1: case AV_CODEC_ID_WMAV2: case AV_CODEC_ID_WMAPRO: case AV_CODEC_ID_WMALOSSLESS: return "WMA";
    default: break;
    }
    if (!strcmp(container, "aiff")) return "AIFF";
    if (!strcmp(container, "wav") || !strcmp(container, "w64")) return "WAV";
    if (!strcmp(container, "dsf")) return "DSF";
    if (!strcmp(container, "iff")) return "DSD";
    return "UNK";
}

struct source *source_open(const struct song *song, int *error) {
    struct source *s = calloc(1, sizeof *s);
    int r;
    *error = ERR_OTHER;
    if (!s) return NULL;
    s->drop_until = -1;
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
    if (!(s->pkt = av_packet_alloc()) || !(s->frame = av_frame_alloc())) goto fail;
    if (s->dec->sample_rate <= 0 || s->dec->ch_layout.nb_channels <= 0) goto fail;
    s->out.rate = s->dec->sample_rate;
    s->out.channels = s->dec->ch_layout.nb_channels;
    switch (av_get_packed_sample_fmt(s->dec->sample_fmt)) {
    case AV_SAMPLE_FMT_S32: s->out.bytes = 4; break;
    case AV_SAMPLE_FMT_U8: case AV_SAMPLE_FMT_S16: case AV_SAMPLE_FMT_FLT: case AV_SAMPLE_FMT_DBL: s->out.bytes = 2; break;
    default: goto fail;
    }
    if (song->start > 0 && source_seek(s, song->start) < 0) goto fail;
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
    avcodec_free_context(&s->dec);
    avformat_close_input(&s->fmt);
    free(s->buf);
    free(s);
}

const struct pcm_format *source_format(const struct source *s) { return &s->out; }
int64_t source_frames(const struct source *s) { return s->base + s->frames; }

void source_info(const struct source *s, struct info *i) {
    int64_t rate = s->fmt->bit_rate > 0 ? s->fmt->bit_rate : s->dec->bit_rate;
    memset(i, 0, sizeof *i);
    i->bitrate = (int)(rate / 8) * 8; /* stock reports whole bytes a second, times 8 */
    i->rate = s->out.rate;
    i->length = s->fmt->duration > 0 ? (int)(s->fmt->duration / AV_TIME_BASE) : 0;
    i->bits = s->out.bytes == 2 ? 16 : s->dec->bits_per_raw_sample > 0 ? s->dec->bits_per_raw_sample : 32;
    snprintf(i->codec, sizeof i->codec, "%s", codec_name(s));
}

static inline int16_t round16(double v) {
    long x = lrint(v * 32768.0);
    return x > 32767 ? 32767 : x < -32768 ? -32768 : (int16_t)x;
}

/* frame, from sample `skip` on, into buf as interleaved S16 or S32. */
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
    case AV_SAMPLE_FMT_FLT: { int16_t *out = (int16_t *)s->buf; EACH(float, (int16_t)av_clip_int16(lrintf(v * 32768.0f))) break; }
    case AV_SAMPLE_FMT_DBL: { int16_t *out = (int16_t *)s->buf; EACH(double, round16(v)) break; }
    default: return AVERROR(EINVAL);
    }
#undef EACH
    return n;
}

int source_read(struct source *s, const uint8_t **data) {
    for (;;) {
        int r = s->done ? AVERROR_EOF : avcodec_receive_frame(s->dec, s->frame);
        if (r == AVERROR_EOF) return s->done = 1, 0;
        if (r == AVERROR(EAGAIN)) {
            r = av_read_frame(s->fmt, s->pkt);
            if (r < 0) { /* the end, or an error that reads as one: what was decoded still comes out */
                if (!s->draining) avcodec_send_packet(s->dec, NULL), s->draining = 1;
                else s->done = 1;
                continue;
            }
            if (s->pkt->stream_index == s->stream) avcodec_send_packet(s->dec, s->pkt); /* a bad packet is skipped */
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
        int n = convert(s, skip);
        av_frame_unref(s->frame);
        if (n < 0) return n;
        if (n == 0) continue;
        s->frames += n;
        *data = s->buf;
        return n;
    }
}

int source_seek(struct source *s, int seconds) {
    AVStream *st = s->fmt->streams[s->stream];
    int64_t target = (int64_t)seconds * s->out.rate;
    int64_t ts = av_rescale_q(target, (AVRational){1, s->out.rate}, st->time_base);
    if (st->start_time != AV_NOPTS_VALUE) ts += st->start_time;
    if (av_seek_frame(s->fmt, s->stream, ts, AVSEEK_FLAG_BACKWARD) < 0) return -1;
    avcodec_flush_buffers(s->dec);
    s->draining = s->done = 0;
    s->frames = 0;
    s->base = target;
    s->drop_until = st->start_time != AV_NOPTS_VALUE ? target + av_rescale_q(st->start_time, st->time_base, (AVRational){1, s->out.rate}) : target;
    return 0;
}
