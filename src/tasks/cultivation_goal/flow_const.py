"""流程层共用的常量与哨兵值（拆文件后集中一处，避免互相 import 成环）。"""

# 连打确认框：小区域对应继续挑战弹窗，大区域对应收场弹窗。
CG_BURST_CONFIRM_RANGE = (0.6469, 0.8174, 0.7027, 0.9097)
CG_FINAL_CONFIRM_RANGE = (0.4074, 0.8174, 0.4578, 0.9056)

# 用来区分「实例上本来没有这个属性」和「属性值就是 None」。
_MISSING = object()

# 原版按这个 info 值决定要不要进副本, 0 表示今日体力目标已达标。
CG_INFO_MUST_USE = "must use stamina"
# 培养目标模式要刷空体力, 委派空幕时用一个大值让它不限次。
CG_CONSOLE_STAMINA_OPEN = 9999
