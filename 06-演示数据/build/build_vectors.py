# -*- coding: utf-8 -*-
"""生成虚构用海矢量 SQL（project_geometries 插入脚本）。

每个项目 1~3 个多边形，沿海岸走向，形态按用海方式区分：
码头/养殖=块状，管道/桥梁=细长条，其余=带抖动的四边形。
输出 build/vectors.sql（供 docker compose exec -T postgres psql 执行）
"""
from __future__ import annotations
import json
import random
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dataset import PROJECTS

def quad(cx: float, cy: float, w: float, h: float, rng: random.Random, rot: float = 0.0) -> list:
    """以 (cx,cy) 为中心的带抖动四边形（经纬度度）。"""
    import math
    pts = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    j = lambda: rng.uniform(0.85, 1.15)
    out = []
    for x, y in pts:
        x, y = x * j(), y * j()
        xr = x * math.cos(rot) - y * math.sin(rot)
        yr = x * math.sin(rot) + y * math.cos(rot)
        out.append([round(cx + xr, 6), round(cy + yr, 6)])
    out.append(out[0])
    return out

def shapes_for(p: dict, rng: random.Random) -> list[list]:
    t = p["use_type"]
    lng, lat = p["lng"], p["lat"]
    if t == "海底电缆管道":
        return [quad(lng - 0.012, lat, 0.030, 0.0018, rng, rot=0.35)]
    if t == "跨海桥梁":
        return [quad(lng, lat + 0.004, 0.0022, 0.026, rng, rot=0.15)]
    if t == "港口码头":
        return [quad(lng, lat, 0.012, 0.005, rng, rot=0.2),
                quad(lng + 0.008, lat - 0.006, 0.004, 0.003, rng, rot=0.2)]
    if t == "养殖用海":
        return [quad(lng, lat, 0.024, 0.016, rng, rot=-0.1)]
    if t in ("电厂温排水", "其他用海"):
        return [quad(lng, lat, 0.010, 0.007, rng, rot=0.4),
                quad(lng + 0.009, lat - 0.005, 0.006, 0.004, rng, rot=0.4)]
    return [quad(lng, lat, 0.008, 0.005, rng, rot=rng.uniform(-0.4, 0.4))]

def geojson_poly(coords: list) -> dict:
    return {"type": "Polygon", "coordinates": [coords]}

def main() -> Path:
    lines = ["-- 虚构用海矢量（澜海市临澜县/沧屿县），由 build_vectors.py 生成",
             "DELETE FROM project_geometries WHERE region IN ('临澜县','沧屿县');"]
    total = 0
    for p in PROJECTS:
        rng = random.Random(p["code"])
        for seq, coords in enumerate(shapes_for(p, rng), 1):
            gj = geojson_poly(coords)
            gj_text = json.dumps(gj, ensure_ascii=False, separators=(",", ":"))
            shape_len = round(sum(((coords[i][0] - coords[i + 1][0]) ** 2 +
                                   (coords[i][1] - coords[i + 1][1]) ** 2) ** 0.5
                                  for i in range(len(coords) - 1)) * 111000, 1)
            shape_area = round(abs((coords[1][0] - coords[0][0]) *
                                   (coords[2][1] - coords[1][1])) * 111000 ** 2 / 10000, 2)
            total += 1
            lines.append(
                f"INSERT INTO project_geometries (id, project_id, project_code, survey_code,"
                f" bianhao_raw, period, region, geometry, geometry_type, sequence, shape_length,"
                f" shape_area, source_layer, raw_attributes, created_at, geom)\n"
                f"SELECT '{uuid.uuid4()}', p.id, '{p['code']}', '{p['survey_code']}',"
                f" '{p['sea_area_code']}', '2026-Q1', '{p['region']}',\n"
                f" '{gj_text}', 'Polygon', {seq}, {shape_len}, {shape_area},"
                f" 'HYS_EXTENT_2026Q1', '{{\"BJHC\":\"{p['sea_area_code']}\"}}', now(),\n"
                f" ST_GeomFromGeoJSON('{gj_text}') FROM projects p WHERE p.code = '{p['code']}';")
    lines.append(f"-- 共 {total} 个多边形")
    out = Path(__file__).parent / "vectors.sql"
    out.write_text("\n\n".join(lines), encoding="utf-8")
    print(f"生成 {out.name}: {len(PROJECTS)} 项目 {total} 个多边形")
    return out

if __name__ == "__main__":
    main()
