# lcn2kai-decompress

Decompression/unpacking routine for files included on Nissan Connect 3 SD cards. Tested with "Europe V5" (pictured below).

You may also wish to check out the head unit root exploit by [ea](https://github.com/ea) at [github.com/ea/bosch_headunit_root/](https://github.com/ea/bosch_headunit_root/).

## How to Use

Decompress a specified file or files:

```
DecompressAlgorithm.py <filepath>
```

Decompress all files in the current directory:

```
DecompressAlgorithm.py all
```

Files will be saved as `<filename>.BIN`, or `<filename>.PNG` if the extension is one of `.PHD`, `.PHN`, `.PND`, `.PNN`.

## Background

If you have a Nissan made within the last 10 or so years, chances are you have a head unit that looks something like this:

![connect-3](https://user-images.githubusercontent.com/33162278/118888942-4723ed80-b8f4-11eb-9d92-c9cc1ca157e1.png)

Here in Europe, the SD card slot next to the CD reader contains one of these:

![Nissan Connect 3 Europe V5 SD card](https://user-images.githubusercontent.com/33162278/116246634-12dc6780-a762-11eb-8bb7-04a77ce8f2cc.jpg)

Taking a cursory look at the files on the SD card, you will realise most of them are unreadable (although there are a few plaintext and SQLite DB files).

If you're able to find a copy of the head unit's firmware online (hint: [Nissan QashQai Forums](https://www.qashqaiforums.co.uk/)) then it's fairly straightforward to find the binary responsible for decompression (another hint: do a `grep` for `"CPRNAV_2"`, the file signature, and you find one result: `/var/opt/bosch/dynamic/processes/DAPIAPP.OUT`). Fortunately the symbols haven't been stripped so it's not too hard to get a basic understanding of the methods/structs/etc. used.

## Compression Format

The values listed below are from the file `/CRYPTNAV/DATA/DATA/INSTRUCT/3D_PICT/ALP/ALR/JUG00378.PNN` (values shown in little-endian).

### Header

All compressed files have the following header:

| Offset | Type   | Value            | Description                                         |
| ---    | ---    | ---              | ---                                                 |
| `0x00` | uint16 | 5                | Compression version.                                |
| `0x02` | uint16 | 16               | Unknown.                                            |
| `0x04` | char   | "CPRNAV_2"       | Signature.                                          |
| `0x0C` | uint32 | `0x3602` / `566` | Unpacked size, in bytes.                            |
| `0x10` | uint16 | 3                | Compression mode; 3 = compressed, 1 = uncompressed? |
| `0x12` | uint16 | 1                | Unknown.                                            |
| `0x14` | uint32 | `0x1C` / `28`    | Offset to first block of packed data.               |

Values between `0x18` and `0x1C` (i.e. the first block) are offsets to the end of each block, in this case:

| Offset | Type   | Value            | Description               |
| ---    | ---    | ---              | ---                       |
| `0x18` | uint32 | `0x0801` / `264` | Offset to end of block 1. |

In the case of `JUG00378.PNN`, `0x108` is also the end of the file.

### Blocks

Compressed files contain one or more blocks of packed data. Blocks have a very simple header consisting of two values: "unpacking data" size and the unpacked block size. "Unpacking data" is data used to look up values from a preset code table which indicate things such as how many bytes to copy. Blocks appear to only unpack a maximum of `0x4000` bytes.

| Offset | Type   | Value              | Description                                                    |
| ---    | ---    | ---                | ---                                                            |
| `0x1C` | uint16 | `0x4B00` / `75`    | Size of unpacking data.                                        |
| `0x1E` | uint16 | `0xCA3D` / `15818` | Size of unpacked data when subtracted from `0x4000`, i.e. 566. |
