# import os
# import subprocess
# from collections import Counter

# START_CODE = b"\x00\x00\x01"
# MAX_FRAMES = None          # đặt None nếu muốn chuyển toàn bộ video
# OUTPUT_H264 = "sample.h264"


# def convert_mp4_to_h264(src, dst, frames=None):
#     """Tương đương:
#     ffmpeg -i REF.mp4 -c copy -bsf:v h264_mp4toannexb sample.h264
#     """
#     cmd = ["./ffmpeg/ffmpeg", "-y", "-i", src, "-c", "copy", "-an"]
#     if frames:
#         cmd += ["-frames:v", str(frames)]
#     cmd += ["-bsf:v", "h264_mp4toannexb", dst]
#     subprocess.run(cmd, check=True)


# def count_after_start_code(path):
#     """Tìm mọi 00 00 01, lấy 4 byte ngay sau và đếm tần suất."""
#     with open(path, "rb") as f:
#         data = f.read()

#     counter = Counter()
#     pos = data.find(START_CODE)
#     while pos != -1:
#         chunk = data[pos + 3: pos + 7]
#         if len(chunk) == 4:
#             counter[chunk.hex(" ").upper()] += 1
#         pos = data.find(START_CODE, pos + 4)
#     return counter


# def main():
#     src = input("Nhập tên file mp4 (vd: REF.mp4): ").strip().strip('"')
#     if not os.path.isfile(src):
#         print(f"Không tìm thấy file: {src}")
#         return

#     print("Đang chuyển đổi sang H.264 (Annex B)...")
#     convert_mp4_to_h264(src, OUTPUT_H264, MAX_FRAMES)

#     counter = count_after_start_code(OUTPUT_H264)

#     print(f"\nTổng số start code 00 00 01: {sum(counter.values())}")
#     print(f"Số cụm 4 byte khác nhau: {len(counter)}\n")
#     print(f"{'4 byte sau':<14}{'Số lần'}")
#     for chunk, n in counter.most_common():
#         print(f"{chunk:<14}{n}")


# if __name__ == "__main__":
#     main()








import os
import subprocess
from collections import Counter

START_CODE = b"\x00\x00\x01"
MAX_FRAMES = None          # đặt None nếu muốn chuyển toàn bộ video
OUTPUT_H264 = "sample.h264"

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


def convert_mp4_to_h264(src, dst, frames=None):
    """Tương đương:
    ffmpeg -i REF.mp4 -c copy -bsf:v h264_mp4toannexb sample.h264
    """
    cmd = ["./ffmpeg/ffmpeg", "-y", "-i", src, "-c", "copy", "-an"]
    if frames:
        cmd += ["-frames:v", str(frames)]
    cmd += ["-bsf:v", "h264_mp4toannexb", dst]
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


def analyze(path):
    with open(path, "rb") as f:
        data = f.read()

    counter = Counter()      # (hex, nhãn) -> số lần
    frame_counter = Counter()  # số frame thực (slice có first_mb_in_slice = 0)

    pos = data.find(START_CODE)
    while pos != -1:
        chunk = data[pos + 3: pos + 7]
        if len(chunk) == 4:
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

    print("Đang chuyển đổi sang H.264 (Annex B)...")
    convert_mp4_to_h264(src, OUTPUT_H264, MAX_FRAMES)

    counter, frame_counter = analyze(OUTPUT_H264)

    print(f"\nTổng số start code 00 00 01: {sum(counter.values())}")
    print(f"Số cụm 4 byte khác nhau: {len(counter)}\n")

    print(f"{'4 byte sau':<14}{'Số lần':<10}Loại")
    print("-" * 55)
    for (chunk, label), n in counter.most_common():
        print(f"[{chunk}]  {n:<10}{label}")

    print("\nThống kê số frame (đếm slice đầu tiên của mỗi frame):")
    for label, n in frame_counter.most_common():
        print(f"  {label:<28}{n}")


if __name__ == "__main__":
    main()








# import os
# import subprocess
# from collections import Counter

# START_CODE = b"\x00\x00\x01"     # start code 3 byte
# BYTES_AFTER = 3                  # số byte đọc sau start code
# MAX_FRAMES = None                # None = chuyển toàn bộ video, hoặc đặt số frame (vd: 16)
# OUTPUT_H264 = "sample.h264"
# FFMPEG_PATH = "./ffmpeg/ffmpeg"  # đổi thành "ffmpeg" nếu đã có trong PATH

# # False: tính cả 00 00 01 nằm trong 00 00 00 01 (start code 4 byte)
# # True : chỉ tính 00 00 01 thuần (byte đứng trước khác 00)
# STRICT_3BYTE_ONLY = False


# def convert_mp4_to_h264(src, dst, frames=None):
#     """Tương đương:
#     ffmpeg -i REF.mp4 -c copy -bsf:v h264_mp4toannexb sample.h264
#     """
#     cmd = [FFMPEG_PATH, "-y", "-i", src, "-c", "copy", "-an"]
#     if frames:
#         cmd += ["-frames:v", str(frames)]
#     cmd += ["-bsf:v", "h264_mp4toannexb", dst]
#     subprocess.run(cmd, check=True)


# def count_after_start_code(path):
#     """Tìm mọi 00 00 01, lấy 3 byte ngay sau và đếm tần suất."""
#     with open(path, "rb") as f:
#         data = f.read()

#     counter = Counter()
#     pos = data.find(START_CODE)
#     while pos != -1:
#         is_valid = True
#         if STRICT_3BYTE_ONLY and pos > 0 and data[pos - 1] == 0x00:
#             is_valid = False

#         if is_valid:
#             start = pos + len(START_CODE)
#             chunk = data[start:start + BYTES_AFTER]
#             if len(chunk) == BYTES_AFTER:
#                 counter[chunk.hex(" ").upper()] += 1

#         pos = data.find(START_CODE, pos + len(START_CODE))
#     return counter


# def main():
#     src = input("Nhập tên file mp4 (vd: REF.mp4): ").strip().strip('"')
#     if not os.path.isfile(src):
#         print(f"Không tìm thấy file: {src}")
#         return

#     print("Đang chuyển đổi sang H.264 (Annex B)...")
#     convert_mp4_to_h264(src, OUTPUT_H264, MAX_FRAMES)

#     counter = count_after_start_code(OUTPUT_H264)

#     print(f"\nTổng số start code 00 00 01: {sum(counter.values())}")
#     print(f"Số cụm {BYTES_AFTER} byte khác nhau: {len(counter)}\n")
#     print(f"{'Start code':<12}{str(BYTES_AFTER) + ' byte sau':<14}{'Số lần'}")
#     for chunk, n in counter.most_common():
#         print(f"{'00 00 01':<12}{chunk:<14}{n}")


# if __name__ == "__main__":
#     main()