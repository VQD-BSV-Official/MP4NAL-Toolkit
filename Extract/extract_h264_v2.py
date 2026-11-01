#!/usr/bin/env python3
"""
extract_h264_v2.py - Trích xuất H.264 từ file MP4 hỏng bằng các cụm 4 byte
đặc trưng trong Log_video.txt / Log_audio.txt.

Cách dùng:
    python extract_h264_v2.py broken.mp4 Header Log_video.txt Log_audio.txt video.h264

Cấu trúc file đầu ra:
    [Header] 25 88 82 00 ...data
    00 00 01 21 9A 58 21 ...data
    00 00 01 01 00 3F C9 ...data
    ...

Loại byte rác (không phụ thuộc loại rác của từng file), 4 lớp:
    1. Độ dài AVCC (4 byte đứng trước NAL)  -> bỏ mọi thứ sau `size` byte
    2. Chuỗi cấm 00 00 00 / 00 00 01 / 00 00 02 (không thể có trong NAL hợp lệ)
    3. Marker rác lặp lại, tự học từ chính file
    4. Chuỗi ký tự in được dài (rác ASCII như GUID/watermark)
"""
import re
import sys
from collections import Counter

START_CODE = b"\x00\x00\x01"
LOG_RE = re.compile(r"\[((?:[0-9A-Fa-f]{2}\s?){4})\]\s+(\d+)")

# Không thể xuất hiện bên trong NAL hợp lệ (emulation prevention)
BAD_SEQ = re.compile(rb"\x00\x00[\x00-\x02]")
# Chuỗi ký tự in được dài -> rác ASCII (video thật gần như không có)
ASCII_RUN = re.compile(rb"[\x20-\x7E]{24,}")


# ---------------------------------------------------------------- log
def load_signatures(path):
    """Đọc log -> set các cụm 4 byte (bytes)."""
    sigs = set()
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = LOG_RE.search(line)
            if m:
                sigs.add(bytes.fromhex(m.group(1).replace(" ", "")))
    return sigs


def find_events(data, video_sigs, audio_sigs):
    """Trả về list (vị trí, 'V'|'A') đã sắp xếp, quét cả match chồng lấn."""
    pats = {s: "V" for s in video_sigs}
    for s in audio_sigs:
        pats.setdefault(s, "A")          # trùng với video thì ưu tiên video

    rx = re.compile(
        b"(?=(" + b"|".join(re.escape(p) for p in pats) + b"))", re.DOTALL
    )
    return [(m.start(), pats[m.group(1)]) for m in rx.finditer(data)]


# ---------------------------------------------------------------- lọc rác
def base_trim(seg, size):
    """Lớp 1 + 2: cắt theo độ dài AVCC và chuỗi cấm."""
    if 0 < size <= len(seg):
        seg = seg[:size]
    m = BAD_SEQ.search(seg, 1)           # bỏ qua byte 0 (NAL header)
    if m:
        seg = seg[:m.start()]
    return seg


def learn_markers(raw, n=8, tail=256, min_ratio=0.3):
    """Tìm chuỗi n byte lặp ở phần đuôi của nhiều đoạn -> rác riêng của file."""
    cnt = Counter()
    for seg, size in raw:
        s = base_trim(seg, size)[-tail:]
        cnt.update({s[i:i + n] for i in range(len(s) - n + 1)})
    need = max(5, int(len(raw) * min_ratio))
    return [p for p, c in cnt.items() if c >= need and len(set(p)) > 2]


def trim_segment(seg, size, markers, use_ascii=True):
    """Áp dụng đủ 4 lớp lọc rác lên một đoạn."""
    seg = base_trim(seg, size)
    cut = len(seg)

    # Lớp 3: marker rác học được
    for mk in markers:
        i = seg.find(mk, 1, cut)
        if i != -1:
            cut = i

    # Lớp 4: rác ASCII dài
    if use_ascii:
        m = ASCII_RUN.search(seg, 4, cut)
        if m:
            cut = m.start()

    return seg[:cut]


# ---------------------------------------------------------------- chính
def extract(mp4_path, header_path, vlog, alog, out_path, use_ascii=True):
    video_sigs = load_signatures(vlog)
    audio_sigs = load_signatures(alog)
    print(f"Video signatures: {len(video_sigs)}, Audio signatures: {len(audio_sigs)}")

    with open(mp4_path, "rb") as f:
        data = f.read()
    with open(header_path, "rb") as f:
        header = f.read()

    # Nếu Header đã kết thúc bằng start code thì NAL đầu nối thẳng vào sau nó
    header_has_start = header.endswith(START_CODE)

    events = find_events(data, video_sigs, audio_sigs)
    print(f"Tìm thấy {len(events)} mốc")

    # Bước 1: gom đoạn thô (từ mốc video đến mốc kế tiếp, video hoặc audio)
    raw = []
    for i, (pos, kind) in enumerate(events):
        if kind != "V":
            continue
        nxt = events[i + 1][0] if i + 1 < len(events) else len(data)
        size = int.from_bytes(data[pos - 4:pos], "big") if pos >= 4 else 0
        raw.append((data[pos:nxt], size))
    print(f"Đoạn video thô: {len(raw)}")

    # Bước 2: học rác riêng của file này
    markers = learn_markers(raw)
    print(f"Học được {len(markers)} marker rác")

    # Bước 3: cắt rác và ghi file
    n = 0
    removed = 0
    with open(out_path, "wb") as out:
        out.write(header)                # chỉ ghi Header 1 lần duy nhất
        for seg, size in raw:
            clean = trim_segment(seg, size, markers, use_ascii)
            removed += len(seg) - len(clean)
            if len(clean) < 4:
                continue
            # NAL đầu tiên: Header + 25 88 82 00 ... (không thêm start code
            # nếu Header đã có sẵn); các NAL sau: 00 00 01 + NAL
            if not (n == 0 and header_has_start):
                out.write(START_CODE)
            out.write(clean)
            n += 1

    print(f"Đã ghi {n} NAL vào {out_path} (bỏ khoảng {removed} byte rác/length)")


if __name__ == "__main__":
    args = sys.argv[1:]
    use_ascii = True
    if "--no-ascii" in args:             # tắt lớp 4 nếu thấy cắt nhầm
        args.remove("--no-ascii")
        use_ascii = False
    if len(args) != 5:
        print(__doc__)
        sys.exit(1)
    extract(*args, use_ascii=use_ascii)
