"""番茄小说下载器核心引擎（可独立运行，也可被 GUI 加载）"""
import time
import requests
import bs4
import re
import os
import random
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from tqdm import tqdm

# 全局变量
cookie_path = "cookie.json"
MAX_WORKERS = 5  # 默认线程数
OUTPUT_FORMAT = "txt"  # 默认输出格式：txt（单文件）或 chapter（每章一个文件）

_cookie_cache = None  # Cookie 缓存，避免每个请求都重新生成


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


def _generate_new_cookie() -> str:
    """生成一个全新的 Cookie（带随机 novel_web_id）"""
    # 生成 19 位随机 ID（与番茄站点常见的 novel_web_id 位数一致）
    novel_web_id = random.randint(6000000000000000000, 7999999999999999999)
    return 'novel_web_id=' + str(novel_web_id)


# 获取或加载Cookie（带缓存，避免每个请求都重新生成）
def get_cookie(force: bool = False) -> Optional[str]:
    global _cookie_cache
    if not force and _cookie_cache:
        return _cookie_cache

    # 先尝试读取本地缓存的 Cookie 文件
    if not force:
        try:
            with open(cookie_path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            if isinstance(cached, str) and cached.startswith('novel_web_id='):
                _cookie_cache = cached
                return cached
        except Exception:
            pass

    # 生成并验证新 Cookie（限制重试次数，避免无限循环）
    for _ in range(5):
        time.sleep(random.uniform(0.05, 0.15))
        cookie = _generate_new_cookie()
        headers = {
            'User-Agent': get_random_user_agent(),
            'cookie': cookie,
        }
        try:
            response = fetch_url('https://fanqienovel.com', headers=headers, timeout=10)
            if response is not None and response.status_code == 200 and len(response.text) > 200:
                with open(cookie_path, 'w', encoding='utf-8') as f:
                    json.dump(cookie, f)
                _cookie_cache = cookie
                log(f"cookie已生成: {cookie}")
                return cookie
        except Exception as e:
            log(f"请求失败: {e}")
    return None


# 模拟浏览器请求
def get_headers() -> Dict[str, str]:
    cookie = get_cookie() or ''
    return {
        "User-Agent": get_random_user_agent(),
        "Cookie": cookie,
    }


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


# 加密内容解析
CODE_ST = 58344
CODE_ED = 58715
charset = ['D', '在', '主', '特', '家', '军', '然', '表', '场', '4', '要', '只', 'v', '和', '?', '6', '别', '还', 'g',
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


def interpreter(cc: int) -> str:
    """解析加密内容"""
    bias = cc - CODE_ST
    if 0 <= bias < len(charset):  # 检查bias是否在charset的有效范围内
        if charset[bias] == '?':
            return chr(cc)
        return charset[bias]
    return chr(cc)


def clean_content(content: str) -> str:
    """清理 HTML 标签并保留段落结构"""
    content = re.sub(r'<header>.*?</header>', '', content, flags=re.DOTALL)
    content = re.sub(r'<footer>.*?</footer>', '', content, flags=re.DOTALL)
    content = re.sub(r'</?article>', '', content)
    content = re.sub(r'<p idx="\d+">', '\n', content)
    content = re.sub(r'</p>', '\n', content)
    content = re.sub(r'<[^>]+>', '', content)
    content = re.sub(r'\n{2,}', '\n', content).strip()
    return '\n'.join('    ' + line if line.strip() else line for line in content.split('\n'))


def fetch_content_from_official(item_id: str, headers: Dict[str, str]) -> Optional[str]:
    """从官网 reader 页面解析并解密章节内容"""
    url = f"https://fanqienovel.com/reader/{item_id}"
    response = fetch_url(url, headers)
    if response is None:
        log(f"官网请求失败: {item_id}")
        return None
    if response.status_code != 200:
        log(f"官网请求失败，状态码: {response.status_code} ({item_id})")
        return None
    soup = bs4.BeautifulSoup(response.text, 'lxml')
    content_div = soup.find('div', class_='muye-reader-content')
    if not content_div:
        log(f"官网页面未找到正文容器: {item_id}")
        return None
    lines = []
    for p in content_div.find_all('p'):
        raw = p.get_text()
        decrypted = ''.join(interpreter(ord(ch)) for ch in raw)
        lines.append(decrypted)
    if not any(line.strip() for line in lines):
        log(f"官网正文解密后为空: {item_id}")
        return None
    return '\n'.join('    ' + line if line.strip() else line for line in lines)


def down_text(item_id: str, headers: Dict[str, str]) -> Optional[str]:
    """下载章节内容：从官网 reader 页面获取并解密，支持重试"""
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        content = fetch_content_from_official(item_id, headers)
        if content:
            return content

        retry_count += 1
        log(f"下载失败，正在重试({retry_count}/{max_retries})")
        time.sleep(2 * retry_count)

    log("达到最大重试次数，下载失败")
    return None


def extract_chapter_titles(soup: bs4.BeautifulSoup) -> List[str]:
    """提取章节标题"""
    titles = []
    for item in soup.select('div.chapter-item'):
        title = item.get_text(strip=True)
        if title:
            titles.append(title)
    return titles


def get_book_info(book_id: str, headers: Dict[str, str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """获取书名、作者、简介"""
    url = f'https://fanqienovel.com/page/{book_id}'
    response = fetch_url(url, headers)
    if response is None or response.status_code != 200:
        log(f"网络请求失败，状态码: {response.status_code if response else '未知'}")
        return None, None, None

    soup = bs4.BeautifulSoup(response.text, 'html.parser')

    # 获取书名
    name_element = soup.find('h1')
    name = name_element.text if name_element else "未知书名"

    # 获取作者
    author_name_element = soup.find('div', class_='author-name')
    author_name = None
    if author_name_element:
        author_name_span = author_name_element.find('span', class_='author-name-text')
        author_name = author_name_span.text if author_name_span else "未知作者"

    # 获取简介
    description_element = soup.find('div', class_='page-abstract-content')
    description = None
    if description_element:
        description_p = description_element.find('p')
        description = description_p.text if description_p else "无简介"

    return name, author_name, description


def sanitize_filename(name: Optional[str]) -> str:
    """过滤文件名中的非法字符（Windows 保留字符），空则回退为默认名"""
    cleaned = re.sub(r'[\\/*?:"<>|]', '', name or '').strip(' .')
    return cleaned or "未命名"


def safe_title(titles: List[str], i: int) -> str:
    """获取章节标题，索引越界时回退为默认标题，避免 IndexError"""
    return titles[i] if i < len(titles) else f"第{i + 1}章"


def download_chapter(div: Any, headers: Dict[str, str], titles: List[str], i: int, total: int) -> Tuple[int, str, Optional[str]]:
    """下载单个章节，返回 (章节索引, 标题, 内容)；失败时内容为 None"""
    if not div.a:
        log(f"第 {i + 1} 章没有链接，跳过")
        return i, safe_title(titles, i), None

    # 直接从章节目录链接提取章节 ID（/reader/{item_id}），免去每章一次详情页请求
    item_id = div.a['href'].rstrip('/').split('/')[-1]
    if not item_id:
        log(f"第 {i + 1} 章无法解析章节ID，跳过")
        return i, safe_title(titles, i), None

    # 轻微节流：错开并发请求，降低触发站点风控的概率
    time.sleep(random.uniform(0.1, 0.3))
    content = down_text(item_id, headers)

    if content:
        log(f'已下载 {i + 1}/{total}')
        return i, safe_title(titles, i), content
    else:
        log(f"第 {i + 1} 章下载失败")
        return i, safe_title(titles, i), None


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


def _fetch_catalog(book_id: str, headers: Dict[str, str]) -> Tuple[List[Any], List[str]]:
    """拉取章节列表页，返回 (章节节点列表, 标题列表)；失败返回空列表"""
    url = f'https://fanqienovel.com/page/{book_id}'
    response = fetch_url(url, headers)
    if response is None or response.status_code != 200:
        log(f"获取章节列表失败，状态码: {response.status_code if response else '未知'}")
        return [], []
    soup = bs4.BeautifulSoup(response.text, 'lxml')
    return soup.select("div.chapter-item"), extract_chapter_titles(soup)


def _download_all(li_list: List[Any], titles: List[str], headers: Dict[str, str]) -> List[Tuple[str, Optional[str]]]:
    """多线程下载全部章节，结果按原索引归位（单章失败不影响整体）"""
    total = len(li_list)
    # 结果按索引暂存，最后统一按顺序写出（避免并发写文件导致乱序/竞争）
    chapter_results: List[Optional[Tuple[str, Optional[str]]]] = [None] * total

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        for i, div in enumerate(li_list):
            futures.append(executor.submit(download_chapter, div, get_headers(), titles, i, total))

        # 使用进度条
        for _ in tqdm(as_completed(futures), total=total, desc="下载进度"):
            pass

        # 收集结果并按索引归位
        for future in futures:
            try:
                index, title, content = future.result()
                chapter_results[index] = (title, content)
            except Exception as e:
                log(f"章节下载异常: {e}")
    return chapter_results


def Run(book_id: str, save_path: str) -> None:
    """运行下载：获取书籍信息 → 章节列表 → 多线程下载 → 按格式写出"""
    global OUTPUT_FORMAT
    _clamp_workers()

    headers = get_headers()

    # 获取书籍信息
    name, author_name, description = get_book_info(book_id, headers)
    if not name:
        log("无法获取书籍信息，请检查小说ID或网络连接。")
        return

    # 获取章节列表
    li_list, titles = _fetch_catalog(book_id, headers)
    total = len(li_list)
    if total == 0:
        log("未找到任何章节，请检查小说ID是否正确。")
        return

    os.makedirs(save_path, exist_ok=True)
    log(f"使用 {MAX_WORKERS} 个线程下载，共 {total} 章")
    chapter_results = _download_all(li_list, titles, headers)

    # 组装有序章节（跳过下载失败的）
    chapters = [(i, title, content) for i, (title, content) in enumerate(chapter_results) if content]
    if not chapters:
        log("所有章节均下载失败。")
        return

    # 根据输出格式写出文件
    if OUTPUT_FORMAT == "txt":
        output_file = write_txt(save_path, name, author_name, description, chapters)
        log(f"小说已下载到: {output_file}")
    elif OUTPUT_FORMAT == "chapter":
        output_dir = write_chapters(save_path, name, author_name, description, chapters)
        log(f"小说已下载到: {output_dir}")
    elif OUTPUT_FORMAT == "epub":
        generate_epub(save_path, book_id, name, author_name, description, chapters)
    else:
        log(f"不支持的输出格式: {OUTPUT_FORMAT}（可选：txt / chapter / epub）")


def main() -> None:
    book_id = input("欢迎使用番茄小说下载器精简版！\n作者：Dlmos（Dlmily）\n基于DlmOS驱动\nGithub：https://github.com/Dlmily/Tomato-Novel-Downloader-Lite\n参考代码：https://github.com/ying-ck/fanqienovel-downloader/blob/main/src/ref_main.py\n赞助/了解新产品：https://afdian.com/a/dlbaokanluntanos\n\n请输入小说 ID：")
    save_path = input("请输入保存路径：")

    Run(book_id, save_path)
    log("下载完成！")


if __name__ == "__main__":
    main()
