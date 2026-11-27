################################################################################
#
# vamp-assets: assets/ from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

VAMP_ASSETS_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT)
VAMP_ASSETS_SITE = $(VAMP_SRC)/vamp
VAMP_ASSETS_SITE_METHOD = local
VAMP_ASSETS_LICENSE = OFL-1.1
VAMP_ASSETS_LICENSE_FILES = assets/fonts/Inter-OFL.txt

define VAMP_ASSETS_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0644 $(@D)/assets/fonts/Inter-Regular.ttf $(TARGET_DIR)/usr/share/vamp/fonts/inter.ttf
	$(INSTALL) -D -m 0644 $(@D)/assets/fonts/Inter-Medium.ttf $(TARGET_DIR)/usr/share/vamp/fonts/inter-medium.ttf
	$(INSTALL) -D -m 0644 $(@D)/assets/fonts/Inter-OFL.txt $(TARGET_DIR)/usr/share/vamp/fonts/Inter-OFL.txt
endef

$(eval $(generic-package))
