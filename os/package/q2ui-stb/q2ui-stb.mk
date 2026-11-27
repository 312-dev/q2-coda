################################################################################
#
# q2ui-stb: two headers of github.com/nothings/stb at tools/ui.py's commit (test/os_image.py checks they agree)
#
################################################################################

Q2UI_STB_VERSION = f0569113c93ad095470c54bf34a17b36646bbbb5
Q2UI_STB_SITE = https://raw.githubusercontent.com/nothings/stb/$(Q2UI_STB_VERSION)
Q2UI_STB_SOURCE = stb_truetype.h
Q2UI_STB_EXTRA_DOWNLOADS = stb_image.h
Q2UI_STB_LICENSE = Public Domain or MIT
Q2UI_STB_INSTALL_STAGING = YES
Q2UI_STB_INSTALL_TARGET = NO

define Q2UI_STB_EXTRACT_CMDS
	cp $(Q2UI_STB_DL_DIR)/stb_truetype.h $(Q2UI_STB_DL_DIR)/stb_image.h $(@D)/
endef

define Q2UI_STB_INSTALL_STAGING_CMDS
	$(INSTALL) -D -m 0644 $(@D)/stb_truetype.h $(STAGING_DIR)/usr/include/stb/stb_truetype.h
	$(INSTALL) -D -m 0644 $(@D)/stb_image.h $(STAGING_DIR)/usr/include/stb/stb_image.h
endef

$(eval $(generic-package))
