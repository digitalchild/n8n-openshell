# NemoClaw / OpenShell MCP Integration Failure Report

## Summary

We are blocked on authenticated MCP access from an OpenClaw sandbox managed by NemoClaw.

Two failures have been observed:

1. **MCP credential resolution fails** with `credential_unavailable`, despite matching provider attachment, endpoint binding, and successful delivery of a revision-scoped placeholder to a fresh sandbox process.
2. **NemoClaw sandbox execution subsequently hangs**, including a trivial `/bin/echo` command. Host-side timeouts terminate the invocations with exit code `124`.

Additionally, OpenClaw advertises `/mcporter` in autocomplete, but the agent reports that the corresponding skill is unavailable.

We need a verified remediation or known-good image/version combination for a training lab with an imminent submission deadline.

## Environment

| Component | Observed configuration |
|---|---|
| NemoClaw | `v0.0.112`, with two local diagnostic patches described below |
| OpenShell CLI | `0.0.106` |
| Agent | OpenClaw |
| Sandbox | `lab` |
| Active gateway | `nemoclaw` |
| MCP adapter | mcporter |
| Network policy tier | Restricted |
| MCP transport | Streamable HTTP over HTTPS |
| MCP destination | Private RFC1918 IPv4 address, explicitly trusted |
| Inference model shown in OpenClaw | `nvidia/nemotron-3.5-lightning-30b-a3b` |

The running gateway and supervisor binary versions have **not** been independently verified against the CLI version.

Endpoint used during testing:

```text
https://192.168.0.240/mcp/incident-tools
```

No credential values are included in this report.

## 1. Initial failure: diagnostic builders reject a registered private endpoint

The managed registration initially reported healthy state:

```text
private address pins: match
provider attached: yes
provider credentials: ready
policy: present
adapter: registered
env: ready
```

However, tool discovery was skipped:

```text
tool discovery skipped: no valid managed endpoint is available
```

Credential probing was also skipped:

```text
no credential binding or safe endpoint to probe
```

### Source finding

In the `v0.0.112` source, both diagnostic builders call:

```typescript
normalizeMcpServerUrl(entry.url)
```

without supplying the persisted `trustedPrivateHost`.

Affected files:

```text
src/lib/actions/sandbox/mcp-bridge-resolution-probe.ts
src/lib/actions/sandbox/mcp-bridge-tool-discovery.ts
```

The ordinary status URL check does supply that declaration. This explains why status accepts the registration while the diagnostic builders reject the private-IP URL.

### Local modifications

Both builder parameter types were extended to include:

```typescript
trustedPrivateHost
```

Both normalization calls were changed to:

```typescript
normalizeMcpServerUrl(entry.url, {
  trustedPrivateHosts: entry.trustedPrivateHost
    ? [entry.trustedPrivateHost]
    : undefined,
})
```

The modified CLI was built from the pinned source checkout.

Afterward, diagnostics progressed from pre-request skips to actual HTTP responses.

**These are local, non-upstream patches. They affect diagnostic URL validation only. No OpenShell credential-resolution code was modified.**

## 2. Current failure: credential resolution denied after policy approval

Command:

```bash
nemoclaw lab mcp status incident-tools --tools --json
```

Relevant output:

```json
{
  "server": "incident-tools",
  "agent": "openclaw",
  "warnings": [],
  "support": {
    "supported": true,
    "mode": "bridge",
    "adapter": "mcporter"
  },
  "url": "https://192.168.0.240/mcp/incident-tools",
  "trustedPrivateTarget": {
    "host": "192.168.0.240",
    "recordedPins": ["192.168.0.240"],
    "currentPins": ["192.168.0.240"],
    "state": "match"
  },
  "env": {
    "names": ["N8N_MCP_TOKEN"],
    "missing": ["N8N_MCP_TOKEN"],
    "ready": true
  },
  "provider": {
    "name": "lab-mcp-incident-tools-ef87001dc0903436",
    "registryPresent": true,
    "gatewayPresent": true,
    "attached": true,
    "credentialReady": true
  },
  "policy": {
    "name": "mcp-bridge-incident-tools",
    "registryPresent": true,
    "gatewayPresent": true
  },
  "adapter": {
    "registered": true
  },
  "toolDiscovery": {
    "ok": false,
    "count": 0,
    "tools": [],
    "truncated": false,
    "detail": "MCP endpoint rejected tool discovery (HTTP 500)"
  }
}
```

`N8N_MCP_TOKEN` is the actual credential environment-key name in this registration. Its value is omitted.

The host variable was intentionally unset after registration. OpenShell still reports the credential ready.

### Credential-probe result

Command:

```bash
nemoclaw lab mcp status incident-tools --probe --json
```

Result:

```json
{
  "ok": null,
  "httpStatus": 500,
  "controlHttpStatus": 403,
  "detail": "the placeholder probe received HTTP 500 and the unresolvable control HTTP 403; differing rejections do not prove resolution because the endpoint may reject two different literal bearers differently — verify the stored credential value and compare against a known-good host"
}
```

The control’s HTTP `403` has not been independently attributed to the upstream service versus OpenShell.

### Audit evidence

Repeated Node-based discovery requests show:

```text
[1788954934.668] [sandbox] [OCSF ] [ocsf] NET:OPEN [INFO]
ALLOWED /usr/local/bin/node(28479) -> 192.168.0.240:443
[policy:mcp_bridge_incident_tools engine:opa]

[1788954934.680] [sandbox] [OCSF ] [ocsf] HTTP:POST [INFO]
ALLOWED POST http://192.168.0.240:443/mcp/incident-tools
[policy:mcp_bridge_incident_tools engine:l7-mcp]
[msg:JSONRPC_L7_REQUEST decision=allow rule_methods=initialize ...]

[1788954934.681] [sandbox] [OCSF ] [ocsf] HTTP:UNKNOWN [MED]
DENIED UNKNOWN
[policy:mcp_bridge_incident_tools engine:credential-binding]
[reason:credential_unavailable]
```

Curl-based credential probing shows the same sequence:

```text
ALLOWED /usr/bin/curl(...) -> 192.168.0.240:443
ALLOWED POST ... rule_methods=initialize
DENIED ... engine:credential-binding
reason:credential_unavailable
```

**The destination and MCP-method checks pass. The demonstrated rejection occurs at credential resolution, not the restricted tier’s destination allowlist.**

The audit renderer shows `http://...:443`, while the configured endpoint is HTTPS. This rendering alone has not been treated as evidence of a transport downgrade.

## 3. Live endpoint binding matches the registered provider

Command:

```bash
openshell sandbox get lab --policy-only
```

Relevant policy excerpt:

```yaml
network_policies:
  mcp_bridge_incident_tools:
    name: mcp_bridge_incident_tools
    endpoints:
      - host: '192.168.0.240'
        path: /mcp/incident-tools
        port: 443
        protocol: mcp
        enforcement: enforce
        rules:
          - allow:
              method: initialize
          - allow:
              method: notifications/initialized
          - allow:
              method: ping
          - allow:
              method: tools/list
          - allow:
              method: tools/call
        allowed_ips:
          - '192.168.0.240'
        credential_binding:
          provider: lab-mcp-incident-tools-ef87001dc0903436
        mcp:
          max_body_bytes: 131072
          strict_tool_names: true
          allow_all_known_mcp_methods: false
    binaries:
      - path: /usr/local/bin/mcporter
      - path: /usr/bin/mcporter
      - path: /usr/local/bin/openclaw
      - path: /usr/local/bin/node
      - path: /usr/bin/node
```

Additional generated resource, prompt, task, and notification methods are omitted from this excerpt.

The provider name matches the current registration exactly.

## 4. Managed credential restart did not resolve the failure

The credential was re-entered on the host and exported for:

```bash
nemoclaw lab mcp restart incident-tools
```

Observed completion:

```text
Policy version 4 submitted
Policy version 4 loaded
Applied preset: mcp-bridge-incident-tools

Policy version 5 submitted
Policy version 5 loaded
Applied preset: mcp-bridge-incident-tools

Refreshed MCP server 'incident-tools'.
```

Subsequent probing and discovery still returned:

```text
Placeholder probe: HTTP 500
Control probe: HTTP 403
Tool discovery: HTTP 500
```

Fresh audit events continued to report:

```text
credential_unavailable
```

## 5. Provider type and runtime placeholder delivery were verified

Command:

```bash
openshell sandbox provider list lab
```

Observed providers:

```text
NAME                                      TYPE             CREDENTIAL_KEYS CONFIG_KEYS
nvidia-prod                               nvidia           1               0
lab-mcp-incident-tools-ef87001dc0903436     nemoclaw-mcp-v1  1               0
```

A fresh sandbox Node process inspected only the presence and format of the credential environment variable, without printing its value.

Result:

```json
{
  "variablePresent": true,
  "isOpenShellPlaceholder": true,
  "isRevisionScoped": true
}
```

This demonstrates that a revision-scoped placeholder reaches a fresh process. It does not establish that the proxy can resolve that placeholder.

## 6. mcporter configuration inspection previously succeeded

Command:

```bash
nemoclaw lab exec -- \
  mcporter --root /sandbox/.openclaw/workspace \
  config get incident-tools --json
```

Output:

```json
{
  "name": "incident-tools",
  "source": {
    "kind": "local",
    "path": "/sandbox/.openclaw/workspace/config/mcporter.json"
  },
  "transport": "http",
  "baseUrl": "https://192.168.0.240/mcp/incident-tools",
  "headers": {
    "Authorization": "Bearer openshell:resolve:env:N8N_MCP_TOKEN",
    "accept": "application/json, text/event-stream"
  }
}
```

Therefore, the executable and registration were present and readable at that point.

### Unconfirmed placeholder hypothesis

The stored Authorization header uses an **unscoped** placeholder, while the fresh process receives a **revision-scoped** placeholder.

A comparison test was attempted to send otherwise identical initialization requests with each placeholder through the configured proxy.

The invocation hung without producing results.

**No conclusion can be drawn from that comparison. No revision-scoped placeholder was hardcoded into the managed configuration.**

## 7. OpenClaw slash-command inconsistency

OpenClaw autocomplete visibly advertises:

```text
/mcporter
List, configure, authenticate, call, and inspect MCP servers/tools
with mcporter over HTTP or stdio.
```

However, an agent response stated:

```text
The "mcporter" skill isn't available in this OpenClaw environment.
I searched the tool catalog and skills directory, but mcporter
doesn't exist as an active tool here.
```

The same UI showed an agent run remaining active for approximately 1 minute 39 seconds.

This is evidence of inconsistent command availability versus the agent’s response. It does not identify whether the cause is skill loading, eligibility, stale session state, model behavior, or a blocked execution path.

The UI run timer is not proof that a mcporter subprocess was running for that duration.

## 8. Sandbox execution now times out independently of MCP

### Skill inspection

Attempted command:

```bash
timeout --kill-after=3s 20s \
  nemoclaw lab exec --timeout 15 -- \
  openclaw skills info mcporter --json
```

Observed:

```text
No output
Exit code: 124
```

The outer host-side timeout expired. This is not evidence that the skill is absent.

### Trivial execution test

Attempted command:

```bash
echo "[host] Starting sandbox execution check"

if timeout --kill-after=3s 12s \
  nemoclaw lab exec --timeout 8 -- /bin/echo SANDBOX_EXEC_OK
then
  echo "[host] Completed successfully"
else
  rc=$?
  echo "[host] Failed or timed out: exit $rc"
fi
```

Observed:

```text
[host] Starting sandbox execution check
[host] Failed or timed out: exit 124
```

No `SANDBOX_EXEC_OK` output appeared.

This establishes that the host CLI invocation failed to finish within the timeout. It does not establish that the remote `echo` process started.

Earlier execution and configuration-inspection commands succeeded, so this is a later or intermittent failure rather than proof that execution never worked.

## 9. Pending test

A direct OpenShell execution test was suggested:

```bash
timeout --kill-after=3s 15s \
  openshell sandbox exec --name lab -- /bin/echo OPENSHELL_EXEC_OK
```

**No result is available yet.**

This would distinguish a NemoClaw wrapper/prerequisite hang from a failure extending into OpenShell’s execution path.

Cancellation of the active OpenClaw run was also suggested, but completion was not confirmed.

## 10. Conclusions and open questions

### Supported by evidence

- Managed MCP configuration and provider binding match.
- Destination and MCP-method policy checks permit the request.
- Credential resolution fails repeatedly with `credential_unavailable`.
- Republishing the credential through managed restart does not fix it.
- The correct provider profile is attached.
- Fresh processes receive revision-scoped placeholders.
- mcporter configuration inspection previously worked.
- Later NemoClaw exec commands time out even without MCP activity.
- `/mcporter` is advertised in autocomplete.

### Not established

- Whether unscoped placeholder handling causes the credential failure.
- Whether the exec hang is caused by a lifecycle lock, gateway, supervisor, or another prerequisite.
- Whether the credential failure and execution hang share a cause.
- Whether running component versions differ from the CLI.
- Whether the slash-command inconsistency is a separate skill/session issue.
- Whether a rebuild or alternate version resolves these failures.

No broader network grants, TLS bypass, raw-token substitution in mcporter, or manual lock-file deletion has been established as an appropriate fix.

## Requested assistance

1. Confirm whether this authenticated private-HTTPS MCP path is supported and tested with NemoClaw `v0.0.112` and OpenShell `0.0.106`.
2. Review the missing trusted-private declaration in the two diagnostic builders.
3. Identify why runtime resolution reports `credential_unavailable` despite matching binding, ready metadata, and placeholder delivery.
4. Confirm the expected behavior of unscoped versus revision-scoped placeholders for managed mcporter registrations.
5. Provide a supported diagnostic/recovery procedure for hanging `nemoclaw lab exec` invocations.
6. Provide a **verified working image or version combination** suitable for a repeatable training lab.

## Source references

- [v0.0.112 credential-probe implementation](https://github.com/NVIDIA/NemoClaw/blob/v0.0.112/src/lib/actions/sandbox/mcp-bridge-resolution-probe.ts)
- [v0.0.112 tool-discovery implementation](https://github.com/NVIDIA/NemoClaw/blob/v0.0.112/src/lib/actions/sandbox/mcp-bridge-tool-discovery.ts)
- [v0.0.112 URL validation](https://github.com/NVIDIA/NemoClaw/blob/v0.0.112/src/lib/actions/sandbox/mcp-bridge-url-validation.ts)
- [v0.0.112 status implementation](https://github.com/NVIDIA/NemoClaw/blob/v0.0.112/src/lib/actions/sandbox/mcp-bridge-status.ts)
- [Managed MCP lifecycle documentation](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/manage-sandboxes/mcp-servers/manage-mcp-servers)
- [OpenShell provider and credential-binding behavior](https://docs.nvidia.com/openshell/sandboxes/providers-v2)

**Current status: blocked. No verified end-to-end remediation.**