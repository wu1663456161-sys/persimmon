#!/usr/bin/env python3
"""柿子选股爬虫 - 运行在 GitHub Actions 上，无 CORS 限制"""
import requests, json, time, os, sys
from datetime import datetime, timedelta

BASE_EM = "https://push2.eastmoney.com/api/qt"
BASE_HIS = "https://push2his.eastmoney.com/api/qt"
OUTPUT = "data.json"

def fetch(url, retry=2):
    for i in range(retry+1):
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
            if r.status_code==200: return r.json()
        except: pass
        if i<retry: time.sleep(1)
    return None

def get_indices():
    idx = {
        "1.000001":"sh000001","0.399001":"sz399001","0.399006":"sz399006"
    }
    result = {}
    for secid, code in idx.items():
        d = fetch(f"{BASE_EM}/stock/get?fields=f43,f44,f45,f46,f47,f48,f60,f170,f169&secid={secid}")
        if d and d.get("data"):
            dd = d["data"]
            result[code] = {
                "name": dd.get("f58",""),
                "price": (dd.get("f43",0) or 0)/100,
                "prev": (dd.get("f60",0) or 0)/100,
                "pct": (dd.get("f170",0) or 0)/100,
                "vol": dd.get("f47",0) or 0,
                "amt": dd.get("f48",0) or 0,
                "high": (dd.get("f44",0) or 0)/100,
                "low": (dd.get("f45",0) or 0)/100
            }
    return result

def get_ranking():
    url = f"{BASE_EM}/clist/get?pn=1&pz=200&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80&fields=f2,f3,f5,f6,f12,f14,f15,f16,f17&fid=f3"
    d = fetch(url)
    if not d or not d.get("data"): return []
    stocks = []
    for s in d["data"].get("diff",[]):
        code = s.get("f12","")
        name = s.get("f14","")
        if "ST" in name or "退" in name: continue
        if code.startswith("688") or code.startswith("30"): continue
        stocks.append({
            "code": code, "name": name,
            "price": s.get("f2",0), "pct": s.get("f3",0),
            "vol": s.get("f5",0), "amt": s.get("f6",0),
            "high": s.get("f15",0), "low": s.get("f16",0)
        })
    return stocks

def get_klines(codes, days=30):
    results = {}
    for code in codes[:60]:
        mkt = "1" if code.startswith("6") else "0"
        secid = f"{mkt}.{code}"
        url = f"{BASE_HIS}/stock/kline/get?secid={secid}&fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f57&klt=101&fqt=0&end=20500101&lmt={days}"
        d = fetch(url)
        if d and d.get("data") and d["data"].get("klines"):
            k = []
            for line in d["data"]["klines"]:
                p = line.split(",")
                k.append({
                    "d": p[0], "o": float(p[1]), "c": float(p[2]),
                    "h": float(p[3]), "l": float(p[4]), "v": float(p[5])
                })
            results[code] = k
    return results

def calc_score(kdata, quote):
    if not kdata or len(kdata)<5:
        return {"score":0, "reasons":["数据不足"], "buy":"--", "sell":"--"}
    closes = [d["c"] for d in kdata]
    vols = [d["v"] for d in kdata]
    latest = closes[-1]
    lv = vols[-1]

    ma5 = sum(closes[-5:])/5
    ma10 = sum(closes[-10:])/10 if len(closes)>=10 else ma5
    ma20 = sum(closes[-20:])/20 if len(closes)>=20 else ma10

    avg_v5 = sum(vols[-5:])/5
    avg_v20 = sum(vols[-20:])/20 if len(vols)>=20 else avg_v5

    score = 0
    reasons = []

    if ma5>ma10>ma20: score+=20; reasons.append("多头排列")
    elif ma5>ma20: score+=12; reasons.append("均线偏多")
    else: score+=5

    if latest>ma5 and latest>ma20: score+=10; reasons.append("价站均线")

    if lv>avg_v5*1.2 and lv>avg_v20: score+=15; reasons.append("量能放大")
    elif lv>avg_v5: score+=8
    else: score+=3

    up_days = sum(1 for i in range(1,min(5,len(closes))) if closes[-i]>closes[-i-1])
    if up_days>=4: score+=15; reasons.append("连续强势")
    elif up_days>=3: score+=10
    else: score+=4

    hh = max(closes[-30:])
    ll = min(closes[-30:])
    if latest>=hh*0.97: score+=10
    elif latest<=ll*1.05: score+=12; reasons.append("低位反转")
    else: score+=6

    if lv>avg_v20*2: score-=5; reasons.append("异常放量")

    score = max(0, min(100, score))

    buy_pt = "待信号确认"
    sell_pt = f"{quote['price']*0.95:.2f}"
    if score>=78:
        buy_pt = f"回踩{ma20:.2f}附近"
        sell_pt = f"止损{quote['price']*0.95:.2f}/止盈{quote['price']*1.08:.2f}"
    elif score>=60:
        buy_pt = f"回踩{ma10:.2f}附近"
        sell_pt = f"止损{quote['price']*0.95:.2f}/止盈{quote['price']*1.05:.2f}"

    return {"score":score, "reasons":reasons, "buy":buy_pt, "sell":sell_pt}

def get_sectors():
    url = f"{BASE_EM}/clist/get?pn=1&pz=30&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f2,f3,f12,f14&fid=f3"
    d = fetch(url)
    if not d or not d.get("data"): return []
    return d["data"].get("diff",[])[:10]

def get_sector_stocks(code):
    url = f"{BASE_EM}/clist/get?pn=1&pz=8&po=1&np=1&fltt=2&invt=2&fid=f3&fs=b:{code}&fields=f2,f3,f12,f14&fid=f3"
    d = fetch(url)
    if not d or not d.get("data"): return []
    return d["data"].get("diff",[])

def get_global_markets():
    codes = ["100.DJIA","100.NDX","100.SPX","100.HSI","100.N225"]
    names = {"100.DJIA":"道琼斯","100.NDX":"纳斯达克","100.SPX":"标普500","100.HSI":"恒生","100.N225":"日经"}
    result = []
    for c in codes:
        d = fetch(f"{BASE_EM}/stock/get?fields=f43,f170,f58&secid={c}")
        if d and d.get("data"):
            result.append({
                "name": names.get(c,""), "price": d["data"].get("f43",0)/100,
                "pct": d["data"].get("f170",0)/100
            })
    return result

def get_us_hot():
    codes = {"105.AAPL":"苹果","105.MSFT":"微软","105.NVDA":"英伟达",
             "105.TSLA":"特斯拉","105.GOOG":"谷歌","105.AMZN":"亚马逊","105.META":"Meta","105.AMD":"AMD"}
    result = []
    for c, n in codes.items():
        d = fetch(f"{BASE_EM}/stock/get?fields=f43,f170&secid={c}")
        if d and d.get("data"):
            result.append({"name":n, "price":d["data"].get("f43",0)/100, "pct":d["data"].get("f170",0)/100})
    return result

def main():
    print("柿子爬虫启动...")
    result = {"time":"", "indices":{}, "picks":[], "sectors":[], "global":{}}

    now = datetime.now()
    result["time"] = now.strftime("%Y-%m-%d %H:%M")
    print(f"时间: {result['time']}")

    try:
        indices = get_indices()
        result["indices"] = indices
        print(f"指数: {len(indices)}个 OK")
    except Exception as e:
        print(f"指数获取失败: {e}")

    try:
        stocks = get_ranking()
        print(f"股票池: {len(stocks)}只")
        codes = [s["code"] for s in stocks[:60]]
        klines = get_klines(codes)
        print(f"K线: {len(klines)}只 OK")

        scored = []
        for s in stocks:
            kd = klines.get(s["code"])
            if kd:
                info = calc_score(kd, s)
                info["name"] = s["name"]
                info["code"] = s["code"]
                info["price"] = s["price"]
                info["pct"] = s["pct"]
                info["vol"] = s["vol"]
                scored.append(info)

        scored.sort(key=lambda x: x["score"], reverse=True)
        result["picks"] = scored[:10]
        print(f"选股: {len(result['picks'])}只 OK")
    except Exception as e:
        print(f"选股失败: {e}")

    try:
        sectors = get_sectors()
        top_sectors = []
        blacklist = ["房地产","证券","银行","保险"]
        for sec in sectors:
            name = sec.get("f14","")
            if any(b in name for b in blacklist): continue
            stocks = get_sector_stocks(sec["f12"])
            if len(stocks)>=4:
                top_sectors.append({
                    "name": name, "code": sec["f12"], "pct": sec.get("f3",0),
                    "stocks": [{"name":s.get("f14",""),"code":s.get("f12",""),"pct":s.get("f3",0)} for s in stocks[:4]]
                })
            if len(top_sectors)>=3: break
        result["sectors"] = top_sectors
        print(f"板块: {len(top_sectors)}个 OK")
    except Exception as e:
        print(f"板块失败: {e}")

    try:
        result["global"] = {
            "us": get_global_markets(),
            "usStocks": get_us_hot()
        }
        print(f"外围: OK")
    except Exception as e:
        print(f"外围失败: {e}")

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"输出: {OUTPUT} ({os.path.getsize(OUTPUT)} bytes)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
