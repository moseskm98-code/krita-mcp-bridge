"""Local authenticated bridge. All Krita calls execute on Qt's main thread.

HTTP workers only validate authentication/envelope and enqueue named requests.
There is no eval, exec, shell, plugin-install, restart, or arbitrary-Python op.
"""
from pathlib import Path
import atexit
import hashlib
import hmac
import json
import os
import queue
import secrets
import shutil
import stat
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from krita import Krita, Extension, InfoObject, Selection
from PyQt5.QtCore import QByteArray, QPointF, Qt, QThread, QTimer, QUuid
from PyQt5.QtGui import QImage, QPainter, QPolygonF, QColor
from PyQt5.QtWidgets import QApplication

from .validation import (BridgeError, require, keys, identifier, label, boolean,
                         dimensions, no_symlink_path, polygon,
                         FILTER_SCHEMAS, filter_properties, rectangle, png_dimensions, uniform_dimensions)

PROTOCOL_VERSION = 1
PRIVATE_ROOT = Path.home() / '.local/share/krita-mcp-bridge'
OPERATIONS = ('state', 'open_copy', 'import_png', 'mask_set', 'filter_add',
              'layer_set_visibility', 'layer_reorder', 'save_as', 'export_png', 'preview',
              'export_region', 'import_patch', 'clone_patch', 'prepare_patch')
_runtime = None
_extension = None


def digest(filename):
    h = hashlib.sha256()
    with open(filename, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def private_dir(path):
    # Create only this user's local runtime hierarchy; do not accept a symlink
    # in any component and do not alter unrelated directory permissions.
    path = Path(path)
    missing = []
    current = path
    while not current.exists():
        require(not current.is_symlink(), 'Runtime path contains a symlink', 'SYMLINK_PATH')
        missing.append(current)
        current = current.parent
    no_symlink_path(str(current), directory=True)
    for item in reversed(missing):
        item.mkdir(mode=0o700)
    no_symlink_path(str(path), directory=True)
    require(path.stat().st_uid == os.getuid(), 'Runtime directory has a different owner', 'UNSAFE_RUNTIME')
    os.chmod(path, 0o700)
    return path


def node_id(node):
    return str(node.uniqueId().toString()).strip('{}').lower()


def refresh(doc):
    doc.waitForDone()
    doc.refreshProjection()
    doc.waitForDone()


def scalar(value):
    if value is None or type(value) in (str, bool, int, float):
        return value
    return str(value)


class Job:
    def __init__(self, op, args):
        self.op, self.args = op, args
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.started = False
        self.cancelled = False
        self.response = None


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    server_version = 'KritaLocalBridge/1'

    def log_message(self, fmt, *args):
        pass  # Never log Authorization or request bodies.

    def reply(self, status, body):
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        runtime = self.server.runtime
        if self.path != '/rpc':
            self.reply(404, {'ok': False, 'error': {'code': 'NOT_FOUND', 'message': 'Use POST /rpc'}})
            return
        # Native MCP requests have no Origin. Do not expose browser CORS access.
        authorization = self.headers.get('Authorization', '')
        authorized = hmac.compare_digest(authorization, 'Bearer ' + runtime.token)
        if not authorized or self.headers.get('Origin') is not None:
            self.reply(401, {'ok': False, 'error': {'code': 'UNAUTHORIZED', 'message': 'Authentication required'}})
            return
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            self.reply(415, {'ok': False, 'error': {'code': 'CONTENT_TYPE', 'message': 'Use application/json'}})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            require(0 < length <= 1_048_576, 'Request size is outside the supported range')
            self.connection.settimeout(15)
            raw = self.rfile.read(length)
            require(len(raw) == length, 'Incomplete request body')
            request = json.loads(raw.decode('utf-8'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))
            keys(request, required=('op', 'args'))
            require(request['op'] in OPERATIONS, 'Operation is not supported', 'UNKNOWN_OPERATION')
            require(isinstance(request['args'], dict), 'args must be an object')
            job = Job(request['op'], request['args'])
            try:
                runtime.jobs.put_nowait(job)
            except queue.Full:
                raise BridgeError('BUSY', 'Operation queue is full; retry after the current operation')
            if not job.done.wait(180):
                with job.lock:
                    if not job.started:
                        job.cancelled = True
                        raise BridgeError('TIMEOUT_CANCELLED', 'Queued request was cancelled before execution')
                raise BridgeError('TIMEOUT_RUNNING', 'Operation is still running; inspect state before retrying a write')
            self.reply(200, job.response)
        except BridgeError as error:
            self.reply(200, {'ok': False, 'error': {'code': error.code, 'message': str(error)}})
        except (ValueError, TypeError, UnicodeError, OSError):
            self.reply(400, {'ok': False, 'error': {'code': 'INVALID_REQUEST', 'message': 'Invalid request envelope or JSON'}})

    def do_GET(self):
        self.reply(405, {'ok': False, 'error': {'code': 'METHOD_NOT_ALLOWED', 'message': 'Use authenticated POST /rpc'}})

    do_OPTIONS = do_GET


class BridgeRuntime:
    def __init__(self):
        self.app = Krita.instance()
        require(QThread.currentThread() == QApplication.instance().thread(),
                'Start the bridge inside Krita Scripter on the Qt main thread', 'WRONG_THREAD')
        self.session_id = str(uuid.uuid4())
        self.token = secrets.token_urlsafe(48)
        self.documents = {}
        self.jobs = queue.Queue(maxsize=32)
        self.closed = False
        self.root = private_dir(PRIVATE_ROOT)
        self.session_dir = private_dir(self.root / 'sessions' / self.session_id)
        self.copy_dir = private_dir(self.session_dir / 'copies')
        self.preview_dir = private_dir(self.session_dir / 'previews')
        self.connection_file = self.root / 'connection.json'
        self.server = None
        self.timer = None

    def start(self):
        if self.connection_file.exists() or self.connection_file.is_symlink():
            no_symlink_path(str(self.connection_file))
            st = self.connection_file.stat()
            require(st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o600,
                    'Existing connection file is not private', 'UNSAFE_RUNTIME')
            try:
                previous = json.loads(self.connection_file.read_text(encoding='utf-8'))
                pid = previous.get('pid')
                if type(pid) is int and pid > 0:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        pass
                    else:
                        raise BridgeError('ACTIVE_BRIDGE', 'A bridge connection points to a running process; do not replace it')
            except (ValueError, TypeError):
                raise BridgeError('UNSAFE_RUNTIME', 'Existing connection file is invalid; inspect it before activation')
        self.server = LocalServer(('127.0.0.1', 0), Handler)
        self.server.runtime = self
        connection = {'host': '127.0.0.1', 'port': self.server.server_address[1],
            'token': self.token, 'pid': os.getpid(), 'sessionId': self.session_id,
            'protocolVersion': PROTOCOL_VERSION}
        temporary = self.root / ('.connection-' + self.session_id + '.json')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(connection, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.connection_file)
        except Exception:
            temporary.unlink(missing_ok=True)
            self.server.server_close()
            raise
        self.timer = QTimer(QApplication.instance())
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.pump)
        self.timer.start()
        self.http_thread = threading.Thread(target=self.server.serve_forever,
            kwargs={'poll_interval': 0.2}, name='KritaLocalBridgeHTTP', daemon=True)
        self.http_thread.start()
        QApplication.instance().aboutToQuit.connect(self.stop)
        atexit.register(self.stop)
        return self.public_status()

    def public_status(self):
        return {'status': 'ready' if not self.closed else 'stopped',
            'sessionId': self.session_id, 'protocolVersion': PROTOCOL_VERSION,
            'host': '127.0.0.1', 'port': self.server.server_address[1],
            'connectionFile': str(self.connection_file)}

    def stop(self):
        if self.closed:
            return
        self.closed = True
        if self.timer:
            self.timer.stop()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        try:
            if not self.connection_file.is_symlink():
                current = json.loads(self.connection_file.read_text(encoding='utf-8'))
                if current.get('sessionId') == self.session_id:
                    self.connection_file.unlink()
        except (OSError, ValueError):
            pass

    def pump(self):
        # QTimer runs on the application thread, one job at a time. No worker
        # performs a Krita call, creates a QImage, or accesses a Document.
        try:
            job = self.jobs.get_nowait()
        except queue.Empty:
            return
        with job.lock:
            if job.cancelled:
                job.done.set()
                self.jobs.task_done()
                return
            job.started = True
        try:
            result = getattr(self, 'op_' + job.op)(job.args)
            job.response = {'ok': True, 'result': result}
        except BridgeError as error:
            job.response = {'ok': False, 'error': {'code': error.code, 'message': str(error)}}
        except Exception as error:
            # No stack, source bytes, Authorization or token is returned.
            job.response = {'ok': False, 'error': {'code': 'KRITA_ERROR',
                'message': type(error).__name__ + ': ' + str(error).replace(self.token, '[redacted]')}}
        finally:
            job.done.set()
            self.jobs.task_done()

    def document(self, value):
        doc_id = identifier(value)
        require(doc_id in self.documents, 'Unknown bridge document ID', 'DOCUMENT_NOT_FOUND')
        record = self.documents[doc_id]
        doc = record['document']
        require(any(doc == opened for opened in self.app.documents()),
                'This bridge document has been closed', 'DOCUMENT_CLOSED')
        dimensions(doc.width(), doc.height())
        return doc, record

    def open_document_batch(self, path):
        # Krita copies the application batch mode while opening a document.
        # Setting only Document.batchmode afterwards cannot suppress import UI.
        old_batch = self.app.batchmode()
        try:
            self.app.setBatchmode(True)
            return self.app.openDocument(str(path))
        finally:
            self.app.setBatchmode(old_batch)

    def node(self, doc, value):
        uuid_value = identifier(value)
        node = doc.nodeByUniqueID(QUuid(uuid_value))
        require(node is not None and node != doc.rootNode(), 'Node is not in this document', 'NODE_NOT_FOUND')
        return node

    def node_state(self, node, depth=0):
        require(depth <= 32, 'Layer nesting exceeds supported depth', 'DOCUMENT_TOO_COMPLEX')
        bounds = node.bounds()
        return {'nodeId': node_id(node), 'name': node.name(), 'type': node.type(),
            'visible': node.visible(), 'opacity': node.opacity(), 'locked': node.locked(),
            'bounds': {'x': bounds.x(), 'y': bounds.y(), 'width': bounds.width(), 'height': bounds.height()},
            'children': [self.node_state(child, depth + 1) for child in node.childNodes()]}

    def doc_state(self, doc_id, doc, record):
        root = doc.rootNode()
        return {'documentId': doc_id, 'name': doc.name(), 'path': doc.fileName(),
            'width': doc.width(), 'height': doc.height(), 'modified': doc.modified(),
            'colorModel': doc.colorModel(), 'colorDepth': doc.colorDepth(), 'colorProfile': doc.colorProfile(),
            'sourcePath': record['sourcePath'], 'sourceSha256': record['sourceSha256'],
            'layers': [self.node_state(child) for child in root.childNodes()]}

    def op_state(self, args):
        keys(args)
        documents = []
        for doc_id in list(self.documents):
            try:
                doc, record = self.document(doc_id)
                documents.append(self.doc_state(doc_id, doc, record))
            except BridgeError as error:
                if error.code != 'DOCUMENT_CLOSED':
                    raise
        available = self.app.filters()
        filters = []
        for filter_id, schema in FILTER_SCHEMAS.items():
            defaults = {}
            if filter_id in available:
                # InfoObject belongs to the Filter wrapper. A chained call can
                # destroy that temporary owner before properties() executes.
                filter_object = self.app.filter(filter_id)
                configuration = filter_object.configuration()
                defaults = configuration.properties()
            filters.append({'id': filter_id, 'support': 'native-filter-mask' if filter_id in available else 'unavailable',
                'allowedProperties': schema, 'defaultProperties': {key: scalar(value) for key, value in defaults.items()}})
        return {**self.public_status(), 'kritaVersion': self.app.version(), 'documents': documents,
            'capabilities': {'operations': list(OPERATIONS), 'filters': filters,
                'onlyBridgeCopies': True, 'preservesCoordinates': True,
                'imageSizeLimit': {'maxSide': 12000, 'maxPixels': 24000000},
                'layerReorder': 'same-parent-only', 'outputPolicy': 'new-path-only',
                'regionOperations': {'pixelFormat': 'BGRA/U8', 'coordinateSpace': 'canvas',
                    'resampling': False, 'layerReadMode': 'raw-paintlayer',
                    'projectionReadMode': 'current-document-projection', 'profilePolicy': 'exact-match'},
                'patchPreparation': {'explicitOperation': 'prepare_patch', 'sameAspectRatioOnly': True,
                    'interpolation': 'Qt.SmoothTransformation', 'outputFormat': 'PNG RGBA/U8'},
                'noArbitraryPython': True, 'geometryGuarantee': False}}

    def op_open_copy(self, args):
        keys(args, required=('path',))
        source = no_symlink_path(args['path'], suffixes={'.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.kra'})
        source_sha = digest(source)
        copied = self.copy_dir / (str(uuid.uuid4()) + source.suffix.lower())
        with open(source, 'rb') as src, open(copied, 'xb') as dst:
            shutil.copyfileobj(src, dst)
        require(digest(copied) == source_sha, 'Source changed while being copied', 'SOURCE_CHANGED')
        doc = self.open_document_batch(copied)
        require(doc is not None, 'Krita could not open the copy', 'OPEN_FAILED')
        try:
            doc.setBatchmode(True)
            doc.setAutosave(False)
            doc.waitForDone()
            dimensions(doc.width(), doc.height())
            doc.setFileName('')
            doc.setName(source.stem + ' — bridge copy')
            window = self.app.activeWindow()
            # kritarunner can host the same API without a GUI window. Documents
            # opened by Krita.openDocument remain registered in that process.
            if window is not None:
                window.addView(doc)
            doc_id = str(uuid.uuid4())
            record = {'document': doc, 'sourcePath': str(source), 'sourceSha256': source_sha,
                      'copyPath': str(copied)}
            self.documents[doc_id] = record
            return self.doc_state(doc_id, doc, record)
        except Exception:
            doc.close()
            raise

    def op_import_png(self, args):
        keys(args, required=('documentId', 'path', 'name'), optional=('parentId',))
        doc, record = self.document(args['documentId'])
        source = no_symlink_path(args['path'], suffixes={'.png'})
        name = label(args['name'])
        parent = self.node(doc, args['parentId']) if args.get('parentId') else doc.rootNode()
        require(parent == doc.rootNode() or parent.type() == 'grouplayer', 'PNG layers require a group parent')
        require((doc.colorModel(), doc.colorDepth()) == ('RGBA', 'U8'), 'PNG import requires an RGBA/U8 document', 'UNSUPPORTED_COLORSPACE')
        temporary = self.open_document_batch(source)
        require(temporary is not None, 'Krita could not decode PNG', 'OPEN_FAILED')
        try:
            temporary.setBatchmode(True)
            temporary.setAutosave(False)
            refresh(temporary)
            require((temporary.width(), temporary.height()) == (doc.width(), doc.height()), 'PNG must match canvas dimensions; no automatic resize', 'SIZE_MISMATCH')
            require((temporary.colorModel(), temporary.colorDepth(), temporary.colorProfile()) ==
                    (doc.colorModel(), doc.colorDepth(), doc.colorProfile()),
                    'PNG color model/depth/profile differs; no implicit conversion', 'COLORSPACE_MISMATCH')
            pixels = temporary.pixelData(0, 0, doc.width(), doc.height())
            require(len(pixels) == doc.width() * doc.height() * 4, 'Unexpected pixel byte count', 'PIXEL_IO_ERROR')
            node = doc.createNode(name, 'paintlayer')
            require(node is not None, 'Could not create layer', 'LAYER_CREATE_FAILED')
            require(node.setPixelData(pixels, 0, 0, doc.width(), doc.height()), 'Could not populate layer', 'PIXEL_IO_ERROR')
            require(parent.addChildNode(node, None), 'Could not attach layer', 'LAYER_CREATE_FAILED')
            refresh(doc)
            return {'documentId': args['documentId'], 'nodeId': node_id(node), 'layer': self.node_state(node),
                    'sourcePath': str(source), 'sourceSha256': digest(source)}
        finally:
            temporary.close()

    def pixel_format(self, obj):
        require((obj.colorModel(), obj.colorDepth()) == ('RGBA', 'U8'),
                'Region operations require RGBA/U8 pixels', 'UNSUPPORTED_COLORSPACE')
        return obj.colorProfile()

    def op_prepare_patch(self, args):
        keys(args, required=('path', 'width', 'height'))
        source = no_symlink_path(args['path'], suffixes={'.png'})
        width, height = png_dimensions(source)
        scale = uniform_dimensions(width, height, args['width'], args['height'])
        source_sha = digest(source)
        image = QImage(str(source))
        require(not image.isNull() and (image.width(), image.height()) == (width, height),
                'Qt could not decode patch PNG at its declared dimensions', 'INVALID_PNG')
        color_space = image.colorSpace()
        profile_sha = hashlib.sha256(bytes(color_space.iccProfile())).hexdigest() if color_space.isValid() else None
        # ARGB32 is straight-alpha native BGRA/U8 on this little-endian macOS.
        # Explicit patch preparation may resample; import_patch never does.
        image = image.convertToFormat(QImage.Format_ARGB32)
        resampled = (width, height) != (args['width'], args['height'])
        output = image.scaled(args['width'], args['height'], Qt.KeepAspectRatio,
                              Qt.SmoothTransformation) if resampled else image.copy()
        require((output.width(), output.height()) == (args['width'], args['height']),
                'Uniform resample did not produce the requested dimensions', 'SIZE_MISMATCH')
        output = output.convertToFormat(QImage.Format_ARGB32)
        if color_space.isValid():
            output.setColorSpace(color_space)
        target = self.preview_dir / ('prepared-patch-' + str(uuid.uuid4()) + '.png')
        temporary = self.preview_dir / ('.prepared-patch-' + str(uuid.uuid4()) + '.png')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            require(output.save(str(temporary), 'PNG'), 'Qt did not write the prepared patch', 'EXPORT_FAILED')
            check = QImage(str(temporary))
            require(not check.isNull() and (check.width(), check.height()) == (args['width'], args['height']),
                    'Prepared PNG could not be reopened at the expected size', 'EXPORT_FAILED')
            require(check.hasAlphaChannel(), 'Prepared PNG lost its alpha channel', 'EXPORT_FAILED')
            if color_space.isValid():
                require(check.colorSpace() == color_space, 'Prepared PNG color profile differs', 'COLORSPACE_MISMATCH')
            require(digest(source) == source_sha, 'Patch source changed while being read', 'SOURCE_CHANGED')
            no_symlink_path(str(target), must_exist=False, suffixes={'.png'})
            os.link(temporary, target)
            return {'path': str(target), 'sha256': digest(target), 'bytes': target.stat().st_size,
                    'width': args['width'], 'height': args['height'],
                    'input': {'path': str(source), 'sha256': source_sha, 'width': width, 'height': height},
                    'resampled': resampled, 'scale': scale, 'pixelFormat': 'BGRA/U8',
                    # QColorSpace.description() is Qt6-only; this worker is Qt5.
                    'colorProfile': 'icc-sha256:' + profile_sha if profile_sha else None}
        finally:
            temporary.unlink(missing_ok=True)

    def read_pixels(self, obj, rect):
        pixels = obj.pixelData(rect['x'], rect['y'], rect['width'], rect['height'])
        require(len(pixels) == rect['width'] * rect['height'] * 4,
                'Unexpected pixel byte count', 'PIXEL_IO_ERROR')
        return pixels

    def paint_patch(self, doc, parent, name, rect, pixels):
        """New transparent canvas layer; only the supplied rectangle is written."""
        node = doc.createNode(name, 'paintlayer')
        require(node is not None, 'Could not create patch layer', 'LAYER_CREATE_FAILED')
        require(self.pixel_format(node) == self.pixel_format(doc),
                'New layer profile differs; no implicit conversion', 'COLORSPACE_MISMATCH')
        node.setBlendingMode('normal')
        node.setOpacity(255)
        node.setVisible(True)
        require(node.setPixelData(pixels, rect['x'], rect['y'], rect['width'], rect['height']),
                'Could not populate patch layer', 'PIXEL_IO_ERROR')
        require(bytes(self.read_pixels(node, rect)) == bytes(pixels),
                'Patch pixel readback differs', 'PIXEL_IO_ERROR')
        require(parent.addChildNode(node, None), 'Could not attach patch layer', 'LAYER_CREATE_FAILED')
        refresh(doc)
        return node

    def op_import_patch(self, args):
        keys(args, required=('documentId', 'path', 'name', 'x', 'y'), optional=('parentId',))
        doc, record = self.document(args['documentId'])
        profile = self.pixel_format(doc)
        source = no_symlink_path(args['path'], suffixes={'.png'})
        width, height = png_dimensions(source)
        rect = rectangle({'x': args['x'], 'y': args['y'], 'width': width, 'height': height}, doc.width(), doc.height())
        name = label(args['name'])
        parent = self.node(doc, args['parentId']) if 'parentId' in args else doc.rootNode()
        require(parent == doc.rootNode() or parent.type() == 'grouplayer', 'Patch layers require a group parent')
        source_sha = digest(source)
        temporary = self.open_document_batch(source)
        require(temporary is not None, 'Krita could not decode patch PNG', 'OPEN_FAILED')
        try:
            temporary.setBatchmode(True)
            temporary.setAutosave(False)
            refresh(temporary)
            require((temporary.width(), temporary.height()) == (width, height),
                    'Decoded patch size differs from PNG header', 'SIZE_MISMATCH')
            require(self.pixel_format(temporary) == profile,
                    'Patch color profile differs; no implicit conversion', 'COLORSPACE_MISMATCH')
            pixels = self.read_pixels(temporary, {'x': 0, 'y': 0, 'width': width, 'height': height})
            require(digest(source) == source_sha, 'Patch source changed while being read', 'SOURCE_CHANGED')
            proof = {'path': str(source), 'sha256': source_sha, 'width': width, 'height': height,
                'colorModel': 'RGBA', 'colorDepth': 'U8', 'colorProfile': profile,
                'nativePixelSha256': hashlib.sha256(bytes(pixels)).hexdigest(), 'pixelFormat': 'BGRA/U8'}
        finally:
            temporary.close()
        node = self.paint_patch(doc, parent, name, rect, pixels)
        return {'documentId': args['documentId'], 'nodeId': node_id(node), 'layer': self.node_state(node),
                'rect': rect, 'canvas': {'width': doc.width(), 'height': doc.height()}, 'input': proof}

    def op_clone_patch(self, args):
        keys(args, required=('documentId', 'nodeId', 'rect', 'destination', 'name'))
        doc, record = self.document(args['documentId'])
        profile = self.pixel_format(doc)
        source = self.node(doc, args['nodeId'])
        require(source.type() == 'paintlayer', 'clone_patch reads only raw paint layers', 'UNSUPPORTED_NODE')
        require(self.pixel_format(source) == profile,
                'Source layer profile differs; no implicit conversion', 'COLORSPACE_MISMATCH')
        source_rect = rectangle(args['rect'], doc.width(), doc.height())
        keys(args['destination'], required=('x', 'y'))
        rect = rectangle({**args['destination'], 'width': source_rect['width'], 'height': source_rect['height']},
                         doc.width(), doc.height())
        name = label(args['name'])
        parent = source.parentNode()
        require(parent is not None and (parent == doc.rootNode() or parent.type() == 'grouplayer'),
                'Source layer must have a group parent', 'UNSUPPORTED_NODE')
        refresh(doc)
        pixels = self.read_pixels(source, source_rect)
        node = self.paint_patch(doc, parent, name, rect, pixels)
        require(bytes(self.read_pixels(source, source_rect)) == bytes(pixels),
                'Source pixels changed during clone', 'SOURCE_CHANGED')
        return {'documentId': args['documentId'], 'nodeId': node_id(node), 'sourceNodeId': node_id(source),
                'layer': self.node_state(node), 'sourceRect': source_rect, 'rect': rect,
                'canvas': {'width': doc.width(), 'height': doc.height()},
                'nativePixelSha256': hashlib.sha256(bytes(pixels)).hexdigest(), 'pixelFormat': 'BGRA/U8'}

    def op_export_region(self, args):
        keys(args, required=('documentId', 'rect'), optional=('nodeId',))
        doc, record = self.document(args['documentId'])
        profile = self.pixel_format(doc)
        rect = rectangle(args['rect'], doc.width(), doc.height())
        source = self.node(doc, args['nodeId']) if 'nodeId' in args else doc
        if 'nodeId' in args:
            require(source.type() == 'paintlayer', 'export_region reads only raw paint layers', 'UNSUPPORTED_NODE')
            require(self.pixel_format(source) == profile,
                    'Source layer profile differs; no implicit conversion', 'COLORSPACE_MISMATCH')
        refresh(doc)
        pixels = self.read_pixels(source, rect)
        temporary = self.app.createDocument(rect['width'], rect['height'], 'Bridge region export',
                                            'RGBA', 'U8', profile, 72.0)
        require(temporary is not None, 'Could not create export document', 'DOCUMENT_CREATE_FAILED')
        try:
            temporary.setBatchmode(True)
            temporary.setAutosave(False)
            require(self.pixel_format(temporary) == profile,
                    'Export document profile differs', 'COLORSPACE_MISMATCH')
            root = temporary.rootNode()
            # createDocument initially adds a background; remove it only in this
            # disposable document so transparent patch pixels stay transparent.
            for child in root.childNodes():
                require(root.removeChildNode(child), 'Could not clear export background', 'LAYER_CREATE_FAILED')
            local_rect = {'x': 0, 'y': 0, 'width': rect['width'], 'height': rect['height']}
            self.paint_patch(temporary, root, 'Region pixels', local_rect, pixels)
            target = self.preview_dir / ('region-' + str(uuid.uuid4()) + '.png')
            output = self.write_output(temporary, target, 'png', force_srgb=False)
        finally:
            temporary.close()
        return {'documentId': args['documentId'], 'nodeId': args.get('nodeId'), 'rect': rect,
                'canvas': {'width': doc.width(), 'height': doc.height()}, **output,
                'nativePixelSha256': hashlib.sha256(bytes(pixels)).hexdigest(), 'pixelFormat': 'BGRA/U8',
                'colorProfile': profile,
                'readMode': 'raw-paintlayer' if 'nodeId' in args else 'current-document-projection'}

    def mask_bytes(self, args, width, height):
        require(('polygon' in args) != ('grayPngPath' in args), 'Supply exactly one polygon or grayPngPath')
        if 'grayPngPath' in args:
            require('holes' not in args, 'holes is supported only with polygon')
            source = no_symlink_path(args['grayPngPath'], suffixes={'.png'})
            image = QImage(str(source))
            require(not image.isNull() and (image.width(), image.height()) == (width, height),
                    'Mask PNG must match canvas dimensions', 'SIZE_MISMATCH')
            require(image.allGray(), 'grayPngPath must contain a grayscale mask', 'INVALID_MASK')
            rgba = image.convertToFormat(QImage.Format_RGBA8888)
            ptr = rgba.constBits()
            ptr.setsize(rgba.bytesPerLine() * height)
            raw = bytes(ptr)
            require(all(raw[y * rgba.bytesPerLine() + x * 4 + 3] == 255 for y in range(height) for x in range(width)),
                    'Gray mask PNG must be opaque; white shows, black hides', 'INVALID_MASK')
        else:
            points = polygon(args['polygon'], width, height)
            holes = args.get('holes', [])
            require(isinstance(holes, list) and len(holes) <= 128, 'Too many polygon holes')
            holes = [polygon(hole, width, height) for hole in holes]
            image = QImage(width, height, QImage.Format_ARGB32)
            image.fill(QColor(0, 0, 0, 255))
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 255))
            painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
            painter.setBrush(QColor(0, 0, 0, 255))
            for hole in holes:
                painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in hole]))
            painter.end()
        gray = image.convertToFormat(QImage.Format_Grayscale8)
        ptr = gray.constBits()
        ptr.setsize(gray.bytesPerLine() * height)
        raw = bytes(ptr)
        values = b''.join(raw[y * gray.bytesPerLine():y * gray.bytesPerLine() + width] for y in range(height))
        if boolean(args.get('invert', False)):
            values = bytes(255 - v for v in values)
        return values

    def op_mask_set(self, args):
        keys(args, required=('documentId', 'nodeId'), optional=('name', 'polygon', 'holes', 'grayPngPath', 'invert', 'replaceMaskId'))
        doc, record = self.document(args['documentId'])
        parent = self.node(doc, args['nodeId'])
        require(parent.type() in ('paintlayer', 'grouplayer'), 'Transparency mask requires a paint or group layer')
        name = label(args.get('name'), 'Editable transparency mask')
        values = self.mask_bytes(args, doc.width(), doc.height())
        selection = Selection()
        selection.setPixelData(QByteArray(values), 0, 0, doc.width(), doc.height())
        require(bytes(selection.pixelData(0, 0, doc.width(), doc.height())) == values, 'Mask selection write failed', 'PIXEL_IO_ERROR')
        replacement = args.get('replaceMaskId')
        if replacement:
            mask = self.node(doc, replacement)
            require(mask.type() == 'transparencymask' and mask.parentNode() == parent,
                    'replaceMaskId must be a transparency mask of the stated layer')
            mask.setSelection(selection)
            mask.setName(name)
        else:
            # Tested remedy: generic createNode lacks initialized selection.
            mask = doc.createTransparencyMask(name)
            require(mask is not None, 'Could not create typed mask', 'MASK_CREATE_FAILED')
            mask.setSelection(selection)
            require(parent.addChildNode(mask, None), 'Could not attach mask', 'MASK_CREATE_FAILED')
        refresh(doc)
        attached_selection = mask.selection()
        require(bytes(attached_selection.pixelData(0, 0, doc.width(), doc.height())) == values, 'Attached mask readback differs', 'PIXEL_IO_ERROR')
        return {'documentId': args['documentId'], 'nodeId': node_id(mask), 'parentId': node_id(parent),
                'maskSha256': hashlib.sha256(values).hexdigest(), 'selectedPixels': sum(value > 0 for value in values),
                'layer': self.node_state(mask)}

    def op_filter_add(self, args):
        keys(args, required=('documentId', 'nodeId', 'name', 'filterId', 'properties'), optional=('selectionGrayPngPath',))
        doc, record = self.document(args['documentId'])
        parent = self.node(doc, args['nodeId'])
        require(parent.type() in ('paintlayer', 'grouplayer'), 'Filter mask requires a paint or group layer')
        name = label(args['name'])
        filter_id = args['filterId']
        properties = filter_properties(filter_id, args['properties'])
        require(filter_id in self.app.filters(), 'Filter is unavailable in this Krita build', 'UNSUPPORTED_FILTER')
        filter_object = self.app.filter(filter_id)
        configuration = filter_object.configuration()
        for key, value in properties.items():
            configuration.setProperty(key, value)
        filter_object.setConfiguration(configuration)
        selection = Selection()
        if args.get('selectionGrayPngPath'):
            values = self.mask_bytes({'grayPngPath': args['selectionGrayPngPath']}, doc.width(), doc.height())
            selection.setPixelData(QByteArray(values), 0, 0, doc.width(), doc.height())
            require(bytes(selection.pixelData(0, 0, doc.width(), doc.height())) == values,
                    'Filter selection write failed', 'PIXEL_IO_ERROR')
        else:
            selection.select(0, 0, doc.width(), doc.height(), 255)
        mask = doc.createFilterMask(name, filter_object, selection)
        require(mask is not None and parent.addChildNode(mask, None), 'Could not attach filter mask', 'FILTER_CREATE_FAILED')
        refresh(doc)
        return {'documentId': args['documentId'], 'nodeId': node_id(mask), 'parentId': node_id(parent),
                'filterId': filter_id, 'properties': properties,
                'selectionGrayPngPath': args.get('selectionGrayPngPath'), 'layer': self.node_state(mask)}

    def op_layer_set_visibility(self, args):
        keys(args, required=('documentId', 'nodeId', 'visible'))
        doc, record = self.document(args['documentId'])
        node = self.node(doc, args['nodeId'])
        node.setVisible(boolean(args['visible']))
        refresh(doc)
        return {'documentId': args['documentId'], 'nodeId': node_id(node), 'visible': node.visible()}

    def op_layer_reorder(self, args):
        keys(args, required=('documentId', 'nodeId', 'aboveId'))
        doc, record = self.document(args['documentId'])
        node = self.node(doc, args['nodeId'])
        parent = node.parentNode()
        siblings = parent.childNodes()
        above = self.node(doc, args['aboveId']) if args['aboveId'] is not None else None
        require(above != node, 'A layer cannot be ordered above itself')
        require(above is None or above.parentNode() == parent, 'Reordering across parents is not supported')
        ordered = [item for item in siblings if item != node]
        insertion = 0 if above is None else next(i for i, item in enumerate(ordered) if item == above) + 1
        ordered.insert(insertion, node)
        # Typed API preserves the same nodes and pixels; no cut/paste or raster move.
        parent.setChildNodes(ordered)
        refresh(doc)
        expected = [node_id(item) for item in ordered]
        actual = [node_id(item) for item in parent.childNodes()]
        require(actual == expected, 'Layer order readback differs', 'LAYER_ORDER_FAILED')
        return {'documentId': args['documentId'], 'nodeId': node_id(node), 'siblingOrderBottomToTop': actual}

    def write_output(self, doc, target, kind, force_srgb=True):
        target = no_symlink_path(str(target), must_exist=False, suffixes={'.kra' if kind == 'kra' else '.png'})
        temporary = target.parent / ('.krita-bridge-' + str(uuid.uuid4()) + target.suffix)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        old_name = doc.fileName()
        old_batch = doc.batchmode()
        old_modified = doc.modified()
        try:
            doc.setBatchmode(True)
            refresh(doc)
            if kind == 'kra':
                result = doc.saveAs(str(temporary))
            else:
                config = InfoObject()
                for key, value in {'alpha': True, 'compression': 6, 'forceSRGB': force_srgb,
                        'indexed': False, 'interlaced': False, 'saveSRGBProfile': True}.items():
                    config.setProperty(key, value)
                result = doc.exportImage(str(temporary), config)
            doc.waitForDone()
            require(result and temporary.is_file() and temporary.stat().st_size > 0, 'Krita did not write the output', 'EXPORT_FAILED')
            if kind == 'png':
                check = QImage(str(temporary))
                require(not check.isNull() and (check.width(), check.height()) == (doc.width(), doc.height()),
                        'Exported PNG could not be reopened at the expected size', 'EXPORT_FAILED')
            # Atomic exclusive publication: link fails if someone created target.
            # Native Krita never receives a pre-existing user output path.
            no_symlink_path(str(target), must_exist=False)
            os.link(temporary, target)
            temporary.unlink()
            if kind == 'kra':
                doc.setFileName(str(target))
            return {'path': str(target), 'sha256': digest(target), 'bytes': target.stat().st_size,
                    'width': doc.width(), 'height': doc.height()}
        except Exception:
            if kind == 'kra':
                doc.setFileName(old_name)
                doc.setModified(old_modified)
            raise
        finally:
            doc.setBatchmode(old_batch)
            # Remove only our unique staging artifact. Never remove a user file.
            temporary.unlink(missing_ok=True)
            Path(str(temporary) + '~').unlink(missing_ok=True)

    def op_save_as(self, args):
        keys(args, required=('documentId', 'path'))
        doc, record = self.document(args['documentId'])
        return {'documentId': args['documentId'], **self.write_output(doc, args['path'], 'kra')}

    def op_export_png(self, args):
        keys(args, required=('documentId', 'path'))
        doc, record = self.document(args['documentId'])
        return {'documentId': args['documentId'], **self.write_output(doc, args['path'], 'png')}

    def op_preview(self, args):
        keys(args, required=('documentId',))
        doc, record = self.document(args['documentId'])
        target = self.preview_dir / ('preview-' + str(uuid.uuid4()) + '.png')
        return {'documentId': args['documentId'], **self.write_output(doc, target, 'png')}


def start_bridge():
    global _runtime
    if _runtime is not None and not _runtime.closed:
        return _runtime.public_status()
    runtime = BridgeRuntime()
    status = runtime.start()
    _runtime = runtime
    return status


class BridgeExtension(Extension):
    def setup(self):
        try:
            start_bridge()
        except Exception as error:
            print('Krita Local MCP Bridge did not start:', type(error).__name__, str(error))

    def createActions(self, window):
        pass


def install_extension():
    global _extension
    if _extension is None:
        _extension = BridgeExtension(Krita.instance())
        Krita.instance().addExtension(_extension)
