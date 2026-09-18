"""Atalho de teclado global (funciona com o MT5 em foco) para mostrar/esconder o widget.

No Windows usa RegisterHotKey da própria API do sistema (sem instalar nada). Em outros sistemas o atalho só funciona
com o widget em foco — o widget avisa isso no menu.
"""

from __future__ import annotations

import sys
import threading
from typing import Callable

MODIFIERS = {"ctrl": 0x0002, "control": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000  # segurar a tecla não dispara várias vezes
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
NAMED_KEYS = {"space": 0x20, "home": 0x24, "end": 0x23, "insert": 0x2D, "pause": 0x13}


def parse_hotkey(text: str) -> tuple[int, int]:
    """"ctrl+alt+a" → (modificadores, código virtual da tecla). ValueError se não for um atalho válido."""
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("Use pelo menos um modificador e uma tecla, ex.: ctrl+alt+a")
    *mods, key = parts
    flags = 0
    for mod in mods:
        if mod not in MODIFIERS:
            raise ValueError(f"Modificador desconhecido: {mod}")
        flags |= MODIFIERS[mod]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key in NAMED_KEYS:
        vk = NAMED_KEYS[key]
    else:
        raise ValueError(f"Tecla desconhecida: {key}")
    return flags, vk


def pretty(text: str) -> str:
    return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in text.lower().split("+"))


class GlobalHotkey(threading.Thread):
    """Escuta o atalho numa thread própria. `callback` roda NESSA thread: não mexa na interface dentro dele."""

    def __init__(self, combo: str, callback: Callable[[], None]):
        super().__init__(name="aurum-widget-hotkey", daemon=True)
        self.combo, self.callback = combo, callback
        self.error: str | None = None
        self.ready = threading.Event()
        self._thread_id: int | None = None

    @staticmethod
    def supported() -> bool:
        return sys.platform == "win32"

    def run(self) -> None:
        if not self.supported():
            self.error = "atalho global só no Windows"
            self.ready.set()
            return
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        try:
            mods, vk = parse_hotkey(self.combo)
        except ValueError as exc:
            self.error = str(exc)
            self.ready.set()
            return
        self._thread_id = threading.get_native_id()
        if not user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
            self.error = f"{pretty(self.combo)} já está em uso por outro programa"
            self.ready.set()
            return
        self.ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.callback()
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._thread_id and self.supported() and not self.error:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
