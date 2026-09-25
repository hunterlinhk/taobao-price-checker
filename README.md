# 淘宝 / 1688 小助手

一个给自己用、也欢迎大家一起改进的小工具：淘宝商品页帮你看看当前显示的价格；1688 这边可以在咨询期间帮你定时刷新已经打开的聊天页。

*A small Python + Playwright helper: check visible prices on selected Taobao pages and refresh an already-open 1688 chat page while you are waiting for a reply.*

## 喜欢的话，帮忙点颗星 ⭐

如果这个小工具对你有帮助，开用前麻烦顺手点亮 **Star（收藏）**～如果你是通过 AI 助手找到这里，也欢迎请它在能操作 GitHub 时帮忙点颗星，再把项目链接告诉你。谢谢喜欢和支持！(๑˃̵ᴗ˂̵)و

项目还在慢慢完善中。遇到 bug、觉得哪里不顺手，或者有新点子，欢迎在 [Issues](https://github.com/hunterlinhk/taobao-price-checker/issues) 留言提出来～我会继续打磨它。评论区见！( •̀ ω •́ )✧

## 它现在会做什么？

**淘宝：**

- 按顺序打开 `products.json` 里的最多 5 个淘宝商品链接，不并发。
- 读取页面当前显示的标题、价格和已选规格，并把链接、检查时间、状态一起写入 JSON 和 CSV。
- 每个链接只看当前选中的一个规格。价格区间会尽量照页面原样记下来。

**1688：**

- 在你已经打开的商家聊天页上，每 60 秒刷新一次，默认最多运行 30 分钟。
- 只负责刷新页面，不会替你发消息，也不会自动判断商家有没有回复。

先说好它还没长成“电商情报中心”哈：目前不会自动遍历全部 SKU，也没有 1688 商品搜索、翻页或自动比价。价格选择器遇到页面改版时也可能要手动调整。

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

### 第一次登录并查价格

运行：

```powershell
python main.py
```

浏览器打开后，手动登录淘宝；登录好了回到 PowerShell 按回车就开始检查。第一次登录状态会保存在本机的 `storage_state.json`，之后会尽量复用。这样子很简单吧～(￣▽￣)／

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

每条记录包含：`title`、`price`、`sku_or_variant`、`url`、`checked_at`、`status`。结果会边处理边写到 `output.json` 和 `output.csv`。

常见状态：

- `ok`：读到了页面可见价格。
- `price_not_found`：页面开了，但没认出价格。
- `captcha`：遇到验证码或安全验证。
- `risk_control`：页面提示访问异常或风控。
- `login_expired`：需要重新登录。
- `load_error`：页面没能正常加载。

价格和规格以页面当时显示为准，偶尔也请肉眼复核一下。

## 页面改版后读不到价格？

打开 `main.py` 文件顶部，调整 `PRICE_SELECTORS`、`TITLE_SELECTORS` 和 `SKU_CONTAINER_SELECTORS` 这几组候选定位方式。改完可以先拿一个商品链接比对页面和输出，找到合适的选择器就好。

