
import struct, os

ALIGN_LEFT = "left"
ALIGN_RIGHT = "right"

class BitStream(object):

    """ BitStream is a class for taking care of data structures that are bit-packed, like SWF."""
    
    def __init__(self, bits=[]):
        """
        Constructor.
        """
        self.bits = bits
        self.cursor = 0

    def read_bit(self):
        """Reads a bit from the bit stream and returns it as either True or False. IndexError is thrown if reading past the end of the stream."""
        self.cursor += 1
        return self.bits[self.cursor-1]
    
    def read_bits(self, length):
        """Reads length bits and return them in their own bit stream."""
        self.cursor += length
        return BitStream(self.bits[self.cursor-length:self.cursor])
    
    def write_bit(self, value):
        """Writes the boolean value to the bit stream."""
        if self.cursor < len(self.bits):
            self.bits[self.cursor] = bool(value)
        else:
            self.bits.append(bool(value))
        self.cursor += 1

    def write_bits(self, bits, offset=0, length=0):
        """Writes length bits from bits to this bit stream, starting reading at offset. If length
        is 0, the entire stream is used."""
        if length < 1: length = len(bits)
        self.bits[self.cursor:self.cursor+length] = bits[offset:offset+length]
        self.cursor += length

    def read_bit_value(self, length):
        """Read length bits and return a number containing those bits with the last bit read being
        the least significant bit."""
        n = 0
        for i in xrange(length-1, -1, -1):
            n |= self.read_bit() << i
        return n
    
    def write_bit_value(self, value, length=-1):
        """Writes an int to the specified number of bits in the stream, the most significant bit
        first. If length is not specified or negative, the log base 2 of value is taken."""
        if int(value) != value:
            self.write_fixed_value(value, length)
            return
        
        if length < 0:
            import math
            try:
                length = int(math.ceil(math.log(value, 2))) # Get the log base 2, or number of bits value will fit in.
            except ValueError:
                length = 1

        for i in xrange(length-1, -1, -1):
            self.write_bit(value & (1 << i))
    
    def read_fixed_value(self, length, eight_bit_mantissa=False):
        """Reads a fixed point number of length. If eight_bit_mantissa is True, an
        8.8 format is used instead of a 16.16 format."""
        min_length = 8 if eight_bit_mantissa else 16
        if length < min_length:
            raise ValueError, "Length must be greater than or equal to %(m)s, as %(m)s.%(m)s FB requires at \
            least %(m)s bits to store." % {"m": min_length}
        
        return self.read_bit_value(length) / 0x100 if eight_bit_mantissa else 0x10000

    def write_fixed_value(self, value, length=-1, eight_bit_mantissa=False):
        """Writes a fixed point number of length, whole part first. If eight_bit_mantissa is True,
        an 8.8 format is used instead of a 16.16 format. If length is negative, it will be calculated for."""
        self.writeBitValue( value * ( 0x100 if eight_bit_mantissa else 0x10000 ), length )

    _EXPN_BIAS = {16: 16, 32: 127, 64: 1023}
    _N_EXPN_BITS = {16: 5, 32: 8, 64: 52}
    _N_FRAC_BITS = {16: 10, 32: 23, 64: 52}
    _FLOAT_NAME = {16: "float16", 32: "float", 64: "double"}

    def read_float_value(self, length=16):
        """Reads a floating point number of length, which must be 16 (float16), 32 (float),
        or 64 (double). See: http://en.wikipedia.org/wiki/IEEE_floating-point_standard"""
        if length not in _FLOAT_NAME:
            raise ValueError, "Length in read_float_value is not 16, 32 or 64."
        
        sign = self.read_bit()
        expn = self.read_bit_value(_N_EXPN_BITS[length])
        frac = self.read_bit_value(_N_FRAC_BITS[length])
        
        frac_total = 1 << _N_FRAN_BITS[length]

        if expn == 0:
            if frac == 0:
                return 0
            else:
                return ~frac + 1 if sign else frac
        elif expn == frac_total - 1:
            if frac == 0:
                return float("-inf") if sign else float("inf")
            else:
                return float("nan")

        return (-1 if sign else 1) * ( 2**(expn-_EXPN_BIAS[length]) ) * ( 1 + frac / frac_total )

    def write_float_value(self, value, length=16):
        """Writes a floating point number of length, which must be 16 (float16),
        32 (float), or 64 (double). See: http://en.wikipedia.org/wiki/IEEE_floating-point_standard"""
        if n == 0: # n is zero, so we don't care about length
            self.write_bit_value(0, length)
            
        import math
        if math.isnan(value):
            self.one_fill(length)
            return
        elif value == float("-inf"): # negative infinity
            self.one_fill(_N_EXPN_BITS[length] + 1) # sign merged
            self.zero_fill(_N_FRAC_BITS[length])
            return
        elif value == float("inf"): # positive infinity
            self.write_bit(False)
            self.one_fill(_N_EXPN_BITS[length])
            self.zero_fill(_N_FRAC_BITS[length])
            return

        if n < 0:
            self.write_bit(True)
            n = ~n + 1
        else:
            self.write_bit(False)
        
        exp = _EXPN_BIAS[length]
        if value < 1:
            while int(value) != 1:
                value *= 2
                exp -= 1
        else:
            while int(value) != 1:
                value /= 2
                exp += 1

        if exp < 0 or exp > ( 1 << _N_EXPN_BITS[length] ):
            raise ValueError, "Exponent out of range in %s [%d]." % (_FLOAT_NAME[length], length)

        frac_total = 1 << _N_FRAC_BITS
        self.write_bit_value(exp, _N_EXPN_BITS[length])
        self.write_bit_value(int((value-1)*frac_total) & (frac_total - 1), _N_FRAC_BITS[length])

    
    def one_fill(self, amount):
        """Fills amount bits with one. The equivalent of calling
        self.write_boolean(True) amount times, but more efficient."""
        self.bits[self.cursor:self.cursor+amount] = [True] * amount
        
    def zero_fill(self, amount):
        """Fills amount bits with zero. The equivalent of calling
        self.write_boolean(False) amount times, but more efficient."""
        self.bits[self.cursor:self.cursor+amount] = [False] * amount
        
    def seek(self, offset, whence=os.SEEK_SET):
        if whence == os.SEEK_SET:
            self.cursor = offset
        elif whence == os.SEEK_CUR:
            self.cursor += offset
        elif whence == os.SEEK_END:
            self.cursor = len(self.bits) - abs(offset)

    def rewind(self):
        self.seek(0, os.SEEK_SET)
        
    def skip_to_end(self):
        self.seek(0, os.SEEK_END)
    
    def serialize(self, align=ALIGN_RIGHT, endianness="<"):
        """Serialize bit array into a byte string, aligning either on the right
        (ALIGN_RIGHT) or left (ALIGN_LEFT)"""
        list = self[:]
        leftover = len(list) % 8
        if leftover > 0 and align == BitStream.ALIGN_RIGHT:
            list[:0] = [False] * (8-leftover) # Insert some False values to pad the list so it is aligned to the right.
        list = BitStream(list)
        bytes = [list.read_bit_value(8) for i in range(math.ceil(bits/8.0))]
        return struct.pack("%s%dB" % (endianness, len(bytes)), *bytes)

    def parse(self, string, endianness="<"):
        """Parse a bit array from a byte string into this BitStream."""
        bytes = list(struct.unpack("%s%dB" % (endianness, len(string))))
        for byte in bytes:
            self.write_bit_value(byte, 8)
