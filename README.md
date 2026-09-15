# AURUM — Analisador de Mercado

Painel web local que analisa forex, ouro, cripto, ações da B3 e dos EUA como um trader: lê tendência, força,
exagero, volatilidade e estrutura de preço, e diz **quando entrar, quando esperar e quando sair** — com janela de
entrada, aviso antecipado ("PREPARE-SE ~15:45"), stop loss obrigatório e números **calibrados no histórico**.

> **ISTO NÃO PREVÊ RESULTADOS. USE STOP LOSS. NÃO APOSTE O QUE NÃO PODE PERDER.**
> Não é recomendação de investimento. Nenhuma ferramenta acerta sempre: o AURUM mede onde há vantagem e mostra isso.

## Como abrir (pelo navegador)

O AURUM abre no navegador em **http://localhost:8765**. O motor de análise roda no seu computador, em segundo plano e
sem janela.

| Arquivo | Para que serve |
|---|---|
| `ATIVAR_INICIO_AUTOMATICO.bat` | **Rode uma vez.** O AURUM passa a ligar sozinho quando você entra no Windows — depois é só abrir o navegador. |
| `INICIAR_AURUM.bat` | Liga agora (instala as dependências na primeira vez) e abre o navegador. Se já estiver ligado, só abre. |
| `PARAR_AURUM.bat` | Desliga o servidor. |
| `DESATIVAR_INICIO_AUTOMATICO.bat` | Tira da inicialização do Windows. |

- No Chrome/Edge, use **Instalar AURUM** (ícone na barra de endereço) para ter um atalho que abre como app.
- Se o servidor estiver desligado, o painel mostra como ligar e reconecta sozinho quando ele voltar.
- Nunca sobem duas cópias: a segunda só abre o navegador. Log em `data/aurum.log`.
- Primeira vez usando? Aba **Tutorial** → **Fazer o tour guiado**.

Pelo terminal:

```bash
pip install -r requirements.txt
```

```bash
python run.py
```

## Estratégia padrão (XAU/USD no M15): rompimento de zona + pullback + novo rompimento

Pensada para o ouro no M15, de preferência com os preços do MT4 da corretora (ver abaixo). Para comprar (a venda é o
espelho):

1. **Rompimento:** um candle fecha acima de uma **resistência principal** do mensal, semanal ou diário.
2. **Correção:** o preço devolve pelo menos 0,5 ATR e forma um **fundo confirmado** (2 candles de cada lado) **sem
   voltar para dentro da zona**. Fechar de volta dentro da zona cancela o padrão; ele também expira em 1 dia.
3. **Novo rompimento:** entrada só quando um candle **fecha acima do topo anterior à correção** (linha GATILHO).
4. **Vela gigante:** se o candle do gatilho tiver mais de **1.500 pontos (US$ 15)**, não entra — espera outra
   correção. Ajustável em ⚙.
5. **Stop** abaixo do fundo da correção (até 3%); alvo 2× o risco; próxima resistência a pelo menos 1 risco.
6. **Notícias fortes** (alto impacto do dólar): entradas bloqueadas 30 min antes e depois (ajustável em ⚙).
7. **Sessão de Londres/NY** (04h–14h de Brasília) ligada: sem ela o resultado caiu para o empate.
8. **PREPARE-SE** quando o fundo já se formou e o preço está a até 1 ATR do gatilho.

**Resultado medido (honesto):** 2 anos de M15 do ouro (PAXG/USDT, token lastreado em ouro, 70 mil candles, custo
0,03%), sem o filtro de notícias (não há calendário histórico):

| Variante | Operações | Fator de lucro | R por operação | Últimos 30% |
|---|---|---|---|---|
| Rompimento + pullback, sessão Londres/NY | 43 | 1,28 | +0,11 | fator 0,75 |
| Só compras | 24 | 2,01 | +0,30 | fator 1,09 |
| Só vendas | 19 | 0,69 | −0,14 | fator 0,43 |
| Mesmo padrão 24h | 109 | 0,97 | −0,01 | fator 1,02 |

O ouro subiu de ~US$ 2.400 para ~US$ 4.300 nesse período, o que explica compras boas e vendas ruins. São poucas
operações (~2 por mês) e o período mais recente perdeu: trate como hipótese a confirmar na aba **Desempenho** e em
conta demo. O bloqueio de regras ruins desliga sozinho um lado que perder no histórico do ativo.

Outras estratégias em ⚙ → **Estratégia de entrada**: zonas (reação e rompimento direto), indicadores ou todas.

## Zonas do mensal, semanal e diário (modo "zonas")

1. Do histórico diário saem os candles semanais e mensais. Topos e fundos confirmados (sem olhar o futuro) viram
   **zonas** com peso por tempo gráfico: mensal 3, semanal 2, diário 1. Zona principal = peso ≥ 2, largura máxima de
   1 ATR diário. As zonas mais próximas aparecem no gráfico (`S Mensal+Semanal`) e no plano.
2. No **M15** (ou no tempo gráfico aberto) quatro entradas:
   - **Compra no suporte / venda na resistência:** o candle toca a zona e fecha do outro lado dela, a favor da reação.
   - **Rompimento:** candle de força fecha além da zona; ela passa a proteger a entrada.
3. **Entrada sempre coberta:** preço a no máximo 1 ATR da zona, **stop atrás da zona** (dentro do limite de 3%) e a
   zona seguinte a pelo menos 1 risco de distância. Zona longe demais = sem entrada.
4. **PREPARE-SE** quando o preço chega a 1 ATR de uma zona principal, antes do candle de reação.

Em ⚙ → **Estratégia de entrada** dá para trocar para as regras antigas de indicadores ou usar as duas.

**Resultado medido (honesto):** 10 ativos (forex, ouro, BTC/ETH/SOL, WDO, WIN), custos descontados, período recente
fora da amostra. No M15 as zonas perderam menos que os indicadores no histórico (−0,09R contra −0,25R por operação),
mas **nenhuma das duas teve resultado positivo depois dos custos**; no 1h ficaram perto do empate. Exigir a tendência
do tempo maior a favor piorou. Por isso a **Confiabilidade** e o bloqueio de regras ruins continuam valendo por ativo:
só leve para o dinheiro real o que a aba **Desempenho** mostrar funcionando.

## MetaTrader 4 (Hantec Markets ou outra corretora)

O MT4 não tem API para Python. O AURUM lê as velas que o robô **`mt4/AURUM_Exporter.mq4`** grava em arquivo —
**somente leitura, o robô não envia ordens**.

1. No MT4: **Arquivo → Abrir pasta de dados → MQL4 → Experts** e copie `AURUM_Exporter.mq4` para lá.
2. Reinicie o MT4 (ou clique com o botão direito em **Expert Advisors → Atualizar**). Abra o robô no MetaEditor e
   aperte **Compilar** se ele não aparecer.
3. Arraste **AURUM_Exporter** para qualquer gráfico. Em **Entradas**, escreva os ativos como aparecem na sua
   corretora (ex.: `EURUSD,GBPUSD,XAUUSD`). Não precisa marcar "Permitir negociação automática".
4. No AURUM: ⚙ → **MetaTrader 4** → marque **Usar os gráficos do MT4** (e o sufixo, se os ativos tiverem, ex.: `.r`).
   O status mostra **CONECTADO** e a fonte dos dados passa a ser a sua corretora.

Arquivos em `%APPDATA%\MetaQuotes\Terminal\Common\Files\AURUM\`. Com o MT4 fechado há mais de 3 minutos, o AURUM volta
para Yahoo/Binance sozinho. A hora do servidor da corretora é convertida para UTC.

> **Atenção:** a Hantec Markets informa no próprio site que **não é autorizada pela CVM** a oferecer serviços a
> residentes no Brasil. Forex/CFD com alavancagem pode perder mais que o depositado. Confira a regulação antes de
> depositar e prefira começar em conta demo.

## Proteções automáticas

- **Limites do dia** (⚙ → Gestão de risco): máximo de operações, perda máxima em R e perdas seguidas
  (padrão 3 · −2R · 2). Ao atingir um limite o sinal vira **PAUSA** até o dia seguinte. A faixa **Hoje** mostra o uso.
- **Notícias de alto impacto**: agenda econômica da semana (feed público do Forex Factory; sem eventos do Brasil).
  De 30 min antes a 30 min depois de um evento de alto impacto da moeda do ativo, `ENTRAR AGORA` fica em espera.
- **Checklist** obrigatório ao registrar uma entrada (stop colocado, tamanho respeitado, avisos lidos).
- **Tour guiado** pela tela na aba Tutorial.
- **Segurança do app local:** só atende em `localhost` e exige o cabeçalho `X-AURUM` em ações que alteram dados,
  para que nenhum site aberto no navegador consiga mandar comandos ao AURUM.

## Operando pela XP Investimentos

A XP negocia ativos da B3. Para dólar use o **mini dólar (WDO)**; para índice, o **mini índice (WIN)** — digite `WDO` ou `WIN`.

- O card **Ordem para a XP** monta a boleta: código do vencimento vigente (ex.: `WDOV26`), lado, quantidade de
  contratos pelo seu risco, stop (disparo e limite) e alvos arredondados ao tick, com botão **Copiar ordem**.
  Para ações: lote padrão (múltiplos de 100) ou fracionário (`PETR4F`).
- **O AURUM não envia ordens.** Você confere e envia no **XP Unity** (home broker gratuito da XP).
- **Preço exato:** sem uma fonte em tempo real, WDO usa dólar à vista × 1000, WIN usa o Ibovespa e ações da B3 têm
  ~15 min de atraso. Digite o preço que aparece no XP Unity no campo **“Preço agora no XP Unity”** da boleta: entrada,
  stop e alvos se ajustam mantendo as distâncias do plano.
- **MetaTrader 5 (opcional, desligado):** quem quiser cotações em tempo real pode ligar em ⚙. A conexão é testada em
  outro processo, só quando o MT5 está aberto, e nunca trava o painel.

| Contrato | Tick | Valor do ponto |
|---|---|---|
| WDO (mini dólar) | 0,5 ponto | R$ 10 por contrato |
| WIN (mini índice) | 5 pontos | R$ 0,20 por contrato |

## Operando pela Binance (cripto)

Para `BTC`, `ETH`, `SOL`… o card vira **Ordem para a Binance**, com as regras reais do par lidas da Binance:

- **Ordem 1 · Entrada:** Spot, tipo Limite, preço arredondado ao tick do par.
- **Ordem 2 · Proteção OCO:** Take Profit + Stop Loss (gatilho e limite), enviada logo depois que a entrada executar.
- **Quantidade** no passo (step) do par, limitada pelo seu risco **e** pelo dinheiro disponível; o capital em reais
  de ⚙ é convertido para USDT pelo dólar do dia. Avisa quando fica abaixo do valor mínimo de ordem da Binance.
- **Sinal de venda:** na Spot só se vende a moeda que já está na carteira. O AURUM não monta ordem em Futuros nem
  alavancagem.
- **O AURUM não envia ordens nem pede chave de API.** Você confere e envia no app ou site da Binance.

## Começar pequeno (a partir de R$ 50)

Aba **Começar pequeno**:

- **Plano de escalada:** treino sem dinheiro → R$ 50 → 100 → 200 → 500 → 1.000 → 2.000 → 5.000. Sobe só com
  20 operações conferidas, resultado ≥ +2R, fator de lucro ≥ 1,1 e queda máxima < 6R. Volta um nível com −4R,
  5 perdas seguidas ou queda de 6R. Risco por operação: 3% até R$ 200, 2% até R$ 1.000, 1% acima — com pouco
  dinheiro 1% daria ordens abaixo do mínimo da Binance (US$ 5). Você decide quando mudar; o capital de ⚙ acompanha.
- **Ranking de moedas:** os ~20 pares USDT mais negociados da Binance (sem stablecoins e tokens alavancados) passam
  pelo motor com **só regras de compra** (Spot), nos gráficos de 15 min, 1h e diário, e recebem nota pelo resultado
  fora da amostra depois das taxas, pelo mínimo de ordem que cabe no seu capital e pelo spread. Atualiza a cada
  6 horas ou quando você muda a estratégia ou o capital.
- **Cripto só na compra (Spot)** ligado por padrão: sem sinais de venda em cripto.
- A proteção **OCO** usa quantidade 0,1% menor que a compra: a Binance desconta a taxa da moeda recebida.

## Fontes de dados

| Ativo | Fonte | Observação |
|---|---|---|
| Opcional: forex/ouro do MT4 (ex.: Hantec) | robô `AURUM_Exporter` no MetaTrader 4 | tempo real da sua corretora quando ligado em ⚙ |
| Opcional: ativos do MT5 aberto | MetaTrader 5 (desligado por padrão) | tempo real quando ligado em ⚙ |
| WDO / WIN sem MT5 | Yahoo (dólar à vista × 1000 / Ibovespa) | referência aproximada, só no horário do pregão |
| Cripto (`BTC`, `ETH`, `SOL`…) | Binance (API pública) | tempo real, atualização incremental, até 8.000 candles |
| Forex, ouro (`XAUUSD` → `GC=F`), EUA | Yahoo Finance | forex sem volume |
| Ações da B3 | Yahoo Finance | atraso de ~15 min |

Históricos baixados no limite de cada fonte (5m/15m: 60 dias · 1h: 2 anos · diário: 5 anos) para as estatísticas serem confiáveis.

## O que tem no painel

| Área | O que mostra |
|---|---|
| **Sinal agora** | Estado do sinal, "É o momento certo? SIM/NÃO", janela de entrada, contagem regressiva, **chance histórica** (com o ponto de empate), **confiabilidade**, tempo maior, sessão forex e avisos de risco |
| **Plano da operação** | Entrada, stop (ATR, máx. 3%), alvos 1 e 2, quantidade pelo risco, **pips e lotes** no forex, suportes e resistências próximos |
| **Gráfico** | Candles, MME9/21, Bollinger, VWAP, volume, RSI, MACD, suporte/resistência, sinais históricos e linhas do plano |
| **Previsão** | Direção dos indicadores + **probabilidade calibrada** (quantas vezes subiu 3 candles depois nessa faixa de força), divergências, padrões de candle, tempo maior, VWAP e horário estimado dos próximos cruzamentos |
| **Regras de entrada** | 6 regras com checklist ✓/✕, status (DISPAROU, FORMANDO, QUASE, EM CURSO, **BLOQUEADA**) e resultado histórico |
| **Backtest** | Acerto, fator de lucro e R com **custos descontados**, e validação **fora da amostra** (período recente) |
| **Radar** | Favoritos lado a lado com sinal e confiabilidade; botão para adicionar os principais pares forex |
| **Histórico** | Tudo que o scanner analisou, alertas, CSV e relatório em texto |
| **Operações** | Registre "Já entrei": stop, alvo, **stop móvel** e sinais de saída acompanhados |
| **Desempenho** | Cada `ENTRAR AGORA` emitido é gravado e conferido nos candles seguintes: acerto e resultado **reais** |
| **Regras** | Como o motor decide e o que foi validado |
| **Tutorial** | Passo a passo com progresso salvo, rotina diária e perguntas frequentes |

Atalhos: `/` busca · `1`–`5` tempo gráfico · `S` alterna Simples/Pro.

## Regras e calibração

**Entrada (compra; a venda é o espelho):**

- **Compra forte:** MME9 > MME21 + MACD acima do sinal + RSI 30–70 + preço acima da MME50 + ADX ≥ 20 + tempo maior em alta.
- **Compra de recuperação:** RSI < 30 + tocou a banda inferior + MACD estabilizando + candle de reação.
- **Compra no momento:** MACD e médias cruzaram para cima há poucos candles + volume forte + RSI > 50 + tempo maior não contra.
- **Forex e ouro intraday:** todas as regras só na sessão de Londres/Nova York (04h–14h de Brasília).

O sinal só vira `ENTRAR AGORA` quando confirma **no fechamento do candle**, a força concorda e a regra **não foi
bloqueada** por ter perdido dinheiro no histórico daquele ativo e tempo gráfico.

**O que foi validado** (11 pares de forex + ouro, até 2 anos, spread descontado, período recente fora da amostra):

| Filtro | Efeito medido | Situação |
|---|---|---|
| Tempo maior a favor | melhorou o fator de lucro em todos os tempos gráficos | ligado |
| Sessão Londres/NY | melhorou 5m e 1h | ligado |
| Stop 2× ATR no intraday | melhorou; no diário 1,5× foi melhor | automático |
| Bloqueio de regras ruins | melhorou o resultado fora da amostra | ligado |
| Suporte/resistência como filtro | piorou | só leitura |
| Exigir divergência | piorou | só leitura |

Resultado honesto: em forex, **1h com os filtros** foi o único tempo gráfico positivo fora da amostra; 5m e 15m
continuaram negativos depois dos custos. Por isso o app mostra a **Confiabilidade** de cada ativo + tempo gráfico.

**Saída (com operação registrada):** stop loss · alvo · stop móvel (2,5 ATR do melhor preço) · preço esticado com
lucro · tendência inverteu. Aviso para mover o stop para a entrada quando o lucro passa de 1× o risco.

## Análise contínua e alertas

O scanner analisa favoritos, o ativo aberto e as operações abertas **logo após o fechamento de cada candle** (e no
mínimo a cada N minutos), grava tudo em `data/aurum.db` e confere o resultado dos sinais emitidos. Alertas: tela,
som, notificação do navegador e **Telegram**.

### Telegram

1. No Telegram, abra **@BotFather**, envie `/newbot` e copie o token.
2. No AURUM, clique em ⚙ e cole o token em “Alertas no Telegram”.
3. Abra o seu bot no Telegram e envie `/start`.
4. Clique em **Descobrir meu chat id** (preenche sozinho) e depois em **Enviar mensagem de teste**.
5. Marque **Enviar alertas no Telegram** e clique em **Salvar**.

### Alertas com o PC desligado (GitHub Actions)

Uma cópia do motor roda a cada candle de 15 min nos servidores do GitHub (grátis) e manda no Telegram só quando um sinal
**ENTRAR AGORA** ou **SAIR AGORA** aparece. Todo dia, depois das 8h, chega um resumo "AURUM nuvem ativo" para você
saber que está funcionando. Os ativos, o tempo gráfico e o capital ficam em `cloud_config.json`.

1. Crie uma conta em <https://github.com> (se ainda não tiver) e um repositório **privado** vazio, ex.: `aurum`.
2. Na pasta do AURUM, envie o código (troque `SEU_USUARIO`):
   ```bash
   git remote add origin https://github.com/SEU_USUARIO/aurum.git
   git push -u origin main
   ```
   A pasta `data/` (com o seu token do Telegram) **não é enviada** — está no `.gitignore`.
3. No repositório: **Settings → Secrets and variables → Actions → New repository secret**. Crie
   `TELEGRAM_TOKEN` (token do @BotFather) e `TELEGRAM_CHAT_ID` (o mesmo chat id que aparece em ⚙ no AURUM).
4. Aba **Actions → AURUM nuvem → Run workflow** para testar na hora. Em ~2 minutos chega o resumo no Telegram.

Agenda (horário de Brasília): segunda a sexta das 07h às 16h45 a cada candle de 15 min, no resto do dia de hora em
hora; sábado e domingo a cada 2 horas. São ~1.300 execuções por mês: sem limite em repositório público; em privado
(2.000 min/mês) pode estourar a cota, e aí o GitHub pausa até o mês seguinte. O GitHub pode atrasar execuções
agendadas em alguns minutos nos horários de pico — no M15 isso pode comer parte da janela de entrada.
Para testar localmente sem enviar nada: `python -m app.cloud --dry-run`.

Na nuvem não há MT4: o ouro vem do Yahoo (`GC=F`, futuro COMEX), que fica alguns dólares acima do XAU/USD à vista
da corretora. Use o alerta como aviso de que o padrão aconteceu e confira gatilho e stop no seu MT4.

## Estrutura

```
app/
  config.py        tempos gráficos, históricos, custos e padrões validados
  assets.py        ativo digitado -> símbolo; horário de mercado e sessões forex
  market_data.py   MetaTrader 5, Binance e Yahoo Finance, com cache
  mt5_source.py    leitura em tempo real do MetaTrader 5 aberto (só leitura)
  b3.py            WDO/WIN: vencimento vigente, tick e valor do ponto
  ticket.py        boleta pronta para a XP (B3) e para a Binance Spot (Limite + OCO)
  news.py          agenda econômica e bloqueio perto de notícias de alto impacto
  indicators.py    RSI (Wilder), MACD, MMEs, Bollinger, ATR, ADX, volume, VWAP
  levels.py        pivôs, suporte/resistência, divergências e padrões de candle (sem olhar o futuro)
  zones.py         zonas de suporte/resistência do mensal, semanal e diário (sem olhar o futuro)
  strategy.py      regras de zona e de indicadores, filtros de contexto, força e estimativas de tempo
  mt4_source.py    leitura das velas exportadas pelo robô do MetaTrader 4
  screener.py      ranking de moedas da Binance para capital pequeno
  scaling.py       plano de escalada (R$ 50 → R$ 5.000)
  pipeline.py      análise sem banco (nuvem e ranking)
  backtest.py      backtest com custos, validação fora da amostra, calibração e confiabilidade
  decision.py      decisão do sinal, confiança, avisos, stop móvel e plano (pips/lotes)
  explain.py       textos em português e previsão
  analysis.py      monta a análise completa
  service.py       orquestração, alertas, radar e conferência dos sinais emitidos
  scanner.py       análise contínua alinhada ao fechamento dos candles
  database.py      SQLite (análises, sinais, favoritos, operações, alertas, configurações)
  notifier.py      Telegram
  cloud.py         varredura sem interface para a nuvem (GitHub Actions)
  main.py          API FastAPI + arquivos do painel
static/            painel (HTML, CSS, JavaScript)
mt4/               robô exportador para o MetaTrader 4 (somente leitura)
.github/workflows/ agenda da varredura na nuvem
cloud_config.json  ativos e tempo gráfico monitorados na nuvem
tests/             testes sem internet (dados sintéticos)
CLAUDE_DESKTOP_PROMPT/   especificação e código original que deram origem ao projeto
```

## Testes

```bash
python -m unittest discover -s tests -t .
```
