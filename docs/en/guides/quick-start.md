# Quick Start

## 🚀 Start a Task

1.  Navigate to the level or scene you want to automate.
2.  Click the **"Start"** button in the program's interface.

## 💻 Command Line Arguments

This command runs the daily routine at startup and exits when the task finishes.

```powershell
.\ok-nte.exe -t DailyRoutineTask -e --auto-start true
# or
.\.venv\Scripts\python.exe main.py -t DailyRoutineTask -e
```

*   With `-t` or `--task`, you can specify the task's class name (such as `DailyRoutineTask`) or its name.
*   Use `-e` or `--exit` to close the program after the task completes.
