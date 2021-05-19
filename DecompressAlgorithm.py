import struct

class DecompressAlgorithm():
	def __init__(self):
		self.code_table_list   = CodeTableList()    # pointer to cpr_tclCodeTableList
		self.unknown_1         = 0                  # queues the next DWORD from the file into memory
		self.file_pointer      = 0                  # pointer into file being decompressed
		self.write_address     = 0                  # pointer into allocated memory, i.e. where decompressed data will be written
		self.num_bytes_to_copy = 0                  # size / number of bytes to copy
		self.unknown_5         = 0                  # ?
		self.unknown_6         = 0                  # ?
		self.bit_pos           = 0                  # current bit position in u32GetNextBits
		self.dword_remainder   = 0                  # stores remainder of bit shifting in u32GetNextBits
		self.unknown_9         = 0                  # cpr_tclCodeTable->Entry5
		self.unknown_10        = 0                  # ~(-1 << Unknown9)             // some kind of bit mask
		self.unknown_11        = 0                  # cpr_tclCodeTable->Entry10 >> 1
		self.unknown_12        = 0                  # block size? set in vInterpreteHeader to WORD following packed data size

		# Assigned in decompress()
		self.stream        = None
		self.stream_data   = None
		self.unpacked_data = None

	def decompress(self, file_path):
		self.stream = open(file_path, "rb")
		self.stream_data = self.stream.read()

		self.stream.seek(0)

		version   = struct.unpack("<H", self.stream.read(2))[0]
		unknown   = struct.unpack("<H", self.stream.read(2))[0] # Always seems to be 16 (apparently can't exceed 64)
		signature = self.stream.read(8).decode()

		# Not sure about this
		size_uncompressed = struct.unpack("<I", self.stream.read(4))[0]
		self.unpacked_data = [b"\x00"] * size_uncompressed

		if version != 5:
			raise Exception(f"Invalid compression version (expected 5, got {version})")

		if unknown > 64:
			raise Exception(f"Invalid unknown value (expected <= 64, got {unknown})")

		if signature != "CPRNAV_2":
			raise Exception(f'Invalid signature (expected "CPRNAV_2", got "{signature}")')

		self.stream.seek(0x14)

		header_size = struct.unpack("<I", self.stream.read(4))[0]

		self.stream.seek(header_size)

		# Read first DWORD
		self.unknown_1 = struct.unpack("<I", self.stream.read(4))[0]

		# cpr_tclDecompressAlgorithm::u32GetNextBits always returns a uint32 (hence the name) so shift off two bytes
		packed_data_size = self.get_next_bits(16) >> 16
		unknown_word = self.get_next_bits(16) >> 16

		# As in cpr_tclDecompressAlgorithm::vInterpreteHeader, these properties
		# are set to the first two WORDs preceding the packed data.
		# self.file_pointer is the offset of the section beginning AFTER the packed data
		self.file_pointer  = self.stream.tell() + packed_data_size - 8 # minus 8 due to two reading two DWORDs
		self.unknown_12 = unknown_word

		current_bytes = self.get_next_bits(32)

		code_table = self.code_table_list.code_tables[current_bytes & 3]

		current_bytes >>= 2

		num_bits = 2

		# cpr_tclDecompressAlgorithm::vInit
		self.unknown_9  = code_table.entry_5
		self.unknown_10 = ~(-1 << self.unknown_9) # May also be written: pow(2, val) - 1 or (1 << val) - 1
		self.unknown_11 = code_table.entry_10 >> 1

		while True:
			while True:
				# cpr_tclCodeTable::iu32SearchIndexOfCode
				entry_index = code_table.entry_3[current_bytes & code_table.entry_4]

				# cpr_tclCodeTable::corfoGetCodeEntry
				code_entry = code_table.entry_1[entry_index]

				num_bits += code_entry.u8_0

				current_bytes >>= code_entry.u8_0

				if code_entry.cmd_type != 3:
					break

				else:
					print("to-do")
					exit()

			if code_entry.cmd_type == 2:
				next_bytes = self.get_next_bits(num_bits)

				num_bits = code_entry.u8_1

				v37 = next_bytes | current_bytes
				v38 = v37 & ~(-1 << num_bits)

				amt_to_copy = v38 + code_entry.u16_1

				current_bytes = v37 >> num_bits

				for i in range(amt_to_copy):
					self.unpacked_data[self.write_address] = bytes([self.stream_data[self.file_pointer]])
					self.file_pointer += 1
					self.write_address += 1

			else:
				print("to-do")
				exit()

	def get_next_bits(self, n):
		bit_pointer = self.bit_pos
		remaining_bits = 0
		this = None

		# bit_pointer is set - return value is extracted from existing DWORD
		if bit_pointer:

			overlap = n >= 32 - bit_pointer

			# Check if it's possible to return the required number of bits from the current DWORD
			# e.g. if bit pointer is 8 and 14 bits are requested, there are 24 bits from which to extract the requested 14
			if n <= 32 - bit_pointer: # e.g. if 14 <= 24: ...
				prev_val_remainder = self.dword_remainder

				print("prev_val_remainder:", prev_val_remainder, hex(prev_val_remainder))

				# ... will only be true if required bits is the same as (32 - bit pointer) ?
				# e.g. 16 bits required, and bit pointer is 16
				if n >= 32 - bit_pointer:
					some_remainder = 0 # requested number of bits is the same size of the current value

				else:
					# e.g. 8 + 14 = bit pointer now 22
					bit_pointer += n

					# e.g. 0x002556C8 << 10 = 0x00009458
					this = 0xFFFFFFFF & (prev_val_remainder << (32 - bit_pointer))

					# e.g. 0x002556C8 ^ (0x00009458 >> 10) = 0x000040C8
					some_remainder = prev_val_remainder ^ (this >> (32 - bit_pointer))

				# Requested exact number of bits remaining - return the current value and set bit pointer back to 0
				if overlap:
					self.bit_pos = some_remainder
					this = prev_val_remainder

				else:
					self.dword_remainder = some_remainder
					self.bit_pos = bit_pointer

			# Requested number of bits is more than is currently allocated - current/next DWORDs need to be shifted and ORd
			else:
				# e.g. 14 - (32 - 22) = 4
				v12 = n - (32 - bit_pointer)

				self.bit_pos = v12

				v14 = 0xFFFFFFFF & (self.unknown_1 << (32 - v12))
				v15 = 0xFFFFFFFF & (self.unknown_1 >> v12 << v12)

				v16 = self.dword_remainder

				self.unknown_1 = struct.unpack("<I", self.stream.read(4))[0]
				self.dword_remainder = v15

				this = v14 | (v16 >> v12)

		# bit_pointer is 0 - read the next DWORD
		else:
			current_dword = self.unknown_1 # unknown_1 contains DWORD from previous call OR the first DWORD of the file

			# Simplest option - just return the most recently read DWORD
			if n == 32:
				this = current_dword

			else:
				remaining_bits = 32 - n # e.g. 24, if 8 bits are requested

				# Shift DWORD left so only the required bits remain (constrained to 32-bit value with 0xFFFFFFFF)
				# e.g. 0x382556C8 -> 0x00000038
				this = 0xFFFFFFFF & (current_dword << remaining_bits)

				# Number of requested bits, e.g. 8
				self.bit_pos = n

				# Current DWORD minus the number of requested bits
				# e.g. 0x382556C8 -> 0x002556C8
				self.dword_remainder = current_dword ^ ((0xFFFFFFFF & (current_dword << remaining_bits)) >> remaining_bits)

			self.unknown_1 = struct.unpack("<I", self.stream.read(4))[0]

		return this

class CodeTableList():
	def __init__(self):
		self.code_tables = [
			CodeTable(),
			CodeTable(),
			CodeTable(),
			CodeTable()
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

	def update_reference_list(self):
		# get the highest of the LAST value from entry1's rows
		bits = max([entry.u8_2 for entry in self.entry_1])

		self.entry_2 = 1 << bits
		self.entry_3 = [None] * self.entry_2
		self.entry_4 = self.entry_2 - 1

		i = 0

		while i < self.entry_0:
			val_1 = self.entry_1[i].u16_0
			val_2 = self.entry_1[i].u8_2
			val_3 = 1 << val_2

			while val_1 < self.entry_2:
				self.entry_3[val_1] = i

				val_1 += val_3

			i += 1

	def get_entries(self, table_type):
		if table_type == 0:
			return [
				CodeTableEntry(0,  1,  1,   0,   0,    1, 2, 0, 0),
				CodeTableEntry(1,  2,  5,   2,   32,   3, 2, 2, 4),
				CodeTableEntry(2,  2,  5,   546, 4640, 3, 3, 2, 11),
				CodeTableEntry(3,  2,  5,   34,  544,  3, 3, 2, 8),
				CodeTableEntry(6,  2,  9,   0,   0,    2, 3, 3, 0),
				CodeTableEntry(7,  6,  37,  2,   32,   3, 4, 5, 4),
				CodeTableEntry(15, 6,  37,  34,  544,  3, 5, 5, 8),
				CodeTableEntry(31, 6,  37,  546, 4640, 3, 6, 5, 11),
				CodeTableEntry(63, 10, 265, 0,   0,    2, 6, 8, 0)
			]

		if table_type == 1:
			return [
				CodeTableEntry(0,  1,  1,   0,   0,    1, 2, 0, 0),
				CodeTableEntry(1,  2,  5,   4,   32,   3, 2, 2, 3),
				CodeTableEntry(2,  2,  5,   548, 4640, 3, 3, 2, 10),
				CodeTableEntry(3,  2,  5,   36,  544,  3, 3, 2, 7),
				CodeTableEntry(6,  2,  9,   0,   0,    2, 3, 3, 0),
				CodeTableEntry(7,  6,  37,  4,   32,   3, 4, 5, 3),
				CodeTableEntry(15, 6,  37,  36,  544,  3, 5, 5, 7),
				CodeTableEntry(31, 6,  37,  548, 4640, 3, 6, 5, 10),
				CodeTableEntry(63, 10, 265, 0,   0,    2, 6, 8, 0)
			]

		if table_type == 2:
			return [
				CodeTableEntry(0,  1,  1,   0,    0,    1, 2, 0, 0),
				CodeTableEntry(1,  2,  5,   4,    64,   3, 2, 2, 4),
				CodeTableEntry(2,  2,  5,   1092, 9184, 3, 3, 2, 11),
				CodeTableEntry(3,  2,  5,   68,   1088, 3, 3, 2, 8),
				CodeTableEntry(6,  2,  9,   0,    0,    2, 3, 3, 0),
				CodeTableEntry(7,  6,  21,  4,    64,   3, 4, 4, 4),
				CodeTableEntry(15, 6,  21,  68,   1088, 3, 5, 4, 8),
				CodeTableEntry(31, 6,  21,  1092, 9184, 3, 6, 4, 11),
				CodeTableEntry(63, 10, 137, 0,    0,    2, 6, 7, 0)
			]

		if table_type == 3:
			return [
				CodeTableEntry(0,  1,  1,   0,   0,    1, 2, 0, 0),
				CodeTableEntry(1,  2,  5,   2,   32,   3, 2, 2, 4),
				CodeTableEntry(2,  2,  5,   546, 2592, 3, 3, 2, 10),
				CodeTableEntry(3,  2,  5,   34,  544,  3, 3, 2, 8),
				CodeTableEntry(6,  2,  9,   0,   0,    2, 3, 3, 0),
				CodeTableEntry(7,  6,  37,  2,   32,   3, 4, 5, 4),
				CodeTableEntry(15, 6,  37,  34,  544,  3, 5, 5, 8),
				CodeTableEntry(31, 6,  37,  546, 2592, 3, 6, 5, 10),
				CodeTableEntry(63, 10, 265, 0,   0,    2, 6, 8, 0)
			]

class CodeTableEntry():
	def __init__(self, *args):
		self.u16_0    = args[0]
		self.u16_1    = args[1]
		self.u16_2    = args[2]
		self.u16_3    = args[3]
		self.u16_4    = args[4]
		self.cmd_type = args[5]
		self.u8_0     = args[6]
		self.u8_1     = args[7]
		self.u8_2     = args[8]

def main():
	file_path = "./JUG00378.PNN"

	print(f"reading file: {file_path}\n")

	decompressor = DecompressAlgorithm()

	decompressor.decompress(file_path)

	# Comment/remove exit() below to see what the code tables look like
	exit()

	for i, table in enumerate(decompressor.code_table_list.code_tables):
		print("TABLE", i, "->")

		for key in table.__dict__:
			if key == "entry_1":
				print("  ", key, "->")

				for row in table.__dict__[key]:
					print(
						"    ",
						row.u16_0,
						row.u16_1,
						row.u16_2,
						row.u16_3,
						row.u16_4,
						row.cmd_type,
						row.u8_0,
						row.u8_1,
						row.u8_2
					)
			else:
				print("  ", key, "->", table.__dict__[key])

		print("\n\n")

if __name__ == "__main__":
	main()