"""Decisão do sinal (entrar, preparar, aguardar, sair, manter) e plano de risco."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from .backtest import stop_distance
from .config import LOCAL_TZ
from .explain import fmt_price, fmt_time, num
from .news import describe_block
from .strategy import BUY, SELL

SIDE_WORD = {BUY: "COMPRA ↑", SELL: "VENDA ↓"}
CONFIDENCE_PRIOR_WEIGHT = 12  # "trades virtuais" que puxam taxas de acerto com pouca amostra para a média


# ---------------------------------------------------------------- confiança e avisos

def risk_warnings(ctx: dict, side: str) -> list[str]:
    """O que um trader checaria antes de apertar o botão."""
    row, market, tf = ctx["row"], ctx["market"], ctx["tf"]
    warnings = []
    if side == SELL and row["rsi"] < 30:
        warnings.append("RSI já está sobrevendido: risco de repique contra a venda. Use stop curto.")
    if side == BUY and row["rsi"] > 70:
        warnings.append("RSI já está sobrecomprado: risco de correção contra a compra. Use stop curto.")
    if pd.notna(row["adx"]) and row["adx"] < 18:
        warnings.append("Mercado lateral (ADX baixo): sinais de tendência falham mais.")
    if ctx.get("atr_ratio") and ctx["atr_ratio"] > 1.5:
        warnings.append("Volatilidade acima do normal: reduza o tamanho da posição.")
    if market["open"] and market.get("next_change") and tf.seconds < 86400:
        minutes_left = (datetime.fromisoformat(market["next_change"]) - ctx["now"]).total_seconds() / 60
        if 0 < minutes_left <= max(30, tf.seconds * 3 / 60):
            warnings.append(f"Mercado fecha em ~{minutes_left:.0f} min: pouco tempo para a operação andar.")
    upcoming = (ctx.get("news") or {}).get("upcoming") or []
    soon = next((e for e in upcoming if e["impact"] == "High" and -30 <= e["minutes"] <= 120), None)
    if soon:
        when = f"em {soon['minutes']} min" if soon["minutes"] >= 0 else f"há {-soon['minutes']} min"
        warnings.append(f"Notícia de alto impacto: {soon['title']} ({soon['country']}) às {soon['time'][-5:]} ({when}). "
                        "O preço pode saltar e passar do stop.")
    reliability = ctx.get("reliability") or {}
    if reliability.get("level") == "BAIXA":
        warnings.append(f"Confiabilidade BAIXA neste tempo gráfico: {reliability.get('short')}")
    levels = ctx.get("levels") or {}
    level = levels.get("nearest_resistance") if side == BUY else levels.get("nearest_support")
    plan_dist = ctx.get("stop_dist")
    if level and plan_dist:
        room = abs(level["price"] - ctx["price"]) / plan_dist
        if room < ctx["reward"]:
            kind = "Resistência" if side == BUY else "Suporte"
            warnings.append(f"{kind} em {fmt_price(level['price'], ctx['decimals'])} antes do alvo 2 "
                            f"(a {room:.1f}x o risco): considere realizar no alvo 1.")
    return warnings


def confidence(ctx: dict, setup_id: str, warnings: list[str]) -> dict:
    """Chance histórica de o alvo vir antes do stop, ajustada pela amostra (não é garantia)."""
    stats = ctx["bt"].get(setup_id) or {}
    breakeven = 100 / (1 + ctx["reward"])
    overall = ctx["bt_overall"].get("win_rate")
    prior = overall if overall is not None else breakeven
    trades, wins = stats.get("trades", 0), stats.get("wins", 0)
    k = CONFIDENCE_PRIOR_WEIGHT
    value = (wins + k * prior / 100) / (trades + k) * 100
    penalty = sum(3 for w in warnings if not w.startswith(("Resistência", "Suporte")))
    value = max(1.0, min(95.0, value - penalty))
    return {"value": int(round(value)), "breakeven": round(breakeven, 1), "trades": trades,
            "edge": "positiva" if value >= breakeven + 3 else "neutra" if value >= breakeven - 3 else "negativa"}


# ---------------------------------------------------------------- entrada

def entry_signal(ctx: dict, fired_closed, fired_live, near, in_course, blocked) -> dict:
    """Sinal técnico + proteções (limites do dia e notícias). As proteções sempre vencem."""
    sig = _technical_signal(ctx, fired_closed, fired_live, near, in_course, blocked)
    return apply_guards(sig, ctx)


def apply_guards(sig: dict, ctx: dict) -> dict:
    risk = ctx.get("risk") or {}
    if risk.get("blocked"):
        ignored = f" O sinal de {sig['setup']} foi ignorado para proteger seu capital." if sig["action"] in (
            "ENTRAR_AGORA", "PREPARE_SE") and sig.get("setup") else ""
        return {**sig, "action": "PAUSA", "title": "PAUSA", "tone": "pause", "side": None, "right_moment": False,
                "confidence": None, "confidence_info": None, "window": None, "horizon": None,
                "headline": "Limite do dia atingido — pare por hoje",
                "explanation": " ".join(risk.get("reasons", [])) + ignored +
                               " Os limites ficam em ⚙ Configurações → Gestão de risco.",
                "simple": "Pare por hoje. Disciplina protege o capital.", "guard": "risk"}
    block = (ctx.get("news") or {}).get("blocking")
    if block and ctx.get("news_guard") and sig["action"] in ("ENTRAR_AGORA", "PREPARE_SE"):
        return {**sig, "action": "AGUARDE", "title": "AGUARDE", "tone": "wait", "right_moment": False,
                "window": None, "horizon": None,
                "headline": f"Notícia de alto impacto — não entre agora ({sig.get('setup') or 'sinal'} em espera)",
                "explanation": f"{describe_block(block)} Entradas ficam bloqueadas de "
                               f"{ctx['news'].get('guard_minutes', 30)} min antes a {ctx['news'].get('guard_minutes', 30)} min depois, "
                               "porque o preço costuma saltar e o stop pode não segurar.",
                "simple": "Notícia forte chegando. Espere passar.", "guard": "news"}
    return sig


def _technical_signal(ctx: dict, fired_closed, fired_live, near, in_course, blocked) -> dict:
    tf, daily, market = ctx["tf"], ctx["daily"], ctx["market"]
    close_at = ctx["live_close_at"]

    def pick(candidates):
        return max(candidates, key=lambda s: (ctx["bt"].get(s.id, {}).get("profit_factor") or 0,
                                              ctx["score"] if s.side == BUY else -ctx["score"]))

    if fired_closed:
        s = pick(fired_closed)
        warnings = risk_warnings(ctx, s.side)
        conf = confidence(ctx, s.id, warnings)
        stats = ctx["bt"].get(s.id) or {}
        history = ""
        if stats.get("trades"):
            history = (f" No histórico deste ativo essa regra bateu o alvo em {stats['win_rate']:.0f}% de "
                       f"{stats['trades']} vezes (fator de lucro {stats['profit_factor'] or '—'}).")
        if not market["open"]:
            return _signal("MERCADO_FECHADO", "MERCADO FECHADO", "wait", s, conf, False, warnings,
                           f"Último candle deu {s.name}, mas o mercado está fechado.",
                           f"{s.description} Reavalie na abertura ({market.get('next_change_label') or 'próxima sessão'}): "
                           "o preço pode abrir bem diferente (gap).",
                           "Mercado fechado. Não opere agora.")
        until = close_at if ctx["forming"] else ctx["now"] + timedelta(seconds=tf.seconds)
        horizon_end = ctx["last_start"] + timedelta(seconds=tf.seconds * 3)
        sig = _signal("ENTRAR_AGORA", "ENTRAR AGORA", "buy" if s.side == BUY else "sell", s, conf, True, warnings,
                      f"{SIDE_WORD[s.side]} · {s.name}",
                      f"{s.description} Sinal confirmado no fechamento do último candle.{history}",
                      f"Momento favorável para {'COMPRAR' if s.side == BUY else 'VENDER'}. Use o stop loss.")
        sig["window"] = {"from": fmt_time(ctx["now"], daily), "until": fmt_time(until, daily),
                         "until_iso": until.astimezone(LOCAL_TZ).isoformat(),
                         "label": f"Entre até {fmt_time(until, daily)} (fechamento do candle atual)"}
        sig["horizon"] = {"candles": 3, "until": fmt_time(horizon_end, daily),
                          "label": f"Horizonte curto: 3 candles (até ~{fmt_time(horizon_end, daily)})"}
        return sig

    if fired_live:
        s = pick(fired_live)
        warnings = risk_warnings(ctx, s.side)
        sig = _signal("PREPARE_SE", "PREPARE-SE", "prepare", s, confidence(ctx, s.id, warnings), False, warnings,
                      f"{SIDE_WORD[s.side]} se formando · {s.name}",
                      f"Todas as condições de {s.name} aparecem no candle atual, mas ele ainda não fechou. "
                      f"Se confirmar às {fmt_time(close_at, daily)}, a entrada é no candle seguinte.",
                      f"Fique pronto. Confirma às {fmt_time(close_at, daily)}.")
        sig["window"] = {"from": fmt_time(close_at, daily),
                         "until": fmt_time(close_at + timedelta(seconds=tf.seconds), daily),
                         "until_iso": close_at.astimezone(LOCAL_TZ).isoformat(),
                         "label": f"Confirmação às {fmt_time(close_at, daily)}"}
        return sig

    if near and market["open"]:
        s, eta, missing = min(near, key=lambda item: item[1])
        at = ctx["last_start"] + timedelta(seconds=tf.seconds * (eta + 1))
        sig = _signal("PREPARE_SE", "PREPARE-SE", "prepare", s, None, False, [],
                      f"{s.name} pode disparar em ~{eta} candle{'s' if eta > 1 else ''}",
                      f"Falta só 1 condição: “{missing}”. Pela velocidade atual, deve acontecer por volta de "
                      f"{fmt_time(at, daily)}. É uma estimativa — aguarde a confirmação antes de entrar.",
                      f"Quase lá. Possível sinal ~{fmt_time(at, daily)}.")
        sig["window"] = {"from": fmt_time(at, daily), "until": None, "until_iso": at.astimezone(LOCAL_TZ).isoformat(),
                         "label": f"Estimativa: ~{fmt_time(at, daily)}"}
        return sig

    if not market["open"]:
        return _signal("MERCADO_FECHADO", "MERCADO FECHADO", "wait", None, None, False, [],
                       "Mercado fechado — sem operação agora.",
                       f"A análise usa o último pregão. {market.get('next_change_label') or ''}.",
                       "Mercado fechado. Não opere agora.")

    if blocked:
        s = blocked[0]
        stats = ctx["bt"].get(s.id) or {}
        return _signal("AGUARDE", "AGUARDE", "wait", s, None, False, [],
                       f"{s.name} apareceu, mas foi bloqueada pelo histórico",
                       f"Neste ativo e tempo gráfico essa regra perdeu mais do que ganhou "
                       f"(fator de lucro {stats.get('profit_factor') or '—'} em {stats.get('trades', 0)} sinais). "
                       "O AURUM não recomenda entrar. Você pode desligar esse filtro em ⚙.",
                       "Sinal fraco no histórico — não opere.", side_visible=False)

    if in_course:
        s = in_course[0]
        direction = "alta" if s.side == BUY else "baixa"
        return _signal("AGUARDE", "AGUARDE", "wait", s, None, False, [],
                       f"Tendência de {direction} em andamento — entrada tardia",
                       f"{s.name} já disparou antes. Entrar agora é correr atrás do preço. "
                       "Espere um recuo até a média MME21 ou um novo sinal.",
                       "Não opere agora — aguarde um recuo.", side_visible=False)

    return _signal("AGUARDE", "AGUARDE", "wait", None, None, False, [],
                   "Nenhum sinal claro agora",
                   "Os indicadores não estão alinhados. Operar sem confirmação é apostar. Aguarde.",
                   "Não opere agora — aguarde confirmação.")


def _signal(action, title, tone, setup, conf, right_moment, warnings, headline, explanation, simple,
            side_visible: bool = True) -> dict:
    return {
        "action": action, "title": title, "tone": tone,
        "side": setup.side if (setup and side_visible) else None,
        "setup": setup.name if setup else None, "setup_id": setup.id if setup else None,
        "confidence": conf["value"] if conf else None, "confidence_info": conf,
        "right_moment": right_moment, "warnings": warnings,
        "headline": headline, "explanation": explanation, "simple": simple,
        "window": None, "horizon": None,
    }


# ---------------------------------------------------------------- posição aberta

def position_signal(position: dict, ctx: dict, max_stop: float, decimals: int) -> dict:
    row, closed_row, price = ctx["row"], ctx["closed_row"], ctx["price"]
    long = position["side"] == BUY
    entry = float(position["entry_price"])
    stop = position.get("stop_price") or (entry * (1 - max_stop / 100) if long else entry * (1 + max_stop / 100))
    target = position.get("target_price")
    pnl = (price / entry - 1) * 100 * (1 if long else -1)
    risk_dist = abs(entry - stop)
    gain_dist = (price - entry) if long else (entry - price)
    rules = []

    def rule(name, hit, text):
        rules.append({"name": name, "ok": bool(hit), "text": text})
        return bool(hit)

    stop_hit = rule("STOP LOSS", price <= stop if long else price >= stop,
                    f"Preço {'abaixo' if long else 'acima'} de {fmt_price(stop, decimals)} (proteção obrigatória)")
    target_hit = rule("ALVO", target is not None and (price >= target if long else price <= target),
                      f"Preço chegou ao alvo {fmt_price(target, decimals)}" if target else "Sem alvo definido")
    if long:
        profit_hit = rule("SAIR PARA LUCRO", row["rsi"] > 70 or row["high"] >= row["bb_upper"],
                          "RSI acima de 70 ou preço tocou a banda superior")
        reversal_hit = rule("TENDÊNCIA INVERTEU",
                            closed_row["macd"] < closed_row["macd_signal"] and closed_row["ema9"] < closed_row["ema21"],
                            "MACD abaixo do sinal + MME9 abaixo da MME21")
    else:
        profit_hit = rule("SAIR PARA LUCRO", row["rsi"] < 30 or row["low"] <= row["bb_lower"],
                          "RSI abaixo de 30 ou preço tocou a banda inferior")
        reversal_hit = rule("TENDÊNCIA INVERTEU",
                            closed_row["macd"] > closed_row["macd_signal"] and closed_row["ema9"] > closed_row["ema21"],
                            "MACD acima do sinal + MME9 acima da MME21")

    # Stop móvel (chandelier): acompanha o preço a 2,5 ATR do extremo desde a entrada.
    trail = None
    atr = float(row["atr"]) if pd.notna(row["atr"]) else 0
    if atr and ctx.get("since_entry") is not None and len(ctx["since_entry"]):
        seg = ctx["since_entry"]
        trail = float(seg["high"].max() - 2.5 * atr) if long else float(seg["low"].min() + 2.5 * atr)
        if (long and trail <= stop) or (not long and trail >= stop):
            trail = None

    base = {"side": position["side"], "setup": None, "setup_id": None, "confidence": None, "confidence_info": None,
            "window": None, "horizon": None, "exit_rules": rules, "pnl_pct": round(pnl, 2), "warnings": [],
            "trailing_stop": num(trail, decimals)}

    def exit_(headline, explanation, simple, urgent=False):
        return {**base, "action": "SAIR_AGORA", "title": "SAIR AGORA", "tone": "exit", "right_moment": True,
                "headline": headline, "explanation": explanation, "simple": simple, "urgent": urgent}

    if stop_hit:
        return exit_("STOP LOSS ATIVADO",
                     f"Resultado {pnl:+.2f}%. Esse é o limite de proteção. Saia imediatamente — não espere recuperar.",
                     "STOP LOSS — SAIA AGORA.", urgent=True)
    if target_hit:
        return exit_("ALVO ATINGIDO — realize o lucro",
                     f"Resultado {pnl:+.2f}%. O preço chegou no alvo planejado. Disciplina: realize.",
                     "Alvo atingido. Realize o lucro.")
    if trail is not None and ((long and price <= trail) or (not long and price >= trail)) and gain_dist > 0:
        return exit_("STOP MÓVEL ATINGIDO — proteja o lucro",
                     f"Resultado {pnl:+.2f}%. O preço devolveu 2,5 ATR desde o melhor ponto. Saia com o lucro.",
                     "Stop móvel atingido. Saia.")
    if profit_hit and risk_dist and gain_dist >= 0.5 * risk_dist:
        return exit_("SAIR PARA LUCRO — preço esticado",
                     f"Resultado {pnl:+.2f}%. O preço está esticado e costuma devolver daqui. "
                     "Realize o lucro total ou parcial. Não seja ganancioso.",
                     "Preço esticado. Realize o lucro.")
    if reversal_hit:
        return exit_("TENDÊNCIA INVERTEU — saia para não perder mais",
                     f"Resultado {pnl:+.2f}%. MACD e médias viraram contra a sua operação.",
                     "Tendência virou. Saia.")
    hint = "Continue acompanhando. Saia só quando aparecer um dos sinais de saída."
    if profit_hit and gain_dist > 0:
        hint = "Preço esticado a seu favor, mas o lucro ainda é pequeno. Se virar contra, o stop protege."
    if risk_dist and gain_dist >= risk_dist:
        hint = (f"Lucro já passou de 1x o risco: mova o stop para o preço de entrada ({fmt_price(entry, decimals)})"
                f"{' ou para o stop móvel ' + fmt_price(trail, decimals) if trail else ''} e proteja o ganho.")
    return {**base, "action": "MANTENHA", "title": "MANTENHA", "tone": "hold", "right_moment": False,
            "headline": f"Posição de {position['side']} aberta · {pnl:+.2f}%",
            "explanation": hint, "simple": "Mantenha. Nenhum sinal de saída."}


# ---------------------------------------------------------------- plano

FOREX_LOT = 100_000


def pip_size(symbol: str, kind: str) -> float | None:
    if kind == "forex":
        return 0.01 if "JPY" in symbol else 0.0001
    if symbol in ("GC=F",):
        return 0.1
    return None


def trade_plan(price, atr, side, settings_num, decimals, symbol: str, kind: str, dist: float | None = None) -> dict:
    """`dist` = distância de um stop estrutural (atrás da zona); sem ela o stop sai do ATR."""
    atr_mult, max_stop, reward = settings_num["atr_mult"], settings_num["max_stop"], settings_num["reward"]
    structural = dist is not None
    dist = min(dist, price * max_stop / 100) if structural else stop_distance(price, atr, atr_mult, max_stop)
    long = side == BUY
    capital, risk_pct = settings_num["capital"], settings_num["risk_pct"]
    risk_amount = capital * risk_pct / 100
    qty = risk_amount / dist if dist else 0
    leveraged = kind in ("forex", "futures", "b3fut")  # operam com margem, não com o valor cheio
    capped = qty * price > capital and not leveraged
    if capped:
        qty = capital / price
    plan = {
        "side": side,
        "entry": num(price, decimals),
        "stop": num(price - dist if long else price + dist, decimals),
        "stop_pct": num(dist / price * 100, 3),
        "stop_by": ("zona" if structural else "ATR") if dist < price * max_stop / 100 - 1e-12 else f"limite de {max_stop:g}%",
        "max_stop_pct": max_stop,
        "atr_mult": atr_mult,
        "target1": num(price + dist if long else price - dist, decimals),
        "target2": num(price + dist * reward if long else price - dist * reward, decimals),
        "target1_pct": num(dist / price * 100, 3),
        "target2_pct": num(dist * reward / price * 100, 3),
        "reward_ratio": reward,
        "capital": capital,
        "risk_pct": risk_pct,
        "risk_amount": num(qty * dist, 2),
        "quantity": num(qty, 6 if price > 1000 else 2),
        "notional": num(qty * price, 2),
        "capped": capped,
        "stop_dist": dist,
    }
    pip = pip_size(symbol, kind)
    if pip:
        stop_pips = dist / pip
        if kind == "forex":
            quote, base = symbol[3:6], symbol[:3]
            pip_value = FOREX_LOT * pip if quote == "USD" else FOREX_LOT * pip / price if base == "USD" else FOREX_LOT * pip
            approx = quote != "USD" and base != "USD"
        else:  # ouro: contrato padrão de 100 onças
            pip_value, approx = 100 * pip, False
        lots = risk_amount / (stop_pips * pip_value) if stop_pips and pip_value else None
        plan["pips"] = {
            "size": pip, "stop": round(stop_pips, 1), "target1": round(stop_pips, 1),
            "target2": round(stop_pips * reward, 1), "pip_value_per_lot": round(pip_value, 2),
            "lots": round(lots, 2) if lots else None, "approx": approx,
            "unit": "lote padrão (100 mil)" if kind == "forex" else "contrato (100 oz)",
        }
    return plan


def position_plan(position, price, atr, settings_num, decimals, symbol, kind) -> dict:
    """Plano da operação já aberta: níveis a partir do preço de entrada registrado."""
    entry = float(position["entry_price"])
    long = position["side"] == BUY
    max_stop, reward = settings_num["max_stop"], settings_num["reward"]
    plan = trade_plan(entry, atr, position["side"], settings_num, decimals, symbol, kind)
    stop = position.get("stop_price") or entry * (1 - max_stop / 100 if long else 1 + max_stop / 100)
    dist = abs(entry - stop)
    target = position.get("target_price") or (entry + dist * reward if long else entry - dist * reward)
    qty = position.get("quantity")
    plan.update({
        "stop": num(stop, decimals), "stop_pct": num(dist / entry * 100, 3),
        "target1": num(entry + dist if long else entry - dist, decimals), "target1_pct": num(dist / entry * 100, 3),
        "target2": num(target, decimals), "target2_pct": num(abs(target - entry) / entry * 100, 3),
        "quantity": num(qty, 6) if qty else plan["quantity"],
        "notional": num(qty * entry, 2) if qty else plan["notional"],
        "risk_amount": num(qty * dist, 2) if qty else plan["risk_amount"],
        "from_position": True, "stop_dist": dist,
    })
    return plan


def position_view(position: dict, price: float, decimals: int) -> dict:
    long = position["side"] == BUY
    entry = float(position["entry_price"])
    qty = float(position.get("quantity") or 0)
    return {
        **position,
        "current_price": num(price, decimals),
        "pnl_pct": num((price / entry - 1) * 100 * (1 if long else -1), 2),
        "pnl_amount": num((price - entry) * qty * (1 if long else -1), 2) if qty else None,
    }
