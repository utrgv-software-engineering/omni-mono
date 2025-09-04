#!/bin/bash
# Build script for the Bubble Tea TUI backend

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Building Bubble Tea TUI backend...${NC}"

# Check if Go is installed
if ! command -v go &> /dev/null; then
    echo -e "${RED}Error: Go is not installed.${NC}"
    echo "Please install Go from https://golang.org/dl/"
    exit 1
fi

# Navigate to the Go module directory
cd "$(dirname "$0")/cmd/omni-tui"

# Download dependencies
echo -e "${YELLOW}Downloading dependencies...${NC}"
go mod download

# Build the binary
echo -e "${YELLOW}Building binary...${NC}"
go build -o ../../bin/omni-tui

echo -e "${GREEN}Build successful!${NC}"
echo -e "Binary created at: bin/omni-tui"
echo ""
echo "To use the Bubble Tea backend, run:"
echo "  python -m omniagents.cli run -c your_agent.yaml --mode bubbletea"