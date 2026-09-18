"""Widget flutuante do AURUM: janela pequena, sempre visível, por cima do MetaTrader 5.

Mostra o sinal atual (ENTRAR AGORA, PREPARE-SE, SAIR AGORA, PAUSA…), ativo, tempo gráfico, entrada, stop, alvo,
risco e confiança — os mesmos números do painel e da boleta. Atualiza sozinho logo depois do fechamento de cada
candle, a cada poucos segundos (preço) e na hora em que o AURUM detecta mudança de sinal.

Uso: WIDGET_AURUM.bat · ou  pythonw -m widget  [--symbol XAUUSD] [--tf 15m]
Teclas (com o widget em foco): Esc compacto/expandido · + e − opacidade · R atualizar · T tempo gráfico ·
Ctrl+Q fechar. Atalho global (qualquer janela em foco): Ctrl+Alt+A mostra/esconde. Botão direito: menu.
"""

from __future__ import annotations

import argparse
import queue
import sys
import time
import tkinter as tk
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tkinter import simpledialog

from app.config import DISCLAIMER

from . import client
from .client import (ALERT_SECONDS, LIVE_ACTIONS, LIVE_SECONDS, TIMEFRAMES, AurumApi, ApiError, SignalView,
                     WidgetConfig, build_view, countdown, fmt_num, next_refresh, panel_url, start_server)
from .hotkey import GlobalHotkey, pretty

BG, PANEL, PANEL_2, LINE = "#0b0e14", "#121722", "#1a2130", "#263045"
TEXT, MUTED, GOLD, AMBER = "#e8e6e1", "#8a8f98", "#d4a93a", "#f5a524"
UI_FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica"
MONO_FONT = "Consolas" if sys.platform == "win32" else "Courier"
WIDTH = 336  # largura mínima em px (escala junto com o DPI do monitor)
BOOT_SECONDS = 90  # tempo para o AURUM ligar e fazer a primeira análise


class AurumWidget:
    def __init__(self, root: tk.Tk, api: AurumApi, cfg: WidgetConfig, *, config_path: Path | None = None,
                 use_hotkey: bool = True, autostart: bool = True):
        self.root, self.api, self.cfg = root, api, cfg
        self.config_path = config_path or client.CONFIG_PATH
        self.view: SignalView | None = None
        self.last_key: tuple | None = None
        self.alert_id: int | None = None
        self.offline = False
        self.server_started_at = 0.0
        self._bar_value = (0.0, MUTED)
        self.hidden = False
        self.favorites: list[dict] = []
        self.status = "Conectando ao AURUM…"
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="aurum-widget")
        self._results: queue.Queue = queue.Queue()
        self._inflight: set[str] = set()
        self._timers: dict[str, str] = {}
        self._drag = (0, 0)
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        self.hotkey: GlobalHotkey | None = None

        self._window()
        self._build()
        self._bind_keys()
        self.set_compact(cfg.compact, save=False)
        self._place()
        if use_hotkey:
            self.hotkey = GlobalHotkey(cfg.hotkey, lambda: self._results.put(("hotkey", None)))
            self.hotkey.start()
        self.root.after(100, self._drain)
        if autostart:
            self.refresh()
            self._every("live", LIVE_SECONDS, self._poll_live)
            self._every("alerts", ALERT_SECONDS, self._poll_alerts)
            self._every("tick", 1, self._tick)
            self._submit("favorites", self.api.favorites)

    # ------------------------------------------------------------ janela

    def px(self, value: float) -> int:
        return int(round(value * self.scale))

    def _window(self) -> None:
        r = self.root
        r.title("AURUM · widget")
        r.overrideredirect(True)  # sem barra de título: arraste pelo topo
        r.configure(bg=GOLD)  # 1 px de moldura dourada
        r.attributes("-topmost", self.cfg.topmost)
        r.attributes("-alpha", self.cfg.opacity)
        icon = client.ROOT / "static" / "icons" / "aurum-192.png"
        try:
            r.iconphoto(True, tk.PhotoImage(file=str(icon)))
        except tk.TclError:
            pass
        r.protocol("WM_DELETE_WINDOW", self.quit)

    def _label(self, parent, text="", *, size=9, weight="normal", fg=TEXT, bg=BG, mono=False, **kw) -> tk.Label:
        return tk.Label(parent, text=text, fg=fg, bg=bg, font=(MONO_FONT if mono else UI_FONT, size, weight),
                        bd=0, **kw)

    def _build(self) -> None:
        self.frame = tk.Frame(self.root, bg=BG)
        self.frame.pack(fill="both", expand=True, padx=1, pady=1)

        # Cabeçalho (sempre visível; é por onde se arrasta a janela)
        self.header = tk.Frame(self.frame, bg=PANEL, padx=self.px(6), pady=self.px(4))
        self.header.pack(fill="x")
        self.pill = self._label(self.header, "…", size=8, weight="bold", fg=BG, bg=MUTED, padx=5)
        self.pill.pack(side="left")
        self.symbol_label = self._label(self.header, "—", size=10, weight="bold", bg=PANEL, padx=6)
        self.symbol_label.pack(side="left")
        self.tf_label = self._label(self.header, self.cfg.timeframe, size=8, weight="bold", fg=GOLD, bg=PANEL_2, padx=4,
                                    cursor="hand2")
        self.tf_label.pack(side="left")
        self.close_btn = self._label(self.header, "×", size=11, weight="bold", fg=MUTED, bg=PANEL, padx=4, cursor="hand2")
        self.close_btn.pack(side="right")
        self.compact_btn = self._label(self.header, "▾", size=10, fg=MUTED, bg=PANEL, padx=4, cursor="hand2")
        self.compact_btn.pack(side="right")
        self.menu_btn = self._label(self.header, "⋮", size=10, weight="bold", fg=MUTED, bg=PANEL, padx=4, cursor="hand2")
        self.menu_btn.pack(side="right")
        self.count_label = self._label(self.header, "", size=8, fg=MUTED, bg=PANEL, mono=True, padx=4)
        self.count_label.pack(side="right")
        self.price_label = self._label(self.header, "", size=10, weight="bold", bg=PANEL, mono=True)
        self.price_label.pack(side="right")

        # Corpo (some no modo compacto)
        self.body = tk.Frame(self.frame, bg=BG, padx=self.px(10), pady=self.px(8))
        tk.Frame(self.body, bg=BG, width=self.px(WIDTH - 22), height=0).pack()  # largura mínima estável
        top = tk.Frame(self.body, bg=BG)
        top.pack(fill="x")
        self.action_label = self._label(top, "AURUM", size=15, weight="bold", fg=MUTED)
        self.action_label.pack(side="left")
        self.side_label = self._label(top, "", size=10, weight="bold", fg=MUTED, padx=6)
        self.side_label.pack(side="left", pady=(self.px(4), 0))
        self.plan_tag = self._label(top, "", size=7, weight="bold", fg=MUTED, bg=PANEL_2, padx=4)
        self.plan_tag.pack(side="right")
        self.headline = self._label(self.body, self.status, size=9, fg=TEXT, justify="left", anchor="w",
                                    wraplength=self.px(WIDTH - 26))
        self.headline.pack(fill="x", pady=(self.px(2), 0))
        self.window_label = self._label(self.body, "", size=8, weight="bold", fg=GOLD, anchor="w")
        self.window_label.pack(fill="x")

        grid = tk.Frame(self.body, bg=PANEL, padx=self.px(8), pady=self.px(6))
        grid.pack(fill="x", pady=(self.px(6), 0))
        grid.columnconfigure(1, weight=1)
        self.cells: dict[str, tuple[tk.Label, tk.Label]] = {}
        self.names: dict[str, tk.Label] = {}
        rows = (("entry", "Entrada"), ("stop", "Stop loss"), ("target", "Take profit"), ("risk", "Risco"),
                ("gain", "Ganho no alvo"), ("size", "Tamanho"), ("confidence", "Confiança"))
        for i, (key, name) in enumerate(rows):
            self.names[key] = self._label(grid, name, size=8, fg=MUTED, bg=PANEL, anchor="w")
            self.names[key].grid(row=i, column=0, sticky="w")
            value = self._label(grid, "—", size=10, weight="bold", bg=PANEL, mono=True, anchor="e")
            value.grid(row=i, column=1, sticky="e", padx=(self.px(6), 0))
            detail = self._label(grid, "", size=7, fg=MUTED, bg=PANEL, anchor="e")
            detail.grid(row=i, column=2, sticky="e", padx=(self.px(6), 0))
            self.cells[key] = (value, detail)
        self.bar = tk.Canvas(grid, height=self.px(4), bg=PANEL_2, highlightthickness=0, bd=0)
        self.bar.grid(row=len(rows), column=0, columnspan=3, sticky="ew", pady=(self.px(4), 0))
        self.bar.bind("<Configure>", lambda e: self._draw_bar(*self._bar_value))

        self.warn_label = self._label(self.body, "", size=8, fg=AMBER, justify="left", anchor="w",
                                      wraplength=self.px(WIDTH - 26))
        self.warn_label.pack(fill="x", pady=(self.px(6), 0))
        self.action_btn = self._label(self.body, "", size=9, weight="bold", fg=BG, bg=GOLD, padx=8, pady=3,
                                      cursor="hand2")

        foot = tk.Frame(self.body, bg=BG)
        foot.pack(fill="x", pady=(self.px(6), 0))
        self.source_label = self._label(foot, "", size=7, fg=MUTED, anchor="w")
        self.source_label.pack(side="left")
        self.opacity_value = self._label(foot, "", size=7, fg=MUTED, mono=True)
        self.opacity_value.pack(side="right")
        self.opacity = tk.Scale(foot, from_=30, to=100, orient="horizontal", showvalue=False, length=self.px(70),
                                sliderlength=self.px(10), width=self.px(8), bd=0, highlightthickness=0, bg=MUTED,
                                troughcolor=LINE, activebackground=GOLD, cursor="hand2", command=self._on_opacity)
        self.opacity.set(round(self.cfg.opacity * 100))
        self.opacity_value.configure(text=f"{round(self.cfg.opacity * 100)}%")
        self.opacity.pack(side="right", padx=(self.px(4), self.px(3)))
        self._label(foot, "◐ opacidade", size=7, fg=MUTED).pack(side="right")
        self.disclaimer = self._label(self.body, DISCLAIMER, size=6, fg=MUTED, justify="left", anchor="w",
                                      wraplength=self.px(WIDTH - 26))
        self.disclaimer.pack(fill="x", pady=(self.px(4), 0))

        for widget in (self.header, self.pill, self.symbol_label, self.price_label, self.count_label):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
            widget.bind("<Double-Button-1>", lambda e: self.set_compact(not self.cfg.compact))
        self.tf_label.bind("<Button-1>", lambda e: self.next_timeframe())
        self.close_btn.bind("<Button-1>", lambda e: self.quit())
        self.compact_btn.bind("<Button-1>", lambda e: self.set_compact(not self.cfg.compact))
        self.menu_btn.bind("<Button-1>", self._show_menu)
        self.action_btn.bind("<Button-1>", lambda e: self._start_aurum())
        self.root.bind_all("<Button-3>", self._show_menu)

    def _bind_keys(self) -> None:
        r = self.root
        r.bind("<Escape>", lambda e: self.set_compact(not self.cfg.compact))
        for key in ("<plus>", "<KP_Add>", "<equal>"):
            r.bind(key, lambda e: self.set_opacity(self.cfg.opacity + 0.1))
        for key in ("<minus>", "<KP_Subtract>"):
            r.bind(key, lambda e: self.set_opacity(self.cfg.opacity - 0.1))
        r.bind("<KeyPress-r>", lambda e: self.refresh())
        r.bind("<KeyPress-t>", lambda e: self.next_timeframe())
        r.bind("<Control-q>", lambda e: self.quit())
        r.bind("<Button-1>", lambda e: r.focus_force(), add="+")

    def _place(self) -> None:
        self.root.update_idletasks()
        width = self.root.winfo_reqwidth()
        left, top, right, bottom = _virtual_screen(self.root)
        x = self.cfg.x if self.cfg.x is not None else right - width - self.px(24)
        y = self.cfg.y if self.cfg.y is not None else top + self.px(90)
        x = min(max(x, left), right - self.px(60))  # monitor desligado: traz a janela de volta para a tela
        y = min(max(y, top), bottom - self.px(40))
        self.root.geometry(f"+{int(x)}+{int(y)}")

    def _drag_start(self, event) -> None:
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event) -> None:
        self.root.geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    def _drag_end(self, _event) -> None:
        self.cfg.x, self.cfg.y = self.root.winfo_x(), self.root.winfo_y()
        self.cfg.save(self.config_path)

    # ------------------------------------------------------------ ações

    def set_compact(self, compact: bool, save: bool = True) -> None:
        self.cfg.compact = compact
        if compact:
            self.body.pack_forget()
        else:
            self.body.pack(fill="x")
        self.compact_btn.configure(text="▸" if compact else "▾")
        self.root.geometry("")  # a janela acompanha o conteúdo (a posição continua a mesma)
        if save:
            self.cfg.save(self.config_path)

    def set_opacity(self, value: float) -> None:
        self.cfg.opacity = round(min(1.0, max(0.3, value)), 2)
        self.root.attributes("-alpha", self.cfg.opacity)
        self.opacity_value.configure(text=f"{round(self.cfg.opacity * 100)}%")
        if int(self.opacity.get()) != round(self.cfg.opacity * 100):
            self.opacity.set(round(self.cfg.opacity * 100))
        self.cfg.save(self.config_path)

    def _on_opacity(self, value: str) -> None:
        if abs(float(value) / 100 - self.cfg.opacity) >= 0.01:
            self.set_opacity(float(value) / 100)

    def set_topmost(self, on: bool) -> None:
        self.cfg.topmost = on
        self.root.attributes("-topmost", on)
        self.cfg.save(self.config_path)

    def toggle_visible(self) -> None:
        if self.hidden:
            self.show()
        else:
            self.hidden = True
            self.root.withdraw()

    def show(self) -> None:
        self.hidden = False
        self.root.deiconify()
        self.root.after(20, lambda: self.root.attributes("-topmost", self.cfg.topmost))
        self.root.lift()

    def set_symbol(self, symbol: str) -> None:
        self.cfg.symbol = symbol.strip()
        self.view = None
        self.last_key = None
        self.symbol_label.configure(text=self.cfg.symbol.upper())
        self.headline.configure(text=f"Analisando {self.cfg.symbol.upper()}…")
        self.refresh()

    def set_timeframe(self, timeframe: str) -> None:
        self.cfg.timeframe = timeframe
        self.tf_label.configure(text=timeframe)
        self.last_key = None
        self.cfg.save(self.config_path)
        self.headline.configure(text=f"Analisando no {timeframe}…")
        self.refresh()

    def next_timeframe(self) -> None:
        self.set_timeframe(TIMEFRAMES[(TIMEFRAMES.index(self.cfg.timeframe) + 1) % len(TIMEFRAMES)])

    def open_panel(self) -> None:
        webbrowser.open(panel_url(self.api.base_url, self.view.symbol_key if self.view else self.cfg.symbol,
                                  self.cfg.timeframe))

    def ask_symbol(self) -> None:
        self.root.attributes("-topmost", False)  # a caixa de diálogo precisa ficar por cima
        try:
            text = simpledialog.askstring("AURUM", "Ativo (ex.: XAUUSD, EURUSD, BTC, PETR4):", parent=self.root)
        finally:
            self.root.attributes("-topmost", self.cfg.topmost)
        if text and text.strip():
            self.set_symbol(text)

    def quit(self) -> None:
        self.cfg.x, self.cfg.y = self.root.winfo_x(), self.root.winfo_y()
        self.cfg.save(self.config_path)
        if self.hotkey:
            self.hotkey.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

    def _booting(self) -> bool:
        return bool(self.server_started_at) and time.time() - self.server_started_at < BOOT_SECONDS

    def _start_aurum(self) -> None:
        if not self.offline or self._booting():
            return
        start_server()
        self.server_started_at = time.time()
        self.status = "Ligando o AURUM… (a primeira análise leva alguns segundos)"
        self.render_offline()
        self._schedule("analysis", 3, self.refresh)

    # ------------------------------------------------------------ busca de dados (threads) → interface (main)

    def _submit(self, kind: str, fn, *args) -> None:
        if kind in self._inflight:
            return
        self._inflight.add(kind)
        future: Future = self._pool.submit(fn, *args)
        future.add_done_callback(lambda f: self._results.put((kind, f)))

    def _drain(self) -> None:
        try:
            while True:
                kind, future = self._results.get_nowait()
                if kind == "hotkey":
                    self.toggle_visible()
                    continue
                self._inflight.discard(kind)
                try:
                    self._handle(kind, future.result())
                except ApiError as exc:
                    self._handle_error(kind, exc)
                except Exception as exc:  # nunca deixa o widget morrer por uma resposta inesperada
                    self._handle_error(kind, ApiError(f"Resposta inesperada do AURUM: {exc}"))
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _every(self, name: str, seconds: float, fn) -> None:
        def run():
            fn()
            self._timers[name] = self.root.after(int(seconds * 1000), run)
        self._timers[name] = self.root.after(int(seconds * 1000), run)

    def _schedule(self, name: str, seconds: float, fn) -> None:
        if name in self._timers:
            self.root.after_cancel(self._timers[name])
        self._timers[name] = self.root.after(int(max(0.2, seconds) * 1000), fn)

    def refresh(self) -> None:
        self._submit("analysis", self.api.analysis, self.cfg.symbol, self.cfg.timeframe)

    def _poll_live(self) -> None:
        if self.view and not self.offline and self.view.market_open and not self.hidden:
            self._submit("live", self.api.live, self.view.symbol_key, self.view.timeframe, self.view.bar_start)

    def _poll_alerts(self) -> None:
        if not self.offline:
            self._submit("alerts", self.api.alerts, self.alert_id or 0)

    def _tick(self) -> None:
        if self.view:
            self.count_label.configure(text=countdown(self.view.close_at, self.view.timeframe))

    def _handle(self, kind: str, data) -> None:
        if kind == "analysis":
            was_offline, self.offline = self.offline, False
            view = build_view(data)
            if view.symbol_key != self.cfg.symbol:  # nome digitado (XAUUSD) → código do AURUM (GC=F)
                self.cfg.symbol = view.symbol_key
                self.cfg.save(self.config_path)
            self.render(view, was_offline)
            self._schedule("analysis", next_refresh(view.close_at, view.timeframe), self.refresh)
        elif kind == "live" and self.view:
            price = (data.get("candle") or {}).get("price")
            if price is not None and data.get("symbol") == self.view.symbol_key:
                self.price_label.configure(text=fmt_num(price, self.view.decimals))
        elif kind == "alerts":
            items = data or []
            newest = max((int(a["id"]) for a in items), default=self.alert_id or 0)
            first = self.alert_id is None
            self.alert_id = newest
            mine = [a for a in items if a.get("symbol") == self.cfg.symbol and a.get("timeframe") == self.cfg.timeframe]
            if mine and not first:
                self.refresh()  # o AURUM mudou o sinal: busca a análise nova na hora
        elif kind == "favorites":
            self.favorites = data or []

    def _handle_error(self, kind: str, exc: ApiError) -> None:
        if kind != "analysis":
            return  # preço ao vivo, alertas e favoritos: a próxima análise mostra o estado real
        if not exc.offline:  # ex.: ativo não encontrado
            self.headline.configure(text=str(exc))
            self._schedule("analysis", 30, self.refresh)
            return
        self.offline = True
        if self.cfg.start_server and not self.server_started_at:
            self._start_aurum()  # liga o AURUM sozinho uma vez
            return
        booting = self._booting()
        self.status = ("Ligando o AURUM… (a primeira análise leva alguns segundos)" if booting else
                       "AURUM desligado. Clique em “Ligar AURUM” ou abra o INICIAR_AURUM.bat.")
        self.render_offline()
        self._schedule("analysis", 3 if booting else 5, self.refresh)

    # ------------------------------------------------------------ desenho

    def render(self, v: SignalView, was_offline: bool = False) -> None:
        changed = self.last_key is not None and v.key != self.last_key
        first = self.last_key is None
        self.view, self.last_key = v, v.key
        self.pill.configure(text=f" {v.title} ", bg=v.color)
        self.symbol_label.configure(text=v.symbol)
        self.tf_label.configure(text=v.timeframe)
        self.price_label.configure(text=fmt_num(v.price, v.decimals))
        self.count_label.configure(text=countdown(v.close_at, v.timeframe))
        self.action_label.configure(text=v.title, fg=v.color)
        self.side_label.configure(text=v.side_text, fg=v.color if v.action in LIVE_ACTIONS else MUTED)
        self.plan_tag.configure(text="SUA OPERAÇÃO" if v.position else "SIMULAÇÃO" if v.simulated else "ENVIAR AGORA",
                                fg=MUTED if v.simulated else v.color)
        self.headline.configure(text=v.headline)
        self.window_label.configure(text=v.window)
        values = {"entry": (v.entry, ""), "stop": (v.stop, v.stop_detail), "target": (v.target, v.target_detail),
                  "risk": (v.risk, v.risk_detail), "gain": (v.gain, v.gain_detail), "size": (v.size, ""),
                  "confidence": (v.confidence, v.confidence_detail)}
        self.names["gain"].configure(text="Resultado" if v.position else "Ganho no alvo")
        self.names["size"].configure(text="Sua posição" if v.position else "Tamanho")
        for key, (value, detail) in values.items():
            label, small = self.cells[key]
            label.configure(text=value, fg={"stop": "#f0454f", "target": "#16c784"}.get(key, TEXT))
            small.configure(text=detail)
        self._draw_bar(v.confidence_value, v.color)
        self.warn_label.configure(text="\n".join(f"⚠ {w}" for w in v.warnings[:2]))
        self.action_btn.pack_forget()
        stamp = datetime.now().strftime("%H:%M:%S")
        self.source_label.configure(text=f"{v.source} · confiab. {v.reliability} · {stamp}")
        self.root.title(f"{v.title} · {v.symbol} — AURUM")
        if changed or (first and v.action in LIVE_ACTIONS):
            self._on_signal_change(v)

    def render_offline(self) -> None:
        self.pill.configure(text=" OFFLINE ", bg=MUTED)
        self.action_label.configure(text="AURUM", fg=MUTED)
        self.side_label.configure(text="")
        self.headline.configure(text=self.status)
        self.count_label.configure(text="")
        if not self._booting():
            self.action_btn.configure(text="▶  Ligar AURUM")
            self.action_btn.pack(anchor="w", pady=(self.px(6), 0), before=self.warn_label)

    def _draw_bar(self, value: float, color: str) -> None:
        self._bar_value = (value, color)
        width = max(1, self.bar.winfo_width())
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, width * max(0.0, min(100.0, value)) / 100, self.px(4), fill=color, width=0)

    def _on_signal_change(self, v: SignalView) -> None:
        important = v.action in LIVE_ACTIONS
        if important and self.hidden and self.cfg.show_on_signal:
            self.show()
        if important and self.cfg.beep:
            _beep()
        self._flash(v.color, 8 if important else 4)

    def _flash(self, color: str, times: int) -> None:
        if times <= 0:
            self.root.configure(bg=GOLD)
            self.header.configure(bg=PANEL)
            return
        on = times % 2 == 0
        self.root.configure(bg=color if on else GOLD)
        self.header.configure(bg=PANEL_2 if on else PANEL)
        self.root.after(220, lambda: self._flash(color, times - 1))

    # ------------------------------------------------------------ menu (botão direito ou ⋮)

    def _show_menu(self, event) -> None:
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=TEXT, activebackground=GOLD, activeforeground=BG, bd=0)
        menu.add_command(label="Atualizar agora  (R)", command=self.refresh)
        menu.add_command(label="Abrir o painel no navegador", command=self.open_panel)
        menu.add_separator()

        assets = tk.Menu(menu, tearoff=0, bg=PANEL, fg=TEXT, activebackground=GOLD, activeforeground=BG)
        for fav in self.favorites[:20]:
            label = f"{fav['symbol']}  ·  {fav.get('name') or ''}".strip(" ·")
            assets.add_command(label=label, command=lambda s=fav["symbol"]: self.set_symbol(s))
        if self.favorites:
            assets.add_separator()
        assets.add_command(label="Outro ativo…", command=self.ask_symbol)
        menu.add_cascade(label=f"Ativo  ({self.view.symbol if self.view else self.cfg.symbol})", menu=assets)

        frames = tk.Menu(menu, tearoff=0, bg=PANEL, fg=TEXT, activebackground=GOLD, activeforeground=BG)
        tf_var = tk.StringVar(value=self.cfg.timeframe)
        for tf in TIMEFRAMES:
            frames.add_radiobutton(label=tf, value=tf, variable=tf_var, command=lambda t=tf: self.set_timeframe(t))
        menu.add_cascade(label=f"Tempo gráfico  ({self.cfg.timeframe})", menu=frames)

        alpha = tk.Menu(menu, tearoff=0, bg=PANEL, fg=TEXT, activebackground=GOLD, activeforeground=BG)
        for pct in (100, 90, 80, 70, 60, 50, 40):
            alpha.add_command(label=f"{pct}%", command=lambda p=pct: self.set_opacity(p / 100))
        menu.add_cascade(label=f"Opacidade  ({round(self.cfg.opacity * 100)}%)", menu=alpha)
        menu.add_separator()

        self._menu_vars = [tk.BooleanVar(value=self.cfg.compact), tk.BooleanVar(value=self.cfg.topmost),
                           tk.BooleanVar(value=self.cfg.beep), tk.BooleanVar(value=self.cfg.show_on_signal)]
        compact, topmost, beep, show = self._menu_vars
        menu.add_checkbutton(label="Compacto  (Esc)", variable=compact, command=lambda: self.set_compact(compact.get()))
        menu.add_checkbutton(label="Sempre por cima das outras janelas", variable=topmost,
                             command=lambda: self.set_topmost(topmost.get()))
        menu.add_checkbutton(label="Som em ENTRAR AGORA / SAIR AGORA", variable=beep,
                             command=lambda: self._set_flag("beep", beep.get()))
        menu.add_checkbutton(label="Reaparecer sozinho quando houver sinal", variable=show,
                             command=lambda: self._set_flag("show_on_signal", show.get()))
        hotkey_text = (f"Mostrar/esconder: {pretty(self.cfg.hotkey)}" if self.hotkey and not self.hotkey.error
                       else f"Atalho global indisponível ({self.hotkey.error})" if self.hotkey and self.hotkey.error
                       else "Atalho global desligado")
        menu.add_command(label=hotkey_text, state="disabled")
        menu.add_command(label="Esconder agora", command=self.toggle_visible)
        menu.add_separator()
        menu.add_command(label="Fechar o widget  (Ctrl+Q)", command=self.quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _set_flag(self, name: str, value: bool) -> None:
        setattr(self.cfg, name, value)
        self.cfg.save(self.config_path)


def _beep() -> None:
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except (ImportError, RuntimeError):
        pass


def _virtual_screen(root: tk.Tk) -> tuple[int, int, int, int]:
    """Área de todos os monitores (esquerda, topo, direita, base) — a janela pode ficar no segundo monitor."""
    if sys.platform == "win32":
        try:
            import ctypes
            metric = ctypes.windll.user32.GetSystemMetrics
            left, top, width, height = metric(76), metric(77), metric(78), metric(79)
            if width and height:
                return left, top, left + width, top + height
        except (AttributeError, OSError):
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _single_instance() -> bool:
    """Só um widget por vez (Windows): um segundo clique no .bat não abre outra janela."""
    if sys.platform != "win32":
        return True
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "AURUM_widget_flutuante")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Widget flutuante do AURUM (fica por cima do MT5)")
    parser.add_argument("--symbol", help="ativo, ex.: XAUUSD, EURUSD, BTC")
    parser.add_argument("--tf", choices=TIMEFRAMES, help="tempo gráfico")
    parser.add_argument("--opacity", type=float, help="0.3 a 1.0")
    parser.add_argument("--url", default=client.DEFAULT_URL, help="endereço do AURUM")
    parser.add_argument("--hotkey", help="atalho global, ex.: ctrl+alt+a")
    parser.add_argument("--no-hotkey", action="store_true", help="sem atalho global")
    args = parser.parse_args(argv)
    if not _single_instance():
        return
    if sys.platform == "win32":
        try:  # texto nítido em monitores com zoom (125%, 150%…)
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    cfg = WidgetConfig.load()
    if args.symbol:
        cfg.symbol = args.symbol
    if args.tf:
        cfg.timeframe = args.tf
    if args.opacity:
        cfg.opacity = min(1.0, max(0.3, args.opacity))
    if args.hotkey:
        cfg.hotkey = args.hotkey
    root = tk.Tk()
    AurumWidget(root, AurumApi(args.url), cfg, use_hotkey=not args.no_hotkey)
    root.mainloop()


if __name__ == "__main__":
    main()
