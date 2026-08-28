# 实现计划：登录 Cookie 支持 + 最大力度防风控

- 日期：2026-08-28
- 设计文档：`docs/plans/2026-08-28-login-cookie-design.md`
- 执行者：本会话 AI（或后续会话的 implementer）
- 验证方式：`D:\soft\minconda\envs\novel\python.exe -m unittest discover -s tests -v`（全部通过）+ GUI 手工验证

## 目标

1. 用户可在 GUI 粘贴登录 Cookie + 浏览器 UA，解锁下载被锁定的章节
2. 登录态下执行保守防风控策略：固定 UA+Cookie、并发≤2、请求间隔 1~3s、风控信号立即停止不重试
3. 登录 Cookie 持久化到 `cookie.json`（新格式向后兼容），提供"清除Cookie"按钮

## 边界

- 不改动现有游客模式默认行为（用户未提供登录 Cookie 时一切照旧，但 UA 也改为会话内固定）
- 不新增第三方依赖（仅用标准库 `threading`/`json`）
- `.gitignore` 已含 `cookie.json`，无需改动

## 实施步骤

### Step 1：cookie.json 新格式支持（novel_downloader.py）

**文件**：`novel_downloader.py`（`cookie_path` 定义区，第 20~24 行附近）

**行为变化**：
- `_get_cookie()` 不再只认 `novel_web_id=` 字符串，改为解析三种形态：
  - 形态A `{"type":"login","cookie":...,"ua":...}` → 登录态，优先使用
  - 形态B `{"type":"guest","cookie":...}` → 游客态
  - 形态C 纯字符串 `"novel_web_id=..."` → 识别为游客态，自动迁移为形态 B
- 新增模块级函数：
  - `save_login_cookie(cookie: str, ua: str) -> None`：写形态 A
  - `save_guest_cookie(cookie: str) -> None`：写形态 B
  - `clear_cookie_file() -> None`：删除文件并清类级缓存
  - `load_login_credentials() -> Optional[Tuple[str, str]]`：读形态 A 返回 (cookie, ua)
  - `_parse_cookie_file() -> Tuple[Optional[str], Optional[str]]`：解析文件返回 (cookie, ua)；登录优先
- 类级新增 `_login_cookie_cache: Optional[str]`、`_login_ua_cache: Optional[str]` 与现有 `_cookie_cache` 并存
- `_get_cookie(force)` 优先级：登录态缓存 > 文件登录态 > 游客缓存 > 生成游客

**可测试行为**：
- 写形态 A 后 `_parse_cookie_file()` 返回 (cookie, ua)
- 形态 C 纯字符串可被识别为游客 cookie
- `clear_cookie_file()` 后文件不存在、类级缓存清空

### Step 2：会话固定 UA + RateLimiter（novel_downloader.py）

**文件**：`novel_downloader.py`（第 33~39 行 `get_random_user_agent` 附近，第 284 行 `_cookie_cache` 附近）

**行为变化**：
- 新增 `RateLimiter` 类：
  ```python
  class RateLimiter:
      def __init__(self, min_interval: float = 0.0):
      def acquire(self) -> None  # 线程安全；不足间隔则 sleep 补齐
  ```
- `FanqieSite` 新增实例属性（`__init__`）：
  - `_session_ua: Optional[str]` —— 会话固定 UA，首次确定后不复用随机
  - `_session_rate_limiter: RateLimiter`
- `make_headers()` 改为：
  - 登录态（有 `_login_cookie_cache`）：UA = 用户 UA（若提供）否则会话内首次随机并固定；Cookie = 登录 Cookie
  - 游客态：UA 会话内首次随机并固定；Cookie = 游客 Cookie
- 新增模块级 `effective_workers(requested: int) -> int`：登录态返回 `min(requested, 2)`，游客态返回 `min(requested, 10)`
- 新增模块级 `request_interval_seconds() -> float`：登录态 `uniform(1.0, 3.0)`，游客态 `uniform(0.1, 0.3)`
- `download_chapter()` 中 `time.sleep(random.uniform(0.1, 0.3))` 替换为走统一节流：`rate_limiter.acquire()`（登录态实例）或保留游客逻辑

**可测试行为**：
- `RateLimiter` 在间隔设为 0.05 时两次 acquire 至少间隔 0.05 秒（monotonic 计时）
- `effective_workers(5)` 登录态 = 2，游客态 = 5
- 登录态下 `make_headers()` 连续两次调用 UA 相同

### Step 3：风控信号检测与停止（novel_downloader.py）

**文件**：`novel_downloader.py`（`ChapterLockedError` 定义区第 489 行附近、`fetch_url` 第 89 行附近）

**行为变化**：
- 新增异常 `RiskControlError(Exception)`
- 新增 `check_risk_control(response) -> Optional[str]`：返回命中原因或 None
  - 403 / 429 → 命中
  - 响应体含 `captcha` / `安全验证` / `verify` → 命中
  - `response.url` 或重定向 Location 含 `login` → 命中
- `FanqieSite.fetch_chapter()`：拿到 response 后先 `check_risk_control`，命中抛 `RiskControlError`
- `FanqieSite.get_book_info()` / `fetch_catalog()`：同样先检测，命中抛 `RiskControlError`
- `_fetch_with_retry()`：`RiskControlError` **不重试**直接上抛；仅网络类失败（返回 None）重试
- `_download_all()`：捕获 `RiskControlError`，`executor.shutdown(wait=False, cancel_futures=True)` 停止所有任务，日志提示后重新抛出
- `Run()`：捕获 `RiskControlError` 输出明确提示（"检测到风控信号，已停止下载保护账号"）

**可测试行为**：
- `check_risk_control` 对 403/429/验证码页/登录跳转返回原因字符串，正常页返回 None
- `_fetch_with_retry` 遇 `RiskControlError` 只调用 1 次（不重试）

### Step 4：登录 Cookie 验证（novel_downloader.py）

**文件**：`novel_downloader.py`（`get_cookie` 兼容入口区，第 470 行附近）

**行为变化**：
- 新增 `verify_login_cookie(cookie: str, ua: str) -> Tuple[bool, str]`：
  - 用该 Cookie+UA 请求 `HOME`
  - 200 且无风控信号 → `(True, "有效")`
  - 403/登录跳转/风控页 → `(False, 原因)`
  - 网络异常 → `(False, "网络异常")`（不视为 Cookie 无效）

**可测试行为**：
- mock `fetch_url` 返回 403 → `(False, ...)`
- mock 返回 200 正常页 → `(True, ...)`

### Step 5：GUI 改造（gui.py）

**文件**：`gui.py`（`create_widgets` 第 78 行附近、`start_download` 第 167 行附近）

**行为变化**：
- 输入区新增一行（row 5，下载按钮下移）：
  - "登录Cookie:" 输入框（`ttk.Entry`，宽度 50）
  - "User-Agent:" 输入框（`ttk.Entry`）
  - "清除Cookie" 按钮
- `__init__`：启动时 `load_login_credentials()` 有值则回填两个输入框
- `start_download()`：
  - 若 Cookie 输入框非空 → `verify_login_cookie`，失败弹窗提示并中止
  - 成功 → `save_login_cookie(cookie, ua)` 持久化
  - 若输入框为空但有文件登录态 → 使用文件登录态，不重复验证（或验证一次）
  - 若两者皆空 → 游客模式
- `run_download()` 日志明确显示：`登录模式（保守限速 ≤2 线程 / 1~3s 间隔）` 或 `游客模式`
- 清除按钮回调：`clear_cookie_file()` + 清空输入框 + 日志提示
- 增加 login cookie/ua 作为参数传给 `run_download`，并在下载线程里设置 `novel_downloader.FanqieSite` 的登录态缓存

**可测试行为**：GUI 手工验证（无自动化 UI 测试）

### Step 6：测试（tests/test_engine.py）

新增测试类：
- `TestCookieFile`：形态 A/B/C 解析、保存、清除、登录优先
- `TestRateLimiter`：间隔下限
- `TestEffectiveWorkers`：登录态封顶 2
- `TestRiskControl`：`check_risk_control` 各信号
- `TestFetchWithRetryRisk`：风控不重试
- `TestVerifyLoginCookie`：验证逻辑

注意：现有 `TestFetchWithRetry` / `TestDownTextRetry` 中 mock `n.time.sleep` 的模式保持不变；`_fetch_with_retry` 签名不变，避免破坏现有测试。

### Step 7：README 更新

- 增加"登录 Cookie 使用指南"：F12 → Network → 任意请求 → Request Headers → 复制 Cookie 与 User-Agent 两行
- 风控安全提示与账号风险说明
- 游客/登录两种模式说明

## 验证清单

1. `D:\soft\minconda\envs\novel\python.exe -m unittest discover -s tests -v` 全部通过
2. `D:\soft\minconda\envs\novel\python.exe -c "import novel_downloader"` 无导入错误
3. GUI 手工：粘贴 Cookie+UA → 日志显示登录模式 → 清除按钮生效 → 重启回填
4. 无登录 Cookie 时游客模式行为与旧版一致
