# Script này để gộp data đã check từ 2 file json và lọc bỏ những review có rating == 0
# Sau đó lưu và thống kê số lượng lại
import json
from collections import Counter

# --- Các đường dẫn file ---
file_paths = ["data-review/data-review-1.json", "data-review/data-review-2.json"]
output_file = "data-review/merged_reviews.json"

# --- Đọc và gộp dữ liệu ---
merged_data = []
for path in file_paths:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        merged_data.extend(data)

# --- Lọc bỏ những review có rating == 0 ---
filtered_data = [r for r in merged_data if r.get("rating", 0) != 0]

# --- Ghi ra file tổng hợp ---
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(filtered_data, f, ensure_ascii=False, indent=2)

# --- Thống kê số lượng mẫu theo rating ---
rating_counts = Counter(r["rating"] for r in filtered_data)
print("📊 Thống kê số lượng review theo rating:")
for rating, count in sorted(rating_counts.items(), reverse=True):
    print(f"⭐ {rating} sao: {count} mẫu")

print(f"\n✅ Đã lưu file tổng hợp vào: {output_file}")
print(f"➡️ Tổng số review sau khi lọc: {len(filtered_data)}")
