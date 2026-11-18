#!/usr/bin/env python3
"""
Render cloud-init from the repository template and MCP assets.

Reads:
    cloud-init/user-data.yaml
    .env
    nemoclaw-mcp/

Writes:
    cloud-init/user-data-rendered.yaml

Environment settings are declared in the source template.
This script replaces their placeholders and packages the MCP files.
It does not modify the source template or source assets.
"""

import json
import os
import re
import shlex
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cloud-init/user-data.yaml"
OUTPUT = ROOT / "cloud-init/user-data-rendered.yaml"
ENV_FILE = ROOT / ".env"
ASSETS = ROOT / "nemoclaw-mcp"
DEST = "/opt/lab/nemoclaw-mcp"

REQUIRED = {
    "NVIDIA_INFERENCE_API_KEY",
    "NEMOCLAW_MODEL",
    "N8N_MCP_TOKEN",
    "N8N_OWNER_PASSWORD_HASH",
}

DEFAULTS = {
    "N8N_PERSONALIZATION_ENABLED": "false",
    "N8N_DISABLED_MODULES": "instance-ai",
    "N8N_LICENSE_ACTIVATION_KEY": "",
}


def read_settings():
    if not ENV_FILE.is_file():
        raise SystemExit(f"Missing environment file: {ENV_FILE}")

    accepted = REQUIRED | DEFAULTS.keys()
    values = dict(DEFAULTS)
    seen = set()

    assignment = re.compile(
        r"^\s*(?:export\s+)?"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$"
    )

    for number, line in enumerate(
        ENV_FILE.read_text().splitlines(), start=1
    ):
        match = assignment.fullmatch(line)
        if not match or match[1] not in accepted:
            continue

        name = match[1]
        if name in seen:
            raise SystemExit(f"Duplicate {name} in .env.")
        seen.add(name)

        try:
            parts = shlex.split(
                match[2], comments=True, posix=True
            )
        except ValueError:
            raise SystemExit(
                f"Invalid quoting on .env line {number}."
            )

        if len(parts) > 1:
            raise SystemExit(f"Expected one value for {name}.")

        values[name] = parts[0] if parts else ""

    missing = {name for name in REQUIRED if not values.get(name)}
    if missing:
        raise SystemExit(
            "Missing .env values: " + ", ".join(sorted(missing))
        )

    key = values["NVIDIA_INFERENCE_API_KEY"]
    if (
        not re.fullmatch(r"nvapi-[A-Za-z0-9._-]+", key)
        or "REPLACE" in key
    ):
        raise SystemExit("Set a real NVIDIA_INFERENCE_API_KEY.")

    token = values["N8N_MCP_TOKEN"]
    if (
        any(
            char.isspace() or ord(char) < 32 or ord(char) == 127
            for char in token
        )
        or "openshell:resolve:" in token
        or "REPLACE" in token
    ):
        raise SystemExit(
            "Set N8N_MCP_TOKEN to the raw, limited workflow token."
        )

    if not re.fullmatch(
        r"\$2[aby]\$(?:0[4-9]|[12][0-9]|3[01])\$[./A-Za-z0-9]{53}",
        values["N8N_OWNER_PASSWORD_HASH"],
    ):
        raise SystemExit(
            "N8N_OWNER_PASSWORD_HASH must be a complete bcrypt hash."
        )

    survey = values["N8N_PERSONALIZATION_ENABLED"].lower()
    if survey not in {"true", "false"}:
        raise SystemExit(
            "N8N_PERSONALIZATION_ENABLED must be true or false."
        )
    values["N8N_PERSONALIZATION_ENABLED"] = survey

    modules = values["N8N_DISABLED_MODULES"]
    if modules:
        names = [name.strip() for name in modules.split(",")]
        if any(
            not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name)
            for name in names
        ):
            raise SystemExit(
                "N8N_DISABLED_MODULES must be comma-separated module names."
            )
        values["N8N_DISABLED_MODULES"] = ",".join(names)

    license_key = values["N8N_LICENSE_ACTIVATION_KEY"]
    if license_key and (
        any(
            char.isspace() or ord(char) < 32 or ord(char) == 127
            for char in license_key
        )
        or "'" in license_key
        or "REPLACE" in license_key
    ):
        raise SystemExit(
            "N8N_LICENSE_ACTIVATION_KEY must be a real single-line key "
            "without whitespace or embedded single quotes."
        )

    return values


def replace_placeholders(files, target, replacements):
    matches = [
        entry for entry in files
        if entry.get("path") == target
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"Expected exactly one write_files entry for {target}."
        )

    content = matches[0]["content"]

    for marker, value in replacements.items():
        if content.count(marker) != 1:
            raise SystemExit(
                f"Expected exactly one {marker} placeholder in {target}."
            )
        content = content.replace(marker, value)

    matches[0]["content"] = content


def main():
    settings = read_settings()
    document = yaml.safe_load(SOURCE.read_text())

    if not isinstance(document, dict):
        raise SystemExit("Cloud-init template must be a YAML mapping.")

    files = document.get("write_files")
    if not isinstance(files, list):
        raise SystemExit("Cloud-init template needs a write_files list.")

    # Replace only within the intended environment file.
    # Placeholder checks inside provision.sh remain untouched.
    replace_placeholders(
        files,
        "/home/nvidia/.config/nemoclaw/env",
        {
            "nvapi-REPLACE-ME":
                settings["NVIDIA_INFERENCE_API_KEY"],
            "__NEMOCLAW_MODEL__":
                settings["NEMOCLAW_MODEL"],
        },
    )

    replace_placeholders(
        files,
        "/home/nvidia/lab/n8n-owner.env",
        {
            "PASTE_BCRYPT_HASH_HERE":
                settings["N8N_OWNER_PASSWORD_HASH"],
            "__N8N_PERSONALIZATION_ENABLED__":
                settings["N8N_PERSONALIZATION_ENABLED"],
            "__N8N_DISABLED_MODULES__":
                settings["N8N_DISABLED_MODULES"],
            "__N8N_LICENSE_ACTIVATION_KEY__":
                settings["N8N_LICENSE_ACTIVATION_KEY"],
        },
    )

    config = json.loads(
        (ASSETS / "incident-mcp/config.example.json").read_text()
    )
    if (
        config.get("proxy_url") is not None
        or config.get("ca_file") is not None
    ):
        raise SystemExit(
            "Use proxy_url: null and no hardcoded ca_file "
            "in config.example.json."
        )

    asset_files = {
        "install.sh": "0755",
        "policy/incident-mcp.yaml": "0644",
        "incident-mcp/SKILL.md": "0644",
        "incident-mcp/config.example.json": "0644",
        "incident-mcp/scripts/client.js": "0644",
    }

    for relative, mode in asset_files.items():
        source_file = ASSETS / relative
        if not source_file.is_file():
            raise SystemExit(f"Missing MCP asset: {source_file}")

        files.append({
            "path": f"{DEST}/{relative}",
            "defer": True,
            "owner": "nvidia:nvidia",
            "permissions": mode,
            "content": source_file.read_text(),
        })

    files.append({
        "path": "/opt/lab/incident-mcp.token",
        "defer": True,
        "owner": "nvidia:nvidia",
        "permissions": "0600",
        "content": settings["N8N_MCP_TOKEN"] + "\n",
    })

    paths = [entry["path"] for entry in files]
    if len(paths) != len(set(paths)):
        raise SystemExit(
            "Duplicate write_files paths. "
            "MCP assets should be packaged by the renderer only."
        )

    def represent_string(dumper, value):
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str",
            value,
            style="|" if "\n" in value else None,
        )

    yaml.SafeDumper.add_representer(str, represent_string)

    fd, temporary = tempfile.mkstemp(
        prefix=".user-data-",
        dir=OUTPUT.parent,
    )

    try:
        with os.fdopen(fd, "w") as output:
            output.write("#cloud-config\n")
            yaml.safe_dump(document, output, sort_keys=False)
        os.replace(temporary, OUTPUT)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    print(f"Created: {OUTPUT.relative_to(ROOT)}")
    print("Permissions: 0600")
    print(
        "Personalization enabled: "
        + settings["N8N_PERSONALIZATION_ENABLED"]
    )
    print(
        "Disabled n8n modules: "
        + (settings["N8N_DISABLED_MODULES"] or "none")
    )
    print(
        "License activation key: "
        + (
            "included"
            if settings["N8N_LICENSE_ACTIVATION_KEY"]
            else "not supplied"
        )
    )
    print("Contains credentials. Key values were not displayed.")


if __name__ == "__main__":
    main()
