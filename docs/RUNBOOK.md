# Runbook

Bugs found while building this lab, in the order they surfaced. Symptom, cause, fix.
Read this before re-diagnosing something from scratch.

## Fixed

### Caddy proxied to a dead IP

**Symptom:** n8n unreachable through Caddy on every VM.
**Cause:** an earlier draft hardcoded `reverse_proxy 172.31.7.20:5678`, a private IP with
no relationship to this VM's docker-compose network. Containers reach each other by
compose service name, not by a fixed host IP.
**Fix:** `reverse_proxy n8n:5678` (the compose service name).

### `NEMOCLAW_INSTALL_TAG` set to a version that never existed

**Symptom:** `fatal: couldn't find remote ref v0.36.0`.
**Cause:** NemoClaw's git tags run `v0.0.X` (confirmed with
`git ls-remote --tags https://github.com/NVIDIA/NemoClaw.git`; latest at the time was
`v0.0.120`). `v0.36.0` was never a real tag.
**Fix:** pinned to `v0.0.36`, which does exist
(https://github.com/NVIDIA/NemoClaw/releases/tag/v0.0.36) and is the version this lab
was actually built and tested against.

### `npm: command not found` during NemoClaw's own dependency install

**Symptom:** NemoClaw's installer gets past cloning and initializing, then fails at
"Installing NemoClaw dependencies" with `bash: line 1: npm: command not found`, even
though the same shell just reported `Runtime OK: Node.js v22.23.2, npm 10.9.8`.
**Cause:** nvm only puts `node`/`npm` on `PATH` in a shell that has sourced `nvm.sh`.
NemoClaw's dependency-install step runs in a subshell that does not source it, so it
cannot see them regardless of what the parent shell can see.
**Fix:** after `nvm install 22`, symlink `node`/`npm`/`npx` into `/usr/local/bin`, so
they resolve in every shell, nvm-aware or not.

### `$NVM_DIR` silently corrupted to a literal string

**Symptom:** `bash: $NVM_DIR/nvm.sh: No such file or directory`, and `echo $NVM_DIR`
printing the literal text `$HOME/.nvm` (dollar sign kept, not expanded to a real path).
**Cause:** an intermediate draft used an unquoted heredoc (`<<NEMOEOF`, no quotes around
the delimiter) with `\$HOME`/`\$NVM_DIR` inside it, relying on the outer shell to strip
exactly one backslash before handing the body to the inner shell. That is correct only
through that one specific execution path. Copy-pasting the same lines into an
interactive terminal (which most people debugging live will do) does not go through
that stripping step, so the backslash survives and the dollar sign stays literal.
**Fix:** every heredoc and `bash -lc` body in `provision.sh`'s embedded script is now
single-quoted (fully literal, no expansion of any kind). Any value the outer script
needs to supply goes in as a positional argument after `_` (`... bash -lc '...' _ "$value"`,
read inside as `$1`), never string-interpolated into the body. What is on screen is
exactly what runs, regardless of how it is invoked.

### Node-version guard never passed

**Symptom:** the nvm/node install block re-ran on every boot even after node was
already installed.
**Cause:** the guard checked `-ge 24`, but the block installs node 22. `22 -ge 24` is
always false.
**Fix:** guard checks `-ge 22`, matching what is actually installed.

### `n8n-setup/docker-compose.yaml` (early draft, since removed) was invalid YAML

**Symptom:** would have failed at `docker compose up` with an indentation error.
**Cause:** `volumes:` and `services:` were indented as children of `networks:` instead
of top-level siblings. Confirmed with `python3 -c "import yaml; yaml.safe_load(...)"`;
it never actually parsed.
**Fix:** this lab dropped the Postgres/custom-network design entirely in favor of a
single n8n container on the default compose network (see "Simplifications" below), so
the file no longer exists. Noted here in case a future revision reintroduces
multi-container compose: check indentation with a real YAML parser, not by eye.

## Simplifications

**No Postgres, no custom docker network.** n8n runs single-container with its default
SQLite storage, on the default compose bridge network, addressed by service name
(`n8n:5678`). A 105-minute lab with 50 identical throwaway VMs has no need for a
production-shaped Postgres backend, and every container-ordering/connection-string bug
a second service can introduce is a bug this lab does not need to have.

## Open

### `nemoclaw mcp add` registration: real command found, not yet run successfully

Earlier revisions of this doc said `nemoclaw mcp add` was "confirmed broken." That was
based on the original report from before this repo checked NVIDIA's own documentation.
As of 2026-09-08, the correct, documented command is (see
docs.nvidia.com/nemoclaw/user-guide/openclaw/manage-sandboxes/mcp-servers/add-an-mcp-server):

```
nemoclaw <sandbox-name> mcp add <server-name> --url <https-url> --env <VAR>
```

Requirements from that page: HTTPS only, no loopback endpoint (`127.0.0.1`/`::1`
rejected), exactly one `--env` bearer credential per server (a variable NAME, resolved
from the invoking shell's environment, not a literal value), and a
`--trusted-private-host <host>` flag for a self-signed/internal certificate like this
lab's Caddy cert. This single command both registers the server with OpenClaw AND
generates the matching OpenShell network-policy grant; no separate `openshell policy
set` call should be needed once it succeeds.

`cloud-init/user-data.yaml` Step 10 now tries this command first
(`nemoclaw lab mcp add internal-tools --url https://<vm-ip>/mcp/internal-tools --env
N8N_INTERNAL_TOOLS_TOKEN --trusted-private-host <vm-ip>`), falling back to the
network-policy-only grant (below) if it fails or if `N8N_INTERNAL_TOOLS_TOKEN` isn't
set yet.

**Not yet verified: whether this actually works against the pinned `v0.0.36`.** It has
not been run successfully in this lab as of this writing (the sandbox build itself was
still failing on the Patch-4 bug above at the time this was written). If it also fails
once that's fixed, that failure is the real thing to report to NVIDIA, since this is now
confirmed to be the correct invocation, not a guess.

**Fallback: network-policy-only grant.** If registration keeps failing, `provision.sh`
grants the sandbox raw network access to n8n's MCP endpoint via OpenShell's own sandbox
policy (`openshell policy set lab --policy <file> --wait`; same mechanism
`agent-access-gateway`'s `policy/sandbox_policy.yaml` and `install.sh` Step 8 use).
**This does NOT register an MCP server** - it only means the host/port/path isn't
blocked. OpenClaw's `tools/list` will not show a server that was never registered, so
this is a debugging aid (confirm the route works) rather than a substitute for the real
command above.

### Incident-tools workflow not built

`N8N_MCP_PATH` defaults to `/mcp/internal-tools`, matching an MCP Server Trigger
workflow that does not exist yet in this repo. It is exercise content, authored live.
Until it exists and is published, the network-policy grant step in `provision.sh` has
nothing real to reach (it will succeed, but the path it opens serves nothing).
