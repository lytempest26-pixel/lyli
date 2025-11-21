import json
import random
from pathlib import Path
from math import ceil
from collections import defaultdict

# Seed để kết quả sampling tái lập
random.seed(42)

# Đường dẫn cố định
INPUT_FILES = [
    Path("data-review/data-reviews.json"),
]
OUTPUT_DIR = Path("data-review")

VALID_RATINGS = {1, 2, 3, 4, 5}
CAP_PER_OTHERS = 1000  # trần cho 1,2,4,5
CAP_FOR_THREE = 2000   # trần cho 3

def read_jsonl(path: Path):
    records = []
    with path.open('r', encoding='utf-8') as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                records.append(obj)
            except json.JSONDecodeError as e:
                print(f"[WARN] Bỏ qua dòng {i} trong {path.name}: JSON không hợp lệ ({e})")
    return records

def filter_records(records):
    kept = []
    removed = 0
    for r in records:
        if 'content' in r and r['content'] is not None:
            kept.append(r)
        else:
            removed += 1
    print(f"[INFO] Giữ lại: {len(kept)} | Loại bỏ: {removed}")
    return kept

def extract_rating(value):
    """Chuẩn hoá rating về int 1..5, nếu không hợp lệ thì trả None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        r = int(value)
        return r if r in VALID_RATINGS else None
    if isinstance(value, str):
        v = value.strip()
        if v.isdigit():
            r = int(v)
            return r if r in VALID_RATINGS else None
    return None

def bucket_by_rating(records):
    buckets = {r: [] for r in VALID_RATINGS}
    invalid = 0
    for rec in records:
        r = extract_rating(rec.get("rating"))
        if r is None:
            invalid += 1
            continue
        buckets[r].append(rec)
    print("[INFO] Số record thiếu/invalid rating:", invalid)
    for r in sorted(VALID_RATINGS):
        print(f"[INFO] Rating {r}: {len(buckets[r])} record hợp lệ")
    return buckets

def sample_balanced(buckets):
    """
    Cố gắng chọn cân bằng:
      - others (1,2,4,5): s <= 1000, s <= len(each bucket)
      - rating 3: 2*s, đồng thời 2*s <= len(bucket3) và <= 2000
    Nếu không tìm được s > 0 (vì thiếu dữ liệu), fallback:
      - others: min(1000, len(bucket))
      - rating 3: min(2000, len(bucket3), 2 * max(others_selected))
    """
    len1 = len(buckets[1])
    len2 = len(buckets[2])
    len3 = len(buckets[3])
    len4 = len(buckets[4])
    len5 = len(buckets[5])

    # Cố gắng tìm s cân bằng
    s_candidates = [
        CAP_PER_OTHERS,
        len1, len2, len4, len5,
        len3 // 2  # vì 3 cần gấp đôi
    ]
    s = min(s_candidates) if all(x is not None for x in s_candidates) else 0

    if s > 0:
        n1 = s
        n2 = s
        n4 = s
        n5 = s
        n3 = min(CAP_FOR_THREE, 2 * s)  # 2*s chắc chắn <= len3 theo cách chọn s
    else:
        # Fallback không cân bằng tuyệt đối nhưng “tối đa có thể”
        n1 = min(CAP_PER_OTHERS, len1)
        n2 = min(CAP_PER_OTHERS, len2)
        n4 = min(CAP_PER_OTHERS, len4)
        n5 = min(CAP_PER_OTHERS, len5)
        baseline = max(n1, n2, n4, n5)  # đảm bảo 3 >= 2 * baseline nếu có thể
        if baseline == 0:
            n3 = min(CAP_FOR_THREE, len3)
        else:
            n3 = min(CAP_FOR_THREE, len3, 2 * baseline)

    targets = {1: n1, 2: n2, 3: n3, 4: n4, 5: n5}
    print("[INFO] Kế hoạch sample (mục tiêu):", targets)

    sampled = {}
    for r in sorted(VALID_RATINGS):
        pool = buckets[r]
        k = targets[r]
        if k >= len(pool):
            sampled[r] = list(pool)  # lấy hết nếu không đủ
        else:
            sampled[r] = random.sample(pool, k)

    # In thực tế lấy được
    realized = {r: len(sampled[r]) for r in sampled}
    print("[INFO] Thực lấy:", realized)
    return sampled

def split_half(lst):
    mid = ceil(len(lst) / 2)
    return lst[:mid], lst[mid:]

def write_json_array(path: Path, data):
    with path.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[OK] Đã ghi: {path} ({len(data)} record)")

def main():
    all_records = []

    # Đọc 2 file JSONL
    for path in INPUT_FILES:
        if not path.exists():
            raise FileNotFoundError(f"Không tìm thấy file: {path}")
        print(f"[INFO] Đọc file: {path}")
        records = read_jsonl(path)
        all_records.extend(records)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("[INFO] Lọc record có content != null")
    filtered = filter_records(all_records)

    print("[INFO] Gom theo rating")
    buckets = bucket_by_rating(filtered)

    print("[INFO] Lấy mẫu theo quy tắc (1,2,4,5: <=1000; 3: <=2000 và gấp đôi các rating khác)")
    sampled = sample_balanced(buckets)

    # Sắp xếp output theo thứ tự rating 1->2->3->4->5
    ordered_ratings = [1, 2, 3, 4, 5]

    # File tổng: nối theo thứ tự rating
    all_ordered = []
    for r in ordered_ratings:
        all_ordered.extend(sampled[r])

    # Chia đôi theo rating để giữ cân bằng
    part1, part2 = [], []
    for r in ordered_ratings:
        half1, half2 = split_half(sampled[r])
        part1.extend(half1)
        part2.extend(half2)

    # Ghi file
    out_all = OUTPUT_DIR / "data-reviews.json"
    out_1 = OUTPUT_DIR / "data-review-1.json"
    out_2 = OUTPUT_DIR / "data-review-2.json"

    write_json_array(out_all, all_ordered)
    write_json_array(out_1, part1)
    write_json_array(out_2, part2)

if __name__ == "__main__":
    main()
