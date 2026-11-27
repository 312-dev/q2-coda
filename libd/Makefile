# q2libd (docs/libd.md). Built by Buildroot for the image (os/package/q2libd), against q2player's FFmpeg and this
# image's SQLite, and by test/libd.py for this machine against its own FFmpeg and SQLite.
OUT ?= build/host
CFLAGS ?= -O2
LIBS ?= -lavformat -lavcodec -lavutil -lsqlite3
$(OUT)/q2libd: q2libd.c ../perm/q2perm.h | $(OUT)
	$(CC) -std=c11 -Wall -Wextra $(CFLAGS) -I../perm $(if $(SQLITE_INC),-isystem $(SQLITE_INC)) -o $@ q2libd.c $(LDFLAGS) $(LIBS) -pthread
$(OUT):
	mkdir -p $@
