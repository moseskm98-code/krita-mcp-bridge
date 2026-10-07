# Operations / Операции

Call `krita_state` first. Use actual document/layer IDs, capabilities, and filter `allowedProperties`. Full client schemas are published through MCP `listTools`; Python adds canvas, color, file, and filter validation.

Сначала вызовите `krita_state`. Используйте фактические ID, возможности и допустимые параметры фильтров. Полные схемы доступны через `listTools`. Python дополнительно проверяет холст, цвет, файлы и параметры.

```sh
node server/call.mjs open_copy '{"path":"/absolute/source.png"}'
node server/call.mjs import_png @/absolute/import.json
```

Example / пример `import.json`:

```json
{
  "documentId": "DOCUMENT_UUID_FROM_OPEN_COPY",
  "path": "/absolute/full-canvas-layer.png",
  "name": "Environment"
}
```

Replace example IDs with actual canonical lowercase UUIDs. Output paths must be new and absolute; parents must exist. Symlink paths are rejected.

Замените ID примера на реальные UUID в нижнем регистре. Выходные пути должны быть новыми и абсолютными. Родительские папки должны существовать. Symlink не допускается.

- `mask_set`: exactly one `polygon` or `grayPngPath`; optional `holes`, `invert`, `replaceMaskId`. Grayscale masks are opaque and full-canvas: white reveals, black hides, gray blends.
- `filter_add`: `colorbalance`, `hsvadjustment`, `levels`; optional full-canvas `selectionGrayPngPath`. Use `allowedProperties`; native factory-default names can differ, so do not blindly copy all defaults.
- `export_region`: `rect:{x,y,width,height}`; optional `nodeId` selects raw paint-layer pixels without masks/filters. Otherwise exports projection.
- `import_patch`: `path,name,x,y`, optional `parentId`. Exact size/profile, integer coordinates, no implicit resize.
- `prepare_patch`: `path,width,height`. Explicit equal-aspect resampling to a new PNG, without perspective alignment or generated detail.
- `clone_patch`: `nodeId,rect,destination:{x,y},name`. Copy raw pixels to a new normal sibling layer; source remains unchanged. Not healing.
- `layer_reorder`: `aboveId:null` means bottom; otherwise move above a sibling.

Limits: 12000 pixels per side, 24 million pixels per canvas, input file size 128 MiB. PNG imports require matching RGBA/U8 profiles. Only actual exports can verify the visual result.

Пределы: 12000 пикселей по стороне, 24 млн пикселей на холст, входной файл до 128 MiB. Импорт PNG требует совпадения RGBA/U8 и профиля. Проверять качество нужно по настоящему экспорту.
