# OK 游戏助手 · 聚合启动器 (OK Game Launcher Hub)

> 一个统一管理基于 [ok-script](https://ok-script.com/) 框架的各游戏自动化启动器的桌面工具。
> 一个窗口看遍所有游戏的运行状态、版本、更新日志，一键启动 / 强制关闭 / 窗口内更新。

---

## 这是什么

OK 生态目前是「一个框架 (ok-script) + 一堆独立游戏仓库（ok-nte / ok-wuthering-waves / ok-end-field …）」的松散结构，每个游戏都要单独开一个窗口。本项目把这些启动器**聚合到一个 WeGame 风格的游戏库界面**里，解决「开一堆窗口、看不清谁在跑、更新麻烦」的痛点。

核心能力：

- **统一卡片展示**：每个游戏一个卡片，含封面、版本下拉、更新日志（changelog）。
- **真实运行态监测**：通过 `tasklist` 精确识别各启动器进程（PyAppify 打包后进程名是 `pythonw.exe`，靠窗口标题区分），徽章 + 独立「运行中」标签 + 启动/强制关闭按钮三控件分工。
- **窗口内更新**：下载目标版本到本启动器 `repos/<key>/`（不碰原启动器目录），可一键「应用到 working 目录」。
- **强制关闭**：运行中时按钮变「强制关闭」，二次确认后终止进程（需以管理员身份运行才能生效）。

---

## 与 AUTO-MAS 的区别（不是同类竞品）

常被问「不是已经有 AUTO-MAS 了吗」。两者都带「管理多个脚本」的字样，但**管的层级完全不同**：

> **AUTO-MAS 管「跑起来的任务」，本工具管「助手本体」。**

| 维度 | AUTO-MAS | 本工具（OK 聚合启动器） |
|---|---|---|
| 一句话定位 | 多脚本 × 多配置的**批量代肝调度器** | 多游戏助手的**安装器 + 进程/版本看板** |
| 管什么 | 脚本实例 × 用户配置 × 任务队列 | 助手本体（装 / 启 / 停 / 更新 / 卸载） |
| 多账号配置管理 | ✅ 核心能力 | ❌ 不做（交给各助手自己） |
| 任务队列 / 调度编排 | ✅ 核心能力 | ❌ 不做 |
| 日志监看 + 异常自动重启 | ✅ 核心能力（无人值守） | ❌ 不做 |
| 代理记录 / 日志片段留存 | ✅ | ❌ |
| **安装助手本体** | ❌ 假定已装好 | ✅ **NSIS 整包直解、17 节点测速、MirrorChyan CDK** |
| **版本 / changelog 查看** | ❌ | ✅ 读本地 git 仓库，与原启动器同源 |
| **一键更新助手** | ❌ | ✅ 窗口内更新并应用到 working |
| 卸载（含清理残留） | ❌ | ✅ 三档：保留缓存 / 彻底清除 / 取消 |
| 体积 | **GB 级**（Electron，主程序 196 MB） | 单个 `.py` + PySide6（源码 KB 级） |
| 技术栈 | Electron + Python 后端 | 纯 Python（PySide6 + qfluentwidgets） |
| 协议 | AGPL-3.0 | GPL-3.0 |
| 遥测 | 收集版本号 + 运行时错误 | 无 |

### 覆盖的游戏也不同（关键）

AUTO-MAS 的脚本适配器为 `general / M9A / MAA / MaaEnd / Okww / SRC`：

- `Okww` → 鸣潮 **ok-ww**（与本工具管的是同一个）
- `MaaEnd` → 终末地，但走 **MaaFramework** 生态，**不是** ok-script 系的 `ok-end-field`
- **没有 `ok-nte`（异环）** —— AUTO-MAS 不支持异环

而本工具三个都管，且**全部是 ok-script 系**：`ok-nte` / `ok-ww` / `ok-end-field`。
想在 ok-script 系里用异环、或用 `ok-end-field`（而非 MaaEnd）管终末地，AUTO-MAS 帮不上忙。

### 结论：上下游关系，可以一起用

两者不是二选一，而是**互补**：

```
本工具：把 ok-nte / ok-ww / ok-end-field 装好、更新好、看住谁在跑
   ↓
AUTO-MAS：拿这些已装好的助手，编排多账号任务队列、无人值守代肝
```

如果你只需要「装 / 看 / 更新几个助手」，本工具更轻（无需 GB 级 Electron，无遥测）；
如果需要「多账号轮转 + 崩溃自动重启 + 任务编排」，那是 AUTO-MAS 的领域，本工具不做也不打算做。

---

## ⚠️ 免责声明

本软件是**第三方开源工具**，与上述游戏官方、ok-script 项目方均无隶属关系，仅供个人学习研究。

- 本项目基于 **ok-script** 生态构建，依其 Apache-2.0 + Commons Clause 协议要求，**明确提及并链接**：https://ok-script.com/
- 被管理的各游戏启动器（ok-nte / ok-ww / ok-end-field 等）各自拥有独立的开源协议
  （如 GPL-3.0 / AGPL-3.0，详见其各自仓库 LICENSE）。本工具仅以独立进程调用其可执行文件，**不复制、不修改其源码**，不构成衍生义务。
- 使用后果由使用者自行承担。

---

## 环境依赖

- Python 3.10+
- [PySide6](https://pypi.org/project/PySide6/)
- [PyQt-Fluent-Widgets](https://pypi.org/project/PyQt-Fluent-Widgets/)（`qfluentwidgets`）

安装：

```bash
pip install PySide6 PyQt-Fluent-Widgets
```

---

## 配置

所有路径都在 `config.json` 里，**不写死在代码里**。首次使用前按需修改：

```jsonc
{
  "install_root": "D:/OKApps",          // 你的游戏启动器根目录
  "apps": [
    {
      "key": "ok-nte",
      "display": "异环",
      "exe": "ok-nte/ok-nte/ok-nte.exe",          // 相对 install_root
      "app_json": "ok-nte/ok-nte/data/apps/ok-nte/app.json",
      "working": "ok-nte/ok-nte/data/apps/ok-nte/working",
      "pythonw": "ok-nte/ok-nte/data/apps/ok-nte/python/pythonw.exe",
      "icon": "assets/ok-nte.png",                // 可选，缺省回退到 assets/<key>.png
      "website": "https://ok-script.com/ok-nte/"
    }
    // ok-ww / ok-end-field 同理
  ]
}
```

> `exe / app_json / working / pythonw / icon` 均可写相对 `install_root` 的路径，也可写绝对路径。

---

## 运行

```bash
# 普通运行（监测、启动、更新均可，但「强制关闭」需管理员权限才生效）
python launcher.py

# 以管理员身份运行（推荐，强制关闭按钮才能真正杀进程）
# 右键「以管理员身份运行」或：
runas /user:Administrator "python launcher.py"
```

启动后会自动检测窗口主题并适配浅色/深色样式。

---

## 已知限制

1. **强制关闭需要管理员权限**：聚合启动器以普通权限运行时，`taskkill` 无法终止 PyAppify 启动器进程（拒绝访问）；以管理员身份运行后才会生效。
2. **运行态判定依赖 Windows `tasklist`**：目前仅支持 Windows。
3. **仅中文 Windows 体验最佳**：状态色遵循中国股票市场约定（涨/运行 → 红，跌 → 绿）。
4. 本工具不内置游戏资源下载，仅管理你本机已安装的 ok-script 系启动器。

---

## 贡献 / 接手

本项目以 **GPL-3.0** 开源，欢迎接手维护。

- 代码托管（待补充，可在 GitHub / Gitee 自行 fork）
- 上游生态入口：
  - ok-script 框架：https://ok-script.com/
  - ok-wuthering-waves（鸣潮，社区最活跃）：https://github.com/ok-oldking/ok-wuthering-waves
  - ok-nte（异环）：https://github.com/BnanZ0/ok-nte
  - ok-end-field（终末地）：https://github.com/AliceJump/ok-end-field
  - 开发者交流：ok-script 官方 QQ 群 938132715

---

## 许可证

GPL-3.0，详见 [LICENSE](./LICENSE)。本项目基于 ok-script（Apache-2.0 + Commons Clause）生态，特此署名：https://ok-script.com/
