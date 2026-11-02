import os
import math
import subprocess
from array import array
from collections import Counter

FFMPEG_PATH = "./ffmpeg/ffmpeg"   # đổi thành "ffmpeg" nếu đã có trong PATH
OUTPUT_PCM = "sample.pcm"
MAX_SECONDS = None         # đặt None nếu muốn chuyển toàn bộ audio
SAMPLE_RATE = 44100        # tần số lấy mẫu khi xuất PCM
CHANNELS = 2               # số kênh khi xuất PCM
TOP_N = 20                 # số mẫu xuất hiện nhiều nhất cần in

BYTES_PER_SAMPLE = 2       # s16le = 16 bit little-endian
BLOCK_SAMPLES = 1024       # số mẫu (mỗi kênh) trong 1 block để phân loại
SILENCE_THRESHOLD = 16     # |biên độ| <= ngưỡng này coi là im lặng
CLIP_THRESHOLD = 32760     # |biên độ| >= ngưỡng này coi là clipping

# PCM không có start code / sync word như H.264, H.265, AAC,
# nên phân tích dựa trên giá trị mẫu và thống kê theo block.


def convert_mp4_to_pcm(src, dst, seconds=None):
    """Tương đương:
    ./ffmpeg/ffmpeg -i REF.mp4 -vn -c:a pcm_s16le -ar 44100 -ac 2 -f s16le sample.pcm
    """
    cmd = [FFMPEG_PATH, "-y", "-i", src, "-vn", "-c:a", "pcm_s16le",
           "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-f", "s16le", dst]
    subprocess.run(cmd, check=True)


def load_samples(path):
    """Đọc file PCM s16le thành mảng số nguyên có dấu 16 bit."""
    with open(path, "rb") as f:
        data = f.read()
    usable = len(data) - len(data) % (BYTES_PER_SAMPLE * CHANNELS)
    samples = array("h")
    samples.frombytes(data[:usable])
    if os.sys.byteorder == "big":
        samples.byteswap()
    return data[:usable], samples


def classify_value(v):
    """Phân loại 1 giá trị mẫu."""
    if v == 0:
        return "Zero (im lặng tuyệt đối)"
    if abs(v) <= SILENCE_THRESHOLD:
        return "Gần im lặng"
    if abs(v) >= CLIP_THRESHOLD:
        return "Clipping (chạm trần)"
    return "Tín hiệu thường"


def classify_block(block):
    """Phân loại 1 block mẫu theo biên độ lớn nhất."""
    peak = max(max(block), -min(block))
    if peak == 0:
        return "Block zero (toàn 0)"
    if peak <= SILENCE_THRESHOLD:
        return "Block gần im lặng"
    if peak >= CLIP_THRESHOLD:
        return "Block có clipping"
    return "Block tín hiệu thường"


def analyze(raw, samples):
    """Trả về (đếm_cụm_mẫu, đếm_block, thống_kê)."""
    step = CHANNELS
    n_frames = len(samples) // step

    # 1) Tần suất từng "cụm mẫu" (1 mẫu của mọi kênh)
    counter = Counter()
    frame_bytes = BYTES_PER_SAMPLE * CHANNELS
    for i in range(n_frames):
        chunk = raw[i * frame_bytes:(i + 1) * frame_bytes]
        counter[chunk] += 1

    top = []
    for chunk, n in counter.most_common(TOP_N):
        values = [int.from_bytes(chunk[k:k + 2], "little", signed=True)
                  for k in range(0, frame_bytes, 2)]
        label = classify_value(values[0])
        top.append((chunk.hex(" ").upper(), values, label, n))

    # 2) Phân loại theo block
    block_counter = Counter()
    block_len = BLOCK_SAMPLES * CHANNELS
    for start in range(0, len(samples) - block_len + 1, block_len):
        block_counter[classify_block(samples[start:start + block_len])] += 1

    # 3) Thống kê chung
    total = len(samples)
    peak = max(max(samples), -min(samples)) if total else 0
    rms = math.sqrt(sum(s * s for s in samples) / total) if total else 0
    dc = sum(samples) / total if total else 0
    zeros = sum(1 for s in samples if s == 0)
    stats = {
        "frames": n_frames,
        "duration": n_frames / SAMPLE_RATE,
        "peak": peak,
        "rms": rms,
        "dc": dc,
        "zero_ratio": zeros / total if total else 0,
        "distinct": len(counter),
    }
    return top, block_counter, stats


def main():
    src = input("Nhập tên file mp4 (vd: REF.mp4): ").strip().strip('"')
    if not os.path.isfile(src):
        print(f"Không tìm thấy file: {src}")
        return

    print("Đang chuyển đổi sang PCM (s16le)...")
    convert_mp4_to_pcm(src, OUTPUT_PCM, MAX_SECONDS)

    raw, samples = load_samples(OUTPUT_PCM)
    if not samples:
        print("File PCM rỗng.")
        return

    top, block_counter, st = analyze(raw, samples)

    print(f"\nĐịnh dạng: s16le, {SAMPLE_RATE} Hz, {CHANNELS} kênh")
    print(f"Tổng số cụm mẫu: {st['frames']}  (~{st['duration']:.2f} giây)")
    print(f"Số cụm mẫu khác nhau: {st['distinct']}")
    print(f"Biên độ đỉnh: {st['peak']}   RMS: {st['rms']:.1f}   DC offset: {st['dc']:.2f}")
    print(f"Tỉ lệ mẫu bằng 0: {st['zero_ratio'] * 100:.2f}%\n")

    print(f"Top {TOP_N} cụm mẫu xuất hiện nhiều nhất:")
    print(f"{'Byte':<14}{'Giá trị':<18}{'Số lần':<10}Loại")
    print("-" * 70)
    for hx, values, label, n in top:
        print(f"[{hx}]  {str(values):<18}{n:<10}{label}")

    print(f"\nThống kê block ({BLOCK_SAMPLES} mẫu/kênh):")
    for label, n in block_counter.most_common():
        print(f"  {label:<28}{n}")


if __name__ == "__main__":
    main()
