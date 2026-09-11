import assert from "node:assert/strict";
import { once } from "node:events";
import test from "node:test";

process.env.NODE_ENV = "test";
process.env.CODEBOT_RAKAZO_INTERNAL_URL = "http://127.0.0.1:9/api/internal/model-sampling/v1";
process.env.CODEBOT_RAKAZO_SAMPLING_TOKEN = "test-only-token";
process.env.RAKAZO_ADAPTER_SHARED_KEY = "codebot-local-bridge";

test("adapter rejects requests without the private Rakazo key", async () => {
  const { createAdapterServer } = await import("./index.js");
  const server = createAdapterServer().listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const response = await fetch(`http://127.0.0.1:${address.port}/v1/models`);
  assert.equal(response.status, 401);
  server.close();
  await once(server, "close");
});

test("adapter accepts the same private key persisted through Rakazo models.connect", async () => {
  const { createAdapterServer } = await import("./index.js");
  const server = createAdapterServer().listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const response = await fetch(`http://127.0.0.1:${address.port}/v1/models`, {
    headers: { authorization: "Bearer codebot-local-bridge" },
  });
  // 测试上游故意指向未监听端口，因此 502 证明请求已通过入口鉴权并进入代理，
  // 而不是再次得到用户截图中的 adapter 401。
  assert.equal(response.status, 502);
  server.close();
  await once(server, "close");
});

test("adapter health never returns the internal token", async () => {
  const { createAdapterServer } = await import("./index.js");
  const server = createAdapterServer().listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const response = await fetch(`http://127.0.0.1:${address.port}/health`);
  const body = await response.text();
  assert.equal(body.includes("test-only-token"), false);
  server.close();
  await once(server, "close");
});
