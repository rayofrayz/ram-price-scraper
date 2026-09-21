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
    'COLORFUL', 'HIKVISION', 'HIKSEMI', 'APACER', 'GALAX', 'WESTERN DIGITAL', 'WD', 
    'SEAGATE', 'SANDISK', 'ACER', 'TRANSCEND', 'TOSHIBA', 'KIOXIA', 'PHILIPS'
]

# =============================================================================
# 1. ฟังก์ชันสแครปเฉพาะ RAM (DDR4 Bus 3200 & DDR5 All)
# =============================================================================
async def scrape_ram(page) -> list:
    results = []
    urls = [
        "https://www.advice.co.th/product/ram-for-pc",
        "https://www.advice.co.th/product/ram-for-notebook"
    ]
    
    for url in urls:
        try:
            print(f"🌐 Scraping RAM: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            
            for _ in range(8):
                await page.evaluate("window.scrollBy(0, 2000)")
                await page.wait_for_timeout(800)

            items = await page.query_selector_all("div[class*='product'], .product-box, .product-card")
            
            for item in items:
                text = await item.inner_text()
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                
                title, price_str = None, None
                for line in lines:
                    line_u = line.upper()
                    if ("RAM" in line_u or "DDR" in line_u) and not title:
                        if len(line) > 6 and not any(k in line_u for k in ['บาท', '฿', 'SPECIAL', 'SAVE']):
                            title = line
                    if ("฿" in line or "บาท" in line or re.search(r'^\d{1,2},\d{3}$', line) or re.search(r'^\d{3,5}$', line)) and not price_str:
                        price_str = line

                if title and price_str:
                    title_u = title.upper()
                    
                    ddr_type = 'DDR5' if 'DDR5' in title_u else ('DDR4' if 'DDR4' in title_u else None)
                    if not ddr_type:
                        continue
                        
                    bus_match = re.search(r'\b(2133|2400|2666|2933|3200|3600|4800|5200|5600|6000|6200|6400|6600|6800|7200|7600|8000)\b', title_u)
                    bus_speed = f"{bus_match.group(1)}MHz" if bus_match else "UNKNOWN"
                    
                    if ddr_type == "DDR4" and bus_speed != "3200MHz" and bus_speed != "UNKNOWN":
                        continue

                    try:
                        price = float(re.sub(r'[^\d.]', '', price_str))
                    except:
                        continue

                    is_sodimm = any(k in title_u for k in ['NB', 'NOTEBOOK', 'SO-DIMM', 'SODIMM', 'LAPTOP'])
                    form_factor = "SO-DIMM (Notebook)" if is_sodimm else "U-DIMM (Desktop/Gaming)"
                    
                    cap_match = re.search(r'(\d+)\s*GB', title_u)
                    capacity = f"{cap_match.group(1)}GB" if cap_match else "UNKNOWN"
                    
                    brand = "OTHER"
                    for b in BRANDS:
                        if re.search(rf'\b{b}\b', title_u):
                            brand = b
                            break

                    results.append({
                        "Source": "Advice",
                        "Category": "RAM",
                        "Brand": brand,
                        "Model_Raw": title.strip(),
                        "Form_Factor": form_factor,
                        "Tech_Spec": f"{ddr_type} ({bus_speed})",
                        "Capacity": capacity,
                        "Price": price
                    })
        except Exception as e:
            print(f"RAM Scraping Error on {url}: {e}")
            
    print(f"✅ Scraped RAM Total: {len(results)} items")
    return results

# =============================================================================
# 2. ฟังก์ชันสแครปเฉพาะ SSD (M.2 NVMe/SATA & 2.5" SATA)
# =============================================================================
async def scrape_ssd(page) -> list:
    results = []
    urls = [
        "https://www.advice.co.th/product/ssd-solid-state-drive",
        "https://www.advice.co.th/product/ssd-solid-state-drive/ssd-m-2-nvme",
        "https://www.advice.co.th/product/ssd-solid-state-drive/ssd-sata-2-5-"
    ]
    
    for url in urls:
        try:
            print(f"🌐 Scraping SSD: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            
            for _ in range(10):
                await page.evaluate("window.scrollBy(0, 2000)")
                await page.wait_for_timeout(800)

            items = await page.query_selector_all("div[class*='product'], .product-box, .product-card, .product-item")
            
            for item in items:
                text = await item.inner_text()
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                
                title, price_str = None, None
                for line in lines:
                    line_u = line.upper()
                    if any(k in line_u for k in ['SSD', 'SOLID', 'NVME', 'SATA', 'M.2', 'PORTABLE']) and not title:
                        if len(line) > 5 and not any(k in line_u for k in ['บาท', '฿', 'SPECIAL', 'SAVE']):
                            title = line
                    if ("฿" in line or "บาท" in line or re.search(r'^\d{1,2},\d{3}$', line) or re.search(r'^\d{3,5}$', line)) and not price_str:
                        price_str = line

                if title and price_str:
                    title_u = title.upper()
                    
                    try:
                        price = float(re.sub(r'[^\d.]', '', price_str))
                    except:
                        continue
                        
                    if price <= 0:
                        continue

                    if any(k in title_u for k in ['M.2', 'NVME', '2280', 'PCIE']):
                        form_factor = 'M.2'
                        tech_spec = 'M.2 NVMe PCIe' if any(k in title_u for k in ['NVME', 'PCIE', 'GEN']) else 'M.2 SATA'
                    elif any(k in title_u for k in ['2.5', 'SATA3', 'SATA III', 'SATA']):
                        form_factor = '2.5 inch'
                        tech_spec = 'SATA III (2.5")'
                    else:
                        form_factor = 'SSD (General)'
                        tech_spec = 'SATA / NVMe'

                    cap_match = re.search(r'(\d+)\s*(GB|TB)', title_u)
                    capacity = f"{cap_match.group(1)}{cap_match.group(2)}" if cap_match else "UNKNOWN"

                    brand = "OTHER"
                    for b in BRANDS:
                        if re.search(rf'\b{b}\b', title_u):
                            brand = b
                            break

                    results.append({
                        "Source": "Advice",
                        "Category": "SSD",
                        "Brand": brand,
                        "Model_Raw": title.strip(),
                        "Form_Factor": form_factor,
                        "Tech_Spec": tech_spec,
                        "Capacity": capacity,
                        "Price": price
                    })
        except Exception as e:
            print(f"SSD Scraping Error on {url}: {e}")
            
    print(f"✅ Scraped SSD Total: {len(results)} items")
    return results

# =============================================================================
# 3. ฟังก์ชันสแครป MicroSD Card, SD Card และ Flash Drive
# =============================================================================
async def scrape_storage_media(page) -> list:
    results = []
    urls = [
        "https://www.advice.co.th/product/memory-flashdrive-reader",
        "https://www.advice.co.th/product/memory-card/micro-sd-card",
        "https://www.advice.co.th/product/memory-card/sd-card",
        "https://www.advice.co.th/product/flash-drive"
    ]
    
    for url in urls:
        try:
            print(f"🌐 Scraping Storage Media: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            
            for _ in range(10):
                await page.evaluate("window.scrollBy(0, 2000)")
                await page.wait_for_timeout(800)

            items = await page.query_selector_all("div[class*='product'], .product-box, .product-card, .product-item")
            
            for item in items:
                text = await item.inner_text()
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                
                title, price_str = None, None
                for line in lines:
                    line_u = line.upper()
                    if any(k in line_u for k in ['MICROSD', 'MICRO SD', 'SD CARD', 'SDHC', 'SDXC', 'FLASH DRIVE', 'FLASHDRIVE', 'THUMB DRIVE', 'MEMO CARD', 'GB', 'TB']) and not title:
                        if len(line) > 5 and not any(k in line_u for k in ['บาท', '฿', 'SPECIAL', 'SAVE']):
                            title = line
                    if ("฿" in line or "บาท" in line or re.search(r'^\d{1,2},\d{3}$', line) or re.search(r'^\d{3,5}$', line)) and not price_str:
                        price_str = line

                if title and price_str:
                    title_u = title.upper()
                    
                    try:
                        price = float(re.sub(r'[^\d.]', '', price_str))
                    except:
                        continue
                        
                    if price <= 0:
                        continue

                    # ระบุประเภท (MicroSD, SD Card, Flash Drive)
                    if any(k in title_u for k in ['MICROSD', 'MICRO SD', 'MICRO-SD']):
                        category = 'MicroSD Card'
                        form_factor = 'MicroSD'
                    elif any(k in title_u for k in ['SD CARD', 'SDHC', 'SDXC']) and 'MICRO' not in title_u:
                        category = 'SD Card'
                        form_factor = 'SD Card'
                    elif any(k in title_u for k in ['FLASH DRIVE', 'FLASHDRIVE', 'THUMB DRIVE', 'USB']):
                        category = 'Flash Drive'
                        form_factor = 'USB Drive'
                    else:
                        category = 'Storage Media'
                        form_factor = 'General'

                    spec_match = re.search(r'(USB\s*\d\.\d|CLASS\s*10|U1|U3|V30|A1|A2|UHS-[I|II]+)', title_u)
                    tech_spec = spec_match.group(0) if spec_match else "Standard"

                    cap_match = re.search(r'(\d+)\s*(GB|TB)', title_u)
                    capacity = f"{cap_match.group(1)}{cap_match.group(2)}" if cap_match else "UNKNOWN"

                    brand = "OTHER"
                    for b in BRANDS:
                        if re.search(rf'\b{b}\b', title_u):
                            brand = b
                            break

                    results.append({
                        "Source": "Advice",
                        "Category": category,
                        "Brand": brand,
                        "Model_Raw": title.strip(),
                        "Form_Factor": form_factor,
                        "Tech_Spec": tech_spec,
                        "Capacity": capacity,
                        "Price": price
                    })
        except Exception as e:
            print(f"Storage Media Scraping Error on {url}: {e}")
            
    print(f"✅ Scraped Storage Media Total: {len(results)} items")
    return results

# =============================================================================
# 4. ฟังก์ชันส่ง Email
# =============================================================================
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
    msg['Subject'] = f"📊 รายงานราคา Hardware, RAM, SSD & Flash Drive Advice ({status_msg})"

    body = f"สวัสดีครับ\n\nรายงานสรุปราคา RAM, SSD และ Flash Drive / Memory Card จาก Advice\nสถานะ: {status_msg}\n\nดูรายละเอียดในไฟล์แนบได้เลยครับ"
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

# =============================================================================
# 5. Main Workflow
# =============================================================================
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

        # เรียกทำงานแยกฟังก์ชันทั้ง 3 หมวด
        ram_data = await scrape_ram(page)
        ssd_data = await scrape_ssd(page)
        media_data = await scrape_storage_media(page)
        
        await browser.close()

        all_data = ram_data + ssd_data + media_data
        file_name = "Hardware_Advice_Prices.xlsx"
        
        if all_data:
            df = pd.DataFrame(all_data)
            df = df.drop_duplicates(subset=['Source', 'Model_Raw', 'Price'])
            
            ram_df = df[df['Category'] == 'RAM']
            ssd_df = df[df['Category'] == 'SSD']
            media_df = df[df['Category'].isin(['MicroSD Card', 'SD Card', 'Flash Drive', 'Storage Media'])]

            with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name="All Hardware Cleaned", index=False)
                if not ram_df.empty:
                    ram_df.to_excel(writer, sheet_name="RAM Summary", index=False)
                if not ssd_df.empty:
                    ssd_df.to_excel(writer, sheet_name="SSD Summary", index=False)
                if not media_df.empty:
                    media_df.to_excel(writer, sheet_name="FlashDrive & MemoryCard", index=False)

            status_msg = f"ดึงสำเร็จรวม {len(df)} รายการ (RAM: {len(ram_df)}, SSD: {len(ssd_df)}, Media: {len(media_df)})"
        else:
            df_empty = pd.DataFrame([{"Message": "No data matching criteria found"}])
            df_empty.to_excel(file_name, index=False)
            status_msg = "ไม่พบข้อมูลสินค้า"

        send_email_with_excel(file_name, status_msg)

if __name__ == "__main__":
    asyncio.run(main())
