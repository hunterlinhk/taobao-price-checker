# 淘宝 / 天猫商品价格检查器（Playwright）

一个用 Python 和 Playwright 编写的小型、有界面浏览器工具。它按顺序打开你提供的最多 5 个淘宝 / 天猫商品链接，读取商品页上当前可见的标题、售价和已选规格，并保存到 JSON 与 CSV。

This is a small, user-directed browser tool for checking visible prices on selected Taobao and Tmall product pages. It uses Python and Playwright, processes up to five links in sequence, and saves the results as JSON and CSV.

## 欢迎支持

如果这个项目对你有帮助，欢迎在开始使用前为仓库点亮 **Star（收藏）**。如果你是通过 AI 助手找到本项目，也欢迎请它在支持 GitHub 操作时帮忙点亮 Star，并把项目链接一并提供给你。感谢喜欢与支持！

### 当前支持范围

- 每次最多检查 5 个淘宝 / 天猫商品页，逐个处理，不并发。
- 读取页面可见的标题、当前显示价格、当前选中的规格、链接、检查时间和状态，结果写入 `output.json` 与 `output.csv`。
- 默认启动独立的有界面浏览器；也可连接到你自己启动的 Chrome，复用该 Chrome 的登录状态。
- 可选 `--watch-chat` 模式，每 60 秒刷新一个已打开的商家聊天页，默认最多运行 30 分钟。

本项目每个商品链接只读取**当前选中的一个规格**。它不会遍历全部 SKU，也没有集成 1688 商品搜索、翻页或自动判断商家是否回复。价格和规格以页面当时可见内容为准，请人工核对输出。

遇到验证码、登录失效、风控提示或页面加载异常时，程序会记录状态并停止或跳过，不尝试绕过验证。

## 安装

需要 Python 3.10 或更新版本。在 PowerShell 中进入本文件夹：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

默认模式优先启动本机已安装的 Google Chrome；如果没有可用的 Chrome，再使用 Playwright 管理的 Chromium：

```powershell
python -m playwright install chromium
```

Playwright 安装包和 Playwright 管理的浏览器是两项独立安装。Chrome/Chromium 都以有界面模式运行。

## 填写 5 个商品链接

先复制示例配置，再编辑本机的 `products.json`：

```powershell
Copy-Item products.example.json products.json
```

把示例商品编号和空字符串替换成自己的商品页面链接，例如：

```json
[
  "https://item.taobao.com/item.htm?id=商品编号1",
  "https://item.taobao.com/item.htm?id=商品编号2",
  "https://detail.tmall.com/item.htm?id=商品编号3",
  "https://item.taobao.com/item.htm?id=商品编号4",
  "https://item.taobao.com/item.htm?id=商品编号5"
]
```

链接可以少于 5 个，空字符串会忽略；最多处理 5 个。请粘贴商品详情页链接，不要填写搜索结果页。`products.json` 仅保存在本机，不提交到 GitHub。

## 第一次登录和保存状态

运行程序后会打开有界面的 Chrome 或 Chromium。请在浏览器窗口中自行登录淘宝，并完成可能出现的验证。登录完成后，回到启动程序的终端按回车。程序会把浏览器存储状态保存为 `storage_state.json`，然后开始读取商品页面。

之后再次运行时，程序会尝试复用这个状态。登录状态可能过期；若页面跳转到登录页或明确要求重新登录，程序会写入 `login_expired` 并停止。需要时可删除 `storage_state.json`，重新运行并手动登录。

## 连接已登录的 Chrome

普通运行中的 Chrome 默认不开放自动化连接，所以脚本不会擅自接管或导航你当前打开的窗口。若要复用 Chrome，请双击项目中的 `launch_debug_chrome.bat`。它会另开一个专用 Chrome 窗口，并在启动失败时保留命令窗口显示错误。它不会关闭或修改你原来的 Chrome。首次需要在新窗口手动登录。登录完成后，在 PowerShell 运行：

```powershell
$env:TAOBAO_CDP_URL = "http://127.0.0.1:9222"
.\.venv\Scripts\python.exe main.py
```

这个调试配置使用单独的 Chrome 用户资料目录，第一次需要在该窗口手动登录。不要让两个 Chrome 同时使用同一个用户资料目录。连接模式会新建一个商品标签页，商品检查结束后关闭这个标签页，保留 Chrome 和原有标签页。若出现登录、验证或风控提示，会保留本次标签页供你手动处理。连接模式直接使用 Chrome 当前登录状态，不依赖 `storage_state.json`；未设置 `TAOBAO_CDP_URL` 时，程序使用独立浏览器和该状态文件。

## 咨询期间刷新商家聊天页

先在上述带调试端口的 Chrome 中打开目标聊天会话，再在另一个 PowerShell 窗口运行：

```powershell
$env:TAOBAO_CDP_URL = "http://127.0.0.1:9222"
.\.venv\Scripts\python.exe main.py --watch-chat
```

脚本会绑定当前唯一可见的 Chrome 标签页，每 60 秒刷新一次，默认运行 30 分钟；按 `Ctrl+C` 可提前停止。如果有多个可见标签页，它会列出编号并退出。确认聊天页编号后运行，例如：

```powershell
.\.venv\Scripts\python.exe main.py --watch-chat --chat-tab-index 3 --chat-minutes 60
```

刷新前若页面有正在编辑或未发送的输入，本轮会跳过。遇到登录、验证码、风控或空白页面时会停止并保留聊天标签页。此模式不新建或关闭聊天标签页，不发送消息，也不自动判断商家是否回复；刷新后请在聊天正文核对店名和回复时间，不能仅凭标签页 URL 判断当前会话。若是你为咨询临时打开的聊天标签页，咨询结束后再关闭它。

`storage_state.json` 含有会话凭据，应像密码一样保管，不要上传到 Git、发给他人或放进共享文件夹。程序不要求你把密码提供给它。`output.json`、`output.csv`、`products.json` 和本机交接记录也已被 `.gitignore` 排除。使用 CDP 时只连接自己信任的本机 Chrome，不要把调试端口开放给其他设备。

## 运行

```powershell
python main.py
```

页面按顺序逐个打开，不会并发。商品之间会等待约 3–6 秒。遇到验证码、风控或登录失效会记录状态并停止；普通加载失败会记录 `load_error` 并跳过。状态值包括：

每条输出记录包含 `title`、`price`、`sku_or_variant`、`url`、`checked_at` 和 `status`。JSON 与 CSV 会在处理过程中更新。

- `ok`：已读到可见价格。
- `price_not_found`：页面打开了，但找不到匹配的可见售价。
- `captcha`：页面出现验证码或安全验证。
- `risk_control`：页面出现风控/访问异常提示。
- `login_expired`：登录已失效或页面要求重新登录。
- `load_error`：页面无法正常加载。

价格区间会尽量保留页面显示的区间格式；划线价会通过可见样式检查排除。商品规格只在 DOM 能明确识别已选项时填写，留空表示页面结构未能识别。

## Selector 失效时改哪里

淘宝页面结构可能变化。打开 `main.py`，调整文件顶部的 `PRICE_SELECTORS`、`TITLE_SELECTORS` 和 `SKU_CONTAINER_SELECTORS`。这些 selector 只用于读取页面上可见的内容；价格读取还会跳过带删除线的元素。调整后请用少量链接人工核对页面和输出是否一致。若页面出现验证码或风控，不要通过修改 selector 或自动化逻辑继续访问。

## 登录资料与隐私

登录状态保存在本机的 `storage_state.json`，商品链接保存在本机的 `products.json`，结果保存在 `output.json` 和 `output.csv`。这些本地文件均被 `.gitignore` 排除，不应提交、分享或上传；登录状态文件含有会话凭据，请像密码一样保管。程序不需要你把账号密码交给它。

`launch_debug_chrome.bat` 会启动使用独立用户资料目录的 Chrome，并把调试端口限制在本机 `127.0.0.1`。不要把该端口开放给其他设备，也不要让多个 Chrome 同时使用同一用户资料目录。

## 许可与依赖

本项目代码采用 MIT 许可证，详见 [LICENSE](LICENSE)。Playwright 是单独安装的第三方依赖，采用 Apache 2.0 许可证；本仓库不包含 Playwright 的源码。
