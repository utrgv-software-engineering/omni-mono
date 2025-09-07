#!/bin/bash
# Run omni-tui with debug output redirected to debug.log
echo "Starting omni-tui with debug output to debug.log..."
echo "Use Ctrl+C to quit, then check debug.log for output"
OMNI_DEBUG=1 ./omni-tui --server ws://0.0.0.0:8000/ws --resume 2> debug.log
echo "Debug output saved to debug.log"
echo "View with: cat debug.log | less"