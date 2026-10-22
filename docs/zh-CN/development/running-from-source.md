# 从源码运行

## 环境准备

- Python 3.12。
- 推荐安装 [uv](https://docs.astral.sh/uv/)。
- Windows 开发环境。

```bash
git clone https://github.com/BnanZ0/ok-nte.git
cd ok-nte
uv sync
```

## 启动

```bash
# 正式入口
python main.py

# 调试入口，输出更详细的日志
python main_debug.py
```

## 测试与静态检查

仓库使用 `unittest`。有本地虚拟环境时，优先使用它的解释器：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*.py" --top-level-directory .
.\.venv\Scripts\python.exe -m py_compile src\flow.py
```

如已安装 Ruff，可运行：

```powershell
ruff check .
```

## 构建文档站点

文档依赖与应用依赖分离。安装后可生成可由任意静态网站服务器托管的 HTML：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-docs.txt
.\.venv\Scripts\python.exe -m mkdocs build --strict
```

默认输出目录为 `site/`。本地预览可运行：

```powershell
.\.venv\Scripts\python.exe -m mkdocs serve
```
