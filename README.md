# ระบบติดตามฝนและอุณหภูมิ อุทยานแห่งชาติแม่ปิง

โครงการนี้วางฐานข้อมูลสำหรับ Dashboard เชิงแผนที่ของอุทยานแห่งชาติแม่ปิง โดยใช้หน่วย HQ และ มป.1–มป.12 เป็นจุดรายงานหลัก และใช้ข้อมูลสถานีรอบอุทยาน เรดาร์ ดาวเทียม แบบจำลองพยากรณ์ และภูมิประเทศเป็นข้อมูลสนับสนุน

## หลักการสำคัญ

- แยก `ค่าที่วัดจริง ณ จุด` ออกจาก `ค่าประมาณของพื้นที่/กริด` และ `ค่าพยากรณ์`
- ไม่ถือว่าพิกัดจากชื่อหมู่บ้านหรือสถานที่ท่องเที่ยวเป็นพิกัดสถานีจริง
- เก็บค่าพยากรณ์ที่จุดรายงาน/สถานีทุกครั้งตาม `issued_at` และ `valid_at` เพื่อประเมินย้อนหลัง ส่วนกริด 5 กม. เก็บ snapshot ล่าสุดสำหรับแผนที่เพื่อลดขนาดฐานข้อมูล
- ให้น้ำหนักข้อมูลตามคุณภาพ ความสด ระยะห่าง ความสูง ภูมิประเทศ และผลงานย้อนหลัง
- รายงานความคลาดเคลื่อนเป็นหน่วยจริง เช่น มม. และ °C ควบคู่กับอัตราจับเหตุการณ์ ไม่สรุปเป็น “ความแม่นยำเปอร์เซ็นต์เดียว”

## ช่วงเวลาที่ใช้

- Dashboard ย้อนหลัง: 7 และ 30 วัน
- ฐานประเมินระยะแรก: rolling 60–90 วัน
- ฐานปรับเทียบที่เหมาะสม: อย่างน้อย 1 ฤดูฝน และควรเก็บต่อเนื่อง 2–3 ปี
- พยากรณ์เชิงปฏิบัติการ: 0–72 ชั่วโมง
- แนวโน้ม: วันที่ 4–7 พร้อมระดับความไม่แน่นอน

## โครงสร้างไฟล์

- `data/stations.csv` — Master 13 จุด พร้อมสถานะพิกัดและความขัดแย้ง
- `data/source_registry.csv` — ทะเบียนแหล่งข้อมูลและข้อจำกัด
- `data/source_routes.csv` — ลำดับแหล่งหลัก–สำรอง 20 เส้นทาง พร้อม lineage, freshness และ timeout
- `data/support_station_shortlist.csv` — สถานี ThaiWater ใกล้อุทยานที่ตรวจ completeness เบื้องต้นแล้ว
- `db/schema.sql` — โครงสร้างฐานข้อมูล SQLite/PostgreSQL-compatible foundation
- `src/metrics.py` — การคำนวณ MAE, RMSE, Bias, WAPE, POD, FAR, CSI และ Brier Score
- `src/validate_station_master.py` — ตรวจคุณภาพ Master 13 จุด
- `src/discover_thaiwater_stations.py` — ค้นสถานีฝนรอบแนวเขตอุทยาน 25/75 กม. จาก ThaiWater public JSON
- `src/fetch_thaiwater_observations.py` — ดึงฝนรายชั่วโมง/รายวันและอุณหภูมิของสถานี Priority 1 เข้า SQLite
- `src/fetch_open_meteo_baseline.py` — เก็บ snapshot พยากรณ์ 7 วันโดยไม่เขียนทับ model run เดิม
- `src/build_park_grid.py` — สร้างกริดศูนย์กลางเซลล์ 5 กม. ภายในแนวเขตอุทยานแบบ deterministic
- `src/fetch_grid_forecast.py` — ดึงฝน โอกาสฝน และอุณหภูมิ 7 วันของทุกเซลล์เป็น latest snapshot
- `src/audit_park_boundary.py` — ตรวจเทียบ OSM กับชั้นอุทยาน GISTDA/MNRE ปี 2557 และบันทึกเหตุผลการเลือกขอบเขต
- `src/audit_tmd_qpe.py` — ตรวจเวลาและ metadata ของ TMD QPE ASCII พร้อม freshness gate 6 ชั่วโมง
- `src/source_portfolio.py` — อัปเดตทะเบียนหลายแหล่งในฐานข้อมูลเดิมโดยไม่ลบประวัติ
- `src/fetch_gistda_disasters.py` — ดึงขอบเขตน้ำท่วม/จุดความร้อน GISTDA เมื่อมี API key
- `src/fetch_usgs_earthquakes.py` — ดึงแผ่นดินไหวล่าสุดรอบประเทศไทยจาก USGS
- `src/fetch_tmd_qpe.py` — อ่าน QPE 0.01° ที่ศูนย์กลางกริดและเขียน snapshot เฉพาะเมื่อผลิตภัณฑ์สด
- `src/train_bias_corrections.py` — ฝึก offset/ratio แยกจุดและ lead bucket พร้อม gate 60 วัน/10 เหตุการณ์ฝน
- `src/build_system_readiness.py` — คำนวณความพร้อมด้านเทคนิคและวุฒิภาวะของหลักฐานแยกกัน
- `src/evaluate_forecasts.py` — จับคู่ forecast–observation ที่ location/time เดียวกันและคำนวณคะแนนแยก lead bucket
- `src/check_operational_quality.py` — quality gate สำหรับ freshness, completeness, duplicate, range, capability และ referential integrity
- `src/build_dashboard.py` — สร้าง Dashboard แผนที่แบบไฟล์ HTML เดียวจากฐานข้อมูลจริง
- `src/build_accuracy_report_artifact.py` — สร้าง canonical artifact สำหรับรายงานตรวจความพร้อม Accuracy
- `src/run_operational_cycle.py` — orchestration แบบมี lock/log สำหรับดึงข้อมูล ตรวจคะแนน และสร้าง artifact ทั้งสาย
- `src/sync_supabase.py` — ดึงประวัติจาก Supabase ก่อนรันและบันทึกผลกลับแบบ server-side หลังรัน
- `src/validate_release.py` — หยุด deploy หากค่าประเมินไม่ครบ 13 จุด × 2 ตัวแปร หรือ artifact ไม่สมบูรณ์
- `dashboard/index.html` — Dashboard snapshot ที่สร้างเมื่อ 23 สิงหาคม 2569
- `reports/accuracy-readiness/report.html` — รายงาน technical แบบ self-contained พร้อม evidence และข้อจำกัด
- `tests/test_metrics.py` — ชุดทดสอบตัวชี้วัด
- `docs/architecture.md` — สถาปัตยกรรมข้อมูลและกติกาการผสานข้อมูล
- `docs/data-quality-status.md` — สถานะความพร้อมและปัญหาที่ต้องแก้
- `docs/station-discovery-2026-08-23.md` — ผลค้น 317 สถานีและ shortlist ที่ควรเชื่อมก่อน
- `docs/dashboard-spec.md` — ข้อกำหนดแผนที่และหน้าจอ

## ตรวจสอบระบบ

```bash
python3 src/validate_station_master.py
python3 -m unittest discover -s tests -v
python3 src/init_db.py data/maeping_weather.db
python3 src/fetch_thaiwater_observations.py data/maeping_weather.db
python3 src/fetch_open_meteo_baseline.py data/maeping_weather.db
python3 src/build_park_grid.py data/maeping_weather.db
python3 src/fetch_grid_forecast.py data/maeping_weather.db
python3 src/audit_park_boundary.py
python3 src/audit_tmd_qpe.py
python3 src/fetch_tmd_qpe.py data/maeping_weather.db
python3 src/train_bias_corrections.py data/maeping_weather.db
python3 src/build_system_readiness.py data/maeping_weather.db
python3 src/evaluate_forecasts.py data/maeping_weather.db
python3 src/build_dashboard.py data/maeping_weather.db dashboard/index.html
python3 src/build_accuracy_report_artifact.py data/maeping_weather.db
python3 src/run_operational_cycle.py
python3 src/discover_thaiwater_stations.py --radius-km 75 > /tmp/maeping-support-stations.csv
```

`run_operational_cycle.py` เป็นคำสั่งหลักสำหรับงานประจำ มี file lock ป้องกันรันซ้อน ทำแต่ละขั้นต่อแม้บาง API ล้มเหลว บันทึกสถานะล่าสุดไว้ที่ `data/operational_status.json` และเก็บประวัติ JSONL ใน `runtime/operational_cycle.jsonl` ไฟล์ runtime/status และ SQLite ไม่ถูกนำเข้า Git แต่สถานะรอบล่าสุดจะถูกฝังใน Dashboard ที่สร้างเสร็จแล้ว

GitHub Actions รัน operational cycle อัตโนมัติทุก 3 ชั่วโมง ดึงประวัติจาก Supabase ก่อนคำนวณ บันทึก observation/forecast/estimate และสถานะรอบกลับ Supabase แล้วจึง deploy GitHub Pages เมื่อ artifact ผ่าน validation เท่านั้น Quality gate ทำงานก่อน verification ทุกครั้ง

เพื่อควบคุมพื้นที่ฐานข้อมูล ระบบเก็บ forecast history ใน Supabase เฉพาะฝน/อุณหภูมิ lead 0–72 ชั่วโมงของสถานีสนับสนุนที่มีค่าตรวจวัด ย้อนหลัง 100 วัน ส่วนกริด 5 กม. เก็บเฉพาะ model run ล่าสุด ขณะที่ Dashboard ยังฝังพยากรณ์ล่าสุดของจุดรายงานทุกจุดตามปกติ

เปิด [Dashboard](dashboard/index.html) ได้โดยตรงจากไฟล์ ไม่ต้องติดตั้ง server และไม่ต้องโหลดไลบรารีแผนที่ภายนอก ตัวฐานข้อมูล `*.db` ถูกละเว้นจาก Git เพราะเป็นไฟล์ที่สร้างใหม่ได้จากคำสั่งด้านบน ส่วน HTML เก็บ snapshot ของข้อมูลและ provenance ไว้สำหรับตรวจทาน

## การเผยแพร่ออนไลน์

GitHub Pages เผยแพร่เฉพาะโฟลเดอร์ `dashboard` ผ่าน `.github/workflows/deploy-pages.yml` เมื่อมีการ push ไปยัง branch `main`, ตามตาราง `17 */3 * * *` (ทุก 3 ชั่วโมง) หรือสั่งรันด้วยตนเอง ฐานข้อมูล SQLite, runtime log และกุญแจ Supabase ไม่ถูกนำขึ้นเว็บ ต้องตั้ง GitHub Actions secret ชื่อ `SUPABASE_SECRET_KEY` โดยใช้ Supabase secret key (`sb_secret_...`) หรือ legacy service-role key เท่านั้น

หน้า Dashboard แสดง “ข้อมูลล่าสุด”, “เวลาสร้างหน้าเว็บ” และ “รอบอัปเดตถัดไป” แยกกัน พร้อมเปลี่ยนสถานะเป็นเขียวเมื่ออายุข้อมูลไม่เกิน 4 ชั่วโมง เหลืองเมื่อ 4–8 ชั่วโมง และแดงเมื่อเกิน 8 ชั่วโมง

## สถานะปัจจุบัน

ชั้นระบบพร้อมใช้งานและ operational cycle ล่าสุดสำเร็จทุกขั้น: observation 1,027 แถวจาก 7 สถานี, forecast history รายจุด 103,122 แถว, รอบรายจุดล่าสุด 16,416 แถว, กริด 5 กม. 40 ช่องและ latest forecast snapshot 18,240 แถว ตัวอ่าน TMD QPE พร้อม extract ที่ centroid แต่ freshness gate ตัดไฟล์ล่าสุดซึ่งเก่า 1,021 ชั่วโมงออก จึงมี radar estimate สด 0 แถว Quality gate เป็น `warning` เฉพาะ data gap นี้ โดย critical/suspect/duplicate/orphan เท่ากับ 0

Dashboard แสดงความพร้อมด้านเทคนิค 93.5%, หลักฐานความแม่นยำ 32.5% และ operational readiness 69.1% ตามสูตร 60/40 ปัจจุบัน forecast–observation ยังมี 0 คู่ใน 70 กลุ่ม Calibration pipeline จึงสร้างครบ 70 กลุ่มแต่ guard parameter ทั้งหมดไว้ ไม่มี Accuracy หรือ bias correction ปลอม แนวเขต OSM ถูกตรวจเทียบกับชั้น GISTDA/MNRE ปี 2557 แล้ว: พื้นที่ต่างกันประมาณ 1.33% และ centroid กริดตรงกัน 39/40 ช่อง แต่ยังรอ geometry ปัจจุบันจาก DNP
