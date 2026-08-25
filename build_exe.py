import PyInstaller.__main__
import os
import sys

# 确保脚本路径正确
script_path = os.path.dirname(os.path.abspath(__file__))
gui_path = os.path.join(script_path, 'gui.py')
icon_path = os.path.join(script_path, 'favicon.ico')  # 如果有图标文件的话

# 设置PyInstaller参数
args = [
    gui_path,
    '--name=番茄小说下载器',
    '--onefile',
    '--windowed',
    '--clean',
    # '--icon=' + icon_path,  # 如果有图标文件，取消此行注释
    '--add-data=novel_downloader.py;.',
    '--add-data=cookie.json;.',  # 添加cookie文件
    '--hidden-import=requests',
    '--hidden-import=bs4',
    '--hidden-import=lxml',
    '--hidden-import=ebooklib',
    '--hidden-import=ebooklib.epub',  # 明确导入epub子模块
    '--hidden-import=tqdm',
]

# 运行PyInstaller
PyInstaller.__main__.run(args)

# 避免中文输出导致的编码错误
try:
    print("Build completed! The executable file is in the dist folder.")
except UnicodeEncodeError:
    # 如果出现编码错误，尝试使用不同的编码输出
    try:
        # 使用UTF-8编码强制输出
        sys.stdout.buffer.write("打包完成！可执行文件位于 dist 文件夹中\n".encode('utf-8'))
    except:
        # 如果仍然失败，退回到ASCII输出
        print("Build completed! The executable file is in the dist folder.")