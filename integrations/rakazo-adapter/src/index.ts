import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { Readable } from "node:stream";

const MAX_REQUEST_BYTES = 8 * 1024 * 1024;
const PORT = Number.parseInt(process.env.PORT ?? "8787", 10);
const HOST = process.env.HOST?.trim() || "0.0.0.0";
const INTERNAL_BASE_URL = (process.env.CODEBOT_RAKAZO_INTERNAL_URL ?? "").replace(/\/+$/, "");
const INTERNAL_TOKEN = process.env.CODEBOT_RAKAZO_SAMPLING_TOKEN ?? "";
const RAKAZO_KEY = process.env.RAKAZO_ADAPTER_SHARED_KEY ?? "local";
const ADAPTER_MODE = process.env.CODEBOT_RAKAZO_ADAPTER_MODE === "project-mcp" ? "project-mcp" : "model";

function json(response: ServerResponse, status: number, value: unknown): void {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
  });
  response.end(body);
}

function authorized(request: IncomingMessage): boolean {
  const header = request.headers.authorization ?? "";
  return header === `Bearer ${RAKAZO_KEY}`;
}

async function readBody(request: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > MAX_REQUEST_BYTES) throw new Error("REQUEST_TOO_LARGE");
    chunks.push(buffer);
  }
  return Buffer.concat(chunks);
}

async function proxy(request: IncomingMessage, response: ServerResponse, path: string): Promise<void> {
  const projectMcp = ADAPTER_MODE === "project-mcp";
  const incomingAuthorization = request.headers.authorization ?? "";
  if ((!projectMcp && !authorized(request)) || (projectMcp && !incomingAuthorization.startsWith("Bearer "))) {
    json(response, 401, { error: { message: "Rakazo adapter authorization failed", type: "authentication_error" } });
    return;
  }

  const controller = new AbortController();
  request.once("aborted", () => controller.abort());
  response.once("close", () => {
    if (!response.writableEnded) controller.abort();
  });

  let body: Buffer | undefined;
  try {
    body = request.method && !["GET", "HEAD"].includes(request.method) ? await readBody(request) : undefined;
  } catch (error) {
    const tooLarge = error instanceof Error && error.message === "REQUEST_TOO_LARGE";
    json(response, tooLarge ? 413 : 400, { error: { message: tooLarge ? "Request exceeds 8 MiB" : "Invalid request body" } });
    return;
  }

  try {
    // model 模式用进程级采样令牌替换 Rakazo 的固定 local key；project-mcp
    // 模式则保留每项目随机 Bearer，让 Codebot 后端完成项目身份与权限校验。
    const authorization = projectMcp ? incomingAuthorization : `Bearer ${INTERNAL_TOKEN}`;
    const contentType = request.headers["content-type"] ?? "application/json";
    const upstream = await fetch(`${INTERNAL_BASE_URL}${path}`, {
      method: request.method,
      headers: {
        authorization,
        accept: request.headers.accept ?? "application/json",
        "content-type": contentType,
        ...(request.headers["mcp-protocol-version"]
          ? { "mcp-protocol-version": String(request.headers["mcp-protocol-version"]) }
          : {}),
        ...(request.headers["mcp-session-id"]
          ? { "mcp-session-id": String(request.headers["mcp-session-id"]) }
          : {}),
        "user-agent": projectMcp
          ? "Codebot-Rakazo-Project-MCP-Loopback/0.2"
          : "Codebot-Rakazo-Model-Adapter/0.2",
      },
      body: body?.toString("utf8"),
      signal: controller.signal,
      redirect: "error",
    });

    const upstreamContentType = upstream.headers.get("content-type") ?? "application/json; charset=utf-8";
    response.writeHead(upstream.status, {
      "content-type": upstreamContentType,
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      ...(upstream.headers.get("mcp-protocol-version")
        ? { "mcp-protocol-version": upstream.headers.get("mcp-protocol-version") as string }
        : {}),
      ...(upstream.headers.get("mcp-session-id")
        ? { "mcp-session-id": upstream.headers.get("mcp-session-id") as string }
        : {}),
    });
    if (!upstream.body) {
      response.end();
      return;
    }
    Readable.fromWeb(upstream.body as import("node:stream/web").ReadableStream).pipe(response);
  } catch (error) {
    if (controller.signal.aborted) {
      if (!response.headersSent) json(response, 499, { error: { message: "Client cancelled request" } });
      else response.destroy();
      return;
    }
    // 不把可能含地址、令牌或 provider 细节的底层异常返回给 Rakazo。
    json(response, 502, { error: { message: "Codebot model sampling bridge is unavailable", type: "upstream_error" } });
  }
}

export function createAdapterServer() {
  return createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://adapter.invalid");
    if (request.method === "GET" && url.pathname === "/health") {
      const configured = Boolean(INTERNAL_BASE_URL && (ADAPTER_MODE === "project-mcp" || INTERNAL_TOKEN));
      json(response, configured ? 200 : 503, {
        ok: configured,
        service: ADAPTER_MODE === "project-mcp"
          ? "codebot-rakazo-project-mcp-loopback"
          : "codebot-rakazo-model-adapter",
      });
      return;
    }
    if (ADAPTER_MODE === "project-mcp") {
      await proxy(request, response, `${url.pathname}${url.search}`);
      return;
    }
    if (request.method === "GET" && url.pathname === "/v1/models") {
      await proxy(request, response, "/models");
      return;
    }
    if (request.method === "POST" && url.pathname === "/v1/chat/completions") {
      await proxy(request, response, "/chat/completions");
      return;
    }
    json(response, 404, { error: { message: "Not found", type: "invalid_request_error" } });
  });
}

if (process.env.NODE_ENV !== "test") {
  if (
    !Number.isSafeInteger(PORT)
    || PORT < 1
    || PORT > 65535
    || !INTERNAL_BASE_URL
    || (ADAPTER_MODE === "model" && !INTERNAL_TOKEN)
  ) {
    // 启动失败优于以空令牌或错误上游地址继续服务。
    process.stderr.write("Rakazo adapter configuration is incomplete\n");
    process.exit(1);
  }
  createAdapterServer().listen(PORT, HOST, () => {
    process.stdout.write(`Codebot Rakazo ${ADAPTER_MODE} adapter listening on ${HOST}:${PORT}\n`);
  });
}
