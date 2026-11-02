import os
import subprocess
from collections import Counter

START_CODE = b"\x00\x00\x01"
BYTES_AFTER = 4            # số byte đọc sau start code (header NAL H.265 = 2 byte)
MAX_FRAMES = None          # đặt None nếu muốn chuyển toàn bộ video
OUTPUT_H265 = "sample.h265"
FFMPEG_PATH = "./ffmpeg/ffmpeg"   # đổi thành "ffmpeg" nếu đã có trong PATH

# NAL unit type của H.265 (6 bit)
NAL_NAMES = {
    0: "TRAIL_N",
    1: "TRAIL_R",
    2: "TSA_N",
    3: "TSA_R",
    4: "STSA_N",
    5: "STSA_R",
    6: "RADL_N",
    7: "RADL_R",
    8: "RASL_N",
    9: "RASL_R",
    16: "BLA_W_LP",
    17: "BLA_W_RADL",
    18: "BLA_N_LP",
    19: "IDR_W_RADL",
    20: "IDR_N_LP",
    21: "CRA_NUT",
    32: "VPS",
    33: "SPS",
    34: "PPS",
    35: "AUD",
    36: "End of sequence",
    37: "End of bitstream",
    38: "Filler data",
    39: "Prefix SEI",
    40: "Suffix SEI",
}

SLICE_TYPES = {0: "B", 1: "P", 2: "I"}


def convert_mp4_to_h265(src, dst, frames=None):
    """Tương đương:
    ffmpeg -i REF.mp4 -c copy -bsf:v hevc_mp4toannexb sample.h265
    """
    cmd = [FFMPEG_PATH, "-y", "-i", src, "-c", "copy", "-an"]
    if frames:
        cmd += ["-frames:v", str(frames)]
    cmd += ["-bsf:v", "hevc_mp4toannexb", dst]
    subprocess.run(cmd, check=True)


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
    """Phân loại các byte sau start code.
    Trả về (nhãn, là_slice_đầu_frame).

    NAL header H.265 (2 byte):
      forbidden_zero_bit (1) | nal_unit_type (6) | nuh_layer_id (6) | nuh_temporal_id_plus1 (3)
    """
    if len(chunk) < 2:
        return "Quá ngắn", False

    if chunk[0] & 0x80:
        return "Không hợp lệ (forbidden_zero_bit=1)", False

    nal_type = (chunk[0] >> 1) & 0x3F
    layer_id = ((chunk[0] & 0x01) << 5) | (chunk[1] >> 3)
    tid_plus1 = chunk[1] & 0x07
    if tid_plus1 == 0:
        return "Không hợp lệ (temporal_id_plus1=0)", False

    name = NAL_NAMES.get(nal_type, f"NAL type {nal_type}")

    # Chỉ VCL (type 0..21) mới là slice
    if nal_type > 21 or nal_type in (10, 11, 12, 13, 14, 15):
        return name, False

    try:
        br = BitReader(strip_emulation_prevention(chunk[2:]))
        first_slice_in_pic = br.read_bit()
        if not first_slice_in_pic:
            # Slice tiếp theo của cùng frame: cần SPS/PPS mới đọc được tiếp
            return f"{name} (slice tiếp theo)", False

        if 16 <= nal_type <= 23:           # IRAP
            br.read_bit()                  # no_output_of_prior_pics_flag
        br.read_ue()                       # slice_pic_parameter_set_id
        # Giả định num_extra_slice_header_bits = 0 (hầu hết video)
        slice_type = br.read_ue()
    except (EOFError, ValueError):
        return f"{name} (?)", False

    kind = SLICE_TYPES.get(slice_type, "?")
    label = f"{name} ({kind}-slice)"
    return label, True


def analyze(path):
    with open(path, "rb") as f:
        data = f.read()

    counter = Counter()        # (hex, nhãn) -> số lần
    frame_counter = Counter()  # số frame thực (first_slice_segment_in_pic_flag = 1)

    pos = data.find(START_CODE)
    while pos != -1:
        chunk = data[pos + 3: pos + 3 + BYTES_AFTER]
        if len(chunk) == BYTES_AFTER:
            label, is_first_slice = classify(chunk)
            counter[(chunk.hex(" ").upper(), label)] += 1
            if is_first_slice:
                frame_counter[label] += 1
        pos = data.find(START_CODE, pos + 3)

    return counter, frame_counter


def main():
    src = input("Nhập tên file mp4 (vd: REF.mp4): ").strip().strip('"')
    if not os.path.isfile(src):
        print(f"Không tìm thấy file: {src}")
        return

    print("Đang chuyển đổi sang H.265 (Annex B)...")
    convert_mp4_to_h265(src, OUTPUT_H265, MAX_FRAMES)

    counter, frame_counter = analyze(OUTPUT_H265)

    print(f"\nTổng số start code 00 00 01: {sum(counter.values())}")
    print(f"Số cụm {BYTES_AFTER} byte khác nhau: {len(counter)}\n")

    print(f"{str(BYTES_AFTER) + ' byte sau':<16}{'Số lần':<10}Loại")
    print("-" * 60)
    for (chunk, label), n in counter.most_common():
        print(f"[{chunk}]  {n:<10}{label}")

    print("\nThống kê số frame (đếm slice đầu tiên của mỗi frame):")
    for label, n in frame_counter.most_common():
        print(f"  {label:<32}{n}")


if __name__ == "__main__":
    main()
