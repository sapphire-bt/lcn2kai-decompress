# lcn2kai-decompress

Decompression/unpacking routine for files included on Nissan Connect 3 SD cards. Tested with "Europe V5" (pictured below).

You may also wish to check out the head unit root exploit by [ea](https://github.com/ea) at [github.com/ea/bosch_headunit_root/](https://github.com/ea/bosch_headunit_root/).

## How to Use

Requires Python 3.10+, no dependencies.

Decompress one or more files:

```
DecompressAlgorithm.py <filepath> [<filepath> ...]
```

Decompress every file in a directory and its subdirectories (`all` is shorthand for the current directory):

```
DecompressAlgorithm.py <directory>
DecompressAlgorithm.py all
```

Output goes to the current directory, or to `-o <directory>`, mirroring the structure of any input directory. Files are saved as `<filename>.BIN`, or `<filename>.PNG` if the extension is one of `.PHD`, `.PHN`, `.PND`, `.PNN`. Files without the `CPRNAV_2` signature are skipped.

## Background

If you have a Nissan made within the last 10 or so years, chances are you have a head unit that looks something like this:

![connect-3](https://user-images.githubusercontent.com/33162278/118888942-4723ed80-b8f4-11eb-9d92-c9cc1ca157e1.png)

Here in Europe, the SD card slot next to the CD reader contains one of these:

![Nissan Connect 3 Europe V5 SD card](https://user-images.githubusercontent.com/33162278/116246634-12dc6780-a762-11eb-8bb7-04a77ce8f2cc.jpg)

Taking a cursory look at the files on the SD card, you will realise most of them are unreadable (although there are a few plaintext and SQLite DB files).

If you're able to find a copy of the head unit's firmware online (hint: [Nissan QashQai Forums](https://www.qashqaiforums.co.uk/)) then it's fairly straightforward to find the binary responsible for decompression (another hint: do a `grep` for `"CPRNAV_2"`, the file signature, and you find one result: `/var/opt/bosch/dynamic/processes/DAPIAPP.OUT`). Fortunately the symbols haven't been stripped so it's not too hard to get a basic understanding of the methods/structs/etc. used.

## Compression Format

This is a description of what `cpr_tclFileheader`, `cpr_tclSectionDecompress` and `cpr_tclDecompressAlgorithm` in `DAPIAPP.OUT` do. Example values are from `/CRYPTNAV/DATA/DATA/INSTRUCT/3D_PICT/ALP/ALR/JUG00378.PNN`. Everything is little-endian.

### Header

| Offset | Type      | Example    | Description                                                                             |
| ---    | ---       | ---        | ---                                                                                     |
| `0x00` | uint16    | 5          | Compression version; must be 5.                                                         |
| `0x02` | uint16    | 16         | Block size in KiB (at most 64). Every file seen uses 16, i.e. `0x4000` bytes per block. |
| `0x04` | char[8]   | "CPRNAV_2" | Signature. The firmware reads files without it as plain, uncompressed files.            |
| `0x0C` | uint32    | 566        | Unpacked size, in bytes.                                                                |
| `0x10` | uint8     | 3          | Mode: 3 = compressed, 1 = stored (copied as-is).                                        |
| `0x11` | uint8     | 0          | Unknown. Kept by the firmware but not used to decompress.                               |
| `0x12` | uint16    | 1          | Not read by the firmware.                                                               |
| `0x14` | uint32[]  | 28, 264    | Offsets of each block, then the end of the data.                                        |

There are `ceil(unpacked size / block size)` blocks, so `n + 1` offsets. `JUG00378.PNN` has one block, from `0x1C` to `0x108` (the end of the file).

### Blocks

Each block unpacks to exactly one block-size chunk of the output (the last one is cut short at the unpacked size). A block is laid out as:

```
[header] [bitstream ...] [literal bytes ...]
```

| Offset | Type   | Example | Description                                                                         |
| ---    | ---    | ---     | ---                                                                                 |
| `+0`   | uint16 | 75      | Offset of the literal bytes, from the start of the block.                           |
| `+2`   | uint16 | 15818   | Number of zero bytes that fill out the chunk after the decoded data (`0x4000 - 566`). |

With 64 KiB blocks, both fields are uint32 instead.

The bitstream starts straight after the header and is read least-significant bit first, a uint32 at a time. Its first 2 bits choose one of four code tables. After that it is a series of tokens, each a prefix code followed by extra bits, until the chunk is full. For table 0, the only one used in any file seen:

| Code (next bits == value) | Command         | Length                  | Distance                        |
| ---                       | ---             | ---                     | ---                             |
| 2 bits == 0               | copy 1 literal  | 1                       |                                 |
| 2 bits == 1               | copy from output | 2 + 2 bits              | 2 + (4 bits << 1)               |
| 3 bits == 3               | copy from output | 2 + 2 bits              | 34 + (8 bits << 1)              |
| 3 bits == 2               | copy from output | 2 + 2 bits              | 546 + (11 bits << 1)            |
| 3 bits == 6               | copy literals   | 2 + 3 bits              |                                 |
| 4 bits == 7               | copy from output | 6 + 5 bits              | 2 + (4 bits << 1)               |
| 5 bits == 15              | copy from output | 6 + 5 bits              | 34 + (8 bits << 1)              |
| 6 bits == 31              | copy from output | 6 + 5 bits              | 546 + (11 bits << 1)            |
| 6 bits == 63              | copy literals   | 10 + 8 bits             |                                 |

"Copy literals" takes the next bytes from the block's literal area. "Copy from output" is an LZ77-style match: it copies `length` bytes starting `distance` bytes back in the output. Extra bits are read length first, then distance.

Distances are always a multiple of the table's alignment. The other tables use 4-byte alignment (`<< 2`), a different window size, or both. See `CODE_TABLES` in `DecompressAlgorithm.py` for all four.
