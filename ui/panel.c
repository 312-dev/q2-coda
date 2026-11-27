/* The panel's colour mode (docs/ui-rewrite.md#gradients). The X2000's display controller sends 24
 * bits a pixel over MIPI DSI, but stock's panel init tells the panel to take 16 (DCS COLMOD 0x3A
 * with 0x55: 5, 6 and 5 bits), so a third of what the canvas holds never reaches the glass. q2ui
 * tells the panel 24 (0x77) when it starts and when the screen has been dark, since a panel that
 * is set up again on waking is back at 16. A restart of the Q2 puts stock's 0x55 back.
 *
 * The command goes as the kernel's driver sends one, through the DSI host's generic packet
 * registers, reached by mapping /dev/mem: wait until the status says the payload and command
 * FIFOs are not full, write the payload word, then the header word, which sends it. Nothing else
 * of the controller is written, ever, and nothing at all unless the registers read as this
 * board's video link does (PANEL_CODING, PANEL_PKT): on any other machine, an emulator or a test
 * with no such file, this does nothing. --panel-mode keep leaves the panel as it is. */
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

#include "q2ui.h"

#define DSI_BASE 0x10075000u      /* the X2000's DSI host (DesignWare) */
#define DPI_COLOR_CODING 0x10     /* 5: 24 bits a pixel from the controller */
#define VID_PKT_SIZE 0x3c         /* 0x140: 320 pixels a line */
#define GEN_HDR 0x6c
#define GEN_PLD_DATA 0x70
#define CMD_PKT_STATUS 0x74
#define STATUS_FULL 0x0a          /* bit 1: the command FIFO is full; bit 3: the payload FIFO is */
#define PANEL_CODING 5
#define PANEL_PKT 0x140
#define DCS_LONG_WRITE_2 0x00000239u /* a DCS long write on channel 0, two bytes of payload */
#define COLMOD 0x3a
#define COLMOD_24 0x77

const char *panel_mem = "/dev/mem";
unsigned panel_base = DSI_BASE;
int panel_keep;
static volatile uint32_t *dsi;
static int tried;

/* 1 when the command was handed to the controller. */
int panel_24bit(void) {
    if (panel_keep) return 0;
    if (!tried) {
        tried = 1;
        int fd = open(panel_mem, O_RDWR | O_SYNC);
        if (fd < 0) return 0;
        void *map = mmap(0, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, (off_t)panel_base);
        close(fd);
        if (map != MAP_FAILED) dsi = map;
    }
    if (!dsi || dsi[DPI_COLOR_CODING / 4] != PANEL_CODING || dsi[VID_PKT_SIZE / 4] != PANEL_PKT) return 0;
    for (int waited = 0; dsi[CMD_PKT_STATUS / 4] & STATUS_FULL; ++waited) {
        if (waited == 20) return 0; /* 20 ms and still full: left for the next time */
        usleep(1000);
    }
    dsi[GEN_PLD_DATA / 4] = COLMOD | COLMOD_24 << 8;
    dsi[GEN_HDR / 4] = DCS_LONG_WRITE_2;
    return 1;
}
