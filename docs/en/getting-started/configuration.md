# Before you start

> [!IMPORTANT]
> Before starting the automation, please check and confirm the following settings:
>
> *   **GPU Settings**
>     *   **Graphics Filters**: **Disable** all graphics card filters, HDR, and sharpening effects (e.g., NVIDIA Freestyle, AMD FidelityFX).
>     *   **GPU Color (AMD users)**: Display colors need to match standard NVIDIA defaults to avoid image recognition issues. If color discrepancies cause failures, try enabling **"Automatically manage color for apps"** in Windows system settings.
> *   **In-game Settings**
>     *   **Resolution**: Recommended to use **1920x1080** or higher **16:9** resolutions.
>     *   **Keybindings**: Please use the game's **default** keybindings.
>     *   **Brightness** (Graphics): Use the **default** in-game brightness.
>     *   **UI Opacity** (Others ➔ Interface Settings): Set to **1.0**, and disable other settings that cause the UI to differ from the default.
>     *   **Damage Display** (Control ➔ System Features / 系统功能): **Disable**. Damage numbers can obscure the UI and interfere with auto-combat recognition.
>     *   **Movement Camera Correction / 移动镜头修正** (Control ➔ Camera Settings / 摄像机设置): **Disable**.
>     *   **Reset Camera When Pressing Lock-on / 按下锁定镜头回正** (Control ➔ Camera Settings / 摄像机设置): **Enable**.
>
> The exact in-game English wording of **System Features** and the camera section and options has not been verified. Chinese labels are included above for reference; the camera labels are descriptive translations.
>
> **Third-party Software**: Disable any overlays that display information on the game screen, such as the **framerate counter** from MSI Afterburner.

> [!WARNING]
> **Window and System State Precautions**
> *   **Mouse Interference**: When the game window is in the **foreground**, do not move your mouse, as it will interfere with the program's simulated clicks.
> *   **Window State**: The game window can be in the background but **must not be minimized**.
> *   **System State**: Do not let your computer **turn off the display** or **lock the screen**, as this will interrupt the program.
