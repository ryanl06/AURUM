"""Reconhecimento de padrões com medição honesta.

1. Análogos históricos (vizinhos mais próximos): procura no próprio histórico do ativo os momentos
   mais parecidos com o atual (formato dos últimos candles + estado dos indicadores) e resume o que
   aconteceu depois — probabilidade de alta e cone de projeção (percentis 10/50/90).
   A qualidade é medida em janela deslizante fora da amostra: cada previsão só usa o passado.
2. Padrões de candle vetorizados, com a estatística real de cada um neste ativo.
3. Estrutura de mercado por pivôs (topos e fundos) e topo/fundo duplo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .levels import Pivots

PATH_BARS = 12  # candles que descrevem o "formato" recente
PROJECTION = 10  # candles projetados no cone
MAIN_HORIZON = 3
K_NEIGHBORS = 40
MAX_CANDIDATES = 8000
WARMUP = 60


# ---------------------------------------------------------------- análogos (k vizinhos mais próximos)

def feature_matrix(df: pd.DataFrame) -> np.ndarray:
    """Uma linha por candle, usando só informação disponível até aquele candle."""
    close = df["close"].to_numpy(float)
    rets = np.diff(np.log(close), prepend=np.nan)
    vol = pd.Series(rets).rolling(50, min_periods=20).std().to_numpy()
    norm = np.clip(rets / np.where(vol > 0, vol, np.nan), -4, 4)
    path = np.column_stack([np.roll(norm, j) for j in range(PATH_BARS)])
    path[:PATH_BARS] = np.nan
    atr = df["atr"].to_numpy(float)
    safe_atr = np.where(atr > 0, atr, np.nan)
    state = np.column_stack([
        (df["rsi"].to_numpy(float) / 100 - 0.5) * 4,
        (df["bb_pctb"].to_numpy(float) - 0.5) * 2,
        np.clip(df["macd_hist"].to_numpy(float) / safe_atr, -3, 3),
        np.clip((close - df["ema21"].to_numpy(float)) / safe_atr, -4, 4) / 2,
        np.clip((close - df["ema50"].to_numpy(float)) / safe_atr, -6, 6) / 3,
        df["adx"].to_numpy(float) / 25 - 1,
    ])
    # Estado pesa um pouco mais que cada retorno isolado (são 12 retornos contra 6 estados).
    return np.hstack([path * 0.8, state * 1.6])


def forward_moves(close: np.ndarray, bars: int = PROJECTION) -> np.ndarray:
    """Variação percentual de cada candle até 1..bars candles depois (NaN quando o futuro não existe)."""
    n = len(close)
    out = np.full((n, bars), np.nan)
    for h in range(1, bars + 1):
        out[: n - h, h - 1] = close[h:] / close[: n - h] - 1
    return out * 100


def _neighbors(features: np.ndarray, query: int, last_candidate: int, k: int) -> tuple[np.ndarray, np.ndarray]:
    lo = max(WARMUP, last_candidate - MAX_CANDIDATES)
    if last_candidate - lo < k * 3:
        return np.array([], int), np.array([])
    cand = features[lo:last_candidate]
    q = features[query]
    if np.isnan(q).any():
        return np.array([], int), np.array([])
    dist = np.sqrt(np.nansum((cand - q) ** 2, axis=1))
    valid = ~np.isnan(cand).any(axis=1)
    dist = np.where(valid, dist, np.inf)
    take = min(k, int(valid.sum()))
    if take < 10:
        return np.array([], int), np.array([])
    idx = np.argpartition(dist, take - 1)[:take]
    return idx + lo, dist[idx]


def _summarize(moves: np.ndarray, dist: np.ndarray) -> dict:
    weights = 1 / (dist + 0.5)
    weights /= weights.sum()
    main = moves[:, MAIN_HORIZON - 1]
    up_rate = float((weights * (main > 0)).sum() * 100)
    return {
        "up_rate": round(up_rate, 1),
        "median": [round(float(v), 3) for v in np.nanmedian(moves, axis=0)],
        "p10": [round(float(v), 3) for v in np.nanpercentile(moves, 10, axis=0)],
        "p90": [round(float(v), 3) for v in np.nanpercentile(moves, 90, axis=0)],
        "avg_main": round(float((weights * main).sum()), 3),
    }


def analog_forecast(df: pd.DataFrame, i: int, validate: bool = True) -> dict | None:
    """Previsão por análogos para o candle i (use um candle fechado)."""
    features = feature_matrix(df)
    moves = forward_moves(df["close"].to_numpy(float))
    idx, dist = _neighbors(features, i, i - PROJECTION, K_NEIGHBORS)
    if len(idx) == 0:
        return None
    summary = _summarize(moves[idx], dist)
    typical = _typical_distance(features, i)
    similarity = "alta" if typical and dist.mean() < typical * 0.6 else "média" if typical and dist.mean() < typical * 0.85 else "baixa"
    result = {
        **summary, "neighbors": int(len(idx)), "horizon": MAIN_HORIZON, "projection_bars": PROJECTION,
        "similarity": similarity, "examples": [int(x) for x in sorted(idx)[-5:]],
    }
    result["validation"] = walk_forward(df, features, moves, i) if validate else None
    scale = (result["validation"] or {}).get("range_scale", 1.0)
    if scale != 1.0:
        med = np.array(result["median"])
        result["p10"] = [round(float(v), 3) for v in med - (med - np.array(result["p10"])) * scale]
        result["p90"] = [round(float(v), 3) for v in med + (np.array(result["p90"]) - med) * scale]
    result["range_scale"] = scale
    return result


def _typical_distance(features: np.ndarray, i: int, sample: int = 300) -> float | None:
    lo = max(WARMUP, i - MAX_CANDIDATES)
    pool = features[lo:i - PROJECTION]
    pool = pool[~np.isnan(pool).any(axis=1)]
    if len(pool) < 50 or np.isnan(features[i]).any():
        return None
    rng = np.random.default_rng(7)
    pick = pool[rng.choice(len(pool), size=min(sample, len(pool)), replace=False)]
    return float(np.sqrt(((pick - features[i]) ** 2).sum(axis=1)).mean())


def walk_forward(df: pd.DataFrame, features: np.ndarray, moves: np.ndarray, end: int, queries: int = 250) -> dict:
    """Mede os análogos nos últimos 30% do histórico, cada previsão usando só o que veio antes dela."""
    n = end + 1
    start = max(int(n * 0.7), WARMUP + K_NEIGHBORS * 4 + PROJECTION)
    last_query = end - MAIN_HORIZON
    if last_query - start < 30:
        return {"tested": 0}
    step = max(1, (last_query - start) // queries)
    hits = confident_hits = confident = tested = covered = 0
    brier = brier_base = 0.0
    width_ratio = []
    base_up = float(np.nanmean(moves[:start, MAIN_HORIZON - 1] > 0))
    for q in range(start, last_query, step):
        idx, dist = _neighbors(features, q, q - PROJECTION, K_NEIGHBORS)
        actual = moves[q, MAIN_HORIZON - 1]
        if len(idx) == 0 or np.isnan(actual):
            continue
        # Versão enxuta do resumo (só o que a validação usa): muito mais rápida que percentis coluna a coluna.
        weights = 1 / (dist + 0.5)
        p = float((weights * (moves[idx, MAIN_HORIZON - 1] > 0)).sum() / weights.sum())
        up = actual > 0
        tested += 1
        far = moves[q, PROJECTION - 1]
        ends = moves[idx, PROJECTION - 1]
        ends = ends[~np.isnan(ends)]
        if not np.isnan(far) and len(ends) >= 10:
            lo, mid, hi = np.percentile(ends, [10, 50, 90])
            covered += lo <= far <= hi
            half = (hi - mid) if far >= mid else (mid - lo)
            if half > 0:
                width_ratio.append(abs(far - mid) / half)
        hits += (p > 0.5) == up
        brier += (p - up) ** 2
        brier_base += (base_up - up) ** 2
        if abs(p - 0.5) >= 0.1:
            confident += 1
            confident_hits += (p > 0.5) == up
    if tested == 0:
        return {"tested": 0}
    accuracy = hits / tested * 100
    conf_acc = confident_hits / confident * 100 if confident else None
    skill = (1 - brier / brier_base) * 100 if brier_base else 0.0
    # Critério rígido: muitos ativos e tempos gráficos são testados, então acertos modestos podem ser sorte.
    edge = "possível" if (conf_acc is not None and confident >= 80 and conf_acc >= 57 and skill > 1 and accuracy >= 53) else \
        "fraca" if accuracy >= 53 and skill > -1 else "nenhuma"
    return {
        "tested": tested, "accuracy": round(accuracy, 1), "confident": confident,
        "confident_accuracy": None if conf_acc is None else round(conf_acc, 1),
        "skill": round(skill, 1), "base_up_rate": round(base_up * 100, 1), "edge": edge,
        "range_coverage": round(covered / tested * 100, 1), "range_target": 80,
        # Fator que alarga (ou estreita) o cone para cobrir 80% dos casos neste ativo e tempo gráfico.
        "range_scale": round(float(np.clip(np.quantile(width_ratio, 0.8), 0.6, 2.5)), 3) if len(width_ratio) >= 30 else 1.0,
    }


# ---------------------------------------------------------------- padrões de candle (vetorizados)

BULLISH = {"engolfo_alta", "martelo", "estrela_manha", "tres_soldados"}
PATTERN_INFO = {
    "engolfo_alta": ("Engolfo de alta", "bull", "compradores engoliram o candle anterior"),
    "engolfo_baixa": ("Engolfo de baixa", "bear", "vendedores engoliram o candle anterior"),
    "martelo": ("Martelo", "bull", "preço rejeitado no fundo"),
    "estrela_cadente": ("Estrela cadente", "bear", "preço rejeitado no topo"),
    "estrela_manha": ("Estrela da manhã", "bull", "virada de três candles no fundo"),
    "estrela_tarde": ("Estrela da tarde", "bear", "virada de três candles no topo"),
    "tres_soldados": ("Três soldados brancos", "bull", "três altas fortes seguidas"),
    "tres_corvos": ("Três corvos negros", "bear", "três quedas fortes seguidas"),
}


def candle_flags(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = (df[k] for k in ("open", "high", "low", "close"))
    po, pc = o.shift(1), c.shift(1)
    body, rng = (c - o).abs(), (h - l).replace(0, np.nan)
    upper, lower = h - np.maximum(o, c), np.minimum(o, c) - l
    up, down = c > o, c < o
    trend_down = c < df["ema21"]
    trend_up = c > df["ema21"]
    avg_body = body.rolling(20, min_periods=5).mean()
    big = body > avg_body
    small = body < avg_body * 0.5
    flags = pd.DataFrame(index=df.index)
    flags["engolfo_alta"] = (pc < po) & up & (o <= pc) & (c >= po) & (body > (pc - po).abs()) & trend_down.shift(1, fill_value=False)
    flags["engolfo_baixa"] = (pc > po) & down & (o >= pc) & (c <= po) & (body > (pc - po).abs()) & trend_up.shift(1, fill_value=False)
    flags["martelo"] = (lower >= 2 * body) & (upper <= np.maximum(body, rng * 0.1)) & trend_down
    flags["estrela_cadente"] = (upper >= 2 * body) & (lower <= np.maximum(body, rng * 0.1)) & trend_up
    flags["estrela_manha"] = (down.shift(2) & big.shift(2) & small.shift(1) & up & big
                              & (c > (o.shift(2) + c.shift(2)) / 2) & trend_down.shift(2, fill_value=False))
    flags["estrela_tarde"] = (up.shift(2) & big.shift(2) & small.shift(1) & down & big
                              & (c < (o.shift(2) + c.shift(2)) / 2) & trend_up.shift(2, fill_value=False))
    flags["tres_soldados"] = up & up.shift(1) & up.shift(2) & big & big.shift(1) & big.shift(2) & (c > c.shift(1)) & (c.shift(1) > c.shift(2))
    flags["tres_corvos"] = down & down.shift(1) & down.shift(2) & big & big.shift(1) & big.shift(2) & (c < c.shift(1)) & (c.shift(1) < c.shift(2))
    return flags.fillna(False).astype(bool)


def pattern_report(df: pd.DataFrame, flags: pd.DataFrame, i: int, horizon: int = MAIN_HORIZON) -> list[dict]:
    """Padrões no candle i com o histórico real de cada um neste ativo (acerto = foi na direção esperada)."""
    close = df["close"].to_numpy(float)
    fwd = np.full(len(close), np.nan)
    fwd[: len(close) - horizon] = close[horizon:] / close[: len(close) - horizon] - 1
    out = []
    for key in flags.columns:
        if not bool(flags[key].iat[i]):
            continue
        name, tone, text = PATTERN_INFO[key]
        past = np.flatnonzero(flags[key].to_numpy()[: max(0, i - horizon)])
        moves = fwd[past]
        moves = moves[~np.isnan(moves)]
        n = int(len(moves))
        expected_up = key in BULLISH
        hit = float(((moves > 0) if expected_up else (moves < 0)).mean() * 100) if n else None
        out.append({
            "key": key, "name": name, "tone": tone, "text": f"{name} — {text}", "occurrences": n,
            "hit_rate": None if hit is None else round(hit, 1), "horizon": horizon,
            "avg_move_pct": round(float(moves.mean() * 100), 3) if n else None,
            "reliable": bool(n >= 20 and hit is not None and hit >= 55),
        })
    return out


# ---------------------------------------------------------------- estrutura de mercado

def market_structure(df: pd.DataFrame, pivots: Pivots, i: int) -> dict:
    """Topos e fundos confirmados até i: tendência por estrutura, topo/fundo duplo e rompimentos."""
    highs, lows = df["high"].to_numpy(float), df["low"].to_numpy(float)
    close = float(df["close"].iat[i])
    atr = float(df["atr"].iat[i]) if pd.notna(df["atr"].iat[i]) else close * 0.004
    ph = [p for p in pivots.high_idx if p + pivots.k <= i][-3:]
    pl = [p for p in pivots.low_idx if p + pivots.k <= i][-3:]
    result = {"trend": "indefinida", "text": "Poucos topos e fundos confirmados", "tone": "neutral", "events": []}
    if len(ph) >= 2 and len(pl) >= 2:
        hh, hl = highs[ph[-1]] > highs[ph[-2]], lows[pl[-1]] > lows[pl[-2]]
        lh, ll = highs[ph[-1]] < highs[ph[-2]], lows[pl[-1]] < lows[pl[-2]]
        if hh and hl:
            result.update(trend="alta", tone="bull", text="Estrutura de alta: topos e fundos cada vez mais altos")
        elif lh and ll:
            result.update(trend="baixa", tone="bear", text="Estrutura de baixa: topos e fundos cada vez mais baixos")
        else:
            result.update(trend="lateral", text="Estrutura lateral: topos e fundos sem direção definida")
        if abs(highs[ph[-1]] - highs[ph[-2]]) <= 0.35 * atr and close < highs[ph[-1]] - atr:
            result["events"].append({"tone": "bear", "text": "Possível topo duplo — dois topos no mesmo nível e preço recuando"})
        if abs(lows[pl[-1]] - lows[pl[-2]]) <= 0.35 * atr and close > lows[pl[-1]] + atr:
            result["events"].append({"tone": "bull", "text": "Possível fundo duplo — dois fundos no mesmo nível e preço reagindo"})
    if ph:
        level = highs[ph[-1]]
        recent = df["close"].iloc[max(0, i - 2): i + 1].to_numpy(float)
        if close > level and (recent[:-1] <= level).any():
            result["events"].append({"tone": "bull", "text": "Rompimento do último topo — compradores superaram a resistência"})
    if pl:
        level = lows[pl[-1]]
        recent = df["close"].iloc[max(0, i - 2): i + 1].to_numpy(float)
        if close < level and (recent[:-1] >= level).any():
            result["events"].append({"tone": "bear", "text": "Perda do último fundo — vendedores romperam o suporte"})
    return result


# ---------------------------------------------------------------- consenso

def consensus(score: float | None, calibration_up: float | None, analog: dict | None, htf_bias: int | None,
              structure: dict) -> dict:
    """Quantas leituras independentes apontam para o mesmo lado."""
    votes = []

    def vote(name: str, direction: int | None, detail: str):
        votes.append({"name": name, "direction": direction, "detail": detail})

    vote("Indicadores", None if score is None or abs(score) < 15 else (1 if score > 0 else -1),
         "força " + ("—" if score is None else f"{score:+.0f}"))
    vote("Histórico da força", None if calibration_up is None or abs(calibration_up - 50) < 3 else (1 if calibration_up > 50 else -1),
         "—" if calibration_up is None else f"subiu {calibration_up:.0f}%")
    analog_ok = analog and (analog.get("validation") or {}).get("edge") in ("real", "fraca")
    vote("Padrões semelhantes", None if not analog or abs(analog["up_rate"] - 50) < 5 else (1 if analog["up_rate"] > 50 else -1),
         "—" if not analog else f"subiu {analog['up_rate']:.0f}%" + ("" if analog_ok else " (sem vantagem medida)"))
    vote("Tempo maior", None if not htf_bias else htf_bias, {1: "em alta", -1: "em baixa"}.get(htf_bias or 0, "neutro"))
    vote("Estrutura", {"alta": 1, "baixa": -1}.get(structure.get("trend")), structure.get("trend", "—"))
    ups = sum(1 for v in votes if v["direction"] == 1)
    downs = sum(1 for v in votes if v["direction"] == -1)
    if ups >= 4 and downs == 0:
        label, tone = "Consenso de ALTA", "bull"
    elif downs >= 4 and ups == 0:
        label, tone = "Consenso de BAIXA", "bear"
    elif ups > downs:
        label, tone = "Leve viés de alta", "bull"
    elif downs > ups:
        label, tone = "Leve viés de baixa", "bear"
    else:
        label, tone = "Sem consenso", "neutral"
    return {"votes": votes, "up": ups, "down": downs, "total": len(votes), "label": label, "tone": tone}
