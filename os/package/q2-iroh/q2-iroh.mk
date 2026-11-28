################################################################################
#
# q2-iroh: Car Remote's iroh sidecar, tools/iroh.py's pinned binary (built from remote/iroh-sidecar outside the
# image, kept in the third-party cache under its SHA-256), checked by os/support/pins.py from the exported tree;
# configs/vamp_q2_defconfig keeps Buildroot's strip off it (BR2_STRIP_EXCLUDE_FILES), so the image has the pinned bytes
#
################################################################################

Q2_IROH_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2_IROH_SITE = $(VAMP_SRC)/vamp
Q2_IROH_SITE_METHOD = local
Q2_IROH_LICENSE = GPL-3.0
Q2_IROH_LICENSE_FILES = LICENSE

define Q2_IROH_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(shell python3 $(@D)/os/support/pins.py iroh) $(TARGET_DIR)/usr/bin/q2-iroh
endef

$(eval $(generic-package))
