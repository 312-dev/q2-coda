################################################################################
#
# q2dacloop: dacloop/ from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2DACLOOP_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2DACLOOP_SITE = $(VAMP_SRC)/vamp
Q2DACLOOP_SITE_METHOD = local
Q2DACLOOP_LICENSE = GPL-3.0+
Q2DACLOOP_LICENSE_FILES = LICENSE
Q2DACLOOP_DEPENDENCIES = alsa-lib

define Q2DACLOOP_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/dacloop OUT=$(@D)/out CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" \
		LDFLAGS="$(TARGET_LDFLAGS)" ALSA=-lasound $(@D)/out/q2dacloop
endef

define Q2DACLOOP_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2dacloop $(TARGET_DIR)/usr/bin/q2dacloop
endef

$(eval $(generic-package))
