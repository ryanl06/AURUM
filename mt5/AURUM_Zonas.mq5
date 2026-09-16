//+------------------------------------------------------------------+
//| AURUM_Zonas.mq5                                                   |
//| Envia para o AURUM as zonas que você desenha no MetaTrader 5.    |
//|                                                                  |
//| SOMENTE LEITURA: este indicador não abre, altera nem fecha ordens|
//| e não mexe nos seus desenhos. A cada poucos segundos ele grava   |
//| as LINHAS HORIZONTAIS e os RETÂNGULOS de TODOS os gráficos       |
//| abertos em:                                                      |
//|   %APPDATA%\MetaQuotes\Terminal\Common\Files\AURUM\zonas.csv     |
//|                                                                  |
//| Coloque-o em UM gráfico só (qualquer ativo). Escreva no campo    |
//| "Descrição" do objeto um nome para a zona, se quiser (ex.:       |
//| "Resistência semanal"): ele aparece no AURUM.                    |
//+------------------------------------------------------------------+
#property copyright   "AURUM"
#property version     "1.00"
#property description "Exporta linhas horizontais e retângulos (suas zonas) para o AURUM. Não envia ordens."
#property indicator_chart_window
#property indicator_plots 0

input int Intervalo_Seg = 5; // A cada quantos segundos atualizar

int OnInit()
{
   EventSetTimer(MathMax(2, Intervalo_Seg));
   Export();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

int OnCalculate(const int rates_total, const int prev_calculated, const int begin, const double &price[])
{
   return(rates_total);
}

void OnTimer()
{
   Export();
}

string Clean(string text)
{
   StringReplace(text, ",", " ");
   StringReplace(text, "\r", " ");
   StringReplace(text, "\n", " ");
   return text;
}

void Export()
{
   FolderCreate("AURUM", FILE_COMMON);
   string temp_name = "AURUM\\zonas.csv.tmp";
   int h = FileOpen(temp_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   FileWriteString(h, StringFormat("#AURUM-ZONAS,%s,%s\r\n", Clean(AccountInfoString(ACCOUNT_COMPANY)),
                                   Clean(AccountInfoString(ACCOUNT_SERVER))));
   long chart = ChartFirst();
   while(chart >= 0)
   {
      string sym = ChartSymbol(chart);
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      int total = ObjectsTotal(chart, -1, -1);
      for(int i = 0; i < total; i++)
      {
         string name = ObjectName(chart, i, -1, -1);
         ENUM_OBJECT type = (ENUM_OBJECT)ObjectGetInteger(chart, name, OBJPROP_TYPE);
         double p1 = 0.0, p2 = 0.0;
         string kind = "";
         if(type == OBJ_HLINE)
         {
            p1 = ObjectGetDouble(chart, name, OBJPROP_PRICE, 0);
            p2 = p1;
            kind = "linha";
         }
         else if(type == OBJ_RECTANGLE)
         {
            p1 = ObjectGetDouble(chart, name, OBJPROP_PRICE, 0);
            p2 = ObjectGetDouble(chart, name, OBJPROP_PRICE, 1);
            kind = "retangulo";
         }
         else
            continue;
         FileWriteString(h, StringFormat("%s,%s,%s,%s,%s,%s\r\n", sym, kind, Clean(name), DoubleToString(p1, digits),
                                         DoubleToString(p2, digits), Clean(ObjectGetString(chart, name, OBJPROP_TEXT))));
      }
      chart = ChartNext(chart);
   }
   FileClose(h);
   FileMove(temp_name, FILE_COMMON, "AURUM\\zonas.csv", FILE_COMMON | FILE_REWRITE); // troca atômica
}
//+------------------------------------------------------------------+
