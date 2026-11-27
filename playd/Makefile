# q2playd (docs/playd.md). Built by Buildroot for the image (os/package/q2playd), and by test/playd.py for this
# machine against its own SQLite library and the header tools/thirdparty.py keeps, as tools/ui.py builds q2ui:
#   make OUT=DIR CC=cc SQLITE_INC=DIR SQLITE_LIB=/usr/lib/<arch>-linux-gnu/libsqlite3.so.0
OUT ?= build/host
CFLAGS ?= -O2
SQLITE_LIB ?= -lsqlite3
$(OUT)/q2playd: q2playd.c ../perm/q2perm.h | $(OUT)
	$(CC) -std=c11 -Wall -Wextra -I../perm $(CFLAGS) $(if $(SQLITE_INC),-isystem $(SQLITE_INC)) -o $@ q2playd.c $(LDFLAGS) $(SQLITE_LIB)
$(OUT):
	mkdir -p $@
