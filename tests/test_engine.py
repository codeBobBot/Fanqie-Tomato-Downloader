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
    """解密字符映射表"""

    def test_known_char_decrypt(self):
        # CODE_ST 处 bias=0，对应 charset[0] = 'D'
        self.assertEqual(n.interpreter(n.CODE_ST), 'D')

    def test_out_of_range_passthrough(self):
        # 超出映射范围的字符原样返回
        c = ord('a') + 1000
        self.assertEqual(n.interpreter(c), chr(c))


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
    """正文下载失败重试逻辑"""

    def test_retries_three_times_then_none(self):
        original_fetch = n.fetch_content_from_official
        original_sleep = n.time.sleep
        calls = {'count': 0}

        def fake_fetch(item_id, headers):
            calls['count'] += 1
            return None

        n.fetch_content_from_official = fake_fetch
        n.time.sleep = lambda seconds: None  # 加速测试
        try:
            result = n.down_text('123', {'Cookie': 'x'})
            self.assertIsNone(result)
            self.assertEqual(calls['count'], 3)  # 恰好重试 3 次
        finally:
            n.fetch_content_from_official = original_fetch
            n.time.sleep = original_sleep


if __name__ == '__main__':
    unittest.main()
