//+------------------------------------------------------------------+
//| AURUM_Exporter.mq4                                                |
//| Exporta as velas do MetaTrader 4 para o AURUM ler.               |
//|                                                                  |
//| SOMENTE LEITURA: este robô não abre, altera nem fecha ordens.    |
//| Ele grava arquivos CSV na pasta comum do MetaTrader:             |
//|   %APPDATA%\MetaQuotes\Terminal\Common\Files\AURUM\              |
//|   <ATIVO>_<TEMPO>.csv       histórico (regravado a cada candle)  |
//|   <ATIVO>_<TEMPO>_live.csv  últimos 3 candles (a cada ciclo)     |
//+------------------------------------------------------------------+
#property copyright "AURUM"
#property version   "1.10"
#property strict
#property description "Exporta velas (M1, M5, M15, H1, D1, W1, MN1) para o AURUM. Não envia ordens."

input string Ativos        = "XAUUSD,EURUSD,GBPUSD,USDJPY"; // Ativos separados por vírgula (vazio = só o do gráfico)
input int    Intervalo_Seg = 5;                              // A cada quantos segundos atualizar o candle ao vivo

#define TF_COUNT 7
int    TF_PERIOD[TF_COUNT] = {PERIOD_M1, PERIOD_M5, PERIOD_M15, PERIOD_H1, PERIOD_D1, PERIOD_W1, PERIOD_MN1};
string TF_NAME[TF_COUNT]   = {"M1", "M5", "M15", "H1", "D1", "W1", "MN1"};
int    TF_BARS[TF_COUNT]   = {1500, 6000, 30000, 20000, 2000, 520, 240};  // M15: ~1 ano para o backtest

string   g_symbols[];
datetime g_last_bar[];  // horário do último candle gravado no histórico, por ativo e tempo gráfico
int      g_cycle = 0;
int      g_offset = 0;     // fuso do servidor em segundos (servidor - GMT)
bool     g_offset_ok = false;

int OnInit()
{
   string list = StringLen(Ativos) > 0 ? Ativos : Symbol();
   int n = StringSplit(list, ',', g_symbols);
   ArrayResize(g_last_bar, n * TF_COUNT);
   ArrayInitialize(g_last_bar, 0);
   for(int i = 0; i < n; i++)
   {
      g_symbols[i] = StringTrimRight(StringTrimLeft(g_symbols[i]));
      if(StringLen(g_symbols[i]) > 0)
         SymbolSelect(g_symbols[i], true);  // precisa estar na Observação do Mercado para receber cotações
   }
   FolderCreate("AURUM", FILE_COMMON);
   EventSetTimer(MathMax(1, Intervalo_Seg));
   ExportAll();
   Print("AURUM_Exporter ativo: ", n, " ativo(s). Somente leitura, nenhuma ordem é enviada.");
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   ExportAll();
}

void UpdateOffset()
{
   // Com o mercado aberto TimeCurrent() é a hora do servidor; arredonda para meia hora.
   int diff = (int)(TimeCurrent() - TimeGMT());
   int rounded = (int)MathRound(diff / 1800.0) * 1800;
   if(MathAbs(diff - rounded) <= 300 && MathAbs(rounded) <= 14 * 3600)
   {
      g_offset = rounded;
      g_offset_ok = true;
   }
}

void ExportAll()
{
   UpdateOffset();
   g_cycle++;
   for(int i = 0; i < ArraySize(g_symbols); i++)
   {
      if(StringLen(g_symbols[i]) == 0)
         continue;
      for(int t = 0; t < TF_COUNT; t++)
      {
         int total = iBars(g_symbols[i], TF_PERIOD[t]);
         if(total <= 0)
            continue;  // histórico ainda carregando: tenta no próximo ciclo
         datetime bar0 = iTime(g_symbols[i], TF_PERIOD[t], 0);
         int slot = i * TF_COUNT + t;
         // Histórico completo só quando nasce um candle novo (ou a cada ~5 min, para pegar correções de dados).
         if(bar0 != g_last_bar[slot] || g_cycle % 60 == 1)
         {
            WriteBars(g_symbols[i], t, MathMin(total, TF_BARS[t]), "");
            g_last_bar[slot] = bar0;
         }
         WriteBars(g_symbols[i], t, MathMin(total, 3), "_live");
      }
   }
}

void WriteBars(string sym, int t, int count, string suffix)
{
   int period = TF_PERIOD[t];
   int digits = (int)MarketInfo(sym, MODE_DIGITS);
   string final_name = "AURUM\\" + sym + "_" + TF_NAME[t] + suffix + ".csv";
   string temp_name = final_name + ".tmp";
   int h = FileOpen(temp_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;

   FileWriteString(h, StringFormat("#AURUM,%s,%s,%d,%d,%d,%s\r\n", sym, TF_NAME[t], g_offset, g_offset_ok ? 1 : 0,
                                   digits, AccountCompany()));
   for(int shift = count - 1; shift >= 0; shift--)
   {
      FileWriteString(h, StringFormat("%d,%s,%s,%s,%s,%d\r\n",
                                      (int)iTime(sym, period, shift),
                                      DoubleToString(iOpen(sym, period, shift), digits),
                                      DoubleToString(iHigh(sym, period, shift), digits),
                                      DoubleToString(iLow(sym, period, shift), digits),
                                      DoubleToString(iClose(sym, period, shift), digits),
                                      (int)iVolume(sym, period, shift)));
   }
   FileClose(h);
   FileMove(temp_name, FILE_COMMON, final_name, FILE_COMMON | FILE_REWRITE);  // troca atômica: o AURUM nunca lê arquivo pela metade
}
//+------------------------------------------------------------------+
