################################################################################
#
# q2sysd: sysd/ from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2SYSD_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2SYSD_SITE = $(VAMP_SRC)/vamp
Q2SYSD_SITE_METHOD = local
Q2SYSD_LICENSE = GPL-3.0+
Q2SYSD_LICENSE_FILES = LICENSE

define Q2SYSD_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/sysd OUT=$(@D)/out CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" \
		LDFLAGS="$(TARGET_LDFLAGS)" $(@D)/out/q2sysd
endef

define Q2SYSD_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2sysd $(TARGET_DIR)/usr/bin/q2sysd
endef

$(eval $(generic-package))
