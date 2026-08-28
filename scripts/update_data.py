from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

SOURCE = "https://anxinletech.com/instrument-qdii.html"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CURRENT = DATA_DIR / "current.json"
HISTORY = DATA_DIR / "history.json"
CN_TZ = timezone(timedelta(hours=8))


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


def parse_row(cells: list[str], topic: str) -> dict | None:
    if len(cells) < 2:
        return None
    first = cells[0].strip()
    m = re.search(r"[（(]\s*(\d{6})\s*[）)]", first)
    if not m:
        return None
    code = m.group(1)
    name = re.sub(r"\s*[（(]\s*\d{6}\s*[）)]\s*", "", first).strip()
    status_raw = cells[1] if len(cells) > 1 else ""
    quota_text = cells[2] if len(cells) > 2 else "—"
    channel_text = cells[3] if len(cells) > 3 else "—"
    status, fund_type = normalize_status(status_raw)

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

    return {
        "index": topic,
        "code": code,
        "name": name,
        "status": status,
        "agency": agency,
        "direct": direct,
        "type": fund_type,
        "share": share_class(name, fund_type),
        "channel_note": channel_text or "—",
    }


def scrape() -> list[dict]:
    funds: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="zh-CN")
        page.goto(SOURCE, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)

        for topic in ("标普500", "纳斯达克100"):
            heading = page.locator(
                f"xpath=//*[self::h2 or self::h3 or self::h4][contains(normalize-space(), '{topic} 共')]"
            ).first
            if heading.count() == 0:
                continue
            table = heading.locator("xpath=following::table[1]")
            if table.count() == 0:
                continue
            rows = table.locator("tbody tr")
            for i in range(rows.count()):
                cells = rows.nth(i).locator("td").all_inner_texts()
                item = parse_row(cells, topic)
                if item:
                    funds.append(item)
        browser.close()

    dedup = {f["code"]: f for f in funds}
    funds = list(dedup.values())
    nas = sum(1 for f in funds if f["index"] == "纳斯达克100")
    sp = sum(1 for f in funds if f["index"] == "标普500")
    if nas < 45 or sp < 15:
        raise RuntimeError(f"渲染后解析数量异常：纳指 {nas} 只，标普 {sp} 只；停止覆盖旧数据。")
    return funds


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    funds = scrape()
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

    payload = {
        "updated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
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
    HISTORY.write_text(json.dumps(hist[-90:], ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
