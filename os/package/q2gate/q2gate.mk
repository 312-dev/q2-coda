################################################################################
#
# q2gate: gate/ from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2GATE_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2GATE_SITE = $(VAMP_SRC)/vamp
Q2GATE_SITE_METHOD = local
Q2GATE_LICENSE = GPL-3.0+
Q2GATE_LICENSE_FILES = LICENSE

define Q2GATE_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/gate OUT=$(@D)/out CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" \
		LDFLAGS="$(TARGET_LDFLAGS)" $(@D)/out/q2gate
endef

define Q2GATE_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2gate $(TARGET_DIR)/usr/bin/q2gate
endef

$(eval $(generic-package))
