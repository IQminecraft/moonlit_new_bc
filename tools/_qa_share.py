import sys
from playwright.sync_api import sync_playwright
links = [l.strip() for l in open('tools/_qa_links.txt') if l.strip()]
abyss, teams = links[0], links[1]
fails = []
def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    if not cond: fails.append(msg)

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    pg = b.new_page(viewport={'width':1400,'height':950})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.on('console', lambda m: errs.append(m.text) if m.type=='error' else None)

    # ---- abyss view ----
    pg.goto(abyss, wait_until='networkidle')
    pg.wait_for_selector('.abyss-canvas')
    sel = pg.text_content('#selectedCharLabel')
    check(sel.strip()=='幽境の激戦 編成', f'abyss label = {sel.strip()!r}')
    # calc method row hidden
    calc_hidden = pg.evaluate('''() => { const s=document.getElementById("scoreCalcSelect");
        if(!s) return "missing"; const row=s.closest("div"); return getComputedStyle(row).display==="none"; }''')
    check(calc_hidden is True, f'abyss calc row hidden = {calc_hidden}')
    # header gradient band removed: first .abs child should NOT be a linear-gradient overlay
    grad = pg.evaluate('''() => [...document.querySelectorAll(".abyss-canvas .abs")].some(d => (d.getAttribute("style")||"").includes("linear-gradient") && d.style.height && d.style.top==="0px")''')
    check(not grad, 'abyss header gradient band gone')
    # enter party view: click first member button
    pg.click('.abyss-canvas .m button')
    pg.wait_for_selector('#sharePartyBack')
    back = pg.text_content('#sharePartyBack')
    check(back.strip()=='← 幽境画面に戻る', f'back button = {back.strip()!r}')
    # dots present initially
    dots0 = pg.locator('.tc-body .substat-dot').count()
    # toggle dots OFF/ON live via the settings switch (no reload)
    before_url = pg.url
    pg.evaluate('() => settingsSubstatDotsBtn.click()')
    pg.wait_for_timeout(400)
    check(pg.url==before_url, 'no reload on dot toggle')
    dots1 = pg.locator('.tc-body .substat-dot').count()
    check(dots0>0 and dots1==0, f'dots live {dots0} -> {dots1}')
    # toggle back ON
    pg.evaluate('() => settingsSubstatDotsBtn.click()')
    pg.wait_for_timeout(400)
    dots2 = pg.locator('.tc-body .substat-dot').count()
    check(dots2==dots0, f'dots live back {dots1} -> {dots2}')
    # base_prec live in party view (uid present -> re-fetch): change select
    pg.select_option('#basePrecSelect', '2')
    pg.wait_for_timeout(1500)
    check(pg.url==before_url, 'no reload on base_prec change')
    detail = pg.evaluate('''() => !!document.querySelector(".tc-body")''')
    check(detail, 'party still rendered after precision change')
    # back returns to abyss canvas
    pg.click('#sharePartyBack')
    pg.wait_for_selector('.abyss-canvas')
    check(True, 'back to abyss canvas')
    # calc stays hidden after navigation
    calc_hidden2 = pg.evaluate('''() => { const s=document.getElementById("scoreCalcSelect"); const row=s.closest("div"); return getComputedStyle(row).display==="none"; }''')
    check(calc_hidden2 is True, 'abyss calc row still hidden after nav')

    # ---- teams view: calc row should stay visible ----
    pg.goto(teams, wait_until='networkidle')
    pg.wait_for_selector('.tc-body')
    teams_sel = pg.text_content('#selectedCharLabel')
    check(teams_sel.strip()=='編成共有', f'teams label = {teams_sel.strip()!r}')
    calc_vis = pg.evaluate('''() => { const s=document.getElementById("scoreCalcSelect"); const row=s.closest("div"); return getComputedStyle(row).display!=="none"; }''')
    check(calc_vis is False, 'teams calc row hidden too (snapshot frozen; no Enka re-fetch)')
    # teams home view: dot toggle live too
    td0 = pg.locator('.tc-body .substat-dot').count()
    pg.evaluate('() => settingsSubstatDotsBtn.click()')
    pg.wait_for_timeout(400)
    td1 = pg.locator('.tc-body .substat-dot').count()
    check(td0>0 and td1==0, f'teams home dots live {td0} -> {td1}')
    pg.evaluate('() => settingsSubstatDotsBtn.click()')
    pg.wait_for_timeout(400)

    real = [e for e in errs if 'favicon' not in e.lower()]
    check(not real, f'no console/page errors: {real[:4]}')
    b.close()

print('---')
print('ALL PASS' if not fails else f'{len(fails)} FAILURES')
sys.exit(1 if fails else 0)
