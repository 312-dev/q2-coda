################################################################################
#
# q2ui: this tree's ui/, exported from the commit the image is built from (tools/vampos.py build)
#
################################################################################

# the pin and this tree's commit: the glyphs (tools/vampos.py ui_art) and the install are this tree's, so a new commit
# installs again whatever the pin (Buildroot keeps a built version's stamps)
Q2UI_VERSION = $(shell cat $(VAMP_SRC)/q2ui/COMMIT)-$(shell cut -c1-12 $(VAMP_SRC)/vamp/COMMIT)
Q2UI_SITE = $(VAMP_SRC)/q2ui
Q2UI_SITE_METHOD = local
Q2UI_LICENSE = GPL-3.0+
Q2UI_LICENSE_FILES = LICENSE
Q2UI_DEPENDENCIES = sqlite q2ui-stb

define Q2UI_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/ui OUT=$(@D)/out \
		CC="$(TARGET_CC) $(TARGET_CPPFLAGS)" STRIP="$(TARGET_STRIP)" STB=$(STAGING_DIR)/usr/include/stb STBI=$(STAGING_DIR)/usr/include/stb \
		SQLITE_INC=$(STAGING_DIR)/usr/include SQLITE_LIB=$(STAGING_DIR)/usr/lib/libsqlite3.so $(@D)/out/q2ui
endef

define Q2UI_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2ui $(TARGET_DIR)/usr/bin/q2ui
	$(INSTALL) -d $(TARGET_DIR)/usr/share/vamp/icons
	$(INSTALL) -m 0644 $(@D)/icons/*.png $(TARGET_DIR)/usr/share/vamp/icons/
endef

$(eval $(generic-package))
