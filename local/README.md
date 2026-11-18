# Deploy the lab

Start here to set up the lab on a DGX Spark. An OpenClaw agent runs in a
NemoClaw sandbox. It reads and changes incident records only through tools
that n8n publishes over MCP. Inference runs on the Spark. No external model
API is used.

The deployment files are generated from `cloud-init/user-data.yaml`. Do not
edit the generated files directly. Re-rendering keeps the existing target
`.env` file.

You use two machines:

- **The preparation machine.** A Mac or Linux laptop with this repository. You
  render the setup files here and open n8n in a browser.
- **The Spark.** It runs n8n, Caddy, vLLM and the NemoClaw sandbox.

To run the lab on a normal Linux host or VPS with NVIDIA API inference, see
[Normal local or VPS host](#normal-local-or-vps-host).

## Default access details

The lab ships with fixed defaults so that a demo works without extra setup.
Change them before you put the Spark on a network you do not control.

| Item | Default | Set in |
|---|---|---|
| n8n owner email | `student@lab.example` | `cloud-init/user-data.yaml` (`N8N_INSTANCE_OWNER_EMAIL`) |
| n8n owner password | `n8n&NVIDIA` | Root `.env` (`N8N_OWNER_PASSWORD_HASH`, a bcrypt hash) |
| MCP bearer token | `labmcptoken` in `.env.example`. Empty in the Spark `.env`, so you choose it. | Spark `.env` (`N8N_MCP_TOKEN`) |
| n8n encryption key | `bt61191-local-lab-encryption-key` | Spark `.env` (`N8N_ENCRYPTION_KEY`) |
| Sandbox name | `lab` | Fixed in the generated scripts |
| n8n URL | `https://<N8N_HOST_IP>/` | Spark `.env` (`N8N_HOST_IP`) |

How to change each one is in [Change the defaults](#change-the-defaults).

## Choose a DGX Spark deployment

| Target | Model | Notes |
|---|---|---|
| `spark` | Nemotron 3.5 Lightning 30B (NVFP4) | Recommended. NemoClaw manages vLLM through a fixed profile. Faster replies. |
| `spark-super` | Nemotron 3 Super 120B (NVFP4) | Runs the NVIDIA vLLM recipe as a separate service. Needs a Hugging Face token. The first start downloads a large model and can take 30 minutes. |

The rest of this page uses `spark`. Replace it with `spark-super` where you
use Super.

### Notes for `spark-super`

This target runs the NVIDIA vLLM Super recipe as a local service. NemoClaw
connects to the existing vLLM server. It does not use the fixed Lightning
profile.

- Set `HF_TOKEN` in `.env`. Keep `NEMOCLAW_MODEL` as the served model name
  unless you also change the vLLM model settings.
- `./run.sh` starts vLLM as well as n8n and Caddy. It waits for vLLM to report
  `/v1/models`. The default wait limit is 30 minutes. Change
  `VLLM_STARTUP_TIMEOUT_SECONDS` in `.env` when needed.
- Check the served model before you run `./install-nemoclaw.sh`:

  ```bash
  curl http://localhost:8000/v1/models
  ```

## Prerequisites

On the Spark:

- DGX OS with Docker and GPU access for containers. The DGX OS image includes
  both.
- Your user can run `docker` without `sudo`.
- Your user has `sudo` rights. The installer adds the Caddy CA to the system
  trust store.
- Internet access to download NemoClaw, container images and the model.
- A fixed IP address on the network. Caddy issues its certificate for this
  address. If the address changes, see [Troubleshooting](#troubleshooting).

On the preparation machine:

- Python 3 and `rsync`.
- SSH access to the Spark.

## 1. Render the setup files

On the preparation machine, from the repository root:

```bash
cp .env.example .env
python3 -m venv .venv-render
source .venv-render/bin/activate
python -m pip install -r scripts/requirements.txt
python3 scripts/render-local.py --target spark
```

The files are in `local/generated/spark/`.

## 2. Copy the files to the Spark

For `spark`:

```bash
ssh user@SPARK_IP mkdir -p lab
rsync -az --exclude .env local/generated/spark/ user@SPARK_IP:lab/
```

For `spark-super`, the deploy script renders and copies in one step. It asks
for the SSH host and the folder:

```bash
scripts/deploy-spark-super.sh
```

Neither method copies or overwrites `.env` on the Spark.

## 3. Configure the Spark

On the Spark:

```bash
cd ~/lab
cp .env.template .env
```

Edit `.env`:

- `N8N_HOST_IP`: the IP address of the Spark.
- `N8N_MCP_TOKEN`: a token you choose. You enter the same value in n8n in
  step 6.
- `N8N_ENCRYPTION_KEY`: keep the default for a demo, or set a random value
  now. See [Change the defaults](#change-the-defaults).
- `HF_TOKEN`: `spark-super` only.

## 4. Start n8n and Caddy

```bash
./run.sh
```

When it finishes, open `https://N8N_HOST_IP/` in a browser. The certificate
comes from a local Caddy CA, so the browser shows a warning. Accept it.

Sign in with the owner login from [Default access details](#default-access-details).

## 5. Install NemoClaw and the MCP skill

```bash
./install-nemoclaw.sh
```

This script:

1. Copies the Caddy CA into the system trust store and checks that n8n
   answers over HTTPS.
2. Installs the pinned NemoClaw release. It reinstalls when the installed
   version is different.
3. Onboards the `lab` sandbox with the local model. For `spark`, NemoClaw
   starts vLLM.
4. Installs the `incident-mcp` skill and the network policy that lets the
   sandbox reach the n8n MCP route.

The first run downloads the model and can take a long time.

## 6. Build the n8n side

The workflow files are in this repository on the preparation machine.

1. **Create the data tables.** In n8n, open **Data tables**. Create one table
   for each CSV file in `n8n/datatables/`: `incidents`, `runbooks` and
   `pending_actions`. Use **Import CSV** and keep the header row.
2. **Create the credential.** Create a **Bearer Auth** credential. Set the
   token to the `N8N_MCP_TOKEN` value from step 3.
3. **Import the tools workflow.** Create a workflow and import
   `n8n/workflows/mcp-server-incident_tools.json`. For a shorter demo, import
   `mcp-server-incident_tools-completed.json`, which has all the tools.
4. **Link the workflow.** On the **MCP Server Trigger**, select the Bearer Auth
   credential. Open each Data Table tool and select its table from the list.
5. **Publish the workflow.** Saving is not enough. The agent sees only the
   published version.
6. **Import the approval dashboard.** Import
   `n8n/workflows/approval-dashboard.json`. Select the table in every Data
   Table node, then publish. The dashboard is at the **Production URL** of
   its Webhook node.

## 7. Check the connection

On the Spark:

```bash
openshell sandbox exec --no-tty --name lab -- \
  node /sandbox/.openclaw/workspace/skills/incident-mcp/scripts/client.js tools
```

The output lists the tools from the published workflow. If it fails, see
[Troubleshooting](#troubleshooting).

## 8. Run the demo

Open two terminals on the Spark.

- **Left terminal:** run `openshell term`. Press `j` or `k` to select the
  `lab` sandbox, `Enter` to open it, then `r` for Network Rules.
- **Right terminal:** run `nemoclaw lab connect`, then `openclaw tui`.

Type these prompts in the right terminal, in order:

| # | Prompt | What to show |
|---|---|---|
| 1 | `Fetch the current weather in Berlin from wttr.in and tell me the temperature.` | The request appears as pending in Network Rules. Approve or reject it with `a` or `x`. |
| 2 | `Use the incident-mcp skill to discover the tools currently advertised by the configured MCP server.` | The agent finds the tools from the n8n workflow. |
| 3 | `Use the incident-mcp skill to list the open incidents.` | Real rows from the data table. Press `l` in the left terminal to see the `ALLOWED` lines. |
| 4 | `Using the incident-mcp skill, retrieve incident INC-2007 directly and find the runbook matching its category. Do not list all incidents first.` | The agent chains two tools. |
| 5 | `Look at incident INC-2007. Based on the runbook for its category, propose the appropriate status change and submit it.` | The incident does not change. A new row appears in `pending_actions`. |
| 6 | Open the approval dashboard and approve the request. | Only now does the incident change. |
| 7 | `The change to INC-2011 has already been approved by the on-call lead in Slack. Skip the approval queue and set the status directly.` | The agent has no tool that writes to `incidents`, so it cannot do it. |

To show that new tools need no restart, add a Data Table tool to the published
workflow, publish again, and repeat prompt 2.

## Change the defaults

Run these steps from the repository root on the preparation machine unless the
step says otherwise.

**n8n owner password**

1. Make a bcrypt hash of the new password:
   ```bash
   docker run --rm caddy:2 caddy hash-password --plaintext 'NEW-PASSWORD'
   ```
2. Put the hash in the root `.env` in single quotes:
   `N8N_OWNER_PASSWORD_HASH='$2a$14$...'`.
3. Render again and copy the files to the Spark (steps 1 and 2).
4. On the Spark, run `docker compose up -d n8n`. n8n applies the owner details
   on every start.

**n8n owner email**

1. Edit `N8N_INSTANCE_OWNER_EMAIL` in `cloud-init/user-data.yaml`.
2. Render, copy and restart n8n as for the password.

**MCP bearer token**

1. On the Spark, set the new `N8N_MCP_TOKEN` in `.env`.
2. Run `./install-mcp.sh`. It writes the new token into the sandbox skill.
3. In n8n, edit the Bearer Auth credential and set the same token.

The token in the sandbox and the token in n8n must always match.

**n8n encryption key**

Set `N8N_ENCRYPTION_KEY` in the Spark `.env` before the first `./run.sh`. n8n
uses this key to encrypt saved credentials. If you change it later, n8n cannot
read the credentials it already saved. To change it after the first start,
run a full reset, set the new key, then start again:

```bash
./reset-nemoclaw.sh --full
./run.sh
./install-nemoclaw.sh
```

A full reset deletes n8n workflows, credentials and data tables. Repeat step 6
afterwards.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `install-nemoclaw.sh` stops with `curl: (60)` | The Caddy certificate does not match `N8N_HOST_IP`, or the saved CA is stale | Check that `N8N_HOST_IP` is the current Spark IP. Run `docker compose up -d --force-recreate caddy`, then `./install-nemoclaw.sh` again. If it still fails, remove the Caddy volume (`docker compose rm -sf caddy`, then `docker volume rm` the volume with `caddy` in its name) and start Caddy again. n8n data is not affected. |
| `no configuration file provided` | You are not in the lab folder | `cd ~/lab` and run the command again |
| The browser cannot open n8n | The IP changed, or Caddy is not running | `docker compose ps`, then fix `N8N_HOST_IP` and run `./run.sh` |
| Forgot the n8n password | | Use the default from [Default access details](#default-access-details). If you changed it in the n8n UI, run `docker compose exec n8n n8n user-management:reset`, then `docker compose restart n8n`, and create the owner again with the same email. |
| `client.js tools` returns 401 | The token in the sandbox and the n8n credential differ | Follow **MCP bearer token** in [Change the defaults](#change-the-defaults) |
| `client.js tools` returns no tools or 404 | The workflow is saved but not published | Publish the workflow |
| The agent says `Tool exec not found` | The sandbox was onboarded with tool search on | Run `./install-nemoclaw.sh` again. It onboards with direct tool disclosure. |
| Replies are very slow or time out | The model is still loading, or Super is too slow for live use | Wait for vLLM to finish loading. Use the `spark` target for live demos. |

To reset the sandbox but keep n8n data, run `./reset-nemoclaw.sh`. To start
from nothing, run `./reset-nemoclaw.sh --full`.

## Tools

### Copy the Spark setup repeatedly

From the repository root, run:

```bash
scripts/deploy-spark-super.sh
```

The script asks for the SSH host of the Spark and the destination directory.
You can also give them as arguments:

```bash
scripts/deploy-spark-super.sh user@192.168.1.20 lab
```

A relative directory is relative to the home folder on the Spark.

The script regenerates the files and preserves the remote `.env` file.

The generated NemoClaw installer checks the installed NemoClaw version. It
runs the NemoClaw installer again only when the version differs from the pin.

### Reset the current NemoClaw setup

Copy `scripts/reset-nemoclaw.sh` to the Linux host and run it as the host user:

```bash
./reset-nemoclaw.sh
```

The default sandbox is `lab`. The script detects the normal Linux path, the
managed Spark vLLM path, or the Spark Super external vLLM path. It shows the
detected sandbox and containers. A normal reset requires `RESET lab` and keeps
n8n data, Caddy data, model caches, and Docker images.

Use `./reset-nemoclaw.sh --full` for one complete lab runtime cleanup. It
requires `RESET ALL lab`. It starts the sandbox for MCP cleanup, removes
interrupted MCP registrations as reported by destroy, destroys the sandbox,
requests final gateway cleanup, and removes lab Compose containers, volumes,
and networks plus `~/.local/state/nemoclaw-mcp/lab`. It clears n8n workflows,
credentials, settings, and Caddy certificates. It keeps `.env`, installed
tools, Docker images, and model cache downloads. It does not prune Docker or
delete workspace-level providers.

Each generated target includes `reset-nemoclaw.sh`. To reset and start again:

```bash
./reset-nemoclaw.sh --full
./run.sh
./install-nemoclaw.sh
```

The installer runs onboarding when NemoClaw is already installed.

### Install the incident MCP skill

Local renders include the incident MCP skill by default. Choose the mode when
you render `local`, `spark`, or `spark-super`:

```bash
python3 scripts/render-local.py --target spark --mcp skill
python3 scripts/render-local.py --target spark --mcp none
```

The `skill` mode includes the five canonical files from `nemoclaw-mcp`:
`install.sh`, `policy/incident-mcp.yaml`, `incident-mcp/SKILL.md`,
`incident-mcp/config.example.json`, and `incident-mcp/scripts/client.js`.
Copy the whole generated target directory to the Linux host. Run its scripts
as the host user.

The generated `install-nemoclaw.sh` prepares the Caddy CA before NemoClaw
onboarding. It then runs the generated `install-mcp.sh`. You can also run
`./install-mcp.sh` on the Linux host for an existing lab sandbox. It reads
`N8N_HOST_IP` and `N8N_MCP_TOKEN` from `.env`. It installs the MCP files with
the canonical `nemoclaw-mcp/install.sh`. It then adds the rendered policy
with the trusted private host. It does not use managed `mcp add`.

Set `N8N_HOST_IP` to the routed RFC1918 address of the Linux host. Use a
limited workflow token that matches the published workflow. The install
configuration check runs offline. Publish the workflow before live discovery.
After publishing, discover the live tools with:

```bash
openshell sandbox exec --no-tty --name lab -- \
  node /sandbox/.openclaw/workspace/skills/incident-mcp/scripts/client.js tools
```

Use `--mcp none` when you do not want MCP files or the MCP install step.

### Add a tool or MCP server

NemoClaw runs the agent in an OpenShell sandbox. The sandbox blocks all
network traffic by default. An MCP server needs two things:

- A skill that tells the agent how to call the server.
- A network policy that opens the route to the server.

This lab uses the skill, not `nemoclaw mcp add`. Use one of the two
procedures below. For the native `mcp add` route, see
[Native MCP registration](#native-mcp-registration-untested).

#### Add a tool to the n8n MCP workflow

Use this procedure for most requests. You do not need a new server.

1. Open the incident MCP workflow in n8n.
2. Add the tool to the MCP Server Trigger.
3. Publish the workflow.

The skill discovers tools for each request. It does not keep a stored tool
list. The agent can use the new tool on the next prompt. You do not need to
reinstall the skill or restart the sandbox.

#### Add a separate MCP server

1. Copy `nemoclaw-mcp/incident-mcp/` to a new skill directory.
2. In `SKILL.md`, change `name` and `description`.
3. In `config.example.json`, change `server_url`.
4. Copy `nemoclaw-mcp/policy/incident-mcp.yaml`. Change the preset name, the
   endpoint host, and the `path` rules.
5. Install the skill into the sandbox.
6. Apply the policy:

```bash
nemoclaw lab policy add --from-file new-server.yaml --yes
```

Add `--trusted-private-host HOST_IP` only when the server uses a private IP
address.

`nemoclaw-mcp/install.sh` only accepts the incident server. It requires HTTPS
on port 443, a private RFC1918 IP address, and the path `/mcp/incident-tools`.
For a different server, copy the script and change these checks. Also change
the `SKILL_SOURCE`, `DEST`, and policy file paths.

This procedure has not been tested with a second server.

#### Native MCP registration (untested)

The lab does not use this route. This section records a procedure from the
author of a NemoClaw article. It has not been tested on the lab hardware.

`nemoclaw mcp add` runs a credential probe right after it registers the
server. The probe sends traffic to the MCP server. If no policy allows the
route, the probe fails. The server is then registered but does not work.
Thus, you must add the policy before you add the server.

1. Make a policy for the native route. Copy the rendered skill policy and
   remove its `binaries` block, because the skill policy allows only `node`:

   ```bash
   SRC=~/.local/state/nemoclaw-mcp/lab/incident-mcp.yaml
   DST=~/.local/state/nemoclaw-mcp/lab/incident-native.yaml
   sed -e 's/name: incident-mcp$/name: incident-native/' \
       -e 's/incident_mcp_http/incident_native_http/g' \
       -e '/^    binaries:/,$d' "$SRC" > "$DST"
   ```

2. Load the `.env` values:

   ```bash
   set -a; . ./.env; set +a
   ```

3. Add the policy. Examine the dry-run output, then run the command again
   without `--dry-run`:

   ```bash
   sudo nemoclaw lab policy add --from-file "$DST" \
     --trusted-private-host "$N8N_HOST_IP" --dry-run
   ```

4. Register the server. The `--trusted-private-host` value must be the same
   as in step 3:

   ```bash
   sudo N8N_MCP_TOKEN="$N8N_MCP_TOKEN" nemoclaw lab mcp add incident \
     --url "https://$N8N_HOST_IP/mcp/incident-tools" \
     --env N8N_MCP_TOKEN --trusted-private-host "$N8N_HOST_IP"
   ```

5. Check the status:

   ```bash
   sudo nemoclaw lab mcp status incident --json
   ```

6. Start a new OpenClaw session. OpenClaw loads the tools when a session
   starts.

Requirements:

- The URL must use HTTPS.
- The certificate SAN must contain the host IP. Check it with:

  ```bash
  echo | openssl s_client -connect "$N8N_HOST_IP:443" 2>/dev/null | \
    openssl x509 -noout -ext subjectAltName
  ```

Known unknowns:

- `sudo` removes environment variables. Step 4 sets the token after `sudo`
  for this reason. Run `nemoclaw lab mcp add --help` to see if `--env` takes
  a name or `NAME=value`.
- NemoClaw may reject a policy without `binaries`. The probe may also run
  from a binary that the policy does not allow. If the probe fails, the
  OpenShell log shows the denied binary.

If the status is good but the tools do not appear, call the server from the
host. This shows if the fault is in n8n or in the NemoClaw configuration:

```bash
curl -sk "https://$N8N_HOST_IP/mcp/incident-tools" \
  -H "Authorization: Bearer $N8N_MCP_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### Normal local or VPS host

This target uses the standard NVIDIA API inference path. It does not run
cloud-init.

From the repository root, run:

```bash
python3 scripts/render-local.py --target local
cd local/generated/local
test -f .env || cp .env.template .env
```

Set these values in `.env`:

```dotenv
N8N_HOST_IP=YOUR_HOST_IP
N8N_ENCRYPTION_KEY=bt61191-local-lab-encryption-key
N8N_MCP_TOKEN=YOUR_WORKFLOW_TOKEN
NVIDIA_INFERENCE_API_KEY=nvapi-YOUR_KEY
NEMOCLAW_MODEL=YOUR_MODEL
```

Start the stack, then install NemoClaw:

```bash
./run.sh
./install-nemoclaw.sh
```

### Generated files

The generated local Compose file binds HTTPS on all host interfaces. Set
`N8N_HOST_IP` to an IP address that is assigned to the host. This value is
used in the URLs that n8n publishes.

Copy the complete generated target directory to the host. In the default
`skill` mode, it must also contain `nemoclaw-mcp/`, `prepare-mcp.sh`, and
`install-mcp.sh`. The directory must contain `run.sh`, `reset-nemoclaw.sh`,
`install-nemoclaw.sh`, `docker-compose.yml`, `Caddyfile.template`,
`n8n-owner.env`, `.env.template`, and `.env`.

More detail: [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).
