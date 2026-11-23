"""把插件任务固定在宿主已有分组里。

宿主 task_manager 加载插件后会执行 `task.group_name = manifest.script_name`，
把插件单开成一栏。这里用数据描述符吞掉那次赋值，任务就留在宿主自己的分组。

插件形态下无法改宿主代码，用描述符是最小的落点：类属性优先于实例赋值，
`getattr(task, "group_name")` 永远返回构造时给的分组名。
"""

from __future__ import annotations


class FixedGroup:
    """只读 group_name：外部赋值被忽略，读到的永远是初始化时给的分组名。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def __get__(self, instance, owner=None) -> str:
        return self.name

    def __set__(self, instance, value) -> None:
        return None
