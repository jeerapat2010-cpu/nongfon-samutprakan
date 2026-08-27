#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# NongFon Samut Prakan - Fusion Intelligence v9.4.1 iOS Fix
# Radar + Himawari IR + point forecast. Automated nowcast aid; official warnings should be checked with TMD.

from __future__ import annotations
import io, os, re, json, math, hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import numpy as np
import requests
from PIL import Image
from zoneinfo import ZoneInfo

BKK=ZoneInfo("Asia/Bangkok")
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"; DATA.mkdir(exist_ok=True)
STATUS_PATH=DATA/"radar_status.json"; STATE_PATH=DATA/"radar_state.json"; ALERT_HISTORY_PATH=DATA/"alert_history.json"
SATDA="https://satda.tmd.go.th/"
JMA_TARGET="https://www.jma.go.jp/bosai/himawari/data/satimg/targetTimes_fd.json"
UA={"User-Agent":"NongFon-SamutPrakan/9.4.1"}

DISTRICTS=[
 {"name":"เมืองสมุทรปราการ","short":"เมืองฯ","lat":13.60056,"lon":100.59667},
 {"name":"บางพลี","short":"บางพลี","lat":13.60711,"lon":100.70795},
 {"name":"บางบ่อ","short":"บางบ่อ","lat":13.58513,"lon":100.86470},
 {"name":"พระประแดง","short":"พระประแดง","lat":13.65833,"lon":100.53389},
 {"name":"พระสมุทรเจดีย์","short":"พระสมุทรเจดีย์","lat":13.56861,"lon":100.56167},
 {"name":"บางเสาธง","short":"บางเสาธง","lat":13.59472,"lon":100.83000},
]
PLOT={"x0_frac":66/816,"x1_frac":675/816,"y0_frac":42/1000,"y1_frac":936/1000,"lon0":94.0,"lon1":108.0,"lat0":3.0,"lat1":23.0}

def get(url,timeout=25):
    r=requests.get(url,headers=UA,timeout=timeout); r.raise_for_status(); return r

def scrape_frames(limit=6):
    html=get(SATDA).text
    pattern=r'(?:href|src)=["\']([^"\']*radar_composite/max/(\d{12})\.png)["\']'
    items={s:urljoin(SATDA,h) for h,s in re.findall(pattern,html,flags=re.I)}
    if not items:
        for s in re.findall(r'(\d{12})\.png',html):
            items[s]=f"https://satda.tmd.go.th/wp-content/uploads/data/radar_composite/max/{s}.png"
    ss=sorted(items)[-limit:]; return [(s,items[s]) for s in ss]

def parse_stamp(s): return datetime.strptime(s,"%Y%m%d%H%M").replace(tzinfo=BKK)
def load_image(url): return Image.open(io.BytesIO(get(url).content)).convert("RGB")
def plot_xy(img,lat,lon):
    w,h=img.size
    x0,x1=PLOT["x0_frac"]*w,PLOT["x1_frac"]*w; y0,y1=PLOT["y0_frac"]*h,PLOT["y1_frac"]*h
    return x0+(lon-PLOT["lon0"])/(PLOT["lon1"]-PLOT["lon0"])*(x1-x0), y1-(lat-PLOT["lat0"])/(PLOT["lat1"]-PLOT["lat0"])*(y1-y0)

def rain_strength(img):
    a=np.asarray(img).astype(np.int16); r,g,b=a[:,:,0],a[:,:,1],a[:,:,2]; mx,mn=a.max(2),a.min(2); sat=mx-mn
    s=np.zeros(r.shape,dtype=np.uint8)
    green=(g>70)&(g>r*1.18)&(g>b*1.08)&(sat>45)
    yellow=(r>150)&(g>95)&(b<115)&(sat>55)
    red=(r>165)&(g<115)&(b<105)&(sat>65)
    mag=(r>145)&(b>85)&(g<145)&(sat>45)&(r>g*1.12)
    s[green]=1;s[yellow]=2;s[red]=3;s[mag]=4
    return s

def sample(mask,x,y,radius_px=6):
    h,w=mask.shape; xi,yi=int(round(x)),int(round(y))
    x0,x1=max(0,xi-radius_px),min(w,xi+radius_px+1); y0,y1=max(0,yi-radius_px),min(h,yi+radius_px+1)
    if x0>=x1 or y0>=y1:return {"density":0.0,"strength":0}
    cut=mask[y0:y1,x0:x1]; yy,xx=np.ogrid[y0:y1,x0:x1]; vals=cut[((xx-xi)**2+(yy-yi)**2)<=radius_px**2]
    if vals.size==0:return {"density":0.0,"strength":0}
    wet=vals>0; density=float(wet.mean())
    if wet.sum()<2:return {"density":density,"strength":0}
    return {"density":density,"strength":max(1,int(np.percentile(vals[wet],80)))}

def bbox_pixels(img,lon0,lon1,lat0,lat1):
    x0,y1=plot_xy(img,lat0,lon0); x1,y0=plot_xy(img,lat1,lon1)
    return *sorted((int(x0),int(x1))),*sorted((int(y0),int(y1)))

def shifted_overlap(prev,cur,dx,dy):
    h,w=prev.shape; xs0=max(0,-dx);xs1=min(w,w-dx);ys0=max(0,-dy);ys1=min(h,h-dy)
    xd0=xs0+dx;xd1=xs1+dx;yd0=ys0+dy;yd1=ys1+dy
    if xs1<=xs0 or ys1<=ys0:return 0.0
    a=prev[ys0:ys1,xs0:xs1]; b=cur[yd0:yd1,xd0:xd1]
    return float(np.logical_and(a,b).sum()/math.sqrt(max(1,a.sum())*max(1,b.sum())))

def estimate_motion(images,masks,stamps):
    if len(masks)<2:return {"available":False,"dx":0,"dy":0,"score":0.0,"direction":"ยังประเมินไม่ได้","speed_kmh":0}
    img=images[-1]; xa,xb,ya,yb=bbox_pixels(img,99.3,102.0,12.4,14.8); vectors=[]
    for i in range(max(1,len(masks)-3),len(masks)):
        p=masks[i-1][ya:yb,xa:xb]>0; c=masks[i][ya:yb,xa:xb]>0
        if p.sum()<12 or c.sum()<12:continue
        best=(0.0,0,0)
        for dy in range(-12,13):
            for dx in range(-12,13):
                sc=shifted_overlap(p,c,dx,dy)
                if sc>best[0]:best=(sc,dx,dy)
        if best[0]>=0.30:
            dt=max(5,(parse_stamp(stamps[i])-parse_stamp(stamps[i-1])).total_seconds()/60); vectors.append((best[0],best[1],best[2],dt))
    if len(vectors)<2:return {"available":False,"dx":0,"dy":0,"score":0.0,"direction":"ยังประเมินไม่ได้","speed_kmh":0}
    wt=np.array([v[0] for v in vectors]); dx=float(np.average([v[1] for v in vectors],weights=wt)); dy=float(np.average([v[2] for v in vectors],weights=wt)); score=float(np.average([v[0] for v in vectors],weights=wt)); dt=float(np.average([v[3] for v in vectors],weights=wt))
    w,h=img.size; pxdegx=(PLOT["x1_frac"]*w-PLOT["x0_frac"]*w)/(PLOT["lon1"]-PLOT["lon0"]); pxdegy=(PLOT["y1_frac"]*h-PLOT["y0_frac"]*h)/(PLOT["lat1"]-PLOT["lat0"])
    kmx=(111.32*math.cos(math.radians(13.6)))/pxdegx; kmy=111.0/pxdegy; km=math.sqrt((dx*kmx)**2+(dy*kmy)**2); speed=min(120.0,km/(dt/60.0)) if dt else 0.0
    ang=math.degrees(math.atan2(-dy,dx))
    if -22.5<=ang<22.5:direction="ตะวันออก"
    elif 22.5<=ang<67.5:direction="ตะวันออกเฉียงเหนือ"
    elif 67.5<=ang<112.5:direction="เหนือ"
    elif 112.5<=ang<157.5:direction="ตะวันตกเฉียงเหนือ"
    elif ang>=157.5 or ang<-157.5:direction="ตะวันตก"
    elif -157.5<=ang<-112.5:direction="ตะวันตกเฉียงใต้"
    elif -112.5<=ang<-67.5:direction="ใต้"
    else:direction="ตะวันออกเฉียงใต้"
    return {"available":True,"dx":dx,"dy":dy,"score":score,"direction":direction,"speed_kmh":speed,"frame_minutes":dt}

def open_meteo(d):
    url="https://api.open-meteo.com/v1/forecast"; params={"latitude":d["lat"],"longitude":d["lon"],"minutely_15":"precipitation","hourly":"precipitation_probability,precipitation","forecast_days":1,"timezone":"Asia/Bangkok"}
    try:
        x=requests.get(url,params=params,headers=UA,timeout=15).json(); mins=[float(v or 0) for v in x.get("minutely_15",{}).get("precipitation",[])[:9]]; pp=[float(v or 0) for v in x.get("hourly",{}).get("precipitation_probability",[])[:4]]; hm=[float(v or 0) for v in x.get("hourly",{}).get("precipitation",[])[:4]]
        return {"sum_2h_mm":sum(mins[:8]),"max_prob":max(pp or [0]),"max_hourly_mm":max(hm or [0])}
    except Exception:return {"sum_2h_mm":0,"max_prob":0,"max_hourly_mm":0}

def sev_name(s):return {0:"none",1:"light",2:"moderate",3:"heavy",4:"very_heavy"}.get(int(s),"none")
def risk_level(r):return "severe" if r>=85 else "high" if r>=70 else "watch" if r>=55 else "normal"

def analyze_district(img,masks,motion,d,meteo):
    latest=masks[-1]; x,y=plot_xy(img,d["lat"],d["lon"]); recent=[sample(m,x,y,6) for m in masks[-3:]]
    now=recent[-1]; wet_frames=sum(1 for q in recent if q["density"]>=0.045 and q["strength"]>0); persistent=wet_frames>=2; strong_now=now["strength"]>=3 and now["density"]>=0.035
    eta=None; best=now["strength"] if (persistent or strong_now) else 0
    if (persistent or strong_now) and best>0:eta=0
    elif motion["available"] and motion.get("score",0)>=0.38:
        frame=max(5,float(motion.get("frame_minutes",15))); hits=[]
        for lead in range(15,121,15):
            n=lead/frame; q=sample(latest,x-motion["dx"]*n,y-motion["dy"]*n,7)
            if q["density"]>=0.055 and q["strength"]>0:hits.append((lead,q))
        if hits:eta=hits[0][0];best=max(q["strength"] for _,q in hits[:2])
    if eta==0:risk={1:58,2:72,3:84,4:92}.get(best,45)
    elif eta is not None and eta<=30:risk={1:54,2:67,3:79,4:88}.get(best,45)
    elif eta is not None and eta<=60:risk={1:50,2:62,3:74,4:84}.get(best,42)
    elif eta is not None:risk={1:46,2:57,3:68,4:78}.get(best,38)
    else:risk=18
    if meteo["max_prob"]>=80:risk+=5
    elif meteo["max_prob"]>=60:risk+=3
    if meteo["sum_2h_mm"]>=3:risk+=5
    elif meteo["sum_2h_mm"]>=0.8:risk+=3
    if eta is None and meteo["max_prob"]>=80 and meteo["sum_2h_mm"]>=0.8:risk=max(risk,52)
    conf=30+(22 if persistent else 0)+(12 if strong_now else 0)+(12 if eta is not None and eta>0 else 0)
    if motion["available"]:conf+=min(20,max(0,(motion.get("score",0)-0.30)*50))
    if meteo["max_prob"]>=60 or meteo["sum_2h_mm"]>=0.8:conf+=8
    conf=min(94,conf); risk=int(max(0,min(100,round(risk))))
    if best<=1:risk=min(risk,69)
    if conf<65:risk=min(risk,69)
    return {**d,"raw_radar_risk":risk,"risk":risk,"level":risk_level(risk),"eta_min":eta,"severity":sev_name(best),"confidence":int(round(conf)),"radar_persistence_frames":wet_frames,"motion_correlation":round(float(motion.get("score",0)),2),"forecast_probability":round(meteo["max_prob"]),"forecast_2h_mm":round(meteo["sum_2h_mm"],2)}

def mercator_xy(lat,lon,z=5):
    n=2**z
    x=(lon+180)/360*n
    y=(1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n
    return x,y

def sat_tile_url(t,x,y,band="B13",prod="TBB",z=5):
    return f"https://www.jma.go.jp/bosai/himawari/data/satimg/{t['basetime']}/fd/{t['validtime']}/{band}/{prod}/{z}/{x}/{y}.jpg"

def load_sat_mosaic(t,z=5,x0=24,y0=14):
    # 2x2 JMA tiles ครอบคลุมสมุทรปราการและปริมณฑล
    canvas=Image.new("RGB",(512,512))
    for j,y in enumerate((y0,y0+1)):
        for i,x in enumerate((x0,x0+1)):
            im=Image.open(io.BytesIO(get(sat_tile_url(t,x,y,z=z)).content)).convert("RGB")
            canvas.paste(im,(i*256,j*256))
    a=np.asarray(canvas).astype(np.float32)
    # luminance ใช้กับภาพ IR ที่อาจมีการกลับโทนสว่าง/มืด
    return 0.2126*a[:,:,0]+0.7152*a[:,:,1]+0.0722*a[:,:,2]

def _parse_jma_time(t):
    try:
        return datetime.strptime(t["validtime"],"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None

def satellite_frames():
    try:
        tt=get(JMA_TARGET).json()
        if len(tt)<5:
            return [],None,{"status":"unavailable","reason":"not_enough_frames","frames":0}
        picks=[tt[-5],tt[-3],tt[-1]]
        frames=[]
        for t in picks:
            try:
                frames.append((t,load_sat_mosaic(t)))
            except Exception as e:
                print("satellite frame error",t.get("validtime"),type(e).__name__,str(e)[:100])
        if not frames:
            return [],picks[-1],{"status":"unavailable","reason":"download_failed","frames":0}
        latest=frames[-1][0]
        dt=_parse_jma_time(latest)
        age=None
        if dt:
            age=max(0,(datetime.now(timezone.utc)-dt).total_seconds()/60)
        status="online" if age is None or age<=35 else "stale" if age<=120 else "unavailable"
        return frames,latest,{"status":status,"age_min":round(age,1) if age is not None else None,"frames":len(frames)}
    except Exception as e:
        print("satellite error",type(e).__name__,str(e)[:120])
        return [],None,{"status":"unavailable","reason":type(e).__name__,"frames":0}

def _sat_local_stats(a,d,r=14):
    x,y=mercator_xy(d["lat"],d["lon"],5)
    px=(x-24)*256
    py=(y-14)*256
    xi,yi=int(round(px)),int(round(py))
    y0,y1=max(0,yi-r),min(a.shape[0],yi+r+1)
    x0,x1=max(0,xi-r),min(a.shape[1],xi+r+1)
    cut=a[y0:y1,x0:x1]
    if cut.size<25:
        return None

    # Dynamic calibration: ไม่ผูกกับเลขสีตายตัว 170/210 อีกต่อไป
    gp02,gp10,gp50,gp90,gp98=np.percentile(a,[2,10,50,90,98])
    lp30,lp50,lp70=np.percentile(cut,[30,50,70])
    eps=1e-6

    # รองรับทั้ง render แบบ cloud top สว่างและมืด
    # ใช้ "ค่ากลางของพื้นที่" เทียบกับค่ากลางของภาพ เพื่อไม่ให้ noise ปกติกลายเป็นคะแนนสูง
    bright_med=np.clip((lp50-gp50)/(gp98-gp50+eps),0,1)
    dark_med=np.clip((gp50-lp50)/(gp50-gp02+eps),0,1)
    bright_frac=float((cut>=gp90).mean())
    dark_frac=float((cut<=gp10).mean())

    # ในพื้นที่ปกติ จะมี pixel อยู่ปลาย distribution ราว 10% อยู่แล้ว
    # จึงนับเฉพาะ "ส่วนเกิน" เหนือ baseline นี้
    bright_excess=np.clip((bright_frac-0.10)/0.55,0,1)
    dark_excess=np.clip((dark_frac-0.10)/0.55,0,1)

    bright_signal=0.75*bright_med+0.25*bright_excess
    dark_signal=0.75*dark_med+0.25*dark_excess

    if bright_signal>=dark_signal:
        signal=float(bright_signal); polarity="bright"
    else:
        signal=float(dark_signal); polarity="dark"

    return {
        "signal":signal,
        "polarity":polarity,
        "texture":float(np.std(cut)),
        "median":float(lp50),
        "regional_median":float(gp50),
    }

def sat_score_for_district(frames,d,sat_health):
    status=sat_health.get("status","unavailable")
    unavailable={
        "satellite_score":None,
        "satellite_trend":"unavailable",
        "satellite_status":status,
        "satellite_signal_pct":None,
        "satellite_texture":None,
    }
    if not frames:
        return unavailable

    stats=[]
    for _,a in frames:
        s=_sat_local_stats(a,d)
        if s is not None:
            stats.append(s)
    if not stats:
        return unavailable

    current=stats[-1]
    signals=[s["signal"] for s in stats]
    # anomaly + texture -> 0..95
    raw=current["signal"]*85 + min(10,current["texture"]/3.2)
    score=int(round(np.clip(raw,0,95)))
    delta=signals[-1]-signals[0] if len(signals)>=2 else 0

    if delta>=0.09:
        trend="growing"
    elif delta<=-0.09:
        trend="weakening"
    else:
        trend="steady"

    if status=="stale":
        trend="stale"
    elif status=="unavailable":
        return unavailable

    return {
        "satellite_score":score,
        "satellite_trend":trend,
        "satellite_status":status,
        "satellite_signal_pct":round(current["signal"]*100,1),
        "satellite_texture":round(current["texture"],1),
        "satellite_polarity":current["polarity"],
        "satellite_growth":round(delta,3),
    }

def fuse(row,sat):
    r=int(row["risk"])
    conf=int(row["confidence"])
    s=sat.get("satellite_score")
    trend=sat.get("satellite_trend")
    status=sat.get("satellite_status","unavailable")
    bonus=0
    conf_bonus=0

    # ใช้ดาวเทียมเพิ่มความเชื่อมั่นเฉพาะเมื่อข้อมูลสดเท่านั้น
    if status=="online" and s is not None:
        if s>=80:
            bonus=7; conf_bonus=7
        elif s>=65:
            bonus=4; conf_bonus=5
        elif s>=50:
            bonus=2; conf_bonus=3
        if trend=="growing" and s>=50:
            bonus+=3; conf_bonus+=3

    r=min(100,r+bonus)
    conf=min(96,conf+conf_bonus)

    # ดาวเทียมอย่างเดียวห้ามดันเป็น High/Severe
    if row["severity"]=="none" and row["eta_min"] is None:
        if row["forecast_probability"]>=80 and row["forecast_2h_mm"]>=0.8:
            r=min(r,69)
        else:
            r=min(r,64)

    row.update(sat)
    row["risk"]=int(r)
    row["confidence"]=int(conf)
    row["level"]=risk_level(r)
    return row

def read_json(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except Exception:return default

def alert_tier(top):
    if top["risk"]>=85 or (top["eta_min"]==0 and top["severity"] in ("heavy","very_heavy")):return "severe"
    if top["risk"]>=70 and top["confidence"]>=68:return "high"
    if top["risk"]>=60 and top["confidence"]>=60:return "early"
    return None

def tier_rank(t):return {"early":1,"high":2,"severe":3}.get(t,0)

def append_alert_history(item):
    history=read_json(ALERT_HISTORY_PATH,[])
    if not isinstance(history,list):
        history=[]
    # prevent duplicate rows when a retry reuses the same event/status
    key=f"{item.get('event_key')}|{item.get('status')}|{item.get('tier')}|{item.get('district')}"
    if not any(f"{x.get('event_key')}|{x.get('status')}|{x.get('tier')}|{x.get('district')}"==key for x in history[-40:]):
        history.append(item)
    history=history[-200:]
    ALERT_HISTORY_PATH.write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding="utf-8")

def make_alert_history_item(status,top,tier,push_status,error=None):
    now=datetime.now(timezone.utc)
    return {
        "time":now.isoformat(),
        "event_key":status.get("event_key"),
        "district":top.get("name"),
        "tier":tier,
        "risk":top.get("risk"),
        "confidence":top.get("confidence"),
        "eta_min":top.get("eta_min"),
        "severity":top.get("severity"),
        "satellite_score":top.get("satellite_score"),
        "push_status":push_status,
        "status":push_status,
        "error":error,
    }

def send_onesignal(status,state):
    app=os.getenv("ONESIGNAL_APP_ID","").strip()
    key=os.getenv("ONESIGNAL_REST_API_KEY","").strip()
    site=os.getenv("SITE_URL","").strip() or "https://jeerapat2010-cpu.github.io/nongfon-samutprakan/"
    top=max(status["districts"],key=lambda x:x["risk"])
    tier=alert_tier(top)
    if not tier:
        return False,state

    # ถ้าไม่มี credential ไม่ถือว่าเป็น failed push เพราะระบบยังไม่ได้พยายามส่งจริง
    if not app or not key:
        return False,state

    now=datetime.now(timezone.utc)
    event=status["event_key"]
    try:
        last_at=datetime.fromisoformat(state.get("last_push_at",""))
    except Exception:
        last_at=None

    last_tier=state.get("last_push_tier")
    cooldown={"early":3600,"high":5400,"severe":3600}[tier]
    same_event=state.get("last_push_key")==event
    escalated=tier_rank(tier)>tier_rank(last_tier)

    if same_event and not escalated and last_at and (now-last_at).total_seconds()<cooldown:
        return False,state

    eta="มีฝนแล้ว" if top["eta_min"]==0 else (f"ประมาณ {top['eta_min']} นาที" if top["eta_min"] is not None else "ภายใน 2 ชม.")
    sev={"light":"เบา","moderate":"ปานกลาง","heavy":"หนัก","very_heavy":"หนักมาก","none":"ยังไม่ชัด"}[top["severity"]]
    icon={"early":"🌦️","high":"☔","severe":"🚨"}[tier]
    label={"early":"เริ่มเฝ้าระวัง","high":"เสี่ยงสูง","severe":"เตือนสูง"}[tier]
    title=f"{icon} เฝ้าน้องฝน: {label} {top['name']}"
    sat_txt=top.get("satellite_score") if top.get("satellite_score") is not None else "—"
    body=f"Fusion {top['risk']}/100 • {eta} • ฝน{sev} • Sat {sat_txt}/100 • มั่นใจ {top['confidence']}%"
    payload={
        "app_id":app,
        "target_channel":"push",
        "included_segments":["Subscribed Users"],
        "headings":{"en":title,"th":title},
        "contents":{"en":body,"th":body},
        "url":site,
        "data":{"event_key":event,"tier":tier}
    }

    try:
        r=requests.post(
            "https://api.onesignal.com/notifications",
            headers={"Authorization":f"Key {key}","Content-Type":"application/json"},
            json=payload,
            timeout=20
        )
        if r.ok:
            state.update({
                "last_push_key":event,
                "last_push_at":now.isoformat(),
                "last_push_tier":tier
            })
            append_alert_history(make_alert_history_item(status,top,tier,"sent"))
            return True,state

        err=f"{r.status_code}: {r.text[:160]}"
        print("OneSignal error",err)
        append_alert_history(make_alert_history_item(status,top,tier,"failed",err))
        return False,state
    except Exception as e:
        err=f"{type(e).__name__}: {str(e)[:160]}"
        print("OneSignal exception",err)
        append_alert_history(make_alert_history_item(status,top,tier,"failed",err))
        return False,state

def maybe_write_status(status):
    old=read_json(STATUS_PATH,{})
    def sig(x):
        return {"version":x.get("version"),"province":x.get("province"),"motion":{k:x.get("motion",{}).get(k) for k in ("available","direction","speed_kmh")},"satellite":{k:x.get("satellite",{}).get(k) for k in ("available","status","age_min","time","top_score","trend")},"districts":[{k:d.get(k) for k in ("name","risk","level","eta_min","severity","satellite_score","satellite_trend")} for d in x.get("districts",[])]}
    changed=sig(status)!=sig(old)
    try:heartbeat=(datetime.now(timezone.utc)-datetime.fromisoformat(old.get("generated_at",""))).total_seconds()>=900
    except Exception:heartbeat=True
    if changed or heartbeat or not STATUS_PATH.exists():
        STATUS_PATH.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding="utf-8");return True
    return False

def main():
    frames=scrape_frames(6)
    if not frames:raise SystemExit("No TMD composite frames found")
    stamps=[];urls=[];images=[];masks=[]
    for stamp,url in frames:
        try:
            im=load_image(url);stamps.append(stamp);urls.append(url);images.append(im);masks.append(rain_strength(im))
        except Exception as e:print("skip radar",stamp,e)
    if not images:raise SystemExit("No radar images downloaded")
    motion=estimate_motion(images,masks,stamps);radar_dt=parse_stamp(stamps[-1]);age=max(0,(datetime.now(BKK)-radar_dt).total_seconds()/60)
    sframes,slatest,sat_health=satellite_frames()
    sat_dt=None
    if slatest:
        try:sat_dt=datetime.strptime(slatest["validtime"],"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except Exception:pass
    districts=[]
    for d in DISTRICTS:
        row=analyze_district(images[-1],masks,motion,d,open_meteo(d)); sat=sat_score_for_district(sframes,d,sat_health); row=fuse(row,sat)
        if age>45:
            row["confidence"]=max(25,row["confidence"]-20);row["risk"]=max(0,row["risk"]-8);row["level"]=risk_level(row["risk"])
        districts.append(row)
    top=max(districts,key=lambda x:x["risk"]); level=risk_level(top["risk"]); eta="มีฝนแล้ว" if top["eta_min"]==0 else (f"คาดประมาณ {top['eta_min']} นาที" if top["eta_min"] is not None else "ยังไม่เห็นก้อนฝนเข้าถึงภายใน 120 นาที")
    sev={"light":"เบา","moderate":"ปานกลาง","heavy":"หนัก","very_heavy":"หนักมาก","none":"ยังไม่ชัด"}[top["severity"]]
    sat_phrase="เมฆก่อตัวเพิ่ม" if top.get("satellite_trend")=="growing" else "เมฆทรงตัว" if top.get("satellite_trend")=="steady" else "เมฆลดลง" if top.get("satellite_trend")=="weakening" else "ข้อมูลดาวเทียมเก่า" if top.get("satellite_trend")=="stale" else "ดาวเทียมไม่พร้อม"
    if top["risk"]>=85:summary=f"🚨 {top['name']}: {eta} • ฝน{sev} • Fusion {top['risk']}/100 • {sat_phrase}"
    elif top["risk"]>=70:summary=f"⚠️ {top['name']}: {eta} • ฝน{sev} • Fusion {top['risk']}/100 • {sat_phrase}"
    elif top["risk"]>=60:summary=f"🌦️ เริ่มเฝ้าระวัง {top['name']} • Fusion {top['risk']}/100 • Satellite {top.get('satellite_score') if top.get('satellite_score') is not None else "—"}/100"
    else:summary="ภาพรวมสมุทรปราการยังไม่พบแนวฝนที่มีความเสี่ยงสูงจาก Radar + Satellite Fusion"
    seed=f"{top['name']}|{level}|{top['eta_min']}|{top['severity']}|{motion.get('direction')}|{top['satellite_trend']}|{radar_dt:%Y%m%d%H}"
    status={"version":"9.4.1 iOSFix","generated_at":datetime.now(timezone.utc).isoformat(),"radar_time":radar_dt.astimezone(timezone.utc).isoformat(),"radar_age_min":round(age,1),"event_key":hashlib.sha1(seed.encode()).hexdigest()[:14],
            "province":{"risk":top["risk"],"level":level,"summary":summary,"top_district":top["name"]},
            "motion":{"available":bool(motion["available"]),"direction":motion["direction"],"speed_kmh":round(float(motion["speed_kmh"]),1),"correlation":round(float(motion["score"]),2)},
            "satellite":{"available":bool(sframes),"status":sat_health.get("status","unavailable"),"age_min":sat_health.get("age_min"),"frames":sat_health.get("frames",0),"provider":"JMA Himawari-8/9 B13 IR","time":sat_dt.isoformat() if sat_dt else None,"top_score":top.get("satellite_score"),"trend":top.get("satellite_trend")},
            "fusion":{"principle":"Radar primary + Himawari IR support + Open-Meteo support","satellite_alone_high_alert":False,"early_watch":60,"high":70,"severe":85},
            "districts":districts,
            "sources":{"tmd_satda":SATDA,"tmd_radar_frame":urls[-1],"tmd_suvarnabhumi":"https://weather.tmd.go.th/svp120.php","tmd_satellite":"https://satda.tmd.go.th/tmd_satellite.php","jma_himawari":"https://www.jma.go.jp/bosai/map.html#contents=himawari","open_meteo":"https://open-meteo.com/"},
            "note":"Automated nowcast aid; satellite is supporting evidence. Official warnings should be checked with TMD."}
    state=read_json(STATE_PATH,{})
    pushed,state=send_onesignal(status,state);STATE_PATH.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    wrote=maybe_write_status(status)
    print(json.dumps({"status_written":wrote,"radar_time":status["radar_time"],"satellite":bool(sframes),"top":top["name"],"risk":top["risk"],"push":pushed},ensure_ascii=False))

if __name__=="__main__":main()
