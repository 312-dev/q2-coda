# q2dacloop (docs/dacloop.md). Built by Buildroot for the image (os/package/q2dacloop) and by test/dacloop.py for this
# machine. ALSA: the image's libasound, or one named by file (ALSA=), declared by player/alsa_min.h.
OUT ?= build/host
CFLAGS ?= -O2
ALSA ?= -lasound
$(OUT)/q2dacloop: q2dacloop.c pitch.h ../player/alsa_min.h | $(OUT)
	$(CC) -std=c11 -Wall -Wextra $(CFLAGS) -I../player -o $@ q2dacloop.c $(LDFLAGS) $(ALSA)
$(OUT):
	mkdir -p $@
