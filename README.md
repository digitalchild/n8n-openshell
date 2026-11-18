# BT61191 - Your Agent Doesn't Get Root: Governing AI Actions with n8n and NVIDIA NemoClaw

GTC Berlin 2026 X-Lab. 105 minutes, beginner.

To set up a DGX Spark, start with [`local/README.md`](local/README.md).

This repository prepares the infrastructure and exercises for a 50-VM training
lab. Each VM runs n8n and an OpenClaw agent inside a NemoClaw/OpenShell sandbox.

The agent discovers tools dynamically from an n8n MCP Server Trigger. OpenShell
restricts the network route, while n8n workflows define the available actions
and enforce application-level authorization.

The working agent integration uses a generic Node MCP client and an OpenClaw
skill. It does not use mcporter or NemoClaw's managed `mcp add` registration.

## What's here

| Path | Purpose |
|---|---|
| `cloud-init/user-data.yaml` | Source template for VM provisioning: packages, n8n owner account, Caddy HTTPS, NemoClaw onboarding, skill installation, and network policy application. Render it before deployment. |
| `scripts/render-cloud-init.py` | Packages the MCP source files and deployment credentials into `cloud-init/user-data-rendered.yaml`. Run on the preparation machine, not inside the sandbox. |
| `scripts/requirements.txt` | Python dependencies for the cloud-init renderer. Includes PyYAML, which provides the `yaml` module. |
| `nemoclaw-mcp/incident-mcp/` | Runtime skill, configuration template, and dependency-free Node MCP client. Tool names and schemas are discovered from the server, not hardcoded here. |
| `nemoclaw-mcp/install.sh` | Installs the skill and limited MCP token into an existing sandbox. Supports interactive use and unattended cloud-init installation. |
| `nemoclaw-mcp/policy/incident-mcp.yaml` | Network policy template permitting the incident MCP route. `HOST_IP` is resolved on the VM before application. |
| `n8n/datatables/` | Seed CSV files for incidents, pending actions, and runbooks. |
| `n8n/workflows/` | The incident-tools MCP workflow (starter and completed) and the approval dashboard. |
| `mcp-test/` | Independent Python MCP discovery checker for testing an endpoint outside the sandbox. |
| [`local/`](local/README.md) | **Start here.** DGX Spark setup, default access details, demo prompts, troubleshooting and tools. |
| [`scripts/reset-nemoclaw.sh`](scripts/reset-nemoclaw.sh) | Detects and resets the active local, Spark, or Spark Super NemoClaw setup. |
| `docs/ARCHITECTURE.md` | Architecture and security-boundary documentation. |
| `docs/SETUP.md` | Environment setup and verification guidance. |
| `docs/RUNBOOK.md` | Troubleshooting history and recovery guidance. |
| `docs/NVIDIA_BUG_REPORT.md` | NVIDIA-facing investigation notes and reproduction evidence. |
| `docs/BLOCKER.md` | Blocker tracking and historical context. |

Generate a local deployment from the same cloud-init source. Use `local` for
a normal Linux host or VPS. Use `spark` for DGX Spark with Lightning. Use
`spark-super` for DGX Spark with Nemotron Super.

```bash
python3 scripts/render-local.py --target local
python3 scripts/render-local.py --target spark
python3 scripts/render-local.py --target spark-super
```

The generated files are under `local/generated/` and are not committed. See
[`local/README.md`](local/README.md) for setup instructions.

Use `scripts/reset-nemoclaw.sh [--full] [sandbox]` on the Linux host to reset
the current lab. The default reset keeps n8n and Caddy data. Use
`./reset-nemoclaw.sh --full` from a generated target for one complete lab
runtime cleanup. It keeps `.env`, installed tools, Docker images, and model
cache downloads. It does not prune Docker or delete workspace-level providers.

Some troubleshooting documents may describe older releases or failed approaches.
The current integration and verification status are summarized below.

## Architecture

```text
OpenClaw inside the NemoClaw sandbox
    |
    | incident-mcp skill invokes the generic Node client
    |
    | tools/list discovers the current catalog
    | tools/call invokes a selected tool
    |
OpenShell proxy and network policy
    |
    | HTTPS, restricted by host, port, method, and path
    |
Caddy
    |
n8n MCP Server Trigger: /mcp/incident-tools
    |
Connected tools and their workflows
```

Adding or removing a tool in the published MCP workflow changes what subsequent
discovery returns. No skill or client edit is required.

The skill supplies instructions for using MCP. It does not replace MCP with
hardcoded commands or define the server's tool catalog.

## Security boundaries

OpenShell controls network access. The incident policy permits the Node client
to use POST, GET, and DELETE on the incident MCP path.

The policy uses `protocol: rest` with `enforcement: enforce`. It checks HTTP
methods and paths, not MCP tool names or arguments. The server and workflows
must enforce which actions are authorized.

The client supplies a dedicated workflow MCP bearer token directly. That token
is stored in an owner-only file inside the sandbox, but the agent runs as that
owner and can technically read it. This is an intentional tradeoff from the
managed credential-substitution approach investigated during development.

Never use an n8n administrator API key or owner password as this token.

Protected writes must require server-side human authorization. A skill
instruction to ask permission, or a receipt acknowledging an approval request,
is not an approval gate.

The incident policy is additive. Other baseline rules and presets remain in
place. Review the complete effective policy before claiming that the MCP route
is the sandbox's only reachable destination.

## Quick start: prepare cloud-init

Run these commands from the repository root on your preparation machine,
including macOS.

### 1. Configure deployment credentials

Create a root `.env` containing:

```dotenv
NVIDIA_INFERENCE_API_KEY=nvapi-REPLACE-ME
NEMOCLAW_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
N8N_MCP_TOKEN=REPLACE-WITH-INCIDENT-WORKFLOW-BEARER-TOKEN
N8N_OWNER_PASSWORD_HASH='PASTE_BCRYPT_HASH_HERE'
```

Replace all three values.

`N8N_MCP_TOKEN` must match the Bearer Auth credential used by the incident MCP
workflow during the exercise. It is not an instance-wide management MCP token.

`N8N_OWNER_PASSWORD_HASH` must be a complete bcrypt hash, not a plaintext
password. Keep the single quotes around the hash.

The default n8n owner login email is `student@lab.example`. Its details are
managed through environment variables and reapplied on startup.

### 2. Install the renderer dependencies

Create and activate a Python virtual environment, then install the dependencies
from `scripts/requirements.txt`:

```bash
python3 -m venv .venv-render
source .venv-render/bin/activate
python -m pip install -r scripts/requirements.txt
```

PyYAML provides the `yaml` module imported by the renderer. It is needed only
on the preparation machine. The sandbox MCP client has no npm dependencies.

When opening a new terminal, reactivate the environment before rendering:

```bash
source .venv-render/bin/activate
```

The independent checker in `mcp-test/` has its own requirements file. Its
dependencies are not needed to render cloud-init.

### 3. Render the deployment file

```bash
python scripts/render-cloud-init.py
```

This reads:

- `cloud-init/user-data.yaml`
- Root `.env`
- The files under `nemoclaw-mcp/`

It writes:

```text
cloud-init/user-data-rendered.yaml
```

Supply the **rendered file** to the cloud-init-compatible provisioning system.
Do not deploy the source template directly or run another credential-injection
step over the rendered output.

The rendered file contains deployment credentials and is excluded by
`.gitignore`. Transfer and store it through the provisioning system's approved
credential-handling process.

### 4. Provision one VM first

Inside the VM:

```bash
cloud-init status --wait --long
sudo tail -f /var/log/lab-provision.log
```

Do not roll out the remaining VMs until the full exercise passes on a fresh
instance. Prefer fresh instances from the base image rather than cloning an
already-configured VM with its credentials and runtime identities.

## What provisioning does

Cloud-init:

1. Installs the required host packages and Docker.
2. Starts n8n and Caddy.
3. Configures the n8n owner account.
4. Creates and imports Caddy's CA before NemoClaw onboarding.
5. Installs NemoClaw and creates the OpenClaw sandbox.
6. Runs `nemoclaw-mcp/install.sh` non-interactively.
7. Applies the rendered incident network policy.

`HOST_IP` remains a literal marker in the repository's client and policy
templates. The installer substitutes the VM's actual address before parsing
the URL and writes the rendered files under:

```text
~/.local/state/nemoclaw-mcp/lab/
```

The repository templates are not modified.

The client reads proxy and TLS trust settings from the sandbox runtime. It
does not assume a fixed proxy listener or a version-specific CA file path.

No managed `mcp add` command is run.

## What attendees do

The repository includes starter data and an incident-tools workflow.
Provisioning does not automatically import or publish them.

Attendees follow the exercise to:

1. Sign in to n8n.
2. Import the data tables.
3. Import the starter workflow and its matching credential.
4. Check table references and credential selection.
5. Publish the workflow.
6. Use OpenClaw to discover and call its tools.
7. Change the exposed tool catalog and observe the next discovery.
8. Build and test the human-approval flow for protected changes.

The skill and network policy are already installed by cloud-init. No separate
managed MCP registration is required after the workflow is published.

## Verify the integration

After the incident MCP workflow is published, run these on the VM host.

### Discover the live tools

```bash
timeout --kill-after=3s 75s \
  openshell sandbox exec --no-tty --name lab -- \
  node /sandbox/.openclaw/workspace/skills/incident-mcp/scripts/client.js tools
```

The starter workflow advertises:

- `list_open_incidents`
- `get_incident`
- `search_runbooks`

These names are examples from the starter workflow, not a fixed client allowlist.

### Call a read-only tool

```bash
timeout --kill-after=3s 75s \
  openshell sandbox exec --no-tty --name lab -- \
  node /sandbox/.openclaw/workspace/skills/incident-mcp/scripts/client.js \
  call list_open_incidents '{}'
```

### Verify a forbidden path

```bash
timeout --kill-after=3s 30s \
  openshell sandbox exec --no-tty --name lab -- \
  node /sandbox/.openclaw/workspace/skills/incident-mcp/scripts/client.js \
  probe-path /rest/login
```

Require an OpenShell L7 policy denial. An application-generated authentication
error is not proof that the network policy blocked the request.

Inspect audit events with:

```bash
openshell logs lab --tail --source sandbox
```

### Test through OpenClaw

Connect:

```bash
nemoclaw lab connect
```

In a new chat, ask:

> Use the incident-mcp skill to discover the current MCP tools. Then retrieve
> incident INC-2007, find its matching runbook, and recommend triage steps.
> Use only read-only tools and do not perform remediation.

Check the actual tool execution and returned data, not just the model's claim
that it completed the task.

## Status

### Verified on the running development VM

- NemoClaw `v0.0.112`.
- OpenShell CLI `0.0.106`.
- Sandbox Node `v22.23.2`.
- n8n `2.38.4`.
- Dynamic MCP discovery through the runtime-configured proxy.
- A successful incident-listing tool call returning data.
- OpenShell L7 denial of `/rest/login` using the same Node client.
- OpenClaw using the skill for incident and runbook interaction.

The version string does not establish whether earlier local diagnostic patches
remain in that development installation. The new cloud-init template uses the
official pinned release without applying those patches.

### Still requiring acceptance testing

- The complete updated cloud-init on a fresh VM.
- Adding/removing tools without changing the skill or client.
- The authenticated human-approval and protected-write flow.
- Repeatability across the intended 50-VM deployment.

### Known outstanding issues

NemoClaw's managed MCP integration failed during investigation with
`credential_unavailable` despite healthy registration metadata. Public GitHub
MCP testing also encountered DNS-pin drift and conflicting policy-advisor
drafts. These are not resolved by the custom client; the lab uses a different
client and credential path.

The working client has received HTTP 403 during session DELETE cleanup.
Discovery and read-only calls completed successfully before that warning.
Its cause remains to be investigated. Do not replay a completed tool call
because cleanup failed.

The earlier `v0.0.36` sandbox-build failure is historical context, not the
current blocker for the demonstrated integration.

## Scope

This repository contains the lab infrastructure, starter data, workflow, and
agent integration.

Infrastructure-specific VM recreation scripts are not part of the NVIDIA lab
deliverable. The rendered cloud-init can be supplied to the provisioning
platform used for the event.
