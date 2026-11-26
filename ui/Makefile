# Built by tools/ui.py, which passes the compiler, stb_truetype's folder, SQLite's header and library:
#   make OUT=build/host CC=cc STB=DIR SQLITE_INC=DIR SQLITE_LIB=/usr/lib/x86_64-linux-gnu/libsqlite3.so.0
#   make OUT=build/mips CC="mips-mti-linux-gnu-gcc -EL ..." STB=DIR SQLITE_INC=DIR SQLITE_LIB=ROOTFS/usr/lib/libsqlite3.so.0.8.6
OUT ?= build/host
STRIP ?= strip
SRC := main.c fb.c draw.c text.c input.c songs.c list.c ../patch/wheel_accel.c
OBJ := $(patsubst %.c,$(OUT)/%.o,$(notdir $(SRC)))
CFLAGS := -std=c11 -D_GNU_SOURCE -O2 -g -Wall -Wextra -ffunction-sections -fdata-sections -isystem $(STB) -isystem $(SQLITE_INC)
# SQLite linked by file: the root filesystem has the library and no header, and no libsqlite3.so link for -l
LIBS := $(SQLITE_LIB) -Wl,--allow-shlib-undefined -lm

$(OUT)/q2ui: $(OBJ)
	$(CC) -o $@.debug $(OBJ) -Wl,--gc-sections $(LIBS)
	$(STRIP) -o $@ $@.debug

$(OUT)/%.o: %.c q2ui.h | $(OUT)
	$(CC) $(CFLAGS) -c $< -o $@

$(OUT)/wheel_accel.o: ../patch/wheel_accel.c ../patch/wheel_accel.h | $(OUT)
	$(CC) $(CFLAGS) -c $< -o $@

$(OUT)/list.o: ../patch/wheel_accel.h

$(OUT):
	mkdir -p $@
