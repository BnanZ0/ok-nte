# 安装

## 🖥️ 运行环境与兼容性

* **操作系统**：Windows
* **游戏分辨率**：1920x1080 或更高（**仅支持 16:9 比例**）
* **游戏语言**：简体中文 / English

## 🚀 安装指南

### 方式一：使用安装包 (推荐)

此方法适合绝大多数用户，简单快捷，并支持自动更新。

* **[GitHub](https://github.com/BnanZ0/ok-nte/releases)**: 官方发布页，全球访问速度快。
* **[Mirror酱](https://mirrorchyan.com/zh/projects?rid=ok-nte-zip&channel=stable)**: 国内镜像，下载可能需要购买其平台的
  CD-KEY。
* **[百度网盘](https://pan.baidu.com/s/102Mh1djq2B1T-cIJhct9Gg?pwd=okww)**: 免费下载
* **[夸克网盘](https://pan.quark.cn/s/24433f3febc1)**: 免费下载

### 方式二：从源码运行 (适合开发者)

此方法需要您具备 Python 环境，适合希望进行二次开发或调试的用户。

1. **环境要求**：确保已安装 **Python 3.12**。
2. **克隆仓库**：
   ```bash
   git clone https://github.com/BnanZ0/ok-nte.git
   cd ok-nte
   ```
3. **安装依赖**：
   ```bash
   uv sync
   # or
   pip install -r requirements.txt
   ```

   **💡 提示**：每次更新代码后，建议重新运行此命令以确保依赖库为最新版本。
4. **运行程序**：
   ```bash
   # 运行正式版
   python main.py

   # 运行调试版 (会输出更详细的日志)
   python main_debug.py
   ```
