[English](README_en.md) | [简体中文](README.md)

<div align="center">
  <img src="icons/icon.png" alt="icon" width="200"><br>
  <h1>ok-nte</h1>
  <p>一款基于图像识别的《异环》自动化工具，支持后台运行。</p>
  <p>基于 <a href="https://github.com/ok-oldking/ok-script">ok-script</a> 框架开发。</p>
  
  <p>
    <img src="https://img.shields.io/badge/platform-Windows-blue" alt="平台">
    <img src="https://img.shields.io/badge/python-3.12-skyblue" alt="Python版本">
    <a href="https://github.com/BnanZ0/ok-neverness-to-everness/releases"><img src="https://img.shields.io/github/downloads/BnanZ0/ok-neverness-to-everness/total" alt="总下载量"></a>
    <a href="https://github.com/BnanZ0/ok-neverness-to-everness/releases"><img src="https://img.shields.io/github/v/release/BnanZ0/ok-neverness-to-everness" alt="最新版本"></a>
    <a href="https://discord.gg/vVyCatEBgA"><img alt="Discord" src="https://img.shields.io/discord/296598043787132928?color=5865f2&label=%20Discord"></a>
  </p>
</div>

## ⚠️ 免责声明

本软件为开源、免费的外部工具，仅供学习和交流使用，旨在通过模拟操作简化《异环》的游戏玩法。

-   **工作原理**：程序仅通过识别现有用户界面与游戏进行交互，不修改任何游戏文件或代码。
-   **使用目的**：旨在为用户提供便利，无意破坏游戏平衡或提供任何不公平优势。
-   **法律责任**：使用本软件产生的所有问题及后果，均与本项目及开发者团队无关。开发者团队拥有对本项目的最终解释权。
-   **商业行为**：若您遇到商家使用本软件进行代练并收费，此行为可能涉及设备与时间成本，与本软件本身无关。

> **请注意：根据[《异环》公平游戏宣言]()：**
>
> > ""
> > ""
>
> **您应充分了解并自愿承担使用本工具可能带来的所有风险。**

<details>
<summary><strong>Disclaimer in English</strong></summary>

This software is an open-source, free external tool intended for learning and exchange purposes only. It is designed to automate the gameplay of *Neverness To Everness* by interacting with the game solely through the existing user interface and in compliance with relevant laws and regulations. The package is intended to provide a simplified way for users to interact with the game and is not meant to disrupt the game balance or provide any unfair advantage. This package does not modify any game files or game code in any way.

All issues and consequences arising from the use of this software are not related to this project or its development team. The development team reserves the final right of interpretation for this project. If you encounter vendors using this software for services and charging a fee, this may cover their costs for equipment and time; any resulting problems or consequences are not associated with this software.
</details>

## ✨ 主要功能


*   **后台运行**
    *   支持 PC 版游戏在后台运行时进行自动化操作

## 🖥️ 运行环境与兼容性

*   **操作系统**：Windows
*   **游戏分辨率**：1920x1080 或更高（推荐 16:9 宽高比）
*   **游戏语言**：简体中文 / English

## 🚀 安装指南

### 方式一：使用安装包 (推荐)

此方法适合绝大多数用户，简单快捷，并支持自动更新。

1.  前往 [**Releases**](https://github.com/BnanZ0/ok-neverness-to-everness/releases) 页面。
2.  下载最新的 `ok-nte-win32-China-setup.exe` 文件。
3.  双击运行安装程序，按提示完成安装即可。

### 方式二：从源码运行 (适合开发者)

此方法需要您具备 Python 环境，适合希望进行二次开发或调试的用户。

1.  **环境要求**：确保已安装 **Python 3.12** 或更高版本。
2.  **克隆仓库**：
    ```bash
    git clone https://github.com/BnanZ0/ok-neverness-to-everness.git
    cd ok-neverness-to-everness
    ```
3.  **安装依赖**：
    ```bash
    pip install -r requirements.txt --upgrade
    ```
    *提示：每次更新代码后，建议重新运行此命令以确保依赖库为最新版本。*
4.  **运行程序**：
    ```bash
    # 运行正式版
    python main.py
    
    # 运行调试版 (会输出更详细的日志)
    python main_debug.py
    ```

## 📖 使用指南与 FAQ

为确保程序稳定运行，请在使用前仔细阅读以下配置要求和常见问题解答。

### 一、 使用前配置 (必读)

在启动自动化前，请务必检查并确认以下设置：

*   **图形设置**
    *   **显卡滤镜**：**关闭** 所有显卡滤行和锐化效果（如 NVIDIA Freestyle, AMD FidelityFX）。
    *   **游戏亮度**：使用游戏 **默认亮度**。
    *   **游戏UI缩放**：使用游戏 **默认缩放100%**。
*   **分辨率**
    *   推荐使用 **1920x1080** 或以上的主流分辨率。
*   **按键设置**
    *   请务必使用游戏 **默认** 按键绑定。
*   **第三方软件**
    *   关闭任何在游戏画面上显示信息的悬浮窗，如 MSI Afterburner (小飞机) 的 **帧率显示**。
*   **窗口与系统状态**
    *   **鼠标干扰**：当游戏窗口处于 **前台** 时，请勿移动鼠标，否则会干扰程序的模拟点击。
    *   **窗口状态**：游戏窗口可以置于后台，但 **不可最小化**。
    *   **系统状态**：请勿让电脑 **熄屏** 或 **锁屏**，否则将导致程序中断。

### 二、 快速上手

1.  进入您想要自动化的关卡或场景。
2.  在程序界面上点击 **“开始”** 按钮即可。

### 三、 常见问题解答 (FAQ)

*   **无**

### 四、 问题反馈

如果以上方法未能解决您的问题，欢迎通过 [**Issues**](https://github.com/BnanZ0/ok-neverness-to-everness/issues) 向我们反馈。为帮助我们快速定位问题，请在提交时提供以下信息：

*   **问题截图**：清晰展示异常界面或错误提示。
*   **日志文件**：附上程序目录下的 `.log` 日志文件。
*   **详细描述**：您进行了哪些操作？问题具体表现是什么？问题是稳定复现还是偶尔发生？

## 💬 社区与交流

*   **QQ 用户群**: `1090560071`
*   **QQ 开发者群**: ``
*   **QQ 频道**: [点击加入]()
*   **Discord**: [https://discord.gg/vVyCatEBgA](https://discord.gg/vVyCatEBgA)

## 🔗 使用[ok-script](https://github.com/ok-oldking/ok-script)开发的项目：

* 鸣潮 [https://github.com/ok-oldking/ok-wuthering-wave](https://github.com/ok-oldking/ok-wuthering-waves)
* 明日方舟:终末地 [https://github.com/ok-oldking/ok-ef](https://github.com/ok-oldking/ok-end-field)
* 原神(停止维护,
  但是后台过剧情可用) [https://github.com/ok-oldking/ok-genshin-impact](https://github.com/ok-oldking/ok-genshin-impact)
* 少前2 [https://github.com/ok-oldking/ok-gf2](https://github.com/ok-oldking/ok-gf2)
* 星铁 [https://github.com/Shasnow/ok-starrailassistant](https://github.com/Shasnow/ok-starrailassistant)
* 星痕共鸣 [https://github.com/Sanheiii/ok-star-resonance](https://github.com/Sanheiii/ok-star-resonance)
* 二重螺旋 [https://github.com/BnanZ0/ok-duet-night-abyss](https://github.com/BnanZ0/ok-duet-night-abyss)
* 白荆回廊(停止更新) [https://github.com/ok-oldking/ok-baijing](https://github.com/ok-oldking/ok-baijing)


## ❤️ 赞助与致谢
| **微信支付** | **支付宝** |
| :---: | :---: |
| <img src="https://github.com/user-attachments/assets/39db1471-6e5f-4c4d-a6f0-b06d38cb44cf" width="350px" height="350px" style="object-fit: contain;" /> | <img src="https://github.com/user-attachments/assets/4c566443-af3a-4b73-9530-386e66f6eb23" width="350px" height="350px" style="object-fit: contain;" /> |

感谢您的支持！
### 赞助商 (Sponsors)
*   **EXE 签名**: Free code signing provided by [SignPath.io](https://signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

### 致谢
*   [ok-oldking/OnnxOCR](https://github.com/ok-oldking/OnnxOCR)
*   [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)
*   [Toufool/AutoSplit](https://github.com/Toufool/AutoSplit)
