"""Minimal RAR4 and RAR5 writers for test fixtures: regular files, stored (no compression), no
encryption. RAR archives cannot be made with free tools, so the CBR tests build their own from
the published format notes (https://www.rarlab.com/technote.htm). libarchive reads them."""

import struct
import zlib

RAR4_MAGIC = b"Rar!\x1a\x07\x00"
RAR5_MAGIC = b"Rar!\x1a\x07\x01\x00"


def rar4(files: list[tuple[str, bytes]]) -> bytes:
    def block(head_type: int, flags: int, body: bytes, data: bytes = b"") -> bytes:
        size = 7 + len(body)
        rest = struct.pack("<BHH", head_type, flags, size) + body
        return struct.pack("<H", zlib.crc32(rest) & 0xFFFF) + rest + data

    out = RAR4_MAGIC + block(0x73, 0, b"\0" * 6)  # main header: two reserved fields
    for name, data in files:
        raw = name.encode()
        body = struct.pack(
            "<IIBIIBBHI",
            len(data),  # packed size
            len(data),  # unpacked size
            3,  # host OS: Unix
            zlib.crc32(data),
            0x5A210000,  # DOS date/time (2025-01-01)
            20,  # version needed: 2.0
            0x30,  # method: store
            len(raw),
            0o100644 << 0,  # Unix attributes: regular file
        )
        out += block(0x74, 0x8000, body + raw, data)  # 0x8000: data area follows
    return out + block(0x7B, 0x4000, b"")


def _vint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def rar5(files: list[tuple[str, bytes]]) -> bytes:
    def header(fields: bytes, data: bytes = b"") -> bytes:
        body = _vint(len(fields)) + fields
        return struct.pack("<I", zlib.crc32(body)) + body + data

    out = RAR5_MAGIC + header(_vint(1) + _vint(0) + _vint(0))  # main: type 1, no flags
    for name, data in files:
        raw = name.encode()
        fields = (
            _vint(2)  # type: file
            + _vint(0x0002)  # header flags: data area present
            + _vint(len(data))  # data size
            + _vint(0x0004)  # file flags: CRC32 present
            + _vint(len(data))  # unpacked size
            + _vint(0o100644)  # attributes: Unix mode with the regular-file type
            + struct.pack("<I", zlib.crc32(data))
            + _vint(0)  # compression: version 0, store
            + _vint(1)  # host OS: Unix
            + _vint(len(raw))
            + raw
        )
        out += header(fields, data)
    return out + header(_vint(5) + _vint(0) + _vint(0))  # end of archive
