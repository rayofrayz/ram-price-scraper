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

BRANDS = ['ADATA', 'XPG', 'KINGSTON', 'CRUCIAL', 'CORSAIR', 'LEXAR', 'G.SKILL', 'TEAMGROUP', 'BLACKBERRY', 'PNY', 'SAMSUNG', 'HYNIX', 'KLEVV', 'THERMALTAKE', 'COLORFUL', 'HIKVISION', 'HIKSEMI', 'APACER']

def clean_and_parse_ram(raw_title: str, price_val, source: str) -> dict:
    title_upper = raw_title.upper()
    
    # 1. ตรวจสอบประเภท DDR
    ddr_type = None
    if 'DDR5' in title_upper:
        ddr_type = 'DDR5'
    elif 'DDR4' in title_upper:
        ddr_type = 'DDR4'
    else:
        return None

    # 2. สกัด Bus Speed
    bus_match = re.search(r'\b(2133|2400|2666|2933|3200|3600|4800|5200|5600|6000|6200|6400|6600|6800|7200|7600|8000)\b', title_upper)
    bus_speed = f"{bus_match.group(1)}MHz" if bus_match else "UNKNOWN"

    # -------------------------------------------------------------
    # เงื่อนไขการกรอง:
    # - DDR4: เอาเฉพาะ Bus 3200MHz เท่านั้น
    # - DDR5: เอาทุก Bus Speed
    # -------------------------------------------------------------
    if ddr_type == "DDR4":
        if bus_speed != "3200MHz" and bus_speed != "UNKNOWN":
            return None

    # 3. ระบุ Form Factor (Notebook vs Desktop/Gaming)
    is_sodimm = any(k in title_upper for k in ['NB', 'NOTEBOOK', 'SO-DIMM', 'SODIMM', 'LAPTOP'])
    form_factor = "SO-DIMM (Notebook)" if is_sodimm else "U-DIMM (Desktop/Gaming)"

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

    # 6. สกัด Capacity
    cap_match = re.search(r'(\d+)\s*GB', title_upper)
    capacity = f"{cap_match.group(1)}GB" if cap_match else "UNKNOWN"

    # 7. สกัด Part Number
    part_number = "N/A"
    pn_match = re.search(r'\(([^)]+)\)', raw_title)
    if pn_match:
        part_number = pn_match.group(1).strip()
    else:
        for token in raw_title.split():
            clean_t = token.strip('(),')
            if re.match(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9\-/]{6,25}$', clean_t):
                part_number = clean_t
                break

    return {
        "Source": source,
        "Brand": brand_found,
        "Model_Raw": raw_title.strip(),
        "Part_Number": part_number,
        "Form_Factor": form_factor,
        "DDR_Type": ddr_type,
        "Bus_Speed": bus_speed,
        "Capacity": capacity,
        "Price": clean_price
    }

async def scrape_advice(page) -> list:
    results = []
    # หมวดหมู่ RAM ของ Advice (PC DDR4 3200, PC DDR5, NB DDR4 3200, NB DDR5)
    urls = [
        "https://www.advice.co.th/product/ram-for-pc/ram-pc-ddr4-3200-",
        "https://www.advice.co.th/product/ram-for-pc/ram-pc-ddr5",
        "https://www.advice.co.th/product/ram-for-notebook/notebook-ddr4-3200-",
        "https://www.advice.co.th/product/ram-for-notebook/notebook-ddr5"
    ]
    
    for url in urls:
        try:
            print(f"🌐 Scraping Advice: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
            
            # เลื่อนลงเพื่อให้หน้าเว็บโหลดLazy Load สินค้าทั้งหมด
            for _ in range(6):
                await page.evaluate("window.scrollBy(0, 1500)")
                await page.wait_for_timeout(800)

            items = await page.query_selector_all(".product-box, .product-list-item, div[class*='product']")
            
            for item in items:
                text_content = await item.inner_text()
                lines = [line.strip() for line in text_content.split('\n') if line.strip()]
                
                name, price = None, None
                for line in lines:
                    if ("RAM" in line.upper() or any(b in line.upper() for b in BRANDS)) and not name:
                        if len(line) > 6:
                            name = line
                    if ("฿" in line or "บาท" in line or re.search(r'^\d{1,2},\d{3}$', line) or re.search(r'^\d{3,5}$', line)) and not price:
                        price = line

                if name and price:
                    parsed = clean_and_parse_ram(name, price, "Advice")
                    if parsed:
                        results.append(parsed)
        except Exception as e:
            print(f"Advice Scraping Error on {url}: {e}")
            
    print(f"✅ Advice Total Filtered: {len(results)} items")
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
    msg['Subject'] = f"📊 รายงานราคา RAM Advice - DDR4(Bus 3200) & DDR5 ({status_msg})"

    body = f"สวัสดีครับ\n\nรายงานสรุปราคา RAM จาก Advice (DDR4 Bus 3200 & DDR5 All)\nสถานะ: {status_msg}\n\nดูรายละเอียดในไฟล์แนบได้เลยครับ"
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

        # สแครปเฉพาะ Advice เท่านั้น
        advice_data = await scrape_advice(page)
        await browser.close()

        file_name = "RAM_Advice_Prices.xlsx"
        
        if advice_data:
            df = pd.DataFrame(advice_data)
            df = df.drop_duplicates(subset=['Source', 'Model_Raw', 'Price'])
            
            summary_df = df.pivot_table(
                index=['Form_Factor', 'DDR_Type', 'Capacity', 'Bus_Speed', 'Brand', 'Part_Number'],
                values='Price',
                aggfunc='min'
            ).reset_index()

            with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name="Raw Cleaned", index=False)
                summary_df.to_excel(writer, sheet_name="Summary Price", index=False)

            status_msg = f"ดึง Advice สำเร็จ {len(df)} รายการ"
        else:
            df_empty = pd.DataFrame([{"Message": "No RAM matching criteria found"}])
            df_empty.to_excel(file_name, index=False)
            status_msg = "ไม่พบข้อมูล RAM"

        send_email_with_excel(file_name, status_msg)

if __name__ == "__main__":
    asyncio.run(main())
