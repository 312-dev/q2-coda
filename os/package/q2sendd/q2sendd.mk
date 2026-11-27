################################################################################
#
# q2sendd: sendd/ and patch/'s LocalSend from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2SENDD_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2SENDD_SITE = $(VAMP_SRC)/vamp
Q2SENDD_SITE_METHOD = local
Q2SENDD_LICENSE = GPL-3.0+
Q2SENDD_LICENSE_FILES = LICENSE
Q2SENDD_DEPENDENCIES = openssl

define Q2SENDD_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/sendd OUT=$(@D)/out CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" \
		LDFLAGS="$(TARGET_LDFLAGS)" $(@D)/out/q2sendd $(@D)/out/q2send
endef

define Q2SENDD_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2sendd $(TARGET_DIR)/usr/bin/q2sendd
	$(INSTALL) -D -m 0755 $(@D)/out/q2send $(TARGET_DIR)/usr/bin/q2send
endef

$(eval $(generic-package))
