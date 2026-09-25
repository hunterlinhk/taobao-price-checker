"""Read visible Taobao prices or refresh one existing merchant chat tab.

Price checks use a headed Chrome/Chromium session and the rendered DOM.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
PRODUCTS_FILE = BASE_DIR / "products.json"
STATE_FILE = BASE_DIR / "storage_state.json"
JSON_FILE = BASE_DIR / "output.json"
CSV_FILE = BASE_DIR / "output.csv"
MAX_PRODUCTS = 5
MAX_SKUS_PER_PRODUCT = 50
CHAT_REFRESH_SECONDS = 60
CHAT_WATCH_MINUTES = 30

CSV_FIELDS = [
    "title", "price", "price_label", "sku_or_variant", "quantity",
    "shipping", "shipping_label", "shipping_status", "ship_to",
    "promotion_text", "url", "checked_at", "status",
]
SKU_GROUP_SELECTOR = '[class*="skuItem--"]'
SKU_OPTION_SELECTOR = '[class*="valueItem--"][data-vid]'

# Try several page structures. Keep these selectors easy to adjust when Taobao
# changes its rendered markup. Every candidate is checked for visibility.
PRICE_SELECTORS = [
    '[data-testid*="price" i]',
    '[aria-label*="价格"]',
    ".tb-rmb-num",
    'span[class*="price" i]',
    'span[class*="Price"]',
    "strong",
    "span",
    '[class*="price" i]',
]
TITLE_SELECTORS = [
    'h1:visible',
    '[data-testid*="title" i]:visible',
    '[class*="title" i]:visible',
    "#J_Title:visible",
]
SKU_CONTAINER_SELECTORS = [
    '[data-testid*="sku" i] [aria-checked="true"]',
    '[data-testid*="sku" i] [aria-selected="true"]',
    '[class*="sku" i] [aria-checked="true"]',
    '[class*="sku" i] [aria-selected="true"]',
    '[class*="sku" i] [class*="selected" i]',
    '[class*="sku" i] [class*="active" i]',
    '[class*="prop" i] [class*="selected" i]',
    '[class*="prop" i] [class*="active" i]',
]

PRICE_RE = re.compile(
    r"(?:¥|￥)\s*\d[\d,]*(?:\.\d{1,2})?"
    r"(?:\s*[-—至~～]\s*(?:¥|￥)?\s*\d[\d,]*(?:\.\d{1,2})?)?"
)

CHALLENGE_MARKERS = (
    "验证码",
    "安全验证",
    "拖动滑块",
    "滑动验证",
    "请完成验证",
    "captcha",
)
RISK_MARKERS = (
    "访问异常",
    "当前访问存在风险",
    "行为异常",
    "系统检测到异常",
    "请求过于频繁",
    "为了保障您的账户安全",
)
LOGIN_MARKERS = (
    "登录已过期",
    "请重新登录",
    "登录后继续",
    "账号已退出",
)


def load_products() -> list[str]:
    try:
        raw = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"找不到 {PRODUCTS_FILE.name}，请先创建商品链接列表。")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{PRODUCTS_FILE.name} 不是有效 JSON：{exc}") from exc

    if not isinstance(raw, list):
        raise ValueError("products.json 顶层必须是 URL 字符串数组。")
    urls = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    if not urls:
        raise ValueError("请先在 products.json 中填写至少一个淘宝商品链接。")
    if len(urls) > MAX_PRODUCTS:
        raise ValueError(f"此脚本最多处理 {MAX_PRODUCTS} 个商品链接。")
    for url in urls:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not (
            host == "taobao.com"
            or host.endswith(".taobao.com")
            or host == "tmall.com"
            or host.endswith(".tmall.com")
            or host == "tb.cn"
            or host.endswith(".tb.cn")
        ):
            raise ValueError(f"只接受淘宝/天猫商品链接：{url}")
    return urls


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def product_url_without_sku(url: str) -> str:
    """Keep all-SKU results from linking every row to the input's initial SKU."""
    parsed = urlparse(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key.lower() != "skuid"]
    return urlunparse(parsed._replace(query=urlencode(query)))


def visible_text(locator, limit: int = 30) -> list[tuple[str, bool]]:
    """Return text plus a line-through flag for visible matching elements."""
    found: list[tuple[str, bool]] = []
    try:
        count = min(locator.count(), limit)
        for index in range(count):
            element = locator.nth(index)
            if not element.is_visible():
                continue
            text = " ".join(element.inner_text(timeout=800).split())
            if not text:
                continue
            struck = element.evaluate(
                "el => { for (let n = el; n && n !== document.body; n = n.parentElement) "
                "{ if (getComputedStyle(n).textDecorationLine.includes('line-through')) return true; } "
                "return false; }"
            )
            found.append((text, bool(struck)))
    except Exception:
        return found
    return found


def read_title(page) -> str:
    try:
        tab_title = page.title().strip()
        # Taobao's tab title usually contains the product name; remove only its
        # standard site suffix. Ignore generic section titles such as “参数”.
        tab_title = re.sub(r"\s*[-|_]\s*(?:淘宝网|天猫)\s*$", "", tab_title)
        if 2 <= len(tab_title) <= 240 and tab_title not in {"参数", "商品详情", "宝贝详情"}:
            return tab_title
    except Exception:
        pass

    for selector in TITLE_SELECTORS:
        for text, _ in visible_text(page.locator(selector), limit=8):
            if 2 <= len(text) <= 240 and text not in {"参数", "商品详情", "宝贝详情"}:
                return text
    try:
        return page.title().strip()
    except Exception:
        return ""


def read_price_details(page) -> tuple[str, str]:
    # The current Taobao detail panel separates the highlighted payable display
    # from its "before discount" comparison price. Prefer the highlighted block.
    for selector in (
        '#SkuPanel_tbpcDetail_ssr2025 [class*="highlightPrice--"]',
        '[class*="normalPrice--"] [class*="highlightPrice--"]',
    ):
        for text, struck in visible_text(page.locator(selector), limit=5):
            if not struck:
                match = PRICE_RE.search(text)
                if match:
                    label = text[:match.start()].strip() or "页面标价"
                    return match.group(0).strip(), label

    for selector in PRICE_SELECTORS:
        for text, struck in visible_text(page.locator(selector), limit=40):
            if struck:
                continue
            match = PRICE_RE.search(text)
            if match:
                return match.group(0).strip(), "页面可见价格（待核对）"
    return "", ""


def read_price(page) -> str:
    return read_price_details(page)[0]


def _read_taobao_delivery(page) -> dict[str, str]:
    """Read the visible product-page delivery quote without changing quantity."""
    panel = page.locator("#SkuPanel_tbpcDetail_ssr2025")
    freight = panel.locator('[class*="freight--"]').first
    label = freight.inner_text(timeout=1200).strip() if freight.count() and freight.is_visible() else ""
    if "免运费" in label or "包邮" in label:
        shipping = "￥0"
    else:
        match = PRICE_RE.search(label)
        shipping = match.group(0).strip() if match else ""
    location = panel.locator('[class*="deliveryAddrWrap--"]').first
    ship_to = " ".join(location.inner_text(timeout=1200).split()) if location.count() and location.is_visible() else ""
    count = panel.locator('input[class*="countValue--"]').first
    quantity = count.input_value(timeout=1200).strip() if count.count() and count.is_visible() else ""
    try:
        panel_text = panel.inner_text(timeout=1500)
        promotion_match = re.search(r"券满\s*\d+(?:\.\d+)?\s*减\s*\d+(?:\.\d+)?", panel_text)
        promotion = "".join(promotion_match.group(0).split()) if promotion_match else ""
    except Exception:
        promotion = ""
    return {
        "quantity": quantity,
        "shipping": shipping,
        "shipping_label": label,
        "shipping_status": "ok" if shipping else "not_found",
        "ship_to": ship_to,
        "promotion_text": promotion,
    }


def read_taobao_delivery(page) -> dict[str, str]:
    try:
        return _read_taobao_delivery(page)
    except Exception:
        return {
            "quantity": "", "shipping": "", "shipping_label": "",
            "shipping_status": "read_error", "ship_to": "", "promotion_text": "",
        }


def selected_option(option) -> bool:
    try:
        return any(token.startswith("isSelected--") for token in (option.get_attribute("class") or "").split())
    except Exception:
        return False


def sku_groups(page):
    groups = []
    for group in page.locator(SKU_GROUP_SELECTOR).all():
        options = group.locator(SKU_OPTION_SELECTOR)
        if options.count():
            groups.append(group)
    return groups


def read_all_skus(page, url: str, title: str) -> list[dict[str, str]]:
    """Read a one-dimensional SKU panel; refuse ambiguous multi-dimensional panels."""
    url = product_url_without_sku(url)
    groups = sku_groups(page)
    if len(groups) != 1:
        record = empty_record(url, "sku_layout_unsupported")
        record["title"] = title
        return [record]
    group = groups[0]
    guide = page.get_by_text("知道了", exact=True)
    if guide.count() and guide.first.is_visible():
        guide.first.click(timeout=3000)
    options = group.locator(SKU_OPTION_SELECTOR)
    count = options.count()
    if count > MAX_SKUS_PER_PRODUCT:
        record = empty_record(url, "sku_limit_exceeded")
        record["title"] = title
        return [record]

    records = []
    for index in range(count):
        page_status = classify_page(page)
        if page_status:
            record = empty_record(url, page_status)
            record["title"] = title
            records.append(record)
            break
        option = group.locator(SKU_OPTION_SELECTOR).nth(index)
        name = " ".join(option.inner_text(timeout=1500).split())
        record = empty_record(url, "sku_not_selected")
        record["title"] = title
        record["sku_or_variant"] = name
        if option.get_attribute("data-disabled") == "true":
            record["status"] = "sku_unavailable"
            records.append(record)
            continue
        try:
            if not selected_option(option):
                option.scroll_into_view_if_needed(timeout=3000)
                option.click(timeout=5000)
            page.wait_for_function(
                """index => {
                    const group = [...document.querySelectorAll('[class*="skuItem--"]')]
                      .find(el => el.querySelector('[class*="valueItem--"][data-vid]'));
                    const option = group?.querySelectorAll('[class*="valueItem--"][data-vid]')[index];
                    return option && [...option.classList].some(c => c.startsWith('isSelected--'));
                }""",
                arg=index,
                timeout=5000,
            )
            # Selection can update before the price request completes. Inspect
            # twice after a short settling period; never accept a changing value.
            page.wait_for_timeout(1000)
            first = read_price_details(page)
            page.wait_for_timeout(400)
            second = read_price_details(page)
            if first != second:
                page.wait_for_timeout(800)
                first, second = second, read_price_details(page)
            if first == second and second[0]:
                record["price"], record["price_label"] = second
                record["status"] = "ok" if second[1] != "页面可见价格（待核对）" else "price_needs_review"
                record.update(read_taobao_delivery(page))
            else:
                record["status"] = "price_not_stable" if second[0] else "price_not_found"
        except Exception:
            record["status"] = "sku_not_selected"
        record["checked_at"] = now_iso()
        records.append(record)
    return records


def read_variant(page) -> str:
    candidates: list[str] = []
    for selector in SKU_CONTAINER_SELECTORS:
        for text, _ in visible_text(page.locator(selector), limit=12):
            if 1 <= len(text) <= 120 and text not in candidates:
                candidates.append(text)

    return " | ".join(candidates[:6])


def classify_page(page) -> str | None:
    current_url = page.url.lower()
    if any(
        part in current_url
        for part in ("login.taobao.com", "login.m.taobao.com", "login.1688.com", "passport")
    ):
        return "login_expired"

    try:
        body = page.locator("body").inner_text(timeout=1500).lower()
    except Exception:
        return "load_error"
    if not body.strip():
        return "load_error"

    if any(marker.lower() in body for marker in CHALLENGE_MARKERS):
        return "captcha"
    if any(marker.lower() in body for marker in RISK_MARKERS):
        return "risk_control"
    if any(marker.lower() in body for marker in LOGIN_MARKERS):
        return "login_expired"
    return None


def empty_record(url: str, status: str) -> dict[str, str]:
    return {
        "title": "",
        "price": "",
        "price_label": "",
        "sku_or_variant": "",
        "quantity": "",
        "shipping": "",
        "shipping_label": "",
        "shipping_status": "",
        "ship_to": "",
        "promotion_text": "",
        "url": url,
        "checked_at": now_iso(),
        "status": status,
    }


def save_records(records: list[dict[str, str]]) -> None:
    JSON_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    with CSV_FILE.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(records)


def chat_has_unsent_input(page) -> bool:
    """Avoid reloading a chat while a visible editor holds a draft or focus."""
    return bool(
        page.evaluate(
            """() => {
              const editors = [...document.querySelectorAll(
                'textarea, input:not([type]), input[type="text"], input[type="search"], [contenteditable="true"]'
              )];
              return editors.some(el => {
                const rect = el.getBoundingClientRect();
                if (!rect.width || !rect.height || getComputedStyle(el).visibility === 'hidden') {
                  return false;
                }
                const active = document.activeElement === el;
                const text = ('value' in el ? el.value : el.innerText || '').trim();
                return active || Boolean(text);
              });
            }"""
        )
    )


def chat_tab_label(index: int, page) -> str:
    try:
        title = page.title().strip()[:70]
    except Exception:
        title = "(标题不可读)"
    parsed = urlparse(page.url)
    address = f"{parsed.netloc}{parsed.path}"[:100]
    return f"[{index}] {title} — {address}"


def select_chat_tab(context, requested_index: int | None):
    pages = context.pages
    if requested_index is not None:
        if 0 <= requested_index < len(pages) and not pages[requested_index].is_closed():
            return pages[requested_index]
        print("现有标签页：")
        for index, page in enumerate(pages):
            if not page.is_closed():
                print(chat_tab_label(index, page))
        raise ValueError("标签页编号无效；请重新运行并从当前列表中选择。")

    visible_pages = []
    for index, page in enumerate(pages):
        if page.is_closed() or not page.url.startswith(("http://", "https://")):
            continue
        try:
            if page.evaluate("document.visibilityState") == "visible":
                visible_pages.append((index, page))
        except Exception:
            continue
    if len(visible_pages) == 1:
        return visible_pages[0][1]

    print("无法唯一确定当前聊天标签页。现有标签页：")
    for index, page in enumerate(pages):
        if not page.is_closed():
            print(chat_tab_label(index, page))
    raise ValueError("请确认聊天页编号，然后使用 --chat-tab-index 编号重新运行。")


def watch_chat(tab_index: int | None, minutes: int) -> int:
    endpoint = os.environ.get("TAOBAO_CDP_URL", "").strip()
    if not endpoint:
        print("聊天刷新需要已连接的 Chrome。请先设置 TAOBAO_CDP_URL。", file=sys.stderr)
        return 2
    if minutes < 1 or minutes > 240:
        print("--chat-minutes 必须在 1 到 240 之间。", file=sys.stderr)
        return 2

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint)
            if not browser.contexts:
                print("Chrome 没有可复用的浏览器上下文。", file=sys.stderr)
                return 1
            try:
                page = select_chat_tab(browser.contexts[0], tab_index)
            except ValueError as exc:
                print(exc, file=sys.stderr)
                return 2

            print(f"开始观察：{chat_tab_label(browser.contexts[0].pages.index(page), page)}")
            print(f"每 {CHAT_REFRESH_SECONDS} 秒刷新一次，最多 {minutes} 分钟；按 Ctrl+C 提前结束。")
            started = time.monotonic()
            deadline = started + minutes * 60
            next_refresh = started + CHAT_REFRESH_SECONDS
            try:
                while next_refresh <= deadline:
                    time.sleep(max(0, next_refresh - time.monotonic()))
                    if page.is_closed():
                        print("聊天标签页已关闭，停止刷新。")
                        break
                    try:
                        if chat_has_unsent_input(page):
                            print(f"{now_iso()} 页面有正在编辑或未发送的内容，本轮跳过刷新。")
                            next_refresh = max(
                                next_refresh + CHAT_REFRESH_SECONDS,
                                time.monotonic() + CHAT_REFRESH_SECONDS,
                            )
                            continue
                        status = classify_page(page)
                        if status in {"captcha", "risk_control", "login_expired", "load_error"}:
                            print(f"{now_iso()} 检测到 {status}，停止刷新并保留该标签页供手动处理。")
                            break
                        page.reload(wait_until="domcontentloaded", timeout=60000)
                        page.locator("body").wait_for(timeout=20000)
                        status = classify_page(page)
                        if status in {"captcha", "risk_control", "login_expired", "load_error"}:
                            print(f"{now_iso()} 刷新后出现 {status}，停止刷新并保留该标签页。")
                            break
                        print(f"{now_iso()} 已刷新聊天页，请在页面正文核对店名与回复时间。")
                    except Exception as exc:
                        print(f"{now_iso()} 刷新失败，已停止并保留该标签页：{exc}", file=sys.stderr)
                        return 1
                    next_refresh = max(next_refresh + CHAT_REFRESH_SECONDS, time.monotonic() + CHAT_REFRESH_SECONDS)
            except KeyboardInterrupt:
                print("已停止聊天页定时刷新。")
            else:
                print("本次咨询的定时刷新已结束；聊天标签页仍保留在 Chrome 中。")
            # This page existed before attachment; never close it or Chrome.
            return 0
    except Exception as exc:
        print(f"无法连接已打开的 Chrome：{exc}", file=sys.stderr)
        return 1


def main(all_skus: bool = False) -> int:
    try:
        urls = load_products()
    except ValueError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    records: list[dict[str, str]] = []
    with sync_playwright() as playwright:
        attached_to_existing_browser = False
        browser = None
        try:
            cdp_endpoint = os.environ.get("TAOBAO_CDP_URL", "").strip()
            if cdp_endpoint:
                # CDP attachment reuses the signed-in Chrome profile and does
                # not own the browser process. Never close that browser below.
                browser = playwright.chromium.connect_over_cdp(cdp_endpoint)
                if not browser.contexts:
                    raise RuntimeError("连接成功，但 Chrome 没有可复用的浏览器上下文。")
                context = browser.contexts[0]
                attached_to_existing_browser = True
                print("已连接到你已打开的 Chrome；将新建一个标签页，结束时保留 Chrome。")
            else:
                # Default mode stays isolated from an already-open Chrome.
                # Playwright cannot attach to a regular Chrome process unless
                # Chrome was started with remote debugging enabled.
                try:
                    browser = playwright.chromium.launch(channel="chrome", headless=False)
                except Exception:
                    print("未能启动本机 Google Chrome，改用 Playwright Chromium。")
                    browser = playwright.chromium.launch(headless=False)
                print("已启动独立浏览器窗口；当前打开的普通 Chrome 不会被接管。")
                print("如需复用已登录 Chrome，请设置 TAOBAO_CDP_URL 并从带远程调试的 Chrome 启动。")
        except Exception as exc:
            print(
                "无法连接浏览器。若设置了 TAOBAO_CDP_URL，请确认 Chrome 正在监听该地址；"
                "否则请检查 Chrome/Playwright 是否可用。\n"
                f"详细信息：{exc}",
                file=sys.stderr,
            )
            return 1

        context_options = (
            {}
            if attached_to_existing_browser
            else ({"storage_state": str(STATE_FILE)} if STATE_FILE.exists() else {})
        )
        context = None
        page = None
        keep_page_open = False
        try:
            context = browser.contexts[0] if attached_to_existing_browser else browser.new_context(**context_options)
            page = context.new_page()

            if not attached_to_existing_browser and not STATE_FILE.exists():
                print("首次运行：请在打开的浏览器中手动登录淘宝，完成后回到终端按回车。")
                try:
                    page.goto("https://www.taobao.com", wait_until="domcontentloaded", timeout=60000)
                    page.locator("body").wait_for(timeout=20000)
                except PlaywrightTimeoutError:
                    print("淘宝首页加载超时。请检查浏览器页面；不会尝试绕过验证。")
                    return 1
                input("登录完成后按回车继续（不要把密码输入终端或聊天）：")
                # Export from a blank document so a busy storefront page does
                # not stall Playwright while it inspects that origin's storage.
                page.goto("about:blank")
                context.storage_state(path=str(STATE_FILE))
                print(f"登录状态已保存到 {STATE_FILE.name}。请妥善保管，不要分享此文件。")

            for index, url in enumerate(urls):
                if index:
                    wait_seconds = random.uniform(3.0, 6.0)
                    print(f"等待 {wait_seconds:.1f} 秒后打开下一个商品…")
                    time.sleep(wait_seconds)

                print(f"[{index + 1}/{len(urls)}] 打开商品页面：{url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.locator("body").wait_for(timeout=20000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=7000)
                    except PlaywrightTimeoutError:
                        # Some storefront pages keep background requests alive.
                        # The rendered page is still inspected once; no retry.
                        pass
                    page.wait_for_timeout(1200)
                except PlaywrightTimeoutError:
                    records.append(empty_record(url, "load_error"))
                    save_records(records)
                    print("页面加载失败，已记录 load_error 并跳过。")
                    continue
                except Exception as exc:
                    records.append(empty_record(url, "load_error"))
                    save_records(records)
                    print(f"页面无法正常加载，已记录 load_error 并跳过：{exc}")
                    continue

                status = classify_page(page)
                if status:
                    records.append(empty_record(url, status))
                    save_records(records)
                    if attached_to_existing_browser and status in {
                        "captcha", "risk_control", "login_expired"
                    }:
                        keep_page_open = True
                        print(f"检测到 {status}，已记录并停止。该标签页会保留，请在 Chrome 中自行处理。")
                    else:
                        print(f"检测到 {status}，已记录并停止。")
                    break

                title = read_title(page)
                if all_skus:
                    product_records = read_all_skus(page, url, title)
                    records.extend(product_records)
                    print(f"完成：{len(product_records)} 条规格记录。")
                else:
                    price, price_label = read_price_details(page)
                    variant = read_variant(page)
                    status = "ok" if price and price_label != "页面可见价格（待核对）" else (
                        "price_needs_review" if price else "price_not_found"
                    )
                    records.append(
                        {
                            "title": title,
                            "price": price,
                            "price_label": price_label,
                            "sku_or_variant": variant,
                            **read_taobao_delivery(page),
                            "url": url,
                            "checked_at": now_iso(),
                            "status": status,
                        }
                    )
                    print(f"完成：status={status}, price={price or '(未找到)'}")
                save_records(records)

            print(f"结果已写入 {JSON_FILE.name} 和 {CSV_FILE.name}。")
            return 0
        finally:
            # A CDP connection shares the user's context. Close only the tab
            # created by this run; never close pre-existing tabs or Chrome.
            if page is not None and not keep_page_open:
                try:
                    page.close()
                except Exception as exc:
                    print(f"无法关闭本次创建的标签页：{exc}", file=sys.stderr)
            if context is not None and not attached_to_existing_browser:
                context.close()
            if browser is not None and not attached_to_existing_browser:
                browser.close()


def cli() -> int:
    parser = argparse.ArgumentParser(description="检查淘宝或 1688 商品价格、搜索 1688，或刷新聊天页。")
    parser.add_argument("--search-1688", metavar="关键词", help="在 1688 站内搜索并逐页保存候选商品")
    parser.add_argument("--max-pages", type=int, default=0, help="1688 搜索最多看几页；0 表示实际全部分页（最多 100 页）")
    parser.add_argument("--check-1688", action="store_true", help="读取 products_1688.json 中的 1688 商品规格价格")
    parser.add_argument("--watch-chat", action="store_true", help="仅在咨询期间刷新当前 Chrome 聊天页")
    parser.add_argument("--all-skus", action="store_true", help="逐个读取单规格组商品的全部规格价格")
    parser.add_argument("--chat-tab-index", type=int, help="聊天页在当前 Chrome 标签页列表中的编号")
    parser.add_argument(
        "--chat-minutes",
        type=int,
        default=CHAT_WATCH_MINUTES,
        help=f"定时刷新持续分钟数，默认 {CHAT_WATCH_MINUTES} 分钟",
    )
    args = parser.parse_args()
    search_requested = args.search_1688 is not None
    modes = sum(bool(mode) for mode in (search_requested, args.check_1688, args.watch_chat))
    if modes > 1 or (args.all_skus and modes):
        parser.error("一次只能选择一种模式。")
    if args.max_pages and not search_requested:
        parser.error("--max-pages 需要与 --search-1688 一起使用。")
    if search_requested or args.check_1688:
        from market1688 import check_1688, search_1688
        try:
            return search_1688(args.search_1688, args.max_pages) if search_requested else check_1688()
        except (ValueError, FileNotFoundError, RuntimeError, PlaywrightTimeoutError) as exc:
            print(f"1688 检查未完成：{exc}", file=sys.stderr)
            return 2
    if args.watch_chat:
        return watch_chat(args.chat_tab_index, args.chat_minutes)
    if args.chat_tab_index is not None or args.chat_minutes != CHAT_WATCH_MINUTES:
        parser.error("--chat-tab-index 和 --chat-minutes 需要与 --watch-chat 一起使用。")
    return main(all_skus=args.all_skus)


if __name__ == "__main__":
    raise SystemExit(cli())
