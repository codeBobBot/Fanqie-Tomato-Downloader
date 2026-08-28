"""番茄小说下载器核心引擎（可独立运行，也可被 GUI 加载）

多站点适配器架构：新增站点只需实现 NovelSite 接口并注册到 SITE_REGISTRY。
公共层（请求重试/文件名安全/章节范围/写文件/多线程归位/日志）对所有站点复用。
"""
import time
import threading
import requests
import bs4
import re
import os
import random
import json
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from tqdm import tqdm

# 全局变量
cookie_path = "cookie.json"
MAX_WORKERS = 5  # 默认线程数
OUTPUT_FORMAT = "txt"  # 默认输出格式：txt（单文件）或 chapter（每章一个文件）
CHAPTER_RANGE: Optional[str] = None  # 默认下载全部章节；'N'=仅第N章，'N-M'=第N到M章


def log(*args):
    """统一日志输出：带时间戳，走 sys.stdout（GUI 通过重定向 stdout 捕获）"""
    print(f"[{time.strftime('%H:%M:%S')}]", *args)


# 获取随机User-Agent
def get_random_user_agent() -> str:
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:91.0) Gecko/20100101 Firefox/91.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36 Edg/93.0.961.47",
    ]
    return random.choice(user_agents)


class RateLimiter:
    """跨线程共享的请求节流器：保证全局请求间隔不低于 min_interval，降低触发风控的概率"""

    def __init__(self, min_interval: float = 0.0):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self) -> None:
        """请求前调用；距上次请求不足 min_interval 则阻塞补齐（线程安全）"""
        with self._lock:
            now = time.monotonic()
            wait = self._last + self.min_interval - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last = now


_global_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """返回全局节流器单例，并按当前模式同步间隔配置（登录态 1~3s，游客态 0.1~0.3s）"""
    global _global_rate_limiter
    if _global_rate_limiter is None:
        _global_rate_limiter = RateLimiter(request_interval_seconds())
    else:
        _global_rate_limiter.min_interval = request_interval_seconds()
    return _global_rate_limiter


def request_interval_seconds() -> float:
    """登录态下 1~3 秒（模拟真人阅读节奏）；游客态保持 0.1~0.3 秒"""
    if FanqieSite.is_login_mode():
        return random.uniform(1.0, 3.0)
    return random.uniform(0.1, 0.3)


def effective_workers(requested: int) -> int:
    """登录态并发封顶 2，游客态封顶 10（保护账号，避免高频并发触发风控）"""
    try:
        requested = int(requested)
    except (TypeError, ValueError):
        return 2 if FanqieSite.is_login_mode() else 5
    if FanqieSite.is_login_mode():
        return max(1, min(requested, 2))
    return max(1, min(requested, 10))


# =========================== 数据模型 ===========================

@dataclass
class BookInfo:
    """书籍元信息（站点无关）"""
    name: str
    author: Optional[str] = None
    description: Optional[str] = None


@dataclass
class ChapterRef:
    """章节引用：index 为全书顺序索引（0-based，引擎统一分配），item_id 为站点内章节标识"""
    index: int
    title: str
    item_id: str
    url: str = field(default="")


# =========================== 站点适配器抽象 ===========================

class NovelSite(ABC):
    """小说站点适配器接口：新站点实现全部抽象方法并注册到 SITE_REGISTRY 即可接入"""

    name = "base"
    display_name = "未知站点"

    @abstractmethod
    def make_headers(self) -> Dict[str, str]:
        """构造请求头（含站点各自的 Cookie/反爬策略）"""

    @abstractmethod
    def get_book_info(self, book_id: str) -> Optional[BookInfo]:
        """获取书籍元信息；失败返回 None"""

    @abstractmethod
    def fetch_catalog(self, book_id: str) -> List[ChapterRef]:
        """拉取章节列表；失败返回空列表"""

    @abstractmethod
    def fetch_chapter(self, item_id: str) -> Optional[str]:
        """获取单章正文（单次获取，重试由引擎统一处理）；失败返回 None；
        章节被锁定（VIP/需登录）时抛 ChapterLockedError 以跳过无效重试"""


# =========================== 通用工具 ===========================

def fetch_url(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 10, max_retries: int = 3) -> Optional[requests.Response]:
    """统一 HTTP GET 请求：带超时与重试，避免网络挂起导致永久阻塞；失败返回 None"""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            return response
        except requests.RequestException as e:
            if attempt < max_retries - 1:
                log(f"请求失败，正在重试({attempt + 1}/{max_retries}): {e}")
                time.sleep(1 + attempt)
            else:
                log(f"请求失败，已达最大重试次数: {e} ({url})")
    return None


def clean_content(content: str) -> str:
    """清理 HTML 标签并保留段落结构（通用 HTML 清洗）"""
    content = re.sub(r'<header>.*?</header>', '', content, flags=re.DOTALL)
    content = re.sub(r'<footer>.*?</footer>', '', content, flags=re.DOTALL)
    content = re.sub(r'</?article>', '', content)
    content = re.sub(r'<p idx="\d+">', '\n', content)
    content = re.sub(r'</p>', '\n', content)
    content = re.sub(r'<[^>]+>', '', content)
    content = re.sub(r'\n{2,}', '\n', content).strip()
    return '\n'.join('    ' + line if line.strip() else line for line in content.split('\n'))


def sanitize_filename(name: Optional[str]) -> str:
    """过滤文件名中的非法字符（Windows 保留字符），空则回退为默认名"""
    cleaned = re.sub(r'[\\/*?:"<>|]', '', name or '').strip(' .')
    return cleaned or "未命名"


def safe_title(titles: List[str], i: int) -> str:
    """获取章节标题，索引越界时回退为默认标题，避免 IndexError"""
    return titles[i] if i < len(titles) else f"第{i + 1}章"


def parse_chapter_range(range_str: Optional[str]) -> Optional[Tuple[int, int]]:
    """解析下载范围字符串，返回 0-based 半开区间 [start, end)
    支持格式：''/None=全部；'N'=仅第N章；'N-M'=下载第N到M章（含两端）
    格式非法、编号小于 1、区间倒置时抛出 ValueError"""
    if range_str is None:
        return None
    s = str(range_str).strip()
    if not s:
        return None
    m = re.fullmatch(r'(\d+)(?:\s*-\s*(\d+))?', s)
    if not m:
        raise ValueError(f"下载范围格式不合法: {s!r}（支持：留空=全部、N=仅第N章、N-M=下载第N到M章）")
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else None
    if start < 1:
        raise ValueError(f"下载范围格式不合法: {s!r}（章节编号从 1 开始）")
    if end is None:
        return start - 1, start  # 单章 [N-1, N)
    if end < start:
        raise ValueError(f"下载范围格式不合法: {s!r}（结束章节不能小于起始章节）")
    return start - 1, end  # 区间 [N-1, M)


def write_txt(save_path: str, book_name: str, author_name: Optional[str], description: Optional[str],
              chapters: List[Tuple[int, str, str]]) -> str:
    """将章节按顺序流式写入单个 TXT 文件（逐章写出，避免全量驻留内存）"""
    output_file_path = os.path.join(save_path, f"{sanitize_filename(book_name)}.txt")
    with open(output_file_path, 'w', encoding='utf-8') as f:
        f.write(f'小说名: {book_name}\n作者: {author_name or "未知作者"}\n内容简介: {description or "无简介"}\n\n')
        for _, title, content in chapters:
            f.write(f'{title}\n')
            f.write(content + '\n\n')
    return output_file_path


def write_chapters(save_path: str, book_name: str, author_name: Optional[str], description: Optional[str],
                   chapters: List[Tuple[int, str, str]]) -> str:
    """将每个章节保存为独立文件"""
    chapter_dir = os.path.join(save_path, sanitize_filename(book_name))
    os.makedirs(chapter_dir, exist_ok=True)
    info_file = os.path.join(chapter_dir, "书籍信息.txt")
    with open(info_file, 'w', encoding='utf-8') as f:
        f.write(f'小说名: {book_name}\n作者: {author_name or "未知作者"}\n内容简介: {description or "无简介"}\n')
    for i, title, content in chapters:
        chapter_title = sanitize_filename(title)
        chapter_file = os.path.join(chapter_dir, f"{i + 1:04d}_{chapter_title}.txt")
        with open(chapter_file, 'w', encoding='utf-8') as f:
            f.write(f'{title}\n\n')
            f.write(content)
    return chapter_dir


def generate_epub(save_path: str, book_id: str, book_name: str, author_name: Optional[str],
                  description: Optional[str], chapters: List[Tuple[int, str, str]]) -> Optional[str]:
    """基于有序章节数据直接生成 EPUB，不再依赖 TXT 二次解析"""
    try:
        from ebooklib import epub
        log("正在生成EPUB...")

        book = epub.EpubBook()
        book.set_identifier(f'fanqie_{book_id}')
        book.set_title(book_name)
        book.set_language('zh-CN')
        if author_name:
            book.add_author(author_name)

        # 添加CSS样式
        style = '''
        @namespace epub "http://www.idpf.org/2007/ops";
        body {
            font-family: SimSun, serif;
            line-height: 1.5;
        }
        h1 {
            text-align: center;
            margin-bottom: 1em;
        }
        '''
        nav_css = epub.EpubItem(uid="style_nav", file_name="style/nav.css", media_type="text/css", content=style)
        book.add_item(nav_css)

        # 添加简介章节
        intro = epub.EpubHtml(title="简介", file_name="intro.xhtml", lang="zh-CN")
        intro.content = f"<h1>简介</h1><p>{description or '无简介'}</p>"
        book.add_item(intro)

        # 按顺序添加各章节
        toc = [intro]
        chapter_items = []
        for i, title, content in chapters:
            c = epub.EpubHtml(title=title, file_name=f'chapter_{i}.xhtml', lang="zh-CN")
            c.content = f"<h1>{title}</h1>{content.replace(chr(10), '<br/>')}"
            book.add_item(c)
            chapter_items.append(c)
            toc.append(c)

        # 添加导航与脊柱
        book.toc = toc
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = [intro, 'nav'] + chapter_items

        # 写入EPUB文件
        epub_path = os.path.join(save_path, f"{sanitize_filename(book_name)}.epub")
        epub.write_epub(epub_path, book, {})
        log(f"EPUB格式已创建: {epub_path}")
        return epub_path
    except ImportError:
        log("EPUB转换失败: 缺少ebooklib库。您可以使用 'pip install ebooklib' 安装后重试。")
    except Exception as e:
        log(f"EPUB转换过程中出错: {str(e)}")
    return None


def _clamp_workers() -> int:
    """校验并限制线程数在 1~10，防止异常输入导致资源耗尽"""
    global MAX_WORKERS
    try:
        MAX_WORKERS = max(1, min(int(MAX_WORKERS), 10))
    except (TypeError, ValueError):
        MAX_WORKERS = 5
    return MAX_WORKERS


# =========================== 番茄小说适配器 ===========================

class FanqieSite(NovelSite):
    """番茄小说（fanqienovel.com）站点适配器：免登录伪造 Cookie + 官网页面解析 + 字符混淆解密"""

    name = "fanqie"
    display_name = "番茄小说"
    HOME = "https://fanqienovel.com"

    CODE_ST = 58344
    CODE_ED = 58715
    _CHARSET = ['D', '在', '主', '特', '家', '军', '然', '表', '场', '4', '要', '只', 'v', '和', '?', '6', '别', '还', 'g',
                '现', '儿', '岁', '?', '?', '此', '象', '月', '3', '出', '战', '工', '相', 'o', '男', '首', '失', '世', 'F',
                '都', '平', '文', '什', 'V', 'O', '将', '真', 'T', '那', '当', '?', '会', '立', '些', 'u', '是', '十', '张',
                '学', '气', '大', '爱', '两', '命', '全', '后', '东', '性', '通', '被', '1', '它', '乐', '接', '而', '感',
                '车', '山', '公', '了', '常', '以', '何', '可', '话', '先', 'p', 'i', '叫', '轻', 'M', '士', 'w', '着', '变',
                '尔', '快', 'l', '个', '说', '少', '色', '里', '安', '花', '远', '7', '难', '师', '放', 't', '报', '认',
                '面', '道', 'S', '?', '克', '地', '度', 'I', '好', '机', 'U', '民', '写', '把', '万', '同', '水', '新', '没',
                '书', '电', '吃', '像', '斯', '5', '为', 'y', '白', '几', '日', '教', '看', '但', '第', '加', '候', '作',
                '上', '拉', '住', '有', '法', 'r', '事', '应', '位', '利', '你', '声', '身', '国', '问', '马', '女', '他',
                'Y', '比', '父', 'x', 'A', 'H', 'N', 's', 'X', '边', '美', '对', '所', '金', '活', '回', '意', '到', 'z',
                '从', 'j', '知', '又', '内', '因', '点', 'Q', '三', '定', '8', 'R', 'b', '正', '或', '夫', '向', '德', '听',
                '更', '?', '得', '告', '并', '本', 'q', '过', '记', 'L', '让', '打', 'f', '人', '就', '者', '去', '原', '满',
                '体', '做', '经', 'K', '走', '如', '孩', 'c', 'G', '给', '使', '物', '?', '最', '笑', '部', '?', '员', '等',
                '受', 'k', '行', '一', '条', '果', '动', '光', '门', '头', '见', '往', '自', '解', '成', '处', '天', '能',
                '于', '名', '其', '发', '总', '母', '的', '死', '手', '入', '路', '进', '心', '来', 'h', '时', '力', '多',
                '开', '己', '许', 'd', '至', '由', '很', '界', 'n', '小', '与', 'Z', '想', '代', '么', '分', '生', '口',
                '再', '妈', '望', '次', '西', '风', '种', '带', 'J', '?', '实', '情', '才', '这', '?', 'E', '我', '神', '格',
                '长', '觉', '间', '年', '眼', '无', '不', '亲', '关', '结', '0', '友', '信', '下', '却', '重', '己', '老',
                '2', '音', '字', 'm', '呢', '明', '之', '前', '高', 'P', 'B', '目', '太', 'e', '9', '起', '稜', '她', '也',
                'W', '用', '方', '子', '英', '每', '理', '便', '西', '数', '期', '中', 'C', '外', '样', 'a', '海', '们',
                '任']

    _cookie_cache: Optional[str] = None  # 类级 Cookie 缓存，所有实例共享
    _login_cookie_cache: Optional[str] = None  # 类级登录态 Cookie 缓存（登录优先于游客）
    _login_ua_cache: Optional[str] = None  # 类级登录态 UA 缓存（与 Cookie 配套，防止设备指纹不一致）

    _LOGIN_COOKIE_MARKERS = ('sessionid', 'passport_csrf_token', 'ttwid', 'sid_tt')

    def __init__(self) -> None:
        self._session_ua: Optional[str] = None  # 会话固定 UA：首次确定后整次下载复用，不再每请求随机

    @classmethod
    def is_login_mode(cls) -> bool:
        """是否处于登录态（存在登录 Cookie 缓存）"""
        return bool(cls._login_cookie_cache)

    @classmethod
    def _looks_like_login(cls, cookie: str) -> bool:
        """通过登录特征字段判断 Cookie 是否携带登录态"""
        lower = cookie.lower()
        return any(marker in lower for marker in cls._LOGIN_COOKIE_MARKERS)

    # ---------- Cookie 与请求头 ----------

    @staticmethod
    def _generate_new_cookie() -> str:
        """生成一个全新的 Cookie（带随机 novel_web_id，与番茄站点常见的位数一致）"""
        novel_web_id = random.randint(6000000000000000000, 7999999999999999999)
        return 'novel_web_id=' + str(novel_web_id)

    def _get_cookie(self, force: bool = False) -> Optional[str]:
        """获取或生成 Cookie（类级缓存 + 本地文件缓存，避免每个请求都重新生成）
        优先级：登录态 > 文件游客态 > 新生成游客态"""
        cache = type(self)._cookie_cache
        if not force and cache:
            return cache

        # 先尝试读取本地缓存的 Cookie 文件（支持登录态/游客态/旧版纯字符串）
        if not force:
            cookie, ua = _parse_cookie_file()
            if cookie:
                type(self)._cookie_cache = cookie
                if self._looks_like_login(cookie):
                    type(self)._login_cookie_cache = cookie
                    type(self)._login_ua_cache = ua
                return cookie

        # 生成并验证新 Cookie（游客态；限制重试次数，避免无限循环）
        for _ in range(5):
            time.sleep(random.uniform(0.05, 0.15))
            cookie = self._generate_new_cookie()
            headers = {
                'User-Agent': get_random_user_agent(),
                'cookie': cookie,
            }
            try:
                response = fetch_url(self.HOME, headers=headers, timeout=10)
                if response is not None and response.status_code == 200 and len(response.text) > 200:
                    with open(cookie_path, 'w', encoding='utf-8') as f:
                        json.dump({'type': 'guest', 'cookie': cookie}, f)
                    type(self)._cookie_cache = cookie
                    log(f"cookie已生成: {cookie}")
                    return cookie
            except Exception as e:
                log(f"请求失败: {e}")
        return None

    def make_headers(self) -> Dict[str, str]:
        cookie = self._get_cookie() or ''
        # 会话固定 UA：登录态优先用用户粘贴的 UA（与 Cookie 配套），否则会话内首次随机后固定复用
        ua = self._session_ua
        if not ua:
            ua = self._login_ua_cache or get_random_user_agent()
            self._session_ua = ua
        return {
            "User-Agent": ua,
            "Cookie": cookie,
        }

    # ---------- 正文解密 ----------

    @staticmethod
    def interpreter(cc: int) -> str:
        """解析加密内容：Unicode 码点 → 常用汉字映射（番茄官网 reader 页字符混淆）"""
        bias = cc - FanqieSite.CODE_ST
        charset = FanqieSite._CHARSET
        if 0 <= bias < len(charset):  # 检查bias是否在charset的有效范围内
            if charset[bias] == '?':
                return chr(cc)
            return charset[bias]
        return chr(cc)

    # ---------- 元信息 / 目录 / 正文 ----------

    def get_book_info(self, book_id: str) -> Optional[BookInfo]:
        """获取书名、作者、简介；命中风控信号抛 RiskControlError"""
        url = f'{self.HOME}/page/{book_id}'
        response = fetch_url(url, self.make_headers())
        if response is None:
            log("网络请求失败")
            return None
        reason = check_risk_control(response)
        if reason:
            raise RiskControlError(reason)
        if response.status_code != 200:
            log(f"网络请求失败，状态码: {response.status_code}")
            return None

        soup = bs4.BeautifulSoup(response.text, 'html.parser')

        name_element = soup.find('h1')
        name = name_element.text if name_element else "未知书名"

        author_name = None
        author_name_element = soup.find('div', class_='author-name')
        if author_name_element:
            author_name_span = author_name_element.find('span', class_='author-name-text')
            author_name = author_name_span.text if author_name_span else "未知作者"

        description = None
        description_element = soup.find('div', class_='page-abstract-content')
        if description_element:
            description_p = description_element.find('p')
            description = description_p.text if description_p else "无简介"

        return BookInfo(name=name, author=author_name, description=description)

    def fetch_catalog(self, book_id: str) -> List[ChapterRef]:
        """拉取章节列表，返回统一的 ChapterRef 列表；命中风控信号抛 RiskControlError"""
        url = f'{self.HOME}/page/{book_id}'
        response = fetch_url(url, self.make_headers())
        if response is None:
            log("获取章节列表失败")
            return []
        reason = check_risk_control(response)
        if reason:
            raise RiskControlError(reason)
        if response.status_code != 200:
            log(f"获取章节列表失败，状态码: {response.status_code}")
            return []
        soup = bs4.BeautifulSoup(response.text, 'lxml')
        items = soup.select("div.chapter-item")
        titles = self._extract_chapter_titles(soup)

        catalog: List[ChapterRef] = []
        for i, div in enumerate(items):
            href = div.a['href'].rstrip('/') if div.a and div.a.get('href') else ''
            item_id = href.split('/')[-1] if href else ''
            catalog.append(ChapterRef(index=i, title=safe_title(titles, i), item_id=item_id, url=href))
        return catalog

    @staticmethod
    def _extract_chapter_titles(soup: bs4.BeautifulSoup) -> List[str]:
        """提取章节标题（番茄官网目录结构）"""
        titles = []
        for item in soup.select('div.chapter-item'):
            title = item.get_text(strip=True)
            if title:
                titles.append(title)
        return titles

    def fetch_chapter(self, item_id: str) -> Optional[str]:
        """从官网 reader 页面解析并解密章节内容（单次获取，重试由引擎统一处理）
        章节被锁定（VIP/需登录）时抛出 ChapterLockedError；命中风控信号抛 RiskControlError"""
        url = f"{self.HOME}/reader/{item_id}"
        response = fetch_url(url, self.make_headers())
        if response is None:
            log(f"官网请求失败: {item_id}")
            return None
        reason = check_risk_control(response)
        if reason:
            raise RiskControlError(reason)
        if response.status_code != 200:
            log(f"官网请求失败，状态码: {response.status_code} ({item_id})")
            return None
        # 优先读取页面内嵌正文数据（__INITIAL_STATE__）；锁定章节在此识别，避免把 VIP 引导文案当作正文
        content = self._extract_chapter_content(response.text, item_id)
        if content is not None:
            return content
        # 后备：解析渲染后的正文容器（兼容页面结构变化）
        soup = bs4.BeautifulSoup(response.text, 'lxml')
        content_div = soup.find('div', class_='muye-reader-content')
        if not content_div:
            log(f"官网页面未找到正文容器: {item_id}")
            return None
        lines = []
        for p in content_div.find_all('p'):
            raw = p.get_text()
            decrypted = ''.join(self.interpreter(ord(ch)) for ch in raw)
            lines.append(decrypted)
        if not any(line.strip() for line in lines):
            log(f"官网正文解密后为空: {item_id}")
            return None
        return '\n'.join('    ' + line if line.strip() else line for line in lines)

    @classmethod
    def _extract_chapter_content(cls, page_text: str, item_id: str) -> Optional[str]:
        """从 __INITIAL_STATE__.reader.chapterData.content 提取并解密正文。
        返回解密后的正文；章节被锁定且无可用正文时抛 ChapterLockedError；页面无内嵌数据返回 None"""
        marker = 'window.__INITIAL_STATE__='
        i = page_text.find(marker)
        if i < 0:
            return None
        try:
            j = page_text.find('{', i + len(marker))
            state, _ = json.JSONDecoder().raw_decode(page_text[j:])
            chapter = state.get('reader', {}).get('chapterData', {})
        except Exception:
            return None
        # 锁定章节一律跳过（页面中的 VIP 引导文案不是正文，不能混入下载结果）
        if chapter.get('isChapterLock'):
            raise ChapterLockedError("章节已锁定，需登录番茄账号或使用 APP 阅读")
        content = chapter.get('content')
        if isinstance(content, str) and content.strip():
            soup = bs4.BeautifulSoup(content, 'lxml')
            lines = []
            for p in soup.find_all('p'):
                raw = p.get_text()
                decrypted = ''.join(cls.interpreter(ord(ch)) for ch in raw)
                lines.append(decrypted)
            if any(line.strip() for line in lines):
                return '\n'.join('    ' + line if line.strip() else line for line in lines)
        return None


# ---------- cookie.json 文件读写（登录态 / 游客态，向后兼容） ----------

def _write_cookie_file(data: Dict[str, Any]) -> None:
    with open(cookie_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def _parse_cookie_file() -> Tuple[Optional[str], Optional[str]]:
    """解析 cookie.json，返回 (cookie, ua)。
    形态A 登录态 / 形态B 游客态 / 形态C 旧版纯字符串（视为游客态）；解析失败返回 (None, None)"""
    try:
        with open(cookie_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return None, None
    if isinstance(data, dict):
        cookie = data.get('cookie')
        if isinstance(cookie, str) and cookie.strip():
            return cookie, (data.get('ua') or None)
        return None, None
    if isinstance(data, str) and data.strip():
        return data, None  # 旧版纯字符串：视为游客 Cookie
    return None, None


def save_login_cookie(cookie: str, ua: str) -> None:
    """保存登录态 Cookie（Cookie + 配套 UA）到 cookie.json，并更新类级缓存"""
    _write_cookie_file({'type': 'login', 'cookie': cookie, 'ua': ua or ''})
    FanqieSite._cookie_cache = cookie
    FanqieSite._login_cookie_cache = cookie
    FanqieSite._login_ua_cache = ua or None


def save_guest_cookie(cookie: str) -> None:
    """保存游客态 Cookie 到 cookie.json，并清除登录态缓存"""
    _write_cookie_file({'type': 'guest', 'cookie': cookie})
    FanqieSite._cookie_cache = cookie
    FanqieSite._login_cookie_cache = None
    FanqieSite._login_ua_cache = None


def clear_cookie_file() -> None:
    """删除 cookie.json 并清空类级缓存（登录态与游客态均清除）"""
    try:
        os.remove(cookie_path)
    except OSError:
        pass
    FanqieSite._cookie_cache = None
    FanqieSite._login_cookie_cache = None
    FanqieSite._login_ua_cache = None


def load_login_credentials() -> Optional[Tuple[str, str]]:
    """读取 cookie.json 中的登录态；不存在或为游客态时返回 None"""
    cookie, ua = _parse_cookie_file()
    if cookie and FanqieSite._looks_like_login(cookie):
        return cookie, ua
    return None


def verify_login_cookie(cookie: str, ua: str) -> Tuple[bool, str]:
    """用给定 Cookie + UA 请求站点首页验证登录态有效性。
    返回 (是否有效, 说明)；网络异常视为"无法验证"而非"Cookie 无效"（False + 网络提示）"""
    headers = {
        'User-Agent': ua or get_random_user_agent(),
        'Cookie': cookie,
    }
    try:
        response = fetch_url(FanqieSite.HOME, headers=headers, timeout=10)
    except Exception as e:
        return False, f"网络异常: {e}"
    if response is None:
        return False, "网络请求失败"
    reason = check_risk_control(response)
    if reason:
        return False, reason
    if getattr(response, 'status_code', None) == 200 and len(getattr(response, 'text', '') or '') > 200:
        return True, "Cookie 有效"
    return False, f"异常响应（HTTP {getattr(response, 'status_code', '未知')}）"


# 兼容入口：默认使用番茄站点（供外部脚本 / 旧接口引用）
def get_cookie(force: bool = False) -> Optional[str]:
    """兼容入口：使用默认番茄站点获取 Cookie"""
    return FanqieSite()._get_cookie(force)


def get_headers() -> Dict[str, str]:
    """兼容入口：使用默认番茄站点构造请求头"""
    return FanqieSite().make_headers()


# =========================== 站点注册表 ===========================

SITE_REGISTRY: Dict[str, type] = {
    "fanqie": FanqieSite,
}


# =========================== 下载编排（站点无关） ===========================

class ChapterLockedError(Exception):
    """章节被锁定（VIP/需登录），无法获取正文"""


class RiskControlError(Exception):
    """检测到风控信号（403/429/验证码页/登录跳转），立即停止下载以保护账号"""


def check_risk_control(response: Optional[requests.Response]) -> Optional[str]:
    """风控体检：命中 403/429、验证码页、登录跳转时返回命中原因，否则返回 None"""
    if response is None:
        return None
    status = getattr(response, 'status_code', None)
    if status in (403, 429):
        return f"HTTP {status}（疑似风控限流）"
    text = getattr(response, 'text', '') or ''
    lowered = text.lower()
    for marker in ('captcha', '安全验证'):
        if marker.lower() in lowered:
            return f"检测到验证码/风控页面（{marker}）"
    url = getattr(response, 'url', '') or ''
    if 'login' in url.lower():
        return "检测到跳转登录页（Cookie 可能已失效）"
    return None


def _fetch_with_retry(site: NovelSite, item_id: str) -> Optional[str]:
    """统一正文获取重试逻辑：锁定章节直接跳过；风控异常立即上抛不重试；其余失败重试 3 次"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            content = site.fetch_chapter(item_id)
        except ChapterLockedError as e:
            log(f"章节已锁定，跳过: {item_id}（{e}）")
            return None
        except RiskControlError:
            raise  # 风控信号：绝不重试，立即停止保护账号
        if content:
            return content
        log(f"下载失败，正在重试({attempt + 1}/{max_retries})")
        time.sleep(2 * (attempt + 1))
    log("达到最大重试次数，下载失败")
    return None


def download_chapter(site: NovelSite, ref: ChapterRef, total: int) -> Tuple[int, str, Optional[str]]:
    """下载单个章节，返回 (索引, 标题, 内容)；失败时内容为 None"""
    if not ref.item_id:
        log(f"第 {ref.index + 1} 章没有链接或无法解析章节ID，跳过")
        return ref.index, ref.title, None

    # 全局节流：登录态 1~3s/请求、游客态 0.1~0.3s/请求，错开并发以降低触发风控的概率
    get_rate_limiter().acquire()
    content = _fetch_with_retry(site, ref.item_id)

    if content:
        log(f'已下载 {ref.index + 1}/{total}')
        return ref.index, ref.title, content
    else:
        log(f"第 {ref.index + 1} 章下载失败")
        return ref.index, ref.title, None


def _download_all(site: NovelSite, catalog: List[ChapterRef],
                  start: Optional[int] = None, end: Optional[int] = None) -> List[Optional[Tuple[str, Optional[str]]]]:
    """多线程下载章节，结果按原索引归位（单章失败不影响整体）
    start/end 为 0-based 半开区间 [start, end)，None 表示全部；索引对应全书真实章节号"""
    total = len(catalog)
    indices = range(start, end) if start is not None else range(total)
    count = len(indices)
    # 结果按索引暂存，最后统一按顺序写出（避免并发写文件导致乱序/竞争）
    chapter_results: List[Optional[Tuple[str, Optional[str]]]] = [None] * total

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        for i in indices:
            futures.append(executor.submit(download_chapter, site, catalog[i], total))

        # 使用进度条（进度为本次范围内的章节数）
        for _ in tqdm(as_completed(futures), total=count, desc="下载进度"):
            pass

        # 收集结果并按索引归位；命中风控信号则取消全部任务并上抛（保护账号）
        risk_error: Optional[RiskControlError] = None
        for future in futures:
            try:
                index, title, content = future.result()
                chapter_results[index] = (title, content)
            except RiskControlError as e:
                risk_error = e
                for f in futures:
                    f.cancel()
                break
            except Exception as e:
                log(f"章节下载异常: {e}")
    if risk_error:
        raise risk_error
    return chapter_results


def Run(book_id: str, save_path: str, chapter_range: Optional[str] = None, site: str = "fanqie") -> None:
    """运行下载：获取书籍信息 → 章节列表 → 多线程下载 → 按格式写出
    chapter_range: None/''=全部；'N'=仅第N章；'1-N'=前N章；未传时使用全局 CHAPTER_RANGE
    site: 站点标识（SITE_REGISTRY 的 key，默认 fanqie）"""
    global OUTPUT_FORMAT, CHAPTER_RANGE, MAX_WORKERS
    _clamp_workers()
    # 登录态下执行保守防风控策略：并发封顶 2、请求间隔 1~3s
    if FanqieSite.is_login_mode():
        MAX_WORKERS = effective_workers(MAX_WORKERS)
        log("登录模式：保守限速（≤2 线程 / 1~3s 请求间隔），检测到风控信号将立即停止以保护账号")
    else:
        log("游客模式：使用匿名 Cookie，部分章节可能被锁定")

    # 解析站点
    site_cls = SITE_REGISTRY.get(site)
    if site_cls is None:
        log(f"不支持的站点: {site!r}（可选: {', '.join(SITE_REGISTRY)}）")
        return
    site_obj = site_cls()

    # 解析章节范围（显式参数优先于全局变量），格式非法直接返回
    range_str = chapter_range if chapter_range is not None else CHAPTER_RANGE
    try:
        parsed = parse_chapter_range(range_str)
    except ValueError as e:
        log(str(e))
        return
    start = parsed[0] if parsed else None
    end = parsed[1] if parsed else None

    # 获取书籍信息（风控信号直接停止）
    try:
        info = site_obj.get_book_info(book_id)
    except RiskControlError as e:
        log(f"检测到风控信号，已停止下载以保护账号: {e}")
        return
    if info is None or not info.name:
        log("无法获取书籍信息，请检查小说ID或网络连接。")
        return

    # 获取章节列表（风控信号直接停止）
    try:
        catalog = site_obj.fetch_catalog(book_id)
    except RiskControlError as e:
        log(f"检测到风控信号，已停止下载以保护账号: {e}")
        return
    total = len(catalog)
    if total == 0:
        log("未找到任何章节，请检查小说ID是否正确。")
        return

    # 校验范围边界并截断越界部分
    if start is not None:
        if start >= total:
            log(f"章节范围超出：第{start + 1}章不存在（全书共 {total} 章）。")
            return
        if end > total:
            log(f"章节范围超出：第{end}章不存在（全书共 {total} 章），已截断为第{start + 1}-{total}章。")
            end = total
        log(f"本次下载范围: 第 {start + 1} - {end} 章（共 {end - start} 章）")

    os.makedirs(save_path, exist_ok=True)
    log(f"使用 {MAX_WORKERS} 个线程下载，共 {total} 章")
    try:
        chapter_results = _download_all(site_obj, catalog, start, end)
    except RiskControlError as e:
        log(f"检测到风控信号，已停止下载以保护账号: {e}")
        return

    # 组装有序章节（跳过未下载/下载失败的；范围下载时未选中的索引为 None）
    chapters = [(i, result[0], result[1]) for i, result in enumerate(chapter_results)
                if result is not None and result[1]]
    if not chapters:
        log("所有章节均下载失败。")
        return

    # 汇总提示：范围下载时用实际选取的章节数，全量下载时用全书章节数
    attempted = (end - start) if start is not None else total
    skipped = attempted - len(chapters)
    if skipped > 0:
        log(f"共 {len(chapters)} 章下载成功，{skipped} 章失败或被跳过（锁定章节需登录番茄账号，详见上方日志）")

    # 根据输出格式写出文件
    if OUTPUT_FORMAT == "txt":
        output_file = write_txt(save_path, info.name, info.author, info.description, chapters)
        log(f"小说已下载到: {output_file}")
    elif OUTPUT_FORMAT == "chapter":
        output_dir = write_chapters(save_path, info.name, info.author, info.description, chapters)
        log(f"小说已下载到: {output_dir}")
    elif OUTPUT_FORMAT == "epub":
        generate_epub(save_path, book_id, info.name, info.author, info.description, chapters)
    else:
        log(f"不支持的输出格式: {OUTPUT_FORMAT}（可选：txt / chapter / epub）")


def main() -> None:
    book_id = input("欢迎使用番茄小说下载器精简版！\n作者：Dlmos（Dlmily）\n基于DlmOS驱动\nGithub：https://github.com/Dlmily/Tomato-Novel-Downloader-Lite\n参考代码：https://github.com/ying-ck/fanqienovel-downloader/blob/main/src/ref_main.py\n赞助/了解新产品：https://afdian.com/a/dlbaokanluntanos\n\n请输入小说 ID：")
    save_path = input("请输入保存路径：")
    range_str = input("下载范围（留空=全部；N=仅第N章；N-M=第N到M章）：").strip()
    site = input(f"小说网站（可选: {', '.join(SITE_REGISTRY)}，默认 fanqie）：").strip() or "fanqie"

    Run(book_id, save_path, range_str or None, site)
    log("下载完成！")


if __name__ == "__main__":
    main()
