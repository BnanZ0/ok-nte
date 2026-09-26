# -*- coding: utf-8 -*-
"""
OK 游戏助手 - 统一管理窗口（WeGame 风格游戏库）

设计原则：本窗口只做「启动器」该做的事，默认不碰原启动器的目录；但 working/ 属于
官方仓库范畴（用户已授权可写），仅在用户明确点击「应用到 working 目录」时才写入：
  - 不碰 <app>/repo（git 仓库，镜像 git 只在本启动器 repos/<key> 内）
  - <app>/working 默认只读；「应用到 working 目录」= 覆盖代码文件 + 保留运行数据，
    单次弹窗确认、先终止进程、不产生备份（用户明确不要备份，省空间）
  - <app>/app.json 只读展示；仅应用成功后才写回 current_version（清残留更新中间态）

WeGame 风格卡片：
  - 封面区（渐变底 + 游戏图标）
  - 状态徽章：未安装 / 已安装 / 运行中 / 可更新
  - 按钮：未安装 ->「安装」（打开官方下载页）；已安装 ->「▶ 启动应用」（直开本体，不再单列原版管理窗口）
  - 只读展示：版本下拉 + 版本说明（changelog，GitHub compare，失败回退 update_note）
  - 窗口内更新：点「更新到 vX」把目标版本下载到本启动器目录 repos/<key>（真实进度条，
    原启动器目录零触碰）；下载完成后可一键「应用到 working 目录」（覆盖代码、保留运行数据、
    更新 app.json 当前版本），或交回「原版管理窗口」由原启动器完成

已收录游戏助手：
  - 异环 (ok-nte)
  - 鸣潮 (ok-ww)
  - 终末地 (ok-end-field) —— 未安装时显示「安装」，点击打开官方下载页
"""

import sys
import os
import re
import json
import ctypes
import subprocess
import traceback
import threading
import urllib.request
import urllib.error
import urllib.parse
import zipfile
import shutil
import time

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QUrl
from PySide6.QtGui import QIcon, QPixmap, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QWidget, QDialog, QHBoxLayout, QVBoxLayout, QGridLayout,
    QMessageBox, QLabel, QScrollArea, QTextEdit, QProgressBar, QProgressDialog,
)
from qfluentwidgets import (
    setTheme, Theme, CardWidget, IconWidget, StrongBodyLabel,
    CaptionLabel, PushButton, ComboBox, FluentIcon, IndeterminateProgressBar,
)


# ===== 无需自提权（见下方说明） =====
# 早期版本曾在此处用 runas 重启自己（弹 UAC）以管理员身份运行，因为当时 changelog
# 走 spawn ok-*.exe 的 PyAppify API，而 ok-*.exe 的 manifest 要求管理员（740）。
# 现已改为直接读本地 git 仓库（GitVersionFetcher），只读本地文件、不需要管理员，
# 故删除自提权逻辑：既消除每次启动的 UAC 弹窗 + 命令行闪烁，也不再无谓地重启进程。

# ===== 应用配置（从 config.json 加载，避免硬编码路径） =====
LAUNCHER_DIR = os.path.dirname(os.path.abspath(__file__))
# 图标统一使用 ok-script 官网 project-icons（已缓存到启动器自身 assets 目录），
# 各 app 的 app.json / working 默认只读展示；「应用到 working 目录」为用户主动授权的写入动作。
ASSETS_DIR = os.path.join(LAUNCHER_DIR, "assets")
# 独立镜像仓库目录：clone / fetch  ️只发生在这里，原启动器的 repo/ 完全不碰。
# 目录结构：LAUNCHER_REPOS_DIR/<key>/  （即一份独立的 git 仓库）
REPOS_DIR = os.path.join(LAUNCHER_DIR, "repos")
# 7-Zip 便携版持久化目录（与 install_root/_dl_<key>/ 解耦）。
# 旧 bug：7z 装到 tmp/7zportable/，run() 成功后 shutil.rmtree(tmp) 把它一起删了，
# 下次 install → 系统无 7z → 又自动下 1.6MB → 又被下次 rmtree 删，无限循环。
# 改成放 LAUNCHER_DIR/.cache/7zportable/，rmtree(tmp) 碰不到,一次装好永久复用。
LAUNCHER_7Z_DIR = os.path.join(LAUNCHER_DIR, ".cache", "7zportable")
# release 查询缓存目录：GitHub API（api.github.com/repos/.../releases/latest）未登录限 60 次/小时，
# 反复安装/刷新会很快打满 → 403 rate limit。把查询结果落盘缓存（带 TTL），
# 同一次会话内重试、跨次重启都直接读缓存，几乎不再打 API。
LAUNCHER_CACHE_DIR = os.path.join(LAUNCHER_DIR, ".cache")
RELEASE_CACHE_TTL = 30 * 60  # 30 分钟


def load_apps():
    """从 config.json 加载应用列表，并将相对路径拼成绝对路径。

    config.json 里的 exe/app_json/working/pythonw/icon 都相对于 install_root，
    这样他人 clone 后只需改 config.json 的 install_root 即可，无需改动代码。
    """
    cfg_path = os.path.join(LAUNCHER_DIR, "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        raise RuntimeError(f"读取配置文件失败: {cfg_path} ({e})")

    root = cfg.get("install_root", "D:/OKApps").replace("/", os.sep)
    apps = []
    for a in cfg.get("apps", []):
        def absify(p):
            if not p:
                return ""
            if os.path.isabs(p):
                return p
            return os.path.join(root, p.replace("/", os.sep))
        app = dict(a)
        app["install_root"] = root
        app["exe"] = absify(a.get("exe", ""))
        app["app_json"] = absify(a.get("app_json", ""))
        app["working"] = absify(a.get("working", ""))
        app["pythonw"] = absify(a.get("pythonw", ""))
        # icon：先用配置路径解析，文件不存在则回退到启动器自带 assets 目录下的同名文件
        # （注意：图标是跟启动器打包走的，始终在 ASSETS_DIR，不应依赖 install_root 下的 assets）
        icon = a.get("icon", "")
        if icon:
            cand = absify(icon) if (os.path.isabs(icon) or "/" in icon) else \
                os.path.join(ASSETS_DIR, icon)
            app["icon"] = cand if os.path.isfile(cand) else \
                os.path.join(ASSETS_DIR, f"{a.get('key', 'app')}.png")
        else:
            app["icon"] = os.path.join(ASSETS_DIR, f"{a.get('key', 'app')}.png")
        apps.append(app)
    return apps


APPS = load_apps()


def ver_key(v):
    """把版本字符串转成可排序的元组，正式版排在 pre/beta/alpha/rc 前面。

    例: v1.3.4 -> (1,3,4,0,0)；v1.3.4-beta.1 -> (1,3,4,1,1)
    """
    m = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", v or "")
    nums = [int(m.group(i)) if m and m.group(i) else 0 for i in (1, 2, 3)]
    if any(k in v for k in ("beta", "pre", "alpha", "rc", "-b")):
        pre = 0  # 预发布：排在正式版后面（正式版用 999）
        pm = re.search(r"(?:beta|pre|alpha|rc)[.\-]?(\d*)", v)
        prenum = int(pm.group(1)) if pm and pm.group(1) else 0
    else:
        pre = 999  # 正式版：永远排在预发布前面
        prenum = 0
    return (nums[0], nums[1], nums[2], pre, prenum)


def is_prerelease(v):
    """判断版本是否为预发布（beta/alpha/rc/pre）。"""
    return any(k in (v or "").lower() for k in ("beta", "alpha", "rc", "pre"))


def compare_version(a, b):
    """比较两个版本：a > b 返回 1，a < b 返回 -1，相等返回 0。"""
    ka, kb = ver_key(a), ver_key(b)
    return (ka > kb) - (ka < kb)


def format_version_display(version, current):
    """生成下拉框显示文本：vX.Y.Z 正式版（升级/降级/当前）。

    主卡片 ComboBox 和 UpdateDialog 复用此函数，保证标记一致。
    """
    type_label = "测试版" if is_prerelease(version) else "正式版"
    cmp = compare_version(version, current) if current else 0
    if version == current:
        action_label = "当前"
    elif cmp > 0:
        action_label = "升级"
    elif cmp < 0:
        action_label = "降级"
    else:
        action_label = ""
    if action_label:
        return f"{version} {type_label}（{action_label}）"
    return f"{version} {type_label}"


def parse_repo_from_git_url(git_url):
    """从 git_url 解析 owner/repo；支持 GitHub 与 cnb.cool（后者按同路径试 GitHub）。"""
    for pat in [
        r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
        r"https?://cnb\.cool/([^/]+)/([^/]+?)(?:\.git)?/?$",
    ]:
        m = re.match(pat, git_url)
        if m:
            return m.group(1), m.group(2)
    return None, None


# ⚠️ 重要：cnb.cool 源现在**直接走 cnb.cool 自己的 Gitea 兼容 API** 拉 changelog，
# 不再映射到 GitHub。原因：某些 cnb 镜像在 GitHub 上是独立仓库（名字不同），
# 强制映射会拉到错误内容。典型例子：鸣潮 China 源 cnb 仓库名为 `ok-ww-update2`，
# 而 GitHub 对应仓库是 `ok-ww-update`（无 2），两者更新历史不同——映射到 GitHub
# 后 changelog 内容与原启动器（读 cnb 自身）对不上。
# 异环的 cnb 仓库 `BnanZ0/ok-nte-update` 恰好与 GitHub 同名同内容，是特例，
# 此前误以为是普遍规律。故此处映射表留空，cnb 源一律用 cnb API。
CNB_GITHUB_REPO_MAP = {}


def resolve_github_repo(owner, repo):
    """若 cnb.cool 源有已知的 GitHub 真实仓库，返回真实 owner/repo；否则原样返回。"""
    return CNB_GITHUB_REPO_MAP.get((owner, repo), (owner, repo))


def _normalize_tag(tag):
    """去掉 git peeled ref 后缀 '^{}'，避免 v3.5.28^{} 这种脏 tag 混进列表。"""
    if not tag:
        return tag
    if tag.endswith("^{}"):
        tag = tag[:-3]
    return tag


def fetch_versions_from_git_url(git_url):
    """从 git 远程只读拉取所有 tags，按版本号从新到旧排序。失败返回空列表。"""
    if not git_url:
        return []

    owner, repo = parse_repo_from_git_url(git_url)

    # 1) GitHub API 分页拉全量 tags（最快、最完整；cnb.cool 按同路径走 GitHub）
    if owner and repo:
        tags = []
        try:
            for page in range(1, 11):  # 最多 10 页 = 1000 个版本
                url = f"https://api.github.com/repos/{owner}/{repo}/tags?per_page=100&page={page}"
                req = urllib.request.Request(url, headers={"User-Agent": "ok-launcher/1.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = json.loads(r.read().decode("utf-8"))
                if not data:
                    break
                tags.extend(_normalize_tag(t["name"]) for t in data if "name" in t)
            if tags:
                return sorted(tags, key=ver_key, reverse=True)
        except Exception:
            pass

    # 2) dulwich 兜底（通吃 GitHub / cnb.cool 等 smart HTTP）
    try:
        from dulwich.client import get_transport_and_path

        client, path = get_transport_and_path(git_url)
        refs = client.get_refs(path)
        tags = []
        for ref_name in refs.keys():
            if isinstance(ref_name, bytes):
                ref_name = ref_name.decode("utf-8", "replace")
            if ref_name.startswith("refs/tags/"):
                tag = _normalize_tag(ref_name.replace("refs/tags/", ""))
                if tag and tag not in tags:
                    tags.append(tag)
        if tags:
            return sorted(tags, key=ver_key, reverse=True)
    except Exception:
        pass

    return []


def fetch_changelog(owner, repo, base, head, limit=10):
    """用 GitHub compare API 拉取 base...head 之间的 commits 列表（只读）。

    返回格式化的多行文本；失败抛异常。limit 控制显示最近 N 条。
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/compare/{base}...{head}"
    req = urllib.request.Request(url, headers={"User-Agent": "ok-launcher/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    commits = data.get("commits", [])
    lines = []
    for c in reversed(commits):  # 从新到旧，和原版窗口一致
        msg = c.get("commit", {}).get("message", "").split("\n")[0].strip()
        author = c.get("commit", {}).get("author", {}).get("name", "")
        if not author:
            author = c.get("author", {}).get("login", "") or ""
        if msg:
            lines.append(f"• {msg}" + (f"（{author}）" if author else ""))
    if not lines:
        return "该版本暂无更新说明。"
    return "\n".join(lines[:limit])


def fetch_changelog_cnb(owner, repo, base, head, limit=10):
    """用 cnb.cool 的 Gitea 兼容 API 拉取 head 版本附近的更新 commits（只读）。

    对应 China 源：changelog 直接来自 cnb 镜像仓库本身（与原启动器一致），
    不再映射到 GitHub（否则会拉到不同仓库的内容）。cnb.cool 的 commits API 形如
    /api/v1/repos/{owner}/{repo}/commits?sha={head}&limit={n}，返回从 head 往前
    的 commit 列表（Gitea 格式），正好对应「目标版本的更新说明」。
    """
    sha = urllib.parse.quote(head, safe="")
    url = (f"https://cnb.cool/api/v1/repos/{owner}/{repo}/commits"
           f"?sha={sha}&limit={limit}")
    req = urllib.request.Request(url, headers={"User-Agent": "ok-launcher/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    lines = []
    for c in data:
        msg = c.get("commit", {}).get("message", "").split("\n")[0].strip()
        author = c.get("author", {}).get("login", "") or ""
        if not author:
            author = c.get("commit", {}).get("author", {}).get("name", "")
        if msg:
            lines.append(f"• {msg}" + (f"（{author}）" if author else ""))
    if not lines:
        return "该版本暂无更新说明。"
    return "\n".join(lines[:limit])


class ChangelogFetcher(QThread):
    """后台拉取 changelog（只读，不影响任何本地文件）。

    根据 git_url 来源分发：
      - cnb.cool 源 → cnb.cool 自身 Gitea API（与原启动器一致，不映射到 GitHub）
      - 其它（github.com 等）→ GitHub compare API
    """
    fetched = Signal(str)
    failed = Signal(str)

    def __init__(self, git_url, owner, repo, base, head, parent=None):
        super().__init__(parent)
        self.git_url = git_url
        self.owner = owner
        self.repo = repo
        self.base = base
        self.head = head

    def run(self):
        try:
            if self.git_url.rstrip("/").startswith("https://cnb.cool/"):
                text = fetch_changelog_cnb(self.owner, self.repo, self.base, self.head)
            else:
                text = fetch_changelog(self.owner, self.repo, self.base, self.head)
            self.fetched.emit(text)
        except Exception as e:
            self.failed.emit(str(e))


def calculate_update_notes(update_notes, current_version, target_version):
    """复刻 PyAppify 的 pyappify.calculate_update_notes：

    从版本列表中取 current → target（含两端）区间内每个版本的 update_note，
    拼接成更新说明。版本列表顺序须与 ``--get-version-list`` 返回一致。
    """
    if not isinstance(update_notes, list):
        return []
    versions = [item for item in update_notes
                if isinstance(item, dict) and item.get("version")]

    def normalize(version):
        return str(version or "").lstrip("v")

    def find_index(version):
        normalized = normalize(version)
        for index, item in enumerate(versions):
            if normalize(item["version"]) == normalized:
                return index
        return None

    target_index = find_index(target_version)
    if target_index is None:
        return []

    current_index = find_index(current_version)
    if current_index is None:
        selected = versions[target_index:]
    else:
        first = min(current_index, target_index)
        last = max(current_index, target_index)
        selected = versions[first:last + 1]

    notes = []
    for item in selected:
        raw = item.get("update_note") or []
        if isinstance(raw, list):
            notes.extend(str(n) for n in raw)
        else:
            notes.append(str(raw))
    return notes


class GitVersionFetcher(QThread):
    """后台从各游戏**本地 git 仓库**读取「版本 + 更新说明」列表。

    为什么不用原启动器的 PyAppify ``--get-version-list`` exe API：
    ok-ww / ok-nte 是 Tauri **单实例**应用，该 API 只能在**已运行的实例内部**被处理
    （单实例插件把参数转发给运行中的实例，再由它的 PyAppify 运行时写回 response 文件）。
    从外部 spawn exe 永远进不了这个模式——要么变成 GUI 首实例（弹出原启动器界面），
    要么参数被单实例插件丢弃，导致 response 为空。证据见 ``ok-ww/logs/app.2026-08-20``：
    我们 spawn 的 ok-ww.exe 直接以 ``running with tauri ui`` 启动成完整 GUI，全程无视
    ``--get-version-list`` 参数。

    因此改为直接读本地仓库：tags -> 版本、tag 提交信息 -> 更新说明，
    这正是 PyAppify 自身用的同一份数据（已用 ``git log -1 --format=%B <tag>`` 与原启动器
    ``get_update_notes`` 输出逐字核对一致，如 ok-ww v3.6.4 的 11 条说明完全吻合）。
    不需要 git 在 PATH——优先用 WorkBuddy 自带的 PortableGit（用户机器位于
    ``~/.workbuddy/binaries/PortableGit``），找不到再回退 app.json 缓存。
    只读本地文件、读本地仓库，**绝不启动任何 exe**，因此不会再弹出原启动器。
    """
    fetched = Signal(list)   # list of {version, update_note}
    failed = Signal(str)

    def __init__(self, exe_path, parent=None):
        super().__init__(parent)
        self.exe_path = exe_path

    @staticmethod
    def _find_git():
        import glob, shutil
        base = os.path.join(os.path.expanduser("~"), ".workbuddy",
                            "binaries", "PortableGit", "versions")
        cands = []
        # PortableGit 版本目录：versions/<ver>/mingw64/bin/git.exe
        cands += glob.glob(os.path.join(base, "*", "mingw64", "bin", "git.exe"))
        which = shutil.which("git")
        if which:
            cands.append(which)
        for p in (r"C:\Program Files\Git\bin\git.exe",
                  r"C:\Program Files (x86)\Git\bin\git.exe"):
            if os.path.isfile(p):
                cands.append(p)
        for c in cands:
            if os.path.isfile(c):
                return c
        return None

    def _emit_cached(self, app_json):
        """git 不可用 / 仓库缺失时的兜底：用 app.json 的 available_versions +
        当前版本 update_note，保证下拉框可用、绝不报“获取失败”。"""
        try:
            with open(app_json, "r", encoding="utf-8") as f:
                aj = json.load(f)
            av = aj.get("available_versions") or []
            cur = aj.get("current_version")
            cur_note = aj.get("update_note") or []
            items = []
            for v in av:
                notes = cur_note if v == cur else []
                items.append({"version": v, "update_note": notes})
            if items:
                self.fetched.emit(items)
                return
        except Exception:
            pass
        self.failed.emit("无法读取版本信息（git 不可用且 app.json 缓存缺失）")

    def run(self):
        exe = self.exe_path
        try:
            key = os.path.splitext(os.path.basename(exe))[0]
            app_root = os.path.dirname(exe)
            repo = os.path.join(app_root, "data", "apps", key, "repo")
            app_json = os.path.join(app_root, "data", "apps", key, "app.json")
            git = self._find_git()
            if not git or not os.path.isdir(repo):
                self._emit_cached(app_json)
                return
            # 版本顺序以 app.json 的 available_versions 为准（最新在前），与 get_version_list 一致
            order = []
            try:
                with open(app_json, "r", encoding="utf-8") as f:
                    order = (json.load(f).get("available_versions") or [])
            except Exception:
                order = []
            out = subprocess.run([git, "-C", repo, "tag"],
                                 capture_output=True, text=True,
                                 encoding="utf-8", errors="replace",
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if out.returncode != 0:
                self._emit_cached(app_json)
                return
            tags = [t.strip() for t in out.stdout.splitlines() if t.strip()]
            tag_set = set(tags)
            ordered = [v for v in order if v in tag_set]
            extra = [t for t in tags if t not in set(ordered)]
            ordered += sorted(extra, reverse=True)   # 仓库有但 app.json 未列的，按版本倒序补在后面
            items = []
            for v in ordered:
                msg = subprocess.run([git, "-C", repo, "log", "-1",
                                      "--format=%B", v],
                                     capture_output=True, text=True,
                                     encoding="utf-8", errors="replace",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if msg.returncode != 0:
                    notes = []
                else:
                    notes = [ln.strip() for ln in msg.stdout.splitlines()
                             if ln.strip()]
                items.append({"version": v, "update_note": notes})
            if not items:
                self._emit_cached(app_json)
                return
            # 落盘一份原始结果（覆盖写），方便核对，也便于排查与 git 上游的差异
            try:
                _ld = os.path.join(LAUNCHER_DIR, "logs")
                os.makedirs(_ld, exist_ok=True)
                with open(os.path.join(_ld, "versions-{}.json".format(key)),
                          "w", encoding="utf-8") as _f:
                    json.dump(items, _f, ensure_ascii=False, indent=1)
            except Exception:
                pass
            self.fetched.emit(items)
        except Exception as e:
            self.failed.emit(str(e))


def parse_git_progress(line):
    """从 dulwich / git 进度文本解析出百分比（取最后一个 N%）。无则 -1。"""
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    pct = -1
    for m in re.finditer(r"(\d+)%", line):
        pct = int(m.group(1))
    return pct, (line or "").strip()


def ensure_mirror(key, git_url, target_tag=None, progress_cb=None):
    """在 LAUNCHER_REPOS_DIR/<key> 维护一份独立镜像仓库。

    只写启动器自己的目录，原启动器的 repo/working/app.json 完全不碰。
    首次 clone，之后增量 fetch；可选 checkout 到 target_tag。返回本地仓库目录。
    """
    repo_dir = os.path.join(REPOS_DIR, key)
    os.makedirs(repo_dir, exist_ok=True)
    from dulwich import porcelain
    from dulwich.repo import Repo
    from dulwich.client import get_transport_and_path

    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        porcelain.clone(git_url, repo_dir, progress=progress_cb)
    else:
        repo = Repo(repo_dir)
        try:
            client, path = get_transport_and_path(git_url)
            client.fetch(path, repo, progress=progress_cb)
        except Exception:
            # 增量 fetch 失败不致命：本地已有旧镜像，仍可 checkout 已有 tag
            pass
    if target_tag:
        try:
            repo = Repo(repo_dir)
            porcelain.checkout(repo, target_tag, force=True)
        except Exception:
            pass
    return repo_dir



class InstallWorker(QThread):
    """后台从 GitHub release 下载 win32.zip 就地解压到目标安装目录（进度协议同 MirrorUpdater）。"""

    # ===== 同 key 并发硬锁（进程内） =====
    # 旧版「重复下载」根因：用户点取消后 worker 还在后台跑（cancel 标志要等到下载循环才传到），
    # 旧 _on_cancel 没清 self._install_worker 引用，UI 信号 disconnect 后不动了，用户以为停了又点，
    # 守卫看旧 worker 还活着就拦住，等旧 worker 一结束再点又起新 worker——多个 worker 并发各自下整包，
    # 进度互相覆盖看起来就像「一直在换源重复下载」。这里加进程内按 key 的锁 + 落盘日志双保险。
    _locks = {}  # key -> Lock，确保同一 key 同时只有一个 worker 在跑
    _SEVENZIP_PATH = None  # 进程内缓存：一次成功后,所有后续 install 直接复用,不再重下 1.6MB

    def _log(self, msg):
        """下载过程落盘日志，写到 logs/install-<key>.log，方便排查「换源/重复下载」。"""
        try:
            _d = os.path.join(LAUNCHER_DIR, "logs")
            os.makedirs(_d, exist_ok=True)
            _p = os.path.join(_d, f"install-{self.key}.log")
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(_p, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    progress = Signal(int, str)
    done = Signal(str, str)        # (install_dir, version_tag)
    failed = Signal(str)

    # key -> (github_repo, zip 前缀, mirrorchyan_rid, pyappify_内部名)
    #   - 第 3 个是 MirrorChyan 的 rid（README 官方链接）
    #   - 第 4 个是 PyAppify 打包出来的内部名（= 解压后 data/apps/<名> 与 <名>.exe），
    #     它和 launcher key 不一定相同：终末地内部叫 ok-ef，但 launcher 配置用 ok-end-field。
    #     整包(China-setup)解压后需要把 ok-ef 改名/复制对齐成 launcher 期望的名字。
    REPOS = {
        "ok-nte":       ("BnanZ0/ok-nte",                 "ok-nte-win32",      "ok-nte",      "ok-nte"),
        "ok-ww":        ("ok-oldking/ok-wuthering-waves", "ok-ww-win32",        "okww",        "ok-ww"),
        "ok-end-field": ("AliceJump/ok-end-field",         "ok-ef-win32",        "ok-end-field","ok-ef"),
    }

    # 万载云 GitHub 反代（国内直连、免登录免 key、覆盖全部游戏）。
    # 实测：GET <WANZAIYUN_PROXY><原始github链接> 直接 200 吐 octet-stream。
    # 主域名若失效，改 config.json 的 wanzaiyun_proxy 即可，无需改代码。
    WANZAIYUN_PROXY = "https://github.top-host.top/"

    # 万载云页面下拉框「节点」里的全部加速入口（2026-08-28 从 wanzaiyun.com github.js
    # 解混淆 PROXY_LIST 取得，共 17 个）。其中 github.top-host.top / proxy.gitwarp.top
    # 是万载云自家 CDN，其余 15 个是第三方公共 gh-proxy。安装前会并发测速，挑最快的用。
    # 顺序无关紧要——安装时会按实测吞吐重排。
    WANZAIYUN_NODES = [
        "https://gh.xmly.dev/",
        "https://gh-proxy.org/",
        "https://v4.gh-proxy.org/",
        "https://v6.gh-proxy.org/",
        "https://cdn.gh-proxy.org/",
        "https://github.xxlab.tech/",
        "https://gh-proxy.com/",
        "https://gh.b52m.cn/",
        "https://g.blfrp.cn/",
        "https://gh.jasonzeng.dev/",
        "https://gitproxy.mrhjx.cn/",
        "https://github.geekery.cn/",
        "https://ghproxy.sakuramoe.dev/",
        "https://github.cnxiaobai.com/",
        "https://ghproxy.net/",
        "https://proxy.gitwarp.top/",
        "https://github.top-host.top/",
    ]

    def __init__(self, key, install_root, install_dir, cdk="", proxy=None, app=None, parent=None):
        super().__init__(parent)
        self.key = key
        self.install_root = install_root
        # 目标安装目录（绝对路径）。win32.zip 解完后整目录或顶层单层目录应落到这里。
        # 调用方按 config.app["exe"] 反推得出（已实测符合 PyAppify install 行为）。
        self.install_dir = install_dir
        # MirrorChyan CDK（空=不启用快源，run() 自动回退 cnb/GitHub）
        self.cdk = (cdk or "").strip()
        # 万载云反代域名（None=用默认 WANZAIYUN_PROXY）
        self.proxy = (proxy or "").strip() or self.WANZAIYUN_PROXY
        # 调用方传进来的 app dict（含 config["exe"] 期望名）。解完后用来对齐 host 名。
        self.app = app or {}
        # 取消标志位：用户点对话框的「取消」按钮时设为 True，下载/解压循环每 chunk 检查并 early return。
        # 之前是 None 取消按钮 + 无标志位 → 用户被下载困死、连点会多 worker 并发下整包。
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _cleanup_tmp(self, tmp):
        """取消时清理下载临时目录，避免留半截 zip/7z_installer.exe/7zportable 占空间。"""
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        # 发一个静默的「已取消」标记,run() 不会发 failed,UI 不弹错误框
        self.progress.emit(-1, "已取消下载,临时文件已清理")

    def _get_latest_release(self, repo):
        """查 GitHub releases/latest，返回完整 release JSON。

        带本地文件缓存（TTL=RELEASE_CACHE_TTL），避免在「反复安装/刷新」场景里
        把未登录 60 次/小时的 api.github.com 配额打满 → 403 rate limit。
        - 缓存未过期：直接返回缓存，零 API 请求；
        - 缓存过期/缺失：打 API；若命中 403 且本地有缓存，则用缓存兜底（并记日志），
          没有缓存才把 403 作为异常抛出，让 run() 给出清晰提示。
        """
        owner, name = repo.split("/")
        cache_file = os.path.join(LAUNCHER_CACHE_DIR, f"release_{owner}_{name}.json")
        os.makedirs(LAUNCHER_CACHE_DIR, exist_ok=True)

        # 1) 缓存命中且未过期 → 直接用，不再打 API
        if os.path.isfile(cache_file):
            try:
                cached = json.load(open(cache_file, encoding="utf-8"))
                ts = cached.get("_fetched_at", 0)
                if (time.time() - ts) < RELEASE_CACHE_TTL:
                    self._log(f"release 缓存命中（{name}，{int((time.time()-ts))}s 前），跳过 API")
                    return cached
            except Exception:
                pass

        # 2) 打 API
        api = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(api, headers={"User-Agent": "WorkBuddy-OKLauncher"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 403:
                self._log(f"GitHub API 403 限流（{repo}），尝试用本地缓存兜底")
                if os.path.isfile(cache_file):
                    try:
                        return json.load(open(cache_file, encoding="utf-8"))
                    except Exception:
                        pass
                raise RuntimeError(
                    "GitHub API 触发限流（HTTP 403: rate limit exceeded）。\n"
                    "未登录调用 api.github.com 限 60 次/小时，反复安装/刷新已打满。\n"
                    "请稍等约 1 小时再试；或填 MirrorChyan CDK 走国内快源绕开 GitHub。"
                )
            raise
        except Exception:
            # 网络异常：有缓存就用缓存兜底，没有就原样抛出
            if os.path.isfile(cache_file):
                self._log(f"GitHub API 异常，用本地缓存兜底（{name}）")
                try:
                    return json.load(open(cache_file, encoding="utf-8"))
                except Exception:
                    pass
            raise

        # 3) 成功 → 写缓存
        data["_fetched_at"] = time.time()
        try:
            json.dump(data, open(cache_file, "w", encoding="utf-8"), ensure_ascii=False)
            self._log(f"release 已缓存（{name}，{len(data.get('assets', []))} 个资产）")
        except Exception:
            pass
        return data

    def _asset_for(self):
        """调 GitHub releases/latest，找一个 .zip 资产。返回 (url, name, size, tag)。"""
        if self.key not in self.REPOS:
            raise RuntimeError(f"未配置 {self.key} 的 release 仓库")
        repo, *_ = self.REPOS[self.key]
        data = self._get_latest_release(repo)
        for a in data.get("assets", []):
            n = a.get("name", "") or ""
            if n.lower().endswith(".zip"):
                return a["browser_download_url"], n, int(a.get("size", 0) or 0), data.get("tag_name", "")
        raise RuntimeError(f"未在 {repo} 找到 .zip 资产")

    def _scan_local_installer(self, tmp, key):
        """扫描 _dl_<key>/ 里是否已存在上一次下好的安装包，直接复用，完全不打 GitHub API、不重下。

        返回 (zip_path, zip_name, zip_size, is_full) 或 None。
        判定：优先 china-setup 整包(.exe)；否则任意 >50MB 的 .exe 当整包；>5MB 的 .zip 当 host 引导包。
        关键用途：用户之前下到一半/下完但解压失败留下的 _dl_<key>/<name>，本次重装时
        如果不复用就会再去打 api.github.com 查 release（未登录限流 403）→ 卡死。
        所以「本地已有包」时一律优先复用，绕开 API 与下载。
        """
        if not tmp or not os.path.isdir(tmp):
            return None
        cands = []
        for fn in os.listdir(tmp):
            fp = os.path.join(tmp, fn)
            if not os.path.isfile(fp):
                continue
            low = fn.lower()
            sz = os.path.getsize(fp)
            if "china-setup" in low and low.endswith(".exe") and sz > 50 * 1024 * 1024:
                cands.append((fp, fn, sz, True))
            elif low.endswith(".exe") and sz > 50 * 1024 * 1024:
                cands.append((fp, fn, sz, True))
            elif low.endswith(".zip") and sz > 5 * 1024 * 1024:
                cands.append((fp, fn, sz, False))
        if not cands:
            return None
        # 取最大的那个（最可能是完整安装的）
        cands.sort(key=lambda x: x[2], reverse=True)
        fp, fn, sz, is_full = cands[0]
        return fp, fn, sz, is_full

    def _mirror_url(self, tag, asset_name):
        """cnb.cool 镜像直链（国内快）。作者没在 cnb 开 release 镜像时此链接会 404，由 run() 回退 GitHub。"""
        repo, *_ = self.REPOS[self.key]
        owner, name = repo.split("/")
        return f"https://cnb.cool/{owner}/{name}/-/releases/download/{tag}/{asset_name}"

    def _mirrorchyan_url(self):
        """MirrorChyan CDK 加速源。config 填了有效 CDK 才返回临时下载直链，否则返回 None（run() 跳过此项）。

        域名已实测可达；无/无效 CDK 时 API 返回 code!=0，本函数返回 None 让 run() 回退 cnb/GitHub。
        rid 取自各游戏 README 官方链接：ok-nte=ok-nte / ok-ww=okww / ok-end-field=ok-end-field。
        """
        if not self.cdk:
            return None
        _, _, rid = self.REPOS[self.key]
        api = (
            f"https://mirrorchyan.com/api/resources/{rid}/latest"
            f"?os=win&arch=x64&channel=stable"
            f"&user_agent=WorkBuddy-OKLauncher&cdk={urllib.parse.quote(self.cdk)}"
        )
        req = urllib.request.Request(api, headers={"User-Agent": "WorkBuddy-OKLauncher"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                info = json.loads(r.read())
        except Exception:
            return None
        if not isinstance(info, dict) or info.get("code") != 0:
            return None
        data = info.get("data") or {}
        url = data.get("url") or ""
        return url if url.startswith("http") else None

    # ===== 万载云多节点测速选源 =====
    def _probe(self, url, timeout=4):
        """对单个候选源做「零流量探测」，返回 (ok, latency_s, content_length)。

        ok=False 表示超时 / 非 200/206 / 连不上，调用方直接跳过。

        零流量策略（彻底消除「下完了又下」的 bug）：
        1) 优先 HEAD 请求——只收响应头，完全不收 body，零字节浪费；
        2) 若源不支持 HEAD（返回 405/400 等），回退 Range: bytes=0-0 且只读 1 字节即断，
           最多白收 1 个 TCP 窗口（约几十 KB），远小于旧逻辑（旧逻辑 Range 0-131071 会
           把不支持 Range 的源整个文件推过来，等于每次测速白下完整安装包）。
        排序以「延迟最低」为准（就近 CDN 即最快），不再用实测吞吐。
        """
        # 1) HEAD：零 body
        try:
            req = urllib.request.Request(
                url, method="HEAD",
                headers={"User-Agent": "WorkBuddy-OKLauncher"},
            )
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status not in (200, 206):
                    raise urllib.error.HTTPError(url, r.status, "head", r.headers, None)
                latency = time.time() - t0
                cl = int(r.headers.get("Content-Length") or 0)
                return (True, latency, cl)
        except urllib.error.HTTPError:
            # 2) 回退 Range 0-0（极少数只支持 GET 的源）
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "WorkBuddy-OKLauncher",
                        "Range": "bytes=0-0",
                    },
                )
                t0 = time.time()
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    if r.status not in (200, 206):
                        return (False, 999.0, 0)
                    r.read(1)  # 只读 1 字节即断
                    latency = time.time() - t0
                    cl = int(r.headers.get("Content-Length") or 0)
                    return (True, latency, cl)
            except Exception:
                return (False, 999.0, 0)
        except Exception:
            return (False, 999.0, 0)

    def _select_fastest(self, github_url, tag="", asset_name=""):
        """并发测速所有候选源，挑「延迟最低」的那个返回 (label, url, latency_s)。

        候选源 = MirrorChyan(填了CDK) + 万载云 17 节点(两种拼法) + cnb + GitHub 直链。
        万载云节点拼法：<node><github_url>（带协议）优先，失败再试 <node><去协议路径>；
        哪种形式 200 就用哪种。返回 None 表示全部失败。
        """
        import concurrent.futures as _cf

        cands = []  # (label, url)
        # MirrorChyan（私有快源，有 CDK 才进候选）
        mc = self._mirrorchyan_url()
        if mc:
            cands.append(("MirrorChyan(CDK)", mc))
        # 万载云 17 节点：带协议 + 去协议两种拼法都试
        if github_url.startswith("https://github.com/"):
            no_proto = github_url[len("https://"):]
            for nd in self.WANZAIYUN_NODES:
                cands.append((f"万载云 {nd}", nd + github_url))
                cands.append((f"万载云 {nd}(noproto)", nd + no_proto))
            # config.json 里单独配的自定义万载云域名（覆盖默认节点列表）
            custom = (self.proxy or "").strip()
            if custom and custom not in self.WANZAIYUN_NODES:
                cands.append((f"万载云自定义 {custom}", custom + github_url))
                cands.append((f"万载云自定义 {custom}(noproto)", custom + no_proto))
        # cnb 镜像（仅 win32.zip 这类常规资产，整包通常没有）
        if not self._is_full and tag and asset_name:
            mirror = self._mirror_url(tag, asset_name)
            if mirror:
                cands.append(("cnb.cool", mirror))
        # GitHub 直链（兜底，永远能进候选）
        cands.append(("GitHub 直链", github_url))

        self.progress.emit(-1, f"正在测速 {len(cands)} 个下载源（并发，每源只读 1 字节）…")

        def _test(item):
            label, url = item
            ok, lat, cl = self._probe(url)
            return (label, url, ok, lat, cl)

        scored = []
        with _cf.ThreadPoolExecutor(max_workers=20) as ex:
            for res in ex.map(_test, cands):
                label, url, ok, lat, cl = res
                # 必须探测成功且拿到真实文件大小（Content-Length>0 说明该源真有这个文件）
                if ok and cl > 0:
                    scored.append((label, url, lat))
        if not scored:
            return None
        # 按延迟升序，取最近（最快）的源
        scored.sort(key=lambda x: x[2])
        # 测速结束查一次取消标志：避免用户在 17 节点测速阶段点取消后,
        # 选完源继续跑 _stream_download 整包下载,造成"取消无效还在下"的假象
        if getattr(self, "_cancelled", False):
            return None
        return scored  # 返回按延迟升序的完整候选列表 [(label, url, latency_s), ...]

    def _stream_download(self, url, zip_path, total_hint):
        """实际下载循环，复用速度/ETA 进度。返回 total(字节)。失败抛异常（含 urllib.error.HTTPError）。"""
        req = urllib.request.Request(url, headers={"User-Agent": "WorkBuddy-OKLauncher"})
        t0 = time.time()
        host = url.split("//", 1)[-1].split("/", 1)[0]
        self.progress.emit(0, f"正在连接 {host}…")
        # timeout=60：连接或读取任一步超过 60 秒无数据即超时。万载云节点抽风时会卡在
        # "连接建立但不推数据"，旧值 600 会让单次下载挂 10 分钟、用户以为卡死又点 → 并发。
        # 60 秒足够暴露坏源，配合 run() 的「前 3 名换源重试」快速失败退出。
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or total_hint or 0)
            got = 0
            chunk = 64 * 1024
            last_emit = 0.0
            with open(zip_path, "wb") as f:
                while True:
                    # 用户点取消则立即中断下载。半截 zip 调用方会清掉 tmp，不会留垃圾。
                    if getattr(self, "_cancelled", False):
                        return 0
                    b = resp.read(chunk)
                    if not b:
                        break
                    f.write(b)
                    got += len(b)
                    now = time.time()
                    if now - last_emit < 0.3 and b:
                        continue
                    last_emit = now
                    elapsed = max(now - t0, 1e-6)
                    speed = got / elapsed
                    speed_mb = speed / 1048576
                    if total > 0:
                        pct = min(99, int(got * 100 / total))
                        remain_b = total - got
                        eta_s = int(remain_b / speed) if speed > 0 else 0
                        eta_str = (
                            f"{eta_s//60}分{eta_s%60}秒" if eta_s >= 60
                            else f"{eta_s}秒"
                        )
                        self.progress.emit(
                            pct,
                            f"下载中 {got/1048576:.1f}/{total/1048576:.1f}MB "
                            f"({pct}%) · {speed_mb:.2f}MB/s · 约剩 {eta_str}",
                        )
                    else:
                        self.progress.emit(
                            -1,
                            f"下载中 {got/1048576:.1f}MB · {speed_mb:.2f}MB/s",
                        )
        return total

    # ===== 完整 NSIS 整包安装支持 =====
    def _setup_asset_for(self):
        """在 release 资产里找「完整安装包」（NSIS setup.exe）。

        优先匹配 *china-setup*.exe（国内版整包，含完整 Python venv + working + cache，
        解压后 working/main.py 直接存在，启动按钮即可直开本体，无需再跑 host 初始化）；
        其次匹配 *setup*.exe。返回 (url, name, size, tag) 或 None（没有整包时回退 win32.zip host）。
        """
        if self.key not in self.REPOS:
            return None
        repo, *_ = self.REPOS[self.key]
        try:
            data = self._get_latest_release(repo)
        except Exception:
            return None
        tag = data.get("tag_name", "")
        best = None
        for a in data.get("assets", []):
            n = (a.get("name", "") or "").lower()
            if not n.endswith(".exe"):
                continue
            if "china-setup" in n:
                return a["browser_download_url"], n, int(a.get("size", 0) or 0), tag
            if "setup" in n and best is None:
                best = (a["browser_download_url"], n, int(a.get("size", 0) or 0), tag)
        return best

    def _ensure_7z(self, tmp=None):
        """返回可用的 7z.exe 路径（解 NSIS 必须 full 7z.exe；7za/7zr 解不了 NSIS）。

        优先级：
          1) 进程内缓存 _SEVENZIP_PATH（一次成功后后续 install 复用,不再装/下）
          2) 持久化 LAUNCHER_7Z_DIR\\7z.exe（成功装好后保留,跨次复用,跨进程复用）
          3) 已知位置：launcher .cache/7zportable/ + PATH + 全局 Program Files
          4) 兜底：下 NSIS 7z2602-x64.exe (1.58 MB),ShellExecuteW+runas 提权静默装
             到 LOCALAPPDATA\\Programs\\7-Zip\\ → 需要用户点一次 UAC 弹窗"是"。
             装好后 7z.exe 落到用户目录,主进程（无论是否 admin）都能直接调,
             后续所有 install 都直接命中 step 2。

        旧 step 4 链路（下 7zr.exe → 解 extra.7z 拿 7z.exe）已彻底删除：
        extra.7z 实际**不含** 7z.exe（os.walk 永远找不到）,UI 看上去一直在
        "测速/连接"，但实际永远解不出能解 NSIS 的 7z.exe。跑 6 次 740 全失败
        也是这条链路的副作用。

        返回 None 表示 4 步全失败（调用方应让用户手动装 7-Zip）。
        """
        import shutil as _sh
        LAUNCHER_CACHE_DIR = os.path.join(LAUNCHER_DIR, ".cache")

        def _verify(path, min_kb=500):
            """检查文件存在且 > min_kb KB, 可信返回 path, 否则 None。"""
            try:
                if path and os.path.isfile(path) and os.path.getsize(path) > min_kb * 1024:
                    return path
            except Exception:
                pass
            return None

        # 1) 进程内缓存
        cached = _verify(InstallWorker._SEVENZIP_PATH)
        if cached:
            return cached
        # 2) 持久化便携目录（成功装好后保留,跨 worker/跨 install 复用）
        persistent = _verify(os.path.join(LAUNCHER_7Z_DIR, "7z.exe"))
        if persistent:
            InstallWorker._SEVENZIP_PATH = persistent
            self._log(f"复用持久化 7z.exe: {persistent}")
            return persistent

        # 3) 已知位置：先扫 launcher 自己的 .cache/7zportable/（用户通常会装 7za）,
        #    再扫 PATH（scoop/choco 等可能装着 full 7z.exe）,再扫全局 Program Files。
        candidates = []
        # 3a) launcher .cache/7zportable/ 子目录
        if os.path.isdir(LAUNCHER_7Z_DIR):
            for name in ("7z.exe", "7za.exe", "7zr.exe"):
                p = _verify(os.path.join(LAUNCHER_7Z_DIR, name))
                if p:
                    candidates.append((f"launcher/.cache/7zportable/{name}", p))
        # 3b) PATH（含 7za/7zr 等独立控制台版,以及 scoop/choco 装的 7z）
        for name in ("7z", "7za", "7zr", "7z.exe", "7za.exe", "7zr.exe"):
            p = _verify(_sh.which(name) or "")
            if p:
                candidates.append((f"PATH/{name}", p))
        # 3c) 全局常见安装位置（含 x86、x64、ProgramW6432、显式 7-Zip 目录）
        roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 "C:/Program Files", "C:/Program Files (x86)",
                 os.environ.get("ProgramW6432"),
                 os.environ.get("LOCALAPPDATA"), "C:/Program Files/7-Zip"]
        seen = set()
        for base in roots:
            if not base or not os.path.isdir(base):
                continue
            for sub in ("7-Zip", ""):
                for name in ("7z.exe", "7za.exe", "7zr.exe"):
                    p = _verify(os.path.join(base, sub, name) if sub else os.path.join(base, name))
                    if p and p not in seen:
                        seen.add(p)
                        candidates.append((f"{base}/{sub}/{name}".replace("//", "/"), p))

        # 3-final) 任何候选如果是 full 7z.exe,直接返回（它已能解 NSIS）。
        # 按 candidates 顺序 = 偏好顺序（launcher > PATH > 全局）。
        for label, p in candidates:
            if os.path.basename(p).lower() == "7z.exe":
                InstallWorker._SEVENZIP_PATH = p
                self._log(f"复用 7z.exe: {label} -> {p}")
                return p

        # 没有任何 full 7z.exe（只有 7za/7zr 不行 → 它们解不了 NSIS）
        if candidates:
            self._log(
                f"已知位置只有 7za/7zr（不解 NSIS）：{[(c[0], os.path.basename(c[1])) for c in candidates[:5]]}"
            )
        else:
            self._log("已知位置都未发现 7-Zip，准备提权装 7z2602-x64.exe")

        # 4) 兜底：下 NSIS 7z2602-x64.exe + ShellExecuteW+runas 提权静默装到 LOCALAPPDATA。
        #    任何失败/取消都返回 None，调用方应报错让用户手动装 7-Zip 或放弃。
        return self._admin_install_7zip()

    def _admin_install_7zip(self):
        """兜底：下 NSIS 7z2602-x64.exe → ShellExecuteW+runas 提权静默装到 LOCALAPPDATA。

        NSIS installer (manifest=requiresAdministrator) 静默安装 /S 必须 admin,
        所以即使是写 LOCALAPPDATA 用户目录,也需要先弹一次 UAC 弹窗。
        ShellExecuteW("runas", ...) 触发 UAC,用户同意后 admin 子进程装到:
            %LOCALAPPDATA%\\Programs\\7-Zip\\7z.exe
        装好后主进程（non-admin）也能调,且落到用户目录不受系统保护。

        返回 7z.exe 路径或 None（失败/取消）。
        """
        import ctypes as _ct

        # 准备 installer 保存位置（避免污染 _dl_<key>/ 临时目录,跨 install/跨用户保留）
        installer_dir = os.path.join(LAUNCHER_DIR, ".cache")
        os.makedirs(installer_dir, exist_ok=True)
        setup_exe = os.path.join(installer_dir, "7z_installer.exe")

        # 0) 如果本地已有完整 NSIS installer (>1.4MB,典型 1.58MB),复用,不重下
        if os.path.isfile(setup_exe) and os.path.getsize(setup_exe) > 1_400_000:
            self._log(f"复用本地 NSIS installer: {setup_exe} ({os.path.getsize(setup_exe)/1048576:.1f}MB)")
        else:
            # 下 NSIS 7z2602-x64.exe (1.58MB)。优先测速 → 下到 installer_dir,
            # 总耗时通常 < 3s,且只下一次,跨次复用 (大缓存,跨次零下载)
            url_direct = "https://github.com/ip7z/7zip/releases/download/26.02/7z2602-x64.exe"
            cands = [(f"万载云 {nd}", nd + url_direct) for nd in self.WANZAIYUN_NODES]
            custom = (self.proxy or "").strip()
            if custom and custom not in self.WANZAIYUN_NODES:
                cands.append((f"万载云自定义 {custom}", custom + url_direct))
            _KNOWN_GH_PROXY = [
                "https://gh-proxy.org/",
                "https://ghproxy.com/",
                "https://cdn.gh-proxy.org/",
                "https://gh.xmly.dev/",
            ]
            for nd in _KNOWN_GH_PROXY:
                cands.append((f"备用 {nd}", nd + url_direct))
            cands.append(("GitHub 直链", url_direct))
            self.progress.emit(-1, f"准备 7-Zip NSIS installer：测速 {len(cands)} 个源…")
            scored = []
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=20) as ex:
                for r in ex.map(lambda it: (it[0], it[1], self._probe(it[1])), cands):
                    label, url, prob = r
                    ok, lat, cl = prob
                    if ok and cl > 0:
                        scored.append((label, url, lat))
                # 上面是 list comprehension 风格,等价的 map+filter:留给未来重构
            if not scored:
                self._log("7z2602-x64.exe 所有候选源都不可达,放弃自动准备")
                return None
            scored.sort(key=lambda x: x[2])
            last_err = None
            for label, url, lat in scored[:3]:  # 最多试 3 个源,够用
                if getattr(self, "_cancelled", False):
                    return None
                try:
                    self.progress.emit(-1, f"下 7-Zip installer：{label}（{lat*1000:.0f}ms）…")
                    if os.path.isfile(setup_exe):
                        try: os.remove(setup_exe)
                        except Exception: pass
                    self._stream_download(url, setup_exe, 0)
                    if getattr(self, "_cancelled", False):
                        return None
                    if not os.path.isfile(setup_exe) or os.path.getsize(setup_exe) < 1_400_000:
                        self._log(f"NSIS installer 文件异常（{os.path.getsize(setup_exe) if os.path.isfile(setup_exe) else 0}B）")
                        continue
                    self._log(f"NSIS installer 就绪: {setup_exe} ({os.path.getsize(setup_exe)/1048576:.1f}MB)")
                    break
                except Exception as e:
                    last_err = e
                    self._log(f"下 NSIS installer 失败({label}): {e}")
                    continue
            else:
                # 三个源都失败
                self._log(f"NSIS installer 全部失败: {last_err}")
                return None
            if not os.path.isfile(setup_exe) or os.path.getsize(setup_exe) < 1_400_000:
                self._log("NSIS installer 未达成,放弃")
                return None

        # 1) 提权静默装到 LOCALAPPDATA
        install_target = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
            "Programs", "7-Zip"
        )
        # NSIS /D= 路径必须无空格且 forward slashes；UNC/带空格路径常引发 NSIS 自身 bug,
        # 把 LOCALAPPDATA 一般是 C:\\Users\\<name>\\AppData\\Local,安全。
        # 若路径含空格：用短路径 \\?\\,否则直接用绝对路径。
        install_target_nsis = install_target.replace("/", "\\")
        # 如果子目录不存在,有些 NSIS installer 会自动创建,为稳我们 mkdir:
        try:
            os.makedirs(install_target_nsis, exist_ok=True)
        except Exception:
            pass

        args = f'/S /D="{install_target_nsis}"'
        self.progress.emit(-1, f"需要点一次 UAC 弹窗以安装 7-Zip（一次性）→ {install_target_nsis}")
        self._log(f"提权静默装 7-Zip NSIS: {setup_exe} {args}")
        try:
            rc = _ct.windll.shell32.ShellExecuteW(None, "runas", setup_exe, args, None, 0)  # SW_HIDE
        except Exception as e:
            self._log(f"ShellExecuteW 异常: {e}")
            return None
        # ShellExecuteW 返回值 > 32 表示成功(>32 = success),<=32 = error
        if rc <= 32:
            self._log(f"ShellExecuteW runas 失败 rc={rc} (用户可能点了'否'或未登录)")
            return None
        # ShellExecuteW 异步：我们轮询 7z.exe 是否出现在 install_target。
        # 静默 NSIS /S 通常 2-5 秒完成,最长 30 秒（防御性等更久）。
        self.progress.emit(-1, "等待 UAC + NSIS 静默安装完成…")
        target_7z = os.path.join(install_target_nsis, "7z.exe")
        for tick in range(120):  # 60 秒
            if getattr(self, "_cancelled", False):
                return None
            if os.path.isfile(target_7z) and os.path.getsize(target_7z) > 500 * 1024:
                InstallWorker._SEVENZIP_PATH = target_7z
                self._log(f"7-Zip 装好: {target_7z}")
                self.progress.emit(-1, f"7-Zip 就绪：{target_7z}")
                return target_7z
            time.sleep(0.5)
        self._log(f"提权装 7-Zip 超时未发现 7z.exe: {target_7z}")
        return None


    def _extract_nsis(self, setup_file, extract_dir):
        """用 7z 解 NSIS 整包到 extract_dir（NSIS 是公开格式，7z 23+ 直接支持）。"""
        sevenzip = self._ensure_7z(os.path.dirname(setup_file))
        if not sevenzip:
            raise RuntimeError(
                "解压完整安装包需要 7-Zip，但本机未找到且无法自动下载。\n"
                "请到 https://www.7-zip.org 安装 7-Zip 后重试，或重新打开原版启动器完成安装。"
            )
        os.makedirs(extract_dir, exist_ok=True)
        self.progress.emit(-1, "正在解压完整安装包（NSIS，约需 1-2 分钟）…")
        rc = subprocess.run(
            [sevenzip, "x", setup_file, f"-o{extract_dir}", "-y"],
            capture_output=True, text=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW：不弹黑窗
        )
        if rc.returncode != 0:
            raise RuntimeError(
                "7z 解压完整安装包失败：\n" + (rc.stderr or "")[:500]
                + "\n请把这段发我看看。"
            )

    def run(self):
        # 同 key 并发硬锁：拿不到锁直接退出（调用方守卫已拦，这里是兜底，防 worker 内部重入）
        _lock = InstallWorker._locks.setdefault(self.key, threading.Lock())
        if not _lock.acquire(blocking=False):
            self._log(f"同 key({self.key}) 已有 worker 在跑，本次直接放弃（防并发重复下载）")
            return
        try:
            self._log(f"==== 开始安装 {self.key} ====")
            tmp         = os.path.join(self.install_root, f"_dl_{self.key}")
            extract_dir = os.path.join(tmp, "_extract")
            install_dir = self.install_dir
            for q in (tmp, extract_dir):
                os.makedirs(q, exist_ok=True)

            # 1) 优先复用上次已下好的安装包（_dl_<key>/ 里），完全不打 GitHub API、不重下。
            #    用户之前下到一半/下完但解压失败留下的整包，本次重装直接复用，
            #    既能绕开「反复打 api.github.com 触发 403 限流」，又省 440MB 重下 + D 盘空间。
            local = self._scan_local_installer(tmp, self.key)
            need_download = True
            if local:
                zip_path, zip_name, zip_size, self._is_full = local
                tag = ""
                total = zip_size
                need_download = False
                self._log(f"发现本地安装包，直接复用（跳过 API+下载）：{zip_name} {zip_size/1048576:.0f}MB")
                self.progress.emit(100, f"已存在安装包（{zip_size/1048576:.0f}MB），跳过下载")
            else:
                # 2) 打 GitHub release API（带缓存，避免限流 403）查最新包
                self.progress.emit(-1, "正在查询最新 release…")
                setup = self._setup_asset_for()
                if setup:
                    zip_url, zip_name, zip_size, tag = setup
                    self._is_full = True
                else:
                    zip_url, zip_name, zip_size, tag = self._asset_for()
                    self._is_full = False
                zip_path = os.path.join(tmp, zip_name)
                total = 0
                # 文件名恰好匹配 API 返回的资产名 → 也算复用（不重复下）
                if os.path.isfile(zip_path):
                    _sz = os.path.getsize(zip_path)
                    _enough = (zip_size and _sz >= zip_size * 0.99) or (not zip_size and _sz > 100 * 1024)
                    if _enough:
                        total = _sz
                        need_download = False
                        self._log(f"复用已下载的安装包（{_sz/1048576:.1f}MB），跳过下载")
                        self.progress.emit(100, f"已存在完整安装包（{_sz/1048576:.1f}MB），跳过下载")

            # 用户在查 release / 测速 / 下载前就按了「取消」→ 收工
            if getattr(self, "_cancelled", False):
                self._cleanup_tmp(tmp)
                return

            # 目标已存在：可能是半装(host 引导包)。整包安装直接覆盖——先备份旧目录。
            if os.path.isdir(install_dir):
                if self._is_full:
                    bak = f"{install_dir}.host-bak-{int(time.time())}"
                    try:
                        shutil.move(install_dir, bak)
                        self.progress.emit(-1, f"已备份旧目录到 {bak}")
                    except Exception as e:
                        raise RuntimeError(
                            f"安装目录已存在且无法备份覆盖：{install_dir}\n"
                            f"请先卸载后再装。({e})"
                        )
                else:
                    raise RuntimeError(f"目标已存在：{install_dir}\n请先卸载旧版本后再装。")

            total_mb = zip_size / 1048576 if zip_size else 0
            kind_label = "完整安装包" if self._is_full else "host 引导包"

            if need_download:
                self.progress.emit(-1, f"准备下载 {zip_name}（约 {total_mb:.1f} MB）…")
                # 并发测速所有候选源（万载云 17 节点 + MirrorChyan + cnb + GitHub 直链），挑最快的
                scored = self._select_fastest(zip_url, tag, zip_name)
                if not scored:
                    raise RuntimeError(
                        "所有下载源测速均失败（万载云 17 节点 / MirrorChyan / cnb / GitHub 全超时）。\n"
                        "请检查本机网络是否能访问 GitHub，或稍后重试。"
                    )
                # 取前 3 名源依次尝试（失败换下一个，最多 3 次），避免单源抽风时无限重试/
                # 看起来「一直在换源重复下载」。每次失败删半截 zip，下次从头下。
                top = scored[:3]
                self._log(f"测速完成，候选 {len(scored)} 个，取前 {len(top)} 名依次尝试："
                          + "；".join(f"{l}({lt*1000:.0f}ms)" for l, _, lt in top))
                last_err = None
                for attempt, (label, cu, latency_s) in enumerate(top, 1):
                    if getattr(self, "_cancelled", False):
                        self._cleanup_tmp(tmp)
                        return
                    self._log(f"第{attempt}次下载，选源：{label}（延迟 {latency_s*1000:.0f}ms）")
                    self.progress.emit(
                        -1,
                        f"🚀 第{attempt}次下载，选源：{label}（延迟 {latency_s*1000:.0f}ms）",
                    )
                    try:
                        total = self._stream_download(cu, zip_path, zip_size)
                        break  # 成功即跳出重试循环
                    except Exception as e:
                        last_err = e
                        self._log(f"第{attempt}次下载失败：{e}")
                        self.progress.emit(-1, f"下载失败，换下一个源重试（{attempt}/{len(top)}）")
                        try:
                            if os.path.isfile(zip_path):
                                os.remove(zip_path)
                        except Exception:
                            pass
                    continue
                else:
                    # 3 次都失败：明确报错退出，不再静默重试
                    raise RuntimeError(
                        f"3 次下载均失败（已换 {len(top)} 个源）：{last_err}\n"
                        "请检查网络后重试，或填 MirrorChyan CDK 进一步加速。"
                    )

            real_size = os.path.getsize(zip_path) if os.path.isfile(zip_path) else 0
            if total <= 0:
                total = real_size
            self.progress.emit(100, f"下载完成 {real_size/1048576:.1f}MB, 准备解压")

            if not os.path.isfile(zip_path) or os.path.getsize(zip_path) < 100 * 1024:
                # 下载途中被取消：zip 不完整,直接清理 tmp 退出
                if getattr(self, "_cancelled", False):
                    self._cleanup_tmp(tmp)
                    return
                raise RuntimeError("下载失败或文件过小，请检查网络后重试")

            if getattr(self, "_cancelled", False):
                self._cleanup_tmp(tmp)
                return
            if self._is_full:
                # 整包：用 7z 解 NSIS，再做 PyAppify 内部名 → launcher key 对齐
                self._extract_nsis(zip_path, extract_dir)
                pyapp_key = self.REPOS[self.key][3]      # 如 ok-ef
                expected_exe = os.path.basename(self.app.get("exe", "") or "")
                launcher_key = os.path.splitext(expected_exe)[0] or self.key  # 如 ok-end-field
                # 对齐 data/apps/<pyapp_key> -> <launcher_key>
                inner_src = os.path.join(extract_dir, "data", "apps", pyapp_key)
                inner_dst = os.path.join(extract_dir, "data", "apps", launcher_key)
                if os.path.isdir(inner_src) and not os.path.exists(inner_dst):
                    try:
                        os.rename(inner_src, inner_dst)
                        self.progress.emit(-1, f"已对齐应用目录：{pyapp_key} → {launcher_key}")
                    except Exception as e:
                        self.progress.emit(-1, f"对齐应用目录失败（{e}），将尝试直接启动")
                # 对齐 host exe：<pyapp_key>.exe -> <launcher_key>.exe
                src_exe = os.path.join(extract_dir, pyapp_key + ".exe")
                if os.path.isfile(src_exe) and expected_exe and not os.path.isfile(os.path.join(extract_dir, expected_exe)):
                    try:
                        shutil.copy2(src_exe, os.path.join(extract_dir, expected_exe))
                        self.progress.emit(-1, f"已对齐 host 名：{pyapp_key}.exe → {expected_exe}")
                    except Exception as e:
                        self.progress.emit(-1, f"对齐 host 名失败（{e}），不影响启动")
                # 清理：删掉对齐后多余的旧 host exe（避免 install_dir 里残留两个 exe）
                if os.path.isfile(src_exe) and expected_exe:
                    try:
                        os.remove(src_exe)
                    except Exception:
                        pass
            else:
                # host 引导包：直接解 zip
                self.progress.emit(-1, "正在解压…")
                try:
                    with zipfile.ZipFile(zip_path) as zf:
                        zf.extractall(extract_dir)
                except zipfile.BadZipFile:
                    raise RuntimeError("下载到的不是有效 zip（可能被墙/被代理截断）。可尝试代理或 MirrorChyan CDK 重新下载。")

            self.progress.emit(-1, "正在整理文件…")
            # 自适应平铺：解压目录扫顶层
            #   - 顶层只有一个目录（如 ok-nte\ok-nte\... 或 ok-ef\ok-ef.exe）→ 整目录 move 到 install_dir
            #   - 顶层是多个文件/目录（如 ok-ww.exe, data\, python\）→ 整 extract move 到 install_dir
            top = sorted(os.listdir(extract_dir))
            if len(top) == 1 and os.path.isdir(os.path.join(extract_dir, top[0])):
                shutil.move(os.path.join(extract_dir, top[0]), install_dir)
            else:
                shutil.move(extract_dir, install_dir)

            # host 引导包（非整包）才需要做 host 名对齐；整包已在上面对齐过。
            if not self._is_full:
                expected_exe = os.path.basename(self.app.get("exe", "") or "")
                if expected_exe and os.path.isdir(install_dir):
                    target_exe = os.path.join(install_dir, expected_exe)
                    if not os.path.isfile(target_exe):
                        exes = sorted(
                            f for f in os.listdir(install_dir)
                            if f.lower().endswith(".exe") and os.path.isfile(os.path.join(install_dir, f))
                        )
                        if len(exes) == 1:
                            try:
                                shutil.copy2(os.path.join(install_dir, exes[0]), target_exe)
                                self.progress.emit(-1, f"已对齐 host 名：{exes[0]} → {expected_exe}")
                            except Exception as e:
                                self.progress.emit(-1, f"对齐 host 名失败（不影响解压）：{e}")

            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass

            if not os.path.isdir(install_dir):
                raise RuntimeError(f"安装目录未生成：{install_dir}")

            self.progress.emit(100, f"安装完成：{install_dir}")
            self.done.emit(install_dir, tag or "")
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            # 释放同 key 并发锁（覆盖 try 内所有 return / raise / 正常结束）
            try:
                _lock.release()
            except Exception:
                pass
            self._log(f"==== 结束安装 {self.key} ====")


class MirrorUpdater(QThread):
    """后台把目标 tag 拉到本地镜像仓库（只写 launcher/repos/<key>），发真实进度。"""
    progress = Signal(int, str)   # percent（-1 表示未知），text
    done = Signal(str)            # 本地仓库目录
    failed = Signal(str)

    def __init__(self, key, git_url, target_tag, parent=None):
        super().__init__(parent)
        self.key = key
        self.git_url = git_url
        self.target_tag = target_tag

    def _on_progress(self, line):
        pct, text = parse_git_progress(line)
        self.progress.emit(pct, text)

    def run(self):
        try:
            local = ensure_mirror(
                self.key, self.git_url, self.target_tag, self._on_progress
            )
            self.done.emit(local)
        except Exception as e:
            self.failed.emit(str(e))


# ===== 应用：把本地镜像同步到原启动器的 working/（直接覆盖，不备份）=====
# 说明：写的是官方 app 的 working/，属于官方本地安装目录，可写（用户已授权）。
# 不做整目录备份（占空间），但同步只覆盖"代码文件"，运行数据目录/数据库一律保留，
# 且原启动器本身可从仓库 checkout 任意版本做回滚，无需额外备份。

# working/ 里需要保留、绝不从镜像覆盖的运行数据路径（名匹配或后缀匹配）
_PRESERVE_DIRNAMES = {
    "cache", "logs", "config", "custom_chars", "ok_tasks",
    "screenshots", "data", "__pycache__", ".git", "venv", ".venv",
}
_PRESERVE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def _sync_repo_to_working(src_dir, dst_dir):
    """从镜像 src_dir 智能同步到 dst_dir：覆盖代码文件，保留运行数据目录/数据库。

    返回 (ok: bool, msg: str)。返回前会对 dst 目录做最小写入（仅代码文件）。
    """
    import shutil
    from pathlib import Path

    src = Path(src_dir)
    dst = Path(dst_dir)
    if not src.is_dir():
        return False, f"镜像目录不存在: {src_dir}"
    if not dst.is_dir():
        return False, f"目标 working 目录不存在: {dst_dir}"

    copied = skipped = 0
    for src_file in src.rglob("*"):
        if src_file.is_dir():
            continue
        rel = src_file.relative_to(src)
        # 跳过运行数据目录 / 数据库
        if any(p in _PRESERVE_DIRNAMES for p in rel.parts):
            skipped += 1
            continue
        if src_file.suffix in _PRESERVE_SUFFIXES:
            skipped += 1
            continue
        dst_file = dst / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        copied += 1
    return True, f"已同步 {copied} 个代码文件，保留 {skipped} 个运行数据文件"


def _kill_app_process(app):
    """通过 PowerShell 精确终止命令行含该 app 的 working 目录的 python/pythonw 进程。"""
    working_dir = app.get("working", "")
    if not working_dir:
        return True, "无 working 路径，跳过终止"
    ps_cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' or "
        "Name = 'pythonw.exe'\" | Where-Object { $_.CommandLine -like "
        f"\"*{working_dir}*\" }} | ForEach-Object {{ Stop-Process -Id "
        "$_.ProcessId -Force -ErrorAction SilentlyContinue }}"
    )
    try:
        # text=True 解码 PowerShell 输出可能因 GBK/UTF-8 不匹配抛 UnicodeDecodeError，
        # 显式 errors="replace" 保证 reader 线程不炸、主流程稳定。
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, errors="replace", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:  # noqa: BLE001
        return False, f"终止进程失败: {e}"
    return True, "已终止运行中的进程"


def _kill_app_by_title(key):
    """按窗口标题关键字精确终止正在运行的原启动器进程。

    与 _is_process_running 监测同源：PyAppify 打包的启动器运行时进程名是内嵌
    pythonw.exe（无独立 ok-nte.exe 镜像名），且 CommandLine 在 wmic 视角被降权清空，
    无法靠 working 目录匹配——但 tasklist /V 的窗口标题稳定可见（如 "ok-nte v1.3.7"）。
    故直接 tasklist 拿 PID 列表后用 taskkill /PID /F 终止，纯 cmd、GBK、零编码坑。

    返回 (ok: bool, msg: str)。
    """
    try:
        import csv as _csv
        import io as _io
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/V", "/FO", "CSV", "/NH"],
            capture_output=True, text=True,
            encoding="gbk", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        killed = 0
        for row in _csv.reader(_io.StringIO(out.stdout)):
            # CSV: image,pid,session,ses#,mem,status,user,cpu,window title
            if len(row) >= 9 and key in row[8].lower():
                pid = row[1].strip()
                if pid.isdigit():
                    r = subprocess.run(
                        ["taskkill", "/PID", pid, "/F", "/T"],
                        capture_output=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    if r.returncode == 0:
                        killed += 1
                    else:
                        err = (r.stderr or r.stdout).decode("gbk", "replace")
                        if "拒绝访问" in err or r.returncode == 1:
                            # 普通权限被杀拒绝，按需提权重试（弹一次 UAC）
                            runas(
                                "taskkill.exe",
                                ["/PID", pid, "/F", "/T"],
                                os.environ.get("SystemRoot", ""),
                            )
                            killed += 1
        if killed:
            return True, f"已强制关闭 {killed} 个运行中的进程"
        return True, "未发现运行中的进程"
    except Exception as e:  # noqa: BLE001
        return False, f"终止进程失败: {e}"


def apply_mirror_to_working(app, target_version, mirror_dir):
    """把 launcher/repos/<key>/ 镜像的 target_version 应用到原启动器 working/。

    流程：①终止运行进程 ②从镜像同步代码文件到 working/ ③更新 app.json 的 current_version。
    返回 (ok: bool, msg: str)。失败时绝不半途修改 app.json。
    """
    import json

    working_dir = app.get("working", "")
    app_json = app.get("app_json", "")
    if not working_dir or not app_json:
        return False, "缺少 working/app_json 路径配置"

    ok, msg = _kill_app_process(app)
    if not ok:
        return False, msg

    ok, msg = _sync_repo_to_working(mirror_dir, working_dir)
    if not ok:
        return False, msg

    # 同步成功后再更新 app.json 的当前版本（失败也不影响已同步的代码）
    try:
        with open(app_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["current_version"] = target_version
        # 清掉原启动器可能残留的更新中间态
        for k in ("update_state", "update_target_version", "update_error"):
            data.pop(k, None)
        with open(app_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        return False, f"代码已同步成功，但 app.json 写入失败：{e}（可手动修改 current_version）"

    return True, f"已应用 {target_version} 到 working/"


class ApplyWorker(QThread):
    """后台执行 apply_mirror_to_working，发进度/完成/失败信号。"""
    progress = Signal(str)
    done = Signal(bool, str)   # ok, msg

    def __init__(self, app, target_version, mirror_dir, parent=None):
        super().__init__(parent)
        self.app = app
        self.target_version = target_version
        self.mirror_dir = mirror_dir

    def run(self):
        try:
            self.progress.emit("正在终止应用进程…")
            ok, msg = _kill_app_process(self.app)
            if not ok:
                self.done.emit(False, msg)
                return
            self.progress.emit("正在同步代码到 working 目录（保留运行数据）…")
            ok, msg = _sync_repo_to_working(self.mirror_dir, self.app["working"])
            if not ok:
                self.done.emit(False, msg)
                return
            self.progress.emit("正在更新 app.json 当前版本…")
            import json
            with open(self.app["app_json"], "r", encoding="utf-8") as f:
                data = json.load(f)
            data["current_version"] = self.target_version
            for k in ("update_state", "update_target_version", "update_error"):
                data.pop(k, None)
            with open(self.app["app_json"], "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.done.emit(True, f"已应用 {self.target_version} 到 working/")
        except Exception as e:  # noqa: BLE001
            self.done.emit(False, f"应用失败：{e}")


# ===== 仅读取的工具函数（不写任何 app 目录） =====
def load_app_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_current_profile(data):
    """返回当前 profile 字典（根据 current_profile 从 profiles 里找）。"""
    name = data.get("current_profile", "China")
    for p in data.get("profiles", []) or []:
        if p.get("name") == name:
            return p
    if data.get("profiles"):
        return data["profiles"][0]
    return {}


def make_icon(path):
    try:
        if path and os.path.isfile(path):
            pm = QPixmap(path)
            if not pm.isNull():
                return QIcon(pm)
    except Exception:
        pass
    try:
        return FluentIcon.GAME.icon()
    except Exception:
        return QIcon()


def _sum_size(path):
    """递归计算文件/目录字节数。不存在返回 0；被占用的文件跳过不抛异常。"""
    if not os.path.exists(path):
        return 0
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _human_size(n):
    """字节数转人类可读（B / KB / MB / GB）。"""
    try:
        n = int(n)
    except Exception:
        return "?"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n/1024/1024:.1f} MB"
    return f"{n/1024/1024/1024:.2f} GB"


# ================= 本地游戏本体扫描（显卡驱动式「检测到游戏→引导装助手」） =================
# 2026-08-29 在用户本机实测过的识别方式（证据）：
#   - WeGame 鸣潮：注册表 HKLM\...\Uninstall\鸣潮 的 InstallSource 直接就是游戏根
#     （D:\WeGameApps\rail_apps\Wuthering Waves(2002137)），本体 exe 在
#     Client\Binaries\Win64\Client-Win64-Shipping.exe（深 3）
#   - TapTap 异环：D:\Taptap\PC Games\714119\Neverness To Everness\...，
#     本体 exe 在 Client\WindowsNoEditor\HT\Binaries\Win64\HTGame.exe（深 6，无注册表项）
#   - TapTap 终末地：D:\Taptap\PC Games\232326\games\Endfield Game\Endfield.exe（深 2）
# 原则：**注册表/渠道目录只当「线索」，最终以标志性 exe 的存在为准**——
#   实测注册表里「鸣潮助手」（WeGame 官方 aki 助手）也含"鸣潮"二字，但它的目录里
#   没有游戏本体 exe，天然不会误报；关键词 "NTE" 会误命中 "Python Interpreter"，
#   已从关键词表剔除。
GAME_SIGNATURES = {
    "ok-nte": {
        "display": "异环",
        "keywords": ["异环", "neverness to everness"],   # 注册表 DisplayName 小写匹配
        "game_exe": "HTGame.exe",
    },
    "ok-ww": {
        "display": "鸣潮",
        "keywords": ["鸣潮", "wuthering waves"],
        "game_exe": "Client-Win64-Shipping.exe",
    },
    "ok-end-field": {
        "display": "终末地",
        "keywords": ["终末地", "endfield"],
        "game_exe": "Endfield.exe",
    },
}

# walk 找 exe 的最大目录深度（异环 HTGame.exe 从渠道 id 目录起算深 6，留 1 层余量）
_GAME_SCAN_MAX_DEPTH = 7
# walk 剪枝：纯工程/缓存目录直接跳过，省时间
_GAME_SCAN_PRUNE = {".git", "__pycache__", "tcls", "wegamelauncher", "engine",
                    "content", "paks", "plugins"}


def _iter_uninstall_entries():
    """遍历 3 个 Uninstall 键，yield (DisplayName, InstallSource, InstallLocation, UninstallString)。"""
    import winreg
    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ):
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i); i += 1
            except OSError:
                break
            try:
                k = winreg.OpenKey(root, sub)
            except OSError:
                continue

            def _q(name, _k=k):
                try:
                    v, _ = winreg.QueryValueEx(_k, name)
                    return str(v)
                except OSError:
                    return ""
            yield (_q("DisplayName"), _q("InstallSource"), _q("InstallLocation"),
                   _q("UninstallString"))


def _channel_game_roots():
    """常见渠道游戏库根目录（存在才返回）。实测用户本机有效的是 TapTap 与 WeGame rail_apps。"""
    roots = []
    for drv in "CDEF":
        for rel in ("Taptap/PC Games", "WeGameApps/rail_apps"):
            p = f"{drv}:/{rel}"
            if os.path.isdir(p):
                roots.append(p)
    return roots


def _find_exe_limited(root, exe_name, max_depth=_GAME_SCAN_MAX_DEPTH):
    """限深 walk 找 exe。命中返回完整路径，否则 ""。"""
    root = os.path.normpath(root)
    base = root.count(os.sep)
    exe_low = exe_name.lower()
    for cur, dirs, files in os.walk(root):
        if cur.count(os.sep) - base >= max_depth:
            dirs[:] = []          # 到深限，不再下钻
        low = {f.lower() for f in files}
        if exe_low in low:
            p = os.path.join(cur, exe_name)
            if os.path.isfile(p):
                return p
        dirs[:] = [d for d in dirs if d.lower() not in _GAME_SCAN_PRUNE]
    return ""


def scan_local_game(key):
    """扫描本机某个游戏本体。命中返回游戏 exe 完整路径，否则 ""。

    线索来源（按序）：
      1) 注册表 Uninstall 项里 DisplayName 含关键词 → 取 InstallSource/InstallLocation/
         UninstallString 所在目录当候选根
      2) 渠道游戏库（Taptap/PC Games、WeGameApps/rail_apps）下每个子目录当候选根
    对每个候选根限深 walk 找标志性 exe——exe 存在才算数（线索会骗人，exe 不会）。
    """
    sig = GAME_SIGNATURES.get(key)
    if not sig:
        return ""
    exe = sig["game_exe"]
    kws = sig["keywords"]
    cands = []

    # 1) 注册表线索
    for name, src, loc, unins in _iter_uninstall_entries():
        if not name or not any(kw in name.lower() for kw in kws):
            continue
        for p in (src, loc, os.path.dirname((unins or "").strip('"'))):
            p = (p or "").strip().strip('"')
            if p and os.path.isdir(p):
                cands.append(os.path.normpath(p))

    # 2) 渠道库兜底
    for base in _channel_game_roots():
        try:
            for sub in os.listdir(base):
                p = os.path.join(base, sub)
                if os.path.isdir(p):
                    cands.append(os.path.normpath(p))
        except OSError:
            continue

    # 逐候选找（去重防重复 walk 大目录）
    seen = set()
    for root in cands:
        if root in seen:
            continue
        seen.add(root)
        hit = _find_exe_limited(root, exe)
        if hit:
            return hit
    return ""


class GameScanWorker(QThread):
    """后台扫描多个游戏本体，避免注册表枚举 + 大目录 walk 卡 UI。"""

    done = Signal(dict)   # {key: game_exe_path}

    def __init__(self, keys, parent=None):
        super().__init__(parent)
        self.keys = list(keys)

    def run(self):
        found = {}
        for k in self.keys:
            try:
                p = scan_local_game(k)
            except Exception:
                p = ""
            if p:
                found[k] = p
        self.done.emit(found)


def send_to_trash(path):
    """把文件或目录移入 Windows 回收站（可撤销）。返回 (success, message)。"""
    if not os.path.exists(path):
        return False, f"路径不存在：{path}"

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 0x0003
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_SILENT = 0x0004

    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = path + "\0\0"
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None

    ret = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if ret == 0 and not op.fAnyOperationsAborted:
        return True, "已移入回收站"
    return False, f"操作失败或已被取消（错误码：{ret}）"


def runas(exe, args_list, cwd):
    """以管理员身份运行（弹 UAC）。args_list 为字符串列表。"""
    params = subprocess.list2cmdline(args_list) if args_list else ""
    ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, cwd, 1)
    return ret > 32


def run_exe(parent, exe, args=None, cwd=None, need_admin=False, show_errors=True):
    """运行程序：普通方式失败(740)或 need_admin=True 时用 runas 提权。"""
    if not os.path.isfile(exe):
        if show_errors:
            QMessageBox.critical(parent, "失败", f"找不到可执行文件：\n{exe}")
        return False
    args = args or []
    cwd = cwd or os.path.dirname(exe)
    if need_admin:
        return runas(exe, args, cwd)
    try:
        subprocess.Popen(
            [exe] + args, cwd=cwd, shell=False,
            creationflags=0x00000008,  # DETACHED_PROCESS
        )
        return True
    except OSError as e:
        if getattr(e, "winerror", None) == 740 or "740" in str(e):
            return runas(exe, args, cwd)
        if show_errors:
            QMessageBox.critical(parent, "失败", f"无法启动：\n{exe}\n\n错误：{e}")
        return False
    except Exception as e:
        if show_errors:
            QMessageBox.critical(parent, "失败", f"无法启动：\n{exe}\n\n错误：{e}")
        return False


class TagLabel(QLabel):
    def __init__(self, text, bg="#3a3a44", fg="#ffffff"):
        super().__init__(text)
        self.setStyleSheet(
            f"background-color:{bg}; color:{fg}; border-radius:6px; "
            f"padding:2px 8px; font-size:11px;"
        )
        self.setAlignment(Qt.AlignCenter)


class StatusBadge(QLabel):
    """WeGame 风格状态徽章。"""

    def __init__(self, text, color="#cfcfcf", bg="rgba(255,255,255,0.15)"):
        super().__init__(text)
        self.setStyleSheet(
            f"background-color:{bg}; color:{color}; border-radius:9px; "
            f"padding:3px 12px; font-size:12px; font-weight:600;"
        )
        self.setAlignment(Qt.AlignCenter)


class AppCard(CardWidget):
    """一张游戏卡片。静态部分（封面/名称/徽章/状态行）固定，动态部分按安装状态重建。"""

    def __init__(self, app):
        super().__init__()
        self.app = app
        # 单列竖排：只固定宽度（匹配 760px 容器 - 32px 边距），高度随 changelog 内容自动撑开。
        # 之前 setFixedSize(440, 560) 是为 2 列网格设计的，写死高 560 导致长 changelog 被截。
        self.setFixedWidth(720)

        self._version_map = {}  # display text -> raw tag
        self._changelog_worker = None
        self._version_fetcher = None
        self._version_notes_list = None  # 原启动器返回的 [{version, update_note}]（同源）
        self.game_found_path = ""        # 本地游戏本体扫描结果（""=未检测到）

        # 静态骨架
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.setAlignment(Qt.AlignTop)

        # 封面区（渐变底 + 游戏图标）
        self.cover = QLabel()
        self.cover.setFixedHeight(150)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            "stop:0 #3b4256, stop:1 #262b3a); border-radius:10px;"
        )
        root.addWidget(self.cover)

        # 名称 + 状态徽章
        head = QHBoxLayout()
        head.setSpacing(10)
        name = StrongBodyLabel(app["display"])
        name.setStyleSheet("font-size:20px; font-weight:700;")
        head.addWidget(name)
        head.addStretch(1)
        self.badge = StatusBadge("已安装", color="#8dffb0", bg="rgba(45,125,50,0.25)")
        head.addWidget(self.badge)
        # 独立运行态标签：仅运行中时显示「● 运行中」，与徽章（已安装/可更新）、
        # 按钮（强制关闭）三者分工互不重复。
        self.run_tag = QLabel("● 运行中")
        self.run_tag.setStyleSheet(
            "color:#8dffb0; font-size:12px; font-weight:600; padding:2px 4px;"
        )
        self.run_tag.setVisible(False)
        head.addWidget(self.run_tag)
        root.addLayout(head)

        # 信息行：版本 / 配置
        info_row = QHBoxLayout()
        info_row.setSpacing(6)
        self.ver_tag = TagLabel("未知", bg="#616161")
        info_row.addWidget(self.ver_tag)
        self.profile_tag = TagLabel("", bg="#3a3a44")
        info_row.addWidget(self.profile_tag)
        info_row.addStretch(1)
        root.addLayout(info_row)

        self.status_label = CaptionLabel("")
        root.addWidget(self.status_label)

        # 更新进度区（持久，不随 body_box 重建）：原启动器更新时实时反映
        self.update_bar = IndeterminateProgressBar()
        self.update_bar.setFixedHeight(6)
        self.update_bar.setVisible(False)
        root.addWidget(self.update_bar)
        self.update_label = CaptionLabel("")
        self.update_label.setVisible(False)
        root.addWidget(self.update_label)

        # 动态内容容器：根据安装状态重建
        self.body_box = QVBoxLayout()
        self.body_box.setSpacing(10)
        root.addLayout(self.body_box)

        # ===== 安装进度区（持久，默认隐藏，install_app 启动时显示并驱动） =====
        # 不再用 QProgressDialog 模态弹窗——之前那个弹窗在 PySide6 自定义窗口主题下
        # 居中渲染时与窗口本体撞色/撞焦点，截图里顶部那条"刷新检测/安装"长条就是它
        # 被窗口遮住上半截、label 文字被挤在按钮区里渲染造成的视错觉（"界面也出 bug 了"）。
        # 改成内嵌在卡片底部，用户随时看到进度、随时点"取消"，不会被模态抢焦点。
        self.install_progress = QProgressBar()
        self.install_progress.setRange(0, 100)
        self.install_progress.setValue(0)
        self.install_progress.setFixedHeight(8)
        self.install_progress.setTextVisible(False)
        self.install_progress.setStyleSheet(
            "QProgressBar { background-color:rgba(255,255,255,0.10); border-radius:4px; }"
            "QProgressBar::chunk { background-color:#1976d2; border-radius:4px; }"
        )
        self.install_progress.setVisible(False)
        root.addWidget(self.install_progress)
        self.install_status = CaptionLabel("")
        self.install_status.setWordWrap(True)
        self.install_status.setStyleSheet("color:#9ec5ff; font-size:12px;")
        self.install_status.setVisible(False)
        root.addWidget(self.install_status)
        self.install_cancel_btn = PushButton("✕ 取消下载")
        self.install_cancel_btn.setFixedHeight(32)
        self.install_cancel_btn.setStyleSheet(
            "QPushButton { background-color:#455a64; color:white; border-radius:6px; "
            "font-weight:600; } QPushButton:hover { background-color:#37474f; }"
        )
        self.install_cancel_btn.setVisible(False)
        self.install_cancel_btn.clicked.connect(self._on_install_cancel_clicked)
        root.addWidget(self.install_cancel_btn)

        root.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_data)
        self._timer.start(5000)

        self.rebuild_body()  # 首次填充

    # ===== 动态内容 =====
    def clear_body(self):
        """彻底清空 body_box 里的所有子项（widget + layout 都要清）。

        旧版只 widget().deleteLater()，但 build_*_body 加进去的是 QHBoxLayout
        （btn_row / ver_row），item.widget() 对 layout item 返回 None，被跳过——
        残留的 layout item 仍占位且引用上一次的 child widget，反复 rebuild_body 后
        上一组的按钮组（"启动 + 原版管理"）+ 下一组的（"安装 + 刷新检测"）并存，
        UI 看起来就是「按钮挤一起重叠」（用户最新截图就是这个症状）。
        """
        while self.body_box.count():
            item = self.body_box.takeAt(0)
            if item is None:
                break
            # 子 widget：直接销毁
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
                continue
            # 子 layout：清掉 layout 自己的子 widget（递归一层够用），再 setParent(None)
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    if si is None:
                        break
                    sw = si.widget()
                    if sw is not None:
                        sw.setParent(None)
                        sw.deleteLater()

    def _resolve_host_exe(self, install_dir=None):
        """定位 install_dir 里的 host exe（PyAppify 引导包）。

        返回 (expected_path, actual_path)：
        - expected_path：config["exe"] 的预期路径
        - actual_path  ：install_dir 里实际存在的 .exe（PyAppify host）
          若 config 期望的名字已存在，actual = expected；否则 = 唯一 .exe；
          若 install_dir 不存在或没有 .exe，返回 (expected, None)。

        win32.zip 解出的 host 命名可能跟 config 不一致
        （如 ok-end-field 解出 ok-ef.exe，config 期望 ok-end-field.exe），
        故优先以 config 名为准，找不到再退而求其次。
        """
        cfg_exe = self.app.get("exe", "") or ""
        install_dir = install_dir or os.path.dirname(cfg_exe)
        if not install_dir or not os.path.isdir(install_dir):
            return (cfg_exe, None)
        expected = cfg_exe
        if os.path.isfile(expected):
            return (expected, expected)
        # 找唯一 .exe 作为 host
        exes = sorted(
            f for f in os.listdir(install_dir)
            if f.lower().endswith(".exe") and os.path.isfile(os.path.join(install_dir, f))
        )
        if len(exes) == 1:
            return (expected, os.path.join(install_dir, exes[0]))
        if len(exes) > 1:
            # 多个 exe：挑名字跟 key 接近的那个
            key = self.app.get("key", "")
            for e in exes:
                if key and key in e.lower():
                    return (expected, os.path.join(install_dir, e))
            return (expected, os.path.join(install_dir, exes[0]))
        return (expected, None)

    def rebuild_body(self):
        """根据当前 app.json 是否有效，重建动态区。"""
        self.data = load_app_json(self.app["app_json"])
        self.profile = get_current_profile(self.data)
        # 三态：
        #   - installed   : working/main.py 存在（PyAppify 完整初始化过）
        #   - host_ready  : working/main.py 不在，但 install_dir 里有任意 .exe（半装——刚解压完）
        #   - not_installed: 啥都没有
        cfg_exe = self.app.get("exe", "") or ""
        install_dir = os.path.dirname(cfg_exe) or ""
        app_json_dir = os.path.dirname(self.app.get("app_json", "") or "")
        working_main = os.path.join(app_json_dir, "working", "main.py") if app_json_dir else ""
        # _resolve_host_exe 返回 (expected, actual)；只要 actual 不为空就是有 host
        _, host_actual = self._resolve_host_exe(install_dir)
        working_main_exists = bool(working_main) and os.path.isfile(working_main)
        host_exists = bool(host_actual) and os.path.isfile(host_actual)
        self._installed = working_main_exists
        self._host_ready = host_exists and not working_main_exists
        self._host_actual = host_actual if host_exists else ""
        self._install_dir = install_dir

        # 封面图标（装好后才有真实图标，未安装用默认）
        self.cover.setPixmap(make_icon(self.app["icon"]).pixmap(96, 96))

        # 徽章与信息行
        if self._installed:
            self.ver_tag.setText(self.data.get("current_version") or "未知")
            self.ver_tag.setStyleSheet(
                "background-color:#2e7d32; color:#ffffff; border-radius:6px; "
                "padding:2px 8px; font-size:11px;"
            )
            prof = self.data.get("current_profile", "")
            self.profile_tag.setText(prof)
            self.profile_tag.setVisible(bool(prof))
            # 已安装态：先把 status_label 清空，避免上次未安装态写入的「未检测到本地
            # 安装」/「待初始化」等文字残留造成「badge 已安装 / 文案未安装」自相矛盾
            # 的诡异状态（用户最新截图就是这个症状）。运行中会由 launch_app 的
            # QTimer.singleShot(5s, clear) 短暂占位「已发起启动」，下次轮询会自然清空。
            self.status_label.setText("")
            if not self.status_label.text():
                self.status_label.setText(self.get_status_text())
            self.refresh_badge()
        elif self._host_ready:
            # 半装：host.exe 已就位、但 working/main.py 还没生成
            self.ver_tag.setText("待初始化")
            self.ver_tag.setStyleSheet(
                "background-color:#e65100; color:#ffffff; border-radius:6px; "
                "padding:2px 8px; font-size:11px;"
            )
            self.profile_tag.clear()
            self.profile_tag.setVisible(False)
            self.status_label.setText("已解压 host 引导包，需运行一次完成初始化")
            self.badge.setText("待初始化")
            self.badge.setStyleSheet(
                "background-color:#e65100; color:#ffffff; border-radius:9px; "
                "padding:3px 12px; font-size:12px; font-weight:600;"
            )
        else:
            self.ver_tag.setText("未安装")
            self.ver_tag.setStyleSheet(
                "background-color:#616161; color:#ffffff; border-radius:6px; "
                "padding:2px 8px; font-size:11px;"
            )
            self.profile_tag.clear()
            self.profile_tag.setVisible(False)
            self.status_label.setText("未检测到本地安装")
            self.badge.setText("未安装")
            self.badge.setStyleSheet(
                "background-color:rgba(255,255,255,0.15); color:#cfcfcf; "
                "border-radius:9px; padding:3px 12px; font-size:12px; font-weight:600;"
            )

        # 动态区
        self.clear_body()
        if self._installed:
            self.build_installed_body()
        elif self._host_ready:
            self.build_half_installed_body()
        else:
            self.build_uninstalled_body()

        # 兜底：每次 rebuild 都强制 hide 安装进度三件套，防止 install_app 在
        # _on_failed / cancel 等异常路径未跑到 _hide_install_progress_ui 时，
        # 残留的 install_progress/install_status/install_cancel_btn 跟新按钮挤一起。
        self._hide_install_progress_ui()

        # 更新进度（含未安装时若 app.json 仍含更新状态，也显示）
        self.update_progress_ui()

    def build_installed_body(self):
        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.start_btn = PushButton("▶  启动应用")
        self.start_btn.setFixedHeight(42)
        self.start_btn.setStyleSheet(
            "QPushButton { background-color:#2e7d32; color:white; border-radius:8px; "
            "font-weight:600; } QPushButton:hover { background-color:#1b5e20; }"
        )
        # 槽随运行态切换（见 _refresh_start_btn），这里不固定 connect
        btn_row.addWidget(self.start_btn, stretch=1)
        self.body_box.addLayout(btn_row)

        # 卸载：独立整行、红色描边实体按钮，醒目但不刺眼
        self.uninstall_btn = PushButton("卸载此助手")
        self.uninstall_btn.setFixedHeight(38)
        self.uninstall_btn.setStyleSheet(
            "QPushButton { background-color:rgba(255,82,82,0.10); "
            "color:#ff6b6b; border:1px solid #ff5252; border-radius:8px; "
            "font-weight:600; } "
            "QPushButton:hover { background-color:rgba(255,82,82,0.22); "
            "color:#ff8585; } "
            "QPushButton:pressed { background-color:rgba(255,82,82,0.35); }"
        )
        self.uninstall_btn.setCursor(Qt.PointingHandCursor)
        self.uninstall_btn.clicked.connect(self.uninstall_app)
        self.body_box.addWidget(self.uninstall_btn)

        # 更新按钮：已安装卡片总是显示，根据真实完整版本列表判断"是否有可更新版本"
        self.update_btn = PushButton("检查更新中…")
        self.update_btn.setFixedHeight(38)
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.clicked.connect(self.open_update_dialog)
        self.body_box.addWidget(self.update_btn)
        self.refresh_update_button()  # 用当前已知信息立即刷一次

        # 查看版本（只读下拉）
        ver_row = QHBoxLayout()
        ver_row.setSpacing(10)
        ver_row.addWidget(CaptionLabel("查看版本"))
        self.ver_combo = ComboBox()
        self.ver_combo.setMinimumWidth(200)
        self.ver_combo.setPlaceholderText("选择版本查看说明...")
        self.populate_versions()
        ver_row.addWidget(self.ver_combo)

        self.refresh_btn = PushButton("↻ 刷新")
        self.refresh_btn.setFixedHeight(32)
        self.refresh_btn.setFixedWidth(78)
        self.refresh_btn.setToolTip("重新从网络拉取最新版本列表与更新说明")
        self.refresh_btn.clicked.connect(self.manual_refresh)
        ver_row.addWidget(self.refresh_btn)
        ver_row.addStretch(1)
        self.body_box.addLayout(ver_row)

        # 版本说明（changelog，只读）
        self.body_box.addWidget(CaptionLabel("版本说明"))
        self.changelog_text = QTextEdit()
        self.changelog_text.setReadOnly(True)
        # 自适应高度：内容少时 ≥120px 紧凑显示；内容多时按 commit 行数自动撑开。
        # 单列竖排布局下卡片可自由变高，不再设 maxHeight 截断（之前 360px 对长 commit 列表仍不够）。
        # QTextEdit 默认 sizeHint 基于 viewport，不会随 document 增长——需要监听 contentsChanged
        # 主动把 minHeight 调成「document 高度 + 边框 + 内边距」，才能让卡片随 changelog 自由撑高。
        self.changelog_text.setMinimumHeight(120)
        self.changelog_text.setPlaceholderText("选择目标版本后显示更新内容...")
        self.changelog_text.setStyleSheet(
            "QTextEdit { background-color: rgba(0,0,0,0.12); border-radius:6px; "
            "padding:6px; border:none; }"
        )
        self.changelog_text.document().contentsChanged.connect(self._adjust_changelog_height)
        self.body_box.addWidget(self.changelog_text)

        self.ver_combo.currentTextChanged.connect(self.on_version_changed)
        self.load_changelog()  # 初始状态也加载一次

    def build_uninstalled_body(self):
        # 未安装：「安装」按钮 + 「🔄 刷新检测」小按钮（让用户跑完外部初始化后能主动 trigger rebuild）
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.install_btn = PushButton("安装")
        self.install_btn.setFixedHeight(42)
        self.install_btn.setStyleSheet(
            "QPushButton { background-color:#1976d2; color:white; border-radius:8px; "
            "font-weight:600; } QPushButton:hover { background-color:#1565c0; }"
        )
        self.install_btn.clicked.connect(self.install_app)
        btn_row.addWidget(self.install_btn, stretch=1)

        self.rescan_btn = PushButton("🔄 刷新检测")
        self.rescan_btn.setFixedHeight(42)
        self.rescan_btn.setFixedWidth(120)
        self.rescan_btn.setStyleSheet(
            "QPushButton { background-color:#37474f; color:white; border-radius:8px; "
            "font-weight:600; } QPushButton:hover { background-color:#455a64; }"
        )
        self.rescan_btn.setToolTip("如果你已经在外部装好了，点这里重新检测本机状态")
        self.rescan_btn.clicked.connect(self.rebuild_body)
        btn_row.addWidget(self.rescan_btn)
        self.body_box.addLayout(btn_row)

        # 游戏本体扫描提示（显卡驱动式：检测到游戏 → 引导装助手）。常驻显示直到装好。
        if getattr(self, "game_found_path", ""):
            sig = GAME_SIGNATURES.get(self.app.get("key", ""), {})
            gname = sig.get("display") or self.app.get("display", "游戏")
            found_hint = CaptionLabel(
                f"🎮 检测到本机已安装《{gname}》游戏本体：\n{self.game_found_path}\n"
                f"点「安装」装上本助手后，即可自动化日常任务。"
            )
            found_hint.setWordWrap(True)
            found_hint.setStyleSheet(
                "color:#64b5f6; background-color:rgba(25,118,210,0.10); "
                "border-radius:6px; padding:8px;"
            )
            self.body_box.addWidget(found_hint)

        hint = CaptionLabel(
            "点击「安装」即可装助手：优先下载完整安装包（解压即可直开本体）；"
            "若官方只提供 host 包，则解压后需再点一次「启动 host 初始化」。\n"
            "没检测到游戏本体不影响安装——助手装好后靠窗口自动找到游戏，装在哪都行。"
        )
        hint.setWordWrap(True)
        self.body_box.addWidget(hint)

    def set_game_found(self, exe_path):
        """游戏本体扫描命中后由主窗口回调。已装态忽略；未装态记录并重绘显示提示条。"""
        if not exe_path:
            return
        if getattr(self, "_installed", False) or getattr(self, "_host_ready", False):
            return  # 已装好助手，不需要引导
        self.game_found_path = exe_path
        self.rebuild_body()

    def build_half_installed_body(self):
        """半装状态：host.exe 已就位但 working/main.py 尚未生成。

        主动作改为「装完整版」——让聚合启动器自己下载完整 NSIS 整包并 7z 解好，
        解压后 working/main.py 就位，「启动应用」即可直开本体，彻底跳过 PyAppify host。
        仅在整包下载失败时才用「启动 host 初始化（兜底）」让 host 自己拉一次运行时。
        """
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        full_btn = PushButton("▶  装完整版（推荐）")
        full_btn.setFixedHeight(42)
        full_btn.setStyleSheet(
            "QPushButton { background-color:#2e7d32; color:white; border-radius:8px; "
            "font-weight:600; } QPushButton:hover { background-color:#1b5e20; }"
        )
        full_btn.setToolTip(
            "聚合启动器自己下载完整 NSIS 整包（~440MB，走镜像/反代）并 7z 解好，\n"
            "解压后 working/main.py 就位，之后「启动应用」直接开本体，永不弹 PyAppify host 小窗。"
        )
        full_btn.clicked.connect(self.install_app)
        btn_row.addWidget(full_btn, stretch=2)

        host_btn = PushButton("启动 host 初始化（兜底）")
        host_btn.setFixedHeight(42)
        host_btn.setStyleSheet(
            "QPushButton { background-color:#e65100; color:white; border-radius:8px; "
            "font-weight:600; } QPushButton:hover { background-color:#ef6c00; }"
        )
        host_btn.setToolTip(
            "仅当「装完整版」因网络/镜像失败时才用：启动 host.exe 让 PyAppify\n"
            "自己拉运行时（约几百 MB、需联网数分钟），结束后回这里点「🔄 刷新」。"
        )
        host_btn.clicked.connect(self.open_manager)  # 复用原版启动器入口（它会 runas 启动 host.exe）
        btn_row.addWidget(host_btn, stretch=2)

        self.rescan_btn = PushButton("🔄 刷新")
        self.rescan_btn.setFixedHeight(42)
        self.rescan_btn.setFixedWidth(120)
        self.rescan_btn.setStyleSheet(
            "QPushButton { background-color:#37474f; color:white; border-radius:8px; "
            "font-weight:600; } QPushButton:hover { background-color:#455a64; }"
        )
        self.rescan_btn.setToolTip("如果你已经在外部完成了 host 初始化，点这里重新检测")
        self.rescan_btn.clicked.connect(self.rebuild_body)
        btn_row.addWidget(self.rescan_btn)
        self.body_box.addLayout(btn_row)

        hint = CaptionLabel(
            "已存在 host 引导包，但完整运行时（Python venv、working/、cache）尚未初始化。\n\n"
            "推荐点上方「▶ 装完整版（推荐）」让聚合启动器直接下完整安装包并解好，\n"
            "之后「启动应用」即可直开助手主窗口，彻底跳过 PyAppify host 小窗。\n"
            "（若整包下载失败，再用「启动 host 初始化（兜底）」让 host 自己拉一次。）"
        )
        hint.setWordWrap(True)
        self.body_box.addWidget(hint)

    # ===== 只读数据 =====
    def manual_refresh(self):
        """手动重新拉取版本列表与说明（只读，不写任何 app 目录）。"""
        self.data = load_app_json(self.app["app_json"])
        self.profile = get_current_profile(self.data)
        self.populate_versions()  # 重新填缓存版本 + 后台拉取完整 github 版本
        self.load_changelog()

    def _adjust_changelog_height(self):
        """按 document 实际高度调整 changelog_text 的 minHeight，让卡片随 commit 列表自动撑开。

        QTextEdit 默认 sizeHint 基于 viewport，不会随 document 增长——必须手动把
        minHeight 设成「document 高度 + 边框 + 内边距」。监听 contentsChanged 后，
        setPlainText/setHtml 一变文档就触发，连带父卡片布局也跟着撑高。
        """
        if not hasattr(self, "changelog_text"):
            return
        doc_h = int(self.changelog_text.document().size().height())
        # frameWidth()*2（上下边框）+ 文档上下内边距 + 4px 微调余量
        extra = self.changelog_text.frameWidth() * 2 + 6
        self.changelog_text.setMinimumHeight(max(120, doc_h + extra))

    def refresh_data(self):
        data = load_app_json(self.app["app_json"])
        now_installed = bool(data)
        if now_installed != self._installed:
            # 用户装好/卸载后刷新整张卡片动态区
            self.rebuild_body()
            return
        if not self._installed:
            return
        self.data = data
        self.profile = get_current_profile(self.data)
        # 仅在空白时才覆盖小灰字，避免抢走"已发起启动"过渡提示
        if not self.status_label.text():
            self.status_label.setText(self.get_status_text())
        self.refresh_badge()
        self.update_progress_ui()
        new_ver = self.data.get("current_version", "") or "未知"
        if self.ver_tag.text() != new_ver:
            self.ver_tag.setText(new_ver)
            self.populate_versions()
            self.load_changelog()
            self.refresh_update_button()

    def _is_process_running(self):
        """判定本 app 对应的原启动器是否真实在跑（兜底 app.json.running 不可靠）。

        监测目标就是那个 exe 程序本体（如 ok-nte.exe）。判定优先级：
          1）先看进程表里是否有该 exe 的镜像名（tasklist /FI IMAGENAME）——最直接、最准；
          2）PyAppify 打包的启动器常以内嵌 pythonw.exe 方式运行（exe 主体藏在 pythonw
             里，进程表里见不到 ok-nte.exe 镜像名，只见 pythonw.exe），且 PyAppify
             会把 CommandLine/ExecutablePath 在 wmic 视角下清空（token 降权），无法靠
             命令行匹配 working 目录。**唯一可靠线索是窗口标题**（tasklist /V CSV 第 9
             列），异环标题固定含 "ok-nte"、鸣潮含 "ok-ww" 等。tasklist 走纯 cmd、
             GBK 编码与 encoding="gbk" 完美匹配，不会有 PowerShell 那种 UTF-16/UTF-8
             编码混乱的坑（曾因 encoding="gbk" 解 PowerShell stdout 抛 UnicodeDecodeError
             导致整条路径静默 return False 的根因）。

        所有子进程走 CREATE_NO_WINDOW，不弹黑窗。
        """
        try:
            exe = self.app.get("exe", "")
            key = os.path.basename(exe).lower().removesuffix(".exe")  # "ok-nte"
            if not key:
                return False

            import subprocess as _sp
            import csv as _csv
            import io as _io
            flags = getattr(_sp, "CREATE_NO_WINDOW", 0)

            # ① 优先查 exe 镜像名（某些版本/状态下进程表里会有 ok-nte.exe）
            out = _sp.run(
                ["tasklist", "/FI", f"IMAGENAME eq {key}.exe", "/NH"],
                capture_output=True, text=True,
                encoding="gbk", errors="replace", creationflags=flags,
            )
            if f"{key}.exe" in out.stdout.lower():
                return True

            # ② 回退：pythonw 形态运行时，按窗口标题定位（cmd GBK、稳）
            #    限定 /FI pythonw.exe 避免扫全表（200 进程会阻塞 2~5 秒）
            out = _sp.run(
                ["tasklist", "/FI", "IMAGENAME eq pythonw.exe",
                 "/V", "/FO", "CSV", "/NH"],
                capture_output=True, text=True,
                encoding="gbk", errors="replace", creationflags=flags,
            )
            for row in _csv.reader(_io.StringIO(out.stdout)):
                # CSV: image,pid,session,ses#,mem,status,user,cpu,window title
                if len(row) >= 9 and key in row[8].lower():
                    return True
            return False
        except Exception:
            return False

    def refresh_badge(self):
        """根据更新/安装状态刷新徽章（仅承载「未安装 / 已安装 / 可更新」语义）。

        运行态由三个独立控件分工，互不重复：
          - 徽章：只显示「已安装 / 可更新 vX」（不写"运行中"）
          - run_tag（徽章右侧独立标签）：仅运行中时显示「● 运行中」绿字
          - 启动按钮：运行中时改写「强制关闭」红底（点它二次确认后杀进程）
        运行判定走 _is_process_running 兜底（app.json.running 不可靠）。
        """
        d = self.data
        # 严格以真实进程为准：app.json.running 字段是原启动器启动时写、关闭时没清，
        # 异环进程死透后 app.json 还留着 true，会让聚合启动器持续误判"运行中"。
        # 兜底机制是「tasklist 找不到时多等一次轮询」而非直接相信过期字段。
        running = self._is_process_running()
        # 按钮随运行态切换（启动应用 / 强制关闭）
        self._refresh_start_btn(running=running)
        # run_tag：仅运行中可见
        if hasattr(self, "run_tag"):
            self.run_tag.setVisible(bool(running))
        if running:
            self.badge.setText("已安装")
            self.badge.setStyleSheet(
                "background-color:rgba(45,125,50,0.25); color:#8dffb0; "
                "border-radius:9px; padding:3px 12px; font-size:12px; font-weight:600;"
            )
            return
        versions = d.get("available_versions", []) or []
        current = d.get("current_version", "")
        if versions and current and versions[0] != current:
            self.badge.setText(f"可更新 {versions[0]}")
            self.badge.setStyleSheet(
                "background-color:rgba(255,152,0,0.25); color:#ffb74d; "
                "border-radius:9px; padding:3px 12px; font-size:12px; font-weight:600;"
            )
            return
        self.badge.setText("已安装")
        self.badge.setStyleSheet(
            "background-color:rgba(45,125,50,0.25); color:#8dffb0; "
            "border-radius:9px; padding:3px 12px; font-size:12px; font-weight:600;"
        )

    def _refresh_start_btn(self, running: bool):
        """根据运行态切换「启动/关闭」按钮：未运行→「▶ 启动应用」（绿、启动）；
        运行中→「强制关闭」（红、终止进程）。槽随状态动态切换，避免误触发。

        与徽章同源（都基于 _is_process_running）。运行中时按钮可点，点一下直接
        taskkill 掉窗口标题含本 app key 的 pythonw 进程（见 _kill_app_by_title），
        比灰着不能点更实用；关掉后下次轮询自动回落到「▶ 启动应用」。
        """
        if not hasattr(self, "start_btn"):
            return
        # 先断开旧槽，避免状态切换后记重触发
        try:
            self.start_btn.clicked.disconnect()
        except Exception:
            pass
        if running:
            self.start_btn.setText("强制关闭")
            self.start_btn.setEnabled(True)
            self.start_btn.setStyleSheet(
                "QPushButton { background-color:#c62828; color:white; border-radius:8px; "
                "font-weight:600; } QPushButton:hover { background-color:#8e0000; }"
            )
            self.start_btn.clicked.connect(self._on_force_stop)
        else:
            self.start_btn.setText("▶  启动应用")
            self.start_btn.setEnabled(True)
            self.start_btn.setStyleSheet(
                "QPushButton { background-color:#2e7d32; color:white; border-radius:8px; "
                "font-weight:600; } QPushButton:hover { background-color:#1b5e20; }"
            )
            self.start_btn.clicked.connect(self.launch_app)

    def _on_force_stop(self):
        """运行中时「强制关闭」按钮回调：二次确认后终止 ok-nte 进程并刷新。"""
        name = self.app.get("name", os.path.basename(
            self.app.get("exe", "")).lower().removesuffix(".exe"))
        ans = QMessageBox.question(
            self.window(), "强制关闭确认",
            f"确定要强制关闭「{name}」吗？\n\n"
            "该操作会直接终止进程，未保存的数据可能丢失，且无法撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,  # 默认聚焦“否”，防误触
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        key = os.path.basename(self.app.get("exe", "")).lower().removesuffix(".exe")
        ok, msg = _kill_app_by_title(key)
        QMessageBox.information(self.window(), "强制关闭", msg)
        # 立即刷新状态（不依赖下次 5 秒轮询）
        self.refresh_data()

    def refresh_update_button(self):
        """根据下拉框选中的版本（或回退到最新比 current 新的）刷新按钮文案/颜色。

        行为：
          - 下拉选中 > current → "更新到 {X}" 橙色
          - 下拉选中 < current → "降级到 {X}" 蓝色
          - 下拉选中 == current（且后台有更新）→ "更新到 {最新newer}" 橙色
            —— 这样下拉在当前版本时也能立刻看到"有新版本可装"提示
          - 下拉选中 == current（且无更新）→ "已是最新" 灰色
          - 下拉无有效选择 + 后台有更新 → "更新到 {最新newer}" 橙色
          - 下拉无有效选择 + 已是最新 → "已是最新" 灰色
        """
        if not hasattr(self, "update_btn"):
            return
        cur = _normalize_tag(self.data.get("current_version", "")) if self.data else ""

        # 1) 预算"最新比 current 新的"——这是兜底目标
        latest_newer = None
        all_v = getattr(self, "_all_versions", None) or []
        if cur and all_v:
            for v in all_v:
                vn = _normalize_tag(v)
                if vn and compare_version(vn, cur) > 0:
                    latest_newer = vn
                    break

        target = None
        label_prefix = "更新到"

        # 2) 看下拉选中的（仅当 != current 时覆盖 latest_newer；选 current 时不覆盖，
        #    这样按钮会回落到 latest_newer，给出"有新版本可装"的提示）
        if hasattr(self, "ver_combo") and hasattr(self, "_version_map"):
            sel_text = self.ver_combo.currentText()
            sel_raw = self._version_map.get(sel_text)
            if sel_raw:
                sel_norm = _normalize_tag(sel_raw)
                if sel_norm and cur:
                    cmp = compare_version(sel_norm, cur)
                    if cmp > 0:
                        target = sel_norm
                        label_prefix = "更新到"
                    elif cmp < 0:
                        target = sel_norm
                        label_prefix = "降级到"
                    # cmp == 0: 不覆盖，target 保持 None → 走 latest_newer 兜底

        # 3) 兜底 latest_newer
        if not target and latest_newer:
            target = latest_newer
            label_prefix = "更新到"

        # 4) 再兜底到 app.json 缓存（_all_versions 还没拉到的过渡期）
        if not target and self.data:
            avail = self.data.get("available_versions", []) or []
            for v in avail:
                vn = _normalize_tag(v)
                if cur and vn and compare_version(vn, cur) > 0:
                    target = vn
                    label_prefix = "更新到"
                    break

        # 缓存 target，供 open_update_dialog 预选用
        self._current_target = target
        self._current_target_label = (
            f"{label_prefix} {target}" if target else None
        )

        if target:
            # 升降级按钮（升级橙色，降级蓝色以示"回退需谨慎"）
            self.update_btn.setText(f"{label_prefix} {target}")
            self.update_btn.setEnabled(True)
            if label_prefix == "降级到":
                self.update_btn.setStyleSheet(
                    "QPushButton { background-color:rgba(33,150,243,0.15); "
                    "color:#64b5f6; border:1px solid #2196f3; border-radius:8px; "
                    "font-weight:600; } "
                    "QPushButton:hover { background-color:rgba(33,150,243,0.28); }"
                )
            else:
                self.update_btn.setStyleSheet(
                    "QPushButton { background-color:rgba(255,152,0,0.15); "
                    "color:#ffb74d; border:1px solid #ff9800; border-radius:8px; "
                    "font-weight:600; } "
                    "QPushButton:hover { background-color:rgba(255,152,0,0.28); }"
                )
            tip = f"当前 {cur}，{label_prefix} {target}"
            # 特别说明：下拉在当前版本但按钮指向更新的情况
            if hasattr(self, "ver_combo") and hasattr(self, "_version_map"):
                sel_text = self.ver_combo.currentText()
                sel_raw = self._version_map.get(sel_text)
                if sel_raw and cur and _normalize_tag(sel_raw) == cur:
                    tip += "（下拉在当前版本，按钮指向最新可用更新）"
            tip += "（下载写入本启动器目录，下载后可一键应用到 working/）"
            self.update_btn.setToolTip(tip)
        else:
            self.update_btn.setText("已是最新")
            self.update_btn.setEnabled(True)  # 仍可点开看版本/说明
            self.update_btn.setStyleSheet(
                "QPushButton { background-color:rgba(255,255,255,0.05); "
                "color:#9a9a9a; border:1px solid #555; border-radius:8px; "
                "font-weight:500; } "
                "QPushButton:hover { background-color:rgba(255,255,255,0.10); "
                "color:#cccccc; }"
            )
            self.update_btn.setToolTip("当前已是最新版本")

    def get_status_text(self):
        """小灰字 status_label 的文案：仅承担"启动过渡"提示，运行态完全交给徽章。

        之前版本小灰字会显示"运行中/未运行"，跟徽章 1:1 复读（信息冗余）。
        现在空字符串：徽章负责总结态（运行中/已安装/未安装/可更新），
        小灰字仅在刚点启动瞬间显示"已发起启动（等待窗口出现）"，
        5 秒后由 launch_app 的 QTimer.singleShot 清空，回到空白。
        """
        return ""

    def _human_update_state(self, state):
        """把原启动器写的 update_state 翻成中文阶段名。"""
        s = (state or "").lower()
        if any(k in s for k in ("check", "detect", "检测")):
            return "检测更新"
        if any(k in s for k in ("download", "下载")):
            return "下载中"
        if any(k in s for k in ("extract", "unzip", "解压", "decompress")):
            return "解压中"
        if any(k in s for k in ("install", "安装")):
            return "安装中"
        if any(k in s for k in ("finish", "done", "完成")):
            return "即将完成"
        return state or "进行中"

    def update_progress_ui(self):
        """根据 app.json 的 update_state 显示更新进度（只读，不写任何文件）。"""
        d = self.data or {}
        state = (d.get("update_state") or "").strip()
        err = d.get("update_error")

        # 空闲：隐藏
        if not state or state.lower() == "idle":
            self.update_bar.setVisible(False)
            self.update_label.setVisible(False)
            return

        # 出错：红字提示，隐藏进度条
        if "error" in state.lower() or "fail" in state.lower() or err:
            self.update_bar.setVisible(False)
            self.update_label.setVisible(True)
            self.update_label.setStyleSheet("color:#ff6b6b; font-size:12px;")
            msg = str(err) if err else state
            self.update_label.setText(f"更新失败：{msg}")
            return

        # 进行中：显示滚动进度条 + 阶段文字（原启动器未提供精确百分比）
        target = d.get("update_target_version") or ""
        label = f"更新中：{self._human_update_state(state)}"
        if target:
            label += f" → {target}"
        self.update_bar.setVisible(True)
        self.update_label.setVisible(True)
        self.update_label.setStyleSheet("color:#ffb74d; font-size:12px;")
        self.update_label.setText(label)

    def on_version_changed(self):
        """下拉框选中版本变化：刷新版本说明 + 刷新更新/降级按钮。"""
        self.load_changelog()
        self.refresh_update_button()

    def _ensure_current_in_versions(self, versions):
        """确保 current 在 versions 列表里（app.json 可能漏写当前版本，导致下拉里看不到）。

        不强制置顶：按版本号（newest first）插入到正确位置，让 current 出现在它本该在的位置。
        用 ver_key 做存在性比较，避免 refs/tags/vX.Y.Z^{} 这种带 ref 前缀的形态被漏判。
        """
        cur = _normalize_tag(self.data.get("current_version", "")) if self.data else ""
        if not cur:
            return versions
        cur_key = ver_key(cur)
        # 已存在（按 ver_key 比较，含 v 前缀/ refs/tags/ 前缀 / ^{} 后缀的形态都能匹配）就不动
        if any(ver_key(_normalize_tag(v)) == cur_key for v in versions):
            return versions
        cur_n = _normalize_tag(cur)
        # current 不在列表里 → 按 ver_key 排序插入到正确位置（newest first）
        result = list(versions)
        inserted = False
        for i, v in enumerate(versions):
            if ver_key(_normalize_tag(v)) < cur_key:
                result.insert(i, cur_n)
                inserted = True
                break
        if not inserted:
            # current 比所有列出的版本都旧（或无可比）→ 追加到末尾
            result.append(cur_n)
        return result

    def populate_versions(self):
        self.ver_combo.blockSignals(True)
        self.ver_combo.clear()
        self._version_map.clear()
        current = _normalize_tag(self.data.get("current_version", ""))
        versions = self._ensure_current_in_versions(
            self.data.get("available_versions", []) or []
        )
        seen = set()
        for v in versions:
            v = _normalize_tag(v)
            if v in seen:
                continue
            seen.add(v)
            display = format_version_display(v, current)
            self._version_map[display] = v
            self.ver_combo.addItem(display)
        # 打开启动器时默认跳到最新版本，让用户一眼看到有没有更新
        if versions:
            self.ver_combo.setCurrentIndex(0)
        self.ver_combo.blockSignals(False)

        # 后台从各游戏本地 git 仓库读取「版本 + 更新说明」（与原启动器同源，
        # 数据完全一致；不再 spawn 原启动器 exe，避免弹出原启动器 GUI）。只读。
        exe = self.app.get("exe", "")
        if exe and os.path.isfile(exe):
            if self._version_fetcher and self._version_fetcher.isRunning():
                self._version_fetcher.requestInterruption()
                self._version_fetcher.wait(1000)
            worker = GitVersionFetcher(exe, parent=self)
            worker.fetched.connect(self._on_versions_fetched)
            worker.failed.connect(self._on_version_fetch_failed)
            worker.start()
            self._version_fetcher = worker
            self._version_fetch_started = True
            # 异步请求刚发出去时，先在 changelog 上提示一下"正在拉取"，避免用户看到缓存说明
            if hasattr(self, "changelog_text") and self.changelog_text.toPlainText().startswith("该版本"):
                self.changelog_text.setPlainText("正在从原启动器拉取版本说明…")
        else:
            # 无 exe（如未安装）时保留缓存列表，changelog 走兜底说明
            pass

    def _on_versions_fetched(self, items):
        """原启动器版本列表（含 update_note）拉取完成：刷新下拉框与说明缓存。"""
        if not items or not hasattr(self, "ver_combo"):
            return
        # items: 原启动器返回的 list of {version, previous_version, update_note}
        notes_list = [it for it in items
                      if isinstance(it, dict) and it.get("version")]
        if not notes_list:
            return
        self._version_notes_list = notes_list
        version_strings = [_normalize_tag(it["version"]) for it in notes_list]
        current = _normalize_tag(self.data.get("current_version", "")) if self.data else ""
        old_text = self.ver_combo.currentText()
        version_strings = self._ensure_current_in_versions(version_strings)

        self.ver_combo.blockSignals(True)
        self.ver_combo.clear()
        self._version_map.clear()
        seen = set()
        for v in version_strings:
            v = _normalize_tag(v)
            if v in seen:
                continue
            seen.add(v)
            display = format_version_display(v, current)
            self._version_map[display] = v
            self.ver_combo.addItem(display)

        # 尽量保留用户已选；若已选项不存在则默认跳到最新版
        idx = self.ver_combo.findText(old_text)
        self.ver_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.ver_combo.blockSignals(False)
        self._all_versions = version_strings  # 缓存完整列表，供更新对话框/按钮使用
        self.load_changelog()
        self.refresh_update_button()

    def _on_version_fetch_failed(self, error_msg):
        """PyAppify exe 拉版本列表失败：把真实错误显示出来（之前是静默吞掉），便于排查。

        仍然保留 app.json 缓存的版本列表 + 缓存的 update_note 作为兜底，不让界面塌掉。
        """
        # 只在 changelog 还没被用户看到真实数据时，才覆盖提示
        current_text = self.changelog_text.toPlainText() if hasattr(self, "changelog_text") else ""
        if current_text.startswith("正在从原启动器拉取版本说明"):
            self.changelog_text.setPlainText(
                "⚠ 原启动器拉取失败，已回退到 app.json 缓存说明：\n"
                f"  错误：{error_msg}\n\n"
            )
        # 把错误记到启动器日志（terminal / log file），方便事后排查
        try:
            print(f"[GitVersionFetcher] 失败: {error_msg}")
        except Exception:
            pass

    def _show_cached_note(self):
        """在线/原启动器拿不到说明时，回退 app.json 缓存的 update_note。"""
        notes = self.data.get("update_note", []) if self.data else []
        if notes:
            self.changelog_text.setPlainText(
                "在线说明获取失败，已回退到最新版缓存说明：\n\n" +
                "\n".join(f"• {n}" for n in notes)
            )
            return
        self.changelog_text.setPlainText("该版本暂不支持在线显示更新说明。")

    def load_changelog(self):
        """显示 current → 目标版本 的更新说明，数据与原启动器同源（PyAppify 版本列表）。"""
        version = self._version_map.get(self.ver_combo.currentText())
        current = _normalize_tag(self.data.get("current_version", "")) if self.data else ""

        if not version:
            self.changelog_text.clear()
            self.changelog_text.setPlaceholderText("选择目标版本后显示更新内容...")
            return

        notes_list = getattr(self, "_version_notes_list", None)
        if notes_list:
            notes = calculate_update_notes(notes_list, current, version)
            if notes:
                self.changelog_text.setPlainText("\n".join(f"• {n}" for n in notes))
                return
            # 拉到了版本列表但拼不出 notes → 大概率是字段名/嵌套结构和我们的假设不一致。
            # 把 PyAppify 真实返回的前 2 条 sample 出来给用户看，方便排查（不要静默兜底）。
            import json as _dj
            sample = _dj.dumps(notes_list[:2], ensure_ascii=False, indent=1)[:1200]
            self.changelog_text.setPlainText(
                "⚠ 原启动器返回了版本列表但未能解析 update_note。\n"
                "调试信息（PyAppify 真实返回的前 2 条原始结构），"
                "请把这段截图给开发者：\n\n" + sample
            )
            return

        # 兜底：版本列表未就绪或网络不通时，回退到 app.json 缓存的 update_note
        self._show_cached_note()

    # ===== 动作：启动（直接跑游戏助手本体，这是启动器的本职） =====
    def launch_app(self):
        app = self.app
        main_script = (self.profile or {}).get("main_script", "main.py")
        admin = bool((self.profile or {}).get("admin", False))
        main_path = os.path.join(app["working"], main_script)
        pythonw = app["pythonw"]

        if not os.path.isfile(main_path):
            QMessageBox.critical(
                self.window(), "启动失败",
                f"找不到助手主程序：\n{main_path}\n\n请重新打开原版启动器修复安装（聚合启动器仅负责启动）。",
            )
            return
        if not os.path.isfile(pythonw):
            QMessageBox.critical(
                self.window(), "启动失败",
                f"找不到 Python：\n{pythonw}\n\n请重新打开原版启动器修复安装（聚合启动器仅负责启动）。",
            )
            return

        # CWD=working：保证相对 import 与日志落点正确
        ok = run_exe(
            self.window(), pythonw,
            args=[main_path], cwd=app["working"],
            need_admin=admin, show_errors=True,
        )
        if ok:
            self.status_label.setText("已发起启动（等待窗口出现）")
            # 5 秒后清空，避免与徽章长期 1:1 复读"运行中"造成信息冗余
            QTimer.singleShot(5000, lambda: self.status_label.setText(""))

    # ===== 动作：安装（从 GitHub Releases 下 win32.zip 就地解压，免管理员、不写注册表） =====
    def _ilog(self, msg):
        """UI 侧安装日志，与 InstallWorker._log 写到同一个 logs/install-<key>.log。"""
        try:
            _d = os.path.join(LAUNCHER_DIR, "logs")
            os.makedirs(_d, exist_ok=True)
            _p = os.path.join(_d, f"install-{self.app.get('key','?')}.log")
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(_p, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] [UI] {msg}\n")
        except Exception:
            pass

    def install_app(self):
        if getattr(self, "_installed", False):
            return
        # 防重复触发：已有安装任务在跑则直接返回。
        # 没有这个守卫,连点两次「安装」会启动两个 InstallWorker 并发下同一个文件,
        # 表现就是用户截图里的「重复下载、关不掉」(旧 bug 修了 _probe 之后剩下的)。
        existing = getattr(self, "_install_worker", None)
        if existing is not None and existing.isRunning():
            return
        key = self.app.get("key", "")
        if key not in InstallWorker.REPOS:
            QMessageBox.information(
                self.window(), "安装",
                f"「{self.app.get('display','?')}」暂未配置一键安装（key={key}）。\n"
                "请到 ok-script.com 手动下载，或重新打开原版启动器安装。",
            )
            return
        install_root = self.app.get("install_root", "") or ""
        if not install_root:
            QMessageBox.warning(
                self.window(), "安装",
                "找不到 install_root，请检查 config.json 的 install_root 字段。"
            )
            return
        # install_dir 取「config 里 exe 字段所在目录」= PyAppify install_path
        # （不同游戏深度不同：ok-nte 是 install_root/ok-nte/ok-nte/ 双层，ok-ww/end-field 单层）
        install_dir = os.path.dirname(self.app.get("exe", "") or "") or ""
        if not install_dir:
            QMessageBox.warning(self.window(), "安装",
                f"无法从 config 推导安装目录（exe={self.app.get('exe','')}）。")
            return
        # 读 MirrorChyan CDK 与万载云反代域名，决定下载源提示
        try:
            _cfg = json.load(open(os.path.join(LAUNCHER_DIR, "config.json"), encoding="utf-8"))
            cdk = (_cfg.get("mirrorchyan_cdk", "") or "").strip()
            wz_proxy = (_cfg.get("wanzaiyun_proxy", "") or "").strip()
        except Exception:
            cdk = ""
            wz_proxy = ""
        src_hint = (
            "· 来源：MirrorChyan 国内快源（已配 CDK）> 万载云反代 > cnb/GitHub"
            if cdk
            else "· 来源：万载云反代（免 key 国内直连）> cnb（仅鸣潮）/GitHub 直链"
        )
        src_extra = "" if cdk else "\n  右上「⚙ 设置」可填 MirrorChyan CDK 进一步加速"
        resp = QMessageBox.question(
            self.window(), "一键安装",
            f"将从官方 release 下载并安装「{self.app['display']}」到：\n"
            f"  {install_dir}\n\n"
            f"{src_hint}{src_extra}\n"
            "· 优先下载完整安装包（解压即可直开本体，跳过 PyAppify host 小窗）\n"
            "· 若官方仅提供 host 包，则解压后需再点一次「启动 host 初始化」\n\n"
            "确认下载安装？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return

        self._ilog(f"用户确认安装：key={key}, install_dir={install_dir}, cdk={'有' if cdk else '无'}")
        # ===== 进入安装态：禁用所有触发按钮，激活内嵌进度区 =====
        # 同时禁用 install_btn + rescan_btn + 半装卡片的两个按钮,防连点
        for _b_name in ("install_btn", "rescan_btn"):
            _b = getattr(self, _b_name, None)
            if _b is not None:
                _b.setEnabled(False)
        # 半装状态下还有 full_btn/host_btn（局部变量），重建时已变引用,这里不强求
        self.install_progress.setValue(0)
        self.install_progress.setVisible(True)
        self.install_status.setText(f"正在准备「{self.app['display']}」一键安装…")
        self.install_status.setVisible(True)
        self.install_cancel_btn.setVisible(True)

        self._install_worker = InstallWorker(key, install_root, install_dir, cdk=cdk, proxy=wz_proxy, app=self.app, parent=self)
        self._ilog("InstallWorker 已创建并即将 start()")

        def _on_progress(pct, text):
            if pct >= 0:
                self.install_progress.setValue(pct)
            if text:
                self.install_status.setText(text)
            QApplication.processEvents()

        def _on_done(install_dir_done, tag):
            self.install_progress.setValue(100)
            self.install_status.setText(f"解压完成：{tag or ''}")
            QApplication.processEvents()
            self._hide_install_progress_ui()
            # 判断是整包直装还是 host 引导包：working/main.py 是否存在
            app_json_dir = os.path.dirname(self.app.get("app_json", "") or "")
            working_main = os.path.join(app_json_dir, "working", "main.py") if app_json_dir else ""
            if os.path.isfile(working_main):
                QMessageBox.information(
                    self.window(), "安装完成",
                    f"「{self.app['display']}」完整版已装好：\n{install_dir_done}\n\n"
                    "✅ 完整运行环境已就位。直接点卡片上的「▶ 启动应用」\n"
                    "即可打开助手主窗口（已跳过 PyAppify host 小窗）。",
                )
            else:
                QMessageBox.information(
                    self.window(), "解压完成",
                    f"「{self.app['display']}」host 引导包已解压：\n{install_dir_done}\n\n"
                    "这是 PyAppify host 引导包，完整运行时（Python venv、working/、cache）\n"
                    "需要 host 第一次启动时自动拉取（约几百 MB，按网络耗时数分钟）。\n\n"
                    "请点卡片上的「▶ 启动 host 初始化」按钮，让它跑一次自动下完所有组件。\n"
                    "初始化完成后点「🔄 刷新」即可识别为已安装。",
                )
            self._install_worker = None
            self._ilog(f"安装成功：{install_dir_done}, tag={tag}")
            self.rebuild_body()

        def _on_failed(msg):
            self._hide_install_progress_ui()
            self._install_worker = None
            self._ilog(f"安装失败：{msg}")
            # 按当前状态给提示：首次装 vs 已装兜底 区别开，不再说"都还没装怎么点原版"
            already = (getattr(self, "install_btn", None) and self.install_btn.text() != "安装") or getattr(self, "_host_ready", False)
            extra = (
                "可重新打开原版启动器完成更新（聚合启动器仅负责启动 / 整包安装）。"
                if already
                else "请检查网络后重试，或到 ok-script.com 手动下载安装包。"
            )
            QMessageBox.critical(
                self.window(), "安装失败",
                f"下载/解压失败：\n\n{msg}\n\n{extra}",
            )
            self.rebuild_body()

        self._install_worker.progress.connect(_on_progress)
        self._install_worker.done.connect(_on_done)
        self._install_worker.failed.connect(_on_failed)
        self._install_worker.start()

    def _on_install_cancel_clicked(self):
        """取消按钮被点：通知 worker 停、清 worker 引用、隐藏进度 UI。
        关键修复：旧版 _on_cancel 没把 self._install_worker = None,导致下次
        点 install_btn 时守卫看到旧 worker 还活着就 return,用户感觉按钮坏了。
        同时增加 wait(800) 强等线程退出,守卫不会被"isRunning()==True 但马上要退"的状态骗到。
        """
        w = getattr(self, "_install_worker", None)
        if w is None:
            self._ilog("取消点击：无运行中 worker，直接隐藏进度 UI")
            self._hide_install_progress_ui()
            return
        self._ilog("用户点击取消，通知 worker 停止")
        try:
            w.cancel()
        except Exception:
            pass
        try:
            # 等 worker 在 cancel 标志传到下载循环后自然退出(最多 800ms),
            # 不强 terminate,避免半截 zip 文件句柄泄漏
            if w.isRunning():
                w.wait(800)
        except Exception:
            pass
        try:
            w.progress.disconnect()
            w.done.disconnect()
            w.failed.disconnect()
        except Exception:
            pass
        self._install_worker = None
        self._ilog("取消完成：worker 引用已清空，进度 UI 已隐藏")
        self._hide_install_progress_ui()
        self.rebuild_body()

    def _hide_install_progress_ui(self):
        """隐藏安装进度三件套 + 恢复原按钮可用态（无论 done/failed/cancel 都走它）。"""
        try:
            self.install_progress.setVisible(False)
            self.install_progress.setValue(0)
            self.install_status.setVisible(False)
            self.install_status.setText("")
            self.install_cancel_btn.setVisible(False)
        except Exception:
            pass
        for _b_name in ("install_btn", "rescan_btn"):
            _b = getattr(self, _b_name, None)
            if _b is not None:
                _b.setEnabled(True)

    # ===== 动作：打开原版管理窗口（唯一会改配置的入口，由原启动器自己处理） =====
    def open_manager(self):
        # 优先用实际找到的 host exe（host 名可能跟 config 不一致，如 ok-end-field 解出来叫 ok-ef.exe）
        target = getattr(self, "_host_actual", "") or self.app.get("exe", "")
        run_exe(self.window(), target, [], need_admin=True)

    # ===== 动作：窗口内更新（下载到本启动器目录，真实进度；应用交回原启动器） =====
    def open_update_dialog(self):
        # 先刷新按钮状态，确保 _current_target 是最新的
        self.refresh_update_button()
        avail = self.data.get("available_versions", []) or []
        all_versions = getattr(self, "_all_versions", None) or avail
        versions = self._ensure_current_in_versions(
            all_versions if all_versions else avail
        )
        cur = _normalize_tag(self.data.get("current_version", ""))

        if not versions:
            QMessageBox.information(
                self.window(), "更新",
                f"「{self.app['display']}」暂无可用版本信息，请稍后再试。"
            )
            return

        # 用户在主窗口下拉里选中的版本
        selected_raw = None
        if hasattr(self, "ver_combo") and hasattr(self, "_version_map"):
            selected_raw = self._version_map.get(self.ver_combo.currentText())

        # preselected 优先级：按钮的目标 > 用户下拉选中 > 当前
        # 这样下拉在 current 时点击按钮，对话框也会打开到 latest_newer（而非 current）
        preselected = (
            getattr(self, "_current_target", None)
            or selected_raw
            or cur
            or None
        )

        git_url = (self.profile or {}).get("git_url", "")
        dlg = UpdateDialog(
            self, self.app, cur, versions, git_url, preselected=preselected
        )
        dlg.exec()

    # ===== 动作：卸载（移入回收站，可撤销；不动其他 app 目录） =====
    def uninstall_app(self):
        app_dir = os.path.dirname(self.app["exe"])
        if not os.path.isdir(app_dir):
            QMessageBox.information(
                self.window(), "卸载",
                f"未找到「{self.app['display']}」安装目录：\n{app_dir}"
            )
            return

        # 本启动器如果位于该 app 目录内，无法卸载自身
        launcher_dir = os.path.normcase(os.path.abspath(os.path.dirname(__file__)))
        app_dir_norm = os.path.normcase(os.path.abspath(app_dir))
        if launcher_dir == app_dir_norm or launcher_dir.startswith(app_dir_norm + os.sep):
            QMessageBox.warning(
                self.window(), "无法卸载",
                f"本启动器位于「{self.app['display']}」的安装目录内，无法在此窗口内卸载自身。\n\n"
                f"如需卸载，请关闭本启动器后手动删除目录：\n{app_dir}"
            )
            return

        # 正在运行则先让用户关闭。
        # **必须用实时进程检测 (_is_process_running)，不能信 self.data["running"]**：
        # app.json.running 是原启动器启动时写、关闭时没清的陈旧字段（注释见
        # _is_process_running），进程死透后还常驻 true，会让卸载按钮永远点不动。
        # refresh_badge 已接实时检测兜底，uninstall_app 当时漏接了。
        if self._is_process_running():
            QMessageBox.warning(
                self.window(), "无法卸载",
                f"「{self.app['display']}」正在运行，请先关闭后再卸载。"
            )
            return

        # === 收集所有与本游戏相关的"该清"和"该留"路径（带体积）===
        # 之前只把 app_dir 扔回收站，repos/<key>/ 镜像 (~60MB)、install 日志、
        # versions 缓存全留在 D:\OKApps\launcher\ 下，充其量算"半卸载"。
        key = self.app.get("key", "") or ""
        install_root = self.app.get("install_root", "") or ""

        # 必清（不管选哪档都会移入回收站）
        recycle_items = []    # [(path, label, size_bytes)]
        # 可选清：下载缓存，默认保留（重装时自动复用省流量），
        # 选「彻底清除（含下载缓存）」时才并入 recycle_items 一起带走。
        optional_items = []

        # 1) 安装目录（必清）
        recycle_items.append((app_dir, "安装目录", _sum_size(app_dir)))

        # 2) 启动器侧 git 镜像 repos/<key>/
        if key:
            repos_dir = os.path.join(LAUNCHER_DIR, "repos", key)
            if os.path.isdir(repos_dir):
                recycle_items.append((repos_dir, "启动器版本镜像 (git)", _sum_size(repos_dir)))

        # 3) 启动器侧日志 / 版本缓存（小文件，单独列出来用户更清楚）
        if key:
            logs_dir = os.path.join(LAUNCHER_DIR, "logs")
            for fn in (f"install-{key}.log", f"versions-{key}.json",
                       f"versions-{key}.json.bak"):
                fp = os.path.join(logs_dir, fn)
                if os.path.isfile(fp):
                    recycle_items.append((fp, "启动器日志/缓存", os.path.getsize(fp)))

        # 4) 下载缓存 _dl_<key>/ —— 可选清除
        if key and install_root:
            dl_dir = os.path.join(install_root, f"_dl_{key}")
            if os.path.isdir(dl_dir):
                optional_items.append((dl_dir, "下载缓存（已下好的安装包）", _sum_size(dl_dir)))

        # === 构造确认对话框：体积透明 + 三档按钮 ===
        lines = [f"将把「{self.app['display']}」以下内容移入回收站：", ""]
        for p, label, sz in recycle_items:
            lines.append(f"  · {label}（{_human_size(sz)}）")
            lines.append(f"      {p}")
        if optional_items:
            lines.append("")
            lines.append("以下是下载缓存，默认保留（重装时自动复用）；")
            lines.append("想连它一起清掉就点「彻底清除（含下载缓存）」：")
            for p, label, sz in optional_items:
                lines.append(f"  · {label}（{_human_size(sz)}）")
                lines.append(f"      {p}")

        box = QMessageBox(self.window())
        box.setWindowTitle("确认卸载")
        box.setText("\n".join(lines))
        box.setIcon(QMessageBox.Warning)
        if optional_items:
            btn_soft = box.addButton("移入回收站（保留缓存）", QMessageBox.AcceptRole)
            btn_hard = box.addButton("彻底清除（含下载缓存）", QMessageBox.DestructiveRole)
        else:
            # 没有下载缓存可清，就只给一档
            btn_soft = box.addButton("移入回收站", QMessageBox.AcceptRole)
            btn_hard = None
        btn_cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(btn_cancel)
        box.exec()

        clicked = box.clickedButton()
        if clicked is None or clicked is btn_cancel:
            return

        # 选了「彻底清除」→ 把下载缓存并进清理清单
        thorough = (btn_hard is not None and clicked is btn_hard)
        if thorough:
            recycle_items = recycle_items + optional_items
            optional_items = []   # 已被清掉，汇总里不再显示"保留"

        # === 执行清理（逐项独立判断，失败的列出来让用户处理）===
        ok_list = []   # 移入回收站成功的
        fail_list = [] # 失败的 [(path, label, msg)]
        for p, label, _sz in recycle_items:
            ok, msg = send_to_trash(p)
            if ok:
                ok_list.append((p, label))
            else:
                fail_list.append((p, label, msg))

        if not fail_list:
            summary = f"已把「{self.app['display']}」以下内容移入回收站：\n\n"
            for p, label in ok_list:
                summary += f"  · {label}\n        {p}\n"
            if optional_items:
                summary += "\n保留（未清除，重装时自动复用）：\n"
                for p, label, _sz in optional_items:
                    summary += f"  · {label}\n        {p}\n"
            summary += "\n回收站里可还原。"
            QMessageBox.information(self.window(),
                                    "彻底清除完成" if thorough else "卸载完成",
                                    summary)
        else:
            summary = (f"部分完成：{len(ok_list)}/{len(recycle_items)} 项已移入回收站，"
                       f"{len(fail_list)} 项失败。\n\n")
            if ok_list:
                summary += "已清理：\n"
                for p, label in ok_list:
                    summary += f"  · {label}\n        {p}\n"
            summary += "\n失败（文件可能被占用）：\n"
            for p, label, msg in fail_list:
                summary += f"  · {label}: {msg}\n        {p}\n"
            summary += "\n请关闭相关程序后，对失败项可再点一次「卸载此助手」重试。"
            QMessageBox.warning(self.window(), "部分完成", summary)

        self.rebuild_body()


class UpdateDialog(QDialog):
    """窗口内「更新到所选版本」对话框：点开始后自动下载并应用到原启动器 working/。

    流程：点「开始更新」→ 一次确认 → 后台下载镜像到 launcher/repos/<key>（作缓存，
    加速下次增量 fetch）→ 自动终止运行进程 → 同步代码到 app['working']（覆盖代码、
    保留缓存/日志/配置/数据库等运行数据，不备份、不占额外空间）→ 写回 app.json 当前版本。
    失败时可点「打开原版管理窗口」交回原启动器处理。
    写的是官方 app 的本地安装目录，属于官方仓库范畴，可写（用户已授权）。
    """

    def __init__(self, parent, app, current, versions, git_url, preselected=None):
        super().__init__(parent)
        self.app = app
        self.git_url = git_url
        self.versions = versions  # 从新到旧
        self.current = current
        self._worker = None

        self.setWindowTitle(f"更新 {app['display']}")
        self.setMinimumWidth(480)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)

        # 目标版本选择（与主卡片 ComboBox 用同样的格式化：v3.5.28 正式版（当前））
        row = QHBoxLayout()
        row.addWidget(CaptionLabel("目标版本"))
        self.combo = ComboBox()
        self.combo.setMinimumWidth(240)
        cur_n = _normalize_tag(self.current)
        for i, ver in enumerate(versions):
            label = format_version_display(ver, self.current)
            if i == 0:
                # 最新项：把"（升级）"换成"（最新）"；若它就是当前则合写"（当前·最新）"
                if "（当前）" in label:
                    label = label.replace("（当前）", "（当前·最新）")
                else:
                    label = label.replace("（升级）", "（最新）")
            self.combo.addItem(label)
        # 预选用户在主窗口下拉里选的版本（若有）；否则默认最新
        sel_idx = 0
        if preselected:
            target_n = _normalize_tag(preselected)
            for i, ver in enumerate(versions):
                if _normalize_tag(ver) == target_n:
                    sel_idx = i
                    break
        self.combo.setCurrentIndex(sel_idx)
        row.addWidget(self.combo, stretch=1)
        v.addLayout(row)

        # 真实进度条
        self.bar = QProgressBar()
        self.bar.setFixedHeight(16)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        v.addWidget(self.bar)

        self.status = CaptionLabel(
            "点击「开始更新」直接下载并更新到原启动器源程序目录 working/：\n"
            "覆盖代码文件，保留缓存 / 日志 / 配置 / 数据库等运行数据；不备份、不占额外空间。"
        )
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        # 按钮行：第一行 = 下载/关闭；第二行 = 下载完成后的两种应用方式
        btn_row1 = QHBoxLayout()
        self.start_btn = PushButton("开始更新")
        self.start_btn.setFixedHeight(38)
        self.start_btn.clicked.connect(self.start_update)
        btn_row1.addWidget(self.start_btn)

        self.close_btn = PushButton("关闭")
        self.close_btn.setFixedHeight(38)
        self.close_btn.clicked.connect(self.reject)
        btn_row1.addWidget(self.close_btn)
        v.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self.apply_btn = PushButton("打开原版管理窗口")
        self.apply_btn.setFixedHeight(38)
        self.apply_btn.setEnabled(False)
        self.apply_btn.setToolTip(
            "交回原启动器（仅在下载/应用失败时使用，正常情况下已自动更新到 working/）。"
        )
        self.apply_btn.clicked.connect(self.open_manager)
        btn_row2.addWidget(self.apply_btn)
        v.addLayout(btn_row2)

    def start_update(self):
        target = _normalize_tag(self.versions[self.combo.currentIndex()])
        working = self.app.get("working", "")
        # 单次确认（防误点；点开始即自动下载 + 终止进程 + 覆盖 working/ 代码 + 保留运行数据）
        resp = QMessageBox.question(
            self,
            "确认更新到原启动器",
            f"将下载 {target} 并直接更新到原启动器源程序目录：\n{working}\n\n"
            "· 覆盖代码文件，保留缓存 / 日志 / 配置 / 数据库等运行数据\n"
            "· 应用若正在运行会先被终止\n"
            "· 直接覆盖、不产生备份（不占额外空间）\n\n"
            "确认继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        self.start_btn.setEnabled(False)
        self.combo.setEnabled(False)
        self.status.setText(f"正在下载 {target}…")
        self.bar.setValue(0)
        self._worker = MirrorUpdater(self.app["key"], self.git_url, target, parent=self)
        self._worker.progress.connect(self.on_progress)
        self._worker.done.connect(self.on_done)
        self._worker.failed.connect(self.on_failed)
        self._worker.start()

    def on_progress(self, pct, text):
        if pct >= 0:
            self.bar.setValue(pct)
            self.status.setText(
                (text[:80] if text else "") or f"下载中… {pct}%"
            )
        else:
            self.status.setText(text or "下载中…")

    def on_done(self, local_dir):
        target = _normalize_tag(self.versions[self.combo.currentIndex()])
        self.bar.setValue(100)
        self.status.setText(
            f"下载完成，正在更新到原启动器 working/（{target}）…"
        )
        # 下载完成自动应用：杀进程 → 同步代码 → 写 app.json（用户无需再点按钮）
        self._apply_worker = ApplyWorker(self.app, target, local_dir, parent=self)
        self._apply_worker.progress.connect(self.status.setText)
        self._apply_worker.done.connect(self.on_apply_done)
        self._apply_worker.start()

    def on_failed(self, msg):
        self.bar.setValue(0)
        self.status.setText(
            f"下载失败：{msg}\n\n可改点「打开原版管理窗口」由原启动器直接更新。"
        )
        self.start_btn.setEnabled(True)
        self.combo.setEnabled(True)

    # 注：原「应用到 working 目录」按钮 + apply_direct 方法已删除，
    # 下载完成后由 on_done 自动调用 ApplyWorker 完成 kill + 同步 + 写 current_version。

    def on_apply_done(self, ok, msg):
        if ok:
            self.bar.setValue(100)
            self.status.setText(
                f"{msg}\n\n运行数据已保留。可关闭本窗口，"
                "再从主窗口点「启动应用」运行新版本。"
            )
            self.apply_btn.setEnabled(True)  # 已自动应用，可选去原版窗口看一眼
        else:
            self.status.setText(
                f"应用失败：{msg}\n\n可改点「打开原版管理窗口」由原启动器处理。"
            )
            self.apply_btn.setEnabled(True)
            self.start_btn.setEnabled(True)
            self.combo.setEnabled(True)

    def open_manager(self):
        run_exe(self, self.app["exe"], [], need_admin=True)
        self.accept()


class Launcher(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OK 游戏助手")
        self.setMinimumSize(1020, 640)
        self.resize(1100, 700)
        # 窗口图标用「OK 游戏助手」通用图标，而不是某个具体游戏图标
        self.setWindowIcon(QIcon(os.path.join(ASSETS_DIR, "ok-script-app.png")))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部标题栏：左侧标题居中拉伸，右侧紧凑"⚙ 设置"按钮（填 MirrorChyan CDK）
        title_bar = QWidget()
        title_bar.setFixedHeight(56)
        title_bar.setStyleSheet("background-color:#2b2b32;")
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(20, 0, 16, 0)
        tb_layout.setSpacing(12)
        title_label = StrongBodyLabel("OK 游戏助手")
        title_label.setStyleSheet("color:#ffffff; font-size:20px; font-weight:700; background:transparent;")
        title_label.setAlignment(Qt.AlignCenter)
        tb_layout.addWidget(title_label, stretch=1)
        self.settings_btn = PushButton("⚙ 设置")
        self.settings_btn.setFixedHeight(32)
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setStyleSheet(
            "QPushButton { background-color:rgba(255,255,255,0.10); color:#ffffff;"
            " border-radius:6px; padding:0 14px; font-size:13px; }"
            "QPushButton:hover { background-color:rgba(255,255,255,0.18); }"
        )
        self.settings_btn.setToolTip("配置 MirrorChyan CDK（可到 mirrorchyan.com 申请）")
        self.settings_btn.clicked.connect(self.open_settings)
        tb_layout.addWidget(self.settings_btn)
        # 🔍 扫描游戏：显卡驱动式，扫本机游戏本体，提示「装了游戏但没装助手」的卡片
        self.scan_btn = PushButton("🔍 扫描游戏")
        self.scan_btn.setFixedHeight(32)
        self.scan_btn.setCursor(Qt.PointingHandCursor)
        self.scan_btn.setStyleSheet(
            "QPushButton { background-color:rgba(255,255,255,0.10); color:#ffffff;"
            " border-radius:6px; padding:0 14px; font-size:13px; }"
            "QPushButton:hover { background-color:rgba(255,255,255,0.18); }"
        )
        self.scan_btn.setToolTip("扫描本机已安装的游戏本体（注册表 + TapTap/WeGame 渠道库），"
                                 "检测到游戏但未装助手时会在卡片上提示")
        self.scan_btn.clicked.connect(self.run_game_scan)
        tb_layout.addWidget(self.scan_btn)
        root.addWidget(title_bar)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(36, 20, 36, 20)
        cl.setSpacing(16)

        hint = CaptionLabel(
            "游戏库：点「▶ 启动应用」直接打开助手主窗口；有新版时点「更新到 vX.Y.Z」"
            "可在本窗口内下载（显示真实进度，仅写入本启动器目录）。\n"
            "正式切换版本 / 系统设置仍请点「原版管理窗口」，由它来完成。"
        )
        hint.setAlignment(Qt.AlignCenter)
        cl.addWidget(hint)

        # 单列竖排：QGridLayout 同行的卡片会强制等高，会把左侧想撑开的 changelog 压住；
        # 改成单列后每张卡片独占一行，宽度由卡内 changelog 自由决定，整体窗口滚动即可。
        card_layout = QVBoxLayout()
        card_layout.setSpacing(20)
        card_layout.setContentsMargins(0, 0, 0, 0)
        self.cards = {}  # key -> AppCard（游戏本体扫描结果回调要用）
        for app in APPS:
            card = AppCard(app)
            self.cards[app.get("key", "")] = card
            card_layout.addWidget(card)

        card_container = QWidget()
        card_container.setLayout(card_layout)
        card_container.setMaximumWidth(760)

        card_wrapper = QWidget()
        card_wrapper_layout = QHBoxLayout(card_wrapper)
        card_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        card_wrapper_layout.addStretch(1)
        card_wrapper_layout.addWidget(card_container)
        card_wrapper_layout.addStretch(1)
        cl.addWidget(card_wrapper)
        cl.addStretch(1)

        # 内容区域整体限制最大宽度并居中，避免顶栏提示和卡片在超宽窗口下被拉得太散
        content.setMaximumWidth(1040)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # 用 wrapper 让 content 在 scroll area 内水平居中
        wrapper = QWidget()
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addStretch(1)
        wrapper_layout.addWidget(content)
        wrapper_layout.addStretch(1)
        scroll.setWidget(wrapper)
        root.addWidget(scroll)

        # 启动稍歇后自动扫一次本机游戏本体（后台线程，不卡 UI）
        self._scan_worker = None
        self._scan_prompted = False
        QTimer.singleShot(1200, self.run_game_scan)

    # ===== 本地游戏本体扫描（显卡驱动式「检测到游戏 → 引导装助手」） =====
    def run_game_scan(self):
        """手动点「🔍 扫描游戏」或启动 1.2s 后自动触发。只扫尚未装好助手的 key。"""
        if getattr(self, "_scan_worker", None) is not None and self._scan_worker.isRunning():
            return
        keys = [k for k, c in self.cards.items()
                if not (getattr(c, "_installed", False) or getattr(c, "_host_ready", False))]
        if not keys:
            return
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("🔍 扫描中…")
        self._scan_worker = GameScanWorker(keys)
        self._scan_worker.done.connect(self._on_game_scan_done)
        self._scan_worker.start()

    def _on_game_scan_done(self, found):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("🔍 扫描游戏")
        self._scan_worker = None
        if not found:
            return
        lines = []
        for key, exe in found.items():
            card = self.cards.get(key)
            if card is None:
                continue
            already = bool(getattr(card, "game_found_path", ""))
            card.set_game_found(exe)  # 内部已装态会忽略
            if getattr(card, "game_found_path", "") == exe and not already:
                sig = GAME_SIGNATURES.get(key, {})
                lines.append(f"· 《{sig.get('display') or key}》：{exe}\n  → 可在其卡片上点「安装」装上助手")
        # 首次发现时弹一次汇总（之后常驻卡片提示，不再打扰）
        if lines and not self._scan_prompted:
            self._scan_prompted = True
            QMessageBox.information(
                self, "检测到本机游戏",
                "扫描到以下游戏本体，但对应的助手还没装：\n\n"
                + "\n".join(lines)
                + "\n\n（提示会常驻在对应卡片上，装好后自动消失）"
            )

    def open_settings(self):
        """弹一个紧凑的输入框，让用户填/改/清空 MirrorChyan CDK，写回 config.json。"""
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        cfg_path = os.path.join(LAUNCHER_DIR, "config.json")
        cur = ""
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cur = ((json.load(f).get("mirrorchyan_cdk", "")) or "").strip()
        except Exception:
            pass
        status = "已启用 ✓（当前已配置 MirrorChyan CDK，国内最快）" if cur else "未启用（当前走 cnb.cool/GitHub，国内可能偏慢）"
        text, ok = QInputDialog.getText(
            self, "MirrorChyan CDK 设置",
            f"当前：{status}\n\n"
            "申请 CDK：到 https://mirrorchyan.com 登录后「我的 CDK」处获取\n"
            "留空提交 = 清空（不启用 MirrorChyan，走 cnb.cool/GitHub 回退）\n\n"
            "CDK：",
            QLineEdit.Normal, cur,
        )
        if not ok:
            return
        new_cdk = (text or "").strip()
        # 写回 config.json，保留其他字段
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "设置失败", f"读 config.json 失败：{e}")
            return
        cfg["mirrorchyan_cdk"] = new_cdk
        # 第二项：万载云反代域名（空=用内置 github.top-host.top）
        try:
            cur_wz = (cfg.get("wanzaiyun_proxy", "") or "").strip()
        except Exception:
            cur_wz = ""
        wz_text, ok2 = QInputDialog.getText(
            self, "万载云反代域名",
            f"当前：{cur_wz or '（默认）https://github.top-host.top/'}\n\n"
            "万载云 GitHub 反代域名，免登录免 key、覆盖全部游戏。\n"
            "主域名失效时可改成其它可用节点；留空 = 用内置默认域名。\n\n"
            "反代域名（需以 https:// 结尾，含末尾斜杠）：",
            QLineEdit.Normal, cur_wz,
        )
        if not ok2:
            return
        new_wz = (wz_text or "").strip().rstrip("/") + ("/" if wz_text.strip() else "")
        cfg["wanzaiyun_proxy"] = new_wz
        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "设置失败", f"写 config.json 失败：{e}")
            return
        QMessageBox.information(
            self, "已保存",
            ("MirrorChyan CDK 已配置。一键安装/更新将优先走国内最快加速源。" if new_cdk
             else "MirrorChyan CDK 已清空。安装/更新将走万载云反代/cnb.cool/GitHub 回退。")
            + ("\n万载云反代域名已更新。" if new_wz else "\n万载云反代域名已重置为默认。")
        )


def _is_admin():
    """检测当前进程是否以管理员身份运行。"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _relaunch_as_admin():
    """以管理员身份重启本启动器自身（弹 UAC 一次），原进程退出。"""
    exe = sys.executable  # 当前 pythonw.exe
    script = os.path.abspath(__file__)
    cwd = os.path.dirname(script)
    return runas(exe, [script], cwd)


def main():
    # 默认不在启动时强制提权：否则每次双击都弹一次 UAC，且提权副本偶发起不来
    # 会表现为“双击没反应/打不开”。需要管理员的操作（如强制关闭）改为按需提权。
    # 设置环境变量 LAUNCHER_ELEVATE=1 可恢复“启动即管理员”的旧行为。
    if os.environ.get("LAUNCHER_ELEVATE") and not _is_admin():
        try:
            _relaunch_as_admin()
        except Exception:
            pass
        sys.exit(0)

    def show_error(etype, value, tb):
        msg = "".join(traceback.format_exception(etype, value, tb))
        try:
            QMessageBox.critical(None, "启动器出错", msg)
        except Exception:
            pass
        sys.__excepthook__(etype, value, tb)

    sys.excepthook = show_error

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    setTheme(Theme.AUTO)
    win = Launcher()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
