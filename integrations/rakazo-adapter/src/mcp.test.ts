import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer } from "node:http";
import test from "node:test";

test("project MCP loopback preserves the per-project bearer token", async () => {
  let observedAuthorization = "";
  let observedPath = "";
  const upstream = createServer(async (request, response) => {
    observedAuthorization = request.headers.authorization ?? "";
    observedPath = request.url ?? "";
    for await (const _chunk of request) {
      // 消费请求体，确保代理能够完整结束上游请求。
    }
    response.writeHead(200, {
      "content-type": "application/json",
      "mcp-protocol-version": "2024-11-05",
    });
    response.end(JSON.stringify({ jsonrpc: "2.0", id: 1, result: { ok: true } }));
  }).listen(0, "127.0.0.1");
  await once(upstream, "listening");
  const upstreamAddress = upstream.address();
  assert.ok(upstreamAddress && typeof upstreamAddress === "object");

  process.env.NODE_ENV = "test";
  process.env.CODEBOT_RAKAZO_ADAPTER_MODE = "project-mcp";
  process.env.CODEBOT_RAKAZO_INTERNAL_URL = `http://127.0.0.1:${upstreamAddress.port}/api/internal/rakazo-project-mcp`;
  delete process.env.CODEBOT_RAKAZO_SAMPLING_TOKEN;
  const { createAdapterServer } = await import("./index.js");
  const adapter = createAdapterServer().listen(0, "127.0.0.1");
  await once(adapter, "listening");
  const adapterAddress = adapter.address();
  assert.ok(adapterAddress && typeof adapterAddress === "object");

  const response = await fetch(`http://127.0.0.1:${adapterAddress.port}/project-one`, {
    method: "POST",
    headers: {
      authorization: "Bearer project-specific-token",
      "content-type": "application/json",
      "mcp-protocol-version": "2024-11-05",
    },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
  });
  assert.equal(response.status, 200);
  assert.equal(observedAuthorization, "Bearer project-specific-token");
  assert.equal(observedPath, "/api/internal/rakazo-project-mcp/project-one");
  assert.equal(response.headers.get("mcp-protocol-version"), "2024-11-05");

  adapter.close();
  upstream.close();
  await Promise.all([once(adapter, "close"), once(upstream, "close")]);
});
