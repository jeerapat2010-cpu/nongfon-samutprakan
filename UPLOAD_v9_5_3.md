# v9.5.3 Zoom + Motion Hard Fix

สาเหตุที่ v9.5.2 ไม่ทำงานบนเว็บจริง:
- GitHub index.html ปัจจุบันยังเป็น v9.5.1
- ไม่มี startRadarAnimation() อยู่ในไฟล์จริง
- radar_status.json ก็ยังเป็น v9.5.1

v9.5.3 แก้แบบหน้าเว็บชุดเดียว:
- Composite crop แบบ calibrated จริง: lon ~98.8–102.0E / lat ~12.2–15.2N
- เห็น กทม. สมุทรปราการ และปริมณฑลเป็นหลัก
- ไม่ต้องเห็นเหนือ/ใต้ทั้งประเทศ
- ถ้าเป็น station radar ไม่ crop ซ้ำ
- ปุ่มภาพเคลื่อนไหวไม่ต้องพึ่ง radar_frames จาก backend
- สร้าง URL ย้อนหลังทุก 15 นาทีจาก timestamp ล่าสุด
- preload และใช้เฉพาะเฟรมที่โหลดได้จริง
- animation ทุก 0.8 วินาที
- ปุ่มเรดาร์ล่าสุดหยุด loop ได้
- ถ้าสถานีมี GIF loop ใช้ GIF โดยตรง

อัป root repo เท่านั้น:
- index.html
- sw.js

ไม่ต้องอัป scripts
ไม่ต้อง Run workflow
