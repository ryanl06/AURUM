"""Textos em português simples: indicadores, leituras de mercado e previsão."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pandas as pd

from .config import LOCAL_TZ


def num(value, digits: int = 4):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, digits)


def fmt_time(ts: datetime, daily: bool) -> str:
    local = ts.astimezone(LOCAL_TZ)
    return local.strftime("%d/%m") if daily else local.strftime("%H:%M")


def fmt_datetime(ts: pd.Timestamp) -> str:
    return ts.tz_convert(LOCAL_TZ).strftime("%d/%m %H:%M")


def fmt_price(value: float, decimals: int) -> str:
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------- indicadores

def explain_rsi(v: float | None) -> dict:
    if v is None:
        return {"text": "Calculando…", "tone": "neutral", "zone": "—"}
    if v < 30:
        return {"text": "Muito barato (sobrevendido) — pode subir", "tone": "bull", "zone": "Sobrevendido"}
    if v > 70:
        return {"text": "Muito caro (sobrecomprado) — pode cair", "tone": "bear", "zone": "Sobrecomprado"}
    if v < 45:
        return {"text": "Vendedores com um pouco mais de força", "tone": "neutral", "zone": "Fraco"}
    if v > 55:
        return {"text": "Compradores com força saudável", "tone": "neutral", "zone": "Forte"}
    return {"text": "Neutro — nem caro, nem barato", "tone": "neutral", "zone": "Neutro"}


def explain_macd(macd: float, signal: float, hist: float, prev_hist: float) -> dict:
    rising = hist > prev_hist
    if macd > signal:
        text = "Subindo e acelerando — tendência de alta" if rising else "Acima do sinal, mas perdendo força"
        return {"text": text, "tone": "bull" if rising else "neutral", "arrow": "up"}
    text = "Descendo e acelerando — tendência de baixa" if not rising else "Abaixo do sinal, mas a queda perde força"
    return {"text": text, "tone": "bear" if not rising else "neutral", "arrow": "down"}


def explain_adx(v: float | None) -> dict:
    if v is None:
        return {"text": "Calculando…", "tone": "neutral"}
    if v < 20:
        return {"text": "Sem tendência (lateral) — sinais de tendência valem menos", "tone": "neutral"}
    if v < 25:
        return {"text": "Tendência começando a ganhar força", "tone": "neutral"}
    if v < 40:
        return {"text": "Tendência forte", "tone": "good"}
    return {"text": "Tendência muito forte (cuidado com exaustão)", "tone": "good"}


def explain_bollinger(pctb: float | None, squeeze: bool) -> dict:
    if pctb is None:
        return {"text": "Calculando…", "tone": "neutral"}
    if pctb < 0:
        text, tone = "Abaixo da banda inferior — esticado para baixo", "bull"
    elif pctb < 0.2:
        text, tone = "Perto do fundo da faixa de preço", "bull"
    elif pctb > 1:
        text, tone = "Acima da banda superior — esticado para cima", "bear"
    elif pctb > 0.8:
        text, tone = "Perto do topo da faixa de preço", "bear"
    else:
        text, tone = "No meio da faixa de preço", "neutral"
    if squeeze:
        text += " · bandas apertadas: movimento forte pode estar perto"
    return {"text": text, "tone": tone}


def explain_atr(atr_pct: float | None, ratio: float | None) -> dict:
    if atr_pct is None:
        return {"text": "Calculando…", "tone": "neutral", "level": "—"}
    if ratio and ratio > 1.5:
        return {"text": "Volatilidade ALTA — risco maior, use posição menor", "tone": "bear", "level": "Alta"}
    if ratio and ratio < 0.7:
        return {"text": "Volatilidade baixa — movimentos pequenos", "tone": "neutral", "level": "Baixa"}
    return {"text": "Volatilidade normal para este ativo", "tone": "neutral", "level": "Normal"}


def atr_ratio(df: pd.DataFrame, i: int) -> float | None:
    hist = df["atr_pct"].iloc[max(0, i - 100): i + 1].dropna()
    if len(hist) <= 10 or not hist.median():
        return None
    return float(df["atr_pct"].iat[i] / hist.median())


def is_squeeze(df: pd.DataFrame, i: int) -> bool:
    widths = df["bb_width"].iloc[max(0, i - 120): i + 1].dropna()
    return bool(len(widths) > 30 and df["bb_width"].iat[i] <= widths.quantile(0.15))


def indicator_views(df: pd.DataFrame, i: int, decimals: int, volume_ok: bool) -> dict:
    row, prev = df.iloc[i], df.iloc[i - 1]
    ratio = atr_ratio(df, i)
    squeeze = is_squeeze(df, i)
    rsi_v = num(row["rsi"], 1)
    views = {
        "rsi": {"value": rsi_v, **explain_rsi(rsi_v)},
        "macd": {"value": num(row["macd"], decimals + 2), "signal": num(row["macd_signal"], decimals + 2),
                 "hist": num(row["macd_hist"], decimals + 2),
                 **explain_macd(row["macd"], row["macd_signal"], row["macd_hist"], prev["macd_hist"])},
        "ema": {"ema9": num(row["ema9"], decimals), "ema21": num(row["ema21"], decimals),
                "ema50": num(row["ema50"], decimals),
                "text": "Média curta acima da longa — preço subindo" if row["ema9"] > row["ema21"]
                else "Média curta abaixo da longa — preço descendo",
                "tone": "bull" if row["ema9"] > row["ema21"] else "bear"},
        "bollinger": {"upper": num(row["bb_upper"], decimals), "mid": num(row["bb_mid"], decimals),
                      "lower": num(row["bb_lower"], decimals), "pctb": num(row["bb_pctb"], 3),
                      "squeeze": squeeze, **explain_bollinger(num(row["bb_pctb"], 3), squeeze)},
        "atr": {"value": num(row["atr"], decimals), "pct": num(row["atr_pct"], 3),
                "ratio": num(ratio, 2), **explain_atr(num(row["atr_pct"], 3), ratio)},
        "adx": {"value": num(row["adx"], 1), "plus_di": num(row["plus_di"], 1),
                "minus_di": num(row["minus_di"], 1), **explain_adx(num(row["adx"], 1))},
        "volume": {"available": volume_ok, "value": num(row["volume"], 0), "ratio": num(row["vol_ratio"], 2),
                   "text": ("Sem dado de volume para este ativo (normal em forex)" if not volume_ok else
                            "Volume forte — muita gente negociando" if row["vol_ratio"] >= 1.5 else
                            "Volume fraco — pouca participação" if row["vol_ratio"] < 0.6 else "Volume normal"),
                   "tone": "neutral"},
    }
    if "vwap" in df and pd.notna(row.get("vwap")):
        above = row["close"] > row["vwap"]
        views["vwap"] = {"value": num(row["vwap"], decimals), "tone": "bull" if above else "bear",
                         "text": "Preço acima da VWAP — compradores no controle do dia" if above
                         else "Preço abaixo da VWAP — vendedores no controle do dia"}
    return views


# ---------------------------------------------------------------- previsão

ETA_LABELS = {
    "ema_up": "Médias devem cruzar para CIMA", "ema_down": "Médias devem cruzar para BAIXO",
    "macd_up": "MACD deve cruzar para CIMA", "macd_down": "MACD deve cruzar para BAIXO",
    "rsi_up_70": "RSI deve entrar em sobrecompra (>70)", "rsi_down_30": "RSI deve entrar em sobrevenda (<30)",
}


def forecast(df: pd.DataFrame, i: int, score: float, etas: dict, last_start: datetime, seconds: int, daily: bool,
             *, extra_reads: list[dict], probability: dict | None, horizon: int) -> dict:
    row, prev = df.iloc[i], df.iloc[i - 1]
    reads: list[dict] = []

    def add(tone, text):
        reads.append({"tone": tone, "text": text})

    rsi_v = row["rsi"]
    hist_up = row["macd_hist"] > prev["macd_hist"]
    if rsi_v > 70:
        add("bear", "RSI sobrecomprado — pode cair")
    elif rsi_v < 30:
        add("bull", "RSI sobrevendido — pode subir")
    above = (df["macd"] > df["macd_signal"]).iloc[i - 2: i + 1]
    if above.iloc[-1] and not above.iloc[0]:
        add("bull", "MACD cruzou para cima — tendência de alta confirmada")
    elif not above.iloc[-1] and above.iloc[0]:
        add("bear", "MACD cruzou para baixo — tendência de baixa confirmada")
    elif row["macd"] < 0 and row["macd"] < row["macd_signal"]:
        add("bear", "MACD negativo — pressão de baixa")
    elif row["macd"] > 0 and row["macd"] > row["macd_signal"]:
        add("bull", "MACD positivo — pressão de alta")
    if rsi_v < 30 and hist_up:
        add("bull", "Sobrevendido e MACD reagindo — provavelmente sobe em breve")
    if rsi_v > 70 and not hist_up:
        add("bear", "Sobrecomprado e MACD perdendo força — provavelmente desce em breve")
    if row["ema9"] > row["ema21"] and row["macd"] > row["macd_signal"]:
        add("bull", "Médias e MACD alinhados para cima")
    elif row["ema9"] < row["ema21"] and row["macd"] < row["macd_signal"]:
        add("bear", "Médias e MACD alinhados para baixo")
    if is_squeeze(df, i):
        add("neutral", "Bandas de Bollinger apertadas — rompimento forte pode estar próximo")
    if pd.notna(row["adx"]) and row["adx"] < 18:
        add("neutral", "ADX baixo — mercado lateral, sinais menos confiáveis")
    reads.extend(extra_reads)

    eta_list = []
    for key, label in ETA_LABELS.items():
        candles = etas.get(key)
        if candles:
            at = last_start + timedelta(seconds=seconds * (candles + 1))
            eta_list.append({"key": key, "label": label, "candles": candles, "at": fmt_time(at, daily),
                             "tone": "bull" if key.endswith("up") else "bear" if key.endswith("down") else "neutral"})
    eta_list.sort(key=lambda e: e["candles"])

    if score >= 25:
        direction, headline = "ALTA", "TENDÊNCIA ALTA — provavelmente sobe"
    elif score <= -25:
        direction, headline = "BAIXA", "TENDÊNCIA BAIXA — provavelmente desce"
    else:
        direction, headline = "LATERAL", "SEM DIREÇÃO CLARA — mercado indeciso"

    prob_view = None
    if probability and probability["count"] >= 30:
        up = probability["up_rate"]
        prob_view = {
            "up_rate": up, "down_rate": round(100 - up, 1), "count": probability["count"], "horizon": horizon,
            "avg_move_pct": probability["avg_move_pct"],
            "text": (f"Nas {probability['count']} vezes em que a força esteve nesta faixa, o preço estava mais "
                     f"{'alto' if up >= 50 else 'baixo'} {horizon} candles depois em {max(up, 100 - up):.0f}% dos casos."),
            "edge": "forte" if abs(up - 50) >= 8 else "fraca" if abs(up - 50) >= 3 else "nenhuma",
        }
        # A previsão só afirma direção quando o histórico confirma a leitura dos indicadores.
        if direction == "ALTA" and up < 50:
            headline = "TENDÊNCIA ALTA — mas o histórico não confirma a continuação"
        elif direction == "BAIXA" and up > 50:
            headline = "TENDÊNCIA BAIXA — mas o histórico não confirma a continuação"
    return {"direction": direction, "headline": headline, "reads": reads, "etas": eta_list, "probability": prob_view}
