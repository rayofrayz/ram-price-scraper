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

BRANDS = ['ADATA', 'XPG', 'KINGSTON', 'CRUCIAL', 'CORSAIR', 'LEXAR', 'G.SKILL', 'TEAMGROUP', 'BLACKBERRY', 'PNY']

def clean_and_parse_ram(raw_title: str, price_str: str, source: str) -> dict:
    title_upper = raw_title.upper()
    clean_price = None
    if price_str:
        num_only = re.sub(r'[^\d.]', '', price_str)
        try:
            clean_price = float(num_only)
        except ValueError:
            clean_price = None

    brand_found = "OTHER"
    for b in BRANDS:
        if re.search(rf'\b{b}\b', title_upper):
            brand_found = b
            break
            
    form_factor = "Desktop (DIMM)"
    if any(k in title_upper for k in ['NB', 'NOTEBOOK', 'SO-DIMM', 'SODIMM', 'LAPTOP']):
        form_factor = "Notebook (SO-DIMM)"

    ddr_match = re.search(r'DDR[345]', title_upper)
    ddr_type = ddr_match.group(0) if ddr_match else "UNKNOWN"

    cap_match = re.search(r'(\d+)\s*GB', title_upper)
    capacity = f"{cap_match.group(1)}GB" if cap_match else "UNKNOWN"

    bus_match = re.search(r'\b(2400|2666|3200|3600|4800|5200|5600|6000|6400|7200)\b', title_upper)
    bus_speed = f"{bus_match.group(1)}MHz" if bus_match else "UNKNOWN"

    part_number = "N/A"
    pn_match = re.search(r'\(([^)]+)\)', raw_title)
    if pn_match:
        part_number = pn_match.group(1).strip()
    else:
        for token in raw_title.split():
            clean_t = token.strip('(),')
            if re.match(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9\-/]{6,20}$', clean_t):
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
    try:
        print("🌐 Scraping Advice...")
        # ใช้ URL หน้าหมวดหมู่ RAM โดยตรง
        await page.goto("https://www.advice.co.th/product/ram-for-notebook", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        
        # เลื่อนหน้าจอเพื่อโหลด Lazy Load Images/Data
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 1000)")
            await page.wait_for_timeout(1000)

        # สกัดข้อมูลจากโครงสร้าง HTML ของ Advice
        items = await page.query_selector_all(".product-box, .product-list-item, div[class*='product']")
        for item in items:
            text_content = await item.inner_text()
            lines = [line.strip() for line in text_content.split('\n') if line.strip()]
            
            # ค้นหาบรรทัดที่มีข้อความ RAM และราคาบาท
            name = None
            price = None
            for line in lines:
                if "RAM" in line.upper() and not name:
                    name = line
                if ("฿" in line or "บาท" in line or re.search(r'^\d{3,5}$', line.replace(',', ''))) and not price:
                    price = line

            if name and price:
                results.append(clean_and_parse_ram(name, price, "Advice"))
    except Exception as e:
        print(f"Advice Scraping Error: {e}")
    print(f"✅ Advice Scraped: {len(results)} items")
    return results

async def scrape_jib(page) -> list:
    results = []
    try:
        print("🌐 Scraping JIB...")
        await page.goto("https://www.jib.co.th/web/product/product_list/3/1026", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 1000)")
            await page.wait_for_timeout(1000)

        items = await page.query_selector_all(".div_product_item, .prod_list_box, div[class*='product']")
        for item in items:
            text_content = await item.inner_text()
            lines = [line.strip() for line in text_content.split('\n') if line.strip()]
            
            name = None
            price = None
            for line in lines:
                if any(b in line.upper() for b in BRANDS) and not name:
                    name = line
                if ("บาท" in line or "฿" in line or re.search(r'^\d{1,2},\d{3}$', line)) and not price:
                    price = line

            if name and price:
                results.append(clean_and_parse_ram(name, price, "JIB"))
    except Exception as e:
        print(f"JIB Scraping Error: {e}")
    print(f"✅ JIB Scraped: {len(results)} items")
    return results

def send_email_with_excel(filepath, status_msg=""):
    sender_email = os.environ.get("SENDER_EMAIL")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    receiver_email = os.environ.get("RECEIVER_EMAIL")

    if not sender_email or not app_password or not receiver_email:
        print("❌ Environment variables missing!")
        return

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = f"📊 รายงานเปรียบเทียบราคา RAM ประจำวัน ({status_msg})"

    body = f"สวัสดีครับ\n\nรายงานสรุปราคา RAM ประจำวัน\nสถานะ: {status_msg}\n\nดูรายละเอียดในไฟล์แนบได้เลยครับ"
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
        server.send_message(msg)
        server.quit()
        print("✉️ ส่ง Email สำเร็จ!")
    except Exception as e:
        print(f"❌ SMTP Error: {e}")

async def main():
    async with async_playwright() as p:
        # เปิด เบราว์เซอร์ พร้อมเทคนิค Bypass Anti-Bot
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="th-TH"
        )
        page = await context.new_page()
        
        # ปลอมตัวไม่ให้เว็บรู้ว่าเป็น Playwright/Automation
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        advice_data = await scrape_advice(page)
        jib_data = await scrape_jib(page)
        await browser.close()

        all_data = advice_data + jib_data
        file_name = "RAM_Competitor_Prices.xlsx"
        
        if all_data:
            df = pd.DataFrame(all_data)
            df = df.dropna(subset=['Price'])
            
            pivot_df = df.pivot_table(
                index=['Form_Factor', 'DDR_Type', 'Capacity', 'Bus_Speed', 'Brand', 'Part_Number'],
                columns='Source',
                values='Price',
                aggfunc='min'
            ).reset_index()

            with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name="Raw Data Cleaned", index=False)
                pivot_df.to_excel(writer, sheet_name="Pivot Comparison", index=False)

            status_msg = f"ดึงข้อมูลสำเร็จทั้งหมด {len(df)} รายการ"
        else:
            df_empty = pd.DataFrame([{"Message": "No data scraped today"}])
            df_empty.to_excel(file_name, index=False)
            status_msg = "ไม่สามารถสกัดข้อมูลได้ (ถูก Anti-Bot บล็อก)"

        send_email_with_excel(file_name, status_msg)

if __name__ == "__main__":
    asyncio.run(main())
