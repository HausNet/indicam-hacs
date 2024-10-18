#!/bin/bash

# Track beta releases of Home Assistant, and run tests against it. Report any errors for
# resolution prior to release.
#
# TODO: Figure out how to check out the current lead Beta
#

set -e

# Empty out workspace
cd ~/workspace/hausnet
rm -rf indicam-track-hass
mkdir indicam-track-hass
cd indicam-track-hass

# Get indicam HACS source & prospectively link it to Home Assistant
git clone git@github.com:HausNet/indicam-hacs.git
cd indicam-hacs
ln -s ../hass/homeassistant ./homeassistant
cd ..

# Fresh clone of HASS
git clone git@gitlab.com:hausnet/hass.git 
cd hass
git checkout dev

# Get latest upstream & check in
git remote add upstream https://github.com/home-assistant/home-assistant.git
git fetch upstream
git merge upstream/dev
git push

# Set up, then run tests
script/setup
. venv/bin/activate
mkdir config/custom_components
ln -s ../../../indicam-hacs/custom_components/indicam ./config/custom_components/indicam
script/gen_requirements_all.py
pip3 install -r requirements_test_all.txt
cd ../indicam_hacs
pip3 install -r requirements.txt
pytest tests


