# เฝ้าน้องฝน v8.2 — เปิด Push ตอนปิดแอป

## สิ่งที่โค้ดเตรียมไว้แล้ว
- GitHub Actions ตรวจฝนประมาณทุก 10 นาที
- Radar Intelligence ประเมิน Risk / ETA / Confidence
- ส่ง OneSignal เฉพาะเมื่อ Risk >= 70 และ Confidence ผ่านเกณฑ์
- ป้องกันแจ้งซ้ำเหตุการณ์เดิม
- OneSignal ใช้ service worker เดียวกับ PWA โดยกำหนด path/scope ให้ถูกกับ GitHub Pages project URL
- API Key ไม่อยู่ในหน้าเว็บ

## OneSignal
สร้าง Web Push app แบบ Custom Code
Site Name: เฝ้าน้องฝน สมุทรปราการ
Site URL / Origin: https://jeerapat2010-cpu.github.io
Auto Resubscribe: ON

จาก OneSignal Settings > Keys & IDs:
- App ID -> GitHub secret ชื่อ ONESIGNAL_APP_ID
- API Key -> GitHub secret ชื่อ ONESIGNAL_REST_API_KEY

ห้ามใส่ API Key ลงไฟล์ GitHub

จากนั้น GitHub:
Settings > Secrets and variables > Actions > New repository secret

แล้วไป:
Actions > NongFon Rain Watch > Run workflow

หลัง workflow ทำงาน push-config.js จะถูกสร้างด้วย App ID อัตโนมัติ
จากนั้นเปิด PWA แล้วกด “เปิดแจ้งเตือน” และ Allow

iPhone/iPad:
- iOS/iPadOS 16.4+
- ต้อง Add to Home Screen ก่อน
- เปิดจากไอคอนบน Home Screen แล้วค่อยกดเปิดแจ้งเตือน
