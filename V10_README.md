# เฝ้าน้องฝน สมุทรปราการ — v10 Final Accuracy

รุ่นนี้ออกแบบให้เป็น baseline ระยะยาว ไม่แก้ threshold แบบจุดต่อจุดอีก

## Engine
- TMD Radar Composite 8 เฟรม: จุดฝน ความแรง persistence การเคลื่อนตัว ETA
- Multi-Radar health: หนองจอก → หนองแขม → สุวรรณภูมิ → Composite
- JMA Himawari: ใช้เป็นหลักฐานสนับสนุน ไม่ยืนยันฝนถึงพื้นเพียงลำพัง
- Open-Meteo: อ่านช่วงเวลาปัจจุบัน/อนาคตจริง ไม่อ่าน 4 ชั่วโมงแรกของวัน
- P15 / P30 / P60: โอกาสฝนใน 15/30/60 นาที
- Data Quality + Model Confidence แยกกัน

## Push
4 ระดับ: เฝ้าระวัง / เตรียมรับฝน / ฝนใกล้ถึง / ฝนหนัก-รุนแรง
- ใช้หลายหลักฐาน ไม่ติด confidence threshold ตัวเดียว
- escalation ส่งทันที
- cooldown ตามระดับ
- hysteresis 2 รอบก่อนลดระดับ
- ถ้า radar เก่าหรือ Data Quality ต่ำ จะงด Push
- ปิด local notification อัตโนมัติในหน้าเว็บ เพื่อไม่ให้ซ้ำกับ OneSignal

## Verification
ระบบเก็บ prediction และตรวจย้อนหลังเมื่อถึง 15/30/60 นาที โดยใช้ Radar Composite เป็น observation proxy
คำนวณ POD / FAR / CSI / Brier และเริ่ม calibration แบบ conservative เมื่อมีตัวอย่าง >=80

## Reliability
- Watchdog สำรองทุก 15 นาที: ถ้าสถานะสาธารณะเกิน 18 นาที จะรัน analyzer ช่วยอีกชั้น
- GitHub schedule ทุก 5 นาทีที่นาที 02/07/12/... เพื่อลด congestion
- retry analyzer สูงสุด 3 ครั้ง
- ไม่ cancel run ที่กำลังทำงาน
- หน้าแอปมี watchdog; ข้อมูลเก่า >25 นาทีแสดงแดง และงด Push จากข้อมูลเก่า

## ไฟล์ที่ต้องแทน
- index.html
- sw.js
- scripts/radar_intelligence.py
- .github/workflows/rain-watch.yml

requirements.txt เดิมใช้ได้ ไม่ต้องแก้
