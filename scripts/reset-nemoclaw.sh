#!/usr/bin/env bash
# Stop and destroy the current NemoClaw lab setup.
# Use --full to also remove the lab's n8n and Caddy data.

set -euo pipefail

SANDBOX=lab
FULL=0
SANDBOX_SET=0
for arg in "$@"; do
  case "$arg" in
    --full) FULL=1 ;;
    -h|--help)
      echo "Usage: $0 [--full] [sandbox]"
      echo "--full removes lab containers, volumes, network, and MCP state."
      echo "The .env file, installed tools, images, and model caches are kept."
      exit 0
      ;;
    -*) echo "Unknown option: $arg" >&2; exit 1 ;;
    *)
      [ "$SANDBOX_SET" -eq 0 ] || {
        echo "Provide only one sandbox name." >&2
        exit 1
      }
      SANDBOX="$arg"
      SANDBOX_SET=1
      ;;
  esac
done

export PATH="$HOME/.local/bin:$PATH"

command -v docker >/dev/null 2>&1 || {
  echo "docker is required." >&2
  exit 1
}
docker info >/dev/null || {
  echo "Start Docker before resetting the lab." >&2
  exit 1
}

[[ "$SANDBOX" =~ ^[a-z][a-z0-9-]{0,18}$ ]] || {
  echo "Invalid sandbox name: $SANDBOX" >&2
  exit 1
}

SANDBOX_EXISTS=0
if command -v nemoclaw >/dev/null 2>&1; then
  SANDBOX_LIST="$(nemoclaw list)"
  if grep -Eq "(^|[[:space:]])${SANDBOX}([[:space:]]|$)" <<<"$SANDBOX_LIST"; then
    SANDBOX_EXISTS=1
  fi
elif [ "$FULL" -eq 0 ]; then
  echo "nemoclaw is required." >&2
  exit 1
fi

MANAGED_ID=""
MANAGED_PRESET=""
EXTERNAL_ID=""

if docker container inspect nemoclaw-vllm >/dev/null 2>&1; then
  MANAGED_LABELS="$(docker container inspect --format '{{json .Config.Labels}}' nemoclaw-vllm)"
  if grep -q '"com.nvidia.nemoclaw.managed-vllm":"true"' <<<"$MANAGED_LABELS" &&
     ! grep -q 'com.nvidia.nemoclaw.vllm-role' <<<"$MANAGED_LABELS" &&
     ! grep -q 'com.nvidia.nemoclaw.vllm-endpoint' <<<"$MANAGED_LABELS" &&
     ! grep -q 'com.nvidia.nemoclaw.vllm-cluster' <<<"$MANAGED_LABELS"; then
    MANAGED_ID="$(docker container inspect --format '{{.Id}}' nemoclaw-vllm)"
    MANAGED_PRESET="$(docker container inspect --format '{{index .Config.Labels "com.nvidia.nemoclaw.serving-preset"}}' nemoclaw-vllm)"
  fi
fi

CONTAINER_IDS="$(docker ps -aq)"
while read -r container_id; do
  [ -n "$container_id" ] || continue
  [ "$container_id" = "$MANAGED_ID" ] && continue

  image="$(docker container inspect --format '{{.Config.Image}}' "$container_id")"
  cmd="$(docker container inspect --format '{{json .Config.Cmd}}' "$container_id")"
  service="$(docker container inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$container_id")"
  project="$(docker container inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$container_id")"

  if [ "$project" = "lab" ] && [ "$service" = "vllm" ] &&
     [[ "$image" == *vllm* ]] &&
     [[ "$cmd" == *nemotron-3-super* || "$cmd" == *Nemotron-3-Super* ]]; then
    EXTERNAL_ID="$container_id"
    break
  fi
done <<<"$CONTAINER_IDS"

if [ -n "$MANAGED_ID" ]; then
  KIND="spark"
elif [ -n "$EXTERNAL_ID" ]; then
  KIND="spark-super"
else
  KIND="local/linux"
fi

echo "Detected setup: $KIND"
echo "Sandbox: $SANDBOX"
if [ "$SANDBOX_EXISTS" -eq 1 ]; then
  nemoclaw "$SANDBOX" status || true
else
  echo "Sandbox status: not found"
fi
if [ -n "$MANAGED_ID" ]; then
  echo "Managed vLLM: $MANAGED_ID"
  echo "Serving preset: ${MANAGED_PRESET:-unknown}"
fi
if [ -n "$EXTERNAL_ID" ]; then
  echo "External Super vLLM: $EXTERNAL_ID"
fi

echo
if [ "$FULL" -eq 1 ]; then
  echo "Full reset will delete sandbox $SANDBOX and all Docker lab project data."
  echo "This includes n8n workflows, credentials, settings, and Caddy certificates."
  echo "It also clears the host MCP state for $SANDBOX."
  echo "The .env file, installed tools, images, and model caches are kept."
  CONFIRMATION="RESET ALL $SANDBOX"
else
  echo "This will stop and destroy the detected NemoClaw setup."
  echo "It will not remove model caches, Docker images, n8n data, or Caddy data."
  CONFIRMATION="RESET $SANDBOX"
fi
read -r -p "Type $CONFIRMATION to continue: " confirmation
if [ "$confirmation" != "$CONFIRMATION" ]; then
  echo "Reset cancelled."
  exit 0
fi

if [ "$SANDBOX_EXISTS" -eq 1 ]; then
  # MCP removal needs a running sandbox. Destroy handles shutdown itself.
  echo "Starting sandbox $SANDBOX for MCP cleanup..."
  nemoclaw "$SANDBOX" start
  echo "Destroying sandbox $SANDBOX..."
  DESTROY_ARGS=(--yes)
  if [ "$FULL" -eq 1 ]; then
    DESTROY_ARGS+=(--cleanup-gateway)
  fi
  REMOVED_MCP=" "
  while true; do
    if DESTROY_OUTPUT="$(nemoclaw "$SANDBOX" destroy "${DESTROY_ARGS[@]}" 2>&1)"; then
      printf '%s\n' "$DESTROY_OUTPUT"
      break
    else
      DESTROY_STATUS=$?
    fi
    printf '%s\n' "$DESTROY_OUTPUT" >&2
    MCP_SERVER="$(sed -nE "s/.*MCP server '([[:alnum:]_.-]+)' has an incomplete add transaction.*/\\1/p" <<<"$DESTROY_OUTPUT")"
    if [ -z "$MCP_SERVER" ] || [[ "$MCP_SERVER" == *$'\n'* ]] ||
       [[ "$REMOVED_MCP" == *" $MCP_SERVER "* ]]; then
      exit "$DESTROY_STATUS"
    fi
    echo "Removing unfinished MCP entry: $MCP_SERVER"
    nemoclaw "$SANDBOX" mcp remove "$MCP_SERVER" --force
    REMOVED_MCP+="$MCP_SERVER "
  done
fi

if [ -n "$MANAGED_ID" ] &&
   docker container inspect "$MANAGED_ID" >/dev/null 2>&1; then
  echo "Stopping managed vLLM container..."
  docker container stop "$MANAGED_ID" >/dev/null
  echo "Removing managed vLLM container..."
  docker container rm "$MANAGED_ID" >/dev/null
fi

if [ -n "$EXTERNAL_ID" ] && [ "$FULL" -eq 0 ] &&
   docker container inspect "$EXTERNAL_ID" >/dev/null 2>&1; then
  echo "Stopping external Super vLLM container..."
  docker container stop "$EXTERNAL_ID" >/dev/null
  echo "Removing external Super vLLM container..."
  docker container rm "$EXTERNAL_ID" >/dev/null
fi

if [ "$FULL" -eq 1 ]; then
  # Include stopped containers and Compose one-off containers before volumes.
  LAB_CONTAINERS="$(docker ps -aq --filter label=com.docker.compose.project=lab)"
  while read -r container_id; do
    [ -n "$container_id" ] || continue
    echo "Removing lab container: $container_id"
    docker container rm -f "$container_id" >/dev/null
  done <<<"$LAB_CONTAINERS"

  LAB_VOLUMES="$(docker volume ls -q --filter label=com.docker.compose.project=lab)"
  while read -r volume; do
    [ -n "$volume" ] || continue
    echo "Removing lab volume: $volume"
    docker volume rm "$volume" >/dev/null
  done <<<"$LAB_VOLUMES"

  LAB_NETWORKS="$(docker network ls -q --filter label=com.docker.compose.project=lab)"
  while read -r network; do
    [ -n "$network" ] || continue
    echo "Removing lab network: $network"
    docker network rm "$network" >/dev/null
  done <<<"$LAB_NETWORKS"

  rm -rf -- "$HOME/.local/state/nemoclaw-mcp/$SANDBOX"
  echo "Full lab reset complete. Run ./run.sh, then ./install-nemoclaw.sh."
else
  echo "NemoClaw reset complete. Model caches and Docker images were kept."
fi
