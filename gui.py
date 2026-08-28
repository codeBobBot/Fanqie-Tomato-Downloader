import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os
import sys
import time
import queue
from tqdm import tqdm
import importlib.util

# 获取正确的核心引擎文件路径
def get_script_path():
    if getattr(sys, 'frozen', False):
        # 如果是打包后的环境
        return os.path.join(sys._MEIPASS, "novel_downloader.py")
    else:
        # 如果是开发环境
        return "novel_downloader.py"

# 导入核心引擎中的函数
script_path = get_script_path()
spec = importlib.util.spec_from_file_location("novel_downloader", script_path)
novel_downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(novel_downloader)

class RedirectText:
    """将下载线程的输出写入消息队列，由 GUI 主线程轮询显示，避免跨线程操作 UI"""
    def __init__(self, msg_queue):
        self.msg_queue = msg_queue

    def write(self, string):
        self.msg_queue.put(("log", string))

    def flush(self):
        pass

class CustomTqdm(tqdm):
    """自定义tqdm进度条，将更新通过消息队列发送到GUI，避免跨线程直接操作UI"""
    def __init__(self, *args, progress_queue=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_queue = progress_queue
        self._last_update_time = 0
        self._update_interval = 0.1  # 更新UI的间隔时间（秒），避免UI卡顿

    def update(self, n=1):
        displayed = super().update(n)
        # 限制更新频率
        current_time = time.time()
        if current_time - self._last_update_time > self._update_interval:
            if self.progress_queue is not None:
                percentage = int(self.n / self.total * 100) if self.total else 0
                self.progress_queue.put(("progress", percentage, self.n, self.total))
            self._last_update_time = current_time
        return displayed

class NovelDownloaderGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("通用小说下载器")
        self.geometry("800x600")
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # 默认设置
        self.threads_var = tk.StringVar(value="5")
        self.format_var = tk.StringVar(value="txt")
        self.site_var = tk.StringVar(value="fanqie")
        self.create_widgets()
        self.is_downloading = False
        self.download_thread = None

        # 回填已保存的登录 Cookie（若 cookie.json 中存在登录态）
        self.load_saved_login()
        
        # 设置图标（如果有的话）
        try:
            self.iconbitmap("icon.ico")
        except:
            pass
            
    def create_widgets(self):
        # 创建主框架
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 创建输入区域
        input_frame = ttk.LabelFrame(main_frame, text="输入信息", padding="10")
        input_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # 小说网站选择
        ttk.Label(input_frame, text="小说网站:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.site_combo = ttk.Combobox(input_frame, textvariable=self.site_var,
                                       values=list(novel_downloader.SITE_REGISTRY),
                                       state="readonly", width=30)
        self.site_combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)

        # 小说ID输入
        ttk.Label(input_frame, text="小说ID:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.book_id_entry = ttk.Entry(input_frame, width=50)
        self.book_id_entry.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(input_frame, text="(从小说网址中获取)").grid(row=1, column=2, sticky=tk.W, padx=5, pady=5)
        
        # 保存路径输入
        ttk.Label(input_frame, text="保存路径:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        self.save_path_entry = ttk.Entry(input_frame, width=50)
        self.save_path_entry.grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)
        self.save_path_entry.insert(0, os.path.join(os.getcwd(), "novels"))
        browse_button = ttk.Button(input_frame, text="浏览", command=self.browse_folder)
        browse_button.grid(row=2, column=2, sticky=tk.W, padx=5, pady=5)
        
        # 线程数选择
        ttk.Label(input_frame, text="线程数:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=5)
        threads_frame = ttk.Frame(input_frame)
        threads_frame.grid(row=3, column=1, sticky=tk.W, padx=5, pady=5)
        for i in range(1, 11):
            ttk.Radiobutton(threads_frame, text=str(i), value=str(i), variable=self.threads_var).pack(side=tk.LEFT, padx=2)
        
        # 输出格式选择
        ttk.Label(input_frame, text="输出格式:").grid(row=4, column=0, sticky=tk.W, padx=5, pady=5)
        format_frame = ttk.Frame(input_frame)
        format_frame.grid(row=4, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Radiobutton(format_frame, text="TXT", value="txt", variable=self.format_var).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(format_frame, text="EPUB", value="epub", variable=self.format_var).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(format_frame, text="分章节TXT", value="chapter", variable=self.format_var).pack(side=tk.LEFT, padx=10)
        
        # 下载范围输入
        ttk.Label(input_frame, text="下载范围:").grid(row=5, column=0, sticky=tk.W, padx=5, pady=5)
        self.range_entry = ttk.Entry(input_frame, width=20)
        self.range_entry.grid(row=5, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(input_frame, text="(留空=全部 | N=仅第N章 | N-M=第N到M章)").grid(row=5, column=2, sticky=tk.W, padx=5, pady=5)
        
        # 登录 Cookie 与 User-Agent（可选，留空=游客模式；登录后可下载被锁定的章节）
        ttk.Label(input_frame, text="登录Cookie:").grid(row=6, column=0, sticky=tk.W, padx=5, pady=5)
        self.cookie_entry = ttk.Entry(input_frame, width=40)
        self.cookie_entry.grid(row=6, column=1, sticky=tk.W, padx=5, pady=5)
        clear_button = ttk.Button(input_frame, text="清除Cookie", command=self.clear_cookie)
        clear_button.grid(row=6, column=2, sticky=tk.W, padx=5, pady=5)
        ttk.Label(input_frame, text="User-Agent:").grid(row=7, column=0, sticky=tk.W, padx=5, pady=5)
        self.ua_entry = ttk.Entry(input_frame, width=50)
        self.ua_entry.grid(row=7, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(input_frame, text="(可选) 浏览器 F12→Network→请求头 复制 Cookie 与 User-Agent；登录后可解锁锁定章节，留空=游客模式").grid(
            row=8, column=1, columnspan=3, sticky=tk.W, padx=5, pady=2)
        
        # 下载按钮
        self.download_button = ttk.Button(input_frame, text="开始下载", command=self.start_download)
        self.download_button.grid(row=9, column=1, pady=10)
        
        # 进度条区域
        progress_frame = ttk.LabelFrame(main_frame, text="下载进度", padding="10")
        progress_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.progress_var = tk.IntVar()
        self.progress_bar = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL, length=100, mode='determinate', variable=self.progress_var)
        self.progress_bar.pack(fill=tk.X, padx=5, pady=5)
        
        self.progress_label = ttk.Label(progress_frame, text="准备就绪")
        self.progress_label.pack(anchor=tk.W, padx=5)
        
        # 日志区域
        log_frame = ttk.LabelFrame(main_frame, text="下载日志", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, width=80, height=15)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)
        
        # 滚动条
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        
        # 底部信息
        info_label = ttk.Label(main_frame, text="作者: Dlmos (Dlmily) | 基于DlmOS驱动 | GitHub: https://github.com/Dlmily/Tomato-Novel-Downloader-Lite", font=("Arial", 8))
        info_label.pack(side=tk.BOTTOM, pady=5)
        
    def browse_folder(self):
        folder_path = filedialog.askdirectory()
        if folder_path:
            self.save_path_entry.delete(0, tk.END)
            self.save_path_entry.insert(0, folder_path)
            
    def start_download(self):
        if self.is_downloading:
            messagebox.showinfo("提示", "下载已在进行中")
            return
            
        book_id = self.book_id_entry.get().strip()
        save_path = self.save_path_entry.get().strip()
        
        if not book_id:
            messagebox.showerror("错误", "请输入小说ID")
            return
            
        if not save_path:
            messagebox.showerror("错误", "请选择保存路径")
            return

        # 校验下载范围格式（语法层面；越界在引擎内提示）
        chapter_range = self.range_entry.get().strip()
        try:
            novel_downloader.parse_chapter_range(chapter_range)
        except ValueError as e:
            messagebox.showerror("错误", str(e))
            return

        # 校验小说网站
        site = self.site_var.get()
        if site not in novel_downloader.SITE_REGISTRY:
            messagebox.showerror("错误", f"不支持的网站: {site}")
            return

        # 检查EPUB格式所需的库
        output_format = self.format_var.get()
        if output_format == "epub":
            try:
                import ebooklib
            except ImportError:
                response = messagebox.askyesno("缺少依赖", "转换为EPUB格式需要安装'ebooklib'库。是否现在安装？")
                if response:
                    self.install_ebooklib()
                    # 安装后尝试再次导入
                    try:
                        import ebooklib
                    except ImportError:
                        messagebox.showerror("错误", "安装ebooklib失败，请尝试手动安装：pip install ebooklib")
                        return
                else:
                    return
            
        # 确保保存路径存在
        try:
            os.makedirs(save_path, exist_ok=True)
        except Exception as e:
            messagebox.showerror("错误", f"创建保存路径失败: {str(e)}")
            return

        # 登录 Cookie 校验与持久化（留空=游客模式；已保存的登录态自动生效）
        login_cookie = self.cookie_entry.get().strip()
        login_ua = self.ua_entry.get().strip()
        if login_cookie:
            ok, msg = novel_downloader.verify_login_cookie(login_cookie, login_ua)
            if not ok:
                if msg.startswith("网络"):
                    messagebox.showerror("验证失败", f"无法验证登录 Cookie（{msg}）。\n请确认网络连接正常后重试。")
                else:
                    messagebox.showerror("Cookie 无效",
                                         f"登录 Cookie 验证失败：{msg}\n\n请重新从浏览器复制 Cookie 与 User-Agent"
                                         "（F12 → Network → 任意请求 → Request Headers → 复制 Cookie 和 User-Agent 两行）。")
                return
            novel_downloader.save_login_cookie(login_cookie, login_ua)
        elif novel_downloader.load_login_credentials():
            # 未填写但文件已有登录态：保留并使用（引擎自动加载）
            pass

        # 准备下载
        self.is_downloading = True
        self.download_button.config(text="下载中...", state=tk.DISABLED)
        self.progress_var.set(0)
        self.progress_label.config(text="开始下载...")
        
        # 清空日志
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        
        # 通过消息队列与下载线程通信，避免跨线程直接操作UI
        self.msg_queue = queue.Queue()
        self.stdout_redirect = RedirectText(self.msg_queue)
        sys.stdout = self.stdout_redirect
        
        # 替换tqdm类，使其通过消息队列更新GUI进度条
        novel_downloader.tqdm = lambda *args, **kwargs: CustomTqdm(
            *args, **kwargs, progress_queue=self.msg_queue
        )
        
        # 启动主线程消息轮询
        self.after(50, self.poll_queue)
        
        # 设置线程数和输出格式
        threads = int(self.threads_var.get())
        output_format = self.format_var.get()
        novel_downloader.MAX_WORKERS = threads
        novel_downloader.OUTPUT_FORMAT = output_format
        
        # 在新线程中运行下载
        self.download_thread = threading.Thread(target=self.run_download,
                                                 args=(book_id, save_path, output_format, chapter_range, site))
        self.download_thread.daemon = True
        self.download_thread.start()
        
    def poll_queue(self):
        """主线程轮询消息队列，安全更新UI"""
        drained = False
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                drained = True
                if msg[0] == "log":
                    self.log_text.config(state=tk.NORMAL)
                    self.log_text.insert(tk.END, msg[1])
                    self.log_text.see(tk.END)
                    self.log_text.config(state=tk.DISABLED)
                elif msg[0] == "progress":
                    _, percentage, n, total = msg
                    self.progress_var.set(percentage)
                    self.progress_label.configure(text=f"下载进度: {percentage}% ({n}/{total})")
        except queue.Empty:
            pass
        # 下载中或仍有未处理消息时继续轮询
        if self.is_downloading or drained:
            self.after(50, self.poll_queue)

    def load_saved_login(self):
        """启动时若 cookie.json 保存了登录态，则回填 Cookie 与 UA 输入框"""
        try:
            creds = novel_downloader.load_login_credentials()
        except Exception:
            creds = None
        if creds:
            self.cookie_entry.insert(0, creds[0])
            self.ua_entry.insert(0, creds[1] or "")

    def clear_cookie(self):
        """清除 cookie.json 与输入框内容，回退游客模式"""
        try:
            novel_downloader.clear_cookie_file()
        except Exception as e:
            messagebox.showerror("错误", f"清除 Cookie 失败: {e}")
            return
        self.cookie_entry.delete(0, tk.END)
        self.ua_entry.delete(0, tk.END)
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, "已清除登录 Cookie，回退游客模式。\n")
        self.log_text.config(state=tk.DISABLED)

    def run_download(self, book_id, save_path, output_format, chapter_range="", site="fanqie"):
        try:
            print(f"开始下载小说 ID: {book_id}")
            print(f"小说网站: {site}")
            print(f"保存路径: {save_path}")
            print(f"使用线程数: {novel_downloader.MAX_WORKERS}")
            print(f"输出格式: {output_format}")
            print(f"下载范围: {chapter_range if chapter_range else '全部章节'}")
            mode = ("登录模式（保守限速 ≤2 线程 / 1~3s 请求间隔）"
                    if novel_downloader.FanqieSite.is_login_mode() else "游客模式")
            print(f"运行模式: {mode}")
            novel_downloader.Run(book_id, save_path, chapter_range or None, site)
            self.after(100, self.download_complete, "下载完成！")
        except Exception as e:
            self.after(100, self.download_complete, f"下载出错: {str(e)}")
        finally:
            # 无论成功失败都恢复标准输出
            sys.stdout = sys.__stdout__

    def download_complete(self, message):
        self.is_downloading = False
        self.download_button.config(text="开始下载", state=tk.NORMAL)
        self.progress_label.config(text=message)
        messagebox.showinfo("下载状态", message)
    
    def install_ebooklib(self):
        """安装ebooklib库"""
        try:
            self.log_text.config(state=tk.NORMAL)
            self.log_text.delete(1.0, tk.END)
            self.log_text.insert(tk.END, "正在安装ebooklib库...\n")
            self.log_text.config(state=tk.DISABLED)
            
            # 打包后的可执行环境中 sys.executable 是 exe 本身，无法用 -m pip
            if getattr(sys, 'frozen', False):
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, "当前为打包版本，请手动安装：pip install ebooklib\n")
                self.log_text.config(state=tk.DISABLED)
                return

            import subprocess
            result = subprocess.run([sys.executable, "-m", "pip", "install", "ebooklib"],
                                  capture_output=True, text=True)
            
            self.log_text.config(state=tk.NORMAL)
            if result.returncode == 0:
                self.log_text.insert(tk.END, "安装成功！\n")
            else:
                self.log_text.insert(tk.END, f"安装失败: {result.stderr}\n")
            self.log_text.config(state=tk.DISABLED)
        except Exception as e:
            messagebox.showerror("安装错误", f"安装ebooklib库时出错: {str(e)}")
        
    def on_closing(self):
        if self.is_downloading:
            if messagebox.askyesno("确认", "下载正在进行中，确定要退出吗？"):
                # 恢复标准输出
                sys.stdout = sys.__stdout__
                self.destroy()
        else:
            self.destroy()

if __name__ == "__main__":
    app = NovelDownloaderGUI()
    app.mainloop()