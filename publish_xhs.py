# -*- coding: utf-8 -*-
"""小红书视频发布（自用封装，跑在 RedBookSkills 的 Chrome 登录态上）。

为什么不用 RedBookSkills 自带的 publish：
  1) 它等视频处理只给 120 秒，8.7MB 的片子经常等不完就退出，标题正文根本没写进去；
  2) 它点发布靠 .publish-page-publish-btn button.bg-red，2026-10 的小红书发布页已换成
     封闭影子根的自定义元素 <xhs-publish-btn>，选择器找不到；
  3) 那个自定义元素横跨「暂存离开」和「发布」两个按钮，按元素中心点会落在两者间的空隙里。

这个脚本的做法：
  - 上传走 CDP 的 DOM.setFileInputFiles，不模拟点击；
  - 标题正文走 Input.insertText，让框架收到正常的输入事件；
  - 原创声明单独处理，走完须知弹窗；
  - 发布按钮先截图找红色像素块定位，再按该坐标点，不猜选择器；
  - 全程不碰「关联活动」，发现活动抽屉被打开就先关掉。

用法：
  python -X utf8 publish_xhs.py --video 片子.mp4 --title "标题" --content-file 正文.txt
  加 --no-original 则跳过原创声明；--dry-run 只填不发。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

import websocket

RB = Path(__file__).resolve().parents[1] / "RedBookSkills" / "scripts"
sys.path.insert(0, str(RB))

PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish"
SUCCESS_MARK = "/publish/success"


class CDP:
    def __init__(self, port: int = 9222):
        self.port = port
        self.ws = None
        self.n = 0

    def page_target(self) -> str:
        d = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json"))
        pages = [p for p in d if p.get("type") == "page" and p.get("webSocketDebuggerUrl")]
        for p in pages:
            if "creator.xiaohongshu.com" in p.get("url", ""):
                return p["webSocketDebuggerUrl"]
        return pages[0]["webSocketDebuggerUrl"]

    def connect(self):
        self.ws = websocket.create_connection(
            self.page_target(), timeout=40, suppress_origin=True
        )

    def call(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                return msg
            # 其它事件（network/console）直接丢掉

    def ev(self, expr: str, wait: float = 20.0):
        mid = self.n + 1
        self.ws.send(json.dumps({
            "id": mid, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True, "awaitPromise": True},
        }))
        self.n = mid
        end = time.time() + wait
        while time.time() < end:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                continue
            if msg.get("id") == mid:
                return msg["result"]["result"].get("value")
        return None

    def click(self, x: float, y: float, settle: float = 1.2):
        for t in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", {
                "type": t, "x": x, "y": y,
                **({"button": "left", "clickCount": 1} if t != "mouseMoved" else {}),
            })
            time.sleep(0.12)
        time.sleep(settle)


def log(msg: str):
    print(f"[xhs] {msg}", flush=True)


# ---------------------------------------------------------------- 页面操作

def ensure_page(c: CDP):
    c.call("Page.navigate", {"url": PUBLISH_URL})
    time.sleep(6)
    if "login" in (c.ev("location.href") or ""):
        raise SystemExit("登录态失效：先在 RedBookSkills 里扫码登录（cdp_publish.py login）")


def upload_video(c: CDP, video: Path):
    """用 CDP 把文件塞进 input，不点上传框。"""
    doc = c.call("DOM.getDocument", {"depth": -1})
    root = doc["result"]["root"]["nodeId"]
    node = c.call("DOM.querySelector", {"nodeId": root, "selector": "input.upload-input"})
    nid = node.get("result", {}).get("nodeId")
    if not nid:
        node = c.call("DOM.querySelector", {"nodeId": root, "selector": "input[type=file]"})
        nid = node.get("result", {}).get("nodeId")
    if not nid:
        raise SystemExit("找不到上传入口，发布页可能又改版了")
    c.call("DOM.setFileInputFiles", {"files": [str(video)], "nodeId": nid})
    log("已塞入文件，等处理")

    deadline = time.time() + 420  # 小红书转码偶发很慢，给到 7 分钟
    while time.time() < deadline:
        time.sleep(5)
        txt = c.ev("document.body.innerText") or ""
        if "检测为高清视频" in txt or "设置封面" in txt:
            log("视频处理完成")
            return
        if "上传失败" in txt or "格式不支持" in txt:
            raise SystemExit("上传被拒：" + txt[:200])
    raise SystemExit("等视频处理超时（420 秒）")


def fill_text(c: CDP, title: str, content: str):
    c.ev("(()=>{const e=document.querySelector('div.d-input input'); if(e){e.focus();} return !!e;})()")
    time.sleep(0.3)
    c.ev("(()=>{const e=document.querySelector('div.d-input input'); if(!e)return; const s=getSelection(); const r=document.createRange(); r.selectNodeContents(e); s.removeAllRanges(); s.addRange(r);})()")
    c.call("Input.insertText", {"text": title})

    ed = "div.tiptap.ProseMirror"
    c.ev(f"(()=>{{const e=document.querySelector('{ed}'); if(e)e.focus(); return !!e;}})()")
    time.sleep(0.3)
    c.call("Input.insertText", {"text": content})
    time.sleep(0.5)
    ok_title = c.ev("(document.querySelector('div.d-input input')||{}).value") or ""
    ok_body = c.ev(f"(document.querySelector('{ed}')||{{}}).innerText") or ""
    log(f"标题 {len(ok_title)} 字 / 正文 {len(ok_body)} 字")
    if not ok_title.strip() or not ok_body.strip():
        raise SystemExit("标题或正文没写进去，停下不发")


def close_activity_drawer(c: CDP):
    """误触「关联」会弹活动抽屉，它会挡住整个发布区，必须先关。"""
    if c.ev("!!document.querySelector('.event-detail-drawer')"):
        log("有关联活动抽屉，点遮罩关掉（不关联任何活动）")
        c.click(300, 500)
        time.sleep(1)


def declare_original(c: CDP):
    close_activity_drawer(c)
    cls = c.ev("(document.querySelector('.original-wrapper .d-switch-simulator')||{}).className") or ""
    if "checked" in cls:
        log("原创声明已是开启状态")
        return
    c.ev("document.querySelector('.original-wrapper').scrollIntoView({block:'center'})")
    time.sleep(0.6)
    r = c.ev("(()=>{const e=document.querySelector('.original-wrapper .d-switch-simulator'); const b=e.getBoundingClientRect(); return [b.x+b.width/2,b.y+b.height/2];})()")
    c.click(r[0], r[1])
    time.sleep(1)

    # 须知弹窗：先勾同意，再点「声明原创」
    for _ in range(10):
        if c.ev("!!document.querySelector('.d-checkbox input')"):
            break
        time.sleep(0.5)
    r = c.ev("(()=>{const e=document.querySelector('.d-checkbox'); const b=e.getBoundingClientRect(); return [b.x+b.width/2,b.y+b.height/2];})()")
    if r:
        c.click(r[0], r[1])
        time.sleep(0.6)
    r = c.ev("""(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='声明原创');
        if(!b) return null; const q=b.getBoundingClientRect(); return [q.x+q.width/2,q.y+q.height/2];})()""")
    if not r:
        raise SystemExit("没找到「声明原创」确认按钮")
    c.click(r[0], r[1])
    time.sleep(1)
    close_activity_drawer(c)
    cls = c.ev("(document.querySelector('.original-wrapper .d-switch-simulator')||{}).className") or ""
    log("原创声明：" + ("已开启" if "checked" in cls else "没开成，需要人工看一眼"))


def find_red_button(c: CDP):
    """截图找红色像素块，换算成 CSS 坐标。返回 (cx, cy) 或 None。"""
    from PIL import Image

    r = c.call("Page.captureScreenshot", {"format": "png"})
    im = Image.open(io.BytesIO(base64.b64decode(r["result"]["data"]))).convert("RGB")
    w, h = im.size
    px = im.load()
    xs, ys = [], []
    top = int(h * 0.70)
    for y in range(top, h):
        for x in range(w):
            rr, g, b = px[x, y]
            if rr > 185 and g < 95 and b < 105:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    dpr = c.ev("devicePixelRatio") or 1.5
    if isinstance(dpr, (list, tuple)):   # evaluate 返回数组时取第一个
        dpr = dpr[0] if dpr else 1.5
    dpr = float(dpr)
    return ((min(xs) + max(xs)) / 2 / dpr, (min(ys) + max(ys)) / 2 / dpr)


def find_red_button_in_host(c: CDP):
    """只在 xhs-publish-btn 的矩形里找红色块，避免扫到页面其它红色元素。"""
    from PIL import Image

    rect = c.ev("""(()=>{const h=document.querySelector('xhs-publish-btn');
        if(!h) return null; const r=h.getBoundingClientRect();
        return [r.x, r.y, r.width, r.height];})()""")
    if not rect:
        return None
    r = c.call("Page.captureScreenshot", {"format": "png"})
    im = Image.open(io.BytesIO(base64.b64decode(r["result"]["data"]))).convert("RGB")
    px = im.load()
    dpr = c.ev("devicePixelRatio") or 1.5
    if isinstance(dpr, (list, tuple)):
        dpr = dpr[0] if dpr else 1.5
    dpr = float(dpr)
    x0, y0, w, h = [float(v) * dpr for v in rect]
    xs, ys = [], []
    for y in range(max(0, int(y0)), min(im.size[1], int(y0 + h))):
        for x in range(max(0, int(x0)), min(im.size[0], int(x0 + w))):
            rr, g, b = px[x, y]
            if rr > 185 and g < 95 and b < 105:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return ((min(xs) + max(xs)) / 2 / dpr, (min(ys) + max(ys)) / 2 / dpr)


def publish(c: CDP):
    close_activity_drawer(c)
    c.ev("window.scrollTo(0,0)")
    time.sleep(0.8)
    # 只认按钮容器里的红块。容器里没有红块说明按钮还没变红（视频处理中），
    # 这时宁可报错退出，也不要退到全屏扫红——2026-11-03 就是这样点歪的。
    pt = None
    for _ in range(10):
        pt = find_red_button_in_host(c)
        if pt:
            break
        time.sleep(2)
    if not pt:
        raise SystemExit("没在底部找到红色发布按钮，页面多半进了异常状态")
    log(f"点发布 ({pt[0]:.0f},{pt[1]:.0f})")
    c.click(pt[0], pt[1], settle=4)
    for _ in range(6):
        url = c.ev("location.href") or ""
        if SUCCESS_MARK in url:
            log("发布成功：" + url[:80])
            return url
        time.sleep(2)
    raise SystemExit("点了发布但没跳到成功页，需要人工确认：" + (c.ev("location.href") or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--content-file", required=True)
    ap.add_argument("--no-original", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--account", default=None, help="Chrome 档案名（九离AI 用 jiuliai），不传=default")
    ap.add_argument("--port", type=int, default=9222)
    a = ap.parse_args()

    video = Path(a.video)
    if not video.exists():
        raise SystemExit(f"视频不存在：{video}")
    content = Path(a.content_file).read_text(encoding="utf-8").strip()

    from chrome_launcher import ensure_chrome  # noqa: E402
    ensure_chrome(port=a.port, headless=False, account=a.account)
    time.sleep(2)

    c = CDP(a.port)
    c.connect()
    ensure_page(c)
    upload_video(c, video)
    fill_text(c, a.title, content)
    if not a.no_original:
        declare_original(c)
    if a.dry_run:
        log("dry-run：已填完，停在这里不发")
        return
    publish(c)


if __name__ == "__main__":
    main()
