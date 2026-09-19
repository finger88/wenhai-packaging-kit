# -*- coding: utf-8 -*-
"""虚拟演示数据 · 导入编排器。

前置条件：
  1. 环境已重置（docker compose down -v 后 up，空库，admin/初始密码自动创建）
  2. bundle/.env 的 LLM_* 已指向可用的 GLM 配置（合同 AI 识别用），且 backend 已 recreate
  3. gen_contracts.py / gen_excel.py / build_vectors.py 已运行（out/ 与 vectors.sql 就绪）

执行内容（全部走平台真实 API）：
  登录 → 建 PM 账号 → 人员池 → 上传合同×2（指定归属 PM）→ AI 识别 → 确认立项
  → 核查 Excel×4（parse-excel → preview → confirm）→ 矢量 SQL → 报告团队
  → 报告模板×2 → 图集 → 重建问答索引 → 汇总校验

用法：python import_runner.py [--base http://localhost] [--admin-pass xxx]
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from dataset import (ACCOUNTS, CITY, CONTRACTS, OUT, PERSONNEL, PROJECTS, REGIONS,
                     TEAM_LEAD, TEAM_MEMBERS_CYCLE, _INS_NOTES)

BUILD = Path(__file__).resolve().parent
BUNDLE = BUILD.parent.parent / "ocean-deploy-2026-08-28" / "bundle"   # 原实例（勿动）
VM = BUILD.parent / "vm"                                              # 虚拟数据隔离实例
STAGING = BUILD.parent / "staging"

def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.s = requests.Session()

    def login(self, username: str, password: str):
        r = self.s.post(f"{self.base}/api/auth/login",
                        json={"username": username, "password": password}, timeout=30)
        r.raise_for_status()
        self.s.headers["Authorization"] = f"Bearer {r.json()['token']}"

    def _req(self, method: str, path: str, timeout=300, **kw):
        r = self.s.request(method, f"{self.base}{path}", timeout=timeout, **kw)
        if not r.ok:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def get(self, path, **kw): return self._req("GET", path, **kw)
    def post(self, path, **kw): return self._req("POST", path, **kw)
    def put(self, path, **kw): return self._req("PUT", path, **kw)
    def patch(self, path, **kw): return self._req("PATCH", path, **kw)

# ---------------------------------------------------------------- 映射

HEADER_TO_FIELD = {
    "项目编号": "code", "调查编号": "survey_code", "海域管理号": "sea_area_code",
    "项目名称": "name", "使用权人": "right_holder", "项目位置": "location_desc",
    "批复时间": "approval_date", "用海期限": "sea_use_period", "用海方式": "sea_use_type",
    "用海面积（公顷）": "sea_use_area",
}
PROJ_BY_CODE = {p["code"]: p for p in PROJECTS}

def row_to_item(row: dict) -> dict:
    p = PROJ_BY_CODE[row["项目编号"]]
    item = {v: str(row.get(k, "") or "") for k, v in HEADER_TO_FIELD.items()}
    item.update({
        "description": p["desc"], "province": "浙江省",
        "city": p["region"],   # 平台的 city 字段=「城市/区县」，存区县名（合同/报告按此匹配范围）
        "lng": p["lng"], "lat": p["lat"], "status": p["status"],
        "inspection_date": str(row.get("核查日期", "")),
        "extra_data": {k: ("" if v is None else v) for k, v in row.items()},
    })
    return item

def import_excel(api: Api, contract_id: str, xlsx: Path, period: str):
    log(f"导入核查清单: {xlsx.name}（期次 {period}）")
    with open(xlsx, "rb") as f:
        parsed = api.post("/api/projects/parse-excel",
                          files={"file": (xlsx.name, f,
                                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    log(f"  解析: {len(parsed['rows'])} 行, 嵌入图 {parsed['image_count']} 张")
    items = [row_to_item(r) for r in parsed["rows"]]
    prev = api.post(f"/api/projects/batch/preview?contract_id={contract_id}&period={period}",
                    json=items)
    new_codes = [c["code"] for c in prev["new_candidates"]]
    log(f"  预检: 新建 {len(new_codes)} / 匹配 {len(prev['matched'])}")
    api.post(f"/api/projects/batch/confirm?contract_id={contract_id}&period={period}",
             json={"upload_id": parsed["upload_id"], "items": items,
                   "confirm_new_codes": new_codes, "field_resolutions": {}})
    log("  确认导入完成")

def wait_recognition(api: Api, contract_id: str, timeout_s: int = 900):
    for _ in range(timeout_s // 5):
        time.sleep(5)
        c = api.get(f"/api/contracts/{contract_id}")
        st, prog = c["recognition_status"], c.get("recognition_progress", "")
        if st in ("reviewing", "confirmed"):
            return c
        if st == "pending" and ("失败" in prog or "错误" in prog):
            raise RuntimeError(f"识别失败: {prog[:200]}")
        log(f"  识别中… [{st}] {prog[:60]}")
    raise TimeoutError("识别超时")

# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost")
    ap.add_argument("--admin-pass", default=os.environ.get("DEMO_ADMIN_PASS", ""))
    args = ap.parse_args()
    api = Api(args.base)

    log("== 1. 登录 admin ==")
    api.login("admin", args.admin_pass)

    log("== 2. 创建 PM 账号 ==")
    users = {u["username"]: u["id"] for u in api.get("/api/users")}
    for username, password, name, _key in ACCOUNTS:
        if username not in users:
            api.post("/api/users", json={"username": username, "password": password, "name": name})
    users = {u["username"]: u["id"] for u in api.get("/api/users")}
    owner_by_key = {k: users[a[0]] for a in ACCOUNTS for k in [a[3]]}
    log(f"  PM 账号: {[a[0] for a in ACCOUNTS]}")

    log("== 3. 人员池 ==")
    have = {p["name"] for p in api.get("/api/personnel")}
    for name, dept, title in PERSONNEL:
        if name not in have:
            api.post("/api/personnel",
                     json={"name": name, "dept": dept, "title": title})
    log(f"  人员池 {len(api.get('/api/personnel'))} 人")

    log("== 4. 上传合同 + AI 识别 + 确认立项 ==")
    for c in CONTRACTS:
        existing = next((x for x in api.get("/api/contracts")
                         if x["contract_no"] == c["contract_no"]), None)
        if existing and existing["recognition_status"] == "confirmed":
            c["_id"] = existing["id"]
            log(f"  合同 {c['contract_no']} 已确认立项，跳过")
            continue
        if existing:
            c["_id"] = existing["id"]
            api.patch(f"/api/contracts/{c['_id']}", json={"recognition_status": "pending"})
            log(f"  合同 {c['contract_no']} 已存在（{existing['recognition_status']}），重置后重新识别…")
        else:
            pdf = OUT / "contracts" / f"{c['contract_no']}.pdf"
            with open(pdf, "rb") as f:
                created = api.post("/api/contracts",
                                   files={"file": (pdf.name, f, "application/pdf")},
                                   data={"name": c["name"], "contract_no": c["contract_no"],
                                         "owner_id": owner_by_key[c["key"]]})
            c["_id"] = created["id"]
            log(f"  合同 {c['contract_no']} 上传 ✓，发起 AI 识别…")
        api.post(f"/api/contracts/{c['_id']}/recognize")
        result = wait_recognition(api, c["_id"])
        rec = result.get("recognition_result") or {}
        log(f"  AI 识别: 甲方={str(rec.get('甲方', ''))[:24]} | 金额={str(rec.get('合同金额', ''))[:36]}")
        final = {
            "甲方": c["party_a"], "乙方": c["party_b_short"],
            "合同金额": f"（大写）：{c['amount_upper']}（小写：{c['amount_num']}）",
            "服务内容": "；".join(c["services"]),
            "成果清单": c["deliveries"],
        }
        api.patch(f"/api/contracts/{c['_id']}",
                  json={"recognition_status": "confirmed", "recognition_result": final,
                        "summary": f"{c['region']}2026年度海域使用动态监测监管服务（虚构演示数据）"})
        log(f"  立项确认 ✓ {c['name']}")

    log("== 5. 导入核查清单 Excel ==")
    for c in CONTRACTS:
        for period in ("2026-Q1", "2026-Q2"):
            tag = "2026Q1" if period.endswith("Q1") else "2026Q2"
            xlsx = OUT / "excel" / f"{c['region']}{tag}_核查清单.xlsx"
            import_excel(api, c["_id"], xlsx, period)

    log("== 6. 矢量入库 ==")
    sql = (BUILD / "vectors.sql").read_text(encoding="utf-8")
    subprocess.run(["docker", "compose", "exec", "-T", "postgres",
                    "psql", "-U", "ocean", "-d", "ocean", "-v", "ON_ERROR_STOP=1"],
                   input=sql.encode("utf-8"), cwd=str(VM), check=True)
    log("  矢量入库 ✓")

    log("== 7. 报告模板（先发布，团队岗位从已发布模板推导） ==")
    tpl_files = [
        ("海域使用动态监测监管报告模板（演示）",
         STAGING / "templates" / "5a7a3297-62fb-41cc-ad3f-d9712cc9bc87.docx"),
        ("监管图集模板（澜海底版·演示）",
         STAGING / "templates" / "a7c1a5e2-1d3b-4f6a-8b9c-0e1f2a3b4c5d.docx"),
    ]
    tpl_by_name = {t["name"]: t for t in api.get("/api/report-templates")}
    for name, path in tpl_files:
        t = tpl_by_name.get(name)
        if t and t["status"] == "active":
            log(f"  模板已发布，跳过 ✓ {name}")
            continue
        if not t:
            with open(path, "rb") as f:
                t = api.post("/api/report-templates",
                             files={"file": (path.name, f,
                                             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                             data={"name": name, "scope": "region_period",
                                   "description": "虚构演示数据配套模板"})
        api.patch(f"/api/report-templates/{t['id']}", json={"status": "active"})
        log(f"  模板发布 ✓ {name}")

    log("== 8. 报告团队配置 ==")
    for region in REGIONS:
        slots = api.get(f"/api/report-teams/required-slots?region={region}&period=2026-Q1")
        assignments = dict(TEAM_LEAD)
        cyc = iter(TEAM_MEMBERS_CYCLE * 3)
        filled = 0
        for s in slots:
            key = s.get("assign_key") or ""
            if key and s.get("kind") == "person" and key not in assignments:
                assignments[key] = next(cyc)
                filled += 1
        api.put("/api/report-teams",
                json={"region": region, "year": "2026", "assignments": assignments})
        log(f"  {region} 团队 {len(assignments)} 岗位（模板 {len(slots)} 槽位，填充 {filled}）✓")

    log("== 9. 图集 ==")
    from dataset import photo_pool
    pool_imgs = sorted((STAGING / "region-figures").rglob("*.jp*"))
    proj_imgs = photo_pool()
    assert pool_imgs, "区县图素材池为空"
    pool_idx = proj_idx = 0

    def next_img(fkey: str) -> Path:
        nonlocal pool_idx, proj_idx
        if fkey.startswith(("3_", "fix_", "result")) and proj_imgs:
            img = proj_imgs[proj_idx % len(proj_imgs)]
            proj_idx += 1
            return img
        img = pool_imgs[pool_idx % len(pool_imgs)]
        pool_idx += 1
        return img
    for region in REGIONS:
        have_keys = {f["figure_key"] for f in api.get(
            f"/api/region-figures?region={region}&period=2026-Q1")}
        req = api.get(f"/api/region-figures/required-keys?region={region}&period=2026-Q1")
        photos = api.get(f"/api/region-figures/inspection-photos?region={region}&period=2026-Q1")
        by_project: dict[str, list] = {}
        for ph in photos:
            by_project.setdefault(ph["project_id"], []).append(ph)
        uploaded, picked = 0, 0
        for k in req:
            ftype, fkey, pid = k.get("figure_type"), k["figure_key"], k.get("project_id")
            if fkey in have_keys:
                continue
            try:
                if ftype in (None, "", "overlay", "landing", "island_overlay"):
                    img = next_img(fkey)
                    with open(img, "rb") as f:
                        api.post("/api/region-figures",
                                 files={"file": (img.name, f, "image/jpeg")},
                                 data={"region": region, "period": "2026-Q1",
                                       "figure_key": fkey, "caption": k.get("alias", "")})
                    uploaded += 1
                elif ftype in ("drone", "field") and pid:
                    phs = by_project.get(pid) or []
                    if not phs:
                        continue
                    api.post("/api/region-figures/from-inspection",
                             json={"region": region, "period": "2026-Q1", "figure_key": fkey,
                                   "source_path": phs[0]["rel_path"], "caption": k.get("alias", "")})
                    picked += 1
            except Exception as e:  # 单个图位失败不阻塞
                log(f"  ⚠️ 图位 {fkey} 失败: {e}")
        log(f"  {region}: 上传区县/叠置图 {uploaded}, 现场选图 {picked}")

    log("== 10. 重建问答索引 ==")
    api.post("/api/chat/rebuild-index")
    ok = False
    for _ in range(90):
        time.sleep(5)
        st = api.get("/api/chat/index-status")
        prog = str(st.get("progress", ""))
        log(f"  {prog[:60]}")
        if "完成" in prog:
            ok = True
            break
    if not ok:
        log("  ⚠️ 索引超时未完成，请手动检查 /api/chat/index-status")

    log("== 11. 汇总校验 ==")
    projects = api.get("/api/projects")
    ins_total = 0
    for p in projects:
        ins_total += len(api.get(f"/api/projects/{p['id']}/inspections"))
    log(f"  项目: {len(projects)}（预期 9）  核查记录: {ins_total}（预期 16）")
    log("导入编排完成。")

if __name__ == "__main__":
    main()
