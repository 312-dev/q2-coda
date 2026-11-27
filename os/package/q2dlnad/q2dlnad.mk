################################################################################
#
# q2dlnad: dlnad/ from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2DLNAD_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2DLNAD_SITE = $(VAMP_SRC)/vamp
Q2DLNAD_SITE_METHOD = local
Q2DLNAD_LICENSE = GPL-3.0+
Q2DLNAD_LICENSE_FILES = LICENSE

define Q2DLNAD_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/dlnad OUT=$(@D)/out CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" \
		LDFLAGS="$(TARGET_LDFLAGS)" $(@D)/out/q2dlnad
endef

define Q2DLNAD_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2dlnad $(TARGET_DIR)/usr/bin/q2dlnad
endef

$(eval $(generic-package))
