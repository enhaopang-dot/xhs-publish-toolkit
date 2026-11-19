# -*- coding: utf-8 -*-
"""小红书笔记数据拉取（给中台内容 Agent 吃）。

走 RedBookSkills 的 content-data：那是创作者后台「笔记基础信息」同一份接口，
拿到的曝光/观看/点赞/评论/收藏/涨粉是原始数值，不是页面上四舍五入过的显示值。

输出两份：
  data/xhs_notes.json  结构化，字段对齐 content_live.py 里抖音那套 works
  data/xhs_notes.csv   原始表，人看

用法：python -X utf8 pull_xhs_data.py [--out-dir 目录] [--pages 3]
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RB = Path(__file__).resolve().parents[1] / "RedBookSkills"

BASE = Path(__file__).resolve().parents[3]  # 14 内容管理/05-发布与复盘/自动发布工具/xhs-auto -> 14 内容管理
DEFAULT_OUT = Path(r"D:/Claude/项目/00 AI Native/04-中台程序/data")


def _num(v) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v or "").strip().replace(",", "")
    if s in ("", "-"):
        return 0
    mult = 1
    if s.endswith("万"):
        mult, s = 10000, s[:-1]
    elif s.endswith("亿"):
        mult, s = 100000000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def pull(out_dir: Path, pages: int, account: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "xhs_notes.csv"
    rows: list[dict] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        cmd = [
            sys.executable, "-X", "utf8", "scripts/cdp_publish.py",
        ]
        if account:
            cmd += ["--account", account]
        cmd += [
            "content-data", "--page-num", str(page), "--page-size", "20",
            "--csv-file", str(csv_path),
        ]
        p = subprocess.run(cmd, cwd=str(RB), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        blob = p.stdout or ""
        if "CONTENT_DATA_CSV" not in blob:
            print(f"[page {page}] 失败：{(p.stderr or blob)[-300:]}", flush=True)
            break
        # 从 stdout 里切出 JSON 段（脚本会先打一段结构化结果）
        start = blob.find("{\n")
        end = blob.rfind("}")
        try:
            payload = json.loads(blob[start:end + 1])
        except Exception:
            print(f"[page {page}] 返回体解析失败", flush=True)
            break
        page_rows = payload.get("rows") or []
        new = 0
        for r in page_rows:
            nid = r.get("_id") or ""
            if nid and nid in seen:
                continue
            seen.add(nid)
            rows.append(r)
            new += 1
        print(f"[page {page}] 拿到 {len(page_rows)} 条，新增 {new} 条", flush=True)
        if len(page_rows) < 10:
            break

    works = []
    for r in rows:
        works.append({
            "title": r.get("标题", ""),
            "pub": (r.get("发布时间") or "").strip() or "—",
            "state": "已发布",
            "play": _num(r.get("观看")),
            "like": _num(r.get("点赞")),
            "comment": _num(r.get("评论")),
            "share": _num(r.get("分享")),
            "save": _num(r.get("收藏")),
            "imp": _num(r.get("曝光")),
            "id": r.get("_id", ""),
        })
    works.sort(key=lambda w: w["pub"], reverse=True)

    out = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "account": "九离 AI",
        "ok": bool(works),
        "count": len(works),
        "works": works,
    }
    (out_dir / "xhs_notes.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--account", default=None, help="Chrome 档案名，九离AI 用 jiuliai")
    a = ap.parse_args()
    out = pull(Path(a.out_dir), a.pages, a.account)
    print(json.dumps({"ok": out["ok"], "count": out["count"],
                      "file": str(Path(a.out_dir) / "xhs_notes.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
