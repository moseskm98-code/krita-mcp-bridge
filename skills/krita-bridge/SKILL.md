---
name: krita-bridge
description: "Edit local image copies through Krita MCP: layers, masks, color correction, precise patches, and new KRA/PNG outputs; no GUI automation."
---

# Krita through MCP / Krita через MCP

1. Read README and call `krita_state`. If inactive, use the documented worker or explicit manual activation. Do not use GUI automation, keystrokes, screenshots, or hidden code injection.
2. Open a copy with `krita_open_copy`. Use actual `documentId` and `nodeId` for every operation.
3. Define the allowed edit area. Add layers, masks, and native filters. Use explicit coordinates for patches. `prepare_patch` only resamples equal proportions; clone copies raw pixels, not healing.
4. Save a new KRA and export a new PNG. Never overwrite source or existing outputs.
5. Inspect the true PNG and critical regions. Source coordinates alone do not certify geometry or quality.
6. After cancellation, timeout, or connection loss, inspect state and files before retrying. A cancelled MCP request may still execute; see known issues.

Прочитайте README и состояние. Работайте с копией по фактическим ID. Задайте область правки. Сохраняйте новые KRA/PNG и проверяйте настоящий экспорт. После ошибки связи не повторяйте правку вслепую. Не перезапускайте пользовательскую GUI Krita.
