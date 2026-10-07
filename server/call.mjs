#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

// This CLI exercises real MCP initialize/listTools/callTool over stdio.
// JSON may be passed as one shell-quoted argument or read from @/absolute/path.json.
const [requestedName = 'state', argument = '{}'] = process.argv.slice(2);
const name = requestedName.startsWith('krita_') ? requestedName : `krita_${requestedName}`;
const client = new Client({ name: 'krita-bridge-cli', version: '0.2.0' });
const serverPath = fileURLToPath(new URL('./index.mjs', import.meta.url));
const transport = new StdioClientTransport({ command: process.execPath, args: [serverPath], env: Object.fromEntries(Object.entries(process.env).filter(([, value]) => value !== undefined)), stderr: 'inherit' });
try {
  const args = JSON.parse(argument.startsWith('@') ? await readFile(argument.slice(1), 'utf8') : argument);
  await client.connect(transport);
  const listing = await client.listTools();
  if (requestedName === 'list') {
    process.stdout.write(`${JSON.stringify(listing, null, 2)}\n`);
  } else {
    if (!listing.tools.some(tool => tool.name === name)) throw new Error(`Unknown tool ${name}. Run: node server/call.mjs list`);
    const result = await client.callTool({ name, arguments: args }, undefined, { timeout: Number(process.env.KRITA_MCP_TIMEOUT_MS || 120000) + 15000 });
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    if (result.isError) process.exitCode = 1;
  }
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
} finally { await client.close(); }
