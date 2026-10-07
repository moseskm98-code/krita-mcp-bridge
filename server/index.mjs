#!/usr/bin/env node
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { BridgeClient, BridgeError, defaultConnectionPath } from './bridge.mjs';
import { operations, runOperation } from './operations.mjs';

const bridge = new BridgeClient({
  connectionPath: process.env.KRITA_MCP_CONNECTION_FILE || defaultConnectionPath,
  timeoutMs: Number(process.env.KRITA_MCP_TIMEOUT_MS || 120000),
});
const server = new McpServer({ name: 'krita-local-bridge', version: '0.2.0' });

for (const [operation, definition] of Object.entries(operations)) {
  server.registerTool(`krita_${operation}`, {
    title: definition.title,
    description: definition.description,
    inputSchema: definition.schema,
    annotations: {
      readOnlyHint: !!definition.readOnly,
      destructiveHint: false,
      idempotentHint: ['state', 'layer_set_visibility'].includes(operation),
      openWorldHint: false,
    },
  }, async args => {
    try {
      const result = await runOperation(bridge, operation, args);
      return { content: [{ type: 'text', text: JSON.stringify({ ok: true, result }) }], structuredContent: { ok: true, result } };
    } catch (error) {
      const failure = { ok: false, error: { code: error instanceof BridgeError ? error.code : 'INTERNAL_ERROR', message: error instanceof BridgeError ? error.message : 'Unexpected local bridge error.' } };
      if (error.details) failure.error.details = error.details;
      return { content: [{ type: 'text', text: JSON.stringify(failure) }], structuredContent: failure, isError: true };
    }
  });
}

await server.connect(new StdioServerTransport());
