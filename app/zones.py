"""Suportes e resistências principais dos gráficos mensal, semanal e diário, usados como gatilho no M15.

Como funciona (sem olhar o futuro):
1. Do histórico diário saem os candles semanais e mensais. Em cada tempo gráfico procuramos topos e fundos
   (pivôs) — um pivô só existe depois que os k candles seguintes FECHARAM.
2. Os níveis conhecidos em cada momento são agrupados em zonas (tolerância em ATR diário). O peso da zona
   soma o tempo gráfico de cada toque: mensal 3, semanal 2, diário 1. Zona principal = peso ≥ 2.
3. No gráfico de entrada (ex.: M15), cada candle usa só as zonas conhecidas até o fechamento dele e marca:
   toque e rejeição no suporte/resistência, rompimento, a zona escolhida, o stop do outro lado da zona e o
   espaço até a zona seguinte.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .levels import find_pivots

TIMEFRAMES = (  # código, nome, peso, k (candles de cada lado para confirmar o pivô), janela de validade
    ("M", "Mensal", 3, 2, None),
    ("W", "Semanal", 2, 2, pd.Timedelta(days=3 * 365)),
    ("D", "Diário", 1, 3, pd.Timedelta(days=270)),
)
CLUSTER_ATR = 0.5  # níveis a menos de 0,5 ATR diário viram a mesma zona
MAX_WIDTH_ATR = 1.0  # uma zona nunca passa de 1 ATR diário de largura
PAD_ATR = 0.08  # folga das bordas da zona
MIN_WEIGHT = 2  # zona principal: um pivô semanal/mensal ou pelo menos dois diários
TOUCH_ATR = 1.0  # até 1 ATR do gráfico de entrada além da borda ainda conta como toque
BREAK_ATR = 0.1  # rompimento: fechar pelo menos 0,1 ATR além da borda
STOP_ATR = 0.25  # stop fica 0,25 ATR além da borda oposta da zona
MIN_STOP_ATR = 0.5  # stop nunca mais curto que 0,5 ATR (ruído)


@dataclass
class Zones:
    low: np.ndarray
    high: np.ndarray
    weight: np.ndarray
    label: list[str]

    @property
    def mid(self) -> np.ndarray:
        return (self.low + self.high) / 2

    def __len__(self) -> int:
        return len(self.low)


EMPTY = Zones(np.array([]), np.array([]), np.array([]), [])


def _periods(daily: pd.DataFrame, code: str) -> pd.DataFrame:
    ohlc = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if code == "D":
        frame = daily[["open", "high", "low", "close"]].copy()
        frame["end"] = frame.index + pd.Timedelta(days=1)
        return frame
    if code == "W":
        frame = daily.resample("W-MON", label="left", closed="left").agg(ohlc).dropna()
        frame["end"] = frame.index + pd.Timedelta(days=7)
        return frame
    frame = daily.resample("MS").agg(ohlc).dropna()
    frame["end"] = frame.index + pd.offsets.MonthBegin(1)
    return frame


def htf_levels(daily: pd.DataFrame) -> pd.DataFrame:
    """Todos os pivôs confirmados: preço, peso, tempo gráfico, quando aconteceu e quando passou a ser conhecido."""
    rows = []
    for code, name, weight, k, _ in TIMEFRAMES:
        frame = _periods(daily, code)
        if len(frame) < 2 * k + 1:
            continue
        piv = find_pivots(frame, k)
        ends = frame["end"].to_numpy()
        for idx, col in ((piv.high_idx, "high"), (piv.low_idx, "low")):
            for j in idx:
                if j + k >= len(frame):
                    continue
                rows.append({"price": float(frame[col].iat[j]), "code": code, "name": name, "weight": weight,
                             "at": frame.index[j], "known_at": pd.Timestamp(ends[j + k])})
    return pd.DataFrame(rows, columns=["price", "code", "name", "weight", "at", "known_at"])


def daily_atr(daily: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR diário indexado pelo momento em que fica conhecido (fechamento do dia)."""
    prev = daily["close"].shift(1)
    tr = pd.concat([daily["high"] - daily["low"], (daily["high"] - prev).abs(), (daily["low"] - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    atr.index = atr.index + pd.Timedelta(days=1)
    return atr


@dataclass
class LevelSet:
    """Níveis em arrays ordenados por preço (filtrar milhares de vezes em pandas seria lento)."""
    price: np.ndarray
    weight: np.ndarray
    name: np.ndarray
    at: np.ndarray  # ns desde a época
    known_at: np.ndarray
    expires_after: np.ndarray  # janela de validade em ns (0 = nunca expira)

    @classmethod
    def from_frame(cls, levels: pd.DataFrame) -> "LevelSet":
        frame = levels.sort_values("price")
        windows = {code: (w.value if w is not None else 0) for code, _, _, _, w in TIMEFRAMES}
        return cls(frame["price"].to_numpy(float), frame["weight"].to_numpy(int), frame["name"].to_numpy(object),
                   pd.DatetimeIndex(frame["at"]).as_unit("ns").asi8, pd.DatetimeIndex(frame["known_at"]).as_unit("ns").asi8,
                   frame["code"].map(windows).to_numpy("int64"))


def zones_at(levels: pd.DataFrame | LevelSet, when: pd.Timestamp, atr: float) -> Zones:
    """Zonas principais conhecidas no instante `when`."""
    lv = levels if isinstance(levels, LevelSet) else LevelSet.from_frame(levels)
    if not len(lv.price) or not np.isfinite(atr) or atr <= 0:
        return EMPTY
    now = pd.Timestamp(when).as_unit("ns").value
    keep = (lv.known_at <= now) & ((lv.expires_after == 0) | (lv.at >= now - lv.expires_after))
    if not keep.any():
        return EMPTY
    prices, weights, names = lv.price[keep], lv.weight[keep], lv.name[keep]
    groups, start = [], 0
    for i in range(1, len(prices) + 1):
        # Nova zona quando o nível seguinte está longe OU a zona já ficaria larga demais (evita encadear níveis).
        if (i == len(prices) or prices[i] - prices[i - 1] > atr * CLUSTER_ATR
                or prices[i] - prices[start] > atr * MAX_WIDTH_ATR):
            groups.append((start, i))
            start = i
    low, high, weight, label = [], [], [], []
    for a, b in groups:
        w = int(weights[a:b].sum())
        if w < MIN_WEIGHT:
            continue
        low.append(prices[a:b].min() - atr * PAD_ATR)
        high.append(prices[a:b].max() + atr * PAD_ATR)
        weight.append(w)
        label.append("+".join(n for n in ("Mensal", "Semanal", "Diário") if n in set(names[a:b])))
    return Zones(np.array(low), np.array(high), np.array(weight, dtype=float), label)


def prepare(daily: pd.DataFrame | None) -> tuple[LevelSet, pd.Series] | None:
    """Níveis e ATR diário (a parte cara, reaproveitável enquanto o diário não muda)."""
    if daily is None or len(daily) < 60:
        return None
    frame_levels = htf_levels(daily)
    if frame_levels.empty:
        return None
    return LevelSet.from_frame(frame_levels), daily_atr(daily).dropna()


def zone_features(df: pd.DataFrame, tf_seconds: int, daily: pd.DataFrame | None,
                  prepared: tuple[LevelSet, pd.Series] | None = None) -> pd.DataFrame | None:
    """Leitura das zonas em cada candle do gráfico de entrada (usa df["atr"] do próprio gráfico)."""
    if "atr" not in df:
        return None
    prepared = prepared or prepare(daily)
    if prepared is None:
        return None
    levels, atr_d = prepared
    closes_at = df.index + pd.Timedelta(seconds=tf_seconds)
    # Para cada candle, a última atualização diária conhecida até o fechamento dele.
    snap_pos = np.searchsorted(atr_d.index.to_numpy(), closes_at.to_numpy(), side="right") - 1

    n = len(df)
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    pc = np.concatenate([[np.nan], c[:-1]])
    atr = df["atr"].to_numpy(float)
    out = {name: np.full(n, np.nan) for name in (
        "sup_low", "sup_high", "sup_weight", "res_low", "res_high", "res_weight", "bup_low", "bup_high",
        "bup_weight", "bdn_low", "bdn_high", "bdn_weight", "next_res", "next_sup", "near_sup", "near_res")}
    flags = {name: np.zeros(n, dtype=bool) for name in ("sup_touch", "sup_reject", "res_touch", "res_reject",
                                                         "break_up", "break_down")}
    labels = {name: np.empty(n, dtype=object) for name in ("sup_label", "res_label", "bup_label", "bdn_label")}
    cache: dict[int, Zones] = {}

    for pos in np.unique(snap_pos):
        rows = np.flatnonzero(snap_pos == pos)
        if pos < 0:
            continue
        if pos not in cache:
            cache[pos] = zones_at(levels, atr_d.index[pos], float(atr_d.iat[pos]))
        z = cache[pos]
        if not len(z):
            continue
        zl, zh, zw = z.low[None, :], z.high[None, :], z.weight[None, :]
        ri = rows[:, None]
        tol = np.nan_to_num(atr[rows], nan=0.0)[:, None]

        sup_touch = (l[ri] <= zh) & (l[ri] >= zl - TOUCH_ATR * tol) & (pc[ri] >= zl)
        sup_rej = sup_touch & (c[ri] > zh)
        res_touch = (h[ri] >= zl) & (h[ri] <= zh + TOUCH_ATR * tol) & (pc[ri] <= zh)
        res_rej = res_touch & (c[ri] < zl)
        brk_up = (pc[ri] <= zh) & (o[ri] <= zh) & (c[ri] > zh + BREAK_ATR * tol)
        brk_dn = (pc[ri] >= zl) & (o[ri] >= zl) & (c[ri] < zl - BREAK_ATR * tol)

        def pick(mask: np.ndarray, prefix: str) -> np.ndarray:
            """Guarda a zona mais forte que satisfaz `mask` em cada candle; devolve quais candles tiveram alguma."""
            weight = np.where(mask, zw, -1)
            best, has = weight.argmax(axis=1), weight.max(axis=1) > 0
            sel = rows[has]
            out[f"{prefix}_low"][sel] = z.low[best[has]]
            out[f"{prefix}_high"][sel] = z.high[best[has]]
            out[f"{prefix}_weight"][sel] = z.weight[best[has]]
            labels[f"{prefix}_label"][sel] = [z.label[b] for b in best[has]]
            return has

        # A zona mostrada é a que segurou o preço; sem rejeição, a mais forte que foi tocada.
        for side, touch, rej in (("sup", sup_touch, sup_rej), ("res", res_touch, res_rej)):
            rejected = pick(rej, side)
            pick(touch & ~rejected[:, None], side)
            flags[f"{side}_reject"][rows] = rejected
            flags[f"{side}_touch"][rows] = touch.any(axis=1)
        flags["break_up"][rows] = pick(brk_up, "bup")
        flags["break_down"][rows] = pick(brk_dn, "bdn")

        above = np.where(zl > c[ri], zl, np.inf).min(axis=1)
        below = np.where(zh < c[ri], zh, -np.inf).max(axis=1)
        out["next_res"][rows] = np.where(np.isfinite(above), above, np.nan)
        out["next_sup"][rows] = np.where(np.isfinite(below), below, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            out["near_sup"][rows] = (c[rows] - out["next_sup"][rows]) / atr[rows]
            out["near_res"][rows] = (out["next_res"][rows] - c[rows]) / atr[rows]

    frame = pd.DataFrame({**out, **flags, **labels}, index=df.index)
    frame.attrs["zones_now"] = cache.get(int(snap_pos[-1])) if len(snap_pos) and snap_pos[-1] >= 0 else None
    return frame


def stop_prices(feat: pd.DataFrame, atr: pd.Series) -> dict[str, pd.Series]:
    """Stop do outro lado da zona para cada tipo de entrada (nunca mais curto que 0,5 ATR do candle)."""
    a = atr.reindex(feat.index)
    return {
        "compra_suporte": feat["sup_low"] - STOP_ATR * a,
        "venda_resistencia": feat["res_high"] + STOP_ATR * a,
        "compra_rompimento": feat["bup_low"] - STOP_ATR * a,
        "venda_rompimento": feat["bdn_high"] + STOP_ATR * a,
    }


def describe(feat: pd.DataFrame, i: int, setup_id: str, decimals: int) -> str | None:
    prefix = {"compra_suporte": "sup", "venda_resistencia": "res", "compra_rompimento": "bup", "venda_rompimento": "bdn"}.get(setup_id)
    if prefix is None or not np.isfinite(feat[f"{prefix}_low"].iat[i]):
        return None
    kind = {"sup": "Suporte", "res": "Resistência", "bup": "Resistência rompida", "bdn": "Suporte rompido"}[prefix]
    low, high = feat[f"{prefix}_low"].iat[i], feat[f"{prefix}_high"].iat[i]
    fmt = lambda v: f"{v:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")  # noqa: E731
    return f"{kind} {feat[f'{prefix}_label'].iat[i]} ({fmt(low)} – {fmt(high)})"


def zones_view(zones: Zones | None, price: float, decimals: int, per_side: int = 3) -> dict:
    """Zonas principais mais próximas do preço, para o painel e o gráfico."""
    if zones is None or not len(zones):
        return {"supports": [], "resistances": []}
    items = [{"low": round(float(lo), decimals), "high": round(float(hi), decimals), "mid": round(float((lo + hi) / 2), decimals),
              "weight": int(w), "label": lab, "distance_pct": round(((lo + hi) / 2 / price - 1) * 100, 2),
              "strength": "forte" if w >= 5 else "média" if w >= 3 else "fraca"}
             for lo, hi, w, lab in zip(zones.low, zones.high, zones.weight, zones.label)]
    supports = sorted((z for z in items if z["mid"] < price), key=lambda z: -z["mid"])[:per_side]
    resistances = sorted((z for z in items if z["mid"] >= price), key=lambda z: z["mid"])[:per_side]
    return {"supports": supports, "resistances": resistances}
