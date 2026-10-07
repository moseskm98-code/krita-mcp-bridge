import test from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { fileURLToPath } from 'node:url';

test('real MCP stdio startup lists named tools and returns inactive bridge error', async () => {
  const client = new Client({ name: 'krita-bridge-test', version: '0.1.0' });
  const transport = new StdioClientTransport({ command: process.execPath, args: [fileURLToPath(new URL('../server/index.mjs', import.meta.url))], env: { ...process.env, KRITA_MCP_CONNECTION_FILE: '/definitely-absent-krita-bridge/connection.json' }, stderr: 'pipe' });
  try {
    await client.connect(transport);
    const result = await client.listTools();
    assert.equal(result.tools.length, 14);
    assert(result.tools.every(tool => tool.name.startsWith('krita_')));
    assert(!result.tools.some(tool => /python|script|exec/.test(tool.name)));
    const response = await client.callTool({ name: 'krita_state', arguments: {} });
    assert.equal(response.isError, true);
    assert.equal(response.structuredContent.error.code, 'BRIDGE_NOT_ACTIVE');
    const invalid = await client.callTool({ name: 'krita_mask_set', arguments: { documentId: '11111111-1111-4111-8111-111111111111', nodeId: '22222222-2222-4222-8222-222222222222' } });
    assert.equal(invalid.isError, true);
  } finally { await client.close(); }
});
