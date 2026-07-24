import gzip
import os
import struct


def _nbt_write_string(buf: bytearray, s: str):
    encoded = s.encode("utf-8")
    buf += struct.pack(">H", len(encoded))
    buf += encoded


def _nbt_read_string(data: bytes, pos: int):
    length = struct.unpack(">H", data[pos:pos + 2])[0]
    pos += 2
    s = data[pos:pos + length].decode("utf-8")
    pos += length
    return s, pos


def read_servers_dat(path):
    servers = []
    if not os.path.isfile(path):
        return servers
    try:
        with open(path, "rb") as f:
            raw = gzip.decompress(f.read())
    except Exception:
        return servers

    pos = 0
    try:
        pos += 1
        _, pos = _nbt_read_string(raw, pos)

        pos += 1
        name, pos = _nbt_read_string(raw, pos)
        if name != "servers":
            return servers

        pos += 1
        count = struct.unpack(">i", raw[pos:pos + 4])[0]
        pos += 4

        for _ in range(count):
            entry = {}
            while True:
                tag = raw[pos]
                pos += 1
                if tag == 0:
                    break
                key, pos = _nbt_read_string(raw, pos)
                if tag == 8:
                    value, pos = _nbt_read_string(raw, pos)
                    entry[key] = value
                elif tag == 1:
                    pos += 1
                elif tag == 4:
                    pos += 8
                else:
                    break
            if entry:
                servers.append(entry)
    except Exception:
        return []
    return servers


def write_servers_dat(path, servers):
    buf = bytearray()
    buf += bytes([10])
    _nbt_write_string(buf, "")

    buf += bytes([9])
    _nbt_write_string(buf, "servers")
    buf += bytes([10])
    buf += struct.pack(">i", len(servers))

    for s in servers:
        buf += bytes([8])
        _nbt_write_string(buf, "ip")
        _nbt_write_string(buf, s["ip"])

        buf += bytes([8])
        _nbt_write_string(buf, "name")
        _nbt_write_string(buf, s["name"])

        buf += bytes([1])
        _nbt_write_string(buf, "acceptTextures")
        buf += struct.pack(">b", 1)

        buf += bytes([0])

    buf += bytes([0])

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(gzip.compress(bytes(buf)))