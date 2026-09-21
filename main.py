import asyncio
import json
import os
import random
import re
import smtplib
from datetime import datetime, timezone, timedelta
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
    'SEAGATE', 'SANDISK', 'ACER', 'TRANSCEND',
    # เพิ่มสำหรับ memory card / flash drive
    'SILICON POWER', 'STRONTIUM', 'TOSHIBA', 'SONY', 'HP', 'EAGET', 'VERBATIM', 'PATRIOT',
]

# ข้อความปุ่ม "โหลดเพิ่ม" ที่เว็บไทยมักใช้ (เผื่อ SSD ใช้ pagination แทน infinite scroll)
LOAD_MORE_TEXTS = ["ดูเพิ่มเติม", "โหลดเพิ่ม", "แสดงเพิ่มเติม", "ดูสินค้าเพิ่มเติม", "Load More", "Show More"]

# ข้อความที่บ่งบอกว่าเว็บกำลังแสดงหน้า "ไม่พบสินค้า" (อาจเกิดจาก anti-bot / rate limit
# หลังจากสแครปหน้าอื่นมาอย่างหนักในเซสชันเดียวกัน ไม่ใช่ URL ผิดเสมอไป)
BLOCKED_MARKERS = [
    "สินค้าที่คุณค้นหาไม่พบแล้ว",
    "ไม่พบสินค้าที่คุณค้นหา",
    "กลับสู่หน้าแรก",
]


async def is_blocked_or_notfound(page) -> bool:
    try:
        text = await page.inner_text("body")
    except Exception:
        return False
    return any(marker in text for marker in BLOCKED_MARKERS)


async def human_pause(a=2.0, b=6.0):
    """หน่วงเวลาสุ่มเลียนแบบมนุษย์ ลดโอกาสโดน anti-bot ตรวจจับ"""
    await asyncio.sleep(random.uniform(a, b))


async def safe_goto(page, url, max_retries=3):
    """
    เปิดหน้าเว็บพร้อม retry อัตโนมัติถ้าเจอหน้า 'ไม่พบสินค้า' (ซึ่งมักเป็นสัญญาณ
    ของ anti-bot/rate-limit มากกว่า URL ผิด เพราะ URL เดิมนี้ยืนยันแล้วว่ามีอยู่จริง)
    คืนค่า True ถ้าเปิดสำเร็จและไม่โดนบล็อก, False ถ้าลองจนครบแล้วยังไม่สำเร็จ
    """
    for attempt in range(1, max_retries + 1):
        await human_pause(2.5, 6.0)  # หน่วงก่อนเปิดหน้าทุกครั้ง ไม่ยิงรัวๆ ติดกัน
        try:
            await page.goto(url, wait_until="networkidle", timeout=90000)
        except Exception:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            except Exception as e:
                print(f"   ❌ เปิดหน้าไม่สำเร็จ (attempt {attempt}): {e}")
                continue

        await dismiss_popups(page)

        if await is_blocked_or_notfound(page):
            print(f"   🚫 เจอหน้า 'ไม่พบสินค้า' (attempt {attempt}/{max_retries}) - อาจโดน anti-bot, กำลังลองใหม่...")
            # พักยาวขึ้นทุกครั้งที่ลองใหม่ (backoff) และลองรีเฟรชแทนการ goto ซ้ำ
            await human_pause(6.0 + attempt * 4, 12.0 + attempt * 4)
            try:
                await page.reload(wait_until="networkidle", timeout=90000)
            except Exception:
                pass
            if not await is_blocked_or_notfound(page):
                return True
            continue

        return True

    return False

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
        await page.wait_for_timeout(random.randint(900, 1800))  # จังหวะสุ่มเล็กน้อย ลด pattern แบบบอท

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
        if category == "SSD" and not title and any(k in line_u for k in ['SSD', 'SOLID', 'NVME', 'M.2', '2280', '2242', '2230']):
            if len(line) > 5 and not any(k in line_u for k in ['บาท', '฿', 'SPECIAL', 'SAVE']):
                title = line
        if category == "FLASH" and not title and any(k in line_u for k in [
            'SD CARD', 'MICROSD', 'MICRO SD', 'TF CARD', 'FLASH DRIVE', 'FLASHDRIVE',
            'CARD READER', 'OTG', 'PENDRIVE', 'THUMB DRIVE', 'USB',
        ]):
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


async def save_debug(page, name, console_log=None):
    """เซฟ screenshot + html + console/JS error log เก็บไว้ debug เวลาสแครปได้ 0 รายการ"""
    try:
        await page.screenshot(path=os.path.join(DEBUG_DIR, f"{name}.png"), full_page=True)
        html = await page.content()
        with open(os.path.join(DEBUG_DIR, f"{name}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        if console_log:
            with open(os.path.join(DEBUG_DIR, f"{name}_console.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(console_log) if console_log else "(ไม่มี console log)")
        print(f"🪲 บันทึกไฟล์ debug แล้ว: {name}.png / {name}.html / {name}_console.txt")
    except Exception as e:
        print(f"⚠️ บันทึก debug ไม่สำเร็จ: {e}")


def attach_console_logger(page):
    """ติดตาม console message และ JS error ทั้งหมดของหน้านี้ คืน list ที่จะถูกเติมเรื่อยๆ
    ใช้เพื่อ debug ว่าทำไมหน้าถึง render ไม่ได้ (error อะไรที่เกิดขึ้นจริงในเบราว์เซอร์)"""
    log = []

    def on_console(msg):
        try:
            log.append(f"[console:{msg.type}] {msg.text}")
        except Exception:
            pass

    def on_pageerror(exc):
        log.append(f"[pageerror] {exc}")

    def on_requestfailed(req):
        try:
            log.append(f"[requestfailed] {req.method} {req.url} -> {req.failure}")
        except Exception:
            pass

    def on_response(resp):
        try:
            if resp.status >= 400:
                log.append(f"[http {resp.status}] {resp.url}")
        except Exception:
            pass

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("requestfailed", on_requestfailed)
    page.on("response", on_response)
    return log


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
        console_log = attach_console_logger(page)
        ok = await safe_goto(page, url)
        if not ok:
            print(f"   🚫 เปิดหน้านี้ไม่สำเร็จหลังลองหลายครั้ง (ยังเจอหน้า 'ไม่พบสินค้า'): {url}")
            await save_debug(page, f"ram_{url.rstrip('/').split('/')[-1]}_blocked", console_log)
            continue

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
            await save_debug(page, f"ram_{url.rstrip('/').split('/')[-1]}", console_log)

    print(f"✅ Scraped RAM Total: {len(results)} items")
    return results


# =============================================================================
# 2. ฟังก์ชันสแครปเฉพาะ SSD (M.2 NVMe/SATA & 2.5" SATA)
# =============================================================================
async def scrape_ssd(page) -> list:
    results = []
    # ยืนยันแล้วจากการเปิดจริงในเบราว์เซอร์ปกติ: หน้านี้แสดงสินค้า SSD พร้อมราคาได้ถูกต้อง
    # (ต่างจาก solid-state-drive-ssd- ที่แม้ URL จะถูกต้องแต่ headless render ไม่ขึ้น)
    # หมายเหตุ: หน้านี้เป็นหมวดรวม "SSD / HARD DISK / STORAGE" อาจมี HDD ปนอยู่ด้วย
    # จึงต้องกรองด้วย title keyword ที่เจาะจง SSD จริงๆ เท่านั้น (ดู parse_lines_to_item)
    urls = [
        "https://www.advice.co.th/product/harddisk-storage",
    ]

    for url in urls:
        print(f"🌐 Scraping SSD: {url}")
        console_log = attach_console_logger(page)
        ok = await safe_goto(page, url)
        if not ok:
            print(f"   🚫 เปิดหน้านี้ไม่สำเร็จหลังลองหลายครั้ง (ยังเจอหน้า 'ไม่พบสินค้า'): {url}")
            await save_debug(page, f"ssd_{url.rstrip('/').split('/')[-1]}_blocked", console_log)
            continue

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

            # กันเคสที่หลุดมาจาก fallback title-guessing แล้วดันเป็น HDD จานหมุนธรรมดา
            # (หมวดนี้เป็น "SSD / HARD DISK / STORAGE" รวมกัน มี HDD ปนอยู่ด้วย)
            if not any(k in title_u for k in ['SSD', 'SOLID', 'NVME', 'M.2']):
                continue
            if any(k in title_u for k in ['HDD', 'HARDDISK', 'HARD DISK', 'RPM', '7200RPM', '5400RPM']):
                continue

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
            await save_debug(page, f"ssd_{url.rstrip('/').split('/')[-1]}", console_log)
        elif found_this_url >= 20:
            # หน้าหลักได้ข้อมูลเพียงพอแล้ว ข้ามหน้าย่อยที่เหลือ ลดจำนวน request ที่อาจโดนจับได้
            print("   ✅ ได้ข้อมูลเพียงพอจากหน้านี้แล้ว ข้ามหน้าย่อยที่เหลือเพื่อลดความเสี่ยงโดนบล็อก")
            break

    print(f"✅ Scraped SSD Total: {len(results)} items")
    return results


# =============================================================================
# 2.5 ฟังก์ชันสแครป Memory Card / Flash Drive (MicroSD, SD Card, Flash Drive, Card Reader)
# =============================================================================
async def scrape_flash(page) -> list:
    results = []
    # หน้ารวม MEMORY CARD / FLASH DRIVE - ยืนยันว่า render แบบ SSR จริง (มีเนื้อหาจริงใน raw HTML
    # ไม่ใช่แค่ "loading...") เหมือนกับ harddisk-storage ที่ใช้งานได้จริงสำหรับ SSD
    urls = [
        "https://www.advice.co.th/product/memory-flashdrive-reader",
    ]

    for url in urls:
        print(f"🌐 Scraping FLASH: {url}")
        console_log = attach_console_logger(page)
        ok = await safe_goto(page, url)
        if not ok:
            print(f"   🚫 เปิดหน้านี้ไม่สำเร็จหลังลองหลายครั้ง (ยังเจอหน้า 'ไม่พบสินค้า'): {url}")
            await save_debug(page, f"flash_{url.rstrip('/').split('/')[-1]}_blocked", console_log)
            continue

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
            title, price_str = parse_lines_to_item(lines, "FLASH")
            if not (title and price_str):
                continue

            title_u = title.upper()

            # ต้องมีคำที่บ่งบอกว่าเป็นสินค้ากลุ่มนี้จริงๆ (กันของอื่นที่หลุดมาจาก fallback)
            if not any(k in title_u for k in [
                'SD CARD', 'MICROSD', 'MICRO SD', 'TF CARD', 'FLASH DRIVE', 'FLASHDRIVE',
                'CARD READER', 'OTG', 'PENDRIVE', 'THUMB DRIVE', 'USB',
            ]):
                continue

            try:
                price = float(re.sub(r'[^\d.]', '', price_str))
            except Exception:
                continue
            if price <= 0:
                continue

            # จำแนกประเภทย่อย (เก็บไว้ในคอลัมน์ Form_Factor เหมือน RAM/SSD เพื่อให้ schema ตรงกัน)
            if any(k in title_u for k in ['MICROSD', 'MICRO SD', 'TF CARD']):
                sub_type = 'MicroSD Card'
            elif 'SD CARD' in title_u or re.search(r'\bSD\b', title_u):
                sub_type = 'SD Card'
            elif 'CARD READER' in title_u or 'READER' in title_u:
                sub_type = 'Card Reader'
            elif any(k in title_u for k in ['FLASH DRIVE', 'FLASHDRIVE', 'OTG', 'PENDRIVE', 'THUMB DRIVE']):
                sub_type = 'Flash Drive'
            else:
                sub_type = 'USB Storage (General)'

            # อินเทอร์เฟซ/ความเร็ว ถ้าระบุไว้ในชื่อสินค้า
            if 'USB 3.2' in title_u or 'USB3.2' in title_u:
                tech_spec = 'USB 3.2'
            elif 'USB 3.1' in title_u or 'USB3.1' in title_u:
                tech_spec = 'USB 3.1'
            elif 'USB 3.0' in title_u or 'USB3.0' in title_u or 'USB 3' in title_u:
                tech_spec = 'USB 3.0'
            elif 'USB 2.0' in title_u or 'USB2.0' in title_u:
                tech_spec = 'USB 2.0'
            elif 'UHS-I' in title_u or 'UHS-1' in title_u:
                tech_spec = 'UHS-I'
            elif 'UHS-II' in title_u or 'UHS-2' in title_u:
                tech_spec = 'UHS-II'
            elif re.search(r'CLASS\s*10', title_u):
                tech_spec = 'Class 10'
            elif 'TYPE-C' in title_u or 'TYPE C' in title_u:
                tech_spec = 'USB Type-C'
            else:
                tech_spec = 'UNKNOWN'

            cap_match = re.search(r'(\d+)\s*(GB|TB)', title_u)
            capacity = f"{cap_match.group(1)}{cap_match.group(2)}" if cap_match else "UNKNOWN"

            brand = "OTHER"
            for b in BRANDS:
                if re.search(rf'\b{re.escape(b)}\b', title_u):
                    brand = b
                    break

            results.append({
                "Source": "Advice",
                "Category": "FLASH",
                "Brand": brand,
                "Model_Raw": title.strip(),
                "Form_Factor": sub_type,
                "Tech_Spec": tech_spec,
                "Capacity": capacity,
                "Price": price,
            })
            found_this_url += 1

        print(f"   -> เก็บได้ {found_this_url} รายการจากหน้านี้")
        if found_this_url == 0:
            await save_debug(page, f"flash_{url.rstrip('/').split('/')[-1]}", console_log)

    print(f"✅ Scraped FLASH Total: {len(results)} items")
    return results


# =============================================================================
# 2.9 ฟังก์ชันสร้าง Dashboard (HTML แบบ static ฝังข้อมูลไว้ในตัว สำหรับ host บน GitHub Pages)
# =============================================================================
def build_dashboard_html(records: list, generated_at: str) -> str:
    """
    สร้างหน้า dashboard HTML แบบ self-contained ไฟล์เดียว ไม่ต้องมี backend
    ฝังข้อมูลสินค้าทั้งหมดไว้เป็น JSON ในตัวไฟล์เลย เปิดดูได้ทันทีผ่าน GitHub Pages
    เรียกใหม่ทุกครั้งที่ scraper รัน แล้ว commit ไฟล์นี้กลับเข้า repo (ดูคำแนะนำ workflow)
    """
    safe_json = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")

    return """<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Advice Hardware Price Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Kanit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.4/chart.umd.min.js"></script>
<style>
  :root {
    --blue: #0b5ed7;
    --blue-dark: #073e91;
    --bg: #f4f7fb;
    --card: #ffffff;
    --text: #1a2233;
    --muted: #66738f;
    --border: #e3e8f0;
    --ram: #0b5ed7;
    --ssd: #16a37a;
    --flash: #e8792c;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: 'Kanit', sans-serif;
    background: var(--bg);
    color: var(--text);
  }
  header {
    background: linear-gradient(135deg, var(--blue-dark), var(--blue));
    color: #fff;
    padding: 28px 24px;
  }
  header h1 { margin: 0 0 4px 0; font-size: 24px; font-weight: 600; }
  header p { margin: 0; opacity: 0.85; font-size: 14px; }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 24px; }
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 14px;
    margin-bottom: 24px;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 18px;
  }
  .card .label { font-size: 13px; color: var(--muted); margin-bottom: 6px; }
  .card .value { font-size: 22px; font-weight: 600; }
  .card .sub { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .tabs {
    display: flex;
    gap: 8px;
    margin-bottom: 18px;
    flex-wrap: wrap;
  }
  .tab-btn {
    border: 1px solid var(--border);
    background: #fff;
    padding: 8px 16px;
    border-radius: 999px;
    cursor: pointer;
    font-family: inherit;
    font-size: 14px;
    color: var(--text);
    transition: all .15s;
  }
  .tab-btn.active { background: var(--blue); color: #fff; border-color: var(--blue); }
  .panel {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 18px;
    margin-bottom: 20px;
  }
  .panel h2 { margin: 0 0 14px 0; font-size: 16px; font-weight: 600; }
  .chart-wrap { position: relative; height: 320px; }
  .controls {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 14px;
  }
  .controls input, .controls select {
    font-family: inherit;
    padding: 8px 12px;
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 14px;
    background: #fff;
  }
  .controls input { flex: 1; min-width: 180px; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 500; cursor: pointer; user-select: none; white-space: nowrap; }
  th:hover { color: var(--blue); }
  tr:hover td { background: #f8fafd; }
  .badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 999px;
    font-size: 11.5px;
    font-weight: 600;
    color: #fff;
  }
  .badge.RAM { background: var(--ram); }
  .badge.SSD { background: var(--ssd); }
  .badge.FLASH { background: var(--flash); }
  .price { font-weight: 600; white-space: nowrap; }
  .pager {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 12px;
    font-size: 13px;
    color: var(--muted);
  }
  .pager button {
    border: 1px solid var(--border);
    background: #fff;
    border-radius: 6px;
    padding: 5px 12px;
    cursor: pointer;
    font-family: inherit;
  }
  .pager button:disabled { opacity: 0.4; cursor: default; }
  .empty-note { text-align: center; padding: 30px; color: var(--muted); }
  footer { text-align: center; padding: 20px; color: var(--muted); font-size: 12.5px; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1420; --card:#171e2e; --text:#e8ecf5; --muted:#93a0bd; --border:#2a3348; }
    .controls input, .controls select, .tab-btn { background:#1c2436; color:var(--text); }
    tr:hover td { background: #1c2436; }
  }
</style>
</head>
<body>

<header>
  <h1>📊 Advice Hardware Price Dashboard</h1>
  <p>RAM · SSD · Memory Card / Flash Drive — อัปเดตล่าสุด __GENERATED_AT__</p>
</header>

<div class="wrap">

  <div class="cards" id="summaryCards"></div>

  <div class="tabs" id="categoryTabs"></div>

  <div class="panel">
    <h2 id="chartTitle">ราคาเฉลี่ยตามแบรนด์</h2>
    <div class="chart-wrap"><canvas id="brandChart"></canvas></div>
  </div>

  <div class="panel">
    <h2>รายการสินค้าทั้งหมด</h2>
    <div class="controls">
      <input type="text" id="searchInput" placeholder="ค้นหาชื่อสินค้า หรือแบรนด์...">
      <select id="brandFilter"><option value="">ทุกแบรนด์</option></select>
    </div>
    <div id="tableWrap"></div>
    <div class="pager">
      <span id="pageInfo"></span>
      <div>
        <button id="prevPage">← ก่อนหน้า</button>
        <button id="nextPage">ถัดไป →</button>
      </div>
    </div>
  </div>

</div>

<footer>สร้างโดย Advice Price Scraper · ข้อมูลดึงจาก advice.co.th อัตโนมัติทุกวัน</footer>

<script>
const DATA = __DATA_JSON__;

let currentCategory = 'ALL';
let currentPage = 1;
const PAGE_SIZE = 25;
let sortKey = 'Price';
let sortDir = 1;
let chartInstance = null;

const CAT_LABELS = { ALL: 'ทั้งหมด', RAM: 'RAM', SSD: 'SSD', FLASH: 'Flash / Memory' };
const CAT_COLORS = { RAM: '#0b5ed7', SSD: '#16a37a', FLASH: '#e8792c' };

function fmtPrice(n) {
  return '฿' + Number(n).toLocaleString('th-TH', { maximumFractionDigits: 0 });
}

function filteredData() {
  let rows = currentCategory === 'ALL' ? DATA : DATA.filter(r => r.Category === currentCategory);
  const q = document.getElementById('searchInput').value.trim().toLowerCase();
  if (q) {
    rows = rows.filter(r =>
      (r.Model_Raw || '').toLowerCase().includes(q) ||
      (r.Brand || '').toLowerCase().includes(q)
    );
  }
  const brand = document.getElementById('brandFilter').value;
  if (brand) rows = rows.filter(r => r.Brand === brand);
  return rows;
}

function renderSummary() {
  const cats = ['ALL', 'RAM', 'SSD', 'FLASH'];
  const wrap = document.getElementById('summaryCards');
  wrap.innerHTML = '';
  cats.forEach(cat => {
    const rows = cat === 'ALL' ? DATA : DATA.filter(r => r.Category === cat);
    if (!rows.length && cat !== 'ALL') return;
    const prices = rows.map(r => r.Price);
    const avg = prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : 0;
    const min = prices.length ? Math.min(...prices) : 0;
    const div = document.createElement('div');
    div.className = 'card';
    div.innerHTML = `
      <div class="label">${CAT_LABELS[cat]}</div>
      <div class="value">${rows.length.toLocaleString()} รายการ</div>
      <div class="sub">เฉลี่ย ${fmtPrice(avg)} · ต่ำสุด ${fmtPrice(min)}</div>
    `;
    wrap.appendChild(div);
  });
}

function renderTabs() {
  const cats = ['ALL', ...Array.from(new Set(DATA.map(r => r.Category)))];
  const wrap = document.getElementById('categoryTabs');
  wrap.innerHTML = '';
  cats.forEach(cat => {
    const btn = document.createElement('button');
    btn.className = 'tab-btn' + (cat === currentCategory ? ' active' : '');
    btn.textContent = CAT_LABELS[cat] || cat;
    btn.onclick = () => {
      currentCategory = cat;
      currentPage = 1;
      populateBrandFilter();
      renderAll();
    };
    wrap.appendChild(btn);
  });
}

function populateBrandFilter() {
  const rows = currentCategory === 'ALL' ? DATA : DATA.filter(r => r.Category === currentCategory);
  const brands = Array.from(new Set(rows.map(r => r.Brand))).sort();
  const sel = document.getElementById('brandFilter');
  const prev = sel.value;
  sel.innerHTML = '<option value="">ทุกแบรนด์</option>' + brands.map(b => `<option value="${b}">${b}</option>`).join('');
  if (brands.includes(prev)) sel.value = prev;
}

function renderChart() {
  const rows = currentCategory === 'ALL' ? DATA : DATA.filter(r => r.Category === currentCategory);
  const byBrand = {};
  rows.forEach(r => {
    if (!byBrand[r.Brand]) byBrand[r.Brand] = [];
    byBrand[r.Brand].push(r.Price);
  });
  let brands = Object.keys(byBrand).map(b => ({
    brand: b,
    avg: byBrand[b].reduce((a, c) => a + c, 0) / byBrand[b].length,
    count: byBrand[b].length,
  }));
  brands.sort((a, b) => b.count - a.count);
  brands = brands.slice(0, 12).sort((a, b) => a.avg - b.avg);

  document.getElementById('chartTitle').textContent =
    'ราคาเฉลี่ยตามแบรนด์ — ' + CAT_LABELS[currentCategory];

  const ctx = document.getElementById('brandChart');
  const color = currentCategory === 'ALL' ? '#0b5ed7' : CAT_COLORS[currentCategory];
  if (chartInstance) chartInstance.destroy();
  chartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: brands.map(b => b.brand + ' (' + b.count + ')'),
      datasets: [{
        label: 'ราคาเฉลี่ย (บาท)',
        data: brands.map(b => Math.round(b.avg)),
        backgroundColor: color,
        borderRadius: 6,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { callback: v => '฿' + v.toLocaleString() } }
      }
    }
  });
}

function renderTable() {
  let rows = filteredData();
  rows = rows.slice().sort((a, b) => {
    let va = a[sortKey], vb = b[sortKey];
    if (typeof va === 'string') { va = va.toLowerCase(); vb = (vb || '').toLowerCase(); }
    if (va < vb) return -1 * sortDir;
    if (va > vb) return 1 * sortDir;
    return 0;
  });

  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  if (currentPage > totalPages) currentPage = totalPages;
  const pageRows = rows.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const wrap = document.getElementById('tableWrap');
  if (!rows.length) {
    wrap.innerHTML = '<div class="empty-note">ไม่พบสินค้าตรงเงื่อนไข</div>';
  } else {
    const cols = [
      ['Category', 'หมวด'], ['Brand', 'แบรนด์'], ['Model_Raw', 'ชื่อสินค้า'],
      ['Form_Factor', 'ประเภท'], ['Tech_Spec', 'สเปค'], ['Capacity', 'ความจุ'], ['Price', 'ราคา'],
    ];
    let html = '<table><thead><tr>';
    cols.forEach(([key, label]) => {
      const arrow = sortKey === key ? (sortDir === 1 ? ' ▲' : ' ▼') : '';
      html += `<th data-key="${key}">${label}${arrow}</th>`;
    });
    html += '</tr></thead><tbody>';
    pageRows.forEach(r => {
      html += '<tr>';
      html += `<td><span class="badge ${r.Category}">${r.Category}</span></td>`;
      html += `<td>${r.Brand}</td>`;
      html += `<td>${r.Model_Raw}</td>`;
      html += `<td>${r.Form_Factor || ''}</td>`;
      html += `<td>${r.Tech_Spec || ''}</td>`;
      html += `<td>${r.Capacity || ''}</td>`;
      html += `<td class="price">${fmtPrice(r.Price)}</td>`;
      html += '</tr>';
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
    wrap.querySelectorAll('th').forEach(th => {
      th.onclick = () => {
        const key = th.dataset.key;
        if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = 1; }
        renderTable();
      };
    });
  }

  document.getElementById('pageInfo').textContent =
    `หน้า ${currentPage}/${totalPages} · ${rows.length.toLocaleString()} รายการ`;
  document.getElementById('prevPage').disabled = currentPage <= 1;
  document.getElementById('nextPage').disabled = currentPage >= totalPages;
}

function renderAll() {
  renderTabs();
  renderChart();
  renderTable();
}

document.getElementById('searchInput').addEventListener('input', () => { currentPage = 1; renderTable(); });
document.getElementById('brandFilter').addEventListener('change', () => { currentPage = 1; renderTable(); });
document.getElementById('prevPage').addEventListener('click', () => { if (currentPage > 1) { currentPage--; renderTable(); } });
document.getElementById('nextPage').addEventListener('click', () => { currentPage++; renderTable(); });

renderSummary();
populateBrandFilter();
renderAll();
</script>
</body>
</html>
""".replace("__DATA_JSON__", safe_json).replace("__GENERATED_AT__", generated_at)


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
    msg['Subject'] = f"📊 รายงานราคา RAM & SSD & Flash/Memory Advice ({status_msg})"

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


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


async def new_stealth_context(browser):
    """สร้าง context ใหม่ทุกครั้ง (คนละ cookie/session) พร้อมสุ่ม user-agent และใส่ referer
    เหมือนมาจาก Google เพื่อลดโอกาสโดน anti-bot/rate-limit ต่อเนื่องข้ามหมวดหมู่"""
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1920, "height": 1080},
        locale="th-TH",
        extra_http_headers={"Referer": "https://www.google.com/"},
    )
    page = await context.new_page()
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return context, page


# =============================================================================
# 4. Main Workflow
# =============================================================================
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        # ใช้ context แยกกันคนละอันสำหรับ RAM และ SSD (คนละ cookie/session ตั้งแต่ต้น)
        # เผื่อการบล็อกที่เจอเป็นแบบผูกกับ session ไม่ใช่แค่จังหวะเวลา
        ram_context, ram_page = await new_stealth_context(browser)
        ram_data = await scrape_ram(ram_page)
        await ram_context.close()

        cooldown = random.uniform(5, 12)
        print(f"⏳ พักก่อนเริ่มสแครป SSD {cooldown:.1f} วินาที...")
        await asyncio.sleep(cooldown)

        ssd_context, ssd_page = await new_stealth_context(browser)
        ssd_data = await scrape_ssd(ssd_page)
        await ssd_context.close()

        cooldown2 = random.uniform(5, 12)
        print(f"⏳ พักก่อนเริ่มสแครป FLASH {cooldown2:.1f} วินาที...")
        await asyncio.sleep(cooldown2)

        flash_context, flash_page = await new_stealth_context(browser)
        flash_data = await scrape_flash(flash_page)
        await flash_context.close()

        await browser.close()

        all_data = ram_data + ssd_data + flash_data
        file_name = "Hardware_Advice_Prices.xlsx"

        if all_data:
            df = pd.DataFrame(all_data)
            df = df.drop_duplicates(subset=['Source', 'Model_Raw', 'Price'])

            ram_df = df[df['Category'] == 'RAM']
            ssd_df = df[df['Category'] == 'SSD']
            flash_df = df[df['Category'] == 'FLASH']

            with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name="All Hardware Cleaned", index=False)
                if not ram_df.empty:
                    ram_df.to_excel(writer, sheet_name="RAM Summary", index=False)
                if not ssd_df.empty:
                    ssd_df.to_excel(writer, sheet_name="SSD Summary", index=False)
                if not flash_df.empty:
                    flash_df.to_excel(writer, sheet_name="Flash-Memory Summary", index=False)

            status_msg = (
                f"ดึงสำเร็จรวม {len(df)} รายการ "
                f"(RAM: {len(ram_df)}, SSD: {len(ssd_df)}, Flash/Memory: {len(flash_df)})"
            )

            # สร้าง dashboard HTML (ฝังข้อมูลในตัว) สำหรับ host บน GitHub Pages
            th_tz = timezone(timedelta(hours=7))
            generated_at = datetime.now(th_tz).strftime("%d/%m/%Y %H:%M น.")
            os.makedirs("docs", exist_ok=True)
            dashboard_html = build_dashboard_html(df.to_dict("records"), generated_at)
            with open(os.path.join("docs", "index.html"), "w", encoding="utf-8") as f:
                f.write(dashboard_html)
            print("📊 สร้าง docs/index.html (dashboard) เรียบร้อยแล้ว")
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
