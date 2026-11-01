import os
import subprocess
from collections import Counter

SYNC_WORD = b"\xFF\xF1"    # ADTS sync (MPEG-4, không CRC)
SKIP_BYTES = 5             # bỏ 5 byte sau FF F1
TAKE_BYTES = 4             # lấy 4 byte tiếp theo
MAX_FRAMES = None          # đặt None nếu muốn chuyển toàn bộ audio
OUTPUT_AAC = "sample.aac"

# id_syn_ele: 3 bit đầu của raw_data_block
SYN_ELE_NAMES = {
    0: "SCE (Single Channel Element)",
    1: "CPE (Channel Pair Element)",
    2: "CCE (Coupling Channel Element)",
    3: "LFE (Low Frequency Element)",
    4: "DSE (Data Stream Element)",
    5: "PCE (Program Config Element)",
    6: "FIL (Fill Element)",
    7: "END",
}


def convert_mp4_to_aac(src, dst, frames=None):
    """Tương đương:
    ./ffmpeg/ffmpeg -i "$goodfile" -c copy -f adts "$sample_aac"
    """
    cmd = ["./ffmpeg/ffmpeg", "-i", src, "-c", "copy"]
    if frames:
        cmd += ["-frames:a", str(frames)]
    cmd += ["-f", "adts", dst]
    subprocess.run(cmd, check=True)


def classify(chunk):
    """Phân loại 4 byte dữ liệu AAC sau header ADTS.
    Trả về nhãn dựa trên id_syn_ele (3 bit đầu)."""
    syn_ele = chunk[0] >> 5
    return SYN_ELE_NAMES.get(syn_ele, f"Element {syn_ele}")


def analyze(path):
    with open(path, "rb") as f:
        data = f.read()

    counter = Counter()        # (hex, nhãn) -> số lần
    frame_counter = Counter()  # số frame thực (mỗi header FF F1 = 1 frame)

    pos = data.find(SYNC_WORD)
    while pos != -1:
        start = pos + len(SYNC_WORD) + SKIP_BYTES   # = pos + 7
        chunk = data[start: start + TAKE_BYTES]
        if len(chunk) == TAKE_BYTES:
            label = classify(chunk)
            counter[(chunk.hex(" ").upper(), label)] += 1
            frame_counter[label] += 1
        pos = data.find(SYNC_WORD, pos + len(SYNC_WORD))

    return counter, frame_counter


def main():
    src = input("Nhập tên file mp4 (vd: REF.mp4): ").strip().strip('"')
    if not os.path.isfile(src):
        print(f"Không tìm thấy file: {src}")
        return

    print("Đang chuyển đổi sang AAC (ADTS)...")
    convert_mp4_to_aac(src, OUTPUT_AAC, MAX_FRAMES)

    counter, frame_counter = analyze(OUTPUT_AAC)

    print(f"\nTổng số header FF F1: {sum(counter.values())}")
    print(f"Số cụm 4 byte khác nhau: {len(counter)}\n")

    print(f"{'4 byte sau':<14}{'Số lần':<10}Loại")
    print("-" * 60)
    for (chunk, label), n in counter.most_common():
        print(f"[{chunk}]  {n:<10}{label}")

    print("\nThống kê số frame (đếm theo header ADTS):")
    for label, n in frame_counter.most_common():
        print(f"  {label:<34}{n}")


if __name__ == "__main__":
    main()