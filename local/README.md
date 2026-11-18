# Local deployments

Local deployments are generated from `cloud-init/user-data.yaml`. Do not edit
the generated files directly.

Re-rendering keeps the existing target `.env` file.

## Reset the current NemoClaw setup

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

## Copy the Spark setup repeatedly

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

## Install the incident MCP skill

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

## Add a tool or MCP server

NemoClaw runs the agent in an OpenShell sandbox. The sandbox blocks all
network traffic by default. An MCP server needs two things:

- A skill that tells the agent how to call the server.
- A network policy that opens the route to the server.

This lab uses the skill, not `nemoclaw mcp add`. Use one of the two
procedures below. For the native `mcp add` route, see
[Native MCP registration](#native-mcp-registration-untested).

### Add a tool to the n8n MCP workflow

Use this procedure for most requests. You do not need a new server.

1. Open the incident MCP workflow in n8n.
2. Add the tool to the MCP Server Trigger.
3. Publish the workflow.

The skill discovers tools for each request. It does not keep a stored tool
list. The agent can use the new tool on the next prompt. You do not need to
reinstall the skill or restart the sandbox.

### Add a separate MCP server

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

### Native MCP registration (untested)

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

## Normal local or VPS host

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

## DGX Spark

This target uses the local vLLM profile for Nemotron 3.5 Lightning. It does
not use NVIDIA API inference.

From the repository root, run:

```bash
python3 scripts/render-local.py --target spark
cd local/generated/spark
test -f .env || cp .env.template .env
```

Set `N8N_HOST_IP`, `N8N_ENCRYPTION_KEY`, and `N8N_MCP_TOKEN` in `.env`. The
generator sets `NEMOCLAW_MODEL` and the DGX Spark profile from the canonical
configuration.

Start the stack, then install NemoClaw:

```bash
./run.sh
./install-nemoclaw.sh
```

Both targets use the sandbox name `lab` and the same n8n and Caddy stack.

## DGX Spark with Nemotron Super

This target runs the NVIDIA vLLM Super recipe as a local service. NemoClaw
connects to the existing vLLM server. It does not use the fixed Lightning
profile.

From the repository root, run:

```bash
python3 scripts/render-local.py --target spark-super
cd local/generated/spark-super
test -f .env || cp .env.template .env
```

Set `N8N_HOST_IP`, `N8N_ENCRYPTION_KEY`, `N8N_MCP_TOKEN`, and `HF_TOKEN` in
`.env`. Keep `NEMOCLAW_MODEL` as the served model name unless you also change
the vLLM model settings.

Start vLLM and the n8n stack:

```bash
./run.sh
```

`run.sh` waits for vLLM to report `/v1/models`. The default wait limit is 30
minutes. Change `VLLM_STARTUP_TIMEOUT_SECONDS` in `.env` when needed.

Wait for vLLM to finish loading. Check the served model:

```bash
curl http://localhost:8000/v1/models
```

Then install NemoClaw and connect it to the existing server:

```bash
./install-nemoclaw.sh
```

The generated local Compose file binds HTTPS on all host interfaces. Set
`N8N_HOST_IP` to an IP address that is assigned to the host. This value is
used in the URLs that n8n publishes.

Copy the complete generated target directory to the host. In the default
`skill` mode, it must also contain `nemoclaw-mcp/`, `prepare-mcp.sh`, and
`install-mcp.sh`. The directory must contain `run.sh`, `reset-nemoclaw.sh`,
`install-nemoclaw.sh`, `docker-compose.yml`, `Caddyfile.template`,
`n8n-owner.env`, `.env.template`, and `.env`.
