(function () {
    const STYLE_ID = 'genshin-tutorial-style';
    const CSS = [
        '.gt-overlay{position:fixed;inset:0;background:rgba(2,6,12,.52);backdrop-filter:blur(1px);z-index:10000;}',
        '.gt-highlight{position:fixed;z-index:10001;pointer-events:none;border-radius:12px;box-shadow:0 0 0 9999px rgba(2,6,12,.62),0 0 0 2px rgba(94,234,212,.95),0 0 24px rgba(94,234,212,.35);transition:top .16s ease,left .16s ease,width .16s ease,height .16s ease;}',
        '.gt-card{position:fixed;z-index:10002;width:min(370px,calc(100vw - 24px));background:rgba(17,24,39,.98);border:1px solid rgba(148,163,184,.28);color:#e5e7eb;border-radius:14px;box-shadow:0 18px 50px rgba(0,0,0,.45);padding:14px 14px 12px;}',
        '.gt-step{font-size:11px;color:#67e8f9;font-weight:700;letter-spacing:.06em;margin-bottom:4px;}',
        '.gt-title{font-size:15px;font-weight:700;color:#fff;margin-bottom:5px;}',
        '.gt-text{font-size:13px;line-height:1.65;color:#cbd5e1;}',
        '.gt-foot{display:flex;align-items:center;gap:8px;margin-top:12px;}',
        '.gt-count{margin-right:auto;font-size:11px;color:#94a3b8;}',
        '.gt-btn{border:none;border-radius:9px;padding:7px 12px;font-size:12.5px;font-weight:700;cursor:pointer;transition:opacity .15s,transform .1s;}',
        '.gt-btn:active{transform:scale(.98);}',
        '.gt-btn.ghost{background:transparent;border:1px solid rgba(148,163,184,.35);color:#cbd5e1;}',
        '.gt-btn.primary{background:#2dd4bf;color:#082f2a;}',
        '.gt-btn:hover{opacity:.88;}',
        '.gt-hidden{display:none !important;}'
    ].join('\n');

    let overlay = null;
    let highlight = null;
    let card = null;
    let titleEl = null;
    let textEl = null;
    let stepEl = null;
    let countEl = null;
    let prevBtn = null;
    let nextBtn = null;
    let exitBtn = null;
    let steps = [];
    let idx = 0;
    let active = false;
    let doneKey = '';

    function ensureStyle() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = CSS;
        document.head.appendChild(style);
    }

    function createDom() {
        if (overlay) return;
        overlay = document.createElement('div');
        overlay.className = 'gt-overlay gt-hidden';
        highlight = document.createElement('div');
        highlight.className = 'gt-highlight gt-hidden';
        card = document.createElement('div');
        card.className = 'gt-card gt-hidden';
        card.setAttribute('role', 'dialog');
        card.setAttribute('aria-modal', 'true');
        card.innerHTML =
            '<div class="gt-step"></div>' +
            '<div class="gt-title"></div>' +
            '<div class="gt-text"></div>' +
            '<div class="gt-foot">' +
            '<span class="gt-count"></span>' +
            '<button type="button" class="gt-btn ghost gt-exit">終了</button>' +
            '<button type="button" class="gt-btn ghost gt-prev">前へ</button>' +
            '<button type="button" class="gt-btn primary gt-next">次へ</button>' +
            '</div>';
        stepEl = card.querySelector('.gt-step');
        titleEl = card.querySelector('.gt-title');
        textEl = card.querySelector('.gt-text');
        countEl = card.querySelector('.gt-count');
        exitBtn = card.querySelector('.gt-exit');
        prevBtn = card.querySelector('.gt-prev');
        nextBtn = card.querySelector('.gt-next');
        overlay.addEventListener('click', () => next());
        exitBtn.addEventListener('click', () => finish(false));
        prevBtn.addEventListener('click', () => prev());
        nextBtn.addEventListener('click', () => next());
        document.body.appendChild(overlay);
        document.body.appendChild(highlight);
        document.body.appendChild(card);
    }

    function isVisible(el) {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 && rect.height <= 0) return false;
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
    }

    function normalize(list) {
        return (list || []).filter((step) => {
            if (!step || !step.selector) return false;
            const el = document.querySelector(step.selector);
            return el && isVisible(el);
        });
    }

    function currentTarget() {
        const step = steps[idx];
        return step ? document.querySelector(step.selector) : null;
    }

    function placeCard(rect) {
        const gap = 12;
        const margin = 12;
        const cardRect = card.getBoundingClientRect();
        let top = rect.bottom + gap;
        if (top + cardRect.height + margin > window.innerHeight) {
            top = rect.top - cardRect.height - gap;
        }
        if (top < margin) top = margin;
        let left = Math.max(margin, Math.min(rect.left, window.innerWidth - cardRect.width - margin));
        card.style.top = `${Math.round(top)}px`;
        card.style.left = `${Math.round(left)}px`;
    }

    function position() {
        if (!active) return;
        const el = currentTarget();
        if (!el || !isVisible(el)) {
            next();
            return;
        }
        el.scrollIntoView({ block: 'center', inline: 'center' });
        const rect = el.getBoundingClientRect();
        const pad = 6;
        highlight.style.top = `${Math.round(rect.top - pad)}px`;
        highlight.style.left = `${Math.round(rect.left - pad)}px`;
        highlight.style.width = `${Math.round(rect.width + pad * 2)}px`;
        highlight.style.height = `${Math.round(rect.height + pad * 2)}px`;
        placeCard(rect);
    }

    function render() {
        const step = steps[idx];
        if (!step) {
            finish(true);
            return;
        }
        stepEl.textContent = `STEP ${idx + 1} / ${steps.length}`;
        titleEl.textContent = step.title || '';
        textEl.textContent = step.text || '';
        countEl.textContent = `${idx + 1} / ${steps.length}`;
        prevBtn.classList.toggle('gt-hidden', idx === 0);
        nextBtn.textContent = (idx === steps.length - 1) ? '完了' : '次へ';
        position();
    }

    function addListeners() {
        window.addEventListener('resize', position);
        window.addEventListener('scroll', position, true);
        document.addEventListener('keydown', onKey, true);
    }

    function removeListeners() {
        window.removeEventListener('resize', position);
        window.removeEventListener('scroll', position, true);
        document.removeEventListener('keydown', onKey, true);
    }

    function onKey(e) {
        if (!active) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            finish(false);
        } else if (e.key === 'ArrowRight' || e.key === 'Enter') {
            e.preventDefault();
            next();
        } else if (e.key === 'ArrowLeft') {
            e.preventDefault();
            prev();
        }
    }

    function start(rawSteps, opts) {
        ensureStyle();
        createDom();
        steps = normalize(rawSteps);
        if (!steps.length) return false;
        idx = 0;
        active = true;
        doneKey = (opts && opts.doneKey) || '';
        overlay.classList.remove('gt-hidden');
        highlight.classList.remove('gt-hidden');
        card.classList.remove('gt-hidden');
        addListeners();
        render();
        return true;
    }

    function finish(markDone) {
        if (!active) return;
        active = false;
        removeListeners();
        if (overlay) overlay.classList.add('gt-hidden');
        if (highlight) highlight.classList.add('gt-hidden');
        if (card) card.classList.add('gt-hidden');
        if (markDone && doneKey) {
            try { localStorage.setItem(doneKey, '1'); } catch (e) {}
        }
    }

    function next() {
        if (!active) return;
        if (idx < steps.length - 1) {
            idx += 1;
            render();
        } else {
            finish(true);
        }
    }

    function prev() {
        if (!active) return;
        if (idx > 0) {
            idx -= 1;
            render();
        }
    }

    window.GenshinTutorial = {
        start: start,
        close: () => finish(false),
        isDone: function (key) {
            try { return localStorage.getItem(key) === '1'; } catch (e) { return true; }
        },
        markDone: function (key) {
            try { localStorage.setItem(key, '1'); } catch (e) {}
        }
    };
})();
