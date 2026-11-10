import subprocess, time, json, urllib.request, asyncio, websockets, base64

chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
user_data = r"C:\Users\anant\AppData\Local\Temp\chrome_vibe_measure_2"

proc = subprocess.Popen([
    chrome_path,
    "--headless=new",
    "--remote-debugging-port=9223",
    f"--user-data-dir={user_data}",
    "--window-size=1440,900",
    "about:blank"
])

time.sleep(2)

async def run_cdp():
    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9223/json").read())
    ws_url = tabs[0]["webSocketDebuggerUrl"]
    
    async with websockets.connect(ws_url) as ws:
        msg_id = 1
        async def send(method, params=None):
            nonlocal msg_id
            m = {"id": msg_id, "method": method}
            if params: m["params"] = params
            msg_id += 1
            await ws.send(json.dumps(m))
            while True:
                res = json.loads(await ws.recv())
                if res.get("id") == m["id"]:
                    return res.get("result", {})

        await send("Page.enable")
        await send("Runtime.enable")
        await send("Network.enable")

        # 1. Fetch cookie directly from backend
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/v1/auth/login",
            data=json.dumps({"email":"admin@vibe.enterprise","password":"Password1234!"}).encode(),
            headers={"Content-Type":"application/json"}
        )
        with urllib.request.urlopen(req) as resp:
            set_cookie = resp.headers.get("Set-Cookie")
            # Extract vibe_session value
            session_val = set_cookie.split("vibe_session=")[1].split(";")[0]

        # Set cookie in Chrome for localhost
        await send("Network.setCookie", {
            "name": "vibe_session",
            "value": session_val,
            "domain": "localhost",
            "path": "/",
            "httpOnly": True,
            "secure": False,
            "sameSite": "Lax"
        })

        pages = [
            ("/", "Dashboard"),
            ("/workspaces", "Workspaces"),
            ("/dashboards", "Dashboards"),
            ("/analytics", "Analytics"),
            ("/reports", "Reports"),
            ("/users", "Users"),
            ("/roles", "Roles"),
            ("/audit", "Audit"),
            ("/files", "Files"),
            ("/system", "System"),
            ("/settings", "Settings")
        ]

        measure_script = """
            (() => {
                const main = document.querySelector('main');
                const container = main ? main.firstElementChild : null;
                const h1 = document.querySelector('h1');
                return JSON.stringify({
                    url: window.location.pathname,
                    containerLeft: container ? Math.round(container.getBoundingClientRect().left) : null,
                    containerWidth: container ? Math.round(container.getBoundingClientRect().width) : null,
                    h1Left: h1 ? Math.round(h1.getBoundingClientRect().left) : null,
                    h1Text: h1 ? h1.textContent.trim().substring(0, 30) : 'none'
                });
            })()
        """

        print("=== MEASURING AUTHENTICATED PAGE BOUNDING BOXES (Viewport 1440px) ===")
        for path, name in pages:
            await send("Page.navigate", {"url": f"http://localhost:3000{path}"})
            await asyncio.sleep(1.2)
            eval_res = await send("Runtime.evaluate", {
                "expression": measure_script,
                "returnByValue": True
            })
            val = json.loads(eval_res.get("result", {}).get("value", "{}"))
            c_left = val.get("containerLeft")
            c_w = val.get("containerWidth")
            h1_left = val.get("h1Left")
            h1_t = val.get("h1Text")
            u = val.get("url")
            print(f"{name:<12} (url: {u:<14}) | Container Left: {c_left}px | Width: {c_w}px | H1 Left: {h1_left}px | Title: {h1_t}")

        # Capture screenshots
        await send("Page.navigate", {"url": "http://localhost:3000/users"})
        await asyncio.sleep(1.0)
        ss = await send("Page.captureScreenshot")
        with open(r"C:\Users\anant\.gemini\antigravity\brain\e16570f2-b8e0-438c-9c36-08751fbe4162\users_aligned.png", "wb") as f:
            f.write(base64.b64decode(ss["data"]))

        await send("Page.navigate", {"url": "http://localhost:3000/analytics"})
        await asyncio.sleep(1.0)
        ss = await send("Page.captureScreenshot")
        with open(r"C:\Users\anant\.gemini\antigravity\brain\e16570f2-b8e0-438c-9c36-08751fbe4162\analytics_aligned.png", "wb") as f:
            f.write(base64.b64decode(ss["data"]))

        print("Screenshots saved successfully.")

try:
    asyncio.run(run_cdp())
finally:
    proc.terminate()
