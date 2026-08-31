import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ui.main_window import MainWindow
from ui.log_clipboard_support import install_log_clipboard_support


def main():
    app = MainWindow()
    install_log_clipboard_support(app)
    app.mainloop()


if __name__ == "__main__":
    main()
