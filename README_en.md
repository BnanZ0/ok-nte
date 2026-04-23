[English](README_en.md) | [简体中文](README.md)

<div align="center">
  <img src="icons/icon.png" alt="icon" width="200"><br>
  <h1>ok-nte</h1>
  <p>An image-recognition-based automation tool for <em>Neverness To Everness</em>, with background operation support.</p>
  <p>Developed based on the <a href="https://github.com/ok-oldking/ok-script">ok-script</a> framework.</p>
  
  <p>
    <img src="https://img.shields.io/badge/platform-Windows-blue" alt="Platform">
    <img src="https://img.shields.io/badge/python-3.12-skyblue" alt="Python Version">
    <a href="https://github.com/BnanZ0/ok-nte/releases"><img src="https://img.shields.io/github/downloads/BnanZ0/ok-nte/total" alt="Total Downloads"></a>
    <a href="https://github.com/BnanZ0/ok-nte/releases"><img src="https://img.shields.io/github/v/release/BnanZ0/ok-nte" alt="Latest Release"></a>
    <a href="https://discord.gg/vVyCatEBgA"><img alt="Discord" src="https://img.shields.io/discord/296598043787132928?color=5865f2&label=%20Discord"></a>
  </p>
</div>

## ⚠️ Disclaimer

This software is an open-source, free external tool intended for learning and exchange purposes only. It is designed to automate the gameplay of *Neverness To Everness* by interacting with the game solely through the existing user interface and in compliance with relevant laws and regulations. The package is intended to provide a simplified way for users to interact with the game and is not meant to disrupt the game balance or provide any unfair advantage. This package does not modify any game files or game code in any way.

All issues and consequences arising from the use of this software are not related to this project or its development team. The development team reserves the final right of interpretation for this project. If you encounter vendors using this software for services and charging a fee, this may cover their costs for equipment and time; any resulting problems or consequences are not associated with this software.

> **Please Note: According to the [*Neverness To Everness* Fair Play Declaration](https://nte.perfectworld.com/en/article/news/gamebroad/20260206/260828.html):**
>
> > ""
> > The use of any third-party tools that undermine fair gameplay is strictly prohibited. We will take strong action against violations involving illegal tools such as cheats, speed hacks, macro scripts, and similar software.
> >
> > Prohibited behaviors include, but are not limited to: auto-farming, skill acceleration, god mode, teleportation, and game data manipulation. Any account found to be involved in such activities will be banned upon verification.
> > ""
>
> **You should fully understand and voluntarily assume all potential risks associated with using this tool.**

## ✨ Main Features

*   **Background Operation**
    *   Supports automation while the PC game is running in the background

## 🖥️ System Requirements & Compatibility

*   **Operating System**: Windows
*   **Game Resolution**: 1920x1080 or higher (16:9 aspect ratio recommended)
*   **Game Language**: Simplified Chinese / English

## 🚀 Installation Guide

### Method 1: Using the Installer (Recommended)

This method is suitable for most users. It's simple, fast, and supports automatic updates.

1.  Go to the [**Releases**](https://github.com/BnanZ0/ok-nte/releases) page.
2.  Download the latest `ok-nte-win32-Global-setup.exe` file.
3.  Double-click the installer and follow the prompts to complete the installation.

### Method 2: Running from Source (For Developers)

This method requires a Python environment and is suitable for users who want to contribute, modify, or debug the code.

1.  **Prerequisites**: Ensure you have **Python 3.12** or a newer version installed.
2.  **Clone the repository**:
    ```bash
    git clone https://github.com/BnanZ0/ok-nte.git
    cd ok-nte
    ```
3.  **Install dependencies**:
    ```bash
    uv sync
    # or
    pip install -r requirements.txt
    ```
    *Tip: After pulling new code, it's recommended to run this command again to ensure all dependencies are up to date.*
4.  **Run the application**:
    ```bash
    # Run the standard version
    python main.py
    
    # Run the debug version (outputs more detailed logs)
    python main_debug.py
    ```

## 📖 Usage Guide & FAQ

To ensure the program runs stably, please carefully read the following configuration requirements and frequently asked questions before use.

### 1. Pre-use Configuration (Required)

Before starting the automation, please check and confirm the following settings:

*   **Graphics Settings**
    *   **Graphics Filters**: **Disable** all graphics card filters and sharpening effects (e.g., NVIDIA Freestyle, AMD FidelityFX).
    *   **Game Brightness**: Use the **default** in-game brightness.
*   **Resolution**
    *   Recommended to use **1920x1080** or other common 16:9 resolutions.
*   **Keybindings**
    *   Please use the game's **default** keybindings.
*   **Third-party Software**
    *   Disable any overlays that display information on the game screen, such as the **framerate counter** from MSI Afterburner.
*   **Window and System State**
    *   **Mouse Interference**: When the game window is in the **foreground**, do not move your mouse, as it will interfere with the program's simulated clicks.
    *   **Window State**: The game window can be in the background but **must not be minimized**.
    *   **System State**: Do not let your computer **turn off the display** or **lock the screen**, as this will interrupt the program.

### 2. Quick Start

1.  Navigate to the level or scene you want to automate.
2.  Click the **"Start"** button in the program's interface.

### 3. Frequently Asked Questions (FAQ)

*   **None**

### 4. Bug Reports & Feedback

If the solutions above do not resolve your issue, feel free to report it via [**Issues**](https://github.com/BnanZ0/ok-nte/issues). To help us quickly identify the problem, please provide the following information in your report:

*   **Screenshot**: A clear image of the error or unusual behavior.
*   **Log File**: Attach the `.log` file from the program's directory.
*   **Detailed Description**: What were you doing? What exactly happened? Can you reproduce the issue consistently, or does it happen randomly?

## 💬 Community

*   **Discord**: [https://discord.gg/vVyCatEBgA](https://discord.gg/vVyCatEBgA)

## 🔗 Projects developed using [ok-script](https://github.com/ok-oldking/ok-script):

* Wuthering Waves [https://github.com/ok-oldking/ok-wuthering-waves](https://github.com/ok-oldking/ok-wuthering-waves)
* End Field [https://github.com/ok-oldking/ok-end-field](https://github.com/ok-oldking/ok-end-field)
* Genshin Impact (discontinued, but background story progression is still usable) [https://github.com/ok-oldking/ok-genshin-impact](https://github.com/ok-oldking/ok-genshin-impact)
* Girls' Frontline 2 [https://github.com/ok-oldking/ok-gf2](https://github.com/ok-oldking/ok-gf2)
* Honkai: Star Rail [https://github.com/Shasnow/ok-starrailassistant](https://github.com/Shasnow/ok-starrailassistant)
* Star-Resonance [https://github.com/Sanheiii/ok-star-resonance](https://github.com/Sanheiii/ok-star-resonance)
* Duet Night Abyss [https://github.com/BnanZ0/ok-duet-night-abyss](https://github.com/BnanZ0/ok-duet-night-abyss)
* Ash Echoes (discontinued) [https://github.com/ok-oldking/ok-baijing](https://github.com/ok-oldking/ok-baijing)

## ❤️ Support & Acknowledgments
*   Like this project? [Lighten the star⭐](https://github.com/BnanZ0/ok-nte)

### Contributors

<a href="https://github.com/BnanZ0/ok-nte/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=BnanZ0/ok-nte" />
</a>

### Sponsors
*   **EXE Signing**: Free code signing provided by [SignPath.io](https://signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

### Acknowledgments
*   [ok-oldking/OnnxOCR](https://github.com/ok-oldking/OnnxOCR)
*   [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)
*   [Toufool/AutoSplit](https://github.com/Toufool/AutoSplit)
