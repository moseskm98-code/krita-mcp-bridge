import { z } from 'zod';
import { isAbsolute, extname } from 'node:path';
import { BridgeError } from './bridge.mjs';

const absolutePath = z.string().min(1).max(4096).refine(value => isAbsolute(value) && !value.includes('\0'), 'Use an absolute local filesystem path.');
const pngPath = absolutePath.refine(value => extname(value).toLowerCase() === '.png', 'Expected a .png path.');
const kraPath = absolutePath.refine(value => extname(value).toLowerCase() === '.kra', 'Expected a .kra path.');
const id = z.string().uuid();
const name = z.string().min(1).max(240);
const point = z.tuple([z.number().finite().min(0).max(100000), z.number().finite().min(0).max(100000)]);
const polygon = z.array(point).min(3).max(10000);
const pixelPosition = z.number().int().min(0).max(11999);
const rect = z.object({ x: pixelPosition, y: pixelPosition, width: z.number().int().min(1).max(12000), height: z.number().int().min(1).max(12000) }).strict()
  .refine(value => value.x + value.width <= 12000 && value.y + value.height <= 12000, 'Rectangle exceeds the supported maximum canvas.');
const filterProperties = z.record(z.string().min(1).max(120), z.union([z.boolean(), z.number().finite(), z.string().max(4096)]));

export const operations = {
  state: {
    title: 'Read Krita documents and layers',
    description: 'Read actual open documents, document IDs, full layer trees and supported filter IDs/default properties from the active Krita plugin. No UI automation. IDs are valid only in the current Krita session.',
    schema: z.object({}).strict(),
    readOnly: true,
  },
  open_copy: {
    title: 'Open a working copy in Krita',
    description: 'Open a local raster or Krita document as a separate working document without replacing existing open documents or overwriting the input. Returns a runtime document ID; always save to a new .kra path.',
    schema: z.object({ path: absolutePath }).strict(),
  },
  import_png: {
    title: 'Import PNG as a layer',
    description: 'Import a PNG into an existing document as a new top paint layer, optionally in a group. PNG dimensions must match the document exactly; no implicit resize, warp or crop.',
    schema: z.object({ documentId: id, path: pngPath, name, parentId: id.optional() }).strict(),
  },
  export_region: {
    title: 'Export a document or layer region',
    description: 'Export an exact pixel rectangle to a new local PNG with its original canvas coordinates. Without nodeId exports the current document projection; with nodeId exports raw pixels of that paint layer without its masks or filters. No resampling. Returns rect, canvas, path and file hash.',
    schema: z.object({ documentId: id, rect, nodeId: id.optional() }).strict(),
    readOnly: true,
  },
  import_patch: {
    title: 'Place an exact PNG patch as a new layer',
    description: 'Import a local PNG patch at the requested integer x/y coordinates as a new paint layer, optionally in a group. The whole patch must fit the canvas. Krita requires matching RGBA/U8 color profile; there is no implicit resize, crop or color conversion. Existing source pixels stay in their layers.',
    schema: z.object({ documentId: id, path: pngPath, name, x: pixelPosition, y: pixelPosition, parentId: id.optional() }).strict(),
  },
  prepare_patch: {
    title: 'Prepare an explicitly resized PNG patch',
    description: 'Create a new local PNG at explicitly requested dimensions using native Krita/Qt resampling. Only exactly equal aspect ratios are accepted; no crop, stretch, warp or geometric alignment. The input is never overwritten. Returns input/output dimensions and hashes plus resampled status. Use before strict import_patch when an ImageGen patch has the right proportions but a different resolution.',
    schema: z.object({ path: pngPath, width: z.number().int().min(1).max(12000), height: z.number().int().min(1).max(12000) }).strict().refine(value => value.width * value.height <= 24000000, 'Output exceeds the 24 megapixel limit.'),
  },
  clone_patch: {
    title: 'Clone raw source pixels into a new layer',
    description: 'Copy an exact rectangle of raw pixels from one paint layer to a new normal paint layer at destination x/y, in the same parent. Source layer pixels, coordinates and masks remain unchanged. The destination must fit the canvas; no resampling or healing is performed.',
    schema: z.object({ documentId: id, nodeId: id, rect, destination: z.object({ x: pixelPosition, y: pixelPosition }).strict(), name }).strict(),
  },
  mask_set: {
    title: 'Create or update a transparency mask',
    description: 'Attach a transparency mask to a layer from polygon coordinates or a full-canvas grayscale PNG. White reveals, black hides; invert is optional. Replace only a named existing mask owned by this layer. Coordinates must be inside the actual canvas.',
    schema: z.object({
      documentId: id, nodeId: id, name: name.optional(), polygon: polygon.optional(),
      holes: z.array(polygon).max(100).optional(), grayPngPath: pngPath.optional(),
      invert: z.boolean().optional(), replaceMaskId: id.optional(),
    }).strict().superRefine((value, context) => {
      if ((value.polygon !== undefined) === (value.grayPngPath !== undefined)) context.addIssue({ code: 'custom', message: 'Provide exactly one of polygon or grayPngPath.' });
      if (value.holes !== undefined && value.polygon === undefined) context.addIssue({ code: 'custom', path: ['holes'], message: 'holes require polygon.' });
    }),
  },
  filter_add: {
    title: 'Add an ordinary correction mask',
    description: 'Add a non-generative Krita filter mask to an existing layer. Optional selectionGrayPngPath supplies a full-canvas grayscale selection: white applies the correction, black protects, gray blends. Only colorbalance, hsvadjustment and levels are permitted, and only when the live plugin reports them available. First read state for actual default properties; unsupported filters/properties fail explicitly.',
    schema: z.object({ documentId: id, nodeId: id, name, filterId: z.enum(['colorbalance', 'hsvadjustment', 'levels']), properties: filterProperties, selectionGrayPngPath: pngPath.optional() }).strict(),
  },
  layer_set_visibility: {
    title: 'Set layer visibility',
    description: 'Set the visibility of a specific existing node. Does not change pixels or delete nodes.',
    schema: z.object({ documentId: id, nodeId: id, visible: z.boolean() }).strict(),
  },
  layer_reorder: {
    title: 'Reorder a layer',
    description: 'Move a node immediately above another sibling in the same parent. aboveId=null moves it to the bottom. Cross-parent moves and invalid IDs fail explicitly.',
    schema: z.object({ documentId: id, nodeId: id, aboveId: id.nullable() }).strict(),
  },
  save_as: {
    title: 'Save a new Krita document',
    description: 'Save the editable document to a new absolute .kra path. Never silently overwrites an existing file. Inspect state and target file after a timeout before retrying.',
    schema: z.object({ documentId: id, path: kraPath }).strict(),
  },
  export_png: {
    title: 'Export the actual document to PNG',
    description: 'Export the current composited Krita document to a new .png file. Returns its actual path and metadata. Existing files are not overwritten. This is the true document projection, not a screenshot.',
    schema: z.object({ documentId: id, path: pngPath }).strict(),
  },
  preview: {
    title: 'Export a fresh document preview',
    description: 'Export the actual current document projection to a unique local PNG in the bridge preview directory. Returns a local image path for visual inspection; never uses Computer Use or screenshots.',
    schema: z.object({ documentId: id }).strict(),
    readOnly: true,
  },
};

export async function runOperation(bridge, operation, args) {
  const definition = operations[operation];
  if (!definition) throw new BridgeError('UNKNOWN_OPERATION', 'Unknown named Krita operation.');
  const parsed = definition.schema.safeParse(args);
  if (!parsed.success) {
    throw new BridgeError('INVALID_ARGUMENTS', parsed.error.issues.map(issue => `${issue.path.join('.') || 'arguments'}: ${issue.message}`).join('; ').slice(0, 2000));
  }
  return bridge.call(operation, parsed.data);
}
