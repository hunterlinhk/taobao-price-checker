# 淘宝 / 1688 小助手

一个给自己用一起改进的小工具：淘宝商品页帮你看看当前显示的价格；1688 这边可以在咨询期间帮你定时刷新已经打开的聊天页。注意不是爬虫，只是浏览器访问工具，如果你希望是爬虫这么简单的可以不用看了，个人感觉比Codex的Chrome use好用蛮多。。

*A small Python + Playwright helper: check visible prices on selected Taobao pages and refresh an already-open 1688 chat page while you are waiting for a reply.*

## 喜欢的话，帮忙点颗星 ⭐

如果这个小工具对你有帮助，开用前麻烦顺手点亮 Star（收藏）～如果是通过 AI 助手找到这里，也欢迎请它在能操作 GitHub 时帮忙点颗星，再把项目链接告诉你。谢谢喜欢和支持！(๑˃̵ᴗ˂̵)و

项目还在慢慢完善中。遇到 bug、觉得哪里不顺手，或者有新点子，欢迎在 [Issues](https://github.com/hunterlinhk/taobao-price-checker/issues) 留言提出来( •̀ ω •́ )✧
我会去改的！

## 它现在会做什么？

**淘宝：**

- 按顺序打开 `products.json` 里的最多 5 个淘宝商品链接，不并发。
- 读取页面当前显示的标题、价格、已选规格、数量、运费和优惠提示，并写入 JSON 和 CSV。
- 默认读取当前选中的规格；使用 `--all-skus` 可串行读取只有一个规格组的商品选项。

**1688：**

- 可用站内关键词搜索并逐页保存商品候选，默认最多查看 100 页。
- 可检查 `products_1688.json` 中最多 5 个商品的规格表价格和库存，并读取选 1 件时页面显示的金额与运费。
- 可在咨询期间每 60 秒刷新已打开的聊天页，默认最多运行 30 分钟。
- 搜索卡片价格只适合筛选候选，具体规格价格要进入商品页查看；脚本不会替你发消息或提交订单。

淘宝多规格组组合暂不支持；1688 商品页目前支持“价格｜库存”规格表布局。页面改版后定位方式可能需要调整。

## 开始使用

需要 Windows、Python 3.10 或更新版本，以及 Google Chrome。打开 PowerShell，进入项目文件夹后运行：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

如果电脑没装 Chrome，或者想用 Playwright 自带的 Chromium，再运行：

```powershell
python -m playwright install chromium
```

### 放入商品链接

先复制示例配置：

```powershell
Copy-Item products.example.json products.json
```

再编辑 `products.json`，放入最多 5 个淘宝商品详情页链接。例如：

```json
[
  "https://item.taobao.com/item.htm?id=商品编号1",
  "https://detail.tmall.com/item.htm?id=商品编号2"
]
```

### 填写 1688 商品链接

复制示例文件，再把空数组替换为 1 到 5 个 1688 商品详情页链接：

```powershell
Copy-Item products_1688.example.json products_1688.json
```

`products_1688.json` 是你自己的本地商品清单。

### 第一次登录并查价格

运行：

```powershell
python main.py
```

浏览器打开后，手动登录淘宝；登录好了回到 PowerShell 按回车就开始检查。第一次登录状态会保存在本机的 `storage_state.json`，之后会尽量复用。这样子很简单吧～(￣▽￣)／

### 读取淘宝全部可选规格

先在 `products.json` 中放入商品链接，然后运行：

```powershell
.\.venv\Scripts\python.exe main.py --all-skus
```

规格会逐个选择并单独写入结果。当前只支持单规格组；多规格组合会标记为不支持。

### 搜索 1688 商品

先在专用 Chrome 登录 1688，然后运行：

```powershell
$env:TAOBAO_CDP_URL = "http://127.0.0.1:9222"
.\.venv\Scripts\python.exe main.py --search-1688 "简短关键词"
```

默认按页面显示的分页逐页查看，最多 100 页。想限制为 3 页时加 `--max-pages 3`。搜索卡片上的价格可能对应其他规格，选好候选后再用商品检查模式确认。

### 检查 1688 规格价格和运费

把商品详情页链接填入 `products_1688.json`，然后运行：

```powershell
.\.venv\Scripts\python.exe main.py --check-1688
```

结果写入 `prices_1688.json` 和 `prices_1688.csv`。每个规格分别记录规格表价格、库存，以及选 1 件时页面显示的金额和运费。页面规格表不受支持时会记录对应状态。

### 想用已经登录的 Chrome？

双击 `launch_debug_chrome.bat`，它会打开一个专用 Chrome 窗口。先在里面登录需要使用的电商网站，然后在另一个 PowerShell 窗口运行：

```powershell
$env:TAOBAO_CDP_URL = "http://127.0.0.1:9222"
.\.venv\Scripts\python.exe main.py
```

脚本会在这个 Chrome 里新开商品标签页，完成后关掉自己开的商品页，保留原有窗口和标签页。遇到需要你处理的页面时会把标签页留下。

### 1688 咨询期间刷新聊天页

先在专用 Chrome 里打开要等回复的聊天，再运行：

```powershell
$env:TAOBAO_CDP_URL = "http://127.0.0.1:9222"
.\.venv\Scripts\python.exe main.py --watch-chat
```

默认每分钟刷新一次，最多跑 30 分钟；按 `Ctrl+C` 可以提前结束。如果开着多个标签页，脚本会请你选聊天页编号，例如：

```powershell
.\.venv\Scripts\python.exe main.py --watch-chat --chat-tab-index 3 --chat-minutes 60
```

有没发送的输入内容时会跳过本轮刷新；遇到登录、验证码、风控或空白页会停下来。

## 输出怎么看？

淘宝记录包含标题、当前价格与标签、规格、数量、运费、收货地区、优惠提示、链接、检查时间和状态，写入 `output.json` 与 `output.csv`。1688 搜索和规格价格使用独立的 JSON/CSV 文件。

常见状态：

- `ok`：读到了页面可见价格。
- `price_not_found`：页面开了，但没认出价格。
- `captcha`：遇到验证码或安全验证。
- `risk_control`：页面提示访问异常或风控。
- `login_expired`：需要重新登录。
- `load_error`：页面没能正常加载。
- `price_needs_review`、`price_not_stable`：价格来源不够明确，或切换规格后价格仍在变化。
- `sku_layout_unsupported`、`sku_unavailable`：规格布局不支持，或该规格无法选择。

价格和规格以页面当时显示为准，偶尔也请肉眼复核一下。

## 页面改版后读不到价格？

淘宝价格、标题和规格定位方式可在 `main.py` 中调整；运费读取也在该文件的 `read_taobao_delivery()` 附近。1688 搜索和规格表定位在 `market1688.py`。改完可以先用一个链接对照页面和输出。

