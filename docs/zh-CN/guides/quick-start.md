# 快速上手

## 🚀 启动任务

1. 进入您想要自动化的关卡或场景。
2. 在程序界面上点击 **“开始”** 按钮即可。

## 💻 命令行参数

以下命令会在程序启动时运行日常任务，并在任务完成后退出。

```powershell
.\ok-nte.exe -t DailyRoutineTask -e --auto-start true
# or
.\.venv\Scripts\python.exe main.py -t DailyRoutineTask -e
```

*   `-t` 或 `--task` 可填写任务的类名（如 `DailyRoutineTask`）或任务名称。
*   使用 `-e` 或 `--exit` 在任务完成后自动退出程序。
