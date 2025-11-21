# Import các thư viện cần thiết để sử dụng
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import requests

# Danh sách ngành hàng cố định lấy từ crawl.py (53-61)
CATEGORIES: Dict[str, int] = {
    "book": 8322,
    "life": 1883,
    "phone": 1789,
    "mom&baby": 2549,
    "digital_accessories": 1815,
    "electricity": 1882,
    "beauty&health": 1520,
    "vehicle": 8594,
    "woman": 931,
    "department_store": 4384,
    "sport": 1975,
    "man": 915,
    "laptop": 1846,
    "shoe_man": 1686,
    "refrigeration&tv": 4221,
    "shoe_woman": 1703,
    "camera": 1801,
    "fashion_accessories": 27498,
    "watches&jewelry": 8371,
    "backpacks&suitcases": 6000,
    "fashion_bag_woman": 976,
    "fashion_bag_man": 27616,
    "house": 15078,
}

# Hai endpoint liệt kê sản phẩm; nếu endpoint đầu lỗi sẽ thử endpoint sau
LISTING_APIS = [
    "https://tiki.vn/api/personalish/v1/blocks/listings",
    "https://tiki.vn/api/v2/products",
]
REVIEW_API = "https://tiki.vn/api/v2/reviews"

# Cấu hình cố định cho script (đường dẫn output, timeout, nhịp gọi API...)
OUTPUT_PATH = Path("data/reviews.json")
REQUEST_TIMEOUT = 15
REQUEST_DELAY = 0.5  # giây nghỉ giữa các request để tránh bị chặn
LISTING_LIMIT_PER_PAGE = 40

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/119.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


class RequestError(RuntimeError):
    """Lỗi khi gọi API nhiều lần nhưng vẫn thất bại."""


def request_json(session: requests.Session, url: str, params: Dict[str, Any], retries: int = 5) -> Dict[str, Any]:
    """Gọi API JSON với retry/backoff khi bị lỗi tạm thời hoặc 429."""
    backoff = 1.0
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                raise requests.HTTPError("Quá nhiều yêu cầu", response=resp)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                return data
            raise ValueError("Payload trả về không đúng dạng dict")
        except (requests.RequestException, ValueError) as exc:
            if attempt >= retries:
                raise RequestError(f"Gọi {url} thất bại sau {retries} lần: {exc}") from exc
            time.sleep(backoff)
            backoff = min(backoff * 2, 16)
    raise RequestError("Lỗi không xác định khi gọi request_json")


def _normalize_timestamp(value: Any) -> Optional[str]:
    """Chuyển nhiều định dạng thời gian thành ISO UTC; nếu không được thì trả về raw."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
        except Exception:
            return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    except Exception:
        return s


def _extract_user(review_obj: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Chuẩn hoá thông tin người đánh giá từ nhiều biến thể field khác nhau."""
    user = (
        review_obj.get("created_by")
        or review_obj.get("customer")
        or review_obj.get("user")
        or review_obj.get("author")
    )
    rid = (
        review_obj.get("reviewer_id")
        or review_obj.get("customer_id")
        or review_obj.get("user_id")
    )
    rname = review_obj.get("reviewer_name")

    if isinstance(user, dict):
        rid = rid or user.get("id") or user.get("user_id") or user.get("customer_id")
        rname = (
            rname
            or user.get("name")
            or user.get("full_name")
            or user.get("nickname")
            or user.get("display_name")
        )

    rid_s = str(rid) if rid is not None else None
    rname_s = rname.strip() if isinstance(rname, str) else None
    return rid_s, rname_s


def _collect_urls(items: Any, prefer_key: str) -> List[str]:
    """Rút trích danh sách URL media (ảnh/video) từ list string hoặc list dict."""
    if not isinstance(items, list):
        return []
    urls: List[str] = []
    for item in items:
        if isinstance(item, str):
            urls.append(item)
        elif isinstance(item, dict):
            val = item.get(prefer_key) or item.get("full_path") or item.get("url")
            if isinstance(val, str):
                urls.append(val)
    return urls


def _collect_variant(review_obj: Dict[str, Any]) -> Dict[str, Any]:
    """Chuẩn hoá thông tin biến thể/thuộc tính của sản phẩm trong review thành dict."""
    variant = (
        review_obj.get("attribute_summary")
        or review_obj.get("variant")
        or review_obj.get("options")
        or review_obj.get("attributes")
        or review_obj.get("contribute_info")
    )

    if isinstance(variant, dict):
        return variant

    if isinstance(variant, list):
        out: Dict[str, Any] = {}
        notes: List[str] = []
        for item in variant:
            if isinstance(item, dict):
                key = item.get("name") or item.get("key")
                val = item.get("value") or item.get("val") or item.get("text")
                if isinstance(key, str) and val is not None:
                    out[key.strip()] = str(val).strip()
                else:
                    notes.append(str(item))
            elif isinstance(item, str):
                text = item.strip()
                if not text:
                    continue
                if ":" in text:
                    k, v = text.split(":", 1)
                    out[k.strip()] = v.strip()
                else:
                    notes.append(text)
        if notes and not out:
            return {"_notes": "; ".join(notes)}
        if notes:
            out["_notes"] = "; ".join(notes)
        return out or {"_raw": variant}

    if isinstance(variant, str):
        out: Dict[str, Any] = {}
        notes: List[str] = []
        for seg in variant.split(";"):
            seg = seg.strip()
            if not seg:
                continue
            if ":" in seg:
                k, v = seg.split(":", 1)
                out[k.strip()] = v.strip()
            else:
                notes.append(seg)
        if notes and not out:
            return {"_notes": "; ".join(notes)}
        if notes:
            out["_notes"] = "; ".join(notes)
        return out or {"_raw": variant}

    if variant is None:
        return {}

    try:
        return {"_raw": str(variant)}
    except Exception:
        return {"_raw": "UNKNOWN"}


def _standardize_product(raw: Dict[str, Any], category_id: int, category_slug: str) -> Optional[Dict[str, Any]]:
    """Chuẩn hoá object sản phẩm trả về từ API listing về dạng tối thiểu cần dùng."""
    pid = raw.get("id") or raw.get("product_id")
    if not pid:
        product = raw.get("product") if isinstance(raw.get("product"), dict) else None
        if product:
            pid = product.get("id") or product.get("product_id")
            raw = product
    if not pid:
        return None
    name = raw.get("name") or raw.get("title") or ""
    url = raw.get("url_path") or raw.get("url")
    if not url:
        url = f"/p{pid}.html"
    full_url = url if str(url).startswith("http") else f"https://tiki.vn{url}"
    return {
        "id": int(pid),
        "name": name,
        "url": full_url,
        "category_id": category_id,
        "category_slug": category_slug,
    }


def fetch_products_for_category(
    session: requests.Session,
    category_id: int,
    category_slug: str,
    max_products: Optional[int] = None,
) -> Iterable[Dict[str, Any]]:
    """Yield lần lượt các sản phẩm của một ngành hàng bằng cách thử từng API listing."""
    seen_ids: set[int] = set()
    page = 1
    total = 0
    while True:
        payload: Optional[Dict[str, Any]] = None
        items: List[Dict[str, Any]] = []
        for base in LISTING_APIS:
            params = {"limit": LISTING_LIMIT_PER_PAGE, "category": category_id, "page": page}
            try:
                payload = request_json(session, base, params)
            except RequestError:
                continue
            if isinstance(payload, dict):
                data = payload.get("data")
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict) and isinstance(data.get("items"), list):
                    items = data["items"]
                elif isinstance(payload.get("items"), list):
                    items = payload["items"]
                else:
                    items = []
            if items:
                break
        if not items:
            break

        for raw in items:
            product = _standardize_product(raw, category_id, category_slug)
            if not product:
                continue
            if product["id"] in seen_ids:
                continue
            seen_ids.add(product["id"])
            yield product
            total += 1
            if max_products and total >= max_products:
                return

        paging = {}
        if isinstance(payload, dict):
            paging = payload.get("paging") or payload.get("pagination") or {}
        next_page = paging.get("next_page")
        last_page = paging.get("last_page")
        current_page = paging.get("current_page") or page
        try:
            current_page = int(current_page)
        except (TypeError, ValueError):
            current_page = page
        try:
            next_page = int(next_page) if next_page is not None else None
        except (TypeError, ValueError):
            next_page = None
        try:
            last_page = int(last_page) if last_page is not None else None
        except (TypeError, ValueError):
            last_page = None

        if max_products and total >= max_products:
            break
        if last_page and current_page >= last_page:
            break
        if next_page and next_page != current_page:
            page = next_page
        else:
            page = current_page + 1

        time.sleep(REQUEST_DELAY)


def build_review_dict(product: Dict[str, Any], review: Dict[str, Any]) -> Dict[str, Any]:
    """Gộp dữ liệu review thô + metadata sản phẩm thành dict sẵn sàng để ghi JSON."""
    product_id = product["id"]
    review_id = (
        review.get("id")
        or review.get("review_id")
        or f"{product_id}-{review.get('created_at')}-{hash(json.dumps(review, sort_keys=True))}"
    )
    created_at = review.get("created_at") or review.get("created_time") or review.get("created")
    reply = review.get("reply") if isinstance(review.get("reply"), dict) else None
    customer_id, customer_name = _extract_user(review)
    images = _collect_urls(review.get("images"), "full_path")
    videos = _collect_urls(review.get("videos"), "url")
    variant = _collect_variant(review)

    return {
        "review_id": str(review_id),
        "product_id": product_id,
        "product_name": product.get("name", ""),
        "category_id": product.get("category_id"),
        "category_slug": product.get("category_slug"),
        "rating": review.get("rating") or review.get("rating_value"),
        "title": review.get("title"),
        "content": review.get("content") or review.get("body") or review.get("text"),
        "pros": review.get("pros"),
        "cons": review.get("cons"),
        "images": json.dumps(images, ensure_ascii=False),
        "videos": json.dumps(videos, ensure_ascii=False),
        "created_at": _normalize_timestamp(created_at),
        "buyer_verified": 1 if (
            review.get("buyer_verified")
            or review.get("is_buyer")
            or review.get("is_purchased")
        ) else 0,
        "helpful_count": review.get("thank_count") or review.get("helpful_count") or 0,
        "like_count": review.get("like_count") or review.get("likes"),
        "dislike_count": review.get("dislike_count"),
        "reply_from_seller": reply.get("content") if reply else None,
        "reply_time": _normalize_timestamp(
            reply.get("created_at") or reply.get("created_time") if reply else None
        ),
        "variant": json.dumps(variant, ensure_ascii=False),
        "reviewer_id": customer_id,
        "reviewer_name": customer_name,
    }


def fetch_reviews_for_product(
    session: requests.Session,
    product: Dict[str, Any],
    delay: float = REQUEST_DELAY,
) -> Iterable[Dict[str, Any]]:
    """Lấy tuần tự từng trang review cho một sản phẩm; yield từng review dạng dict."""
    page = 1
    seen_keys: set[Tuple[str, Optional[str], str]] = set()
    while True:
        params = {
            "product_id": product["id"],
            "page": page,
            "limit": 20,
            "include": "comments,contribute_info",
        }
        try:
            payload = request_json(session, REVIEW_API, params)
        except RequestError as exc:
            print(f"[LỖI] Không lấy được review cho sản phẩm {product['id']}: {exc}")
            return
        reviews = payload.get("data") or []
        if not reviews:
            break

        for review in reviews:
            row = build_review_dict(product, review)
            dedup_key = (row["review_id"], row["created_at"], (row.get("content") or "")[:100])
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            yield row

        paging = payload.get("paging") or {}
        current_page = paging.get("current_page") or page
        next_page = paging.get("next_page")
        last_page = paging.get("last_page")
        try:
            current_page = int(current_page)
        except (TypeError, ValueError):
            current_page = page
        try:
            next_page = int(next_page) if next_page is not None else None
        except (TypeError, ValueError):
            next_page = None
        try:
            last_page = int(last_page) if last_page is not None else None
        except (TypeError, ValueError):
            last_page = None

        if last_page and current_page >= last_page:
            break
        if next_page and next_page != current_page:
            page = next_page
        else:
            page = current_page + 1

        time.sleep(delay)


def run() -> None:
    """Điểm vào chính: duyệt ngành hàng → sản phẩm → review và ghi JSON dần dần."""
    print("[THÔNG BÁO] Bắt đầu crawl review cho danh sách ngành hàng cố định.")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    total_products = 0
    total_reviews = 0

    # Mở file ở chế độ ghi mới và bắt đầu mảng JSON
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        f.write("[\n")
        first_written = False

        try:
            for slug, cid in CATEGORIES.items():
                print(f"[THÔNG BÁO] Đang lấy danh sách sản phẩm cho ngành hàng '{slug}' (ID {cid}).")
                try:
                    products = list(fetch_products_for_category(session, cid, slug))
                except RequestError as exc:
                    print(f"[LỖI] Không lấy được sản phẩm cho ngành hàng {slug}: {exc}")
                    continue

                if not products:
                    print(f"[CẢNH BÁO] Không tìm thấy sản phẩm nào cho ngành hàng {slug}.")
                    continue

                print(
                    f"[THÔNG BÁO] Ngành hàng '{slug}' có {len(products)} sản phẩm sẽ được xử lý."
                )

                for product in products:
                    total_products += 1
                    print(
                        f"[THÔNG BÁO] → Đang lấy review cho sản phẩm ID {product['id']} - {product['name']}"
                    )

                    product_count = 0
                    # Ghi lần lượt từng review; đảm bảo dấu phẩy đúng chuẩn JSON
                    for row in fetch_reviews_for_product(session, product):
                        formatted = json.dumps(row, ensure_ascii=False, indent=2)
                        indented = "  " + formatted.replace("\n", "\n  ")
                        if first_written:
                            f.write(",\n")
                        else:
                            first_written = True
                        f.write(indented)
                        product_count += 1
                        total_reviews += 1

                    print(
                        f"[THÔNG BÁO] ← Hoàn thành sản phẩm {product['id']} với {product_count} review mới."
                    )
        finally:
            session.close()
            # Kết thúc mảng JSON hợp lệ
            f.write("\n]\n")

    if total_reviews == 0:
        print("[CẢNH BÁO] Không thu được review nào.")
    else:
        print(
            f"[HOÀN TẤT] Đã crawl {total_reviews} review từ {total_products} sản phẩm. "
            f"Lưu dữ liệu tại '{OUTPUT_PATH.resolve()}'."
        )


if __name__ == "__main__":
    run()