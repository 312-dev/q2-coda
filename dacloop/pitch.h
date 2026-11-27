/* The fill level's controller (docs/dacloop.md#holding-the-fill-level), apart from ALSA so test/dacloop.py can run it
 * against a simulated computer whose clock is off.
 *
 * In USB DAC mode the computer's clock and the DAC's are two crystals: a computer running fast fills the loop's
 * buffers, one running slow empties them, by its error in parts per million of the rate (100 ppm at 96 kHz is about
 * 10 frames a second, a 100 ms prebuffer spent in under 20 minutes). The gadget is asynchronous, so the Q2 tells the
 * computer how fast to send in its feedback endpoint, and the kernel's "Capture Pitch 1000000" control scales that
 * feedback: 1000000 is the nominal rate, 1000100 asks for 100 ppm more. Once a second the loop gives pitch_step the
 * fill's error in seconds (the frames between the computer and the DAC, less the prebuffer's), and sets the control
 * to what it returns.
 *
 * A proportional-integral loop, critically damped with a time constant of PITCH_TAU_S: the proportional part pulls
 * the fill back, the integral learns the computer's steady error so the fill settles on the target itself and not
 * beside it. Slow on purpose: the measurement is a few packets' worth of noise and the drift is tiny, so a quick loop
 * would only move the computer's rate about. The integral stops while the output is held at a limit (the control's
 * own range, and PITCH_SPAN_PPM, beyond any real crystal's error), so it does not wind up. */
#ifndef Q2DACLOOP_PITCH_H
#define Q2DACLOOP_PITCH_H

#define PITCH_NOMINAL 1000000
#define PITCH_TAU_S 60.0    /* the loop's time constant: about 5 of them to settle */
#define PITCH_SPAN_PPM 2000 /* never asked further from nominal than this */

struct pitch { double sum; }; /* the error's integral, seconds times seconds; zero at each session's start */

/* the control's next value for an error of err_s seconds (above the target positive), dt seconds after the last;
 * lo and hi are the control's range */
static int pitch_step(struct pitch *p, double err_s, double dt, long lo, long hi) {
    const double kp = 2.0 / PITCH_TAU_S, ki = 1.0 / (PITCH_TAU_S * PITCH_TAU_S); /* zeta 1, omega 1/tau */
    if (lo < PITCH_NOMINAL - PITCH_SPAN_PPM) lo = PITCH_NOMINAL - PITCH_SPAN_PPM;
    if (hi > PITCH_NOMINAL + PITCH_SPAN_PPM) hi = PITCH_NOMINAL + PITCH_SPAN_PPM;
    double sum = p->sum + err_s * dt, u = PITCH_NOMINAL - (kp * err_s + ki * sum) * 1e6;
    /* held at a limit, the integral moves only back towards the range */
    if (u > (double)hi) u = (double)hi, p->sum = err_s > 0 ? sum : p->sum;
    else if (u < (double)lo) u = (double)lo, p->sum = err_s < 0 ? sum : p->sum;
    else p->sum = sum;
    return (int)(u + (u < 0 ? -0.5 : 0.5));
}

#endif
