import os
import struct
import sys

class DecompressAlgorithm():
	def __init__(self):
		self.code_table_list    = CodeTableList()
		self.curr_dword         = 0
		self.file_pointer       = 0
		self.write_address      = 0
		self.curr_dword_bit_pos = 0
		self.dword_remainder    = 0
		self.unknown_9          = 0
		self.unknown_10         = 0
		self.unknown_11         = 0
		self.unknown_12         = 0

		# Assigned in decompress()
		self.stream        = None
		self.stream_data   = None
		self.unpacked_data = None

	def unpack(self, f, b):
		return struct.unpack(f, b)[0]

	def decompress(self, file_path):
		self.stream = open(file_path, "rb")
		self.stream_data = self.stream.read()

		self.stream.seek(0)

		version   = self.unpack("<H", self.stream.read(2))
		unknown   = self.unpack("<H", self.stream.read(2)) # Always seems to be 16 (apparently can't exceed 64)
		signature = self.stream.read(8).decode()

		size_uncompressed = self.unpack("<I", self.stream.read(4))
		self.unpacked_data = [0x00] * size_uncompressed

		if version != 5:
			raise Exception(f"Invalid compression version (expected 5, got {version})")

		if unknown > 64:
			raise Exception(f"Invalid unknown value (expected <= 64, got {unknown})")

		if signature != "CPRNAV_2":
			raise Exception(f'Invalid signature (expected "CPRNAV_2", got "{signature}")')

		self.stream.seek(0x14)

		header_size = self.unpack("<I", self.stream.read(4))

		self.stream.seek(header_size)

		# Read first four bytes of input file into memory for u32_get_next_bits method
		self.curr_dword = self.unpack("<I", self.stream.read(4))

		# cpr_tclDecompressAlgorithm::u32GetNextBits always returns a uint32 (hence the name) so shift off two bytes
		packed_data_size = self.u32_get_next_bits(16) >> 16
		unknown_word = self.u32_get_next_bits(16) >> 16

		# 0xFC063554
		info_dword = self.u32_get_next_bits(32)

		# cpr_tclDecompressAlgorithm::vInterpreteHeader
		self.file_pointer = self.stream.seek(header_size) + packed_data_size
		self.unknown_12 = unknown_word

		code_table = self.code_table_list.code_tables[info_dword & 3]

		info_dword >>= 2

		num_bits = 2

		# cpr_tclDecompressAlgorithm::vInit
		self.unknown_9  = code_table.entry_5
		self.unknown_10 = (1 << code_table.entry_5) - 1
		self.unknown_11 = code_table.entry_10 >> 1

		while 1:
			while 1:
				# cpr_tclCodeTable::iu32SearchIndexOfCode
				entry_index = code_table.entry_3[info_dword & code_table.entry_4]

				# cpr_tclCodeTable::corfoGetCodeEntry
				code_entry = code_table.entry_1[entry_index]

				num_bits += code_entry.u8_0

				info_dword >>= code_entry.u8_0

				print()
				print("CMD TYPE:", code_entry.cmd_type)

				if code_entry.cmd_type != 3:
					break

				# cmd type 3: copy bytes from the allocated memory into itself at a different address
				next_bits = self.u32_get_next_bits(num_bits)

				more_bits = next_bits | info_dword

				something = more_bits & ((1 << code_entry.u8_1) - 1)

				amt_to_copy = something + code_entry.u16_1

				v33 = more_bits >> code_entry.u8_1

				offset = -(code_entry.u16_3 + ((v33 & ((1 << code_entry.u8_2) - 1)) << self.unknown_11))

				bytes_to_copy = self.unpacked_data[self.write_address + offset:self.write_address + offset + amt_to_copy]

				print("copying", amt_to_copy, "bytes")
				print("offset", self.write_address, "+", offset, "=", self.write_address + offset)
				print("bytes_to_copy:", [hex(b) for b in bytes_to_copy])

				for b in bytes_to_copy:
					self.unpacked_data[self.write_address] = b
					self.write_address += 1

				num_bits = code_entry.u8_2 + code_entry.u8_1

				info_dword = v33 >> code_entry.u8_2

			# cmd type 2: copy bytes from the input file into the allocated memory
			if code_entry.cmd_type == 2:
				next_bits = self.u32_get_next_bits(num_bits) # in first iteration, reads a byte: 0x38 (as 0x00000038)

				num_bits = code_entry.u8_1

				more_bits = next_bits | info_dword

				something = more_bits & ((1 << num_bits) - 1)

				amt_to_copy = something + code_entry.u16_1

				info_dword = more_bits >> num_bits

				# Copy bytes into allocated memory
				bytes_to_copy = self.stream_data[self.file_pointer:self.file_pointer + amt_to_copy]

				print("copying", amt_to_copy, "bytes")
				print("bytes_to_copy:", [hex(b) for b in bytes_to_copy])

				for b in bytes_to_copy:
					self.unpacked_data[self.write_address] = b
					self.write_address += 1
					self.file_pointer += 1

			# cmd type 1: copy one byte from the input file to the allocated memory
			else:
				try:
					print("copying", 1, "byte")
					print("bytes_to_copy:", [hex(ord(self.stream_data[self.file_pointer:self.file_pointer + 1]))])

					self.unpacked_data[self.write_address] = ord(self.stream_data[self.file_pointer:self.file_pointer + 1])
					self.write_address += 1
					self.file_pointer += 1
				except Exception as e:
					with open("_dump.png", "wb") as f:
						f.write(bytearray(self.unpacked_data))
					raise e

	def u32_get_next_bits(self, n):
		print("getting", n, "bits")

		if n <= 0:
			raise Exception("Not enough bits")

		if n > 32:
			raise Exception("Too many bits")

		curr_dword_bit_pos = self.curr_dword_bit_pos

		if curr_dword_bit_pos:
			# determines whether or not [at least] the rest of curr_dword is being returned
			some_bool = n >= 32 - curr_dword_bit_pos # e.g. 16 >= (32 - 16) = 16 >= 16 = true

			if n <= 32 - curr_dword_bit_pos: # 16 <= (32 - 16) = 16 <= 16 = true
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

				self.curr_dword = self.unpack("<I", self.stream.read(4))
				self.dword_remainder = v15

				next_bits = v14 | (dword_remainder >> some_bits)

		else:
			dword_requested = n == 32

			if n == 32:
				next_bits = self.curr_dword

				next_dword = self.unpack("<I", self.stream.read(4))

			else:
				next_dword = self.unpack("<I", self.stream.read(4))

				remaining_dword_bits = 32 - n

				next_bits = 0xFFFFFFFF & (self.curr_dword << remaining_dword_bits)

				self.dword_remainder = self.curr_dword ^ (next_bits >> remaining_dword_bits)

			if dword_requested:
				self.curr_dword = next_dword

			else:
				self.curr_dword = next_dword
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
		self.entry_0  = 0
		self.entry_1  = 0
		self.entry_2  = 0
		self.entry_3  = 0
		self.entry_4  = 0
		self.entry_5  = 0
		self.entry_6  = 0
		self.entry_7  = 0
		self.entry_8  = 0
		self.entry_9  = 0
		self.entry_10 = 0

	# cpr_tclCodeTable::vSetStandardTable
	def set_standard_table(self, table_type):
		self.entry_0 = 9
		self.entry_1 = self.get_entries(table_type)
		self.entry_5 = 6

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

	# also from cpr_tclCodeTable::vSetStandardTable
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
		bits = max([entry.u8_0 for entry in self.entry_1])

		self.entry_4 = (1 << bits) - 1
		self.entry_2 = 1 << bits
		self.entry_3 = [None] * (1 << bits)

		i = 0

		while i < self.entry_0:
			val_1 = self.entry_1[i].u8_0
			val_2 = self.entry_1[i].u16_0
			val_3 = 1 << val_1

			while val_2 < self.entry_2:
				self.entry_3[val_2] = i

				val_2 += val_3

			i += 1

class CodeTableEntry():
	# cpr_tclCodeTableEntry::vSetEntry
	def __init__(self, *args):
		self.u16_0    = args[1]
		self.u16_1    = args[4]
		self.u16_2    = args[5]
		self.u16_3    = args[7]
		self.u16_4    = args[8]
		self.cmd_type = args[0]
		self.u8_0     = args[2]
		self.u8_1     = args[3]
		self.u8_2     = args[6]

def main(argc, argv):
	if argc < 2:
		print(f"Usage: {argv[0]} <filepath> [<filepath> ...]")
		return 1

	paths = argv[1:]

	for file_path in paths:
		print(f"reading file: {file_path}\n")

		decompressor = DecompressAlgorithm()

		decompressor.decompress(file_path)

	return 0

if __name__ == "__main__":
	sys.exit(main(len(sys.argv), sys.argv))
