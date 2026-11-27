################################################################################
#
# q2ui: ui/ from the pinned commit of feat/render-engine (tools/vampos.py Q2UI_COMMIT)
#
################################################################################

Q2UI_VERSION = $(shell cat $(VAMP_SRC)/q2ui/COMMIT)
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
endef

$(eval $(generic-package))
