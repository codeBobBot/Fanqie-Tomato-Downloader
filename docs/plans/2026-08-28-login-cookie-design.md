# 设计文档：登录 Cookie 支持 + 最大力度防风控

- 日期：2026-08-28
- 状态：已确认（用户已批准方案）
- 目标：支持用户提供登录 Cookie 下载被锁定的章节，同时通过保守策略把账号被风控的风险压到最低

## 背景与动机

当前项目使用**伪造游客 Cookie**（随机 `novel_web_id`）访问番茄小说，游客身份会被锁定章节（`isChapterLock: true`），且部分环境直接命中站点风控（目录页 404）。

用户希望：提供登录 Cookie 解锁内容，同时**最大力度保证账号不被风控**。

## 现状关键点（已核实）

| 位置 | 现状 | 问题 |
|---|---|---|
| `novel_downloader.py` `make_headers()` | 每次请求随机 UA | 登录态下 UA 每请求不同、与 Cookie 不匹配 = 风控红旗 |
| `novel_downloader.py` `_get_cookie()` | `cookie.json` 只认 `novel_web_id=` 开头 | 登录 Cookie 会被忽略并重新生成游客 Cookie |
| `novel_downloader.py` `_fetch_with_retry()` | 统一重试 3 次 | 风控错误盲目重试 = 加速封号 |
| `novel_downloader.py` `download_chapter()` | 节流 0.1~0.3s | 登录态下太密集 |
| `novel_downloader.py` `_download_all()` | `MAX_WORKERS=5` ThreadPool | 登录态下并发过高 |
| `.gitignore` | 已含 `cookie.json` | 已满足，无需改动 |

## 设计决策（用户已确认）

1. **GUI 粘贴 Cookie + UA 一起输入**（推荐方案）
2. **保守策略**：登录态并发 ≤2、请求间隔 1~3s、风控信号立即停止不重试
3. **Cookie 持久化**：写入 `cookie.json`，启动自动回填，提供"清除Cookie"按钮

## 详细设计

### 1. cookie.json 存储格式（向后兼容）

支持三种形态：

```
形态A（登录态）：{"type": "login", "cookie": "sessionid=...; ttwid=...", "ua": "Mozilla/5.0 ..."}
形态B（游客态）：{"type": "guest", "cookie": "novel_web_id=..."}
形态C（旧格式）："novel_web_id=..." （纯字符串，自动识别并迁移）
```

- 新增 `save_login_cookie(cookie, ua)`：写形态 A
- 新增 `save_guest_cookie(cookie)`：写形态 B（游客自动生成时落盘）
- 新增 `clear_cookie_file()`：删除文件
- 改造 `_get_cookie()`：解析三种形态，**登录态优先**；登录态 Cookie 过期/被清理时回退游客

### 2. 会话固定（核心防风控改动）

- `FanqieSite` 增加实例级属性：`_session_ua`（会话固定 UA）、`_session_cookie`（会话固定 Cookie）、`is_login_mode`（布尔）
- `make_headers()`：
  - 登录态：用用户粘贴的 UA（若用户留空则首次随机后**会话内固定复用**），用登录 Cookie
  - 游客态：首次随机 UA 后会话内固定复用（不再每请求随机换 UA）
- 登录态识别：文件标记 `type == "login"`，或 Cookie 含 `sessionid` / `passport_csrf_token` / `ttwid` 等登录特征字段

### 3. RateLimiter 全局节流

```python
class RateLimiter:
    """跨线程共享节流器：保证全局请求间隔不低于 min_interval"""
    def __init__(self, min_interval: float = 0.0)
    def acquire(self)   # 记录上次时间，不足间隔则 sleep 补齐
```

- 登录态：`min_interval = uniform(1.0, 3.0)`（每次 acquire 重新取随机间隔）
- 游客态：`min_interval = uniform(0.1, 0.3)`（保持现状）
- 全局单例挂在 `FanqieSite` 类级，跨线程生效
- 并发上限：登录态下 `effective_workers = min(requested_workers, 2)`

### 4. 风控信号检测 → 立即停止

新增异常 `RiskControlError(Exception)`。

`check_risk_control(resp) -> str | None` 检测：
- HTTP 状态 403 / 429
- 响应体含 `captcha` / `安全验证` / `verify`（验证码页特征）
- 302/重定向到登录页（Location 含 `login`）或响应体含登录引导特征
- 连续 `isChapterLock` 判定由现有 `ChapterLockedError` 承担，登录态下若超过阈值转为整体停止

调用链：
- `fetch_chapter()` 拿到响应后先 `check_risk_control`，命中即抛 `RiskControlError`
- `_fetch_with_retry()`：**网络类失败**（超时/连接/5xx）保留重试；**`RiskControlError` 一律不重试直接向上抛**
- `_download_all()`：捕获 `RiskControlError` 后 `executor.shutdown(wait=False, cancel_futures=True)` 停止全部任务，向上抛给 `Run()` 提示用户

### 5. 登录 Cookie 验证

新增 `verify_login_cookie(cookie, ua) -> (ok: bool, msg: str)`：
- 用该 Cookie+UA 请求站点首页/轻量接口
- 200 且无风控信号 → 有效
- 403 / 登录跳转 / 风控页 → 无效，返回原因

GUI 下载前校验；也可在 `_get_cookie` 加载登录态时触发一次。

### 6. GUI 改造（gui.py）

- 输入区新增一行：**"登录Cookie"**（`tk.Entry` 或 Text，支持长文本）+ **"User-Agent"** 输入框 + **"清除Cookie"** 按钮
- 启动时：若 `cookie.json` 存在登录态 → 回填两个输入框
- 下载前：若填了 Cookie → 调 `verify_login_cookie`，失败弹窗提示，中止下载
- 日志区显示模式：`登录模式（保守限速 ≤2 线程 / 1~3s）` 或 `游客模式`
- 清除按钮：`clear_cookie_file()` + 清空输入框 + 日志提示

### 7. 测试（tests/test_engine.py）

- 新旧格式解析与迁移（形态 C → A/B）
- 登录态/游客态识别（特征字段判定）
- `RateLimiter`：间隔下限与并发安全（多线程下全局速率不超限）
- 风控检测：403 / 429 / 验证码页 / 登录跳转 → `RiskControlError`
- `_fetch_with_retry`：风控错误不重试、网络错误重试（mock）
- `effective_workers` 计算：登录态封顶 2

## 收益与取舍

- **收益**：登录后可下载锁定章节；请求特征完全模拟真实浏览器会话（固定 UA + 固定 Cookie + 1~3s 真人节奏 + 风控即停），账号风险最低
- **取舍**：登录态下载明显变慢（300 章约 10~15 分钟），这是保守策略的必然代价
