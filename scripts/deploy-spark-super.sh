#!/usr/bin/env bash
# Render and copy the DGX Spark Nemotron Super setup to a remote host.
# The remote .env file is never copied or overwritten.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE="${1:-}"
REMOTE_DIR="${2:-}"
GENERATED="$ROOT/local/generated/spark-super"

command -v python3 >/dev/null 2>&1 || {
  echo "python3 is required." >&2
  exit 1
}
command -v ssh >/dev/null 2>&1 || {
  echo "ssh is required." >&2
  exit 1
}
command -v rsync >/dev/null 2>&1 || {
  echo "rsync is required." >&2
  exit 1
}

# Prompt for the destination when it is not given as arguments.
if [ -z "$REMOTE" ] || [ -z "$REMOTE_DIR" ]; then
  if [ ! -t 0 ]; then
    echo "Usage: $0 <ssh-host> <remote-directory>" >&2
    exit 1
  fi
  [ -n "$REMOTE" ] || read -r -p "SSH host of the Spark (for example user@192.168.1.20): " REMOTE
  [ -n "$REMOTE_DIR" ] || read -r -p "Directory on the Spark, relative to the home folder or absolute (for example lab): " REMOTE_DIR
fi
if [ -z "$REMOTE" ] || [ -z "$REMOTE_DIR" ]; then
  echo "An SSH host and a remote directory are required." >&2
  exit 1
fi

echo "Rendering spark-super from cloud-init/user-data.yaml..."
python3 "$ROOT/scripts/render-local.py" --target spark-super

echo "Creating $REMOTE:$REMOTE_DIR..."
ssh "$REMOTE" "mkdir -p -- '$REMOTE_DIR'"

echo "Copying generated files. The remote .env is preserved..."
rsync -az \
  --exclude '.env' \
  "$GENERATED/" \
  "$REMOTE:$REMOTE_DIR/"

echo
echo "Deployment files copied to $REMOTE:$REMOTE_DIR"
echo "Run on the Spark:"
echo "  cd $REMOTE_DIR"
echo "  cp .env.template .env   # first run only"
echo "  ./run.sh"
echo "  ./install-nemoclaw.sh"
