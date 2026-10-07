"""Protocol/path validation; no Krita import and no image mutations."""
from pathlib import Path
import math
import os
import stat
import struct
import uuid

MAX_PIXELS = 24_000_000
MAX_SIDE = 12000
MAX_FILE_BYTES = 128 * 1024 * 1024


class BridgeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, message, code='INVALID_ARGUMENT'):
    if not condition:
        raise BridgeError(code, message)


def keys(args, required=(), optional=()):
    require(isinstance(args, dict), 'args must be an object')
    require(set(required) <= set(args), 'Missing required argument')
    require(set(args) <= set(required) | set(optional), 'Unknown argument')


def identifier(value):
    require(isinstance(value, str), 'ID must be a UUID string')
    try:
        result = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise BridgeError('INVALID_ARGUMENT', 'ID must be a UUID string')
    require(value == result, 'ID must use canonical lowercase UUID format')
    return result


def label(value, default=None):
    if value is None:
        value = default
    require(isinstance(value, str) and 0 < len(value.strip()) <= 200, 'Name must contain 1..200 characters')
    require(not any(ord(ch) < 32 for ch in value), 'Name contains control characters')
    return value.strip()


def boolean(value):
    require(type(value) is bool, 'Expected a boolean')
    return value


def number(value, low, high):
    require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
            'Number is outside the supported range')
    return value


def dimensions(width, height):
    require(type(width) is int and type(height) is int and 0 < width <= MAX_SIDE
            and 0 < height <= MAX_SIDE and width * height <= MAX_PIXELS,
            'Unsupported image size', 'IMAGE_TOO_LARGE')


def integer(value, low, high):
    require(type(value) is int and low <= value <= high,
            'Expected an integer coordinate within canvas bounds', 'INVALID_RECT')
    return value


def rectangle(value, width, height):
    """Half-open pixel rectangle. No rounding, clipping or coordinate changes."""
    dimensions(width, height)
    keys(value, required=('x', 'y', 'width', 'height'))
    x = integer(value['x'], 0, width - 1)
    y = integer(value['y'], 0, height - 1)
    w = integer(value['width'], 1, width - x)
    h = integer(value['height'], 1, height - y)
    dimensions(w, h)
    return {'x': x, 'y': y, 'width': w, 'height': h}


def png_dimensions(path):
    # Inspect IHDR before invoking Krita's decoder, bounding decoded allocation.
    with open(path, 'rb') as stream:
        header = stream.read(24)
    require(len(header) == 24 and header[:8] == b'\x89PNG\r\n\x1a\n'
            and header[8:16] == b'\x00\x00\x00\rIHDR',
            'Input is not a PNG with a valid IHDR header', 'INVALID_PNG')
    width, height = struct.unpack('>II', header[16:24])
    dimensions(width, height)
    return width, height


def uniform_dimensions(source_width, source_height, width, height):
    dimensions(source_width, source_height)
    dimensions(width, height)
    require(source_width * height == source_height * width,
            'Aspect ratio must match exactly; cropping and warping are not supported', 'ASPECT_MISMATCH')
    return width / source_width


def no_symlink_path(value, must_exist=True, directory=False, suffixes=None):
    """Reject all symlink components, relative paths, traversal and overwrites."""
    require(isinstance(value, str) and len(value) <= 4096 and '\x00' not in value, 'Invalid path')
    raw = Path(value)
    require(raw.is_absolute() and '..' not in raw.parts, 'Path must be absolute without traversal')
    current = Path(raw.anchor)
    for index, component in enumerate(raw.parts[1:]):
        current = current / component
        last = index == len(raw.parts[1:]) - 1
        try:
            st = current.lstat()
        except FileNotFoundError:
            require(last and not must_exist, 'Path or parent does not exist', 'PATH_NOT_FOUND')
            continue
        require(not stat.S_ISLNK(st.st_mode), 'Symlink paths are not allowed', 'SYMLINK_PATH')
        if not last:
            require(stat.S_ISDIR(st.st_mode), 'Path parent is not a directory', 'INVALID_PATH')
        elif must_exist:
            expected = stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)
            require(expected, 'Path has an unsupported file type', 'INVALID_PATH')
        else:
            raise BridgeError('FILE_EXISTS', 'Output already exists; choose a new filename')
    if must_exist and not directory:
        require(raw.stat().st_size <= MAX_FILE_BYTES, 'Input file is too large', 'FILE_TOO_LARGE')
    if suffixes:
        require(raw.suffix.lower() in suffixes, 'Unsupported file extension')
    return raw


def polygon(points, width, height):
    require(isinstance(points, list) and 3 <= len(points) <= 4096, 'Polygon requires 3..4096 points')
    output = []
    for pair in points:
        require(isinstance(pair, list) and len(pair) == 2, 'Polygon point must be [x,y]')
        output.append((number(pair[0], 0, width), number(pair[1], 0, height)))
    return output


FILTER_SCHEMAS = {
    'colorbalance': {**{f'{axis}_{tone}': [-100, 100]
        for axis in ['cyan_red', 'magenta_green', 'yellow_blue']
        for tone in ['shadows', 'midtones', 'highlights']}, 'preserve_luminosity': 'boolean'},
    'hsvadjustment': {'h': [-180, 180], 's': [-100, 100], 'v': [-100, 100],
        'type': [0, 4], 'colorize': 'boolean', 'compatibilityMode': 'boolean'},
    'levels': {'mode': ['lightness'], 'lightness': 'inBlack;inWhite;gamma;outBlack;outWhite'},
}


def filter_properties(filter_id, properties):
    require(filter_id in FILTER_SCHEMAS, 'Filter is not in the allowlist', 'UNSUPPORTED_FILTER')
    require(isinstance(properties, dict) and 0 < len(properties) <= 12, 'Expected filter properties')
    schema = FILTER_SCHEMAS[filter_id]
    require(set(properties) <= set(schema), 'Unsupported filter property')
    for key, value in properties.items():
        rule = schema[key]
        if rule == 'boolean':
            boolean(value)
        elif key == 'mode':
            require(value == 'lightness', 'Only lightness levels mode is supported')
        elif key == 'lightness':
            require(isinstance(value, str) and len(value) < 150, 'Invalid levels curve')
            try:
                values = [float(x) for x in value.split(';')]
            except ValueError:
                raise BridgeError('INVALID_ARGUMENT', 'Invalid levels curve')
            require(len(values) == 5 and all(math.isfinite(x) for x in values), 'Levels curve requires five finite values')
            ib, iw, gamma, ob, ow = values
            require(0 <= ib < iw <= 1 and 0.1 <= gamma <= 10 and 0 <= ob < ow <= 1, 'Invalid levels range')
        else:
            number(value, rule[0], rule[1])
            if key == 'type':
                require(type(value) is int, 'HSV type must be an integer')
    if filter_id == 'levels':
        require(set(properties) == {'mode', 'lightness'}, 'Levels requires mode and lightness')
    return properties
