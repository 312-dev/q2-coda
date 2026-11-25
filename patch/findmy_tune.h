/* Find My Vamp's jingle (docs/internals.md#find-my-vamp): the phrase, and how it is played on the
 * internal buzzer through the stock PWM driver, /dev/jz_pwm (soc_pwm.ko). The payload's findmy.c
 * and the audition binary tools/findmy_jingle.c both include it, so what is auditioned is what the
 * Vamp plays.
 *
 * The buzzer has no volume: a 50% square wave is the loudest it gets, and it is loudest near its
 * resonance, about 2 to 3 kHz (the MCU's own click sits there). So every note is in 2.3 to 3.2 kHz,
 * the middle favoured, and short rests part the notes, which a buzzer would otherwise smear into
 * one tone. To retune, change the table alone.
 *
 * The includer defines FM_IOCTL(fd, request, arg) (the ioctl, < 0 on failure) and FM_WAIT(ms)
 * (sleeps ms, and answers nonzero to stop the phrase) before including this. */
#ifndef FINDMY_TUNE_H
#define FINDMY_TUNE_H

/* soc_pwm.ko's ioctls: RELEASE and REQUEST take a channel by value and a pin name ("PC01", 12
 * bytes) and answer the channel; CONFIG takes u32[6] {1, 0, 0, frequency in Hz, 100, channel};
 * LEVEL takes u32[2] {channel, duty %}. CONFIG is refused while the channel runs, so the level goes
 * to 0 first. */
#define FM_PWM_RELEASE 0x80045016u
#define FM_PWM_REQUEST 0x8001500bu
#define FM_PWM_CONFIG 0x80185021u
#define FM_PWM_LEVEL 0xc004502cu
#define FM_PWM_CH 1 /* the buzzer's PWM channel, pin PC01 */
#define FM_DUTY 50

/* The phrase: {Hz, ms}, Hz 0 a rest. D7, F#7, E7, F#7, then a held G7, and a pause before it plays
 * again (about 1.7 s a pass), auditioned on the user's Q2 and kept (2026-11-25). */
static const unsigned short fm_tune[][2] = {
    { 2349, 110 }, { 0, 30 },     { 2960, 110 }, { 0, 30 },     { 2637, 110 },
    { 0, 30 },     { 2960, 110 }, { 0, 30 },     { 3136, 260 }, { 0, 900 },
};
#define FM_STEPS (sizeof fm_tune / sizeof *fm_tune)

static int fm_level(int fd, int ch, unsigned duty) {
    unsigned a[2] = { (unsigned)ch, duty };
    return FM_IOCTL(fd, FM_PWM_LEVEL, a);
}

/* The buzzer's channel taken: released first, as the MCU's click leaves the driver's channel marked
 * as working ("Cannot configure at working" on the next CONFIG). The channel, or < 0. */
static int fm_take(int fd) {
    char pin[12] = "PC01";
    FM_IOCTL(fd, FM_PWM_RELEASE, FM_PWM_CH);
    return FM_IOCTL(fd, FM_PWM_REQUEST, pin);
}

/* One note at hz on *ch: level 0, CONFIG, level 50. A CONFIG refused (the MCU took the channel
 * meanwhile) lets the channel go and takes it again, once; then the note is skipped. 0 sounding,
 * else < 0. */
static int fm_note(int fd, int *ch, unsigned hz) {
    for (int tries = 0; tries < 2; ++tries) {
        if (*ch < 0 || tries) {
            if (*ch >= 0) FM_IOCTL(fd, FM_PWM_RELEASE, *ch);
            if ((*ch = fm_take(fd)) < 0) return -1;
        }
        unsigned c[6] = { 1, 0, 0, hz, 100, (unsigned)*ch };
        fm_level(fd, *ch, 0);
        if (FM_IOCTL(fd, FM_PWM_CONFIG, c) >= 0) return fm_level(fd, *ch, FM_DUTY);
    }
    return -1;
}

/* One pass of the phrase: 1 when FM_WAIT stopped it, the buzzer then silent. */
static int fm_phrase(int fd, int *ch) {
    for (unsigned i = 0; i < FM_STEPS; ++i) {
        if (!fm_tune[i][0] || fm_note(fd, ch, fm_tune[i][0]) < 0) fm_level(fd, *ch, 0);
        if (FM_WAIT(fm_tune[i][1])) return fm_level(fd, *ch, 0), 1;
    }
    return 0;
}

/* The end: the pin driven low and the channel given back, for the MCU's next click. */
static void fm_done(int fd, int ch) {
    if (ch < 0) return;
    fm_level(fd, ch, 0);
    FM_IOCTL(fd, FM_PWM_RELEASE, ch);
}

#endif
