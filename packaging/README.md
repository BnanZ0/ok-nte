# PyInstaller 打包

此目录维护 OK-NTE 的 Windows `onedir` 构建配置。项目路径由 spec 和脚本所在位置推导。

## 构建

在项目根目录执行以下命令, 使用项目 `.venv` 中的 Python 3.12 和应用依赖:

```powershell
uv sync
uv pip install --python .venv\Scripts\python.exe -r packaging\requirements.txt
.\packaging\build.ps1
```

已有完整 `.venv` 时可跳过 `uv sync`。构建脚本不会自动安装依赖。
`uv sync` 可能移除单独安装的构建工具, 后续构建前需要重新执行第二条命令。

输出为 `dist/ok-nte/ok-nte.exe`, 中间文件位于 `build/pyinstaller/`。
构建会替换旧的 `dist/ok-nte/`, 请先退出其中运行的程序, 将个人配置和日志保存到其他位置。
构建脚本会在调用 PyInstaller 前, 自动清除旧输出目录 `cache/openvino/*.blob` 的只读属性, 避免 OpenVINO 运行缓存阻止清理。程序仍需先退出, 以释放可能占用的文件。
分发时压缩整个 `dist/ok-nte/` 目录, 不能只复制 EXE。
`build/` 和 `dist/` 已由项目 `.gitignore` 排除。

## 输出目录

```text
dist/ok-nte/
  ok-nte.exe
  _internal/        # Python、第三方库、DLL 和冻结应用模块
  assets/           # 模型、图片、声音和其他应用资源
  icons/
  i18n/
  mid_lib/public/   # 内置公开曲目
  README.md
  SPONSOR.md
  LICENSE
```

`build.ps1` 在 PyInstaller 构建完成后, 将应用资源和说明文件按原目录结构复制到 EXE 旁。单独运行 spec 不会执行此复制步骤; 分发完整包请使用构建脚本。
个人 MIDI、收藏、配置、日志和自定义角色继续使用 EXE 所在目录。冻结程序启动时将工作目录设为 EXE 所在目录, 避免快捷方式的工作目录影响数据位置。
应用继续使用原有资源路径和 `get_path_relative_to_exe`, 不需要为 `_internal` 改写资源读取或覆盖 ok-script 的路径函数。第三方库自己的运行资源仍随对应库放在 `_internal`。

## 配置说明

- `ok-nte.spec`: 全量收集应用 `src` 的动态导入, 具体内置角色以 `.py` 源码放在 `_internal/src/char/`, 不进入 PYZ, 供角色发现、执行、查看和复制使用。`BaseChar`、`Support` 和其余应用模块只收集到 PYZ。修改内置角色源码后需重启程序才能重新加载。使用 PyInstaller 原生 `_internal` 布局收集依赖; 应用模型、图标、翻译和公开 MIDI 由构建脚本放到 EXE 旁。
- 启动入口统一使用项目根目录的 `main.py`。它在加载应用之前调用 `multiprocessing.freeze_support()`, 让 MIDI 进程池工作进程进入正确入口; 普通源码运行时该调用没有作用。
- EXE 图标使用 `icons/icon.png`, 构建时由 PyInstaller 通过 Pillow 转换为 Windows 图标格式。应用窗口图标仍由应用配置加载同一 PNG。
- EXE 使用无控制台窗口模式 (`console=False`), 对应源码通过 `pythonw.exe main.py` 启动的效果。冻结 EXE 使用 PyInstaller 窗口版启动器, 不依赖外部 `pythonw.exe`; 构建脚本使用 `python.exe` 显示构建日志。
- EXE 内嵌请求管理员权限的 manifest (`uac_admin=True`), 正常启动时由 Windows 发起权限提升请求。该构建选项适用于冻结 EXE, 源码直接运行的权限由启动方式决定。
- 第三方 Python 模块由 PyInstaller 分析导入关系, 并自动使用已安装的 hook。spec 不再遍历第三方库的全部 `.py`。
- spec 显式补齐 `win32timezone` 隐藏导入。pywin32 在转换 COM 日期时从 C 扩展动态加载该模块, Windows 计划任务的时间读取依赖它。
- `hooks/hook-ok.py`: 通过官方隔离查询接口读取当前 ok-script 的延迟导入表, 补齐相应模块, 收集 UI 资源和包元数据。该 hook 依赖当前版本的 `_LAZY_IMPORTS` 结构, 升级 ok-script 后需复核。
- `hooks/hook-onnxocr.py`: 补齐 OCR 模型和字典。
- `hooks/hook-openvino.py`: 补齐原生运行时动态加载的设备插件、模型前端 DLL 和配套 JSON; Python 模块依赖由正常分析处理。
- Qt 使用 PyInstaller 内置的 PySide6 hook, 不再手动删除 ICU DLL。
- 构建脚本以 Python `-I` 隔离模式运行, 忽略外部 Python 路径和用户包; 构建进程的 PATH 仅保留 `.venv/Scripts` 与 Windows 系统目录, 并清除外部 Qt/QML 搜索路径。脚本退出后恢复调用进程的环境变量, 不修改系统环境设置。
- 内置识别资源来自 `assets/`。`ok_templates/` 是模板编辑器保存截图的本地目录, 不随正式包收集; 模板编辑器需要时会创建该目录。
- 应用依赖来自当前 `.venv`; 构建工具版本记录在 `requirements.txt`。依赖或资源变化会改变包体积和文件数。
- OpenVINO hook 仅收集运行 DLL 和配套数据, 不全量收集开发头文件和静态导入库。

该目录只定义冻结包构建。原有 setup/pip 更新流程保持原有入口。
Mirror 的包发布、启动器适配和运行时更新需另外接入; 仅添加此配置不会自动切换发布方式。
构建完成后仍需验证实际桌面显示、游戏窗口交互和声音设备。

参考: [PyInstaller 官方 hook 机制](https://pyinstaller.org/en/stable/hooks.html)、[隐藏导入说明](https://pyinstaller.org/en/stable/when-things-go-wrong.html#listing-hidden-imports)。自定义 hook 是本项目针对依赖库补齐运行资源的配置, 并非这些库的上游官方 hook。

## 通用 SignPath 签名

签名实现放在 `.github/actions/sign/action.yml`, 是在调用方当前 runner 上执行的
composite action。`build.yml` 保持原来的单个 build job, 通过
`uses: ./.github/actions/sign` 调用, 签名结果直接覆盖原 EXE, 然后继续打包和发布。
`SIGN_BUILD`、`SIGN_SETUP` 和 `USE_RELEASE` 保持原有行为; 签名失败会停止 build。

调用方先上传未签名文件, 再提供 `artifact_id`、`api_token` 和 `output_directory`。
action 执行 Sign、可选 Approve, 等待签名完成并下载结果到指定目录。
默认使用 `single-exe` 和 `release-signing`; 安装器传 `artifact_configuration: setupexe`,
并沿用原先不显式 Approve 的策略, 传 `approve: 'false'`。
仓库需要配置 `SIGNPATH_API_TOKEN`。

## PyAppify Action

在项目根目录的 `pyappify.yml` 中配置:

```yaml
mirrorchyan:
  resource_id: YOUR_RESOURCE_ID
  stable_channel: stable
  packaging: packaging
```

PyAppify 根据此目录自动获取唯一的 `.spec` 和 `requirements.txt`, 安装应用和构建依赖,
并调用本目录的 `build.ps1` 完成构建。workflow 调用 PyAppify Action 即可,
不需要再传 spec 或依赖文件路径。`resource_id` 需替换为真实 Mirror RID;
测试 workflow 可以通过 Action 参数覆盖为本地测试 RID。
