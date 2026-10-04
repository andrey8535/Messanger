"""
client.py — GUI-клиент зашифрованного чата (tkinter, без сторонних зависимостей).

    python client.py [--host HOST] [--port PORT]

Окно без системной рамки: тянется за края и углы, двигается за шапку.
Вкладки: «Чат» и «Настройки» (тема, шрифт, цвет текста). Настройки лежат
в client_settings.json рядом со скриптом.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

try:
    from protocol import handshake
except ModuleNotFoundError as exc:
    venv_py = Path(__file__).parent / "venv" / "Scripts" / "python.exe"
    if __name__ == "__main__" and venv_py.exists() and Path(sys.executable).resolve() != venv_py.resolve():
        ret = subprocess.call([str(venv_py), str(Path(__file__).resolve())] + sys.argv[1:])
        sys.exit(ret)
    try:
        from protocol import handshake
    except Exception:
        handshake = None

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 26327
SETTINGS_PATH = Path(__file__).with_name("client_settings.json")
CHEATS_PATH = Path(__file__).with_name("cheats.json")


def parse_host_port(target: str, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    target = target.strip()
    if not target:
        return "127.0.0.1", default_port
    port = default_port
    if ":" in target:
        parts = target.split(":")
        target = parts[0].strip()
        for p in reversed(parts[1:]):
            p = p.strip()
            if p.isdigit():
                port = int(p)
                break
    # Fix typo where port was appended with a dot instead of colon (e.g. 172.86.68.225.26327)
    m = re.match(r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\.(\d+)$", target)
    if m:
        target = m.group(1)
        port = int(m.group(2))
    return target or "127.0.0.1", port


def run_embedded_server(host="127.0.0.1", port=DEFAULT_PORT):
    try:
        from server import Hub, serve_client
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(64)
        hub = Hub()
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=serve_client, args=(conn, addr, hub), daemon=True).start()
    except Exception:
        pass

KEY = "#000001"        # цвет, который Windows делает прозрачным (скруглённые углы окна)

# Базовые («опорные») размеры в расчёте на экран 96 DPI / масштаб 100%.
# Реальные значения, которые использует приложение, хранятся в self.MIN_W,
# self.RING и т.д. — они вычисляются в App.__init__ с учётом DPI монитора
# (self.scale), поэтому интерфейс выглядит одинаково "по размеру" на любом
# экране и при любом масштабировании Windows (100% / 125% / 150% / 200%...).
MIN_W, MIN_H = 720, 480
RING = 8               # кольцо вокруг содержимого: здесь ловим края для растягивания
CORNER = 20
RADIUS = 18


def enable_dpi_awareness() -> None:
    """Включает per-monitor DPI awareness на Windows до создания окна Tk.

    Без этого Windows сама растягивает битмап окна на экранах с масштабом
    ≠ 100% (тексты и иконки становятся размытыми, а размеры — не соответствуют
    реальному разрешению). С этим — окно рисуется в реальных пикселях монитора,
    а недостающую компенсацию размеров берёт на себя App.scale.
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # Per-Monitor v2
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Темы и настройки                                                            #
# --------------------------------------------------------------------------- #

def _theme(dot, bg, card, card_hi, border, accent, on_accent, text, dim):
    return dict(dot=dot, bg=bg, card=card, card_hi=card_hi, border=border, accent=accent,
                on_accent=on_accent, text=text, dim=dim, danger="#ef5b5b", ok="#3ecf8e")


THEMES = {
    "dark":   _theme("#26262c", "#151518", "#212126", "#2b2b31", "#2c2c33", "#a5a5bd", "#131316", "#ececf1", "#8a8a96"),
    "light":  _theme("#f4f4f8", "#f4f4f8", "#ffffff", "#e9e9f0", "#dcdce6", "#5b5bd6", "#ffffff", "#1c1c24", "#6f6f7c"),
    "red":    _theme("#f0616a", "#1a1315", "#251a1d", "#312226", "#3a2a2e", "#f0616a", "#1a0c0e", "#f4eaeb", "#9a8286"),
    "purple": _theme("#b9a1fa", "#17141d", "#221e2b", "#2c2738", "#2f2a3b", "#b9a1fa", "#17111f", "#eeecf5", "#8a839f"),
    "blue":   _theme("#5b9dff", "#111620", "#1a2231", "#232d41", "#27324a", "#5b9dff", "#08101c", "#e8eefa", "#7f8ba3"),
    "green":  _theme("#43d18f", "#101914", "#17251d", "#1f3126", "#233a2c", "#43d18f", "#07140d", "#e8f5ee", "#7d9587"),
}
THEME_ORDER = [("dark", "Тёмная"), ("light", "Светлая"), ("red", "Красная"), ("system", "Системная"),
               ("purple", "Фиолетовая"), ("blue", "Синяя"), ("green", "Зелёная")]

TEXT_COLORS = ["#ffffff", "#e0e0e0", "#ffd166", "#ff8fab", "#7dd3fc", "#86efac", "#c4b5fd", "#fdba74"]
FONT_CHOICES = ["Segoe UI", "Bahnschrift", "Verdana", "Tahoma", "Calibri", "Arial", "Trebuchet MS",
                "Georgia", "Consolas", "Courier New", "Comic Sans MS"]
FONT_MIN, FONT_MAX = 8, 20

DEFAULTS = dict(
    theme="purple",
    font="Segoe UI",
    font_size=11,
    text_color=None,
    name="",
    geometry=None,
    offline_mode=True,
    server_addr="127.0.0.1:26327",
    last_tab="cheats",
)


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}


def save_settings(cfg: dict) -> None:
    try:
        SETTINGS_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass


DEFAULT_STARTER_CHEATS: list[dict] = []


def load_cheats() -> list[dict]:
    if not CHEATS_PATH.exists():
        save_cheats([])
        return []
    try:
        data = json.loads(CHEATS_PATH.read_text("utf-8"))
        if isinstance(data, list):
            return data
        return []
    except (OSError, ValueError):
        return []


def save_cheats(items: list[dict]) -> None:
    try:
        CHEATS_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass


def system_is_light() -> bool:
    if sys.platform == "win32":
        try:
            import winreg
            path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1
        except OSError:
            pass
    return False


def fmt_time(ts=None) -> str:
    return time.strftime("%H:%M", time.localtime(ts if isinstance(ts, (int, float)) else time.time()))


# --------------------------------------------------------------------------- #
# Небольшие виджеты и анимации                                                #
# --------------------------------------------------------------------------- #

def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:
        return (128, 128, 128)


def rgb_to_hex(rgb: tuple[int, int, int] | tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def lerp_color(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    return rgb_to_hex((
        r1 + (r2 - r1) * t,
        g1 + (g2 - g1) * t,
        b1 + (b2 - b1) * t
    ))


def round_points(x1, y1, x2, y2, r):
    return [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r,
            x2, y2 - r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]


def draw_round(canvas, x1, y1, x2, y2, r, fill, outline=None, tags=()):
    return canvas.create_polygon(round_points(x1, y1, x2, y2, r), smooth=True, fill=fill,
                                 outline=outline or fill, tags=tags)


class Card(tk.Canvas):
    """Скруглённая панель. Дети кладутся в .inner; auto_height — высота по содержимому."""

    def __init__(self, parent, fill, bg, radius=14, outline=None, auto_height=False, **kw):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, **kw)
        self.fill, self.outline, self.radius, self.auto = fill, outline, radius, auto_height
        self.pad = radius // 3 + 2      # чтобы прямоугольный inner не торчал за скруглением
        self.inner = tk.Frame(self, bg=fill)
        self.win = self.create_window(self.pad, self.pad, window=self.inner, anchor="nw")
        self.bind("<Configure>", self._layout)
        if auto_height:
            self.inner.bind("<Configure>", lambda e: self._sync_height())
            self.after_idle(self._sync_height)

    def _sync_height(self):
        if self.inner.winfo_exists():
            h = self.inner.winfo_reqheight() + 2 * self.pad
            if int(self.cget("height")) != h:
                self.configure(height=h)

    def _layout(self, e):
        self.delete("shape")
        r = min(self.radius, e.width // 2, e.height // 2)
        draw_round(self, 1, 1, e.width - 1, e.height - 1, r, self.fill, self.outline, "shape")
        self.tag_lower("shape")
        kw = {"width": max(e.width - 2 * self.pad, 1)}
        if not self.auto:
            kw["height"] = max(e.height - 2 * self.pad, 1)
        self.itemconfigure(self.win, **kw)

    def update_colors(self, fill=None, bg=None, outline=None):
        if fill is not None:
            self.fill = fill
            if self.inner.winfo_exists():
                self.inner.configure(bg=fill)
        if bg is not None:
            self.configure(bg=bg)
        if outline is not None:
            self.outline = outline
        w, h = self.winfo_width(), self.winfo_height()
        if w > 2 and h > 2:
            self.delete("shape")
            r = min(self.radius, w // 2, h // 2)
            draw_round(self, 1, 1, w - 1, h - 1, r, self.fill, self.outline, "shape")
            self.tag_lower("shape")


class Chip(tk.Canvas):
    """Кнопка-«таблетка» с плавной анимацией наведения, микро-нажатием и обновлением темы."""

    def __init__(self, parent, app, text, selected, command, font=None, dot=None, bg=None,
                 color=None, fg=None):
        pal = app.pal
        self.app = app
        self.cmd = command
        self._font = font or app.f_base
        self.dot = dot
        self.raw_color = color
        self.custom_fg = fg
        self.custom_bg = bg
        self._text = text
        self._pressed = False
        self._anim_timer = None
        self._hovered = False

        dot_w = 22 if dot else 0
        w = self._font.measure(text) + 28 + dot_w
        h = self._font.metrics("linespace") + 14
        super().__init__(parent, width=w, height=h, bg=bg or pal["card"], highlightthickness=0,
                         bd=0, cursor="hand2")
        self.selected = selected
        self._calc_colors()
        self._current_color = self.base

        self.shape = draw_round(self, 1, 1, w - 1, h - 1, h // 2, self.base)
        x = 14
        if dot:
            dot(self, x + 7, h // 2)
            x += dot_w
        text_fill = fg or (pal["on_accent"] if (selected or color) else pal["text"])
        self.text_id = self.create_text(x, h // 2, text=text, anchor="w", font=self._font, fill=text_fill)

        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _calc_colors(self):
        pal = self.app.pal
        if self.raw_color:
            self.base = self.raw_color
            self.hot = pal["border"]
        else:
            self.base = pal["accent"] if self.selected else pal["card_hi"]
            self.hot = pal["accent"] if self.selected else pal["border"]

    def _paint(self, color):
        self._current_color = color
        self.itemconfigure(self.shape, fill=color, outline=color)

    def _animate_to(self, target_color, steps=6, interval=16):
        if self._anim_timer:
            self.after_cancel(self._anim_timer)
            self._anim_timer = None
        start_color = self._current_color
        if start_color.lower() == target_color.lower():
            return

        def _step(step):
            if not self.winfo_exists():
                return
            t = step / steps
            eased = 1.0 - (1.0 - t) ** 3
            c = lerp_color(start_color, target_color, eased)
            self._paint(c)
            if step < steps:
                self._anim_timer = self.after(interval, lambda: _step(step + 1))
            else:
                self._anim_timer = None
                self._paint(target_color)

        self._anim_timer = self.after(interval, lambda: _step(1))

    def _on_enter(self, e):
        self._hovered = True
        self._animate_to(self.hot)

    def _on_leave(self, e):
        self._hovered = False
        if self._pressed:
            self.move("all", 0, -1)
            self._pressed = False
        self._animate_to(self.base)

    def _on_press(self, e):
        if not self._pressed:
            self._pressed = True
            self.move("all", 0, 1)

    def _on_release(self, e):
        if self._pressed:
            self.move("all", 0, -1)
            self._pressed = False
            w = self.winfo_width()
            h = self.winfo_height()
            inside = (0 <= e.x <= w and 0 <= e.y <= h) if (e and hasattr(e, "x")) else True
            if inside and self.cmd:
                self.cmd()

    def set_selected(self, selected: bool):
        self.selected = selected
        self._calc_colors()
        pal = self.app.pal
        text_fill = self.custom_fg or (pal["on_accent"] if (selected or self.raw_color) else pal["text"])
        self.itemconfigure(self.text_id, fill=text_fill)
        target = self.hot if self._hovered else self.base
        self._animate_to(target)

    def set_text(self, text: str):
        self._text = text
        dot_w = 22 if self.dot else 0
        w = self._font.measure(text) + 28 + dot_w
        h = self._font.metrics("linespace") + 14
        self.configure(width=w, height=h)
        self.delete("all")
        self.shape = draw_round(self, 1, 1, w - 1, h - 1, h // 2, self._current_color)
        x = 14
        if self.dot:
            self.dot(self, x + 7, h // 2)
            x += dot_w
        pal = self.app.pal
        text_fill = self.custom_fg or (pal["on_accent"] if (self.selected or self.raw_color) else pal["text"])
        self.text_id = self.create_text(x, h // 2, text=text, anchor="w", font=self._font, fill=text_fill)

    def update_theme(self, pal=None, bg=None):
        if not self.winfo_exists():
            return
        if self._anim_timer:
            self.after_cancel(self._anim_timer)
            self._anim_timer = None
        pal = pal or self.app.pal
        if bg or self.custom_bg:
            self.configure(bg=bg or self.custom_bg or pal["card"])
        self._calc_colors()
        self._current_color = self.hot if self._hovered else self.base
        self._paint(self._current_color)
        text_fill = self.custom_fg or (pal["on_accent"] if (self.selected or self.raw_color) else pal["text"])
        self.itemconfigure(self.text_id, fill=text_fill, font=self._font)


class Swatch(tk.Canvas):
    def __init__(self, parent, app, color, selected, command, bg=None):
        pal = app.pal
        self.app, self.color, self.selected, self.cmd = app, color, selected, command
        self._pressed = False
        super().__init__(parent, width=30, height=30, bg=bg or pal["card"], highlightthickness=0,
                         bd=0, cursor="hand2")
        self.draw()
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Leave>", self._on_leave)

    def draw(self):
        pal = self.app.pal
        self.delete("all")
        if self.selected:
            self.create_oval(1, 1, 29, 29, outline=pal["accent"], width=2)
        self.create_oval(6, 6, 24, 24, fill=self.color, outline=pal["border"])

    def _on_press(self, e):
        if not self._pressed:
            self._pressed = True
            self.move("all", 0, 1)

    def _on_release(self, e):
        if self._pressed:
            self.move("all", 0, -1)
            self._pressed = False
            w = self.winfo_width()
            h = self.winfo_height()
            inside = (0 <= e.x <= w and 0 <= e.y <= h) if (e and hasattr(e, "x")) else True
            if inside and self.cmd:
                self.cmd()

    def _on_leave(self, e):
        if self._pressed:
            self.move("all", 0, -1)
            self._pressed = False

    def set_selected(self, selected: bool):
        self.selected = selected
        self.draw()

    def update_theme(self, bg=None):
        if not self.winfo_exists():
            return
        self.configure(bg=bg or self.app.pal["card"])
        self.draw()


class Flow(tk.Frame):
    """Раскладывает детей в строки с переносом по ширине."""

    def __init__(self, parent, bg, gap=8):
        super().__init__(parent, bg=bg, height=1)
        self.gap, self.items = gap, []
        self.bind("<Configure>", lambda e: self.relayout(e.width))

    def add(self, widget):
        self.items.append(widget)
        self.after_idle(self.relayout)

    def relayout(self, width=None):
        width = width or self.winfo_width()
        if width <= 1:
            return
        x = y = row_h = 0
        for w in self.items:
            ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
            if x and x + ww > width:
                x, y, row_h = 0, y + row_h + self.gap, 0
            w.place(x=x, y=y)
            x += ww + self.gap
            row_h = max(row_h, wh)
        self.configure(height=max(y + row_h, 1))


class AutoScrollbar(ttk.Scrollbar):
    """Тонкий скроллбар поверх содержимого; прячется, если прокручивать нечего."""

    def __init__(self, parent, place_kw, style):
        super().__init__(parent, orient="vertical", style=style)
        self._kw = place_kw

    def set(self, lo, hi):
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.place_forget()
        else:
            self.place(**self._kw)
        super().set(lo, hi)


class ScrollFrame(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=app.pal["bg"])
        self.canvas = tk.Canvas(self, bg=app.pal["bg"], highlightthickness=0, bd=0, yscrollincrement=20)
        self.canvas.pack(fill="both", expand=True)
        self.bar = AutoScrollbar(self, dict(relx=1.0, x=-2, rely=0, relheight=1.0, width=8, anchor="ne"),
                                 "Flat.Vertical.TScrollbar")
        self.bar.configure(command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.body = tk.Frame(self.canvas, bg=app.pal["bg"])
        self.win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

    def needs_scroll(self):
        return self.body.winfo_reqheight() > self.canvas.winfo_height()


class PlaceholderEntry(tk.Entry):
    def __init__(self, parent, app, placeholder, bg, initial="", is_password=False):
        pal = app.pal
        self.app = app
        self.is_password = is_password
        super().__init__(parent, bg=bg, fg=pal["text"], insertbackground=pal["text"], relief="flat",
                         bd=0, highlightthickness=0, font=app.f_base, selectbackground=pal["accent"],
                         selectforeground=pal["on_accent"])
        self.placeholder, self.dim, self.fg, self.shown = placeholder, pal["dim"], pal["text"], False
        if initial:
            self.insert(0, initial)
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", lambda e: self._show())
        self.bind("<Button-1>", lambda e: self.focus_force())
        self._show()

    def update_theme(self, pal=None, bg=None):
        if not self.winfo_exists():
            return
        pal = pal or self.app.pal
        self.dim = pal["dim"]
        self.fg = pal["text"]
        kw = {
            "fg": self.dim if self.shown else self.fg,
            "insertbackground": pal["text"],
            "selectbackground": pal["accent"],
            "selectforeground": pal["on_accent"],
            "font": self.app.f_base,
        }
        if bg is not None:
            kw["bg"] = bg
        self.configure(**kw)

    def _show(self):
        if not self.get():
            self.shown = True
            self.configure(fg=self.dim, show="")
            self.insert(0, self.placeholder)
        else:
            if self.is_password:
                self.configure(show="•")

    def _focus_in(self, _e):
        if self.shown:
            self.delete(0, "end")
            self.configure(fg=self.fg, show="•" if self.is_password else "")
            self.shown = False
        else:
            if self.is_password:
                self.configure(show="•")

    def value(self):
        return "" if self.shown else self.get().strip()

    def clear(self):
        self.delete(0, "end")
        self.shown = False
        self.configure(fg=self.fg, show="•" if self.is_password else "")
        if self.focus_get() is not self:
            self._show()

    def set_text(self, val: str):
        self.delete(0, "end")
        self.shown = False
        self.configure(fg=self.fg, show="•" if self.is_password else "")
        if val:
            self.insert(0, str(val))
        else:
            if self.focus_get() is not self:
                self._show()


class NavItem(tk.Canvas):
    def __init__(self, parent, app, key, icon, label):
        h = max(42, app.f_base.metrics("linespace") + 22)
        super().__init__(parent, height=h, bg=app.pal["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.app, self.key, self.icon, self.label = app, key, icon, label
        self.hover = False
        self._pressed = False
        self._anim_timer = None
        self._cur_fill = app.pal["bg"]

        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_enter(self, e):
        self.hover = True
        if self.app.tab != self.key:
            self._animate_fill(self.app.pal["card_hi"])
        else:
            self.draw()

    def _on_leave(self, e):
        self.hover = False
        if self._pressed:
            self.move("all", 0, -1)
            self._pressed = False
        if self.app.tab != self.key:
            self._animate_fill(self.app.pal["bg"])
        else:
            self.draw()

    def _on_press(self, e):
        if not self._pressed:
            self._pressed = True
            self.move("all", 0, 1)

    def _on_release(self, e):
        if self._pressed:
            self.move("all", 0, -1)
            self._pressed = False
            w = self.winfo_width()
            h = self.winfo_height()
            inside = (0 <= e.x <= w and 0 <= e.y <= h) if (e and hasattr(e, "x")) else True
            if inside:
                self.app.show_tab(self.key)

    def _animate_fill(self, target_color, steps=5, interval=16):
        if self._anim_timer:
            self.after_cancel(self._anim_timer)
            self._anim_timer = None
        start_color = self._cur_fill
        if start_color.lower() == target_color.lower():
            return

        def _step(step):
            if not self.winfo_exists():
                return
            t = step / steps
            eased = 1.0 - (1.0 - t) ** 2
            c = lerp_color(start_color, target_color, eased)
            self._cur_fill = c
            self._render_with_fill(c)
            if step < steps:
                self._anim_timer = self.after(interval, lambda: _step(step + 1))
            else:
                self._anim_timer = None
                self._cur_fill = target_color
                self._render_with_fill(target_color)

        self._anim_timer = self.after(interval, lambda: _step(1))

    def _render_with_fill(self, fill):
        app, pal = self.app, self.app.pal
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        active = app.tab == self.key
        draw_round(self, 1, 1, w - 1, h - 1, 12, fill)
        self.create_text(24, h // 2, text=self.icon, font=app.f_icon,
                         fill=pal["accent"] if active else pal["dim"])
        self.create_text(48, h // 2, text=self.label, anchor="w",
                         font=app.f_bold if active else app.f_base,
                         fill=pal["text"] if active else pal["dim"])
        if active:
            self.create_line(w - 6, h // 2 - 11, w - 6, h // 2 + 11, width=3, capstyle="round",
                             fill=pal["accent"])

    def draw(self):
        if self._anim_timer:
            self.after_cancel(self._anim_timer)
            self._anim_timer = None
        app, pal = self.app, self.app.pal
        self.configure(bg=pal["bg"])
        active = app.tab == self.key
        fill = pal["card"] if active else (pal["card_hi"] if self.hover else pal["bg"])
        self._cur_fill = fill
        self._render_with_fill(fill)


class WinButton(tk.Canvas):
    def __init__(self, parent, app, kind, command):
        super().__init__(parent, width=36, height=28, bg=app.pal["bg"], highlightthickness=0, bd=0,
                         cursor="hand2")
        self.app, self.kind, self.cmd = app, kind, command
        self.hover = False
        self._pressed = False
        self._anim_timer = None
        self._cur_fill = app.pal["bg"]

        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.draw(False)

    def _on_enter(self, e):
        self.hover = True
        target = self.app.pal["danger"] if self.kind == "close" else self.app.pal["card_hi"]
        self._animate_to(target)

    def _on_leave(self, e):
        self.hover = False
        if self._pressed:
            self.move("all", 0, -1)
            self._pressed = False
        self._animate_to(self.app.pal["bg"])

    def _on_press(self, e):
        if not self._pressed:
            self._pressed = True
            self.move("all", 0, 1)

    def _on_release(self, e):
        if self._pressed:
            self.move("all", 0, -1)
            self._pressed = False
            w = self.winfo_width()
            h = self.winfo_height()
            inside = (0 <= e.x <= w and 0 <= e.y <= h) if (e and hasattr(e, "x")) else True
            if inside and self.cmd:
                self.cmd()

    def _animate_to(self, target_color, steps=5, interval=16):
        if self._anim_timer:
            self.after_cancel(self._anim_timer)
            self._anim_timer = None
        start_color = self._cur_fill
        if start_color.lower() == target_color.lower():
            return

        def _step(step):
            if not self.winfo_exists():
                return
            t = step / steps
            eased = 1.0 - (1.0 - t) ** 2
            c = lerp_color(start_color, target_color, eased)
            self._cur_fill = c
            self._render_with_fill(c)
            if step < steps:
                self._anim_timer = self.after(interval, lambda: _step(step + 1))
            else:
                self._anim_timer = None
                self._cur_fill = target_color
                self._render_with_fill(target_color)

        self._anim_timer = self.after(interval, lambda: _step(1))

    def _render_with_fill(self, fill_color):
        pal = self.app.pal
        self.delete("all")
        is_bg = fill_color.lower() == pal["bg"].lower()
        if not is_bg:
            draw_round(self, 1, 1, 35, 27, 8, fill_color)

        if self.kind == "close" and not is_bg:
            fg = "#ffffff"
        elif self.hover:
            fg = pal["text"]
        else:
            fg = pal["dim"]

        if self.kind == "min":
            self.create_line(12, 14, 24, 14, fill=fg, width=2)
        elif self.kind == "max":
            self.create_rectangle(13, 9, 23, 19, outline=fg, width=2)
        else:
            self.create_line(13, 9, 23, 19, fill=fg, width=2)
            self.create_line(23, 9, 13, 19, fill=fg, width=2)

    def draw(self, hover=False):
        if self._anim_timer:
            self.after_cancel(self._anim_timer)
            self._anim_timer = None
        pal = self.app.pal
        self.configure(bg=pal["bg"])
        self.hover = hover
        if hover:
            hot = pal["danger"] if self.kind == "close" else pal["card_hi"]
            self._cur_fill = hot
        else:
            self._cur_fill = pal["bg"]
        self._render_with_fill(self._cur_fill)


def draw_theme_dot(canvas, cx, cy, key, pal):
    box = (cx - 7, cy - 7, cx + 7, cy + 7)
    if key == "system":
        canvas.create_arc(*box, start=90, extent=180, fill="#1c1c22", outline="")
        canvas.create_arc(*box, start=270, extent=180, fill="#f2f2f6", outline="")
        canvas.create_oval(*box, outline=pal["border"])
    else:
        canvas.create_oval(*box, fill=THEMES[key]["dot"], outline=pal["border"])


class ChatItem(tk.Frame):
    def __init__(self, parent, app, chat, is_active, is_unread, on_click):
        self.app = app
        self.chat = chat
        self.chat_id = chat.get("id", "general")
        self.on_click = on_click
        pal = app.pal
        self.is_active = is_active
        self.is_unread = is_unread
        self.bg = pal["card_hi"] if is_active else pal["card"]
        super().__init__(parent, bg=self.bg, cursor="hand2")
        self.pack(fill="x", padx=4, pady=2)

        c_type = chat.get("type", "group")
        icon = "🌐" if c_type == "global" else ("💬" if c_type == "direct" else "👥")
        self.lbl_icon = tk.Label(self, text=icon, bg=self.bg, fg=pal["text"], font=app.f_base, cursor="hand2")
        self.lbl_icon.pack(side="left", padx=(8, 4), pady=5)

        name = chat.get("name", "Чат")
        if len(name) > 14:
            name = name[:13] + "…"
        fg = pal["accent"] if is_active else pal["text"]
        f_font = app.f_bold if (is_active or is_unread) else app.f_base
        self.lbl_name = tk.Label(self, text=name, bg=self.bg, fg=fg, font=f_font, anchor="w", cursor="hand2")
        self.lbl_name.pack(side="left", fill="x", expand=True, pady=5)

        if is_unread:
            self.badge = tk.Label(self, text="●", bg=self.bg, fg=pal["accent"], font=app.f_bold, cursor="hand2")
            self.badge.pack(side="right", padx=(0, 6))

        bind_targets = [self, self.lbl_icon, self.lbl_name]
        if is_unread and hasattr(self, "badge"):
            bind_targets.append(self.badge)
        for w in bind_targets:
            w.bind("<Button-1>", lambda e: self.on_click(self.chat_id))
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)

    def _on_enter(self, _e):
        if not self.is_active:
            hover_bg = self.app.pal["card_hi"]
            for w in [self, self.lbl_icon, self.lbl_name] + ([self.badge] if hasattr(self, "badge") else []):
                w.configure(bg=hover_bg)

    def _on_leave(self, _e):
        if not self.is_active:
            for w in [self, self.lbl_icon, self.lbl_name] + ([self.badge] if hasattr(self, "badge") else []):
                w.configure(bg=self.bg)


# --------------------------------------------------------------------------- #
# Приложение                                                                  #
# --------------------------------------------------------------------------- #

class App(tk.Tk):
    def __init__(self, host: str | None = None, port: int | None = None):
        super().__init__()

        # ---- адаптивность под размер/DPI экрана --------------------------- #
        try:
            dpi = float(self.winfo_fpixels("1i"))
        except Exception:
            dpi = 96.0
        self.scale = max(0.75, min(2.5, dpi / 96.0))
        try:
            self.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass
        # Реальные (масштабированные) размеры окна и его "хитбоксов" —
        # используются вместо модульных констант везде в этом классе.
        self.MIN_W = round(MIN_W * self.scale)
        self.MIN_H = round(MIN_H * self.scale)
        self.RING = max(6, round(RING * self.scale))
        self.CORNER = max(14, round(CORNER * self.scale))
        self.RADIUS = max(10, round(RADIUS * self.scale))
        self._resize_after = None

        self.cfg = load_settings()
        cfg_h, cfg_p = parse_host_port(self.cfg.get("server_addr", f"{DEFAULT_HOST}:{DEFAULT_PORT}"))
        self.host = host or cfg_h or DEFAULT_HOST
        self.port = port or cfg_p or DEFAULT_PORT
        self.cfg["server_addr"] = f"{self.host}:{self.port}"
        self.events: queue.Queue = queue.Queue()
        self.chan = None
        self.conn_id = 0
        self.conn_state = "offline"              # offline | connecting | online
        self.name = self.cfg.get("name", "")
        self.history: list[dict] = []
        self.tab = self.cfg.get("last_tab", "cheats")
        self.auth_mode = "login"                 # login | register
        self.connect_text, self.connect_error = "", False
        self.maximized = False
        self._normal_geometry = ""
        self.body = None

        # Управление чатами и сообщениями
        self.chats: list[dict] = [
            {"id": "general", "name": "Общий чат", "type": "global", "members": []}
        ]
        self.current_chat_id = "general"
        self.chat_histories: dict[str, list[dict]] = {"general": []}
        self.unread_chats: set[str] = set()
        self.all_users: list[str] = []
        self.chat_item_widgets: list[ChatItem] = []
        self.selected_group_members: set[str] = set()
        self.create_chat_mode = "direct"
        self.chat_text = self.overlay = self.settings_scroll = self.cheats_scroll = None
        self._settings_y = 0.0
        self._cheats_y = 0.0
        self._edge = None

        # Офлайн режим и сервер
        self.offline_dismissed = self.cfg.get("offline_mode", True)
        self.embedded_server_running = False

        # Читы и модификации
        self.cheats: list[dict] = load_cheats()
        self.running_processes: dict[str, subprocess.Popen] = {}
        self.cheats_filter_game = "Все"
        self.editing_cheat_id = None
        self._toast_timer = None

        self.title("CheatChat")
        self.overrideredirect(True)
        self.configure(bg=KEY)
        try:
            self.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(self, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._draw_panel())
        self.canvas.bind("<Motion>", self._edge_hover)
        self.canvas.bind("<ButtonPress-1>", self._edge_press)
        self.canvas.bind("<B1-Motion>", self._edge_drag)

        self._place_window()
        self.rebuild()
        self.bind("<Configure>", self._on_root_resize)
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(50, self._pump)
        self.after(1000, self._check_running_processes)
        self.after(3000, self._poll_system)
        self.after(200, self._fix_taskbar)

    # ---- тема, шрифты ------------------------------------------------------ #

    def resolve(self):
        key = self.cfg["theme"]
        if key == "system" or key not in THEMES:
            key = "light" if (key == "system" and system_is_light()) else "dark"
        self.theme_key = key
        self.pal = dict(THEMES[key])
        if self.cfg["text_color"]:
            self.pal["text"] = self.cfg["text_color"]

    def make_fonts(self):
        fams = set(tkfont.families())
        avail = [f for f in FONT_CHOICES if f in fams] or [tkfont.nametofont("TkDefaultFont").actual("family")]
        self.font_choices = avail
        fam = self.cfg["font"] if self.cfg["font"] in fams else avail[0]
        size = max(FONT_MIN, min(FONT_MAX, int(self.cfg["font_size"])))
        self.f_base = tkfont.Font(family=fam, size=size)
        self.f_bold = tkfont.Font(family=fam, size=size, weight="bold")
        self.f_small = tkfont.Font(family=fam, size=max(size - 2, 7))
        self.f_italic = tkfont.Font(family=fam, size=size, slant="italic")
        self.f_title = tkfont.Font(family=fam, size=size + 5, weight="bold")
        self.f_brand = tkfont.Font(family=fam, size=size + 2, weight="bold")
        icon_fam = "Segoe MDL2 Assets" if "Segoe MDL2 Assets" in fams else fam
        self.mdl2 = icon_fam == "Segoe MDL2 Assets"
        self.f_icon = tkfont.Font(family=icon_fam, size=size + 2)

    def setup_ttk(self):
        pal = self.pal
        st = ttk.Style(self)
        st.theme_use("clam")
        for name, trough in (("Flat", pal["bg"]), ("Card", pal["card"])):
            style = f"{name}.Vertical.TScrollbar"
            st.layout(style, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
            st.configure(style, background=pal["border"], troughcolor=trough, bordercolor=trough,
                         lightcolor=pal["border"], darkcolor=pal["border"], gripcount=0)
            st.map(style, background=[("active", pal["accent"]), ("pressed", pal["accent"])])

    def update_fonts_live(self):
        fams = set(tkfont.families())
        avail = [f for f in FONT_CHOICES if f in fams] or [tkfont.nametofont("TkDefaultFont").actual("family")]
        self.font_choices = avail
        fam = self.cfg["font"] if self.cfg["font"] in fams else avail[0]
        size = max(FONT_MIN, min(FONT_MAX, int(self.cfg["font_size"])))
        self.f_base.configure(family=fam, size=size)
        self.f_bold.configure(family=fam, size=size, weight="bold")
        self.f_small.configure(family=fam, size=max(size - 2, 7))
        self.f_italic.configure(family=fam, size=size, slant="italic")
        self.f_title.configure(family=fam, size=size + 5, weight="bold")
        self.f_brand.configure(family=fam, size=size + 2, weight="bold")
        icon_fam = "Segoe MDL2 Assets" if "Segoe MDL2 Assets" in fams else fam
        self.mdl2 = icon_fam == "Segoe MDL2 Assets"
        self.f_icon.configure(family=icon_fam, size=size + 2)

    def set_cfg(self, **kw):
        font_changed = any(k in kw for k in ("font", "font_size"))
        self.cfg.update(kw)
        save_settings(self.cfg)
        if font_changed:
            self.update_fonts_live()
        self.resolve()
        self.setup_ttk()
        self.apply_theme_live()

    def _render_send_btn(self, hover=False):
        pal = self.pal
        if not hasattr(self, "chat_send_btn") or not self.chat_send_btn.winfo_exists():
            return
        c = self.chat_send_btn
        c.delete("all")
        bg_col = pal["border"] if hover else pal["accent"]
        c.create_oval(1, 1, 35, 35, fill=bg_col, outline=bg_col)
        c.create_polygon(11, 11, 27, 18, 11, 25, 15, 18, fill=pal["on_accent"], outline=pal["on_accent"])

    def _on_send_press(self, e):
        if hasattr(self, "chat_send_btn") and self.chat_send_btn.winfo_exists():
            self.chat_send_btn.move("all", 0, 1)

    def _on_send_release(self, e):
        if hasattr(self, "chat_send_btn") and self.chat_send_btn.winfo_exists():
            self.chat_send_btn.move("all", 0, -1)
            w, h = self.chat_send_btn.winfo_width(), self.chat_send_btn.winfo_height()
            if 0 <= e.x <= w and 0 <= e.y <= h:
                self.send_message()

    def _update_overlay_theme(self):
        pal = self.pal
        if not hasattr(self, "overlay") or not self.overlay.winfo_exists():
            return
        self.overlay.update_colors(fill=pal["card"], bg=pal["bg"], outline=pal["accent"])
        if hasattr(self, "overlay_hdr") and self.overlay_hdr.winfo_exists():
            self.overlay_hdr.configure(bg=pal["card"])
        if hasattr(self, "lbl_overlay_title") and self.lbl_overlay_title.winfo_exists():
            self.lbl_overlay_title.configure(bg=pal["card"], fg=pal["text"])
        if hasattr(self, "lbl_overlay_close") and self.lbl_overlay_close.winfo_exists():
            self.lbl_overlay_close.configure(bg=pal["card"], fg=pal["dim"])
        if hasattr(self, "lbl_overlay_sub") and self.lbl_overlay_sub.winfo_exists():
            self.lbl_overlay_sub.configure(bg=pal["card"], fg=pal["dim"])
        if hasattr(self, "lbl_overlay_name") and self.lbl_overlay_name.winfo_exists():
            self.lbl_overlay_name.configure(bg=pal["card"], fg=pal["text"])
        if hasattr(self, "card_overlay_name") and self.card_overlay_name.winfo_exists():
            self.card_overlay_name.update_colors(fill=pal["card_hi"], bg=pal["card"])
        if hasattr(self, "name_entry") and self.name_entry.winfo_exists():
            self.name_entry.update_theme(pal=pal, bg=pal["card_hi"])
        if hasattr(self, "lbl_overlay_pwd") and self.lbl_overlay_pwd.winfo_exists():
            self.lbl_overlay_pwd.configure(bg=pal["card"], fg=pal["text"])
        if hasattr(self, "card_overlay_pwd") and self.card_overlay_pwd.winfo_exists():
            self.card_overlay_pwd.update_colors(fill=pal["card_hi"], bg=pal["card"])
        if hasattr(self, "password_entry") and self.password_entry.winfo_exists():
            self.password_entry.update_theme(pal=pal, bg=pal["card_hi"])
        if hasattr(self, "auth_mode_row") and self.auth_mode_row.winfo_exists():
            self.auth_mode_row.configure(bg=pal["card"])
        for c in getattr(self, "auth_mode_chips", []):
            c.update_theme(pal=pal, bg=pal["card"])
        if hasattr(self, "lbl_overlay_srv") and self.lbl_overlay_srv.winfo_exists():
            self.lbl_overlay_srv.configure(bg=pal["card"], fg=pal["text"])
        if hasattr(self, "card_overlay_srv") and self.card_overlay_srv.winfo_exists():
            self.card_overlay_srv.update_colors(fill=pal["card_hi"], bg=pal["card"])
        if hasattr(self, "server_entry") and self.server_entry.winfo_exists():
            self.server_entry.update_theme(pal=pal, bg=pal["card_hi"])
        if hasattr(self, "lbl_connect") and self.lbl_connect.winfo_exists():
            is_ok = not self.connect_error and ("успешн" in self.connect_text.lower() or "✓" in self.connect_text)
            color = pal["ok"] if is_ok else (pal["danger"] if self.connect_error else pal["dim"])
            self.lbl_connect.configure(bg=pal["card"], fg=color)
        if hasattr(self, "overlay_btn_row") and self.overlay_btn_row.winfo_exists():
            self.overlay_btn_row.configure(bg=pal["card"])
        for c in getattr(self, "overlay_chips", []):
            c.update_theme(pal=pal, bg=pal["card"])

    def _update_cheat_modal_theme(self):
        pal = self.pal
        if not hasattr(self, "cheat_modal") or not self.cheat_modal.winfo_exists():
            return
        self.cheat_modal.update_colors(fill=pal["card"], bg=pal["bg"], outline=pal["accent"])
        for fr in (getattr(self, "modal_hdr", None), getattr(self, "modal_row_gv", None),
                   getattr(self, "modal_col_g", None), getattr(self, "modal_col_v", None),
                   getattr(self, "modal_btn_bar", None)):
            if fr and fr.winfo_exists():
                fr.configure(bg=pal["card"])
        for lbl in (getattr(self, "lbl_modal_title", None), getattr(self, "lbl_m_name", None),
                    getattr(self, "lbl_m_game", None), getattr(self, "lbl_m_ver", None),
                    getattr(self, "lbl_m_path", None), getattr(self, "lbl_m_args", None),
                    getattr(self, "lbl_m_notes", None)):
            if lbl and lbl.winfo_exists():
                lbl.configure(bg=pal["card"], fg=pal["text"])
        for lbl in (getattr(self, "lbl_modal_close", None), getattr(self, "lbl_modal_subtitle", None)):
            if lbl and lbl.winfo_exists():
                lbl.configure(bg=pal["card"], fg=pal["dim"])
        if hasattr(self, "lbl_modal_err") and self.lbl_modal_err.winfo_exists():
            self.lbl_modal_err.configure(bg=pal["card"], fg=pal["danger"])

        for c in (getattr(self, "w_m_name", None), getattr(self, "w_m_game", None),
                  getattr(self, "w_m_ver", None), getattr(self, "w_m_path", None),
                  getattr(self, "w_m_args", None), getattr(self, "w_m_notes", None)):
            if c and c.winfo_exists():
                c.update_colors(fill=pal["card_hi"], bg=pal["card"])

        for ent in (getattr(self, "modal_name_entry", None), getattr(self, "modal_game_entry", None),
                    getattr(self, "modal_version_entry", None), getattr(self, "modal_path_entry", None),
                    getattr(self, "modal_args_entry", None), getattr(self, "modal_notes_entry", None)):
            if ent and ent.winfo_exists():
                ent.update_theme(pal=pal, bg=pal["card_hi"])

        if hasattr(self, "modal_chip_browse") and self.modal_chip_browse.winfo_exists():
            self.modal_chip_browse.update_theme(pal=pal, bg=pal["card_hi"])
        if hasattr(self, "modal_del_chip") and self.modal_del_chip.winfo_exists():
            self.modal_del_chip.update_theme(pal=pal, bg=pal["card"])
        if hasattr(self, "modal_chip_cancel") and self.modal_chip_cancel.winfo_exists():
            self.modal_chip_cancel.update_theme(pal=pal, bg=pal["card"])
        if hasattr(self, "modal_chip_save") and self.modal_chip_save.winfo_exists():
            self.modal_chip_save.update_theme(pal=pal, bg=pal["card"])

    def _update_create_chat_modal_theme(self):
        pal = self.pal
        if not hasattr(self, "create_chat_overlay") or not self.create_chat_overlay.winfo_exists():
            return
        self.create_chat_overlay.update_colors(fill=pal["card"], bg=pal["bg"], outline=pal["accent"])
        for fr in (getattr(self, "create_chat_modal_hdr", None), getattr(self, "create_mode_row", None),
                   getattr(self, "create_chat_body", None)):
            if fr and fr.winfo_exists():
                fr.configure(bg=pal["card"])
        if hasattr(self, "lbl_modal_create_title") and self.lbl_modal_create_title.winfo_exists():
            self.lbl_modal_create_title.configure(bg=pal["card"], fg=pal["text"])
        if hasattr(self, "lbl_modal_create_close") and self.lbl_modal_create_close.winfo_exists():
            self.lbl_modal_create_close.configure(bg=pal["card"], fg=pal["dim"])
        if hasattr(self, "chip_create_direct") and self.chip_create_direct.winfo_exists():
            self.chip_create_direct.update_theme(pal=pal, bg=pal["card"])
        if hasattr(self, "chip_create_group") and self.chip_create_group.winfo_exists():
            self.chip_create_group.update_theme(pal=pal, bg=pal["card"])
        if hasattr(self, "lbl_grp_name") and self.lbl_grp_name.winfo_exists():
            self.lbl_grp_name.configure(bg=pal["card"], fg=pal["text"])
        if hasattr(self, "card_grp_name") and self.card_grp_name.winfo_exists():
            self.card_grp_name.update_colors(fill=pal["card_hi"], bg=pal["card"])
        if hasattr(self, "entry_grp_name") and self.entry_grp_name.winfo_exists():
            self.entry_grp_name.update_theme(pal=pal, bg=pal["card_hi"])
        if hasattr(self, "lbl_select_users") and self.lbl_select_users.winfo_exists():
            self.lbl_select_users.configure(bg=pal["card"], fg=pal["text"])
        if hasattr(self, "users_scroll_card") and self.users_scroll_card.winfo_exists():
            self.users_scroll_card.update_colors(fill=pal["card_hi"], bg=pal["card"])
        if hasattr(self, "users_scroll") and self.users_scroll.winfo_exists():
            self.users_scroll.configure(bg=pal["card_hi"])
            self.users_scroll.canvas.configure(bg=pal["card_hi"])
            self.users_scroll.body.configure(bg=pal["card_hi"])
        if hasattr(self, "btn_create_group_submit") and self.btn_create_group_submit.winfo_exists():
            self.btn_create_group_submit.update_theme(pal=pal, bg=pal["card"])
        self.render_create_modal_users()

    def apply_theme_live(self):
        pal = self.pal
        # 1. Рамка и фон окна
        self._draw_panel()

        # 2. Основные контейнеры
        if hasattr(self, "body") and self.body.winfo_exists():
            self.body.configure(bg=pal["bg"])
        if hasattr(self, "sidebar_divider") and self.sidebar_divider.winfo_exists():
            self.sidebar_divider.configure(bg=pal["border"])
        if hasattr(self, "content_frame") and self.content_frame.winfo_exists():
            self.content_frame.configure(bg=pal["bg"])
        if hasattr(self, "header_frame") and self.header_frame.winfo_exists():
            self.header_frame.configure(bg=pal["bg"])
        if hasattr(self, "lbl_title") and self.lbl_title.winfo_exists():
            self.lbl_title.configure(bg=pal["bg"], fg=pal["text"])
        if hasattr(self, "pages_frame") and self.pages_frame.winfo_exists():
            self.pages_frame.configure(bg=pal["bg"])

        # 3. Кнопки управления окном
        for wb in getattr(self, "win_buttons", []):
            wb.draw(False)

        # 4. Боковая панель
        if hasattr(self, "sidebar_frame") and self.sidebar_frame.winfo_exists():
            self.sidebar_frame.configure(bg=pal["bg"])
        if hasattr(self, "profile_frame") and self.profile_frame.winfo_exists():
            self.profile_frame.configure(bg=pal["bg"])
        if hasattr(self, "profile_texts") and self.profile_texts.winfo_exists():
            self.profile_texts.configure(bg=pal["bg"])
        if hasattr(self, "lbl_name") and self.lbl_name.winfo_exists():
            self.lbl_name.configure(bg=pal["bg"], fg=pal["text"])
        if hasattr(self, "lbl_status") and self.lbl_status.winfo_exists():
            self.lbl_status.configure(bg=pal["bg"])
        if hasattr(self, "brand_frame") and self.brand_frame.winfo_exists():
            self.brand_frame.configure(bg=pal["bg"])
        if hasattr(self, "lbl_brand_name") and self.lbl_brand_name.winfo_exists():
            self.lbl_brand_name.configure(bg=pal["bg"], fg=pal["text"])
        if hasattr(self, "logo_canvas") and self.logo_canvas.winfo_exists():
            self.logo_canvas.configure(bg=pal["bg"])
            self.logo_canvas.delete("all")
            self.logo_canvas.create_polygon(16, 2, 28, 9, 28, 23, 16, 30, 4, 23, 4, 9, fill=pal["accent"], outline=pal["accent"])
            for pts in ((16, 16, 4, 9), (16, 16, 28, 9), (16, 16, 16, 30)):
                self.logo_canvas.create_line(*pts, fill=pal["bg"], width=2)
        for item in getattr(self, "nav", []):
            item.draw()
        self.update_state_ui()

        # 5. Чат
        if hasattr(self, "page_chat") and self.page_chat.winfo_exists():
            self.page_chat.configure(bg=pal["bg"])
        if hasattr(self, "chat_banner") and self.chat_banner.winfo_exists():
            self.chat_banner.update_colors(fill=pal["card_hi"], bg=pal["bg"])
        if hasattr(self, "chat_banner_row") and self.chat_banner_row.winfo_exists():
            self.chat_banner_row.configure(bg=pal["card_hi"])
        if hasattr(self, "lbl_chat_banner") and self.lbl_chat_banner.winfo_exists():
            self.lbl_chat_banner.configure(bg=pal["card_hi"], fg=pal["dim"])
        for c in getattr(self, "chat_banner_chips", []):
            c.update_theme(pal=pal, bg=pal["card_hi"])

        if hasattr(self, "chat_panes") and self.chat_panes.winfo_exists():
            self.chat_panes.configure(bg=pal["bg"])
        if hasattr(self, "chats_sidebar_card") and self.chats_sidebar_card.winfo_exists():
            self.chats_sidebar_card.update_colors(fill=pal["card"], bg=pal["bg"])
        if hasattr(self, "lbl_chats_title") and self.lbl_chats_title.winfo_exists():
            self.lbl_chats_title.configure(bg=pal["card"], fg=pal["text"])
        if hasattr(self, "btn_new_chat") and self.btn_new_chat.winfo_exists():
            self.btn_new_chat.update_theme(pal=pal, bg=pal["card"])
        if hasattr(self, "chat_top_bar") and self.chat_top_bar.winfo_exists():
            self.chat_top_bar.configure(bg=pal["card"])
        if hasattr(self, "lbl_active_chat_title") and self.lbl_active_chat_title.winfo_exists():
            self.lbl_active_chat_title.configure(bg=pal["card"], fg=pal["text"])
        if hasattr(self, "lbl_active_chat_sub") and self.lbl_active_chat_sub.winfo_exists():
            self.lbl_active_chat_sub.configure(bg=pal["card"], fg=pal["dim"])
        if hasattr(self, "chat_top_divider") and self.chat_top_divider.winfo_exists():
            self.chat_top_divider.configure(bg=pal["card_hi"])

        if hasattr(self, "chat_bar") and self.chat_bar.winfo_exists():
            self.chat_bar.update_colors(fill=pal["card_hi"], bg=pal["card"])
        if hasattr(self, "msg_entry") and self.msg_entry.winfo_exists():
            self.msg_entry.update_theme(pal=pal, bg=pal["card_hi"])
        if hasattr(self, "chat_send_btn") and self.chat_send_btn.winfo_exists():
            self.chat_send_btn.configure(bg=pal["card_hi"])
            self._render_send_btn(False)
        if hasattr(self, "chat_box") and self.chat_box.winfo_exists():
            self.chat_box.update_colors(fill=pal["card"], bg=pal["bg"])
        if hasattr(self, "chat_text") and self.chat_text.winfo_exists():
            self.chat_text.configure(bg=pal["card"], fg=pal["text"],
                                    selectbackground=pal["accent"], selectforeground=pal["on_accent"])
            self.configure_tags(self.chat_text)
        self.render_chat_list()
        self._update_overlay_theme()
        self._update_create_chat_modal_theme()

        # 6. Читы
        if hasattr(self, "page_cheats") and self.page_cheats.winfo_exists():
            self.page_cheats.configure(bg=pal["bg"])
        if hasattr(self, "cheats_toolbar") and self.cheats_toolbar.winfo_exists():
            self.cheats_toolbar.configure(bg=pal["bg"])
        if hasattr(self, "cheats_left_tools") and self.cheats_left_tools.winfo_exists():
            self.cheats_left_tools.configure(bg=pal["bg"])
        for c in getattr(self, "cheats_toolbar_chips", []):
            c.update_theme(pal=pal, bg=pal["bg"])
        if hasattr(self, "cheats_search_card") and self.cheats_search_card.winfo_exists():
            self.cheats_search_card.update_colors(fill=pal["card"], bg=pal["bg"])
        if hasattr(self, "cheats_search_entry") and self.cheats_search_entry.winfo_exists():
            self.cheats_search_entry.update_theme(pal=pal, bg=pal["card"])
        if hasattr(self, "cheats_sub_bar") and self.cheats_sub_bar.winfo_exists():
            self.cheats_sub_bar.configure(bg=pal["bg"])
        if hasattr(self, "cheats_filter_box") and self.cheats_filter_box.winfo_exists():
            self.cheats_filter_box.configure(bg=pal["bg"])
        if hasattr(self, "btn_filter_all") and self.btn_filter_all.winfo_exists():
            self.btn_filter_all.update_theme(pal=pal, bg=pal["bg"])
        if hasattr(self, "btn_filter_mc") and self.btn_filter_mc.winfo_exists():
            self.btn_filter_mc.update_theme(pal=pal, bg=pal["bg"])
        if hasattr(self, "lbl_cheats_toast") and self.lbl_cheats_toast.winfo_exists():
            self.lbl_cheats_toast.configure(bg=pal["bg"])
        if hasattr(self, "cheats_scroll") and self.cheats_scroll.winfo_exists():
            self.cheats_scroll.configure(bg=pal["bg"])
            self.cheats_scroll.canvas.configure(bg=pal["bg"])
            self.cheats_scroll.body.configure(bg=pal["bg"])
        if hasattr(self, "cheats_cards_container") and self.cheats_cards_container.winfo_exists():
            self.cheats_cards_container.configure(bg=pal["bg"])
            self.refresh_cheats_list()
        if hasattr(self, "modal_backdrop") and self.modal_backdrop.winfo_exists():
            self.modal_backdrop.configure(bg=pal["bg"])
        self._update_cheat_modal_theme()

        # 7. Настройки
        if hasattr(self, "page_settings") and self.page_settings.winfo_exists():
            self.page_settings.configure(bg=pal["bg"])
        if hasattr(self, "settings_scroll") and self.settings_scroll.winfo_exists():
            self.settings_scroll.configure(bg=pal["bg"])
            self.settings_scroll.canvas.configure(bg=pal["bg"])
            self.settings_scroll.body.configure(bg=pal["bg"])
        if hasattr(self, "settings_container") and self.settings_container.winfo_exists():
            self.settings_container.configure(bg=pal["bg"])
            self._render_settings_sections()

    # ---- окно: панель, перетаскивание, края -------------------------------- #

    def _place_window(self):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        try:
            _, _, waw, wah = self.work_area()
        except Exception:
            waw, wah = sw, sh
        waw, wah = waw or sw, wah or sh
        # Размер по умолчанию — доля рабочей области экрана (а не фиксированные
        # пиксели), поэтому на маленьком ноутбучном экране окно открывается
        # компактным, а на большом мониторе — просторным, но не огромным.
        default_w = max(self.MIN_W, min(round(waw * 0.62), round(1400 * self.scale)))
        default_h = max(self.MIN_H, min(round(wah * 0.70), round(900 * self.scale)))
        w, h, x, y = default_w, default_h, None, None
        g = self.cfg.get("geometry")
        if isinstance(g, list) and len(g) == 4:
            w, h, x, y = g
        w, h = max(self.MIN_W, min(int(w), sw)), max(self.MIN_H, min(int(h), sh))
        if x is None or not (-w + 120 < x < sw - 120 and 0 <= y < sh - 80):
            x, y = (sw - w) // 2, (sh - h) // 2
        self.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    def _draw_panel(self):
        c = self.canvas
        c.delete("panel")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 4 or h < 4:
            return
        r = 0 if self.maximized else self.RADIUS
        draw_round(c, 1, 1, w - 1, h - 1, r, self.pal["bg"], self.pal["border"], "panel")

    def _edge_at(self, x, y):
        if self.maximized:
            return None
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        L, R, T, B = x < self.RING, x >= w - self.RING, y < self.RING, y >= h - self.RING
        nl, nr, nt, nb = x < self.CORNER, x >= w - self.CORNER, y < self.CORNER, y >= h - self.CORNER
        if (T and nl) or (L and nt):
            return "nw"
        if (T and nr) or (R and nt):
            return "ne"
        if (B and nl) or (L and nb):
            return "sw"
        if (B and nr) or (R and nb):
            return "se"
        return "w" if L else "e" if R else "n" if T else "s" if B else None

    def _edge_hover(self, e):
        cursors = {"n": "size_ns", "s": "size_ns", "w": "size_we", "e": "size_we",
                   "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw"}
        self.canvas.configure(cursor=cursors.get(self._edge_at(e.x, e.y), ""))

    def _edge_press(self, e):
        self._edge = self._edge_at(e.x, e.y)
        self._start = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y(),
                       self.winfo_width(), self.winfo_height())

    def _edge_drag(self, e):
        if not self._edge:
            return
        rx, ry, x, y, w, h = self._start
        dx, dy = e.x_root - rx, e.y_root - ry
        d = self._edge
        if "e" in d:
            w = max(self.MIN_W, w + dx)
        if "s" in d:
            h = max(self.MIN_H, h + dy)
        if "w" in d:
            nw = max(self.MIN_W, w - dx)
            x, w = x + (w - nw), nw
        if "n" in d:
            nh = max(self.MIN_H, h - dy)
            y, h = y + (h - nh), nh
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ---- адаптивная раскладка при изменении размера окна ------------------- #

    def _sidebar_target_width(self, win_w=None):
        """Ширина навигационной панели слева — плавно меняется вместе
        с шириной окна (уже, если окно узкое; чуть шире на просторных
        экранах), но никогда не выходит за разумные пределы."""
        w = win_w if win_w is not None else self.winfo_width()
        lo, hi = round(150 * self.scale), round(260 * self.scale)
        base, ref = round(220 * self.scale), round(940 * self.scale)
        if w <= 1:
            return base
        return max(lo, min(hi, round(base + (w - ref) * 0.12)))

    def _chats_panel_target_width(self, win_w=None):
        """Ширина списка чатов на вкладке «Чат» — аналогично, но с
        собственными пределами (список чатов может быть чуть уже)."""
        w = win_w if win_w is not None else self.winfo_width()
        lo, hi = round(160 * self.scale), round(260 * self.scale)
        base, ref = round(195 * self.scale), round(940 * self.scale)
        if w <= 1:
            return base
        return max(lo, min(hi, round(base + (w - ref) * 0.08)))

    def _on_root_resize(self, e):
        if e.widget is not self:
            return
        if self._resize_after:
            self.after_cancel(self._resize_after)
        self._resize_after = self.after(80, self._apply_responsive_layout)

    def _apply_responsive_layout(self):
        self._resize_after = None
        try:
            w = self.winfo_width()
        except tk.TclError:
            return
        if w <= 1:
            return
        try:
            if hasattr(self, "sidebar_frame") and self.sidebar_frame.winfo_exists():
                self.sidebar_frame.configure(width=self._sidebar_target_width(w))
            if hasattr(self, "chats_sidebar_card") and self.chats_sidebar_card.winfo_exists():
                self.chats_sidebar_card.configure(width=self._chats_panel_target_width(w))
        except tk.TclError:
            pass

    def bind_drag(self, *widgets):
        for w in widgets:
            w.bind("<ButtonPress-1>", self._move_start)
            w.bind("<B1-Motion>", self._move_do)
            w.bind("<Double-Button-1>", lambda e: self.toggle_max())

    def _move_start(self, e):
        self._mx, self._my = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()

    def _move_do(self, e):
        if not self.maximized:
            self.geometry(f"+{e.x_root - self._mx}+{e.y_root - self._my}")

    def work_area(self):
        if sys.platform == "win32":
            try:
                from ctypes import wintypes
                rect = wintypes.RECT()
                ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
            except Exception:
                pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def toggle_max(self):
        if not self.maximized:
            self._normal_geometry = self.geometry()
            x, y, w, h = self.work_area()
            self.maximized = True
            self.geometry(f"{w}x{h}+{x}+{y}")
        else:
            self.maximized = False
            self.geometry(self._normal_geometry)
        self._draw_panel()

    def _fix_taskbar(self):
        """Без рамки окно пропадает с панели задач — возвращаем."""
        if sys.platform != "win32":
            return
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, (style & ~0x80) | 0x40000)
            self.withdraw()
            self.after(10, self._show_again)
        except Exception:
            pass

    def _show_again(self):
        self.deiconify()
        self.focus_force()

    def minimize(self):
        self._min_geometry = self.geometry()
        self.overrideredirect(False)
        self.iconify()
        self.bind("<Map>", self._on_map)

    def _on_map(self, e):
        if e.widget is self and self.state() == "normal":
            self.unbind("<Map>")
            self.overrideredirect(True)
            self.geometry(self._min_geometry)
            self.after(10, self._fix_taskbar)

    def _on_wheel(self, e):
        sf = self.settings_scroll
        if self.tab == "settings" and sf is not None and sf.needs_scroll():
            sf.canvas.yview_scroll(-3 if e.delta > 0 else 3, "units")
        elif self.tab == "cheats" and self.cheats_scroll is not None and self.cheats_scroll.needs_scroll():
            self.cheats_scroll.canvas.yview_scroll(-3 if e.delta > 0 else 3, "units")

    def _poll_system(self):
        if self.cfg["theme"] == "system" and self.theme_key != ("light" if system_is_light() else "dark"):
            self.resolve()
            self.setup_ttk()
            self.apply_theme_live()
        self.after(3000, self._poll_system)

    def close(self):
        if not self.maximized:
            self.cfg["geometry"] = [self.winfo_width(), self.winfo_height(), self.winfo_x(), self.winfo_y()]
        self.cfg["last_tab"] = self.tab
        self.cfg["offline_mode"] = getattr(self, "offline_dismissed", True)
        save_settings(self.cfg)
        self.save_cheats()
        chan, self.chan = self.chan, None
        if chan is not None:
            try:
                chan.send(json.dumps({"type": "quit"}).encode())
            except Exception:
                pass
            chan.close()
        self.destroy()

    # ---- построение интерфейса -------------------------------------------- #

    def rebuild(self):
        self.resolve()
        self.make_fonts()
        self.setup_ttk()
        if self.settings_scroll is not None:
            self._settings_y = self.settings_scroll.canvas.yview()[0]
        if self.cheats_scroll is not None:
            try:
                self._cheats_y = self.cheats_scroll.canvas.yview()[0]
            except Exception:
                self._cheats_y = 0.0
        if self.body is not None:
            self.body.destroy()
        self._draw_panel()
        self.body = tk.Frame(self, bg=self.pal["bg"])
        self.body.place(x=self.RING, y=self.RING, relwidth=1, relheight=1,
                         width=-2 * self.RING, height=-2 * self.RING)
        self.body.grid_columnconfigure(2, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self.build_sidebar()
        self.sidebar_divider = tk.Frame(self.body, bg=self.pal["border"], width=1)
        self.sidebar_divider.grid(row=0, column=1, sticky="ns", pady=14)
        self.build_content()
        self.show_tab(self.tab)
        self.refresh_chat()
        self.update_state_ui()

    def build_sidebar(self):
        pal = self.pal
        self.sidebar_frame = sb = tk.Frame(self.body, bg=pal["bg"], width=self._sidebar_target_width())
        sb.grid(row=0, column=0, sticky="ns")
        sb.pack_propagate(False)

        self.profile_frame = profile = tk.Frame(sb, bg=pal["bg"], cursor="hand2")
        profile.pack(side="bottom", fill="x", padx=18, pady=18)
        profile.bind("<Button-1>", lambda e: self.show_connect_modal())
        self.avatar = tk.Canvas(profile, width=38, height=38, bg=pal["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.avatar.pack(side="left")
        self.avatar.bind("<Button-1>", lambda e: self.show_connect_modal())
        self.profile_texts = texts = tk.Frame(profile, bg=pal["bg"], cursor="hand2")
        texts.pack(side="left", padx=10, fill="x", expand=True)
        texts.bind("<Button-1>", lambda e: self.show_connect_modal())
        self.lbl_name = tk.Label(texts, bg=pal["bg"], fg=pal["text"], font=self.f_bold, anchor="w", cursor="hand2")
        self.lbl_name.pack(fill="x")
        self.lbl_name.bind("<Button-1>", lambda e: self.show_connect_modal())
        self.lbl_status = tk.Label(texts, bg=pal["bg"], fg=pal["dim"], font=self.f_small, anchor="w", cursor="hand2")
        self.lbl_status.pack(fill="x")
        self.lbl_status.bind("<Button-1>", lambda e: self.show_connect_modal())

        self.brand_frame = brand = tk.Frame(sb, bg=pal["bg"])
        brand.pack(fill="x", padx=18, pady=(20, 22))
        self.logo_canvas = logo = tk.Canvas(brand, width=32, height=32, bg=pal["bg"], highlightthickness=0, bd=0)
        logo.pack(side="left")
        logo.create_polygon(16, 2, 28, 9, 28, 23, 16, 30, 4, 23, 4, 9, fill=pal["accent"], outline=pal["accent"])
        for pts in ((16, 16, 4, 9), (16, 16, 28, 9), (16, 16, 16, 30)):
            logo.create_line(*pts, fill=pal["bg"], width=2)
        self.lbl_brand_name = name = tk.Label(brand, text="CheatChat", bg=pal["bg"], fg=pal["text"], font=self.f_brand)
        name.pack(side="left", padx=10)
        self.bind_drag(brand, logo, name)

        self.nav = []
        nav_items = [
            ("chat", "Чат", "", "✉"),
            ("cheats", "Игры", "\uE7FC", "🎮"),
            ("settings", "Настройки", "", "⚙"),
        ]
        for key, label, glyph, fallback in nav_items:
            item = NavItem(sb, self, key, glyph if self.mdl2 else fallback, label)
            item.pack(fill="x", padx=12, pady=3)
            self.nav.append(item)

    def build_content(self):
        pal = self.pal
        self.content_frame = content = tk.Frame(self.body, bg=pal["bg"])
        content.grid(row=0, column=2, sticky="nsew", padx=18, pady=(12, 18))
        self.header_frame = header = tk.Frame(content, bg=pal["bg"])
        header.pack(fill="x")
        self.lbl_title = tk.Label(header, bg=pal["bg"], fg=pal["text"], font=self.f_title)
        self.lbl_title.pack(side="left", pady=(0, 2))
        self.win_buttons = []
        for kind, cmd in (("close", self.close), ("max", self.toggle_max), ("min", self.minimize)):
            wb = WinButton(header, self, kind, cmd)
            wb.pack(side="right", padx=1)
            self.win_buttons.append(wb)
        self.bind_drag(header, self.lbl_title)

        self.pages_frame = pages = tk.Frame(content, bg=pal["bg"])
        pages.pack(fill="both", expand=True, pady=(14, 0))
        self.page_chat = self.build_chat(pages)
        self.page_cheats = self.build_cheats(pages)
        self.page_settings = self.build_settings(pages)

    def show_tab(self, key):
        self.tab = key
        self.page_chat.pack_forget()
        self.page_cheats.pack_forget()
        self.page_settings.pack_forget()
        if key == "chat":
            page = self.page_chat
            title = "Чат"
        elif key == "cheats":
            page = self.page_cheats
            title = "Читы Minecraft"
        else:
            page = self.page_settings
            title = "Настройки"
        page.pack(fill="both", expand=True)
        self.lbl_title.configure(text=title)
        for item in self.nav:
            item.draw()
        if key == "chat":
            target = self.msg_entry if (self.conn_state == "online" or self.offline_dismissed) else self.name_entry
            self.after(50, lambda: target.winfo_exists() and target.focus_force())
        elif key == "cheats":
            self.refresh_cheats_list()
            if self._cheats_y:
                self.after(30, lambda: self.cheats_scroll.canvas.yview_moveto(self._cheats_y))
        elif self._settings_y:
            self.after(30, lambda: self.settings_scroll.canvas.yview_moveto(self._settings_y))

    # ---- вкладка «Чат» ----------------------------------------------------- #

    def configure_tags(self, text):
        pal = self.pal
        text.tag_configure("ts", foreground=pal["dim"], font=self.f_small)
        text.tag_configure("who", foreground=pal["text"], font=self.f_bold)
        text.tag_configure("me", foreground=pal["accent"], font=self.f_bold)
        text.tag_configure("body", foreground=pal["text"], font=self.f_base)
        text.tag_configure("sys", foreground=pal["dim"], font=self.f_italic)
        text.tag_configure("err", foreground=pal["danger"], font=self.f_base)

    @staticmethod
    def insert_item(text, item):
        text.configure(state="normal")
        if item["kind"] == "msg":
            text.insert("end", item["ts"] + "  ", "ts")
            text.insert("end", item["who"] + "  ", "me" if item["me"] else "who")
            text.insert("end", item["text"] + "\n", "body")
        else:
            text.insert("end", item["text"] + "\n", item["kind"])
        text.configure(state="disabled")

    def make_text(self, parent, **kw):
        pal = self.pal
        text = tk.Text(parent, bg=pal["card"], fg=pal["text"], font=self.f_base, bd=0, highlightthickness=0,
                       wrap="word", state="disabled", cursor="arrow", insertwidth=0, spacing1=3, spacing3=3,
                       selectbackground=pal["accent"], selectforeground=pal["on_accent"], **kw)
        self.configure_tags(text)
        return text

    def build_chat(self, parent):
        pal = self.pal
        page = tk.Frame(parent, bg=pal["bg"])

        # Информационная плашка офлайн-режима
        self.chat_banner = Card(page, pal["card_hi"], pal["bg"], radius=12, auto_height=True)
        b_inner = self.chat_banner.inner
        self.chat_banner_row = row_b = tk.Frame(b_inner, bg=pal["card_hi"])
        row_b.pack(fill="x", padx=14, pady=7)
        self.lbl_chat_banner = tk.Label(row_b, text="🔌 Автономный режим · сервер не подключён", bg=pal["card_hi"],
                                        fg=pal["dim"], font=self.f_small)
        self.lbl_chat_banner.pack(side="left")
        self.chat_banner_chips = [
            Chip(row_b, self, "Подключиться…", False, self.show_connect_modal, bg=pal["card_hi"]),
            Chip(row_b, self, "Локальный сервер", False, self.start_local_and_connect, bg=pal["card_hi"]),
        ]
        self.chat_banner_chips[0].pack(side="right", padx=(6, 0))
        self.chat_banner_chips[1].pack(side="right")
        self.chat_banner.pack(side="top", fill="x", pady=(0, 10))

        # Контейнер для двух колонок: слева список чатов, справа активный диалог
        self.chat_panes = panes = tk.Frame(page, bg=pal["bg"])
        panes.pack(fill="both", expand=True)

        # 1. Левая колонка: Список чатов
        self.chats_sidebar_card = sb_card = Card(panes, pal["card"], pal["bg"], radius=16,
                                                  width=self._chats_panel_target_width())
        sb_card.pack(side="left", fill="y", padx=(0, 10))
        sb_card.pack_propagate(False)

        sb_inner = sb_card.inner
        sb_hdr = tk.Frame(sb_inner, bg=pal["card"])
        sb_hdr.pack(fill="x", padx=10, pady=(10, 6))

        self.lbl_chats_title = tk.Label(sb_hdr, text="Чаты", bg=pal["card"], fg=pal["text"], font=self.f_bold)
        self.lbl_chats_title.pack(side="left", pady=2)

        self.btn_new_chat = Chip(sb_hdr, self, "+ Новый", False, self.show_create_chat_modal, font=self.f_small, bg=pal["card"])
        self.btn_new_chat.pack(side="right")

        tk.Frame(sb_inner, bg=pal["card_hi"], height=1).pack(fill="x", padx=6, pady=(0, 4))

        self.chats_scroll = ScrollFrame(sb_inner, self)
        self.chats_scroll.pack(fill="both", expand=True, padx=2, pady=2)
        self.chats_list_container = self.chats_scroll.body

        # 2. Правая колонка: Активный чат
        self.chat_main_card = self.chat_box = box = Card(panes, pal["card"], pal["bg"], radius=16)
        box.pack(side="right", fill="both", expand=True)

        main_inner = box.inner

        # Шапка активного чата
        self.chat_top_bar = top_bar = tk.Frame(main_inner, bg=pal["card"])
        top_bar.pack(fill="x", padx=14, pady=(10, 6))

        self.lbl_active_chat_title = tk.Label(top_bar, text="🌐 Общий чат", bg=pal["card"], fg=pal["text"],
                                              font=self.f_bold, anchor="w")
        self.lbl_active_chat_title.pack(side="left")

        self.lbl_active_chat_sub = tk.Label(top_bar, text="Публичный канал · видят все", bg=pal["card"],
                                            fg=pal["dim"], font=self.f_small, anchor="w")
        self.lbl_active_chat_sub.pack(side="left", padx=10)

        self.chat_top_divider = tk.Frame(main_inner, bg=pal["card_hi"], height=1)
        self.chat_top_divider.pack(fill="x", padx=8, pady=(0, 4))

        # Окно сообщений
        self.chat_text = self.make_text(main_inner, padx=14, pady=8)
        self.chat_text.pack(fill="both", expand=True)
        self.chat_sb = AutoScrollbar(main_inner, dict(relx=1.0, x=-3, y=42, relheight=1.0, height=-100, width=8, anchor="ne"),
                                     "Card.Vertical.TScrollbar")
        self.chat_sb.configure(command=self.chat_text.yview)
        self.chat_text.configure(yscrollcommand=self.chat_sb.set)

        # Нижняя панель ввода
        self.chat_bar = bar = Card(main_inner, pal["card_hi"], pal["card"], radius=12, auto_height=True)
        bar.pack(side="bottom", fill="x", padx=10, pady=(4, 10))

        self.chat_send_btn = tk.Canvas(bar.inner, width=36, height=36, bg=pal["card_hi"], highlightthickness=0, bd=0,
                                       cursor="hand2")
        self._render_send_btn(False)
        self.chat_send_btn.pack(side="right", padx=(0, 6), pady=6)
        self.chat_send_btn.bind("<ButtonPress-1>", self._on_send_press)
        self.chat_send_btn.bind("<ButtonRelease-1>", self._on_send_release)
        self.chat_send_btn.bind("<Enter>", lambda e: self._render_send_btn(True))
        self.chat_send_btn.bind("<Leave>", lambda e: self._render_send_btn(False))

        self.msg_entry = PlaceholderEntry(bar.inner, self, "Написать сообщение или команду (/help)…", pal["card_hi"])
        self.msg_entry.pack(side="left", fill="x", expand=True, padx=(14, 8), pady=10)
        self.msg_entry.bind("<Return>", lambda e: self.send_message())

        self.render_chat_list()

        self.overlay = Card(page, pal["card"], pal["bg"], radius=18, outline=pal["accent"], auto_height=True)
        inner = self.overlay.inner

        self.overlay_hdr = hdr = tk.Frame(inner, bg=pal["card"])
        hdr.pack(fill="x", padx=20, pady=(16, 2))
        self.lbl_overlay_title = tk.Label(hdr, text="Вход в аккаунт", bg=pal["card"], fg=pal["text"],
                                          font=self.f_brand)
        self.lbl_overlay_title.pack(side="left")
        self.lbl_overlay_close = tk.Label(hdr, text="✕", bg=pal["card"], fg=pal["dim"], font=self.f_bold, cursor="hand2")
        self.lbl_overlay_close.pack(side="right")
        self.lbl_overlay_close.bind("<Button-1>", lambda e: self.continue_offline())

        self.lbl_overlay_sub = tk.Label(inner, text="Зашифрованный чат • Авторизация через Supabase", bg=pal["card"],
                                        fg=pal["dim"], font=self.f_small, anchor="w", justify="left", wraplength=400)
        self.lbl_overlay_sub.pack(anchor="w", padx=20, pady=(0, 6))

        # Переключатель режимов Вход / Регистрация
        self.auth_mode_row = tk.Frame(inner, bg=pal["card"])
        self.auth_mode_row.pack(fill="x", padx=20, pady=(2, 8))
        self.chip_mode_login = Chip(self.auth_mode_row, self, "Вход", self.auth_mode == "login",
                                    lambda: self.set_auth_mode("login"), bg=pal["card"])
        self.chip_mode_reg = Chip(self.auth_mode_row, self, "Регистрация", self.auth_mode == "register",
                                  lambda: self.set_auth_mode("register"), bg=pal["card"])
        self.chip_mode_login.pack(side="left", padx=(0, 8))
        self.chip_mode_reg.pack(side="left")
        self.auth_mode_chips = [self.chip_mode_login, self.chip_mode_reg]

        self.lbl_overlay_name = tk.Label(inner, text="Логин (имя пользователя)", bg=pal["card"], fg=pal["text"], font=self.f_bold, anchor="w")
        self.lbl_overlay_name.pack(fill="x", padx=20, pady=(4, 2))
        self.card_overlay_name = Card(inner, pal["card_hi"], pal["card"], radius=10, auto_height=True)
        self.card_overlay_name.pack(fill="x", padx=20)
        self.name_entry = PlaceholderEntry(self.card_overlay_name.inner, self, "Имя пользователя", pal["card_hi"], self.name)
        self.name_entry.pack(fill="x", padx=10, pady=7)
        self.name_entry.bind("<Return>", lambda e: self.password_entry.focus_force())

        self.lbl_overlay_pwd = tk.Label(inner, text="Пароль", bg=pal["card"], fg=pal["text"], font=self.f_bold, anchor="w")
        self.lbl_overlay_pwd.pack(fill="x", padx=20, pady=(6, 2))
        self.card_overlay_pwd = Card(inner, pal["card_hi"], pal["card"], radius=10, auto_height=True)
        self.card_overlay_pwd.pack(fill="x", padx=20)
        self.password_entry = PlaceholderEntry(self.card_overlay_pwd.inner, self, "Пароль", pal["card_hi"], is_password=True)
        self.password_entry.pack(fill="x", padx=10, pady=7)
        self.password_entry.bind("<Return>", lambda e: self.connect())

        self.lbl_overlay_srv = tk.Label(inner, text="Адрес сервера (хост:порт)", bg=pal["card"], fg=pal["text"], font=self.f_bold, anchor="w")
        self.lbl_overlay_srv.pack(fill="x", padx=20, pady=(6, 2))
        self.card_overlay_srv = Card(inner, pal["card_hi"], pal["card"], radius=10, auto_height=True)
        self.card_overlay_srv.pack(fill="x", padx=20)
        cur_addr = self.cfg.get("server_addr", f"{self.host}:{self.port}")
        self.server_entry = PlaceholderEntry(self.card_overlay_srv.inner, self, "127.0.0.1:26327", pal["card_hi"], cur_addr)
        self.server_entry.pack(fill="x", padx=10, pady=7)
        self.server_entry.bind("<Return>", lambda e: self.connect())

        self.lbl_connect = tk.Label(inner, bg=pal["card"], font=self.f_small, anchor="w", justify="left",
                                    wraplength=400)
        self.lbl_connect.pack(fill="x", padx=20, pady=(4, 0))

        self.overlay_btn_row = btn_row = tk.Frame(inner, bg=pal["card"])
        btn_row.pack(fill="x", padx=20, pady=(12, 18))

        self.btn_submit_auth = Chip(btn_row, self, "Войти" if self.auth_mode == "login" else "Зарегистрироваться",
                                    True, self.connect, font=self.f_bold, bg=pal["card"])
        self.overlay_chips = [
            Chip(btn_row, self, "Без сервера", False, self.continue_offline, bg=pal["card"]),
            Chip(btn_row, self, "Локальный сервер", False, self.start_local_and_connect, bg=pal["card"]),
            self.btn_submit_auth,
        ]
        self.overlay_chips[0].pack(side="left", padx=(0, 6))
        self.overlay_chips[1].pack(side="left")
        self.overlay_chips[2].pack(side="right")

        self.build_create_chat_modal(page)
        return page

    def continue_offline(self):
        self.offline_dismissed = True
        self.cfg["offline_mode"] = True
        save_settings(self.cfg)
        self.update_state_ui()
        if not self.history:
            self.add_item("sys", "Вы находитесь в автономном режиме (без подключения к серверу).")
            self.add_item("sys", "Все вкладки («Читы», «Настройки», локальный интерфейс) полностью доступны.")

    def show_connect_modal(self):
        self.show_tab("chat")
        self.offline_dismissed = False
        self.overlay.place(relx=0.5, rely=0.5, anchor="center", width=460)
        self.after(50, lambda: self.name_entry.focus_force())

    def set_auth_mode(self, mode: str):
        self.auth_mode = mode
        if hasattr(self, "chip_mode_login") and hasattr(self, "chip_mode_reg"):
            self.chip_mode_login.set_selected(mode == "login")
            self.chip_mode_reg.set_selected(mode == "register")
        if hasattr(self, "lbl_overlay_title"):
            self.lbl_overlay_title.configure(text="Вход в аккаунт" if mode == "login" else "Регистрация аккаунта")
        if hasattr(self, "lbl_overlay_sub"):
            self.lbl_overlay_sub.configure(
                text="Зашифрованный чат • Авторизация через Supabase" if mode == "login" else "Создание учетной записи в Supabase"
            )
        if hasattr(self, "btn_submit_auth"):
            self.btn_submit_auth.set_text("Войти" if mode == "login" else "Зарегистрироваться")
        if hasattr(self, "lbl_connect"):
            self.connect_text = ""
            self.connect_error = False
            self.lbl_connect.configure(text="")

    def start_local_and_connect(self):
        self.lbl_connect.configure(text="Запуск локального сервера…", fg=self.pal["dim"])
        addr = self.server_entry.value() if hasattr(self, "server_entry") and self.server_entry.value() else "127.0.0.1:26327"
        h, p = parse_host_port(addr, DEFAULT_PORT)
        self.host, self.port = h, p
        self.cfg["server_addr"] = f"{h}:{p}"
        save_settings(self.cfg)

        def _start():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                res = s.connect_ex((h, p))
                s.close()
                if res != 0:
                    run_embedded_server(h, p)
            except Exception:
                pass

        threading.Thread(target=_start, daemon=True).start()
        self.embedded_server_running = True
        self.after(400, self.connect)

    def refresh_chat(self):
        if self.chat_text is None:
            return
        self.chat_text.configure(state="normal")
        self.chat_text.delete("1.0", "end")
        self.chat_text.configure(state="disabled")
        history = self.chat_histories.get(self.current_chat_id, [])
        for item in history:
            self.insert_item(self.chat_text, item)
        self.chat_text.see("end")

    def add_item(self, kind, text, who="", me=False, ts=None, chat_id=None):
        chat_id = chat_id or self.current_chat_id or "general"
        item = dict(kind=kind, text=text, who=who, me=me, ts=fmt_time(ts), chat_id=chat_id)
        if chat_id not in self.chat_histories:
            self.chat_histories[chat_id] = []
        self.chat_histories[chat_id].append(item)
        del self.chat_histories[chat_id][:-2000]

        if chat_id == "general":
            self.history.append(item)
            del self.history[:-2000]

        if chat_id == self.current_chat_id:
            if self.chat_text is not None:
                stick = self.chat_text.yview()[1] >= 0.999
                self.insert_item(self.chat_text, item)
                if stick or me:
                    self.chat_text.see("end")
        else:
            if kind == "msg":
                self.unread_chats.add(chat_id)
                self.render_chat_list()

    def select_chat(self, chat_id: str):
        self.current_chat_id = chat_id
        self.unread_chats.discard(chat_id)
        self.render_chat_list()
        self.update_active_chat_header()
        self.refresh_chat()

        # Если для этого чата еще не загружена история и мы онлайн, запрашиваем
        if not self.chat_histories.get(chat_id) and self.conn_state == "online" and self.chan:
            try:
                self.chan.send(json.dumps({"type": "get_history", "chat_id": chat_id}).encode())
            except Exception:
                pass

    def update_active_chat_header(self):
        chat = next((c for c in self.chats if c.get("id") == self.current_chat_id), None)
        if not chat:
            title = "🌐 Общий чат"
            sub = "Публичный канал · видят все"
        else:
            c_type = chat.get("type", "group")
            c_name = chat.get("name", "Чат")
            if c_type == "global":
                title = f"🌐 {c_name}"
                sub = "Публичный канал · видят все"
            elif c_type == "direct":
                title = f"💬 {c_name}"
                sub = "Личные сообщения"
            else:
                title = f"👥 {c_name}"
                members = chat.get("members", [])
                sub = f"Группа · {len(members)} участников" if members else "Групповой чат"

        if hasattr(self, "lbl_active_chat_title") and self.lbl_active_chat_title.winfo_exists():
            self.lbl_active_chat_title.configure(text=title)
        if hasattr(self, "lbl_active_chat_sub") and self.lbl_active_chat_sub.winfo_exists():
            self.lbl_active_chat_sub.configure(text=sub)

    def render_chat_list(self):
        if not hasattr(self, "chats_list_container") or not self.chats_list_container.winfo_exists():
            return
        for child in self.chats_list_container.winfo_children():
            child.destroy()
        self.chat_item_widgets.clear()
        for chat in self.chats:
            cid = chat.get("id", "general")
            is_active = (cid == self.current_chat_id)
            is_unread = (cid in self.unread_chats)
            item = ChatItem(self.chats_list_container, self, chat, is_active, is_unread, self.select_chat)
            self.chat_item_widgets.append(item)

    def build_create_chat_modal(self, page):
        pal = self.pal
        self.create_chat_overlay = Card(page, pal["card"], pal["bg"], radius=18, outline=pal["accent"], auto_height=True)
        inner = self.create_chat_overlay.inner

        hdr = tk.Frame(inner, bg=pal["card"])
        hdr.pack(fill="x", padx=20, pady=(16, 4))
        self.create_chat_modal_hdr = hdr
        self.lbl_modal_create_title = tk.Label(hdr, text="Новый чат", bg=pal["card"], fg=pal["text"], font=self.f_brand)
        self.lbl_modal_create_title.pack(side="left")
        close_btn = tk.Label(hdr, text="✕", bg=pal["card"], fg=pal["dim"], font=self.f_bold, cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self.create_chat_overlay.place_forget())
        self.lbl_modal_create_close = close_btn

        self.create_mode_row = tk.Frame(inner, bg=pal["card"])
        self.create_mode_row.pack(fill="x", padx=20, pady=(4, 8))
        self.chip_create_direct = Chip(self.create_mode_row, self, "💬 Личный диалог", True,
                                       lambda: self.set_create_chat_mode("direct"), bg=pal["card"])
        self.chip_create_group = Chip(self.create_mode_row, self, "👥 Групповой чат", False,
                                      lambda: self.set_create_chat_mode("group"), bg=pal["card"])
        self.chip_create_direct.pack(side="left", padx=(0, 8))
        self.chip_create_group.pack(side="left")

        self.create_chat_body = tk.Frame(inner, bg=pal["card"])
        self.create_chat_body.pack(fill="x", padx=20, pady=(0, 16))

        self.lbl_grp_name = tk.Label(self.create_chat_body, text="Название группы", bg=pal["card"], fg=pal["text"],
                                     font=self.f_bold, anchor="w")
        self.card_grp_name = Card(self.create_chat_body, pal["card_hi"], pal["card"], radius=10, auto_height=True)
        self.entry_grp_name = PlaceholderEntry(self.card_grp_name.inner, self, "Например: Разработчики, Друзья…", pal["card_hi"])
        self.entry_grp_name.pack(fill="x", padx=10, pady=7)

        self.lbl_select_users = tk.Label(self.create_chat_body, text="Выберите собеседника:", bg=pal["card"], fg=pal["text"],
                                         font=self.f_bold, anchor="w")
        self.lbl_select_users.pack(fill="x", pady=(6, 4))

        self.users_scroll_card = Card(self.create_chat_body, pal["card_hi"], pal["card"], radius=10, height=130)
        self.users_scroll_card.pack(fill="x", pady=(2, 8))
        self.users_scroll_card.pack_propagate(False)

        self.users_scroll = ScrollFrame(self.users_scroll_card.inner, self)
        self.users_scroll.pack(fill="both", expand=True, padx=4, pady=4)
        self.users_list_frame = self.users_scroll.body

        self.btn_create_group_submit = Chip(self.create_chat_body, self, "Создать группу", True,
                                            self._on_submit_create_group, font=self.f_bold, bg=pal["card"])

    def show_create_chat_modal(self, mode="direct", initial_name=""):
        if self.conn_state == "online" and self.chan:
            try:
                self.chan.send(json.dumps({"type": "get_users"}).encode())
            except Exception:
                pass
        self.set_create_chat_mode(mode)
        if initial_name and hasattr(self, "entry_grp_name"):
            self.entry_grp_name.delete(0, "end")
            self.entry_grp_name.insert(0, initial_name)
        self.create_chat_overlay.place(relx=0.5, rely=0.5, anchor="center", width=460)
        self.render_create_modal_users()

    def set_create_chat_mode(self, mode: str):
        self.create_chat_mode = mode
        if hasattr(self, "chip_create_direct") and hasattr(self, "chip_create_group"):
            self.chip_create_direct.set_selected(mode == "direct")
            self.chip_create_group.set_selected(mode == "group")

        if mode == "direct":
            if hasattr(self, "lbl_grp_name"):
                self.lbl_grp_name.pack_forget()
            if hasattr(self, "card_grp_name"):
                self.card_grp_name.pack_forget()
            if hasattr(self, "lbl_select_users"):
                self.lbl_select_users.configure(text="Выберите собеседника:")
            if hasattr(self, "btn_create_group_submit"):
                self.btn_create_group_submit.pack_forget()
        else:
            if hasattr(self, "lbl_grp_name") and hasattr(self, "card_grp_name"):
                self.lbl_grp_name.pack(fill="x", pady=(2, 2))
                self.card_grp_name.pack(fill="x", pady=(0, 6))
            if hasattr(self, "lbl_select_users"):
                self.lbl_select_users.configure(text="Выберите участников группы:")
            if hasattr(self, "btn_create_group_submit"):
                self.btn_create_group_submit.pack(fill="x", pady=(6, 0))

        self.selected_group_members.clear()
        self.render_create_modal_users()

    def render_create_modal_users(self):
        if not hasattr(self, "users_list_frame"):
            return
        for child in self.users_list_frame.winfo_children():
            child.destroy()

        pal = self.pal
        users = [u for u in self.all_users if u != self.name]
        if not users:
            tk.Label(self.users_list_frame, text="Загрузка пользователей или список пуст…", bg=pal["card_hi"],
                     fg=pal["dim"], font=self.f_small).pack(padx=8, pady=16)
            return

        if self.create_chat_mode == "direct":
            for u in users:
                btn_row = tk.Frame(self.users_list_frame, bg=pal["card_hi"], cursor="hand2")
                btn_row.pack(fill="x", padx=4, pady=2)
                lbl_av = tk.Label(btn_row, text="💬", bg=pal["card_hi"], fg=pal["text"], cursor="hand2")
                lbl_av.pack(side="left", padx=(6, 4), pady=4)
                lbl_u = tk.Label(btn_row, text=u, bg=pal["card_hi"], fg=pal["text"], font=self.f_base, cursor="hand2")
                lbl_u.pack(side="left", fill="x", expand=True, pady=4)

                def _start(target=u):
                    self.create_chat_overlay.place_forget()
                    self.request_create_direct_chat(target)

                for w in (btn_row, lbl_av, lbl_u):
                    w.bind("<Button-1>", lambda e, fn=_start: fn())
        else:
            for u in users:
                is_sel = u in self.selected_group_members
                c = Chip(self.users_list_frame, self, u, is_sel, lambda target=u: self._toggle_group_member(target), bg=pal["card_hi"])
                c.pack(side="left", padx=3, pady=3)

    def _toggle_group_member(self, username: str):
        if username in self.selected_group_members:
            self.selected_group_members.remove(username)
        else:
            self.selected_group_members.add(username)
        self.render_create_modal_users()

    def _on_submit_create_group(self):
        name = self.entry_grp_name.value() if hasattr(self, "entry_grp_name") else ""
        if not name:
            self.add_item("err", "Введите название группы")
            return
        members = list(self.selected_group_members)
        self.create_chat_overlay.place_forget()
        self.request_create_group_chat(name, members)

    def request_create_direct_chat(self, target_user: str):
        if not target_user:
            return
        if self.conn_state == "online" and self.chan:
            try:
                self.chan.send(json.dumps({"type": "create_direct_chat", "with_user": target_user}).encode())
            except Exception as e:
                self.add_item("err", f"Ошибка создания чата: {e}")
        else:
            chat_id = f"dm_{min(self.name or 'Вы', target_user)}_{max(self.name or 'Вы', target_user)}"
            existing = next((c for c in self.chats if c.get("id") == chat_id), None)
            if not existing:
                chat = {"id": chat_id, "name": target_user, "type": "direct", "members": [self.name or 'Вы', target_user]}
                self.chats.append(chat)
            self.render_chat_list()
            self.select_chat(chat_id)

    def request_create_group_chat(self, group_name: str, members: list[str]):
        if not group_name:
            return
        if self.conn_state == "online" and self.chan:
            try:
                self.chan.send(json.dumps({"type": "create_group_chat", "name": group_name, "members": members}).encode())
            except Exception as e:
                self.add_item("err", f"Ошибка создания группы: {e}")
        else:
            import secrets
            chat_id = f"grp_{secrets.token_hex(4)}"
            chat = {"id": chat_id, "name": group_name, "type": "group", "members": [self.name or 'Вы'] + members}
            self.chats.append(chat)
            self.render_chat_list()
            self.select_chat(chat_id)

    def update_state_ui(self):
        pal = self.pal
        online = self.conn_state == "online"
        connecting = self.conn_state == "connecting"
        initial = (self.name[:1] or "?").upper()
        self.avatar.delete("all")
        avatar_bg = pal["accent"] if online else pal["card_hi"]
        self.avatar.create_oval(1, 1, 37, 37, fill=avatar_bg, outline=avatar_bg)
        self.avatar.create_text(19, 19, text=initial, font=self.f_bold, fill=pal["on_accent"] if online else pal["text"])
        self.lbl_name.configure(text=self.name or "Гость")
        if online:
            status = "● в сети"
            st_color = pal["ok"]
        elif connecting:
            status = "● подключение…"
            st_color = pal["dim"]
        elif getattr(self, "embedded_server_running", False):
            status = "● локальный сервер"
            st_color = pal["ok"]
        else:
            status = "○ офлайн (без сервера)"
            st_color = pal["dim"]

        self.lbl_status.configure(text=status, fg=st_color)
        if hasattr(self, "lbl_connect"):
            is_ok = not self.connect_error and ("успешн" in self.connect_text.lower() or "✓" in self.connect_text)
            color = pal["ok"] if is_ok else (pal["danger"] if self.connect_error else pal["dim"])
            self.lbl_connect.configure(text=self.connect_text, fg=color)

        if hasattr(self, "chat_banner"):
            if online:
                self.chat_banner.pack_forget()
            else:
                self.chat_banner.pack(side="top", fill="x", pady=(0, 10))

        if online or getattr(self, "offline_dismissed", False):
            self.overlay.place_forget()
        else:
            self.overlay.place(relx=0.5, rely=0.5, anchor="center", width=460)

    # ---- сеть -------------------------------------------------------------- #

    def connect(self):
        if self.conn_state != "offline":
            return
        name = self.name_entry.value()[:32]
        if not name:
            self.connect_text, self.connect_error = "Введите имя пользователя", True
            self.update_state_ui()
            return

        password = self.password_entry.value() if hasattr(self, "password_entry") else ""
        if not password:
            self.connect_text, self.connect_error = "Введите пароль", True
            self.update_state_ui()
            return

        if self.auth_mode == "register" and len(password) < 4:
            self.connect_text, self.connect_error = "Пароль должен содержать от 4 символов", True
            self.update_state_ui()
            return

        if hasattr(self, "server_entry") and self.server_entry.value():
            h, p = parse_host_port(self.server_entry.value(), DEFAULT_PORT)
            self.host, self.port = h, p
            self.cfg["server_addr"] = f"{h}:{p}"

        self.name, self.cfg["name"] = name, name
        save_settings(self.cfg)
        status_action = "Авторизация…" if self.auth_mode == "login" else "Регистрация…"
        self.conn_state, self.connect_text, self.connect_error = "connecting", status_action, False
        self.conn_id += 1
        self.update_state_ui()
        threading.Thread(target=self._worker, args=(self.conn_id, name, password, self.auth_mode), daemon=True).start()

    def _worker(self, cid, name, password, mode):
        try:
            if handshake is None:
                raise RuntimeError("Библиотека cryptography не установлена.")
            sock = socket.create_connection((self.host, self.port), timeout=5)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            chan = handshake(sock, server=False)
            sock.settimeout(None)
            chan.send(json.dumps({"type": mode, "username": name, "password": password}).encode())
        except Exception as exc:
            err_msg = str(exc)
            if "11001" in err_msg or "getaddrinfo" in err_msg:
                err_msg = f"Неверный адрес сервера ({self.host}:{self.port}) — хост не найден."
            elif "10061" in err_msg or "refused" in err_msg.lower():
                err_msg = f"Сервер {self.host}:{self.port} недоступен (порт закрыт или сервер выключен)."
            elif "timed out" in err_msg.lower() or "timeout" in err_msg.lower():
                err_msg = f"Время ожидания подключения к {self.host}:{self.port} истекло."
            self.events.put((cid, "failed", f"Не удалось подключиться: {err_msg}"))
            return
        self.chan = chan
        try:
            while True:
                raw = chan.recv()
                if not raw:
                    break
                self.events.put((cid, "msg", json.loads(raw)))
        except Exception as exc:
            self.events.put((cid, "closed", str(exc)))

    def _pump(self):
        try:
            while True:
                cid, kind, payload = self.events.get_nowait()
                if cid == self.conn_id:
                    self._handle(kind, payload)
        except queue.Empty:
            pass
        self.after(50, self._pump)

    def _handle(self, kind, payload):
        if kind == "failed":
            self.fail(payload)
        elif kind == "closed":
            if self.conn_state == "online":
                self.add_item("err", "Соединение с сервером потеряно.")
                self.fail("Соединение потеряно. Нажмите «Войти», чтобы подключиться снова.")
            elif self.conn_state == "connecting":
                self.fail("Сервер закрыл соединение.")
        else:
            self._on_server(payload)

    def _on_server(self, msg):
        kind = msg.get("type")
        if kind == "welcome":
            self.conn_state, self.name = "online", msg.get("you", self.name)
            others = [n for n in msg.get("online", []) if n != self.name]
            self.add_item("sys", f"Вы вошли как {self.name}. " +
                          ("В сети: " + ", ".join(others) if others else "Пока вы одни."))
            self.update_state_ui()
            self.msg_entry.focus_force()
        elif kind == "register_ok":
            text = str(msg.get("text", "Регистрация успешна! Теперь выполните вход."))
            self.conn_state = "offline"
            self.connect_text = "✓ " + text
            self.connect_error = False
            self.set_auth_mode("login")
            self.update_state_ui()
            if self.chan:
                try:
                    self.chan.close()
                except Exception:
                    pass
                self.chan = None
        elif kind == "system":
            self.add_item("sys", str(msg.get("text", "")))
        elif kind == "chat_list":
            server_chats = msg.get("chats", [])
            if server_chats:
                existing_ids = {c.get("id") for c in self.chats}
                for sc in server_chats:
                    if sc.get("id") not in existing_ids:
                        self.chats.append(sc)
                    else:
                        existing = next((ec for ec in self.chats if ec.get("id") == sc.get("id")), None)
                        if existing:
                            existing.update(sc)
                self.render_chat_list()
                self.update_active_chat_header()
        elif kind == "chat_created":
            chat = msg.get("chat")
            if chat:
                cid = chat.get("id")
                existing = next((c for c in self.chats if c.get("id") == cid), None)
                if not existing:
                    self.chats.append(chat)
                else:
                    existing.update(chat)
                self.render_chat_list()
                self.select_chat(cid)
        elif kind == "user_list":
            self.all_users = [u for u in msg.get("users", []) if u != self.name]
            self.render_create_modal_users()
        elif kind == "history":
            chat_id = msg.get("chat_id", "general")
            messages = msg.get("messages", [])
            self.chat_histories[chat_id] = []
            if messages:
                for m in messages:
                    sender = str(m.get("from", "?"))
                    text = str(m.get("text", ""))
                    ts = m.get("ts")
                    is_me = (sender == self.name)
                    self.add_item("msg", text, who="Вы" if is_me else sender, me=is_me, ts=ts, chat_id=chat_id)
            elif chat_id != "general":
                self.add_item("sys", "Здесь пока нет сообщений. Напишите первым!", chat_id=chat_id)
        elif kind == "msg":
            chat_id = msg.get("chat_id", "general")
            sender = str(msg.get("from", "?"))
            text = str(msg.get("text", ""))
            ts = msg.get("ts")
            self.add_item("msg", text, who=sender, me=False, ts=ts, chat_id=chat_id)
        elif kind == "error":
            text = str(msg.get("text", ""))
            if self.conn_state == "online":
                self.add_item("err", text)
            else:
                self.fail(text)

    def fail(self, text):
        chan, self.chan = self.chan, None
        if chan is not None:
            chan.close()
        self.conn_state, self.connect_text, self.connect_error = "offline", text, True
        self.update_state_ui()

    def send_message(self):
        text = self.msg_entry.value()
        if not text:
            return
        if text == "/quit":
            self.close()
            return
        if self.conn_state != "online":
            if text == "/help":
                self.add_item("sys", "Команды: /help — справка, /dm <имя> — личный чат, /group <название> — создать группу, /cheats — вкладка читов, /connect — подключение к серверу, /server — запустить локальный сервер, /clear — очистить чат, /quit — выход.")
            elif text.startswith(("/dm ", "/личка ", "/msg ")):
                target = text.split(" ", 1)[1].strip()
                self.request_create_direct_chat(target)
            elif text.startswith(("/group ", "/группа ")):
                gname = text.split(" ", 1)[1].strip()
                self.show_create_chat_modal("group", initial_name=gname)
            elif text in ("/cheats", "/читы"):
                self.show_tab("cheats")
            elif text in ("/server", "/сервер"):
                self.start_local_and_connect()
            elif text in ("/connect", "/подключиться"):
                self.show_connect_modal()
            elif text == "/clear":
                self.chat_histories[self.current_chat_id] = []
                self.refresh_chat()
            else:
                self.add_item("msg", text, who=f"{self.name or 'Вы'} (офлайн)", me=True, chat_id=self.current_chat_id)
                self.add_item("sys", "Сообщение сохранено локально (сервер не подключён).", chat_id=self.current_chat_id)
            self.msg_entry.clear()
            return
        try:
            if text == "/who":
                self.chan.send(json.dumps({"type": "who"}).encode())
            elif text.startswith(("/dm ", "/личка ", "/msg ")):
                target = text.split(" ", 1)[1].strip()
                self.request_create_direct_chat(target)
            elif text.startswith(("/group ", "/группа ")):
                gname = text.split(" ", 1)[1].strip()
                self.show_create_chat_modal("group", initial_name=gname)
            elif text == "/clear":
                self.chat_histories[self.current_chat_id] = []
                self.refresh_chat()
            else:
                self.chan.send(json.dumps({"type": "msg", "chat_id": self.current_chat_id, "text": text}).encode())
                self.add_item("msg", text, who="Вы", me=True, chat_id=self.current_chat_id)
            self.msg_entry.clear()
        except Exception as exc:
            self.add_item("err", f"Ошибка отправки: {exc}")

    # ---- вкладка «Настройки» ---------------------------------------------- #

    def build_settings(self, parent):
        pal = self.pal
        self.settings_scroll = sf = ScrollFrame(parent, self)
        self.settings_container = tk.Frame(sf.body, bg=pal["bg"])
        self.settings_container.pack(fill="both", expand=True)
        self._render_settings_sections()
        return sf

    def _render_settings_sections(self):
        pal = self.pal
        if not hasattr(self, "settings_container") or not self.settings_container.winfo_exists():
            return
        for ch in self.settings_container.winfo_children():
            ch.destroy()

        def section(title, hint):
            card = Card(self.settings_container, pal["card"], pal["bg"], radius=16, auto_height=True)
            card.pack(fill="x", pady=(0, 14), padx=(0, 14))
            tk.Label(card.inner, text=title, bg=pal["card"], fg=pal["text"], font=self.f_bold
                     ).pack(anchor="w", padx=16, pady=(14, 0))
            tk.Label(card.inner, text=hint, bg=pal["card"], fg=pal["dim"], font=self.f_small
                     ).pack(anchor="w", padx=16)
            return card.inner

        def flow(inner):
            f = Flow(inner, pal["card"])
            f.pack(fill="x", padx=16, pady=(12, 16))
            return f

        inner = section("Тема оформления", "Цвет всего интерфейса")
        f = flow(inner)
        for key, label in THEME_ORDER:
            f.add(Chip(f, self, label, self.cfg["theme"] == key, lambda k=key: self.set_cfg(theme=k),
                       dot=lambda c, x, y, k=key: draw_theme_dot(c, x, y, k, pal)))

        inner = section("Шрифт", "Семейство и размер — применяется ко всему приложению")
        f = flow(inner)
        current = self.f_base.actual("family")
        for fam in self.font_choices:
            f.add(Chip(f, self, fam, fam == current, lambda v=fam: self.set_cfg(font=v),
                       font=tkfont.Font(family=fam, size=self.cfg["font_size"])))
        row = tk.Frame(inner, bg=pal["card"])
        row.pack(fill="x", padx=16, pady=(0, 16))
        size = self.cfg["font_size"]
        tk.Label(row, text="Размер", bg=pal["card"], fg=pal["text"], font=self.f_base).pack(side="left")
        Chip(row, self, "−", False, lambda: self.set_cfg(font_size=max(FONT_MIN, size - 1))
             ).pack(side="left", padx=(14, 8))
        tk.Label(row, text=f"{size} pt", bg=pal["card"], fg=pal["text"], font=self.f_bold, width=6
                 ).pack(side="left")
        Chip(row, self, "+", False, lambda: self.set_cfg(font_size=min(FONT_MAX, size + 1))
             ).pack(side="left", padx=8)

        inner = section("Цвет текста", "«Авто» берёт цвет из выбранной темы")
        f = flow(inner)
        custom = self.cfg["text_color"]
        f.add(Chip(f, self, "Авто", custom is None, lambda: self.set_cfg(text_color=None)))
        colors = TEXT_COLORS + ([custom] if custom and custom not in TEXT_COLORS else [])
        for color in colors:
            f.add(Swatch(f, self, color, color == custom, lambda c=color: self.set_cfg(text_color=c)))
        f.add(Chip(f, self, "Свой цвет…", False, self.pick_color))

        inner = section("Предпросмотр", "Так будут выглядеть сообщения")
        preview = self.make_text(inner, height=3, padx=16, pady=4)
        preview.pack(fill="x", padx=0, pady=(6, 14))
        for item in (dict(kind="msg", ts=fmt_time(), who="Анна", me=False, text="Привет! Как дела?"),
                     dict(kind="msg", ts=fmt_time(), who="Вы", me=True, text="Отлично, тестирую новый интерфейс."),
                     dict(kind="sys", ts="", who="", me=False, text="Анна вышла из чата")):
            self.insert_item(preview, item)

    def pick_color(self):
        _, hex_color = colorchooser.askcolor(color=self.cfg["text_color"] or self.pal["text"],
                                             parent=self, title="Цвет текста")
        if hex_color:
            self.set_cfg(text_color=hex_color)

    # ---- сохранение и менеджер читов -------------------------------------- #

    def save_cheats(self):
        save_cheats(self.cheats)

    def _check_running_processes(self):
        changed = False
        for cid, proc in list(self.running_processes.items()):
            if proc.poll() is not None:
                self.running_processes.pop(cid, None)
                changed = True
        if changed and self.tab == "cheats":
            self.refresh_cheats_list()
        self.after(1000, self._check_running_processes)

    # ---- вкладка «Читы» (Minecraft) --------------------------------------- #

    def build_cheats(self, parent):
        pal = self.pal
        page = tk.Frame(parent, bg=pal["bg"])

        # Верхняя панель действий
        self.cheats_toolbar = toolbar = tk.Frame(page, bg=pal["bg"])
        toolbar.pack(fill="x", pady=(0, 10))

        self.cheats_left_tools = left_tools = tk.Frame(toolbar, bg=pal["bg"])
        left_tools.pack(side="left")

        self.cheats_toolbar_chips = [
            Chip(left_tools, self, "+ Добавить игру", True, self.add_cheat_modal, font=self.f_bold, bg=pal["bg"]),
        ]



        # Поиск ч
        self.cheats_search_card = search_card = Card(toolbar, pal["card"], pal["bg"], radius=12, auto_height=True)
        search_card.pack(side="right")
        self.cheats_search_entry = PlaceholderEntry(search_card.inner, self, "Поиск игры...", pal["card"])
        self.cheats_search_entry.pack(side="left", padx=(10, 10), pady=6)
        self.cheats_search_entry.bind("<KeyRelease>", lambda e: self.refresh_cheats_list())

        # Вторая строка: фильтры и статусное уведомление
        self.cheats_sub_bar = sub_bar = tk.Frame(page, bg=pal["bg"])
        sub_bar.pack(fill="x", pady=(0, 10))

        self.cheats_filter_box = filter_box = tk.Frame(sub_bar, bg=pal["bg"])
        filter_box.pack(side="left")
        self.btn_filter_all = Chip(filter_box, self, "Все", self.cheats_filter_game == "Все",
                                   lambda: self.set_cheats_filter("Все"), bg=pal["bg"])
        self.btn_filter_all.pack(side="left", padx=(0, 6))
        pal = self.pal
        card = Card(filter_box, pal["card"], pal["bg"], radius=16, auto_height=True)
        inner = card.inner
        box = tk.Frame(inner, bg=pal["card"])
        Chip(filter_box, self, "+ Добавить игру", True, self.add_cheat_modal, font=self.f_bold, bg=pal["card"]
             ).pack()

        #self.btn_filter_mc.pack(side="left")

        self.lbl_cheats_toast = tk.Label(sub_bar, bg=pal["bg"], fg=pal["ok"], font=self.f_small, anchor="e")
        self.lbl_cheats_toast.pack(side="right", padx=(10, 4))

        # Список карточек со скроллом
        self.cheats_scroll = sf = ScrollFrame(page, self)
        sf.pack(fill="both", expand=True)

        self.cheats_cards_container = tk.Frame(sf.body, bg=pal["bg"])
        self.cheats_cards_container.pack(fill="both", expand=True)

        # Модальный оверлей добавления/редактирования
        self.modal_backdrop = tk.Frame(page, bg=pal["bg"])
        self.build_cheat_modal(self.modal_backdrop)

        return page

    def build_cheat_modal(self, parent):
        pal = self.pal
        self.cheat_modal = Card(parent, pal["card"], pal["bg"], radius=18, outline=pal["accent"], auto_height=True)
        self.cheat_modal.place(relx=0.5, rely=0.5, anchor="center", width=490)
        inner = self.cheat_modal.inner

        self.modal_hdr = hdr = tk.Frame(inner, bg=pal["card"])
        hdr.pack(fill="x", padx=20, pady=(16, 2))
        self.lbl_modal_title = tk.Label(hdr, text="Добавить игру", bg=pal["card"], fg=pal["text"], font=self.f_brand)
        self.lbl_modal_title.pack(side="left")
        self.lbl_modal_close = lbl_close = tk.Label(hdr, text="✕", bg=pal["card"], fg=pal["dim"], font=self.f_bold, cursor="hand2")
        lbl_close.pack(side="right")
        lbl_close.bind("<Button-1>", lambda e: self.hide_cheat_modal())

        self.lbl_modal_subtitle = tk.Label(inner, text="Укажите исполняемый файл (.jar, .exe, .bat) и параметры игры", bg=pal["card"],
                                           fg=pal["dim"], font=self.f_small, anchor="w")
        self.lbl_modal_subtitle.pack(fill="x", padx=20, pady=(0, 10))

        self.lbl_m_name = tk.Label(inner, text="Название", bg=pal["card"], fg=pal["text"], font=self.f_bold, anchor="w")
        self.lbl_m_name.pack(fill="x", padx=20, pady=(4, 2))
        self.w_m_name = w_name = Card(inner, pal["card_hi"], pal["card"], radius=10, auto_height=True)
        w_name.pack(fill="x", padx=20)
        self.modal_name_entry = PlaceholderEntry(w_name.inner, self, "Например: Meteor Client или Celestial", pal["card_hi"])
        self.modal_name_entry.pack(fill="x", padx=10, pady=7)

        self.modal_row_gv = row_gv = tk.Frame(inner, bg=pal["card"])
        row_gv.pack(fill="x", padx=20, pady=(6, 2))

        self.modal_col_g = col_g = tk.Frame(row_gv, bg=pal["card"])
        col_g.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.lbl_m_game = tk.Label(col_g, text="Игра", bg=pal["card"], fg=pal["text"], font=self.f_bold, anchor="w")
        self.lbl_m_game.pack(fill="x")
        self.w_m_game = w_game = Card(col_g, pal["card_hi"], pal["card"], radius=10, auto_height=True)
        w_game.pack(fill="x", pady=(2, 0))
        self.modal_game_entry = PlaceholderEntry(w_game.inner, self, "Minecraft", pal["card_hi"], "Minecraft")
        self.modal_game_entry.pack(fill="x", padx=10, pady=7)

        self.modal_col_v = col_v = tk.Frame(row_gv, bg=pal["card"])
        col_v.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.lbl_m_ver = tk.Label(col_v, text="Версия / Мод-лоадер", bg=pal["card"], fg=pal["text"], font=self.f_bold, anchor="w")
        self.lbl_m_ver.pack(fill="x")
        self.w_m_ver = w_ver = Card(col_v, pal["card_hi"], pal["card"], radius=10, auto_height=True)
        w_ver.pack(fill="x", pady=(2, 0))
        self.modal_version_entry = PlaceholderEntry(w_ver.inner, self, "1.20.4 Fabric / 1.16.5", pal["card_hi"])
        self.modal_version_entry.pack(fill="x", padx=10, pady=7)

        self.lbl_m_path = tk.Label(inner, text="Путь к исполняемому файлу (.jar, .exe, .bat)", bg=pal["card"], fg=pal["text"],
                                   font=self.f_bold, anchor="w")
        self.lbl_m_path.pack(fill="x", padx=20, pady=(6, 2))
        self.w_m_path = w_path = Card(inner, pal["card_hi"], pal["card"], radius=10, auto_height=True)
        w_path.pack(fill="x", padx=20)
        self.modal_path_entry = PlaceholderEntry(w_path.inner, self, "C:\\игры\\client.jar или .exe", pal["card_hi"])
        self.modal_path_entry.pack(side="left", fill="x", expand=True, padx=(10, 6), pady=7)
        self.modal_chip_browse = Chip(w_path.inner, self, "Обзор…", False, self._pick_cheat_file, bg=pal["card_hi"])
        self.modal_chip_browse.pack(side="right", padx=(0, 6), pady=4)

        self.lbl_m_args = tk.Label(inner, text="Аргументы запуска (необязательно)", bg=pal["card"], fg=pal["text"],
                                   font=self.f_bold, anchor="w")
        self.lbl_m_args.pack(fill="x", padx=20, pady=(6, 2))
        self.w_m_args = w_args = Card(inner, pal["card_hi"], pal["card"], radius=10, auto_height=True)
        w_args.pack(fill="x", padx=20)
        self.modal_args_entry = PlaceholderEntry(w_args.inner, self, "Например: -Xmx4G или доп. флаги", pal["card_hi"])
        self.modal_args_entry.pack(fill="x", padx=10, pady=7)

        self.lbl_m_notes = tk.Label(inner, text="Описание / Заметки", bg=pal["card"], fg=pal["text"],
                                    font=self.f_bold, anchor="w")
        self.lbl_m_notes.pack(fill="x", padx=20, pady=(6, 2))
        self.w_m_notes = w_notes = Card(inner, pal["card_hi"], pal["card"], radius=10, auto_height=True)
        w_notes.pack(fill="x", padx=20)
        self.modal_notes_entry = PlaceholderEntry(w_notes.inner, self, "Горячие клавиши, функции или комментарий…", pal["card_hi"])
        self.modal_notes_entry.pack(fill="x", padx=10, pady=7)

        self.lbl_modal_err = tk.Label(inner, text="", bg=pal["card"], fg=pal["danger"],
                                      font=self.f_small, anchor="w")
        self.lbl_modal_err.pack(fill="x", padx=20, pady=(4, 0))

        self.modal_btn_bar = btn_bar = tk.Frame(inner, bg=pal["card"])
        btn_bar.pack(fill="x", padx=20, pady=(12, 18))

        self.modal_del_chip = Chip(btn_bar, self, "Удалить", False, self._modal_delete_click,
                                   color=pal["danger"], fg="#ffffff", bg=pal["card"])
        self.modal_chip_cancel = Chip(btn_bar, self, "Отмена", False, self.hide_cheat_modal, bg=pal["card"])
        self.modal_chip_cancel.pack(side="right", padx=(8, 0))
        self.modal_chip_save = Chip(btn_bar, self, "Сохранить", True, self.save_cheat_modal, font=self.f_bold, bg=pal["card"])
        self.modal_chip_save.pack(side="right")

    def _pick_cheat_file(self):
        init_dir = None
        cur = self.modal_path_entry.value()
        if cur and Path(cur).parent.exists():
            init_dir = str(Path(cur).parent)
        elif os.getenv("APPDATA"):
            mc = Path(os.getenv("APPDATA")) / ".minecraft"
            if mc.exists():
                init_dir = str(mc)

        filetypes = [
            ("Все поддерживаемые", "*.jar;*.exe;*.bat;*.cmd;*.lnk"),
            ("Java архивы (*.jar)", "*.jar"),
            ("Исполняемые файлы (*.exe)", "*.exe"),
            ("Пакетные скрипты (*.bat, *.cmd)", "*.bat;*.cmd"),
            ("Ярлыки (*.lnk)", "*.lnk"),
            ("Все файлы (*.*)", "*.*"),
        ]
        fn = filedialog.askopenfilename(
            parent=self,
            title="Выберите файл чита или клиента Minecraft",
            filetypes=filetypes,
            initialdir=init_dir,
        )
        if fn:
            self.modal_path_entry.set_text(fn)
            if not self.modal_name_entry.value():
                stem = Path(fn).stem.replace("_", " ").replace("-", " ").title()
                self.modal_name_entry.set_text(stem)

    def add_cheat_modal(self):
        self.editing_cheat_id = None
        self.lbl_modal_title.configure(text="Добавить игру / клиент")
        self.modal_name_entry.set_text("")
        self.modal_game_entry.set_text("Minecraft")
        self.modal_version_entry.set_text("")
        self.modal_path_entry.set_text("")
        self.modal_args_entry.set_text("")
        self.modal_notes_entry.set_text("")
        self.lbl_modal_err.configure(text="")
        self.modal_del_chip.pack_forget()
        self.modal_backdrop.place(x=0, y=0, relwidth=1, relheight=1)
        self.modal_backdrop.lift()
        self.after(50, lambda: self.modal_name_entry.focus_force())

    def edit_cheat(self, ch: dict):
        self.editing_cheat_id = ch["id"]
        self.lbl_modal_title.configure(text="Редактировать игру")
        self.modal_name_entry.set_text(ch.get("name", ""))
        self.modal_game_entry.set_text(ch.get("game", "Minecraft"))
        self.modal_version_entry.set_text(ch.get("version", ""))
        self.modal_path_entry.set_text(ch.get("path", ""))
        self.modal_args_entry.set_text(ch.get("args", ""))
        self.modal_notes_entry.set_text(ch.get("notes", ""))
        self.lbl_modal_err.configure(text="")
        self.modal_del_chip.pack(side="left")
        self.modal_backdrop.place(x=0, y=0, relwidth=1, relheight=1)
        self.modal_backdrop.lift()
        self.after(50, lambda: self.modal_name_entry.focus_force())

    def hide_cheat_modal(self):
        self.modal_backdrop.place_forget()

    def _modal_delete_click(self):
        if not self.editing_cheat_id:
            return
        for ch in self.cheats:
            if ch["id"] == self.editing_cheat_id:
                self.hide_cheat_modal()
                self.delete_cheat(ch)
                break

    def save_cheat_modal(self):
        name = self.modal_name_entry.value()
        if not name:
            self.lbl_modal_err.configure(text="Пожалуйста, введите название игры")
            return

        game = self.modal_game_entry.value() or ""
        version = self.modal_version_entry.value()
        path = self.modal_path_entry.value()
        args = self.modal_args_entry.value()
        notes = self.modal_notes_entry.value()

        if self.editing_cheat_id:
            for ch in self.cheats:
                if ch["id"] == self.editing_cheat_id:
                    ch["name"] = name
                    ch["game"] = game
                    ch["version"] = version
                    ch["path"] = path
                    ch["args"] = args
                    ch["notes"] = notes
                    break
            self.set_toast(f"Игра «{name}» сохранена")
        else:
            new_ch = {
                "id": f"cheat_{int(time.time() * 1000)}",
                "name": name,
                "game": game,
                "version": version,
                "path": path,
                "args": args,
                "notes": notes,
                "created_at": time.time(),
                "last_launched": None,
            }
            self.cheats.append(new_ch)
            self.set_toast(f"Чит «{name}» успешно добавлен")

        self.save_cheats()
        self.hide_cheat_modal()
        self.refresh_cheats_list()

    def set_cheats_filter(self, game: str):
        self.cheats_filter_game = game
        if hasattr(self, "btn_filter_all") and hasattr(self, "btn_filter_mc"):
            self.btn_filter_all.set_selected(game == "Все")
            self.btn_filter_mc.set_selected(game == "Minecraft")
        self.refresh_cheats_list()

    def set_toast(self, text: str, is_err: bool = False):
        if not hasattr(self, "lbl_cheats_toast") or not self.lbl_cheats_toast.winfo_exists():
            return
        fg = self.pal["danger"] if is_err else self.pal["ok"]
        self.lbl_cheats_toast.configure(text=text, fg=fg)
        if self._toast_timer:
            self.after_cancel(self._toast_timer)
            self._toast_timer = None
        if text:
            self._toast_timer = self.after(4000, lambda: self.set_toast(""))

    def open_minecraft_dir(self):
        appdata = os.getenv("APPDATA")
        if appdata:
            mc = Path(appdata) / ".minecraft"
            mc.mkdir(parents=True, exist_ok=True)
            os.startfile(str(mc))
            self.set_toast("Открыта папка .minecraft")
        else:
            self.set_toast("Переменная APPDATA не найдена", is_err=True)

    def open_mods_dir(self):
        appdata = os.getenv("APPDATA")
        if appdata:
            mods = Path(appdata) / ".minecraft" / "mods"
            mods.mkdir(parents=True, exist_ok=True)
            os.startfile(str(mods))
            self.set_toast("Открыта папка mods")
        else:
            self.set_toast("Переменная APPDATA не найдена", is_err=True)

    def open_file_location(self, path_str: str):
        try:
            p = Path(path_str)
            if p.exists():
                folder = p.parent if p.is_file() else p
                os.startfile(str(folder))
                self.set_toast("Открыта папка с файлом")
            else:
                self.set_toast(f"Файл не найден: {path_str}", is_err=True)
        except Exception as exc:
            self.set_toast(f"Ошибка открытия: {exc}", is_err=True)

    def launch_cheat(self, ch: dict):
        cid = ch["id"]
        if cid in self.running_processes and self.running_processes[cid].poll() is None:
            self.stop_cheat(ch)
            return

        path_str = ch.get("path", "").strip()
        if not path_str:
            self.set_toast(f"У чита «{ch['name']}» не указан файл. Выберите его.", is_err=True)
            self.edit_cheat(ch)
            return

        file_path = Path(path_str)
        if not file_path.exists():
            self.set_toast(f"Файл не найден: {file_path.name}. Проверьте путь.", is_err=True)
            return

        args_str = ch.get("args", "").strip()
        extra_args = args_str.split() if args_str else []

        try:
            proc = None
            ext = file_path.suffix.lower()
            work_dir = str(file_path.parent) if file_path.parent.exists() else None

            if ext == ".jar":
                javaw = shutil.which("javaw") or shutil.which("java")
                if javaw:
                    cmd = [javaw, "-jar", str(file_path)] + extra_args
                    proc = subprocess.Popen(cmd, cwd=work_dir)
                else:
                    os.startfile(str(file_path))
            elif ext in (".bat", ".cmd"):
                cmd = [str(file_path)] + extra_args
                proc = subprocess.Popen(cmd, cwd=work_dir, shell=True)
            elif ext == ".exe":
                cmd = [str(file_path)] + extra_args
                proc = subprocess.Popen(cmd, cwd=work_dir)
            else:
                os.startfile(str(file_path))

            ch["last_launched"] = time.time()
            self.save_cheats()

            if proc is not None:
                self.running_processes[cid] = proc
                self.set_toast(f"Запущен {ch['name']} (PID: {proc.pid})")
            else:
                self.set_toast(f"Запущен {ch['name']}")

            self.refresh_cheats_list()
        except Exception as exc:
            self.set_toast(f"Ошибка при запуске: {exc}", is_err=True)

    def stop_cheat(self, ch: dict):
        cid = ch["id"]
        proc = self.running_processes.get(cid)
        if proc:
            try:
                proc.terminate()
                self.after(600, lambda: self._kill_proc_if_alive(cid, proc))
            except Exception:
                pass
            self.set_toast(f"Останавливается {ch['name']}…")
        else:
            self.set_toast(f"Процесс {ch['name']} не найден")
            self.refresh_cheats_list()

    def _kill_proc_if_alive(self, cid: str, proc: subprocess.Popen):
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        self.running_processes.pop(cid, None)
        self.refresh_cheats_list()
        self.set_toast("Процесс завершён")

    def delete_cheat(self, ch: dict):
        ok = messagebox.askyesno("Удалить чит", f"Вы действительно хотите удалить «{ch['name']}»?", parent=self)
        if not ok:
            return
        cid = ch["id"]
        if cid in self.running_processes:
            self.stop_cheat(ch)
        self.cheats = [c for c in self.cheats if c["id"] != cid]
        self.save_cheats()
        self.set_toast(f"Чит «{ch['name']}» удален")
        self.refresh_cheats_list()

    def refresh_cheats_list(self):
        if not hasattr(self, "cheats_cards_container") or not self.cheats_cards_container.winfo_exists():
            return
        for child in self.cheats_cards_container.winfo_children():
            child.destroy()

        query = self.cheats_search_entry.value().lower() if hasattr(self, "cheats_search_entry") else ""
        cat = getattr(self, "cheats_filter_game", "Все")

        filtered = []
        for ch in self.cheats:
            if cat != "Все" and ch.get("game", "").lower() != cat.lower():
                continue
            if query:
                match = (
                    query in ch.get("name", "").lower() or
                    query in ch.get("version", "").lower() or
                    query in ch.get("notes", "").lower() or
                    query in ch.get("path", "").lower()
                )
                if not match:
                    continue
            filtered.append(ch)

        if not filtered:
            self._render_empty_cheats()
            return

        for ch in filtered:
            self._render_cheat_card(ch)

        pal = self.pal
        card = Card(self.cheats_cards_container, pal["card"], pal["bg"], radius=16, auto_height=True)
        inner = card.inner
        box = tk.Frame(inner, bg=pal["card"])
        Chip(box, self, "+ Добавить игру", True, self.add_cheat_modal, font=self.f_bold, bg=pal["card"]
             ).pack()

    def _render_cheat_card(self, ch: dict):
        pal = self.pal
        card = Card(self.cheats_cards_container, pal["card"], pal["bg"], radius=16, auto_height=True)
        card.pack(fill="x", pady=(0, 10), padx=(0, 14))
        inner = card.inner

        cid = ch["id"]
        is_running = cid in self.running_processes and self.running_processes[cid].poll() is None

        row = tk.Frame(inner, bg=pal["card"])
        row.pack(fill="x", padx=14, pady=12)

        icon_canvas = tk.Canvas(row, width=44, height=44, bg=pal["card"], highlightthickness=0, bd=0)
        icon_canvas.pack(side="left", padx=(0, 14))
        badge_bg = pal["accent"] if is_running else pal["card_hi"]
        draw_round(icon_canvas, 1, 1, 43, 43, 12, badge_bg)
        icon_canvas.create_text(22, 22, text="MC", font=self.f_bold,
                                fill=pal["on_accent"] if is_running else pal["accent"])

        info_col = tk.Frame(row, bg=pal["card"])
        info_col.pack(side="left", fill="both", expand=True)

        line1 = tk.Frame(info_col, bg=pal["card"])
        line1.pack(fill="x")
        tk.Label(line1, text=ch.get("name", "Без названия"), bg=pal["card"], fg=pal["text"],
                 font=self.f_bold).pack(side="left")

        game_name = ch.get("game", "Minecraft")
        lbl_game = tk.Label(line1, text=f" {game_name} ", bg=pal["card_hi"], fg=pal["dim"],
                            font=self.f_small)
        lbl_game.pack(side="left", padx=(8, 8))

        if is_running:
            pid = self.running_processes[cid].pid
            lbl_status = tk.Label(line1, text=f"● Запущен (PID: {pid})", bg=pal["card"], fg=pal["ok"],
                                  font=self.f_small)
        else:
            lbl_status = tk.Label(line1, text="● Готов к запуску", bg=pal["card"], fg=pal["dim"],
                                  font=self.f_small)
        lbl_status.pack(side="left")

        ver = ch.get("version", "")
        notes = ch.get("notes", "")
        ver_text = ver + (f" · {notes}" if notes else "")
        if ver_text:
            line2 = tk.Frame(info_col, bg=pal["card"])
            line2.pack(fill="x", pady=(2, 0))
            tk.Label(line2, text=ver_text, bg=pal["card"], fg=pal["dim"],
                     font=self.f_small, anchor="w").pack(side="left")

        path_val = ch.get("path", "").strip()
        line3 = tk.Frame(info_col, bg=pal["card"])
        line3.pack(fill="x", pady=(3, 0))
        if path_val:
            exists = Path(path_val).exists()
            short_path = path_val if len(path_val) < 55 else "..." + path_val[-52:]
            path_color = pal["dim"] if exists else pal["danger"]
            path_prefix = "📁 " if exists else "⚠️ (файл не найден) "
            lbl_path = tk.Label(line3, text=path_prefix + short_path, bg=pal["card"], fg=path_color,
                                font=self.f_small, anchor="w", cursor="hand2" if exists else "")
            lbl_path.pack(side="left")
            if exists:
                lbl_path.bind("<Button-1>", lambda e, p=path_val: self.open_file_location(p))
        else:
            lbl_path = tk.Label(line3, text="⚠️ Файл не указан (нажмите «Изменить» для выбора)",
                                bg=pal["card"], fg=pal["danger"], font=self.f_small, anchor="w")
            lbl_path.pack(side="left")

        last_l = ch.get("last_launched")
        if last_l:
            last_str = f"Последний запуск: {fmt_time(last_l)} ({time.strftime('%d.%m.%Y', time.localtime(last_l))})"
            line4 = tk.Frame(info_col, bg=pal["card"])
            line4.pack(fill="x", pady=(1, 0))
            tk.Label(line4, text=last_str, bg=pal["card"], fg=pal["dim"],
                     font=self.f_small, anchor="w").pack(side="left")

        btn_col = tk.Frame(row, bg=pal["card"])
        btn_col.pack(side="right", padx=(10, 0))

        if is_running:
            Chip(btn_col, self, "⏹ Остановить", False, lambda c=ch: self.stop_cheat(c),
                 color=pal["danger"], fg="#ffffff", bg=pal["card"]).pack(side="left", padx=4)
        else:
            Chip(btn_col, self, "▶ Запустить", True, lambda c=ch: self.launch_cheat(c),
                 color=pal["ok"], fg="#ffffff", bg=pal["card"]).pack(side="left", padx=4)

        Chip(btn_col, self, "✎", False, lambda c=ch: self.edit_cheat(c), bg=pal["card"]).pack(side="left", padx=4)
        Chip(btn_col, self, "✕", False, lambda c=ch: self.delete_cheat(c), bg=pal["card"]).pack(side="left", padx=4)

    def _render_empty_cheats(self):
        pal = self.pal
        card = Card(self.cheats_cards_container, pal["card"], pal["bg"], radius=16, auto_height=True)
        card.pack(fill="x", pady=(0, 14), padx=(0, 14))
        inner = card.inner

        box = tk.Frame(inner, bg=pal["card"])
        box.pack(fill="both", expand=True, padx=20, pady=32)

        icon_c = tk.Canvas(box, width=54, height=54, bg=pal["card"], highlightthickness=0, bd=0)
        icon_c.pack(pady=(0, 10))
        draw_round(icon_c, 2, 2, 52, 52, 16, pal["card_hi"])
        icon_c.create_text(27, 27, text="🎮", font=self.f_title)

        tk.Label(box, text="игры или клиенты пока не найдены", bg=pal["card"], fg=pal["text"],
                 font=self.f_bold).pack(pady=(0, 4))
        tk.Label(box, text="Добавьте вашу первую игру или клиент (.exe), чтобы запускать его в один клик.",
                 bg=pal["card"], fg=pal["dim"], font=self.f_small, justify="center").pack(pady=(0, 16))

        Chip(box, self, "+ Добавить игру", True, self.add_cheat_modal, font=self.f_bold, bg=pal["card"]
             ).pack()


def main():
    enable_dpi_awareness()
    ap = argparse.ArgumentParser(description="encrypted chat GUI client")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    App(args.host, args.port).mainloop()


if __name__ == "__main__":
    main()
