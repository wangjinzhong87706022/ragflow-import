#!/usr/bin/env python3
"""铺开：png 壳模式推广到全部 11 张剩余图（试点大坝注册登记证已完成）。

每张流程（与 pilot_png_shell.py 相同，逐张独立、失败不阻断后续）：
  1. 找同名 md（找不到视为已迁移或缺失，跳过并记录）
  2. 上传原始图片（不 parse，幂等按名查重）
  3. 复制 md 的 11 字段元数据
  4. 把 md 的切片手动挂到 png 名下（幂等：png 已有切片则跳过）
  5. 检索验证：探针题召回中须出现「锚点落在 png 名下分片」——
     验不过则保留 md 不删，标记 FAIL
  6. 验证通过 → 删除 md（记录其 id 供回滚台账）→ 删除后复验

用法：RAGFLOW_API_KEY=… python3 rollout_png_shell.py [--only 名称子串] [--dry-run]
产物：PNG_SHELL_ROLLOUT.md + deleted_md_manifest.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
SRC = Path("/opt/wangjz/ragflow-import/src")
sys.path.insert(0, str(SRC))

from config import PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL, RAGFLOW_PASSWORD  # noqa: E402
from ragflow_client import RAGFlowClient  # noqa: E402

API = "http://localhost:9380/api/v1"
KEY = RAGFLOW_API_KEY or "ragflow-3NZDMvfCVXMVJLXYB_ixCWuduqcI-fcksfBOQ_VCE5E"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
DS2 = "d6ec4e36a1d711f19d7235ad4ea699d4"   # 基础数据
DS4 = "d756f7b8a1d711f19d7235ad4ea699d4"   # 组织管理
CORPUS = Path("/home/scada/SmartTwinRes-skills/pdfs")

# (ds, 图片相对路径, 探针题, 锚点)
TARGETS = [
    (DS2, "05-基础数据与曲线/01-水库基本信息/水库基本信息.jpg",
     "桃曲坡水库的设计总库容是多少万立方米？", "5720"),
    (DS2, "05-基础数据与曲线/02-大坝剖面图.jpg",
     "桃曲坡水库大坝剖面上正常蓄水位是多少米？", "788.50"),
    (DS2, "05-基础数据与曲线/03-溢洪道信息/溢洪道图1.jpg",
     "溢洪道百年一遇设计洪水泄量是多少立方米每秒？", "1454"),
    (DS2, "05-基础数据与曲线/03-溢洪道信息/溢洪道图2.jpg",
     "溢洪道工作闸门采用什么型号的启闭机？", "QPQ2"),
    (DS2, "05-基础数据与曲线/05-库容水位对照表.jpg",
     "水位786.00米时对应的库容是多少万立方米？", "3370"),
    (DS2, "05-基础数据与曲线/06-泄流曲线.jpg",
     "泄流曲线中库水位788.5米时下泄流量大约是多少？", "1400"),
    (DS2, "05-基础数据与曲线/07-抢险物资信息/物资图1.jpg",
     "灌溉中心防汛袋类物资储备最低限额是多少？", "2 万条"),
    (DS2, "05-基础数据与曲线/07-抢险物资信息/物资图2.jpg",
     "桃曲坡水库防汛物资的存放地点在哪里？", "枢纽站库房"),
    (DS4, "07-管理资料/01-组织架构与责任人/三个责任人.jpg",
     "桃曲坡水库防汛安全行政责任人是谁？", "刘浩"),
    (DS4, "07-管理资料/01-组织架构与责任人/中心架构图.png",
     "富平灌溉总站下辖哪些灌溉管理站？", "庄里"),
    (DS4, "07-管理资料/03-供配水计划/各部门用水需求.jpg",
     "2026年度农灌斗口供水计划是多少万立方米？", "5000"),
]


def add_chunk(ds, doc_id, content, keywords=None):
    body = {"content": content}
    if keywords:
        body["important_keywords"] = keywords
    r = requests.post(f"{API}/datasets/{ds}/documents/{doc_id}/chunks",
                      headers=H, json=body, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"add_chunk 失败: {data}")
    return data["data"]["chunk"]["id"]


def delete_doc(ds, doc_id):
    r = requests.delete(f"{API}/datasets/{ds}/documents",
                        headers=H, json={"ids": [doc_id]}, timeout=30)
    return r.json()


def verify(client, ds, png_name, question, anchor):
    """探针检索：锚点出现在 png 名下分片 → True。"""
    data = client.search_datasets([ds], question, top_k=10)
    hits = []
    for c in data.get("chunks", []):
        name = c.get("document_name") or c.get("docnm_kwd") or c.get("document_keyword") or "?"
        content = c.get("content_with_weight") or ""
        if name == png_name and anchor in content:
            hits.append((name, round(c.get("similarity", 0), 3)))
    return bool(hits), hits


def process(client, ds, relpath, question, anchor, dry_run=False):
    img_path = CORPUS / relpath
    png_name = img_path.name
    md_name = img_path.stem + ".md"
    rec = {"ds": ds, "png": png_name, "md": md_name, "status": "", "md_id": None,
           "png_id": None, "chunks": 0, "detail": []}

    md = client.find_document_by_name(ds, md_name)
    png = client.find_document_by_name(ds, png_name)

    if not md and png and int(png.get("chunk_count") or 0) > 0:
        rec["status"] = "SKIP(已迁移)"
        rec["png_id"] = png["id"]
        return rec
    if not md:
        rec["status"] = "SKIP(md 不存在)"
        return rec
    rec["md_id"] = md["id"]
    rec["chunks"] = int(md.get("chunk_count") or 0)
    if dry_run:
        rec["status"] = "DRY-RUN"
        return rec

    # 1) 上传 png（不 parse）
    if png:
        rec["png_id"] = png["id"]
        rec["detail"].append(f"png 已存在 {png['id'][:8]} run={png.get('run')}")
    else:
        uploaded = client.upload_document(ds, img_path)
        png = uploaded[0] if isinstance(uploaded, list) else uploaded
        rec["png_id"] = png["id"]
        rec["detail"].append(f"png 上传 {png['id'][:8]}")
    if png.get("run") not in (None, "0", 0, "UNSTART"):
        rec["detail"].append(f"警告 png run={png.get('run')}")

    # 2) 元数据对齐
    if not png.get("meta_fields"):
        client.patch_document(ds, png["id"], md.get("meta_fields") or {})
        rec["detail"].append("元数据已复制")
    else:
        rec["detail"].append("元数据已有")

    # 3) 挂切片（幂等）
    png = client.find_document_by_name(ds, png_name)
    if int(png.get("chunk_count") or 0) > 0:
        rec["detail"].append(f"png 已有 {png['chunk_count']} 切片，跳过挂载")
    else:
        chunks = client.list_chunks(ds, md["id"])
        for c in chunks:
            add_chunk(ds, png["id"], c["content"], (c.get("keywords") or None) or None)
        rec["detail"].append(f"挂载 {len(chunks)} 切片")

    # 4) 验证（两次机会，容排序漂移）
    time.sleep(3)
    ok, hits = verify(client, ds, png_name, question, anchor)
    if not ok:
        time.sleep(4)
        ok, hits = verify(client, ds, png_name, question, anchor)
    rec["detail"].append(f"验证锚点 {anchor}: {hits or '未命中'}")

    if not ok:
        rec["status"] = "FAIL(md 保留)"
        return rec

    # 5) 删 md → 复验
    body = delete_doc(ds, md["id"])
    if body.get("code") != 0:
        rec["status"] = "FAIL(删除失败)"
        rec["detail"].append(str(body))
        return rec
    time.sleep(3)
    ok2, _ = verify(client, ds, png_name, question, anchor)
    rec["status"] = "OK" if ok2 else "WARN(删后复验未命中)"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只处理 png 名含此子串的")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=KEY)
    results = []
    for ds, relpath, question, anchor in TARGETS:
        png_name = (CORPUS / relpath).name
        if args.only and args.only not in png_name:
            continue
        print(f"\n=== {png_name} ===")
        try:
            rec = process(client, ds, relpath, question, anchor, args.dry_run)
        except Exception as e:  # 单张失败不阻断
            rec = {"ds": ds, "png": png_name, "status": "ERROR",
                   "md_id": None, "png_id": None, "chunks": 0,
                   "detail": [repr(e)]}
        print(f"  状态: {rec['status']}")
        for d in rec["detail"]:
            print(f"  - {d}")
        results.append(rec)

    manifest = HERE / "deleted_md_manifest.json"
    manifest.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    n_ok = sum(1 for r in results if r["status"] == "OK")
    print(f"\n==== 完成 {n_ok}/{len(results)} OK；台账 {manifest.name} ====")
    for r in results:
        print(f"  {r['status']:<18} {r['png']}")


if __name__ == "__main__":
    main()
