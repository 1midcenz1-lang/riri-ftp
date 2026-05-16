#property strict
#property version   "1.10"
#property description "MT5 Expert: sequential M1 backtest with M30 important level + M5 3-candle gap entry"

input datetime InpStartDate = D'2024.01.01 00:00';
input datetime InpEndDate   = D'2024.12.31 23:59';
input int      InpLevelGapPoints = 250; // M30 important level threshold
input int      InpEntryGapPoints = 120; // M5 3-candle gap threshold
input int      InpTouchLookaheadM1 = 120; // max M1 candles to wait for touch after setup
input bool     InpShowDailyLabels = true;
input color    InpBullColor = clrLime;
input color    InpBearColor = clrTomato;

struct TFBar
{
   datetime t;
   double o,h,l,c;
};

struct TradeStat
{
   datetime entry_time, exit_time;
   double entry, stop, target;
   bool is_buy, win;
};

struct DayStat { int wins, losses, targets, stops; };

MqlRates g_m1[];
TFBar g_m5[];
TFBar g_m30[];
TradeStat g_trades[];
string g_prefix;
int g_wins=0,g_losses=0,g_targets=0,g_stops=0;

bool IsBull(const TFBar &b){ return b.c>b.o; }
bool IsBear(const TFBar &b){ return b.c<b.o; }

double ToPts(double x){ return x/_Point; }

bool BuildTF(const MqlRates &src[], int tfMin, TFBar &out[])
{
   ArrayResize(out,0);
   if(ArraySize(src)<tfMin) return false;

   datetime curBucket=0;
   TFBar b;
   bool has=false;
   for(int i=0;i<ArraySize(src);i++)
   {
      datetime bt = (datetime)(src[i].time - (src[i].time % (tfMin*60)));
      if(!has || bt!=curBucket)
      {
         if(has)
         {
            int n=ArraySize(out); ArrayResize(out,n+1); out[n]=b;
         }
         curBucket=bt;
         b.t=bt; b.o=src[i].open; b.h=src[i].high; b.l=src[i].low; b.c=src[i].close;
         has=true;
      }
      else
      {
         if(src[i].high>b.h) b.h=src[i].high;
         if(src[i].low<b.l) b.l=src[i].low;
         b.c=src[i].close;
      }
   }
   if(has){ int n=ArraySize(out); ArrayResize(out,n+1); out[n]=b; }
   return ArraySize(out)>0;
}

int FindM1IndexByTime(datetime t)
{
   for(int i=0;i<ArraySize(g_m1);i++) if(g_m1[i].time>=t) return i;
   return -1;
}

bool SignificantGap3(const TFBar &a,const TFBar &b,const TFBar &c,int minPts)
{
   double g1=MathAbs(ToPts(b.o)-ToPts(a.c));
   double g2=MathAbs(ToPts(c.o)-ToPts(b.c));
   return (g1>=minPts || g2>=minPts);
}

void DrawLevel(datetime t1, datetime t2, double h, double l, bool bull)
{
   string up=g_prefix+"LUP_"+IntegerToString((int)t1);
   string dn=g_prefix+"LDN_"+IntegerToString((int)t1);
   ObjectCreate(0,up,OBJ_TREND,0,t1,h,t2,h);
   ObjectCreate(0,dn,OBJ_TREND,0,t1,l,t2,l);
   ObjectSetInteger(0,up,OBJPROP_COLOR,bull?InpBullColor:InpBearColor);
   ObjectSetInteger(0,dn,OBJPROP_COLOR,bull?InpBullColor:InpBearColor);
}

int ResolveExitM1(int from,double stop,double tp,bool buy)
{
   for(int i=from;i<ArraySize(g_m1);i++)
   {
      if(buy){ if(g_m1[i].low<=stop) return -i; if(g_m1[i].high>=tp) return i; }
      else { if(g_m1[i].high>=stop) return -i; if(g_m1[i].low<=tp) return i; }
   }
   return 0;
}

void AddTrade(datetime et,datetime xt,double e,double s,double t,bool buy,bool win)
{
   TradeStat tr; tr.entry_time=et; tr.exit_time=xt; tr.entry=e; tr.stop=s; tr.target=t; tr.is_buy=buy; tr.win=win;
   int n=ArraySize(g_trades); ArrayResize(g_trades,n+1); g_trades[n]=tr;
   if(win){g_wins++;g_targets++;} else {g_losses++;g_stops++;}

   string id=g_prefix+"TR_"+IntegerToString((int)et);
   ObjectCreate(0,id,OBJ_TREND,0,et,e,xt,(win?t:s));
   ObjectSetInteger(0,id,OBJPROP_WIDTH,2);
   ObjectSetInteger(0,id,OBJPROP_COLOR,win?clrDodgerBlue:clrOrangeRed);
}

bool ActiveImportantLevel(datetime t,bool setupBull,double setupHigh,double setupLow)
{
   int idx=-1;
   for(int i=0;i<ArraySize(g_m30);i++) if(g_m30[i].t<=t) idx=i; else break;
   if(idx<0) return false;

   for(int i=idx;i>=0;i--)
   {
      TFBar b=g_m30[i];
      if(MathAbs(ToPts(b.c)-ToPts(b.o))<InpLevelGapPoints) continue;
      bool bull=IsBull(b);
      double h=b.h,l=b.l;
      DrawLevel(b.t,InpEndDate,h,l,bull);
      if(setupBull && bull && setupLow<=h && setupHigh>=l) return true;
      if(!setupBull && !bull && setupLow<=h && setupHigh>=l) return true;
      return false;
   }
   return false;
}

void PrintStats()
{
   int total=g_wins+g_losses;
   double wr= total>0 ? (100.0*g_wins/total) : 0.0;
   PrintFormat("OVERALL | Trades=%d Wins=%d Losses=%d WinRate=%.2f%% Target=%d Stop=%d RR=1:1",total,g_wins,g_losses,wr,g_targets,g_stops);

   string days[]; DayStat ds[];
   for(int i=0;i<ArraySize(g_trades);i++)
   {
      string d=TimeToString(g_trades[i].entry_time,TIME_DATE);
      int id=-1; for(int j=0;j<ArraySize(days);j++) if(days[j]==d){id=j;break;}
      if(id<0){ id=ArraySize(days); ArrayResize(days,id+1); ArrayResize(ds,id+1); days[id]=d; ds[id].wins=0; ds[id].losses=0; ds[id].targets=0; ds[id].stops=0; }
      if(g_trades[i].win){ds[id].wins++; ds[id].targets++;} else {ds[id].losses++; ds[id].stops++;}
   }

   for(int k=0;k<ArraySize(days);k++)
   {
      int t=ds[k].wins+ds[k].losses; double dwr=t>0?100.0*ds[k].wins/t:0.0;
      PrintFormat("%s | Trades=%d WinRate=%.2f%% Target=%d Stop=%d",days[k],t,dwr,ds[k].targets,ds[k].stops);
      if(InpShowDailyLabels)
      {
         string lid=g_prefix+"DAY_"+IntegerToString(k);
         ObjectCreate(0,lid,OBJ_TEXT,0,(datetime)StringToTime(days[k]+" 12:00"),SymbolInfoDouble(_Symbol,SYMBOL_BID));
         ObjectSetString(0,lid,OBJPROP_TEXT,days[k]+" WR:"+DoubleToString(dwr,1)+"% T:"+IntegerToString(ds[k].targets)+" S:"+IntegerToString(ds[k].stops));
      }
   }
}

int OnInit()
{
   g_prefix="GAPSEQ2_"+_Symbol+"_";
   ArraySetAsSeries(g_m1,false);
   int copied=CopyRates(_Symbol,PERIOD_M1,InpStartDate,InpEndDate,g_m1);
   if(copied<200) return INIT_FAILED;

   if(!BuildTF(g_m1,5,g_m5) || !BuildTF(g_m1,30,g_m30)) return INIT_FAILED;

   for(int i=2;i<ArraySize(g_m5)-1;i++)
   {
      TFBar a=g_m5[i-2], b=g_m5[i-1], c=g_m5[i];
      bool bull3=IsBull(a)&&IsBull(b)&&IsBull(c);
      bool bear3=IsBear(a)&&IsBear(b)&&IsBear(c);
      if(!(bull3||bear3)) continue;
      if(!SignificantGap3(a,b,c,InpEntryGapPoints)) continue;

      double zoneH=MathMax(a.h,MathMax(b.h,c.h));
      double zoneL=MathMin(a.l,MathMin(b.l,c.l));
      if(!ActiveImportantLevel(c.t,bull3,zoneH,zoneL)) continue;

      int m1Start=FindM1IndexByTime(c.t+5*60);
      if(m1Start<0) continue;

      bool touched=false; datetime et=0; double entry=0,stop=0,tp=0; bool buy=bull3;
      for(int j=m1Start;j<ArraySize(g_m1) && j<m1Start+InpTouchLookaheadM1;j++)
      {
         if(bull3 && g_m1[j].low<=c.low){ touched=true; et=g_m1[j].time; entry=c.low; stop=zoneL; tp=entry+(entry-stop); break; }
         if(bear3 && g_m1[j].high>=c.high){ touched=true; et=g_m1[j].time; entry=c.high; stop=zoneH; tp=entry-(stop-entry); break; }
      }
      if(!touched) continue;

      int ex=ResolveExitM1(FindM1IndexByTime(et)+1,stop,tp,buy);
      if(ex==0) continue;
      bool win=ex>0; int exi=MathAbs(ex);
      AddTrade(et,g_m1[exi].time,entry,stop,tp,buy,win);
   }

   PrintStats();
   Comment("Done: sequential backtest from ",TimeToString(InpStartDate)," to ",TimeToString(InpEndDate));
   return INIT_SUCCEEDED;
}

void OnTick(){}
