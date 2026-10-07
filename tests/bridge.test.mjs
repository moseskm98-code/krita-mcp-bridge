import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, writeFile, chmod, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { BridgeClient, readConnection } from '../server/bridge.mjs';
import { runOperation } from '../server/operations.mjs';

const token = 'test-private-token-never-returned-0123456789';
const documentId = '11111111-1111-4111-8111-111111111111';
const nodeId = '22222222-2222-4222-8222-222222222222';
async function fixture(t, handler = (_request, response) => response.end(JSON.stringify({ ok: true, result: {} }))) {
  const directory = await mkdtemp(join(tmpdir(), 'krita-bridge-test-'));
  const connectionPath = join(directory, 'connection.json');
  const server = createServer(handler);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const connection = { host: '127.0.0.1', port: server.address().port, token, pid: process.pid, sessionId: 'test-session', protocolVersion: 1 };
  await writeFile(connectionPath, JSON.stringify(connection), { mode: 0o600 });
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(directory, { recursive: true, force: true }); });
  return { connectionPath, connection, directory, server, client: new BridgeClient({ connectionPath, timeoutMs: 1000 }) };
}

test('inactive plugin returns a clear error without starting or controlling Krita', async () => {
  await assert.rejects(readConnection('/definitely-absent-krita-bridge/connection.json'), error => error.code === 'BRIDGE_NOT_ACTIVE' && error.message.includes('no Computer Use fallback'));
});

test('connection accepts only private current-user loopback protocol files', async t => {
  const f = await fixture(t);
  await chmod(f.connectionPath, 0o644);
  await assert.rejects(readConnection(f.connectionPath), { code: 'CONNECTION_PERMISSIONS' });
  await chmod(f.connectionPath, 0o600);
  await writeFile(f.connectionPath, JSON.stringify({ ...f.connection, host: 'example.com' }));
  await assert.rejects(readConnection(f.connectionPath), { code: 'CONNECTION_INVALID' });
  const linkPath = join(f.directory, 'link.json');
  await symlink(f.connectionPath, linkPath);
  await assert.rejects(readConnection(linkPath), { code: 'CONNECTION_INVALID' });
});

test('uses Bearer authorization and expected RPC envelope; hides echoed credentials', async t => {
  let received;
  const f = await fixture(t, async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    received = { url: request.url, auth: request.headers.authorization, body: JSON.parse(Buffer.concat(chunks)) };
    response.setHeader('content-type', 'application/json');
    response.end(JSON.stringify({ ok: true, result: { documents: [], token, note: `do not echo ${token}` } }));
  });
  const result = await runOperation(f.client, 'state', {});
  assert.deepEqual(received, { url: '/rpc', auth: `Bearer ${token}`, body: { op: 'state', args: {} } });
  assert.equal(result.token, '[redacted]');
  assert(!JSON.stringify(result).includes(token));
});

test('authorization rejection is explicit and does not expose server response/token', async t => {
  const f = await fixture(t, (_request, response) => { response.writeHead(401); response.end(`invalid token ${token}`); });
  await assert.rejects(f.client.call('state', {}), error => error.code === 'BRIDGE_AUTH_FAILED' && !error.message.includes(token));
});

test('timeout reports uncertain mutation outcome instead of suggesting blind retry', async t => {
  const f = await fixture(t, () => {});
  const client = new BridgeClient({ connectionPath: f.connectionPath, timeoutMs: 40 });
  await assert.rejects(client.call('save_as', { documentId, path: '/tmp/test.kra' }), error => error.code === 'BRIDGE_TIMEOUT' && error.details.outcomeUnknown === true && error.message.includes('before retrying'));
});

test('preserves named Krita errors, redacts their token and rejects malformed protocol', async t => {
  let malformed = false;
  const f = await fixture(t, (_request, response) => response.end(malformed ? '{' : JSON.stringify({ ok: false, error: { code: 'FILE_EXISTS', message: `Target exists ${token}` } })));
  await assert.rejects(f.client.call('save_as', {}), error => error.code === 'FILE_EXISTS' && !error.message.includes(token));
  malformed = true;
  await assert.rejects(f.client.call('state', {}), { code: 'BRIDGE_PROTOCOL_ERROR' });
});

test('rejects redirects and unreachable bridge without exposing network details', async t => {
  const f = await fixture(t, (_request, response) => { response.writeHead(302, { location: 'https://example.com/' }); response.end(); });
  await assert.rejects(f.client.call('state', {}), { code: 'BRIDGE_UNREACHABLE' });
  f.server.closeAllConnections();
  await new Promise(resolve => f.server.close(resolve));
  await assert.rejects(f.client.call('state', {}), { code: 'BRIDGE_UNREACHABLE' });
});

test('serializes requests even when called concurrently', async t => {
  const events = [];
  const f = await fixture(t, async (request, response) => {
    let data = '';
    for await (const chunk of request) data += chunk;
    const { op } = JSON.parse(data);
    events.push(`start:${op}`);
    await new Promise(resolve => setTimeout(resolve, 20));
    events.push(`end:${op}`);
    response.end(JSON.stringify({ ok: true, result: { op } }));
  });
  await Promise.all([f.client.call('one', {}), f.client.call('two', {})]);
  assert.deepEqual(events, ['start:one', 'end:one', 'start:two', 'end:two']);
});

test('validates mask source, identifiers, absolute output paths and filter whitelist before HTTP', async () => {
  let requests = 0;
  const bridge = { call: async () => { requests++; return {}; } };
  const invalid = [
    ['mask_set', { documentId, nodeId }],
    ['mask_set', { documentId, nodeId, polygon: [[0, 0], [20, 0], [20, 20]], grayPngPath: '/tmp/a.png' }],
    ['mask_set', { documentId, nodeId, grayPngPath: '/tmp/a.png', holes: [] }],
    ['save_as', { documentId, path: 'relative.kra' }],
    ['export_png', { documentId: 'wrong', path: '/tmp/a.png' }],
    ['export_png', { documentId, path: '/tmp/a.jpg' }],
    ['filter_add', { documentId, nodeId, name: 'x', filterId: 'python', properties: {} }],
    ['filter_add', { documentId, nodeId, name: 'x', filterId: 'levels', properties: { mode: 'lightness', lightness: '0;1;1;0;1' }, selectionGrayPngPath: '/tmp/selection.jpg' }],
    ['state', { script: 'print(1)' }],
  ];
  for (const [operation, args] of invalid) await assert.rejects(runOperation(bridge, operation, args), { code: 'INVALID_ARGUMENTS' });
  assert.equal(requests, 0);
  await runOperation(bridge, 'mask_set', { documentId, nodeId, polygon: [[0, 0], [20, 0], [20, 20]], holes: [] });
  assert.equal(requests, 1);
  await runOperation(bridge, 'filter_add', { documentId, nodeId, name: 'x', filterId: 'levels', properties: { mode: 'lightness', lightness: '0;1;1;0;1' }, selectionGrayPngPath: '/tmp/selection.png' });
  assert.equal(requests, 2);
});

test('ROI operations preserve named exact-coordinate arguments and reject malformed geometry before HTTP', async () => {
  const requests = [];
  const bridge = { call: async (op, args) => { requests.push({ op, args }); return {}; } };
  const area = { x: 540, y: 80, width: 120, height: 100 };
  const valid = [
    ['export_region', { documentId, rect: area }],
    ['export_region', { documentId, nodeId, rect: area }],
    ['import_patch', { documentId, path: '/tmp/patch.png', name: 'Patch', x: 600, y: 200 }],
    ['prepare_patch', { path: '/tmp/patch.png', width: 240, height: 200 }],
    ['clone_patch', { documentId, nodeId, rect: area, destination: { x: 700, y: 200 }, name: 'Clone' }],
  ];
  for (const [op, args] of valid) await runOperation(bridge, op, args);
  assert.deepEqual(requests, valid.map(([op, args]) => ({ op, args })));
  const invalid = [
    ['export_region', { documentId, rect: { ...area, width: 0 } }],
    ['export_region', { documentId, rect: { ...area, x: 0.5 } }],
    ['export_region', { documentId, rect: { ...area, x: -1 } }],
    ['export_region', { documentId, rect: { ...area, x: 11999 } }],
    ['export_region', { documentId, rect: area, resize: 2 }],
    ['import_patch', { documentId, path: '/tmp/patch.jpg', name: 'Patch', x: 600, y: 200 }],
    ['import_patch', { documentId, path: '/tmp/patch.png', name: 'Patch', x: 600, y: -200 }],
    ['prepare_patch', { path: '/tmp/patch.png', width: 0, height: 200 }],
    ['prepare_patch', { path: '/tmp/patch.png', width: 12000, height: 12000 }],
    ['prepare_patch', { path: '/tmp/patch.png', width: 240, height: 200, warp: true }],
    ['clone_patch', { documentId, nodeId, rect: area, destination: { x: 700.5, y: 200 }, name: 'Clone' }],
    ['clone_patch', { documentId, nodeId, rect: area, destination: { x: 700, y: 200, scale: 2 }, name: 'Clone' }],
  ];
  for (const [op, args] of invalid) await assert.rejects(runOperation(bridge, op, args), { code: 'INVALID_ARGUMENTS' });
  assert.equal(requests.length, valid.length);
});
