# AGENTS.md

适用于整个仓库; 更深层的 `AGENTS.md` 补充或覆盖局部规则。

`ok-nte` 是基于 ok-script 的 Windows《异环》桌面自动化工具, 使用截图、OCR、OpenVINO、系统音频和普通键鼠输入。

## 项目约束

- 保持 README 和 `src/config.py` 中仅通过 UI/系统输出交互的边界; 不引入进程注入、Hook 或游戏内存读取。改变边界须有用户明确要求并同步风险说明。
- 不提交或在日志中暴露账号、密钥、个人路径、用户配置、截图、音频样本等本地隐私数据。
- 未经用户明确要求, 不改动 LICENSE、免责声明、赞助链接或发布配置。
- UI 线程不执行模型加载、OCR 大扫描、音频/路径循环或文件 I/O; 后台任务和输入循环必须可停止, 触发任务不能长时间阻塞 executor。
- 自定义角色、出招表、特征和用户配置保持向后兼容; 新字段提供默认值或迁移。新增实验能力默认关闭并说明风险。

## 实现约定

- Python 版本和格式以 `pyproject.toml` 为准。修改 Python 注释和字符串时使用 ASCII `,`、`;`, 避免全角逗号和分号。
- 任务复用 `BaseNTETask` / `NTEOneTimeTask` 和已有 mixin; 领域逻辑留在对应子系统。新增任务或全局配置同步 `src/config.py` 的注册、默认值和说明。
- UI 复用 PySide6/qfluentwidgets; 不为局部功能引入大型框架、服务端或异步运行时。
- 运行期计时统一使用 `src.utils.clock`; 已有 `self.clock` / `self.task.clock` 时直接复用, 无法稳定取得时直接 import。局部超时使用 `clock.timer(timeout=...)` 和 `expired()`, 高精度显式指定 `precise=True`。
- 不直接读取 `time.time()`、`time.monotonic()`、`time.perf_counter()` 及其纳秒版本。真实日期使用带时区的 `datetime`; 不持久化运行期单调时间点。涉及计时 API 和兼容性时查阅 [runtime_clock.md](docs/runtime_clock.md)。

## 验证

存在 `.venv` 时直接调用 `.\.venv\Scripts\python.exe`, 不使用全局 Python, 也不通过 `$py` / `&` 包装解释器命令。

按改动范围选择验证; 下列命令是入口, 不要求每次全部运行:

```powershell
# 单个测试模块, 按改动替换模块名
.\.venv\Scripts\python.exe -m unittest tests.TestCombatPlanner

# 全量测试
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*.py" --top-level-directory .

# 语法检查, 按改动替换路径
.\.venv\Scripts\python.exe -m py_compile path\to\file.py

# Ruff 已安装时, 按改动替换路径
ruff check path\to\file.py
git diff --check
```

全量 discovery 必须保留 `--top-level-directory .`, 以加载 `tests/__init__.py` 的运行时和用户数据隔离; 不缩写成 `-t`, 它与 ok-script 参数冲突。

- 优先验证共享逻辑、planner、战斗状态、解析/迁移和声音捕获的行为回归; 不为角色具体出招、普通 UI 拼装、简单构造或胶水代码机械新增测试。
- 断言行为、状态和必要的外部交互, 避免绑定普通日志文案和内部调用顺序。需要真实游戏窗口、音频或截图的部分说明人工验证范围。
- 完成后简要说明变更、实际执行的验证和未验证范围; 涉及战斗状态、声音捕获或用户数据时说明相关风险。

## 按任务查阅

只读取与当前任务有关的资料:

| 改动范围 | 关键约束与入口 |
| --- | --- |
| 战斗与角色 | `AutoCombatTask` 只协调循环。角色通过 planner 公开 API 声明意图, 不操纵内部状态; `combat_plan()` 构建/评分阶段不发送输入或发布副作用请求。API 用法见 [Combat Planner](docs/zh-CN/development/combat-planner.md)。公开 API 变更同步中英文指南及 `tests/TestCombatPlanner.py`。 |
| 战斗检测 | `CombatCheck` 影响所有自动战斗入口。入战/脱战和目标状态变化需回归验证; 角色死亡、切人或终结技失败要能恢复或明确中止。 |
| 视觉与 OCR | 支持 16:9、1080p 及以上; 优先相对坐标和 `box_of_screen`。预处理复用 `src/utils/game_filters.py` / `image_utils.py`; 推理复用 `BaseNTETask.openvino_detect()` 和全局模型状态。模板变化检查 `Labels`、`assets/coco_annotations.json`、`ok_templates/`。 |
| 声音捕获 | WASAPI 代码在 `src/sound_trigger/capture/`; 修改 Windows 结构体或 HRESULT 时查阅 `tests/test_sound_trigger_capture.py`。线程失败后须可重启或清晰停止。 |
| UI、翻译和配置 | 新文案检查 `i18n/` 的 gettext 翻译; 不手工修改生成的 `.mo`。配置项保留默认值和 `config_description`。 |
| 外置角色、文档和开发环境 | 见 [贡献与验证](docs/zh-CN/development/contributing.md) 和 [从源码运行](docs/zh-CN/development/running-from-source.md); 新文档页面同步 `mkdocs.yml` 导航。 |

## 维护本文件

只保留长期有效、项目特有且能避免重复错误的规则。实现细节放在代码或对应开发文档, 在这里按适用范围链接; 更新规则时合并或替换旧条目。
