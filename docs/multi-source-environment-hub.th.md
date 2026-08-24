# แผนศูนย์รวมข้อมูลสิ่งแวดล้อมหลายแหล่ง

อัปเดต: 24 สิงหาคม 2569

## เป้าหมาย

ระบบดึงข้อมูลจากหน่วยงานไทยเป็นหลัก ครอบคลุมประเทศไทยและให้น้ำหนักการประมวลผลภาคเหนือก่อน จากนั้นใช้แหล่งสากลเป็นการตรวจข้ามและเส้นทางสำรอง รอบดึงข้อมูลเครือข่ายมีงบเวลารวม 480 วินาที (8 นาที) ภายในรอบอัปเดตทุก 3 ชั่วโมง

ทะเบียนปัจจุบันมี 20 เส้นทางปฏิบัติการ แบ่งเป็นอากาศ ฝน น้ำ น้ำท่วม ไฟป่า ภัยแล้ง แผ่นดินไหว ดินถล่ม และคุณภาพอากาศ อยู่ใน `data/source_routes.csv`

## นโยบายค่าใช้จ่าย: ใช้เฉพาะแหล่งฟรี

- ไม่เพิ่มหรือเปิดใช้แหล่งที่ต้องซื้อข้อมูล สมัครสมาชิกแบบเสียเงิน หรือผูกบัตรเพื่อเริ่มคิดค่าบริการ
- อนุญาตเฉพาะข้อมูลเปิดฟรี และบริการที่สมัครบัญชี/API key ได้ฟรีโดยไม่มีค่าใช้จ่ายตามปริมาณการใช้งานของโครงการ
- หากสถานะค่าใช้จ่ายหรือสิทธิ์ใช้งานยังไม่ชัดเจน ให้คงเป็น `candidate` และปิด connector ไว้จนกว่าจะยืนยันจากเอกสารทางการ
- ไม่ใช้ free trial ที่จะเปลี่ยนเป็นแบบเสียเงินอัตโนมัติ และไม่หลบระบบสมาชิกหรือแกะ private API
- MojiWeather ไม่อยู่ใน `source_registry.csv` และ `source_routes.csv` เพราะยังไม่ยืนยัน Official API แบบฟรี จึงไม่ใช้ทั้งแบบสมาชิก การกรอกข้อมูลจากสมาชิก หรือการ scraping
- ECMWF ใช้เฉพาะ Free and Open subset; ไม่สั่งบริการส่งข้อมูลที่มี Service Charge
- NASA Earthdata/IMERG ใช้เฉพาะบัญชีสมัครฟรี ส่วน GISTDA ใช้เฉพาะ Open Government Data API; API key หมายถึงข้อมูลยืนยันตัวตน ไม่ใช่การอนุมัติให้ซื้อบริการ

## หลักสำคัญ

จำนวนแหล่งมากขึ้นไม่ได้แปลว่าแม่นขึ้นโดยอัตโนมัติ ระบบต้องไม่เฉลี่ยทุกแหล่งเท่ากัน เพราะข้อมูลหลายชื่ออาจมีต้นทางเดียวกัน เช่น:

- Open-Meteo เป็นตัวรวบรวมแบบจำลอง จึงห้ามนับซ้ำเป็นเสียงอิสระเมื่อเพิ่ม ECMWF หรือ NOAA โดยตรง
- GISTDA และ NASA FIRMS อาจใช้จุดความร้อน VIIRS ชุดเดียวกัน จึงอยู่ใน `independence_group=viirs_satellite`
- ThaiWater รวมข้อมูลสถานีจากหลายหน่วยงาน จึงต้องเก็บรหัสสถานีและหน่วยงานเจ้าของจริงก่อนเทียบกับ RID/DWR/TMD

การรวมค่าต้องเลือกข้อมูลตามลำดับต่อไปนี้:

1. ผ่าน freshness gate และ quality gate
2. ตรงชนิดตัวแปร หน่วย เวลา และ spatial support
3. แหล่งทางการไทยก่อนสำหรับเหตุการณ์ในประเทศไทย
4. เลือกอย่างมากหนึ่งน้ำหนักเต็มต่อ `independence_group`
5. ใช้ค่าคลาดเคลื่อนย้อนหลังแยกตามพื้นที่ ฤดู และ lead time ปรับน้ำหนัก
6. ถ้าแหล่งหลักล้ม ใช้แหล่งสำรองพร้อมขยายช่วงความไม่แน่นอนและแสดง provenance

## ลำดับแหล่งตามประเภท

| ประเภท | แหล่งหลัก | สำรองลำดับ 1 | สำรองลำดับ 2/ตรวจข้าม |
|---|---|---|---|
| ฝนภาคพื้น | ThaiWater/HII และเจ้าของสถานี | DWR/RID | แบบจำลองใช้เฉพาะ gap estimate ไม่ปลอมเป็นค่าตรวจวัด |
| ฝนเชิงพื้นที่ | TMD Radar QPE | NASA IMERG | JAXA GSMaP |
| พยากรณ์อากาศ | TMD API / ECMWF IFS-AIFS | NOAA GFS-GEFS | Open-Meteo baseline |
| น้ำ/อ่างเก็บน้ำ | RID | DWR/EGAT | GloFAS สำหรับแนวโน้มลำน้ำใหญ่ |
| น้ำท่วมเชิงพื้นที่ | GISTDA flood extent | Copernicus flood products | ยืนยันกับระดับน้ำสถานี |
| ไฟป่า | GISTDA VIIRS / FireDNPX | NASA FIRMS | สภาพอากาศและความชื้นเป็นบริบท |
| แผ่นดินไหว | TMD Earthquake | USGS ComCat/GeoJSON | DMR สำหรับชั้นความเสี่ยงและเหตุการณ์ธรณีพิบัติภัย |
| ดินถล่ม | DMR | ฝนสะสม TMD/ThaiWater | GISTDA soil moisture/drought context |
| คุณภาพอากาศ | PCD Air4Thai | GISTDA GEMS | NASA/แบบจำลองเป็นบริบท |

## GISTDA ที่เพิ่มแล้ว

ตัวเชื่อม `src/fetch_gistda_disasters.py` รองรับ:

- `/features/flood/1day` สำหรับขอบเขตน้ำท่วม
- `/features/viirs/1day` สำหรับจุดความร้อน
- ค่าเริ่มต้นจำกัดกรอบภาคเหนือ `97.0,14.0,101.5,20.8`
- กำหนดกรอบเองได้ด้วย `GISTDA_BBOX`
- ใช้ `GISTDA_API_KEY` ผ่าน secret เท่านั้น ห้ามฝังคีย์ในหน้าเว็บหรือ repository

DRIPlus, NDWI และ SMAP ถูกลงทะเบียนไว้แล้ว แต่ยังปิด connector จนกว่าจะเพิ่มตัวอ่าน WMS/WMTS แบบตัวเลข ไม่ใช้สีจากภาพแทนข้อมูลต้นฉบับ

## สถานะและ fallback

ทุกเส้นทางมีสถานะล่าสุดใน `source_health_latest` ได้แก่ `success`, `stale`, `failed`, `credentials_missing`, `no_data`, `budget_exhausted` และ `not_run` การล้มของแหล่งหนึ่งไม่ทำให้รอบทั้งหมดล้ม หาก quality gate ยังสร้างค่าจากเส้นทางสำรองได้

ข้อมูลเหตุการณ์ล่าสุดเก็บใน `hazard_features_latest` พร้อม geometry, เวลา, source URL, properties และ quality flag โดยยังเป็นตารางหลังบ้านใน Supabase เปิด RLS และอนุญาตเฉพาะ service role

## สิ่งที่ยังต้องทำก่อนเรียกว่าแม่นขึ้น

- สมัครและใส่ GISTDA API key แบบไม่มีค่าใช้จ่ายใน GitHub Secret; หากมีการเรียกเก็บเงินให้หยุดและไม่เชื่อม
- ขอ TMD API token เฉพาะกรณีไม่มีค่าใช้จ่าย และยืนยัน endpoint แผ่นดินไหว
- เชื่อม ECMWF กับ NOAA โดยตรงเพื่อให้มีโมเดลอิสระอย่างน้อยสองตระกูล
- เชื่อม NASA IMERG/JAXA GSMaP และทำ bias correction กับสถานีภาคเหนือ
- เชื่อม RID/DWR/EGAT พร้อม entity resolution ป้องกันสถานีซ้ำ
- เก็บคู่พยากรณ์–ค่าจริงอย่างน้อย 60–90 วัน แล้วคำนวณน้ำหนักจาก MAE/RMSE/WAPE/CSI แยกตามฤดูและ lead time

## แหล่งอ้างอิงทางการ

- GISTDA Disaster Open API: https://disaster.gistda.or.th/services/open-api
- TMD Data Service: https://www.tmd.go.th/service/tmdData
- ThaiWater: https://www.thaiwater.net/
- RID Reservoir API: https://app.rid.go.th/reservoir/api/document/reservoir
- DWR Telemetry: https://telemetry.dwr.go.th/home
- USGS Earthquake Feeds: https://earthquake.usgs.gov/earthquakes/feed/
- NASA GPM IMERG: https://gpm.nasa.gov/data/imerg
- ECMWF Open Data: https://www.ecmwf.int/en/forecasts/datasets/open-data
- NOAA NOMADS: https://nomads.ncep.noaa.gov/
- JAXA GSMaP: https://sharaku.eorc.jaxa.jp/GSMaP/
- Copernicus GloFAS: https://global-flood.emergency.copernicus.eu/
- NASA FIRMS: https://firms.modaps.eosdis.nasa.gov/active_fire/
