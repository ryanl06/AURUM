=== PROMPT PARA CLAUDE DESKTOP ===
Crie um APP DE OPERACOES PROFISSIONAL com estes requisitos exatos:

1. SELEÇÃO DE MOEDA EM TEMPO REAL
   - Deixe escolher o ativo digitando (ex: PETR4.SA, VALE3.SA, XAUUSD=X, EURUSD=X, USDJPY=X, BTC-USD)
   - A moeda pode ser alterada a qualquer momento sem fechar o app
   - Mostre o preço atual, tendência (ALTA/BAIXA), e moeda selecionada claramente

2. ANALISE CONTINUA E CLARA (não precisa ser complexa, precisa ser precisa)
   - Use RSI (14), MACD (12/26/9), Médias Móveis (9 e 21 dias), Bandas de Bollinger (20, 2 desvios)
   - Analise durante o tempo que o usuário precisar (loop contínuo, não apenas uma vez)
   - Cada 15 minutos (ou configurável) gere uma análise nova
   - Guarde todos os dados em SQLite para consultar depois

3. SINAL DE ENTRADA E SAIDA — OBJETIVO E DIRETO
   - ENTRAR COMPRA FORTE: MM9 > MM21 + MACD positivo + RSI 30-70 + tendência ALTA
   - ENTRAR RECUPERAÇÃO: RSI < 30 + preço perto da banda inferior + MACD estável
   - ENTRAR MOMENTO: MACD cruzou para cima + MM9 cruzou MM21 + volume alto
   - SAIR LUCRO: RSI > 70 OU preço tocou banda superior
   - SAIR PERDA: MACD negativo + MM9 abaixo MM21
   - STOP LOSS OBRIGATORIO: -3% do preço de entrada (se já comprou)
   - MOSTRE O MOMENTO CERTO: "AGUARDE", "ENTRAR AGORA", "SAIR AGORA", "MANTENHA"

4. PREVISÃO DIRETA — SUBIR OU DEScer
   - Com base nos indicadores, diga claramente: "TENDENCIA ALTA — provavelmente sobe" ou "TENDENCIA BAIXA — provavelmente desce"
   - Se RSI está muito alto (>70): "Sobrecomprado — pode cair"
   - Se RSI muito baixo (<30): "Sobrevendido — pode subir"
   - Se MACD cruzou para cima: "Tendência de alta confirmada"
   - Se MACD negativo: "Tendência de baixa confirmada"

5. INTERFACE — CLARA E SIMPLES
   - Mostre em tela: Ativo / Preço / RSI / MACD / Médias / Tendência / Sinal de Entrada / Sinal de Saída
   - Use linguagem simples: não use jargão, explique cada número
   - Se o usuário não é trader, cada linha deve ser autoexplicativa
   - Mostre "Este é o momento certo de operar?" — SIM / NÃO

6. PODE ALTERAR MOEDA
   - Permita trocar de PETR4 para XAU/USD/etc sem fechar
   - Quando trocar, analise imediatamente o novo ativo

7. NÃO É CONSELHO FINANCEIRO (obrigatório)
   - Sempre mostre aviso: "ISTO NÃO PREVÊ RESULTADOS. USE STOP LOSS. NÃO APOSTE O QUE NÃO PODE PERDER."

8. CÓDIGO BASE PARA O CLAUDE DESKTOP (copiado de codigo_app_trader.py)
   - Use yfinance para buscar dados
   - Calcule RSI, MACD, MM9/MM21, Bollinger com pandas
   - Salve resultados em SQLite (tabela operacoes)
   - Use threading para análise contínua

=== ARQUIVO COM CODIGO ===
codigo_app_trader.py (incluído nesta pasta)

=== INSTRUÇÃO FINAL ===
Faça o app rodar perfeitamente. Não precisa ser bonito, precisa ser exato e claro. O usuário precisa escolher moeda, ver análise contínua, receber sinal direto de quando entrar e sair, e saber se vai subir ou descer. Tudo explicativo.

Co-Authored-By: Claude Code <noreply@anthropic.com>
🤖 Generated with Claude Code
