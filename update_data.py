# -*- coding: utf-8 -*-
"""Daily official TWSE / TPEx closing snapshot. No intraday or mismatched dates."""
import json, re, datetime, pathlib, sys
from zoneinfo import ZoneInfo
import requests
ROOT=pathlib.Path(__file__).resolve().parent
SESS=requests.Session()
SESS.headers.update({"User-Agent":"Mozilla/5.0 (Taiwan daily stock dashboard; public market data)"})
def fetch(url):
    r=SESS.get(url,timeout=40)
    r.raise_for_status()
    return r.json()
def n(x):
    try: return float(str(x).replace(",","").strip())
    except (ValueError,TypeError): return None
def iscommon(c):
    return bool(re.fullmatch(r"[1-9][0-9]{3}",str(c))) and not str(c).startswith("91")
def asdate(roc):
    return f"{int(roc[:3])+1911:04d}-{roc[3:5]}-{roc[5:7]}"
def load_twse(day):
    ds=day.replace("-","")
    j=fetch("https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date="+ds+"&type=ALLBUT0999")
    target=f"{int(ds[:4])-1911}年{int(ds[4:6])}月{int(ds[6:8])}日"
    t=next((z for z in j.get("tables",[]) if "每日收盤行情" in z.get("title","") and target in z.get("title","") and "收盤價" in z.get("fields",[])),None)
    return t
def main():
    now=datetime.datetime.now(ZoneInfo("Asia/Taipei"))
    timestamp=now.isoformat(timespec="seconds")
    status={"last_checked":timestamp,"state":"checking"}
    try:
        # Prefer the official date-specific after-trading report: the TPEx OpenAPI
        # quote feed may lag behind by one day even after today's report is published.
        day=now.date().isoformat()
        roc=f"{now.year-1911:03d}/{now.month:02d}/{now.day:02d}"
        otc_url="https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?response=json&date="+roc.replace("/","%2F")
        report=fetch(otc_url)
        tab=next((t for t in report.get("tables",[]) if t.get("title")=="上櫃股票行情"),None)
        if tab and int(tab.get("listedCompanies",0))>700 and len(tab.get("data",[]))>800:
            otc_day=day
            otc_rows=[r for r in tab["data"] if len(r)>=16 and iscommon(r[0])]
        else:
            otc=fetch("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes")
            old_rows=[x for x in otc if iscommon(x.get("SecuritiesCompanyCode"))]
            dates={x.get("Date") for x in old_rows}
            if len(dates)!=1:raise ValueError("櫃買中心行情日期不一致")
            otc_day=asdate(next(iter(dates)))
            otc_rows=[[x.get("SecuritiesCompanyCode"),x.get("CompanyName"),x.get("Close"),x.get("Change"),None,None,None,None,None,None,None,None,None,None,None,x.get("Capitals")] for x in old_rows]
            otc_url="https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        status["tpex_date"]=otc_day
        tw_today=load_twse(now.date().isoformat()) if now.weekday()<5 else None
        status["twse_today_available"]=bool(tw_today and len(tw_today.get("data",[]))>1000)
        tw=tw_today if otc_day==now.date().isoformat() and status["twse_today_available"] else load_twse(otc_day)
        if not tw or len(tw.get("data",[]))<950:raise ValueError("證交所對應交易日完整收盤資料尚未發布")
        company=fetch("https://openapi.twse.com.tw/v1/opendata/t187ap03_L")
        issued={str(x.get("公司代號")):n(x.get("已發行普通股數或TDR原股發行股數")) for x in company}
        listing=[]
        for r in tw["data"]:
            c=str(r[0]).strip()
            if not iscommon(c) or c not in issued:continue
            p=n(r[8]);delta=n(r[10]);sh=issued[c]
            if p is None or delta is None or not sh or p<=0:continue
            sign=str(r[9])
            if "green" in sign or ">-" in sign:chg=-abs(delta)
            elif "red" in sign or ">+" in sign:chg=abs(delta)
            elif delta==0:chg=0
            else:continue
            if p-chg<=0:continue
            listing.append({"code":c,"name":str(r[1]),"market":"上市","price":p,"pct":round(chg/(p-chg)*100,3),"cap":round(p*sh)})
        over=[]
        for r in otc_rows:
            c=str(r[0])
            p=n(r[2]);chg=n(r[3]);sh=n(r[15])
            if p is None or chg is None or not sh or p<=0 or p-chg<=0:continue
            over.append({"code":c,"name":str(r[1]),"market":"上櫃","price":p,"pct":round(chg/(p-chg)*100,3),"cap":round(p*sh)})
        if len(listing)<900 or len(over)<500:raise ValueError("上市或上櫃普通股資料筆數不足")
        combined={r["code"]:r for r in listing+over}
        if len(combined)<1500:raise ValueError("可排名股票過少，拒絕公布")
        top=sorted(combined.values(),key=lambda x:x["cap"],reverse=True)[:100]
        for i,r in enumerate(top):r["rank"]=i+1
        result={"date":otc_day,"stocks":top,"total":len(combined),"updated":timestamp,
        "sources":["https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date="+otc_day.replace("-","")+"&type=ALLBUT0999",
        "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
        otc_url],
        "market":"上市及上櫃普通股收盤資料，非盤中",
        "alerts5":len([x for x in top if x["pct"]<=-5])}
        previous=ROOT/"data.json"
        if previous.exists():
            try:
                old=json.loads(previous.read_text(encoding="utf-8"))
                if old.get("date","")>otc_day:raise ValueError("新資料日期早於已保存的資料")
            except json.JSONDecodeError:pass
        previous.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        status["state"]="ready" if otc_day==now.date().isoformat() else "latest_verified"
        status["verified_date"]=otc_day
        status["message"]="當天兩市場收盤資料已核對" if status["state"]=="ready" else ("今日兩市場收盤資料尚未全部公布；顯示最近共同可驗證的交易日 "+otc_day)
        print("VERIFIED",otc_day,"total",len(combined),"alerts",result["alerts5"])
    except Exception as e:
        status["state"]="error"
        status["message"]=str(e)
        print("ERROR",e,file=sys.stderr)
    (ROOT/"status.json").write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding="utf-8")
    if status["state"]=="error":sys.exit(1)
if __name__=="__main__":main()
