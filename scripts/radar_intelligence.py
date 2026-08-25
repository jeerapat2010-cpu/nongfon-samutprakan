#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# NongFon Samut Prakan - Radar Intelligence v8.2
# Automated nowcast aid; official warnings should be checked with TMD.

from __future__ import annotations
import io, os, re, json, math, hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import numpy as np
import requests
from PIL import Image
from zoneinfo import ZoneInfo

BKK = ZoneInfo("Asia/Bangkok")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
STATUS_PATH = DATA / "radar_status.json"
STATE_PATH = DATA / "radar_state.json"

SATDA = "https://satda.tmd.go.th/"
UA = {"User-Agent":"NongFon-SamutPrakan/8.2"}

DISTRICTS = [
 {"name":"เมืองสมุทรปราการ","short":"เมืองฯ","lat":13.60056,"lon":100.59667},
 {"name":"บางพลี","short":"บางพลี","lat":13.60711,"lon":100.70795},
 {"name":"บางบ่อ","short":"บางบ่อ","lat":13.58513,"lon":100.86470},
 {"name":"พระประแดง","short":"พระประแดง","lat":13.65833,"lon":100.53389},
 {"name":"พระสมุทรเจดีย์","short":"พระสมุทรเจดีย์","lat":13.56861,"lon":100.56167},
 {"name":"บางเสาธง","short":"บางเสาธง","lat":13.59472,"lon":100.83000},
]

PLOT={"x0_frac":66/816,"x1_frac":675/816,"y0_frac":42/1000,"y1_frac":936/1000,
      "lon0":94.0,"lon1":108.0,"lat0":3.0,"lat1":23.0}

def get(url, timeout=25):
    r=requests.get(url,headers=UA,timeout=timeout)
    r.raise_for_status()
    return r

def scrape_frames(limit=6):
    html=get(SATDA).text
    pattern=r'(?:href|src)=["\']([^"\']*radar_composite/max/(\d{12})\.png)["\']'
    found=re.findall(pattern,html,flags=re.I)
    items={}
    for href,stamp in found:
        items[stamp]=urljoin(SATDA,href)
    if not items:
        for stamp in re.findall(r'(\d{12})\.png',html):
            items[stamp]=f"https://satda.tmd.go.th/wp-content/uploads/data/radar_composite/max/{stamp}.png"
    stamps=sorted(items)[-limit:]
    return [(s,items[s]) for s in stamps]

def parse_stamp(stamp):
    return datetime.strptime(stamp,"%Y%m%d%H%M").replace(tzinfo=BKK)

def load_image(url):
    return Image.open(io.BytesIO(get(url).content)).convert("RGB")

def plot_xy(img,lat,lon):
    w,h=img.size
    x0,x1=PLOT["x0_frac"]*w,PLOT["x1_frac"]*w
    y0,y1=PLOT["y0_frac"]*h,PLOT["y1_frac"]*h
    x=x0+(lon-PLOT["lon0"])/(PLOT["lon1"]-PLOT["lon0"])*(x1-x0)
    y=y1-(lat-PLOT["lat0"])/(PLOT["lat1"]-PLOT["lat0"])*(y1-y0)
    return x,y

def rain_strength(img):
    a=np.asarray(img).astype(np.int16)
    r,g,b=a[:,:,0],a[:,:,1],a[:,:,2]
    mx,mn=a.max(2),a.min(2)
    sat=mx-mn
    s=np.zeros(r.shape,dtype=np.uint8)
    green=(g>70)&(g>r*1.18)&(g>b*1.08)&(sat>45)
    yellow=(r>150)&(g>95)&(b<115)&(sat>55)
    red=(r>165)&(g<115)&(b<105)&(sat>65)
    magenta=(r>145)&(b>85)&(g<145)&(sat>45)&(r>g*1.12)
    s[green]=1;s[yellow]=2;s[red]=3;s[magenta]=4
    return s

def sample(mask,x,y,radius_px=6):
    h,w=mask.shape;xi,yi=int(round(x)),int(round(y))
    x0,x1=max(0,xi-radius_px),min(w,xi+radius_px+1)
    y0,y1=max(0,yi-radius_px),min(h,yi+radius_px+1)
    if x0>=x1 or y0>=y1:return {"density":0.0,"strength":0}
    cut=mask[y0:y1,x0:x1]
    yy,xx=np.ogrid[y0:y1,x0:x1]
    vals=cut[((xx-xi)**2+(yy-yi)**2)<=radius_px**2]
    if vals.size==0:return {"density":0.0,"strength":0}
    wet=vals>0;density=float(wet.mean())
    if wet.sum()<2:return {"density":density,"strength":0}
    return {"density":density,"strength":max(1,int(np.percentile(vals[wet],80)))}

def bbox_pixels(img,lon0,lon1,lat0,lat1):
    x0,y1=plot_xy(img,lat0,lon0);x1,y0=plot_xy(img,lat1,lon1)
    xa,xb=sorted((int(x0),int(x1)));ya,yb=sorted((int(y0),int(y1)))
    return xa,xb,ya,yb

def shifted_overlap(prev,cur,dx,dy):
    h,w=prev.shape
    xs0=max(0,-dx);xs1=min(w,w-dx);ys0=max(0,-dy);ys1=min(h,h-dy)
    xd0=xs0+dx;xd1=xs1+dx;yd0=ys0+dy;yd1=ys1+dy
    if xs1<=xs0 or ys1<=ys0:return 0.0
    a=prev[ys0:ys1,xs0:xs1];b=cur[yd0:yd1,xd0:xd1]
    inter=np.logical_and(a,b).sum()
    return float(inter/math.sqrt(max(1,a.sum())*max(1,b.sum())))

def estimate_motion(images,masks,stamps):
    if len(masks)<2:return {"available":False,"dx":0,"dy":0,"score":0.0,"direction":"ยังประเมินไม่ได้","speed_kmh":0}
    img=images[-1];xa,xb,ya,yb=bbox_pixels(img,99.3,102.0,12.4,14.8)
    vectors=[]
    for i in range(max(1,len(masks)-3),len(masks)):
        p=masks[i-1][ya:yb,xa:xb]>0;c=masks[i][ya:yb,xa:xb]>0
        if p.sum()<12 or c.sum()<12:continue
        best=(0.0,0,0)
        for dy in range(-12,13):
            for dx in range(-12,13):
                sc=shifted_overlap(p,c,dx,dy)
                if sc>best[0]:best=(sc,dx,dy)
        if best[0]>=0.30:
            dt=max(5,(parse_stamp(stamps[i])-parse_stamp(stamps[i-1])).total_seconds()/60)
            vectors.append((best[0],best[1],best[2],dt))
    # v8.1: ต้องมีอย่างน้อย 2 คู่เฟรมที่สอดคล้องกัน เพื่อลดการตีความ noise เป็นการเคลื่อนตัว
    if len(vectors)<2:return {"available":False,"dx":0,"dy":0,"score":0.0,"direction":"ยังประเมินไม่ได้","speed_kmh":0}
    weights=np.array([v[0] for v in vectors])
    dx=float(np.average([v[1] for v in vectors],weights=weights))
    dy=float(np.average([v[2] for v in vectors],weights=weights))
    score=float(np.average([v[0] for v in vectors],weights=weights))
    dt=float(np.average([v[3] for v in vectors],weights=weights))
    w,h=img.size
    pxdegx=(PLOT["x1_frac"]*w-PLOT["x0_frac"]*w)/(PLOT["lon1"]-PLOT["lon0"])
    pxdegy=(PLOT["y1_frac"]*h-PLOT["y0_frac"]*h)/(PLOT["lat1"]-PLOT["lat0"])
    kmx=(111.32*math.cos(math.radians(13.6)))/pxdegx
    kmy=111.0/pxdegy
    km=math.sqrt((dx*kmx)**2+(dy*kmy)**2)
    speed=min(120.0,km/(dt/60.0)) if dt else 0.0
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
    url="https://api.open-meteo.com/v1/forecast"
    params={"latitude":d["lat"],"longitude":d["lon"],"minutely_15":"precipitation",
            "hourly":"precipitation_probability,precipitation","forecast_days":1,"timezone":"Asia/Bangkok"}
    try:
        x=requests.get(url,params=params,headers=UA,timeout=15).json()
        mins=[float(v or 0) for v in x.get("minutely_15",{}).get("precipitation",[])[:9]]
        pp=[float(v or 0) for v in x.get("hourly",{}).get("precipitation_probability",[])[:4]]
        hm=[float(v or 0) for v in x.get("hourly",{}).get("precipitation",[])[:4]]
        return {"sum_2h_mm":sum(mins[:8]),"max_prob":max(pp or [0]),"max_hourly_mm":max(hm or [0])}
    except Exception:
        return {"sum_2h_mm":0,"max_prob":0,"max_hourly_mm":0}

def sev_name(s):return {0:"none",1:"light",2:"moderate",3:"heavy",4:"very_heavy"}.get(int(s),"none")
def risk_level(r):
    if r>=85:return "severe"
    if r>=70:return "high"
    if r>=55:return "watch"
    return "normal"

def analyze_district(img,masks,motion,d,meteo):
    """
    v8.1 calibration:
    - Current rain requires persistence in >=2 of latest 3 radar frames, except strong echo.
    - Light echo alone can no longer become 'severe'.
    - Projected ETA is used only when motion correlation is reasonably strong.
    - Forecast is supporting evidence, not a replacement for radar.
    """
    latest=masks[-1]
    x,y=plot_xy(img,d["lat"],d["lon"])

    recent=[]
    for m in masks[-3:]:
        recent.append(sample(m,x,y,6))
    now=recent[-1]

    wet_frames=sum(1 for q in recent if q["density"]>=0.045 and q["strength"]>0)
    persistent = wet_frames >= 2
    strong_now = now["strength"] >= 3 and now["density"] >= 0.035

    eta=None
    best=now["strength"] if (persistent or strong_now) else 0

    if (persistent or strong_now) and best>0:
        eta=0
    elif motion["available"] and motion.get("score",0)>=0.38:
        frame=max(5,float(motion.get("frame_minutes",15)))
        hits=[]
        for lead in range(15,121,15):
            n=lead/frame
            q=sample(latest,x-motion["dx"]*n,y-motion["dy"]*n,7)
            if q["density"]>=0.055 and q["strength"]>0:
                hits.append((lead,q))
        if hits:
            eta=hits[0][0]
            best=max(q["strength"] for _,q in hits[:2])

    # Radar-driven calibrated base risk.
    if eta==0:
        base_by_strength={1:58,2:72,3:84,4:92}
        risk=base_by_strength.get(best,45)
    elif eta is not None and eta<=30:
        base_by_strength={1:54,2:67,3:79,4:88}
        risk=base_by_strength.get(best,45)
    elif eta is not None and eta<=60:
        base_by_strength={1:50,2:62,3:74,4:84}
        risk=base_by_strength.get(best,42)
    elif eta is not None:
        base_by_strength={1:46,2:57,3:68,4:78}
        risk=base_by_strength.get(best,38)
    else:
        risk=18

    # Forecast support is deliberately capped.
    if meteo["max_prob"]>=80:risk+=5
    elif meteo["max_prob"]>=60:risk+=3
    if meteo["sum_2h_mm"]>=3:risk+=5
    elif meteo["sum_2h_mm"]>=0.8:risk+=3

    # Forecast-only watch: never high/severe without radar evidence.
    if eta is None and meteo["max_prob"]>=80 and meteo["sum_2h_mm"]>=0.8:
        risk=max(risk,52)

    # Confidence reflects persistence + motion quality + forecast agreement.
    conf=30
    if persistent: conf+=22
    if strong_now: conf+=12
    if eta is not None and eta>0: conf+=12
    if motion["available"]:
        conf+=min(20,max(0,(motion.get("score",0)-0.30)*50))
    if meteo["max_prob"]>=60 or meteo["sum_2h_mm"]>=0.8:
        conf+=8
    conf=min(94,conf)

    risk=int(max(0,min(100,round(risk))))

    # Guard rails: light rain cannot be severe; low confidence cannot be severe.
    if best<=1:
        risk=min(risk,69)
    if conf<65:
        risk=min(risk,69)

    return {**d,"risk":risk,"level":risk_level(risk),"eta_min":eta,"severity":sev_name(best),
            "confidence":int(round(conf)),"radar_persistence_frames":wet_frames,
            "motion_correlation":round(float(motion.get("score",0)),2),
            "forecast_probability":round(meteo["max_prob"]),
            "forecast_2h_mm":round(meteo["sum_2h_mm"],2)}

def read_json(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except Exception:return default

def send_onesignal(status,state):
    app=os.getenv("ONESIGNAL_APP_ID","").strip();key=os.getenv("ONESIGNAL_REST_API_KEY","").strip()
    site=os.getenv("SITE_URL","").strip() or "https://jeerapat2010-cpu.github.io/nongfon-samutprakan/"
    if not app or not key or status["province"]["risk"]<70:return False,state
    if max(d.get("confidence",0) for d in status["districts"])<68:return False,state
    top=max(status["districts"],key=lambda x:x["risk"]);event=status["event_key"];now=datetime.now(timezone.utc)
    try:last_at=datetime.fromisoformat(state.get("last_push_at",""))
    except Exception:last_at=None
    if state.get("last_push_key")==event and last_at and (now-last_at).total_seconds()<5400:return False,state
    eta="มีฝนแล้ว" if top["eta_min"]==0 else (f"ประมาณ {top['eta_min']} นาที" if top["eta_min"] is not None else "ภายใน 2 ชม.")
    sev={"light":"เบา","moderate":"ปานกลาง","heavy":"หนัก","very_heavy":"หนักมาก","none":"ไม่ชัด"}[top["severity"]]
    title=f"☔ เฝ้าน้องฝน: {top['name']}"
    body=f"แนวฝน {eta} • {sev} • ไปทาง{status['motion'].get('direction','ไม่ชัด')} • มั่นใจ {top['confidence']}%"
    payload={"app_id":app,"target_channel":"push","included_segments":["Subscribed Users"],"headings":{"en":title,"th":title},
             "contents":{"en":body,"th":body},"url":site,"data":{"event_key":event}}
    r=requests.post("https://api.onesignal.com/notifications",
                    headers={"Authorization":f"Key {key}","Content-Type":"application/json"},
                    json=payload,timeout=20)
    if r.ok:
        state["last_push_key"]=event;state["last_push_at"]=now.isoformat();return True,state
    print("OneSignal error",r.status_code,r.text[:300]);return False,state

def maybe_write_status(status):
    old=read_json(STATUS_PATH,{})
    def sig(x):
        return {"province":x.get("province"),
                "motion":{k:x.get("motion",{}).get(k) for k in ("available","direction","speed_kmh")},
                "districts":[{k:d.get(k) for k in ("name","risk","level","eta_min","severity")} for d in x.get("districts",[])]}
    changed=sig(status)!=sig(old)
    heartbeat=True
    try:heartbeat=(datetime.now(timezone.utc)-datetime.fromisoformat(old.get("generated_at",""))).total_seconds()>=1800
    except Exception:pass
    if changed or heartbeat or not STATUS_PATH.exists():
        STATUS_PATH.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding="utf-8");return True
    return False

def main():
    frames=scrape_frames(6)
    if not frames:raise SystemExit("No TMD composite frames found")
    stamps=[];urls=[];images=[];masks=[]
    for stamp,url in frames:
        try:
            img=load_image(url);stamps.append(stamp);urls.append(url);images.append(img);masks.append(rain_strength(img))
        except Exception as e:print("skip",stamp,e)
    if not images:raise SystemExit("No radar images downloaded")
    motion=estimate_motion(images,masks,stamps);radar_dt=parse_stamp(stamps[-1])
    age=max(0,(datetime.now(BKK)-radar_dt).total_seconds()/60)
    districts=[]
    for d in DISTRICTS:
        row=analyze_district(images[-1],masks,motion,d,open_meteo(d))
        if age>45:
            row["confidence"]=max(25,row["confidence"]-20);row["risk"]=max(0,row["risk"]-8);row["level"]=risk_level(row["risk"])
        districts.append(row)
    top=max(districts,key=lambda x:x["risk"]);level=risk_level(top["risk"])
    eta="มีฝนแล้ว" if top["eta_min"]==0 else (f"คาดประมาณ {top['eta_min']} นาที" if top["eta_min"] is not None else "ยังไม่เห็นก้อนฝนเข้าถึงภายใน 120 นาที")
    sev={"light":"เบา","moderate":"ปานกลาง","heavy":"หนัก","very_heavy":"หนักมาก","none":"ยังไม่ชัด"}[top["severity"]]
    if top["risk"]>=70:summary=f"{top['name']}: {eta} • ฝน{sev} • ความมั่นใจ {top['confidence']}%"
    elif top["risk"]>=55:summary=f"จับตา {top['name']} • พบสัญญาณฝนบางส่วน แต่ยังต้องติดตามทิศทาง"
    else:summary="ภาพรวมสมุทรปราการยังไม่พบแนวฝนที่มีความเสี่ยงสูงใน 0–120 นาที"
    seed=f"{top['name']}|{level}|{top['eta_min']}|{top['severity']}|{motion.get('direction')}|{radar_dt:%Y%m%d%H}"
    status={"version":"8.2","generated_at":datetime.now(timezone.utc).isoformat(),
            "radar_time":radar_dt.astimezone(timezone.utc).isoformat(),"radar_age_min":round(age,1),
            "event_key":hashlib.sha1(seed.encode()).hexdigest()[:14],
            "province":{"risk":top["risk"],"level":level,"summary":summary,"top_district":top["name"]},
            "motion":{"available":bool(motion["available"]),"direction":motion["direction"],
                      "speed_kmh":round(float(motion["speed_kmh"]),1),"correlation":round(float(motion["score"]),2)},
            "districts":districts,
            "sources":{"tmd_satda":SATDA,"tmd_radar_frame":urls[-1],
                       "tmd_suvarnabhumi":"https://weather.tmd.go.th/svp120.php",
                       "open_meteo":"https://open-meteo.com/"},
            "note":"Automated nowcast aid; official warnings should be checked with TMD."}
    state=read_json(STATE_PATH,{})
    pushed,state=send_onesignal(status,state)
    STATE_PATH.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    wrote=maybe_write_status(status)
    print(json.dumps({"status_written":wrote,"radar_time":status["radar_time"],"top":top["name"],"risk":top["risk"],"push":pushed},ensure_ascii=False))

if __name__=="__main__":main()
