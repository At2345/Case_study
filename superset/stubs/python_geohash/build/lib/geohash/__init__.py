"""Pure-Python `python-geohash` (subset) so Apache Superset installs on Windows without a C compiler.

Superset 4.1.1 only imports `geohash` in `superset/examples/long_lat.py` (an optional example loader)
and uses `geohash.encode`. The workbench never uses geohash charts, so this package implements the
standard geohash algorithm instead of the C extension.
"""
__version__ = "0.9.2"

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_BITS = [16, 8, 4, 2, 1]


def encode(latitude, longitude, precision=12):
    """Encode a lat/lon pair to a geohash string."""
    lat_interval = [-90.0, 90.0]
    lon_interval = [-180.0, 180.0]
    chars, bit, ch, even = [], 0, 0, True
    while len(chars) < precision:
        if even:
            mid = (lon_interval[0] + lon_interval[1]) / 2
            if longitude > mid:
                ch |= _BITS[bit]
                lon_interval[0] = mid
            else:
                lon_interval[1] = mid
        else:
            mid = (lat_interval[0] + lat_interval[1]) / 2
            if latitude > mid:
                ch |= _BITS[bit]
                lat_interval[0] = mid
            else:
                lat_interval[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            chars.append(_BASE32[ch])
            bit, ch = 0, 0
    return "".join(chars)


def decode(geohash):
    """Decode a geohash string to (latitude, longitude, lat_error, lon_error)."""
    lat_interval = [-90.0, 90.0]
    lon_interval = [-180.0, 180.0]
    even = True
    for char in geohash:
        idx = _BASE32.index(char)
        for mask in _BITS:
            if even:
                mid = (lon_interval[0] + lon_interval[1]) / 2
                if idx & mask:
                    lon_interval[0] = mid
                else:
                    lon_interval[1] = mid
            else:
                mid = (lat_interval[0] + lat_interval[1]) / 2
                if idx & mask:
                    lat_interval[0] = mid
                else:
                    lat_interval[1] = mid
            even = not even
    lat = (lat_interval[0] + lat_interval[1]) / 2
    lon = (lon_interval[0] + lon_interval[1]) / 2
    return lat, lon, (lat_interval[1] - lat_interval[0]) / 2, (lon_interval[1] - lon_interval[0]) / 2


def bbox(geohash):
    """Bounding box of a geohash as (lat_min, lon_min, lat_max, lon_max)."""
    lat, lon, lat_err, lon_err = decode(geohash)
    return lat - lat_err, lon - lon_err, lat + lat_err, lon + lon_err
