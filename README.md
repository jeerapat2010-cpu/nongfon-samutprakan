# เฝ้าน้องฝน สมุทรปราการ — Radar Intelligence v8.1

v8.1 เน้นลด False Alert และทำให้ PWA อัปเดตง่ายขึ้น

## Radar calibration
- ต้องเห็นสัญญาณฝนซ้ำอย่างน้อย 2 ใน 3 เฟรมล่าสุดก่อนถือว่า “มีฝนแล้ว” (ยกเว้น echo แรง)
- การคาดทิศทางต้องมีหลักฐานการเคลื่อนตัวอย่างน้อย 2 คู่เฟรม
- เพิ่มเกณฑ์ correlation ก่อนใช้ motion projection
- ฝนเบาอย่างเดียวจะไม่ถูกจัดเป็น Severe
- Confidence ต่ำกว่า 65% จะไม่ถูกจัดเป็น Severe
- พยากรณ์ Open-Meteo เป็นหลักฐานเสริม ไม่สามารถดันเป็น High/Severe โดยไม่มี radar evidence
- Push จะไม่ส่งถ้าความมั่นใจของสถานการณ์ยังต่ำ

## PWA update
- Navigation / index / radar status ใช้ network-first
- Service Worker cache เปลี่ยนเป็น v8.1
- แอปสั่งตรวจ service-worker update เมื่อเปิด
- ผู้ติดตั้งเดิมไม่ต้องติดตั้งใหม่ โดยทั่วไปปิดแอปแล้วเปิดใหม่หรือ Refresh 1 ครั้งก็รับเวอร์ชันใหม่

หมายเหตุ: เป็น automated nowcast aid ไม่ใช่ประกาศเตือนภัยอย่างเป็นทางการของ TMD
