#!/usr/bin/env python3
"""
น้องมายด์เฝ้าฝนและฝุ่น สมุทรปราการ - PM2.5 alert engine (v1.0)

ดึงค่า PM2.5 รายอำเภอจาก Open-Meteo Air Quality (แบบจำลอง CAMS) แล้วตัดสินใจว่าจะส่ง Push (OneSignal)
หรือไม่ โดยออกแบบให้ "ไม่เตือนรัว" และ "ไม่เตือนจากค่าที่กระโดดชั่วคราว":

  * ใช้ค่าเฉลี่ย 3 ชั่วโมงล่าสุดของแต่ละอำเภอ (กันค่ากระโดดชั่วโมงเดียว)
  * เกณฑ์ตามกรมควบคุมมลพิษ: >37.5 = เริ่มมีผลกระทบต่อสุขภาพ (ระดับ 3), >75 = มีผลกระทบต่อสุขภาพ (ระดับ 4)
  * Hysteresis: เมื่ออยู่ในระดับเตือนแล้ว ต้องลดต่ำกว่าเกณฑ์เกิน HYST_UGM3 จึงจะถือว่าออกจากระดับนั้น
  * ส่ง Push เมื่อ "ระดับของจังหวัดสูงขึ้น" (escalation) หรือยังสูงค้างครบ REMIND_HOURS ชม. (reminder)
  * ช่วงพัก 22:00-06:00 (เวลาไทย) ไม่ส่ง - ถ้ายังสูงอยู่จะส่งตอนเช้า
  * จำกัดไม่เกิน MAX_PUSH_PER_DAY ครั้งต่อวัน
  * ถ้าส่งไม่สำเร็จ จะไม่บันทึกว่า "เตือนแล้ว" เพื่อให้ลองใหม่รอบถัดไป

ไฟล์ที่เขียน: data/pm25_state.json (ภายใน) และ data/pm25_status.json (สาธารณะ ให้หน้าเว็บอ่าน)

ตัวแปรสภาพแวดล้อม:
  ONESIGNAL_APP_ID, ONESIGNAL_APP_API_KEY / ONESIGNAL_REST_API_KEY / ONESIGNAL_LEGACY_REST_API_KEY, SITE_URL
  PM25_DRY_RUN=true   พิมพ์ข้อความที่จะส่งแต่ไม่ส่งจริงและไม่เปลี่ยนสถานะการเตือน
  PM25_TEST_PUSH=true ส่งข้อความทดสอบหนึ่งครั้ง (ไม่เปลี่ยนสถานะการเตือน)
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BKK = timezone(timedelta(hours=7))
DATA = Path("data")
STATE_FILE = DATA / "pm25_state.json"
STATUS_FILE = DATA / "pm25_status.json"
UA = {"User-Agent": "NongFon-SamutPrakan-PM25/1.0"}
DEFAULT_SITE = "https://jeerapat2010-cpu.github.io/nongfon-samutprakan/"

DISTRICTS = [
    {"name": "เมืองสมุทรปราการ", "short": "เมืองฯ", "lat": 13.60056, "lon": 100.59667},
    {"name": "บางพลี", "short": "บางพลี", "lat": 13.60711, "lon": 100.70795},
    {"name": "บางบ่อ", "short": "บางบ่อ", "lat": 13.58513, "lon": 100.86470},
    {"name": "พระประแดง", "short": "พระประแดง", "lat": 13.65833, "lon": 100.53389},
    {"name": "พระสมุทรเจดีย์", "short": "พระสมุทรเจดีย์", "lat": 13.56861, "lon": 100.56167},
    {"name": "บางเสาธง", "short": "บางเสาธง", "lat": 13.59472, "lon": 100.83000},
]

# ระดับ 0..4 : ขอบบนของแต่ละระดับ (µg/m3) ตามเกณฑ์ PCD ค่าเฉลี่ย 24 ชม.
LEVEL_MAX = [15.0, 25.0, 37.5, 75.0, float("inf")]
LEVEL_LOWER = [0.0, 15.0, 25.0, 37.5, 75.0]  # ขอบล่างของระดับ (ใช้คิด hysteresis)
LEVEL_LABEL = ["ดีมาก", "ดี", "ปานกลาง", "เริ่มมีผลกระทบต่อสุขภาพ", "มีผลกระทบต่อสุขภาพ"]

ALERT_MIN_LEVEL = 3          # เตือนตั้งแต่ระดับ 3 ขึ้นไป
HYST_UGM3 = 3.0              # ต้องต่ำกว่าขอบล่างอย่างน้อยเท่านี้ จึงถือว่าออกจากระดับ
REMIND_HOURS = 12            # ยังสูงระดับเดิมค้าง -> เตือนซ้ำทุกกี่ชั่วโมง
MAX_PUSH_PER_DAY = 4         # เพดานจำนวน Push ต่อวัน (เวลาไทย)
QUIET_START_H, QUIET_END_H = 22, 6   # ช่วงพัก 22:00-06:00
SMOOTH_HOURS = 3             # เฉลี่ยกี่ชั่วโมงล่าสุดเพื่อตัดสินระดับ
HISTORY_KEEP = 20


# ----------------------------------------------------------------------------- helpers
def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_iso(s):
    try:
        t = datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def norm_key(raw):
    v = (raw or "").strip().strip('"').strip("'")
    return re.sub(r"^(?:key|basic)\s+", "", v, flags=re.I).strip()


def credential():
    for source, name in (
        ("app_api_key", "ONESIGNAL_APP_API_KEY"),
        ("rest_api_key", "ONESIGNAL_REST_API_KEY"),
        ("legacy_rest_api_key", "ONESIGNAL_LEGACY_REST_API_KEY"),
    ):
        k = norm_key(os.getenv(name, ""))
        if k:
            return source, k
    return None, ""


def level_of(v):
    for i, mx in enumerate(LEVEL_MAX):
        if v <= mx:
            return i
    return len(LEVEL_MAX) - 1


def level_with_hysteresis(v, prev):
    """ระดับใหม่ โดยถ้าเคยอยู่ระดับสูงกว่า จะลดลงเมื่อค่าต่ำกว่าขอบล่างของระดับเดิมเกิน HYST_UGM3 เท่านั้น"""
    lv = level_of(v)
    if prev is not None and 0 <= prev < len(LEVEL_LOWER) and lv < prev and v > LEVEL_LOWER[prev] - HYST_UGM3:
        return prev
    return lv


def in_quiet_hours(now):
    h = now.astimezone(BKK).hour
    return h >= QUIET_START_H or h < QUIET_END_H


# ----------------------------------------------------------------------------- data
def fetch_aq():
    """คืนรายการ payload ต่ออำเภอ (ตามลำดับ DISTRICTS)"""
    params = {
        "latitude": ",".join(str(d["lat"]) for d in DISTRICTS),
        "longitude": ",".join(str(d["lon"]) for d in DISTRICTS),
        "current": "pm2_5",
        "hourly": "pm2_5",
        "past_days": 1,
        "forecast_days": 2,
        "timezone": "Asia/Bangkok",
    }
    r = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality", params=params, headers=UA, timeout=25)
    r.raise_for_status()
    j = r.json()
    arr = j if isinstance(j, list) else [j]
    if len(arr) != len(DISTRICTS):
        raise ValueError(f"expected {len(DISTRICTS)} locations, got {len(arr)}")
    return arr


def summarize(payload, d, now):
    """สรุปค่าของหนึ่งอำเภอ: now / avg3 / avg24 / max6 (None ถ้าไม่มีข้อมูล)"""
    times = (payload.get("hourly") or {}).get("time") or []
    vals = (payload.get("hourly") or {}).get("pm2_5") or []
    past, nxt = [], []
    for ts, v in zip(times, vals):
        if v is None:
            continue
        try:
            t = datetime.fromisoformat(ts).replace(tzinfo=BKK)
        except Exception:
            continue
        if now - timedelta(hours=24) < t <= now:
            past.append((t, float(v)))
        elif now < t <= now + timedelta(hours=6):
            nxt.append(float(v))
    past.sort()
    cur = (payload.get("current") or {}).get("pm2_5")
    now_v = float(cur) if cur is not None else (past[-1][1] if past else None)
    if now_v is None:
        return None
    recent = [v for _, v in past[-SMOOTH_HOURS:]] or [now_v]
    avg3 = sum(recent) / len(recent)
    avg24 = sum(v for _, v in past) / len(past) if past else None
    return {
        "name": d["name"], "short": d["short"],
        "now": round(now_v, 1), "avg3": round(avg3, 1),
        "avg24": None if avg24 is None else round(avg24, 1),
        "max6": round(max(nxt), 1) if nxt else None,
    }


# ----------------------------------------------------------------------------- decision
def decide(province_level, state, now):
    """คืน (action, reason, kind) : action = send | skipped | suppressed | none"""
    notified = int(state.get("notified_level", 0) or 0)
    if province_level < ALERT_MIN_LEVEL:
        return "none", "ฝุ่นต่ำกว่าเกณฑ์แจ้งเตือน", None
    if province_level > notified:
        kind = "escalation"
    elif province_level == notified:
        last = parse_iso(state.get("last_push_at") or "")
        hrs = (now - last).total_seconds() / 3600 if last else 1e9
        if hrs >= REMIND_HOURS:
            kind = "reminder"
        else:
            return "skipped", "แจ้งเตือนระดับนี้ไปแล้ว", None
    else:
        return "skipped", "ระดับลดลงจากที่เคยแจ้ง ปรับสถานะเงียบ ๆ", None
    if in_quiet_hours(now):
        return "suppressed", f"ช่วงพัก {QUIET_START_H:02d}:00-{QUIET_END_H:02d}:00 จะส่งตอนเช้าหากยังสูง", kind
    day = now.astimezone(BKK).strftime("%Y-%m-%d")
    daily = state.get("daily") or {}
    if daily.get("date") == day and int(daily.get("count", 0)) >= MAX_PUSH_PER_DAY:
        return "suppressed", f"ครบโควตา {MAX_PUSH_PER_DAY} ครั้งต่อวันแล้ว", kind
    return "send", "ระดับสูงขึ้น" if kind == "escalation" else f"ยังสูงต่อเนื่องครบ {REMIND_HOURS} ชม.", kind


def build_message(rows, kind):
    lvl = max(r["level"] for r in rows)
    alerting = [r for r in rows if r["level"] >= ALERT_MIN_LEVEL]
    top = max(rows, key=lambda r: r["avg3"])
    icon = "🚨" if lvl >= 4 else "😷"
    title = f"{icon} น้องมายด์เตือนฝุ่น PM2.5: {LEVEL_LABEL[lvl]}"
    if kind == "reminder":
        title += " (ยังสูงต่อเนื่อง)"
    where = "ทั้ง 6 อำเภอ" if len(alerting) == len(DISTRICTS) else f"{len(alerting)}/6 อำเภอ ({', '.join(r['short'] for r in alerting)})"
    advice = "ทุกคนควรลดกิจกรรมกลางแจ้ง สวมหน้ากาก N95" if lvl >= 4 else "กลุ่มเสี่ยงควรลดกิจกรรมกลางแจ้ง สวมหน้ากาก N95"
    body = f"สูงสุด {top['short']} {top['avg3']:.0f} µg/ลบ.ม. • เกินเกณฑ์ {where} • {advice}"
    return title, body, lvl


# ----------------------------------------------------------------------------- OneSignal
def send_push(title, body, url, extra_data, key):
    """ส่งผ่าน OneSignal ตามวิธีเดียวกับ Push Guardian : App key ก่อน แล้วค่อยลอง legacy"""
    app = os.getenv("ONESIGNAL_APP_ID", "").strip()
    payload = {
        "app_id": app,
        "target_channel": "push",
        "included_segments": ["Subscribed Users"],
        "headings": {"en": title, "th": title},
        "contents": {"en": body, "th": body},
        "url": url,
        "data": extra_data,
        # ไม่ให้แจ้งเตือนเก่าค้างส่งช้า และให้ข้อความฝุ่นใหม่แทนที่ของเดิม (web push)
        "ttl": 6 * 3600,
        "web_push_topic": "nongfon-pm25",
    }

    def post(p, legacy=False):
        if not legacy:
            return requests.post("https://api.onesignal.com/notifications",
                                 headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
                                 json=p, timeout=20)
        q = dict(p)
        q.pop("target_channel", None)
        return requests.post("https://onesignal.com/api/v1/notifications",
                             headers={"Authorization": f"Basic {key}", "Content-Type": "application/json"},
                             json=q, timeout=20)

    r, method = post(payload), "app-key"
    if r.status_code == 400:  # เผื่อพารามิเตอร์เสริมไม่ถูกรับ -> ลองใหม่แบบเรียบง่าย
        plain = {k: v for k, v in payload.items() if k not in ("ttl", "web_push_topic")}
        r, method = post(plain), "app-key-plain"
    if not r.ok and r.status_code in (401, 403, 404):
        r, method = post(payload, legacy=True), "legacy-basic"
    return r, method


# ----------------------------------------------------------------------------- main
def run(now=None, fetch=fetch_aq, sender=send_push, dry_run=False, test_push=False, data_dir=None):
    global STATE_FILE, STATUS_FILE
    if data_dir:
        STATE_FILE, STATUS_FILE = Path(data_dir) / "pm25_state.json", Path(data_dir) / "pm25_status.json"
    now = (now or datetime.now(BKK)).astimezone(BKK)
    site = os.getenv("SITE_URL", "").strip() or DEFAULT_SITE
    url = site.rstrip("/") + "/#pm"
    state = read_json(STATE_FILE, {})
    state.setdefault("districts", {})
    prev_status = read_json(STATUS_FILE, {})
    history = [h for h in (prev_status.get("push", {}) or {}).get("history", []) if isinstance(h, dict)]
    status = {
        "version": "1.0",
        "generated_at": now.isoformat(),
        "source": "Open-Meteo Air Quality (CAMS model) - ไม่ใช่ค่าจากเครื่องวัดจริง",
        "thresholds": {
            "alert_min_level": ALERT_MIN_LEVEL, "alert_above_ugm3": LEVEL_LOWER[3], "critical_above_ugm3": LEVEL_LOWER[4],
            "hysteresis_ugm3": HYST_UGM3, "smoothing_hours": SMOOTH_HOURS, "remind_hours": REMIND_HOURS,
            "quiet_hours": f"{QUIET_START_H:02d}:00-{QUIET_END_H:02d}:00", "max_push_per_day": MAX_PUSH_PER_DAY,
        },
    }

    # 1) ดึงข้อมูล
    try:
        payloads = fetch()
    except Exception as e:
        status["province"] = prev_status.get("province")
        status["districts"] = prev_status.get("districts", [])
        status["push"] = {"last_decision": {"time": now.isoformat(), "action": "fetch_failed",
                                            "reason": f"ดึงข้อมูลฝุ่นไม่สำเร็จ: {type(e).__name__}"},
                          "last_sent_at": state.get("last_push_at"), "history": history[-HISTORY_KEEP:]}
        write_json(STATUS_FILE, status)
        print("fetch failed:", type(e).__name__, str(e)[:160])
        return status

    rows = []
    for d, p in zip(DISTRICTS, payloads):
        s = summarize(p, d, now)
        if s:
            rows.append(s)
    if not rows:
        status["province"] = None
        status["districts"] = []
        status["push"] = {"last_decision": {"time": now.isoformat(), "action": "no_data", "reason": "ไม่มีค่า PM2.5 ใช้งานได้"},
                          "last_sent_at": state.get("last_push_at"), "history": history[-HISTORY_KEEP:]}
        write_json(STATUS_FILE, status)
        return status

    # 2) ระดับรายอำเภอ (มี hysteresis) และระดับจังหวัด
    for r in rows:
        prev = (state["districts"].get(r["name"]) or {}).get("level")
        r["level"] = level_with_hysteresis(r["avg3"], prev)
        r["label"] = LEVEL_LABEL[r["level"]]
        state["districts"][r["name"]] = {"level": r["level"]}
    prov_level = max(r["level"] for r in rows)
    top = max(rows, key=lambda r: r["avg3"])
    status["province"] = {"level": prov_level, "label": LEVEL_LABEL[prov_level],
                          "max_district": top["name"], "max_value": top["avg3"]}
    status["districts"] = rows

    # 3) ตัดสินใจ
    action, reason, kind = decide(prov_level, state, now)
    decision = {"time": now.isoformat(), "action": action, "reason": reason, "level": prov_level, "kind": kind}

    if test_push:
        title = "🧪 ทดสอบ: น้องมายด์เตือนฝุ่น PM2.5"
        body = f"ระบบแจ้งเตือนฝุ่นทำงานปกติ ตอนนี้สูงสุด {top['short']} {top['avg3']:.0f} µg/ลบ.ม. ({LEVEL_LABEL[prov_level]})"
        action, kind = "test", "test"
        decision.update({"action": "test", "reason": "ส่งข้อความทดสอบตามที่สั่ง", "kind": "test"})
    elif action == "send":
        title, body, _ = build_message(rows, kind)

    if action in ("send", "test"):
        src, key = credential()
        entry = {"time": now.isoformat(), "level": prov_level, "kind": kind, "title": title, "body": body}
        if dry_run:
            decision.update({"action": "dry_run", "reason": "dry-run: ไม่ได้ส่งจริง"})
            print("[DRY RUN]", title, "|", body)
        elif not os.getenv("ONESIGNAL_APP_ID", "").strip() or not key:
            decision.update({"action": "failed", "reason": "ไม่พบ OneSignal App ID / API key"})
            entry["status"] = "failed"
            history.append(entry)
        else:
            try:
                resp, method = sender(title, body, url, {"type": "pm25", "level": prov_level, "kind": kind}, key)
                if resp.ok:
                    decision.update({"action": "sent" if action == "send" else "test_sent", "auth_method": method})
                    entry["status"] = "sent" if action == "send" else "test"
                    history.append(entry)
                    if action == "send":
                        day = now.strftime("%Y-%m-%d")
                        daily = state.get("daily") or {}
                        cnt = int(daily.get("count", 0)) + 1 if daily.get("date") == day else 1
                        state.update({"notified_level": prov_level, "last_push_at": now.isoformat(),
                                      "daily": {"date": day, "count": cnt}})
                else:
                    err = f"HTTP {resp.status_code}: {resp.text[:160]}"
                    decision.update({"action": "failed", "reason": "OneSignal ปฏิเสธคำขอ", "error": err})
                    entry.update({"status": "failed", "error": err})
                    history.append(entry)
            except Exception as e:
                decision.update({"action": "failed", "reason": "เครือข่าย/OneSignal ผิดพลาด", "error": f"{type(e).__name__}: {str(e)[:120]}"})
                entry.update({"status": "failed", "error": decision["error"]})
                history.append(entry)
    else:
        # ไม่ส่ง: ปรับ notified_level ตามความเป็นจริง (ลดระดับ/กลับสู่ปกติ) -- ยกเว้นช่วงถูกระงับ ให้คงไว้เพื่อส่งทีหลัง
        if prov_level < ALERT_MIN_LEVEL:
            state["notified_level"] = 0
        elif action == "skipped" and prov_level < int(state.get("notified_level", 0) or 0):
            state["notified_level"] = prov_level

    status["push"] = {"last_decision": decision, "last_sent_at": state.get("last_push_at"),
                      "history": history[-HISTORY_KEEP:]}
    state["updated_at"] = now.isoformat()
    if not dry_run:
        write_json(STATE_FILE, state)
    write_json(STATUS_FILE, status)
    print(f"level={prov_level} ({LEVEL_LABEL[prov_level]}) top={top['short']} {top['avg3']} -> {decision['action']}: {decision['reason']}")
    return status


if __name__ == "__main__":
    truthy = lambda n: os.getenv(n, "").strip().lower() in ("1", "true", "yes")
    run(dry_run=truthy("PM25_DRY_RUN"), test_push=truthy("PM25_TEST_PUSH"))
    sys.exit(0)
