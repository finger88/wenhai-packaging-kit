# -*- coding: utf-8 -*-
"""生成虚构核查清单 Excel（WPS DISPIMG 式嵌图）。

平台 parse-excel 只认 WPS「插入图片到单元格」的私有结构：
  xl/cellimages.xml（ID_xxx → rIdN）、xl/_rels/cellimages.xml.rels（rIdN → ../media/imageN.jpeg）、
  xl/media/*.jpeg，单元格写 =DISPIMG("ID_xxx",1) 公式。
openpyxl 无法直接产出该结构，故先保存再对 zip 做手术注入。

输出 out/excel/<县><期次>_核查清单.xlsx ×4
"""
from __future__ import annotations
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).parent))
from dataset import CITY, EXCEL_HEADERS, PROJECTS, OUT, REGIONS, _INS_NOTES, assign_photos

CELLIMAGES_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet+xml"

def build_workbook(region: str, period: str, assign: dict, out_path: Path):
    rows = []
    for p in PROJECTS:
        if p["region"] != region:
            continue
        key = (p["code"], period)
        if key not in _INS_NOTES:
            continue
        rows.append((p, key))

    max_n = max((len(assign[k]) for _, k in rows), default=0)
    headers = list(EXCEL_HEADERS) + [f"核查照片_{i}" for i in range(1, max_n + 1)]

    wb = Workbook()
    ws = wb.active
    ws.title = f"{region}{period.replace('-', '')}"
    ws.append(headers)
    for cell in ws[1]:
        cell.font = cell.font.copy(bold=True)

    photo_reg: dict[str, Path] = {}   # DISPIMG ID -> 图片文件
    for p, key in rows:
        ins = _INS_NOTES[key]
        date, people, method, notes, situation = ins
        values = [
            p["code"], p["survey_code"], p["sea_area_code"], p["name"], p["holder"],
            CITY, region, p["loc"], p["approval"], p["period_years"], p["use_type"],
            p["area"], date, people, method, situation, notes,
        ]
        for photo in assign[key]:
            pid = f"ID_{uuid.uuid4().hex.upper()}"
            photo_reg[pid] = photo
            values.append(f'=DISPIMG("{pid}",1)')
        while len(values) < len(headers):
            values.append("")
        ws.append(values)

    widths = [13, 12, 18, 30, 24, 9, 11, 34, 11, 9, 12, 13, 12, 16, 17, 26, 46] + [13] * max_n
    from openpyxl.utils import get_column_letter
    for i, w in enumerate(widths[: len(headers)], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    wb.save(out_path)
    inject_wps_images(out_path, photo_reg)
    return out_path, len(rows), len(photo_reg)

def inject_wps_images(xlsx: Path, photo_reg: dict[str, Path]):
    """把 WPS cellimages 结构注入 openpyxl 保存的 xlsx。"""
    tmp = xlsx.with_suffix(".tmp.xlsx")
    items = list(photo_reg.items())
    pics_xml = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
    ]
    rels_xml = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    for n, (pid, _path) in enumerate(items, 1):
        pics_xml.append(
            '<xdr:pic>'
            f'<xdr:nvPicPr><xdr:cNvPr id="{n}" name="{pid}"/>'
            '<xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr></xdr:nvPicPr>'
            f'<xdr:blipFill><a:blip r:embed="rId{n}"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
            '<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="960000" cy="720000"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
            '</xdr:pic>'
        )
        rels_xml.append(
            f'<Relationship Id="rId{n}"'
            ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"'
            f' Target="../media/image{n}.jpeg"/>'
        )
    pics_xml.append('</xdr:wsDr>')
    rels_xml.append('</Relationships>')

    with zipfile.ZipFile(xlsx, "r") as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "[Content_Types].xml":
                text = data.decode("utf-8")
                if 'Extension="jpeg"' not in text:
                    text = text.replace(
                        "<Default", '<Default Extension="jpeg" ContentType="image/jpeg"/><Default', 1)
                text = text.replace(
                    "</Types>", f'<Override PartName="/xl/cellimages.xml" ContentType="{CELLIMAGES_CT}"/></Types>')
                data = text.encode("utf-8")
            zout.writestr(info, data)
        for n, (pid, path) in enumerate(items, 1):
            zout.writestr(f"xl/media/image{n}.jpeg", Path(path).read_bytes())
        zout.writestr("xl/cellimages.xml", "\n".join(pics_xml).encode("utf-8"))
        zout.writestr("xl/_rels/cellimages.xml.rels", "\n".join(rels_xml).encode("utf-8"))
    shutil.move(tmp, xlsx)

def verify(xlsx: Path) -> str:
    """回读自检：openpyxl 能读、DISPIMG 公式原样保留、zip 内三件套齐全。"""
    wb = load_workbook(xlsx, data_only=False)
    ws = wb.active
    formulas = []
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, str) and "DISPIMG" in c.value:
                formulas.append(c.value)
                break
    with zipfile.ZipFile(xlsx) as z:
        names = z.namelist()
        ok_parts = all(p in names for p in
                       ("xl/cellimages.xml", "xl/_rels/cellimages.xml.rels"))
        media = [n for n in names if n.startswith("xl/media/")]
    assert ok_parts and media and formulas, "自检失败"
    return f"行={ws.max_row - 1} DISPIMG={len(formulas)} media={len(media)} ✓"

if __name__ == "__main__":
    assign = assign_photos()
    out_dir = OUT / "excel"
    out_dir.mkdir(parents=True, exist_ok=True)
    for region in REGIONS:
        for period in ("2026-Q1", "2026-Q2"):
            name = f"{region}{'2026Q1' if period.endswith('Q1') else '2026Q2'}_核查清单.xlsx"
            path = out_dir / name
            _, n_rows, n_photos = build_workbook(region, period, assign, path)
            print(f"{name}: 行={n_rows} 嵌图={n_photos} | 自检: {verify(path)}")
