from src.scene.ScreenRatio import ScreenRatio


class ESCPanelPosition:
    mail = ScreenRatio(0.8707, 0.8736)
    gift = ScreenRatio(0.810, 0.708)
    back_to_login = ScreenRatio(0.9305, 0.8729)

    def __init__(self, parent):
        self._parent = parent


class F1PanelPosition:
    activity = ScreenRatio(0.0551, 0.3833)
    domain = ScreenRatio(0.0563, 0.4924)

    def __init__(self, parent):
        self._parent = parent


class F2PanelPosition:
    mission = ScreenRatio(0.0570, 0.3451)

    def __init__(self, parent):
        self._parent = parent


class F5PanelPosition:
    coffee = ScreenRatio(0.335, 0.675)
    house = ScreenRatio(0.272, 0.392)
    hobbies = ScreenRatio(0.529, 0.472)

    def __init__(self, parent):
        self._parent = parent


class PanelPosition:
    """Normalized click positions grouped by their owning game panel."""

    def __init__(self, parent):
        self.esc = ESCPanelPosition(parent)
        self.f1 = F1PanelPosition(parent)
        self.f2 = F2PanelPosition(parent)
        self.f5 = F5PanelPosition(parent)
