# -*- coding: utf-8 -*-
"""生成虚构合同扫描件 PDF（图片型，可走平台 OCR 链路）。

每份合同 4 页 A4 @200dpi：封面 / 双方与服务内容 / 金额期限成果 / 签字盖章。
输出 out/contracts/<编号>.pdf
"""
from __future__ import annotations
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).parent))
from dataset import CONTRACTS, OUT

W, H = 1654, 2339          # A4 @200dpi
MARGIN = 170
FONTS = Path("C:/Windows/Fonts")
INK = (28, 28, 32)

def F(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)

def new_page() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), (250, 249, 246))
    return img, ImageDraw.Draw(img)

def wrap(d: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=font) > width:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines

def draw_para(d, x, y, text, font, width, fill=INK, line_gap=18, indent2=False):
    lines = wrap(d, text, font, width)
    for i, ln in enumerate(lines):
        ox = x + (font.size if (indent2 and i > 0) else 0)
        d.text((ox, y), ln, font=font, fill=fill)
        y += font.size + line_gap
    return y

def draw_seal(page: Image.Image, cx: int, cy: int, unit: str):
    """红色圆章：双环 + 五角星 + 单位名 + '合同专用章'，轻微旋转。"""
    from PIL import ImageDraw
    R = 150
    layer = Image.new("RGBA", (R * 2 + 40, R * 2 + 40), (0, 0, 0, 0))
    sd = ImageDraw.Draw(layer)
    red = (200, 30, 30, 185)
    c = (R + 20, R + 20)
    sd.ellipse([c[0] - R, c[1] - R, c[0] + R, c[1] + R], outline=red, width=7)
    sd.ellipse([c[0] - R + 14, c[1] - R + 14, c[0] + R - 14, c[1] + R - 14], outline=red, width=3)
    import math
    star, r1, r2 = [], 62, 24
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        r = r1 if i % 2 == 0 else r2
        star.append((c[0] + r * math.cos(ang), c[1] - 18 + r * math.sin(ang)))
    sd.polygon(star, fill=red)
    size = min(30, 270 // max(len(unit), 1))
    nf = F("simhei.ttf", size)
    nw = sd.textlength(unit, font=nf)
    sd.text((c[0] - nw / 2, c[1] + 48), unit, font=nf, fill=red)
    lf = F("simhei.ttf", 24)
    lw = sd.textlength("合同专用章", font=lf)
    sd.text((c[0] - lw / 2, c[1] + 100), "合同专用章", font=lf, fill=red)
    layer = layer.rotate(-9, expand=True, resample=Image.BICUBIC)
    page.paste(layer, (cx - layer.width // 2, cy - layer.height // 2), layer)

def render_contract(c: dict) -> Path:
    pages: list[Image.Image] = []

    # ---------- P1 封面 ----------
    img, d = new_page()
    t = F("simhei.ttf", 96)
    tw = d.textlength("技术服务合同", font=t)
    d.text(((W - tw) / 2, 420), "技术服务合同", font=t, fill=INK)
    st = F("simhei.ttf", 52)
    for i, ln in enumerate(wrap(d, c["name"], st, W - 2 * MARGIN)):
        lw = d.textlength(ln, font=st)
        d.text(((W - lw) / 2, 660 + i * 84), ln, font=st, fill=INK)
    body = F("simfang.ttf", 46)
    y = 1180
    for label, val in [("合同编号：", c["contract_no"]), ("甲　　方：", c["party_a"]),
                       ("乙　　方：", c["party_b_short"])]:
        d.text((MARGIN + 120, y), label, font=body, fill=INK)
        y = draw_para(d, MARGIN + 400, y, val, body, W - MARGIN - (MARGIN + 400),
                      indent2=True) + 34
    df = F("simkai.ttf", 44)
    d.text((W - MARGIN - 560, H - 420), f"签订日期：{c['sign_date']}", font=df, fill=INK)
    pages.append(img)

    # ---------- P2 双方 + 服务内容 ----------
    img, d = new_page()
    h1 = F("simhei.ttf", 56)
    body = F("simfang.ttf", 42)
    num = F("simhei.ttf", 42)
    y = 200
    d.text((MARGIN, y), "一、合同双方", font=h1, fill=INK); y += 120
    y = draw_para(d, MARGIN, y, f"甲方：{c['party_a']}", body, W - 2 * MARGIN) + 26
    y = draw_para(d, MARGIN, y, "地址：临澜市" + c["region"] + "政通路88号　联系人：王科　电话：0580-88**0001",
                  body, W - 2 * MARGIN) + 26
    y = draw_para(d, MARGIN, y, f"乙方（联合体）：{c['party_b']}", body, W - 2 * MARGIN) + 26
    y = draw_para(d, MARGIN, y, "地址：临澜市海湾新区勘察路1号　联系人：陈主办　电话：0580-88**0002",
                  body, W - 2 * MARGIN) + 60
    d.text((MARGIN, y), "二、服务内容", font=h1, fill=INK); y += 120
    for i, s in enumerate(c["services"], 1):
        d.text((MARGIN, y), f"（{i}）", font=num, fill=INK)
        y = draw_para(d, MARGIN + 110, y, s, body, W - 2 * MARGIN - 110, indent2=True) + 24
    pages.append(img)

    # ---------- P3 金额 / 期限 / 成果 ----------
    img, d = new_page()
    y = 200
    d.text((MARGIN, y), "三、合同金额", font=h1, fill=INK); y += 120
    y = draw_para(d, MARGIN, y, f"本合同服务费总额（大写）：{c['amount_upper']}（小写：{c['amount_num']}）。"
                  "按季度均衡支付，甲方收到当季成果并验收合格后15个工作日内支付当季款项。", body,
                  W - 2 * MARGIN, indent2=True) + 50
    d.text((MARGIN, y), "四、服务期限", font=h1, fill=INK); y += 120
    y = draw_para(d, MARGIN, y, f"服务期限：{c['service_period']}。", body, W - 2 * MARGIN) + 50
    d.text((MARGIN, y), "五、成果清单", font=h1, fill=INK); y += 120
    for i, s in enumerate(c["deliveries"], 1):
        y = draw_para(d, MARGIN, y, f"{i}. {s}", body, W - 2 * MARGIN) + 20
    y += 30
    d.text((MARGIN, y), "六、其他约定", font=h1, fill=INK); y += 120
    y = draw_para(d, MARGIN, y, "本合同履行过程中发生的争议，双方应友好协商解决；协商不成的，"
                  "提交临澜仲裁委员会仲裁。未尽事宜由双方另行签订补充协议，补充协议与本合同具有同等法律效力。",
                  body, W - 2 * MARGIN, indent2=True)
    pages.append(img)

    # ---------- P4 签字盖章 ----------
    img, d = new_page()
    body = F("simfang.ttf", 42)
    y = 300
    d.text((MARGIN, y), "（本页以下为签字盖章页，无正文）", font=F("simkai.ttf", 38), fill=(110, 110, 110))
    y = 560
    sign = F("simhei.ttf", 48)
    d.text((MARGIN, y), "甲方（盖章）：", font=sign, fill=INK)
    draw_seal(img, MARGIN + 620, y + 210, c["party_a"])
    d.text((MARGIN + 60, y + 430), "法定代表人或授权代表（签字）：＿＿＿＿＿＿＿＿", font=body, fill=INK)
    d.text((MARGIN + 60, y + 520), f"日期：{c['sign_date']}", font=body, fill=INK)
    y2 = y + 660
    d.text((MARGIN, y2), "乙方（联合体，各自盖章）：", font=sign, fill=INK)
    draw_seal(img, MARGIN + 430, y2 + 210, "澜海海洋勘察设计有限公司")
    draw_seal(img, MARGIN + 980, y2 + 210, "澜海测绘地理信息有限公司")
    d.text((MARGIN + 60, y2 + 430), "法定代表人或授权代表（签字）：＿＿＿＿＿＿＿＿", font=body, fill=INK)
    d.text((MARGIN + 60, y2 + 520), f"日期：{c['sign_date']}", font=body, fill=INK)
    pages.append(img)

    # ---------- 组装 PDF ----------
    out_dir = OUT / "contracts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"{c['contract_no']}.pdf"
    doc = fitz.open()
    for pg in pages:
        pix_path = out_dir / f"_{c['key']}_{len(doc) + 1}.jpg"
        pg.save(pix_path, quality=88)
        page = doc.new_page(width=595, height=842)  # A4 pt
        page.insert_image(page.rect, filename=str(pix_path))
        pix_path.unlink()
    doc.save(str(out_pdf), deflate=True)
    doc.close()
    return out_pdf

if __name__ == "__main__":
    for c in CONTRACTS:
        p = render_contract(c)
        print("生成合同:", p.name, f"{p.stat().st_size // 1024}KB")
