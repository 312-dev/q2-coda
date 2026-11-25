/* The Clicker (docs/internals.md#key-tone): where Key Tone's click is heard. System settings ->
 * Controls -> Clicker, the row that was Key Tone's, cycles Speaker, Headphones, Both and Off.
 * Speaker is the MCU's buzzer as Key Tone was, silent while headphones, Bluetooth or a USB DAC
 * listen. Headphones has q2player sound the click ({mcl-click\KIND}) and never sounds the buzzer:
 * mixed into what it plays, or with nothing playing (a pause, a stop) in silence of its own, the
 * output opened for it and held a few seconds (player/engine.c click_session); with the stock
 * player there is no click.
 * Both has the mixed click and the buzzer whatever listens, as Click With Headphones lifted that
 * gate. Off is nothing: Key Tone off, stock's g_keytone_flag and SYSSET KEYTONE.
 * The output is IPOD CLICKER (0 Both, the default, 1 Speaker, 2 Headphones), so an absent key is
 * Both whatever IPOD KEYTONEHP said (its 1 asked for the buzzer with headphones in, which Both
 * keeps), and a Key Tone saved off reads Off. */
#include "navigation.h"

enum { CLICKER_BOTH, CLICKER_SPEAKER, CLICKER_HEADPHONES };
enum {
    SHOWN_OFF,
    SHOWN_SPEAKER,
    SHOWN_HEADPHONES,
    SHOWN_BOTH
}; /* the row's values, in their cycle's order */
#define CLICKS_FILE "/tmp/q2clicks" /* player/main.c: there while q2player is the player */
#define CLICKS_LOOK_MS 1000         /* how long the file's answer is trusted */

/* the next click's sound, CLICK_KIND_*, 0 a wheel's: ringnav_keydown marks a button's */
int clicker_kind;

static int shown(void) {
    accent(); /* the [IPOD] keys, st.clicker among them */
    if (!g_keytone_flag) return SHOWN_OFF;
    return st.clicker == CLICKER_SPEAKER      ? SHOWN_SPEAKER
           : st.clicker == CLICKER_HEADPHONES ? SHOWN_HEADPHONES
                                              : SHOWN_BOTH;
}

const char *clicker_text(void) {
    static const char *const names[] = { "Clicker: Off", "Clicker: Speaker", "Clicker: Headphones",
                                         "Clicker: Both" };
    return names[shown()];
}

/* The row's click: the next value, saved. Off is Key Tone off, so stock's own readers of the byte
 * see it. */
void clicker_step(void) {
    static const int stored[] = { 0, CLICKER_SPEAKER, CLICKER_HEADPHONES, CLICKER_BOTH };
    int next = (shown() + 1) % 4, tone = next != SHOWN_OFF;
    if (tone != (g_keytone_flag != 0)) {
        g_keytone_flag = (unsigned char)tone;
        write_int_config(tone, "SYSSET", "KEYTONE");
    }
    if (next != SHOWN_OFF && st.clicker != stored[next]) {
        st.clicker = stored[next];
        write_int_config(st.clicker, "IPOD", "CLICKER");
    }
}

/* Whether q2player is the player (its file, looked for once a second), which sounds a click playing
 * or not. */
static int player_there(void) {
    static unsigned long long looked;
    static int there;
    unsigned long long now = now_ms();
    if (!looked || now < looked || now - looked >= CLICKS_LOOK_MS)
        looked = now ? now : 1, there = !access(CLICKS_FILE, 0); /* F_OK */
    return there;
}
/* ... and a song plays (mclGetPlayStatus 2): what the chirp, mixed only into a song, needs */
static int player_mixes(void) { return player_there() && mclGetPlayStatus() == 2; }

/* ringnav_buzzer's routing of one click: the mixed one sent here (ROUTE_MIXED), and whether the
 * buzzer clicks too (ROUTE_BUZZER). listening: headphones, Bluetooth or a USB DAC take the sound,
 * where Speaker's buzzer stays quiet. */
int media_owner(void); /* headset.c: 1 the Library's player, else Spotify, Radio, a video or the tuner */
int clicker_route(int listening) {
    static const char *const sends[] = { "{mcl-click\\wheel}", "{mcl-click\\button}",
                                         "{mcl-click\\beep}" };
    int mode = shown(), mixed = 0;
    if (mode == SHOWN_OFF) return 0;
    /* the Library's player alone takes it: while Radio, a video, Internet Radio or Spotify owns the output
     * (headset.c media_owner), a click there would open the output under it (on Bluetooth, bluealsa's one client,
     * held BT_CLICK_WARM_MS: docs/internals.md#bluetooth-click-warm) */
    if ((mode == SHOWN_HEADPHONES || mode == SHOWN_BOTH) && player_there() && media_owner() == 1) {
        int fd = *(volatile int *)MCL_FD, k = clicker_kind;
        const char *m =
            sends[k >= CLICK_KIND_WHEEL && k <= CLICK_KIND_BEEP ? k - CLICK_KIND_WHEEL : 0];
        /* never waits on the player, nor dies with it (MSG_DONTWAIT | MSG_NOSIGNAL) */
        if (fd != -1 && send(fd, m, strlen(m), 0x4040) > 0) mixed = 1;
    }
    int buzz = mode == SHOWN_BOTH || (mode == SHOWN_SPEAKER && !listening);
    return (buzz ? ROUTE_BUZZER : 0) | (mixed ? ROUTE_MIXED : 0);
}

/* Whether the buzzer clicks under the Clicker, nothing sent to the player: a click of Radio's dial
 * (input.c ringnav_piezo), whose own receiver clicks in what it plays, where q2player plays
 * nothing. Off none; Speaker while nothing listens; Headphones never; Both always. */
int clicker_buzzes(int listening) {
    int mode = shown();
    return mode == SHOWN_BOTH || (mode == SHOWN_SPEAKER && !listening);
}

/* The plug-in chirp (charging.c charge_plugged; q2player's {mcl-click\charge}, player/output.c
 * chirp_at): a sound, not a click, so the buzzer, which can only click, never plays it. Mixed into
 * what q2player plays under Headphones or Both, as a click is; with nothing playing, the stock
 * player or Speaker or Off, there is none: the PCM is closed, or held by hciplayer with no mixer.
 * 1 if sent. */
int clicker_chirp(void) {
    int mode = shown(), fd = *(volatile int *)MCL_FD;
    if ((mode != SHOWN_HEADPHONES && mode != SHOWN_BOTH) || !player_mixes() || fd == -1) return 0;
    static const char m[] = "{mcl-click\\charge}";
    return send(fd, m, sizeof m - 1, 0x4040) > 0; /* MSG_DONTWAIT | MSG_NOSIGNAL */
}
