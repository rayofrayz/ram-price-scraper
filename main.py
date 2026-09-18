import asyncio
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from playwright.async_api import async_playwright
import pandas as pd

BRANDS = [
    'ADATA', 'XPG', 'KINGSTON', 'CRUCIAL', 'CORSAIR', 'LEXAR', 'G.SKILL', 
    'TEAMGROUP', 'BLACKBERRY', 'PNY', 'SAMSUNG', 'HYNIX', 'KLEVV', 'THERMALTAKE', 
    'COLORFUL', 'HIKVISION', 'HIKSEMI', 'APACER', 'GALAX', 'WESTERN DIGITAL', 'WD', 'SEAGATE', 'SANDISK', 'ACER', 'TRANSCEND'
]

def clean_and_parse_hardware(raw_title: str, price_val, source: str) -> dict:
    title_upper = raw_title.upper()
    category = None
    form_factor = None
    tech_spec = None
    
    # -------------------------------------------------------------
    # 1. จำแนกหมวดหมู่ RAM
    # -------------------------------------------------------------
    if 'RAM' in title_upper or 'DDR' in title_upper:
        category = 'RAM'
        if 'DDR5' in title_upper:
            ddr_type = 'DDR5'
        elif 'DDR4' in title_upper:
            ddr_type = 'DDR4'
        else:
            return None

        bus_match = re.search(r'\b(2133|2400|2666|2933|3200|3600|4800|5200|5600|6000|6200|6400|6600|6800|7200|7600|8000)\b', title_upper)
        bus_speed = f"{bus_match.group(1)}MHz" if bus_match else "UNKNOWN"

        # กรองเฉพาะ DDR4 Bus 3200 และ DDR5 ทั้งหมด
        if ddr_type == "DDR4" and bus_speed != "3200MHz" and bus_speed != "UNKNOWN":
            return None

        is_sodimm = any(k in title_upper for k in ['NB', 'NOTEBOOK', 'SO-DIMM', 'SODIMM', 'LAPTOP'])
        form_factor = "SO-DIMM (Notebook)" if is_sodimm else "U-DIMM (Desktop/Gaming)"
        tech_spec = f"{ddr_type} ({bus_speed})"

    # -------------------------------------------------------------
    # 2. จำแนกหมวดหมู่ SSD (SATA 2.5" & M.2 NVMe/SATA)
    # -------------------------------------------------------------
    elif any(k in title_upper for k in ['SSD', 'SOLID STATE', 'NVME', 'SATA', 'M.2']):
        category = 'SSD'
        if any(k in title_upper for k in ['M.2', 'NVME', '2280', 'PCIE']):
            form_factor = 'M.2'
            tech_spec = 'M.2 NVMe PCIe' if any(k in title_upper for k in ['NVME', 'PCIE', 'GEN']) else 'M.2 SATA'
        elif any(k in title_upper for k in ['2.5', 'SATA3', 'SATA III', 'SATA']):
            form_factor = '2.5 inch'
            tech_spec = 'SATA III (2.5")'
        else:
            form_factor = 'SSD (General)'
            tech_spec = 'SATA / NVMe'
    else:
        return None

    # 3. สกัด Capacity (ความจุ GB/TB)
    cap_match = re.search(r'(\d+)\s*(GB|TB)', title_upper)
    capacity = f"{cap_match.group(1)}{cap_match.group(2)}" if cap_match else "UNKNOWN"

    # 4. แปลงราคาเป็นตัวเลข
    try:
        clean_price = float(re.sub(r'[^\d.]', '', str(price_val)))
    except (ValueError, TypeError):
        clean_price = None

    if not clean_price or clean_price <= 0:
        return None

    # 5. สกัด Brand
    brand_found = "OTHER"
    for b in BRANDS:
        if re.search(rf'\b{b}\b', title_upper):
            brand_found = b
            break

    # 6. สกัด Part Number
    part_number = "N/A"
    pn_match = re.search(r'\(([^)]+)\)', raw_title)
    if pn_match:
        part_number = pn_match.group(1).strip()
    else:
        for token in raw_title.split():
            clean_t = token.strip('(),')
            if re.match(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9\-/]{5,25}$', clean_t):
                part_number = clean_t
                break

    return {
        "Source": source,
        "Category": category,
        "Brand": brand_found,
        "Model_Raw": raw_title.strip(),
        "Part_Number": part_number,
        "Form_Factor": form_factor,
        "Tech_Spec": tech_spec,
        "Capacity": capacity,
        "Price": clean_price
    }

async def scrape_advice(page) -> list:
    results = []
    # รายชื่อ URL หมวดหมู่ RAM และ SSD ของ Advice
    categories = [
        {"name": "PC RAM", "url": "https://www.advice.co.th/product/ram-for-pc"},
        {"name": "Notebook RAM", "url": "https://www.advice.co.th/product/ram-for-notebook"},
        {"name": "SSD All", "url": "https://www.advice.co.th/product/solid-state-drive-ssd"},
        {"name": "SSD M.2", "url": "https://www.advice.co.th/product/solid-state-drive-ssd/ssd-m-2-nvme"},
        {"name": "SSD SATA", "url": "https://www.advice.co.th/product/solid-state-drive-ssd/ssd-sata-2-5-"}
    ]
    
    for cat in categories:
        try:
            print(f"🌐 Scraping Advice ({cat['name']}): {cat['url']}")
            await page.goto(cat['url'], wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            
            # เลื่อนลงเพื่อโหลดรายการสินค้า Ajax
            for _ in range(8):
                await page.evaluate("window.scrollBy(0, 2000)")
                await page.wait_for_timeout(800)

            items = await page.query_selector_all(".product-box, .product-list-item, .product-card, div[class*='product']")
            print(f"Found {len(items)} elements on {cat['name']}")

            for item in items:
                text_content = await item.inner_text()
                lines = [line.strip() for line in text_content.split('\n') if line.strip()]
                
                name, price = None, None
                for line in lines:
                    line_u = line.upper()
                    if any(k in line_u for k in ['RAM', 'SSD', 'DDR', 'SATA', 'NVME', 'M.2', 'GB', 'TB']) and not name:
                        if len(line) > 8:
                            name = line
                    if ("฿" in line or "บาท" in line or re.search(r'^\d{1,2},\d{3}$', line) or re.search(r'^\d{3,5}$', line)) and not price:
                        price = line

                if name and price:
                    parsed = clean_and_parse_hardware(name, price, "Advice")
                    if parsed:
                        results.append(parsed)

        except Exception as e:
            print(f"Advice Scraping Error on {cat['name']}: {e}")
            
    print(f"✅ Advice Total Scraped: {len(results)} items")
    return results

def send_email_with_excel(filepath, status_msg=""):
    sender_email = os.environ.get("SENDER_EMAIL")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    receiver_email_raw = os.environ.get("RECEIVER_EMAIL")

    if not sender_email or not app_password or not receiver_email_raw:
        print("❌ Missing secrets!")
        return

    receiver_list = [email.strip() for email in receiver_email_raw.split(',') if email.strip()]

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = ", ".join(receiver_list)
    msg['Subject'] = f"📊 รายงานราคา RAM & SSD Advice ({status_msg})"

    body = f"สวัสดีครับ\n\nรายงานสรุปราคา RAM และ SSD (SATA / M.2) จาก Advice\nสถานะ: {status_msg}\n\nดูรายละเอียดในไฟล์แนบได้เลยครับ"
    msg.attach(MIMEText(body, 'plain'))

    if filepath and os.path.exists(filepath):
        with open(filepath, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename= {os.path.basename(filepath)}")
            msg.attach(part)

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, app_password)
        server.sendmail(sender_email, receiver_list, msg.as_string())
        server.quit()
        print(f"✉️ ส่ง Email สำเร็จไปยัง {len(receiver_list)} รายชื่อ!")
    except Exception as e:
        print(f"❌ SMTP Error: {e}")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="th-TH"
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        advice_data = await scrape_advice(page)
        await browser.close()

        file_name = "Hardware_Advice_Prices.xlsx"
        
        if advice_data:
            df = pd.DataFrame(advice_data)
            df = df.drop_duplicates(subset=['Source', 'Model_Raw', 'Price'])
            
            ram_df = df[df['Category'] == 'RAM']
            ssd_df = df[df['Category'] == 'SSD']

            with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name="All Raw Cleaned", index=False)
                if not ram_df.empty:
                    ram_df.to_excel(writer, sheet_name="RAM Summary", index=False)
                if not ssd_df.empty:
                    ssd_df.to_excel(writer, sheet_name="SSD Summary", index=False)

            status_msg = f"ดึงสำเร็จรวม {len(df)} รายการ (RAM: {len(ram_df)}, SSD: {len(ssd_df)})"
        else:
            df_empty = pd.DataFrame([{"Message": "No data matching criteria found"}])
            df_empty.to_excel(file_name, index=False)
            status_msg = "ไม่พบข้อมูลสินค้า"

        send_email_with_excel(file_name, status_msg)

if __name__ == "__main__":
    asyncio.run(main())
