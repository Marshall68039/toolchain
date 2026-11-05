# ----------------------------
# Makefile Options
# ----------------------------

NAME = HTMLRD
DESCRIPTION = "HTML page reader"
COMPRESSED = NO
ARCHIVED = NO

CFLAGS = -Wall -Wextra -Oz
CXXFLAGS = -Wall -Wextra -Oz

# ----------------------------

# Current CE C/C++ Toolchain. On an older toolchain (before cedev-config existed) use:
#   include $(CEDEV)/meta/makefile.mk
include $(shell cedev-config --makefile)
