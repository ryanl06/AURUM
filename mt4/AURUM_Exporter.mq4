//+------------------------------------------------------------------+
//| AURUM_Exporter.mq4                                                |
//| Exporta as velas do MetaTrader 4 para o AURUM ler.               |
//|                                                                  |
//| SOMENTE LEITURA: este robô não abre, altera nem fecha ordens.    |
//| Ele grava arquivos CSV na pasta comum do MetaTrader:             |
//|   %APPDATA%\MetaQuotes\Terminal\Common\Files\AURUM\              |
//+------------------------------------------------------------------+
#property copyright "AURUM"
#property version   "1.00"
#property strict
#property description "Exporta velas (M1, M5, M15, H1, D1, W1, MN1) para o AURUM. Não envia ordens."

input string Ativos        = "EURUSD,GBPUSD,USDJPY,AUDUSD,XAUUSD"; // Ativos separados por vírgula (vazio = só o do gráfico)
input int    Intervalo_Seg = 5;                                     // A cada quantos segundos atualizar

const int    TF_COUNT = 7;
int          TF_PERIOD[7] = {PERIOD_M1, PERIOD_M5, PERIOD_M15, PERIOD_H1, PERIOD_D1, PERIOD_W1, PERIOD_MN1};
string       TF_NAME[7]   = {"M1", "M5", "M15", "H1", "D1", "W1", "MN1"};
int          TF_BARS[7]   = {600, 3000, 6000, 8000, 1500, 520, 240};
int          TF_EVERY[7]  = {1, 1, 1, 12, 12, 60, 60};  // em ciclos do timer: gráficos maiores mudam menos

string   g_symbols[];
int      g_cycle = 0;
int      g_offset = 0;       // fuso do servidor em segundos (servidor - GMT)
bool     g_offset_ok = false;

int OnInit()
{
   string list = StringLen(Ativos) > 0 ? Ativos : Symbol();
   int n = StringSplit(list, ',', g_symbols);
   for(int i = 0; i < n; i++)
   {
      g_symbols[i] = StringTrimRight(StringTrimLeft(g_symbols[i]));
      if(StringLen(g_symbols[i]) > 0)
         SymbolSelect(g_symbols[i], true);  // precisa estar na Observação do Mercado para receber cotações
   }
   FolderCreate("AURUM", FILE_COMMON);
   EventSetTimer(MathMax(1, Intervalo_Seg));
   ExportAll(true);
   Print("AURUM_Exporter ativo: ", n, " ativo(s). Somente leitura, nenhuma ordem é enviada.");
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   ExportAll(false);
}

void UpdateOffset()
{
   // Com o mercado aberto TimeCurrent() é a hora do servidor; arredonda para meia hora.
   long diff = (long)(TimeCurrent() - TimeGMT());
   long rounded = (long)MathRound(diff / 1800.0) * 1800;
   if(MathAbs((double)(diff - rounded)) <= 300 && MathAbs((double)rounded) <= 14 * 3600)
   {
      g_offset = (int)rounded;
      g_offset_ok = true;
   }
}

void ExportAll(bool force)
{
   UpdateOffset();
   g_cycle++;
   for(int i = 0; i < ArraySize(g_symbols); i++)
   {
      if(StringLen(g_symbols[i]) == 0)
         continue;
      for(int t = 0; t < TF_COUNT; t++)
      {
         if(force || g_cycle % TF_EVERY[t] == 0)
            ExportOne(g_symbols[i], t);
      }
   }
}

void ExportOne(string sym, int t)
{
   int period = TF_PERIOD[t];
   int total = iBars(sym, period);
   if(total <= 0)
      return;  // histórico ainda carregando: tenta no próximo ciclo
   int count = MathMin(total, TF_BARS[t]);
   int digits = (int)MarketInfo(sym, MODE_DIGITS);

   string final_name = "AURUM\\" + sym + "_" + TF_NAME[t] + ".csv";
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
