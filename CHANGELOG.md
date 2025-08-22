# Change Log
A log of changes made, following the [Common Changelog Format](https://common-changelog.org).

## 1.0.7
Pass platform as an array into async_forward_entry_setups()

## 1.0.6
Changed hass.config_entries->async_forward_entry_setup () to async_forward_entry_setups()

## 1.0.5
Fixed the reference to this repo in the manifest

## 1.0.4
Fixed typo with release 1.0.3

## 1.0.3 
Bumped aiofiles dependency to what's installed by HASS

## 1.0.2 - 2024-09-07
Fixed transforming offset percentages to factors 

## 1.0.1 - 2024-09-05
Defect fix to make scan interval work correctly

## 1.0 - 2024-09-04
Config flow instead of YAML

## 1.0b18 - 2024-08-30
Defect fix.

### Changes
Fixed accidental downgrade of indicam-client dependence to 1.0.5 (now 1.0.6 again) 

## 1.0b17 - 2024-08-29
Dependency update.

### Changes
Relaxed Pillow dependency to ">10" to prevent breaks every so often when HASS updates.

