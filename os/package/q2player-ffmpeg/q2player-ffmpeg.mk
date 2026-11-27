################################################################################
#
# q2player-ffmpeg: FFmpeg as tools/player.py builds it for the player
#
################################################################################

Q2PLAYER_FFMPEG_VERSION = 7.1.5
Q2PLAYER_FFMPEG_SOURCE = ffmpeg-$(Q2PLAYER_FFMPEG_VERSION).tar.xz
Q2PLAYER_FFMPEG_SITE = https://ffmpeg.org/releases
Q2PLAYER_FFMPEG_LICENSE = LGPL-3.0+
Q2PLAYER_FFMPEG_LICENSE_FILES = COPYING.LGPLv3
Q2PLAYER_FFMPEG_INSTALL_STAGING = YES
Q2PLAYER_FFMPEG_INSTALL_TARGET = NO
Q2PLAYER_FFMPEG_DEPENDENCIES = q2-mbedtls

# configure_args() is tools/player.py's (quoted for the shell, so expanded by make, not by $$(...)); the TLS lines are its ffmpeg()'s (https through mbedTLS, LGPL 3).
define Q2PLAYER_FFMPEG_CONFIGURE_CMDS
	cd $(@D) && $(TARGET_MAKE_ENV) ./configure \
		$(shell python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py ffmpeg-configure /usr) \
		--enable-cross-compile --cross-prefix=$(TARGET_CROSS) --arch=mips --target-os=linux --cpu=mips32r2 \
		--extra-cflags="$(TARGET_CFLAGS)" --extra-ldflags="$(TARGET_LDFLAGS)" \
		--pkg-config=$(PKG_CONFIG_HOST_BINARY) \
		--enable-mbedtls --enable-version3 --enable-protocol=https --enable-protocol=tls
endef

define Q2PLAYER_FFMPEG_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)
endef

define Q2PLAYER_FFMPEG_INSTALL_STAGING_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D) DESTDIR=$(STAGING_DIR) install
endef

$(eval $(generic-package))
