################################################################################
#
# q2-guest: patch/q2_guest.c and patch/q2_hotspot.sh from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2_GUEST_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2_GUEST_SITE = $(VAMP_SRC)/vamp
Q2_GUEST_SITE_METHOD = local
Q2_GUEST_LICENSE = GPL-3.0+
Q2_GUEST_LICENSE_FILES = LICENSE
Q2_GUEST_DEPENDENCIES = dbus

# The image's one bluealsa (etc/sv/bluealsa/run) carries the sink role and is its supervisor's: no second daemon
# (QG_SINKD), and the main daemon is never restarted for the switch (QG_MAIN_SOURCE, QG_MAIN_STOCK). test/q2_guest.py
# (test_vampos) builds the helper with this line.
Q2_GUEST_DEFINES = -DQG_SINKD=0 -DQG_MAIN_SOURCE='"true"' -DQG_MAIN_STOCK='"true"'

define Q2_GUEST_BUILD_CMDS
	$(TARGET_CC) -DCODA_LIBC $(Q2_GUEST_DEFINES) -Wall -Wno-unused-parameter $(TARGET_CFLAGS) -I$(@D)/patch \
		-o $(@D)/q2-guest $(@D)/patch/q2_guest.c $(TARGET_LDFLAGS) -ldbus-1
endef

define Q2_GUEST_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/q2-guest $(TARGET_DIR)/usr/bin/q2-guest
	$(INSTALL) -D -m 0755 $(@D)/patch/q2_hotspot.sh $(TARGET_DIR)/usr/bin/q2-hotspot
endef

$(eval $(generic-package))
