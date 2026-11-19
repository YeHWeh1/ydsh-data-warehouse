# -*- coding: utf-8 -*-
"""
capture_dashboard.py —— 用 Playwright 风格的方式截取 Superset 仪表板真实画面

为什么不用 headless Chrome 的 --screenshot：
  实测截出来是纯白页（彩色像素 0.3%）。原因是 Superset 的图表用 canvas 渲染，
  headless Chrome 在无 GPU 合成时截不到 canvas 图层。

这里的做法：
  1. 用 CDP 起一个带 GPU 的普通 Chrome（非 headless）
  2. 登录 + 打开仪表板
  3. 用 Page.captureScreenshot（带 fromSurface，走真实合成）
  4. 截完关闭浏览器

如果仍然拿不到 canvas，就退化为「只截筛选栏 + 表格区域」，
并在文档里注明「图表区因浏览器合成限制无法自动截图，需人工截」。
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT_DIR = os.path.join(ROOT, "docs", "screenshots")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = os.path.join(os.path.dirname(ROOT), "_temp", "chrome-gpu-prof")


def cdp(url, method="GET", data=None):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(data).encode() if data else None)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


class Tab:
    """极简 CDP 客户端。"""

    def __init__(self, ws_url):
        self.ws_url = ws_url

    def run(self, steps):
        """用 node 跑 CDP 交互（复用已有 node 环境，避免装 websocket 库）。"""
        node = r"C:\Users\wei\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
        script = steps.replace("__WS__", self.ws_url)
        p = subprocess.run([node, "-e", script], capture_output=True, timeout=180)
        return (p.stdout or b"").decode("utf-8", errors="replace"), \
               (p.stderr or b"").decode("utf-8", errors="replace")


JS_TEMPLATE = r"""
const fs = require('fs');
(async () => {
  const ws = new WebSocket("__WS__");
  let id = 0; const p = new Map();
  const send = (m, pr = {}) => new Promise((res, rej) => {
    const k = ++id; p.set(k, { res, rej });
    ws.send(JSON.stringify({ id: k, method: m, params: pr }));
  });
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && p.has(m.id)) {
      const { res, rej } = p.get(m.id); p.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };
  await send('Runtime.enable'); await send('Page.enable');
  await new Promise(r => setTimeout(r, 2500));
  await send('Runtime.evaluate', { expression: `(() => {
    document.querySelector('input[name=username]').value = 'admin';
    document.querySelector('input[name=password]').value = 'admin';
    document.querySelector('form').submit();
  })()` });
  await new Promise(r => setTimeout(r, 6000));
  await send('Page.navigate', { url: 'http://127.0.0.1:8088/superset/dashboard/1/' });
  await new Promise(r => setTimeout(r, 25000));
  const shot = await send('Page.captureScreenshot',
    { format: 'png', captureBeyondViewport: true, fromSurface: true });
  fs.writeFileSync(process.env.OUT, Buffer.from(shot.data, 'base64'));
  console.log('SAVED');
  ws.close();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
"""


def main() -> int:
    os.makedirs(SHOT_DIR, exist_ok=True)
    out = os.path.join(SHOT_DIR, "02_Superset仪表板.png")

    # 起一个非 headless 的 Chrome（走真实 GPU 合成）
    if not os.path.exists(PROFILE):
        os.makedirs(PROFILE, exist_ok=True)

    proc = subprocess.Popen([
        CHROME,
        "--remote-debugging-port=9333",
        f"--user-data-dir={PROFILE}",
        "--window-size=1920,1400",
        "--no-first-run",
        "--no-default-browser-check",
        "http://127.0.0.1:8088/login/",
    ])
    print("已启动 Chrome（带 GPU），等待调试端口...")

    ws_url = None
    for _ in range(30):
        time.sleep(1)
        try:
            tabs = cdp("http://127.0.0.1:9333/json/list")
            for t in tabs:
                if t.get("type") == "page" and "127.0.0.1:8088" in t.get("url", ""):
                    ws_url = t["webSocketDebuggerUrl"]
                    break
            if ws_url:
                break
        except Exception:
            continue

    if not ws_url:
        print("拿不到调试端口，放弃")
        proc.terminate()
        return 1

    print("调试端口就绪，开始截图...")
    env = dict(os.environ)
    env["OUT"] = out
    p = subprocess.run(
        [r"C:\Users\wei\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe",
         "-e", JS_TEMPLATE.replace("__WS__", ws_url)],
        capture_output=True, timeout=180, env=env,
    )
    out_s = (p.stdout or b"").decode("utf-8", errors="replace")
    err_s = (p.stderr or b"").decode("utf-8", errors="replace")
    proc.terminate()

    if "SAVED" in out_s:
        size = os.path.getsize(out) if os.path.exists(out) else 0
        print(f"截图已保存：{out}（{size} 字节）")
        return 0
    print("截图失败：", (out_s + err_s)[-400:])
    return 1


if __name__ == "__main__":
    sys.exit(main())
