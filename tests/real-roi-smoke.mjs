#!/usr/bin/env node
// Opt-in real Krita test. No GUI, synthetic transport or worker restart.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile, appendFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import sharp from 'sharp';
import { createFixture } from './fixture.mjs';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

if (process.argv.length !== 3 || process.argv[2] !== '--run') {
  process.stderr.write('Activate the updated Krita bridge first. Explicit real test: node tests/real-roi-smoke.mjs --run\n');
  process.exit(2);
}
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const output = join(root, 'output', `real-roi-smoke-${new Date().toISOString().replace(/[:.]/g, '-')}`);
const sourcePath = join(output, 'synthetic-source.png');
const sourceRect = { x: 540, y: 80, width: 120, height: 100 };
const patchMaskRect = { x: 560, y: 100, width: 80, height: 60 };
const cloneDestination = { x: 800, y: 300 };
const cloneMaskRect = { x: 820, y: 320, width: 80, height: 60 };
const client = new Client({ name: 'krita-real-roi-smoke', version: '0.2.0' });
const transport = new StdioClientTransport({ command: process.execPath, args: [join(root, 'server/index.mjs')], env: { ...process.env }, stderr: 'inherit' });
const report = { kind: 'real-Krita-ROI-through-MCP', startedAt: new Date().toISOString(), output, sourcePath, checks: {}, artifacts: {}, toolNames: [] };
const used = new Set();
const hash = value => createHash('sha256').update(value).digest('hex');
let sequence = 0;
const polygon = r => [[r.x, r.y], [r.x + r.width, r.y], [r.x + r.width, r.y + r.height], [r.x, r.y + r.height]];
async function record(name, value) {
  await writeFile(join(output, `${String(++sequence).padStart(2, '0')}-${name}.json`), `${JSON.stringify(value, null, 2)}\n`, { flag: 'wx' });
}
async function call(op, args = {}, expectFailure = false) {
  const name = `krita_${op}`;
  used.add(name);
  const result = await client.callTool({ name, arguments: args }, undefined, { timeout: Number(process.env.KRITA_MCP_TIMEOUT_MS || 120000) + 15000 });
  await record(name, { args, result });
  await appendFile(join(output, 'progress.jsonl'), `${JSON.stringify({ at: new Date().toISOString(), tool: name, ok: !result.isError, expectFailure })}\n`);
  if (expectFailure) {
    assert(result.isError && result.structuredContent?.ok === false, `${name} must reject the invalid request`);
    return result.structuredContent.error;
  }
  assert(!result.isError && result.structuredContent?.ok === true, `${name} failed; inspect saved response before retrying`);
  return result.structuredContent.result;
}
async function rgba(path, width = 1200, height = 1600) {
  const { data, info } = await sharp(path, { failOn: 'error' }).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  assert.equal(info.width, width); assert.equal(info.height, height); assert.equal(info.channels, 4);
  return data;
}
async function artifact(result, label, width = 1200, height = 1600) {
  const bytes = await readFile(result.path);
  assert.equal(result.sha256, hash(bytes), `${label} file SHA`);
  const pixels = await rgba(result.path, width, height);
  report.artifacts[label] = { path: result.path, sha256: hash(bytes), bytes: bytes.length, width, height, rgbaSha256: hash(pixels) };
  return pixels;
}
function sourceCrop(source, rect) {
  const target = Buffer.alloc(rect.width * rect.height * 4);
  for (let row = 0; row < rect.height; row++) {
    source.copy(target, row * rect.width * 4, ((rect.y + row) * 1200 + rect.x) * 4, ((rect.y + row) * 1200 + rect.x + rect.width) * 4);
  }
  return target;
}
function compare(source, actual, allowedRects) {
  assert.equal(actual.length, source.length);
  let changedInside = 0, changedOutside = 0;
  const perRegion = allowedRects.map(() => 0);
  for (let y = 0; y < 1600; y++) for (let x = 0; x < 1200; x++) {
    const index = (y * 1200 + x) * 4;
    if (source.subarray(index, index + 4).equals(actual.subarray(index, index + 4))) continue;
    const region = allowedRects.findIndex(r => x >= r.x && x <= r.x + r.width && y >= r.y && y <= r.y + r.height);
    if (region === -1) changedOutside++; else { changedInside++; perRegion[region]++; }
  }
  return { comparedPixels: 1200 * 1600, changedInside, changedOutside, perRegion };
}
async function preview(documentId, label) { return artifact(await call('preview', { documentId }), label); }
function docFrom(state, id) { const doc = state.documents.find(d => d.documentId === id); assert(doc); return doc; }

await mkdir(join(root, 'output'), { recursive: true });
await mkdir(output);
try {
  await createFixture(sourcePath);
  const initialSourceSHA = hash(await readFile(sourcePath));
  const source = await rgba(sourcePath);
  await client.connect(transport);
  const tools = await client.listTools(); await record('tool-discovery', tools);
  assert.equal(tools.tools.length, 14, 'Updated MCP must expose all fourteen named tools');
  const before = await call('state');
  for (const op of ['export_region', 'import_patch', 'clone_patch', 'prepare_patch']) assert(before.capabilities.operations.includes(op), `Running Krita plugin lacks ${op}; activate the updated worker before testing`);
  const opened = await call('open_copy', { path: sourcePath });
  const documentId = opened.documentId;
  assert.equal(opened.layers.length, 1); assert.equal(opened.layers[0].type, 'paintlayer');
  const baseId = opened.layers[0].nodeId;
  const baseline = await preview(documentId, 'baseline'); assert(baseline.equals(source));

  const rawRegion = await call('export_region', { documentId, nodeId: baseId, rect: sourceRect });
  assert.deepEqual(rawRegion.rect, sourceRect); assert.deepEqual(rawRegion.canvas, { width: 1200, height: 1600 });
  assert.equal(rawRegion.readMode, 'raw-paintlayer'); assert.equal(rawRegion.pixelFormat, 'BGRA/U8');
  const rawPixels = await artifact(rawRegion, 'rawRegion', sourceRect.width, sourceRect.height);
  assert(rawPixels.equals(sourceCrop(source, sourceRect)), 'Raw layer ROI must equal the exact source crop');
  const projectedRegion = await call('export_region', { documentId, rect: sourceRect });
  assert.equal(projectedRegion.readMode, 'current-document-projection');
  assert((await artifact(projectedRegion, 'projectedRegion', sourceRect.width, sourceRect.height)).equals(rawPixels));
  report.checks.sourceAndProjectionROIExact = true;

  const prepared = await call('prepare_patch', { path: rawRegion.path, width: 240, height: 200 });
  assert.equal(prepared.resampled, true); assert.equal(prepared.input.sha256, rawRegion.sha256);
  assert.equal(prepared.input.width, 120); assert.equal(prepared.input.height, 100);
  assert.equal(prepared.width, 240); assert.equal(prepared.height, 200);
  assert.notEqual(prepared.path, rawRegion.path);
  const resizedPixels = await artifact(prepared, 'explicitUniformResize', 240, 200);
  assert(resizedPixels.every((value, index) => index % 4 !== 3 || value === 255), 'Opaque input must remain opaque after native uniform resizing');
  assert.equal(hash(await readFile(rawRegion.path)), rawRegion.sha256, 'Preparation must not overwrite its input');
  await call('prepare_patch', { path: rawRegion.path, width: 240, height: 201 }, true);
  report.checks.explicitUniformResize = { input: [120, 100], output: [240, 200], aspectPreserved: true, inputUnchanged: true, nonuniformRejected: true };

  // Bounds failures must not add a partial layer or clip/resample the request.
  await call('export_region', { documentId, rect: { x: 1199, y: 80, width: 120, height: 100 } }, true);
  await call('import_patch', { documentId, path: rawRegion.path, name: 'Invalid outside patch', x: 1199, y: 80 }, true);
  await call('clone_patch', { documentId, nodeId: baseId, rect: sourceRect, destination: { x: 1199, y: 80 }, name: 'Invalid outside clone' }, true);
  assert.equal(docFrom(await call('state'), documentId).layers.length, 1);
  assert((await preview(documentId, 'afterRejectedBounds')).equals(source));
  report.checks.invalidBoundsNoMutation = true;

  const imported = await call('import_patch', { documentId, path: rawRegion.path, name: 'ROI roundtrip and masked correction', x: sourceRect.x, y: sourceRect.y });
  assert.deepEqual(imported.rect, sourceRect); assert.equal(imported.input.sha256, rawRegion.sha256);
  const patchId = imported.nodeId;
  assert((await preview(documentId, 'importRoundtrip')).equals(source), 'ROI export/import at the same coordinates must preserve every source RGBA byte');
  report.checks.importRoundtripExact = true;
  await call('mask_set', { documentId, nodeId: patchId, name: 'Patch inner rectangle', polygon: polygon(patchMaskRect) });
  await call('filter_add', { documentId, nodeId: patchId, name: 'Ordinary local warm correction', filterId: 'colorbalance', properties: { cyan_red_midtones: 16, yellow_blue_midtones: -16, preserve_luminosity: true } });
  const patched = await preview(documentId, 'maskedPatch');
  const patchDiff = compare(source, patched, [patchMaskRect]);
  assert.equal(patchDiff.changedOutside, 0); assert(patchDiff.changedInside > 0);
  report.checks.maskedPatch = patchDiff;
  await call('layer_set_visibility', { documentId, nodeId: patchId, visible: false });
  assert((await preview(documentId, 'patchHiddenRollback')).equals(source));
  report.checks.patchHideRollbackExact = true;

  const cloned = await call('clone_patch', { documentId, nodeId: baseId, rect: sourceRect, destination: cloneDestination, name: 'Source clone on a new layer' });
  const cloneId = cloned.nodeId;
  assert.equal(cloned.sourceNodeId, baseId); assert.equal(cloned.nativePixelSha256, rawRegion.nativePixelSha256);
  assert.deepEqual(cloned.sourceRect, sourceRect); assert.deepEqual(cloned.rect, { ...cloneDestination, width: sourceRect.width, height: sourceRect.height });
  const cloneROI = await call('export_region', { documentId, nodeId: cloneId, rect: cloned.rect });
  assert((await artifact(cloneROI, 'cloneRawRegion', sourceRect.width, sourceRect.height)).equals(rawPixels), 'Clone must copy exact raw source pixels to the destination');
  await call('mask_set', { documentId, nodeId: cloneId, name: 'Clone inner rectangle', polygon: polygon(cloneMaskRect) });
  const clonePixels = await preview(documentId, 'maskedClone');
  const cloneDiff = compare(source, clonePixels, [cloneMaskRect]);
  assert.equal(cloneDiff.changedOutside, 0); assert(cloneDiff.changedInside > 0);
  report.checks.maskedClone = cloneDiff;
  const baseAfter = await call('export_region', { documentId, nodeId: baseId, rect: sourceRect });
  assert.equal(baseAfter.nativePixelSha256, rawRegion.nativePixelSha256);
  assert((await artifact(baseAfter, 'sourceROIUnchanged', sourceRect.width, sourceRect.height)).equals(rawPixels));
  await call('layer_set_visibility', { documentId, nodeId: cloneId, visible: false });
  assert((await preview(documentId, 'cloneHiddenRollback')).equals(source));
  report.checks.cloneSourceAndHideRollbackExact = true;

  await call('layer_set_visibility', { documentId, nodeId: cloneId, visible: true });
  await call('layer_set_visibility', { documentId, nodeId: patchId, visible: true });
  const combined = await preview(documentId, 'combined');
  const combinedDiff = compare(source, combined, [patchMaskRect, cloneMaskRect]);
  assert.equal(combinedDiff.changedOutside, 0); assert(combinedDiff.perRegion.every(count => count > 0));
  report.checks.combinedLocalEdits = combinedDiff;
  const saved = await call('save_as', { documentId, path: join(output, 'roi-roundtrip.kra') });
  assert.equal(saved.sha256, hash(await readFile(saved.path)));
  report.artifacts.kra = saved;
  const reopened = await call('open_copy', { path: saved.path });
  assert.notEqual(reopened.documentId, documentId); assert.equal(reopened.sourceSha256, saved.sha256);
  assert.equal(reopened.layers.length, 3, 'Editable base, local correction and clone layers must survive KRA reopening');
  const finalExport = await call('export_png', { documentId: reopened.documentId, path: join(output, 'roi-roundtrip.png') });
  assert((await artifact(finalExport, 'reopenedKraExport')).equals(combined), 'Saved KRA must reproduce the exact combined image');
  report.checks.reopenedKraExact = true;
  const after = await call('state');
  for (const doc of before.documents) assert.deepEqual(docFrom(after, doc.documentId), doc, 'Pre-existing bridge-owned document changed');
  assert.equal(hash(await readFile(sourcePath)), initialSourceSHA);
  report.checks.sourceFileUnchanged = true;
  report.checks.preexistingBridgeDocumentsUnchanged = true;
  report.status = 'passed'; report.completedAt = new Date().toISOString(); report.toolNames = [...used];
  report.limitations = ['These are exact local 2D edits and rollback checks, not proof of architectural correctness or hidden geometry.', 'No unmanaged user documents are exposed or visually inspected.'];
  await writeFile(join(output, 'report.json'), `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
  process.stdout.write(`${JSON.stringify({ status: report.status, output, checks: report.checks, artifacts: report.artifacts }, null, 2)}\n`);
} catch (error) {
  report.status = 'failed'; report.failedAt = new Date().toISOString(); report.toolNames = [...used]; report.error = { name: error.name, message: error.message, code: error.code };
  await writeFile(join(output, 'failure.json'), `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
  process.stderr.write(`Real ROI smoke failed: ${error.message}\nEvidence: ${output}\nInspect state before retrying mutations.\n`);
  process.exitCode = 1;
} finally { await client.close(); }
