from __future__ import annotations

from pathlib import Path
from typing import Protocol


class DirectorySelectionCancelled(FileNotFoundError):
    """The user closed the native directory picker without selecting a root."""


class NativeMediaPicker(Protocol):
    def choose_directory(self) -> Path: ...

    def choose_file(self, root: Path) -> Path: ...


class TkNativeMediaPicker:
    """Local-only chooser used by the desktop backend; no browser path is accepted."""

    def choose_directory(self) -> Path:
        from tkinter import Tk, filedialog

        window = Tk()
        window.attributes("-topmost", True)
        window.withdraw()
        try:
            selected = filedialog.askdirectory(
                parent=window, title="AI Canvas · 选择工作目录", mustexist=True
            )
        finally:
            window.destroy()
        if not selected:
            raise DirectorySelectionCancelled("native directory selection was cancelled")
        return Path(selected)

    def choose_file(self, root: Path) -> Path:
        from tkinter import Tk, filedialog

        window = Tk()
        window.withdraw()
        try:
            selected = filedialog.askopenfilename(initialdir=str(root))
        finally:
            window.destroy()
        if not selected:
            raise FileNotFoundError("native file selection was cancelled")
        return Path(selected)
