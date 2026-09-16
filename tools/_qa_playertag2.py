import json, io, sys, urllib.request
from playwright.sync_api import sync_playwright
BASE = 'http://127.0.0.1:8001'
link = open('tools/_qa_taglinks.txt').read().splitlines()[0]
fails = []
def check(c, m):
    print(('PASS ' if c else 'FAIL ') + m)
    if not c: fails.append(m)
def post_layout(lay):
    req = urllib.request.Request(BASE + '/api/abyss_share_layout',
        data=json.dumps({'layout': lay}).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)
def tag_state(pg):
    return pg.evaluate('''() => { const els=[...document.querySelectorAll(".abyss-canvas .abs")];
        const e=els.find(x=>/IQminecraft|UID \\d/.test(x.textContent));
        return e ? {text:e.textContent.trim(), left:e.style.left, top:e.style.top, size:e.style.fontSize} : null; }''')

def reset_layout():
    post_layout({'playerTag': {'show': True, 'x': 646, 'y': 526, 'size': 15, 'align': 'left'}})

# ---- API semantics ----
reset_layout()  # 既知の基準(x=646)から開始: 前実行の残存値で冪等でなくなるため
r = post_layout({'playerTag': {'show': False, 'align': 'left'}})
check(r['layout']['playerTag']['show'] is False and r['layout']['playerTag']['x'] == 646, f'partial show=false keeps x/y: {r["layout"]["playerTag"]}')
r = post_layout({'playerTag': True})
check(r['layout']['playerTag']['show'] is True and r['layout']['playerTag']['x'] == 646, f'legacy bool -> show, pos kept: {r["layout"]["playerTag"]}')
r = post_layout({'playerTag': {'x': 120, 'y': 300, 'size': 22}})
check(r['layout']['playerTag'] == {'show': True, 'x': 120, 'y': 300, 'size': 22, 'align': 'left'}, f'move via dict: {r["layout"]["playerTag"]}')

# ---- HTML share view ----
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    pg = b.new_page(viewport={'width': 1400, 'height': 950})
    errs = []; pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto(link, wait_until='networkidle'); pg.wait_for_selector('.abyss-canvas')
    t = tag_state(pg)
    check(t and t['left'] == '120px' and t['top'] == '300px' and t['size'] == '22px', f'HTML reflects moved pos: {t}')
    post_layout({'playerTag': {'show': False, 'align': 'left'}})
    pg.goto(link, wait_until='networkidle'); pg.wait_for_selector('.abyss-canvas')
    check(tag_state(pg) is None, 'HTML hidden via show=false')
    # admin panel UI checks (login not needed for DOM presence check via direct tab markup)
    reset_layout()
    check(not errs, f'no pageerrors: {errs[:3]}')
    b.close()

# ---- PIL: output = base native size, tag drawn at design coords ----
body = {"uid": "1812256644", "mode": "abyss", "abyss": {"version": "7.0", "difficulty": "master", "links": {},
    "show_uid": True, "uid": "1812256644", "pname": "IQminecraft",
    "bosses": [{"name": "ボスA", "img": "", "time": "", "chars": []}, {"name": "ボスB", "img": "", "time": "", "chars": []}, {"name": "ボスC", "img": "", "time": "", "chars": []}]}}
def png():
    req = urllib.request.Request(BASE + '/api/team_share_image', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()
from PIL import Image
def region(img, dx, dy, w=260, h=34):
    sx, sy = img.size[0] / 868.0, img.size[1] / 560.0
    x0, y0 = int(dx * sx), int(dy * sy)
    return img.crop((x0, y0, x0 + int(w * sx), y0 + int(h * sy)))
def count_nonbg(img, dx, dy):
    # tag 色(ほぼ白)ピクセルを数える: 文字が描かれているか判定
    rgn = region(img, dx, dy).convert('RGB')
    px = rgn.load()
    n = 0
    for y in range(rgn.height):
        for x in range(rgn.width):
            R, G, B = px[x, y]
            if R > 200 and G > 200 and B > 200 and abs(R - G) < 25 and abs(G - B) < 25:
                n += 1
    return n
post_layout({'playerTag': {'show': True, 'x': 646, 'y': 526, 'size': 15, 'align': 'left'}})
A = Image.open(io.BytesIO(png())).convert('RGB')
check(A.size == (1296, 800), f'PIL output = base native size: {A.size}')
a1 = count_nonbg(A, 646, 526); a2 = count_nonbg(A, 60, 120)
check(a1 > 100, f'PIL tag pixels at default design pos (n={a1})')
check(a2 < max(10, a1 // 10), f'PIL no tag at unrelated pos (n={a2} vs {a1})')
post_layout({'playerTag': {'show': True, 'x': 60, 'y': 120, 'size': 15}})
B = Image.open(io.BytesIO(png())).convert('RGB')
b1 = count_nonbg(B, 646, 526); b2 = count_nonbg(B, 60, 120)
check(b2 > 100 and b1 < max(10, b2 // 10), f'PIL tag moved to design (60,120): new={b2} old={b1}')
reset_layout()
print('---')
print('ALL PASS' if not fails else f'{len(fails)} FAILURES')
sys.exit(1 if fails else 0)
