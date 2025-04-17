#!/bin/bash

# Check if module name is provided
if [ -z "$1" ]; then
  echo "Usage: $0 <module_name.py>"
  exit 1
fi
MODULE_NAME="$1"

# Create log directory if it doesn't exist
mkdir -p tmp_logs

# Create timestamped log file
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="tmp_logs/build_log_$TIMESTAMP.txt"

# Use the exact path to serious_python_darwin package
SERIOUS_PYTHON_DIR="/Users/ericmartinez/.pub-cache/hosted/pub.dev/serious_python_darwin-0.9.2"
echo "Using serious_python_darwin directory: $SERIOUS_PYTHON_DIR"

# Verify that this directory actually exists
if [ ! -d "$SERIOUS_PYTHON_DIR" ]; then
  echo "ERROR: serious_python_darwin directory not found at $SERIOUS_PYTHON_DIR"
  echo "Please run: flutter pub add serious_python_darwin:0.9.2"
  exit 1
fi

# Check for the dist_ios directory
if [ ! -d "$SERIOUS_PYTHON_DIR/darwin/dist_ios" ]; then
  echo "ERROR: dist_ios directory not found at $SERIOUS_PYTHON_DIR/darwin/dist_ios"
  ls -la "$SERIOUS_PYTHON_DIR/darwin" || echo "darwin directory not found"
  exit 1
fi

# Verify socket framework exists
if [ ! -d "$SERIOUS_PYTHON_DIR/darwin/dist_ios/python-xcframeworks/_socket.xcframework/ios-arm64/_socket.framework" ]; then
  echo "ERROR: _socket.framework not found at expected location"
  exit 1
fi

# Copy ALL frameworks to site-xcframeworks where the install script looks for it
SITE_XCFRAMEWORKS_DIR="/Users/ericmartinez/openai-agents/build/flutter/ios/Pods/../.symlinks/plugins/serious_python_darwin/darwin/dist_ios/site-xcframeworks"
mkdir -p "$SITE_XCFRAMEWORKS_DIR"
echo "Copying ALL xcframeworks to site-xcframeworks directory"

# Copy all Python xcframeworks
cp -R "$SERIOUS_PYTHON_DIR/darwin/dist_ios/python-xcframeworks/"*.xcframework "$SITE_XCFRAMEWORKS_DIR/"

echo "All frameworks copied to: $SITE_XCFRAMEWORKS_DIR/"

# Echo starting message
echo "Starting build process at $(date)"
echo "Log file: $LOG_FILE"
echo "Building $MODULE_NAME"

# Set PIP_FIND_LINKS to use local wheels
export PIP_FIND_LINKS=/Users/ericmartinez/mobile-forge/dist/
echo "Using local wheels from: $PIP_FIND_LINKS"

# Run the command and tee output to both terminal and log file
uv run flet build ipa \
  --module-name "$MODULE_NAME" \
  --ios-provisioning-profile "Indie Ad-Hoc" \
  --ios-team-id "737TB2WL7V" \
  --ios-signing-certificate "Apple Development" \
  --org "app.indiegym" \
  --project "indie" \
  --clear-cache \
  --verbose 2>&1 | tee "$LOG_FILE"

# Fix the iOS socket remoteAddress issue
echo "Patching main.dart to fix iOS socket issue..."
MAIN_DART_PATH="/Users/ericmartinez/openai-agents/build/flutter/lib/main.dart"

# Check if the patch is already applied
if ! grep -q "debugPrint('Connection from client');" "$MAIN_DART_PATH"; then
    echo "Patch not found, applying..."
    # Create a fix patch file with just the line we need to replace
    cat > /tmp/socket_fix.patch << 'EOL'
--- main.dart.orig
+++ main.dart
@@ -236,2 +236,2 @@
-    debugPrint(
-        'Connection from: ${client.remoteAddress.address}:${client.remotePort}');
+    // Avoid iOS socket issue with remoteAddress
+    debugPrint('Connection from client');
EOL

    # Apply the patch
    patch -l "$MAIN_DART_PATH" /tmp/socket_fix.patch
    rm /tmp/socket_fix.patch

    echo "Applied socket fix patch"
else
    echo "Patch already applied, skipping."
fi

# No post-build steps here - the framework should already be installed by the build process

# Capture exit code
EXIT_CODE=${PIPESTATUS[0]}

# Echo completion message
echo -e "\nBuild completed at $(date)"
echo "Exit code: $EXIT_CODE"
echo "Log saved to: $LOG_FILE"

exit $EXIT_CODE