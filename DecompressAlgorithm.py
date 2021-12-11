import io
import os
import struct
import sys

class DecompressAlgorithm():
	def __init__(self):
		self.reset()
		self.code_table_list = CodeTableList()

	def reset(self):
		try:
			self.stream.close()
		except Exception as e:
			self.stream = None

		self.stream_data   = None
		self.unpacked_data = None
		self.unpacked_pos  = 0
		self.blocks        = []
		self.block_buffer  = None

	def save_file(self, file_path, file_ext = ".PNG"):
		with open(os.path.basename(file_path) + file_ext, "wb") as f:
			f.write(self.unpacked_data)

	def unpack(self, f, b):
		return struct.unpack(f, b)[0]

	def get_uint16(self, stream = None):
		try:
			if stream != None:
				return self.unpack("<H", stream.read(2))

			return self.unpack("<H", self.stream.read(2))
		except Exception as e:
			return 0

	def get_uint32(self, stream = None):
		try:
			if stream != None:
				return self.unpack("<I", stream.read(4))

			return self.unpack("<I", self.stream.read(4))
		except Exception as e:
			return 0

	def parse_header(self):
		self.stream.seek(0)

		version = self.get_uint16()

		if version != 5:
			raise Exception(f"Invalid compression version (expected 5, got {version})")

		unknown = self.get_uint16() # Always seems to be 16 (apparently can't exceed 64)

		if unknown > 64:
			raise Exception(f"Invalid unknown value (expected <= 64, got {unknown})")

		signature = self.stream.read(8).decode()

		if signature != "CPRNAV_2":
			raise Exception(f'Invalid signature (expected "CPRNAV_2", got "{signature}")')

		self.unpacked_size = self.get_uint32()
		self.unpacked_data = bytearray(self.unpacked_size)

		# Bytes 0x10-0x13 always appear to be two ints:
		#   0x0300, i.e. 3 - probably the compression mode; 3 = compressed, 1 = uncompressed.
		#   0x0100, i.e. 1 - no idea
		compression_mode = self.get_uint16()

		if compression_mode != 3:
			raise Exception(f"Invalid compression mode (expected 3, got {compression_mode})")

		self.stream.seek(0x14)

		# After the standard header, one or more DWORDs follow which indicate block offsets.
		# A "block" is a series of bytes which are used with a "reference list" for unpacking the actual data.
		first_block_offset = self.get_uint32()

		blocks = []

		while self.stream.tell() < first_block_offset:
			blocks.append(self.get_uint32())

		# Append the block ranges
		for i in range(len(blocks)):
			if i == 0:
				block_start = self.stream.tell()
			else:
				block_start = blocks[i - 1]

			block_end = blocks[i]

			self.blocks.append((block_start, block_end,))

	def decompress(self, file_path):
		print(f"Reading file: {file_path}")

		# Read in all file data
		self.stream = open(file_path, "rb")
		self.stream_data = self.stream.read()

		# Read file header
		self.parse_header()

		# Unpack the blocks
		for block in self.blocks:
			unpacked_block = self.unpack_block(block)

			# Put unpacked block data into main output byte array
			for b in unpacked_block:
				self.unpacked_data[self.unpacked_pos] = b
				self.unpacked_pos += 1

			while self.unpacked_pos % 0x4000 != 0 and self.unpacked_pos < self.unpacked_size:
				self.unpacked_data[self.unpacked_pos] = 0x00
				self.unpacked_pos += 1

		print(f"Decompressed {file_path}")

		# Save output
		self.save_file(file_path)

		self.reset()

	def unpack_block(self, block):
		self.curr_dword_bit_pos = 0
		self.curr_dword         = 0
		self.dword_remainder    = 0
		self.unknown_9          = 0
		self.unknown_10         = 0
		self.unknown_11         = 0

		file_pointer  = 0
		write_address = 0

		block_start, block_end = block

		self.block_buffer = io.BytesIO(self.stream_data[block_start:block_end])
		block_bytes  = self.block_buffer.read()

		self.block_buffer.seek(0)

		# Read first four bytes of block into for u32_get_next_bits method
		self.curr_dword = self.get_uint32(self.block_buffer)

		# TO-DO: figure out when/why this is set (apparently it's not unpacked_data size being compared...)
		if 0 and some_mystery_variable >= 0x10000:
			unpack_info_size   = self.u32_get_next_bits(32)
			unpacked_data_size = self.u32_get_next_bits(32)

		# cpr_tclDecompressAlgorithm::u32GetNextBits always returns a uint32 (hence the name),
		# so shift off two bytes if WORDs are used here instead of DWORDs.
		else:
			unpack_info_size   = self.u32_get_next_bits(16) >> 16
			unpacked_data_size = 0x4000 - (self.u32_get_next_bits(16) >> 16)

		unpacked_block = bytearray(unpacked_data_size)

		file_pointer = unpack_info_size

		# Read first four bytes of unpacking info
		info_bytes = self.u32_get_next_bits(32)

		code_table = self.code_table_list.code_tables[info_bytes & 3]

		info_bytes >>= 2

		num_bits = 2

		# cpr_tclDecompressAlgorithm::vInit
		self.unknown_9  = code_table.entry_5
		self.unknown_10 = (1 << code_table.entry_5) - 1
		self.unknown_11 = code_table.entry_10 >> 1

		while 1:
			while 1:
				if write_address == unpacked_data_size:
					self.block_buffer.close()
					return unpacked_block

				# cpr_tclCodeTable::iu32SearchIndexOfCode
				entry_index = code_table.entry_3[info_bytes & code_table.entry_4]

				# cpr_tclCodeTable::corfoGetCodeEntry
				code_entry = code_table.reference_list[entry_index]

				num_bits += code_entry.u8_0

				info_bytes >>= code_entry.u8_0

				if code_entry.cmd_type != 3:
					break

				# cmd type 3: copy bytes from the allocated memory into itself at a different address
				next_bits = self.u32_get_next_bits(num_bits)

				info_bytes |= next_bits

				# number of bytes to copy
				amt_to_copy = code_entry.u16_1 + (info_bytes & ((1 << code_entry.u8_1) - 1))

				info_bytes >>= code_entry.u8_1

				# backwards offset from current position in output buffer
				offset = -(code_entry.u16_3 + ((info_bytes & ((1 << code_entry.u8_2) - 1)) << self.unknown_11))

				info_bytes >>= code_entry.u8_2

				# Compressed files appear to be padded to multiples of four, so don't copy any trailing null bytes.
				if unpacked_data_size < write_address + amt_to_copy:
					amt_to_copy = unpacked_data_size - write_address

				bytes_to_copy = unpacked_block[write_address + offset:write_address + offset + amt_to_copy]

				for b in bytes_to_copy:
					unpacked_block[write_address] = b
					write_address += 1

				num_bits = code_entry.u8_2 + code_entry.u8_1

			# cmd type 2: copy bytes from the input file into the allocated memory
			if code_entry.cmd_type == 2:
				next_bits = self.u32_get_next_bits(num_bits)

				info_bytes |= next_bits

				amt_to_copy = code_entry.u16_1 + (info_bytes & ((1 << code_entry.u8_1) - 1))

				info_bytes >>= code_entry.u8_1

				num_bits = code_entry.u8_1

				if unpacked_data_size < write_address + amt_to_copy:
					amt_to_copy = unpacked_data_size - write_address

				bytes_to_copy = block_bytes[file_pointer:file_pointer + amt_to_copy]

				for b in bytes_to_copy:
					unpacked_block[write_address] = b
					write_address += 1
					file_pointer += 1

			# cmd type 1: copy one byte from the input file to the allocated memory
			else:
				unpacked_block[write_address] = ord(block_bytes[file_pointer:file_pointer + 1])
				write_address += 1
				file_pointer += 1

	def u32_get_next_bits(self, n):
		if n <= 0:
			raise Exception(f"not enough bits (requested {n})")

		if n > 32:
			raise Exception(f"too many bits (requested {n})")

		curr_dword_bit_pos = self.curr_dword_bit_pos

		if curr_dword_bit_pos:
			some_bool = n >= 32 - curr_dword_bit_pos

			if n <= 32 - curr_dword_bit_pos:
				dword_remainder = self.dword_remainder

				if n >= 32 - curr_dword_bit_pos:
					something = 0

				else:
					curr_dword_bit_pos += n
					next_bits = 0xFFFFFFFF & (dword_remainder << (32 - curr_dword_bit_pos))
					something = dword_remainder ^ (next_bits >> (32 - curr_dword_bit_pos))

				if some_bool:
					self.curr_dword_bit_pos = something
					next_bits = dword_remainder

				else:
					self.dword_remainder = something
					self.curr_dword_bit_pos = curr_dword_bit_pos

			else:
				some_bits = n - (32 - curr_dword_bit_pos)

				curr_dword = self.curr_dword

				self.curr_dword_bit_pos = some_bits

				v14 = 0xFFFFFFFF & (curr_dword << (32 - some_bits))
				v15 = 0xFFFFFFFF & (curr_dword >> some_bits << some_bits)

				dword_remainder = self.dword_remainder

				self.curr_dword = self.get_uint32(self.block_buffer)
				self.dword_remainder = v15

				next_bits = v14 | (dword_remainder >> some_bits)

		else:
			if n == 32:
				next_bits = self.curr_dword
				self.curr_dword = self.get_uint32(self.block_buffer)

			else:
				remaining_dword_bits = 32 - n

				next_bits = 0xFFFFFFFF & (self.curr_dword << remaining_dword_bits)

				self.dword_remainder = self.curr_dword ^ (next_bits >> remaining_dword_bits)
				self.curr_dword = self.get_uint32(self.block_buffer)
				self.curr_dword_bit_pos = n

		return next_bits

# cpr_tclCodeTableList::cpr_tclCodeTableList
class CodeTableList():
	def __init__(self):
		self.code_tables = [
			CodeTable(),
			CodeTable(),
			CodeTable(),
			CodeTable(),
		]

		self.code_tables[0].set_standard_table(0)
		self.code_tables[1].set_standard_table(1)
		self.code_tables[2].set_standard_table(2)
		self.code_tables[3].set_standard_table(3)

class CodeTable():
	def __init__(self):
		self.entry_0        = 0
		self.reference_list = 0
		self.entry_2        = 0
		self.entry_3        = 0
		self.entry_4        = 0
		self.entry_5        = 0
		self.entry_6        = 0
		self.entry_7        = 0
		self.entry_8        = 0
		self.entry_9        = 0
		self.entry_10       = 0

	# cpr_tclCodeTable::vSetStandardTable
	def set_standard_table(self, table_type):
		self.entry_0        = 9
		self.reference_list = self.get_entries(table_type)
		self.entry_5        = 6

		if table_type == 0:
			self.entry_6  = 16
			self.entry_7  = 4640
			self.entry_8  = 265
			self.entry_9  = 37
			self.entry_10 = 2

		elif table_type == 1:
			self.entry_6  = 15
			self.entry_7  = 4640
			self.entry_8  = 265
			self.entry_9  = 37
			self.entry_10 = 4

		elif table_type == 2:
			self.entry_6  = 15
			self.entry_7  = 9184
			self.entry_8  = 137
			self.entry_9  = 21
			self.entry_10 = 4

		elif table_type == 3:
			self.entry_6  = 16
			self.entry_7  = 2592
			self.entry_8  = 265
			self.entry_9  = 37
			self.entry_10 = 2

		self.update_reference_list()

	# Also from cpr_tclCodeTable::vSetStandardTable
	def get_entries(self, table_type):
		if table_type == 0:
			return [
				CodeTableEntry(1, 0,  2, 0, 1,  1,   0,  0,   0),
				CodeTableEntry(3, 1,  2, 2, 2,  5,   4,  2,   32),
				CodeTableEntry(3, 2,  3, 2, 2,  5,   11, 546, 4640),
				CodeTableEntry(3, 3,  3, 2, 2,  5,   8,  34,  544),
				CodeTableEntry(2, 6,  3, 3, 2,  9,   0,  0,   0),
				CodeTableEntry(3, 7,  4, 5, 6,  37,  4,  2,   32),
				CodeTableEntry(3, 15, 5, 5, 6,  37,  8,  34,  544),
				CodeTableEntry(3, 31, 6, 5, 6,  37,  11, 546, 4640),
				CodeTableEntry(2, 63, 6, 8, 10, 265, 0,  0,   0),
			]

		if table_type == 1:
			return [
				CodeTableEntry(1, 0,  2, 0, 1,  1,   0,  0,   0),
				CodeTableEntry(3, 1,  2, 2, 2,  5,   3,  4,   32),
				CodeTableEntry(3, 2,  3, 2, 2,  5,   10, 548, 4640),
				CodeTableEntry(3, 3,  3, 2, 2,  5,   7,  36,  544),
				CodeTableEntry(2, 6,  3, 3, 2,  9,   0,  0,   0),
				CodeTableEntry(3, 7,  4, 5, 6,  37,  3,  4,   32),
				CodeTableEntry(3, 15, 5, 5, 6,  37,  7,  36,  544),
				CodeTableEntry(3, 31, 6, 5, 6,  37,  10, 548, 4640),
				CodeTableEntry(2, 63, 6, 8, 10, 265, 0,  0,   0),
			]

		if table_type == 2:
			return [
				CodeTableEntry(1, 0,  2, 0, 1,  1,   0,  0,    0),
				CodeTableEntry(3, 1,  2, 2, 2,  5,   4,  4,    64),
				CodeTableEntry(3, 2,  3, 2, 2,  5,   11, 1092, 9184),
				CodeTableEntry(3, 3,  3, 2, 2,  5,   8,  68,   1088),
				CodeTableEntry(2, 6,  3, 3, 2,  9,   0,  0,    0),
				CodeTableEntry(3, 7,  4, 4, 6,  21,  4,  4,    64),
				CodeTableEntry(3, 15, 5, 4, 6,  21,  8,  68,   1088),
				CodeTableEntry(3, 31, 6, 4, 6,  21,  11, 1092, 9184),
				CodeTableEntry(2, 63, 6, 7, 10, 137, 0,  0,    0),
			]

		if table_type == 3:
			return [
				CodeTableEntry(1, 0,  2, 0, 1,  1,   0,  0,   0),
				CodeTableEntry(3, 1,  2, 2, 2,  5,   4,  2,   32),
				CodeTableEntry(3, 2,  3, 2, 2,  5,   10, 546, 2592),
				CodeTableEntry(3, 3,  3, 2, 2,  5,   8,  34,  544),
				CodeTableEntry(2, 6,  3, 3, 2,  9,   0,  0,   0),
				CodeTableEntry(3, 7,  4, 5, 6,  37,  4,  2,   32),
				CodeTableEntry(3, 15, 5, 5, 6,  37,  8,  34,  544),
				CodeTableEntry(3, 31, 6, 5, 6,  37,  10, 546, 2592),
				CodeTableEntry(2, 63, 6, 8, 10, 265, 0,  0,   0),
			]

	# cpr_tclCodeTable::vUpdateReferenceList
	def update_reference_list(self):
		bits = max([entry.u8_0 for entry in self.reference_list])

		self.entry_4 = (1 << bits) - 1
		self.entry_2 = 1 << bits
		self.entry_3 = [None] * (1 << bits)

		i = 0

		while i < self.entry_0:
			val_1 = self.reference_list[i].u8_0
			val_2 = self.reference_list[i].u16_0
			val_3 = 1 << val_1

			while val_2 < self.entry_2:
				self.entry_3[val_2] = i

				val_2 += val_3

			i += 1

class CodeTableEntry():
	# cpr_tclCodeTableEntry::vSetEntry
	def __init__(self, *args):
		self.u16_0    = args[1] # [0]
		self.u16_1    = args[4] # [2]
		self.u16_2    = args[5] # [4]
		self.u16_3    = args[7] # [6]
		self.u16_4    = args[8] # [8] - note the 2 byte gap here, not at the end of the struct!
		self.cmd_type = args[0] # [12]
		self.u8_0     = args[2] # [16]
		self.u8_1     = args[3] # [18]
		self.u8_2     = args[6] # [20]

def main(argc, argv):
	script_name = os.path.basename(argv[0])

	if argc < 2:
		print(f"Usage: {script_name} <filepath> [<filepath> ...]")
		return 1

	paths = argv[1:]

	if paths[0] == "all":
		paths = os.listdir(os.getcwd())

	decompressor = DecompressAlgorithm()

	for file_path in paths:
		if file_path != script_name and os.path.isfile(file_path):
			decompressor.decompress(file_path)

	return 0

if __name__ == "__main__":
	sys.exit(main(len(sys.argv), sys.argv))
