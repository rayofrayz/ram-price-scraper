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

# ==========================================
# 1. CLEANING ENGINE
# ==========================================
BRANDS = ['ADATA', 'XPG', 'KINGSTON', 'CRUCIAL', 'CORSAIR', 'LEXAR', 'G.SKILL', 'TEAMGROUP', 'BLACKBERRY', 'PNY']

def clean_and_parse_ram(raw_title: str, price_str: str, source: str) -> dict:
    title_upper = raw_title.upper()
    
    # Clean Price
    clean_price = None
    if price_str:
        num_only = re.sub(r'[^\d.]', '', price_str)
        try:
            clean_price = float(num_only)
        except ValueError:
            clean_price = None

    # Clean Brand
    brand_found = "OTHER"
    for b in BRANDS:
        if re.search(rf'\b{b}\b', title_upper):
            brand_found = b
            break
            
    # Clean Form Factor
    form_factor = "Desktop (DIMM)"
    if any(k in title_upper for k in ['NB', 'NOTEBOOK', 'SO-DIMM', 'SODIMM', 'LAPTOP']):
        form_factor = "Notebook (SO-DIMM)"

    # Clean Specs
    ddr_match = re.search(r'DDR[345]', title_upper)
    ddr_type = ddr_match.group(0) if ddr_match else "UNKNOWN"

    cap_match = re.search(r'(\d+)\s*GB', title_upper)
    capacity = f"{cap_match.group(1)}GB" if cap_match else "UNKNOWN"

    bus_match = re.search(r'\b(2400|2666|3200|3600|4800|5200|5600|6000|6400|7200)\b', title_upper)
    bus_speed = f"{bus_match.group(1)}MHz" if bus_match else "UNKNOWN"

    # Clean Part Number
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

# ==========================================
# 2. SCRAPERS
# ==========================================
async def scrape_advice(page) -> list:
    results = []
    try:
        await page.goto("https://www.advice.co.th/product/ram-for-notebook/notebook-ddr4-3200-", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        products = await page.query_selector_all(".product-list-item, .product-box")
        for prod in products:
            name_elem = await prod.query_selector(".product-name, .name")
            price_elem = await prod.query_selector(".price, .price-online")
            if name_elem and price_elem:
                results.append(clean_and_parse_ram(await name_elem.inner_text(), await price_elem.inner_text(), "Advice"))
    except Exception as e:
        print(f"Advice Scraping Error: {e}")
    return results

async def scrape_jib(page) -> list:
    results = []
    try:
        await page.goto("https://www.jib.co.th/web/product/product_list/3/1026", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        await page.evaluate("window.scrollBy(0, 1500)")
        await page.wait_for_timeout(2000)
        products = await page.query_selector_all(".div_product_item, .prod_list_box")
        for prod in products:
            name_elem = await prod.query_selector(".title_product, .prod_name")
            price_elem = await prod.query_selector(".price_total, .price_cart")
            if name_elem and price_elem:
                results.append(clean_and_parse_ram(await name_elem.inner_text(), await price_elem.inner_text(), "JIB"))
    except Exception as e:
        print(f"JIB Scraping Error: {e}")
    return results

# ==========================================
# 3. EMAIL SENDER
# ==========================================
def send_email_with_excel(filepath):
    sender_email = os.environ.get("SENDER_EMAIL")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    receiver_email = os.environ.get("RECEIVER_EMAIL")

    if not sender_email or not app_password or not receiver_email:
        print("⚠️ Skipped email: Missing secrets environment variables.")
        return

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = "📊 รายงานเปรียบเทียบราคา RAM ประจำวัน (Advice vs JIB)"

    body = "สวัสดีครับ\n\nระบบดึงข้อมูลและทำ Clean Data ราคา RAM จาก Advice และ JIB เรียบร้อยแล้ว สามารถดูรายละเอียดในไฟล์ Excel ที่แนบมาได้เลยครับ"
    msg.attach(MIMEText(body, 'plain'))

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
        print("✉️ ส่ง Email สำเร็จเรียบร้อย!")
    except Exception as e:
        print(f"❌ ส่ง Email ไม่สำเร็จ: {e}")

# ==========================================
# 4. MAIN PIPELINE
# ==========================================
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900}
        )
        page = await context.new_page()

        print("🌐 กำลังดึงข้อมูลจาก Advice และ JIB...")
        advice_data = await scrape_advice(page)
        jib_data = await scrape_jib(page)
        await browser.close()

        all_data = advice_data + jib_data
        df = pd.DataFrame(all_data)

        if not df.empty:
            df = df.dropna(subset=['Price'])
            pivot_df = df.pivot_table(
                index=['Form_Factor', 'DDR_Type', 'Capacity', 'Bus_Speed', 'Brand', 'Part_Number'],
                columns='Source',
                values='Price',
                aggfunc='min'
            ).reset_index()

            file_name = "RAM_Competitor_Prices.xlsx"
            with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name="Raw Data Cleaned", index=False)
                pivot_df.to_excel(writer, sheet_name="Pivot Comparison", index=False)

            print(f"💾 บันทึกไฟล์ {file_name} สำเร็จ")
            send_email_with_excel(file_name)

if __name__ == "__main__":
    asyncio.run(main())
