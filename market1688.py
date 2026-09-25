"""Read signed-in 1688 search results and visible SKU prices from headed Chrome."""

from __future__ import annotations

import csv
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright


BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "storage_state.json"
PRODUCTS_FILE = BASE_DIR / "products_1688.json"
SEARCH_JSON = BASE_DIR / "search_1688.json"
SEARCH_CSV = BASE_DIR / "search_1688.csv"
PRICE_JSON = BASE_DIR / "prices_1688.json"
PRICE_CSV = BASE_DIR / "prices_1688.csv"
MAX_OFFERS = 5
MAX_SEARCH_PAGES = 100
MONEY_RE = re.compile(r"[¥￥]\s*([\d,]+)(?:\s*(\.\s*\d+))?")
OFFER_RE = re.compile(r"(?:offer/|offerId=)(\d+)")
BLOCK_MARKERS = ("验证码", "安全验证", "滑动验证", "拖动滑块", "请完成验证", "访问异常", "请求过于频繁", "系统检测到异常")

SEARCH_FIELDS = ["keyword", "page", "total_pages", "offer_id", "title", "title_match", "card_price", "card_shipping", "url", "checked_at", "status"]
PRICE_FIELDS = [
    "offer_id", "title", "sku_id", "sku_or_variant", "price", "stock_display", "price_label",
    "preview_unit_price", "shipping", "ship_to", "quantity_checked", "url", "checked_at", "status",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_rows(rows: list[dict], json_path: Path, csv_path: Path, fields: list[str]) -> None:
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def money(text: str) -> str:
    match = MONEY_RE.search(text)
    if not match:
        return ""
    try:
        value = Decimal((match.group(1) + (match.group(2) or "")).replace(",", "").replace(" ", ""))
        return f"¥{value.normalize():f}"
    except InvalidOperation:
        return ""


def page_problem(page) -> str:
    if "login.1688.com" in page.url or "login.taobao.com" in page.url or "passport" in page.url:
        return "login_required"
    body = page.locator("body").inner_text(timeout=5000)
    if not body.strip():
        return "load_error"
    if any(marker in body for marker in BLOCK_MARKERS):
        return "verification_required"
    return ""


@contextmanager
def browser_session():
    with sync_playwright() as playwright:
        endpoint = os.environ.get("TAOBAO_CDP_URL", "").strip()
        if endpoint:
            browser = playwright.chromium.connect_over_cdp(endpoint)
            if not browser.contexts:
                raise RuntimeError("Chrome 没有可复用的浏览器上下文。")
            context = browser.contexts[0]
            attached = True
        else:
            browser = playwright.chromium.launch(channel="chrome", headless=False)
            options = {"storage_state": str(STATE_FILE)} if STATE_FILE.exists() else {}
            context = browser.new_context(**options)
            attached = False
        try:
            yield context, attached
        finally:
            if not attached:
                context.close()
                browser.close()


def search_cards(page, keyword: str, page_number: int, total_pages: int) -> list[dict]:
    cards = page.locator("a.search-offer-wrapper").evaluate_all(
        """elements => elements.map(a => ({href:a.href, text:(a.innerText||'').trim()}))"""
    )
    found = []
    for card in cards:
        match = OFFER_RE.search(card["href"])
        offer_id = match.group(1) if match else ""
        lines = [line.strip() for line in card["text"].splitlines() if line.strip()]
        title = lines[0] if lines else ""
        card_shipping = next((line for line in lines if line.startswith("运费") or line == "包邮"), "")
        found.append({
            "keyword": keyword, "page": page_number, "total_pages": total_pages, "offer_id": offer_id,
            "title": title, "title_match": all(term.lower() in title.lower() for term in keyword.split()),
            "card_price": money(card["text"]),
            "card_shipping": card_shipping,
            "url": f"https://detail.1688.com/offer/{offer_id}.html" if offer_id else card["href"],
            "checked_at": now_iso(),
            "status": "search_card_price_only" if offer_id else "redirect_link_unresolved",
        })
    return found


def search_1688(keyword: str, max_pages: int = 0) -> int:
    keyword = " ".join(keyword.split())
    if not keyword or len(keyword) > 80:
        raise ValueError("搜索词需要 1 到 80 个字符；请用简短关键词。")
    if max_pages < 0 or max_pages > MAX_SEARCH_PAGES:
        raise ValueError(f"--max-pages 只能是 0 到 {MAX_SEARCH_PAGES}；0 表示查看实际全部分页（最多 {MAX_SEARCH_PAGES} 页）。")
    rows: list[dict] = []
    with browser_session() as (context, attached):
        home = context.new_page()
        result = None
        keep_open = False
        try:
            home.goto("https://www.1688.com", wait_until="domcontentloaded", timeout=60000)
            home.wait_for_timeout(1800)
            problem = page_problem(home)
            if problem:
                keep_open = attached
                print(f"1688 首页需要处理：{problem}。浏览器标签页已保留。")
                return 1
            for attempt in range(3):
                search = home.locator('textarea[name="keywords"]:visible').first
                search.fill(keyword)
                home.wait_for_timeout(350)
                if search.input_value() == keyword:
                    break
                if attempt == 2:
                    raise RuntimeError("首页搜索框没有保留输入的关键词；已停止，避免搜索成其他词。")
            with context.expect_page(timeout=15000) as opened:
                home.locator('button[class*="searchBtn--"]:visible').first.click()
            result = opened.value
            result.wait_for_load_state("domcontentloaded", timeout=60000)
            if "s.1688.com/selloffer/offer_search" not in result.url:
                raise RuntimeError("站内搜索没有打开商品结果页；请查看浏览器窗口。")
            query = parse_qs(urlparse(result.url).query, encoding="gbk", errors="replace")
            submitted = (query.get("keywords") or [""])[0]
            if submitted != keyword:
                raise RuntimeError(f"搜索页实际关键词是“{submitted}”，与请求的“{keyword}”不同；已停止，避免记录错误结果。")
            result.locator(".pagination-container").wait_for(timeout=20000)
            result.locator("a.search-offer-wrapper").first.wait_for(timeout=20000)
            result.wait_for_timeout(3500)
            problem = page_problem(result)
            if problem:
                keep_open = attached
                print(f"搜索页需要处理：{problem}。浏览器标签页已保留。")
                return 1
            total_node = result.locator(".pagination-container .fui-paging-num")
            total = int(total_node.inner_text()) if total_node.count() else 1
            limit = min(total, max_pages or MAX_SEARCH_PAGES)
            seen = set()
            for number in range(1, limit + 1):
                if number > 1:
                    previous_cards = result.locator("a.search-offer-wrapper").evaluate_all(
                        "elements => elements.map(element => element.href).join('|')"
                    )
                    for attempt in range(2):
                        next_button = result.locator(".pagination-container .fui-next")
                        try:
                            next_button.click(timeout=5000, no_wait_after=True)
                        except PlaywrightTimeoutError:
                            # A floating result badge occasionally overlaps the
                            # page control even after Playwright scrolls to it.
                            next_button.evaluate("element => element.click()")
                        try:
                            result.wait_for_function(
                                "number => document.querySelector('.pagination-container .fui-current')?.textContent.trim() === String(number)",
                                arg=number, timeout=12000,
                            )
                            break
                        except PlaywrightTimeoutError:
                            if result.locator(".pagination-container .fui-current").inner_text().strip() == str(number):
                                break
                            if attempt:
                                raise RuntimeError(f"无法打开搜索结果第 {number} 页；已保存前面页的结果。")
                    result.locator("a.search-offer-wrapper").first.wait_for(timeout=15000)
                    result.wait_for_function(
                        "previous => [...document.querySelectorAll('a.search-offer-wrapper')].map(element => element.href).join('|') !== previous",
                        arg=previous_cards, timeout=15000,
                    )
                    # Cards can continue to arrive after the first replacement.
                    result.wait_for_timeout(3500)
                problem = page_problem(result)
                if problem:
                    keep_open = attached
                    print(f"第 {number} 页需要处理：{problem}。已保存前面页的结果。")
                    return 1
                cards = search_cards(result, keyword, number, total)
                new = [card for card in cards if (card["offer_id"] or card["url"]) not in seen]
                seen.update(card["offer_id"] or card["url"] for card in new)
                rows.extend(new)
                write_rows(rows, SEARCH_JSON, SEARCH_CSV, SEARCH_FIELDS)
                print(f"第 {number}/{total} 页：{len(cards)} 张商品卡片，新增 {len(new)} 个商品。")
            if total > limit:
                print(f"搜索结果共 {total} 页，本次按上限查看 {limit} 页；结果尚未覆盖全部分页。")
            print(f"已保存 {len(rows)} 个去重候选到 {SEARCH_JSON.name} 和 {SEARCH_CSV.name}。卡片价格仅供筛选，需进商品页核对 SKU。")
            return 0
        finally:
            if result is not None and result is not home and not keep_open and not result.is_closed():
                result.close()
            if not keep_open and not home.is_closed():
                home.close()


def load_offers() -> list[str]:
    raw = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("products_1688.json 顶层必须是链接数组。")
    urls = [value.strip() for value in raw if isinstance(value, str) and value.strip()]
    if not urls or len(urls) > MAX_OFFERS:
        raise ValueError(f"products_1688.json 需要 1 到 {MAX_OFFERS} 个商品链接。")
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "detail.1688.com" or not re.fullmatch(r"/offer/\d+\.html", parsed.path):
            raise ValueError(f"需要 1688 商品详情页链接：{url}")
    return urls


def inspect_offer(page, url: str) -> list[dict]:
    offer_id = OFFER_RE.search(url).group(1)
    title = re.sub(r"\s*[-|]\s*阿里巴巴.*$", "", page.title()).strip()
    rows = page.locator(".industry-pro-sku-selection tr[data-row-key]")
    base = {"offer_id": offer_id, "title": title, "url": f"https://detail.1688.com/offer/{offer_id}.html"}
    if not rows.count():
        return [{**base, "sku_id": "", "sku_or_variant": "", "price": "", "price_label": "", "preview_unit_price": "", "shipping": "", "ship_to": "", "quantity_checked": "", "checked_at": now_iso(), "status": "sku_layout_unsupported"}]
    ship_to = page.locator("a.recieve-address").first.inner_text().strip() if page.locator("a.recieve-address").count() else ""
    count_text = page.locator(".industry-pro-sku-selection-count").first.inner_text() if page.locator(".industry-pro-sku-selection-count").count() else ""
    expected = re.search(r"(\d+)个规格", count_text)
    incomplete = bool(expected and int(expected.group(1)) != rows.count())
    found = []
    for index in range(min(rows.count(), 50)):
        # The page keeps a selected quantity of one and its minus control may
        # refuse to return to zero. Reload between rows so each quote is isolated.
        if index:
            page.reload(wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(800)
        row = rows.nth(index)
        sku_id = row.get_attribute("data-row-key") or ""
        name = row.locator(".gyp-pro-table-title").first.inner_text().strip()
        price_spans = row.locator(".gyp-pro-table-price span")
        # The table header is "价格 | 库存". Its two adjacent spans are NOT
        # decimal fragments: e.g. <span>¥2.8</span><span>500000</span>.
        unit_price = money(price_spans.first.inner_text()) if price_spans.count() else ""
        stock_display = price_spans.nth(1).inner_text().strip() if price_spans.count() > 1 else ""
        record = {**base, "sku_id": sku_id, "sku_or_variant": name, "price": unit_price,
                  "stock_display": stock_display, "price_label": "规格表页面价", "preview_unit_price": "", "shipping": "",
                  "ship_to": ship_to, "quantity_checked": "", "checked_at": now_iso(),
                  "status": "partial_skus" if incomplete else ("ok" if unit_price else "price_not_found")}
        plus = row.locator('[aria-label="plus"]')
        quantity = row.locator('input[role="spinbutton"]')
        try:
            if plus.count() and quantity.count() and quantity.input_value() == "0":
                plus.click(timeout=5000)
                page.wait_for_function(
                    "id => document.querySelector(`tr[data-row-key='${id}'] input[role='spinbutton']`)?.value === '1'",
                    arg=sku_id, timeout=5000,
                )
                page.wait_for_timeout(1800)
                body = page.locator("body").inner_text()
                amount = re.search(r"商品金额\s*[：:]\s*([¥￥]\s*[\d,.]+)", body)
                shipping = re.search(r"另需运费\s*[：:]\s*([¥￥]\s*[\d,.]+)", body)
                record["preview_unit_price"] = money(amount.group(1)) if amount else ""
                record["shipping"] = money(shipping.group(1)) if shipping else ""
                record["quantity_checked"] = "1"
                if not shipping:
                    record["status"] = "shipping_not_found" if unit_price else "price_not_found"
                if record["preview_unit_price"] and record["preview_unit_price"] != unit_price:
                    record["status"] = "price_differs_from_preview"
            else:
                record["status"] = "quantity_probe_unavailable"
        except Exception:
            record["status"] = "quantity_probe_failed"
        record["checked_at"] = now_iso()
        found.append(record)
        problem = page_problem(page)
        if problem:
            found.append({**base, "sku_id": "", "sku_or_variant": "", "price": "", "price_label": "", "preview_unit_price": "", "shipping": "", "ship_to": ship_to, "quantity_checked": "", "checked_at": now_iso(), "status": problem})
            break
    return found


def check_1688() -> int:
    urls = load_offers()
    output = []
    with browser_session() as (context, attached):
        page = context.new_page()
        keep_open = False
        try:
            for index, url in enumerate(urls, 1):
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1000)
                problem = page_problem(page)
                if problem:
                    keep_open = attached
                    print(f"第 {index} 个商品需要处理：{problem}。页面已保留。")
                    output.append({"offer_id": OFFER_RE.search(url).group(1), "url": url, "checked_at": now_iso(), "status": problem})
                    write_rows(output, PRICE_JSON, PRICE_CSV, PRICE_FIELDS)
                    return 1
                records = inspect_offer(page, url)
                output.extend(records)
                write_rows(output, PRICE_JSON, PRICE_CSV, PRICE_FIELDS)
                print(f"第 {index}/{len(urls)} 个商品：记录 {len(records)} 个规格。")
            print(f"结果已保存到 {PRICE_JSON.name} 和 {PRICE_CSV.name}。规格表价格、选 1 件预览金额和运费分别列出。")
            return 0
        finally:
            if not keep_open and not page.is_closed():
                page.close()
