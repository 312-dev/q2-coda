# Built by tools/player.py, which passes the compiler, FFmpeg's prefix and the target:
#   make OUT=build/host CC=cc FFMPEG=PREFIX TARGET=host
#   make OUT=build/mips CC="mips-mti-linux-gnu-gcc -EL ..." FFMPEG=PREFIX TARGET=mips ALSA=ROOTFS/usr/lib/libasound.so.2
# TLS, when FFmpeg was built with mbedTLS, is the link line for its three libraries.
OUT ?= build/host
TARGET ?= host
# patch/peq.h's files' prefix: the Q2's own paths, or under the directory a host test runs the player in
PEQ_ROOT ?= ""
STRIP ?= strip
SRC := main.c ipc.c engine.c source.c output.c dsp.c ../patch/peq.c
OBJ := $(patsubst %.c,$(OUT)/%.o,$(notdir $(SRC)))
CFLAGS := -std=c11 -D_GNU_SOURCE -O2 -g -Wall -Wextra -Wno-unused-parameter -ffunction-sections -fdata-sections -I$(FFMPEG)/include -I../patch -DPEQ_HOST -DPEQ_ROOT='$(PEQ_ROOT)'
LIBS := -L$(FFMPEG)/lib -lavformat -lavcodec -lswresample -lavutil $(TLS) -lm -lpthread
ifeq ($(TARGET),mips)
CFLAGS += -DHAVE_ALSA
# stock's libasound: linked by file, since the root filesystem has no libasound.so link and no headers
LIBS += $(ALSA) -Wl,--allow-shlib-undefined -Wl,-Bstatic -latomic -Wl,-Bdynamic
endif

$(OUT)/q2player: $(OBJ)
	$(CC) -o $@.debug $(OBJ) -Wl,--gc-sections $(LIBS)
	$(STRIP) -o $@ $@.debug

$(OUT)/%.o: %.c q2player.h alsa_min.h | $(OUT)
	$(CC) $(CFLAGS) -c $< -o $@

# patch/peq.c, the payload's EQ engine, built for the player as test/peq.py builds it (PEQ_HOST: libc calls)
$(OUT)/peq.o: ../patch/peq.c ../patch/peq.h ../patch/theme.h | $(OUT)
	$(CC) $(CFLAGS) -c $< -o $@

$(OUT)/dsp.o: ../patch/peq.h

$(OUT):
	mkdir -p $@
