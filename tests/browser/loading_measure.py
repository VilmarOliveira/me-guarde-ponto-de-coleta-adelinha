"""Repeatable cold-cache synthetic mobile/desktop benchmark; no external services."""

import sys, tempfile, threading, random, json, statistics, io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image
from app import create_app
from database import DatabaseConnection
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright

folder = Path(tempfile.mkdtemp(prefix="adelinha-load-"))
app = create_app(
    {
        "TESTING": True,
        "SECRET_KEY": "load-fixture",
        "DATABASE": str(folder / "fixture.db"),
    }
)
db = DatabaseConnection(app.config["DATABASE"])
rng = random.Random(42)
image = Image.frombytes("RGB", (2048, 684), rng.randbytes(2048 * 684 * 3))
out = io.BytesIO()
image.save(out, "JPEG", quality=65)
data = out.getvalue()
db.execute(
    "UPDATE settings SET main_banner_image=?,main_banner_mime='image/jpeg',main_banner_updated_at='load-v1'",
    (data,),
)
for position in (2, 3):
    db.execute(
        "INSERT INTO site_banners(position,image,mime_type,alt_text,updated_at,active) VALUES(?,?,'image/jpeg','Fixture','load-v1',1)",
        (position, data),
    )
db.commit()
server = make_server("127.0.0.1", 5096, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
results = {}
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"]
    )
    for name, width in [("mobile", 390), ("desktop", 1440)]:
        runs = []
        for _ in range(3):
            ctx = browser.new_context(
                viewport={"width": width, "height": 844}, service_workers="block"
            )
            page = ctx.new_page()
            page.route("https://**/*", lambda route: route.abort())
            session = ctx.new_cdp_session(page)
            session.send("Network.enable")
            session.send(
                "Network.emulateNetworkConditions",
                {
                    "offline": False,
                    "latency": 150,
                    "downloadThroughput": 200000,
                    "uploadThroughput": 100000,
                },
            )
            page.goto("http://127.0.0.1:5096/", wait_until="domcontentloaded")
            page.wait_for_function(
                "document.querySelector('[data-slide] img').complete && document.querySelector('[data-slide] img').naturalWidth>0"
            )
            timing = page.evaluate(
                """()=>{let img=document.querySelector('[data-slide] img');let r=performance.getEntriesByType('resource').find(r=>r.name===img.currentSrc);return r?.responseEnd||performance.now()}"""
            )
            page.wait_for_timeout(1000)
            runs.append(
                {
                    "first_banner_ms": round(timing),
                    "transferred_bytes": page.evaluate(
                        "performance.getEntriesByType('resource').reduce((n,r)=>n+r.transferSize,0)"
                    ),
                    "banner_bytes": page.evaluate(
                        "performance.getEntriesByType('resource').filter(r=>/banner|banners/.test(r.name)).reduce((n,r)=>n+r.transferSize,0)"
                    ),
                }
            )
            ctx.close()
        results[name] = {
            "median": {key: statistics.median(r[key] for r in runs) for key in runs[0]},
            "runs": runs,
        }
    browser.close()
server.shutdown()
Path(sys.argv[1]).write_text(json.dumps(results, indent=2))
print(json.dumps(results))
