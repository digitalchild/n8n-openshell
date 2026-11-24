#!/usr/bin/env python3
"""Render local deployment files from cloud-init/user-data.yaml.

The cloud-init document remains the source of truth. This renderer extracts
the shared n8n, Caddy, and NemoClaw environment templates without running
cloud-init on the target host.
"""

import argparse
import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cloud-init/user-data.yaml"
OUTPUT_ROOT = ROOT / "local/generated"

TARGETS = {"local", "spark", "spark-super"}
MCP_ASSETS = (
    "install.sh",
    "policy/incident-mcp.yaml",
    "incident-mcp/SKILL.md",
    "incident-mcp/config.example.json",
    "incident-mcp/scripts/client.js",
)


def write_entry(document, path):
    entries = [entry for entry in document.get("write_files", [])
               if entry.get("path") == path]
    if len(entries) != 1:
        raise SystemExit(f"Expected one write_files entry for {path}.")
    return entries[0]["content"]


def setting_from_content(content, name):
    match = re.search(rf"^{re.escape(name)}=([^\n]+)$", content, re.MULTILINE)
    if not match:
        raise SystemExit(f"Missing {name} in user-data.yaml.")
    return match[1]


def read_root_env():
    env_file = ROOT / ".env"
    if not env_file.is_file():
        raise SystemExit(f"Missing environment file: {env_file}")

    values = {}
    assignment = re.compile(
        r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$"
    )
    for line in env_file.read_text().splitlines():
        match = assignment.fullmatch(line)
        if not match:
            continue
        try:
            parts = shlex.split(match[2], comments=True, posix=True)
        except ValueError as error:
            raise SystemExit(f"Invalid quoting in .env: {error}") from error
        if len(parts) > 1:
            raise SystemExit(
                f"Expected one value for {match[1]} in .env."
            )
        value = parts[0] if parts else ""
        values[match[1]] = value

    required = {"N8N_OWNER_PASSWORD_HASH"}
    missing = sorted(name for name in required if not values.get(name))
    if missing:
        raise SystemExit("Missing .env values: " + ", ".join(missing))
    return values


def render_mcp(destination, ca_file):
    for relative in MCP_ASSETS:
        source = ROOT / "nemoclaw-mcp" / relative
        target = destination / "nemoclaw-mcp" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o755 if relative == "install.sh" else 0o644)

    prepare = '''#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
set -a
. ./.env
set +a
: "${N8N_HOST_IP:?Set N8N_HOST_IP in .env}"
'''
    prepare += f"CA_FILE={shlex.quote(ca_file)}\n"
    prepare += '''
# Use the same Caddy CA as cloud-init before NemoClaw onboarding.
for i in $(seq 1 90); do
  if docker compose -f docker-compose.yml exec -T caddy \\
    test -s /data/caddy/pki/authorities/local/root.crt; then
    break
  fi
  sleep 2
done

CA_TEMP="$(mktemp)"
trap 'rm -f "$CA_TEMP"' EXIT
docker compose -f docker-compose.yml exec -T caddy \\
  cat /data/caddy/pki/authorities/local/root.crt > "$CA_TEMP"
openssl x509 -in "$CA_TEMP" -noout
if ! cmp -s "$CA_TEMP" "$CA_FILE"; then
  sudo install -m 0644 "$CA_TEMP" "$CA_FILE"
fi
sudo update-ca-certificates
install -m 0644 "$CA_TEMP" "$ROOT/caddy-lab-root.crt"

for i in $(seq 1 90); do
  if curl -fsS --max-time 5 --cacert "$CA_FILE" \\
    "https://${N8N_HOST_IP}/healthz" >/dev/null; then
    break
  fi
  sleep 2
done
curl -fsS --max-time 10 --cacert "$CA_FILE" \\
  "https://${N8N_HOST_IP}/healthz" >/dev/null
'''
    prepare_path = destination / "prepare-mcp.sh"
    prepare_path.write_text(prepare)
    prepare_path.chmod(0o755)

    installer = '''#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
set -a
. ./.env
set +a
: "${N8N_HOST_IP:?Set N8N_HOST_IP in .env}"
: "${N8N_MCP_TOKEN:?Set N8N_MCP_TOKEN in .env}"

./prepare-mcp.sh
umask 077
TOKEN_FILE="$(mktemp)"
trap 'rm -f "$TOKEN_FILE"' EXIT
printf '%s\\n' "$N8N_MCP_TOKEN" > "$TOKEN_FILE"
unset N8N_MCP_TOKEN
HOST_IP="$N8N_HOST_IP" INCIDENT_MCP_TOKEN_FILE="$TOKEN_FILE" \\
  bash "$ROOT/nemoclaw-mcp/install.sh" lab

MCP_POLICY="$HOME/.local/state/nemoclaw-mcp/lab/incident-mcp.yaml"
test -s "$MCP_POLICY"
nemoclaw lab policy add \\
  --from-file "$MCP_POLICY" \\
  --trusted-private-host "$N8N_HOST_IP" \\
  --yes

echo "Dynamic MCP skill and policy installed in sandbox lab."
echo "Publish the MCP workflow with its matching bearer token before discovery."
'''
    install_path = destination / "install-mcp.sh"
    install_path.write_text(installer)
    install_path.chmod(0o755)


def render(args):
    document = yaml.safe_load(SOURCE.read_text())
    if not isinstance(document, dict):
        raise SystemExit("Cloud-init template must be a YAML mapping.")

    values = read_root_env()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    target_dir = OUTPUT_ROOT / args.target
    temporary = Path(tempfile.mkdtemp(prefix=f".{args.target}-", dir=OUTPUT_ROOT))

    try:
        compose = write_entry(document, "/home/nvidia/lab/docker-compose.yml")
        compose = compose.replace("${VM_IP}", "${N8N_HOST_IP}")
        compose = compose.replace(
            '- "${N8N_HOST_IP}:443:443"', '- "443:443"'
        )
        (temporary / "docker-compose.yml").write_text(compose)

        caddy = write_entry(document, "/home/nvidia/lab/Caddyfile.template")
        caddy = caddy.replace("${VM_IP}", "${N8N_HOST_IP}")
        (temporary / "Caddyfile.template").write_text(caddy)

        owner = write_entry(document, "/home/nvidia/lab/n8n-owner.env")
        owner = owner.replace(
            "PASTE_BCRYPT_HASH_HERE", values["N8N_OWNER_PASSWORD_HASH"]
        )
        for name in (
            "N8N_PERSONALIZATION_ENABLED",
            "N8N_DISABLED_MODULES",
            "N8N_LICENSE_ACTIVATION_KEY",
        ):
            owner = owner.replace(
                f"__{name}__", values.get(name, "")
            )
        (temporary / "n8n-owner.env").write_text(owner)

        nemoclaw_env = write_entry(
            document, "/home/nvidia/.config/nemoclaw/env"
        )
        mcp_setup = ""
        if args.mcp == "skill":
            ca_file = setting_from_content(
                nemoclaw_env, "NEMOCLAW_CORPORATE_CA_BUNDLE"
            )
            render_mcp(temporary, ca_file)
            mcp_setup = (
                './prepare-mcp.sh\n'
                'export NEMOCLAW_CORPORATE_CA_BUNDLE='
                + shlex.quote(ca_file) + '\n'
            )
        spark_profile = setting_from_content(
            nemoclaw_env, "NEMOCLAW_SPARK_PROFILE"
        )
        super_model = values.get(
            "NEMOCLAW_SPARK_SUPER_MODEL",
            setting_from_content(nemoclaw_env, "NEMOCLAW_SPARK_SUPER_MODEL"),
        )
        super_hf_model = values.get(
            "NEMOCLAW_VLLM_HF_MODEL",
            setting_from_content(nemoclaw_env, "NEMOCLAW_VLLM_HF_MODEL"),
        )
        super_vllm_image = values.get(
            "NEMOCLAW_SPARK_SUPER_VLLM_IMAGE",
            setting_from_content(
                nemoclaw_env, "NEMOCLAW_SPARK_SUPER_VLLM_IMAGE"
            ),
        )

        if args.target in {"local", "spark"} and not values.get(
            "NEMOCLAW_MODEL"
        ):
            raise SystemExit("Missing NEMOCLAW_MODEL in .env.")

        model = (
            super_model
            if args.target == "spark-super"
            else values["NEMOCLAW_MODEL"]
        )

        local_env = [
            "N8N_HOST_IP=",
            "N8N_ENCRYPTION_KEY=bt61191-local-lab-encryption-key",
            f"NEMOCLAW_MODEL={model}",
        ]
        if args.mcp == "skill":
            local_env.append("N8N_MCP_TOKEN=")
        if args.target == "local":
            local_env.append("NVIDIA_INFERENCE_API_KEY=")
        elif args.target == "spark":
            local_env.append(f"NEMOCLAW_PROFILE={spark_profile}")
        else:
            local_env.extend([
                f"NEMOCLAW_VLLM_HF_MODEL={super_hf_model}",
                f"NEMOCLAW_SPARK_SUPER_VLLM_IMAGE={super_vllm_image}",
                "HF_TOKEN=",
                "HF_CACHE_DIR=${HOME}/.cache/huggingface",
                "VLLM_STARTUP_TIMEOUT_SECONDS=1800",
            ])
        (temporary / ".env.template").write_text(
            "# Copy to .env and fill the empty values.\n"
            + "\n".join(local_env) + "\n"
        )

        if args.target == "spark-super":
            vllm = write_entry(
                document,
                "/home/nvidia/lab/spark-super/docker-compose.vllm.yml",
            )
            (temporary / "docker-compose.vllm.yml").write_text(vllm)

        provision = write_entry(document, "/opt/lab/provision.sh")
        tag_match = re.search(
            r"export NEMOCLAW_INSTALL_TAG=([^\n]+)", provision
        )
        ref_match = re.search(
            r"export NEMOCLAW_INSTALL_REF=([^\n]+)", provision
        )
        if not tag_match or not ref_match:
            raise SystemExit(
                "Could not extract the NemoClaw release from user-data.yaml."
            )

        if args.target == "local":
            install = f'''#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd)"
cd "$ROOT"
set -a
. ./.env
set +a
export PATH="$HOME/.local/bin:$PATH"
: "${{NVIDIA_INFERENCE_API_KEY:?Set NVIDIA_INFERENCE_API_KEY in .env}}"
: "${{NEMOCLAW_MODEL:?Set NEMOCLAW_MODEL in .env}}"
{mcp_setup}\
export NVIDIA_INFERENCE_API_KEY NEMOCLAW_MODEL
export NEMOCLAW_INSTALL_TAG={tag_match[1]}
export NEMOCLAW_INSTALL_REF={ref_match[1]}
export NEMOCLAW_AGENT=openclaw
export NEMOCLAW_SANDBOX_NAME=lab
export NEMOCLAW_NON_INTERACTIVE=1
export NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
export NEMOCLAW_PROVIDER=build
export NEMOCLAW_WEB_SEARCH_PROVIDER=none
export NEMOCLAW_POLICY_TIER=restricted
# Reinstall when the installed version differs from the pin.
if [ "$(nemoclaw --version 2>/dev/null)" = "nemoclaw $NEMOCLAW_INSTALL_TAG" ]; then
  echo "NemoClaw $NEMOCLAW_INSTALL_TAG is already installed."
  nemoclaw onboard --non-interactive
else
  curl -fsSL --retry 3 https://www.nvidia.com/nemoclaw.sh | bash
fi
'''
        elif args.target == "spark":
            install = f'''#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd)"
cd "$ROOT"
set -a
. ./.env
set +a
export PATH="$HOME/.local/bin:$PATH"
: "${{NEMOCLAW_MODEL:?Set NEMOCLAW_MODEL in .env}}"
: "${{NEMOCLAW_PROFILE:?Set NEMOCLAW_PROFILE in .env}}"
{mcp_setup}\
export NEMOCLAW_INSTALL_TAG={tag_match[1]}
export NEMOCLAW_INSTALL_REF={ref_match[1]}
export NEMOCLAW_AGENT=openclaw
export NEMOCLAW_SANDBOX_NAME=lab
export NEMOCLAW_NON_INTERACTIVE=1
export NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
# Reinstall when the installed version differs from the pin.
# Defer onboarding: the installer default selects a different model profile.
if ! [ "$(nemoclaw --version 2>/dev/null)" = "nemoclaw $NEMOCLAW_INSTALL_TAG" ]; then
  curl -fsSL --retry 3 https://www.nvidia.com/nemoclaw.sh | \\
    NEMOCLAW_DEFER_ONBOARDING=1 bash
fi
# The profile selects the model. NemoClaw rejects --profile with this override.
unset NEMOCLAW_MODEL
# Direct tool disclosure: progressive mode hides exec behind tool search.
# --fresh discards a saved onboarding session. NemoClaw rejects --profile when it resumes one.
nemoclaw onboard --profile "$NEMOCLAW_PROFILE" --name lab --gpu --fresh \\
  --tool-disclosure direct \\
  --non-interactive --yes --yes-i-accept-third-party-software
'''
        else:
            install = f'''#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd)"
cd "$ROOT"
set -a
. ./.env
set +a
export PATH="$HOME/.local/bin:$PATH"
: "${{NEMOCLAW_MODEL:?Set NEMOCLAW_MODEL in .env}}"
{mcp_setup}\
export NEMOCLAW_INSTALL_TAG={tag_match[1]}
export NEMOCLAW_INSTALL_REF={ref_match[1]}
export NEMOCLAW_AGENT=openclaw
export NEMOCLAW_SANDBOX_NAME=lab
export NEMOCLAW_NON_INTERACTIVE=1
export NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
export NEMOCLAW_PROVIDER=vllm
# Reinstall when the installed version differs from the pin.
# Defer onboarding: the installer default selects a different model profile.
if ! [ "$(nemoclaw --version 2>/dev/null)" = "nemoclaw $NEMOCLAW_INSTALL_TAG" ]; then
  curl -fsSL --retry 3 https://www.nvidia.com/nemoclaw.sh | \\
    NEMOCLAW_DEFER_ONBOARDING=1 bash
fi
# Direct tool disclosure: progressive mode hides exec behind tool search.
# --fresh discards a saved onboarding session. NemoClaw rejects --profile when it resumes one.
nemoclaw onboard --non-interactive --tool-disclosure direct
'''
        if args.mcp == "skill":
            install += '\n./install-mcp.sh\n'
        install_path = temporary / "install-nemoclaw.sh"
        install_path.write_text(install)
        install_path.chmod(0o755)

        launcher = '''#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
[ -f .env ] || { echo "Copy .env.template to .env first." >&2; exit 1; }
for file in docker-compose.yml Caddyfile.template n8n-owner.env; do
  [ -f "$file" ] || {
    echo "Missing $file. Copy the complete generated directory." >&2
    exit 1
  }
done
set -a
. ./.env
set +a
: "${N8N_HOST_IP:?Set N8N_HOST_IP in .env}"
: "${N8N_ENCRYPTION_KEY:?Set N8N_ENCRYPTION_KEY in .env}"
: "${NEMOCLAW_MODEL:?Set NEMOCLAW_MODEL in .env}"

sed "s/\\${N8N_HOST_IP}/$N8N_HOST_IP/g" Caddyfile.template > Caddyfile
'''
        if args.target == "spark-super":
            launcher += '''
: "${HF_CACHE_DIR:?Set HF_CACHE_DIR in .env}"
: "${NEMOCLAW_VLLM_HF_MODEL:?Set NEMOCLAW_VLLM_HF_MODEL in .env}"
[ -f super_v3_reasoning_parser.py ] || \\
  wget -q https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4/raw/main/super_v3_reasoning_parser.py
docker compose -f docker-compose.yml -f docker-compose.vllm.yml up -d

echo "Waiting for vLLM to load the model..."
ready=0
for ((elapsed=0; elapsed<VLLM_STARTUP_TIMEOUT_SECONDS; elapsed+=5)); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/v1/models >/dev/null; then
    ready=1
    break
  fi
  if ! docker compose -f docker-compose.yml -f docker-compose.vllm.yml \\
    ps --status running --services | grep -qx vllm; then
    echo "The vLLM container stopped before becoming ready." >&2
    docker compose -f docker-compose.yml -f docker-compose.vllm.yml logs --tail=80 vllm >&2
    exit 1
  fi
  sleep 5
done
if [ "$ready" -ne 1 ]; then
  echo "vLLM did not become ready within ${VLLM_STARTUP_TIMEOUT_SECONDS}s." >&2
  docker compose -f docker-compose.yml -f docker-compose.vllm.yml logs --tail=80 vllm >&2
  exit 1
fi
'''
        else:
            launcher += '''
docker compose up -d
'''
        launcher += '''

echo "Local n8n and Caddy are running."
echo "n8n: https://$N8N_HOST_IP/"
echo "Run ./install-nemoclaw.sh next."
'''
        launcher_path = temporary / "run.sh"
        launcher_path.write_text(launcher)
        launcher_path.chmod(0o755)

        shutil.copy2(
            ROOT / "scripts/reset-nemoclaw.sh",
            temporary / "reset-nemoclaw.sh",
        )

        (temporary / "README.md").write_text(
            f"# Generated {args.target} local configuration\n\n"
            "These files come from `cloud-init/user-data.yaml` and the "
            "repository MCP assets. Do not edit them directly.\n\n"
            f"MCP mode: `{args.mcp}`.\n"
            "Run `./reset-nemoclaw.sh --full` to clear all lab runtime data.\n"
            + (
                "`install-nemoclaw.sh` installs the MCP skill and policy.\n"
                "For an existing sandbox, run `./install-mcp.sh`.\n"
                "Set `N8N_HOST_IP` and `N8N_MCP_TOKEN` in `.env` first.\n"
                if args.mcp == "skill" else "MCP setup is disabled.\n"
            )
        )

        if target_dir.exists():
            existing_env = target_dir / ".env"
            if existing_env.is_file():
                shutil.copy2(existing_env, temporary / ".env")
            shutil.rmtree(target_dir)
        os.replace(temporary, target_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    print(f"Created: {target_dir.relative_to(ROOT)}")
    print("Existing .env is kept. Copy .env.template only on the first run.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument(
        "--mcp", choices=("skill", "none"), default="skill",
        help="Install the cloud-init MCP skill and policy (default: skill), "
             "or skip MCP setup.",
    )
    render(parser.parse_args())


if __name__ == "__main__":
    main()
