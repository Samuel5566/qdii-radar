from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SOURCE = "https://anxinletech.com/instrument-qdii.html"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CURRENT = DATA_DIR / "current.json"
HISTORY = DATA_DIR / "history.json"
CN_TZ = timezone(timedelta(hours=8))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
}


def amount_num(text: str) -> int:
    text = (text or "").replace(",", "").replace(" ", "")
    m = re.search(r"([\d.]+)万元", text)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r"([\d.]+)元", text)
    return int(float(m.group(1))) if m else 0


def share_class(name: str, fund_type: str) -> str:
    if fund_type == "场内ETF":
        return "ETF"
    for cls in ("A", "C", "D", "E", "F", "I"):
        if re.search(rf"{cls}(?:人民币)?(?:$|\))", name) or f"{cls}人民币" in name:
            return cls
    return "—"


def normalize_status(text: str) -> tuple[str, str]:
    if "场内交易" in text:
        return "场内", "场内ETF"
    if "暂停申购" in text:
        return "暂停", "场外"
    if "开放申购" in text:
        return "开放", "场外"
    return "限购", "场外"


def parse_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    parts = [x.strip() for x in soup.stripped_strings if x and x.strip()]

    funds: list[dict] = []
    topic = None
    wanted_topics = {"纳斯达克100", "标普500"}

    for i, text in enumerate(parts):
        if "标普500 共" in text:
            topic = "标普500"
            continue
        if "纳斯达克100 共" in text:
            topic = "纳斯达克100"
            continue
        if topic not in wanted_topics:
            continue

        code_match = re.fullmatch(r"[（(]\s*(\d{6})\s*[）)]", text)
        embedded_match = re.search(r"[（(]\s*(\d{6})\s*[）)]", text)
        if not code_match and not embedded_match:
            continue

        code = (code_match or embedded_match).group(1)
        if embedded_match and not code_match:
            name = re.sub(r"\s*[（(]\s*\d{6}\s*[）)]\s*", "", text).strip()
        else:
            # In many generated pages, the fund name and （code） are separate text nodes.
            name = parts[i - 1].strip() if i > 0 else code

        look = parts[i + 1 : i + 12]
        status_text = next((x for x in look if any(k in x for k in ("限大额", "暂停申购", "场内交易", "开放申购"))), "限大额")
        status, fund_type = normalize_status(status_text)

        # Locate quota/channel strings near the status cell.
        status_pos = look.index(status_text) if status_text in look else 0
        tail = look[status_pos + 1 : status_pos + 8]
        quota_text = next((x for x in tail if "元" in x or x == "—"), "—")
        channel_text = "—"
        if quota_text in tail:
            qpos = tail.index(quota_text)
            for x in tail[qpos + 1 : qpos + 4]:
                if "公告" not in x and not re.match(r"\d{4}-\d{2}-\d{2}", x):
                    channel_text = x
                    break

        agency = direct = 0
        if fund_type == "场外" and status != "暂停":
            ma = re.search(r"代销\s*([\d.,]+(?:万)?元)", quota_text)
            md = re.search(r"直销\s*([\d.,]+(?:万)?元)", quota_text)
            if ma:
                agency = amount_num(ma.group(1))
            if md:
                direct = amount_num(md.group(1))
            if not ma and not md:
                val = amount_num(quota_text)
                if "直销" in channel_text and "代销" not in channel_text:
                    direct = val
                else:
                    agency = val

        funds.append(
            {
                "index": topic,
                "code": code,
                "name": name,
                "status": status,
                "agency": agency,
                "direct": direct,
                "type": fund_type,
                "share": share_class(name, fund_type),
                "channel_note": channel_text,
            }
        )

    # Remove accidental duplicates while preserving order.
    dedup: dict[str, dict] = {}
    for f in funds:
        dedup[f["code"]] = f
    funds = list(dedup.values())

    nas = sum(1 for f in funds if f["index"] == "纳斯达克100")
    sp = sum(1 for f in funds if f["index"] == "标普500")
    if nas < 45 or sp < 15:
        raise RuntimeError(f"解析数量异常：纳指 {nas} 只，标普 {sp} 只；停止覆盖旧数据。")
    return funds


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    r = requests.get(SOURCE, headers=HEADERS, timeout=30)
    r.raise_for_status()
    funds = parse_page(r.text)

    previous = load_json(CURRENT, {})
    old_map = {x["code"]: x for x in previous.get("funds", [])}

    changes = []
    for f in funds:
        old = old_map.get(f["code"])
        if not old:
            f["change"] = "new"
            continue
        old_max = max(old.get("agency", 0), old.get("direct", 0))
        new_max = max(f.get("agency", 0), f.get("direct", 0))
        if old.get("status") != f.get("status"):
            f["change"] = "status"
        elif new_max > old_max:
            f["change"] = "up"
        elif new_max < old_max:
            f["change"] = "down"
        else:
            f["change"] = "same"
        if f["change"] in {"up", "down", "status"}:
            changes.append({"code": f["code"], "name": f["name"], "change": f["change"]})

    now = datetime.now(CN_TZ).isoformat(timespec="seconds")
    payload = {
        "updated_at": now,
        "source": SOURCE,
        "summary": {
            "total": len(funds),
            "nasdaq100": sum(1 for f in funds if f["index"] == "纳斯达克100"),
            "sp500": sum(1 for f in funds if f["index"] == "标普500"),
            "otc_buyable": sum(1 for f in funds if f["type"] == "场外" and f["status"] in {"限购", "开放"}),
            "changes": len(changes),
        },
        "changes": changes,
        "funds": funds,
    }

    CURRENT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")

    hist = load_json(HISTORY, [])
    hist.append(payload)
    hist = hist[-90:]
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=2), "utf-8")

    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
