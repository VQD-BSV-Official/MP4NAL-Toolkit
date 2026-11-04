#!/usr/bin/env python3
"""
extract_idr.py - Chỉ trích xuất IDR frame từ file MP4 hỏng.
Mỗi IDR frame ghi ra một file: Header + IDR frame  ->  IDR_0001.h264, IDR_0002.h264, ...

Cách dùng:
    python extract_idr.py broken.mp4 Header Log.txt [thu_muc_ra] [-n SO_LUONG] [--no-ascii]

    -n SO_LUONG   (hoặc --max SO_LUONG) chỉ trích xuất SO_LUONG IDR frame đầu tiên.
                  Không điền thì trích xuất hết.
    Ví dụ: python extract_idr.py broken.mp4 Header Log.txt out -n 2

Log.txt là 1 file duy nhất do mp4scan in ra, chứa nhiều track:
    === Track 0: vide / avc1 ... ===   -> chữ ký video
    === Track 1: soun / mp4a ... ===   -> chữ ký audio (PCM thì không có dòng nào)
    === Track 2: meta / rtmd ... ===   -> bỏ qua
Log không có dòng "=== Track" thì coi toàn bộ là video.

Một IDR frame gồm:
    - slice đầu: cụm có nhãn "IDR frame" trong log (vd 25 88 40 01)
    - các slice tiếp theo: cụm có nhãn "IDR slice" (vd 25 00 1E 02, 25 00 0F 78, ...)
Frame kết thúc khi gặp NAL video không thuộc IDR, hoặc khi gặp slice đầu của frame IDR mới.

Cấu trúc mỗi file:
    [Header] 25 88 40 01 ...data
    00 00 01 25 00 1E 02 ...data
    00 00 01 25 00 0F 78 ...data
"""
import mmap
import os
import re
import sys
from collections import Counter

START_CODE = b"\x00\x00\x01"
LOG_RE = re.compile(r"\[((?:[0-9A-Fa-f]{2}\s?){4})\]\s+(\d+)\s*(.*)")
TRACK_RE = re.compile(r"^\s*=+\s*Track\s+\d+\s*:\s*(\w+)\s*/\s*(\w+)")

BAD_SEQ = re.compile(rb"\x00\x00[\x00-\x02]")      # không thể có trong NAL hợp lệ
ASCII_RUN = re.compile(rb"[\x20-\x7E]{24,}")       # rác ASCII dài

MAX_NAL = 16 << 20          # chặn đọc quá 16 MB cho 1 NAL (tránh đọc tới hết file khi hỏng)
MAX_LEARN = 3000            # số đoạn tối đa dùng để học marker rác


# ---------------------------------------------------------------- log
def load_log(path):
    """Đọc 1 log duy nhất -> (video_items, audio_items), mỗi item là (sig bytes, nhãn)."""
    video, audio = [], []
    cur = video                                     # log không có tiêu đề track: coi là video
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            h = TRACK_RE.match(line)
            if h:
                handler = h.group(1).lower()
                cur = video if handler == "vide" else audio if handler == "soun" else None
                continue
            if cur is None:
                continue                            # track meta/khác: bỏ qua
            m = LOG_RE.search(line)
            if m:
                sig = bytes.fromhex(m.group(1).replace(" ", ""))
                cur.append((sig, m.group(3).strip()))
    return video, audio


def find_events(data, video_sigs, audio_sigs):
    """List (vị trí, 'V'|'A', sig) đã sắp xếp, quét cả match chồng lấn."""
    pats = {s: "V" for s in video_sigs}
    for s in audio_sigs:
        pats.setdefault(s, "A")                     # trùng thì ưu tiên video
    rx = re.compile(
        b"(?=(" + b"|".join(re.escape(p) for p in pats) + b"))", re.DOTALL
    )
    return [(m.start(), pats[m.group(1)], m.group(1)) for m in rx.finditer(data)]


# ---------------------------------------------------------------- lọc rác
def base_trim(seg, size):
    """Cắt theo độ dài AVCC và chuỗi cấm 00 00 00/01/02."""
    if 0 < size <= len(seg):
        seg = seg[:size]
    m = BAD_SEQ.search(seg, 1)
    if m:
        seg = seg[:m.start()]
    return seg


def learn_markers(get_seg, count, n=8, tail=256, min_ratio=0.3):
    """Học marker rác lặp ở đuôi nhiều đoạn (rác riêng của từng file).
    Chỉ lấy mẫu tối đa MAX_LEARN đoạn, rải đều, để file lớn không tốn RAM."""
    step = max(1, count // MAX_LEARN)
    picked = range(0, count, step)
    cnt = Counter()
    for i in picked:
        seg, size = get_seg(i)
        s = base_trim(seg, size)[-tail:]
        cnt.update({s[k:k + n] for k in range(len(s) - n + 1)})
    need = max(5, int(len(picked) * min_ratio))
    return [p for p, c in cnt.items() if c >= need and len(set(p)) > 2]


def trim_segment(seg, size, markers, use_ascii=True):
    seg = base_trim(seg, size)
    cut = len(seg)
    for mk in markers:
        i = seg.find(mk, 1, cut)
        if i != -1:
            cut = i
    if use_ascii:
        m = ASCII_RUN.search(seg, 4, cut)
        if m:
            cut = m.start()
    return seg[:cut]


# ---------------------------------------------------------------- chính
def extract_idr(mp4_path, header_path, log_path, out_dir=".", use_ascii=True, max_frames=None):
    vitems, aitems = load_log(log_path)
    video_sigs = {s for s, _ in vitems}
    audio_sigs = {s for s, _ in aitems}
    print(f"Log: {len(video_sigs)} chữ ký video, {len(audio_sigs)} chữ ký audio")
    if not audio_sigs:
        print("  (audio không có chữ ký, ví dụ PCM: chỉ dùng chữ ký video làm mốc)")

    # Phân loại cụm IDR theo nhãn trong log (NAL type 5 = byte đầu & 0x1F == 5)
    idr_sigs = {s for s, lb in vitems if "IDR" in lb.upper() and (s[0] & 0x1F) == 5}
    start_sigs = {s for s, lb in vitems if "IDR FRAME" in lb.upper() and s in idr_sigs}
    print(f"IDR signatures: {len(idr_sigs)} (slice đầu: {len(start_sigs)})")
    if not idr_sigs:
        print("Log video không có cụm IDR nào.")
        return

    with open(header_path, "rb") as f:
        header = f.read()
    header_has_start = header.endswith(START_CODE)

    with open(mp4_path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        events = find_events(data, video_sigs, audio_sigs)
        print(f"Tìm thấy {len(events)} mốc")

        # Bước 1: ghi nhớ vị trí/độ dài của mọi NAL video (không giữ dữ liệu trong RAM)
        raw_all = []          # (pos, length, size)
        index = {}            # chỉ số event -> chỉ số trong raw_all
        for i, (pos, kind, sig) in enumerate(events):
            if kind != "V":
                continue
            nxt = events[i + 1][0] if i + 1 < len(events) else len(data)
            size = int.from_bytes(data[pos - 4:pos], "big") if pos >= 4 else 0
            length = nxt - pos
            if 0 < size <= length:
                length = size
            index[i] = len(raw_all)
            raw_all.append((pos, min(length, MAX_NAL), size))

        def get_seg(k):
            pos, length, size = raw_all[k]
            return data[pos:pos + length], size

        markers = learn_markers(get_seg, len(raw_all))
        print(f"Học được {len(markers)} marker rác")

        # Bước 2: gom slice IDR thành frame và ghi file ngay khi frame kết thúc
        os.makedirs(out_dir, exist_ok=True)
        cur, cur_sigs, written = [], set(), 0

        def close():
            nonlocal cur, cur_sigs, written
            if cur:
                written += 1
                path = os.path.join(out_dir, f"IDR_{written:04d}.h264")
                with open(path, "wb") as out:
                    out.write(header)
                    for j, nal in enumerate(cur):
                        if not (j == 0 and header_has_start):
                            out.write(START_CODE)
                        out.write(nal)
                print(f"  {path}: {len(cur)} NAL, {sum(map(len, cur))} byte")
            cur, cur_sigs = [], set()

        for i, (pos, kind, sig) in enumerate(events):
            if max_frames and written >= max_frames:
                break                      # đã đủ số frame yêu cầu
            if kind != "V":
                continue                   # audio xen giữa: bỏ qua, không cắt frame
            if sig not in idr_sigs:
                close()                    # gặp NAL video khác -> hết frame IDR
                continue
            seg, size = get_seg(index[i])
            nal = trim_segment(seg, size, markers, use_ascii)
            if len(nal) < 4 or (nal[0] & 0x1F) != 5:
                continue
            # bắt đầu frame mới: slice đầu (theo log) hoặc sig đã có trong frame hiện tại
            new_frame = (sig in start_sigs) if start_sigs else (sig in cur_sigs)
            if new_frame:
                close()
            cur.append(nal)
            cur_sigs.add(sig)
        close()                            # sau khi đủ số lượng thì cur đã rỗng -> không ghi thêm

    print(f"Đã ghi {written} IDR frame vào '{out_dir}'")


if __name__ == "__main__":
    args = sys.argv[1:]
    use_ascii = True
    if "--no-ascii" in args:
        args.remove("--no-ascii")
        use_ascii = False
    max_frames = None
    for flag in ("-n", "--max"):
        if flag in args:
            k = args.index(flag)
            try:
                max_frames = int(args[k + 1])
                if max_frames <= 0:
                    raise ValueError
            except (IndexError, ValueError):
                print(f"Sau {flag} phải là một số nguyên dương.")
                sys.exit(1)
            del args[k:k + 2]
    if len(args) not in (3, 4):
        print(__doc__)
        sys.exit(1)
    extract_idr(*args, use_ascii=use_ascii, max_frames=max_frames)
