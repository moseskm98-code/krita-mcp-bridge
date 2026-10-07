"""Pure validation tests. Does not import, start or modify Krita."""
import importlib.util
from pathlib import Path
import tempfile
import struct
import unittest
import uuid

spec = importlib.util.spec_from_file_location('bridge_validation', Path(__file__).parent / 'krita_mcp_bridge/validation.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


class ValidationTests(unittest.TestCase):
    def test_uuid_canonical(self):
        identifier = str(uuid.uuid4())
        self.assertEqual(v.identifier(identifier), identifier)
        with self.assertRaises(v.BridgeError):
            v.identifier('{' + identifier + '}')

    def test_uuid_nonstring_rejected(self):
        with self.assertRaises(v.BridgeError):
            v.identifier(None)

    def test_bool_not_number(self):
        with self.assertRaises(v.BridgeError):
            v.number(True, 0, 100)

    def test_nonfinite_rejected(self):
        for value in [float('nan'), float('inf'), -float('inf')]:
            with self.assertRaises(v.BridgeError):
                v.number(value, -100, 100)

    def test_no_extra_args(self):
        with self.assertRaises(v.BridgeError):
            v.keys({'path': 'x', 'python': 'x'}, required=('path',))

    def test_existing_output_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            # macOS /var may be a symlink; use the real temporary root for tests.
            root = Path(temporary).resolve()
            target = root / 'existing.png'
            target.write_bytes(b'original')
            with self.assertRaises(v.BridgeError) as context:
                v.no_symlink_path(str(target), must_exist=False)
            self.assertEqual(context.exception.code, 'FILE_EXISTS')
            self.assertEqual(target.read_bytes(), b'original')

    def test_missing_output_allowed_existing_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / 'new.png'
            self.assertEqual(v.no_symlink_path(str(target), must_exist=False, suffixes={'.png'}), target)
            self.assertFalse(target.exists())

    def test_symlink_file_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'real.png').write_bytes(b'original')
            (root / 'link.png').symlink_to(root / 'real.png')
            with self.assertRaises(v.BridgeError) as context:
                v.no_symlink_path(str(root / 'link.png'))
            self.assertEqual(context.exception.code, 'SYMLINK_PATH')

    def test_symlink_parent_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'real').mkdir()
            (root / 'link').symlink_to(root / 'real', target_is_directory=True)
            with self.assertRaises(v.BridgeError):
                v.no_symlink_path(str(root / 'link/new.png'), must_exist=False)

    def test_traversal_rejected(self):
        with self.assertRaises(v.BridgeError):
            v.no_symlink_path('/example/../other/file.png')

    def test_unknown_filter_rejected(self):
        with self.assertRaises(v.BridgeError):
            v.filter_properties('arbitrary', {'code': 'x'})

    def test_supported_colorbalance(self):
        values = {'cyan_red_midtones': 12, 'yellow_blue_midtones': -12, 'preserve_luminosity': True}
        self.assertEqual(v.filter_properties('colorbalance', values), values)

    def test_incorrect_colorbalance_keys_rejected(self):
        with self.assertRaises(v.BridgeError):
            v.filter_properties('colorbalance', {'magenta_blue_midtones': 12})

    def test_levels_validated(self):
        value = {'mode': 'lightness', 'lightness': '0;1;1.18;0;1'}
        self.assertEqual(v.filter_properties('levels', value), value)
        for curve in ['0;1;nan;0;1', '1;0;1;0;1', '0;1;0;0;1', '0;1;1;0;1;1']:
            with self.assertRaises(v.BridgeError):
                v.filter_properties('levels', {'mode': 'lightness', 'lightness': curve})

    def test_polygon_bounds(self):
        self.assertEqual(v.polygon([[0, 0], [10, 0], [10, 20]], 10, 20), [(0, 0), (10, 0), (10, 20)])
        with self.assertRaises(v.BridgeError):
            v.polygon([[0, 0], [11, 0], [10, 20]], 10, 20)

    def test_rectangle_exact_edges_and_no_rounding(self):
        rect = {'x': 7, 'y': 9, 'width': 3, 'height': 11}
        self.assertEqual(v.rectangle(rect, 10, 20), rect)
        for field, value in [('x', -1), ('x', 7.0), ('x', True), ('y', 20),
                             ('width', 0), ('width', 4), ('height', 12), ('height', float('nan'))]:
            with self.subTest(field=field, value=value), self.assertRaises(v.BridgeError):
                v.rectangle({**rect, field: value}, 10, 20)

    def test_rectangle_unknown_keys_and_canvas_budget(self):
        with self.assertRaises(v.BridgeError):
            v.rectangle({'x': 0, 'y': 0, 'width': 1, 'height': 1, 'scale': 2}, 10, 20)
        with self.assertRaises(v.BridgeError):
            v.rectangle({'x': 0, 'y': 0, 'width': 1, 'height': 1}, 12000, 12000)

    def test_png_header_checked_before_decoder(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'patch.png'
            prefix = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
            path.write_bytes(prefix + struct.pack('>II', 7, 9))
            self.assertEqual(v.png_dimensions(path), (7, 9))
            for data in [b'not-png', prefix + struct.pack('>II', 0, 9),
                         prefix + struct.pack('>II', 12000, 12000)]:
                path.write_bytes(data)
                with self.subTest(data=data), self.assertRaises(v.BridgeError):
                    v.png_dimensions(path)

    def test_uniform_resample_requires_exact_aspect(self):
        self.assertEqual(v.uniform_dimensions(120, 100, 240, 200), 2)
        self.assertEqual(v.uniform_dimensions(120, 100, 60, 50), 0.5)
        self.assertEqual(v.uniform_dimensions(120, 100, 120, 100), 1)
        for w, h in [(240, 201), (240.0, 200), (True, 1), (0, 0), (12000, 10000)]:
            with self.subTest(width=w, height=h), self.assertRaises(v.BridgeError):
                v.uniform_dimensions(120, 100, w, h)


if __name__ == '__main__':
    unittest.main()
