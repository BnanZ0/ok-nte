# -*- coding: utf-8 -*-
import io, sys

p = r"D:\OKApps\launcher\launcher.py"
cfg = r"D:\OKApps\launcher\config.json"

s = open(p, encoding="utf-8").read()
c = open(cfg, encoding="utf-8").read()

repls = [
    # 1) import urllib.parse
    ("import urllib.request\nimport urllib.error\n",
     "import urllib.request\nimport urllib.error\nimport urllib.parse\n"),

    # 2) REPOS 三元组加 rid（ok-nte/okww/ok-end-field，取自各游戏 README 官方 MirrorChyan 链接）
    ('    REPOS = {\n'
     '        "ok-nte":       ("BnanZ0/ok-nte",                 "ok-nte-win32"),\n'
     '        "ok-ww":        ("ok-oldking/ok-wuthering-waves", "ok-ww-win32"),\n'
     '        "ok-end-field": ("AliceJump/ok-end-field",         "ok-ef-win32"),\n'
     '    }\n',
     '    REPOS = {\n'
     '        "ok-nte":       ("BnanZ0/ok-nte",                 "ok-nte-win32",      "ok-nte"),\n'
     '        "ok-ww":        ("ok-oldking/ok-wuthering-waves", "ok-ww-win32",        "okww"),\n'
     '        "ok-end-field": ("AliceJump/ok-end-field",         "ok-ef-win32",        "ok-end-field"),\n'
     '    }\n'),

    # 3) __init__ 加 cdk
    ('    def __init__(self, key, install_root, install_dir, parent=None):\n'
     '        super().__init__(parent)\n'
     '        self.key = key\n'
     '        self.install_root = install_root\n'
     '        # 目标安装目录（绝对路径）。win32.zip 解完后整目录或顶层单层目录应落到这里。\n'
     '        # 调用方按 config.app["exe"] 反推得出（已实测符合 PyAppify install 行为）。\n'
     '        self.install_dir = install_dir\n',
     '    def __init__(self, key, install_root, install_dir, cdk="", parent=None):\n'
     '        super().__init__(parent)\n'
     '        self.key = key\n'
     '        self.install_root = install_root\n'
     '        # 目标安装目录（绝对路径）。win32.zip 解完后整目录或顶层单层目录应落到这里。\n'
     '        # 调用方按 config.app["exe"] 反推得出（已实测符合 PyAppify install 行为）。\n'
     '        self.install_dir = install_dir\n'
     '        # MirrorChyan CDK（空=不启用快源，run() 自动回退 cnb/GitHub）\n'
     '        self.cdk = (cdk or "").strip()\n'),

    # 4) 新增 _mirrorchyan_url（插在 _mirror_url 后）
    ('    def _mirror_url(self, tag, asset_name):\n'
     '        """cnb.cool 镜像直链（国内快）。作者没在 cnb 开 release 镜像时此链接会 404，由 run() 回退 GitHub。"""\n'
     '        repo, _ = self.REPOS[self.key]\n'
     '        owner, name = repo.split("/")\n'
     '        return f"https://cnb.cool/{owner}/{name}/-/releases/download/{tag}/{asset_name}"\n',
     '    def _mirror_url(self, tag, asset_name):\n'
     '        """cnb.cool 镜像直链（国内快）。作者没在 cnb 开 release 镜像时此链接会 404，由 run() 回退 GitHub。"""\n'
     '        repo, _ = self.REPOS[self.key]\n'
     '        owner, name = repo.split("/")\n'
     '        return f"https://cnb.cool/{owner}/{name}/-/releases/download/{tag}/{asset_name}"\n'
     '\n'
     '    def _mirrorchyan_url(self):\n'
     '        """MirrorChyan CDK 加速源。config 填了有效 CDK 才返回临时下载直链，否则返回 None（run() 跳过此项）。\n'
     '        域名已实测可达；无/无效 CDK 时 API 返回 code!=0，本函数返回 None 让 run() 回退 cnb/GitHub。\n'
     '        rid 取自各游戏 README 官方链接：ok-nte=ok-nte / ok-ww=okww / ok-end-field=ok-end-field。\n'
     '        """\n'
     '        if not self.cdk:\n'
     '            return None\n'
     '        _, _, rid = self.REPOS[self.key]\n'
     '        api = (\n'
     '            f"https://mirrorchyan.com/api/resources/{rid}/latest"\n'
     '            f"?os=win&arch=x64&channel=stable"\n'
     '            f"&user_agent=WorkBuddy-OKLauncher&cdk={urllib.parse.quote(self.cdk)}"\n'
     '        )\n'
     '        req = urllib.request.Request(api, headers={"User-Agent": "WorkBuddy-OKLauncher"})\n'
     '        try:\n'
     '            with urllib.request.urlopen(req, timeout=30) as r:\n'
     '                info = json.loads(r.read())\n'
     '        except Exception:\n'
     '            return None\n'
     '        if not isinstance(info, dict) or info.get("code") != 0:\n'
     '            return None\n'
     '        data = info.get("data") or {}\n'
     '        url = data.get("url") or ""\n'
     '        return url if url.startswith("http") else None\n'),

    # 5) run() 候选源：MirrorChyan(有cdk) -> cnb -> GitHub
    ('            # 候选源：优先 cnb.cool 镜像（国内快），失败回退 GitHub 直链\n'
     '            candidates = []\n'
     '            mirror = self._mirror_url(tag, zip_name)\n'
     '            if mirror:\n'
     '                candidates.append(("mirror", mirror))\n'
     '            candidates.append(("github", zip_url))\n',
     '            # 候选源：优先 MirrorChyan（config 填了有效 CDK 时国内最快）→ cnb.cool 镜像 → GitHub 直链\n'
     '            candidates = []\n'
     '            mc = self._mirrorchyan_url()\n'
     '            if mc:\n'
     '                candidates.append(("mirrorchyan", mc))\n'
     '            mirror = self._mirror_url(tag, zip_name)\n'
     '            if mirror:\n'
     '                candidates.append(("mirror", mirror))\n'
     '            candidates.append(("github", zip_url))\n'),

    # 6) run 循环命中提示
    ('                if kind == "mirror":\n'
     '                    self.progress.emit(-1, "尝试 cnb.cool 镜像（国内加速）…")\n'
     '                total = self._stream_download(cu, zip_path, zip_size)\n'
     '                downloaded = True\n'
     '                if kind == "mirror":\n'
     '                    self.progress.emit(-1, "✅ cnb.cool 镜像命中，走快源")\n'
     '                break\n',
     '                if kind == "mirrorchyan":\n'
     '                    self.progress.emit(-1, "尝试 MirrorChyan CDK 加速源…")\n'
     '                elif kind == "mirror":\n'
     '                    self.progress.emit(-1, "尝试 cnb.cool 镜像（国内加速）…")\n'
     '                total = self._stream_download(cu, zip_path, zip_size)\n'
     '                downloaded = True\n'
     '                if kind == "mirrorchyan":\n'
     '                    self.progress.emit(-1, "✅ MirrorChyan CDK 镜像命中，走快源")\n'
     '                elif kind == "mirror":\n'
     '                    self.progress.emit(-1, "✅ cnb.cool 镜像命中，走快源")\n'
     '                break\n'),

    # 7a) install_app 对话框前读 cdk + 动态来源提示
    ('        install_dir = os.path.dirname(self.app.get("exe", "") or "") or ""\n'
     '        if not install_dir:\n'
     '            QMessageBox.warning(self.window(), "安装",\n'
     '                f"无法从 config 推导安装目录（exe={self.app.get(\'exe\',\'\')}）。")\n'
     '            return\n'
     '        resp = QMessageBox.question(\n'
     '            self.window(), "一键安装",\n'
     '            f"将从 GitHub 官方 release 下载并安装「{self.app[\'display\']}」到：\\n"\n'
     '            f"  {install_dir}\\n\\n"\n'
     '            "· 走 GitHub Releases 直链（国内可能偏慢，耐心等候）\\n"\n'
     '            "· 仅解压 win32.zip 便携包，免管理员、不写注册表\\n"\n'
     '            "· 装完后本卡片会自动识别为「已安装」\\n\\n"\n'
     '            "确认下载安装？",\n'
     '            QMessageBox.Yes | QMessageBox.No,\n'
     '            QMessageBox.No,\n'
     '        )\n',
     '        install_dir = os.path.dirname(self.app.get("exe", "") or "") or ""\n'
     '        if not install_dir:\n'
     '            QMessageBox.warning(self.window(), "安装",\n'
     '                f"无法从 config 推导安装目录（exe={self.app.get(\'exe\',\'\')}）。")\n'
     '            return\n'
     '        # 读 MirrorChyan CDK，决定下载源提示\n'
     '        try:\n'
     '            _cfg = json.load(open(os.path.join(LAUNCHER_DIR, "config.json"), encoding="utf-8"))\n'
     '            cdk = (_cfg.get("mirrorchyan_cdk", "") or "").strip()\n'
     '        except Exception:\n'
     '            cdk = ""\n'
     '        src_hint = ("· 已配置 MirrorChyan CDK，将优先走国内最快加速源" if cdk\n'
     '                    else "· 未配置 MirrorChyan CDK，走 cnb.cool/GitHub（国内可能偏慢）")\n'
     '        resp = QMessageBox.question(\n'
     '            self.window(), "一键安装",\n'
     '            f"将从官方 release 下载并安装「{self.app[\'display\']}」到：\\n"\n'
     '            f"  {install_dir}\\n\\n"\n'
     '            f"{src_hint}\\n"\n'
     '            "· 仅解压 win32.zip 便携包，免管理员、不写注册表\\n"\n'
     '            "· 装完后本卡片会自动识别为「已安装」\\n\\n"\n'
     '            "确认下载安装？",\n'
     '            QMessageBox.Yes | QMessageBox.No,\n'
     '            QMessageBox.No,\n'
     '        )\n'),

    # 7b) install_app 调用处传 cdk
    ('        self._install_worker = InstallWorker(key, install_root, install_dir, parent=self)\n',
     '        self._install_worker = InstallWorker(key, install_root, install_dir, cdk=cdk, parent=self)\n'),

    # 8) config.json 加 mirrorchyan_cdk 字段
    ('  "install_root": "D:/OKApps",\n',
     '  "install_root": "D:/OKApps",\n  "mirrorchyan_cdk": "",\n'),
]

for i, (old, new) in enumerate(repls, 1):
    if old not in s:
        # 也试 config
        if old in c:
            c = c.replace(old, new, 1)
            print(f"  [config #{i}] replaced")
            continue
        raise SystemExit(f"PATCH FAILED at #{i}: old string not found:\n{old[:160]}")
    s = s.replace(old, new, 1)
    print(f"  [launcher #{i}] replaced")

open(p, "w", encoding="utf-8").write(s)
open(cfg, "w", encoding="utf-8").write(c)
print("ALL PATCHES APPLIED")
