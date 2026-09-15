"""Plano de escalada para cripto: começar sem dinheiro, depois com pouco, e só aumentar com resultado medido.

As regras são fixas e transparentes (não é recomendação de investimento):
- Nível 0 (treino): o AURUM registra e confere sozinho cada ENTRAR AGORA de cripto.
- Níveis 1+: contam as operações de cripto que você registrou em "Já entrei" e encerrou.
- SUBIR quando o nível atual tiver amostra suficiente e resultado positivo com queda controlada.
- DESCER quando a perda, a sequência de perdas ou a queda máxima passam do limite.
Você decide quando mudar de nível; o capital de ⚙ é ajustado junto.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .assets import classify
from .config import LOCAL_TZ

LADDER = [0, 50, 100, 200, 500, 1000, 2000, 5000]  # capital em R$ por nível (nível 0 simula o nível 1)
# Risco por operação em % do capital. Com pouco dinheiro 1% daria ordens abaixo do mínimo da Binance (US$ 5):
# a posição usa o saldo e a perda fica limitada pelo stop (nunca mais que 3% do preço).
RISK_BY_LEVEL = [3, 3, 3, 3, 2, 2, 1, 1]
ADVANCE = {"min_trades": 20, "min_total_r": 2.0, "min_profit_factor": 1.1, "max_drawdown_r": 6.0}
RETREAT = {"total_r": -4.0, "losing_streak": 5, "drawdown_r": 6.0}


def level_capital(level: int) -> float:
    return float(LADDER[1] if level == 0 else LADDER[level])


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=LOCAL_TZ)


def results_since(level: int, since: str | None, positions: list[dict], signals: list[dict]) -> list[dict]:
    """Resultados em R de cripto, na ordem em que fecharam, a partir do início do nível."""
    start = _when(since)
    rows = []
    if level == 0:
        for s in signals:
            closed = _when(s.get("closed_ts"))
            if s["status"] != "open" and s.get("result_r") is not None and closed and classify(s["symbol"]) == "crypto":
                rows.append({"when": closed, "r": float(s["result_r"]), "symbol": s["symbol"]})
    else:
        for p in positions:
            closed = _when(p.get("closed_at"))
            if p["status"] == "closed" and p.get("result_r") is not None and closed and classify(p["symbol"]) == "crypto":
                rows.append({"when": closed, "r": float(p["result_r"]), "symbol": p["symbol"]})
    if start:
        rows = [r for r in rows if r["when"] >= start]
    return sorted(rows, key=lambda r: r["when"])


def stats(rows: list[dict]) -> dict:
    rs = [r["r"] for r in rows]
    gains, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    peak = equity = drawdown = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    streak = 0
    for r in reversed(rs):
        if r >= 0:
            break
        streak += 1
    return {
        "trades": len(rs), "wins": sum(1 for r in rs if r > 0),
        "win_rate": round(sum(1 for r in rs if r > 0) / len(rs) * 100, 1) if rs else None,
        "total_r": round(sum(rs), 2), "profit_factor": round(gains / losses, 2) if losses else None,
        "max_drawdown_r": round(drawdown, 2), "losing_streak": streak,
    }


def evaluate(level: int, since: str | None, positions: list[dict], signals: list[dict]) -> dict:
    level = max(0, min(int(level), len(LADDER) - 1))
    st = stats(results_since(level, since, positions, signals))
    all_wins = st["profit_factor"] is None and st["total_r"] > 0  # sem nenhuma perda o fator não existe
    pf_ok = all_wins or (st["profit_factor"] or 0) >= ADVANCE["min_profit_factor"]
    checks = [
        {"label": f"Pelo menos {ADVANCE['min_trades']} operações conferidas", "ok": st["trades"] >= ADVANCE["min_trades"],
         "value": f"{st['trades']}/{ADVANCE['min_trades']}"},
        {"label": f"Resultado de +{ADVANCE['min_total_r']:g}R ou mais", "ok": st["total_r"] >= ADVANCE["min_total_r"],
         "value": f"{st['total_r']:+.1f}R"},
        {"label": f"Fator de lucro {ADVANCE['min_profit_factor']:g} ou mais", "ok": st["trades"] > 0 and pf_ok,
         "value": "—" if st["profit_factor"] is None else f"{st['profit_factor']:.2f}"},
        {"label": f"Queda máxima menor que {ADVANCE['max_drawdown_r']:g}R", "ok": st["max_drawdown_r"] < ADVANCE["max_drawdown_r"],
         "value": f"{st['max_drawdown_r']:.1f}R"},
    ]
    retreat_reasons = []
    if st["total_r"] <= RETREAT["total_r"]:
        retreat_reasons.append(f"Resultado do nível em {st['total_r']:+.1f}R (limite {RETREAT['total_r']:g}R).")
    if st["losing_streak"] >= RETREAT["losing_streak"]:
        retreat_reasons.append(f"{st['losing_streak']} perdas seguidas (limite {RETREAT['losing_streak']}).")
    if st["max_drawdown_r"] >= RETREAT["drawdown_r"]:
        retreat_reasons.append(f"Queda máxima de {st['max_drawdown_r']:.1f}R (limite {RETREAT['drawdown_r']:g}R).")

    top = level == len(LADDER) - 1
    if retreat_reasons and level >= 1:
        decision, text = "DESCER", "As regras do plano mandam voltar um nível e revisar antes de continuar."
    elif retreat_reasons:
        decision, text = "MANTER", "No treino os sinais perderam: continue sem dinheiro até o resultado melhorar."
    elif all(c["ok"] for c in checks) and not top:
        decision = "SUBIR"
        text = ("O treino passou nas regras: você pode começar com dinheiro real no nível 1." if level == 0 else
                f"Nível {level} aprovado: você pode subir para R$ {LADDER[level + 1]:,.0f}.".replace(",", "."))
    else:
        decision = "MANTER"
        text = "Continue neste nível até cumprir todas as condições."

    capital = level_capital(level)
    return {
        "level": level, "since": since, "started": bool(since), "capital": capital, "real_money": level > 0, "decision": decision, "text": text,
        "stats": st, "checks": checks, "retreat_reasons": retreat_reasons,
        "next_capital": None if top else float(LADDER[level + 1]) if level else float(LADDER[1]),
        "risk_pct": RISK_BY_LEVEL[level],
        "max_loss_per_trade": round(capital * RISK_BY_LEVEL[level] / 100, 2),
        "source": "sinais conferidos automaticamente" if level == 0 else "operações registradas em “Já entrei”",
        "ladder": [{"level": i, "capital": float(c) if i else float(LADDER[1]), "risk_pct": RISK_BY_LEVEL[i],
                    "label": "Treino (sem dinheiro)" if i == 0 else f"R$ {c:,.0f}".replace(",", ".")}
                   for i, c in enumerate(LADDER)],
        "rules": {"advance": ADVANCE, "retreat": RETREAT},
    }


def level_settings(level: int, now: datetime | None = None) -> dict[str, str]:
    level = max(0, min(int(level), len(LADDER) - 1))
    now = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
    return {"scale_level": str(level), "scale_since": now.isoformat(timespec="seconds"),
            "capital": f"{level_capital(level):g}", "risk_per_trade_pct": str(RISK_BY_LEVEL[level]),
            "crypto_spot_only": "1"}
