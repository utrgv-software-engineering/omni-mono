#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
cd "$PROJECT_DIR"

/home/ericm/.fvm/flutter_sdk/bin/flutter build web --release

exec python3 -m http.server 8080 --directory "$PROJECT_DIR/build/web"
