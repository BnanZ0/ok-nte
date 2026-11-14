"""Safety confirmation for importing externally supplied Python code."""

from PySide6.QtWidgets import QWidget

from src.ui.foundation.dialogs import show_dialog_and_wait

EXTERNAL_CODE_SAFETY_TITLE = "外置代码安全提示 / External Code Safety Notice"
EXTERNAL_CODE_SAFETY_NOTICE = (
    "外置代码会在扫描或使用时以与本软件相同的权限执行. "
    "请只导入您完全信任的代码, 并自行确认其安全性.\n"
    "External code runs with the same permissions as this software when scanned or used. "
    "Import only code you fully trust and verify its safety yourself.\n\n"
    "导入外置代码的风险由使用者自行承担. "
    "开发者不对外置代码导致的资料损失, 系统变更, 账号处罚或其他损害负责.\n"
    "You assume all risks of importing external code. The developers are not liable for data loss, "
    "system changes, account penalties, or other damages caused by external code."
)


def confirm_external_code_import(parent: QWidget) -> bool:
    return bool(
        show_dialog_and_wait(
            EXTERNAL_CODE_SAFETY_TITLE,
            EXTERNAL_CODE_SAFETY_NOTICE,
            parent=parent,
            rich_text=False,
            hide_cancel=False,
            close_delay_seconds=1,
            width=496,
            message_role="body",
        )
    )
