# Architecture

## What one VM runs

- **NemoClaw sandbox** (`nemoclaw onboard`), running OpenClaw as the agent, on OpenShell.
- **n8n**, single container, SQLite storage. No Postgres: this is a 105-minute lab, not
  a production deployment, and SQLite removes a whole class of container-ordering and
  connection-string bugs for no loss attendees would notice.
- **Caddy**, reverse proxy in front of n8n, terminating TLS with its own internal CA so
  the VM has a real `https://<vm-ip>/` address instead of plain HTTP.

All three come up from one `cloud-init/user-data.yaml`, so every VM is identical.

## How the agent reaches n8n's tools

There are two different ways an agent can end up with tools that live in n8n. This lab
uses the first. It is worth knowing the second exists, and why it was not chosen here.

### 1. Native MCP registration (what this lab uses)

OpenClaw's own MCP client registers n8n's MCP Server Trigger endpoint directly. The
agent's `tools/list` reflects whatever tool nodes are wired to that trigger, live: add a
node and publish, and the next `tools/list` call sees it with no agent restart. This is
the whole point of the lab's Exercise 2.5 ("you did not restart the agent").

The real command, confirmed against NVIDIA's own docs
(docs.nvidia.com/nemoclaw/user-guide/openclaw/manage-sandboxes/mcp-servers/add-an-mcp-server,
checked 2026-09-08), is `nemoclaw <sandbox-name> mcp add <server-name> --url <https-url>
--env <VAR>`, not the bare `nemoclaw mcp add` this repo assumed earlier. It requires
HTTPS, rejects loopback endpoints, and requires exactly one `--env` bearer credential
per server (read from the invoking shell's environment by variable name). It also
generates the sandbox's network-policy grant itself, scoped to that one destination, so
no separate `openshell policy set` call is needed once it succeeds. See
`docs/RUNBOOK.md` for what is still unverified (whether this command actually works
against the pinned NemoClaw version, since it has not been run successfully yet), and
`cloud-init/user-data.yaml` Step 10 for the invocation and its fallback.

### 2. A skill that calls MCP on the agent's behalf (not used here)

The `agent-access-gateway` repo this lab borrows several patterns from does not
register n8n as an MCP server at all. Its agent runs a skill (a script the agent
invokes) that itself speaks MCP to n8n, so every call funnels through one audited path.

That pattern solves a different problem: a single, auditable choke point regardless of
how many tools exist behind it. It does not naturally give live tool discovery the way
native registration does, unless the skill itself reimplements `tools/list` polling,
which is more engineering than this lab's schedule allows. Use it as a reference for the
network-policy pattern (see below), not for how the agent finds its tools.

## Registration vs. network policy

Registering an MCP server and granting network access to it are conceptually two
separate steps, though `nemoclaw mcp add` (per NVIDIA's docs) does both at once: it
writes the endpoint into OpenClaw's config AND generates the matching OpenShell network
policy. `cloud-init/user-data.yaml` Step 10 tries that command first.

If it fails (unset credential, or an actual bug), the fallback is network-policy-only:
`openshell policy set <sandbox> --policy <file> --wait`, scoped to exactly one host, one
port, one path, with `protocol: rest` and explicit method rules. This is the same
mechanism `agent-access-gateway`'s `install.sh` (Step 8) and `policy/sandbox_policy.yaml`
use. It only grants reachability; OpenClaw's `tools/list` will not see a server that was
never registered this way. It exists so the route can be confirmed working independent
of the registration step, not as a substitute for it.

## What this repo does not build

The incident-tools MCP Server Trigger workflow (Data Table nodes reading a `pending_actions`
and `incidents` table, `$fromAI` parameters, the Approval Console form) is the exercise
content, authored live during the lab. This repo's job ends at "n8n and the sandbox are up
and can reach each other"; the workflow inside n8n is not checked in here.
