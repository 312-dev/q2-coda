################################################################################
#
# q2playd: playd/ from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2PLAYD_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2PLAYD_SITE = $(VAMP_SRC)/vamp
Q2PLAYD_SITE_METHOD = local
Q2PLAYD_LICENSE = GPL-3.0+
Q2PLAYD_LICENSE_FILES = LICENSE
Q2PLAYD_DEPENDENCIES = sqlite

define Q2PLAYD_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/playd OUT=$(@D)/out CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" \
		LDFLAGS="$(TARGET_LDFLAGS)" SQLITE_LIB=-lsqlite3 $(@D)/out/q2playd
endef

define Q2PLAYD_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2playd $(TARGET_DIR)/usr/bin/q2playd
endef

$(eval $(generic-package))
