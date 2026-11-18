#!/usr/bin/env bash
# Run on the Ubuntu VM host as the sandbox's owning user.
# Installs the skill and token, and renders a policy for separate application.
# Repository templates are never modified.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SOURCE="$ROOT/incident-mcp"
SANDBOX="${1:-lab}"
DEST=/sandbox/.openclaw/workspace/skills/incident-mcp
STATE_DIR="$HOME/.local/state/nemoclaw-mcp/$SANDBOX"

TOKEN=""
trap 'unset TOKEN' EXIT

[[ "$SANDBOX" =~ ^[a-z][a-z0-9-]{0,18}$ ]] || {
  echo "Invalid sandbox name." >&2
  exit 1
}

for cmd in openshell timeout tar python3; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "Required command missing: $cmd." >&2
    echo "Run this installer on the Ubuntu VM host." >&2
    exit 1
  }
done

for file in \
  "$SKILL_SOURCE/SKILL.md" \
  "$SKILL_SOURCE/config.example.json" \
  "$SKILL_SOURCE/scripts/client.js" \
  "$ROOT/policy/incident-mcp.yaml"; do
  [[ -r "$file" ]] || {
    echo "Missing source file: $file" >&2
    exit 1
  }
done

# Cloud-init supplies HOST_IP.
# Manual runs discover the VM's source address.
if [[ -z "${HOST_IP:-}" ]]; then
  HOST_IP="$(ip -4 route get 1.1.1.1 |
    awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
fi

echo "[1/5] Rendering configuration and policy for $HOST_IP..."

mkdir -p "$STATE_DIR"
chmod 0700 "$STATE_DIR"

python3 - "$ROOT" "$STATE_DIR" "$HOST_IP" <<'PY'
import ipaddress
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

root = Path(sys.argv[1])
state = Path(sys.argv[2])
host = str(ipaddress.IPv4Address(sys.argv[3]))

private_ranges = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]

if not any(
    ipaddress.ip_address(host) in network
    for network in private_ranges
):
    raise SystemExit(
        "HOST_IP must be the VM's routed RFC1918 IPv4 address."
    )

config = json.loads(
    (root / "incident-mcp/config.example.json").read_text()
)

template_url = config.get("server_url", "")
if template_url.count("HOST_IP") != 1:
    raise SystemExit(
        "config.example.json must contain one HOST_IP marker."
    )

# Substitute BEFORE URL parsing, which normalizes hostname case.
config["server_url"] = template_url.replace("HOST_IP", host)
url = urlsplit(config["server_url"])

if (
    url.scheme != "https"
    or url.hostname != host
    or url.username
    or url.password
    or url.query
    or url.fragment
    or url.port not in (None, 443)
    or url.path != "/mcp/incident-tools"
):
    raise SystemExit(
        "Rendered URL does not match the incident MCP policy."
    )

if (
    config.get("proxy_url") is not None
    or config.get("ca_file") is not None
):
    raise SystemExit(
        "Use runtime proxy/CA settings, not hardcoded overrides."
    )

config.pop("ca_file", None)

policy = (root / "policy/incident-mcp.yaml").read_text()

pattern = re.compile(
    r'^([ \t]*-[ \t]*host:[ \t]*)"HOST_IP"[ \t]*$',
    re.MULTILINE,
)

if len(pattern.findall(policy)) != 1:
    raise SystemExit(
        'Policy must contain exactly one endpoint host: "HOST_IP".'
    )

policy = pattern.sub(
    lambda match: f'{match[1]}"{host}"',
    policy,
)

for name, content in [
    ("config.json", json.dumps(config, indent=2) + "\n"),
    ("incident-mcp.yaml", policy),
]:
    target = state / name
    target.write_text(content)
    target.chmod(0o600)

print(f"Rendered files: {state}")
PY

echo "[2/5] Checking sandbox execution..."

timeout --kill-after=3s 15s \
  openshell sandbox exec --no-tty --name "$SANDBOX" -- \
  node -e 'console.log("EXEC_OK", process.execPath, process.version)'

echo "[3/5] Installing skill, client and rendered configuration..."

tar -cf - \
  -C "$SKILL_SOURCE" SKILL.md scripts/client.js \
  -C "$STATE_DIR" config.json |
  timeout --kill-after=3s 30s \
  openshell sandbox exec --no-tty --name "$SANDBOX" -- \
  sh -c '
    set -eu
    umask 077
    mkdir -p "$1/scripts"
    tar -xf - -C "$1"
  ' sh "$DEST"

echo "[4/5] Installing the limited MCP bearer token..."

if [[ -n "${INCIDENT_MCP_TOKEN_FILE:-}" ]]; then
  [[ -r "$INCIDENT_MCP_TOKEN_FILE" ]] || {
    echo "Cannot read INCIDENT_MCP_TOKEN_FILE." >&2
    exit 1
  }
  TOKEN="$(< "$INCIDENT_MCP_TOKEN_FILE")"
elif [[ -t 0 ]]; then
  echo "The sandbox agent user can read this token."
  echo "Use the workflow MCP token, not an administrator key."
  read -r -s -p "MCP bearer token: " TOKEN
  printf '\n'
else
  echo "Non-interactive installation requires INCIDENT_MCP_TOKEN_FILE." >&2
  exit 1
fi

if [[ -z "$TOKEN" ||
      "$TOKEN" == *[[:space:]]* ||
      "$TOKEN" == *openshell:resolve:* ]]; then
  echo "Expected a raw token without whitespace or a placeholder." >&2
  exit 1
fi

printf '%s\n' "$TOKEN" |
  timeout --kill-after=3s 20s \
  openshell sandbox exec --no-tty --name "$SANDBOX" -- \
  sh -c '
    set -eu
    umask 077
    tmp="$(mktemp "$1/.token.XXXXXX")"
    trap '\''rm -f "$tmp"'\'' EXIT
    cat > "$tmp"
    test -s "$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$1/.token"
  ' sh "$DEST"

unset TOKEN

echo "[5/5] Checking installed client configuration..."

timeout --kill-after=3s 20s \
  openshell sandbox exec --no-tty --name "$SANDBOX" -- \
  node "$DEST/scripts/client.js" check

echo
echo "Skill installed. No policy was changed and no MCP tool was called."
echo "Rendered policy: $STATE_DIR/incident-mcp.yaml"
echo "Review/apply the rendered policy, not the repository template."
