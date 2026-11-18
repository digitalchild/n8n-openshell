#!/usr/bin/env node
"use strict";

// Generic Streamable HTTP MCP client.
// Node 22+, standard library only.
//
// Every connection uses an explicit HTTP proxy.
// No direct fallback, no NO_PROXY bypass, no disabled TLS verification.
//
// Certificate trust comes from Node's public roots plus existing runtime
// TLS environment variables. No NemoClaw-specific CA path is hardcoded.
//
// Tool names and schemas are discovered dynamically from the server.

const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const https = require("node:https");
const tls = require("node:tls");
const net = require("node:net");
const { StringDecoder } = require("node:string_decoder");

const SKILL_DIR = path.resolve(__dirname, "..");
const SUPPORTED_PROTOCOLS = [
  "2025-11-25",
  "2025-06-18",
  "2025-03-26",
];

let stage = "configuration";
let secret = "";
let requestId = 0;

const active = new Set();

function track(obj) {
  active.add(obj);
  obj.once("close", () => active.delete(obj));
  return obj;
}

function clean(text) {
  return secret
    ? String(text).split(secret).join("[REDACTED]")
    : String(text);
}

function output(value) {
  process.stdout.write(
    clean(JSON.stringify(value, null, 2)) + "\n"
  );
}

function progress(text) {
  process.stderr.write(`[incident-mcp] ${clean(text)}\n`);
}

function fail(message) {
  throw new Error(message);
}

function isObject(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value)
  );
}

// ---------------------------------------------------------------------------
// Configuration and credentials
// ---------------------------------------------------------------------------

function validateEndpoint(raw) {
  const url = new URL(raw);

  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    fail(
      "Endpoint must be HTTPS, without URL credentials, query, or fragment."
    );
  }

  return url;
}

function loadConfig(configPath) {
  const file = path.resolve(
    configPath || path.join(SKILL_DIR, "config.json")
  );

  const config = JSON.parse(fs.readFileSync(file, "utf8"));

  if (!isObject(config)) {
    fail("Configuration must be a JSON object.");
  }

  config.url = validateEndpoint(config.server_url);
  config.request_timeout_sec ??= 15;
  config.total_timeout_sec ??= 60;

  for (const key of [
    "request_timeout_sec",
    "total_timeout_sec",
  ]) {
    if (
      !Number.isFinite(config[key]) ||
      config[key] <= 0 ||
      config[key] > 600
    ) {
      fail(
        `${key} must be greater than 0 and at most 600 seconds.`
      );
    }
  }

  config.token_file = path.resolve(
    path.dirname(file),
    config.token_file || ".token"
  );

  const proxyRaw =
    config.proxy_url ||
    process.env.https_proxy ||
    process.env.HTTPS_PROXY ||
    process.env.http_proxy ||
    process.env.HTTP_PROXY ||
    process.env.all_proxy ||
    process.env.ALL_PROXY;

  if (!proxyRaw) {
    fail(
      "No proxy configured. Set proxy_url or a proxy environment variable. " +
      "Direct connections are not supported."
    );
  }

  config.proxy = new URL(proxyRaw);

  if (
    config.proxy.protocol !== "http:" ||
    config.proxy.search ||
    config.proxy.hash ||
    config.proxy.pathname !== "/"
  ) {
    fail("proxy_url must be an HTTP proxy origin.");
  }

  // Use the existing runtime trust configuration.
  //
  // Explicit `ca` options replace Node's default CA set, so retain the
  // public roots and add the runtime-provided bundles.
  //
  // No certificate store is modified. The legacy config.ca_file option
  // is intentionally ignored.
  const runtimeBundles = new Set(
    [
      process.env.NODE_EXTRA_CA_CERTS,
      process.env.SSL_CERT_FILE,
      process.env.CURL_CA_BUNDLE,
    ].filter(
      value =>
        typeof value === "string" &&
        value.length > 0
    )
  );

  config.ca = [...tls.rootCertificates];
  config.runtime_ca_files = [...runtimeBundles];

  for (const bundlePath of runtimeBundles) {
    try {
      config.ca.push(
        fs.readFileSync(bundlePath, "utf8")
      );
    } catch {
      fail(
        `Cannot read the runtime TLS bundle ${JSON.stringify(bundlePath)}. ` +
        "No trust settings were changed and TLS verification was not disabled."
      );
    }
  }

  return config;
}

function readToken(config) {
  const fd = fs.openSync(
    config.token_file,
    fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW
  );

  try {
    const stat = fs.fstatSync(fd);

    if (
      !stat.isFile() ||
      (stat.mode & 0o077) ||
      stat.size > 16384
    ) {
      fail(
        "Token must be an owner-only regular file, at most 16 KiB. " +
        "Use chmod 600 on the token file."
      );
    }

    const token = fs.readFileSync(fd, "utf8").trim();

    if (
      !token ||
      /[\r\n]/.test(token) ||
      /^Bearer\s/i.test(token) ||
      token.includes("openshell:resolve:")
    ) {
      fail(
        "Use the real limited MCP token, without a Bearer prefix " +
        "or OpenShell placeholder."
      );
    }

    secret = token;
    return token;
  } finally {
    fs.closeSync(fd);
  }
}

// ---------------------------------------------------------------------------
// HTTPS through the configured HTTP CONNECT proxy
// ---------------------------------------------------------------------------

function connectTunnel(config, target, signal) {
  return new Promise((resolve, reject) => {
    const authority =
      `${target.hostname}:${target.port || 443}`;

    const headers = {
      Host: authority,
    };

    if (
      config.proxy.username ||
      config.proxy.password
    ) {
      const credentials =
        decodeURIComponent(config.proxy.username) +
        ":" +
        decodeURIComponent(config.proxy.password);

      headers["Proxy-Authorization"] =
        "Basic " +
        Buffer.from(credentials).toString("base64");
    }

    const request = track(
      http.request({
        hostname: config.proxy.hostname,
        port: config.proxy.port || 80,
        method: "CONNECT",
        path: authority,
        headers,
        agent: new http.Agent({
          keepAlive: false,
        }),
        signal,
      })
    );

    request.on("error", reject);

    request.on("connect", (response, socket, head) => {
      track(socket);

      progress(
        `proxy CONNECT HTTP ${response.statusCode}`
      );

      if (response.statusCode !== 200) {
        socket.destroy();

        reject(
          new Error(
            `Proxy CONNECT returned HTTP ${response.statusCode}; ` +
            "inspect OpenShell audit logs."
          )
        );

        return;
      }

      if (head.length) {
        socket.unshift(head);
      }

      const host = target.hostname.replace(
        /^\[|\]$/g,
        ""
      );

      const secure = track(
        tls.connect({
          socket,
          servername: net.isIP(host)
            ? undefined
            : host,
          ca: config.ca,
          rejectUnauthorized: true,
          ALPNProtocols: ["http/1.1"],
          checkServerIdentity: (_name, certificate) =>
            tls.checkServerIdentity(host, certificate),
        })
      );

      const abort = () => {
        secure.destroy(
          new Error("Request deadline exceeded.")
        );
      };

      signal.addEventListener(
        "abort",
        abort,
        { once: true }
      );

      secure.once("close", () => {
        signal.removeEventListener("abort", abort);
      });

      secure.once("error", reject);

      secure.once("secureConnect", () => {
        // Explicitly verify IP identities too.
        const identityError = tls.checkServerIdentity(
          host,
          secure.getPeerCertificate()
        );

        if (identityError) {
          secure.destroy();
          reject(identityError);
          return;
        }

        if (signal.aborted) {
          secure.destroy();

          reject(
            new Error("Request deadline exceeded.")
          );

          return;
        }

        resolve(secure);
      });

      if (signal.aborted) {
        abort();
      }
    });

    request.end();
  });
}

// ---------------------------------------------------------------------------
// JSON and incremental SSE response parsing
// ---------------------------------------------------------------------------

// Stop at the matching JSON-RPC response, not at the end of an SSE connection.
// Supports multi-line SSE data fields and ignores unrelated notifications.

function readResponse(response, expectedId, rawOnly) {
  return new Promise((resolve, reject) => {
    const decoder = new StringDecoder("utf8");

    const contentType = String(
      response.headers["content-type"] || ""
    ).toLowerCase();

    const isSSE = contentType.includes(
      "text/event-stream"
    );

    const isJSON = contentType.includes(
      "application/json"
    );

    let raw = "";
    let pending = "";
    let data = [];
    let bytes = 0;
    let done = false;

    const finish = (error, value) => {
      if (done) return;

      done = true;

      if (error) {
        reject(error);
      } else {
        resolve(value);
      }

      response.destroy();
    };

    const accept = message => {
      if (
        !isObject(message) ||
        message.jsonrpc !== "2.0"
      ) {
        fail("Invalid JSON-RPC response.");
      }

      if (message.id === expectedId) {
        if (Object.hasOwn(message, "error")) {
          fail(
            "MCP error: " +
            JSON.stringify(message.error).slice(0, 2000)
          );
        }

        if (!Object.hasOwn(message, "result")) {
          fail("JSON-RPC response has no result.");
        }

        finish(null, message.result);
        return;
      }

      if (
        Object.hasOwn(message, "method") &&
        Object.hasOwn(message, "id")
      ) {
        fail(
          "Server-initiated requests are not supported by this client."
        );
      }
    };

    const dispatch = () => {
      if (!data.length) return;

      const text = data.join("\n");
      data = [];

      if (text.trim()) {
        accept(JSON.parse(text));
      }
    };

    const consumeLines = text => {
      pending += text;

      for (;;) {
        const at = pending.indexOf("\n");

        if (at < 0 || done) break;

        const line = pending
          .slice(0, at)
          .replace(/\r$/, "");

        pending = pending.slice(at + 1);

        if (!line) {
          dispatch();
        } else if (line.startsWith("data:")) {
          data.push(
            line.slice(5).replace(/^ /, "")
          );
        }
      }
    };

    response.on("error", error => {
      finish(error);
    });

    response.on("aborted", () => {
      finish(
        new Error(
          "Response stream aborted before a result."
        )
      );
    });

    response.on("data", chunk => {
      if (done) return;

      bytes += chunk.length;

      // Bounded preview for errors and operator path probes.
      if (rawOnly) {
        raw += decoder.write(chunk);

        if (bytes >= 4096) {
          finish(null, {
            body: raw.slice(0, 4096),
          });
        }

        return;
      }

      if (bytes > 4 * 1024 * 1024) {
        finish(
          new Error("Response exceeded 4 MiB.")
        );

        return;
      }

      try {
        const text = decoder.write(chunk);

        if (isSSE) {
          consumeLines(text);
        } else {
          raw += text;
        }
      } catch (error) {
        finish(error);
      }
    });

    response.on("end", () => {
      if (done) return;

      try {
        const tail = decoder.end();

        if (rawOnly) {
          finish(null, {
            body: (raw + tail).slice(0, 4096),
          });

          return;
        }

        if (isSSE) {
          consumeLines(tail);

          if (!done) {
            consumeLines("\n\n");
          }
        } else if (isJSON) {
          accept(JSON.parse(raw + tail));
        } else {
          fail(
            "Expected MCP JSON/SSE, received " +
            JSON.stringify(contentType) +
            "."
          );
        }

        if (!done) {
          fail(
            "Response ended without the matching JSON-RPC result."
          );
        }
      } catch (error) {
        finish(error);
      }
    });
  });
}

// ---------------------------------------------------------------------------
// One bounded HTTP request
// ---------------------------------------------------------------------------

async function request(
  config,
  method,
  target,
  payload,
  session,
  protocol,
  token,
  rawOnly = false
) {
  const controller = new AbortController();

  const timer = setTimeout(
    () => controller.abort(),
    config.request_timeout_sec * 1000
  );

  let secure;
  let agent;

  try {
    secure = await connectTunnel(
      config,
      target,
      controller.signal
    );

    agent = new https.Agent({
      keepAlive: false,
    });

    agent.createConnection = () => secure;

    const body = payload === null
      ? null
      : Buffer.from(JSON.stringify(payload));

    if (body && body.length > 131072) {
      fail("Request exceeded 128 KiB.");
    }

    const headers = {
      Accept: "application/json, text/event-stream",
      "Accept-Encoding": "identity",
    };

    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }

    if (body) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = body.length;
    }

    if (session) {
      headers["Mcp-Session-Id"] = session;
    }

    if (protocol) {
      headers["MCP-Protocol-Version"] = protocol;
    }

    return await new Promise((resolve, reject) => {
      const req = track(
        https.request(
          target,
          {
            method,
            headers,
            agent,
            signal: controller.signal,
          },
          async response => {
            progress(
              `${method} HTTP ${response.statusCode}`
            );

            try {
              const status = response.statusCode;
              const sid =
                response.headers["mcp-session-id"];

              if (
                sid &&
                (
                  typeof sid !== "string" ||
                  !/^[\x21-\x7e]{1,1024}$/.test(sid)
                )
              ) {
                response.destroy();
                fail("Invalid session header.");
              }

              if (status >= 300 && status < 400) {
                response.destroy();

                fail(
                  "Redirect refused. Configure the exact final MCP URL."
                );
              }

              if (rawOnly) {
                const result = await readResponse(
                  response,
                  null,
                  true
                );

                resolve({
                  status,
                  ...result,
                });

                return;
              }

              if (status < 200 || status >= 300) {
                const result = await readResponse(
                  response,
                  null,
                  true
                );

                fail(
                  `HTTP ${status}: ${clean(result.body)}`
                );
              }

              // JSON-RPC notifications have no response payload.
              if (
                !payload ||
                !Object.hasOwn(payload, "id")
              ) {
                response.destroy();

                resolve({
                  session: sid || session,
                  result: null,
                });

                return;
              }

              const result = await readResponse(
                response,
                payload.id,
                false
              );

              resolve({
                session: sid || session,
                result,
              });
            } catch (error) {
              reject(error);
            }
          }
        )
      );

      req.on("error", reject);
      req.end(body);
    });
  } catch (error) {
    if (controller.signal.aborted) {
      fail(
        `Request exceeded ${config.request_timeout_sec}s.`
      );
    }

    throw error;
  } finally {
    clearTimeout(timer);

    if (agent) {
      agent.destroy();
    }

    if (secure) {
      secure.destroy();
    }
  }
}

// ---------------------------------------------------------------------------
// MCP session lifecycle
// ---------------------------------------------------------------------------

async function withSession(config, token, action) {
  let session = null;
  let protocol = null;

  try {
    stage = "initialize";
    progress(stage);

    const initialized = await request(
      config,
      "POST",
      config.url,
      {
        jsonrpc: "2.0",
        id: ++requestId,
        method: "initialize",
        params: {
          protocolVersion: SUPPORTED_PROTOCOLS[0],
          capabilities: {},
          clientInfo: {
            name: "incident-mcp-skill",
            version: "1.0.1",
          },
        },
      },
      null,
      null,
      token
    );

    session = initialized.session;
    protocol = initialized.result?.protocolVersion;

    if (!SUPPORTED_PROTOCOLS.includes(protocol)) {
      fail(
        "Unsupported negotiated protocol: " +
        JSON.stringify(protocol)
      );
    }

    if (
      !isObject(initialized.result?.capabilities) ||
      !Object.hasOwn(
        initialized.result.capabilities,
        "tools"
      )
    ) {
      fail(
        "Server does not advertise the tools capability."
      );
    }

    stage = "notifications/initialized";
    progress(stage);

    await request(
      config,
      "POST",
      config.url,
      {
        jsonrpc: "2.0",
        method: "notifications/initialized",
      },
      session,
      protocol,
      token
    );

    const rpc = async (method, params) => {
      stage = method;
      progress(method);

      const response = await request(
        config,
        "POST",
        config.url,
        {
          jsonrpc: "2.0",
          id: ++requestId,
          method,
          params,
        },
        session,
        protocol,
        token
      );

      return response.result;
    };

    return await action(
      rpc,
      initialized.result
    );
  } finally {
    if (session) {
      const previousStage = stage;
      stage = "session cleanup";

      try {
        const cleanup = await request(
          {
            ...config,
            request_timeout_sec: 3,
          },
          "DELETE",
          config.url,
          null,
          session,
          protocol,
          token,
          true
        );

        if (
          ![200, 202, 204, 405].includes(cleanup.status)
        ) {
          progress(
            `cleanup warning: HTTP ${cleanup.status}; ` +
            "do not replay a completed call."
          );
        }
      } catch (error) {
        progress(
          "cleanup warning: " +
          clean(error.message).slice(0, 500)
        );
      }

      stage = previousStage;
    }
  }
}

// ---------------------------------------------------------------------------
// Live tool discovery
// ---------------------------------------------------------------------------

async function listTools(rpc) {
  const tools = [];
  const cursors = new Set();
  const names = new Set();

  let cursor;

  for (let page = 0; page < 50; page++) {
    const result = await rpc(
      "tools/list",
      cursor ? { cursor } : {}
    );

    if (!Array.isArray(result?.tools)) {
      fail("tools/list returned no tools array.");
    }

    for (const tool of result.tools) {
      if (
        !isObject(tool) ||
        typeof tool.name !== "string" ||
        !tool.name ||
        names.has(tool.name) ||
        !isObject(tool.inputSchema)
      ) {
        fail("Invalid or duplicate tool definition.");
      }

      names.add(tool.name);
      tools.push(tool);

      if (tools.length > 500) {
        fail("Tool inventory exceeded 500 entries.");
      }
    }

    if (!result.nextCursor) {
      return tools;
    }

    if (
      typeof result.nextCursor !== "string" ||
      cursors.has(result.nextCursor)
    ) {
      fail("Invalid or repeated pagination cursor.");
    }

    cursors.add(result.nextCursor);
    cursor = result.nextCursor;
  }

  fail("Tool inventory exceeded 50 pages.");
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

const HELP = `Usage:
  node scripts/client.js [--config FILE] check
  node scripts/client.js [--config FILE] tools
  node scripts/client.js [--config FILE] call TOOL '{"key":"value"}'
  node scripts/client.js [--config FILE] call TOOL @arguments.json
  node scripts/client.js [--config FILE] probe-path /rest/login

check:
  Read local configuration without making network requests.

tools:
  Discover current tool names, descriptions and input schemas.
  Does not call any tool.

call:
  Discover current tools, then call the selected tool ONCE.
  Arguments are passed directly as a JSON object.
  Calls are never automatically retried.

probe-path:
  Make a credential-free GET to another path on the same origin.
  Correlate the response with OpenShell audit logs to verify enforcement.

Exit codes:
  0    Operation succeeded
  1    Configuration, transport or protocol error
  2    Discovery succeeded but advertised zero tools
  3    MCP tool returned isError
  4    Requested tool is not currently advertised
  124  Overall deadline exceeded
`;

async function main() {
  const argv = process.argv.slice(2);

  if (
    !argv.length ||
    argv.includes("--help") ||
    argv.includes("-h")
  ) {
    process.stdout.write(HELP);
    return;
  }

  let configPath;

  if (argv[0] === "--config") {
    argv.shift();
    configPath = argv.shift();

    if (!configPath) {
      fail("--config requires a file.");
    }
  }

  const [command, toolOrPath, rawArgs] = argv;

  if (
    !["check", "tools", "call", "probe-path"].includes(command)
  ) {
    fail(HELP);
  }

  if (
    (
      ["check", "tools"].includes(command) &&
      argv.length !== 1
    ) ||
    (
      command === "probe-path" &&
      argv.length !== 2
    ) ||
    (
      command === "call" &&
      (argv.length < 2 || argv.length > 3)
    )
  ) {
    fail(HELP);
  }

  const config = loadConfig(configPath);

  if (command === "check") {
    output({
      ok: true,
      endpoint: config.url.href,
      proxy: config.proxy.origin,
      node: process.execPath,
      tokenFilePresent: fs.existsSync(
        config.token_file
      ),
      runtimeCaFiles: config.runtime_ca_files,
      requestTimeoutSec: config.request_timeout_sec,
      totalTimeoutSec: config.total_timeout_sec,
      note:
        "Configuration only. Does not prove connectivity or policy.",
    });

    return;
  }

  let answered = false;
  let toolAttempted = false;

  const timer = setTimeout(() => {
    if (answered) {
      progress(
        "Cleanup reached overall deadline; result retained."
      );

      for (const obj of active) {
        obj.destroy();
      }

      process.exit(process.exitCode || 0);
    }

    output({
      ok: false,
      stage,
      error: "Overall deadline exceeded.",
      warning: command === "call"
        ? "A tool may have executed. Inspect server state before retrying."
        : undefined,
    });

    for (const obj of active) {
      obj.destroy();
    }

    process.exit(124);
  }, config.total_timeout_sec * 1000);

  try {
    if (command === "probe-path") {
      if (
        !/^\/(?!\/)/.test(toolOrPath) ||
        /[?#\\]/.test(toolOrPath)
      ) {
        fail(
          "Use a same-origin absolute path without query or fragment."
        );
      }

      const target = new URL(
        toolOrPath,
        config.url
      );

      if (target.origin !== config.url.origin) {
        fail("Cross-origin probe refused.");
      }

      stage = "credential-free path probe";
      progress(stage);

      const response = await request(
        config,
        "GET",
        target,
        null,
        null,
        null,
        "",
        true
      );

      output({
        path: target.pathname,
        ...response,
        note:
          "A 403 alone does not prove enforcement; check OpenShell audit logs.",
      });

      return;
    }

    const token = readToken(config);
    let argumentText = rawArgs || "{}";

    if (
      command === "call" &&
      argumentText.startsWith("@")
    ) {
      const file = argumentText.slice(1);

      if (fs.statSync(file).size > 131072) {
        fail("Argument file exceeded 128 KiB.");
      }

      argumentText = fs.readFileSync(file, "utf8");
    }

    const argumentsObject = command === "call"
      ? JSON.parse(argumentText)
      : null;

    if (
      command === "call" &&
      !isObject(argumentsObject)
    ) {
      fail("Tool arguments must be a JSON object.");
    }

    await withSession(
      config,
      token,
      async (rpc, initialized) => {
        const tools = await listTools(rpc);

        if (command === "tools") {
          output({
            ok: true,
            server: initialized.serverInfo,
            protocol: initialized.protocolVersion,
            count: tools.length,
            tools,
          });

          process.exitCode = tools.length ? 0 : 2;
          answered = true;
          return;
        }

        if (
          !tools.some(
            tool => tool.name === toolOrPath
          )
        ) {
          output({
            ok: false,
            error: "Tool is not currently advertised.",
            available: tools.map(
              tool => tool.name
            ),
          });

          process.exitCode = 4;
          answered = true;
          return;
        }

        toolAttempted = true;

        const result = await rpc(
          "tools/call",
          {
            name: toolOrPath,
            arguments: argumentsObject,
          }
        );

        output({
          ok: !result?.isError,
          tool: toolOrPath,
          result,
        });

        process.exitCode = result?.isError ? 3 : 0;
        answered = true;
      }
    );
  } catch (error) {
    output({
      ok: false,
      stage,
      error: clean(error.message),
      warning: toolAttempted
        ? "A tool may have executed. Inspect server state before retrying."
        : undefined,
    });

    process.exitCode = 1;
  } finally {
    clearTimeout(timer);
  }
}

if (require.main === module) {
  main().catch(error => {
    output({
      ok: false,
      stage,
      error: clean(error.message),
    });

    process.exitCode = 1;
  });
}
