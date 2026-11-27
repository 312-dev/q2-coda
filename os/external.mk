# VAMP_SRC is the folder tools/vampos.py exports this tree's sources into (vamp/, q2ui/, each with a COMMIT file):
# the packages build from those exports, so an image is a function of the commits it names.
include $(sort $(wildcard $(BR2_EXTERNAL_VAMP_PATH)/package/*/*.mk))
