"""Boleta pronta para o MetaTrader 5, com as especificações reais do ativo na corretora.

Lotes pelo risco: perda por lote = (distância do stop ÷ tamanho do tick) × valor do tick de perda, na moeda da
conta — exatamente como a corretora calcula. Preços arredondados ao tick, checagem da distância mínima de stop
(stops level) e do spread. O AURUM não envia a ordem: você confere e envia no MT5.
"""

from __future__ import annotations

import math

from . import b3
from .explain import fmt_price

CURRENCY_SYMBOL = {"USD": "US$", "BRL": "R$", "EUR": "€", "GBP": "£", "USC": "US¢"}


def _decimals(step: float) -> int:
    text = f"{step:.10f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def build(plan: dict, signal: dict, position: dict | None, spec: dict, timeframe: str) -> dict:
    long = plan["side"] == "COMPRA"
    exiting = bool(position) and signal.get("action") == "SAIR_AGORA"
    tick, digits = spec["tick_size"] or spec["point"], spec["digits"]
    currency = spec.get("account_currency") or ""
    money_symbol = CURRENCY_SYMBOL.get(currency, currency or "$")

    def money(value: float) -> str:
        return f"{money_symbol} {fmt_price(value, 2)}"

    # Compra executa no ASK e venda no BID: parte do preço real de execução quando o MT5 informa.
    live = spec["ask"] if long else spec["bid"]
    entry = b3.round_to_tick(live if live > 0 and signal.get("action") == "ENTRAR_AGORA" else plan["entry"], tick)
    shift = entry - plan["entry"]  # mantém as distâncias do plano a partir do preço de execução
    away, toward = ("down", "down") if long else ("up", "up")
    stop = b3.round_to_tick(plan["stop"] + shift, tick, away)  # stop nunca mais apertado que o plano
    target1 = b3.round_to_tick(plan["target1"] + shift, tick, toward)  # alvo nunca mais longe que o plano
    target2 = b3.round_to_tick(plan["target2"] + shift, tick, toward)
    stop_dist, target_dist = abs(entry - stop), abs(target2 - entry)
    loss_per_lot = stop_dist / tick * spec["tick_value_loss"] if tick else 0.0
    gain_per_lot = target_dist / tick * spec["tick_value_profit"] if tick else 0.0

    warnings = []
    balance = spec.get("balance") or 0.0
    budget = balance * plan["risk_pct"] / 100 if balance > 0 else plan["capital"] * plan["risk_pct"] / 100
    if balance <= 0:
        warnings.append("Saldo da conta MT5 indisponível: o risco foi calculado com o capital de ⚙ (confira a moeda).")
    step, vmin, vmax = spec["volume_step"] or 0.01, spec["volume_min"] or 0.01, spec["volume_max"] or 100.0
    qty_decimals = _decimals(step)
    if position:
        lots = float(position.get("quantity") or 0)
    elif loss_per_lot > 0:
        lots = math.floor(budget / loss_per_lot / step + 1e-9) * step
        if lots < vmin:
            lots = vmin
            warnings.append(f"O lote mínimo ({vmin:g}) arrisca {money(vmin * loss_per_lot)}, acima do seu limite de "
                            f"{money(budget)}. Considere não operar esta entrada.")
        lots = min(lots, vmax)
    else:
        lots = 0.0
    lots = round(lots, qty_decimals)

    min_stop = spec.get("stops_level", 0) * spec["point"]
    if not exiting and min_stop and stop_dist < min_stop:
        warnings.append(f"O stop está a {fmt_price(stop_dist, digits)}, menos que a distância mínima da corretora "
                        f"({fmt_price(min_stop, digits)}): o MT5 vai recusar. Afaste o stop ou não opere.")
    spread = spec.get("spread_points", 0) * spec["point"]
    spread_cost = (spread / tick * spec["tick_value_loss"] * lots) if tick else 0.0
    if not exiting and spread and stop_dist and spread >= stop_dist * 0.2:
        warnings.append(f"Spread atual ({fmt_price(spread, digits)}) é {spread / stop_dist * 100:.0f}% da distância do "
                        "stop: o custo de entrada pesa muito nesta operação.")
    if not spec.get("trade_allowed", True):
        warnings.append("A corretora informa que este ativo não está liberado para negociação na sua conta.")
    if live > 0 and not exiting and abs(live - plan["entry"]) > max(plan["entry"] * 0.003, 10 * spread):
        warnings.append(f"O preço agora no MT5 ({fmt_price(live, digits)}) está longe do preço analisado "
                        f"({fmt_price(plan['entry'], digits)}): o mercado andou. Atualize a análise antes de enviar.")
    if spec.get("demo") is False:
        warnings.append("Conta REAL: confirme que o método já deu resultado na conta demo e na aba Desempenho.")

    name = spec["name"]
    lots_text = f"{lots:.{qty_decimals}f}"
    side_word = ("VENDA" if long else "COMPRA") if exiting else ("COMPRA" if long else "VENDA")
    if exiting:
        orders = [{"title": "Fechar a posição agora", "fields": [
            {"label": "Aba Negociação", "value": f"clique duplo na posição {name}"},
            {"label": "Tipo", "value": "Execução a Mercado"}, {"label": "Volume (lotes)", "key": "quantity"},
        ], "note": "Botão “Fechar”. Stop e take profit da posição somem junto."}]
    else:
        orders = [{"title": "Nova ordem (F9) · Execução a Mercado", "fields": [
            {"label": "Símbolo", "key": "code"}, {"label": "Tipo", "value": "Execução a Mercado"},
            {"label": "Volume (lotes)", "key": "quantity"}, {"label": "Stop Loss", "key": "stop"},
            {"label": "Take Profit", "key": "target2"}, {"label": "Comentário", "value": "AURUM"},
            {"label": "Botão", "value": "Compra a mercado (Buy)" if long else "Venda a mercado (Sell)"},
        ], "note": ("Envie só depois do fechamento do candle que confirmou o sinal. "
                    + ("A compra executa no ASK." if long else "A venda executa no BID."))}]

    ticket = {
        "available": True, "mode": "exit" if exiting else "entry", "exchange": "MetaTrader 5", "kind": "mt5",
        "code": name, "name": spec.get("description") or name, "side": side_word,
        "action": "Zerar a posição" if exiting else "Abrir posição",
        "order_type": "A mercado" if signal.get("action") in ("ENTRAR_AGORA", "SAIR_AGORA") else "Aguarde a confirmação do sinal",
        "quantity": lots, "qty_decimals": qty_decimals, "unit": "lote", "lot_note": None, "tick": tick,
        "point_value": spec["tick_value_loss"] / tick if tick else 0.0, "currency": currency, "currency_symbol": money_symbol,
        "entry": entry, "stop": stop, "stop_limit": stop, "target1": target1, "target2": target2,
        "stop_points": round(stop_dist / spec["point"]) if spec["point"] else None,
        "target_points": round(target_dist / spec["point"]) if spec["point"] else None,
        "risk_money": round(lots * loss_per_lot, 2), "reward_money": round(lots * gain_per_lot, 2),
        "fees_money": round(spread_cost, 2), "balance": balance, "spread": spread, "bid": spec["bid"], "ask": spec["ask"],
        "contract": None, "approximate": False, "warnings": warnings, "decimals": digits, "xp_orders": orders,
        "validity": "—", "window": (signal.get("window") or {}).get("label"),
        "steps": ["No MT5, abra o gráfico do ativo e confira se o preço bate com o AURUM.",
                  "Aperte F9 (Nova ordem), escolha Execução a Mercado e preencha volume, Stop Loss e Take Profit.",
                  "Envie só com o candle de confirmação já fechado e dentro da janela do sinal.",
                  "Registre a entrada no AURUM (“Já entrei nesta operação”) para ele acompanhar a saída."],
    }
    if exiting:
        lines = ["AURUM → METATRADER 5 (SAÍDA)", f"Símbolo: {name}", f"Fechar {side_word} {lots_text} lote(s) a mercado",
                 f"Motivo: {signal.get('headline', '')}"]
    else:
        lines = [
            "AURUM → ORDEM PARA O METATRADER 5",
            f"Símbolo: {name}" + (f" · {spec['company']}" if spec.get("company") else ""),
            f"{'COMPRA (Buy)' if long else 'VENDA (Sell)'} a mercado · volume {lots_text} lote(s)"
            + (f" · {ticket['window']}" if ticket["window"] else ""),
            f"Preço de referência: {fmt_price(entry, digits)} ({'ask' if long else 'bid'})",
            f"Stop Loss: {fmt_price(stop, digits)} (risco {money(ticket['risk_money'])})",
            f"Take Profit: {fmt_price(target2, digits)} (+{money(ticket['reward_money'])}) · alvo parcial {fmt_price(target1, digits)}",
        ]
    lines += [f"Atenção: {w}" for w in warnings]
    lines.append("Isto não prevê resultados. Use stop loss.")
    ticket["text"] = "\n".join(lines)
    return ticket
