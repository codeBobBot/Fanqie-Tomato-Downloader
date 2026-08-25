# 番茄小说下载器

这是一个用于下载番茄小说的工具。

## 功能

- 支持番茄小说内容下载
- 将小说转换为适合阅读的TXT或EPUB格式
- 简洁易用的图形界面
- 支持多线程下载加速
- 提供在线下载功能，无需本地环境

## 使用方法

### 在线下载小说（无需安装任何软件）

您可以直接通过GitHub的在线功能下载小说，无需在本地安装任何软件：

1. 在GitHub仓库页面，点击"Actions"选项卡
2. 在左侧选择"在线下载小说"工作流
3. 点击"Run workflow"按钮
4. 填写以下信息：
   - 小说ID（从番茄小说网址中获取，例如：https://fanqienovel.com/page/7105916563 中的7105916563）
   - 下载线程数（默认为5，可选1-10）
   - 输出格式（选择txt或epub）
5. 点击"Run workflow"开始下载
6. 下载完成后，点击运行记录中的"Summary"标签
7. 在"Artifacts"部分找到并下载小说文件（文件保存期限为7天）

### 直接使用预编译版本

可以在[Releases](https://github.com/POf-L/Fanqie-Tomato-Downloader/releases)页面下载最新的预编译版本。

- Windows用户：下载并解压`Fanqie-Novel-Downloader-Windows.zip`，直接运行`番茄小说下载器.exe`。
- MacOS用户：下载并解压`Fanqie-Novel-Downloader-MacOS.zip`，直接运行`番茄小说下载器`应用。
- Linux用户：下载并解压`Fanqie-Novel-Downloader-Linux.zip`，直接运行`番茄小说下载器`可执行文件。

### 从源码运行

1. 克隆仓库：
```bash
git clone https://github.com/POf-L/Fanqie-Tomato-Downloader.git
cd Fanqie-Tomato-Downloader
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

3. 运行程序：
```bash
python gui.py
```

## 自动构建与发布

本项目使用GitHub Actions自动构建并发布应用。

### 自动构建流程

当你创建新的Release或手动触发工作流时，GitHub Actions会自动：

1. 在Windows、MacOS和Linux上构建可执行文件
2. 将构建好的可执行文件打包为zip文件
3. 上传构建产物到GitHub Artifacts
4. 创建Release发布页面（如果是从手动触发的工作流）

### 手动触发构建并发布

1. 在GitHub仓库页面，点击"Actions"选项卡
2. 在左侧选择"构建与发布"工作流
3. 点击"Run workflow"按钮
4. 填写版本号（如v1.0.0）和是否为预发布版本
5. 点击"Run workflow"开始构建
6. 构建完成后会自动创建Release并上传构建文件

## 常见问题

### 如何查找小说ID？

在番茄小说网站上，打开您想要下载的小说页面，URL中的数字部分就是小说ID。
例如：`https://fanqienovel.com/page/7105916563` 中的 `7105916563` 就是小说ID。

### 下载的文件在哪里？

- 使用GUI应用时，下载的文件保存在您指定的保存路径中
- 使用在线下载功能时，文件将作为GitHub Artifacts提供下载，保存期限为7天

## 许可证

[在此处添加许可证信息]