from ok import Box


class ScreenPosition:
    """
    根据屏幕宽高生成各个位置的 Box。
    支持：
        - 固定位置（top_left、top_right 等）
        - 自定义 Box
        - 百分比/比例 Box
    使用 to_x / to_y 参数。
    """

    def __init__(self, parent):
        self.parent = parent  # parent 必须有 .width 和 .height

    # ---------- 固定位置 ----------
    @property
    def top_left(self) -> Box:
        return Box(x=0, y=0, to_x=self.parent.width // 2, to_y=self.parent.height // 2)

    @property
    def top_right(self) -> Box:
        return Box(x=self.parent.width // 2, y=0, to_x=self.parent.width, to_y=self.parent.height // 2)

    @property
    def bottom_left(self) -> Box:
        return Box(x=0, y=self.parent.height // 2, to_x=self.parent.width // 2, to_y=self.parent.height)

    @property
    def bottom_right(self) -> Box:
        return Box(x=self.parent.width // 2, y=self.parent.height // 2, to_x=self.parent.width, to_y=self.parent.height)

    @property
    def left(self) -> Box:
        return Box(x=0, y=0, to_x=self.parent.width // 2, to_y=self.parent.height)

    @property
    def right(self) -> Box:
        return Box(x=self.parent.width // 2, y=0, to_x=self.parent.width, to_y=self.parent.height)

    @property
    def top(self) -> Box:
        return Box(x=0, y=0, to_x=self.parent.width, to_y=self.parent.height // 2)

    @property
    def bottom(self) -> Box:
        return Box(x=0, y=self.parent.height // 2, to_x=self.parent.width, to_y=self.parent.height)

    @property
    def center(self) -> Box:
        return Box(x=self.parent.width // 4, y=self.parent.height // 4,
                   to_x=self.parent.width * 3 // 4, to_y=self.parent.height * 3 // 4)

    # ---------- 战斗UI按键映射 (基于COCO标注 @ 3840x2160) ----------
    # 这些Box是从COCO数据集标注的bbox转换而来
    # bbox格式: [x, y, w, h] -> Box(x, y, to_x=x+w, to_y=y+h)

    def _scale_box(self, x: int, y: int, w: int, h: int, ref_width: int = 3840, ref_height: int = 2160) -> Box:
        """将基于参考分辨率(3840x2160)的bbox缩放到当前屏幕分辨率"""
        scale_x = self.parent.width / ref_width
        scale_y = self.parent.height / ref_height
        return Box(
            x=int(x * scale_x),
            y=int(y * scale_y),
            to_x=int((x + w) * scale_x),
            to_y=int((y + h) * scale_y)
        )

    # 导航按键 (屏幕右上角)
    @property
    def nav_b(self) -> Box:
        """B键位置 - 背包"""
        return self._scale_box(3383, 13, 33, 34)

    @property
    def nav_c(self) -> Box:
        """C键位置 - 角色"""
        return self._scale_box(3532, 15, 29, 31)

    @property
    def nav_esc(self) -> Box:
        """ESC键位置 - 菜单"""
        return self._scale_box(3688, 16, 65, 29)

    # 交互按键
    @property
    def interact_pick_f(self) -> Box:
        """F键位置 - 拾取"""
        return self._scale_box(2461, 1374, 25, 28)

    # 战斗技能 (屏幕右下角 - 技能栏)
    @property
    def combat_skill_1(self) -> Box:
        """技能1位置"""
        return self._scale_box(3140, 2026, 28, 28)

    @property
    def combat_skill_2(self) -> Box:
        """技能2位置"""
        return self._scale_box(3330, 2023, 30, 31)

    @property
    def combat_skill_3(self) -> Box:
        """技能3位置"""
        return self._scale_box(3526, 2026, 25, 29)

    @property
    def combat_skill_4(self) -> Box:
        """技能4位置"""
        return self._scale_box(3718, 2025, 24, 31)

    @property
    def combat_default_link_skill(self) -> Box:
        """E技能位置 - 中心偏左"""
        return self._scale_box(2213, 963, 23, 26)

    # 终极技能 (屏幕右下角 - 终极技能栏)
    @property
    def combat_ult_1(self) -> Box:
        """终极技能1位置"""
        return self._scale_box(3136, 1697, 34, 31)

    @property
    def combat_ult_2(self) -> Box:
        """终极技能2位置"""
        return self._scale_box(3332, 1697, 29, 31)

    @property
    def combat_ult_3(self) -> Box:
        """终极技能3位置"""
        return self._scale_box(3521, 1694, 34, 34)

    @property
    def combat_ult_4(self) -> Box:
        """终极技能4位置"""
        return self._scale_box(3718, 1698, 27, 29)

    # 战斗UI区域组合
    @property
    def combat_skill_bar(self) -> Box:
        """技能栏区域 (包含1-4技能)"""
        return Box(
            x=self.combat_skill_1.x,
            y=self.combat_skill_1.y,
            to_x=self.combat_skill_4.to_x,
            to_y=self.combat_skill_1.to_y
        )

    @property
    def combat_ult_bar(self) -> Box:
        """终极技能栏区域 (包含ult 1-4)"""
        return Box(
            x=self.combat_ult_1.x,
            y=self.combat_ult_1.y,
            to_x=self.combat_ult_4.to_x,
            to_y=self.combat_ult_3.to_y
        )

    @property
    def nav_panel(self) -> Box:
        """导航面板区域 (b, c, esc)"""
        return Box(
            x=self.nav_b.x,
            y=self.nav_b.y,
            to_x=self.nav_esc.to_x,
            to_y=self.nav_esc.to_y
        )
