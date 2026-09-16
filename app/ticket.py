"""Boleta pronta para digitar na XP (app, XP Pro/Profit ou MetaTrader 5).

O AURUM não envia ordens: ele monta os campos exatos (código do contrato, quantidade,
stop e alvo arredondados ao tick) para você conferir e enviar na plataforma da corretora.
"""

from __future__ import annotations

import math
from datetime import date

from . import b3, ticket_mt5
from .explain import fmt_price

FEE_RATE = 0.001  # Binance Spot: 0,1% por ordem (sem desconto de BNB)
BDR = {"AAPL": "AAPL34", "NVDA": "NVDC34", "TSLA": "TSLA34", "MSFT": "MSFT34", "AMZN": "AMZO34", "GOOGL": "GOGL34"}
STEPS = [
    "Confira se o sinal ainda está dentro da janela de entrada e se há saldo/garantia disponível na XP.",
    "No XP Unity, busque o ativo pelo código abaixo e abra a boleta.",
    "Boleta 1 (entrada): escolha o lado, Tipo de ordem “Limitada”, a quantidade e o preço indicados.",
    "Quando a entrada executar, Boleta 2 (proteção): lado contrário, Tipo de ordem “Stop Loss”, "
    "Preço disparo e Preço limite do plano, e ative o Stop Gain no alvo.",
    "Registre a entrada no AURUM (“Já entrei nesta operação”) para ele avisar a saída.",
]


def _money(value: float) -> str:
    return f"R$ {fmt_price(value, 2)}"


PLURAL = {"contrato": "contratos", "ação": "ações"}


def _units(qty: float, unit: str) -> str:
    return f"{qty:g} {unit if qty == 1 else PLURAL[unit]}"


def _decimals_of(step: float) -> int:
    text = f"{step:.10f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def _binance_ticket(plan: dict, signal: dict, position: dict | None, info: dict, brl_per_usd: float | None,
                    timeframe: str) -> dict:
    """Ordem para a Binance Spot: compra limitada + OCO (take profit e stop loss juntos)."""
    tick, step = info["tick"], info["step"]
    decimals, qty_decimals = _decimals_of(tick), _decimals_of(step)
    long = plan["side"] == "COMPRA"
    exiting = bool(position) and signal.get("action") == "SAIR_AGORA"
    away = "down" if long else "up"
    entry = b3.round_to_tick(plan["entry"], tick)
    stop = b3.round_to_tick(plan["stop"], tick, away)
    target1 = b3.round_to_tick(plan["target1"], tick, away)
    target2 = b3.round_to_tick(plan["target2"], tick, away)
    stop_limit = b3.round_to_tick(stop * (0.999 if long else 1.001), tick, away)  # folga de 0,1% para a ordem executar
    risk_per_coin = abs(entry - stop)

    warnings = []
    capital_usd = plan["capital"] / brl_per_usd if brl_per_usd else plan["capital"]
    if brl_per_usd:
        warnings.append(f"Capital de R$ {fmt_price(plan['capital'], 2)} convertido a US$ {fmt_price(capital_usd, 2)} "
                        f"(dólar a R$ {fmt_price(brl_per_usd, 2)}). A Binance opera em USDT.")
    budget = capital_usd * plan["risk_pct"] / 100
    if position:
        quantity = float(position.get("quantity") or 0)
    elif risk_per_coin > 0:
        raw = min(budget / risk_per_coin, capital_usd * 0.98 / entry)
        quantity = math.floor(raw / step + 1e-9) * step
    else:
        quantity = 0.0
    quantity = round(quantity, qty_decimals)
    notional = quantity * entry
    # Na compra a Binance desconta a taxa (0,1%) da própria moeda: a OCO precisa de um pouco menos, senão dá
    # "saldo insuficiente".
    oco_quantity = round(math.floor(quantity * (1 - FEE_RATE) / step + 1e-9) * step, qty_decimals) if long else quantity
    fees = notional * FEE_RATE * 2  # 0,1% na entrada + 0,1% na saída (taxa padrão)
    min_notional = info["min_notional"]
    if not position and (quantity < info["min_qty"] or notional < min_notional):
        warnings.append(f"A quantidade calculada ({quantity:g} {info['base']}) fica abaixo do mínimo da Binance "
                        f"(ordem de pelo menos US$ {fmt_price(min_notional, 2)}). Aumente o capital ou não opere.")
    elif not position and min(stop_limit, target2) * oco_quantity < min_notional:
        warnings.append(f"A proteção OCO ficaria abaixo do mínimo da Binance (US$ {fmt_price(min_notional, 2)} por "
                        "ordem) e seria recusada. Use um pouco mais de capital antes de entrar.")
    if not position and quantity > 0 and budget / risk_per_coin > quantity * 1.01 and capital_usd > 0:
        real_risk = quantity * risk_per_coin / capital_usd * 100
        warnings.append(f"Com esse capital a ordem usa quase todo o saldo: se bater o stop a perda é "
                        f"~{fmt_price(real_risk, 1)}% do capital (US$ {fmt_price(quantity * risk_per_coin, 2)}).")
    if not position and quantity > 0 and fees >= quantity * risk_per_coin * 0.25:
        warnings.append(f"As taxas (~US$ {fmt_price(fees, 2)}) comem {fees / (quantity * risk_per_coin) * 100:.0f}% do "
                        "risco desta operação — stop curto demais para o custo. Prefira o gráfico de 1h ou diário.")
    if not long and not position:
        warnings.insert(0, "Sinal de VENDA: na Binance Spot você só vende a moeda que já tem. Se não tem, não opere — "
                           "e evite Futuros/alavancagem, onde a perda pode passar do valor investido.")

    validity = "GTC (até cancelar)"
    base, quote = info["base"], info["quote"]
    if exiting:
        side = "VENDER" if long else "COMPRAR"
        orders = [{"title": "Zerar a posição agora", "fields": [
            {"label": "Mercado", "value": "Spot"}, {"label": "Operação", "value": side},
            {"label": "Par", "value": f"{base}/{quote}"}, {"label": "Tipo", "value": "Mercado"},
            {"label": f"Quantidade ({base})", "key": "quantity"},
        ], "note": "Antes, cancele a ordem OCO que ficou aberta em “Ordens abertas”."}]
    else:
        side = "COMPRAR" if long else "VENDER"
        orders = [
            {"title": "Ordem 1 · Entrada", "fields": [
                {"label": "Mercado", "value": "Spot"}, {"label": "Operação", "value": side},
                {"label": "Par", "value": f"{base}/{quote}"}, {"label": "Tipo", "value": "Limite"},
                {"label": "Preço", "key": "entry"}, {"label": f"Quantidade ({base})", "key": "quantity"},
            ], "note": "Se o preço fugir e ainda estiver na janela, use o tipo “Mercado”."},
            {"title": "Ordem 2 · Proteção OCO (logo depois de executar)", "fields": [
                {"label": "Operação", "value": "VENDER" if long else "COMPRAR"}, {"label": "Tipo", "value": "OCO"},
                {"label": "Take Profit · preço", "key": "target2"}, {"label": "Stop Loss · gatilho", "key": "stop"},
                {"label": "Stop Loss · limite", "key": "stop_limit"},
                {"label": f"Quantidade ({base})", "key": "oco_quantity"},
            ], "note": "A OCO coloca alvo e stop juntos: quando um executa, o outro é cancelado sozinho."
                       + (" A quantidade é um pouco menor que a da compra porque a Binance desconta a taxa de 0,1% "
                          "da moeda recebida." if long and oco_quantity < quantity else "")},
        ]
    ticket = {
        "available": True, "mode": "exit" if exiting else "entry", "exchange": "Binance",
        "code": f"{base}{quote}", "name": f"{base}/{quote} · Binance Spot", "kind": "crypto",
        "side": side, "action": "Zerar a posição" if exiting else "Abrir posição",
        "order_type": "A mercado" if signal.get("action") in ("ENTRAR_AGORA", "SAIR_AGORA") else "Aguarde a confirmação do sinal",
        "quantity": quantity, "oco_quantity": oco_quantity, "qty_decimals": qty_decimals, "unit": base, "lot_note": None,
        "min_notional": min_notional,
        "min_capital_brl": round(min_notional * 1.1 / (1 - FEE_RATE) * brl_per_usd, 2) if brl_per_usd else None,
        "tick": tick, "point_value": 1.0, "currency": quote, "currency_symbol": "US$",
        "entry": entry, "stop": stop, "stop_limit": stop_limit, "target1": target1, "target2": target2,
        "stop_points": round(risk_per_coin, decimals), "target_points": round(abs(target2 - entry), decimals),
        "risk_money": round(quantity * risk_per_coin, 2), "reward_money": round(quantity * abs(target2 - entry), 2),
        "fees_money": round(fees, 2), "notional": round(notional, 2),
        "contract": None, "approximate": False, "warnings": warnings, "decimals": decimals, "validity": validity,
        "xp_orders": orders, "window": (signal.get("window") or {}).get("label"),
        "steps": [
            "Abra o app da Binance em Spot (não use Futuros) e escolha o par indicado.",
            "Ordem 1: tipo Limite no preço e na quantidade da boleta (ou Mercado, se o preço fugir dentro da janela).",
            "Quando executar, Ordem 2: tipo OCO com Take Profit no alvo e Stop Loss (gatilho e limite) do plano.",
            "Registre a entrada no AURUM (“Já entrei nesta operação”) para ele avisar a saída.",
        ],
    }
    lines = [
        "AURUM → ORDEM PARA A BINANCE (Spot)",
        f"Par: {base}/{quote}",
        f"Operação: {side} {quantity:.{qty_decimals}f} {base} (~US$ {fmt_price(notional, 2)})",
        f"Entrada: limite {fmt_price(entry, decimals)}" + (f" · {ticket['window']}" if ticket["window"] else ""),
        f"OCO ({'VENDER' if long else 'COMPRAR'} {oco_quantity:.{qty_decimals}f} {base}) · Take Profit: "
        f"{fmt_price(target2, decimals)} · Stop: gatilho {fmt_price(stop, decimals)} / limite {fmt_price(stop_limit, decimals)}",
        f"Risco: US$ {fmt_price(ticket['risk_money'], 2)} · Alvo: +US$ {fmt_price(ticket['reward_money'], 2)} · Taxas estimadas: US$ {fmt_price(fees, 2)}",
    ]
    lines += [f"Atenção: {w}" for w in warnings]
    lines.append("Isto não prevê resultados. Use stop loss.")
    ticket["text"] = "\n".join(lines)
    return ticket


def build_ticket(symbol: str, kind: str, plan: dict, signal: dict, source: str, today: date,
                 position: dict | None = None, asset_name: str | None = None, timeframe: str = "15m",
                 binance: dict | None = None, brl_per_usd: float | None = None, mt5: dict | None = None,
                 mt5_waiting: str | None = None) -> dict:
    if mt5 and kind != "crypto":  # MetaTrader 5 conectado: boleta com lotes e ticks reais da corretora
        return ticket_mt5.build(plan, signal, position, mt5, timeframe)
    if mt5_waiting and kind != "crypto":  # MT5 ligado, mas sem os preços dele nesta análise: não mistura fontes
        return {"available": False, "reason": mt5_waiting, "suggest": None}
    if kind == "forex":
        return {"available": False, "reason": "A XP não oferece forex à vista para pessoa física. Para operar dólar "
                                               "pela XP, use o mini dólar (WDO) na B3.", "suggest": "WDOFUT"}
    if kind == "crypto":
        if not binance:
            return {"available": False, "reason": "Não consegui ler as regras deste par na Binance agora "
                                                   "(par inexistente ou sem internet).", "suggest": None}
        return _binance_ticket(plan, signal, position, binance, brl_per_usd, timeframe)
    if kind == "us":
        bdr = BDR.get(symbol)
        reason = "Ações americanas na XP são negociadas como BDR na B3"
        return {"available": False, "reason": f"{reason} (ex.: {bdr})." if bdr else f"{reason}. Confira o código na XP.",
                "suggest": f"{bdr}.SA" if bdr else None}
    if kind not in ("b3", "b3fut"):
        return {"available": False, "reason": "Ativo não negociado na B3 pela XP.", "suggest": None}

    long = plan["side"] == "COMPRA"
    exiting = bool(position) and signal.get("action") == "SAIR_AGORA"
    risk_budget = plan["capital"] * plan["risk_pct"] / 100
    approximate = "aproximada" in source

    if kind == "b3fut":
        spec = b3.FUTURES[symbol]
        contract = b3.current_contract(symbol, today)
        tick, point_value, unit = spec.tick, spec.point_value, "contrato"
        code, name = contract["code"], spec.name
    else:
        tick, point_value, unit = 0.01, 1.0, "ação"
        code, contract = symbol.removesuffix(".SA"), None
        name = asset_name if asset_name and asset_name != symbol else code

    entry = b3.round_to_tick(plan["entry"], tick)
    stop = b3.round_to_tick(plan["stop"], tick, "down" if long else "up")  # stop nunca mais apertado que o plano
    target1 = b3.round_to_tick(plan["target1"], tick, "down" if long else "up")  # alvo nunca mais longe que o plano
    target2 = b3.round_to_tick(plan["target2"], tick, "down" if long else "up")
    stop_limit = b3.round_to_tick(stop - 2 * tick if long else stop + 2 * tick, tick)
    stop_pts, target_pts = abs(entry - stop), abs(target2 - entry)
    per_unit_risk = stop_pts * point_value

    warnings = []
    if position:
        quantity = float(position.get("quantity") or 0) or None
    elif per_unit_risk <= 0:
        quantity = None
    elif kind == "b3fut":
        quantity = math.floor(risk_budget / per_unit_risk)
        if quantity < 1:
            quantity = 1
            warnings.append(f"1 contrato arrisca {_money(per_unit_risk)}, acima do seu limite de {_money(risk_budget)}. "
                            "Considere não operar ou aumentar o capital configurado.")
    else:
        quantity = math.floor(min(risk_budget / per_unit_risk, plan["capital"] / entry))

    lot_note = None
    if kind == "b3" and quantity:
        if quantity >= 100:
            quantity = quantity // 100 * 100
            lot_note = "lote padrão (múltiplos de 100)"
        else:
            code = f"{code}F"
            lot_note = "mercado fracionário (1 a 99 ações)"
    if kind == "b3fut" and contract and contract["days_to_expiry"] <= 3:
        warnings.append(f"{code} vence em {contract['days_to_expiry']} dia(s): confira se a XP já migrou para o próximo vencimento.")
    if approximate:
        warnings.append("Sem MetaTrader 5 conectado os preços são aproximados. Use as distâncias em pontos a partir "
                        "do preço real mostrado na XP.")

    if kind == "b3" and not long and not position:
        warnings.insert(0, "Sinal de VENDA em ação: vender sem ter as ações na carteira é venda a descoberto "
                           "(exige aluguel e garantia na XP). Se você não tem este papel, não abra venda — "
                           "considere só sinais de COMPRA em ações ou use WDO/WIN, que operam nos dois lados.")

    qty = quantity or 0
    side_word = ("VENDA" if long else "COMPRA") if exiting else ("COMPRA" if long else "VENDA")
    validity = "Hoje" if timeframe in ("1m", "5m", "15m") else "Até cancelar (ou a mais longa disponível)"
    if exiting:
        xp_orders = [{"title": "Zerar a posição agora", "fields": [
            {"label": "Operação", "value": side_word}, {"label": "Ativo", "key": "code"},
            {"label": "Tipo de ordem", "value": "A mercado"}, {"label": "Quantidade", "key": "quantity"},
        ], "note": "Depois de executar, cancele as ordens de Stop Loss/Stop Gain que ficaram abertas."}]
    else:
        protect = "VENDA" if side_word == "COMPRA" else "COMPRA"
        xp_orders = [
            {"title": "Boleta 1 · Entrada", "fields": [
                {"label": "Operação", "value": side_word}, {"label": "Ativo", "key": "code"},
                {"label": "Tipo de ordem", "value": "Limitada"}, {"label": "Quantidade", "key": "quantity"},
                {"label": "Preço", "key": "entry"}, {"label": "Validade", "value": validity},
            ], "note": "Se o preço fugir e você ainda estiver dentro da janela, pode usar “A mercado”."},
            {"title": "Boleta 2 · Proteção (logo depois de executar)", "fields": [
                {"label": "Operação", "value": protect}, {"label": "Tipo de ordem", "value": "Stop Loss"},
                {"label": "Quantidade", "key": "quantity"}, {"label": "Preço disparo", "key": "stop"},
                {"label": "Preço limite", "key": "stop_limit"}, {"label": "Stop Gain", "key": "target2"},
                {"label": "Validade", "value": validity},
            ], "note": "Nunca deixe a posição sem esta ordem. Sem ela, não há stop."},
        ]
    action = "Zerar a posição" if exiting else "Abrir posição"
    order_type = "A mercado" if signal.get("action") in ("ENTRAR_AGORA", "SAIR_AGORA") else "Aguarde a confirmação do sinal"
    points_word = "pts" if kind == "b3fut" else "R$/ação"

    ticket = {
        "available": True, "mode": "exit" if exiting else "entry", "code": code, "name": name, "kind": kind,
        "side": side_word, "action": action, "order_type": order_type, "quantity": qty, "unit": unit,
        "lot_note": lot_note, "tick": tick, "point_value": point_value,
        "entry": entry, "stop": stop, "stop_limit": stop_limit, "target1": target1, "target2": target2,
        "stop_points": round(stop_pts, 4), "target_points": round(target_pts, 4),
        "risk_money": round(qty * per_unit_risk, 2), "reward_money": round(qty * target_pts * point_value, 2),
        "contract": contract, "approximate": approximate, "warnings": warnings, "steps": STEPS,
        "xp_orders": xp_orders, "validity": validity,
        "window": (signal.get("window") or {}).get("label"),
    }
    decimals = 1 if kind == "b3fut" and tick < 1 else 0 if kind == "b3fut" else 2
    expiry = date.fromisoformat(contract["expiry"]).strftime("%d/%m/%Y") if contract else None
    ticket["expiry_label"] = expiry
    if exiting:
        lines = [
            "AURUM → ORDEM PARA A XP (SAÍDA)",
            f"Ativo: {code} · {name}",
            f"Operação: {side_word} {_units(qty, unit)} a mercado — zerar posição",
            f"Motivo: {signal.get('headline', '')}",
        ]
    else:
        lines = [
            "AURUM → ORDEM PARA A XP",
            f"Ativo: {code} · {name}" + (f" (vence {expiry})" if expiry else ""),
            f"Operação: {side_word} {_units(qty, unit)}" + (f" · {lot_note}" if lot_note else ""),
            f"Entrada: {order_type.lower()} (~{fmt_price(entry, decimals)})" + (f" · {ticket['window']}" if ticket["window"] else ""),
            f"Stop loss: disparo {fmt_price(stop, decimals)} · limite {fmt_price(stop_limit, decimals)} "
            f"({fmt_price(stop_pts, decimals)} {points_word} = {_money(ticket['risk_money'])})",
            f"Alvo 1: {fmt_price(target1, decimals)} · Alvo 2 (stop gain): {fmt_price(target2, decimals)} "
            f"(+{_money(ticket['reward_money'])})",
        ]
    lines += [f"Atenção: {w}" for w in warnings]
    lines.append("Isto não prevê resultados. Use stop loss.")
    ticket["text"] = "\n".join(lines)
    ticket["decimals"] = decimals
    return ticket
