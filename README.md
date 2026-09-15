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

## Fontes de dados

| Ativo | Fonte | Observação |
|---|---|---|
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

Uma cópia do motor roda de hora em hora nos servidores do GitHub (grátis) e manda no Telegram só quando um sinal
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

Agenda (horário de Brasília): segunda a sexta de hora em hora; sábado e domingo a cada 2 horas. São ~650 execuções
por mês, dentro dos 2.000 minutos grátis de um repositório privado. O GitHub pode atrasar execuções agendadas em
alguns minutos nos horários de pico. Para testar localmente sem enviar nada: `python -m app.cloud --dry-run`.

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
  strategy.py      regras, filtros de contexto (tempo maior, sessão), força e estimativas de tempo
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
.github/workflows/ agenda da varredura na nuvem
cloud_config.json  ativos e tempo gráfico monitorados na nuvem
tests/             testes sem internet (dados sintéticos)
CLAUDE_DESKTOP_PROMPT/   especificação e código original que deram origem ao projeto
```

## Testes

```bash
python -m unittest discover -s tests -t .
```
