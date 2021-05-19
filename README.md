# lcn2kai-decompress

**THIS SCRIPT DOES NOT CURRENTLY WORK!**


## Background

As a recent owner of a Nissan Pulsar and as a reverse engineering enthusiast, I recently came across [this excellent writeup](https://github.com/ea/bosch_headunit_root/) of how to root the Bosch head unit that's equipped in virtually every modern Nissan vehicle.

In some models, the head unit has an SD card slot next to the CD reader:

![connect-3](https://user-images.githubusercontent.com/33162278/118888942-4723ed80-b8f4-11eb-9d92-c9cc1ca157e1.png)

I was curious to find out what's on there but was quickly disappointed when I discovered that most all the files on there are compressed using a custom compression algorithm.

Looking at the files in a hex editor reveals that they all have the same header, containing a signature "CPRNAV_2". After being pointed in the direction of a firmware update file on the [Nissan QashQai Forums](https://www.qashqaiforums.co.uk/) and searching for the signature, there was one match: `/var/opt/bosch/dynamic/processes/DAPIAPP.OUT`.

The file is an [ELF binary](https://en.wikipedia.org/wiki/Executable_and_Linkable_Format), and opening it in IDA, I was surprised to see the symbols were present. Searching for "CPRNAV_2" in the strings window revealed where the decompression function was: `cpr_tclDecompressAlgorithm::bDecompressData` at offset `0x0091A32C`.


## Current Status

At the time of writing (20th May 2021) my efforts to write a decompressor have been unsuccessful, but I'm creating this repository in the hope that others may be able to offer a fresh look at what I've discovered so far and help in fully reverse engineering the unpacking routine.

I believe the function arguments for `cpr_tclDecompressAlgorithm::bDecompressData` in order are:

1. An instance of `cpr_tclDecompressAlgorithm` (see below for my guess as to the struct).
2. The offset into the file being decompressed, *after* the standard "CPRNAV_2" header.
3. The allocated memory address where the uncompressed data will be written to.
4. The number of bytes allocated for the decompressed file.
5. Often seems to be the same as #4, but is sometimes different.
6. Decompression mode.

### Structs

My guess as to the struct for the first function argument:

```cpp
struct cpr_tclDecompressAlgorithm
{
	uint32_t CodeTableList;  // Pointer to cpr_tclCodeTableList (see struct below)
	uint32_t Unknown1;       // Queues the next DWORD from the compressed input file into memory
	uint32_t FilePointer;    // Pointer into file being decompressed
	uint32_t WriteAddress;   // Pointer into allocated memory, i.e. where decompressed data is being written
	uint32_t NumBytesToCopy; // Size / number of bytes to copy
	uint32_t Unknown5;       // ?
	uint32_t Unknown6;       // ?
	uint32_t BitPos;         // Current bit position in cpr_tclDecompressAlgorithm::u32GetNextBits
	uint32_t DwordRemainder; // Stores remainder of bit shifting in cpr_tclDecompressAlgorithm::u32GetNextBits
	uint32_t Unknown9;       // cpr_tclCodeTable->Entry5 (see struct below)
	uint32_t Unknown10;      // Some kind of bit mask; set in cpr_tclDecompressAlgorithm::vInit to: (1 << cpr_tclDecompressAlgorithm->Unknown9) - 1
	uint32_t Unknown11;      // cpr_tclCodeTable->Entry10 >> 1
	uint32_t Unknown12;      // Possibly the decompressed file size. Set in cpr_tclDecompressAlgorithm::vInterpreteHeader
};
```

Other structs used during decompression:

```cpp
struct cpr_tclCodeTableList
{
	cpr_tclCodeTable CodeTables[4];
};
```

```cpp
struct cpr_tclCodeTable // 44 bytes
{
	uint32_t Entry0; // Always 9 (because there are 9 cpr_tclCodeTableEntry structs in Entry1?)
	uint32_t Entry1; // Pointer to array of 9 cpr_tclCodeTableEntry structs
	uint32_t Entry2;
	uint32_t Entry3;
	uint32_t Entry4;
	uint32_t Entry5; // Always 6
	uint32_t Entry6;
	uint32_t Entry7;
	uint32_t Entry8;
	uint32_t Entry9;
	uint32_t Entry10;
};
```

```cpp
struct cpr_tclCodeTableEntry
{
	uint16_t u16_0;
	uint16_t u16_1;
	uint16_t u16_2;
	uint16_t u16_3;
	uint16_t u16_4;
	uint32_t cpr_tenCmdType; // Command type? Seems to indicate memory copying operation in cpr_tclDecompressAlgorithm::bDecompressData
	uint8_t  u8_0;
	uint8_t  u8_1;
	uint8_t  u8_2;
};
```

The 9 `cpr_tclCodeTableEntry` structs are set in `cpr_tclCodeTable::vSetStandardTable` at offset `0x00922954`.

```cpp
struct FileHeader
{
	uint16_t CompressionVersion; // 5
	uint16_t Unknown1;           // 16
	char     Signature[8];       // "CPRNAV_2"
	uint32_t SizeUncompressed;   // Guess
	uint16_t Unknown2;           // 3
	uint16_t Unknown3;           // 1
	uint32_t HeaderSize;         // Variable - often 0x1C (28) for small files
};
```

## Example

A brief overview of the decompression function is as follows, using one of the smallest files as an example (I encourage you to have the function open in your disassembler of choice for this section). Many of the small files appear to be PNGs, and their contents may look similar to the following:

```
Offset(h) 00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F

00000000  05 00 10 00 43 50 52 4E 41 56 5F 32 36 02 00 00  ....CPRNAV_26...
00000010  03 00 01 00 1C 00 00 00 08 01 00 00 4B 00 CA 3D  ............K.Ê=
00000020  FC 06 35 54 38 25 56 C8 1F 50 E9 06 04 58 85 5E  ü.5T8%VÈ.Pé..X…^
00000030  8C 91 E1 AD 84 DF 60 49 FC 01 61 FF 47 A9 D5 6A  Œ‘á.„ß`Iü.aÿG©Õj
00000040  B5 B2 06 48 65 0D D0 FA BE C3 D3 0E A1 EF 3B BC  µ².He.Ðú¾ÃÓ.¡ï;¼
00000050  EF F0 BE C3 73 0E A1 EF 3B BC EF F0 BE C3 73 0E  ïð¾Ãs.¡ï;¼ïð¾Ãs.
00000060  A1 DB E1 0F 58 E4 3E 89 50 4E 47 0D 0A 1A 0A 00  ¡Ûá.Xä>‰PNG.....
00000070  00 00 0D 49 48 44 52 F0 E0 08 02 B2 E1 7C 5E 00  ...IHDRðà..²á|^.
00000080  01 73 52 47 42 00 AE CE 1C E9 06 62 4B 47 44 00  .sRGB.®Î.é.bKGD.
00000090  FF A0 BD A7 93 09 70 48 59 73 0B 11 01 7F 64 5F  ÿ ½§“.pHYs....d_
000000A0  91 07 74 49 4D 45 07 E0 02 18 07 16 0E CB 92 13  ‘.tIME.à.....Ë’.
000000B0  67 B6 49 44 41 54 78 DA ED D2 C1 0D 04 00 31 C4  g¶IDATxÚíÒÁ...1Ä
000000C0  FE 2B 33 83 AF B4 23 5C 2E A7 27 E0 8B 92 00 43  þ+3ƒ¯´#\.§'à‹’.C
000000D0  83 A1 C1 D0 60 68 0C 0D 86 06 43 83 A1 C1 D0 18  ƒ¡ÁÐ`h..†.Cƒ¡ÁÐ.
000000E0  1A 0C 0D 86 06 31 34 18 1A 63 68 30 34 C6 30 8D  ...†.14..ch04Æ0.
000000F0  60 86 AB 05 8F 4F 03 C2 58 19 85 B6 00 49 45 4E  `†«..O.ÂX.…¶.IEN
00000100  44 AE 42 60 82 00 00 00                          D®B`‚...

```

After the `FileHeader` struct, the next value at `0x18` appears to be the file size (264 bytes).

I believe argument 2 of `cpr_tclDecompressAlgorithm::bDecompressData` is the offset after this value, i.e. `0x1C` (the header size). This seems to line up with `cpr_tclDecompressAlgorithm::vInterpreteHeader` which is called near the beginning of the function; it reads the first two WORDs, `0x4B00` and `0xCA3D`:

* `0x4B00` appears to be the size of the packed data (include the two bytes indicating the size)
* `0xCA3D` when subtracted from `0x4000` seems to give the same value as `FileHeader->SizeUncompressed`, although in some files this is 1 byte off

`cpr_tclDecompressAlgorithm->FilePointer` is incremented `0x4B00` bytes, i.e. to the beginning of the PNG header at `0x67`.

The next DWORD from the packed data is then read into memory, `0xFC063554`.

The first two bits of the DWORD are used as an index into `cpr_tclCodeTableList`: `0xFC063554 & 3 == 0`. The DWORD is then right shifted 2 bits.

`cpr_tclDecompressAlgorithm::vInit` is then called passing the code table as an argument, but this doesn't appear to affect anything in the function.

The DWORD is used again to get one of the 9 available `cpr_tclCodeTableEntry` structs.

Values from the `cpr_tclCodeTableEntry` are used to determine the number of bits to right shift the current DWORD, and the number of bits to read from the next DWORD.

If `cpr_tclCodeTableEntry->cpr_tenCmdType != 3`, some more bit shifting and ORing is carried out on the data read so far, which in turn is used to indicate how many bytes to copy into the allocated memory for the uncompressed file.

In the first iteration this number is 16 which seems to make sense - the first 16 bytes from the file pointer comprise the standard PNG header. The file pointer and write address are incremented 16 bytes, and then the `while` loop begins again.

This is where I believe my script starts to go wrong, as it then starts attempting to copy large amounts of bytes into the allocated memory until the file pointer exceeds the input file size. My current _guess_ is that my implementation of `cpr_tclDecompressAlgorithm::u32GetNextBits` isn't returning the correct values.

As mentioned earlier, `cpr_tenCmdType` seems to indicate the operation for copying data to the allocated memory. My observations are:

* `cpr_tenCmdType` is only ever 1, 2, or 3:
* 1 - doesn't copy anything to memory, just moves the write address/file pointer by one DWORD
* 2 - copies a number of bytes from the input file to the allocated memory
* 3 - copies data from allocated memory back into itself at a different address


## Summary

For anyone who's actually read this far, I would really appreciate a fresh pair of eyes on this. I have included my attempt in Python. Thanks for reading.
