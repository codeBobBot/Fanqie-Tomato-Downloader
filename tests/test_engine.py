"""下载引擎核心纯函数单元测试（不依赖网络）"""
import contextlib
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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
    """下载范围解析（''/None=全部，N=单章，N-M=任意区间）"""

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

    def test_range_between(self):
        self.assertEqual(n.parse_chapter_range('5-10'), (4, 10))
        self.assertEqual(n.parse_chapter_range('3 - 8'), (2, 8))
        self.assertEqual(n.parse_chapter_range('5-5'), (4, 5))
        self.assertEqual(n.parse_chapter_range('2-2'), (1, 2))

    def test_invalid_formats_raise(self):
        for bad in ('abc', '-5', '3-', '0', '0-5', '5-3', '1-0'):
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


class TestExtractChapterContent(unittest.TestCase):
    """从 __INITIAL_STATE__ 提取并解密正文；锁定章节识别"""

    @staticmethod
    def _page(is_locked=False, content='<p>第一段内容</p><p>第二段内容</p>'):
        import json as _json
        state = {'reader': {'chapterData': {'isChapterLock': is_locked, 'content': content}}}
        return f'<script>window.__INITIAL_STATE__={_json.dumps(state, ensure_ascii=False)}</script>'

    def test_unlocked_returns_decrypted_content(self):
        result = n.FanqieSite._extract_chapter_content(self._page(is_locked=False), '1')
        self.assertIn('第一段内容', result)
        self.assertIn('第二段内容', result)
        self.assertTrue(result.startswith('    '))  # 段落缩进

    def test_locked_without_content_raises(self):
        with self.assertRaises(n.ChapterLockedError):
            n.FanqieSite._extract_chapter_content(self._page(is_locked=True, content=''), '1')

    def test_no_initial_state_returns_none(self):
        self.assertIsNone(n.FanqieSite._extract_chapter_content('<html><body>x</body></html>', '1'))

    def test_locked_with_vip_placeholder_raises(self):
        # 锁定章节即使内嵌了 VIP 引导文案，也不得当作正文
        vip = '<p>扫码下载APP免费读，SVIP网页畅读</p>'
        with self.assertRaises(n.ChapterLockedError):
            n.FanqieSite._extract_chapter_content(self._page(is_locked=True, content=vip), '1')

    def test_locked_with_full_content_still_raises(self):
        # 锁定章节一律跳过，即使 content 看似完整
        with self.assertRaises(n.ChapterLockedError):
            n.FanqieSite._extract_chapter_content(self._page(is_locked=True), '1')


class TestFetchWithRetry(unittest.TestCase):
    """锁定章节跳过重试"""

    class _LockedSite(n.NovelSite):
        name = 'locked'

        def __init__(self):
            self.calls = 0

        def make_headers(self):
            return {}

        def get_book_info(self, book_id):
            return None

        def fetch_catalog(self, book_id):
            return []

        def fetch_chapter(self, item_id):
            self.calls += 1
            raise n.ChapterLockedError('locked')

    class _RetrySite(n.NovelSite):
        name = 'retry'

        def __init__(self):
            self.calls = 0

        def make_headers(self):
            return {}

        def get_book_info(self, book_id):
            return None

        def fetch_catalog(self, book_id):
            return []

        def fetch_chapter(self, item_id):
            self.calls += 1
            return None  # 永远失败，验证重试次数

    def test_locked_skips_retry(self):
        site = self._LockedSite()
        with contextlib.nullcontext():
            result = n._fetch_with_retry(site, '1')
        self.assertIsNone(result)
        self.assertEqual(site.calls, 1)  # 锁定章节只请求一次，不重试

    def test_failure_retries_three_times(self):
        site = self._RetrySite()
        with contextlib.nullcontext():
            result = n._fetch_with_retry(site, '1')
        self.assertIsNone(result)
        self.assertEqual(site.calls, 3)


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


class TestCookieFile(unittest.TestCase):
    """cookie.json 三形态解析、保存、清除与登录优先"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig_path = n.cookie_path
        self._orig_cookie_cache = n.FanqieSite._cookie_cache
        self._orig_login_cache = n.FanqieSite._login_cookie_cache
        self._orig_login_ua = n.FanqieSite._login_ua_cache
        n.cookie_path = os.path.join(self._tmp.name, 'cookie.json')
        n.FanqieSite._cookie_cache = None
        n.FanqieSite._login_cookie_cache = None
        n.FanqieSite._login_ua_cache = None
        self.addCleanup(self._restore)

    def _restore(self):
        n.cookie_path = self._orig_path
        n.FanqieSite._cookie_cache = self._orig_cookie_cache
        n.FanqieSite._login_cookie_cache = self._orig_login_cache
        n.FanqieSite._login_ua_cache = self._orig_login_ua

    def test_save_and_parse_login_cookie(self):
        n.save_login_cookie('sessionid=abc; ttwid=xyz', 'Mozilla/5.0 UA')
        cookie, ua = n._parse_cookie_file()
        self.assertEqual(cookie, 'sessionid=abc; ttwid=xyz')
        self.assertEqual(ua, 'Mozilla/5.0 UA')

    def test_load_login_credentials_returns_only_login(self):
        n.save_login_cookie('sessionid=abc', 'UA')
        self.assertEqual(n.load_login_credentials(), ('sessionid=abc', 'UA'))

    def test_legacy_string_parsed_as_guest(self):
        with open(n.cookie_path, 'w', encoding='utf-8') as f:
            json.dump('novel_web_id=123', f)
        cookie, ua = n._parse_cookie_file()
        self.assertEqual(cookie, 'novel_web_id=123')
        self.assertIsNone(ua)
        self.assertIsNone(n.load_login_credentials())

    def test_login_priority_over_guest(self):
        n.save_guest_cookie('novel_web_id=123')
        n.save_login_cookie('sessionid=abc', 'UA')
        cookie, ua = n._parse_cookie_file()
        self.assertEqual(cookie, 'sessionid=abc')  # 登录优先
        self.assertEqual(ua, 'UA')

    def test_save_guest_clears_login_cache(self):
        n.save_login_cookie('sessionid=abc', 'UA')
        n.save_guest_cookie('novel_web_id=999')
        self.assertIsNone(n.FanqieSite._login_cookie_cache)
        cookie, ua = n._parse_cookie_file()
        self.assertEqual(cookie, 'novel_web_id=999')
        self.assertIsNone(ua)

    def test_clear_cookie_file(self):
        n.save_login_cookie('sessionid=abc', 'UA')
        n.clear_cookie_file()
        self.assertFalse(os.path.exists(n.cookie_path))
        self.assertIsNone(n.FanqieSite._cookie_cache)
        self.assertIsNone(n.FanqieSite._login_cookie_cache)


class TestRateLimiter(unittest.TestCase):
    """跨线程节流器：保证请求间隔下限"""

    def test_waits_when_too_frequent(self):
        # 距上次请求不足间隔时，acquire 必须 sleep 等待（用 mock 规避 Windows 定时器精度干扰）
        limiter = n.RateLimiter(min_interval=10)
        limiter.acquire()  # 第一次：无历史请求，不等待
        with mock.patch.object(n.time, 'sleep') as mock_sleep:
            limiter.acquire()
            mock_sleep.assert_called()

    def test_no_wait_when_interval_zero(self):
        limiter = n.RateLimiter(min_interval=0)
        limiter.acquire()
        with mock.patch.object(n.time, 'sleep') as mock_sleep:
            limiter.acquire()
            mock_sleep.assert_not_called()


class TestEffectiveWorkers(unittest.TestCase):
    """登录态并发上限"""

    def setUp(self):
        self._orig = n.FanqieSite._login_cookie_cache
        n.FanqieSite._login_cookie_cache = None
        self.addCleanup(self._restore)

    def _restore(self):
        n.FanqieSite._login_cookie_cache = self._orig

    def test_guest_allows_up_to_10(self):
        self.assertEqual(n.effective_workers(5), 5)
        self.assertEqual(n.effective_workers(12), 10)

    def test_login_caps_at_2(self):
        n.FanqieSite._login_cookie_cache = 'sessionid=abc'
        self.assertEqual(n.effective_workers(5), 2)
        self.assertEqual(n.effective_workers(1), 1)


class TestRiskControl(unittest.TestCase):
    """风控信号识别"""

    @staticmethod
    def _resp(status=200, text='ok', url='https://fanqienovel.com/reader/1'):
        return type('FakeResponse', (), {'status_code': status, 'text': text, 'url': url})()

    def test_normal_page_none(self):
        self.assertIsNone(n.check_risk_control(self._resp()))

    def test_403_and_429(self):
        self.assertIsNotNone(n.check_risk_control(self._resp(status=403)))
        self.assertIsNotNone(n.check_risk_control(self._resp(status=429)))

    def test_captcha_page(self):
        self.assertIsNotNone(n.check_risk_control(self._resp(text='安全验证，请完成验证码')))
        self.assertIsNotNone(n.check_risk_control(self._resp(text='<div>captcha</div>')))

    def test_login_redirect(self):
        self.assertIsNotNone(n.check_risk_control(self._resp(url='https://fanqienovel.com/login')))


class TestFetchWithRetryRisk(unittest.TestCase):
    """风控异常不重试"""

    class _RiskSite(n.NovelSite):
        name = 'risk'

        def __init__(self):
            self.calls = 0

        def make_headers(self):
            return {}

        def get_book_info(self, book_id):
            return None

        def fetch_catalog(self, book_id):
            return []

        def fetch_chapter(self, item_id):
            self.calls += 1
            raise n.RiskControlError('HTTP 403')

    def test_risk_control_not_retried(self):
        site = self._RiskSite()
        with self.assertRaises(n.RiskControlError):
            n._fetch_with_retry(site, '1')
        self.assertEqual(site.calls, 1)


class TestVerifyLoginCookie(unittest.TestCase):
    """登录 Cookie 有效性验证"""

    @staticmethod
    def _patch_fetch(response):
        original = n.fetch_url
        n.fetch_url = lambda url, headers=None, timeout=10, max_retries=3: response
        return original

    def test_valid_cookie(self):
        resp = type('FakeResponse', (), {'status_code': 200, 'text': '<html>' + 'x' * 300,
                                         'url': 'https://fanqienovel.com'})()
        original = self._patch_fetch(resp)
        try:
            ok, msg = n.verify_login_cookie('sessionid=abc', 'UA')
            self.assertTrue(ok)
        finally:
            n.fetch_url = original

    def test_invalid_403(self):
        resp = type('FakeResponse', (), {'status_code': 403, 'text': 'forbidden',
                                         'url': 'https://fanqienovel.com/login'})()
        original = self._patch_fetch(resp)
        try:
            ok, msg = n.verify_login_cookie('sessionid=abc', 'UA')
            self.assertFalse(ok)
        finally:
            n.fetch_url = original


if __name__ == '__main__':
    unittest.main()
