import tkinter as tk
from typing import Any


def install_log_clipboard_support(app: Any) -> None:
    """CTkTextbox 내부 Text 위젯에 직접 로그 복사 단축키/우클릭 메뉴를 설치한다."""
    textbox = getattr(app, "log_textbox", None)
    if textbox is None:
        return

    inner = getattr(textbox, "_textbox", textbox)

    def _copy_selection(_event=None):
        try:
            selected = inner.get("sel.first", "sel.last")
        except Exception:
            return "break"
        try:
            app.clipboard_clear()
            app.clipboard_append(selected)
            app.update_idletasks()
        except Exception:
            pass
        return "break"

    def _select_all(_event=None):
        try:
            inner.tag_add("sel", "1.0", "end-1c")
            inner.mark_set("insert", "1.0")
            inner.see("insert")
        except Exception:
            pass
        return "break"

    def _copy_all(_event=None):
        try:
            value = inner.get("1.0", "end-1c")
            app.clipboard_clear()
            app.clipboard_append(value)
            app.update_idletasks()
        except Exception:
            pass
        return "break"

    # MainWindow의 기존 CTkTextbox 단축키 바인딩보다 나중에 설치하여
    # 실제 내부 Tk Text 위젯에서 Ctrl/Cmd+C가 확실하게 동작하게 한다.
    for seq in ("<Control-c>", "<Control-C>", "<Command-c>", "<Command-C>"):
        inner.bind(seq, _copy_selection)
    for seq in ("<Control-a>", "<Control-A>", "<Command-a>", "<Command-A>"):
        inner.bind(seq, _select_all)

    # 전체 로그 복사는 Ctrl/Cmd+Shift+C
    for seq in ("<Control-Shift-c>", "<Control-Shift-C>", "<Command-Shift-c>", "<Command-Shift-C>"):
        inner.bind(seq, _copy_all)

    menu = tk.Menu(inner, tearoff=False)
    menu.add_command(label="선택 영역 복사", command=_copy_selection)
    menu.add_command(label="전체 선택", command=_select_all)
    menu.add_command(label="전체 로그 복사", command=_copy_all)

    def _show_menu(event):
        try:
            inner.focus_set()
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
        return "break"

    inner.bind("<Button-3>", _show_menu)
    inner.bind("<Button-2>", _show_menu)
    inner.bind("<Control-Button-1>", _show_menu)

    # 디버깅/향후 UI 버튼에서 재사용 가능하도록 노출
    app.copy_selected_log = _copy_selection
    app.copy_all_logs = _copy_all
