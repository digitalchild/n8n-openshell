# Setup

## Prerequisites

- A cloud-init-compatible VM image or provisioning tool (this was tested against a plain
  Ubuntu cloud image; anything that accepts `#cloud-config` user-data works).
- An NVIDIA API key (`nvapi-...`) from build.nvidia.com, with access to
  `nvidia/nemotron-3.5-lightning-30b-a3b` (the only model this lab uses; the older
  `nemotron-3-nano-30b-a3b` / `nemotron-3-super-120b-a12b` are deprecated).
- Outbound internet from the VM at boot time, to: Docker's apt repo, GitHub (NemoClaw
  source), NVIDIA's inference endpoint, npmjs registry (nvm/node install). A workshop
  venue's wifi is the most common single point of failure here; test on the actual
  venue network before the day, not just at your desk.

## Before boot

Edit `cloud-init/user-data.yaml`:

1. `write_files: /home/nvidia/.config/nemoclaw/env`: put your real NVIDIA API key in
   both `NVIDIA_INFERENCE_API_KEY` and `NVIDIA_API_KEY`.
2. `write_files: /home/nvidia/lab/.env`: `N8N_MCP_PATH` defaults to `/mcp/internal-tools`.
   Change it only if the incident-tools MCP Server Trigger workflow you build uses a
   different webhook path.
3. Leave `N8N_OWNER_EMAIL` / `N8N_OWNER_PASSWORD` as-is unless you have a reason to
   change them; they are this VM's own n8n login, not shared with anything external.

## Boot one VM and watch it

Feed `cloud-init/user-data.yaml` to your provisioning tool as user-data, then:

```bash
ssh nvidia@<vm-ip>
tail -f /var/log/lab-provision.log
```

It finishes with `=== lab provision finished ... ===`. Expect several minutes: Docker
install, node/nvm install, NemoClaw clone and onboard, n8n image pull, cert trust.

## Verify

```bash
# n8n reachable through Caddy's TLS
curl -ksf https://<vm-ip>/healthz && echo OK

# sandbox exists and NemoClaw is onboarded
sudo -u nvidia bash -lc 'openshell sandbox list'

# sandbox network policy present (only if N8N_MCP_PATH was set before boot)
sudo -u nvidia bash -lc 'openshell policy get lab'
```

If any of these fail, `docs/RUNBOOK.md` has the specific bugs already found and fixed
in this cloud-init, and the ones still open. Check there before re-debugging from
scratch: several of these symptoms (`npm: command not found`, a `$NVM_DIR` that
resolves to a literal, non-expanded string) have already been diagnosed once.

## Cloning to 50 VMs

Nothing in `cloud-init/user-data.yaml` is written per-VM: `VM_IP` is detected at boot and
Caddy's config is generated from it automatically, so no editing is needed per machine.
The same user-data works unmodified on every VM. Bake it into a golden image and clone,
or feed the identical user-data to 50 fresh boots; either way, there is exactly one file
to keep in sync.
