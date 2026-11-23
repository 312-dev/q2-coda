/* References made with the player's own FFmpeg (tools/player.py's host build), by none of the player's code:
 *
 *   ffref decode FILE              the file's decode as interleaved float, FFmpeg's sample format made packed
 *   ffref resample FILE RATE FMT [FILTER CUTOFF]
 *                                  a 16-bit or wider integer file to RATE as libswresample does it with MPlayer's
 *                                  lavcresample settings (filter 16, phase shift 10, cutoff 0.8), or with FILTER and
 *                                  CUTOFF (a USB DAC's: 32, 0.97), in float, written as FMT: s16 (lrintf of x * 32768,
 *                                  saturated) or f32; what the resampler holds back at the end is flushed
 *
 * to standard output. A reference for the DSD decoders (the system's ffmpeg is another version, whose DSD decoder
 * differs in the last place) and for Bluetooth's and a USB DAC's resampler. */
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/opt.h>
#include <libswresample/swresample.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static SwrContext *swr;
static int rate_out, s16;

static void put(const float *x, int n) {
    if (!s16) { fwrite(x, 4, (size_t)n, stdout); return; }
    for (int i = 0; i < n; ++i) {
        float v = x[i] * 32768.0f;
        int16_t w = v >= 32767.0f ? 32767 : v <= -32768.0f ? -32768 : (int16_t)lrintf(v);
        fwrite(&w, 2, 1, stdout);
    }
}

static void out(const float *x, int frames, int ch) {
    if (!swr) { put(x, frames * ch); return; }
    int room = swr_get_out_samples(swr, frames);
    float *y = malloc((size_t)(room > 0 ? room : 1) * ch * 4);
    int n = swr_convert(swr, (uint8_t **)&y, room, x ? (const uint8_t **)&x : NULL, frames);
    if (n > 0) put(y, n * ch);
    free(y);
}

static void frame_out(const AVFrame *f, int ch) {
    float *x = malloc((size_t)f->nb_samples * ch * 4);
    int planar = av_sample_fmt_is_planar(f->format);
    for (int c = 0; c < ch; ++c)
        for (int i = 0; i < f->nb_samples; ++i) {
            int j = planar ? i : i * ch + c;
            const uint8_t *d = f->extended_data[planar ? c : 0];
            float v;
            switch (av_get_packed_sample_fmt(f->format)) {
            case AV_SAMPLE_FMT_S16: v = ((const int16_t *)d)[j] * (1.0f / 32768); break;
            case AV_SAMPLE_FMT_S32: v = (float)(((const int32_t *)d)[j] * (1.0 / 2147483648.0)); break;
            case AV_SAMPLE_FMT_DBL: v = (float)((const double *)d)[j]; break;
            default: v = ((const float *)d)[j]; break;
            }
            x[i * ch + c] = v;
        }
    out(x, f->nb_samples, ch);
    free(x);
}

int main(int argc, char **argv) {
    AVFormatContext *fmt = NULL;
    const AVCodec *codec = NULL;
    if (argc < 3 || (strcmp(argv[1], "decode") && (strcmp(argv[1], "resample") || (argc != 5 && argc != 7)))) {
        fprintf(stderr, "usage: ffref decode FILE | ffref resample FILE RATE s16|f32 [FILTER CUTOFF]\n");
        return 2;
    }
    if (avformat_open_input(&fmt, argv[2], NULL, NULL) < 0 || avformat_find_stream_info(fmt, NULL) < 0) return 1;
    int st = av_find_best_stream(fmt, AVMEDIA_TYPE_AUDIO, -1, -1, &codec, 0);
    if (st < 0) return 1;
    AVCodecContext *dec = avcodec_alloc_context3(codec);
    avcodec_parameters_to_context(dec, fmt->streams[st]->codecpar);
    dec->pkt_timebase = fmt->streams[st]->time_base;
    if (avcodec_open2(dec, codec, NULL) < 0) return 1;
    int ch = dec->ch_layout.nb_channels;
    if (!strcmp(argv[1], "resample")) {
        rate_out = atoi(argv[3]), s16 = !strcmp(argv[4], "s16");
        AVChannelLayout l;
        av_channel_layout_default(&l, ch);
        swr_alloc_set_opts2(&swr, &l, AV_SAMPLE_FMT_FLT, rate_out, &l, AV_SAMPLE_FMT_FLT, dec->sample_rate, 0, NULL);
        av_opt_set_int(swr, "filter_size", argc == 7 ? atoi(argv[5]) : 16, 0);
        av_opt_set_int(swr, "phase_shift", 10, 0);
        av_opt_set_int(swr, "linear_interp", 0, 0);
        av_opt_set_double(swr, "cutoff", argc == 7 ? atof(argv[6]) : 0.8, 0);
        if (swr_init(swr) < 0) return 1;
    }
    AVPacket *pkt = av_packet_alloc();
    AVFrame *f = av_frame_alloc();
    while (av_read_frame(fmt, pkt) >= 0) {
        if (pkt->stream_index == st && avcodec_send_packet(dec, pkt) >= 0)
            while (avcodec_receive_frame(dec, f) >= 0) frame_out(f, ch), av_frame_unref(f);
        av_packet_unref(pkt);
    }
    avcodec_send_packet(dec, NULL);
    while (avcodec_receive_frame(dec, f) >= 0) frame_out(f, ch), av_frame_unref(f);
    if (swr) out(NULL, 0, ch);
    return 0;
}
