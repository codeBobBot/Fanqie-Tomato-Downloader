# 代码审计报告

## 审计概览

| 项目 | 内容 |
|---|---|
| 审计对象 | Fanqie-Tomato-Downloader（番茄小说下载器） |
| 仓库 | https://github.com/POf-L/Fanqie-Tomato-Downloader |
| 审计日期 | 2026-08-25 |
| 代码形态 | Python 桌面工具（tkinter GUI + CLI），GitHub Actions CI/CD |
| 审计范围 | 全部源码：`2.py`、`gui.py`、`build_exe.py`、`requirements.txt`、`.github/workflows/build-and-release.yml`、`.github/workflows/download-novel.yml`、`README.md` |
| 运行环境 | Windows / Python 3.11.15（miniconda env: novel） |
| 审计方法 | 人工代码走查 + pip-audit 依赖漏洞扫描 |
| 用途说明 | 本报告由 AI 辅助生成，仅代表基于当前代码快照的静态审计结论 |

## 项目概览

- 核心功能：输入番茄小说 ID，多线程并发下载全文，输出 TXT / 分章节 TXT / EPUB。
- 架构：`2.py` 为下载引擎（含章节加密字符表解密、官网/第三方双通道取正文）；`gui.py` 通过 `importlib` 动态加载引擎并做进度展示；`build_exe.py` 打包；两个 workflow 分别承担"三平台自动构建发布"与"GitHub 在线下载"。
- 无测试代码，无类型注解，无依赖锁文件。

## 评分总结

| 维度 | 评分 | 等级 | 严重项(🔴) | 警告项(🟠) | 提示项(🟡) |
|---|---|---|---|---|---|
| 安全 Security | 92 | A | 0 | 0 | 2 |
| 性能 Performance | 98 | A | 0 | 0 | 0 |
| 健壮性 Robustness | 76 | B | 0 | 1 | 3 |
| 可用性 Availability | 91 | A | 0 | 0 | 2 |
| 可维护性 Maintainability | 71 | C | 0 | 2 | 3 |
| 供应链 Supply Chain | 77 | B | 0 | 1 | 3 |
| **综合** | **84.2** | **B** | 0 | 4 | 13 |

---

## 一、安全 Security（92 / A）

无 🔴 级问题。该工具为本地单机程序、无网络暴露面、无鉴权需求，攻击面很小。

| # | 级别 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| S1 | 🟡 | `2.py:160` | 第三方 API 使用**明文 HTTP**（`http://rehaofan.jingluo.love`） | 中间人可篡改正文内容；该接口已实测失效（2026-08-25），若恢复仍存在明文风险 | 迁移至 HTTPS 或直接移除该通道（见 P2） |
| S2 | 🟡 | `2.py:258`、`2.py:333` | 书名直接拼接文件路径，未过滤 `\/*?:"<>|` 等非法字符 | 书名含特殊字符时 `open()` 抛异常，或文件写入异常路径 | 写出前对书名执行 `re.sub(r'[\\/*?:"<>|]', "", name)` |

**已达标项**：无硬编码密钥/令牌；无 `shell=True` 命令注入（`gui.py:280` 使用参数列表）；无 `eval/exec` 动态执行；无 SQL 拼接；cookie 仅含随机 `novel_web_id`，低敏感。

## 二、性能 Performance（98 / A）

无 🟠/🟡 级问题。修复后的性能设计较合理。

| # | 级别 | 位置 | 说明 |
|---|---|---|---|
| P1 | 🟢 | `2.py:372` | 全部章节结果驻留内存再统一写出；1500 章约 4~5MB，可接受 |
| P2 | 🟢 | `2.py:238-253` | `download_chapter` 中 response 未显式关闭，依赖 GC 回收 |

**已达标项**：Cookie 模块级缓存（`_cookie_cache`）+ 文件缓存；第三方接口熔断（`_api_available`）避免每章空等超时；`ThreadPoolExecutor` 并行下载；GUI 进度更新 0.1s 限频（`gui.py:43`）。

## 三、健壮性 Robustness（76 / B）

| # | 级别 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| R1 | 🟠 | `2.py:210`、`2.py:245`、`2.py:357` | 三处 `requests.get()` **未设置 timeout**（`get_book_info`、`download_chapter` 正文页、`Run` 章节列表） | 网络挂起时下载线程**永久阻塞**，整个下载卡死且无提示 | 统一封装带 `timeout=10` 与重试的请求函数 |
| R2 | 🟡 | `2.py:242` | `titles[i]` 索引访问；`extract_chatper_titles` 会跳过空标题，`titles` 长度可能小于 `li_list` | 极端页面结构下 `IndexError` | 越界时回退为 `f"第{i+1}章"` |
| R3 | 🟡 | `2.py:151-152` | `fetch_content_from_official` 静默吞掉所有异常 | 官网解析失败无任何日志，难以排查 | 打印异常摘要后返回 None |
| R4 | 🟡 | `gui.py:280` | PyInstaller frozen 环境下 `sys.executable` 是 exe 自身，`install_ebooklib` 的 `-m pip` 不可用 | 打包版 GUI 的 EPUB 依赖自动安装必然失败 | frozen 环境直接提示手动安装 |
| R5 | 🟢 | `2.py:398-405` | 无效 `OUTPUT_FORMAT` 静默无输出 | 用户输入错误格式无反馈 | 增加 else 分支提示 |
| R6 | 🟢 | — | 无断点续传 | 中断后需全量重下 | 可选优化 |

**已达标项**：章节下载三级重试；单章异常不影响整体（`try/except future.result`）；Cookie 损坏文件静默降级；EPUB 缺库有明确提示。

## 四、可用性 Availability（91 / A）

| # | 级别 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| A1 | 🟡 | `2.py:374-377` | 多线程并发请求官网无限流，线程数可设置很大，存在封 IP 风控风险 | 限制默认并发、增加轻微请求节流 |
| A2 | 🟡 | `download-novel.yml:61` | `threads = int(sys.argv[3])` 无上限校验，用户可填任意值（如 9999） | clamp 到 1~10 |
| A3 | 🟢 | 全部 | 日志无时间戳、无级别 | 单机工具可接受，可选优化 |

**已达标项**：第三方接口熔断可观测；GUI 进度条/日志实时反馈；下载中退出有二次确认（`gui.py:293-297`）。

## 五、可维护性 Maintainability（71 / C）

| # | 级别 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| M1 | 🟠 | — | **无任何测试**。`interpreter` 解密、`clean_content`、`write_txt/write_chapters/generate_epub` 等关键路径零覆盖 | 为核心纯函数补充 pytest 单测 |
| M2 | 🟠 | `.github/workflows/*` | CI 仅"装依赖+运行"，无测试/lint 门禁 | 增加 `pytest` + `python -m py_compile` 步骤 |
| M3 | 🟡 | `2.py` 文件名 | 模块名 `2` 非法（数字开头），`gui.py:22` 与两个 workflow 被迫用 `importlib` hack 加载 | 重命名为合法模块名（如 `novel_downloader.py`） |
| M4 | 🟡 | `2.py:344-405` | `Run()` 单函数多职责（解析+调度+写出分发），约 60 行核心调度逻辑 | 拆分调度/写出 |
| M5 | 🟡 | `build-and-release.yml:47-56,103-112,159-168` | 用 sed/PowerShell 文本替换动态修改 `build_exe.py`，且 `ebooklib.epub` 在源码中已存在，hack 冗余 | 删除冗余替换步骤 |
| M6 | 🟢 | `2.py:197` | `extract_chatper_titles` 拼写错误（chatper）；`funLog` 命名不规范；无类型注解 | 命名修正 + 类型标注 |

**已达标项**：README 使用说明齐全；常量/配置集中在文件头部；`charset` 解密表有注释。

## 六、供应链 Supply Chain（77 / B）

| # | 级别 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| C1 | 🟠 | `2.py:160` | 依赖非官方个人镜像 `rehaofan.jingluo.love`（明文 HTTP，已失效）——外部单点 | 已实现官网回退兜底；建议彻底移除该通道 |
| C2 | 🟡 | `requirements.txt` | 全部使用浮动版本（`>=`），无 lock 文件 | 使用 pip-tools/poetry 生成锁定版本 |
| C3 | 🟡 | `requirements.txt` | `bs4>=0.0.1` 为 PyPI **已废弃的虚拟包**（真实包是 `beautifulsoup4`） | 移除 bs4，仅保留 beautifulsoup4 |
| C4 | 🟡 | `.github/workflows/*` | Actions 使用 `@v3/@v4` tag、`softprops/action-gh-release@v1`，未固定 SHA | 固定到 commit SHA 或启用 Dependabot |

**验证证据**：`pip-audit -r requirements.txt` 执行结果 `No known vulnerabilities found`（依赖无已知漏洞）。默认 PyPI 官方源。

---

## 高优先级改进清单（Top 5）

| 优先级 | 改进项 | 涉及维度 | 预估工作量 |
|---|---|---|---|
| 1 | **为全部 `requests.get` 补充 timeout 与重试**（统一请求封装） | 健壮性 R1 | 小 |
| 2 | **移除已失效的第三方 HTTP API，正文直连官网解密** | 供应链 C1 / 安全 S1 / 性能 | 中 |
| 3 | **补充核心纯函数测试 + CI 测试门禁** | 可维护性 M1/M2 | 中 |
| 4 | **文件名安全化 + `titles[i]` 越界保护** | 安全 S2 / 健壮性 R2 | 小 |
| 5 | **线程数上限 clamp（1~10）** | 可用性 A2 | 小 |

## 验证方式

- 语法验证：`python -m py_compile 2.py gui.py build_exe.py` ✅ 通过
- 依赖漏洞：`pip-audit -r requirements.txt` → `No known vulnerabilities found` ✅
- 功能验证（今日实测）：小说 ID `7143038691944959011`（《十日终焉》1496 章）前 5 章下载成功，正文解密正常；官网回退通道实测可用 ✅

## AI 参与声明

本报告由 CodeBuddy 代码性能与安全审计技能辅助生成。审计基于静态代码走查与自动化扫描，未进行动态渗透测试；结论仅代表审计时点的代码快照。低严重级别（🟢）提示项为可选优化，不阻塞交付。

## 审计过程

1. 建立代码地图：读取全部 7 个源码/配置文件，识别模块边界与关键路径。
2. 依赖扫描：pip-audit 扫描 requirements.txt 已知漏洞。
3. 逐维度人工走查：按安全/性能/健壮性/可用性/可维护性/供应链六维对照清单取证（含行号证据）。
4. 分级定评：🔴 阻断发布 → 🟠 应修复后发布 → 🟡 建议修复 → 🟢 可选优化。
5. 输出本报告与 Top 5 改进清单。
