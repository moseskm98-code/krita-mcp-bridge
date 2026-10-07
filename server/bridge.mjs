import { lstat, readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { z } from 'zod';

const connectionSchema = z.object({
  host: z.literal('127.0.0.1'),
  port: z.number().int().min(1).max(65535),
  token: z.string().min(24).max(512),
  pid: z.number().int().positive(),
  sessionId: z.string().min(1).max(128),
  protocolVersion: z.literal(1),
}).strict();

export class BridgeError extends Error {
  constructor(code, message, details) {
    super(message);
    this.name = 'BridgeError';
    this.code = code;
    this.details = details;
  }
}

export const defaultConnectionPath = join(homedir(), '.local/share/krita-mcp-bridge/connection.json');

export async function readConnection(path = defaultConnectionPath) {
  let stat;
  try { stat = await lstat(path); }
  catch (error) {
    if (error.code === 'ENOENT') throw new BridgeError('BRIDGE_NOT_ACTIVE', 'Krita bridge is not active. Enable its Python plugin in Krita and start Krita; no Computer Use fallback is available.');
    throw new BridgeError('CONNECTION_UNREADABLE', 'Cannot read the local Krita connection file.');
  }
  if (!stat.isFile() || stat.size > 16384) throw new BridgeError('CONNECTION_INVALID', 'Connection must be a small regular file, not a symlink.');
  if ((stat.mode & 0o077) !== 0 || (typeof process.getuid === 'function' && stat.uid !== process.getuid())) {
    throw new BridgeError('CONNECTION_PERMISSIONS', 'Connection file must belong to the current user and be private (mode 0600).');
  }
  let parsed;
  try { parsed = connectionSchema.safeParse(JSON.parse(await readFile(path, 'utf8'))); }
  catch { throw new BridgeError('CONNECTION_INVALID', 'Connection file does not contain valid JSON.'); }
  if (!parsed.success) throw new BridgeError('CONNECTION_INVALID', 'Connection must describe protocol 1 on IPv4 loopback with a local token.');
  return parsed.data;
}

function redact(message, token) {
  return String(message).split(token).join('[redacted]').slice(0, 2000);
}

export class BridgeClient {
  constructor({ connectionPath = defaultConnectionPath, timeoutMs = 120000, fetchImpl = fetch } = {}) {
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 300000) throw new Error('timeoutMs must be between 1 and 300000.');
    this.connectionPath = connectionPath;
    this.timeoutMs = timeoutMs;
    this.fetchImpl = fetchImpl;
    this.queue = Promise.resolve();
  }

  call(op, args) {
    const task = this.queue.then(() => this.request(op, args));
    this.queue = task.catch(() => {});
    return task;
  }

  async request(op, args) {
    const connection = await readConnection(this.connectionPath);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await this.fetchImpl(`http://127.0.0.1:${connection.port}/rpc`, {
        method: 'POST',
        redirect: 'error',
        headers: { 'content-type': 'application/json', 'authorization': `Bearer ${connection.token}` },
        body: JSON.stringify({ op, args }),
        signal: controller.signal,
      });
      if (response.status === 401 || response.status === 403) throw new BridgeError('BRIDGE_AUTH_FAILED', 'Krita rejected the local token. Refresh the connection file by activating the installed bridge; the token is never returned.');
      if (!response.ok) throw new BridgeError('BRIDGE_HTTP_ERROR', `Krita bridge returned HTTP ${response.status}.`);
      const chunks = [];
      let size = 0;
      for await (const chunk of response.body) {
        size += chunk.byteLength;
        if (size > 2 * 1024 * 1024) {
          controller.abort();
          throw new BridgeError('BRIDGE_RESPONSE_TOO_LARGE', 'Bridge response exceeded the 2 MiB safety limit.');
        }
        chunks.push(chunk);
      }
      let body;
      try { body = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
      catch { throw new BridgeError('BRIDGE_PROTOCOL_ERROR', 'Krita bridge returned invalid JSON.'); }
      if (body?.ok === false && typeof body.error?.code === 'string' && typeof body.error?.message === 'string') {
        throw new BridgeError(redact(body.error.code, connection.token), redact(body.error.message, connection.token));
      }
      if (body?.ok !== true || !Object.hasOwn(body, 'result')) throw new BridgeError('BRIDGE_PROTOCOL_ERROR', 'Krita bridge returned an invalid result envelope.');
      // Never return a transport credential, even if a buggy bridge echoes it.
      const safeResult = JSON.parse(JSON.stringify(body.result, (key, value) => {
        if (/^(token|authorization)$/i.test(key)) return '[redacted]';
        return typeof value === 'string' ? value.split(connection.token).join('[redacted]') : value;
      }));
      return safeResult;
    } catch (error) {
      if (error instanceof BridgeError) throw error;
      if (controller.signal.aborted) throw new BridgeError('BRIDGE_TIMEOUT', `No response within ${this.timeoutMs} ms. A mutation may still be running in Krita; inspect state and output paths before retrying.`, { outcomeUnknown: true });
      throw new BridgeError('BRIDGE_UNREACHABLE', 'Cannot reach the local Krita Python plugin. Krita may be closed or the connection file may be stale.');
    } finally { clearTimeout(timer); }
  }
}
