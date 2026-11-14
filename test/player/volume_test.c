/* player/output.c's volume_gains for the levels run.py's test_volume_law checks, one line each: L R gainL gainR. */
#include <stdio.h>
void volume_gains(int volume, float gain[2]);
int main(void) {
    static const int levels[][2] = {{100, 100}, {100, 50}, {50, 50}, {40, 40}, {255, 255}, {80, 40}, {0, 0}};
    for (unsigned i = 0; i < sizeof levels / sizeof *levels; ++i) {
        float g[2];
        volume_gains(levels[i][0] << 8 | levels[i][1], g);
        printf("%d %d %.6f %.6f\n", levels[i][0], levels[i][1], g[0], g[1]);
    }
    return 0;
}
