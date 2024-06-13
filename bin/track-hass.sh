#!/bin/bash

# Track beta releases of Home Assistant, and run tests against it. Report any errors for
# resolution prior to release.
#
# TODO: Figure out how to check out the current lead Beta
#

# Fresh clone
git clone git@gitlab.com:hausnet/hass.git ~/tmp/hass
git checkout master
# Get latest changes
git fetch upstream
git merge upstream/master
git checkout indicam

