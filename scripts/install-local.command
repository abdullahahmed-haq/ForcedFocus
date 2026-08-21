#!/bin/bash
set -euo pipefail

# A quick click-to-run local installation script.
echo "Installing ForcedFocus locally..."
sudo bash "$(dirname "$0")/../install.sh"
echo "Local installation complete! You can close this window."
