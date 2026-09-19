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
    'SEAGATE', 'SANDISK', 'ACER', 'TRANSCEND'
]

# ข้อความปุ่ม "โหลดเพิ่ม" ที่เว็บไทยมักใช้ (เผื่อ SSD ใช้ pagination แทน infinite scroll)
LOAD_MORE_TEXTS = ["ดูเพิ่มเติม", "โหลดเพิ่ม", "แสดงเพิ่มเติม", "ดูสินค้าเพิ่มเติม", "Load More", "Show More"]

# หลายๆ selector ที่เป็นไปได้ เรียงจากเจาะจง -> กว้าง จะลองทีละอันจนกว่าจะเจอของ
CANDIDATE_SELECTORS = [
    "div[class*='product-box'], div[class*='product-card'], div[class*='product-item']",
    "div[class*='product']",
    "a[class*='product']",
    "li[class*='product']",
]

DEBUG_DIR = "debug"
os.makedirs(DEBUG_DIR, exist_ok=True)


async def dismiss_popups(page):
    """ปิด cookie/consent popup ถ้ามี (ไม่ throw ถ้าไม่เจอ)"""
    for txt in ["ยอมรับ", "ยอมรับทั้งหมด", "Accept", "Accept All", "ตกลง"]:
        try:
            btn = page.get_by_text(txt, exact=False)
            if await btn.count() > 0:
                await btn.first.click(timeout=2000)
                await page.wait_for_timeout(500)
        except Exception:
            pass


async def load_all_items(page, max_rounds=15):
    """สลับกันระหว่างกด 'โหลดเพิ่ม' กับ scroll ลง จนกว่าความสูงหน้าเว็บจะไม่เพิ่มแล้ว"""
    last_height = 0
    for _ in range(max_rounds):
        clicked = False
        for txt in LOAD_MORE_TEXTS:
            try:
                btn = page.get_by_text(txt, exact=False)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.scroll_into_view_if_needed()
                    await btn.first.click(timeout=3000)
                    await page.wait_for_timeout(1500)
                    clicked = True
            except Exception:
                pass

        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1200)

        height = await page.evaluate("document.body.scrollHeight")
        if height == last_height and not clicked:
            break
        last_height = height


async def find_working_selector(page):
    """ลอง selector ทีละตัว คืนตัวแรกที่เจอ element จริง"""
    for sel in CANDIDATE_SELECTORS:
        try:
            items = await page.query_selector_all(sel)
            if len(items) >= 3:  # กันกรณี match มั่วแค่ 1-2 ตัว
                return sel, items
        except Exception:
            continue
    return None, []


async def extract_generic_by_currency(page):
    """
    Fallback สุดท้าย: เดินหา element ที่ text มีสัญลักษณ์ ฿ อยู่
    แล้วปีนขึ้นไปหา container (parent) ที่ดูเหมือนการ์ดสินค้า (มีทั้งลิงก์และราคา)
    วิธีนี้ไม่ยึดกับชื่อ class เลย จึงทนต่อการเปลี่ยนดีไซน์เว็บได้ดีกว่า
    """
    raw = await page.evaluate(
        """
        () => {
            const out = [];
            const seen = new Set();
            const all = Array.from(document.querySelectorAll('a, div, li'));
            for (const el of all) {
                const text = (el.innerText || '').trim();
                if (!text.includes('฿')) continue;
                // เอาเฉพาะ element ที่ "เล็กพอ" จะเป็นการ์ดเดียว ไม่ใช่ container รวมทั้งหน้า
                if (text.length > 300) continue;
                const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                if (lines.length < 2) continue;
                const key = lines.slice(0, 2).join('|');
                if (seen.has(key)) continue;
                seen.add(key);
                out.push(lines);
            }
            return out;
        }
        """
    )
    return raw


def parse_lines_to_item(lines, category):
    """common parser: หาบรรทัด title กับ price จาก list ของบรรทัด text"""
    title, price_str = None, None
    for line in lines:
        line_u = line.upper()
        if category == "RAM" and not title and ("RAM" in line_u or "DDR" in line_u):
            if len(line) > 6 and not any(k in line_u for k in ['บาท', '฿', 'SPECIAL', 'SAVE']):
                title = line
        if category == "SSD" and not title and any(k in line_u for k in ['SSD', 'SOLID', 'NVME', 'SATA', 'M.2', 'PORTABLE']):
            if len(line) > 5 and not any(k in line_u for k in ['บาท', '฿', 'SPECIAL', 'SAVE']):
                title = line
        if not price_str and ("฿" in line or "บาท" in line or re.search(r'^\d{1,3}(,\d{3})*(\.\d+)?$', line)):
            price_str = line
    if not title and lines:
        # เผื่อไม่มีบรรทัดไหนตรงเงื่อนไข title แต่บรรทัดแรกๆ น่าจะเป็นชื่อสินค้า
        for line in lines[:3]:
            if len(line) > 8 and "฿" not in line and "บาท" not in line:
                title = line
                break
    return title, price_str


async def save_debug(page, name):
    """เซฟ screenshot + html เก็บไว้ debug เวลาสแครปได้ 0 รายการ"""
    try:
        await page.screenshot(path=os.path.join(DEBUG_DIR, f"{name}.png"), full_page=True)
        html = await page.content()
        with open(os.path.join(DEBUG_DIR, f"{name}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        print(f"🪲 บันทึกไฟล์ debug แล้ว: {name}.png / {name}.html")
    except Exception as e:
        print(f"⚠️ บันทึก debug ไม่สำเร็จ: {e}")


# =============================================================================
# 1. ฟังก์ชันสแครปเฉพาะ RAM (DDR4 Bus 3200 & DDR5 All)
# =============================================================================
async def scrape_ram(page) -> list:
    results = []
    urls = [
        "https://www.advice.co.th/product/ram-for-pc",
        "https://www.advice.co.th/product/ram-for-notebook",
    ]

    for url in urls:
        print(f"🌐 Scraping RAM: {url}")
        try:
            await page.goto(url, wait_until="networkidle", timeout=90000)
        except Exception:
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)

        await dismiss_popups(page)
        try:
            await page.wait_for_function("document.body.innerText.includes('฿')", timeout=20000)
        except Exception:
            print(f"⚠️ ไม่พบสัญลักษณ์ ฿ บนหน้า {url} หลังรอ 20s")

        await load_all_items(page)

        sel, items = await find_working_selector(page)
        raw_items_lines = []
        if items:
            print(f"   ✅ ใช้ selector: {sel} ({len(items)} elements)")
            for item in items:
                text = await item.inner_text()
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                raw_items_lines.append(lines)
        else:
            print("   ⚠️ ไม่เจอ selector ที่ match ได้ ใช้ fallback (ค้นหาด้วยสัญลักษณ์ ฿ แทน)")
            raw_items_lines = await extract_generic_by_currency(page)

        found_this_url = 0
        for lines in raw_items_lines:
            title, price_str = parse_lines_to_item(lines, "RAM")
            if not (title and price_str):
                continue

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
            except Exception:
                continue
            if price <= 0:
                continue

            is_sodimm = any(k in title_u for k in ['NB', 'NOTEBOOK', 'SO-DIMM', 'SODIMM', 'LAPTOP'])
            form_factor = "SO-DIMM (Notebook)" if is_sodimm else "U-DIMM (Desktop/Gaming)"

            cap_match = re.search(r'(\d+)\s*GB', title_u)
            capacity = f"{cap_match.group(1)}GB" if cap_match else "UNKNOWN"

            brand = "OTHER"
            for b in BRANDS:
                if re.search(rf'\b{re.escape(b)}\b', title_u):
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
                "Price": price,
            })
            found_this_url += 1

        print(f"   -> เก็บได้ {found_this_url} รายการจากหน้านี้")
        if found_this_url == 0:
            await save_debug(page, f"ram_{url.rstrip('/').split('/')[-1]}")

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
        "https://www.advice.co.th/product/ssd-solid-state-drive/ssd-sata-2-5-",
    ]

    for url in urls:
        print(f"🌐 Scraping SSD: {url}")
        try:
            await page.goto(url, wait_until="networkidle", timeout=90000)
        except Exception:
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)

        await dismiss_popups(page)
        try:
            await page.wait_for_function("document.body.innerText.includes('฿')", timeout=20000)
        except Exception:
            print(f"⚠️ ไม่พบสัญลักษณ์ ฿ บนหน้า {url} หลังรอ 20s")

        await load_all_items(page)

        sel, items = await find_working_selector(page)
        raw_items_lines = []
        if items:
            print(f"   ✅ ใช้ selector: {sel} ({len(items)} elements)")
            for item in items:
                text = await item.inner_text()
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                raw_items_lines.append(lines)
        else:
            print("   ⚠️ ไม่เจอ selector ที่ match ได้ ใช้ fallback (ค้นหาด้วยสัญลักษณ์ ฿ แทน)")
            raw_items_lines = await extract_generic_by_currency(page)

        found_this_url = 0
        for lines in raw_items_lines:
            title, price_str = parse_lines_to_item(lines, "SSD")
            if not (title and price_str):
                continue

            title_u = title.upper()
            try:
                price = float(re.sub(r'[^\d.]', '', price_str))
            except Exception:
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
                if re.search(rf'\b{re.escape(b)}\b', title_u):
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
                "Price": price,
            })
            found_this_url += 1

        print(f"   -> เก็บได้ {found_this_url} รายการจากหน้านี้")
        if found_this_url == 0:
            await save_debug(page, f"ssd_{url.rstrip('/').split('/')[-1]}")

    print(f"✅ Scraped SSD Total: {len(results)} items")
    return results


# =============================================================================
# 3. ฟังก์ชันส่ง Email
# =============================================================================
def send_email_with_excel(filepath, status_msg="", extra_attachments=None):
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

    body = f"สวัสดีครับ\n\nรายงานสรุปราคา RAM และ SSD จาก Advice\nสถานะ: {status_msg}\n\nดูรายละเอียดในไฟล์แนบได้เลยครับ"
    msg.attach(MIMEText(body, 'plain'))

    attachments = [filepath] + (extra_attachments or [])
    for path in attachments:
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename= {os.path.basename(path)}")
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
# 4. Main Workflow
# =============================================================================
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="th-TH",
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        ram_data = await scrape_ram(page)
        ssd_data = await scrape_ssd(page)

        await browser.close()

        all_data = ram_data + ssd_data
        file_name = "Hardware_Advice_Prices.xlsx"

        if all_data:
            df = pd.DataFrame(all_data)
            df = df.drop_duplicates(subset=['Source', 'Model_Raw', 'Price'])

            ram_df = df[df['Category'] == 'RAM']
            ssd_df = df[df['Category'] == 'SSD']

            with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name="All Hardware Cleaned", index=False)
                if not ram_df.empty:
                    ram_df.to_excel(writer, sheet_name="RAM Summary", index=False)
                if not ssd_df.empty:
                    ssd_df.to_excel(writer, sheet_name="SSD Summary", index=False)

            status_msg = f"ดึงสำเร็จรวม {len(df)} รายการ (RAM: {len(ram_df)}, SSD: {len(ssd_df)})"
        else:
            df_empty = pd.DataFrame([{"Message": "No data matching criteria found"}])
            df_empty.to_excel(file_name, index=False)
            status_msg = "ไม่พบข้อมูลสินค้า"

        # แนบไฟล์ debug (ถ้ามี) ไปกับอีเมลด้วย จะได้เห็นว่าทำไมถึงดึงไม่ได้
        extra = []
        if os.path.isdir(DEBUG_DIR):
            for f in os.listdir(DEBUG_DIR):
                extra.append(os.path.join(DEBUG_DIR, f))

        send_email_with_excel(file_name, status_msg, extra_attachments=extra)


if __name__ == "__main__":
    asyncio.run(main())
