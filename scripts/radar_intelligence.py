#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# NongFon Samut Prakan - v10 Final Accuracy Engine
# Radar + Himawari IR + point forecast. Automated nowcast aid; official warnings should be checked with TMD.

from __future__ import annotations
import io, os, re, json, math, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin
from email.utils import parsedate_to_datetime
import numpy as np
import requests
from PIL import Image
from zoneinfo import ZoneInfo

BKK=ZoneInfo("Asia/Bangkok")
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"; DATA.mkdir(exist_ok=True)
STATUS_PATH=DATA/"radar_status.json"; STATE_PATH=DATA/"radar_state.json"; ALERT_HISTORY_PATH=DATA/"alert_history.json"
VERIFICATION_PATH=DATA/"verification.json"; DECISION_LOG_PATH=DATA/"decision_log.json"
SATDA="https://satda.tmd.go.th/"
COMPOSITE_PAGE="https://satda.tmd.go.th/wp-content/uploads/data/radar_composite/radar_composite.php"
SVP_LATEST="https://weather.tmd.go.th/svp/svp120_latest.jpg"
RADAR_STATIONS=[
    {
        "id":"nongchok","name":"หนองจอก","short":"หนองจอก","priority":1,
        "latest_url":"https://weather.tmd.go.th/pic_bmanck.jpg",
        "loop_url":"https://weather.tmd.go.th/pic_bmancLoop.gif",
        "page_url":"https://weather.tmd.go.th/bma_nck.php",
        "embedded_clock":"TST","embedded_to_thailand_hours":0
    },
    {
        "id":"nongkhame","name":"หนองแขม","short":"หนองแขม","priority":2,
        "latest_url":"https://weather.tmd.go.th/pic_bmankm.jpg",
        "loop_url":"https://weather.tmd.go.th/pic_bmankLoop.gif",
        "page_url":"https://weather.tmd.go.th/bma_nkm.php",
        "embedded_clock":"TST","embedded_to_thailand_hours":0
    },
    {
        "id":"suvarnabhumi","name":"สุวรรณภูมิ","short":"สุวรรณภูมิ","priority":3,
        "latest_url":"https://weather.tmd.go.th/svp/svp120_latest.jpg",
        "loop_url":"https://weather.tmd.go.th/svp/svp120loop.gif",
        "page_url":"https://weather.tmd.go.th/svp120.php",
        "embedded_clock":"UTC","embedded_to_thailand_hours":7
    },
]
JMA_TARGET="https://www.jma.go.jp/bosai/himawari/data/satimg/targetTimes_fd.json"
UA={"User-Agent":"NongFon-SamutPrakan/10.0-FinalAccuracy"}

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
    # อ่านหน้า Radar Composite โดยตรงก่อน เพื่อลดโอกาสติดรายการเก่าจากหน้าแรก SATDA
    items={}
    for page in (COMPOSITE_PAGE,SATDA):
        try:
            html=get(page).text
            pattern=r'(?:href|src)=["\']([^"\']*radar_composite/max/(\d{12})\.png)["\']'
            for h,s in re.findall(pattern,html,flags=re.I):
                items[s]=urljoin(page,h)
            for s in re.findall(r'(?<!\d)(\d{12})\.png',html):
                items.setdefault(s,f"https://satda.tmd.go.th/wp-content/uploads/data/radar_composite/max/{s}.png")
        except Exception as e:
            print("radar page error",page,type(e).__name__,str(e)[:100])
    if not items:
        raise RuntimeError("No radar composite frames found")
    ss=sorted(items)[-limit:]
    return [(s,items[s]) for s in ss]

def _probe_station_by_hash(spec,health_state):
    """Verify station freshness by detecting image-content changes across runs.
    This works even when TMD/BMA omits Last-Modified.
    First sighting is 'warming' and is not trusted as live until a later run sees a new image hash.
    """
    now=datetime.now(timezone.utc)
    base={k:spec[k] for k in ("id","name","short","priority","latest_url","loop_url","page_url","embedded_clock","embedded_to_thailand_hours")}
    try:
        r=get(spec["latest_url"],timeout=20)
        body=r.content
        digest=hashlib.sha1(body).hexdigest()
        prev=health_state.get(spec["id"],{}) if isinstance(health_state,dict) else {}
        prev_hash=prev.get("hash")
        changed_at=prev.get("changed_at")
        verified=bool(prev.get("verified",False))

        if prev_hash and digest!=prev_hash:
            changed_at=now.isoformat()
            verified=True
        elif not prev_hash:
            # first observation: record but don't trust it as live yet
            changed_at=now.isoformat()
            verified=False

        try:
            changed_dt=datetime.fromisoformat(changed_at) if changed_at else now
            if changed_dt.tzinfo is None: changed_dt=changed_dt.replace(tzinfo=timezone.utc)
        except Exception:
            changed_dt=now

        unchanged_min=max(0,(now-changed_dt.astimezone(timezone.utc)).total_seconds()/60)

        if not verified:
            status="warming" if unchanged_min<=20 else "old"
        else:
            status="online" if unchanged_min<=30 else "stale" if unchanged_min<=90 else "old"

        health_state[spec["id"]]={
            "hash":digest,
            "changed_at":changed_at,
            "verified":verified,
            "bytes":len(body)
        }
        return {**base,
                "available":True,
                "time":None,
                "status":status,
                "age_min":round(unchanged_min,1),
                "verified_change":verified,
                "changed_at":changed_at,
                "content_length":len(body),
                "method":"image hash change across workflow runs"}
    except Exception as e:
        return {**base,"available":False,"time":None,"status":"unavailable","age_min":None,
                "verified_change":False,"error":f"{type(e).__name__}: {str(e)[:120]}"}

def multi_radar_meta(radar_dt,composite_url,state):
    health_state=state.setdefault("radar_health",{})
    stations=[_probe_station_by_hash(s,health_state) for s in RADAR_STATIONS]

    # Only a station with a verified changing image can become primary.
    online=[s for s in stations if s.get("status")=="online" and s.get("verified_change")]
    if online:
        selected=sorted(online,key=lambda s:s["priority"])[0].copy()
        reason="verified_priority_online"
    else:
        # Composite has an explicit filename clock and remains the safe fallback.
        age=max(0,(datetime.now(BKK)-radar_dt.astimezone(BKK)).total_seconds()/60)
        selected={
            "id":"composite","name":"TMD Composite","short":"Composite","priority":9,
            "latest_url":composite_url,"loop_url":None,
            "page_url":COMPOSITE_PAGE,"embedded_clock":"UTC annotation / Thai filename",
            "embedded_to_thailand_hours":0,
            "available":True,"time":radar_dt.astimezone(timezone.utc).isoformat(),
            "status":"online" if age<=30 else "stale" if age<=90 else "old",
            "age_min":round(age,1),"method":"composite filename Thailand local time"
        }
        reason="composite_until_station_verified"

    return {
        "mode":"auto_failover_verified",
        "priority":["nongchok","nongkhame","suvarnabhumi","composite"],
        "selected":selected,
        "selection_reason":reason,
        "stations":stations,
        "analysis_source":"TMD Radar Composite",
        "analysis_note":"Station radar freshness is verified by image hash changes. Risk/ETA remain based on calibrated TMD Composite + Himawari + forecast."
    }

# TMD Radar Composite filename timestamp follows Thailand local clock; image annotation may show UTC separately.
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

def _parse_local_time(s):
    try:return datetime.fromisoformat(s).replace(tzinfo=BKK)
    except Exception:return None

def open_meteo(d):
    url="https://api.open-meteo.com/v1/forecast"
    params={
        "latitude":d["lat"],"longitude":d["lon"],
        "current":"temperature_2m,precipitation,rain,showers",
        "minutely_15":"precipitation",
        "hourly":"precipitation_probability,precipitation",
        "forecast_days":2,"timezone":"Asia/Bangkok"
    }
    try:
        x=requests.get(url,params=params,headers=UA,timeout=18).json()
        now=datetime.now(BKK)
        mt=x.get("minutely_15",{}).get("time",[]); mv=x.get("minutely_15",{}).get("precipitation",[])
        mins=[]
        for t,v in zip(mt,mv):
            dt=_parse_local_time(t)
            if dt and now-timedelta(minutes=20)<=dt<=now+timedelta(minutes=130): mins.append((dt,float(v or 0)))
        ht=x.get("hourly",{}).get("time",[]); hp=x.get("hourly",{}).get("precipitation_probability",[]); hm=x.get("hourly",{}).get("precipitation",[])
        hrs=[]
        for t,p,mm in zip(ht,hp,hm):
            dt=_parse_local_time(t)
            if dt and now-timedelta(minutes=30)<=dt<=now+timedelta(hours=3): hrs.append((dt,float(p or 0),float(mm or 0)))
        def maxprob(minutes):
            vals=[p for dt,p,_ in hrs if dt<=now+timedelta(minutes=minutes)]
            return max(vals or [0])
        def summm(minutes):
            vals=[v for dt,v in mins if now-timedelta(minutes=16)<=dt<=now+timedelta(minutes=minutes)]
            return sum(vals)
        cur=x.get("current",{})
        return {
            "ok":True,
            "prob_60":maxprob(60),"prob_120":maxprob(120),
            "sum_60_mm":summm(60),"sum_120_mm":summm(120),
            "max_prob":maxprob(120),"sum_2h_mm":summm(120),
            "current_precip_mm":float(cur.get("precipitation") or 0),
            "current_rain_mm":float(cur.get("rain") or 0),
            "max_hourly_mm":max([mm for _,_,mm in hrs] or [0])
        }
    except Exception as e:
        print("forecast error",d.get("name"),type(e).__name__,str(e)[:100])
        return {"ok":False,"prob_60":0,"prob_120":0,"sum_60_mm":0,"sum_120_mm":0,"max_prob":0,"sum_2h_mm":0,"current_precip_mm":0,"current_rain_mm":0,"max_hourly_mm":0}

def sev_name(s):return {0:"none",1:"light",2:"moderate",3:"heavy",4:"very_heavy"}.get(int(s),"none")
def risk_level(r):return "severe" if r>=85 else "high" if r>=70 else "watch" if r>=55 else "normal"

def analyze_district(img,masks,motion,d,meteo):
    latest=masks[-1]; x,y=plot_xy(img,d["lat"],d["lon"]); recent=[sample(m,x,y,7) for m in masks[-4:]]
    now=recent[-1]
    wet_frames=sum(1 for q in recent if q["density"]>=0.035 and q["strength"]>0)
    persistent=wet_frames>=2
    radar_observed=now["density"]>=0.028 and now["strength"]>0
    strong_now=now["strength"]>=3 and now["density"]>=0.025
    eta=None; best=now["strength"] if (persistent or radar_observed or strong_now) else 0; approach_density=0.0
    if radar_observed and best>0: eta=0
    elif motion["available"] and motion.get("score",0)>=0.34:
        frame=max(5,float(motion.get("frame_minutes",15))); hits=[]
        for lead in range(15,121,15):
            n=lead/frame; q=sample(latest,x-motion["dx"]*n,y-motion["dy"]*n,8)
            if q["density"]>=0.04 and q["strength"]>0:hits.append((lead,q))
        if hits:
            eta=hits[0][0];best=max(q["strength"] for _,q in hits[:2]);approach_density=max(q["density"] for _,q in hits[:2])
    if eta==0:risk={1:58,2:72,3:84,4:92}.get(best,45)
    elif eta is not None and eta<=30:risk={1:55,2:68,3:80,4:89}.get(best,45)
    elif eta is not None and eta<=60:risk={1:51,2:63,3:75,4:85}.get(best,42)
    elif eta is not None:risk={1:46,2:58,3:69,4:79}.get(best,38)
    else:risk=18
    fp=float(meteo.get("prob_60",meteo.get("max_prob",0)))
    f2=float(meteo.get("prob_120",meteo.get("max_prob",0)))
    sum60=float(meteo.get("sum_60_mm",0)); sum120=float(meteo.get("sum_120_mm",meteo.get("sum_2h_mm",0)))
    if fp>=80:risk+=6
    elif fp>=60:risk+=3
    if sum60>=1.5:risk+=5
    elif sum60>=0.4:risk+=3
    if eta is None and f2>=85 and sum120>=0.8:risk=max(risk,52)
    conf=28+(18 if persistent else 0)+(12 if radar_observed else 0)+(10 if eta is not None and eta>0 else 0)
    if motion["available"]:conf+=min(20,max(0,(motion.get("score",0)-0.25)*45))
    if fp>=60 or sum60>=0.4:conf+=8
    conf=min(94,conf);risk=int(max(0,min(100,round(risk))))
    return {**d,
        "raw_radar_risk":risk,"risk":risk,"level":risk_level(risk),"eta_min":eta,"severity":sev_name(best),"confidence":int(round(conf)),
        "radar_observed":bool(radar_observed),"radar_density":round(float(now["density"]),3),"radar_strength":int(now["strength"]),
        "radar_persistence_frames":wet_frames,"radar_approach_density":round(float(approach_density),3),
        "motion_correlation":round(float(motion.get("score",0)),2),
        "forecast_ok":bool(meteo.get("ok")),"forecast_probability":round(f2),"forecast_prob_60":round(fp),"forecast_prob_120":round(f2),
        "forecast_60_mm":round(sum60,2),"forecast_2h_mm":round(sum120,2),"forecast_current_mm":round(float(meteo.get("current_precip_mm",0)),2)
    }

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

def freshness_score(age,good,warn,bad):
    if age is None:return 25
    age=float(age)
    if age<=good:return 100
    if age<=warn:return int(round(100-(age-good)/(warn-good)*35))
    if age<=bad:return int(round(65-(age-warn)/(bad-warn)*45))
    return 0

def quality_score(radar_age,sat_health,motion,multi_meta,meteo):
    radar_q=freshness_score(radar_age,20,45,90)
    sat_q=freshness_score(sat_health.get("age_min"),35,70,120) if sat_health.get("status")!="unavailable" else 20
    corr=float(motion.get("score",0) or 0)
    motion_q=int(np.clip(35+(corr-0.25)*110,25,100)) if motion.get("available") else 35
    stations=multi_meta.get("stations",[])
    online=sum(1 for s in stations if s.get("status")=="online" and s.get("verified_change"))
    station_q=100 if online>=2 else 82 if online==1 else 50 if any(s.get("available") for s in stations) else 20
    forecast_q=100 if meteo.get("ok") else 20
    q=int(round(.50*radar_q+.15*sat_q+.10*motion_q+.10*station_q+.15*forecast_q))
    if radar_age>90:q=min(q,30)
    elif radar_age>60:q=min(q,45)
    return int(np.clip(q,0,100)),{"radar":radar_q,"satellite":sat_q,"motion":motion_q,"stations":station_q,"forecast":forecast_q}

def load_verification():
    v=read_json(VERIFICATION_PATH,{})
    if not isinstance(v,dict):v={}
    v.setdefault("pending",[]);v.setdefault("last_prediction_at",{});v.setdefault("metrics",{})
    for h in (15,30,60):
        v["metrics"].setdefault(str(h),{"n":0,"hits":0,"misses":0,"false_alarms":0,"correct_negatives":0,"brier_sum":0.0,"sum_pred":0.0,"sum_obs":0})
    return v

def calibration_offsets(v):
    out={15:0.0,30:0.0,60:0.0}
    for h in out:
        m=v.get("metrics",{}).get(str(h),{});n=int(m.get("n",0) or 0)
        if n>=80:
            pred=float(m.get("sum_pred",0))/n;obs=float(m.get("sum_obs",0))/n
            out[h]=float(np.clip((obs-pred)*50,-8,8))
    return out

def probability_model(row,sat,motion,data_quality,quality_parts,offsets):
    eta=row.get("eta_min");sev={"none":0,"light":1,"moderate":2,"heavy":3,"very_heavy":4}.get(row.get("severity"),0)
    if row.get("radar_observed"):
        rp15=88+sev*2;rp30=92+sev;rp60=95
    elif eta is not None and eta<=15:rp15,rp30,rp60=82,88,93
    elif eta is not None and eta<=30:rp15,rp30,rp60=64,80,90
    elif eta is not None and eta<=60:rp15,rp30,rp60=36,60,82
    elif eta is not None and eta<=120:rp15,rp30,rp60=16,36,64
    else:rp15,rp30,rp60=8,16,26
    persist=min(9,int(row.get("radar_persistence_frames",0))*3)
    density=min(8,float(row.get("radar_density",0))*70)
    rp15=min(100,rp15+persist+density);rp30=min(100,rp30+persist+density);rp60=min(100,rp60+persist/2)
    f60=float(row.get("forecast_prob_60",0));f120=float(row.get("forecast_prob_120",row.get("forecast_probability",0)))
    sat_score=float(sat.get("satellite_score") or 0) if sat.get("satellite_status")=="online" else 0
    p15=.72*rp15+.20*f60+.08*sat_score
    p30=.65*rp30+.27*f60+.08*sat_score
    p60=.55*rp60+.37*f120+.08*sat_score
    if float(row.get("forecast_60_mm",0))>=0.4:p15+=2;p30+=4;p60+=4
    if float(row.get("forecast_60_mm",0))>=1.5:p15+=3;p30+=3;p60+=3
    if float(row.get("forecast_current_mm",0))>=0.1:p15+=5;p30+=4
    if sat.get("satellite_trend")=="growing" and sat_score>=40:p15+=2;p30+=3;p60+=3
    if quality_parts.get("radar",100)<50:
        p15*=.65;p30*=.75;p60*=.88
    p15+=offsets.get(15,0);p30+=offsets.get(30,0);p60+=offsets.get(60,0)
    p15=int(round(np.clip(p15,0,99)));p30=int(round(np.clip(max(p30,p15),0,99)));p60=int(round(np.clip(max(p60,p30),0,99)))
    radar_signal=bool(row.get("radar_observed") or (eta is not None and eta<=60) or row.get("raw_radar_risk",0)>=55)
    forecast_signal=bool(f60>=60 or float(row.get("forecast_60_mm",0))>=0.4)
    sat_signal=bool(sat.get("satellite_status")=="online" and sat_score>=45)
    sources=sum((radar_signal,forecast_signal,sat_signal))
    agreement=95 if sources>=2 else 70 if sources==1 else 40
    if radar_signal and forecast_signal and abs(rp30-f60)<=25:agreement=min(100,agreement+5)
    model_conf=int(round(.65*data_quality+.35*agreement))
    row.update({
        "p15":p15,"p30":p30,"p60":p60,"data_quality":int(data_quality),"quality_parts":quality_parts,
        "model_confidence":int(np.clip(model_conf,0,99)),"evidence_sources":int(sources),
        "evidence":{"radar":radar_signal,"forecast":forecast_signal,"satellite":sat_signal,"persistence":row.get("radar_persistence_frames",0)>=2}
    })
    # Keep legacy confidence for old UI but make it reflect v10 confidence.
    row["confidence"]=row["model_confidence"]
    return row

def candidate_tier(d):
    q=int(d.get("data_quality",0));p15=int(d.get("p15",0));p30=int(d.get("p30",0));p60=int(d.get("p60",0));sources=int(d.get("evidence_sources",0))
    radar=bool(d.get("evidence",{}).get("radar"));sev=d.get("severity")
    if q<45:return None,"คุณภาพข้อมูลต่ำ จึงงด Push"
    if sev in ("heavy","very_heavy") and d.get("radar_observed") and p30>=70:return "severe","พบฝนหนักเหนือพื้นที่จากเรดาร์"
    if p15>=80 and radar and sources>=2 and q>=55:return "imminent","ฝนมีโอกาสสูงมากภายใน 15 นาทีและมีหลักฐานอย่างน้อย 2 แหล่ง"
    if d.get("eta_min") is not None and d.get("eta_min")<=15 and d.get("raw_radar_risk",0)>=60 and d.get("forecast_prob_60",0)>=60 and q>=55:return "imminent","เรดาร์ชี้แนวฝนใกล้ถึงและพยากรณ์สนับสนุน"
    if p30>=70 and sources>=2 and q>=50:return "prepare","โอกาสฝน 30 นาทีสูงและมีหลักฐานอย่างน้อย 2 แหล่ง"
    if p60>=55 and q>=45 and (radar or d.get("forecast_prob_60",0)>=75):return "early","มีโอกาสฝนใน 60 นาที ควรเริ่มเฝ้าระวัง"
    return None,"ยังไม่ถึงเกณฑ์แจ้งเตือน"

def tier_rank(t):return {None:0,"early":1,"prepare":2,"imminent":3,"severe":4}.get(t,0)
def tier_label(t):return {"early":"เฝ้าระวัง","prepare":"เตรียมรับฝน","imminent":"ฝนใกล้ถึง","severe":"ฝนหนัก/รุนแรง"}.get(t,"ปกติ")
def sev_rank(s):return {"none":0,"light":1,"moderate":2,"heavy":3,"very_heavy":4}.get(s,0)

def append_json_log(path,item,limit=300):
    rows=read_json(path,[])
    if not isinstance(rows,list):rows=[]
    rows.append(item);rows=rows[-limit:]
    path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")

def verification_summary(v):
    out={"method":"radar-observed proxy","note":"ใช้ Radar Composite ณ เวลาเป้าหมายเป็นตัวแทนเหตุการณ์ฝน เพื่อปรับ calibration ภายในระบบ"}
    for h in (15,30,60):
        m=v.get("metrics",{}).get(str(h),{});n=int(m.get("n",0) or 0);hit=int(m.get("hits",0));miss=int(m.get("misses",0));fa=int(m.get("false_alarms",0))
        out[str(h)]={"n":n,"pod":round(hit/(hit+miss),3) if hit+miss else None,"far":round(fa/(hit+fa),3) if hit+fa else None,"csi":round(hit/(hit+miss+fa),3) if hit+miss+fa else None,"brier":round(float(m.get("brier_sum",0))/n,4) if n else None}
    out["calibration_offset_pp"]={str(k):round(vv,1) for k,vv in calibration_offsets(v).items()}
    return out

def update_verification(v,districts,now):
    by={d["name"]:d for d in districts};keep=[]
    for p in v.get("pending",[]):
        try:target=datetime.fromisoformat(p["target_time"]);target=target if target.tzinfo else target.replace(tzinfo=timezone.utc)
        except Exception:continue
        if now<target:keep.append(p);continue
        lag=(now-target).total_seconds()/60
        if lag>25:continue
        d=by.get(p.get("district"))
        if not d:continue
        obs=1 if d.get("radar_observed") else 0;prob=float(p.get("prob",0))/100;h=str(p.get("horizon"));m=v["metrics"][h]
        m["n"]+=1;m["brier_sum"]+=float((prob-obs)**2);m["sum_pred"]+=prob;m["sum_obs"]+=obs
        pred=prob>=.5
        if pred and obs:m["hits"]+=1
        elif pred and not obs:m["false_alarms"]+=1
        elif not pred and obs:m["misses"]+=1
        else:m["correct_negatives"]+=1
    v["pending"]=keep
    last=v.setdefault("last_prediction_at",{})
    for d in districts:
        key=d["name"]
        try:ldt=datetime.fromisoformat(last.get(key,""));ldt=ldt if ldt.tzinfo else ldt.replace(tzinfo=timezone.utc)
        except Exception:ldt=None
        if ldt and (now-ldt).total_seconds()<12*60:continue
        for h,field in ((15,"p15"),(30,"p30"),(60,"p60")):
            v["pending"].append({"district":key,"horizon":h,"prob":int(d.get(field,0)),"created_at":now.isoformat(),"target_time":(now+timedelta(minutes=h)).isoformat()})
        last[key]=now.isoformat()
    v["pending"]=v["pending"][-800:];v["updated_at"]=now.isoformat()
    VERIFICATION_PATH.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding="utf-8")
    return v

def append_alert_history(status,d,tier,push_status,error=None):
    history=read_json(ALERT_HISTORY_PATH,[])
    if not isinstance(history,list):history=[]
    item={"time":datetime.now(timezone.utc).isoformat(),"event_key":status.get("event_key"),"district":d.get("name"),"tier":tier,
          "risk":d.get("risk"),"p15":d.get("p15"),"p30":d.get("p30"),"p60":d.get("p60"),"confidence":d.get("model_confidence"),
          "data_quality":d.get("data_quality"),"eta_min":d.get("eta_min"),"severity":d.get("severity"),"satellite_score":d.get("satellite_score"),
          "push_status":push_status,"status":push_status,"error":error}
    history.append(item);ALERT_HISTORY_PATH.write_text(json.dumps(history[-300:],ensure_ascii=False,indent=2),encoding="utf-8")

def _parse_dt(s):
    try:
        d=datetime.fromisoformat(s);return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:return None

def push_decision(status,state):
    app=os.getenv("ONESIGNAL_APP_ID","").strip();key=os.getenv("ONESIGNAL_REST_API_KEY","").strip();site=os.getenv("SITE_URL","").strip() or "https://jeerapat2010-cpu.github.io/nongfon-samutprakan/"
    now=datetime.now(timezone.utc);engine=state.setdefault("alert_engine",{});district_state=engine.setdefault("districts",{})
    candidates=[]
    for d in status.get("districts",[]):
        tier,reason=candidate_tier(d);d["alert_candidate"]=tier;d["alert_reason"]=reason
        ds=district_state.setdefault(d["name"],{"active_tier":None,"below_count":0})
        prev=ds.get("active_tier")
        if tier_rank(tier)<tier_rank(prev):
            ds["below_count"]=int(ds.get("below_count",0))+1
            if ds["below_count"]>=2:ds["active_tier"]=tier;ds["below_count"]=0
        else:
            ds["active_tier"]=tier;ds["below_count"]=0
        if tier:candidates.append((tier_rank(tier),d.get("p15",0),d.get("p30",0),d,tier,reason))
    candidates.sort(key=lambda x:(x[0],x[1],x[2]),reverse=True)
    decision={"time":now.isoformat(),"configured":bool(app and key),"action":"none","reason":"ยังไม่มีพื้นที่ถึงเกณฑ์ Push","tier":None,"district":None}
    if not candidates:
        status["push_engine"]={"configured":bool(app and key),"decision":decision,"policy":"v10 probabilistic + consensus + hysteresis"};append_json_log(DECISION_LOG_PATH,decision);return False,state
    _,_,_,d,tier,reason=candidates[0];decision.update({"tier":tier,"district":d["name"],"reason":reason,"p15":d["p15"],"p30":d["p30"],"p60":d["p60"],"data_quality":d["data_quality"],"model_confidence":d["model_confidence"]})
    ds=district_state[d["name"]];last_at=_parse_dt(ds.get("last_push_at",""));last_tier=ds.get("last_push_tier");elapsed=(now-last_at).total_seconds()/60 if last_at else 9999
    cooldown={"early":60,"prepare":60,"imminent":30,"severe":20}[tier]
    escalated=tier_rank(tier)>tier_rank(last_tier)
    eta=d.get("eta_min");last_eta=ds.get("last_eta");meaningful=(int(d.get("p15",0))-int(ds.get("last_p15",0) or 0)>=15) or (eta is not None and last_eta is not None and eta<=max(0,last_eta-15)) or sev_rank(d.get("severity"))>sev_rank(ds.get("last_severity"))
    if not app or not key:
        decision.update({"action":"suppressed","reason":"OneSignal credential ไม่พร้อม"});status["push_engine"]={"configured":False,"decision":decision,"policy":"v10 probabilistic + consensus + hysteresis"};append_json_log(DECISION_LOG_PATH,decision);return False,state
    if not escalated and last_at and elapsed<cooldown and not (meaningful and elapsed>=15):
        decision.update({"action":"suppressed","reason":f"ผ่านเกณฑ์ แต่ยังอยู่ใน cooldown อีกประมาณ {max(1,round(cooldown-elapsed))} นาที","cooldown_remaining_min":max(0,round(cooldown-elapsed))})
        status["push_engine"]={"configured":True,"decision":decision,"policy":"v10 probabilistic + consensus + hysteresis"};append_json_log(DECISION_LOG_PATH,decision);return False,state
    icon={"early":"🌦️","prepare":"☔","imminent":"🌧️","severe":"🚨"}[tier];title=f"{icon} เฝ้าน้องฝน: {tier_label(tier)} • {d['short']}"
    eta_txt="มีฝนแล้ว" if eta==0 else (f"~{eta} นาที" if eta is not None else "ภายใน 60 นาที")
    body=f"P15 {d['p15']}% • P30 {d['p30']}% • P60 {d['p60']}% • {eta_txt} • เชื่อมั่น {d['model_confidence']}%"
    payload={"app_id":app,"target_channel":"push","included_segments":["Subscribed Users"],"headings":{"en":title,"th":title},"contents":{"en":body,"th":body},"url":site,"data":{"event_key":status.get("event_key"),"tier":tier,"district":d["name"]}}
    try:
        r=requests.post("https://api.onesignal.com/notifications",headers={"Authorization":f"Key {key}","Content-Type":"application/json"},json=payload,timeout=20)
        if r.ok:
            ds.update({"last_push_at":now.isoformat(),"last_push_tier":tier,"last_p15":d["p15"],"last_eta":eta,"last_severity":d.get("severity")})
            decision.update({"action":"sent","reason":"ส่ง Push ตามเกณฑ์ v10 สำเร็จ"});append_alert_history(status,d,tier,"sent")
            status["push_engine"]={"configured":True,"decision":decision,"policy":"v10 probabilistic + consensus + hysteresis"};append_json_log(DECISION_LOG_PATH,decision);return True,state
        err=f"{r.status_code}: {r.text[:180]}";decision.update({"action":"failed","reason":"OneSignal ตอบกลับผิดพลาด","error":err});append_alert_history(status,d,tier,"failed",err)
    except Exception as e:
        err=f"{type(e).__name__}: {str(e)[:180]}";decision.update({"action":"failed","reason":"ส่ง OneSignal ไม่สำเร็จ","error":err});append_alert_history(status,d,tier,"failed",err)
    status["push_engine"]={"configured":True,"decision":decision,"policy":"v10 probabilistic + consensus + hysteresis"};append_json_log(DECISION_LOG_PATH,decision);return False,state

def maybe_write_status(status):
    old=read_json(STATUS_PATH,{})
    def sig(x):
        return {"version":x.get("version"),"province":x.get("province"),"push_engine":x.get("push_engine"),"system_health":x.get("system_health"),
                "verification":x.get("verification"),"station_radar":x.get("station_radar"),
                "satellite":{k:x.get("satellite",{}).get(k) for k in ("status","age_min","time","top_score","trend")},
                "districts":[{k:d.get(k) for k in ("name","risk","p15","p30","p60","data_quality","model_confidence","alert_candidate","eta_min","severity") } for d in x.get("districts",[])]}
    changed=sig(status)!=sig(old)
    try:heartbeat=(datetime.now(timezone.utc)-datetime.fromisoformat(old.get("generated_at",""))).total_seconds()>=600
    except Exception:heartbeat=True
    if changed or heartbeat or not STATUS_PATH.exists():
        STATUS_PATH.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding="utf-8");return True
    return False

def main():
    run_started=datetime.now(timezone.utc);state=read_json(STATE_PATH,{});verification=load_verification();offsets=calibration_offsets(verification)
    frames=scrape_frames(8)
    if not frames:raise SystemExit("No TMD composite frames found")
    stamps=[];urls=[];images=[];masks=[]
    for stamp,url in frames:
        try:
            im=load_image(url);stamps.append(stamp);urls.append(url);images.append(im);masks.append(rain_strength(im))
        except Exception as e:print("skip radar",stamp,e)
    if not images:raise SystemExit("No radar images downloaded")
    motion=estimate_motion(images,masks,stamps);radar_dt=parse_stamp(stamps[-1]);radar_age=max(0,(datetime.now(BKK)-radar_dt).total_seconds()/60)
    multi_meta=multi_radar_meta(radar_dt,urls[-1],state);station_meta=multi_meta["selected"]
    sframes,slatest,sat_health=satellite_frames();sat_dt=None
    if slatest:
        try:sat_dt=datetime.strptime(slatest["validtime"],"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except Exception:pass
    districts=[]
    for d in DISTRICTS:
        meteo=open_meteo(d);row=analyze_district(images[-1],masks,motion,d,meteo);sat=sat_score_for_district(sframes,d,sat_health);row=fuse(row,sat)
        q,parts=quality_score(radar_age,sat_health,motion,multi_meta,meteo);row=probability_model(row,sat,motion,q,parts,offsets)
        if radar_age>60:
            row["risk"]=max(0,row["risk"]-10);row["level"]=risk_level(row["risk"])
        districts.append(row)
    now=datetime.now(timezone.utc);verification=update_verification(verification,districts,now);vsummary=verification_summary(verification)
    # Recompute alert candidate after verification/calibration state update (probabilities remain from previous offsets by design, preventing feedback jumps within a run).
    for d in districts:
        t,r=candidate_tier(d);d["alert_candidate"]=t;d["alert_reason"]=r
    top=max(districts,key=lambda x:(tier_rank(x.get("alert_candidate")),x.get("p30",0),x.get("risk",0)))
    level=risk_level(top["risk"]);eta="มีฝนแล้ว" if top["eta_min"]==0 else (f"คาดประมาณ {top['eta_min']} นาที" if top["eta_min"] is not None else "ยังไม่เห็นก้อนฝนเข้าถึงภายใน 120 นาที")
    if top.get("alert_candidate"):summary=f"{ {'early':'🌦️','prepare':'☔','imminent':'🌧️','severe':'🚨'}[top['alert_candidate']] } {tier_label(top['alert_candidate'])} {top['name']} • P30 {top['p30']}% • {eta}"
    else:summary=f"✅ ยังไม่ถึงเกณฑ์ Push • พื้นที่สูงสุด {top['name']} P30 {top['p30']}%"
    seed=f"{top['name']}|{top.get('alert_candidate')}|{top['p15']}|{top['p30']}|{radar_dt:%Y%m%d%H%M}"
    system_state="healthy" if radar_age<=45 else "degraded" if radar_age<=90 else "stale"
    status={
        "version":"10.0 FinalAccuracy","generated_at":now.isoformat(),"run_started_at":run_started.isoformat(),"radar_time":radar_dt.astimezone(timezone.utc).isoformat(),"radar_age_min":round(radar_age,1),"event_key":hashlib.sha1(seed.encode()).hexdigest()[:14],
        "province":{"risk":top["risk"],"level":level,"summary":summary,"top_district":top["name"],"p15":top["p15"],"p30":top["p30"],"p60":top["p60"],"data_quality":top["data_quality"],"model_confidence":top["model_confidence"]},
        "system_health":{"state":system_state,"radar_fresh":radar_age<=45,"radar_age_min":round(radar_age,1),"satellite_state":sat_health.get("status"),"satellite_age_min":sat_health.get("age_min"),"workflow_expected_minutes":5,"stale_push_guard":True},
        "station_radar":station_meta,"multi_radar":multi_meta,
        "motion":{"available":bool(motion["available"]),"direction":motion["direction"],"speed_kmh":round(float(motion["speed_kmh"]),1),"correlation":round(float(motion["score"]),2)},
        "satellite":{"available":bool(sframes),"status":sat_health.get("status","unavailable"),"age_min":sat_health.get("age_min"),"frames":sat_health.get("frames",0),"provider":"JMA Himawari-8/9 B13 IR","time":sat_dt.isoformat() if sat_dt else None,"top_score":top.get("satellite_score"),"trend":top.get("satellite_trend")},
        "fusion":{"principle":"Probabilistic nowcast: calibrated TMD Composite + verified station health + Himawari support + current/future Open-Meteo","probability_horizons_min":[15,30,60],"push_levels":["early","prepare","imminent","severe"],"hysteresis_runs":2,"stale_radar_push_cutoff_min":60},
        "verification":vsummary,
        "time_reference":{"display_timezone":"Asia/Bangkok","display_utc_offset":"+07:00","radar_composite_source_timezone":"Asia/Bangkok","radar_composite_add_hours":0,"bma_radar_image_clock":"TST","bma_radar_image_to_thailand_hours":0,"tmd_station_image_clock":"UTC","tmd_station_image_to_thailand_hours":7,"jma_himawari_source_timezone":"UTC","jma_himawari_to_thailand_hours":7},
        "districts":districts,
        "sources":{"tmd_satda":SATDA,"tmd_radar_frame":urls[-1],"tmd_suvarnabhumi":"https://weather.tmd.go.th/svp120.php","tmd_satellite":"https://satda.tmd.go.th/tmd_satellite.php","jma_himawari":"https://www.jma.go.jp/bosai/map.html#contents=himawari","open_meteo":"https://open-meteo.com/"},
        "note":"Automated nowcast aid. Probabilities are continuously verified against a radar-observed proxy; official warnings should be checked with TMD."
    }
    pushed,state=push_decision(status,state)
    state["last_success_at"]=now.isoformat();state["engine_version"]="10.0 FinalAccuracy";STATE_PATH.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    wrote=maybe_write_status(status)
    print(json.dumps({"status_written":wrote,"version":status["version"],"radar_age":status["radar_age_min"],"top":top["name"],"p15":top["p15"],"p30":top["p30"],"p60":top["p60"],"candidate":top.get("alert_candidate"),"push":pushed},ensure_ascii=False))

if __name__=="__main__":main()
