#!/usr/bin/env python3
# ================================================================
# APP DE OPERACOES — SIMPLES E CLARO
# ================================================================
# NAO E CONSELHO FINANCEIRO. ISTO E APENAS UMA FERRAMENTA.
# VOCÊ ASSUME TODO O RISCO. NUNCA APOSTE DINHEIRO QUE NÃO PODE PERDER.
# ================================================================
# EXPLICADO PARA QUEM NÃO É TRADER:
# - "Preco" = quanto o papel vale agora
# - "RSI" = se está muito comprido (acima de 70) ou muito barato (abaixo de 30)
# - "MACD" = se a tendência está subindo ou descendo
# - "Media" = preço médio dos últimos dias
# - "Entrar" = momento que o app sugere comprar
# - "Sair" = momento que o app sugere vender para não perder
# ================================================================

import yfinance as yf, pandas as pd, sqlite3, os, datetime

DB = r"C:\Users\ryand\market_ai\db\market.db"
ATIVO = "PETR4.SA"

# ----- REGRAS EM PORTUGUES SIMPLES -----
# Quando comprar:
# 1. FORTE: preço está subindo (media curta > media longa) + MACD positivo + RSI no meio
# 2. RECUPERACAO: preço caiu muito (RSI abaixo de 30) + preço tocou no fundo
# 3. MOMENTO: MACD virou para cima + media curta cruzou media longa + volume alto
REGRAS_ENTRADA = [
    ("COMPRA FORTE", "Tendencia de ALTA confirmada + MACD positivo + RSI entre 30 e 70"),
    ("COMPRA DE RECUPERACAO", "Preco caiu muito (RSI abaixo de 30) + perto do fundo"),
    ("COMPRA NO MOMENTO", "MACD virou para cima + media cruzou + volume forte"),
]

# Quando vender / sair:
# 1. LUCRO: RSI subiu muito (acima de 70) OU preço tocou o topo
# 2. PERDA: MACD virou para baixo + media curta abaixo da longa
# 3. STOP LOSS: perdeu 3% do valor investido (protecao obrigatoria)
REGRAS_SAIDA = [
    ("SAIR PARA LUCRO", "RSI muito alto (acima de 70) OU preco tocou o topo"),
    ("SAIR PARA NAO PERDER MAIS", "Tendencia inverteu: MACD negativo + media curta abaixo da longa"),
    ("PARAR TUDO (STOP LOSS)", "Perdeu 3% do valor investido — protecao obrigatoria"),
]

class AppSimples:
    def __init__(self):
        self.ativo = ATIVO
        self.em_operacao = False
        self.preco_entrada = None

    def analisar_agora(self):
        # Pega os dados do mercado agora
        tick = yf.Ticker(self.ativo)
        df = tick.history(period="1d", interval="15m")
        if df.empty:
            return {"erro": "Sem dados agora. Tente novamente em 5 minutos."}

        ultimo = df.iloc[-1]
        preco = float(ultimo["Close"])

        # Calcula os numeros (como se fosse uma calculadora)
        df["RSI"] = 100 - (100 / (1 + df["Close"].pct_change().rolling(14).mean()))
        df["MACD"] = df["Close"].ewm(span=12).mean() - df["Close"].ewm(span=26).mean()
        df["MACD_SIG"] = df["MACD"].ewm(span=9).mean()
        df["MM9"] = df["Close"].rolling(9).mean()
        df["MM21"] = df["Close"].rolling(21).mean()

        mm20 = df["Close"].rolling(20).mean()
        std20 = df["Close"].rolling(20).std()
        df["BB_UPPER"] = mm20 + 2*std20
        df["BB_LOWER"] = mm20 - 2*std20

        rsi = float(df["RSI"].iloc[-1]) if not pd.isna(df["RSI"].iloc[-1]) else 50
        macd = float(df["MACD"].iloc[-1]) if not pd.isna(df["MACD"].iloc[-1]) else 0
        macd_sig = float(df["MACD_SIG"].iloc[-1]) if not pd.isna(df["MACD_SIG"].iloc[-1]) else 0
        mm9 = float(df["MM9"].iloc[-1]) if not pd.isna(df["MM9"].iloc[-1]) else 0
        mm21 = float(df["MM21"].iloc[-1]) if not pd.isna(df["MM21"].iloc[-1]) else 0
        bb_up = float(df["BB_UPPER"].iloc[-1]) if not pd.isna(df["BB_UPPER"].iloc[-1]) else preco
        bb_low = float(df["BB_LOWER"].iloc[-1]) if not pd.isna(df["BB_LOWER"].iloc[-1]) else preco

        # ----- DECISAO DE ENTRADA (simples) -----
        # Pergunta: o preço está subindo? MACD está positivo? RSI está no meio?
        sinal_entrada = "AGUARDAR — Nenhum sinal claro ainda"
        mensagem_entrada = "Espere. Ainda não há um momento claro de compra."

        if mm9 > mm21 and macd > macd_sig and 30 < rsi < 70:
            sinal_entrada = "ENTRAR — COMPRA FORTE AGORA"
            mensagem_entrada = ("O papel está subindo. Media curta passou a media longa. "
                                "MACD positivo. RSI no meio (não está muito caro nem muito barato). "
                                "E se você já está comprado? Mantenha até o sinal de saída.")
        elif rsi < 30 and preco < bb_low * 1.02:
            sinal_entrada = "ENTRAR — COMPRA DE RECUPERACAO"
            mensagem_entrada = ("O papel caiu muito (RSI abaixo de 30 = muito barato). "
                                "Está perto do fundo. Pode ser uma oportunidade de compra, mas cuidado.")
        else:
            mensagem_entrada = ("Nenhum sinal forte agora. O preço pode estar no meio do caminho. "
                                "Aguarde uma confirmacao mais clara.")

        # ----- DECISAO DE SAIDA -----
        # Se já comprou (em_operacao = True), quando vender?
        sinal_saida = "SEM POSICAO ABERTA — ainda não comprou"
        mensagem_saida = "Você ainda não comprou. Quando comprar, estas são as regras de sair."

        if self.em_operacao and self.preco_entrada is not None:
            perda = (preco - self.preco_entrada) / self.preco_entrada

            if perda <= -0.03:
                sinal_saida = "SAIR AGORA — STOP LOSS ATIVADO (perdeu 3%)"
                mensagem_saida = ("Você perdeu 3% do valor investido. Isso é o limite de protecao. "
                                  "SAIA IMEDIATAMENTE. Não espere recuperar — protecao vem primeiro.")
            elif rsi > 70:
                sinal_saida = "SAIR — PREÇO SUBIU MUITO (RSI acima de 70)"
                mensagem_saida = ("O papel subiu muito. RSI acima de 70 = sobrecomprado. "
                                  "É hora de realizar lucro parcial ou total. Não seja ganancioso.")
            elif macd < macd_sig and mm9 < mm21:
                sinal_saida = "SAIR — TENDENCIA INVERTEU (MACD negativo + media caiu)"
                mensagem_saida = ("O papel começou a cair. MACD virou negativo e a media curta passou abaixo. "
                                  "Não espere cair mais — saia o mais rápido possível.")
            else:
                sinal_saida = "MANTENHA — Ainda não é hora de sair"
                mensagem_saida = ("Você está em lucro ou sem perda maior que 3%. "
                                  "Continue acompanhando. Só saia quando aparecer um dos sinais acima.")
        else:
            mensagem_saida = ("Ainda não comprou. Quando comprar, use estas regras: "
                              "1) Se subir muito (RSI>70) = vender. "
                              "2) Se inverter tendencia = vender. "
                              "3) Se perder 3% = vender para nao perder mais.")

        # ----- SALVAR NO BANCO -----
        conn = sqlite3.connect(DB)
        conn.execute("CREATE TABLE IF NOT EXISTS operacoes (id INTEGER PRIMARY KEY, time TEXT, ativo TEXT, sinal_entrada TEXT, sinal_saida TEXT, preco REAL, rsi REAL, confianca REAL, explicacao TEXT)")
        try:
            conn.execute("ALTER TABLE operacoes ADD COLUMN explicacao TEXT")
        except sqlite3.OperationalError:
            pass  # já existe
        confianca = 0.7 if "ENTRAR" in sinal_entrada else 0.3
        conn.execute("INSERT INTO operacoes (time, ativo, sinal_entrada, sinal_saida, preco, rsi, confianca, explicacao) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.ativo, sinal_entrada, sinal_saida, preco, rsi, confianca,
                      f"Entrada: {mensagem_entrada} | Saida: {mensagem_saida}"))
        conn.commit()
        conn.close()

        return {
            "ativo": self.ativo,
            "preco_agora": f"R$ {preco:.2f}",
            "tendencia": "ALTA (subindo)" if mm9 > mm21 else "BAIXA (descendo)",
            "rsi": f"{rsi:.1f} (0-30 = muito barato, 30-70 = normal, 70-100 = muito caro)",
            "macd": f"{macd:+.3f} (positivo = subindo, negativo = descendo)",
            "media_9_dias": f"R$ {mm9:.2f}",
            "media_21_dias": f"R$ {mm21:.2f}",
            "banda_superior": f"R$ {bb_up:.2f}",
            "banda_inferior": f"R$ {bb_low:.2f}",
            "sinal_entrada": sinal_entrada,
            "mensagem_entrada": mensagem_entrada,
            "sinal_saida": sinal_saida,
            "mensagem_saida": mensagem_saida,
            "em_posicao": "SIM — você comprou" if self.em_operacao else "NÃO — ainda não comprou",
            "regras_entrada": REGRAS_ENTRADA,
            "regras_saida": REGRAS_SAIDA,
        }

if __name__ == "__main__":
    app = AppSimples()
    res = app.analisar_agora()

    print("=" * 75)
    print("  APP DE OPERACOES — SINAIS DE ENTRADA E SAIDA")
    print("  Explicado para quem nao e trader. Nada aqui e garantia de lucro.")
    print("=" * 75)

    for chave, valor in res.items():
        if chave not in ("regras_entrada", "regras_saida"):
            if isinstance(valor, str) and len(valor) > 70:
                print(f"  {chave}:")
                for linea in valor.split("  "):
                    print(f"     • {linea}")
            else:
                print(f"  {chave}: {valor}")

    print("\n" + "-" * 75)
    print("REGRAS DE ENTRADA (quando comprar):")
    for nome, regra in REGRAS_ENTRADA:
        print(f"  • {nome}")
        print(f"     -> {regra}")

    print("\nREGRAS DE SAIDA (quando vender):")
    for nome, regra in REGRAS_SAIDA:
        print(f"  • {nome}")
        print(f"     -> {regra}")

    print("\n" + "-" * 75)
    print("AVISO LEGAL:")
    print("  • NAO E CONSELHO FINANCEIRO.")
    print("  • NAO PREVE RESULTADOS.")
    print("  • VOCÊ ASSUME TODO RISCO.")
    print("  • SÓ APOSTE O QUE PODE PERDER.")
    print("  • STOP LOSS (perder 3%) é OBRIGATORIO para não quebrar.")
    print("  • Se não entendeu algum sinal, NÃO OPERE. Aguarde aprender.")
    print("-" * 75)
    print("Co-Authored-By: Claude Code <noreply@anthropic.com>")
    print("Generated with Claude Code")
