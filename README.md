# AURUM — Analisador de Mercado

Painel web local que analisa ouro, forex, índices, B3 e cripto como um trader e diz **quando entrar, quando esperar
e quando sair** — com janela de entrada, aviso antecipado (PREPARE-SE), stop loss obrigatório, boleta pronta e
números **medidos no histórico**. Conectado ao **MetaTrader 5**, usa os preços, o histórico, as especificações do
ativo e as zonas que você desenha na sua corretora.

> **ISTO NÃO PREVÊ RESULTADOS. USE STOP LOSS. NÃO APOSTE O QUE NÃO PODE PERDER.**
> Não é recomendação de investimento. O AURUM só lê dados e monta boletas: **nunca envia ordens**.

## Como abrir

O AURUM abre no navegador em **http://localhost:8765**; o motor roda no seu computador, em segundo plano.

| Arquivo | Para que serve |
|---|---|
| `ATIVAR_INICIO_AUTOMATICO.bat` | **Rode uma vez.** O AURUM liga sozinho quando você entra no Windows. |
| `INICIAR_AURUM.bat` | Liga agora (instala as dependências na primeira vez) e abre o navegador. |
| `PARAR_AURUM.bat` | Desliga o servidor. |
| `DESATIVAR_INICIO_AUTOMATICO.bat` | Tira da inicialização do Windows. |

Pelo terminal: `pip install -r requirements.txt` e depois `python run.py`. Log em `data/aurum.log`.
Primeira vez? Aba **Tutorial** → **Fazer o tour guiado**.

## MetaTrader 5

Com o MT5 aberto e logado (⚙ → **Usar MetaTrader 5**, ligado por padrão):

- **Preços e histórico da corretora:** M15 com até 80 mil candles (na Hantec, XAUUSD desde abril de 2023) para o
  backtest; H1 com 22 mil; tempo real no gráfico. O limite é o “Máx. barras no gráfico” do MT5 (padrão 100 mil).
- **Nome do ativo encontrado sozinho**, com o sufixo da corretora (`XAUUSD`, `XAUUSD.m`, `XAUUSDm`, `GOLD`…),
  preferindo o que está habilitado na sua conta. Se não achar: ⚙ → **Nomes dos ativos na corretora**
  (ex.: `XAUUSD=XAUUSD.pro`).
- **Fuso do servidor medido** pela cotação mais recente, guardado por servidor em `data/mt5_fuso.json` e corrigido
  na troca de horário de verão. Corretoras no “fechamento de Nova York” (a Hantec: UTC+3 no verão americano, UTC+2
  no inverno — a pausa diária do ouro fica sempre às 00h do servidor) têm **cada candle do histórico convertido com o
  fuso da sua época**; sem isso os candles de inverno ficariam 1 hora deslocados. O card **MetaTrader 5** mostra o fuso,
  de onde ele veio e se a conta é **demo** ou **real**.
- **Zonas com os candles semanais e mensais da própria corretora.**
- **Boleta em lotes** pelo valor do tick de perda do ativo e pelo saldo da conta: compra no ASK, venda no BID, preços
  no tick, aviso de distância mínima de stop, de spread alto e de lote mínimo acima do seu risco.
- **Fontes nunca se misturam:** o ouro do Yahoo é o futuro `GC=F`, dezenas de dólares acima do XAUUSD. Ao ligar, o
  AURUM já começa a conectar e a primeira análise espera o MT5 (até 15 s) em vez de usar outra fonte; com o MT5 aberto
  mas sem responder, mantém os últimos candles da corretora. Tempo maior, diário, zonas e boleta só entram se vierem
  da mesma fonte do gráfico. Se o MT5 estiver fechado, a análise usa o Yahoo com um aviso, a boleta em lotes fica em
  espera e o sinal não entra na aba **Desempenho**.
- Cripto continua vindo da Binance (onde é operada).
- Conexão à prova de travamento: testada em outro processo e em segundo plano; com o MT5 fechado o AURUM confere a
  cada 10 s e conecta sozinho quando você abrir o MT5. Análises acima de 8 s ficam registradas no log com o tempo de
  cada etapa.

### Suas zonas desenhadas no MT5

1. No MT5: **Arquivo → Abrir pasta de dados → MQL5 → Indicators** e copie `mt5/AURUM_Zonas.mq5` para lá.
2. No **Navegador** do MT5: botão direito em **Indicadores → Atualizar**. Arraste **AURUM_Zonas** para **um**
   gráfico qualquer (ele lê todos os gráficos abertos).
3. Desenhe **linhas horizontais** ou **retângulos** nos seus suportes e resistências. O campo **Descrição** do objeto
   vira o nome da zona no AURUM (ex.: "Resistência semanal").

O indicador só grava os desenhos em `%APPDATA%\MetaQuotes\Terminal\Common\Files\AURUM\zonas.csv` — não envia ordens
nem altera nada. No AURUM elas aparecem como **Sua zona**, pesam mais que as automáticas e entram na estratégia.
Uma zona desenhada vale **a partir do momento em que o AURUM a vê**: o backtest não ganha vantagem de linhas traçadas
olhando o gráfico pronto.

## Estratégia padrão (XAU/USD no M15): rompimento de zona + pullback + novo rompimento

Para comprar (a venda é o espelho):

1. **Rompimento:** um candle fecha acima de uma **resistência principal** do mensal, semanal ou diário (ou de uma
   zona sua).
2. **Correção:** o preço devolve pelo menos 0,5 ATR e forma um **fundo confirmado** (2 candles de cada lado) **sem
   voltar para dentro da zona**. Fechar de volta dentro da zona cancela o padrão; ele expira em 1 dia.
3. **Novo rompimento:** entrada só quando um candle **fecha acima do topo anterior à correção** (linha GATILHO).
4. **Vela gigante:** candle do gatilho com mais de **1.500 pontos (US$ 15)** não vale — espera outra correção.
5. **Stop** abaixo do fundo da correção (até 3%); alvo 2× o risco; próxima resistência a pelo menos 1 risco.
6. **Notícias fortes do dólar:** entradas bloqueadas 30 min antes e depois (ajustável em ⚙).
7. **Sessão Londres/NY** (04h–14h de Brasília) ligada: sem ela o resultado caiu para o empate.
8. **PREPARE-SE** quando o fundo já se formou e o preço está a até 1 ATR do gatilho.

**Zonas automáticas:** topos e fundos confirmados do mensal (peso 3), semanal (2) e diário (1), agrupados em zonas de
até 1 ATR diário; zona principal = peso ≥ 2. Nada é conhecido antes de o candle maior fechar.

**Resultado medido (honesto)** — XAUUSD real da Hantec pelo MT5: 80 mil candles de M15 (28/04/2023 a 16/09/2026),
custo conservador de 0,03% por operação, sessão Londres/NY, stop estrutural e alvo 2R, sem o filtro de notícias (não
há calendário histórico). O backtest do AURUM encerra pelo fechamento a operação que não bateu stop nem alvo em 20
candles (5 h); a última linha mostra o método “puro”, segurando até o stop ou o alvo.

| Variante | Operações | Fator de lucro | R por operação | Queda máx. | Últimos 30% (desde 10/09/2025) |
|---|---|---|---|---|---|
| Rompimento + pullback, sessão Londres/NY | 98 | 1,14 | +0,06 | 10,1R | 23 op. · fator 1,28 |
| Só compras | 52 | 1,18 | +0,09 | 6,3R | 12 op. · fator 1,08 |
| Só vendas | 46 | 1,09 | +0,03 | 4,5R | 11 op. · fator 1,55 |
| Mesmo padrão 24h (sem filtro de sessão) | 233 | 1,06 | +0,03 | 16,6R | 58 op. · fator 1,23 |
| Custo próximo do spread real (~0,01%) | 98 | 1,32 | +0,13 | 7,5R | 23 op. · fator 1,42 |
| Segurando até stop ou alvo (sem saída em 5 h) | 98 | 1,03 | +0,02 | 17,3R | 23 op. · fator 1,15 |

Por período (terços): 2023–24 fator 0,82 · 2024–25 fator 1,65 · 2025–26 fator 1,19. São ~2,4 operações por mês.
**Zonas mais estreitas** (0,75; 0,5; 0,35 e 0,25 ATR diário) foram testadas e nenhuma foi melhor de forma consistente
— a largura continua em até 1 ATR diário. A vantagem medida é pequena e vem mais das compras num ouro em alta: confirme
em **conta demo** e na aba **Desempenho** antes de usar dinheiro real. (Pesquisa anterior, com PAXG da Binance como
substituto: 43 operações, fator 1,28.)

Outras estratégias em ⚙ → **Estratégia de entrada**: zonas (reação e rompimento direto), indicadores ou todas.

## Proteções automáticas

- **Limites do dia:** máximo de operações, perda máxima em R e perdas seguidas (padrão 3 · −2R · 2) → **PAUSA**.
- **Notícias de alto impacto** (Forex Factory): `ENTRAR AGORA` fica em espera perto do evento.
- **Checklist** obrigatório ao registrar uma entrada.
- **App local:** só atende em `localhost` e exige o cabeçalho `X-AURUM` em ações que alteram dados.

## Outras corretoras

- **XP (B3):** WDO e WIN com código do vencimento vigente, contratos pelo risco e ticks da B3; ações em lote padrão ou
  fracionário. Sem MT5 os preços de WDO/WIN são aproximados — a boleta tem o campo **“Preço agora no XP Unity”**.
- **Binance Spot (cripto):** Limite + OCO com tick, step e mínimo reais do par; capital em reais convertido para USDT;
  só compras (na Spot não se vende o que não tem); OCO 0,1% menor por causa da taxa descontada da moeda.
- **Começar pequeno:** plano de escalada (treino → R$ 50 → … → R$ 5.000, sobe só com resultado medido) e ranking das
  moedas mais negociadas da Binance pelo resultado fora da amostra depois das taxas.

## Fontes de dados

| Ativo | Fonte |
|---|---|
| Ouro, forex, índices, B3 (com MT5 ligado e aberto) | **MetaTrader 5** da sua corretora, tempo real |
| Cripto | Binance (API pública, tempo real) |
| Sem MT5 (ou MT5 fechado) | Yahoo Finance (`XAUUSD` → `GC=F`, futuro COMEX; B3 com ~15 min de atraso; WDO/WIN aproximados) |

## Alertas

Tela, som, notificação do navegador e **Telegram** (⚙ → Alertas no Telegram: token do @BotFather, `/start` no bot,
**Descobrir meu chat id**, teste e salve). O scanner analisa favoritos, o ativo aberto e as operações abertas logo
após o fechamento de cada candle.

**Com o PC desligado (GitHub Actions):** `.github/workflows/aurum-nuvem.yml` pode rodar a cada candle de 15 min
(07h–16h45 em dias úteis) e avisar no Telegram. **Vem desligado:** sem os secrets cada execução falhava e o GitHub
mandava um e-mail. Para ligar, crie os secrets e tire o `#` das linhas `schedule` do arquivo. Configure os secrets `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` no repositório e os
ativos em `cloud_config.json`. Na nuvem não há MT5: o ouro vem do `GC=F` (futuro), dezenas de dólares acima do
XAUUSD da corretora (US$ 36 em 16/09/2026) e com zonas próprias — confira gatilho e stop no seu MT5. Teste local: `python -m app.cloud --dry-run`.

## Estrutura

```
app/
  analysis.py      monta a análise completa (sinal, plano, zonas, previsão, backtest, gráfico)
  zones.py         zonas M/S/D, zonas desenhadas e o padrão rompimento + pullback (sem olhar o futuro)
  strategy.py      regras de zona, de pullback e de indicadores; filtros de contexto
  backtest.py      backtest com custos, validação fora da amostra e confiabilidade
  decision.py      decisão do sinal, proteções, stop móvel e plano
  mt5_source.py    MetaTrader 5: conexão segura, nomes com sufixo, fuso do servidor, especificações
  user_zones.py    leitura das zonas exportadas pelo indicador AURUM_Zonas
  market_data.py   MT5, Binance e Yahoo, com cache
  ticket.py        boletas XP (B3) e Binance · ticket_mt5.py: boleta em lotes para o MT5
  service.py       orquestração, alertas, radar · scanner.py: análise contínua
  pipeline.py      análise sem banco (nuvem e ranking) · cloud.py: varredura no GitHub Actions
  screener.py      ranking de moedas · scaling.py: plano de escalada
  news.py          agenda econômica · notifier.py: Telegram · database.py: SQLite
  indicators.py, levels.py, patterns.py, explain.py, assets.py, b3.py, config.py, main.py (API)
mt5/AURUM_Zonas.mq5  indicador que exporta suas zonas do MT5 (somente leitura)
static/              painel (HTML, CSS, JavaScript)
tests/               testes sem internet (dados sintéticos e MT5 simulado)
```

## Testes

```bash
python -m unittest discover -s tests -t .
```
