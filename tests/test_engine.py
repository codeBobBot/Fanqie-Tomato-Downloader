"""下载引擎核心纯函数单元测试（不依赖网络）"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "novel_downloader", str(Path(__file__).resolve().parent.parent / "novel_downloader.py"))
n = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(n)


class TestInterpreter(unittest.TestCase):
    """解密字符映射表（FanqieSite 适配器）"""

    def test_known_char_decrypt(self):
        # CODE_ST 处 bias=0，对应 charset[0] = 'D'
        self.assertEqual(n.FanqieSite.interpreter(n.FanqieSite.CODE_ST), 'D')

    def test_out_of_range_passthrough(self):
        # 超出映射范围的字符原样返回
        c = ord('a') + 1000
        self.assertEqual(n.FanqieSite.interpreter(c), chr(c))


class TestCleanContent(unittest.TestCase):
    """HTML 清洗"""

    def test_strip_tags_and_header(self):
        html = '<header>广告头</header><p idx="1">第一段</p><p>第二段</p>'
        result = n.clean_content(html)
        self.assertIn('第一段', result)
        self.assertIn('第二段', result)
        self.assertNotIn('<header>', result)
        self.assertNotIn('<p', result)
        self.assertNotIn('广告头', result)

    def test_collapse_blank_lines(self):
        result = n.clean_content('<p>a</p>\n\n\n<p>b</p>')
        self.assertNotIn('\n\n\n', result)


class TestSanitizeFilename(unittest.TestCase):
    """文件名非法字符过滤"""

    def test_invalid_chars_removed(self):
        self.assertEqual(n.sanitize_filename('a/b\\c:d*e?f"g<h>i|j'), 'abcdefghij')

    def test_empty_and_none_fallback(self):
        self.assertEqual(n.sanitize_filename(''), '未命名')
        self.assertEqual(n.sanitize_filename(None), '未命名')

    def test_normal_name_unchanged(self):
        self.assertEqual(n.sanitize_filename('十日终焉'), '十日终焉')


class TestSafeTitle(unittest.TestCase):
    """章节标题越界保护"""

    def test_in_range(self):
        self.assertEqual(n.safe_title(['一', '二'], 0), '一')

    def test_out_of_range(self):
        self.assertEqual(n.safe_title(['一'], 5), '第6章')

    def test_empty_titles(self):
        self.assertEqual(n.safe_title([], 0), '第1章')


class TestParseChapterRange(unittest.TestCase):
    """下载范围解析（''/None=全部，N=单章，1-N=前N章）"""

    def test_none_and_empty_means_all(self):
        self.assertIsNone(n.parse_chapter_range(None))
        self.assertIsNone(n.parse_chapter_range(''))
        self.assertIsNone(n.parse_chapter_range('   '))

    def test_single_chapter(self):
        self.assertEqual(n.parse_chapter_range('5'), (4, 5))
        self.assertEqual(n.parse_chapter_range(' 3 '), (2, 3))
        self.assertEqual(n.parse_chapter_range('1'), (0, 1))

    def test_first_n_chapters(self):
        self.assertEqual(n.parse_chapter_range('1-30'), (0, 30))
        self.assertEqual(n.parse_chapter_range('1 - 5'), (0, 5))
        self.assertEqual(n.parse_chapter_range('1-1'), (0, 1))

    def test_invalid_formats_raise(self):
        for bad in ('abc', '-5', '3-', '0', '0-5', '5-3', '1-0', '5-10'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    n.parse_chapter_range(bad)


class TestWriteTxt(unittest.TestCase):
    """单文件 TXT 写出（顺序与文件名安全）"""

    def test_write_content_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            chapters = [(0, '第一章', '内容一'), (1, '第二章', '内容二')]
            path = n.write_txt(tmp, '书名/含非法:字符', '作者', '简介', chapters)
            content = Path(path).read_text(encoding='utf-8')
            self.assertTrue(Path(path).name.endswith('书名含非法字符.txt'))
            self.assertIn('内容一', content)
            self.assertLess(content.index('内容一'), content.index('内容二'))


class TestWriteChapters(unittest.TestCase):
    """分章节 TXT 写出"""

    def test_write_chapter_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            chapters = [(0, '第一章', '内容一')]
            chapter_dir = n.write_chapters(tmp, '书名', '作者', '简介', chapters)
            files = os.listdir(chapter_dir)
            self.assertEqual(len(files), 2)  # 书籍信息.txt + 1 个章节文件
            self.assertTrue(any('0001' in f for f in files))
            self.assertIn('书籍信息.txt', files)


class TestDownTextRetry(unittest.TestCase):
    """正文获取失败重试逻辑（站点无关，mock 适配器）"""

    def test_retries_three_times_then_none(self):
        original_sleep = n.time.sleep
        calls = {'count': 0}

        class FakeSite:
            def fetch_chapter(self, item_id):
                calls['count'] += 1
                return None

        n.time.sleep = lambda seconds: None  # 加速测试
        try:
            result = n._fetch_with_retry(FakeSite(), '123')
            self.assertIsNone(result)
            self.assertEqual(calls['count'], 3)  # 恰好重试 3 次
        finally:
            n.time.sleep = original_sleep

    def test_success_stops_retry(self):
        original_sleep = n.time.sleep
        calls = {'count': 0}

        class FakeSite:
            def fetch_chapter(self, item_id):
                calls['count'] += 1
                return '正文内容'

        n.time.sleep = lambda seconds: None
        try:
            result = n._fetch_with_retry(FakeSite(), '123')
            self.assertEqual(result, '正文内容')
            self.assertEqual(calls['count'], 1)  # 首次成功即返回
        finally:
            n.time.sleep = original_sleep


class TestFanqieSite(unittest.TestCase):
    """番茄适配器：目录解析与元信息提取（mock fetch_url，不依赖网络）"""

    FAKE_CATALOG_HTML = '''<html><body>
        <h1>测试书名</h1>
        <div class="author-name"><span class="author-name-text">作者甲</span></div>
        <div class="page-abstract-content"><p>这是一本测试书</p></div>
        <div class="chapter-item"><a href="https://fanqienovel.com/reader/1001">第一章 起点</a></div>
        <div class="chapter-item"><a href="https://fanqienovel.com/reader/1002">第二章 发展</a></div>
        <div class="chapter-item"><a href="https://fanqienovel.com/reader/1003">第三章 高潮</a></div>
    </body></html>'''

    def setUp(self):
        self.original_fetch_url = n.fetch_url
        n.fetch_url = lambda url, headers=None, timeout=10, max_retries=3: type(
            'FakeResponse', (), {'status_code': 200, 'text': self.FAKE_CATALOG_HTML})()
        self.site = n.FanqieSite()
        self.site.make_headers = lambda: {'User-Agent': 'test', 'Cookie': 'novel_web_id=1'}

    def tearDown(self):
        n.fetch_url = self.original_fetch_url

    def test_fetch_catalog_returns_chapter_refs(self):
        catalog = self.site.fetch_catalog('book123')
        self.assertEqual(len(catalog), 3)
        self.assertEqual(catalog[0].index, 0)
        self.assertEqual(catalog[0].title, '第一章 起点')
        self.assertEqual(catalog[0].item_id, '1001')
        self.assertEqual(catalog[2].item_id, '1003')

    def test_get_book_info_parses_meta(self):
        info = self.site.get_book_info('book123')
        self.assertIsNotNone(info)
        self.assertEqual(info.name, '测试书名')
        self.assertEqual(info.author, '作者甲')
        self.assertEqual(info.description, '这是一本测试书')

    def test_fetch_catalog_failure_returns_empty(self):
        n.fetch_url = lambda url, headers=None, timeout=10, max_retries=3: None
        self.assertEqual(self.site.fetch_catalog('book123'), [])


class TestSiteRegistry(unittest.TestCase):
    """站点注册表与 Run 站点参数"""

    def test_fanqie_registered(self):
        self.assertIn('fanqie', n.SITE_REGISTRY)
        self.assertIs(n.SITE_REGISTRY['fanqie'], n.FanqieSite)

    def test_run_rejects_unknown_site(self):
        # 未知站点应在发起任何网络请求前直接返回
        with tempfile.TemporaryDirectory() as tmp:
            n.Run('123', tmp, site='unknown_site')  # 不应抛异常


if __name__ == '__main__':
    unittest.main()
