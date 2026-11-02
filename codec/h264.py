NAL_NAMES = {
    1: "Non-IDR slice",
    2: "Slice data partition A",
    3: "Slice data partition B",
    4: "Slice data partition C",
    5: "IDR slice",
    6: "SEI",
    7: "SPS",
    8: "PPS",
    9: "AUD",
    10: "End of sequence",
    11: "End of stream",
    12: "Filler data",
}


SLICE_TYPES = {0: "P", 1: "B", 2: "I", 3: "SP", 4: "SI"}



class BitReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0  # vị trí bit

    def read_bit(self):
        if self.pos >= len(self.data) * 8:
            raise EOFError
        byte = self.data[self.pos // 8]
        bit = (byte >> (7 - self.pos % 8)) & 1
        self.pos += 1
        return bit

    def read_ue(self):
        """Giải mã Exp-Golomb không dấu ue(v)."""
        zeros = 0
        while self.read_bit() == 0:
            zeros += 1
            if zeros > 32:
                raise ValueError("ue(v) không hợp lệ")
        value = 0
        for _ in range(zeros):
            value = (value << 1) | self.read_bit()
        return (1 << zeros) - 1 + value




def strip_emulation_prevention(data):
    """Bỏ byte 0x03 trong chuỗi 00 00 03 (emulation prevention)."""
    return data.replace(b"\x00\x00\x03", b"\x00\x00")




def classify(chunk):
    """Phân loại 4 byte sau start code.
    Trả về (nhãn, là_slice_đầu_frame)."""
    nal_header = chunk[0]
    if nal_header & 0x80:
        return "Không hợp lệ (forbidden_zero_bit=1)", False

    nal_type = nal_header & 0x1F
    name = NAL_NAMES.get(nal_type, f"NAL type {nal_type}")

    # Chỉ slice (type 1 và 5) mới có slice_type để đọc
    if nal_type not in (1, 5):
        return name, False

    try:
        br = BitReader(strip_emulation_prevention(chunk[1:]))
        first_mb = br.read_ue()
        slice_type = br.read_ue() % 5      # 5..9 tương đương 0..4
    except (EOFError, ValueError):
        return f"{name} (?)", False

    kind = SLICE_TYPES.get(slice_type, "?")
    if nal_type == 5:
        label = f"IDR frame ({kind}-slice)"
    else:
        label = f"{kind} frame"

    return label, first_mb == 0






