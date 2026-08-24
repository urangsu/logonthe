import re
from typing import Optional


def parse_compact_count(raw: Optional[str]) -> Optional[int]:
    """
    네이버 블로그의 축약형 숫자 표기를 정수(int)로 파싱합니다.
    예:
      - "0", "12", "999", "999+" -> 0, 12, 999, 999
      - "1,234", "10,000" -> 1234, 10000
      - "1천", "1.2천", "9.9천" -> 1000, 1200, 9900
      - "1만", "1.2만", "1.5만" -> 10000, 12000, 15000
      - "10K", "1.2K", "1.2M" -> 10000, 1200, 1200000
    """
    if not raw:
        return None

    clean = raw.strip().replace(",", "")

    # "공감 999", "좋아요 1.2천", "오늘 1,234" 등 앞뒤 텍스트 제거
    match = re.search(r"(\d+(?:\.\d+)?)\s*([천만KMkm\+]?)", clean)
    if not match:
        return None

    num_str = match.group(1)
    unit = match.group(2)

    try:
        val = float(num_str)
    except ValueError:
        return None

    if unit == "천":
        return int(val * 1000)
    elif unit == "만":
        return int(val * 10000)
    elif unit in ("k", "K"):
        return int(val * 1000)
    elif unit in ("m", "M"):
        return int(val * 1000000)
    elif unit == "+":
        return int(val)
    else:
        return int(val)
