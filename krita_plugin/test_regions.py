"""Exercise the actual op methods against an in-memory Krita API double.

These prove coordinate/alpha/input policies, not native Krita API compatibility.
Real PNG decoding, native projection and KRA persistence need the runtime smoke.
No Krita process is opened, restarted or contacted by this suite.
"""
import hashlib
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
import uuid


ROOT = Path(__file__).parent / 'krita_mcp_bridge'
package = types.ModuleType('region_test_bridge')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package


class Dummy:
    pass


class FakeUuid:
    def __init__(self, value):
        self.value = value

    def toString(self):
        return self.value


modules = {
    'krita': {'Krita': Dummy, 'Extension': Dummy, 'InfoObject': Dummy, 'Selection': Dummy},
    'PyQt5': {},
    'PyQt5.QtCore': {**{k: Dummy for k in ['QPointF', 'Qt', 'QThread', 'QTimer']},
                      'QByteArray': bytes, 'QUuid': FakeUuid},
    'PyQt5.QtGui': {k: Dummy for k in ['QImage', 'QPainter', 'QPolygonF', 'QColor']},
    'PyQt5.QtWidgets': {'QApplication': Dummy},
}
previous = {name: sys.modules.get(name) for name in modules}
try:
    for name, values in modules.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        sys.modules[name] = module
    for name in ['validation', 'bridge']:
        spec = importlib.util.spec_from_file_location(package.__name__ + '.' + name, ROOT / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    b = sys.modules[package.__name__ + '.bridge']
finally:
    for name, value in previous.items():
        if value is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = value


class PixelObject:
    def colorModel(self): return 'RGBA'
    def colorDepth(self): return 'U8'
    def colorProfile(self): return self.profile

    def pixelData(self, x, y, width, height):
        return b''.join(bytes(self.data[((y + row) * self.w + x) * 4:
                                     ((y + row) * self.w + x + width) * 4]) for row in range(height))

    def setPixelData(self, data, x, y, width, height):
        assert 0 <= x and 0 <= y and x + width <= self.w and y + height <= self.h
        for row in range(height):
            self.data[((y + row) * self.w + x) * 4:((y + row) * self.w + x + width) * 4] = data[row * width * 4:(row + 1) * width * 4]
        return True


class Node(PixelObject):
    def __init__(self, doc, name, kind='paintlayer', fill=0):
        self.doc, self.w, self.h, self.profile = doc, doc.w, doc.h, doc.profile
        self.data = bytearray([fill]) * (self.w * self.h * 4)
        self.id, self.title, self.kind = str(uuid.uuid4()), name, kind
        self.nodes, self.parent = [], None
        self.blend, self.alpha, self.shown = 'multiply', 42, False

    def uniqueId(self): return FakeUuid(self.id)
    def type(self): return self.kind
    def parentNode(self): return self.parent
    def childNodes(self): return list(self.nodes)
    def setBlendingMode(self, value): self.blend = value
    def setOpacity(self, value): self.alpha = value
    def setVisible(self, value): self.shown = value

    def addChildNode(self, node, above):
        node.parent = self
        self.nodes.append(node)
        return True

    def removeChildNode(self, node):
        self.nodes.remove(node)
        node.parent = None
        return True


class Document(PixelObject):
    def __init__(self, width=8, height=6, profile='sRGB test profile'):
        self.w, self.h, self.profile = width, height, profile
        self.data = bytearray(width * height * 4)
        self.root = Node(self, 'root', 'grouplayer')
        self.closed = False
        self.created = 0

    def width(self): return self.w
    def height(self): return self.h
    def rootNode(self): return self.root
    def waitForDone(self): pass
    def refreshProjection(self): pass
    def setBatchmode(self, value): pass
    def setAutosave(self, value): pass
    def close(self): self.closed = True

    def createNode(self, name, kind):
        self.created += 1
        return Node(self, name, kind)

    def nodeByUniqueID(self, uid):
        def walk(node):
            if node.id == uid.value: return node
            for child in node.nodes:
                found = walk(child)
                if found: return found
        return walk(self.root)


class App:
    def __init__(self, doc):
        self.docs, self.created = [doc], []

    def documents(self): return self.docs

    def createDocument(self, width, height, name, model, depth, profile, resolution):
        assert (model, depth) == ('RGBA', 'U8')
        doc = Document(width, height, profile)
        doc.root.addChildNode(Node(doc, 'white background', fill=255), None)
        self.created.append(doc)
        return doc


class RegionOpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        self.doc = Document()
        self.source = Node(self.doc, 'original')
        self.doc.root.addChildNode(self.source, None)
        self.source.data[:] = bytes((i * 7) % 256 for i in range(len(self.source.data)))
        self.doc.data[:] = bytes((i * 11) % 256 for i in range(len(self.doc.data)))
        self.id = str(uuid.uuid4())
        self.runtime = object.__new__(b.BridgeRuntime)
        self.runtime.app = App(self.doc)
        self.runtime.documents = {self.id: {'document': self.doc}}
        self.runtime.preview_dir = self.folder
        self.runtime.node_state = lambda node: {'nodeId': node.id, 'type': node.type()}
        self.decode_count = 0

    def make_patch(self, width=2, height=2, profile=None):
        path = self.folder / 'patch.png'
        path.write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + struct.pack('>II', width, height))
        patch = Document(width, height, profile or self.doc.profile)
        patch.data[:] = (bytes([10, 20, 30, 0, 40, 50, 60, 128, 70, 80, 90, 255, 1, 2, 3, 64]) * (width * height))[:width * height * 4]
        def decode(path):
            self.decode_count += 1
            return patch
        self.runtime.open_document_batch = decode
        return path, patch

    def test_import_preserves_alpha_position_and_source(self):
        path, patch = self.make_patch()
        before = bytes(self.source.data)
        result = self.runtime.op_import_patch({'documentId': self.id, 'path': str(path), 'name': 'patch', 'x': 3, 'y': 2})
        layer = self.doc.root.nodes[-1]
        self.assertEqual(layer.pixelData(3, 2, 2, 2), bytes(patch.data))
        for y in range(self.doc.h):
            for x in range(self.doc.w):
                if not (3 <= x < 5 and 2 <= y < 4): self.assertEqual(layer.pixelData(x, y, 1, 1), bytes(4))
        self.assertEqual(bytes(self.source.data), before)
        self.assertEqual((layer.blend, layer.alpha, layer.shown), ('normal', 255, True))
        self.assertEqual(result['input']['nativePixelSha256'], hashlib.sha256(patch.data).hexdigest())
        self.assertEqual(result['input']['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result['rect'], {'x': 3, 'y': 2, 'width': 2, 'height': 2})
        self.assertTrue(patch.closed)

    def test_import_outside_bounds_rejected_before_decode(self):
        path, patch = self.make_patch()
        with self.assertRaises(b.BridgeError):
            self.runtime.op_import_patch({'documentId': self.id, 'path': str(path), 'name': 'patch', 'x': 7, 'y': 2})
        self.assertEqual(self.decode_count, 0)
        self.assertEqual(self.doc.created, 0)

    def test_import_profile_mismatch_never_creates_layer(self):
        path, patch = self.make_patch(profile='Different ICC')
        with self.assertRaises(b.BridgeError) as error:
            self.runtime.op_import_patch({'documentId': self.id, 'path': str(path), 'name': 'patch', 'x': 0, 'y': 0})
        self.assertEqual(error.exception.code, 'COLORSPACE_MISMATCH')
        self.assertEqual(self.doc.created, 0)
        self.assertTrue(patch.closed)

    def test_clone_overlapping_rect_reads_snapshot_and_keeps_original(self):
        before = bytes(self.source.data)
        expected = self.source.pixelData(1, 1, 3, 2)
        result = self.runtime.op_clone_patch({'documentId': self.id, 'nodeId': self.source.id,
            'rect': {'x': 1, 'y': 1, 'width': 3, 'height': 2}, 'destination': {'x': 2, 'y': 2}, 'name': 'copy'})
        layer = self.doc.root.nodes[-1]
        self.assertEqual(bytes(self.source.data), before)
        self.assertEqual(layer.pixelData(2, 2, 3, 2), expected)
        self.assertEqual(layer.pixelData(0, 0, 1, 1), bytes(4))
        self.assertIs(layer.parent, self.source.parent)
        self.assertNotEqual(result['nodeId'], result['sourceNodeId'])
        self.assertEqual((layer.blend, layer.alpha), ('normal', 255))

    def test_clone_rejects_fractional_destination_and_masks(self):
        args = {'documentId': self.id, 'nodeId': self.source.id,
            'rect': {'x': 0, 'y': 0, 'width': 1, 'height': 1}, 'destination': {'x': 1.5, 'y': 0}, 'name': 'copy'}
        with self.assertRaises(b.BridgeError): self.runtime.op_clone_patch(args)
        self.source.kind = 'transparencymask'
        args['destination']['x'] = 1
        with self.assertRaises(b.BridgeError): self.runtime.op_clone_patch(args)
        self.assertEqual(self.doc.created, 0)

    def test_export_reads_requested_pixels_without_mutating_document(self):
        rect = {'x': 2, 'y': 1, 'width': 3, 'height': 2}
        captured = []
        def write_output(doc, target, kind, force_srgb=True):
            self.assertEqual(len(doc.root.nodes), 1)  # no opaque default background
            captured.append((bytes(doc.root.nodes[0].data), force_srgb, doc.profile))
            return {'path': str(target), 'sha256': 'fake-file-hash', 'bytes': 1, 'width': doc.w, 'height': doc.h}
        self.runtime.write_output = write_output
        before, projection = bytes(self.source.data), bytes(self.doc.data)
        for layer_id, expected in [(None, self.doc.pixelData(2, 1, 3, 2)), (self.source.id, self.source.pixelData(2, 1, 3, 2))]:
            args = {'documentId': self.id, 'rect': rect}
            if layer_id: args['nodeId'] = layer_id
            result = self.runtime.op_export_region(args)
            self.assertEqual(captured[-1], (expected, False, self.doc.profile))
            self.assertEqual(result['rect'], rect)
            self.assertEqual(result['canvas'], {'width': 8, 'height': 6})
            self.assertEqual((result['width'], result['height']), (3, 2))
            self.assertTrue(self.runtime.app.created[-1].closed)
        self.assertEqual(bytes(self.source.data), before)
        self.assertEqual(bytes(self.doc.data), projection)
        self.assertEqual(len(self.doc.root.nodes), 1)

    def test_export_failure_closes_disposable_document(self):
        def failure(*args, **kwargs): raise RuntimeError('export failed')
        self.runtime.write_output = failure
        with self.assertRaises(RuntimeError):
            self.runtime.op_export_region({'documentId': self.id, 'rect': {'x': 0, 'y': 0, 'width': 2, 'height': 2}})
        self.assertTrue(self.runtime.app.created[-1].closed)
        self.assertEqual(len(self.doc.root.nodes), 1)

    def test_unowned_document_rejected_before_reading(self):
        with self.assertRaises(b.BridgeError) as error:
            self.runtime.op_export_region({'documentId': str(uuid.uuid4()), 'rect': {'x': 0, 'y': 0, 'width': 2, 'height': 2}})
        self.assertEqual(error.exception.code, 'DOCUMENT_NOT_FOUND')
        self.assertFalse(self.runtime.app.created)


if __name__ == '__main__':
    unittest.main()
