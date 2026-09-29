"""Decompressor for Bosch "CPRNAV_2" files, as found on Nissan Connect (LCN2kai) SD cards.

A port of the cpr_* classes in /var/opt/bosch/dynamic/processes/DAPIAPP.OUT:

  cpr_tclFileheader::bInterpreteFileStructure  -> FileHeader.parse
  cpr_tclReferenceList                         -> FileHeader.block_offsets
  cpr_tclDecompressAlgorithm::bDecompressData  -> decompress_block
  cpr_tclDecompressAlgorithm::u32GetNextBits   -> BitReader
  cpr_tclCodeTable::vSetStandardTable          -> CODE_TABLES

File layout (little-endian):

  0x00  u16      version, must be 5
  0x02  u16      block size in KiB, at most 64 (16 in every file seen)
  0x04  char[8]  "CPRNAV_2"
  0x0C  u32      unpacked file size
  0x10  u8       mode: 3 = compressed, 1 = stored
  0x11  u8       unknown; kept in cpr_FileEntry but unused here (0 in every file seen)
  0x12  u16      not read by the firmware (1 in every file seen)
  0x14  u32[n+1] file offsets of blocks 0..n-1, then the end of the data

Every block unpacks to one block-size chunk of output and starts with a header
of two u16s (u32s if the block size is 64 KiB):

  +0  offset of the block's literal bytes, from the block start
  +1  number of zero bytes after the decoded data that fill out the chunk

The bitstream follows the header and ends where the literal bytes begin. It is
read LSB-first. A 2-bit index picks one of four code tables, then each token is
a prefix code (at most 6 bits) followed by the extra bits its entry calls for.
"""

from __future__ import annotations

import argparse
import struct
import sys

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import NamedTuple

SIGNATURE = b"CPRNAV_2"
VERSION = 5
HEADER = struct.Struct("<HH8sIBB2x")
PNG_EXTENSIONS = (".PHD", ".PHN", ".PND", ".PNN")


class CprError(ValueError):
    pass


class Mode(IntEnum):
    STORED = 1
    COMPRESSED = 3


class Cmd(IntEnum):
    """cpr_tenCmdType"""

    LITERAL = 1  # copy one byte from the literal area
    LITERALS = 2  # copy `length` bytes from the literal area
    MATCH = 3  # copy `length` bytes from `distance` bytes back in the output


class CodeEntry(NamedTuple):
    """cpr_tclCodeTableEntry, fields in vSetEntry argument order.

    The in-memory struct is NOT in argument order, and has a gap in the middle:

    ```c
    struct cpr_tclCodeTableEntry {  // size 0x14
        u16 code;       // 0x00  size 2
        u16 len_base;   // 0x02  size 2
        u16 len_max;    // 0x04  size 2
        u16 dist_base;  // 0x06  size 2
        u16 dist_max;   // 0x08  size 2
        u8  _pad0[2];   // 0x0A  size 2  <-- gap, aligns cmd to 4 bytes
        u32 cmd;        // 0x0C  size 4
        u8  code_bits;  // 0x10  size 1
        u8  len_bits;   // 0x11  size 1
        u8  dist_bits;  // 0x12  size 1
        u8  _pad1;      // 0x13  size 1  rounds size up to a multiple of 4
    };
    ```
    """

    cmd: Cmd
    code: int  # prefix code, LSB-first
    code_bits: int
    len_bits: int
    len_base: int
    len_max: int
    dist_bits: int
    dist_base: int
    dist_max: int


class CodeTable:
    """cpr_tclCodeTable; cpr_tclCodeTableList holds four, back to back.

    ```c
    struct cpr_tclCodeTable {                   // size 0x2C
        u32                    num_entries;     // 0x00  always 9
        cpr_tclCodeTableEntry *entries;         // 0x04
        u32                    lookup_size;     // 0x08  1 << max code_bits
        u8                    *lookup;          // 0x0C  entry index per bit pattern
        u32                    lookup_mask;     // 0x10  lookup_size - 1
        u32                    max_code_bits;   // 0x14
        u32                    unknown_18;      // 0x18
        u32                    max_dist;        // 0x1C
        u32                    max_len;         // 0x20
        u32                    max_match_len;   // 0x24
        u32                    align;           // 0x28
    };
    ```

    Only `entries`, the lookup fields and `align` are used to decode.
    vSetStandardTable sets 0x14-0x24 too; vInit copies `max_code_bits` into
    cpr_tclDecompressAlgorithm, but bDecompressData, vInterpreteHeader and
    u32GetNextBits never read the copy. Their names are inferred
    from their values: each matches the largest `code_bits`, `dist_max`,
    `len_max` (any entry) and `len_max` (MATCH entries) respectively.
    `unknown_18` is 16 in the 2-byte-aligned tables and 15 in the others.

    `lookup` here holds entries rather than indices: indexed by the next
    `peek_bits` bits, it gives the entry whose prefix code they start with.

    vInit stores `align >> 1` as the shift applied to a match's distance bits,
    so distances are multiples of 2 or 4.
    """

    def __init__(
        self,
        align: int,
        max_code_bits: int,
        unknown_18: int,
        max_dist: int,
        max_len: int,
        max_match_len: int,
        entries: list[tuple[int, ...]],
    ):
        self.align = align
        self.max_code_bits = max_code_bits
        self.unknown_18 = unknown_18
        self.max_dist = max_dist
        self.max_len = max_len
        self.max_match_len = max_match_len
        self.dist_shift = align >> 1
        self.entries = [CodeEntry(Cmd(e[0]), *e[1:]) for e in entries]
        self.peek_bits = max(e.code_bits for e in self.entries)
        self.lookup = [self.entries[0]] * (1 << self.peek_bits)

        for entry in self.entries:
            for i in range(entry.code, len(self.lookup), 1 << entry.code_bits):
                self.lookup[i] = entry


# cpr_tclCodeTable::vSetStandardTable(0..3). All four share the same prefix
# codes and differ in distance granularity (align) and window size. Each
# entry is a CodeEntry, i.e. vSetEntry's arguments: (cmd, code, code_bits,
# len_bits, len_base, len_max, dist_bits, dist_base, dist_max).
CODE_TABLES = [
    CodeTable(
        align=2,
        max_code_bits=6,
        unknown_18=16,
        max_dist=4640,
        max_len=265,
        max_match_len=37,
        entries=[
            (1, 0, 2, 0, 1, 1, 0, 0, 0),
            (3, 1, 2, 2, 2, 5, 4, 2, 32),
            (3, 2, 3, 2, 2, 5, 11, 546, 4640),
            (3, 3, 3, 2, 2, 5, 8, 34, 544),
            (2, 6, 3, 3, 2, 9, 0, 0, 0),
            (3, 7, 4, 5, 6, 37, 4, 2, 32),
            (3, 15, 5, 5, 6, 37, 8, 34, 544),
            (3, 31, 6, 5, 6, 37, 11, 546, 4640),
            (2, 63, 6, 8, 10, 265, 0, 0, 0),
        ],
    ),
    CodeTable(
        align=4,
        max_code_bits=6,
        unknown_18=15,
        max_dist=4640,
        max_len=265,
        max_match_len=37,
        entries=[
            (1, 0, 2, 0, 1, 1, 0, 0, 0),
            (3, 1, 2, 2, 2, 5, 3, 4, 32),
            (3, 2, 3, 2, 2, 5, 10, 548, 4640),
            (3, 3, 3, 2, 2, 5, 7, 36, 544),
            (2, 6, 3, 3, 2, 9, 0, 0, 0),
            (3, 7, 4, 5, 6, 37, 3, 4, 32),
            (3, 15, 5, 5, 6, 37, 7, 36, 544),
            (3, 31, 6, 5, 6, 37, 10, 548, 4640),
            (2, 63, 6, 8, 10, 265, 0, 0, 0),
        ],
    ),
    CodeTable(
        align=4,
        max_code_bits=6,
        unknown_18=15,
        max_dist=9184,
        max_len=137,
        max_match_len=21,
        entries=[
            (1, 0, 2, 0, 1, 1, 0, 0, 0),
            (3, 1, 2, 2, 2, 5, 4, 4, 64),
            (3, 2, 3, 2, 2, 5, 11, 1092, 9184),
            (3, 3, 3, 2, 2, 5, 8, 68, 1088),
            (2, 6, 3, 3, 2, 9, 0, 0, 0),
            (3, 7, 4, 4, 6, 21, 4, 4, 64),
            (3, 15, 5, 4, 6, 21, 8, 68, 1088),
            (3, 31, 6, 4, 6, 21, 11, 1092, 9184),
            (2, 63, 6, 7, 10, 137, 0, 0, 0),
        ],
    ),
    CodeTable(
        align=2,
        max_code_bits=6,
        unknown_18=16,
        max_dist=2592,
        max_len=265,
        max_match_len=37,
        entries=[
            (1, 0, 2, 0, 1, 1, 0, 0, 0),
            (3, 1, 2, 2, 2, 5, 4, 2, 32),
            (3, 2, 3, 2, 2, 5, 10, 546, 2592),
            (3, 3, 3, 2, 2, 5, 8, 34, 544),
            (2, 6, 3, 3, 2, 9, 0, 0, 0),
            (3, 7, 4, 5, 6, 37, 4, 2, 32),
            (3, 15, 5, 5, 6, 37, 8, 34, 544),
            (3, 31, 6, 5, 6, 37, 10, 546, 2592),
            (2, 63, 6, 8, 10, 265, 0, 0, 0),
        ],
    ),
]


class BitReader:
    """LSB-first bit reader, refilled a u32 at a time.

    u32GetNextBits is the same thing written differently: it returns bits
    left-aligned in a u32, and bDecompressData ORs them into a 32-bit window
    that it only tops up when it needs a token's extra bits.
    """

    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.pos = pos
        self.buf = 0
        self.count = 0

    def peek(self, n: int) -> int:
        while self.count < n:
            word = self.data[self.pos : self.pos + 4]
            self.buf |= int.from_bytes(word, "little") << self.count
            self.pos += 4
            self.count += 32

        return self.buf & ((1 << n) - 1)

    def skip(self, n: int):
        self.buf >>= n
        self.count -= n

    def read(self, n: int) -> int:
        value = self.peek(n)
        self.skip(n)
        return value


def decompress_block(
    data: bytes, start: int, block_size: int, mode: int, limit: int
) -> bytes:
    """Unpack the block at `data[start]` into at most `block_size` bytes.

    `limit` is how much of the file is still to be unpacked. When it is less
    than the block holds, this is the final block, and it is the only one in
    which the firmware lets a token run past the end (it truncates the token).
    Anywhere else, that fails with "CPR Overwrite ...".
    """
    if start & 3:
        raise CprError(f"block at 0x{start:x} is not 4-byte aligned")

    bits = BitReader(data, start)
    field_bits = 32 if block_size >= 0x10000 else 16
    literal_pos = start + bits.read(field_bits)
    end = block_size - bits.read(field_bits)
    last = limit <= end
    end = min(end, limit)

    if mode == Mode.STORED:
        return data[bits.pos : bits.pos + end]

    if mode != Mode.COMPRESSED:
        raise CprError(f"unknown mode {mode}")

    table = CODE_TABLES[bits.read(2)]
    out = bytearray()

    while len(out) < end:
        entry = table.lookup[bits.peek(table.peek_bits)]
        bits.skip(entry.code_bits)

        if entry.cmd == Cmd.LITERAL:
            out.append(data[literal_pos])
            literal_pos += 1
            continue

        length = entry.len_base + bits.read(entry.len_bits)

        if len(out) + length > end:
            if not last:
                raise CprError(
                    f"{entry.cmd.name} of {length} bytes overruns block at 0x{start:x}"
                )
            length = end - len(out)

        if entry.cmd == Cmd.LITERALS:
            out += data[literal_pos : literal_pos + length]
            literal_pos += length
            continue

        distance = entry.dist_base + (bits.read(entry.dist_bits) << table.dist_shift)
        src = len(out) - distance

        if src < 0:
            raise CprError(
                f"match distance {distance} reaches before block at 0x{start:x}"
            )

        if distance >= length:
            out += out[src : src + length]
        else:
            # Overlapping copy: the source runs into bytes this copy writes.
            # The firmware uses memcpy here, which is undefined for overlap;
            # a byte-by-byte copy is what any LZ77 encoder would assume.
            for i in range(length):
                out.append(out[src + i])

    return bytes(out)


@dataclass
class FileHeader:
    block_size: int
    unpacked_size: int
    mode: int
    block_offsets: list[int]  # n + 1 entries; the last marks the end of the data

    @classmethod
    def parse(cls, data: bytes) -> FileHeader:
        if data[4:12] != SIGNATURE or len(data) < HEADER.size:
            raise CprError("not a CPRNAV_2 file")

        version, block_kib, _, size, mode, _ = HEADER.unpack_from(data)

        if version != VERSION or not 0 < block_kib <= 64:
            raise CprError(
                f"unsupported version {version} or block size {block_kib} KiB"
            )

        block_size = block_kib << 10
        num_blocks = -(-size // block_size)
        offsets = struct.unpack_from(f"<{num_blocks + 1}I", data, HEADER.size)

        return cls(block_size, size, mode, list(offsets))


def decompress(
    data: bytes, progress: Callable[[int, int], None] | None = None
) -> bytes:
    """`progress(block, total)` is called before each block, if given."""
    header = FileHeader.parse(data)
    blocks = header.block_offsets[:-1]
    out = bytearray()

    for i, start in enumerate(blocks):
        if progress:
            progress(i + 1, len(blocks))

        remaining = header.unpacked_size - len(out)
        chunk = decompress_block(data, start, header.block_size, header.mode, remaining)

        # Zero-fill to the block size (bDecompressData memsets the tail).
        out += chunk + bytes(min(header.block_size, remaining) - len(chunk))

    return bytes(out)


def is_cpr_file(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(12)[4:] == SIGNATURE


def find_files(paths: list[Path]) -> tuple[list[tuple[Path, Path]], int]:
    """(input file, output path relative to the output directory) for each input,
    and the number of paths that don't exist."""
    found = []
    missing = 0

    for path in paths:
        if path.is_dir():
            found += [
                (f, f.relative_to(path)) for f in sorted(path.rglob("*")) if f.is_file()
            ]
        elif path.is_file():
            found.append((path, Path(path.name)))
        else:
            print(f"File not found: {path}", file=sys.stderr)
            missing += 1

    return found, missing


def progress(src: Path) -> Callable[[int, int], None]:
    """A progress callback that redraws one status line on stderr."""

    def show(block: int, total: int):
        # Redrawing every block is slow for small files, so only redraw on
        # the first and last blocks and every 64th in between.
        if block == 1 or block == total or block % 64 == 0:
            print(
                f"\r{src}: block {block:,}/{total:,}",
                end="",
                file=sys.stderr,
                flush=True,
            )

    return show


def clear_progress():
    if sys.stderr.isatty():
        print("\r\033[K", end="", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Decompress lcn2kai / "CPRNAV_2" files'
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help='files or directories to decompress; directories are searched recursively ("all" means ".")',
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=Path.cwd(),
        help="where to write the output, mirroring any directory structure (default: current directory)",
    )
    args = parser.parse_args(argv)

    paths = [Path(".") if p == Path("all") else p for p in args.paths]
    files, failed = find_files(paths)

    for src, rel in files:
        if not is_cpr_file(src):
            continue

        data = src.read_bytes()

        try:
            out = decompress(data, progress(src) if sys.stderr.isatty() else None)
        except CprError as e:
            clear_progress()
            print(f"{src}: {e}", file=sys.stderr)
            failed += 1
            continue
        except KeyboardInterrupt:
            clear_progress()
            raise

        suffix = ".PNG" if src.suffix.upper() in PNG_EXTENSIONS else ".BIN"
        dest = args.out_dir / rel.with_name(rel.name + suffix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(out)

        clear_progress()
        print(f"{src} ({len(data):,} -> {len(out):,} bytes)")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
