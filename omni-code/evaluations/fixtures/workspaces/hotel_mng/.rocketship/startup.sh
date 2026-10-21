#!/bin/sh
# Rocketship Startup Script
# This script runs when the container starts.
# Customize this file for your project's needs.

set -e

# Export environment variables for SSH sessions
# Azure injects env vars only into the main process, so we save them for SSH access
# Customize the grep pattern to match your app's environment variable prefixes
env | grep -E '^(RAILS_|SECRET_|DATABASE_|REDIS_|SQLITE|ACTIVE_STORAGE|PLATFORM_|BUNDLE_|WEBSITES_)' > /home/rocketship-env 2>/dev/null || true
chmod 644 /home/rocketship-env

# Start SSH service (required for Azure App Service SSH access)
service ssh start

# Run pre-start hook if it exists
if [ -x /rails/rocketship-hooks/pre-start ]; then
    /rails/rocketship-hooks/pre-start
fi

# Drop privileges and start the application as the rails user
# Customize this command for your application
cd /rails
exec gosu rails ./bin/thrust ./bin/rails server
