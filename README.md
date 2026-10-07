# Krita MCP Bridge

**Local image editing through MCP and the official Krita Python API.**

**Локальное редактирование изображений через MCP и официальный Python API Krita.**

[English](#english) · [Русский](#русский)

```text
MCP client → Node.js stdio server → authenticated loopback HTTP → Krita / Qt main thread
```

## English

Krita MCP Bridge exposes **14 named tools** for document copies, editable layers, masks, ordinary color correction, precise image patches, and new KRA/PNG exports. It uses the Krita API, without clicks, keystrokes, application screenshots, or an arbitrary Python execution endpoint. Image generation is handled separately by your client.

### Requirements and status

- Node.js 22 or newer.
- Krita with Python scripting and PyQt5. Native operations were tested on **Krita 5.3.4, macOS**.
- The optional background worker launcher supports macOS. Linux activation through Krita is provided but unverified. Windows is currently unsupported.
- Experimental version **0.2.0**. Normal document and region workflows have real Krita test evidence; error recovery has [known limitations](docs/KNOWN_ISSUES.md). Transport tests alone do not prove native editing.

### Quick start

```sh
git clone https://github.com/moseskm98-code/krita-mcp-bridge.git
cd krita-mcp-bridge
npm ci
```

On macOS, start a separate official `kritarunner` process:

```sh
npm run worker
node server/call.mjs state
```

The launcher creates its own settings and resource profile under `~/.local/share/krita-mcp-bridge/runner-profile`. It does not close or restart GUI Krita. Set `KRITA_RUNNER_PATH` if your Krita application is installed elsewhere. Do not run this launcher and manual activation simultaneously.

Alternatively, manually run this line in Krita's **Scripter**, replacing the path with your clone's absolute path:

```python
import runpy; runpy.run_path('/absolute/path/to/krita-mcp-bridge/bootstrap.py')
```

This activates the Python bridge for that Krita process. The script resolves its package relative to the repository. No Python package is installed into your system Python. See the [Krita Python side](krita_plugin/README.md) for persistent plugin activation.

### Connect an MCP client

Configure a stdio server using the absolute Node executable and repository paths:

```json
{
  "mcpServers": {
    "krita": {
      "command": "/absolute/path/to/node",
      "args": ["/absolute/path/to/krita-mcp-bridge/server/index.mjs"]
    }
  }
}
```

Use your client's equivalent stdio configuration if its format differs. The Node MCP server never launches Krita itself. An inactive Python bridge returns `BRIDGE_NOT_ACTIVE`.

### Tools

| Tool | Purpose |
| --- | --- |
| `krita_state` | Bridge-owned documents, layer trees, live filter capabilities |
| `krita_open_copy` | Open an independent working copy |
| `krita_import_png` | Import a full-canvas PNG as a paint layer |
| `krita_mask_set` | Create or replace a transparency mask from a polygon or grayscale PNG |
| `krita_filter_add` | Add a native Color Balance, HSV, or Levels filter mask |
| `krita_layer_set_visibility` | Set an explicit node's visibility |
| `krita_layer_reorder` | Reorder siblings within the same parent |
| `krita_save_as` | Save a new editable KRA |
| `krita_export_png` | Export a new composited PNG |
| `krita_preview` | Export a fresh local PNG preview |
| `krita_export_region` | Export exact projection or raw paint-layer pixels |
| `krita_prepare_patch` | Explicitly resize a patch with equal aspect ratio |
| `krita_import_patch` | Place a PNG patch at integer canvas coordinates |
| `krita_clone_patch` | Copy raw source pixels to a new layer; not healing |

Always use `documentId` and `nodeId` from actual responses. Full-canvas layers must match dimensions and color profile. Patch import does not resize or align geometry. Existing outputs and symlink paths are rejected. See [operation details](docs/OPERATIONS.md).

### Verification

```sh
npm test
npm run test:python
```

Opt-in native tests require an active bridge. They use synthetic fixtures and new output directories:

```sh
node tests/real-krita-smoke.mjs --run
node tests/real-roi-smoke.mjs --run
```

Only an actual `report.json` with `status: "passed"` confirms that test run. See [verification scope](docs/VERIFICATION.md). Preserved source coordinates do not certify the geometry or visual quality of added content.

### Local security and recovery

HTTP binds only to `127.0.0.1`. A private token file (`0600`) authorizes requests; browser Origin headers are rejected. Never commit or share `connection.json`. The service has local filesystem access under your account; it is not an OS sandbox.

**After timeout, cancellation, or connection loss, inspect state and output files before retrying a mutation.** Cancellation may not stop a queued command. [Known issues](docs/KNOWN_ISSUES.md) describe the current recovery limits.

---

## Русский

Krita MCP Bridge предоставляет **14 именованных инструментов**: копии документов, редактируемые слои, маски, обычный цветокор, точные фрагменты и новые KRA/PNG. Мост использует API Krita. Клики, клавиши, снимки экрана приложения и произвольное исполнение Python через MCP не используются. Генерацию изображений вызывает клиент отдельно.

### Требования и статус

- Node.js 22 или новее.
- Krita с Python и PyQt5. Реальные операции проверены на **Krita 5.3.4 в macOS**.
- Отдельный фоновый worker поддерживает macOS. Ручная активация в Linux предусмотрена, но не проверена. Windows пока не поддерживается.
- Экспериментальная версия **0.2.0**. Обычный цикл и операции с фрагментами проверены в настоящей Krita. У восстановления после ошибок есть [известные ограничения](docs/KNOWN_ISSUES.md). Тест транспорта не доказывает работу редактора.

### Быстрый запуск

```sh
git clone https://github.com/moseskm98-code/krita-mcp-bridge.git
cd krita-mcp-bridge
npm ci
npm run worker
node server/call.mjs state
```

`npm run worker` работает в macOS. Он запускает отдельный официальный `kritarunner` и создаёт свой профиль в `~/.local/share/krita-mcp-bridge/runner-profile`. Открытая GUI Krita не закрывается и не перезапускается. Если приложение установлено в другой папке, укажите `KRITA_RUNNER_PATH`. Не активируйте одновременно worker и мост внутри GUI Krita.

Другой способ — вручную выполнить строку в **Scripter** Krita. Подставьте абсолютный путь к своей копии репозитория:

```python
import runpy; runpy.run_path('/absolute/path/to/krita-mcp-bridge/bootstrap.py')
```

Активация действует в этом процессе Krita. Скрипт находит Python-пакет относительно репозитория и не устанавливает его в системный Python. Постоянная активация описана в [документации Python-плагина](krita_plugin/README.md).

### Подключение клиента и операции

Для MCP-клиента задайте stdio-сервер: абсолютный путь к Node и аргумент `/absolute/path/to/krita-mcp-bridge/server/index.mjs`. Пример JSON есть в английском разделе. Для клиента с другим форматом используйте его аналог настройки stdio. Сам Node-сервер не запускает Krita; без активного Python-моста он возвращает `BRIDGE_NOT_ACTIVE`.

| Инструмент | Действие |
| --- | --- |
| `krita_state` | Состояние копий моста, слои и доступные фильтры |
| `krita_open_copy` | Открыть отдельную рабочую копию |
| `krita_import_png` | Добавить PNG размером с холст отдельным слоем |
| `krita_mask_set` | Создать или заменить маску из полигона или серого PNG |
| `krita_filter_add` | Добавить маску Color Balance, HSV или Levels |
| `krita_layer_set_visibility` | Изменить видимость указанного узла |
| `krita_layer_reorder` | Изменить порядок слоёв в одном родителе |
| `krita_save_as` | Сохранить новый редактируемый KRA |
| `krita_export_png` | Экспортировать новый итоговый PNG |
| `krita_preview` | Создать новый локальный PNG для просмотра |
| `krita_export_region` | Экспортировать точную область проекции или paintlayer |
| `krita_prepare_patch` | Явно изменить размер фрагмента с сохранением пропорций |
| `krita_import_patch` | Поместить PNG-фрагмент по целым координатам холста |
| `krita_clone_patch` | Копировать сырые пиксели в новый слой; это не healing |

Берите `documentId` и `nodeId` из фактических ответов. Полноразмерный слой должен совпадать с холстом по размеру и профилю цвета. Импорт фрагмента не меняет его размер и перспективу. Существующие выходные файлы и пути с symlink отклоняются. [Подробнее об операциях](docs/OPERATIONS.md).

### Проверки и ограничения

```sh
npm test
npm run test:python
```

Для проверки настоящей Krita сначала активируйте мост, затем явно запустите:

```sh
node tests/real-krita-smoke.mjs --run
node tests/real-roi-smoke.mjs --run
```

Тесты создают синтетические изображения и новые папки результата. Успех подтверждает только фактический `report.json` со статусом `passed`. [Границы проверки](docs/VERIFICATION.md).

HTTP слушает только `127.0.0.1`. Токен хранится в закрытом файле с правами `0600`; запросы с Origin отклоняются. Не публикуйте `connection.json`. Мост работает с правами вашего пользователя и не является системной песочницей.

**После тайм-аута, отмены или потери связи проверьте состояние и выходные файлы до повтора правки.** Отмена может не остановить команду в очереди. [Известные ошибки](docs/KNOWN_ISSUES.md) описывают ограничения восстановления.

Сохранение координат исходного слоя не гарантирует правильную геометрию и качество добавленных объектов. Проверять нужно настоящий экспорт и маски.
