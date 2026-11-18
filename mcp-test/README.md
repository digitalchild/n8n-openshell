# MCP Tool Discovery Check

A Python CLI that checks whether an MCP server advertises tools over
Streamable HTTP.

Designed for testing n8n MCP Server Trigger workflows independently of
NemoClaw or another MCP client.

## What it checks

The script initializes an MCP session, prints the server metadata and
capabilities, and requests the complete advertised tool list, following
pagination.

It can display tool input schemas and save the inventory as JSON.

**It never calls a tool.** A successful result confirms tool advertisement,
not that the tools can execute or access their underlying data.

## Requirements

- Python 3.11 or newer
- An HTTPS MCP endpoint supporting Streamable HTTP
- The endpoint's bearer token, if authentication is enabled

This script does not implement OAuth login, custom-header authentication,
stdio, or the legacy HTTP+SSE transport.

Streamable HTTP can return `text/event-stream` responses. Those responses
are supported and do not mean the server is using the legacy SSE transport.

## Installation

On macOS, install Python with Homebrew if needed:

```bash
brew install python
```

From this directory, create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Reactivate the environment when opening a new terminal:

```bash
source .venv/bin/activate
```

## Usage

### Test an authenticated endpoint

```bash
python check_mcp.py \
  "https://n8n.example.com/mcp/incident-tools"
```

The script prompts for the MCP bearer token without displaying it.

Enter only the token, without the `Bearer ` prefix. Use the credential
configured on the MCP Server Trigger, not your n8n login password or a
general n8n API key.

### Display input schemas and save the inventory

```bash
python check_mcp.py \
  "https://n8n.example.com/mcp/incident-tools" \
  --schemas \
  --output mcp-tools.json
```

The output file includes server metadata, capabilities, and tool definitions.
An existing file at the specified path is overwritten.

### Supply a token through the environment

The script reads `N8N_MCP_TOKEN` if it is set. For automation, inject it
through your CI secret store or another secret manager.

For an interactive Bash or Zsh session, this avoids placing the token
directly in shell history:

```bash
printf "MCP bearer token: "
read -r -s N8N_MCP_TOKEN
printf '\n'
export N8N_MCP_TOKEN

python check_mcp.py \
  "https://n8n.example.com/mcp/incident-tools"

unset N8N_MCP_TOKEN
```

### Test an endpoint without authentication

```bash
python check_mcp.py \
  "https://n8n.example.com/mcp/incident-tools" \
  --no-auth
```

Use this only when the endpoint is intentionally configured without
authentication. The flag does not disable authentication on the server.

### Use a private certificate authority

```bash
python check_mcp.py \
  "https://n8n.lab.example/mcp/incident-tools" \
  --ca-file ./lab-root-ca.pem
```

TLS verification remains enabled. The server certificate must match the
hostname or IP address in the URL.

### Increase the timeout

```bash
python check_mcp.py \
  "https://n8n.example.com/mcp/incident-tools" \
  --timeout 60
```

The default per-request timeout is 30 seconds. The overall test is also
bounded, using three times the configured timeout plus 15 seconds.

## Options

| Option | Description |
| --- | --- |
| `url` | Exact HTTPS MCP endpoint URL |
| `--schemas` | Print tool input schemas |
| `--output PATH` | Save the inventory as JSON |
| `--no-auth` | Send no bearer token |
| `--ca-file PATH` | Use a PEM CA file for TLS verification |
| `--timeout SECONDS` | Per-request timeout; default: 30 |
| `--help` | Show command help |

URLs containing embedded credentials, query strings, or fragments are
rejected. Redirects are rejected rather than followed.

## Interpreting results

### PASS

The server completed initialization and advertised at least one tool.

This proves discovery works for this client and endpoint. It does not prove:

- Tool calls succeed.
- Data-table references are correct.
- Another client or sandbox can reach the endpoint.
- The endpoint rejects invalid credentials.

### EMPTY

Initialization and `tools/list` succeeded, but the server returned zero tools.

For n8n, check that enabled tool nodes are attached to the MCP Server
Trigger's Tool connector and that the updated workflow is published.

A normal node elsewhere in the workflow is not automatically exposed as
an MCP tool.

### FAILED

The script could not complete a stage of the test. The output identifies
the stage and available HTTP status information.

A failed discovery does **not** mean the server has zero tools.

If discovery succeeds but session cleanup fails, the script retains the
inventory and reports the cleanup failure separately.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Discovery and cleanup succeeded; at least one tool was advertised |
| `1` | A runtime failure occurred, including a cleanup failure |
| `2` | Successful discovery returned zero tools, or CLI arguments were invalid |
| `130` | Interrupted by the user |

## n8n troubleshooting

Copy the **Production URL** directly from the MCP Server Trigger node.
Publish the workflow before testing its production endpoint.

| Symptom | Check |
| --- | --- |
| HTTP `401` or `403` | Trigger authentication, bearer credential, proxy or WAF rules |
| HTTP `404` | Exact endpoint path and workflow publication |
| Redirect or HTML response | Wrong URL, editor route, SSO login, or proxy challenge |
| Zero advertised tools | Tool connections, disabled nodes, published workflow version |
| Timeout or broken session | Proxy buffering, routing, webhook replicas |
| `tools/list` not found | Correct endpoint and server tool capability |

With multiple n8n webhook replicas, route MCP traffic to a single dedicated
webhook replica as described in the n8n documentation.

## Security

- Test only endpoints you trust.
- Do not commit tokens, credential exports, or `.env` files.
- Treat tool descriptions, schemas, and error messages as server-controlled data.
- Review output before sharing it. Schemas can contain internal details.
- The script redacts literal occurrences of the supplied token, but this
  is not a guarantee that all sensitive data has been removed.
- Do not disable TLS verification to work around certificate errors.

## References

- [n8n MCP Server Trigger](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-langchain.mcptrigger)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)

