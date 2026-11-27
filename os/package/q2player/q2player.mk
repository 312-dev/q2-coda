################################################################################
#
# q2player: player/ from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2PLAYER_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT)
Q2PLAYER_SITE = $(VAMP_SRC)/vamp
Q2PLAYER_SITE_METHOD = local
Q2PLAYER_LICENSE = GPL-3.0+
Q2PLAYER_LICENSE_FILES = LICENSE
Q2PLAYER_DEPENDENCIES = alsa-lib q2player-ffmpeg q2-mbedtls

# PEQ_ROOT empty: the Q2's own paths. Q2_CAR_USB: Car Audio's output, as tools/build.py builds every image's player.
define Q2PLAYER_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/player OUT=$(@D)/out TARGET=mips \
		CC="$(TARGET_CC) $(TARGET_CPPFLAGS)" STRIP="$(TARGET_STRIP)" FFMPEG=$(STAGING_DIR)/usr \
		PEQ_ROOT='""' ALSA=$(STAGING_DIR)/usr/lib/libasound.so.2 \
		TLS="-lmbedtls -lmbedx509 -lmbedcrypto" EXTRA_CFLAGS=-DQ2_CAR_USB $(@D)/out/q2player
endef

define Q2PLAYER_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2player $(TARGET_DIR)/usr/bin/q2player
endef

$(eval $(generic-package))
