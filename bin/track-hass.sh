#!/bin/bash

# Track beta releases of Home Assistant, and run tests against it. Report any errors for
# resolution prior to release.
#
# TODO: Figure out how to check out the current lead Beta
#

# Empty out workspace
cd ~/workspace/hausnet
rm -rf ./indicam-track-hass
mkdir ./indicam-track-hass
cd ./indicam-track-hass

# Get indicam HACS source
git clone git@github.com:HausNet/indicam-hacs.git

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
./script/setup
. ./venv/bin/activate
mkdir ./config/custom_components
ln -s ../../../indicam-hacs/custom_components/indicam ./config/custom_components/indicam
pip3 install ../indicam-hacs/requirements.txt

