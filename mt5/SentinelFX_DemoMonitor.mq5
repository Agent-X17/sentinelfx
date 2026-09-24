#property strict
#property version "1.00"
#property description "Demo-only local diagnostics. No orders, network calls or passwords."

string output="SentinelFX\\demo-status.json";
string Bool(bool value) { return value ? "true" : "false"; }
string Quote(string value) {
   StringReplace(value,"\\","\\\\");
   StringReplace(value,"\"","\\\"");
   StringReplace(value,"\r"," ");
   StringReplace(value,"\n"," ");
   return "\""+value+"\"";
}
void Publish() {
   bool demo=AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO;
   bool connected=(bool)TerminalInfoInteger(TERMINAL_CONNECTED);
   string data="{\"schema\":1,\"generated_at\":"+IntegerToString((long)TimeGMT())+
      ",\"demo\":"+Bool(demo)+",\"connected\":"+Bool(connected);
   if(demo && connected) {
      MqlTick tick;
      bool tick_ok=SymbolInfoTick(_Symbol,tick);
      data+=",\"currency\":"+Quote(AccountInfoString(ACCOUNT_CURRENCY))+
         ",\"balance\":"+DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)+
         ",\"equity\":"+DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2)+
         ",\"free_margin\":"+DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2)+
         ",\"positions\":"+IntegerToString(PositionsTotal())+
         ",\"orders\":"+IntegerToString(OrdersTotal())+
         ",\"symbol\":"+Quote(_Symbol)+",\"tick_available\":"+Bool(tick_ok);
      if(tick_ok) data+=",\"bid\":"+DoubleToString(tick.bid,10)+
         ",\"ask\":"+DoubleToString(tick.ask,10)+
         ",\"tick_server_time\":"+IntegerToString((long)tick.time);
   }
   data+="}";
   int file=FileOpen(output+".tmp",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON,0,CP_UTF8);
   if(file==INVALID_HANDLE) { Print("SentinelFX: cannot write diagnostics: ",GetLastError()); return; }
   FileWriteString(file,data);
   FileClose(file);
   if(!FileMove(output+".tmp",FILE_COMMON,output,FILE_COMMON|FILE_REWRITE))
      Print("SentinelFX: cannot replace diagnostics: ",GetLastError());
}
int OnInit() {
   Publish();
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO) {
      Print("SentinelFX requires a DEMO account.");
      return INIT_FAILED;
   }
   if(!EventSetTimer(2)) return INIT_FAILED;
   Print("SentinelFX demo monitor running. Local diagnostics only.");
   return INIT_SUCCEEDED;
}
void OnTimer() { Publish(); }
void OnDeinit(const int reason) { EventKillTimer(); FileDelete(output,FILE_COMMON); }
