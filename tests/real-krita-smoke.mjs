#!/usr/bin/env node
// INTENTIONAL REAL-APP TEST. Never included in npm test; requires explicit --run.
// Uses named MCP tools only. Does not start, close, click or inject code into Krita.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile, appendFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { dirname, join, isAbsolute } from 'node:path';
import sharp from 'sharp';
import { createFixture } from './fixture.mjs';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

if (process.argv.length !== 3 || process.argv[2] !== '--run') {
  process.stderr.write('This test mutates separate working copies in real Krita. Activate the bridge first, then explicitly run: node tests/real-krita-smoke.mjs --run\n');
  process.exit(2);
}

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const output = join(root, 'output', `real-smoke-${new Date().toISOString().replace(/[:.]/g, '-')}`);
const sourcePath = join(output, 'synthetic-source.png');
const rect = { x0: 540, y0: 80, x1: 960, y1: 288 };
const polygon = [[rect.x0, rect.y0], [rect.x1, rect.y0], [rect.x1, rect.y1], [rect.x0, rect.y1]];
const client = new Client({ name: 'krita-real-smoke', version: '0.1.0' });
const transport = new StdioClientTransport({ command: process.execPath, args: [join(root, 'server/index.mjs')], env: { ...process.env }, stderr: 'inherit' });
const usedTools = new Set();
const report = { kind: 'real-Krita-through-MCP', startedAt: new Date().toISOString(), sourcePath, output, maskRectangle: rect, checks: {}, artifacts: {} };
let sequence = 0;

const sha = buffer => createHash('sha256').update(buffer).digest('hex');
async function fileProof(path) {
  assert(isAbsolute(path), 'Bridge output must be an absolute local path');
  const bytes = await readFile(path);
  assert((await stat(path)).isFile(), 'Artifact must be a regular file');
  return { path, bytes: bytes.length, sha256: sha(bytes) };
}
async function decoded(path) {
  const { data, info } = await sharp(path, { failOn: 'error' }).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  assert.equal(info.width, 1200, 'Actual PNG width');
  assert.equal(info.height, 1600, 'Actual PNG height');
  assert.equal(info.channels, 4, 'Actual decoded RGBA channels');
  return { data, width: info.width, height: info.height, rgbaSha256: sha(data) };
}
async function record(name, value) {
  await writeFile(join(output, `${String(++sequence).padStart(2, '0')}-${name}.json`), `${JSON.stringify(value, null, 2)}\n`, { flag: 'wx' });
}
async function call(operation, args = {}) {
  const tool = `krita_${operation}`;
  usedTools.add(tool);
  const startedAt = new Date().toISOString();
  const result = await client.callTool({ name: tool, arguments: args }, undefined, { timeout: Number(process.env.KRITA_MCP_TIMEOUT_MS || 120000) + 15000 });
  await record(tool, { startedAt, args, response: result });
  await appendFile(join(output, 'progress.jsonl'), `${JSON.stringify({ at: new Date().toISOString(), tool, ok: !result.isError })}\n`);
  assert(!result.isError, `${tool} failed; inspect saved tool response before any retry`);
  const envelope = result.structuredContent;
  assert.equal(envelope?.ok, true, `${tool} must return structured successful bridge envelope`);
  return envelope.result;
}
function documentById(state, documentId) {
  const document = state.documents.find(item => item.documentId === documentId);
  assert(document, `Document ${documentId} must be present in real state`);
  return document;
}
function children(document) {
  assert(Array.isArray(document.layers), 'State must expose the actual root child list bottom to top');
  return document.layers;
}
async function pngProof(result, label) {
  const proof = await fileProof(result.path);
  assert.equal(result.sha256, proof.sha256, `${label}: reported SHA must match the actual file`);
  const pixels = await decoded(result.path);
  report.artifacts[label] = { ...proof, width: pixels.width, height: pixels.height, rgbaSha256: pixels.rgbaSha256 };
  return pixels;
}
function comparePixels(original, candidate) {
  assert.equal(original.data.length, candidate.data.length);
  let changedInside = 0;
  let changedOutside = 0;
  let maxChannelDifference = 0;
  for (let y = 0; y < original.height; y++) {
    for (let x = 0; x < original.width; x++) {
      const offset = (y * original.width + x) * 4;
      let changed = false;
      for (let c = 0; c < 4; c++) {
        const difference = Math.abs(original.data[offset + c] - candidate.data[offset + c]);
        if (difference) changed = true;
        maxChannelDifference = Math.max(maxChannelDifference, difference);
      }
      if (changed) {
        if (x >= rect.x0 && x <= rect.x1 && y >= rect.y0 && y <= rect.y1) changedInside++;
        else changedOutside++;
      }
    }
  }
  return { changedInside, changedOutside, maxChannelDifference, comparedPixels: original.width * original.height };
}

await mkdir(join(root, 'output'), { recursive: true });
await mkdir(output); // Unique run directory. Never reuse or overwrite a previous run.
try {
  await createFixture(sourcePath);
  const sourceProof = await fileProof(sourcePath);
  const original = await decoded(sourcePath);
  report.artifacts.source = { ...sourceProof, rgbaSha256: original.rgbaSha256 };
  await client.connect(transport);
  const tools = await client.listTools();
  await record('tool-discovery', tools);
  const initialState = await call('state');
  assert(Array.isArray(initialState.documents));
  const colorBalanceCapability = initialState.capabilities.filters.find(filter => filter.id === 'colorbalance');
  assert.equal(colorBalanceCapability?.support, 'native-filter-mask', 'Live Krita must report native color-balance filter-mask support');
  assert(Object.hasOwn(colorBalanceCapability.defaultProperties, 'cyan_red_midtones'), 'Live filter must expose the requested correction property');
  const initialIds = initialState.documents.map(item => item.documentId);
  const opened = await call('open_copy', { path: sourcePath });
  const documentId = opened.documentId;
  assert.equal(typeof documentId, 'string');
  assert.equal(opened.sourceSha256, sourceProof.sha256);
  const openedState = await call('state');
  const openedDocument = documentById(openedState, documentId);
  assert.equal(openedDocument.width, 1200);
  assert.equal(openedDocument.height, 1600);
  const base = children(openedDocument).find(node => node.type === 'paintlayer');
  assert(base, 'Synthetic source must open with a paint layer');
  const imported = await call('import_png', { documentId, path: sourcePath, name: 'MCP smoke — masked correction' });
  const nodeId = imported.nodeId;
  assert.equal(typeof nodeId, 'string');
  assert.equal(imported.sourceSha256, sourceProof.sha256);
  const mask = await call('mask_set', { documentId, nodeId, name: 'MCP smoke — sky rectangle', polygon });
  await call('filter_add', { documentId, nodeId, name: 'MCP smoke — warm color balance', filterId: 'colorbalance', properties: { cyan_red_midtones: 12, yellow_blue_midtones: -12, preserve_luminosity: true } });
  const correctedState = await call('state');
  const expectedRestoredOrder = children(documentById(correctedState, documentId)).map(node => node.nodeId);
  assert(expectedRestoredOrder.includes(nodeId));
  assert(expectedRestoredOrder.includes(base.nodeId));
  report.mask = mask;

  await call('layer_set_visibility', { documentId, nodeId, visible: false });
  const hidden = await pngProof(await call('preview', { documentId }), 'hiddenPreview');
  assert(hidden.data.equals(original.data), 'With the correction layer hidden, every decoded RGBA byte must equal the synthetic source');
  report.checks.hiddenPreviewEqualsSource = true;
  await call('layer_set_visibility', { documentId, nodeId, visible: true });

  await call('layer_reorder', { documentId, nodeId, aboveId: null });
  const lowerState = await call('state');
  const lowerOrder = children(documentById(lowerState, documentId)).map(node => node.nodeId);
  assert.notDeepEqual(lowerOrder, expectedRestoredOrder, 'The actual layer order must change');
  assert.equal(lowerOrder[0], nodeId, 'Reordered layer must be bottommost in actual readback');
  const lower = await pngProof(await call('preview', { documentId }), 'belowBasePreview');
  assert(lower.data.equals(original.data), 'The opaque source base must hide the corrected layer below it');
  await call('layer_reorder', { documentId, nodeId, aboveId: base.nodeId });
  const restoredState = await call('state');
  assert.deepEqual(children(documentById(restoredState, documentId)).map(node => node.nodeId), expectedRestoredOrder, 'Original layer order must be restored');
  report.checks.reorderReadbackAndRestoration = true;

  const kraPath = join(output, 'mcp-smoke.kra');
  const pngPath = join(output, 'mcp-smoke.png');
  const saved = await call('save_as', { documentId, path: kraPath });
  report.artifacts.kra = await fileProof(kraPath);
  assert((await readFile(kraPath)).subarray(0, 4).equals(Buffer.from([0x50, 0x4b, 0x03, 0x04])), 'KRA must be an actual ZIP-format Krita document');
  if (saved.sha256 !== undefined) assert.equal(saved.sha256, report.artifacts.kra.sha256);
  const exported = await pngProof(await call('export_png', { documentId, path: pngPath }), 'export');
  const differences = comparePixels(original, exported);
  await record('rgba-comparison', differences);
  assert.equal(differences.changedOutside, 0, 'No RGBA pixel may change outside the mask rectangle');
  assert(differences.changedInside > 0, 'The ordinary correction must visibly change actual pixels inside its mask');
  report.checks.maskedCorrection = differences;

  const reopened = await call('open_copy', { path: kraPath });
  assert.notEqual(reopened.documentId, documentId, 'Reopening must create a separate working document');
  const reopenedState = await call('state');
  const reopenedDocument = documentById(reopenedState, reopened.documentId);
  const reopenedLayers = children(reopenedDocument);
  assert.equal(reopenedLayers.length, 2, 'Saved .kra must retain the base and imported editable layers');
  const reloadedCorrection = reopenedLayers.find(node => node.name === 'MCP smoke — masked correction');
  assert(reloadedCorrection, 'Saved .kra must retain the named correction layer');
  assert(reloadedCorrection.children.some(node => node.type === 'transparencymask'), 'Saved .kra must retain its editable transparency mask');
  assert(reloadedCorrection.children.some(node => node.type === 'filtermask'), 'Saved .kra must retain its editable filter mask');
  const roundtrip = await pngProof(await call('export_png', { documentId: reopened.documentId, path: join(output, 'mcp-smoke-roundtrip.png') }), 'roundtrip');
  assert(roundtrip.data.equals(exported.data), 'The reopened editable .kra must export exactly the same RGBA pixels');
  report.checks.kraRoundtripRGBAEqualsExport = true;
  report.checks.roundtripFileSHAEqualsExport = report.artifacts.roundtrip.sha256 === report.artifacts.export.sha256;
  // File SHA equality is recorded independently of RGBA equality: PNG metadata may differ.
  const finalState = await call('state');
  assert(initialIds.every(id => finalState.documents.some(item => item.documentId === id)), 'All previously open bridge-owned documents must remain open');
  for (const id of initialIds) assert.deepEqual(documentById(finalState, id), documentById(initialState, id), 'A pre-existing bridge-owned document must remain unchanged');
  assert.equal((await fileProof(sourcePath)).sha256, sourceProof.sha256, 'The synthetic source file must remain unchanged');
  assert.equal(usedTools.size, 10, 'The real pass must exercise all ten named MCP operations');
  report.checks.sourceFileUnchanged = true;
  report.checks.preexistingBridgeDocumentsUnchanged = true;
  report.limitations = ['State exposes bridge-owned copies only. This test does not inspect unmanaged user documents or claim pixel verification of their contents.'];
  report.toolsExercised = [...usedTools];
  report.completedAt = new Date().toISOString();
  report.status = 'passed';
  await writeFile(join(output, 'report.json'), `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
  process.stdout.write(`${JSON.stringify({ status: 'passed', output, checks: report.checks, artifacts: report.artifacts }, null, 2)}\n`);
} catch (error) {
  report.status = 'failed';
  report.failedAt = new Date().toISOString();
  report.error = { name: error.name, message: error.message, code: error.code };
  await writeFile(join(output, 'failure.json'), `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
  process.stderr.write(`Real Krita smoke failed: ${error.message}\nEvidence retained: ${output}\nDo not blindly retry mutations; inspect actual document state and output paths first.\n`);
  process.exitCode = 1;
} finally { await client.close(); }
