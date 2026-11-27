################################################################################
#
# q2libd: libd/ from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp, its tags read by q2player's FFmpeg
#
################################################################################

Q2LIBD_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2LIBD_SITE = $(VAMP_SRC)/vamp
Q2LIBD_SITE_METHOD = local
Q2LIBD_LICENSE = GPL-3.0+
Q2LIBD_LICENSE_FILES = LICENSE
Q2LIBD_DEPENDENCIES = sqlite q2player-ffmpeg q2-mbedtls

# FFmpeg is static and built with https (mbedTLS), so its libraries and TLS's go on the link line
define Q2LIBD_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/libd OUT=$(@D)/out CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" \
		LDFLAGS="$(TARGET_LDFLAGS) -Wl,--gc-sections" \
		LIBS="-lavformat -lavcodec -lswresample -lavutil -lmbedtls -lmbedx509 -lmbedcrypto -lsqlite3 -lm -lpthread" \
		$(@D)/out/q2libd
endef

define Q2LIBD_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2libd $(TARGET_DIR)/usr/bin/q2libd
endef

$(eval $(generic-package))
