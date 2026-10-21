#!/bin/sh
# Rocketship SSH Profile
# This script is sourced on SSH login to set up the environment.
# Customize this file for your project's needs.

# Ruby/Bundler configuration
export BUNDLE_PATH=/usr/local/bundle
export BUNDLE_WITHOUT=development
export RAILS_ENV=production

# Change to application directory
cd /rails

# Source Azure environment variables saved by startup script
# These are captured from the main process which receives Azure App Settings
if [ -f /home/rocketship-env ]; then
    set -a
    . /home/rocketship-env
    set +a
fi
