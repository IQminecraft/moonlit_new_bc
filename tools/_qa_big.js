
        const uid = document.getElementById('currentUid').textContent.trim();
        const sidebarAccordionStorageKey = `sidebar_accordion_${uid}`;

        // 検索履歴への保存はここ（取得成功ページ）で行う。
        // 共有閲覧モード(/share/)では履歴を残さない。
        // 存在しないUIDや詳細非公開のUIDはエラーページになるため履歴に入らない。
        // プレイヤー名と先頭キャラのアイコンも保存して履歴表示をリッチにする。
        try {
            if (typeof SHARE_PAYLOAD !== 'undefined' && SHARE_PAYLOAD) throw new Error('share-view');
            const _HISTORY_KEY = 'uid_history';
            let _history = [];
            try { _history = JSON.parse(localStorage.getItem(_HISTORY_KEY) || '[]'); } catch (e) { _history = []; }
            if (!Array.isArray(_history)) _history = [];
            const _nameEl = document.getElementById('currentName');
            const _iconEl = document.querySelector('#charThumbRow img');
            const _icon = (_iconEl && _iconEl.getAttribute('src')) || '';
            // マルチ画面風履歴カード用にスプラッシュ画像も保存
            //（characters/xx_AvatarIcon_yy.webp → splash/UI_Gacha_AvatarImg_yy.webp）
            const _splash = _icon.replace('AvatarIcon', 'Gacha_AvatarImg').replace('/characters/', '/splash/');
            const _entry = {
                uid: uid,
                name: (_nameEl ? _nameEl.textContent : '').trim(),
                icon: _icon,
                splash: _splash,
                level: 0,
                element: 0,
                t: Date.now(),
                pfpId: 0,
                nameCardId: 0,
            };
            _history = _history.filter(item => (typeof item === 'object' && item !== null ? item.uid : item) !== uid);
            _history.unshift(_entry);
            _history = _history.slice(0, 50);
            localStorage.setItem(_HISTORY_KEY, JSON.stringify(_history));
        } catch (e) { /* localStorage 使えない環境は無視 */ }

        // card_data 取得時に表示言語パラメータを付与する（GenshinI18n は /static/js/i18n.js）
        function withLang(sp) {
            try { sp.set('lang', GenshinI18n.getLang()); } catch (e) {}
            return sp;
        }

        // characters.json / weapons.json のエントリから表示用の名前を言語設定に応じて返す
        function listNameOf(data) {
            if (!data) return '';
            if (GenshinI18n.isEn() && data.enName) return data.enName;
            return data.jaName || data.enName || '';
        }

        // ==========================================================
        //  card_data 先読み: ショーケースの全キャラ分をバックグラウンドで
        //  取得しておき、キャラ切替時の待ち時間を減らす。
        //  差し替え表示中などパラメータが違う場合は通常の取得にフォールバック。
        // ==========================================================
        const cardDataPrefetch = new Map(); // params 文字列 → card_data
        let prefetchStarted = false;
        function buildPrefetchParams(charId) {
            const p = new URLSearchParams();
            p.set('calc_method', getSavedCalcMethod(charId));
            if (ver === 'beta') p.set('beta', 'true');
            if (getSavedGrowthPref()) p.set('growth', 'true');
            p.set('base_prec', getSavedBasePrec());
            const resonanceParam = getResonanceParam();
            if (resonanceParam) p.set('resonance', resonanceParam);
            return withLang(p);
        }
        async function prefetchAllCardData() {
            if (prefetchStarted) return;
            prefetchStarted = true;
            const ids = [...document.querySelectorAll('#charThumbRow .char-thumb')]
                .map(el => el.getAttribute('data-char-id')).filter(Boolean);
            for (const cid of ids) {
                try {
                    const key = buildPrefetchParams(cid).toString();
                    if (cardDataPrefetch.has(key)) continue;
                    const res = await fetch(`/api/card_data/${uid}/${cid}?${key}`, { cache: 'no-store' });
                    if (res.ok) {
                        cardDataPrefetch.set(key, await res.json());
                    }
                } catch (e) { /* 先読みはベストエフォート */ }
                await new Promise(r => setTimeout(r, 150)); // サーバー負荷抑止
            }
        }
        window.addEventListener('load', () => setTimeout(prefetchAllCardData, 1200));

        // ==========================================================
        //  生成中の進捗表示: 生成プールの待ち件数をポーリングして表示。
        //  「生成中...」のローディング DOM が消えたら自動停止。
        // ==========================================================
        function startGenProgress() {
            const tick = async () => {
                const el = document.getElementById('genProgressText');
                if (!el) return; // ローディング表示が無くなったら終了
                try {
                    const res = await fetch('/api/card_gen_status', { cache: 'no-store' });
                    if (res.ok) {
                        const d = await res.json();
                        const waiting = Number(d.waiting) || 0;
                        el.textContent = waiting > 0 ? ` / ${GenshinI18n.t('順番待ち')} ${waiting}` : '';
                    }
                } catch (e) { /* ignore */ }
                setTimeout(tick, 1500);
            };
            tick();
        }

        // ==========================================================
        //  スコア履歴: 閲覧したスコアをキャラ別に保存し、小さな折れ線グラフで表示
        //  （admin の UI フラグ show_score_history=false では記録・表示とも無効）
        // ==========================================================
        const SCORE_HISTORY_ENABLED = 0;
        const SCORE_HISTORY_KEY = `score_history_${uid}`;
        function loadScoreHistory() {
            try {
                const all = JSON.parse(localStorage.getItem(SCORE_HISTORY_KEY) || '{}');
                return (all && typeof all === 'object' && !Array.isArray(all)) ? all : {};
            } catch (e) { return {}; }
        }
        function recordScoreHistory(data) {
            try {
                if (!SCORE_HISTORY_ENABLED) return;
                if (!data || typeof currentSelectedCharId === 'undefined' || !currentSelectedCharId) return;
                const score = Number(data.scoreSum);
                if (!isFinite(score)) return;
                const method = String(data.calcMethod || '');
                const all = loadScoreHistory();
                const key = String(currentSelectedCharId);
                const arr = Array.isArray(all[key]) ? all[key] : [];
                const last = arr[arr.length - 1];
                // 同スコア・同方式の再閲覧（30分以内）は追記しない
                if (last && last.score === score && last.method === method && (Date.now() - last.t) < 30 * 60 * 1000) {
                    renderScoreHistoryGraph(key);
                    return;
                }
                arr.push({ t: Date.now(), score, method });
                all[key] = arr.slice(-100);
                localStorage.setItem(SCORE_HISTORY_KEY, JSON.stringify(all));
                renderScoreHistoryGraph(key);
            } catch (e) { /* ignore */ }
        }
        function renderScoreHistoryGraph(charId) {
            const box = document.getElementById('scoreHistoryBox');
            if (!box) return;
            const arr = loadScoreHistory()[String(charId)] || [];
            if (!Array.isArray(arr) || arr.length < 2) {
                box.classList.add('hidden');
                return;
            }
            const scores = arr.map(p => Number(p.score) || 0);
            const min = Math.min(...scores), max = Math.max(...scores);
            const span = Math.max(1e-6, max - min);
            const W = 560, H = 110, PAD = 8;
            const pts = scores.map((s, i) => {
                const x = PAD + (i / (scores.length - 1)) * (W - PAD * 2);
                const y = H - PAD - ((s - min) / span) * (H - PAD * 2);
                return `${x.toFixed(1)},${y.toFixed(1)}`;
            });
            const dots = arr.map((p, i) => {
                const v = Number(p.score) || 0;
                const x = PAD + (i / (arr.length - 1)) * (W - PAD * 2);
                const y = H - PAD - ((v - min) / span) * (H - PAD * 2);
                const d = new Date(p.t);
                const label = `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}  ${p.score}${p.method ? ' (' + p.method + ')' : ''}`;
                return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="var(--accent, #22d3ee)"><title>${esc(label)}</title></circle>`;
            }).join('');
            const label = GenshinI18n.t('スコア履歴');
            box.innerHTML = `
                <div class="text-xs font-semibold mb-1" style="color: var(--text-secondary);">${esc(label)}</div>
                <svg viewBox="0 0 ${W} ${H}" class="w-full" style="height:110px" preserveAspectRatio="none">
                    <polyline points="${pts.join(' ')}" fill="none" stroke="var(--accent, #22d3ee)" stroke-width="2" stroke-linejoin="round"/>
                    ${dots}
                </svg>
                <div class="flex justify-between text-[10px]" style="color: var(--text-muted);">
                    <span>${scores.length}${GenshinI18n.t('件')}</span>
                    <span>min ${min.toFixed(1)} / max ${max.toFixed(1)}</span>
                </div>`;
            box.classList.remove('hidden');
        }

        // ==========================================================
        //  シェア: 表示中のカード画像を Web Share / クリップボード copies / 保存で共有
        // ==========================================================
        async function getCurrentCardBlob() {
            // 生成時に保持した blob を優先（blob: URL は revoke 後に fetch できないため）
            if (window.__lastCardBlob) return window.__lastCardBlob;
            const img = document.getElementById('cardImage');
            const src = img ? img.getAttribute('src') : '';
            if (!src) return null;
            const res = await fetch(src);
            if (!res.ok) return null;
            return await res.blob();
        }
        async function shareCurrentCard() {
            let blob = null;
            try { blob = await getCurrentCardBlob(); } catch (e) {}
            // 編成モード: 表示はHTMLだが、共有は画像生成（PIL）にフォールバックする
            if (!blob && teamMode) {
                const slot = teamSlotById(activeTeamId);
                if (slot) {
                    try {
                        await generateTeamCardImage(slot.chars.slice(), buildTeamConfigs(slot.chars), getAbyssBossParam());
                        blob = await getCurrentCardBlob();
                    } catch (e) { /* 下のエラー表示に流れる */ }
                }
            }
            if (!blob) {
                showToast(GenshinI18n.t('先にカード画像を生成してください'), 'error');
                return;
            }
            const charName = (document.getElementById('selectedCharLabel') || {}).textContent || 'build card';
            const file = new File([blob], 'build_card.png', { type: 'image/png' });
            // 1) Web Share（ファイル共有に対応している環境 = 主にスマホ）
            try {
                if (navigator.canShare && navigator.canShare({ files: [file] })) {
                    await navigator.share({ files: [file], title: charName });
                    return;
                }
            } catch (e) { /* キャンセルは無視してコピーへ */ }
            // 2) クリップボードへ画像コピー
            try {
                if (navigator.clipboard && window.ClipboardItem) {
                    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
                    showToast(GenshinI18n.t('カード画像をコピーしました'), 'success');
                    return;
                }
            } catch (e) { /* ignore */ }
            // 3) フォールバック: ダウンロード
            try {
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'build_card.png';
                a.click();
                setTimeout(() => URL.revokeObjectURL(url), 3000);
                showToast(GenshinI18n.t('画像を保存しました'), 'success');
            } catch (e) {
                showToast(GenshinI18n.t('シェアに失敗しました'), 'error');
            }
        }

        // 言語変更時に表示を再取得（card_data は withLang() 経由で lang パラメータ付きになる）
        document.addEventListener('genshin:langchange', () => {
            try {
                // 編成モード中は単体カードを更新しない（編成カードHTMLを言語付きで再取得）
                if (teamMode) {
                    const slot = (typeof teamSlotById === 'function') ? teamSlotById(activeTeamId) : null;
                    if (slot && slot.chars && slot.chars.length === 4 && typeof generateTeamCardHtml === 'function') {
                        generateTeamCardHtml(slot.chars.slice(), buildTeamConfigs(slot.chars));
                    }
                } else {
                    if (typeof currentSelectedCharId !== 'undefined' && currentSelectedCharId && typeof refreshCurrentCard === 'function') {
                        refreshCurrentCard(currentSelectedCharId, true);
                    }
                    // 画像生成タブ表示中はカード画像も lang 付きで再生成
                    if (typeof viewMode !== 'undefined' && viewMode === 'image' && typeof currentSelectedCharId !== 'undefined' && currentSelectedCharId && typeof loadCardImage === 'function') {
                        loadCardImage(currentSelectedCharId, true);
                    }
                }
                // 再生成ボタンのラベルも現在の状態で言語を反映し直す
                if (typeof updateImageGenButtonLabel === 'function') {
                    const img = document.getElementById('cardImage');
                    updateImageGenButtonLabel(!!(img && img.getAttribute('src') && !img.classList.contains('hidden')));
                }
                if (typeof renderComboRow === 'function') renderComboRow();
                if (typeof updateEditWeaponUI === 'function') updateEditWeaponUI();
                if (typeof renderFakeCharGrid === 'function') renderFakeCharGrid();
                if (typeof renderFakeWeaponGrid === 'function') renderFakeWeaponGrid();
                // スコア履歴グラフのラベルも言語を反映し直す
                if (typeof currentSelectedCharId !== 'undefined' && currentSelectedCharId && typeof renderScoreHistoryGraph === 'function') {
                    renderScoreHistoryGraph(currentSelectedCharId);
                }
                // 再描画で作り直された data-i18n 要素に翻訳を再適用
                GenshinI18n.apply(document);
            } catch (e) { /* ignore */ }
        });

        // シェアボタン: 表示中のカード画像を共有/コピー/保存
        const shareCardBtn = document.getElementById('shareCardBtn');
        if (shareCardBtn) shareCardBtn.addEventListener('click', shareCurrentCard);

        // サーバー由来・ユーザー入力系の文字列を innerHTML に埋め込む前に必ず通す。
        // これを挟まないと JSON 内の `<img onerror=...>` がそのまま HTML として実行される。
        function esc(v) {
            return String(v == null ? '' : v)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        // CSS url('...') に埋め込む文字列。クォート・改行・CSS 構文を壊す文字を除去する。
        function cssUrlSanitize(v) {
            return String(v == null ? '' : v)
                .replace(/\\/g, '')
                .replace(/['"]/g, '')
                .replace(/[\r\n;(),]/g, '');
        }

        // ---- モーダル共通ヘルパー ----
        // Escape で閉じる / 背景のスクロールを固定 / フォーカスをモーダル内に閉じ込める / aria を付与。
        // 生成UIはファクトチェック用のダミーであり、ここでは他UIの挙動を崩さないように最小限に保つ。
        const _modalStack = [];
        let _modalLastFocus = null;

        function _modalFocusables(el) {
            return Array.from(el.querySelectorAll(
                'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
            ));
        }

        function modalGlobalKeydown(e) {
            const top = _modalStack[_modalStack.length - 1];
            if (!top) return;
            if (e.key === 'Escape') {
                e.preventDefault();
                if (top.onEscape) top.onEscape();
                return;
            }
            if (e.key === 'Tab') {
                const items = _modalFocusables(top.el);
                if (!items.length) { e.preventDefault(); return; }
                const first = items[0];
                const last = items[items.length - 1];
                const active = document.activeElement;
                if (e.shiftKey) {
                    if (active === first || !top.el.contains(active)) { e.preventDefault(); last.focus(); }
                } else {
                    if (active === last || !top.el.contains(active)) { e.preventDefault(); first.focus(); }
                }
            }
        }

        /** モーダルを開く。onEscape 省略時は closeModal を呼ぶ。false を渡すと Escape では閉じない(必須選択など) */
        function openModal(el, onEscape) {
            if (!el || el.classList.contains('flex')) return;
            _modalLastFocus = document.activeElement;
            el.classList.remove('hidden');
            el.classList.add('flex');
            el.setAttribute('role', 'dialog');
            el.setAttribute('aria-modal', 'true');
            if (!_modalStack.length) document.addEventListener('keydown', modalGlobalKeydown);
            _modalStack.push({ el, onEscape: typeof onEscape === 'function' ? onEscape : (onEscape === false ? null : () => closeModal(el)) });
            document.body.style.overflow = 'hidden';
            const f = _modalFocusables(el)[0];
            if (f) f.focus();
        }

        function closeModal(el) {
            if (!el) return;
            const i = _modalStack.findIndex(m => m.el === el);
            if (i !== -1) _modalStack.splice(i, 1);
            el.classList.add('hidden');
            el.classList.remove('flex');
            el.removeAttribute('role');
            el.removeAttribute('aria-modal');
            if (!_modalStack.length) {
                document.body.style.overflow = '';
                document.removeEventListener('keydown', modalGlobalKeydown);
            }
            if (_modalLastFocus && _modalLastFocus.focus) _modalLastFocus.focus();
        }

        // ---- 確認ダイアログとトースト（原生 alert/confirm の置き換え）----
        let confirmModalResolve = null;
        const confirmModalEl = document.getElementById('confirmModal');
        const confirmModalBody = document.getElementById('confirmModalBody');
        const confirmModalTitle = document.getElementById('confirmModalTitle');
        const confirmModalOkBtn = document.getElementById('confirmModalOkBtn');
        const confirmModalCancelBtn = document.getElementById('confirmModalCancelBtn');

        function closeConfirmModal(result) {
            closeModal(confirmModalEl);
            if (confirmModalResolve) {
                confirmModalResolve(result);
                confirmModalResolve = null;
            }
        }

        /** confirm() の代替。戻り値は Promise<boolean> */
        function requestConfirmation(message, { title = '確認', confirmText = 'OK', cancelText = 'キャンセル' } = {}) {
            if (confirmModalTitle) confirmModalTitle.textContent = title;
            if (confirmModalBody) confirmModalBody.textContent = message;
            if (confirmModalOkBtn) confirmModalOkBtn.textContent = confirmText;
            if (confirmModalCancelBtn) confirmModalCancelBtn.textContent = cancelText;
            return new Promise((resolve) => {
                confirmModalResolve = resolve;
                openModal(confirmModalEl, () => closeConfirmModal(false));
                if (confirmModalOkBtn) confirmModalOkBtn.focus();
            });
        }
        if (confirmModalOkBtn) confirmModalOkBtn.addEventListener('click', () => closeConfirmModal(true));
        if (confirmModalCancelBtn) confirmModalCancelBtn.addEventListener('click', () => closeConfirmModal(false));

        /** alert() の代替。自動で消えるトースト通知。type: 'info' | 'error' | 'success' */
        function showToast(message, type = 'info') {
            const box = document.getElementById('toastBox');
            if (!box) return;
            const el = document.createElement('div');
            const color = {
                info: 'border-zinc-600 bg-zinc-900/95 text-zinc-100',
                error: 'border-red-500/60 bg-red-950/95 text-red-100',
                success: 'border-emerald-500/60 bg-emerald-950/95 text-emerald-100'
            }[type] || 'border-zinc-600 bg-zinc-900/95 text-zinc-100';
            el.className = `pointer-events-auto max-w-sm rounded-xl border px-4 py-3 text-sm shadow-xl animate-[toastIn_.18s_ease-out] ${color}`;
            el.textContent = message;
            box.appendChild(el);
            setTimeout(() => {
                el.classList.add('opacity-0', 'transition-opacity', 'duration-300');
                setTimeout(() => el.remove(), 320);
            }, 3200);
        }

        // ==========================================================
        //  初回バージョン選択（初回のみ表示。以降は設定からのみ変更可）
        // ==========================================================
        const VER_PREF_KEY_FIRST = 'genshin_build_card_ver_pref';
        function chooseVer(v) {
            if (v !== 'live' && v !== 'beta') v = 'live';
            try { localStorage.setItem(VER_PREF_KEY_FIRST, v); } catch (e) {}
            const params = new URLSearchParams();
            params.set('uid', uid);
            params.set('ver', v);
            window.location.href = `/fetch_uid?${params.toString()}`;
        }
        function applySavedVersionRedirect() {
            // 保存済みのバージョンと現在の ver が違う場合、即時（描画前）に切り替える
            let saved = null;
            try { saved = localStorage.getItem(VER_PREF_KEY_FIRST); } catch (e) { saved = null; }
            if ((saved === 'live' || saved === 'beta') && saved !== ver) {
                const params = new URLSearchParams();
                params.set('uid', uid);
                params.set('ver', saved);
                window.location.replace(`/fetch_uid?${params.toString()}`);
                return true;
            }
            return false;
        }
        // スクリプト評価時に即時適用（Shift+R のハードリロードでも維持される）
        applySavedVersionRedirect();
        function maybeShowVersionChooser() {
            // 保存済みがあれば（一致 or リダイレクト済み）何もしない
            let saved = null;
            try { saved = localStorage.getItem(VER_PREF_KEY_FIRST); } catch (e) { saved = null; }
            if (saved === 'live' || saved === 'beta') return;
            // 初回: 選択モーダルを表示（必須選択なので Escape では閉じない）
            const modal = document.getElementById('verChooseModal');
            if (modal) {
                openModal(modal, false);
            }
        }

        // live / beta の最新バージョンを取得し、選択UIのラベルへ追記（例: Live（正式版）: 7.0）
        (async () => {
            try {
                const res = await fetch('/api/data_versions');
                if (!res.ok) return;
                const d = await res.json();
                const lv = document.getElementById('verChooseLiveVer');
                const bv = document.getElementById('verChooseBetaVer');
                if (lv && d.live) lv.textContent = `: ${d.live}`;
                if (bv && d.beta) bv.textContent = `: ${d.beta}`;
                document.querySelectorAll('.settings-seg-ver').forEach(el => {
                    const v = el.dataset.segVer === 'beta' ? d.beta : d.live;
                    if (v) el.textContent = v;
                });
                // ヘッダーのタイトル横: サイトバージョン + 現在の live/beta データバージョン
                const tv = document.getElementById('topVersionLabel');
                if (tv) {
                    const dataVer = ver === 'beta' ? d.beta : d.live;
                    const parts = [];
                    if (d.site) parts.push(`v${d.site}`);
                    if (dataVer) parts.push(`Ver${dataVer}`);
                    tv.textContent = parts.join(' : ');
                }
            } catch (e) {}
        })();

        // Enka API クールタイム（再取得ボタン制御）
        let enkaCooldownTimer = null;
        function startEnkaCooldown(seconds) {
            const end = Date.now() + seconds * 1000;
            const tick = () => {
                const remain = Math.max(0, Math.ceil((end - Date.now()) / 1000));
                if (!regenerateApiBtn) return;
                if (remain > 0) {
                    regenerateApiBtn.disabled = true;
                    regenerateApiBtn.classList.add('opacity-60', 'cursor-not-allowed');
                    regenerateApiBtn.textContent = `再取得（${remain}s）`;
                } else {
                    regenerateApiBtn.disabled = false;
                    regenerateApiBtn.classList.remove('opacity-60', 'cursor-not-allowed');
                    regenerateApiBtn.textContent = '再取得';
                    clearInterval(enkaCooldownTimer);
                    enkaCooldownTimer = null;
                }
            };
            if (enkaCooldownTimer) clearInterval(enkaCooldownTimer);
            enkaCooldownTimer = setInterval(tick, 1000);
            tick();
        }
        async function initEnkaCooldown() {
            try {
                const res = await fetch(`/api/enka_cooldown?uid=${uid}`);
                const data = await res.json();
                if (data && data.cooldown > 0) startEnkaCooldown(data.cooldown);
            } catch (e) { /* ignore */ }
        }

        // ---- モバイル下部アクションバー連携 ----
        const mobileActionBar = document.getElementById('mobileActionBar');
        const mobileActionRefreshBtn = document.getElementById('mobileActionRefreshBtn');
        const mobileActionRefreshLabel = document.getElementById('mobileActionRefreshLabel');
        const mobileActionFetchBtn = document.getElementById('mobileActionFetchBtn');
        const mobileActionSettingsBtn = document.getElementById('mobileActionSettingsBtn');
        function applyMobileActionBarVisibility() {
            // スマホ幅（640px以下）かつアクションバーがCSSで表示されている場合のみ余白を追加
            if (mobileActionBar && window.matchMedia('(max-width: 640px)').matches) {
                document.body.classList.add('mobile-action-bar-visible');
            } else {
                document.body.classList.remove('mobile-action-bar-visible');
            }
        }
        function syncMobileRefreshLabel() {
            if (mobileActionRefreshLabel) {
                mobileActionRefreshLabel.textContent = (viewMode === 'image' && currentSelectedCharId) ? '再生成' : '生成';
            }
        }
        if (mobileActionRefreshBtn) {
            mobileActionRefreshBtn.addEventListener('click', async () => {
                if (teamMode) { generateActiveTeam(); return; }
                if (!currentSelectedCharId) { showToast(GenshinI18n.t('キャラクターを選択してください'), 'error'); return; }
                if (viewMode !== 'image') {
                    viewMode = 'image';
                    saveViewMode(viewMode);
                    applyViewModeUI();
                    applySettingsPanelUI();
                    applyPageThemeUi();
                    latestGlassRequestToken++;
                    latestHtmlRequestToken++;
                }
                await deleteCachedCardImage(await getSignedCardUrl(buildCardImageUrl(currentSelectedCharId)));
                loadCardImage(currentSelectedCharId, true);
            });
        }
        if (mobileActionFetchBtn) {
            mobileActionFetchBtn.addEventListener('click', () => {
                if (regenerateApiBtn) regenerateApiBtn.click();
            });
        }
        if (mobileActionSettingsBtn) {
            mobileActionSettingsBtn.addEventListener('click', () => {
                if (settingsBtn) settingsBtn.click();
            });
        }
        // 画面幅変化時に余白クラスを再評価
        window.addEventListener('resize', () => {
            applyMobileActionBarVisibility();
            syncMobileRefreshLabel();
        });
        applyMobileActionBarVisibility();

        // ---- サイドバー アコーディオン（開閉状態は localStorage に保存） ----
        function saveSidebarAccordionState() {
            const state = {};
            document.querySelectorAll('[data-sidebar-accordion-btn]').forEach(btn => {
                const body = document.getElementById(btn.getAttribute('aria-controls'));
                state[btn.id] = body ? !body.classList.contains('collapsed') : true;
            });
            try { localStorage.setItem(sidebarAccordionStorageKey, JSON.stringify(state)); } catch (e) { /* ignore */ }
        }

        function initSidebarAccordion() {
            const btns = document.querySelectorAll('[data-sidebar-accordion-btn]');
            btns.forEach(btn => {
                const body = document.getElementById(btn.getAttribute('aria-controls'));
                if (!body) return;
                btn.addEventListener('click', () => {
                    const collapsed = body.classList.toggle('collapsed');
                    btn.setAttribute('aria-expanded', String(!collapsed));
                    saveSidebarAccordionState();
                });
            });
            // 保存済みの開閉状態を復元（デフォルトはすべて展開）
            try {
                const saved = JSON.parse(localStorage.getItem(sidebarAccordionStorageKey)) || {};
                btns.forEach(btn => {
                    const body = document.getElementById(btn.getAttribute('aria-controls'));
                    if (!body || saved[btn.id] !== false) return;
                    body.classList.add('collapsed');
                    btn.setAttribute('aria-expanded', 'false');
                });
            } catch (e) { /* ignore */ }
        }
        initSidebarAccordion();

        const teamCardBtn = document.getElementById('teamCardBtn');
        const settingsBtn = document.getElementById('settingsBtn');
        const settingsModal = document.getElementById('settingsModal');
        const scoreCalcSelect = document.getElementById('scoreCalcSelect');
        const regenerateApiBtn = document.getElementById('regenerateApiBtn');
        const regenerateImageBtn = document.getElementById('regenerateImageBtn');
        const cardImage = document.getElementById('cardImage');
        const placeholderText = document.getElementById('placeholderText');
        const imageAwaitBox = document.getElementById('imageAwaitBox');
        const imageAwaitText = document.getElementById('imageAwaitText');
        const generateCardImageBtn = document.getElementById('generateCardImageBtn'); // optional (center btn removed)
        const htmlCardContainer = document.getElementById('htmlCardContainer');
        const disclaimerFooter = document.getElementById('disclaimerFooter');

        const comboRow = document.getElementById('comboRow');
        const teamComboRow = document.getElementById('teamComboRow');
        // 編成モード中は teamComboRow へ、それ以外は comboRow へ描画する
        function activeComboRow() {
            return (teamMode && teamComboRow && teamViewOnly !== true) ? teamComboRow : comboRow;
        }
        const editWeaponBtn = document.getElementById('editWeaponBtn');
        const editWeaponIcon = document.getElementById('editWeaponIcon');
        const editWeaponLabel = document.getElementById('editWeaponLabel');

        const fakeCharModal = document.getElementById('fakeCharModal');
        const fakeCharGrid = document.getElementById('fakeCharGrid');
        const fakeCharGridStatus = document.getElementById('fakeCharGridStatus');
        const fakeWeaponModal = document.getElementById('fakeWeaponModal');
        const fakeWeaponGrid = document.getElementById('fakeWeaponGrid');
        const fakeWeaponGridStatus = document.getElementById('fakeWeaponGridStatus');

        // ==========================================================
        //  設定パネル（設定ボタン）
        //  ・データソース：Beta / Live をワンボタンでトグル。
        //    クエリ ?ver= を書き換えて /fetch_uid に再アクセスする。
        //    選んだ状態は localStorage に保存し、artifacter.html の検索フォーム
        //    から来た時に自動で同じ ver が使われるようにする
        //  ・表示テーマ：ライト / ダークをワンボタンでトグル（localStorage保存）
        // ==========================================================
        const VER_PREF_KEY = 'genshin_build_card_ver_pref';
        const THEME_PREF_KEY = 'genshin_build_card_theme_pref';
        const FORMAT_PREF_KEY = 'genshin_build_card_img_format_pref';
        const SERVER_STATS_PREF_KEY = 'genshin_build_card_server_stats_pref';
        const SAVE_MODE_KEY = 'genshin_build_card_save_mode';
        const GROWTH_PREF_KEY = 'genshin_build_card_growth_mode';
        const BASE_PREC_PREF_KEY = 'genshin_build_card_base_prec';
        const SUBSTAT_DOTS_PREF_KEY = 'genshin_build_card_substat_dots';
        const SHOW_UID_PREF_KEY = 'genshin_build_card_show_uid';
        const CARD_THEME_PREF_KEY = 'genshin_build_card_card_theme';
        const VALID_CARD_THEMES = ['glass', 'cinema', 'scorecard'];
        const IMAGE_THEME_PREF_KEY = 'genshin_build_card_image_theme';
        const VALID_IMAGE_THEMES = ['glass', 'cinema', 'scorecard'];
        // 元素共鳴（単体カード用の手動選択・UID毎に保存）
        const RESONANCE_PREF_KEY = 'genshin_build_card_resonance_' + uid;

        // 生成画像の保存先（client=ブラウザにキャッシュ / server=サーバーに保存）
        function getSavedSaveMode() {
            try { return localStorage.getItem(SAVE_MODE_KEY) === 'server' ? 'server' : 'client'; } catch (e) { return 'client'; }
        }
        function setSaveMode(m) {
            try { localStorage.setItem(SAVE_MODE_KEY, m === 'server' ? 'server' : 'client'); } catch (e) {}
        }
        function applySaveModeLabel() {
            if (!settingsSaveModeBtns || !settingsSaveModeBtns.length) return;
            const mode = getSavedSaveMode();
            settingsSaveModeBtns.forEach(btn => {
                const on = btn.dataset.settingsSavemode === mode;
                btn.classList.toggle('selected', on);
                btn.setAttribute('aria-checked', on ? 'true' : 'false');
            });
        }

        const settingsVerBtns = document.querySelectorAll('[data-settings-ver]');
        const settingsThemeBtns = document.querySelectorAll('[data-settings-theme]');
        const settingsLangBtns = document.querySelectorAll('[data-settings-lang]');
        const settingsFormatLabel = document.getElementById('settingsFormatLabel');
        const settingsServerStatsBtn = document.getElementById('settingsServerStatsBtn');
        const settingsServerStatsToggle = document.getElementById('settingsServerStatsToggle');
        const settingsSaveModeBtns = document.querySelectorAll('[data-settings-savemode]');
        const settingsStatusViewBtn = document.getElementById('settingsStatusViewBtn');
        const settingsStatusViewToggle = document.getElementById('settingsStatusViewToggle');
        const settingsCardThemeBtns = document.querySelectorAll('.settings-card-theme-btn');
        const settingsImageThemeBtns = document.querySelectorAll('.settings-image-theme-btn');
        const settingsSubstatDotsBtn = document.getElementById('settingsSubstatDotsBtn');
        const settingsSubstatDotsToggle = document.getElementById('settingsSubstatDotsToggle');
        const settingsSubstatDotsKnob = document.getElementById('settingsSubstatDotsKnob');
        const settingsShowUidBtn = document.getElementById('settingsShowUidBtn');
        const settingsShowUidToggle = document.getElementById('settingsShowUidToggle');
        const settingsShowUidKnob = document.getElementById('settingsShowUidKnob');
        const serverStatsPanel = document.getElementById('serverStatsPanel');
        const serverStatsCpuVal = document.getElementById('serverStatsCpuVal');
        const serverStatsCpuBar = document.getElementById('serverStatsCpuBar');
        const serverStatsMemVal = document.getElementById('serverStatsMemVal');
        const serverStatsMemBar = document.getElementById('serverStatsMemBar');
        const serverStatsMemDetail = document.getElementById('serverStatsMemDetail');
        const serverStatsProcVal = document.getElementById('serverStatsProcVal');
        const serverStatsCacheVal = document.getElementById('serverStatsCacheVal');
        let serverStatsTimer = null;

        // ==========================================================
        //  未確定データ(BETA)閲覧同意モーダル
        //  Promiseを返し、「同意して閲覧する」でtrue、「戻る」/背景クリックでfalseを解決する
        // ==========================================================
        const betaConsentModal = document.getElementById('betaConsentModal');
        const betaConsentAgreeBtn = document.getElementById('betaConsentAgreeBtn');
        const betaConsentDeclineBtn = document.getElementById('betaConsentDeclineBtn');
        let betaConsentResolve = null;

        function openBetaConsentModal() {
            return new Promise((resolve) => {
                betaConsentResolve = resolve;
                openModal(betaConsentModal, () => closeBetaConsentModal(false));
            });
        }

        function closeBetaConsentModal(agreed) {
            closeModal(betaConsentModal);
            if (betaConsentResolve) {
                betaConsentResolve(agreed);
                betaConsentResolve = null;
            }
        }

        if (betaConsentAgreeBtn) betaConsentAgreeBtn.addEventListener('click', () => closeBetaConsentModal(true));
        if (betaConsentDeclineBtn) betaConsentDeclineBtn.addEventListener('click', () => closeBetaConsentModal(false));

        // 注意: ここで VER_PREF_KEY を自動上書きしないこと（選択済みの live/beta を
        // 現在の ver で毎回上書きすると、applySavedVersionRedirect とリダイレクトループする）

        function getSavedThemePref() {
            try {
                return localStorage.getItem(THEME_PREF_KEY) === 'light' ? 'light' : 'dark';
            } catch (e) {
                return 'dark';
            }
        }

        function setThemePref(theme) {
            const next = theme === 'light' ? 'light' : 'dark';
            try {
                localStorage.setItem(THEME_PREF_KEY, next);
            } catch (e) { /* ignore storage errors */ }
            document.documentElement.setAttribute('data-theme', next);
        }

        const themeModeBtn = document.getElementById('themeModeBtn');
        if (themeModeBtn) {
            themeModeBtn.addEventListener('click', () => {
                setThemePref(getSavedThemePref() === 'light' ? 'dark' : 'light');
                if (typeof viewMode !== 'undefined' && viewMode === 'image' && currentSelectedCharId && !teamMode) {
                    refreshCurrentCard(currentSelectedCharId, false, false, true);
                }
            });
        }

        function getSavedFormatPref() {
            // WEBP 廃止: 保存値に関係なく常に PNG
            return 'png';
        }

        function setFormatPref(fmt) {
            // WEBP 廃止: 常に PNG として保存（切替ボタンは無効化済み）
            try {
                localStorage.setItem(FORMAT_PREF_KEY, 'png');
            } catch (e) { /* ignore storage errors */ }
        }

        function getSavedServerStatsPref() {
            try {
                return localStorage.getItem(SERVER_STATS_PREF_KEY) === '1';
            } catch (e) {
                return false;
            }
        }

        function setServerStatsPref(on) {
            try {
                localStorage.setItem(SERVER_STATS_PREF_KEY, on ? '1' : '0');
            } catch (e) { /* ignore */ }
        }

        function getSavedGrowthPref() {
            try {
                return localStorage.getItem(GROWTH_PREF_KEY) === '1';
            } catch (e) {
                return false;
            }
        }

        function setGrowthPref(on) {
            try {
                localStorage.setItem(GROWTH_PREF_KEY, on ? '1' : '0');
            } catch (e) { /* ignore */ }
        }

        function getSavedSubstatDotsPref() {
            try {
                return localStorage.getItem(SUBSTAT_DOTS_PREF_KEY) !== '0';
            } catch (e) {
                return true;
            }
        }
        function setSubstatDotsPref(on) {
            try {
                localStorage.setItem(SUBSTAT_DOTS_PREF_KEY, on ? '1' : '0');
            } catch (e) { /* ignore */ }
        }

        function getSavedShowUidPref() {
            try {
                return localStorage.getItem(SHOW_UID_PREF_KEY) === '1';
            } catch (e) {
                return false;
            }
        }
        function setShowUidPref(on) {
            try {
                localStorage.setItem(SHOW_UID_PREF_KEY, on ? '1' : '0');
            } catch (e) { /* ignore */ }
        }

        // ==========================================================
        //  元素共鳴（単体カードの手動選択・最大2つ）
        //  app/card/resonance.py の RESONANCE_TYPES と内容を同期すること。
        //  ステータスに効く共鳴のみが対象（編成カードが一時的に無効なため、
        //  編成からの自動判定ではなく手動で有効な共鳴を選ぶ）。
        // ==========================================================
        const RESONANCE_MAX = 2;
        const RESONANCE_OPTIONS = [
            { key: 'pyro',   elem: 'Pyro',   name: '熱誠の炎', label: '攻撃力+25%',  icon: 'static/assets/props/pyro.png' },
            { key: 'hydro',  elem: 'Hydro',  name: '治療の水', label: 'HP上限+25%',  icon: 'static/assets/props/hydro.png' },
            { key: 'cryo',   elem: 'Cryo',   name: '粉砕の氷', label: '会心率+15%',  icon: 'static/assets/props/cryo.png' },
            { key: 'dendro', elem: 'Dendro', name: '蔓生の草', label: '元素熟知+50', icon: 'static/assets/props/dendro.png' },
        ];

        function getSelectedResonances() {
            try {
                const raw = (localStorage.getItem(RESONANCE_PREF_KEY) || '')
                    .split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
                const keys = [];
                for (const k of raw) {
                    if (RESONANCE_OPTIONS.some(o => o.key === k) && !keys.includes(k)) keys.push(k);
                    if (keys.length >= RESONANCE_MAX) break;
                }
                return keys;
            } catch (e) {
                return [];
            }
        }

        function setSelectedResonances(keys) {
            try {
                localStorage.setItem(RESONANCE_PREF_KEY, (keys || []).join(','));
            } catch (e) { /* ignore */ }
        }

        /** 画像生成 / カードデータ用のクエリパラメータ（未選択は空文字） */
        function getResonanceParam() {
            // デフォルト（実データ）キャラには共鳴を反映しない。
            // 差し替えキャラ選択中のみ有効にする。
            if (!activeComboId) return '';
            return getSelectedResonances().join(',');
        }

        /** ステータス編集パネルの表示切替（デフォルトキャラ選択中・編成モード中は非表示） */
        function updateStatusEditorVisibility() {
            const section = document.getElementById('sidebarStatusSection');
            if (!section) return;
            // 編成モード中は共鳴が編成カード側で自動判定されるため非表示
            section.classList.toggle('hidden', teamMode || !activeComboId);
        }

        function renderResonanceChips() {
            const container = document.getElementById('resonanceChips');
            if (!container) return;
            const selected = getSelectedResonances();
            container.innerHTML = RESONANCE_OPTIONS.map(opt => {
                const on = selected.includes(opt.key);
                return `<button type="button" data-resonance-key="${opt.key}" aria-pressed="${on ? 'true' : 'false'}"
                    class="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs border transition-all text-left ${on ? 'bg-cyan-900/40 border-cyan-500 text-white' : 'bg-zinc-800 border-zinc-700 text-zinc-300 hover:border-zinc-500'}">
                    <img src="/${opt.icon}" class="w-4 h-4 object-contain shrink-0" alt="">
                    <span class="font-semibold whitespace-nowrap">${opt.name}</span>
                    <span class="${on ? 'text-cyan-300' : 'text-zinc-500'}">${opt.label}</span>
                    <span class="ml-auto ${on ? 'text-cyan-400 font-bold' : 'text-zinc-700'}">${on ? '✓' : ''}</span>
                </button>`;
            }).join('');
        }

        function bindResonanceChips() {
            const container = document.getElementById('resonanceChips');
            if (!container) return;
            container.addEventListener('click', (e) => {
                const btn = e.target.closest('button[data-resonance-key]');
                if (!btn) return;
                const key = btn.getAttribute('data-resonance-key');
                let sel = getSelectedResonances();
                if (sel.includes(key)) {
                    sel = sel.filter(k => k !== key);
                } else {
                    if (sel.length >= RESONANCE_MAX) {
                        showToast(`元素共鳴は最大${RESONANCE_MAX}つまで選択できます`, 'error');
                        return;
                    }
                    sel.push(key);
                }
                setSelectedResonances(sel);
                renderResonanceChips();
                onResonanceChanged();
            });
        }

        function onResonanceChanged() {
            // 編成モード中は単体カードを更新しない（編成カードは一時的に無効）
            if (teamMode) return;
            if (!currentSelectedCharId) return;
            // resonance はURLパラメータ変わるためキャッシュ衝突は起きない
            if (viewMode === 'glass') loadGlassCard(currentSelectedCharId);
            else if (viewMode === 'html') loadHtmlCard(currentSelectedCharId);
            else loadCardImage(currentSelectedCharId, true);
        }

        renderResonanceChips();
        bindResonanceChips();

        // ビルドカード（HTML表示）のカードテーマ: glass / cinema / scorecard
        function getSavedCardTheme() {
            try {
                const v = localStorage.getItem(CARD_THEME_PREF_KEY);
                return VALID_CARD_THEMES.includes(v) ? v : 'glass';
            } catch (e) {
                return 'glass';
            }
        }
        function setCardTheme(theme) {
            try {
                if (VALID_CARD_THEMES.includes(theme)) localStorage.setItem(CARD_THEME_PREF_KEY, theme);
            } catch (e) { /* ignore */ }
        }

        // 生成画像のカードテーマ: glass / cinema / scorecard
        // （HTML表示のカードテーマとは独立。未設定時は cinema を既定とする）
        function getSavedImageTheme() {
            try {
                const v = localStorage.getItem(IMAGE_THEME_PREF_KEY);
                return VALID_IMAGE_THEMES.includes(v) ? v : 'cinema';
            } catch (e) {
                return 'cinema';
            }
        }
        function setImageTheme(theme) {
            try {
                if (VALID_IMAGE_THEMES.includes(theme)) localStorage.setItem(IMAGE_THEME_PREF_KEY, theme);
            } catch (e) { /* ignore */ }
        }

        // メイン表示ボックスにページテーマクラスを適用/解除する
        // （HTML表示= glass ビューのときのみ。画像・旧HTML表示では解除）
        function applyPageThemeUi() {
            const box = document.getElementById('mainDisplayBox');
            if (!box) return;
            let theme = 'glass';
            try {
                theme = (viewMode === 'glass') ? getSavedCardTheme() : 'glass';
            } catch (e) {
                theme = 'glass';
            }
            box.classList.toggle('page-theme-cinema', theme === 'cinema');
            box.classList.toggle('page-theme-scorecard', theme === 'scorecard');
            applyBgControlsVisibility();
        }

        function getSavedBasePrec() {
            try {
                const v = localStorage.getItem(BASE_PREC_PREF_KEY);
                return (v === '0' || v === '2') ? v : '0';
            } catch (e) {
                return '0';
            }
        }

        function setBasePrec(prec) {
            try {
                localStorage.setItem(BASE_PREC_PREF_KEY, (prec === '0' || prec === '2') ? prec : '0');
            } catch (e) { /* ignore */ }
        }

        function barColorClass(pct, kind) {
            if (pct == null || !Number.isFinite(pct)) return kind === 'cpu' ? 'bg-cyan-400' : 'bg-emerald-400';
            if (pct >= 90) return 'bg-red-400';
            if (pct >= 70) return 'bg-amber-400';
            return kind === 'cpu' ? 'bg-cyan-400' : 'bg-emerald-400';
        }

        function applyServerStatsToUI(data) {
            if (!data || !data.ok) {
                if (serverStatsCpuVal) serverStatsCpuVal.textContent = '—';
                if (serverStatsMemVal) serverStatsMemVal.textContent = '—';
                if (serverStatsMemDetail) serverStatsMemDetail.textContent = (data && data.error) ? String(data.error) : '取得失敗';
                return;
            }
            const cpu = data.cpu_percent;
            const mem = data.mem_percent;
            if (serverStatsCpuVal) {
                serverStatsCpuVal.textContent = (cpu == null || cpu === undefined) ? 'N/A' : `${cpu}%`;
                serverStatsCpuVal.title = data.cpu_source
                    ? `システム全体 / source: ${data.cpu_source} / cores: ${data.cpu_count ?? '?'}`
                    : 'システム全体';
            }
            if (serverStatsCpuBar) {
                const w = (cpu == null || cpu === undefined) ? 0 : Math.max(0, Math.min(100, Number(cpu)));
                serverStatsCpuBar.style.width = `${w}%`;
                serverStatsCpuBar.className = `h-full rounded-full transition-all duration-500 ${barColorClass(cpu, 'cpu')}`;
            }
            if (serverStatsMemVal) {
                serverStatsMemVal.textContent = (mem == null || mem === undefined) ? 'N/A' : `${mem}%`;
            }
            if (serverStatsMemBar) {
                const w = (mem == null || mem === undefined) ? 0 : Math.max(0, Math.min(100, Number(mem)));
                serverStatsMemBar.style.width = `${w}%`;
                serverStatsMemBar.className = `h-full rounded-full transition-all duration-500 ${barColorClass(mem, 'mem')}`;
            }
            if (serverStatsMemDetail) {
                if (data.mem_used_mb != null && data.mem_total_mb != null) {
                    serverStatsMemDetail.textContent = `${data.mem_used_mb} / ${data.mem_total_mb} MB`;
                } else if (data.errors && data.errors.length) {
                    serverStatsMemDetail.textContent = data.errors[0];
                } else {
                    serverStatsMemDetail.textContent = '—';
                }
            }
            if (serverStatsProcVal) {
                serverStatsProcVal.textContent = (data.process_mb != null) ? `${data.process_mb} MB` : 'N/A';
            }
            if (serverStatsCacheVal && data.cache) {
                const c = data.cache;
                serverStatsCacheVal.textContent = `cache i${c.images||0}/r${c.resized||0}/s${c.splash_blur||0}`;
                serverStatsCacheVal.title = `images=${c.images} resized=${c.resized} splash_blur=${c.splash_blur} fonts=${c.fonts} region_bgs=${c.region_bgs}`;
            }
        }

        async function fetchServerStatsOnce() {
            try {
                const res = await fetch('/api/server_stats', { cache: 'no-store' });
                if (!res.ok) return;
                const data = await res.json();
                applyServerStatsToUI(data);
            } catch (e) {
                /* offline / 一時エラーは無視 */
            }
        }

        function startServerStatsPolling() {
            if (serverStatsTimer) return;
            fetchServerStatsOnce().then(() => setTimeout(fetchServerStatsOnce, 400));
            serverStatsTimer = setInterval(fetchServerStatsOnce, 1000);
        }

        function stopServerStatsPolling() {
            if (serverStatsTimer) {
                clearInterval(serverStatsTimer);
                serverStatsTimer = null;
            }
        }

        function applyServerStatsVisibility() {
            const on = getSavedServerStatsPref();
            if (serverStatsPanel) {
                serverStatsPanel.classList.toggle('hidden', !on);
            }
            if (on) startServerStatsPolling();
            else stopServerStatsPolling();
        }

        function closeSettingsPanel() {
            if (settingsModal) closeModal(settingsModal);
        }

        function toggleSettingsPanel() {
            if (!settingsModal) return;
            if (settingsModal.classList.contains('flex')) closeModal(settingsModal);
            else {
                applySettingsPanelUI();
                openModal(settingsModal, closeSettingsPanel);
            }
        }

        function applySettingsPanelUI() {
            if (settingsVerBtns && settingsVerBtns.length) {
                settingsVerBtns.forEach(btn => {
                    const on = btn.dataset.settingsVer === ver;
                    btn.classList.toggle('selected', on);
                    btn.setAttribute('aria-checked', on ? 'true' : 'false');
                });
            }
            if (settingsThemeBtns && settingsThemeBtns.length) {
                const curTheme = getSavedThemePref();
                settingsThemeBtns.forEach(btn => {
                    const on = btn.dataset.settingsTheme === curTheme;
                    btn.classList.toggle('selected', on);
                    btn.setAttribute('aria-checked', on ? 'true' : 'false');
                });
            }
            if (settingsLangBtns && settingsLangBtns.length) {
                const curLang = GenshinI18n.getLang();
                settingsLangBtns.forEach(btn => {
                    const on = btn.dataset.settingsLang === curLang;
                    btn.classList.toggle('selected', on);
                    btn.setAttribute('aria-checked', on ? 'true' : 'false');
                });
            }
            if (settingsFormatLabel) {
                settingsFormatLabel.textContent = 'PNG（無圧縮）';
            }
            if (settingsServerStatsToggle) {
                const statsOn = getSavedServerStatsPref();
                settingsServerStatsToggle.classList.toggle('on', statsOn);
                if (settingsServerStatsBtn) settingsServerStatsBtn.setAttribute('aria-checked', statsOn ? 'true' : 'false');
            }
            if (settingsStatusViewToggle) {
                const statusOn = (typeof viewMode !== 'undefined' && viewMode === 'html');
                settingsStatusViewToggle.classList.toggle('on', statusOn);
                if (settingsStatusViewBtn) settingsStatusViewBtn.setAttribute('aria-checked', statusOn ? 'true' : 'false');
            }
            if (settingsCardThemeBtns && settingsCardThemeBtns.length) {
                const curCardTheme = getSavedCardTheme();
                settingsCardThemeBtns.forEach(btn => {
                    const on = btn.dataset.cardTheme === curCardTheme;
                    btn.classList.toggle('selected', on);
                    btn.setAttribute('aria-checked', on ? 'true' : 'false');
                });
            }
            if (settingsImageThemeBtns && settingsImageThemeBtns.length) {
                const curImageTheme = getSavedImageTheme();
                settingsImageThemeBtns.forEach(btn => {
                    const on = btn.dataset.imageTheme === curImageTheme;
                    btn.classList.toggle('selected', on);
                    btn.setAttribute('aria-checked', on ? 'true' : 'false');
                });
            }
            applySaveModeLabel();
            if (settingsGrowthToggle) {
                const growthOn = getSavedGrowthPref();
                settingsGrowthToggle.classList.toggle('bg-cyan-500', growthOn);
                settingsGrowthToggle.classList.toggle('bg-zinc-600', !growthOn);
                const settingsGrowthKnob = document.getElementById('settingsGrowthKnob');
                if (settingsGrowthKnob) {
                    settingsGrowthKnob.classList.toggle('translate-x-4', growthOn);
                    settingsGrowthKnob.classList.toggle('bg-white', growthOn);
                    settingsGrowthKnob.classList.toggle('bg-gray-300', !growthOn);
                }
                const settingsGrowthBtn = document.getElementById('settingsGrowthBtn');
                if (settingsGrowthBtn) settingsGrowthBtn.setAttribute('aria-checked', growthOn ? 'true' : 'false');
            }
            if (settingsSubstatDotsToggle) {
                const dotsOn = getSavedSubstatDotsPref();
                settingsSubstatDotsToggle.classList.toggle('bg-cyan-500', dotsOn);
                settingsSubstatDotsToggle.classList.toggle('bg-zinc-600', !dotsOn);
                if (settingsSubstatDotsKnob) {
                    settingsSubstatDotsKnob.classList.toggle('translate-x-4', dotsOn);
                    settingsSubstatDotsKnob.classList.toggle('bg-white', dotsOn);
                    settingsSubstatDotsKnob.classList.toggle('bg-gray-300', !dotsOn);
                }
                if (settingsSubstatDotsBtn) settingsSubstatDotsBtn.setAttribute('aria-checked', dotsOn ? 'true' : 'false');
            }
            if (settingsShowUidToggle) {
                const uidOn = getSavedShowUidPref();
                settingsShowUidToggle.classList.toggle('bg-cyan-500', uidOn);
                settingsShowUidToggle.classList.toggle('bg-zinc-600', !uidOn);
                if (settingsShowUidKnob) {
                    settingsShowUidKnob.classList.toggle('translate-x-4', uidOn);
                    settingsShowUidKnob.classList.toggle('bg-white', uidOn);
                    settingsShowUidKnob.classList.toggle('bg-gray-300', !uidOn);
                }
                if (settingsShowUidBtn) settingsShowUidBtn.setAttribute('aria-checked', uidOn ? 'true' : 'false');
            }
        }

        if (settingsBtn && settingsModal) {
            settingsBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                toggleSettingsPanel();
            });

            // モーダル化したため、外側クリックでの close は backdrop onclick に任せる
            // （従来の document click リスナーは不要になった）

            if (settingsStatusViewBtn) {
                settingsStatusViewBtn.addEventListener('click', () => {
                    // ON ⇔ OFF をトグル。OFF に戻すときは前回のタブ(保存値)へ
                    const next = (viewMode === 'html') ? (getSavedViewMode() === 'html' ? 'glass' : getSavedViewMode()) : 'html';
                    setViewMode(next);
                    applySettingsPanelUI();
                });
            }

            if (settingsCardThemeBtns && settingsCardThemeBtns.length) {
                settingsCardThemeBtns.forEach(btn => {
                    btn.addEventListener('click', () => {
                        const theme = btn.dataset.cardTheme;
                        if (!VALID_CARD_THEMES.includes(theme) || theme === getSavedCardTheme()) return;
                        setCardTheme(theme);
                        applySettingsPanelUI();
                        applyPageThemeUi();
                        // 表示中のビルドカードを新テーマで再描画
                        if (typeof viewMode !== 'undefined' && viewMode === 'glass' && currentSelectedCharId) {
                            loadGlassCard(currentSelectedCharId);
                        }
                    });
                });
            }

            if (settingsImageThemeBtns && settingsImageThemeBtns.length) {
                settingsImageThemeBtns.forEach(btn => {
                    btn.addEventListener('click', () => {
                        const theme = btn.dataset.imageTheme;
                        if (!VALID_IMAGE_THEMES.includes(theme) || theme === getSavedImageTheme()) return;
                        setImageTheme(theme);
                        applySettingsPanelUI();
                        applyBgControlsVisibility();
                        // 画像生成タブ: テーマがURLパラメータになるため新テーマで再生成
                        if (typeof viewMode !== 'undefined' && viewMode === 'image' && currentSelectedCharId && !teamMode) {
                            loadCardImage(currentSelectedCharId, true);
                        }
                    });
                });
            }

            if (settingsVerBtns && settingsVerBtns.length) {
                settingsVerBtns.forEach(btn => {
                    btn.addEventListener('click', async () => {
                        const nextVer = (btn.dataset.settingsVer === 'beta') ? 'beta' : 'live';
                        if (nextVer === ver) return;
                        if (nextVer === 'beta') {
                            const agreed = await openBetaConsentModal();
                            if (!agreed) return;
                        }
                        closeSettingsPanel();
                        try {
                            localStorage.setItem(VER_PREF_KEY, nextVer);
                        } catch (e) { /* ignore storage errors */ }
                        const params = new URLSearchParams();
                        params.set('uid', uid);
                        params.set('ver', nextVer);
                        window.location.href = `/fetch_uid?${params.toString()}`;
                    });
                });
            }

            if (settingsThemeBtns && settingsThemeBtns.length) {
                settingsThemeBtns.forEach(btn => {
                    btn.addEventListener('click', () => {
                        const picked = (btn.dataset.settingsTheme === 'light') ? 'light' : 'dark';
                        if (picked === getSavedThemePref()) return;
                        setThemePref(picked);
                        applySettingsPanelUI();
                    });
                });
            }

            // 言語切替（設定モーダル）: ja ⇄ en。カード画像も lang 付きで再生成される
            if (settingsLangBtns && settingsLangBtns.length) {
                settingsLangBtns.forEach(btn => {
                    btn.addEventListener('click', () => {
                        const picked = (btn.dataset.settingsLang === 'en') ? 'en' : 'ja';
                        if (picked === GenshinI18n.getLang()) return;
                        GenshinI18n.setLang(picked);
                        applySettingsPanelUI();
                    });
                });
            }

            // WEBP 廃止により形式切替は廃止（静的な表示のみ）。

            if (settingsServerStatsBtn) {
                settingsServerStatsBtn.addEventListener('click', () => {
                    const next = !getSavedServerStatsPref();
                    setServerStatsPref(next);
                    applySettingsPanelUI();
                    applyServerStatsVisibility();
                });
            }

            if (settingsSaveModeBtns && settingsSaveModeBtns.length) {
                settingsSaveModeBtns.forEach(btn => {
                    btn.addEventListener('click', () => {
                        const mode = (btn.dataset.settingsSavemode === 'server') ? 'server' : 'client';
                        if (mode === getSavedSaveMode()) return;
                        setSaveMode(mode);
                        applySettingsPanelUI();
                    });
                });
            }

            if (settingsGrowthBtn) {
                settingsGrowthBtn.addEventListener('click', () => {
                    const next = !getSavedGrowthPref();
                    setGrowthPref(next);
                    applySettingsPanelUI();
                    // 育成モード切替後は表示中のカードを更新する
                    if (typeof reloadCurrentCard === 'function') reloadCurrentCard();
                    else if (typeof loadCurrentCardByViewMode === 'function') loadCurrentCardByViewMode();
                    else if (currentSelectedCharId) {
                        if (viewMode === 'glass') loadGlassCard(currentSelectedCharId);
                        else if (viewMode === 'html') loadHtmlCard(currentSelectedCharId);
                        else loadCardImage(currentSelectedCharId, true);
                    }
                });
            }

            if (settingsSubstatDotsBtn) {
                settingsSubstatDotsBtn.addEventListener('click', () => {
                    const next = !getSavedSubstatDotsPref();
                    setSubstatDotsPref(next);
                    applySettingsPanelUI();
                    // 伸び値ドット切替後は表示中のカードを更新する
                    if (typeof reloadCurrentCard === 'function') reloadCurrentCard();
                    else if (typeof loadCurrentCardByViewMode === 'function') loadCurrentCardByViewMode();
                    else if (currentSelectedCharId) {
                        if (viewMode === 'glass') loadGlassCard(currentSelectedCharId);
                        else if (viewMode === 'html') loadHtmlCard(currentSelectedCharId);
                        else loadCardImage(currentSelectedCharId, true);
                    }
                });
            }

            if (settingsShowUidBtn) {
                settingsShowUidBtn.addEventListener('click', () => {
                    const next = !getSavedShowUidPref();
                    setShowUidPref(next);
                    applySettingsPanelUI();
                    // UID表示切替後は表示中のカードを更新する
                    if (typeof reloadCurrentCard === 'function') reloadCurrentCard();
                    else if (typeof loadCurrentCardByViewMode === 'function') loadCurrentCardByViewMode();
                    else if (currentSelectedCharId) {
                        if (viewMode === 'glass') loadGlassCard(currentSelectedCharId);
                        else if (viewMode === 'html') loadHtmlCard(currentSelectedCharId);
                        else loadCardImage(currentSelectedCharId, true);
                    }
                });
            }

            applySaveModeLabel();
            applyServerStatsVisibility();
        }

        // applySettingsPanelUI() は viewMode に依存するため、viewMode が定義された後
        // （スクリプト末尾の applyViewModeUI() 直後）に呼ぶ。ここでは呼ばない。

        document.addEventListener('visibilitychange', () => {
            if (!getSavedServerStatsPref()) return;
            if (document.hidden) stopServerStatsPolling();
            else startServerStatsPolling();
        });

        let currentSelectedCharId = null;

        let combos = [];
        let activeComboId = '';
        /** 実データ武器（差し替えなし時の表示用）。カードデータ取得時に更新 */
        let lastKnownRealWeapon = { name: '', icon: '' };

        let allCharList = {};
        let allWeaponList = {};
        let charIconCache = {};
        let weaponIconCache = {};
        let listsLoaded = false;
        const pendingIconFetches = new Set();
        let allCharIconsPromise = null;
        // Beta専用（live リストに存在しない）判定用。取得失敗時は null のまま → New!! を出さない（誤表示防止）
        let liveCharIds = null;
        let liveWeaponIds = null;

        const TRAVELER_BASE_IDS = ['10000005', '10000007', '10000117', '10000118'];
        const ELEMENT_ORDER = ['Anemo', 'Geo', 'Electro', 'Dendro', 'Hydro', 'Pyro', 'Cryo'];

        function baseIdOf(id) {
            return String(id).split('-')[0];
        }

        function sortCharIds(ids) {
            const normal = [];
            const traveler = [];
            ids.forEach(id => {
                if (TRAVELER_BASE_IDS.includes(baseIdOf(id))) {
                    traveler.push(id);
                } else {
                    normal.push(id);
                }
            });

            normal.sort((a, b) => Number(baseIdOf(b)) - Number(baseIdOf(a)) || String(b).localeCompare(String(a)));

            traveler.sort((a, b) => {
                const baseDiff = TRAVELER_BASE_IDS.indexOf(baseIdOf(a)) - TRAVELER_BASE_IDS.indexOf(baseIdOf(b));
                if (baseDiff !== 0) return baseDiff;
                const elA = (allCharList[a] || {}).element || '';
                const elB = (allCharList[b] || {}).element || '';
                const elDiff = ELEMENT_ORDER.indexOf(elA) - ELEMENT_ORDER.indexOf(elB);
                if (elDiff !== 0) return elDiff;
                return String(a).localeCompare(String(b));
            });

            return [...normal, ...traveler];
        }

        async function loadFakeLists() {
            try {
                const listsBase = ver === 'beta' ? '/static/beta/data/lists' : '/static/data/lists';
                const [charRes, weaponRes] = await Promise.all([
                    fetch(`${listsBase}/characters.json`),
                    fetch(`${listsBase}/weapons.json`)
                ]);
                allCharList = charRes.ok ? await charRes.json() : {};
                allWeaponList = weaponRes.ok ? await weaponRes.json() : {};
            } catch (e) {
                console.error('fake_char / fake_weapon 用リストの取得に失敗しました:', e);
                allCharList = {};
                allWeaponList = {};
            }
            listsLoaded = true;

            // New!! バッジ判定用に live リストも取得する（beta モードのみ。失敗時は New!! を出さない）
            if (ver === 'beta') {
                try {
                    const [liveCharRes, liveWeaponRes] = await Promise.all([
                        fetch('/static/data/lists/characters.json'),
                        fetch('/static/data/lists/weapons.json')
                    ]);
                    if (liveCharRes.ok) liveCharIds = new Set(Object.keys(await liveCharRes.json()));
                    if (liveWeaponRes.ok) liveWeaponIds = new Set(Object.keys(await liveWeaponRes.json()));
                } catch (e) {
                    console.warn('New!! 判定用の live リスト取得に失敗しました:', e);
                }
            }
            // サーバー描画済みのキャラサムネイル行にも New!! を反映する
            applyBetaNewBadgesToCharThumbs();

            if (currentSelectedCharId) {
                loadComboState(currentSelectedCharId);
                const c1 = sanitizeCombosForVer();
                const c2 = sanitizeHiddenCombos();
                if (c1 || c2) saveComboState(currentSelectedCharId);
                renderComboRow();
                updateEditWeaponUI();
                ensureComboIconsLoaded().then(() => { renderComboRow(); updateEditWeaponUI(); });
                if (activeComboId) {
                    refreshCurrentCard(currentSelectedCharId, false);
                }
            }
        }

        function ensureComboIconsLoaded() {
            const charIds = combos.map(c => c.fakeChar).filter(Boolean);
            const weaponIds = combos.map(c => c.fakeWeapon).filter(Boolean);
            return Promise.all([
                ensureCharIconsLoaded(charIds),
                ensureWeaponIconsLoaded(weaponIds)
            ]);
        }

        // Beta専用（live データに存在しない）キャラ/武器の判定。
        // live リスト取得失敗時は null のまま → false を返し New!! を出さない。
        function isBetaOnlyChar(id) {
            if (ver !== 'beta' || !liveCharIds) return false;
            return !liveCharIds.has(String(id));
        }
        function isBetaOnlyWeapon(id) {
            if (ver !== 'beta' || !liveWeaponIds) return false;
            return !liveWeaponIds.has(String(id));
        }
        // positionClass: '' = 右上（既定）, 'tl' = 左上（×ボタン等と重ならないようにする場合）
        function betaNewBadgeHTML(positionClass) {
            return `<div class="beta-new-badge ${positionClass || ''}">New!!</div>`;
        }
        // ページ初期表示のサムネイル行（サーバー描画分）に New!! を付ける
        function applyBetaNewBadgesToCharThumbs() {
            if (ver !== 'beta' || !liveCharIds) return;
            document.querySelectorAll('#charThumbRow .char-thumb').forEach(thumb => {
                if (thumb.querySelector('.beta-new-badge')) return;
                if (isBetaOnlyChar(thumb.getAttribute('data-char-id'))) {
                    thumb.insertAdjacentHTML('beforeend', betaNewBadgeHTML('tl lg'));
                }
            });
        }

        // 差し替え機能で非表示にする対象（キャラ: 134/135 系統、武器: 数値IDが3始まり）
        const HIDDEN_SWAP_CHAR_BASE_IDS = new Set(['10000134', '10000135']);
        function isSwapHiddenChar(id) {
            return HIDDEN_SWAP_CHAR_BASE_IDS.has(baseIdOf(id));
        }
        function isSwapHiddenWeapon(id) {
            return String(id).startsWith('3');
        }
        // 非表示対象を参照しているコンボを除去する（アクティブ中ならデフォルトに戻す）
        function sanitizeHiddenCombos() {
            let changed = false;
            const valid = [];
            for (const c of combos) {
                if (c.fakeChar && isSwapHiddenChar(c.fakeChar)) {
                    if (activeComboId === c.id) activeComboId = '';
                    changed = true;
                    continue;
                }
                if (c.fakeWeapon && isSwapHiddenWeapon(c.fakeWeapon)) {
                    c.fakeWeapon = '';
                    changed = true;
                }
                valid.push(c);
            }
            combos = valid;
            return changed;
        }

        async function fetchCharIcon(id) {
            const key = `char:${id}`;
            if (charIconCache[id] !== undefined || pendingIconFetches.has(key)) return;
            pendingIconFetches.add(key);
            try {
                let fromBeta = false;
                let res = await fetch(`/static/data/characters/${id}.json`);
                if (!res.ok && ver === 'beta') {
                    fromBeta = true;
                    res = await fetch(`/static/beta/data/characters/${id}.json`);
                }
                if (res.ok) {
                    const data = await res.json();
                    charIconCache[id] = { icon: data.icon, fromBeta };
                }
            } catch (e) { /* skip individual failures */ }
            pendingIconFetches.delete(key);
        }

        async function fetchWeaponIcon(id) {
            const key = `weapon:${id}`;
            if (weaponIconCache[id] !== undefined || pendingIconFetches.has(key)) return;
            pendingIconFetches.add(key);
            try {
                let fromBeta = false;
                let res = await fetch(`/static/data/weapons/${id}.json`);
                if (!res.ok && ver === 'beta') {
                    fromBeta = true;
                    res = await fetch(`/static/beta/data/weapons/${id}.json`);
                }
                if (res.ok) {
                    const data = await res.json();
                    weaponIconCache[id] = { icon: data.icon, fromBeta };
                }
            } catch (e) { /* skip individual failures */ }
            pendingIconFetches.delete(key);
        }

        function charAssetUrl(id) {
            const entry = charIconCache[id];
            if (!entry || !entry.icon) return '';
            const root = entry.fromBeta ? '/static/beta/assets' : '/static/assets';
            return `${root}/characters/${entry.icon}.webp`;
        }

        function weaponAssetUrl(id) {
            const entry = weaponIconCache[id];
            if (!entry || !entry.icon) return '';
            const root = entry.fromBeta ? '/static/beta/assets' : '/static/assets';
            return `${root}/weapons/${entry.icon}.webp`;
        }

        function ensureCharIconsLoaded(ids) {
            const targets = [...new Set(ids)].filter(id => charIconCache[id] === undefined);
            return Promise.all(targets.map(fetchCharIcon));
        }

        function ensureWeaponIconsLoaded(ids) {
            const targets = [...new Set(ids)].filter(id => weaponIconCache[id] === undefined);
            return Promise.all(targets.map(fetchWeaponIcon));
        }

        function ensureAllCharIconsLoaded() {
            if (!allCharIconsPromise) {
                allCharIconsPromise = ensureCharIconsLoaded(Object.keys(allCharList));
            }
            return allCharIconsPromise;
        }

        function qualityToRarity(q) {
            if (q === 'QUALITY_ORANGE' || q === 'QUALITY_ORANGE_SP') return 5;
            if (q === 'QUALITY_PURPLE') return 4;
            if (q === 'QUALITY_BLUE') return 3;
            if (q === 'QUALITY_GREEN') return 2;
            return 1;
        }

        function realCharIconSrc(charId) {
            const thumb = document.querySelector(`.char-thumb[data-char-id="${charId}"] img`);
            if (thumb && thumb.getAttribute('src')) return thumb.getAttribute('src');
            // 編成モード中は .char-thumb が消えるため、ショーケース一覧から取得
            if (typeof teamCharIconSrc === 'function') return teamCharIconSrc(charId);
            return '';
        }

        function comboStorageKeys(charId) {
            return {
                list: `combo_list_${uid}_${charId}`,
                active: `combo_active_${uid}_${charId}`
            };
        }

        function loadComboState(charId) {
            const keys = comboStorageKeys(charId);
            try {
                combos = JSON.parse(localStorage.getItem(keys.list)) || [];
            } catch (e) {
                combos = [];
            }
            activeComboId = localStorage.getItem(keys.active) || '';

            if (activeComboId && !combos.find(c => c.id === activeComboId)) {
                activeComboId = '';
            }
        }

        function saveComboState(charId) {
            const keys = comboStorageKeys(charId);
            localStorage.setItem(keys.list, JSON.stringify(combos));
            localStorage.setItem(keys.active, activeComboId);
        }

        // live モードなのに Beta 専用キャラ/武器がコンボに残っている場合
        // （Beta閲覧中に保存された組み合わせ）はデフォルトに戻す。
        // リスト取得に失敗して空の場合は誤削除を避けるためスキップ。
        function sanitizeCombosForVer() {
            if (ver === 'beta') return false;
            const charListOk = Object.keys(allCharList).length > 0;
            const weaponListOk = Object.keys(allWeaponList).length > 0;
            if (!charListOk && !weaponListOk) return false;

            let changed = false;
            const valid = [];
            for (const c of combos) {
                if (charListOk && c.fakeChar && !allCharList[c.fakeChar]) {
                    // Beta専用キャラのコンボは破棄（選択中ならデフォルトに戻る）
                    if (activeComboId === c.id) activeComboId = '';
                    changed = true;
                    continue;
                }
                if (weaponListOk && c.fakeWeapon && !allWeaponList[c.fakeWeapon]) {
                    c.fakeWeapon = '';
                    changed = true;
                }
                valid.push(c);
            }
            combos = valid;
            return changed;
        }

        function getActiveCombo() {
            if (!activeComboId) return null;
            return combos.find(c => c.id === activeComboId) || null;
        }

        function getActiveSelection() {
            const combo = getActiveCombo();
            if (combo) return { fakeChar: combo.fakeChar, fakeWeapon: combo.fakeWeapon };
            return { fakeChar: '', fakeWeapon: '' };
        }

        function getActiveWeaponType(charId) {
            const combo = getActiveCombo();
            const targetId = combo ? combo.fakeChar : charId;
            if (targetId && allCharList[targetId]) {
                return allCharList[targetId].weaponType;
            }
            return '';
        }

        function renderComboRow() {
            const target = (teamMode && teamComboRow) ? teamComboRow : comboRow;
            if (!target) return;
            target.innerHTML = '';

            const defaultTile = document.createElement('div');
            defaultTile.className = `combo-tile relative w-14 h-14 rounded-lg border-2 border-zinc-700 bg-zinc-800 cursor-pointer ${!activeComboId ? 'active' : ''}`;
            defaultTile.title = GenshinI18n.t('デフォルト（実データ）');
            const defaultIconSrc = currentSelectedCharId ? realCharIconSrc(currentSelectedCharId) : '';
            defaultTile.innerHTML = `
                <div class="relative w-full h-full rounded-md overflow-hidden">
                    ${defaultIconSrc ? `<img src="${defaultIconSrc}" class="w-full h-full object-cover drag-none">` : ''}
                    <div class="char-name-label absolute bottom-0 inset-x-0 bg-black/70 text-[9px] leading-tight text-center truncate px-0.5" data-i18n>デフォルト</div>
                </div>
            `;
            defaultTile.onclick = () => activateCombo('');
            target.appendChild(defaultTile);

            combos.forEach(combo => {
                const data = allCharList[combo.fakeChar];
                if (!data) return;
                if (isSwapHiddenChar(combo.fakeChar)) return;
                const iconUrl = charAssetUrl(combo.fakeChar);
                const rarity = qualityToRarity(data.qualityType);

                const tile = document.createElement('div');
                tile.className = `combo-tile relative w-14 h-14 rounded-lg border-2 rarity-${rarity} bg-zinc-800 cursor-pointer ${activeComboId === combo.id ? 'active' : ''}`;
                tile.title = listNameOf(data);
                tile.onclick = () => activateCombo(combo.id);

                const weaponIconUrl = (combo.fakeWeapon && !isSwapHiddenWeapon(combo.fakeWeapon)) ? weaponAssetUrl(combo.fakeWeapon) : '';
                tile.innerHTML = `
                    <div class="relative w-full h-full rounded-md overflow-hidden">
                        ${iconUrl ? `<img src="${iconUrl}" class="w-full h-full object-cover drag-none">` : `<div class="w-full h-full flex items-center justify-center text-[9px] text-zinc-400 text-center px-0.5">${esc(listNameOf(data))}</div>`}
                        <div class="char-name-label absolute bottom-0 inset-x-0 bg-black/70 text-[9px] leading-tight text-center truncate px-0.5">${esc(listNameOf(data))}</div>
                    </div>
                    <div class="combo-remove" onclick="event.stopPropagation(); removeCombo('${combo.id}')">&times;</div>
                    ${weaponIconUrl ? `<div class="weapon-badge"><img src="${weaponIconUrl}" class="w-full h-full object-cover"></div>` : ''}
                `;
                if (isBetaOnlyChar(combo.fakeChar)) {
                    tile.insertAdjacentHTML('beforeend', betaNewBadgeHTML('tl lg'));
                }
                target.appendChild(tile);
            });

            const addTile = document.createElement('div');
            addTile.className = 'w-14 h-14 rounded-lg border-2 border-dashed border-zinc-600 hover:border-cyan-400 flex items-center justify-center cursor-pointer text-xl text-zinc-500 hover:text-cyan-400 transition-all';
            addTile.title = '差し替えキャラを追加';
            addTile.textContent = '+';
            addTile.onclick = openFakeCharModal;
            target.appendChild(addTile);

            updateStatusEditorVisibility();
        }

        function activateCombo(comboId) {
            activeComboId = comboId;
            saveComboState(currentSelectedCharId);
            renderComboRow();
            updateEditWeaponUI();
            // デフォルトに戻したときは、差し替えキャラの武器が残らないよう必ず実データ武器を取り直す
            if (!comboId && currentSelectedCharId) {
                lastKnownRealWeapon = { name: '', icon: '' };
                ensureRealWeaponKnown(currentSelectedCharId);
            }
            if (teamMode) {
                if (typeof renderTeamCharRow === 'function') renderTeamCharRow();
                if (typeof updateTeamMemberLabel === 'function') updateTeamMemberLabel(currentSelectedCharId);
            } else {
                refreshCurrentCard(currentSelectedCharId, false, true);
            }
        }

        /** 差し替え無しの実データ武器を取得して lastKnownRealWeapon を埋める */
        async function ensureRealWeaponKnown(charId) {
            if (!charId) return;
            try {
                const method = getSavedCalcMethod(charId);
                const p = new URLSearchParams();
                p.set('calc_method', method);
                // fake_char / fake_weapon は付けない＝実データ武器
                if (ver === 'beta') p.set('beta', 'true');
                const res = await fetch(`/api/card_data/${uid}/${charId}?${withLang(p)}`);
                if (!res.ok) return;
                const data = await res.json();
                // 取得中にキャラや差し替えが変わっていたら適用しない
                if (currentSelectedCharId !== charId) return;
                if (activeComboId) return; // デフォルト以外に切り替わっていた
                setLastKnownRealWeapon(data);
            } catch (e) { /* ignore */ }
        }

        function removeCombo(comboId) {
            combos = combos.filter(c => c.id !== comboId);
            if (activeComboId === comboId) {
                activeComboId = '';
            }
            saveComboState(currentSelectedCharId);
            renderComboRow();
            updateEditWeaponUI();
            // デフォルトに戻った場合は差し替えキャラの武器を残さず実データ武器を取り直す
            if (!activeComboId && currentSelectedCharId) {
                lastKnownRealWeapon = { name: '', icon: '' };
                ensureRealWeaponKnown(currentSelectedCharId);
            }
            if (teamMode) {
                if (typeof renderTeamCharRow === 'function') renderTeamCharRow();
                if (typeof updateTeamMemberLabel === 'function') updateTeamMemberLabel(currentSelectedCharId);
            } else {
                refreshCurrentCard(currentSelectedCharId, false, true);
            }
        }

        async function openFakeCharModal() {
            openModal(fakeCharModal, closeFakeCharModal);
            renderFakeCharGrid();

            const missingIds = Object.keys(allCharList).filter(id => charIconCache[id] === undefined);
            if (missingIds.length) {
                fakeCharGridStatus.textContent = GenshinI18n.t('アイコンを読み込み中...');
                await ensureAllCharIconsLoaded();
                fakeCharGridStatus.textContent = '';
                renderFakeCharGrid();
            }
        }

        function closeFakeCharModal() {
            closeModal(fakeCharModal);
        }

        function renderFakeCharGrid() {
            fakeCharGrid.innerHTML = '';

            const sortedIds = sortCharIds(Object.keys(allCharList)).filter(id => !isSwapHiddenChar(id));
            sortedIds.forEach(id => {
                const data = allCharList[id];
                const iconUrl = charAssetUrl(id);
                const rarity = qualityToRarity(data.qualityType);
                const tile = document.createElement('div');
                tile.className = `picker-tile relative w-24 h-24 shrink-0 cursor-pointer rounded-lg border-2 rarity-${rarity} bg-zinc-800 overflow-hidden`;
                tile.title = listNameOf(data);
                tile.onclick = () => addComboForChar(id);

                if (iconUrl) {
                    tile.innerHTML = `
                        <img src="${iconUrl}" class="w-full h-full object-cover drag-none" loading="lazy">
                        <div class="char-name-label absolute bottom-0 inset-x-0 bg-black/70 text-[10px] leading-tight text-center truncate px-0.5 py-0.5">${esc(listNameOf(data))}</div>
                    `;
                } else {
                    tile.innerHTML = `<div class="w-full h-full flex items-center justify-center text-[10px] text-zinc-500 text-center px-1">${esc(listNameOf(data))}</div>`;
                }
                if (isBetaOnlyChar(id)) {
                    tile.insertAdjacentHTML('beforeend', betaNewBadgeHTML('lg'));
                }
                fakeCharGrid.appendChild(tile);
            });
        }

        function addComboForChar(fakeCharId) {
            const combo = {
                id: `combo_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
                fakeChar: fakeCharId,
                fakeWeapon: ''
            };
            combos.push(combo);
            activeComboId = combo.id;
            saveComboState(currentSelectedCharId);
            renderComboRow();
            updateEditWeaponUI();
            closeFakeCharModal();
            if (teamMode) {
                if (typeof renderTeamCharRow === 'function') renderTeamCharRow();
                if (typeof updateTeamMemberLabel === 'function') updateTeamMemberLabel(currentSelectedCharId);
            } else {
                refreshCurrentCard(currentSelectedCharId, false, true);
            }
        }

        async function openFakeWeaponModal() {
            if (!activeComboId) return;
            openModal(fakeWeaponModal, closeFakeWeaponModal);
            renderFakeWeaponGrid();

            const weaponType = getActiveWeaponType(currentSelectedCharId);
            const relevantIds = Object.keys(allWeaponList)
                .filter(id => !weaponType || allWeaponList[id].weaponType === weaponType);
            const missingIds = relevantIds.filter(id => weaponIconCache[id] === undefined);

            if (missingIds.length) {
                fakeWeaponGridStatus.textContent = GenshinI18n.t('アイコンを読み込み中...');
                await ensureWeaponIconsLoaded(relevantIds);
                fakeWeaponGridStatus.textContent = '';
                renderFakeWeaponGrid();
            }
        }

        function closeFakeWeaponModal() {
            closeModal(fakeWeaponModal);
        }

        function renderFakeWeaponGrid() {
            fakeWeaponGrid.innerHTML = '';
            fakeWeaponGridStatus.textContent = '';
            const weaponType = getActiveWeaponType(currentSelectedCharId);
            const selection = getActiveSelection();

            const noneTile = document.createElement('div');
            noneTile.className = `picker-tile relative w-16 h-16 shrink-0 cursor-pointer rounded-lg border-2 border-zinc-700 bg-zinc-800 flex items-center justify-center ${!selection.fakeWeapon ? 'selected' : ''}`;
            noneTile.title = GenshinI18n.t('なし（実データ）');
            noneTile.innerHTML = `<span class="text-2xl text-zinc-500">✕</span>`;
            noneTile.onclick = () => selectFakeWeapon('');
            fakeWeaponGrid.appendChild(noneTile);

            const sortedIds = Object.keys(allWeaponList)
                .filter(id => !weaponType || allWeaponList[id].weaponType === weaponType)
                .filter(id => !isSwapHiddenWeapon(id))
                .sort((a, b) => Number(a) - Number(b));

            if (weaponType && sortedIds.length === 0) {
                fakeWeaponGridStatus.textContent = GenshinI18n.t('この武器種の候補が見つかりませんでした。');
            }

            sortedIds.forEach(id => {
                const data = allWeaponList[id];
                const iconUrl = weaponAssetUrl(id);
                const rarity = qualityToRarity(data.qualityType);
                const tile = document.createElement('div');
                tile.className = `picker-tile relative w-16 h-16 shrink-0 cursor-pointer rounded-lg border-2 rarity-${rarity} bg-zinc-800 overflow-hidden ${selection.fakeWeapon === id ? 'selected' : ''}`;
                tile.title = listNameOf(data);
                tile.onclick = () => selectFakeWeapon(id);

                if (iconUrl) {
                    tile.innerHTML = `
                        <img src="${iconUrl}" class="w-full h-full object-cover drag-none" loading="lazy">
                        <div class="char-name-label absolute bottom-0 inset-x-0 bg-black/70 text-[10px] leading-tight text-center truncate px-0.5 py-0.5">${esc(listNameOf(data))}</div>
                    `;
                } else {
                    tile.innerHTML = `<div class="w-full h-full flex items-center justify-center text-[10px] text-zinc-500 text-center px-1">${esc(listNameOf(data))}</div>`;
                }
                if (isBetaOnlyWeapon(id)) {
                    tile.insertAdjacentHTML('beforeend', betaNewBadgeHTML('md'));
                }
                fakeWeaponGrid.appendChild(tile);
            });
        }

        function selectFakeWeapon(weaponId) {
            const combo = getActiveCombo();
            if (!combo) return;
            combo.fakeWeapon = weaponId;
            saveComboState(currentSelectedCharId);
            renderComboRow();
            updateEditWeaponUI();
            closeFakeWeaponModal();
            if (teamMode) {
                if (typeof renderTeamCharRow === 'function') renderTeamCharRow();
                if (typeof updateTeamMemberLabel === 'function') updateTeamMemberLabel(currentSelectedCharId);
            } else {
                refreshCurrentCard(currentSelectedCharId, false, true);
            }
        }

        function applyRealWeaponToEditUI() {
            if (lastKnownRealWeapon.name) {
                editWeaponLabel.textContent = lastKnownRealWeapon.name;
            } else {
                editWeaponLabel.textContent = '—';
            }
            if (lastKnownRealWeapon.icon) {
                editWeaponIcon.src = lastKnownRealWeapon.icon;
                editWeaponIcon.classList.remove('hidden');
            } else {
                editWeaponIcon.classList.add('hidden');
            }
        }

        function setLastKnownRealWeapon(data) {
            if (!data) return;
            // 差し替え武器使用中は card_data の武器が差し替え先になるため、
            // 「実データ武器」キャッシュを上書きしない（デフォルトに戻した時にバグる）
            const selection = getActiveSelection();
            if (selection.fakeWeapon) return;
            let icon = data.weaponIcon || '';
            if (icon && !icon.startsWith('http') && !icon.startsWith('/')) {
                icon = '/' + icon;
            }
            lastKnownRealWeapon = {
                name: data.weaponName || '',
                icon: icon
            };
            updateEditWeaponUI();
        }

        function updateEditWeaponUI() {
            const isDefault = !activeComboId;
            editWeaponBtn.disabled = isDefault;
            editWeaponBtn.classList.toggle('opacity-50', isDefault);
            editWeaponBtn.classList.toggle('cursor-not-allowed', isDefault);
            editWeaponBtn.classList.toggle('hover:border-cyan-400', !isDefault);

            // デフォルト枠、または差し替え枠で武器未指定 → 実データ武器を表示
            const selection = getActiveSelection();
            if (isDefault || !selection.fakeWeapon) {
                applyRealWeaponToEditUI();
                return;
            }

            if (selection.fakeWeapon && allWeaponList[selection.fakeWeapon]) {
                const data = allWeaponList[selection.fakeWeapon];
                const iconUrl = weaponAssetUrl(selection.fakeWeapon);
                editWeaponLabel.textContent = listNameOf(data);
                if (iconUrl) {
                    editWeaponIcon.src = iconUrl;
                    editWeaponIcon.classList.remove('hidden');
                } else {
                    editWeaponIcon.classList.add('hidden');
                }
            } else {
                applyRealWeaponToEditUI();
            }
        }

        editWeaponBtn.addEventListener('click', openFakeWeaponModal);

        // artifacter.html の検索フォームから来た直後の初回表示だけ、
        // 画像生成を必ず forceFetch(=キャッシュ無視で再生成) させるためのフラグ
        let pendingForceFetchFromArtifacter = false;

        window.addEventListener('DOMContentLoaded', async () => {
            // 共有閲覧モード: topbar/ガラスカードを再利用した専用表示
            if (SHARE_PAYLOAD) {
                await enterShareMode();
                return;
            }
            // 初回のみバージョン選択（未選択ならモーダルを表示）
            maybeShowVersionChooser();
            initEnkaCooldown();

            // キャラ毎のデフォルト計算方式（admin 設定）を先に読み込む
            await loadCalcMethodDefaults();

            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('from_artifacter')) {
                const newUrl = window.location.protocol + "//" + window.location.host + window.location.pathname + `?uid=${uid}`;
                window.history.replaceState({ path: newUrl }, '', newUrl);

                // artifacter.html から来た場合は、古いビルドカード画像キャッシュを破棄し、
                // 次に画像を表示する際は必ず作り直した画像が使われるようにする
                pendingForceFetchFromArtifacter = true;
                await clearCardImageCache();
            }

            loadFakeLists();

            // 編成モードが保存されていたら復元（単体カードの自動選択は行わない）
            if (typeof tryImportAbyssShare === 'function' && tryImportAbyssShare()) {
                ensureViewTheme(() => renderAbyssView());
                return;
            }
            if (typeof importSharedTeamsFromUrl === 'function') importSharedTeamsFromUrl();
            if (typeof restoreTeamMode === 'function' && restoreTeamMode()) {
                return;
            }

            const savedCharId = localStorage.getItem(`selected_char_${uid}`);
            let targetThumb = savedCharId
                ? document.querySelector(`.char-thumb[data-char-id="${savedCharId}"]`)
                : null;
            if (!targetThumb) {
                targetThumb = document.querySelector('.char-thumb');
            }
            if (targetThumb) targetThumb.click();
        });

        // キャラ毎のデフォルト計算方式（admin 設定）。未選択時のフォールバックに使う。
        let calcMethodDefaults = {};
        async function loadCalcMethodDefaults() {
            try {
                const res = await fetch('/api/calc_method_defaults', { cache: 'no-store' });
                const data = await res.json();
                if (data && data.ok && data.defaults) calcMethodDefaults = data.defaults;
            } catch (e) { /* 取得失敗時は従来通り crit にフォールバック */ }
        }

        function getSavedCalcMethod(charId) {
            const base = String(charId).split('-')[0];
            return localStorage.getItem(`calc_method_${uid}_${charId}`) || calcMethodDefaults[base] || "crit";
        }
        function saveCalcMethod(charId, method) {
            localStorage.setItem(`calc_method_${uid}_${charId}`, method);
        }

        let currentImageObjectUrl = null;
        let currentLoadedImageUrl = null;   // 今 cardImage に表示している画像のURL
        let latestImageRequestToken = 0;
        let imageGenActiveCount = 0;        // 進行中の画像生成の数

        // 生成中は右上の「再取得 / 再生成」ボタンを押せなくする
        function setTopImageButtonsDisabled(disabled) {
            [regenerateApiBtn, regenerateImageBtn].forEach(btn => {
                if (!btn) return;
                btn.disabled = disabled;
                btn.classList.toggle('opacity-60', disabled);
                btn.classList.toggle('cursor-not-allowed', disabled);
            });
        }

        // 画像生成中は左側のコントロール（計算方法・武器・背景・差し替え）も無効化する
        function setGenerationControlsDisabled(disabled) {
            setTopImageButtonsDisabled(disabled);
            const els = [scoreCalcSelect, editWeaponBtn, bgColorModeSelect, bgColorPicker, bgRegionSelect];
            els.forEach(el => {
                if (!el) return;
                el.disabled = disabled;
                el.classList.toggle('opacity-60', disabled);
            });
            if (comboRow) {
                comboRow.querySelectorAll('button, input, select').forEach(el => {
                    el.disabled = disabled;
                    el.classList.toggle('opacity-60', disabled);
                });
            }
            if (!disabled) {
                // 本来の状態を復元（背景のカスタム色はモード次第、武器は差し替え状態次第）
                if (typeof applyBgColorControlsUI === 'function') applyBgColorControlsUI();
                if (typeof updateEditWeaponUI === 'function') updateEditWeaponUI();
                if (typeof renderComboRow === 'function') renderComboRow();
            }
        }

        // 画像が未生成（またはパラメータ変更で古い）状態 → 中央に生成ボタンを出す。
        // 進行中の生成リクエストは古くなっているのでまとめて無効化する。
        function updateImageGenButtonLabel(hasImage) {
            if (!regenerateImageBtn) return;
            // ボタン内の span（data-i18n）を書き換える。textContent 直指定は
            // 言語切替の辞書適用を壊すため、ここでも GenshinI18n.t を通す
            regenerateImageBtn.innerHTML = `<span data-i18n>${GenshinI18n.t(hasImage ? '再生成' : '生成')}</span>`;
        }

        function showImageAwaitState(message) {
            latestImageRequestToken++;
            cardImage.classList.add('hidden');
            htmlCardContainer.classList.add('hidden');
            const glassEl = document.getElementById('glassCardContainer');
            if (glassEl) glassEl.classList.add('hidden');
            placeholderText.classList.add('hidden');
            if (imageAwaitText) {
                imageAwaitText.textContent = message ? GenshinI18n.t(message) : GenshinI18n.t('右上の「生成」ボタンからビルドカード画像を作成できます');
            }
            if (imageAwaitBox) {
                imageAwaitBox.classList.remove('hidden');
                imageAwaitBox.classList.add('flex');
            }
            disclaimerFooter.classList.add('hidden');
            updateImageGenButtonLabel(false);
        }

        function hideImageAwaitState() {
            if (imageAwaitBox) {
                imageAwaitBox.classList.add('hidden');
                imageAwaitBox.classList.remove('flex');
            }
        }

        /** タブ切替時に画像生成UIの残骸を確実に消す */
        function clearImageTabArtifacts() {
            hideImageAwaitState();
            if (cardImage) cardImage.classList.add('hidden');
            // スケルトンが残っている場合もクリア
            if (htmlCardContainer && viewMode !== 'html') {
                // html タブ以外ではスケルトン表示を残さない
            }
        }

        // ==========================================================
        //  ビルドカード画像のクライアント側キャッシュ（Cache Storage API）
        //  サーバー側には保存しない。1度生成した画像は「再生成」（該当URL削除）
        //  または「再取得」/artifacterからの遷移（全削除）まで保持され、
        //  過去の画像を再表示するときにサーバー再生成なしで再利用される。
        // ==========================================================
        const IMAGE_CACHE_NAME = 'genshin-build-card-image-cache-v1';
        const cacheStorageSupported = (typeof caches !== 'undefined');

        async function getCachedCardImage(url) {
            if (!cacheStorageSupported) return null;
            try {
                const cache = await caches.open(IMAGE_CACHE_NAME);
                const cachedRes = await cache.match(url);
                return cachedRes ? await cachedRes.blob() : null;
            } catch (e) {
                console.warn('画像キャッシュの参照に失敗しました:', e);
                return null;
            }
        }

        async function putCachedCardImage(url, response) {
            if (!cacheStorageSupported) return;
            try {
                const cache = await caches.open(IMAGE_CACHE_NAME);
                await cache.put(url, response);
            } catch (e) {
                console.warn('画像キャッシュの保存に失敗しました:', e);
            }
        }

        async function clearCardImageCache() {
            if (!cacheStorageSupported) return;
            try {
                await caches.delete(IMAGE_CACHE_NAME);
            } catch (e) {
                console.warn('画像キャッシュのクリアに失敗しました:', e);
            }
        }

        /** 過去に1枚でも画像を生成・キャッシュしたことがあるか（初回判定用） */
        async function hasAnyCachedCardImage() {
            if (!cacheStorageSupported) return false;
            try {
                const cache = await caches.open(IMAGE_CACHE_NAME);
                const keys = await cache.keys();
                return keys.length > 0;
            } catch (e) {
                return false;
            }
        }

        async function deleteCachedCardImage(url) {
            if (!cacheStorageSupported) return;
            try {
                const cache = await caches.open(IMAGE_CACHE_NAME);
                await cache.delete(url);
            } catch (e) {
                console.warn('画像キャッシュの削除に失敗しました:', e);
            }
        }

        // ==========================================================
        //  スケルトン・カード
        //  「ビルドカード(HTML)」「画像生成」どちらの読み込み中も、
        //  完成後のレイアウトに近い形のプレースホルダーを表示することで
        //  生成待ちのUXを改善する
        // ==========================================================
        function skeletonBlock(extraClass = '') {
            return `<div class="skeleton ${extraClass}"></div>`;
        }

        function skeletonArtifactTile() {
            return `
                <div class="bg-zinc-900/60 rounded-lg p-3 border border-zinc-800">
                    <div class="flex items-center gap-2 mb-2">
                        ${skeletonBlock('skeleton-avatar w-10 h-10 shrink-0')}
                        <div class="flex-1 min-w-0 space-y-1.5">
                            ${skeletonBlock('h-3 w-2/3')}
                            ${skeletonBlock('h-3 w-1/2')}
                        </div>
                        ${skeletonBlock('h-6 w-8 shrink-0')}
                    </div>
                    <div class="space-y-1.5 pt-1.5 border-t border-zinc-800">
                        ${skeletonBlock('h-3 w-full')}
                        ${skeletonBlock('h-3 w-full')}
                        ${skeletonBlock('h-3 w-4/5')}
                        ${skeletonBlock('h-3 w-4/5')}
                    </div>
                </div>`;
        }

        function renderCardSkeletonHtml() {
            const mainStatsSkeleton = Array.from({ length: 4 })
                .map(() => `
                    <div class="flex items-center gap-2 bg-zinc-900/60 rounded-lg px-3 py-2 border border-zinc-800">
                        ${skeletonBlock('w-6 h-6 shrink-0')}
                        ${skeletonBlock('h-3 flex-1')}
                    </div>`)
                .join('');

            const artifactsSkeleton = Array.from({ length: 5 }).map(skeletonArtifactTile).join('');

            return `
                <div class="grid md:grid-cols-3 gap-4">
                    <div class="md:col-span-1 space-y-3">
                        <div class="flex items-center gap-3 bg-zinc-900/60 rounded-lg p-3 border border-zinc-800">
                            <div class="flex-1 space-y-2">
                                ${skeletonBlock('h-4 w-2/3')}
                                ${skeletonBlock('h-3 w-1/2')}
                            </div>
                            ${skeletonBlock('skeleton-avatar w-12 h-12 shrink-0')}
                        </div>
                        <div class="grid grid-cols-2 gap-1.5">${mainStatsSkeleton}</div>
                        <div class="bg-zinc-900/60 rounded-lg p-3 border border-zinc-800 space-y-2">
                            ${skeletonBlock('h-3 w-1/3')}
                            ${skeletonBlock('h-3 w-2/3')}
                        </div>
                        <div class="flex items-center justify-between bg-zinc-900/60 rounded-lg p-3 border border-zinc-800">
                            ${skeletonBlock('h-3 w-1/3')}
                            ${skeletonBlock('h-6 w-16')}
                        </div>
                    </div>
                    <div class="md:col-span-2 grid grid-cols-1 sm:grid-cols-2 gap-3">
                        ${artifactsSkeleton}
                    </div>
                </div>`;
        }

        // --- 画像生成の背景（元素デフォルト / 地域背景 / カスタム色） ---
        const BG_COLOR_MODE_KEY = 'genshin_build_card_bg_mode';
        const BG_COLOR_CUSTOM_KEY = 'genshin_build_card_bg_custom';
        const BG_REGION_MAP_KEY = 'genshin_build_card_bg_region_map';
        const bgColorModeSelect = document.getElementById('bgColorModeSelect');
        const bgColorPicker = document.getElementById('bgColorPicker');
        const bgRegionControls = document.getElementById('bgRegionControls');
        const bgRegionSelect = document.getElementById('bgRegionSelect');
        const bgRegionAutoLabel = document.getElementById('bgRegionAutoLabel');
        const bgRegionNote = document.getElementById('bgRegionNote');
        const bgColorControls = document.getElementById('bgColorControls');

        function isGlassThemeActive() {
            try {
                const mode = (typeof getSavedViewMode === 'function') ? getSavedViewMode() : 'glass';
                if (mode === 'image') return getSavedImageTheme() === 'glass';
                if (mode === 'glass') return getSavedCardTheme() === 'glass';
                return false;
            } catch (e) {
                return true;
            }
        }

        function applyBgControlsVisibility() {
            if (!bgColorControls) return;
            // 共有閲覧モードでは背景設定を常に出さない(テーマ切替で再表示されるのを防ぐ)
            if (typeof SHARE_PAYLOAD !== 'undefined' && SHARE_PAYLOAD) { bgColorControls.classList.add('hidden'); return; }
            bgColorControls.classList.toggle('hidden', !isGlassThemeActive());
        }

        const BG_REGION_LABELS = {
            mondstadt: 'モンド', liyue: '璃月', inazuma: '稲妻', sumeru: 'スメール',
            fontaine: 'フォテーヌ', natlan: 'ナタ', nodkrai: 'ノッドクライ',
            snezhnaya: 'スネージナヤ', dragonspine: 'ドラゴンスパイネ'
        };
        // 地域表示ラベル: static/data/setting/region_labels.json（ja/en 両方。admin で編集可）を読み、
        // 無いキーは従来の BG_REGION_LABELS（ja のみ）→ キー文字列の順にフォールバック
        let REGION_LABELS = {};
        (async () => {
            try {
                const res = await fetch('/static/data/setting/region_labels.json');
                if (res.ok) {
                    const d = await res.json();
                    if (d && typeof d === 'object') REGION_LABELS = d;
                }
            } catch (e) { /* 読めなければハードコードのラベルへフォールバック */ }
            applyBgColorControlsUI();
        })();
        function bgRegionLabel(name) {
            const entry = REGION_LABELS[name];
            if (entry && typeof entry === 'object') {
                const v = GenshinI18n.isEn() ? (entry.en || entry.ja) : (entry.ja || entry.en);
                if (v) return v;
            }
            return BG_REGION_LABELS[name] || name;
        }

        // 現在表示中キャラの所属地域（card_data の regions: [{name, image|null}]）
        let currentCharRegions = [];
        let currentRegionsCharKey = null;

        function getBgColorMode() {
            try {
                const m = localStorage.getItem(BG_COLOR_MODE_KEY);
                if (m === 'custom' || m === 'region') return m;
            } catch (e) {}
            // 未設定時のデフォルトは地域背景（キャラの所属地域画像。無ければ元素背景にフォールバック）
            return 'region';
        }
        function getCustomBgColor() {
            try {
                const c = localStorage.getItem(BG_COLOR_CUSTOM_KEY);
                if (c && /^#[0-9a-fA-F]{6}$/.test(c)) return c;
            } catch (e) {}
            return '#4a5568';
        }
        function getBgRegionMap() {
            try {
                const raw = localStorage.getItem(BG_REGION_MAP_KEY);
                const m = raw ? JSON.parse(raw) : {};
                return (m && typeof m === 'object' && !Array.isArray(m)) ? m : {};
            } catch (e) { return {}; }
        }
        /** 地域選択の保存キー（差し替えキャラ込みでキャラ単位） */
        function bgRegionStorageKey() {
            const base = currentSelectedCharId != null ? String(currentSelectedCharId) : '';
            if (!base) return '';
            try {
                const sel = getActiveSelection();
                return sel.fakeChar ? `${base}:${sel.fakeChar}` : base;
            } catch (e) { return base; }
        }
        function getSavedBgRegion() {
            const key = bgRegionStorageKey();
            if (!key) return null;
            return getBgRegionMap()[key] || null;
        }
        function saveBgRegion(region) {
            const key = bgRegionStorageKey();
            if (!key) return;
            const m = getBgRegionMap();
            m[key] = region;
            try { localStorage.setItem(BG_REGION_MAP_KEY, JSON.stringify(m)); } catch (e) {}
        }
        /** 背景画像が実際に存在する地域のみ */
        function usableCharRegions(regions) {
            return (regions || []).filter(r => r && r.name && r.image);
        }
        function setCurrentCharRegions(regions) {
            currentCharRegions = Array.isArray(regions) ? regions : [];
            currentRegionsCharKey = bgRegionStorageKey();
            applyBgColorControlsUI();
        }
        /** 現在有効な選択地域（保存値が無効なら先頭にフォールバック） */
        function getSelectedBgRegion() {
            if (getBgColorMode() !== 'region') return null;
            const usable = usableCharRegions(currentCharRegions);
            if (!usable.length) return null;
            const saved = getSavedBgRegion();
            return usable.some(r => r.name === saved) ? saved : usable[0].name;
        }
        function applyBgColorControlsUI() {
            applyBgControlsVisibility();
            if (!bgColorModeSelect || !bgColorPicker) return;
            const mode = getBgColorMode();
            bgColorModeSelect.value = mode;
            bgColorPicker.value = getCustomBgColor();
            bgColorPicker.disabled = mode !== 'custom';
            if (!bgRegionControls) return;
            if (mode !== 'region') {
                bgRegionControls.classList.add('hidden');
                return;
            }
            bgRegionControls.classList.remove('hidden');
            const usable = usableCharRegions(currentCharRegions);
            if (!usable.length) {
                if (bgRegionSelect) bgRegionSelect.classList.add('hidden');
                if (bgRegionAutoLabel) bgRegionAutoLabel.classList.add('hidden');
                if (bgRegionNote) {
                    bgRegionNote.classList.remove('hidden');
                    bgRegionNote.textContent = currentCharRegions.length
                        ? '所属地域に背景画像がありません → 元素背景になります'
                        : '地域未所属 → 元素背景になります';
                }
                return;
            }
            if (bgRegionNote) bgRegionNote.classList.add('hidden');
            const saved = getSavedBgRegion();
            const current = usable.some(r => r.name === saved) ? saved : usable[0].name;
            if (usable.length === 1) {
                // 所属地域が1つのみ → 自動選択（セレクトは出さずラベル表示）
                if (bgRegionSelect) bgRegionSelect.classList.add('hidden');
                if (bgRegionAutoLabel) {
                    bgRegionAutoLabel.classList.remove('hidden');
                    bgRegionAutoLabel.textContent = `${GenshinI18n.t('自動選択')}: ${bgRegionLabel(current)}`;
                }
            } else {
                if (bgRegionAutoLabel) bgRegionAutoLabel.classList.add('hidden');
                if (bgRegionSelect) {
                    bgRegionSelect.classList.remove('hidden');
                    bgRegionSelect.innerHTML = usable.map(r =>
                        `<option value="${esc(r.name)}"${r.name === current ? ' selected' : ''}>${esc(bgRegionLabel(r.name))}</option>`
                    ).join('');
                }
            }
        }
        function getBgColorParam() {
            if (getBgColorMode() !== 'custom') return null;
            return getCustomBgColor().replace('#', '');
        }
        // 画像表示モードでは card_data を取らないため、地域モード時は個別に取得して所属地域を把握する
        async function ensureCharRegionsLoaded(charId) {
            if (!charId || getBgColorMode() !== 'region') return;
            const key = bgRegionStorageKey();
            if (!key || currentRegionsCharKey === key) return;
            try {
                const sel = getActiveSelection();
                const p = new URLSearchParams();
                p.set('calc_method', getSavedCalcMethod(charId));
                if (sel.fakeChar) p.set('fake_char', sel.fakeChar);
                if (sel.fakeWeapon) p.set('fake_weapon', sel.fakeWeapon);
                if (ver === 'beta') p.set('beta', 'true');
                const res = await fetch(`/api/card_data/${uid}/${charId}?${withLang(p)}`);
                if (!res.ok) return;
                const data = await res.json();
                // 取得中にキャラや差し替えが変わっていたら適用しない
                const selNow = getActiveSelection();
                if (currentSelectedCharId === charId &&
                    selNow.fakeChar === sel.fakeChar &&
                    selNow.fakeWeapon === sel.fakeWeapon) {
                    setCurrentCharRegions(data.regions || []);
                }
            } catch (e) { /* ignore */ }
        }
        function onBgSettingChanged() {
            applyBgColorControlsUI();
            // 背景は URL クエリに含まれるためキャッシュキーが分かれる。
            // 背景変更時は即時画像生成（キャッシュが無ければその場で生成）
            if (!currentSelectedCharId) return;
            // 編成モードでは単体カードをリフレッシュしない（設定は保存のみ）
            if (teamMode) return;
            refreshCurrentCard(currentSelectedCharId, false, false, true);
        }
        if (bgColorModeSelect) {
            applyBgColorControlsUI();
            bgColorModeSelect.addEventListener('change', () => {
                try { localStorage.setItem(BG_COLOR_MODE_KEY, bgColorModeSelect.value); } catch (e) {}
                onBgSettingChanged();
            });
        }
        if (bgColorPicker) {
            bgColorPicker.addEventListener('change', () => {
                try { localStorage.setItem(BG_COLOR_CUSTOM_KEY, bgColorPicker.value); } catch (e) {}
                onBgSettingChanged();
            });
        }
        if (bgRegionSelect) {
            bgRegionSelect.addEventListener('change', () => {
                saveBgRegion(bgRegionSelect.value);
                onBgSettingChanged();
            });
        }

        function buildCardImageUrl(charId) {
            const currentMethod = getSavedCalcMethod(charId);
            const selection = getActiveSelection();
            const params = new URLSearchParams();
            if (selection.fakeChar) params.set('fake_char', selection.fakeChar);
            if (selection.fakeWeapon) params.set('fake_weapon', selection.fakeWeapon);
            if (ver === 'beta') params.set('beta', 'true');
            if (document.documentElement.getAttribute('data-theme') === 'light') params.set('light', 'true');
            const imageTheme = getSavedImageTheme();
            if (imageTheme === 'glass') {
                const bgParam = getBgColorParam();
                if (bgParam) {
                    params.set('bg_color', bgParam);
                } else {
                    const bgMode = getBgColorMode();
                    params.set('bg_mode', bgMode);
                    if (bgMode === 'region') {
                        const bgRegion = getSelectedBgRegion();
                        if (bgRegion) params.set('bg_region', bgRegion);
                    }
                }
            }
            params.set('img_format', getSavedFormatPref());
            if (getSavedGrowthPref()) params.set('growth', 'true');
            params.set('base_prec', getSavedBasePrec());
            params.set('substat_dots', getSavedSubstatDotsPref() ? '1' : '0');
            if (getSavedShowUidPref()) params.set('show_uid', 'true');
            const resonanceParam = getResonanceParam();
            if (resonanceParam) params.set('resonance', resonanceParam);
            if (imageTheme === 'cinema' || imageTheme === 'scorecard') params.set('theme', imageTheme);
            // 英語モード時はカード画像内のテキストも英語で生成する
            if (GenshinI18n.isEn()) params.set('lang', 'en');
            const query = params.toString() ? `?${params.toString()}` : '';
            return `/generate_card_image/${uid}/${charId}/${currentMethod}${query}`;
        }

        // ==========================================================
        //  署名付きカード画像URL（エンドポイント秘匿化 / DDoS対策）
        //  /generate_card_image を直接叩かれてもサーバーがHMAC署名を
        //  検証するため、先に /api/card_sign で署名付きURLを取得する。
        //  有効期限内は同じURLを再利用（ブラウザキャッシュのキーと
        //  表示中URLの比較が壊れないようにする）
        // ==========================================================
        const signedCardUrlCache = new Map(); // baseUrl -> {url, exp}

        async function getSignedCardUrl(baseUrl) {
            const cached = signedCardUrlCache.get(baseUrl);
            if (cached && cached.exp * 1000 > Date.now() + 60000) return cached.url;
            const [path, query = ''] = baseUrl.split('?');
            // /generate_card_image/{uid}/{avatar_id}/{calc_method}（先頭スラッシュを除去）
            const seg = path.split('/').filter(Boolean);
            const p = new URLSearchParams({ uid: seg[1], avatar_id: seg[2], calc_method: seg[3] });
            for (const [k, v] of new URLSearchParams(query)) p.set(k, v);
            const res = await fetch(`/api/card_sign?${p.toString()}`, { cache: 'no-store' });
            if (!res.ok) throw new Error(`署名の取得に失敗しました (status: ${res.status})`);
            const data = await res.json();
            // lang は署名対象外のため、署名済みURLに直接付与する（cache=server と同じ扱い）
            let signedUrl = data.url;
            if (GenshinI18n.isEn()) signedUrl += (signedUrl.includes('?') ? '&' : '?') + 'lang=en';
            signedCardUrlCache.set(baseUrl, { url: signedUrl, exp: data.exp });
            return signedUrl;
        }

        async function loadCardImage(charId, forceFetch = false, cacheOnly = false, autoGenOnMiss = false) {
            if(!charId) return;

            const currentMethod = getSavedCalcMethod(charId);
            scoreCalcSelect.value = currentMethod;

            hideImageAwaitState();
            placeholderText.classList.add('hidden');
            cardImage.classList.add('hidden');
            const glassElImg = document.getElementById('glassCardContainer');
            if (glassElImg) glassElImg.classList.add('hidden');
            htmlCardContainer.classList.remove('hidden');
            htmlCardContainer.innerHTML = `
                <div class="flex flex-col items-center justify-center gap-3 py-20">
                    <div class="spinner-primary w-10 h-10"></div>
                    <div class="text-sm text-zinc-500">${GenshinI18n.t('画像を生成中...')} <span id="genProgressText" class="text-zinc-400"></span></div>
                </div>`;

            updateDisclaimerFooter(getActiveSelection());
            startGenProgress();
            const baseUrl = buildCardImageUrl(charId);

            const requestToken = ++latestImageRequestToken;

            // 生成中は右上ボタン＋左側コントロールを無効化（終了時に必ず戻す）
            imageGenActiveCount++;
            setGenerationControlsDisabled(true);

            try {
                // DDoS対策: 署名付きURLを取得してから画像を取得する
                const saveMode = getSavedSaveMode();
                let url = await getSignedCardUrl(baseUrl);
                const fetchUrl = saveMode === 'server' ? (url + (url.includes('?') ? '&' : '?') + 'cache=server') : url;
                let blob = null;
                if (saveMode === 'client') blob = forceFetch ? null : await getCachedCardImage(url);

                if (!blob && cacheOnly) {
                    // 差し替え切替時は、過去に1枚でも生成実績があれば自動生成する。
                    // 1枚もキャッシュが無い初回のみ、中央の生成ボタンを出して待つ
                    if (!(autoGenOnMiss && await hasAnyCachedCardImage())) {
                        if (requestToken !== latestImageRequestToken) return;
                        htmlCardContainer.classList.add('hidden');
                        showImageAwaitState();
                        return;
                    }
                }

                if (!blob) {
                    let res = await fetch(fetchUrl, { cache: forceFetch ? 'reload' : 'default' });
                    if (res.status === 403) {
                        // 署名期限切れ・サーバー再起動などで署名が無効化された場合は
                        // 署名を取り直して1回だけ再試行する
                        signedCardUrlCache.delete(baseUrl);
                        url = await getSignedCardUrl(baseUrl);
                        const retryUrl = saveMode === 'server' ? (url + (url.includes('?') ? '&' : '?') + 'cache=server') : url;
                        res = await fetch(retryUrl, { cache: forceFetch ? 'reload' : 'default' });
                    }
                    if (!res.ok) throw new Error(`画像の取得に失敗しました (status: ${res.status})`);
                    if (saveMode === 'client') putCachedCardImage(url, res.clone());
                    blob = await res.blob();
                }

                if (requestToken !== latestImageRequestToken) return;

                if (currentImageObjectUrl) {
                    URL.revokeObjectURL(currentImageObjectUrl);
                }
                currentImageObjectUrl = URL.createObjectURL(blob);
                window.__lastCardBlob = blob; // シェア機能用に最新のカード画像 blob を保持
                currentLoadedImageUrl = url;
                cardImage.src = currentImageObjectUrl;
                updateImageGenButtonLabel(true);
            } catch (e) {
                if (requestToken !== latestImageRequestToken) return;
                console.error('カード画像の読み込みに失敗しました:', e);
                htmlCardContainer.classList.add('hidden');
                cardImage.classList.add('hidden');
                currentLoadedImageUrl = null;
                // 503（生成プール満杯）は混雑メッセージを出す（右上の「生成」ボタンで再試行できる）
                const isBusy = /status:\s*503/.test(String(e && e.message));
                if (isBusy) {
                    showImageAwaitState('カード生成が混雑しています。しばらく待ってから「生成」を押してください。');
                } else {
                    showImageAwaitState('画像の読み込みに失敗しました。もう一度お試しください。');
                }
            } finally {
                imageGenActiveCount = Math.max(0, imageGenActiveCount - 1);
                if (imageGenActiveCount === 0) setGenerationControlsDisabled(false);
            }
        }

        cardImage.onload = function() {
            // 生成完了前にタブを切り替えられていた場合、
            // 他タブの表示を壊して画像を出さないようにする
            if (viewMode !== 'image') return;
            htmlCardContainer.classList.add('hidden');
            cardImage.classList.remove('hidden');
            hideImageAwaitState();
            updateImageGenButtonLabel(true);
        };

        // ==========================================================
        //  表示モード切替（ステータス / ビルドカード / 画像生成）
        //  選択中タブは localStorage に保存し、リロード後も維持する。
        //  ただし「ビルドカード生成」(image) はリロード時に復元せず
        //  「ステータス」(html) に戻す（ページ開いた瞬間に高コストな
        //  画像生成が走るのを防ぐため）。artifacter の検索から直接
        //  遷移してきた場合 (from_artifacter) のみそのまま維持する。
        // ==========================================================
        const VIEW_MODE_PREF_KEY = 'genshin_build_card_view_mode';
        const VALID_VIEW_MODES = ['html', 'glass', 'image'];

        function getSavedViewMode() {
            try {
                const v = localStorage.getItem(VIEW_MODE_PREF_KEY);
                if (VALID_VIEW_MODES.includes(v)) return v;
            } catch (e) { /* ignore */ }
            // デフォルトはビルドカード（glass）。ステータス表示は設定から ON にできる
            return 'glass';
        }

        function saveViewMode(mode) {
            try {
                localStorage.setItem(VIEW_MODE_PREF_KEY, mode);
            } catch (e) { /* ignore */ }
        }

        let viewMode = getSavedViewMode();
        // ステータス表示トグルが admin 設定で無効な場合、html モードは復元しない
        const STATUS_VIEW_SETTING_ENABLED = 0;
        if (!STATUS_VIEW_SETTING_ENABLED && viewMode === 'html') {
            viewMode = 'glass';
        }
        // 以前はリロード時に image タブを html へ強制リセットしていたが、
        // loadCardImage が「キャッシュなしなら生成ボタン待ち」で自動生成しないよう
        // になったため、表示タブは保存値のまま復元してよい。
        const viewModeButtons = document.querySelectorAll('.view-mode-btn');

        function applyViewModeUI() {
            viewModeButtons.forEach(btn => {
                btn.classList.toggle('active', btn.dataset.view === viewMode);
            });
        }
        // 初期表示でもページテーマを反映（viewMode 初期化直後なので安全）
        applyPageThemeUi();
        applyBgControlsVisibility();

        async function refreshCurrentCard(charId, forceFetch = false, autoGenOnMiss = false, immediateGen = false) {
            if (!charId) return;

            const charChanged = currentSelectedCharId !== charId;
            currentSelectedCharId = charId;
            if (charChanged) {
                // キャラ切替時は所属地域を白紙化（次回取得まで注記表示）
                currentCharRegions = [];
                currentRegionsCharKey = null;
                applyBgColorControlsUI();
            }

            if (listsLoaded && charChanged) {
                loadComboState(charId);
                const c1 = sanitizeCombosForVer();
                const c2 = sanitizeHiddenCombos();
                if (c1 || c2) saveComboState(charId);
                renderComboRow();
                updateEditWeaponUI();
                ensureComboIconsLoaded().then(() => { renderComboRow(); updateEditWeaponUI(); });
            }

            if (viewMode === 'image') {
                // 地域背景モードでは、URL を組む前に所属地域を確定させる。
                // 差し替え切替直後は直前キャラ/差し替えの地域が残っており、
                // 誤った bg_region で画像が生成されるのを防ぐ
                if (getBgColorMode() === 'region') {
                    await ensureCharRegionsLoaded(charId);
                    // 待機中に別キャラへ切り替わっていたら中断
                    if (currentSelectedCharId !== charId) return;
                }
                if (forceFetch) {
                    loadCardImage(charId, true);
                } else {
                    // タブ切替やパラメータ変更ではサーバーでの自動生成をしない。
                    // 最新の画像が表示中ならそのまま、過去に生成したキャッシュがあれば
                    // それから復元し、どちらもなければ中央の生成ボタンを出す
                    // （計算方法/背景変更は immediateGen で即時生成）
                    // DDoS対策: 表示中・キャッシュ比較用のURLも署名付きで統一する
                    let url = null;
                    try {
                        url = await getSignedCardUrl(buildCardImageUrl(charId));
                    } catch (e) {
                        console.warn('署名付きURLの取得に失敗しました:', e);
                    }
                    if (url && currentImageObjectUrl && currentLoadedImageUrl === url) {
                        scoreCalcSelect.value = getSavedCalcMethod(charId);
                        hideImageAwaitState();
                        const glassElImg2 = document.getElementById('glassCardContainer');
                        if (glassElImg2) glassElImg2.classList.add('hidden');
                        htmlCardContainer.classList.add('hidden');
                        placeholderText.classList.add('hidden');
                        cardImage.classList.remove('hidden');
                        updateDisclaimerFooter(getActiveSelection());
                        updateImageGenButtonLabel(true);
                    } else if (immediateGen) {
                        // 計算方法/背景変更・キャラ切替時は即時画像生成（キャッシュが無ければその場で生成）
                        loadCardImage(charId, false, false);
                    } else {
                        // キャッシュが無ければ「自動生成するか否か」は呼び出し元の判断に委ねる
                        // （差し替え切替 = 自動生成、初回選択 = 生成ボタン待ち）
                        loadCardImage(charId, false, true, autoGenOnMiss);
                    }
                }
            } else if (viewMode === 'glass') {
                hideImageAwaitState();
                cardImage.classList.add('hidden');
                htmlCardContainer.classList.add('hidden');
                placeholderText.classList.add('hidden');
                loadGlassCard(charId);
            } else {
                hideImageAwaitState();
                cardImage.classList.add('hidden');
                const glassEl = document.getElementById('glassCardContainer');
                if (glassEl) glassEl.classList.add('hidden');
                placeholderText.classList.add('hidden');
                loadHtmlCard(charId);
            }
            applyPageThemeUi();
        }

        /** 表示モードを切り替える。編成モード中は「画像生成」(image) のみ有効 */
        function setViewMode(mode) {
            if (!VALID_VIEW_MODES.includes(mode)) return;
            if (teamMode && mode !== 'image') return;
            if (mode === viewMode) return;
            viewMode = mode;
            saveViewMode(viewMode);
            applyViewModeUI();
            // 切替前にタブで進行中だった生成リクエストをすべて無効化し、
            // 完了コールバックが切替後のタブの表示を壊さないようにする
            latestImageRequestToken++;
            latestGlassRequestToken++;
            latestHtmlRequestToken++;
            refreshCurrentCard(currentSelectedCharId, false);
        }

        viewModeButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                // 編成モード中は「画像生成」のみ有効
                if (teamMode && btn.dataset.view !== 'image') return;
                setViewMode(btn.dataset.view);
            });
        });

        applyViewModeUI();

        // ==========================================================
        //  グラスモーフィズム・ビルドカード（Bデザイン移植）
        // ==========================================================
        let latestGlassRequestToken = 0;
        const ELEMENT_COLORS = {
            Pyro: '#ff6b4a', Hydro: '#4aa4ff', Anemo: '#74e0c5', Electro: '#c97bff',
            Dendro: '#a0e05a', Cryo: '#9ad4ff', Geo: '#f0c65a', None: '#aaaaaa'
        };
        // サーバー側 element_colors（server.py）と同じRGB。生成画像の元素背景と見た目を揃える
        const ELEMENT_BG_COLORS = {
            Pyro: '#903b2a', Hydro: '#344595', Cryo: '#577fc7', Dendro: '#466b63',
            Geo: '#6a6748', Electro: '#734a8c', Anemo: '#129588', None: '#4a5568'
        };

        // グラスカード下層の背景（element=元素グラデ / region=地域画像 / custom=カスタム色）
        function glassBaseBgStyle(data) {
            const mode = getBgColorMode();
            if (mode === 'custom') {
                return `background:${getCustomBgColor()};`;
            }
            if (mode === 'region') {
                const usable = usableCharRegions(data.regions);
                const saved = getSavedBgRegion();
                const picked = usable.find(r => r.name === saved) || usable[0];
                if (picked) {
                    return `background-image:url('${cssUrlSanitize(assetUrl(picked.image))}');background-size:cover;background-position:center;`;
                }
            }
            const c = ELEMENT_BG_COLORS[data.element] || ELEMENT_BG_COLORS.None;
            return `background:linear-gradient(150deg, ${c}40 0%, ${c}14 40%, rgba(12,14,20,0.95) 100%);`;
        }

        async function loadGlassCard(charId) {
            if (!charId) return;
            const glassEl = document.getElementById('glassCardContainer');
            if (!glassEl) return;
            glassEl.classList.remove('hidden');
            htmlCardContainer.classList.add('hidden');
            cardImage.classList.add('hidden');
            glassEl.innerHTML = '<div class="text-zinc-500 text-sm py-16 text-center">読み込み中...</div>';

            const currentMethod = getSavedCalcMethod(charId);
            scoreCalcSelect.value = currentMethod;

            const selection = getActiveSelection();
            updateDisclaimerFooter(selection);
            const params = new URLSearchParams();
            params.set('calc_method', currentMethod);
            if (selection.fakeChar) params.set('fake_char', selection.fakeChar);
            if (selection.fakeWeapon) params.set('fake_weapon', selection.fakeWeapon);
            if (ver === 'beta') params.set('beta', 'true');
            if (getSavedGrowthPref()) params.set('growth', 'true');
            params.set('base_prec', getSavedBasePrec());
            const resonanceParam = getResonanceParam();
            if (resonanceParam) params.set('resonance', resonanceParam);

            const requestToken = ++latestGlassRequestToken;
            try {
                // 先読み済みなら即座に描画（パラメータが完全一致した場合のみ）
                const pKey = withLang(params).toString();
                if (cardDataPrefetch.has(pKey)) {
                    const pre = cardDataPrefetch.get(pKey);
                    cardDataPrefetch.delete(pKey);
                    if (requestToken === latestGlassRequestToken) { renderGlassCard(pre); recordScoreHistory(pre); }
                    return;
                }
                const res = await fetch(`/api/card_data/${uid}/${charId}?${pKey}`);
                if (!res.ok) {
                    const errBody = await res.json().catch(() => ({}));
                    throw new Error(errBody.detail || `${GenshinI18n.t('データの取得に失敗しました')} (status: ${res.status})`);
                }
                const data = await res.json();
                cardDataPrefetch.set(pKey, data);
                if (requestToken !== latestGlassRequestToken) return;
                renderGlassCard(data);
                recordScoreHistory(data);
            } catch (e) {
                if (requestToken !== latestGlassRequestToken) return;
                console.error('グラスカードの読み込みに失敗しました:', e);
                glassEl.innerHTML = `<div class="text-red-400 text-sm py-16 text-center">読み込みに失敗しました: ${esc(e.message)}</div>`;
                disclaimerFooter.classList.add('hidden');
            }
        }

        function assetUrl(path) {
            if (!path) return '';
            if (path.startsWith('http') || path.startsWith('/')) return path;
            return '/' + path;
        }

        // 育成モード右パネルの HTML 本文（glass / HTML カード共通）
        function roundTo2(n) {
            const v = Number(n);
            return Number.isFinite(v) ? (Math.round(v * 100) / 100).toFixed(2) : '';
        }
        // 実数値の表示用フォーマット。標準(prec='0')は小数第1位で四捨五入、
        // 詳細(第2位) は小数第2位まで、prec='4' は小数第4位まで表示。
        function formatDecPrec(n, prec) {
            const v = Number(n);
            if (!Number.isFinite(v)) return '';
            const dp = String(prec || '0') === '4' ? 4 : (String(prec || '0') === '2' ? 2 : 1);
            const f = Math.pow(10, dp);
            const r = Math.round((v + Number.EPSILON) * f) / f;
            return r.toLocaleString('en-US', { maximumFractionDigits: dp, useGrouping: true });
        }
        function growthPanelHtml(growth) {
            if (!growth) return '';
            const g = growth;
            const blocks = [];

            // セット効果
            if (g.sets && g.sets.length) {
                const setRows = g.sets.map(s => {
                    const count = Number(s.count) || 2;
                    const desc2 = s.set2 ? `<div class="gp-set-desc${s.buff_applied ? ' applied' : ''}">${GenshinI18n.t('2セット:')} ${esc(s.set2)}</div>` : '';
                    // 4セット効果は4点以上装備時のみ表示（2セット/2セット編成では非表示）
                    const desc4 = (count >= 4 && s.set4) ? `<div class="gp-set-desc">${GenshinI18n.t('4セット:')} ${esc(s.set4)}</div>` : '';
                    return `<div class="gp-set-row">
                        <div class="gp-set-name">${esc(s.name)} ×${esc(s.count)}</div>
                        ${desc2}
                        ${desc4}
                    </div>`;
                }).join('');
                blocks.push(`<div class="growth-panel-section"><div class="growth-panel-head">${GenshinI18n.t('聖遺物セット効果')}</div>${setRows}</div>`);
            }

            // 基礎ステータス → %換算（1%あたり）
            const sp = g.stat1pct;
            if (sp) {
                blocks.push(`<div class="growth-panel-section">
                    <div class="growth-panel-head">${GenshinI18n.t('基礎ステータス %換算')}</div>
                    <div class="gp-stat-rows">
                        <div class="gp-stat-row"><span class="gp-stat-label">${esc(sp.label)} ${GenshinI18n.t('1%あたり')}</span><span class="gp-stat-value">${esc(sp.value)}</span></div>
                    </div></div>`);
            }

            // サブステ伸び平均（1回あたり）: HP / 攻撃 / 防御 を % と実数で表示
            const subList = g.subavg_all || (g.subavg ? [g.subavg] : []);
            if (subList.length) {
                const rows = [];
                subList.forEach(s => {
                    if (s.pct_avg != null || s.flat_avg != null) {
                        const short = (GenshinI18n.isEn() ? { hp: 'HP', atk: 'ATK', def: 'DEF' } : { hp: 'HP', atk: '攻撃', def: '防御' })[s.key] || s.label || '';
                        const label = esc(short);
                        if (s.pct_avg != null) {
                            const paren = (s.flat_equiv != null) ? ` (${formatDecPrec(s.flat_equiv, getSavedBasePrec())})` : '';
                            rows.push(`<div class="gp-stat-row"><span class="gp-stat-label">${label}%</span><span class="gp-stat-value">${roundTo2(s.pct_avg)}%${paren}</span></div>`);
                        }
                        if (s.flat_avg != null) {
                            rows.push(`<div class="gp-stat-row"><span class="gp-stat-label">${label}${GenshinI18n.t('実数')}</span><span class="gp-stat-value">${formatDecPrec(s.flat_avg, getSavedBasePrec())}</span></div>`);
                        }
                    }
                });
                if (rows.length) blocks.push(`<div class="growth-panel-section">
                    <div class="growth-panel-head">${GenshinI18n.t('サブステ伸び平均')}</div>
                    <div class="gp-subhead">${GenshinI18n.t('1回あたりの平均')}</div>
                    <div class="gp-stat-rows">${rows.join('')}</div></div>`);
            }

            if (!blocks.length) return '';
            return `<div class="growth-panel-title">育成メモ</div>${''}
                <div class="growth-panel-body">${blocks.join('<div style="height:1px;background:rgba(255,255,255,0.08);margin:2px 0 6px;"></div>')}</div>`;
        }

        // ==========================================================
        //  カードテーマ共通ヘルパー（cinema / scorecard 提案デザイン移植）
        // ==========================================================
        const ELEMENT_JA_NAMES = { Pyro: '炎', Hydro: '水', Anemo: '風', Electro: '雷', Dendro: '草', Cryo: '氷', Geo: '岩', None: '無' };

        function themeDotsHtml(rolls, prefix) {
            if (!getSavedSubstatDotsPref()) return '';
            const arr = rolls || [];
            if (!arr.length) return '';
            return '<span class="' + prefix + '-dots">' + arr.map(t =>
                '<i class="' + prefix + '-dot ' + prefix + '-dot-' + Math.min(4, Math.max(1, Number(t) || 1)) + '"></i>'
            ).join('') + '</span>';
        }

        function themeConstellationUnlocked(data) {
            return Number.isFinite(data.constellation) ? data.constellation : 0;
        }

        // ==========================================================
        //  カードテーマ: CINEMA（ui1_cinema.html）
        // ==========================================================
        function renderCinemaThemeHtml(data, ctx) {
            const elemJa = ELEMENT_JA_NAMES[data.element] || '無';
            const elemIcon = assetUrl('static/assets/props/' + String(data.element || 'none').toLowerCase() + '.png');
            const rarity = Number(data.rarity) === 4 ? 4 : 5;
            const stars = Array.from({ length: rarity }).map(() =>
                '<svg viewBox="0 0 24 24"><path d="M12 2l2.9 6.6 7.1.7-5.4 4.8 1.6 7-6.2-3.7-6.2 3.7 1.6-7L2 9.3l7.1-.7z"/></svg>').join('');
            const constUnlocked = themeConstellationUnlocked(data);
            const constLabel = constUnlocked >= 6 ? '完凸' : 'C' + constUnlocked;

            const dispName = String(data.displayName || '');
            const nameHtml = dispName.endsWith('(swap)')
                ? esc(dispName.slice(0, -'(swap)'.length)) + '<span class="cine-swap">(swap)</span>'
                : esc(dispName);

            const talents = (data.skills || []).map(s =>
                '<div class="cine-bubble">' +
                    (s.icon ? '<img src="' + assetUrl(s.icon) + '" alt="">' : '') +
                    '<span class="cine-lvb num' + (s.boosted ? ' boosted' : '') + '">' + (s.level != null ? esc(s.level) : '1') + '</span>' +
                '</div>').join('');

            const constsIcons = data.constellationIcons || [];
            const consts = Array.from({ length: 6 }).map((_, i) => {
                const on = i < constUnlocked;
                const icon = constsIcons[i] || '';
                return '<div class="cine-const' + (on ? '' : ' off') + '">' + (icon ? '<img src="' + assetUrl(icon) + '" alt="">' : '') + '</div>';
            }).join('');
            const weaponSubParts = (data.weaponStats || []).map(w => esc(w.name) + ' ' + esc(w.value));
            const weaponSub = weaponSubParts.length ? weaponSubParts[0] + (weaponSubParts.length > 1 ? '<br>' + weaponSubParts.slice(1).join(' ・ ') : '') : '';

            const stats = (data.mainStats || []).map(s => {
                const hl = (s.label === '会心率' || s.label === '会心ダメージ') ? ' hl' : '';
                return '<div class="cine-stat' + hl + '"><span class="cine-k">' +
                    (s.icon ? '<img src="' + assetUrl(s.icon) + '" alt="">' : '') + esc(s.label) +
                    '</span><span class="cine-v num">' + esc(s.val) + '</span></div>';
            }).join('');

            const setChips = (data.setBonuses || []).map(s =>
                '<span class="cine-set"><span class="cine-set-cnt num">×' + esc(s.count) + '</span><span class="cine-set-name">' + esc(s.name) + '</span></span>').join('');
            // 元素共鳴チップ（手動選択・ステータス反映中）
            const resoChips = (data.resonanceBadges || []).map(b =>
                '<span class="cine-set"><img src="' + assetUrl('static/assets/props/' + String(b.elem || '').toLowerCase() + '.png') + '" alt="" style="width:14px;height:14px;object-fit:contain;"><span class="cine-set-name">' + esc(b.text) + '</span><span class="cine-set-cnt num">' + esc(b.label) + '</span></span>').join('');

            const arts = (data.artifacts || []).map(a => {
                if (!a) return `<div class="cine-art"><div class="cine-art-empty">${GenshinI18n.t('未装備')}</div></div>`;
                const subs = (a.substats || []).map(sub =>
                    '<div class="cine-sub"><span class="cine-nm">' + esc(sub.name) + '</span>' + themeDotsHtml(sub.rolls, 'cine') + '<span class="cine-vl num">' + esc(sub.value) + '</span></div>').join('');
                return '<div class="cine-art">' +
                    '<div class="cine-art-top">' +
                        '<div class="cine-aicon">' + (a.icon ? '<img src="' + assetUrl(a.icon) + '" alt="">' : '') + '</div>' +
                        '<div class="cine-amain"><div class="cine-aslot">' + esc(a.slot || '') + '</div><div class="cine-amname">' + (a.main ? esc(a.main.name) + ' ' + esc(a.main.value) : '') + '</div></div>' +
                        '<div class="cine-atier"><img src="/static/assets/tiers/' + esc(a.tier || 'B') + '.png" alt="' + esc(a.tier || 'B') + '"></div>' +
                    '</div>' +
                    '<div class="cine-subs">' + subs + '</div>' +
                    '<div class="cine-afoot"><span class="cine-fs">SCORE</span><span class="cine-fv num">' + (Number(a.score) || 0).toFixed(1) + '</span></div>' +
                '</div>';
            }).join('');
            return `
            <div class="cine-card">
              <div class="cine-bg" style="background-image:url('${ctx.splash}')"></div>
              <div class="cine-tint"></div>
              <div class="cine-scrim"></div>
              <div class="cine-head">
                <div class="cine-elem-line">
                  <span class="cine-elem-chip"><img src="${elemIcon}" alt="">${esc(elemJa)}</span>
                  <span class="cine-stars">${stars}</span>
                </div>
                <h1 class="cine-name">${nameHtml}</h1>
                <div class="cine-meta">
                  <span class="cine-lv num">Lv.${data.level != null ? esc(data.level) : '?'}</span>
                  ${data.friendship != null ? '<span class="cine-sep"></span><span class="cine-friend"><svg viewBox="0 0 24 24"><path d="M12 21s-7.5-4.7-10-9.3C.4 8.6 2.3 5 5.7 5c2 0 3.4 1.1 4.3 2.4h4c.9-1.3 2.3-2.4 4.3-2.4 3.4 0 5.3 3.6 3.7 6.7C19.5 16.3 12 21 12 21z"/></svg>' + esc(data.friendship) + '</span>' : ''}
                  <span class="cine-sep"></span>
                  <span>${esc(constLabel)}</span>
                </div>
              </div>
              <div class="cine-rail">
                <div class="cine-rail-group">
                  <div class="cine-rail-label">Talents</div>
                  <div class="cine-bubbles">${talents}</div>
                </div>
                ${constsIcons.length ? '<div class="cine-rail-group"><div class="cine-rail-label">Constellation</div><div class="cine-const-row">' + consts + '</div></div>' : ''}
              </div>
              <div class="cine-right">
                <div class="cine-panel cine-weapon">
                  <div class="cine-wimg">${data.weaponIcon ? '<img src="' + assetUrl(data.weaponIcon) + '" alt="">' : ''}</div>
                  <div class="cine-wbody">
                    <div class="cine-wname">${esc(data.weaponName || '')}</div>
                    ${weaponSub ? '<div class="cine-wsub num">' + weaponSub + '</div>' : ''}
                  </div>
                  <div class="cine-wr num">Lv.${data.weaponLevel != null ? esc(data.weaponLevel) : '?'}<b>R${data.weaponAffix != null ? esc(data.weaponAffix) : '1'}</b></div>
                </div>
                <div class="cine-panel cine-score">
                  <div class="cine-tier"><img src="/static/assets/tiers/${esc(data.tierSum || 'B')}.png" alt="${esc(data.tierSum || 'B')}"></div>
                  <div>
                    <div class="cine-sl">Total Score</div>
                    <div class="cine-sv num">${(Number(data.scoreSum) || 0).toFixed(1)}</div>
                    <div class="cine-sm">${esc(data.calcMethodLabel || data.calcMethod || '')}</div>
                  </div>
                </div>
                ${setChips ? '<div class="cine-setchip">' + setChips + '</div>' : ''}
              </div>
              <div class="cine-stats">${stats}</div>
              <div class="cine-arts">${arts}</div>
              ${resoChips ? '<div class="cine-reso">' + resoChips + '</div>' : ''}
              ${getSavedShowUidPref() ? '<div class="cine-uid num">UID ' + esc(uid) + '</div>' : ''}
            </div>`;
        }
        // ==========================================================
        //  カードテーマ: SCORECARD（ui2v2_scorecard.html）
        // ==========================================================
        function themeSplashBgPosCss(data) {
            // admin 設定のスプラッシュオフセット（-100〜100%, 0=中央）を
            // background-position の % に変換する。未設定なら ''（CSS既定の center 18%）。
            const off = data && data.splashOffset;
            if (!off) return '';
            const x = Number(off.x);
            const y = Number(off.y);
            if (!Number.isFinite(x) && !Number.isFinite(y)) return '';
            const clamp = v => Math.max(-100, Math.min(100, Number.isFinite(v) ? v : 0));
            const px = 50 + 50 * (clamp(x) / 100);
            const py = 50 + 50 * (clamp(y) / 100);
            return `background-position:${Math.round(px * 10) / 10}% ${Math.round(py * 10) / 10}%;`;
        }

        function renderScorecardThemeHtml(data, ctx) {
            const elemJa = ELEMENT_JA_NAMES[data.element] || '無';
            const elemIcon = assetUrl('static/assets/props/' + String(data.element || 'none').toLowerCase() + '.png');
            const constUnlocked = themeConstellationUnlocked(data);
            const constLabel = constUnlocked >= 6 ? '完凸' : 'C' + constUnlocked;
            const score = Number(data.scoreSum) || 0;
            const critValue = Number(data.critValue);
            const critValueStr = Number.isFinite(critValue) ? critValue.toFixed(1) : '—';
            const CIRC = 408; // 2πr（r=65）
            const frac = Math.max(0, Math.min(1, score / 250));
            const dashOffset = Math.round(CIRC * (1 - frac));
            const critRateStat = (data.mainStats || []).find(s => s.label === '会心率');
            const critDmgStat = (data.mainStats || []).find(s => s.label === '会心ダメージ');
            const cvPct = Number.isFinite(critValue) ? Math.max(0, Math.min(100, (critValue / 300) * 100)) : 0;

            const chips = [
                '<span class="scc-chip"><img src="' + elemIcon + '" alt="">' + esc(elemJa) + '元素</span>',
                '<span class="scc-chip">Lv.<b class="num">' + (data.level != null ? esc(data.level) : '?') + '</b></span>',
                (data.friendship != null ? '<span class="scc-chip">好感度 <b class="num">' + esc(data.friendship) + '</b></span>' : ''),
                '<span class="scc-chip">' + (constUnlocked >= 6 ? '完凸' : '命星座') + ' <b class="num">C' + constUnlocked + '</b></span>',
                (data.weaponType ? '<span class="scc-chip">' + esc(data.weaponType) + '</span>' : ''),
            ].filter(Boolean).join('');
            // 元素共鳴チップ（手動選択・ステータス反映中）: 聖遺物行とカード下端の隙間に表示
            const resoChips = (data.resonanceBadges || []).map(b =>
                '<span class="scc-chip"><img src="' + assetUrl('static/assets/props/' + String(b.elem || '').toLowerCase() + '.png') + '" alt="">' + esc(b.text) + ' <b class="num">' + esc(b.label) + '</b></span>'
            ).join('');

            const statRows = (data.mainStats || []).map(s => {
                const hl = (s.label === '会心率' || s.label === '会心ダメージ') ? ' scc-hl' : '';
                return '<div class="scc-srow' + hl + '"><span class="scc-k">' +
                    (s.icon ? '<img src="' + assetUrl(s.icon) + '" alt="">' : '') + esc(s.label) +
                    '</span><span class="scc-v num">' + esc(s.val) + '</span></div>';
            }).join('');

            const weaponSubParts = (data.weaponStats || []).map(w => esc(w.name) + ' ' + esc(w.value));
            const weaponSub = weaponSubParts.length ? weaponSubParts[0] + (weaponSubParts.length > 1 ? '<br>' + weaponSubParts.slice(1).join(' ・ ') : '') : '';

            const setRows = (data.setBonuses && data.setBonuses.length)
                ? data.setBonuses.map(s => '<div class="scc-setrow"><span class="scc-setcnt num">×' + esc(s.count) + '</span><span class="scc-setname">' + esc(s.name) + '</span>' + (s.buff ? '<span class="scc-setbuff">' + esc(s.buff) + '</span>' : '') + '</div>').join('')
                : '<div class="scc-setrow scc-off"><span class="scc-setname">セット効果なし</span></div>';

            const talents = (data.skills || []).map(s =>
                '<div class="scc-bubble">' +
                    (s.icon ? '<img src="' + assetUrl(s.icon) + '" alt="">' : '') +
                    '<span class="scc-lvb num' + (s.boosted ? ' boosted' : '') + '">' + (s.level != null ? esc(s.level) : '1') + '</span>' +
                '</div>').join('');

            const constsIcons = data.constellationIcons || [];
            const consts = Array.from({ length: 6 }).map((_, i) => {
                const on = i < constUnlocked;
                const icon = constsIcons[i] || '';
                return '<div class="scc-const' + (on ? '' : ' off') + '">' + (icon ? '<img src="' + assetUrl(icon) + '" alt="">' : '') + '</div>';
            }).join('');
            // 聖遺物: スコアが最も高いものをベストとしてハイライト
            const arts = data.artifacts || [];
            let bestIdx = -1, bestScore = -1;
            arts.forEach((a, i) => {
                const sc = a ? (Number(a.score) || 0) : -1;
                if (sc > bestScore) { bestScore = sc; bestIdx = i; }
            });

            const artsHtml = arts.map((a, i) => {
                if (!a) return `<div class="scc-art"><div class="scc-art-empty">${GenshinI18n.t('未装備')}</div></div>`;
                const subs = (a.substats || []).map(sub =>
                    '<div class="scc-sub"><span class="scc-nm">' + esc(sub.name) + '</span>' + themeDotsHtml(sub.rolls, 'scc') + '<span class="scc-vl num">' + esc(sub.value) + '</span></div>').join('');
                return '<div class="scc-art' + (i === bestIdx ? ' best' : '') + '">' +
                    '<div class="scc-atop">' +
                        '<div class="scc-aicon">' + (a.icon ? '<img src="' + assetUrl(a.icon) + '" alt="">' : '') + '</div>' +
                        '<div><div class="scc-aslot">' + esc(a.slot || '') + '</div><div class="scc-amain">' + (a.main ? esc(a.main.name) + ' ' + esc(a.main.value) : '') + '</div></div>' +
                        '<div class="scc-atier"><img src="/static/assets/tiers/' + esc(a.tier || 'B') + '.png" alt="' + esc(a.tier || 'B') + '"></div>' +
                    '</div>' +
                    '<div class="scc-asubs">' + subs + '</div>' +
                    '<div class="scc-afoot"><span class="scc-l">SCORE</span><span class="scc-s num">' + (Number(a.score) || 0).toFixed(1) + '</span></div>' +
                '</div>';
            }).join('');
            return `
            <div class="scc-card">
              <div class="scc-banner">
                <div class="scc-bbg" style="background-image:url('${ctx.splash}');${themeSplashBgPosCss(data)}"></div>
                <div class="scc-grid-lines"></div>
                <div class="scc-scrim"></div>
                <svg width="0" height="0"><defs><linearGradient id="sccGaugeGrad" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stop-color="${ctx.elemColor}"/><stop offset="100%" stop-color="#ffcd78"/></linearGradient></defs></svg>
                <div class="scc-bwrap">
                  <div class="scc-cicon">${data.charIcon ? '<img src="' + assetUrl(data.charIcon) + '" alt="">' : ''}</div>
                  <div class="scc-bid">
                    <div class="scc-bname">${esc(data.displayName)}</div>
                    <div class="scc-chips">${chips}</div>
                  </div>
                  <div class="scc-gauge">
                    <svg viewBox="0 0 150 150"><circle class="scc-track" cx="75" cy="75" r="65"/><circle class="scc-arc" cx="75" cy="75" r="65" style="stroke-dashoffset:${dashOffset}"/></svg>
                    <div class="scc-core">
                      <div class="scc-tier"><img src="/static/assets/tiers/${esc(data.tierSum || 'B')}.png" alt="${esc(data.tierSum || 'B')}"></div>
                      <div class="scc-sv num">${score.toFixed(1)}</div>
                      <div class="scc-sl">Total Score</div>
                    </div>
                  </div>
                </div>
              </div>
              <div class="scc-cvbar">
                <span class="scc-cvlbl">Crit Value</span>
                <span class="scc-cvval num">${critValueStr}</span>
                <div class="scc-cvtrack"><i style="width:${cvPct}%"></i></div>
                <span class="scc-cvnote">会心率 <b class="num">${critRateStat ? esc(critRateStat.val) : '—'}</b> ／ 会心ダメ <b class="num">${critDmgStat ? esc(critDmgStat.val) : '—'}</b></span>
              </div>
              <div class="scc-mid">
                <div class="scc-tile scc-tile-status">
                  <div class="scc-tlabel">Status</div>
                  <div class="scc-sgrid">${statRows}</div>
                </div>
                <div class="scc-tile">
                  <div class="scc-tlabel">Weapon / Set</div>
                  <div class="scc-wrow">
                    <div class="scc-wimg">${data.weaponIcon ? '<img src="' + assetUrl(data.weaponIcon) + '" alt="">' : ''}</div>
                    <div><div class="scc-wname">${esc(data.weaponName || '')}</div>${weaponSub ? '<div class="scc-wsub num">' + weaponSub + '</div>' : ''}</div>
                    <div class="scc-wr"><div class="scc-wlv num">Lv.${data.weaponLevel != null ? esc(data.weaponLevel) : '?'}</div><div class="scc-wrf num">R${data.weaponAffix != null ? esc(data.weaponAffix) : '1'}</div></div>
                  </div>
                  ${setRows}
                </div>
                <div class="scc-tile scc-tile-tc">
                  <div class="scc-tlabel">Talents</div>
                  <div class="scc-bubbles">${talents}</div>
                  ${constsIcons.length ? '<div class="scc-tlabel scc-clabel"><span>Constellation</span><span class="scc-cnum num">' + constLabel + '</span></div><div class="scc-consts">' + consts + '</div>' : ''}
                </div>
              </div>
              <div class="scc-arts">${artsHtml}</div>
              ${resoChips ? '<div class="scc-reso">' + resoChips + '</div>' : ''}
              ${getSavedShowUidPref() ? '<div class="scc-uid num">UID ' + esc(uid) + '</div>' : ''}
            </div>`;
        }

        function renderGlassCard(data) {
            const glassEl = document.getElementById('glassCardContainer');
            if (!glassEl) return;
            updateSelectedCharLabel(data);
            setLastKnownRealWeapon(data);
            setCurrentCharRegions(data.regions || []);

            const baseBgStyle = glassBaseBgStyle(data);
            const elemColor = ELEMENT_COLORS[data.element] || '#aaaaaa';
            const elemDim = elemColor + '55';
            const splash = cssUrlSanitize(assetUrl(data.splash));
            const splashAttr = esc(assetUrl(data.splash));
            const skills = data.skills || [];
            const constIcons = data.constellationIcons || [];
            const unlocked = Number.isFinite(data.constellation) ? data.constellation : 0;

            const talentsHtml = skills.map(s => `
                <div class="talent-bubble">
                    ${s.icon ? `<img src="${assetUrl(s.icon)}" alt="">` : ''}
                    <span class="talent-level-badge${s.boosted ? ' talent-boosted' : ''}">Lv.${s.level ?? 1}</span>
                </div>
            `).join('');

            const constsHtml = Array.from({ length: 6 }).map((_, i) => {
                const icon = constIcons[i] || '';
                const isUnlocked = i < unlocked;
                const unlockedCls = isUnlocked ? 'unlocked' : 'locked';
                const lockMark = isUnlocked ? '' : '<div class="const-locked-overlay"><svg viewBox="0 0 24 24"><path d="M12,17A2,2 0 0,0 14,15C14,13.89 13.1,13 12,13A2,2 0 0,0 10,15A2,2 0 0,0 12,17M18,8A2,2 0 0,1 20,10V20A2,2 0 0,1 18,22H6A2,2 0 0,1 4,20V10C4,8.89 4.9,8 6,8H7V7A5,5 0 0,1 12,2A5,5 0 0,1 17,7V8H18M12,4A3,3 0 0,0 9,7V8H15V7A3,3 0 0,0 12,4Z"/></svg></div>';
                return `<div class="const-bubble ${unlockedCls}">${icon ? `<img src="${assetUrl(icon)}" alt="">` : ''}${lockMark}</div>`;
            }).join('');

            const statsHtml = (data.mainStats || []).map(s => {
                const base = s.base != null ? `<div class="stat-breakdown"><span class="stat-base">${esc(s.base)}</span>${s.add ? `<span class="stat-plus">${esc(s.add)}</span>` : (s.val != null && s.base != null ? '' : '')}</div>` : '';
                // base/add may not exist for all; show if available
                let breakdown = '';
                if (s.base != null) {
                    const addVal = (typeof s.val === 'number' && typeof s.base === 'number')
                        ? (s.val - s.base)
                        : null;
                    breakdown = `<div class="stat-breakdown">
                        <span class="stat-base">${esc(s.base)}</span>
                        ${addVal != null ? `<span class="stat-plus">+${esc(addVal)}</span>` : ''}
                    </div>`;
                }
                return `
                <div class="stat-row">
                    <div class="stat-icon">${s.icon ? `<img src="${assetUrl(s.icon)}" alt="">` : ''}</div>
                    <div class="stat-content">
                        <div class="stat-label-wrap">
                            <span class="stat-name">${esc(s.label)}</span>
                            <span class="stat-total">${esc(s.val)}</span>
                        </div>
                        ${breakdown}
                    </div>
                </div>`;
            }).join('');

            const weaponStatsHtml = (data.weaponStats || []).map(w =>
                `<div class="w-stat"><span>${esc(w.name)}</span> <span>${esc(w.value)}</span></div>`
            ).join('');

            const setHtml = (data.setBonuses && data.setBonuses.length)
                ? data.setBonuses.map(s => `<div class="set-row"><span class="set-name">${esc(s.name)}</span>${s.buff ? `<span class="set-buff">${esc(s.buff)}</span>` : ''}<span class="set-count">${esc(s.count)}</span></div>`).join('')
                : `<div class="set-none">セット効果なし</div>`;

            // 元素共鳴（手動選択・ステータス反映中）: カード右下隅に表示
            const resonanceHtml = (data.resonanceBadges && data.resonanceBadges.length)
                ? data.resonanceBadges.map(b => `<div class="set-row"><img src="${assetUrl('static/assets/props/' + String(b.elem || '').toLowerCase() + '.png')}" alt="" style="width:12px;height:12px;object-fit:contain;"><span class="set-name">${esc(b.text)}</span><span class="set-buff">${esc(b.label)}</span></div>`).join('')
                : '';

            // UID表示（表示方法トグル。共鳴チップと同じ高さの左下）
            const uidHtml = getSavedShowUidPref() ? `<div class="uid-corner">UID ${esc(uid)}</div>` : '';

            const arts = data.artifacts || [];
            const artsHtml = arts.map(a => {
                if (!a) {
                    return `<div class="art-card"><div style="margin:auto;opacity:0.3">${GenshinI18n.t('未装備')}</div></div>`;
                }
                const subs = (a.substats || []).map(sub => {
                    let dots = '';
                    if (getSavedSubstatDotsPref() && (sub.rolls || []).length) {
                        const rolls = sub.rolls || [];
                        dots = rolls.map((t, ti) =>
                            `<span class="substat-dot substat-dot-${t + 1}"></span>`
                        ).join('');
                    }
                    return `<div class="substat-item"><div class="substat-name-col"><span class="substat-name">${esc(sub.name)}</span>${dots ? `<span class="substat-dots">${dots}</span>` : ''}</div><span class="substat-val">${esc(sub.value)}</span></div>`;
                }).join('');
                const hasDots = subs.includes('substat-dots');
                return `
                <div class="art-card">
                    <div class="art-header">
                        ${a.icon ? `<img class="art-icon" src="${assetUrl(a.icon)}" alt="">` : '<div class="art-icon"></div>'}
                        <div class="art-main-info">
                            <div class="art-main-label">${a.main ? esc(a.main.name) : ''}</div>
                            <div class="art-main-val">${a.main ? esc(a.main.value) : ''}</div>
                        </div>
                        <div class="art-level">+${a.upgrade ?? 0}</div>
                    </div>
                    <div class="art-substats${hasDots ? ' has-dots' : ''}">${subs}</div>
                    <div class="art-footer">
                        <div class="art-rank"><img src="/static/assets/tiers/${a.tier || 'B'}.png" alt="${a.tier || 'B'}"></div>
                        <div class="art-score"><div class="score-Label">score</div>${(Number(a.score) || 0).toFixed(1)}</div>
                    </div>
                </div>`;
            }).join('');

            // 育成（growth）レイアウト: ON かつ growth データがあれば右側にパネルを追加し、
            // カード（build-card / cine-card / scc-card）を全幅基準に縮小してスペースを作る
            let growthData = null, growthBody = '', growthK = 1, growthLeftW = 1200, growthLeftH = 800, growthPanelW = 0;
            const growthMode = getSavedGrowthPref();
            const cardTheme = getSavedCardTheme();
            // テーマごとのカード高さ（glass=800 / cinema=780 / scorecard=707）
            const themeCardH = (cardTheme === 'cinema') ? 780 : (cardTheme === 'scorecard') ? 707 : 800;
            if (growthMode && data) {
                growthData = data.growth || data.progress || null;
                if (growthData) {
                    growthBody = growthPanelHtml(growthData);
                    if (growthBody) {
                        // 総合スコア列（right-section = 270px / 1200）と同じ横幅にする
                        growthK = 0.775;
                        growthLeftW = Math.round(1200 * growthK);
                        growthLeftH = Math.round(themeCardH * growthK);
                        growthPanelW = 270;
                    } else {
                        growthData = null;
                    }
                }
            }

            const themeCtx = { elemColor, elemDim, splash, splashAttr };
            if (cardTheme === 'cinema' || cardTheme === 'scorecard') {
                const themeHtml = (cardTheme === 'cinema')
                    ? renderCinemaThemeHtml(data, themeCtx)
                    : renderScorecardThemeHtml(data, themeCtx);
                glassEl.innerHTML = `
                <div class="glass-card-root theme-${cardTheme}" style="--element-color:${elemColor};--element-color-dim:${elemDim}">
                  <div class="glass-scale-wrap" id="glassScaleWrap"${growthData && growthBody ? ` data-growth="1"` : ''}>
              ${growthData && growthBody ? `<div class="glass-growth-layout">
              <div class="glass-growth-left" style="width:${growthLeftW}px;height:${growthLeftH}px;--gc-growth-k:${growthK}">` : ''}
                    ${themeHtml}
              ${growthData && growthBody ? `</div>
              <div class="growth-panel" style="width:${growthPanelW}px;height:${growthLeftH}px;--element-color:${elemColor}">${growthBody}</div>
              </div>` : ''}
                  </div>
                </div>`;
            } else {
                glassEl.innerHTML = `
                <div class="glass-card-root${getSavedBasePrec() === '2' ? ' glass-prec-detail' : ''}" style="--element-color:${elemColor};--element-color-dim:${elemDim}">
                  <div class="glass-scale-wrap" id="glassScaleWrap"${growthData && growthBody ? ` data-growth="1"` : ''}>
              ${growthData && growthBody ? `<div class="glass-growth-layout">
              <div class="glass-growth-left" style="width:${growthLeftW}px;height:${growthLeftH}px;--gc-growth-k:${growthK}">` : ''}
              <div class="build-card"${growthData && growthBody ? ` style="transform:scale(${growthK});"` : ''}>
                <div class="glass-card-base-bg" style="${baseBgStyle}"></div>
                <div class="glass-card-bg" style="background-image:url('${splash || ''}')"></div>
                <div class="top-section">
                  <div class="splash-container">
                    ${splash ? `<img class="splash-art" src="${splashAttr}" alt="">` : ''}
                    <div class="char-overlay-info">
                      <div class="char-name">${esc(data.displayName)}</div>
                      <div class="char-meta">
                        <span>Lv. ${data.level != null ? esc(data.level) : '?'}</span>
                        ${data.friendship != null ? `<div class="friendship-pill"><span>❤</span><span>${esc(data.friendship)}</span></div>` : ''}
                      </div>
                    </div>
                    <div class="talents-stack">${talentsHtml}</div>
                    ${(data.constellationIcons && data.constellationIcons.length) ? `<div class="const-stack">${constsHtml}</div>` : ''}
                  </div>
                  <div class="stats-container">${statsHtml}</div>
                  <div class="right-section">
                    <div class="weapon-box">
                      <div class="weapon-img-container">
                        ${data.weaponIcon ? `<img src="${assetUrl(data.weaponIcon)}" alt="">` : ''}
                      </div>
                      <div class="weapon-details">
                        <div class="weapon-title">${esc(data.weaponName)}</div>
                        <div class="weapon-lvl-refine">
                          <span>Lv.${data.weaponLevel != null ? data.weaponLevel : '?'}</span>
                          <span>R${data.weaponAffix != null ? data.weaponAffix : 1}</span>
                        </div>
                        <div class="weapon-substats">${weaponStatsHtml}</div>
                      </div>
                    </div>
                    <div class="set-info-box">${setHtml}</div>
                    <div class="score-box">
                      <div class="rank-badge"><img src="/static/assets/tiers/${esc(data.tierSum || 'B')}.png" alt="${esc(data.tierSum || 'B')}"></div>
                      <div class="score-label">${GenshinI18n.t('総合スコア')}</div>
                      <div class="score-value">${(Number(data.scoreSum) || 0).toFixed(1)}</div>
                      <div class="score-method-title">${GenshinI18n.t('計算方法')}</div>
                      <div class="score-method-label">${esc(data.calcMethodLabel || data.calcMethod || '')}</div>
                    </div>
                  </div>
                </div>
                ${getSavedSubstatDotsPref() ? `<div class="substat-dot-legend"><span class="substat-dot-legend-label">${GenshinI18n.t('伸び値')}</span><span class="substat-dot-legend-bars"><span class="substat-dot substat-dot-1"></span><span class="substat-dot substat-dot-2"></span><span class="substat-dot substat-dot-3"></span><span class="substat-dot substat-dot-4"></span></span></div>` : ''}
                <div class="artifacts-grid">${artsHtml}</div>
                ${resonanceHtml ? `<div class="resonance-corner">${resonanceHtml}</div>` : ''}
                ${uidHtml}
              </div>
              ${growthData && growthBody ? `</div>
              <div class="growth-panel" style="width:${growthPanelW}px;height:${growthLeftH}px;--element-color:${elemColor}">${growthBody}</div>
              </div>` : ''}
              </div>
                </div>`;
            }
            requestAnimationFrame(() => scaleGlassCard());
        }

        function scaleGlassCard() {
            const wrap = document.getElementById('glassScaleWrap');
            const glassEl = document.getElementById('glassCardContainer');
            if (!wrap || !glassEl || glassEl.classList.contains('hidden')) return;
            const parent = glassEl.parentElement;
            if (!parent) return;
            const parentStyle = getComputedStyle(parent);
            const padX = (parseFloat(parentStyle.paddingLeft) || 0) + (parseFloat(parentStyle.paddingRight) || 0);
            const available = Math.max(280, parent.clientWidth - padX);
            const baseW = 1200;
            const left = wrap.querySelector('.glass-growth-left');
            // テーマルート（.build-card / .cine-card / .scc-card）に対応
            const bc = wrap.querySelector('.build-card') || wrap.querySelector('.cine-card') || wrap.querySelector('.scc-card');
            // 育成レイアウト: 実測のカード高さに合わせて縮小後高さ（left/panel）を補正
            if (left && bc) {
                const k = parseFloat(getComputedStyle(left).getPropertyValue('--gc-growth-k')) || 0.775;
                const targetH = Math.round(bc.offsetHeight * k);
                if (parseFloat(left.style.height) !== targetH) {
                    left.style.height = `${targetH}px`;
                    const panel = wrap.querySelector('.growth-panel');
                    if (panel) panel.style.height = `${targetH}px`;
                }
            }
            const bcH = bc ? bc.offsetHeight : 800;
            const baseH = left ? Math.round(bcH * (parseFloat(left.style.height) / bcH || 1)) : bcH;
            const scale = Math.min(1, available / baseW);
            // 左上基準で縮小し、レイアウト上の占有サイズも縮小後に合わせる
            wrap.style.transformOrigin = 'top left';
            wrap.style.transform = `scale(${scale})`;
            wrap.style.width = baseW + 'px';
            // スケールはレイアウトに影響しないため、余白で見た目の占有領域を調整
            wrap.style.marginRight = `${-(baseW * (1 - scale))}px`;
            wrap.style.marginBottom = `${-(baseH * (1 - scale))}px`;
            glassEl.style.width = '100%';
            glassEl.style.height = `${baseH * scale}px`;
            glassEl.style.overflow = 'hidden';
        }

        window.addEventListener('resize', () => {
            if (viewMode === 'glass') scaleGlassCard();
        });

        // ==========================================================
        //  ビルドカード（HTML表示モード）
        // ==========================================================
        let latestHtmlRequestToken = 0;

        function updateDisclaimerFooter(selection) {
            disclaimerFooter.classList.remove('hidden');
        }

        async function loadHtmlCard(charId) {
            if (!charId) return;
            const glassEl = document.getElementById('glassCardContainer');
            if (glassEl) glassEl.classList.add('hidden');
            htmlCardContainer.classList.remove('hidden');
            htmlCardContainer.innerHTML = renderCardSkeletonHtml();

            const currentMethod = getSavedCalcMethod(charId);
            scoreCalcSelect.value = currentMethod;

            const selection = getActiveSelection();
            updateDisclaimerFooter(selection);
            const params = new URLSearchParams();
            params.set('calc_method', currentMethod);
            if (selection.fakeChar) params.set('fake_char', selection.fakeChar);
            if (selection.fakeWeapon) params.set('fake_weapon', selection.fakeWeapon);
            if (ver === 'beta') params.set('beta', 'true');
            if (getSavedGrowthPref()) params.set('growth', 'true');
            params.set('base_prec', getSavedBasePrec());
            const resonanceParam = getResonanceParam();
            if (resonanceParam) params.set('resonance', resonanceParam);

            const requestToken = ++latestHtmlRequestToken;
            try {
                // 先読み済みなら即座に描画（パラメータが完全一致した場合のみ）
                const pKey = withLang(params).toString();
                if (cardDataPrefetch.has(pKey)) {
                    const pre = cardDataPrefetch.get(pKey);
                    cardDataPrefetch.delete(pKey);
                    if (requestToken === latestHtmlRequestToken) { renderHtmlCard(pre); recordScoreHistory(pre); }
                    return;
                }
                const res = await fetch(`/api/card_data/${uid}/${charId}?${pKey}`);
                if (!res.ok) {
                    const errBody = await res.json().catch(() => ({}));
                    throw new Error(errBody.detail || `${GenshinI18n.t('データの取得に失敗しました')} (status: ${res.status})`);
                }
                const data = await res.json();
                cardDataPrefetch.set(pKey, data);
                if (requestToken !== latestHtmlRequestToken) return;
                renderHtmlCard(data);
                recordScoreHistory(data);
            } catch (e) {
                if (requestToken !== latestHtmlRequestToken) return;
                console.error('ビルドカード(HTML)の読み込みに失敗しました:', e);
                htmlCardContainer.innerHTML = `<div class="text-red-400 text-sm py-16 text-center">読み込みに失敗しました: ${esc(e.message)}</div>`;
                disclaimerFooter.classList.add('hidden');
            }
        }

        function tierClass(tier) {
            return `tier-${tier || 'B'}`;
        }

        function renderHtmlCard(data) {
            updateSelectedCharLabel(data);
            setLastKnownRealWeapon(data);
            setCurrentCharRegions(data.regions || []);
            const mainStatsHtml = data.mainStats.map(s => `
                <div class="flex items-center gap-2 bg-zinc-900/60 rounded-lg px-3 py-2 border border-zinc-800">
                    <span class="icon-chip-lg inline-flex items-center justify-center w-7 h-7 rounded-md shrink-0"><img src="/${esc(s.icon)}" class="w-6 h-6 object-contain"></span>
                    <span class="text-xs text-zinc-400 flex-1">${esc(s.label)}</span>
                    <span class="text-sm font-bold text-white">${esc(s.val)}</span>
                </div>
            `).join('');

            const artifactsHtml = data.artifacts.map(a => {
                if (!a) return `<div class="bg-zinc-900/40 rounded-lg p-3 border border-zinc-800 text-xs text-zinc-600 flex items-center justify-center">${GenshinI18n.t('未装備')}</div>`;
                const subsHtml = a.substats.map(s => {
                    let dots = '';
                    if (getSavedSubstatDotsPref() && (s.rolls || []).length) {
                        const rolls = s.rolls || [];
                        dots = rolls.map((t, ti) =>
                            `<span class="substat-dot substat-dot-${t + 1}"></span>`
                        ).join('');
                    }
                    return `
                    <div class="flex items-center gap-1.5 text-xs text-zinc-400">
                        <span class="icon-chip inline-flex items-center justify-center w-5 h-5 rounded-md shrink-0"><img src="/${esc(s.icon)}" class="w-4 h-4 object-contain"></span>
                        <span class="flex flex-col items-start min-w-0 leading-tight">
                            <span class="truncate">${esc(s.name)}</span>
                            ${dots ? `<span class="inline-flex gap-0.5 mt-0.5">${dots}</span>` : ''}
                        </span>
                        <span class="ml-auto text-zinc-200">${esc(s.value)}</span>
                    </div>`;
                }).join('');
                return `
                <div class="bg-zinc-900/60 rounded-lg p-3 border border-zinc-800">
                    <div class="flex items-center gap-2 mb-1.5">
                        <img src="/${esc(a.icon)}" class="w-10 h-10 rounded object-cover border border-zinc-700">
                        <div class="flex-1 min-w-0">
                            <div class="text-xs text-zinc-500">${esc(a.slot)} +${esc(a.upgrade)}</div>
                            <div class="text-xs text-zinc-300 truncate">${esc(a.main.name)} ${esc(a.main.value)}</div>
                        </div>
                        <div class="text-right shrink-0">
                            <div class="text-lg font-bold ${tierClass(a.tier)}">${esc(a.tier)}</div>
                            <div class="text-[10px] text-zinc-500">${(Number(a.score) || 0).toFixed(1)}</div>
                        </div>
                    </div>
                    <div class="space-y-1 pt-1.5 border-t border-zinc-800">${subsHtml}</div>
                </div>`;
            }).join('');

            const setBonusHtml = data.setBonuses.length
                ? data.setBonuses.map(s => `<div class="text-xs text-cyan-300">${esc(s.name)}${s.buff ? ` <span class="text-amber-300">${esc(s.buff)}</span>` : ''} ×${esc(s.count)}</div>`).join('')
                : `<div class="text-xs text-zinc-600">セット効果なし</div>`;

            // 育成（growth）モード: ON かつデータがあれば右列にパネルを追加
            let growthHtml = '';
            if (getSavedGrowthPref()) {
                growthHtml = growthPanelHtml(data.growth || data.progress || null);
            }

            htmlCardContainer.innerHTML = `
                <div class="grid ${growthHtml ? 'md:grid-cols-4' : 'md:grid-cols-3'} gap-4">
                    <div class="md:col-span-1 space-y-3">
                        <div class="flex items-center gap-3 bg-zinc-900/60 rounded-lg p-3 border border-zinc-800">
                            <div class="flex-1">
                                <div class="text-lg font-bold text-primary">${esc(data.displayName)} ${data.level != null ? `Lv.${esc(data.level)}` : ''}${(data.constellationIcons && data.constellationIcons.length && Number.isFinite(data.constellation)) ? ` ${esc(data.constellation)}凸` : ''}</div>
                                <div class="text-xs text-zinc-400">${esc(data.weaponName)}${data.weaponLevel ? ` Lv.${esc(data.weaponLevel)}` : ''}${data.weaponAffix ? ` (精錬${esc(data.weaponAffix)})` : ''}</div>
                            </div>
                            ${data.weaponIcon ? `<img src="/${esc(data.weaponIcon)}" class="w-12 h-12 rounded object-cover border border-zinc-700">` : ''}
                        </div>
                        <div class="grid grid-cols-2 gap-1.5">${mainStatsHtml}</div>
                        <div class="bg-zinc-900/60 rounded-lg p-3 border border-zinc-800">
                            <div class="text-xs text-zinc-500 mb-1">${GenshinI18n.t('セット効果')}</div>
                            ${setBonusHtml}
                        </div>
                        ${(data.resonanceBadges && data.resonanceBadges.length) ? `
                        <div class="bg-zinc-900/60 rounded-lg p-3 border border-zinc-800">
                            <div class="text-xs text-zinc-500 mb-1">${GenshinI18n.t('元素共鳴')}</div>
                            ${data.resonanceBadges.map(b => `<div class="text-xs text-zinc-200 flex items-center gap-1.5 py-0.5"><img src="/static/assets/props/${esc(String(b.elem || '').toLowerCase())}.png" class="w-4 h-4 object-contain shrink-0" alt=""><span class="font-semibold">${esc(b.text)}</span><span class="text-amber-300">${esc(b.label)}</span></div>`).join('')}
                        </div>` : ''}
                        <div class="flex items-center justify-between bg-zinc-900/60 rounded-lg p-3 border border-zinc-800">
                            <span class="text-xs text-zinc-400">${GenshinI18n.t('総合スコア')}</span>
                            <span class="text-2xl font-bold ${tierClass(data.tierSum)}">${(Number(data.scoreSum) || 0).toFixed(1)} <span class="text-sm">${esc(data.tierSum)}</span></span>
                        </div>
                    </div>
                    <div class="md:col-span-2 grid grid-cols-1 sm:grid-cols-2 gap-3">
                        ${artifactsHtml}
                    </div>
                    ${growthHtml ? `<div class="growth-panel-html">${growthHtml}</div>` : ''}
                </div>
            `;
        }

        const selectedCharLabel = document.getElementById('selectedCharLabel');

        function updateSelectedCharLabel(data) {
            if (!selectedCharLabel) return;
            if (!data) {
                selectedCharLabel.textContent = '—';
                return;
            }
            const name = data.displayName || '';
            const lv = data.level != null ? ` Lv.${data.level}` : '';
            selectedCharLabel.textContent = `${name}${lv}`;
        }

        function selectCharById(charId, forceFetch) {
            // 編成モード中は単体カードの選択を行わない（上部は編成メンバー表示）
            if (teamMode) return false;
            const thumb = document.querySelector(`.char-thumb[data-char-id="${charId}"]`);
            if (!thumb) return false;

            document.querySelectorAll('.char-thumb img').forEach(img => {
                img.classList.remove('opacity-100', 'ring-2', 'ring-primary');
                img.classList.add('opacity-50', 'grayscale-[0.3]');
            });
            document.querySelectorAll('.check-badge').forEach(b => b.remove());

            const activeImg = thumb.querySelector('img');
            activeImg.classList.remove('opacity-50', 'grayscale-[0.3]');
            activeImg.classList.add('opacity-100', 'ring-2', 'ring-primary');

            const badgeHTML = `<div class="check-badge absolute top-0.5 right-0.5 bg-cyan-400 rounded-full p-0.5 ring-2 ring-white/30"><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" class="text-black"><path d="M20 6 9 17l-5-5"></path></svg></div>`;
            thumb.insertAdjacentHTML('beforeend', badgeHTML);

            localStorage.setItem(`selected_char_${uid}`, charId);
            if (selectedCharLabel && viewMode !== 'image') {
                selectedCharLabel.textContent = '読み込み中...';
            }
            // 別キャラへの切替時は画像を自動生成する（immediateGen）。
            // 初回選択時は従来どおり自動生成せず、キャッシュが無ければ生成ボタンを出す。
            const isCharSwitch = !!currentSelectedCharId && currentSelectedCharId !== charId;
            refreshCurrentCard(charId, forceFetch, false, isCharSwitch);
            pendingForceFetchFromArtifacter = false;

            // 画像タブではフルカードデータを取らないため、名前だけ軽量に取得してラベル更新する。
            // refreshCurrentCard の後（= 新しいキャラの差し替え状態が確定した後）に取得し、
            // 直前キャラの差し替えキャラ/武器が新しいキャラの武器・地域背景に混入しないようにする
            if (selectedCharLabel && viewMode === 'image') {
                (async () => {
                    try {
                        const method = getSavedCalcMethod(charId);
                        const sel = getActiveSelection();
                        const p = new URLSearchParams();
                        p.set('calc_method', method);
                        if (sel.fakeChar) p.set('fake_char', sel.fakeChar);
                        if (sel.fakeWeapon) p.set('fake_weapon', sel.fakeWeapon);
                        if (ver === 'beta') p.set('beta', 'true');
                        const res = await fetch(`/api/card_data/${uid}/${charId}?${withLang(p)}`);
                        if (!res.ok) return;
                        const data = await res.json();
                        // 取得中にキャラや差し替えが変わっていたら適用しない
                        const selNow = getActiveSelection();
                        if (currentSelectedCharId === charId &&
                            selNow.fakeChar === sel.fakeChar &&
                            selNow.fakeWeapon === sel.fakeWeapon) {
                            updateSelectedCharLabel(data);
                            setLastKnownRealWeapon(data);
                            setCurrentCharRegions(data.regions || []);
                        }
                    } catch (e) { /* ignore */ }
                })();
            }
            return true;
        }

        function bindCharThumbClick(thumbEl) {
            thumbEl.addEventListener('click', function() {
                selectCharById(this.getAttribute('data-char-id'), pendingForceFetchFromArtifacter);
            });
        }

        // 再取得後のキャラ一覧でサムネイル行を作り直す（クリック動作も再バインドする）
        function renderCharThumbRow(charList) {
            const row = document.getElementById('charThumbRow');
            if (!row || !Array.isArray(charList) || charList.length === 0) return false;
            // 編成モード用にショーケース一覧も更新
            if (typeof showcaseCharList !== 'undefined') {
                showcaseCharList = charList.map(ch => ({ id: String(ch.id), icon: ch.icon || '' }));
            }
            row.innerHTML = '';
            for (const ch of charList) {
                const div = document.createElement('div');
                div.className = 'relative w-14 h-14 sm:w-16 sm:h-16 shrink-0 cursor-pointer char-thumb';
                div.setAttribute('data-char-id', String(ch.id));
                div.setAttribute('data-char-icon', ch.icon || '');
                const img = document.createElement('img');
                img.src = ch.icon;
                img.className = 'w-full h-full bg-gradient-to-tr from-[rgba(49,43,71,0.53)] to-[rgba(102,115,150,0.42)] rounded-lg object-cover opacity-50 grayscale-[0.3] transition-all hover:opacity-100 drag-none';
                div.appendChild(img);
                if (isBetaOnlyChar(ch.id)) {
                    div.insertAdjacentHTML('beforeend', betaNewBadgeHTML('tl lg'));
                }
                bindCharThumbClick(div);
                row.appendChild(div);
            }
            return true;
        }

        // 再取得後の選択解決: 選択中キャラが消えていたら「同じ位置」のキャラを選ぶ
        function resolveCharAfterRefresh(prevSelectedCharId, prevSelectedIndex, charList) {
            if (!charList || charList.length === 0) return prevSelectedCharId;
            if (charList.some(ch => String(ch.id) === String(prevSelectedCharId))) return prevSelectedCharId;
            const idx = prevSelectedIndex >= 0 ? Math.min(prevSelectedIndex, charList.length - 1) : 0;
            return String(charList[idx].id);
        }

        document.querySelectorAll('.char-thumb').forEach(bindCharThumbClick);

        scoreCalcSelect.addEventListener('change', function() {
            // 共有閲覧モード: 計算方法はスナップショット凍結済みで変更不可(セレクタも非表示)。無視する。
            if (SHARE_PAYLOAD) { return; }
            if (currentSelectedCharId) {
                saveCalcMethod(currentSelectedCharId, this.value);
                // 編成モードでは計算方法を反映して編成カードを再取得する
                if (teamMode) {
                    const slot = teamSlotById(activeTeamId);
                    if (slot && slot.chars && slot.chars.length === 4) {
                        generateTeamCardHtml(slot.chars.slice(), buildTeamConfigs(slot.chars));
                    }
                    return;
                }
                // 計算方法変更時は即時画像生成（キャッシュが無ければその場で生成）
                refreshCurrentCard(currentSelectedCharId, false, false, true);
            }
        });

        const basePrecSelect = document.getElementById('basePrecSelect');
        if (basePrecSelect) {
            basePrecSelect.value = getSavedBasePrec();
            basePrecSelect.addEventListener('change', function() {
                setBasePrec(this.value);
                // 共有閲覧モード: 凍結済み(2桁)スナップショットをローカル丸め/展開して再描画のみ(enka参照なし)
                if (SHARE_PAYLOAD) { applySharePrecision(); shareLiveRefresh(); return; }
                // 編成モードでは表示精度を反映して編成カードを再取得する
                if (teamMode) {
                    const slot = teamSlotById(activeTeamId);
                    if (slot && slot.chars && slot.chars.length === 4) {
                        generateTeamCardHtml(slot.chars.slice(), buildTeamConfigs(slot.chars));
                    }
                    return;
                }
                // 表示精度の変更はキャッシュを無視して再取得して反映する
                refreshCurrentCard(currentSelectedCharId, true, false);
            });
        }

        regenerateApiBtn.addEventListener('click', async function() {
            if (!teamMode && !currentSelectedCharId) return;

            const originalLabel = regenerateApiBtn.innerHTML;
            setTopImageButtonsDisabled(true);
            regenerateApiBtn.textContent = '取得中...';

            // 再取得前の選択位置を記録（選択キャラが消えた場合の「同じ位置」フォールバック用）
            const prevSelectedCharId = currentSelectedCharId;
            let prevSelectedIndex = -1;
            document.querySelectorAll('.char-thumb').forEach((t, i) => {
                if (t.getAttribute('data-char-id') === prevSelectedCharId) prevSelectedIndex = i;
            });

            let charList = null;
            let cooldownActive = 0;
            try {
                const res = await fetch(`/refresh_uid/${uid}?ver=${ver}`, { method: 'POST' });
                if (!res.ok) {
                    const errBody = await res.json().catch(() => ({}));
                    throw new Error(errBody.detail || `再取得に失敗しました (status: ${res.status})`);
                }
                const data = await res.json();
                if (data && data.cooldown) {
                    cooldownActive = data.cooldown;
                    if (Array.isArray(data.char_list)) charList = data.char_list;
                } else if (data && Array.isArray(data.char_list)) {
                    charList = data.char_list;
                }
            } catch (e) {
                console.error('Enka APIの再取得に失敗しました:', e);
                showToast(`データの再取得に失敗しました。\n${e.message}\n（キャッシュ済みのデータで再生成します）`, 'error');
            } finally {
                setTopImageButtonsDisabled(false);
                regenerateApiBtn.innerHTML = originalLabel;
            }

            if (cooldownActive > 0) {
                showToast(`Enka APIのクールタイム中です。${Math.ceil(cooldownActive)}秒後にお試しください`, 'info');
                startEnkaCooldown(cooldownActive);
                return;
            }

            // Enka API へのリクエストが発生したタイミングでキャッシュ済み画像を破棄し、
            // 必ず作り直した画像が表示されるようにする
            await clearCardImageCache();

            if (teamMode) {
                // 編成モードは維持したままショーケース一覧を更新し、カードを再取得して反映
                if (charList && charList.length) {
                    showcaseCharList = charList.map(ch => ({ id: String(ch.id), icon: ch.icon || '' }));
                    renderTeamCharRow();
                    if (typeof updateTeamMemberLabel === 'function') updateTeamMemberLabel(currentSelectedCharId);
                }
                if (typeof generateActiveTeam === 'function') generateActiveTeam();
                return;
            }

            if (charList && charList.length > 0) {
                // 再取得でキャラ一覧が変わった場合はサムネイル行を書き換える。
                // 選択中キャラが消えた場合は「同じ位置」のキャラを選んで表示する
                renderCharThumbRow(charList);
                const nextCharId = resolveCharAfterRefresh(prevSelectedCharId, prevSelectedIndex, charList);
                selectCharById(nextCharId, true);
            } else {
                // 一覧が取れなかった場合は従来どおり現選択キャラで再生成する
                refreshCurrentCard(prevSelectedCharId, true);
            }
        });

        // ==========================================================
        //  編成カード（12スロット / 編成モード / 4キャラ毎に設定可能）
        // ==========================================================
        // 編成カード生成。false で UI のみ非表示（バックエンドは維持）
        const TEAM_GEN_ENABLED = true;
        const teamCardModal = document.getElementById('teamCardModal');
        const teamCardCancelBtn = document.getElementById('teamCardCancelBtn');
        const teamModeBar = document.getElementById('teamModeBar');
        const teamGenBtn = document.getElementById('teamGenBtn');
        const teamEditSlotBtn = document.getElementById('teamEditSlotBtn');
        const teamExitBtn = document.getElementById('teamExitBtn');
        let pickerTargetIndex = null; // 1枠ピッカーの対象メンバー位置
        let teamViewOnly = false; // 共有リンクから入った場合の閲覧モード（メンバー編集・共有・再取得を無効化）
        let sharedUidHidden = false; // 共有元がUID非表示指定の場合

        // ショーケース一覧（編成モード中は .char-thumb が消えるため、この配列を参照する）
        let showcaseCharList = [];
        document.querySelectorAll('#charThumbRow .char-thumb').forEach(t => {
            showcaseCharList.push({ id: t.getAttribute('data-char-id'), icon: t.getAttribute('data-char-icon') || '' });
        });

        // ---- 12スロット保存 ----
        const TEAM_SLOTS_KEY = 'team_slots_' + uid;
        const TEAM_ACTIVE_KEY = 'team_active_' + uid;
        let teamSlots = loadTeamSlots();
        let activeTeamId = null;
        let teamMode = false;
        let teamActiveIdx = 0;
        function loadTeamSlots() {
            try {
                const arr = JSON.parse(localStorage.getItem(TEAM_SLOTS_KEY)) || [];
                return Array.isArray(arr) ? arr : [];
            } catch (e) { return []; }
        }
        function saveTeamSlots() {
            try { localStorage.setItem(TEAM_SLOTS_KEY, JSON.stringify(teamSlots)); } catch (e) {}
        }
        function teamSlotById(id) {
            return teamSlots.find(s => s && s.id === id) || null;
        }
        function teamCharIconSrc(cid) {
            const ch = showcaseCharList.find(c => String(c.id) === String(cid));
            if (ch && ch.icon) return ch.icon;
            const thumb = document.querySelector(`.char-thumb[data-char-id="${cid}"]`);
            return thumb ? (thumb.getAttribute('data-char-icon') || '') : '';
        }

        // ---- モーダル：スロット管理（12スロット / 名前変更 / 即編成画面へ） ----
        function openTeamCardModal() {
            openModal(teamCardModal, closeTeamCardModal);
            renderTeamSlotList();
        }
        function closeTeamCardModal() {
            closeModal(teamCardModal);
        }
        function normalizeTeamSlots() {
            if (!Array.isArray(teamSlots)) teamSlots = [];
            if (teamSlots.length < 12) teamSlots.length = 12;
            for (let i = 0; i < 12; i++) {
                const s = teamSlots[i];
                if (s && Array.isArray(s.chars)) {
                    while (s.chars.length < 4) s.chars.push(null);
                    if (s.chars.length > 4) s.chars = s.chars.slice(0, 4);
                }
            }
        }
        function slotDisplayName(slot, idx) {
            return (slot && slot.name) ? slot.name : (GenshinI18n.isEn() ? `Team ${idx + 1}` : `編成${idx + 1}`);
        }
        function renderTeamSlotList() {
            normalizeTeamSlots();
            const wrap = document.getElementById('teamSlotList');
            if (!wrap) return;
            wrap.innerHTML = '';
            for (let i = 0; i < 12; i++) {
                const slot = teamSlots[i];
                const row = document.createElement('div');
                if (slot) {
                    const count = slot.chars.filter(c => c).length;
                    const icons = slot.chars.map(c => {
                        if (!c) return '<div class="w-7 h-9 rounded bg-zinc-800 border border-dashed border-zinc-600 flex items-center justify-center text-zinc-600 text-xs">+</div>';
                        const src2 = teamCharIconSrc(c);
                        return `<div class="w-7 h-9 rounded overflow-hidden bg-zinc-800">${src2 ? `<img src="${esc(src2)}" class="w-full h-full object-cover">` : ''}</div>`;
                    }).join('');
                    row.className = 'flex items-center gap-3 p-2 rounded-xl border border-zinc-700 bg-zinc-800/50 hover:border-cyan-500/60 transition-all cursor-pointer';
                    row.title = 'クリックで編成画面を開く';
                    row.innerHTML = `
                        <div class="flex gap-1 shrink-0">${icons}</div>
                        <input type="text" value="${esc(slot.name || '')}" placeholder="${esc(slotDisplayName(slot, i))}" maxlength="24"
                            class="flex-1 min-w-0 bg-zinc-900/80 border border-zinc-700 focus:border-cyan-500 rounded-lg px-2.5 py-1.5 text-sm text-white placeholder-zinc-600 outline-none"
                            data-slot-rename="${i}">
                        <span class="text-[11px] shrink-0 ${count === 4 ? 'text-cyan-400' : 'text-zinc-500'}">${count}/4</span>
                        <button type="button" data-slot-del="${i}" class="shrink-0 px-2 py-1 rounded-lg border border-zinc-700 text-zinc-400 hover:text-red-300 hover:border-red-500/60 text-xs transition-all">削除</button>
                    `;
                    row.onclick = (e) => {
                        if (e.target.closest('input') || e.target.closest('button')) return;
                        closeTeamCardModal();
                        loadTeam(slot.id);
                    };
                    row.querySelector(`[data-slot-del="${i}"]`).onclick = async (e) => {
                        e.stopPropagation();
                        const ok = await requestConfirmation(`${slotDisplayName(slot, i)}を削除しますか？`, {
                            title: '編成スロットの削除',
                            confirmText: '削除',
                            cancelText: 'キャンセル'
                        });
                        if (!ok) return;
                        teamSlots[i] = null;
                        saveTeamSlots();
                        renderTeamSlotList();
                    };
                    const input = row.querySelector(`[data-slot-rename="${i}"]`);
                    input.onclick = (e) => e.stopPropagation();
                    input.onchange = () => {
                        slot.name = input.value.trim().slice(0, 24);
                        saveTeamSlots();
                        input.value = slot.name;
                        input.placeholder = slotDisplayName(slot, i);
                    };
                } else {
                    row.className = 'flex items-center gap-3 p-2 rounded-xl border-2 border-dashed border-zinc-700 bg-zinc-800/30 hover:border-cyan-500/60 transition-all cursor-pointer text-zinc-500 hover:text-cyan-300';
                    row.innerHTML = `<div class="text-lg font-bold leading-none px-2">+</div><div class="text-sm">新規編成を作成</div>`;
                    row.onclick = () => {
                        const s = { id: 'team_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), chars: [null, null, null, null], name: '' };
                        teamSlots[i] = s;
                        saveTeamSlots();
                        closeTeamCardModal();
                        loadTeam(s.id);
                    };
                }
                wrap.appendChild(row);
            }
        }

        // ---- モーダル：メンバー選択（1枠ピッカー） ----
        function openTeamCharPicker(memberIndex) {
            const slot = teamSlotById(activeTeamId);
            if (!slot) return;
            pickerTargetIndex = memberIndex;
            const title = document.getElementById('teamCharPickerTitle');
            if (title) title.textContent = `${memberIndex + 1}番目のメンバーを選択`;
            const removeBtn = document.getElementById('teamCharPickerRemoveBtn');
            if (removeBtn) removeBtn.classList.toggle('hidden', !slot.chars[memberIndex]);
            const grid = document.getElementById('teamCharPickerGrid');
            if (grid) {
                grid.innerHTML = '';
                showcaseCharList.forEach(ch => {
                    const cid = String(ch.id);
                    const src2 = ch.icon;
                    if (!src2) return;
                    const tile = document.createElement('div');
                    tile.className = 'team-select-tile relative w-20 h-20 shrink-0 rounded-lg border-2 overflow-hidden cursor-pointer border-zinc-700 hover:border-cyan-400';
                    tile.innerHTML = `<img src="${esc(src2)}" class="w-full h-full object-cover drag-none" loading="lazy">`;
                    tile.onclick = () => applyTeamMember(cid);
                    grid.appendChild(tile);
                });
            }
            openModal(document.getElementById('teamCharPickerModal'), closeTeamCharPicker);
        }
        function closeTeamCharPicker() {
            closeModal(document.getElementById('teamCharPickerModal'));
            pickerTargetIndex = null;
        }
        function applyTeamMember(cid) {
            const slot = teamSlotById(activeTeamId);
            if (pickerTargetIndex == null || !slot) { closeTeamCharPicker(); return; }
            const pos = pickerTargetIndex;
            slot.chars[pos] = cid;
            saveTeamSlots();
            closeTeamCharPicker();
            renderTeamCharRow();
            refreshTeamMember(pos);
        }
        function removeTeamMember() {
            const slot = teamSlotById(activeTeamId);
            if (pickerTargetIndex == null || !slot) { closeTeamCharPicker(); return; }
            const pos = pickerTargetIndex;
            slot.chars[pos] = null;
            saveTeamSlots();
            closeTeamCharPicker();
            renderTeamCharRow();
            refreshTeamMember(pos);
        }

        async function fetchTeamMemberData(cid, cfg) {
            const p = new URLSearchParams();
            p.set('calc_method', (cfg && cfg.calc_method) || 'crit');
            if (cfg && cfg.fake_char) p.set('fake_char', cfg.fake_char);
            if (cfg && cfg.fake_weapon) p.set('fake_weapon', cfg.fake_weapon);
            if (cfg && cfg.base_prec) p.set('base_prec', cfg.base_prec);
            if (ver === 'beta') p.set('beta', 'true');
            if (GenshinI18n.isEn()) p.set('lang', 'en');
            const res = await fetch(`/api/card_data/${uid}/${cid}?${p.toString()}`, { cache: 'no-store' });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(body.detail || `データ取得に失敗 (${cid}: ${res.status})`);
            }
            const data = await res.json();
            data._isSwap = !!(cfg && cfg.fake_char);
            return data;
        }

        // 1メンバーだけ再取得して編成カードを部分的に更新する（追加/変更/再試行共通）
        async function refreshTeamMember(pos) {
            const slot = teamSlotById(activeTeamId);
            if (!slot) return;
            const cid = slot.chars[pos];
            const token = ++latestTeamHtmlToken;
            if (!cid) {
                if (lastTeamHtmlRender) {
                    lastTeamHtmlRender.datas[pos] = null;
                    renderTeamCardHtml(lastTeamHtmlRender.datas);
                } else {
                    generateTeamCardHtml(slot.chars.slice(), buildTeamConfigs(slot.chars));
                }
                return;
            }
            try {
                const cfg = buildTeamConfigs(slot.chars)[pos] || {};
                const data = await fetchTeamMemberData(cid, cfg);
                if (token !== latestTeamHtmlToken) return;
                if (!lastTeamHtmlRender) {
                    generateTeamCardHtml(slot.chars.slice(), buildTeamConfigs(slot.chars));
                    return;
                }
                lastTeamHtmlRender.datas[pos] = data;
                renderTeamCardHtml(lastTeamHtmlRender.datas);
            } catch (e) {
                if (token !== latestTeamHtmlToken) return;
                showToast(`${pos + 1}番目のメンバー: ${e.message}`, 'error');
            }
        }

        // ---- 編成モード ----
        // バー表示・操作UIをモード別に整える（通常編成 / 閲覧モード）
        function applyTeamModeChrome(viewOnly, idx, slot) {
            const bar = document.getElementById('teamModeBar');
            const title = bar ? bar.querySelector('.text-sm.font-semibold') : null;
            const label = document.getElementById('teamModeSlotLabel');
            const btns = {
                share: document.getElementById('teamShareBtn'),
                exit: document.getElementById('teamExitBtn'),
            };
            const actions = {
                share: document.getElementById('shareCardBtn'),
                regen: document.getElementById('regenerateImageBtn'),
                refetch: document.getElementById('regenerateApiBtn'),
                slots: document.getElementById('teamEditSlotBtn'),
            };
            if (viewOnly) {
                if (title) {
                    const t = title.querySelector('[data-i18n]');
                    if (t) { t.textContent = GenshinI18n.t('閲覧モード'); t.removeAttribute('data-i18n'); }
                }
                if (label && slot) label.textContent = slot.name || (GenshinI18n.isEn() ? `Team ${idx + 1}` : `編成${idx + 1}`);
                if (btns.share) btns.share.classList.add('hidden');
                const abyssBtn = document.getElementById('teamShareAbyssBtn');
                if (abyssBtn) abyssBtn.classList.add('hidden');
                if (btns.exit) btns.exit.classList.remove('hidden');
                Object.values(actions).forEach(b => { if (b) b.classList.add('hidden'); });
            } else {
                if (title) {
                    let t = title.querySelector('[data-i18n]');
                    if (t) t.textContent = GenshinI18n.t('編成モード');
                }
                if (label) label.textContent = GenshinI18n.isEn() ? ` (Team ${idx >= 0 ? idx + 1 : ''})` : `（編成${idx >= 0 ? idx + 1 : ''}）`;
                const abyssBtn2 = document.getElementById('teamShareAbyssBtn');
                if (btns.share) btns.share.classList.remove('hidden');
                if (abyssBtn2) abyssBtn2.classList.remove('hidden');
                if (btns.exit) btns.exit.classList.remove('hidden');
                if (actions.share) actions.share.classList.remove('hidden');
                if (actions.regen) actions.regen.classList.remove('hidden');
                if (actions.refetch) actions.refetch.classList.remove('hidden');
                if (actions.slots) actions.slots.classList.remove('hidden');
            }
        }

        function setTeamViewModeRestriction(on) {
            document.querySelectorAll('.view-mode-btn[data-view="glass"]').forEach(btn => {
                btn.disabled = on;
                btn.classList.toggle('opacity-40', on);
                btn.classList.toggle('cursor-not-allowed', on);
            });
        }
        function loadTeam(slotId) {
            const slot = teamSlotById(slotId);
            if (!slot) return;
            activeTeamId = slotId;
            teamMode = true;
            teamActiveIdx = 0;
            currentSelectedCharId = slot.chars[0];
            // リロード後も編成モードを維持する
            try { localStorage.setItem(TEAM_ACTIVE_KEY, slotId); } catch (e) {}
            if (teamModeBar) teamModeBar.classList.remove('hidden');
            // 編成モード中はヘッダーの「編成カード」ボタンを隠す（スロット管理と重複）
            if (teamCardBtn) teamCardBtn.classList.add('hidden');
            const idx = teamSlots.findIndex(s => s && s.id === slotId);
            applyTeamModeChrome(teamViewOnly, idx, slot);
            renderTeamCharRow();
            selectTeamMember(0);
            // 編成モードでは「画像生成」のみ有効
            viewMode = 'image';
            saveViewMode(viewMode);
            applyViewModeUI();
            setTeamViewModeRestriction(true);
            latestGlassRequestToken++;
            latestHtmlRequestToken++;
            // 編成カード（HTML表示）を自動描画する（1カラム化後に幅が変わるため描画後にスケール調整）
            generateTeamCardHtml(slot.chars.slice(), buildTeamConfigs(slot.chars));
            requestAnimationFrame(() => scaleTeamCardHtml());
            // 編成モード中は背景設定（元素/地域/カスタム色）・ステータス編集・育成情報トグルを非表示
            if (typeof bgColorControls !== 'undefined' && bgColorControls) bgColorControls.classList.add('hidden');
            const growthWrap = document.getElementById('growthToggleWrap');
            if (growthWrap) growthWrap.classList.add('hidden');
            if (typeof updateStatusEditorVisibility === 'function') updateStatusEditorVisibility();
            if (typeof updateGenerateDisable === 'function') updateGenerateDisable();
            if (typeof updateAbyssUI === 'function') updateAbyssUI();
            // 閲覧モード: サイドバー隠し+1カラム化。通常編成: 左サイドバーに差し替え/表示設定を出す
            const cols = document.getElementById('mainColumns');
            if (teamViewOnly) {
                if (cols) cols.classList.add('team-single');
                const sp = document.getElementById('teamSwapPanel');
                if (sp) sp.classList.add('hidden');
            } else {
                if (cols) cols.classList.remove('team-single');
                moveSwapPanelToLeft();
            }
            const rightCol = document.getElementById('sidebarRight');
            if (rightCol) rightCol.classList.toggle('hidden', teamMode);
            if (!teamMode && rightCol) rightCol.classList.remove('hidden');
            // 表示モードトグルも画像生成しか使えないため隠す
            const vmt = document.getElementById('viewModeToggle');
            if (vmt) vmt.classList.add('hidden');
        }

        // 差し替えパネル(#teamSwapPanel)を左サイドバー末尾へ移動し、コンボ行を同期
        function moveSwapPanelToLeft() {
            const aside = document.getElementById('sidebarLeft');
            const panel = document.getElementById('teamSwapPanel');
            if (aside && panel && panel.parentElement !== aside) aside.appendChild(panel);
            if (panel) panel.classList.remove('hidden');
            // コンボ行を teamComboRow に同期させる
            if (typeof renderComboRow === 'function') renderComboRow();
        }
        function teamMemberCombo(cid) {
            const keys = comboStorageKeys(cid);
            let combos = [];
            try { combos = JSON.parse(localStorage.getItem(keys.list)) || []; } catch (e) { combos = []; }
            const active = localStorage.getItem(keys.active) || '';
            const c = combos.find(x => x.id === active) || null;
            return c ? { fakeChar: c.fakeChar || '', fakeWeapon: c.fakeWeapon || '' } : { fakeChar: '', fakeWeapon: '' };
        }
        function renderTeamCharRow() {
            const row = document.getElementById('charThumbRow');
            const slot = teamSlotById(activeTeamId);
            if (!row || !slot) return;
            // 差し替えキャラのアイコン読み込みを先に確保
            const fakeChars = slot.chars.map(cid => teamMemberCombo(cid).fakeChar).filter(Boolean);
            if (fakeChars.length) {
                ensureCharIconsLoaded(fakeChars).then(() => renderTeamCharRowBody(slot));
            }
            renderTeamCharRowBody(slot);
        }
        function renderTeamCharRowBody(slot) {
            const row = document.getElementById('charThumbRow');
            if (!row) return;
            row.innerHTML = '';
            let dragFromIdx = null;
            slot.chars.forEach((cid, idx) => {
                const div = document.createElement('div');
                div.className = 'relative w-14 h-14 sm:w-16 sm:h-16 shrink-0';
                div.setAttribute('data-team-idx', idx);
                div.setAttribute('draggable', String(!teamViewOnly));
                if (!teamViewOnly) div.title = 'ドラッグで並べ替え';
                if (!cid) {
                    // 空き枠: 追加ボタン（閲覧モードでは飾りのみ）
                    div.innerHTML = `
                        <div class="w-full h-full rounded-lg border-2 border-dashed border-zinc-600 ${teamViewOnly ? '' : 'hover:border-cyan-400 cursor-pointer'} flex items-center justify-center text-xl text-zinc-500 transition-all" ${teamViewOnly ? '' : 'title="メンバーを追加"'}>＋</div>
                        <div class="team-order-badge" style="top:2px;left:2px;">${idx + 1}</div>
                    `;
                    if (!teamViewOnly) div.onclick = () => openTeamCharPicker(idx);
                } else {
                    const combo = teamMemberCombo(cid);
                    // 差し替えキャラならそのアイコン、そうでなければ実データのアイコン
                    const src = combo.fakeChar ? charAssetUrl(combo.fakeChar) : teamCharIconSrc(cid);
                    // 差し替え武器があれば右下に武器バッジ
                    const wsrc = (combo.fakeWeapon && !isSwapHiddenWeapon(combo.fakeWeapon)) ? weaponAssetUrl(combo.fakeWeapon) : '';
                    const selected = teamActiveIdx === idx;
                    div.innerHTML = `
                        ${src ? `<img src="${esc(src)}" class="w-full h-full rounded-lg object-cover cursor-pointer drag-none ${selected ? 'opacity-100 ring-2 ring-primary' : 'opacity-50 grayscale-[0.3]'}">` : ''}
                        <div class="team-order-badge" style="top:2px;left:2px;">${idx + 1}</div>
                        ${wsrc ? `<div class="weapon-badge"><img src="${esc(wsrc)}" class="w-full h-full object-cover"></div>` : ''}
                        ${selected && !teamViewOnly ? `<button type="button" data-team-change="${idx}" title="このメンバーを変更" class="absolute left-1/2 -translate-x-1/2 -bottom-1.5 px-1.5 py-0.5 rounded bg-cyan-600 hover:bg-cyan-500 text-white text-[10px] font-bold leading-none shadow border border-cyan-400/60 whitespace-nowrap">変更</button>` : ''}
                    `;
                    div.onclick = (e) => {
                        if (e.target.closest('[data-team-change]')) { openTeamCharPicker(idx); return; }
                        selectTeamMember(idx);
                    };
                }
                // ドラッグで並べ替え
                div.ondragstart = (e) => { dragFromIdx = idx; e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', String(idx)); } catch (err) {} };
                div.ondragover = (e) => { e.preventDefault(); };
                div.ondrop = (e) => {
                    e.preventDefault();
                    const from = dragFromIdx;
                    dragFromIdx = null;
                    if (from == null || from === idx) return;
                    const arr = slot.chars;
                    const [moved] = arr.splice(from, 1);
                    arr.splice(idx, 0, moved);
                    saveTeamSlots();
                    teamActiveIdx = idx;
                    renderTeamCharRow();
                    reorderTeamCardColumns(from, idx);
                };
                row.appendChild(div);
            });
            const label = document.getElementById('selectedCharLabel');
            if (label) label.textContent = '編成モード';
        }
        function selectTeamMember(idx) {
            const slot = teamSlotById(activeTeamId);
            if (!slot) return;
            teamActiveIdx = idx;
            const cid = slot.chars[idx];
            currentSelectedCharId = cid;
            renderTeamCharRow();
            updateTeamCardHighlight();
            // サイドバーをこのキャラの保存済み設定に同期
            loadComboState(cid);
            sanitizeCombosForVer();
            sanitizeHiddenCombos();
            renderComboRow();
            if (cid) {
                updateEditWeaponUI();
                ensureComboIconsLoaded().then(() => { renderComboRow(); updateEditWeaponUI(); });
                scoreCalcSelect.value = getSavedCalcMethod(cid);
                applyBgColorControlsUI();
                updateTeamMemberLabel(cid);
            } else {
                // 空き枠を選択中
                const lbl = document.getElementById('selectedCharLabel');
                if (lbl) lbl.textContent = `${idx + 1}: 未選択`;
            }
        }
        function updateTeamMemberLabel(cid) {
            (async () => {
                try {
                    const sel = getActiveSelection();
                    const p = new URLSearchParams();
                    p.set('calc_method', getSavedCalcMethod(cid));
                    if (sel.fakeChar) p.set('fake_char', sel.fakeChar);
                    if (sel.fakeWeapon) p.set('fake_weapon', sel.fakeWeapon);
                    if (ver === 'beta') p.set('beta', 'true');
                    const res = await fetch(`/api/card_data/${uid}/${cid}?${withLang(p)}`);
                    if (!res.ok) return;
                    const data = await res.json();
                    if (teamMode && currentSelectedCharId === cid) {
                        const label = document.getElementById('selectedCharLabel');
                        if (label) label.textContent = GenshinI18n.isEn() ? `${data.displayName} (Team ${teamActiveIdx + 1})` : `${data.displayName}（編成${teamActiveIdx + 1}）`;
                        setLastKnownRealWeapon(data);
                        setCurrentCharRegions(data.regions || []);
                        updateEditWeaponUI();
                    }
                } catch (e) { /* ignore */ }
            })();
        }
        function exitTeamMode() {
            teamMode = false;
            activeTeamId = null;
            teamActiveIdx = 0;
            try { localStorage.removeItem(TEAM_ACTIVE_KEY); } catch (e) {}
            if (teamModeBar) teamModeBar.classList.add('hidden');
            if (teamCardBtn && TEAM_GEN_ENABLED) teamCardBtn.classList.remove('hidden');  // 無効化中は再表示しない
            setTeamViewModeRestriction(false);
            if (typeof bgColorControls !== 'undefined' && bgColorControls) bgColorControls.classList.remove('hidden');
            const colsEl = document.getElementById('mainColumns');
            if (colsEl) colsEl.classList.remove('team-single');
            const spEl = document.getElementById('teamSwapPanel');
            if (spEl) spEl.classList.add('hidden');
            const rightEl = document.getElementById('sidebarRight');
            if (rightEl) rightEl.classList.remove('hidden'); // キャラ差し替え(右)を復帰
            const growthWrapEl = document.getElementById('growthToggleWrap');
            if (growthWrapEl) growthWrapEl.classList.remove('hidden');
            teamViewOnly = false;
            const boxEl = document.getElementById('mainDisplayBox');
            if (boxEl) { boxEl.style.height = ''; boxEl.style.minHeight = ''; }
            const vmtEl = document.getElementById('viewModeToggle');
            if (vmtEl) vmtEl.classList.remove('hidden');
            if (typeof updateStatusEditorVisibility === 'function') updateStatusEditorVisibility();
            if (typeof updateGenerateDisable === 'function') updateGenerateDisable();
            if (typeof updateAbyssUI === 'function') updateAbyssUI();
            renderCharThumbRow(showcaseCharList);
            if (currentSelectedCharId) selectCharById(currentSelectedCharId, false);
        }
        function restoreTeamMode() {
            if (!TEAM_GEN_ENABLED) return false;  // 編成カード一時無効化: リロード後も復元しない
            let savedId = null;
            try { savedId = localStorage.getItem(TEAM_ACTIVE_KEY); } catch (e) {}
            if (!savedId) return false;
            const slot = teamSlotById(savedId);
            if (!slot) return false;
            loadTeam(savedId);
            return true;
        }

        // ---- キャラ毎の設定を集めて configs を構築（localStorage の既存設定を流用） ----
        function buildTeamConfigs(chars) {
            return chars.map(cid => {
                if (!cid) return {};
                const keys = comboStorageKeys(cid);
                let combos = [];
                try { combos = JSON.parse(localStorage.getItem(keys.list)) || []; } catch (e) { combos = []; }
                const active = localStorage.getItem(keys.active) || '';
                const c = combos.find(x => x.id === active) || null;
                const fakeChar = c ? (c.fakeChar || '') : '';
                const fakeWeapon = c ? (c.fakeWeapon || '') : '';
                const bgMap = getBgRegionMap();
                const regKey = cid + (fakeChar ? ':' + fakeChar : '');
                const region = bgMap[regKey] || '';
                const bgMode = getBgColorMode();
                return {
                    calc_method: getSavedCalcMethod(cid),
                    fake_char: fakeChar,
                    fake_weapon: fakeWeapon,
                    bg_mode: region ? 'region' : (bgMode === 'custom' ? 'custom' : 'element'),
                    bg_color: (region ? '' : (bgMode === 'custom' ? getCustomBgColor().replace('#', '') : '')),
                    bg_region: region,
                    base_prec: getSavedBasePrec(),
                };
            });
        }

        // ---- 編成カード（HTML表示）: /api/card_data を4キャラ分並列取得して DOM 描画 ----
        const TC_ELEM = { Pyro: '#e06a4a', Hydro: '#5d74d6', Anemo: '#3fbfa0', Electro: '#a06fd0',
                          Cryo: '#7fb6e6', Geo: '#d8b25c', Dendro: '#7fbf6a', None: '#8a93a6' };
        const TC_RESONANCE_NAMES = { Pyro: '熱誠の炎', Hydro: '治療の水', Anemo: '迅速の風', Electro: '強権の雷',
                                     Dendro: '蔓生の草', Cryo: '粉砕の氷', Geo: '不動の岩' };
        let latestTeamHtmlToken = 0;
        let lastTeamHtmlRender = null; // 最後に描画した編成カードデータ（再描画用）

        function tcDots(rolls) {
            if (!getSavedSubstatDotsPref()) return '';
            const arr = (rolls || []).filter(t => Number(t) >= 0);
            if (!arr.length) return '';
            return '<span class="substat-dots">' + arr.map(t =>
                `<span class="substat-dot substat-dot-${Math.min(4, Math.max(1, Number(t) + 1))}"></span>`).join('') + '</span>';
        }

        function tcBadges(data) {
            // swap / 元素共鳴のみ（聖遺物セットバッジはスプラッシュに被るため表示しない）
            const badges = [];
            if (data._isSwap) badges.push({ t: 'swap', oc: '#ffc14d', tc: '#ffd66e' });
            if (data.resonanceBadges && data.resonanceBadges.length) {
                data.resonanceBadges.forEach(b => badges.push({
                    t: b.text, oc: TC_ELEM[b.elem] || '#7fa8ff', tc: '#fff',
                    icon: b.elem ? assetUrl('static/assets/props/' + String(b.elem).toLowerCase() + '.png') : ''
                }));
            }
            return badges;
        }

        function tcSubIcon(sub) {
            // サブステ: %値なら *_per.png、flat値なら素のアイコンに正規化する
            let icon = sub.icon || '';
            const isPct = String(sub.value || '').includes('%');
            const m = icon.match(/(hp|atk|def)(_per)?\.png$/i);
            if (m) icon = icon.replace(/(hp|atk|def)(_per)?\.png$/i, `${m[1]}${isPct ? '_per' : ''}.png`);
            return icon;
        }

        function tcColumnHTML(data, idx) {
            if (data === null) {
                // 未選択メンバー: 追加ボタン列
                return `<div class="tc-col">
                    <div class="tc-cell tc-emptycol" data-tc-add="${idx}" title="クリックしてメンバーを追加">
                        <div class="text-5xl font-bold">＋</div>
                        <div class="text-sm mt-2 text-zinc-400">メンバーを追加</div>
                    </div>
                </div>`;
            }
            if (data && data.error) {
                // 取得失敗: 再試行列
                return `<div class="tc-col">
                    <div class="tc-cell tc-emptycol tc-errcol" data-tc-retry="${idx}" title="クリックして再試行">
                        <div class="text-red-300 text-sm px-8 text-center break-words">${esc(data.error)}</div>
                        <div class="mt-3 text-xs underline text-zinc-300">再試行</div>
                    </div>
                </div>`;
            }
            const ec = TC_ELEM[data.element] || TC_ELEM.None;
            const chips = tcBadges(data).map(x =>
                `<span style="border-color:${x.oc};color:${x.tc}">${x.icon ? `<img src="${esc(x.icon)}">` : ''}${esc(x.t)}</span>`).join('');
            const stats = (data.mainStats || []).map(s =>
                `<div class="tc-st"><img src="${assetUrl(s.icon)}"><span class="l">${esc(s.label)}</span><span class="v">${esc(s.val)}</span></div>`).join('');
            const sets = (data.setBonuses || []).filter(s => s.count).slice(0, 3).map(s =>
                `<div class="seti"><img src="${assetUrl(s.icon)}"><span class="cnt">${esc(s.count)}</span></div>`).join('');
            const arts = (data.artifacts || []).map(a => {
                if (!a) return '<div class="tc-cell tc-art empty">' + GenshinI18n.t('未装備') + '</div>';
                const subs = (a.substats || []).slice(0, 4).map(sub =>
                    `<div class="tc-sb"><img src="${assetUrl(tcSubIcon(sub))}">${tcDots(sub.rolls)}<span class="sv">${esc(sub.value)}</span></div>`).join('');
                return `<div class="tc-cell tc-art">
                    <div class="aicon"><img src="${assetUrl(a.icon)}"><span class="up">+${a.upgrade ?? 0}</span></div>
                    <div class="amain"><span class="mn">${esc(a.main ? a.main.name : '')}</span><span class="mv">${esc(a.main ? a.main.value : '')}</span></div>
                    <div class="subs">${subs}</div>
                    <div class="asc"><div class="lab">SCORE</div><div class="val">${(Number(a.score) || 0).toFixed(1)}</div><img src="/static/assets/tiers/${esc(a.tier || 'B')}.png"></div>
                </div>`;
            }).join('');
            const lvPart = data.level != null ? `Lv.${esc(data.level)}` : '';
            const friPart = data.friendship != null ? `${GenshinI18n.t('好感度')}${esc(data.friendship)}` : '';
            const weaponName = data.weaponName || GenshinI18n.t('未装備');
            const wsub = [data.weaponLevel != null ? `Lv.${data.weaponLevel}` : '', data.weaponType || ''].filter(Boolean).join(' ');
            return `<div class="tc-col">
                <div class="tc-cell topline tc-hd" style="border-color:${ec}">
                    <div class="r1"><span class="name">${esc(data.displayName)}</span><span class="cbadge" style="border-color:${ec}">${data.constellation != null ? 'C' + esc(data.constellation) : ''}</span></div>
                    <div class="r2"><span>${lvPart}</span><span>${friPart}</span></div>
                </div>
                <div class="tc-cell tc-splash"><img src="${esc(assetUrl(data.splash))}"><div class="chips">${chips}</div></div>
                <div class="tc-cell tc-stats">${stats}</div>
                <div class="tc-cell tc-weapon">
                    <div class="wicon" style="--tc-elem:${ec}">${data.weaponIcon ? `<img src="${esc(assetUrl(data.weaponIcon))}">` : ''}<span class="ref">R${data.weaponAffix != null ? esc(data.weaponAffix) : '1'}</span></div>
                    <div class="wtxt"><span class="wname">${esc(weaponName)}</span><span class="wsub">${esc(wsub)}</span></div>
                    <div class="sets">${sets}</div>
                </div>
                ${arts}
                <div class="tc-cell tc-total">
                    <div class="rank"><img src="/static/assets/tiers/${esc(data.tierSum || 'B')}.png"></div>
                    <div class="score">${(Number(data.scoreSum) || 0).toFixed(1)}</div>
                    <div class="calc"><div class="cl">${GenshinI18n.t('計算方法')}</div><div class="cv">${esc(data.calcMethodLabel || data.calcMethod || '')}</div></div>
                </div>
            </div>`;
        }

        function renderTeamCardHtml(datas) {
            htmlCardContainer.classList.remove('hidden');
            htmlCardContainer.innerHTML = `<div class="tc-wrap relative">
                <button type="button" id="tcRefreshBtn" title="編成カードを再読み込み"
                    class="absolute top-0 right-0 z-10 px-2.5 py-1.5 rounded-lg bg-zinc-900/85 border border-zinc-700 hover:border-cyan-500/70 text-zinc-300 hover:text-white text-xs font-semibold transition-all">
                    ⟳ 更新
                </button>
                <div class="tc-root">
                    <div class="tc-body">${datas.map((d, i) => tcColumnHTML(d, i)).join('')}</div>
                </div>
                ${getSavedSubstatDotsPref() ? tcLegendHTML() : ''}
            </div>`;
            // カード上の操作をバインド（閲覧モードではメンバー追加は不可・再試行と更新は可）
            htmlCardContainer.querySelectorAll('[data-tc-add]').forEach(el => {
                if (teamViewOnly) { el.classList.add('tc-readonly'); return; }
                el.addEventListener('click', () => openTeamCharPicker(Number(el.dataset.tcAdd)));
            });
            htmlCardContainer.querySelectorAll('[data-tc-retry]').forEach(el => {
                el.addEventListener('click', () => refreshTeamMember(Number(el.dataset.tcRetry)));
            });
            const rb = htmlCardContainer.querySelector('#tcRefreshBtn');
            if (typeof SHARE_PAYLOAD !== 'undefined' && SHARE_PAYLOAD) {
                if (rb) rb.remove();   // 共有閲覧では更新ボタンをカードに重ねない（編成再生成は不可のため）
            } else if (rb) {
                rb.addEventListener('click', () => generateActiveTeam());
            }
            // 画像生成カードと同じくコンテナ幅に合わせて等比縮小して表示する
            scaleTeamCardHtml();
            updateTeamCardHighlight();
            // 設定変更（ドット表示など）時に再フェッチなしで再描画できるよう保持する
            lastTeamHtmlRender = { datas };
        }

        // 伸び値ロールドットの凡例（単体カードと同じ配色）
        function tcLegendHTML() {
            const dots = [1, 2, 3, 4].map(nn => `<span class="substat-dot substat-dot-${nn}"></span>`).join('');
            return `<div class="flex items-center justify-end gap-2 px-1 pb-1 text-[12px] text-zinc-500">
                <span>${GenshinI18n.t('伸び値')}</span>
                <span class="flex">${dots}</span>
            </div>`;
        }

        // 選択中メンバーの列をハイライトする
        function updateTeamCardHighlight() {
            const cols = htmlCardContainer.querySelectorAll('.tc-body .tc-col');
            cols.forEach((cc, i) => cc.classList.toggle('active', teamMode && i === teamActiveIdx));
        }

        // メンバー並べ替えをカードへ反映（再取得なし）
        function reorderTeamCardColumns(from, to) {
            if (!lastTeamHtmlRender || !lastTeamHtmlRender.datas) return;
            const arr = lastTeamHtmlRender.datas;
            const [moved] = arr.splice(from, 1);
            arr.splice(to, 0, moved);
            renderTeamCardHtml(arr);
        }

        // 編成カードHTML(1860px設計)をコンテナ幅に合わせて縮小表示する
        // （ガラスカードの scaleGlassCard と同じ transform 方式。レスポンシブ追従は resize で）
        function scaleTeamCardHtml() {
            const root = htmlCardContainer.querySelector('.tc-root');
            if (!root) return;
            const box = document.getElementById('mainDisplayBox');
            const parentStyle = getComputedStyle(htmlCardContainer);
            const padX = (parseFloat(parentStyle.paddingLeft) || 0) + (parseFloat(parentStyle.paddingRight) || 0);
            const availableW = Math.max(280, htmlCardContainer.clientWidth - padX);
            const baseW = 1860;
            const h = root.offsetHeight || 1124;
            // 編成モード中はボックスの高さをビューポートに合わせる（ページスクロールを無くす）
            let availableH = 1e9;
            if (teamMode && box) {
                const boxTop = box.getBoundingClientRect().top;
                const boxH = Math.max(280, window.innerHeight - boxTop - 96); // 下部の余白/フッター分も引いて完全フィット
                box.style.height = boxH + 'px';
                box.style.minHeight = '0px';
                availableH = Math.max(220, boxH - 40); // 内側padding分を引く
            }
            const scale = Math.max(0.22, Math.min(1, availableW / baseW, availableH / h));
            root.style.transformOrigin = 'top left';
            root.style.transform = `scale(${scale})`;
            // transform はレイアウト領域を縮めないので、余白で見た目の占有サイズを合わせる
            root.style.marginRight = `${-(baseW * (1 - scale))}px`;
            root.style.marginBottom = `${-(h * (1 - scale))}px`;
        }

        window.addEventListener('resize', () => {
            if (teamMode) scaleTeamCardHtml();
        });

        async function generateTeamCardHtml(ids, configs) {
            setGenerationControlsDisabled(true);
            const token = ++latestTeamHtmlToken;
            htmlCardContainer.classList.remove('hidden');
            htmlCardContainer.innerHTML = '<div class="text-zinc-500 text-sm py-16 text-center">読み込み中...</div>';
            const glassEl = document.getElementById('glassCardContainer');
            if (glassEl) glassEl.classList.add('hidden');
            cardImage.classList.add('hidden');
            placeholderText.classList.add('hidden');
            if (imageAwaitBox) imageAwaitBox.classList.add('hidden');
            disclaimerFooter.classList.add('hidden');
            try {
                const results = await Promise.allSettled(ids.map(async (cid, i) => {
                    if (!cid) return null; // 空きメンバー
                    const cfg = (configs && configs[i]) || {};
                    return await fetchTeamMemberData(cid, cfg);
                }));
                if (token !== latestTeamHtmlToken) return;
                // 失敗した列はエラー表示（再試行ボタン付き）、null は追加ボタン列
                const payloads = results.map(r => (r.status === 'fulfilled') ? r.value : { error: (r.reason && r.reason.message) || 'データ取得に失敗しました' });
                renderTeamCardHtml(payloads);
            } catch (e) {
                if (token !== latestTeamHtmlToken) return;
                console.error(e);
                htmlCardContainer.innerHTML = `<div class="text-red-400 text-sm py-16 text-center">編成カードの読み込みに失敗しました: ${esc(e.message)}</div>`;
            } finally {
                if (token === latestTeamHtmlToken) setGenerationControlsDisabled(false);
            }
        }

        // 表示中のカードを現在の設定で再読み込みする（設定変更トグルから呼ばれる）
        function reloadCurrentCard() {
            // 共有閲覧モード: 設定変更は今の共有ビューにライブ反映する
            if (typeof SHARE_PAYLOAD !== 'undefined' && SHARE_PAYLOAD) {
                if (typeof shareLiveRefresh === 'function') shareLiveRefresh();
                return;
            }
            // 編成モード中は編成カード（HTML表示）を再描画する。単体カードは出さない
            if (teamMode) {
                if (lastTeamHtmlRender) {
                    renderTeamCardHtml(lastTeamHtmlRender.datas);
                } else {
                    const slot = teamSlotById(activeTeamId);
                    if (slot && slot.chars && slot.chars.length === 4) {
                        generateTeamCardHtml(slot.chars.slice(), buildTeamConfigs(slot.chars));
                    }
                }
                return;
            }
            if (!currentSelectedCharId) return;
            if (viewMode === 'glass') loadGlassCard(currentSelectedCharId);
            else if (viewMode === 'html') loadHtmlCard(currentSelectedCharId);
            else loadCardImage(currentSelectedCharId, true);
        }

        // ---- 編成カード画像生成 ----
        async function generateTeamCardImage(ids, configs, boss) {
            setGenerationControlsDisabled(true);
            try {
                const p = new URLSearchParams();
                p.set('uid', uid);
                p.set('char_ids', ids.join(','));
                if (configs && configs.length) p.set('configs', JSON.stringify(configs));
                if (boss && boss.version) p.set('boss', JSON.stringify(boss));
                if (ver === 'beta') p.set('beta', 'true');
                p.set('img_format', 'png');
                if (GenshinI18n.isEn()) p.set('lang', 'en');
                const signRes = await fetch(`/api/team_card_sign?${p.toString()}`, { cache: 'no-store' });
                if (!signRes.ok) {
                    const body = await signRes.json().catch(() => ({}));
                    throw new Error(body.detail || `署名の取得に失敗しました (status: ${signRes.status})`);
                }
                const signData = await signRes.json();
                // lang は署名対象外のため、署名済みURLに直接付与する
                let signedTeamUrl = signData.url;
                if (GenshinI18n.isEn()) signedTeamUrl += (signedTeamUrl.includes('?') ? '&' : '?') + 'lang=en';
                const url = signedTeamUrl;
                const saveMode = getSavedSaveMode();
                const fetchUrl = saveMode === 'server' ? (url + (url.includes('?') ? '&' : '?') + 'cache=server') : url;
                let blob = null;
                if (saveMode === 'client') blob = await getCachedCardImage(url);

                htmlCardContainer.classList.remove('hidden');
                htmlCardContainer.innerHTML = `
                    <div class="flex flex-col items-center justify-center gap-3 py-20">
                        <div class="spinner-primary w-10 h-10"></div>
                        <div class="text-sm text-zinc-500">${GenshinI18n.t('編成カードを生成中...')} <span id="genProgressText" class="text-zinc-400"></span></div>
                    </div>`;
                const glassEl = document.getElementById('glassCardContainer');
                if (glassEl) glassEl.classList.add('hidden');
                cardImage.classList.add('hidden');
                placeholderText.classList.add('hidden');
                if (imageAwaitBox) imageAwaitBox.classList.add('hidden');

                if (!blob) {
                    let res = await fetch(fetchUrl, { cache: 'no-store' });
                    if (res.status === 403) {
                        const retry = await fetch(`/api/team_card_sign?${p.toString()}`, { cache: 'no-store' });
                        const retryData = await retry.json();
                        let retrySigned = retryData.url;
                        if (GenshinI18n.isEn()) retrySigned += (retrySigned.includes('?') ? '&' : '?') + 'lang=en';
                        const retryUrl = saveMode === 'server' ? (retrySigned + (retrySigned.includes('?') ? '&' : '?') + 'cache=server') : retrySigned;
                        res = await fetch(retryUrl, { cache: 'no-store' });
                    }
                    if (!res.ok) {
                        const body = await res.json().catch(() => ({}));
                        throw new Error(body.detail || `編成カードの生成に失敗しました (status: ${res.status})`);
                    }
                    if (saveMode === 'client') putCachedCardImage(url, res.clone());
                    blob = await res.blob();
                }
                if (currentImageObjectUrl) URL.revokeObjectURL(currentImageObjectUrl);
                currentImageObjectUrl = URL.createObjectURL(blob);
                currentLoadedImageUrl = url;
                window.__lastCardBlob = blob; // シェア機能用に最新のカード画像 blob を保持
                cardImage.src = currentImageObjectUrl;
            } catch (e) {
                console.error(e);
                htmlCardContainer.innerHTML = `<div class="text-red-400 text-sm py-16 text-center">編成カード生成に失敗しました: ${esc(e.message)}</div>`;
            } finally {
                setGenerationControlsDisabled(false);
            }
        }
        function generateActiveTeam() {
            const slot = teamSlotById(activeTeamId);
            if (!slot) return;
            const ids = slot.chars.slice();
            // 編成カードは HTML 表示（画像生成は「シェア」経由の画像URLで従来通り可能）
            generateTeamCardHtml(ids, buildTeamConfigs(ids));
        }

        // ==========================================================
        //  編成共有（リンク / 画像1枚） と 設定引き継ぎ（合言葉）
        // ==========================================================
        function b64urlEncodeUtf8(str) {
            const bytes = new TextEncoder().encode(str);
            let bin = '';
            bytes.forEach(b => bin += String.fromCharCode(b));
            return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
        }
        // URLで壊れない hex+zlib ペイロード(CompressionStream対応ブラウザ前提)
        async function encodeSharePayload(obj) {
            const json = JSON.stringify(obj);
            let hex = '';
            try {
                const cs = new CompressionStream('deflate');
                const blob = await new Response(new Blob([json]).stream().pipeThrough(cs)).blob();
                const buf = new Uint8Array(await blob.arrayBuffer());
                hex = [...buf].map(b => b.toString(16).padStart(2, '0')).join('');
            } catch (e) {
                hex = b64urlEncodeUtf8(json); // フォールバック(旧形式)
            }
            return hex;
        }
        function b64urlDecodeUtf8(s) {
            s = String(s).replace(/-/g, '+').replace(/_/g, '/');
            while (s.length % 4) s += '=';
            const bin = atob(s);
            const bytes = new Uint8Array([...bin].map(c => c.charCodeAt(0)));
            return new TextDecoder().decode(bytes);
        }

        // ---- 編成共有モーダル（maxTeams=1: 1編成のみ / 3: 3編成=幽境） ----
        let teamShareSelection = new Set();
        let teamShareMax = 1;
        function openTeamShareModal(maxTeams = 1) {
            normalizeTeamSlots();
            teamShareMax = (Number(maxTeams) === 3) ? 3 : 1;
            teamShareSelection = new Set();
            const head = document.querySelector('#teamShareModal h2');
            if (head) head.textContent = teamShareMax === 3 ? '幽境モードで共有（3編成）' : '編成を共有';
            const hint = document.querySelector('#teamShareModal .text-xs.text-zinc-500');
            if (hint) hint.textContent = teamShareMax === 3
                ? '3つの編成を選択してください（幽境は3パーティ制・画像1枚で共有）'
                : '共有する編成を選択（1編成のみ）';
            const wrap = document.getElementById('teamShareSlotList');
            if (wrap) {
                wrap.innerHTML = '';
                teamSlots.forEach((slot, i) => {
                    if (!slot) return;
                    const count = slot.chars.filter(c => c).length;
                    const label = slotDisplayName(slot, i);
                    const row = document.createElement('label');
                    row.className = 'flex items-center gap-2.5 p-2 rounded-lg border border-zinc-700 hover:border-cyan-500/60 cursor-pointer text-sm transition-all';
                    row.innerHTML = `
                        <input type="checkbox" data-share-slot="${i}" class="w-4 h-4 accent-cyan-500">
                        <span class="flex-1 min-w-0 truncate">${esc(label)}</span>
                        <span class="text-[11px] text-zinc-500">${count}/4</span>
                    `;
                    wrap.appendChild(row);
                });
                wrap.querySelectorAll('[data-share-slot]').forEach(cb => {
                    cb.addEventListener('change', () => {
                        if (cb.checked) {
                            if (teamShareSelection.size >= teamShareMax) {
                                cb.checked = false;
                                showToast(teamShareMax === 3 ? '3編成まで選択できます' : '1編成のみ選択できます', 'info');
                                return;
                            }
                            teamShareSelection.add(Number(cb.dataset.shareSlot));
                        } else {
                            teamShareSelection.delete(Number(cb.dataset.shareSlot));
                        }
                        updateTeamShareButtons();
                    });
                });
            }
            updateTeamShareButtons();
            openModal(document.getElementById('teamShareModal'), closeTeamShareModal);
        }
        function closeTeamShareModal() {
            closeModal(document.getElementById('teamShareModal'));
        }
        function updateTeamShareButtons() {
            const ok = teamShareSelection.size === teamShareMax;
            const linkBtn = document.getElementById('teamShareLinkBtn');
            const imgBtn = document.getElementById('teamShareImageBtn');
            if (linkBtn) linkBtn.disabled = !ok;
            if (imgBtn) imgBtn.disabled = !ok;
        }
        function getSelectedShareTeams() {
            return [...teamShareSelection].sort((a, b) => a - b)
                .map(i => teamSlots[i])
                .filter(s => s && s.chars.filter(c => c).length)
                .map(s => ({ n: slotDisplayName(s, teamSlots.indexOf(s)) || '', c: s.chars.filter(c => c) }));
        }
        // 共有対象キャラの card_data を「作成時点」で1回だけ取得し freezing する。
        // base_prec は常に2（小数第2位）で焼き、閲覧側の「標準」はクライアントで四捨五入して表示する。
        // これにより共有リンクは作成時のスナップショット(json)のみを参照し、以後 enka/showcase は参照しない。
        async function collectShareCards(chars) {
            const cards = {};
            const list = [...new Set((chars || []).filter(Boolean).map(String))];
            await Promise.all(list.map(async cid => {
                const cfg = buildTeamConfigs([cid])[0] || {};
                const p = new URLSearchParams();
                p.set('calc_method', cfg.calc_method || 'crit');
                if (cfg.fake_char) p.set('fake_char', cfg.fake_char);
                if (cfg.fake_weapon) p.set('fake_weapon', cfg.fake_weapon);
                p.set('base_prec', '2');            // 常に小数第2位で保存
                if (ver === 'beta') p.set('beta', 'true');
                if (GenshinI18n.isEn()) p.set('lang', 'en');
                try {
                    const res = await fetch(`/api/card_data/${uid}/${cid}?${p.toString()}`, { cache: 'no-store' });
                    if (res.ok) cards[cid] = await res.json();
                } catch (e) {}
            }));
            return cards;
        }
        // サーバーにビルドスナップショット(2桁card_data)を保存し sid を得る。失敗時 null。
        async function createShareSnapshot(chars) {
            const list = [...new Set((chars || []).filter(Boolean).map(String))].slice(0, 12);
            if (!list.length || !/^\d+$/.test(String(uid))) return null;
            const body = {
                uid: String(uid), show_uid: false, beta: ver === 'beta',
                chars: list.map(cid => {
                    const cfg = buildTeamConfigs([cid])[0] || {};
                    return { cid, calc_method: cfg.calc_method || 'crit',
                        fake_char: cfg.fake_char || '', fake_weapon: cfg.fake_weapon || '', base_prec: '2' };
                }),
            };
            try {
                const res = await fetch('/api/share_snapshot', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
                });
                if (!res.ok) return null;
                const j = await res.json();
                return j.sid || null;
            } catch (e) { return null; }
        }
        async function fetchShareSnapshotCards(sid) {
            if (!sid) return null;
            try {
                const res = await fetch(`/api/share_data/${encodeURIComponent(sid)}`, { cache: 'no-store' });
                if (!res.ok) return null;
                const j = await res.json();
                const chars = (j.data || {}).chars || {};
                const cards = {}, cardsStd = {};
                for (const [cid, entry] of Object.entries(chars)) {
                    cards[cid] = (entry && entry.card) || entry;
                    if (entry && entry.card_std) cardsStd[cid] = entry.card_std;
                }
                return { cards, cardsStd };
            } catch (e) { return null; }
        }
        // 小数第2位で焼き込まれた card_data を「標準」表示(整数/第1位)へクライアント丸める。
        // サーバーの format_base_value(prec=0) / format_decimal_value(prec=0) を模倣。
        function _numCore(s) { return String(s).indexOf('%') >= 0; }
        function _roundStd(s) {   // format_decimal_value(prec=0): 第1位、整数なら小数なし、%保持
            let str = String(s), pct = _numCore(str);
            let body = pct ? str.replace(/%/g, '') : str;
            let n = parseFloat(body.replace(/,/g, ''));
            if (!isFinite(n)) return str;
            let r = Math.round(n * 10) / 10;   // 2桁文字列からの再丸めは素の半上げ(nudge無し)でサーバー直丸めと一致させる
            let out;
            if (Number.isInteger(r)) out = r.toLocaleString('en-US');
            else out = r.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
            return pct ? out + '%' : out;
        }
        function _roundInt(s) {   // format_base_value(prec=0): 整数、%は第1丸め扱い
            let str = String(s);
            if (_numCore(str)) return _roundStd(str);
            let n = parseFloat(str.replace(/,/g, ''));
            if (!isFinite(n)) return str;
            return Math.round(n + 1e-9).toLocaleString('en-US');
        }
        function standardizeCardData(card) {
            if (!card || typeof card !== 'object') return card;
            const c = JSON.parse(JSON.stringify(card));
            (c.mainStats || []).forEach(m => {
                if (m && typeof m === 'object') { ['val', 'base', 'add'].forEach(k => { if (typeof m[k] === 'string') m[k] = _roundInt(m[k]); }); }
            });
            (c.artifacts || []).forEach(a => (a && a.substats || []).forEach(s => {
                if (s && typeof s.value === 'string') s.value = _roundStd(s.value);
            }));
            return c;
        }
        function sharePlayerName() {
            const el = document.getElementById('currentName');
            return el ? String(el.textContent || '').trim().slice(0, 40) : '';
        }

        async function buildShareLink(teams, showUid, preCards) {
            // 第一候補: サーバスナップショット(sid)。URLを短く保ち、以後 enka を参照しない。
            let sid = null;
            try { sid = await createShareSnapshot(teams.flatMap(t => t.c || [])); } catch (e) {}
            let cards = {};
            if (!sid) cards = preCards || await collectShareCards(teams.flatMap(t => t.c || [])); // フォールバック: 従来自前埋め込み
            const payload = await encodeSharePayload({
                type: 'teams', uid: showUid ? uid : '', beta: ver === 'beta',
                pname: sharePlayerName(), sid: sid || '', cards: cards || {},
                teams: teams.map(t => ({ n: t.n, c: t.c })),
            });
            return `${location.origin}/share/${payload}`;
        }

        // URL に ?teams= があれば取り込む（起動時に1回）
        function importSharedTeamsFromUrl() {
            try {
                const params = new URLSearchParams(location.search);
                const raw = params.get('teams');
                if (!raw) return;
                const teams = JSON.parse(b64urlDecodeUtf8(raw));
                if (!Array.isArray(teams) || !teams.length || teams.length > 3) return;
                const uidHidden = teams.some(t => t && t.uidHidden);
                const clean = teams.map(t => ({
                    name: String((t && t.n) || '').slice(0, 24),
                    chars: (Array.isArray(t.c) ? t.c : []).slice(0, 4).map(c => String(c)),
                })).filter(t => t.chars.length);
                if (!clean.length) return;
                normalizeTeamSlots();
                const emptyIdx = [];
                for (let i = 0; i < 12; i++) if (!teamSlots[i]) emptyIdx.push(i);
                let placed = 0;
                clean.forEach(t => {
                    const idx = emptyIdx.length ? emptyIdx.shift() : placed;
                    teamSlots[idx] = { id: 'team_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6) + placed,
                                       chars: [...t.chars, null, null, null].slice(0, 4), name: t.name };
                    placed++;
                });
                saveTeamSlots();
                teamViewOnly = true; // 閲覧モードで開く
                sharedUidHidden = uidHidden;
                try { localStorage.setItem(TEAM_ACTIVE_KEY, teamSlots[placed - 1].id); } catch (e) {}
                history.replaceState(null, '', location.pathname + '?uid=' + encodeURIComponent(uid));
                showToast(`共有された${clean.length}編成を取り込みました（閲覧モード）`, 'success');
            } catch (e) { /* 不正なパラメータは無視 */ }
        }

        // ---- 幽境モード共有（バージョン→敵→3パーティ→難易度/時間/メモ） ----
        const ABYSS_DIFFICULTIES = [
            { value: 'master', label: 'マスター (Menacing)' },
            { value: 'extra', label: 'エクストラ (Fearless)' },
            { value: 'ultimate', label: 'アルティメット (Dire)' },
        ];
        const ABYSS_LINK_SERVICES = [
            { id: 'yt', input: 'abyssShareLinkYT', label: 'YouTube', host: /(?:^|\.)(?:youtube\.com|youtu\.be)$/i },
            { id: 'bili', input: 'abyssShareLinkBili', label: 'bilibili', host: /(?:^|\.)bilibili\.com$/i },
            { id: 'tw', input: 'abyssShareLinkTw', label: 'Twitter / X', host: /(?:^|\.)(?:twitter\.com|x\.com)$/i },
        ];
        let abyssShareState = { version: '', enemies: [], enemy: null, time: ['', '', ''], teams: [[null, null, null, null], [null, null, null, null], [null, null, null, null]] };
        let abyssShareLinks = []; // {svc:'yt'|'bili'|'tw', url}

        function openAbyssShareModal() {
            normalizeTeamSlots();
            abyssShareState = { version: '', enemies: [], enemy: null, time: ['', '', ''], teams: [[null, null, null, null], [null, null, null, null], [null, null, null, null]] };
            abyssShareLinks = [];
            let pendingDraft = null;
            const draftP = fetch(`/api/abyss_share_draft?uid=${encodeURIComponent(uid)}&beta=${ver === 'beta' ? 'true' : 'false'}`, { cache: 'no-store' })
                .then(r => r.ok ? r.json() : null).then(j => { if (j) pendingDraft = j.draft; }).catch(() => {});
            // バージョン選択肢をロード
            // JSON内の全 leyline 期間（begin昇順）からバージョン選択肢を作る
            fetch('/api/leyline_versions').then(r => r.json()).then(d => {
                const sel = document.getElementById('abyssShareVersion');
                if (!sel || !d.ok || !d.versions.length) return;
                Promise.all(d.versions.map(v => fetch(v.url).then(r => r.json()).catch(() => null))).then(all => {
                    const entries = [];
                    all.forEach(json => {
                        Object.entries(json || {}).forEach(([id, e]) => {
                            if (!e || !Array.isArray(e.enemies) || !e.enemies.length) return;
                            entries.push({ id: String(id), url: '', verLabel: abyssVersionFor(Number(id) || Number(id)), begin: e.begin || '', enemies: e.enemies });
                        });
                    });
                    entries.sort((a, b) => String(a.begin).localeCompare(String(b.begin)));
                    sel.innerHTML = '<option value="">— バージョンを選択 —</option>' +
                        entries.map(en => `<option value="${esc(en.id)}">${esc(en.verLabel)}</option>`).join('');
                    sel._abyssEntries = entries;
                    sel.onchange = () => {
                        const en = (sel._abyssEntries || []).find(x => x.id === sel.value);
                        if (en) loadAbyssShareEnemies(null, en);
                        else { abyssShareState.enemies = []; renderAbyssShareEnemies(); updateAbyssShareButtons(); }
                    };
                    // 前回の幽境共有下書きがあれば復元（サーバーキャッシュ・UID+β版単位）
                    Promise.resolve(draftP).then(() => { if (pendingDraft) applyAbyssShareDraft(pendingDraft); });
                });
            }).catch(() => {});
            renderAbyssShareEnemies();
            renderAbyssShareDifficulties();
            renderAbyssShareLinkChips();
            updateAbyssShareButtons();
            openModal(document.getElementById('abyssShareModal'), closeAbyssShareModal);
        }
        function closeAbyssShareModal() {
            closeModal(document.getElementById('abyssShareModal'));
        }
        // ---- 幽境共有の下書き（サーバーキャッシュ: uid+β版単位、リンク/画像作成成功時に保存） ----
        function applyAbyssShareDraft(d) {
            try {
                if (!d || typeof d !== 'object') return;
                const vSel = document.getElementById('abyssShareVersion');
                const dSel = document.getElementById('abyssShareDifficulty');
                const entries = (vSel && vSel._abyssEntries) || [];
                if (dSel && d.difficulty) dSel.value = String(d.difficulty);
                if (Array.isArray(d.time)) abyssShareState.time = d.time.slice(0, 3).concat(['', '', '']).slice(0, 3).map(t => String(t || ''));
                if (Array.isArray(d.teams)) abyssShareState.teams = d.teams.slice(0, 3).map(r => {
                    const a = (r || []).slice(0, 4).map(c => (c ? String(c) : null));
                    while (a.length < 4) a.push(null);
                    return a;
                });
                while (abyssShareState.teams.length < 3) abyssShareState.teams.push([null, null, null, null]);
                if (Array.isArray(d.links)) {
                    abyssShareLinks = d.links.map(l => ({ svc: String((l || {}).svc || ''), url: String((l || {}).url || '') })).filter(l => l.svc && /^https?:/.test(l.url));
                }
                const en = entries.find(x => String(x.id) === String(d.versionId || '')) || entries.find(x => x.verLabel === String(d.version || ''));
                if (en) {
                    if (vSel) vSel.value = en.id;
                    loadAbyssShareEnemies(null, en);
                } else if (Array.isArray(d.enemies) && d.enemies.length) {
                    abyssShareState.enemies = d.enemies.slice(0, 3).map(e => ({ name: String((e || {}).name || ''), img: String((e || {}).img || ''), leylineId: String((e || {}).leylineId || '') }));
                    renderAbyssShareBossRows();
                }
                renderAbyssShareLinkChips();
                updateAbyssShareButtons();
                showToast('前回の幽境共有（下書き）を復元しました', 'info');
            } catch (e) { /* 復元失敗は空モーダルで継続 */ }
        }
        function collectAbyssDraft() {
            const vSel = document.getElementById('abyssShareVersion');
            const dSel = document.getElementById('abyssShareDifficulty');
            return {
                versionId: vSel ? String(vSel.value || '') : '',
                version: (vSel && vSel.selectedOptions && vSel.selectedOptions[0] && vSel.selectedOptions[0].textContent) || '',
                difficulty: dSel ? String(dSel.value || '') : '',
                enemies: abyssShareState.enemies, time: abyssShareState.time, teams: abyssShareState.teams, links: abyssShareLinks,
            };
        }
        async function saveAbyssShareDraft() {
            try {
                await fetch('/api/abyss_share_draft', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ uid, beta: ver === 'beta', draft: collectAbyssDraft() }),
                });
            } catch (e) { /* 下書き保存失敗は共有自体を妨げない */ }
        }
        function renderAbyssShareDifficulties() {
            const sel = document.getElementById('abyssShareDifficulty');
            if (!sel) return;
            sel.innerHTML = '<option value="">—</option>' + ABYSS_DIFFICULTIES.map(d => `<option value="${esc(d.value)}">${esc(d.label)}</option>`).join('');
        }
        function renderAbyssShareEnemies() {
            const wrap = document.getElementById('abyssShareBossRows');
            if (!wrap) return;
            wrap.innerHTML = '<div class="text-xs text-zinc-500 py-2 text-center">バージョンを選択するとボス3体が表示されます</div>';
        }
        function selectAbyssShareEnemy() { /* 3ボス全表示方式では選択不要 */ }
        function renderAbyssShareBossRows() {
            const wrap = document.getElementById('abyssShareBossRows');
            if (!wrap) return;
            wrap.innerHTML = '';
            abyssShareState.enemies.forEach((boss, t) => {
                const row = document.createElement('div');
                row.className = 'p-2 rounded-xl border border-zinc-700 bg-zinc-800/50';
                const head = document.createElement('div');
                head.className = 'flex items-center gap-2 mb-1.5';
                head.innerHTML = `
                    ${boss.img ? `<img src="${esc(assetUrl(boss.img))}" class="w-10 h-10 rounded object-cover shrink-0" loading="lazy">` : ''}
                    <span class="min-w-0 flex-1"><span class="bn block truncate font-semibold text-zinc-200 text-sm">${esc(boss.name)}</span></span>
                    <button type="button" data-abyss-pick="${t}" class="px-2.5 py-1 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-bold transition-all shrink-0">パーティを選択</button>
                    <input type="text" data-abyss-time="${t}" placeholder="0:00" maxlength="5" value="${esc(abyssShareState.time[t] || '')}"
                        class="w-16 bg-zinc-900/80 border border-zinc-700 focus:border-cyan-500 rounded-lg px-2 py-1 text-xs text-white placeholder-zinc-600 outline-none text-center shrink-0">`;
                row.appendChild(head);
                const grid = document.createElement('div');
                grid.className = 'flex gap-2';
                for (let m = 0; m < 4; m++) {
                    const slot = document.createElement('button');
                    slot.type = 'button';
                    slot.dataset.abyssTeam = String(t);
                    slot.dataset.abyssMember = String(m);
                    slot.className = 'relative w-12 h-12 rounded-lg border-2 border-dashed border-zinc-600 hover:border-cyan-400 flex items-center justify-center text-zinc-500 hover:text-cyan-300 text-lg transition-all overflow-hidden';
                    slot.innerHTML = '＋';
                    slot.onclick = () => openAbyssShareMemberPicker(t, m);
                    grid.appendChild(slot);
                }
                row.appendChild(grid);
                const timeInput = head.querySelector('[data-abyss-time]');
                timeInput.oninput = () => {
                    let v = timeInput.value.replace(/[^0-9:]/g, '');
                    const parts = v.split(':');
                    let mnt = parseInt(parts[0] || '0', 10);
                    let sec = parseInt(parts[1] || '0', 10) || 0;
                    if (parts.length === 1 && v.length > 2) { mnt = parseInt(v.slice(0, -2) || '0', 10); sec = parseInt(v.slice(-2), 10) || 0; }
                    if (sec > 59) sec = 59;
                    if (mnt > 2 || (mnt === 2 && sec > 0)) { mnt = 2; sec = 0; }
                    const corrected = `${mnt}:${String(sec).padStart(2, '0')}`;
                    if (timeInput.value !== corrected) timeInput.value = corrected;
                    abyssShareState.time[t] = corrected;
                };
                wrap.appendChild(row);
            });
            // 既存の選択状態を反映
            abyssShareState.enemies.forEach((boss, t) => refreshAbyssShareTeamTiles(t));
        }
        async function loadAbyssShareEnemies(_url, entry) {
            const wrap = document.getElementById('abyssShareBossRows');
            if (!wrap) return;
            abyssShareState.enemies = [];
            abyssShareState.enemy = null;
            wrap.innerHTML = '<div class="text-xs text-zinc-500 py-2 text-center">読み込み中...</div>';
            try {
                // 選択された leyline 期間の敵（通常/上級/ボス の並び）から上3体を表示
                const enemies = (entry.enemies || []).map(e => ({
                    name: e.name || '', img: e.img || '', leylineId: entry.id, verLabel: entry.verLabel,
                }));
                abyssShareState.enemies = enemies.slice(0, 3);
                wrap.innerHTML = '';
                if (!abyssShareState.enemies.length) { wrap.innerHTML = '<div class="text-xs text-zinc-500 py-2 text-center">このバージョンのボスデータがありません</div>'; updateAbyssShareButtons(); return; }
                renderAbyssShareBossRows();
                updateAbyssShareButtons();
            } catch (e) {
                wrap.innerHTML = '<div class="text-xs text-red-400 py-2 text-center">敵データの読み込みに失敗しました</div>';
            }
        }
        // パーティ選択ボタン → 既存編成選択モーダル(ボタン式)
        function openAbyssTeamPickModal(t) {
            const grid = document.getElementById('abyssTeamPickList');
            if (grid) {
                grid.innerHTML = '';
                const filled = teamSlots.map((s, i) => ({ s, i })).filter(x => x.s && x.s.chars.filter(c => c).length);
                if (!filled.length) {
                    grid.innerHTML = '<div class="text-xs text-zinc-500 py-3 text-center">保存済みの編成がありません（＋で個別に追加してください）</div>';
                }
                filled.forEach(({ s, i }) => {
                    const cnt = s.chars.filter(c => c).length;
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'w-full flex items-center gap-2.5 p-2 rounded-lg border border-zinc-700 hover:border-cyan-500/60 text-left transition-all';
                    btn.innerHTML = `
                        <div class="flex gap-1 shrink-0">${s.chars.map(c => {
                            const src2 = c ? teamCharIconSrc(c) : '';
                            return `<div class="w-7 h-9 rounded overflow-hidden bg-zinc-800">${src2 ? `<img src="${esc(src2)}" class="w-full h-full object-cover">` : '<div class="w-full h-full border border-dashed border-zinc-600 rounded"></div>'}</div>`;
                        }).join('')}</div>
                        <span class="flex-1 min-w-0 truncate text-sm text-zinc-200">${esc(slotDisplayName(s, i))}</span>
                        <span class="text-[11px] ${cnt === 4 ? 'text-cyan-400' : 'text-zinc-500'} shrink-0">${cnt}/4</span>`;
                    btn.onclick = () => {
                        abyssShareState.teams[t] = [...s.chars];
                        refreshAbyssShareTeamTiles(t);
                        updateAbyssShareButtons();
                        closeAbyssTeamPickModal();
                    };
                    grid.appendChild(btn);
                });
            }
            const title = document.getElementById('abyssTeamPickTitle');
            if (title) title.textContent = `パーティ${t + 1} の編成を選択`;
            openModal(document.getElementById('abyssTeamPickModal'), closeAbyssTeamPickModal);
        }
        function closeAbyssTeamPickModal() {
            closeModal(document.getElementById('abyssTeamPickModal'));
        }

        function refreshAbyssShareTeamTiles(t) {
            for (let m = 0; m < 4; m++) {
                const slot = document.querySelector(`[data-abyss-team="${t}"][data-abyss-member="${m}"]`);
                if (!slot) continue;
                const cid = abyssShareState.teams[t][m];
                if (cid) {
                    const src = teamCharIconSrc(cid);
                    slot.className = 'relative w-12 h-12 rounded-lg border-2 border-zinc-600 flex items-center justify-center overflow-hidden';
                    slot.innerHTML = src ? `<img src="${esc(src)}" class="w-full h-full object-cover">` : `<span class="text-[10px] text-zinc-500">?</span>`;
                } else {
                    slot.className = 'relative w-12 h-12 rounded-lg border-2 border-dashed border-zinc-600 hover:border-cyan-400 flex items-center justify-center text-zinc-500 hover:text-cyan-300 text-lg transition-all overflow-hidden';
                    slot.innerHTML = '＋';
                }
            }
        }
        let abyssPickerTarget = null;
        function openAbyssShareMemberPicker(t, m) {
            abyssPickerTarget = [t, m];
            const grid = document.getElementById('teamCharPickerGrid');
            if (grid) {
                grid.innerHTML = '';
                showcaseCharList.forEach(ch => {
                    const cid = String(ch.id);
                    if (!ch.icon) return;
                    const tile = document.createElement('div');
                    tile.className = 'team-select-tile relative w-20 h-20 shrink-0 rounded-lg border-2 overflow-hidden cursor-pointer border-zinc-700 hover:border-cyan-400';
                    tile.innerHTML = `<img src="${esc(ch.icon)}" class="w-full h-full object-cover drag-none" loading="lazy">`;
                    tile.onclick = () => applyAbyssShareMember(cid);
                    grid.appendChild(tile);
                });
            }
            const title = document.getElementById('teamCharPickerTitle');
            if (title) title.textContent = `パーティ${t + 1} のメンバー${m + 1}`;
            const removeBtn = document.getElementById('teamCharPickerRemoveBtn');
            if (removeBtn) removeBtn.classList.add('hidden');
            openModal(document.getElementById('teamCharPickerModal'), closeTeamCharPicker);
        }
        function applyAbyssShareMember(cid) {
            if (!abyssPickerTarget) { closeTeamCharPicker(); return; }
            const [t, m] = abyssPickerTarget;
            abyssShareState.teams[t][m] = cid;
            closeTeamCharPicker();
            // タイルにアイコン反映
            const slot = document.querySelector(`[data-abyss-team="${t}"][data-abyss-member="${m}"]`);
            if (slot) {
                const src = teamCharIconSrc(cid);
                slot.className = 'relative w-12 h-12 rounded-lg border-2 border-zinc-600 flex items-center justify-center overflow-hidden';
                slot.innerHTML = src ? `<img src="${esc(src)}" class="w-full h-full object-cover">` : `<span class="text-[10px] text-zinc-500">?</span>`;
            }
            updateAbyssShareButtons();
        }
        function removeAbyssShareMember() {
            if (!abyssPickerTarget) { closeTeamCharPicker(); return; }
            const [t, m] = abyssPickerTarget;
            abyssShareState.teams[t][m] = null;
            closeTeamCharPicker();
            const slot = document.querySelector(`[data-abyss-team="${t}"][data-abyss-member="${m}"]`);
            if (slot) {
                slot.className = 'relative w-12 h-12 rounded-lg border-2 border-dashed border-zinc-600 hover:border-cyan-400 flex items-center justify-center text-zinc-500 hover:text-cyan-300 text-lg transition-all';
                slot.innerHTML = '＋';
            }
            updateAbyssShareButtons();
        }
        function detectLinkService(url) {
            try {
                const u = new URL(url);
                if (!u.protocol.startsWith('http')) return null;
                const host = u.hostname.toLowerCase();
                if (/(^|\.)youtube\.com$/.test(host) || /(^|\.)youtu\.be$/.test(host)) return 'yt';
                if (/(^|\.)bilibili\.com$/.test(host)) return 'bili';
                if (/(^|\.)twitter\.com$/.test(host) || /(^|\.)x\.com$/.test(host)) return 'tw';
            } catch (e) {}
            return null;
        }
        function renderAbyssShareLinkChips() {
            const wrap = document.getElementById('abyssShareLinkChips');
            if (!wrap) return;
            const LABELS = { yt: 'YouTube', bili: 'bilibili', tw: 'Twitter / X' };
            wrap.innerHTML = abyssShareLinks.map((l, i) =>
                `<span class="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg bg-zinc-800 border border-zinc-700 text-xs text-zinc-300">
                    <span class="font-semibold text-cyan-400">${esc(LABELS[l.svc] || l.svc)}</span>
                    <button type="button" data-abyss-link-del="${i}" class="text-zinc-500 hover:text-red-300 leading-none">×</button>
                </span>`).join('');
            wrap.querySelectorAll('[data-abyss-link-del]').forEach(b => {
                b.onclick = () => { abyssShareLinks.splice(Number(b.dataset.abyssLinkDel), 1); renderAbyssShareLinkChips(); };
            });
        }
        function collectAbyssShare() {
            const showUid = !!document.getElementById('abyssShareUidChk')?.checked;
            const links = {};
            abyssShareLinks.forEach(l => { links[l.svc] = l.url; });
            return {
                type: 'abyss',
                uid: showUid ? uid : '',
                version: document.getElementById('abyssShareVersion')?.selectedOptions[0]?.textContent || '',
                difficulty: document.getElementById('abyssShareDifficulty')?.value || '',
                links,
                bosses: abyssShareState.enemies.map((boss, t) => ({
                    name: boss.name, img: boss.img, leylineId: boss.leylineId,
                    chars: (abyssShareState.teams[t] || []).filter(c => c),
                    time: (abyssShareState.time || [])[t] || '',
                })),
            };
        }
        async function buildAbyssShareLink(data, preCards) {
            // 第一候補: サーバスナップショット(sid)。失敗時のみ cards 埋め込み(従来自前)。
            let sid = null;
            try { sid = await createShareSnapshot((data.bosses || []).flatMap(b => b.chars || [])); } catch (e) {}
            let cards = {};
            if (!sid) cards = preCards || await collectShareCards((data.bosses || []).flatMap(b => b.chars || []));
            const payload = await encodeSharePayload({ ...data, beta: ver === 'beta', pname: sharePlayerName(), sid: sid || '', cards: cards || {} });
            return `${location.origin}/share/${payload}`;
        }
        function updateAbyssShareButtons() {
            const hasEnemy = abyssShareState.enemies.length === 3;
            const hasChar = abyssShareState.teams.some(t => t.some(c => c));
            const ok = hasEnemy && hasChar;
            const linkBtn = document.getElementById('abyssShareLinkBtn');
            const imgBtn = document.getElementById('abyssShareImageBtn');
            if (linkBtn) linkBtn.disabled = !ok;
            if (imgBtn) imgBtn.disabled = !ok;
        }

        // ==========================================================
        //  共有閲覧モード (/share/ → build_card.html 再利用)
        //  topbar表示 / ガラスカード / 編成カード・幽境キャンバス
        // ==========================================================
        let shareState = { payload: null, cards: {}, layout: {}, view: null };

        async function shareDecodeAsync(data) {
            const s = String(data || '').trim();
            if (!s) return null;
            if (/^[0-9a-fA-F]+$/.test(s)) {
                try {
                    const bytes = new Uint8Array(s.match(/.{2}/g).map(h => parseInt(h, 16)));
                    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate'));
                    const buf = new Uint8Array(await new Response(stream).arrayBuffer());
                    return JSON.parse(new TextDecoder().decode(buf));
                } catch (e) { return null; }
            }
            try {
                let t = s.replace(/-/g, '+').replace(/_/g, '/');
                while (t.length % 4) t += '=';
                return JSON.parse(new TextDecoder().decode(new Uint8Array([...atob(t)].map(c => c.charCodeAt(0)))));
            } catch (e) { return null; }
        }

        async function enterShareMode() {
            const payload = await shareDecodeAsync(SHARE_PAYLOAD);
            if (!payload || (payload.type !== 'abyss' && payload.type !== 'teams')) {
                const ph = document.getElementById('placeholderText');
                if (ph) { ph.textContent = '共有データが見つかりません'; ph.classList.remove('hidden'); }
                return;
            }
            shareState.payload = payload;
            // データ本体: sid があればサーバーのスナップショット(json)のみを参照(enka/showcase は見ない)。
            // 旧リンク(埋め込み cards)はそのままフォールバック。スナップショットには標準表示版(card_std)も凍結済み。
            let cardsRaw = payload.cards && Object.keys(payload.cards).length ? payload.cards : {};
            let cardsStdRaw = null;
            if (payload.sid) {
                const snap = await fetchShareSnapshotCards(payload.sid);
                if (snap) {
                    cardsRaw = Object.assign({}, snap.cards, cardsRaw);
                    if (snap.cardsStd && Object.keys(snap.cardsStd).length) cardsStdRaw = snap.cardsStd;
                }
            }
            shareState.cardsRaw = cardsRaw;
            shareState.cardsStdRaw = cardsStdRaw;
            applySharePrecision();
            if (payload.type === 'abyss') {
                try {
                    const res = await fetch('/api/abyss_share_layout', { cache: 'no-store' });
                    if (res.ok) shareState.layout = (await res.json()).layout || {};
                } catch (e) {}
            }
            applyShareModeChrome();
            renderShareHome();
        }
        // 閲覧者の精度設定で表示カードを決める: 小数第2位=凍結そのまま / 標準=サーバー焼成版(無ければクライアント丸め)。再取得しない。
        function applySharePrecision() {
            const raw = shareState.cardsRaw || {};
            const std = shareState.cardsStdRaw;
            if (getSavedBasePrec() === '2') {
                shareState.cards = raw;
            } else if (std) {
                const out = {};
                for (const [cid, card] of Object.entries(raw)) out[cid] = std[cid] || standardizeCardData(card);
                shareState.cards = out;
            } else {
                const out = {};
                for (const [cid, card] of Object.entries(raw)) out[cid] = standardizeCardData(card);
                shareState.cards = out;
            }
        }

        // 共有モードのUI: 編成モードバー/武器/UIDトグル/差し替えを隠す
        function applyShareModeChrome() {
            if (teamModeBar) teamModeBar.classList.add('hidden');
            if (teamCardBtn) teamCardBtn.classList.add('hidden');
            // 既存実装同様、設定は左カラムに出す（team-single は使わない）
            const weaponSec = document.getElementById('sidebarCharsBtn');
            if (weaponSec) { const sec = weaponSec.closest('div.rounded-xl'); if (sec) sec.classList.add('hidden'); }
            const swap = document.getElementById('sidebarRight');
            if (swap) swap.classList.add('hidden');
            const uidBtn = document.getElementById('settingsShowUidBtn');
            if (uidBtn) { const row = uidBtn.closest('div'); if (row) row.classList.add('hidden'); }
            const growth = document.getElementById('growthToggleWrap');
            if (growth) growth.classList.add('hidden');
            const vmt = document.getElementById('viewModeToggle');
            if (vmt) vmt.classList.add('hidden');
            const cab = document.getElementById('cardActionBtns');
            if (cab) { cab.classList.add('hidden'); cab.style.display = 'none'; }
            // 背景設定を隠す（表示方法セクション自体は左に残す）
            if (typeof bgColorControls !== 'undefined' && bgColorControls) bgColorControls.classList.add('hidden');
            // 計算方法を隠す（共有データは作成時スナップショット固定。変更にはenka再取得が必要になるため）
            if (scoreCalcSelect) {
                const calcRow = scoreCalcSelect.closest('div');
                if (calcRow) calcRow.classList.add('hidden');
            }
            const sel = document.getElementById('selectedCharLabel');
            if (sel) sel.textContent = shareState.payload.type === 'abyss' ? '幽境の激戦 編成' : '編成共有';
            renderShareLinkIcons();
        }
        // 選択中ボックス右端に確認用リンクのブランドアイコン（押すと別タブでリンク先へ）
        function renderShareLinkIcons() {
            const old = document.getElementById('shareLinkIcons');
            if (old) old.remove();
            const links = (shareState.payload && shareState.payload.links) || {};
            const keys = Object.keys(links).filter(k => /^https?:/.test(String(links[k])));
            if (!keys.length) return;
            const sel = document.getElementById('selectedCharLabel');
            if (!sel || !sel.parentElement) return;
            const ICONS = { yt: ['/static/assets/brand/youtube.svg?v=0', 'YouTube'],
                            bili: ['/static/assets/brand/bilibili.svg?v=0', 'bilibili'],
                            tw: ['/static/assets/brand/twitter.svg?v=0', 'Twitter / X'] };
            const holder = document.createElement('div');
            holder.id = 'shareLinkIcons';
            holder.className = 'ml-auto flex items-center gap-2 shrink-0';
            holder.innerHTML = keys.map(k => {
                const ic = ICONS[k] || ['/static/assets/props/rate.webp', k];
                return `<a href="${esc(String(links[k]))}" target="_blank" rel="noopener" title="${esc(ic[1])}"
                    class="w-7 h-7 flex items-center justify-center rounded-lg bg-zinc-800/80 border border-zinc-700 hover:border-cyan-400 transition-all"><img src="${ic[0]}" alt="${esc(ic[1])}" class="w-4 h-4 pointer-events-none"></a>`;
            }).join('');
            sel.parentElement.appendChild(holder);
        }
        // 共有モードではカード上の「⟳ 更新」ボタンを隠す
        function hideShareRefreshBtn() {
            const rb = document.getElementById('tcRefreshBtn');
            if (rb) rb.style.display = 'none';
        }

        // 共有モード: 伸び値ドット/表示精度などの設定変更を今の表示にライブ反映
        function shareLiveRefresh() {
            if (!shareState.payload) return;
            if (shareState.view === 'glass' && shareState.glassCid != null) { showShareGlassCard(shareState.glassCid); return; }
            if (shareState.view === 'party' && shareState.payload.type === 'abyss') { showSharePartyCard(shareState.selIdx || 0); return; }
            renderShareHome();
        }

        // 共有ホーム(編成カード or 幽境キャンバス)を描画
        function renderShareHome() {
            const p = shareState.payload;
            shareState.view = p.type;
            htmlCardContainer.classList.remove('hidden');
            glassCardContainer.classList.add('hidden');
            cardImage.classList.add('hidden');
            const ph = document.getElementById('placeholderText');
            if (ph) ph.classList.add('hidden');
            const back = document.getElementById('shareBackBtn');
            if (back) back.style.display = 'none';
            requestAnimationFrame(hideShareRefreshBtn);
            if (p.type === 'teams') {
                const team = (p.teams || [])[0] || { c: [] };
                const datas = team.c.map(cid => shareState.cards[String(cid)] || null);
                renderTeamCardHtml(datas);
                // 各メンバー列クリックでガラスカード
                const cols = htmlCardContainer.querySelectorAll('.tc-body .tc-col');
                cols.forEach((col, i) => {
                    const cid = team.c[i];
                    if (cid && shareState.cards[String(cid)]) {
                        col.style.cursor = 'pointer';
                        col.title = 'クリックでビルドカードを表示';
                        col.addEventListener('click', () => { shareState.selIdx = i; showShareGlassCard(cid); });
                    }
                });
            } else {
                renderShareAbyssCanvas();
            }
        }

        // 幽境キャンバス: baseimg 868x560 + adminレイアウト絶対配置(画像と同一式)
        function renderShareAbyssCanvas() {
            shareState.view = 'abyss'; // 戻るボタン経由の再描画でも party/glass を指したままにしない(設定ライブ反映の飛先に影響)
            const p = shareState.payload, L = shareState.layout || {};
            const rowH = Number(L.rowHeight ?? 120);
            const rowGap = Number(L.rowGap ?? 14);
            const rowX = Number((L.rowPadding ?? {}).x ?? 18);
            const rowY0 = Number((L.rowPadding ?? {}).y ?? 14);
            const bi = L.bossIcon ?? {}, nm = L.bossName ?? {}, tm = L.clearTime ?? {}, ci = L.charIcon ?? {}, hh = L.header ?? {};
            const biSz = Number(bi.size ?? 64), ciSz = Number(ci.size ?? 60), ciGap = Number(ci.gap ?? 10);
            const DIFF = { master: 'マスター', extra: 'エクストラ', ultimate: 'アルティメット' };
            const LINK = { yt: 'YouTube', bili: 'bilibili', tw: 'Twitter / X' };
            const diffLabel = DIFF[p.difficulty] || p.difficulty || '';
            // 文字色: アルティメットは #FC613F、それ以外は #EBE0DA
            const headColor = p.difficulty === 'ultimate' ? '#FC613F' : '#EBE0DA';
            const bosses = p.bosses || [];
            const parts = [];

            const hhSz = Number(hh.size ?? 34);
            // Ver と難易度を1つの flex 行にまとめて底揃え
            const headerRow = `<div class="abs" style="left:${hh.x ?? 24}px;right:24px;top:${hh.y ?? 24}px;display:flex;align-items:flex-end;justify-content:space-between">` +
                `<span style="font-size:${Math.max(20, hhSz - 8)}px;font-weight:800;letter-spacing:.04em;line-height:1;color:${headColor};text-shadow:0 2px 8px rgba(0,0,0,.85)">Ver ${esc(p.version || '')}</span>` +
                (diffLabel ? `<span style="font-size:24px;font-weight:800;line-height:1;color:${headColor};text-shadow:0 2px 8px rgba(0,0,0,.85)">${esc(diffLabel)}</span>` : '') +
                `</div>`;
            parts.push(headerRow);
            const lk = Object.keys(p.links || {});
            if (lk.length) parts.push(`<div class="abs" style="left:${hh.x ?? 24}px;top:${(hh.y ?? 24) + 48}px;display:flex;gap:14px;font-size:14px">` +
                lk.map(k => `<a href="${esc(p.links[k])}" target="_blank" rel="noopener" style="color:var(--cyan);text-decoration:none;background:rgba(8,20,26,.6);border:1px solid rgba(34,211,238,.35);padding:3px 10px;border-radius:8px">▶ ${esc(LINK[k] || k)}</a>`).join('') + `</div>`);
            // {名前} : {UID} タグ(adminの playerTag で切替・UID非公開共有は名前のみ)
            // {名前} : {UID} タグ（admin の playerTag {x,y,size,show} で位置調整・UID非公開は名前のみ）
            const ptRaw = L.playerTag, ptShow = (typeof ptRaw === 'boolean') ? ptRaw : (ptRaw && ptRaw.show !== false) || ptRaw == null;
            if (ptShow) {
                const ptX = (ptRaw && ptRaw.x != null) ? Number(ptRaw.x) : 846, ptY = (ptRaw && ptRaw.y != null) ? Number(ptRaw.y) : 526, ptSz = (ptRaw && ptRaw.size != null) ? Number(ptRaw.size) : 15;
                const ptAl = (ptRaw && ptRaw.align) || 'right';
                const tagTxt = p.pname && p.uid ? `${p.pname} : ${p.uid}` : (p.uid ? `UID ${p.uid}` : (p.pname || ''));
                const posCss = ptAl === 'right' ? `right:${868 - ptX}px` : `left:${ptX}px`;
                if (tagTxt) parts.push(`<div class="abs" style="${posCss};top:${ptY}px;font-size:${ptSz}px;font-weight:700;color:rgba(255,255,255,.78);text-shadow:0 1px 4px rgba(0,0,0,.85);white-space:nowrap">${esc(tagTxt)}</div>`);
            }
            bosses.forEach((b, i) => {
                const by = rowY0 + i * rowH;
                // ボスアイコン(円形)
                if (b.img) {
                    const bIY = bi.y ? by + Number(bi.y) : by + Math.max(0, (rowH - rowGap - biSz) / 2);
                    parts.push(`<img class="abs" src="${esc(b.img)}" style="left:${rowX + Number(bi.x ?? 20)}px;top:${bIY}px;width:${biSz}px;height:${biSz}px;object-fit:cover;border-radius:50%;box-shadow:0 0 0 2px #8974CB">`);
                }
                const nameX = rowX + Number(nm.x ?? 96);
                const nameY = by + Number(nm.y ?? 14);
                const nameSz = Number(nm.size ?? 21);
                // ボス名(1行・改行なし・「（通常状態）」除去)
                const cleanName = String(b.name || '').replace(/[（(][^）)]*通常状態[）)]/g, '').trim();
                parts.push(`<div class="abs" style="left:${nameX}px;top:${nameY}px;font-size:${nameSz}px;font-weight:800;color:${headColor};white-space:nowrap;line-height:1.1;text-shadow:0 2px 6px rgba(0,0,0,.9)">${esc(cleanName)}</div>`);
                // タイム(名前の直下・必ず見える)
                if (b.time) {
                    const tSz = Number(tm.size ?? 16);
                    const tX = rowX + Number(tm.x ?? 96);
                    const tY = by + Number(tm.y ?? 44);
                    parts.push(`<div class="abs" style="left:${tX}px;top:${tY}px;font-size:${tSz}px;font-weight:800;color:#E6C04A;text-shadow:0 2px 6px rgba(0,0,0,.9);z-index:3">${esc(b.time)}</div>`);
                }
                // キャラメンバー(右下=凸数、左下=武器アイコン)
                const chars = b.chars || [];
                if (chars.length) {
                    let mx;
                    if ((ci.align || 'right') === 'right') mx = 868 - rowX - 24 - chars.length * (ciSz + ciGap) + ciGap + Number(ci.x ?? 0);
                    else mx = rowX + Number(ci.x ?? 0);
                    const my = ci.y ? by + Number(ci.y) : by + Math.max(0, (rowH - rowGap - ciSz) / 2);
                    const memHtml = chars.map(cid => {
                        const cd = shareState.cards[String(cid)] || {};
                        let icon = (b.icons || {})[String(cid)] || cd.charIcon || '';
                        if (icon && !/^\//.test(icon) && !/^https?:/.test(icon)) icon = '/' + icon.replace(/^\.\//, '');
                        const inner = icon ? `<span class="m-img"><img src="${esc(icon)}" loading="lazy"></span>` : `<span class="m-img"><span style="height:100%;display:flex;align-items:center;justify-content:center;color:var(--sub);font-size:9px">?</span></span>`;
                        const cons = (cd.constellation != null) ? `C${cd.constellation}` : '';
                        let wIcon = cd.weaponIcon || '';
                        if (wIcon && !/^\//.test(wIcon) && !/^https?:/.test(wIcon)) wIcon = '/' + wIcon.replace(/^\.\//, '');
                        const badge = `<span class="cons-badge">${esc(cons)}</span>`;
                        const wbadge = wIcon ? `<img class="wpn-badge" src="${esc(wIcon)}" alt="">` : '';
                        return `<div class="m${shareState.cards[cid] ? '' : ' empty'}" style="width:${ciSz}px;height:${ciSz}px">${shareState.cards[cid] ? `<button data-share-cid="${esc(cid)}" data-share-boss="${i}" title="クリックで編成カードを表示">${inner}${wbadge}${badge}</button>` : `${inner}${wbadge}${badge}`}</div>`;
                    }).join('');
                    parts.push(`<div class="mem-abs" style="left:${Math.max(0, mx)}px;top:${my}px;gap:${ciGap}px">${memHtml}</div>`);
                }
            });
            htmlCardContainer.innerHTML = `<div class="share-abyss-wrap"><div class="abyss-canvas" id="shareAbyssCanvas">${parts.join('')}</div></div>`;
            scaleShareAbyss();
            bindShareCardClicks(htmlCardContainer);
        }

        // 幽境: キャラクリック → そのボスの「編成カード」を表示
        function showSharePartyCard(bossIdx) {
            const p = shareState.payload;
            const boss = (p.bosses || [])[bossIdx];
            if (!boss) return;
            shareState.view = 'party';
            const datas = (boss.chars || []).map(cid => shareState.cards[String(cid)] || null);
            // 編成カードを描画(これが htmlCardContainer を満たす)
            renderTeamCardHtml(datas);
            // ヘッダー(戻るボタン+ボス名)を先頭に挿入
            const head = document.createElement('div');
            head.className = 'share-party-head';
            head.innerHTML = `<button type="button" id="sharePartyBack" class="px-3 py-1.5 rounded-lg bg-zinc-800/90 border border-zinc-600 text-sm text-white hover:bg-zinc-700">← 幽境画面に戻る</button><span class="text-sm font-bold ml-3" style="color:var(--rose)">${esc(boss.name || '')}</span>`;
            htmlCardContainer.insertBefore(head, htmlCardContainer.firstChild);
            document.getElementById('sharePartyBack').addEventListener('click', () => { renderShareAbyssCanvas(); });
        }

        // 幽境キャンバスをコンテナ幅に等比縮小(ビルドカードと同じ挙動)
        function scaleShareAbyss() {
            const wrap = htmlCardContainer.querySelector('.share-abyss-wrap');
            const canvas = htmlCardContainer.querySelector('.abyss-canvas');
            if (!wrap || !canvas) return;
            const available = Math.max(280, htmlCardContainer.clientWidth);
            const scale = Math.min(1, available / 868);
            canvas.style.transformOrigin = 'top left';
            canvas.style.transform = `scale(${scale})`;
            wrap.style.width = `${868 * scale}px`;
            wrap.style.height = `${560 * scale}px`;
        }
        window.addEventListener('resize', () => { if (shareState.view === 'abyss') scaleShareAbyss(); });

        // キャラクリック → ガラスカード表示
        function bindShareCardClicks(rootEl) {
            rootEl.querySelectorAll('[data-share-cid]').forEach(btn => {
                btn.addEventListener('click', () => {
                    if (btn.dataset.shareBoss != null) { shareState.selIdx = Number(btn.dataset.shareBoss); showSharePartyCard(Number(btn.dataset.shareBoss)); }
                    else showShareGlassCard(btn.dataset.shareCid);
                });
            });
        }
        function showShareGlassCard(cid) {
            const card = shareState.cards[String(cid)];
            if (!card) return;
            shareState.view = 'glass'; shareState.glassCid = cid;
            htmlCardContainer.classList.add('hidden');
            glassCardContainer.classList.remove('hidden');
            cardImage.classList.add('hidden');
            const ph = document.getElementById('placeholderText');
            if (ph) ph.classList.add('hidden');
            renderGlassCard(card);
            let back = document.getElementById('shareBackBtn');
            if (!back) {
                back = document.createElement('button');
                back.id = 'shareBackBtn';
                back.className = 'fixed top-3 right-3 z-40 px-3 py-1.5 rounded-lg bg-zinc-800/90 border border-zinc-600 text-sm text-white hover:bg-zinc-700 transition-all';
                back.textContent = '← 共有一覧に戻る';
                back.onclick = () => { glassCardContainer.classList.add('hidden'); renderShareHome(); };
                document.body.appendChild(back);
            }
            back.style.display = '';
        }

        // ---- 幽境共有の閲覧（?abyss= 取込） ----
        let abyssViewData = null;

        // 閲覧モード: テーマ未設定ならダーク/ライトの2択を出してから描画する
        function ensureViewTheme(onReady) {
            let theme = null;
            try { theme = localStorage.getItem('genshin_build_card_theme_pref'); } catch (e) {}
            if (theme === 'light' || theme === 'dark') {
                document.documentElement.setAttribute('data-theme', theme);
                onReady();
                return;
            }
            const ov = document.createElement('div');
            ov.id = 'sharedThemePick';
            ov.className = 'fixed inset-0 z-[70] bg-black/85 backdrop-blur-sm flex items-center justify-center p-4';
            ov.innerHTML = `
                <div class="text-center space-y-5">
                    <div class="text-lg font-bold text-white">テーマを選択してください</div>
                    <div class="flex gap-4 justify-center">
                        <button type="button" data-th="dark" class="w-36 py-6 rounded-2xl border-2 border-zinc-600 hover:border-cyan-400 bg-zinc-900 text-white font-bold transition-all">ダーク</button>
                        <button type="button" data-th="light" class="w-36 py-6 rounded-2xl border-2 border-zinc-300 hover:border-cyan-400 bg-zinc-100 text-zinc-900 font-bold transition-all">ライト</button>
                    </div>
                </div>`;
            document.body.appendChild(ov);
            ov.querySelectorAll('[data-th]').forEach(btn => {
                btn.addEventListener('click', () => {
                    const th = btn.dataset.th;
                    try { localStorage.setItem('genshin_build_card_theme_pref', th); } catch (e) {}
                    document.documentElement.setAttribute('data-theme', th);
                    ov.remove();
                    onReady();
                });
            });
        }

        function tryImportAbyssShare() {
            try {
                const raw = new URLSearchParams(location.search).get('abyss');
                if (!raw) return false;
                const data = JSON.parse(b64urlDecodeUtf8(raw));
                if (!data || data.type !== 'abyss') return false;
                abyssViewData = data;
                history.replaceState(null, '', location.pathname + '?uid=' + encodeURIComponent(uid));
                return true;
            } catch (e) { return false; }
        }

        function esc2(s) { return esc(s); }

        function renderAbyssView() {
            const d = abyssViewData;
            if (!d) return;
            teamMode = true;
            teamViewOnly = true;
            activeTeamId = null;
            // バー表示
            const bar = document.getElementById('teamModeBar');
            if (bar) bar.classList.remove('hidden');
            const title = bar ? bar.querySelector('.text-sm.font-semibold') : null;
            if (title) {
                const t = title.querySelector('[data-i18n]');
                if (t) { t.textContent = GenshinI18n.t('閲覧モード'); t.removeAttribute('data-i18n'); }
            }
            const label = document.getElementById('teamModeSlotLabel');
            if (label) label.textContent = d.version ? `幽境 ${d.version}` : '幽境';
            ['teamShareBtn', 'teamShareAbyssBtn'].forEach(id => {
                const b = document.getElementById(id);
                if (b) b.classList.add('hidden');
            });
            ['shareCardBtn', 'regenerateImageBtn', 'regenerateApiBtn', 'teamEditSlotBtn'].forEach(id => {
                const b = document.getElementById(id);
                if (b) b.classList.add('hidden');
            });
            const cols = document.getElementById('mainColumns');
            if (cols) cols.classList.add('team-single');
            // カード表示領域に幽境ビューを構築
            htmlCardContainer.classList.remove('hidden');
            const enemyImg = d.enemy && d.enemy.img ? assetUrl(d.enemy.img) : '';
            const DIFF_LABELS_V = { master: 'マスター', extra: 'エクストラ', ultimate: 'アルティメット' };
            const diffLabelV = DIFF_LABELS_V[d.difficulty] || d.difficulty || '';
            const LINK_LABELS_V = { yt: 'YouTube', bili: 'bilibili', tw: 'Twitter / X' };
            const linkHtmlV = Object.entries(d.links || {}).map(([k, v]) =>
                `<a href="${esc(v)}" target="_blank" rel="noopener noreferrer" class="text-cyan-400 underline hover:text-cyan-300 whitespace-nowrap">${esc(LINK_LABELS_V[k] || k)}</a>`
            ).join('<span class="text-zinc-600 mx-1">·</span>');
            const bosses = (d.bosses && d.bosses.length ? d.bosses : [{ name: d.enemy ? d.enemy.name : '', img: d.enemy ? d.enemy.img : '', chars: (d.teams || [])[0] || [], time: (d.times || [])[0] || '' }]);
            const bossRows = bosses.map((b, i) => {
                const chars = b.chars || [];
                return `
                <div class="rounded-2xl border border-rose-800/50 overflow-hidden" style="background:rgba(20,16,20,.82)">
                    <div class="flex items-center gap-3 px-4 py-3 border-b border-rose-900/50">
                        ${b.img ? `<img src="${esc(assetUrl(b.img))}" class="w-12 h-12 rounded-xl object-cover shrink-0" style="box-shadow:0 0 0 2px rgba(230,90,70,.35)">` : ''}
                        <div class="min-w-0 flex-1">
                            <div class="text-[10px] tracking-widest text-rose-300/80">BOSS ${i + 1}</div>
                            <div class="text-base font-bold truncate text-white">${esc(b.name || '')}</div>
                        </div>
                        ${b.time ? `<span class="text-sm font-bold text-white shrink-0">${esc(b.time)}</span>` : ''}
                    </div>
                    <div class="flex gap-2 flex-wrap px-4 py-3">
                        ${(chars.length ? chars : []).map(cid => `
                            <button type="button" data-abyss-view-char="${esc(cid)}" data-abyss-view-uid="${esc(d.uid || '')}"
                                title="${d.uid ? 'クリックでビルドカードを表示' : 'UID非公開のためビルドカードは表示できません'}"
                                class="relative w-14 h-14 rounded-lg overflow-hidden border-2 border-zinc-700 hover:border-cyan-400 transition-all ${d.uid ? 'cursor-pointer' : 'cursor-default opacity-70'}">
                                ${teamCharIconSrc(cid) ? `<img src="${esc(teamCharIconSrc(cid))}" class="w-full h-full object-cover">` : `<span class="text-[10px] text-zinc-500">${esc(cid)}</span>`}
                            </button>`).join('') || '<span class="text-xs text-zinc-600 py-3">メンバー未登録</span>'}
                        <span class="ml-auto self-center text-[11px] ${chars.length === 4 ? 'text-cyan-400' : 'text-zinc-600'}">${chars.length}/4</span>
                    </div>
                </div>`;
            }).join('');
            htmlCardContainer.innerHTML = `
                <div class="max-w-2xl mx-auto space-y-4 p-2 rounded-2xl" style="background-image:url('/static/assets/abyss_share_base.png?v=0');background-size:cover;background-position:center;">
                    <div class="rounded-2xl p-4" style="background:rgba(10,8,10,.72)">
                        <div class="flex items-center gap-3 flex-wrap">
                            <div class="text-[11px] tracking-widest text-rose-300/90">幽境危路 ${esc(d.version || '')}</div>
                            ${diffLabelV ? `<span class="px-3 py-1.5 rounded-lg bg-rose-900/60 border border-rose-600/60 text-rose-200 text-sm font-bold">${esc(diffLabelV)}</span>` : ''}
                            ${linkHtmlV ? `<div class="text-[13px] ml-auto">${linkHtmlV}</div>` : ''}
                        </div>
                    </div>
                    <div class="space-y-3">${bossRows}</div>
                </div>`;
            // キャラクリック → ビルドカード
            htmlCardContainer.querySelectorAll('[data-abyss-view-char]').forEach(btn => {
                btn.addEventListener('click', () => {
                    const cid = btn.dataset.abyssViewChar;
                    const viewUid = btn.dataset.abyssViewUid;
                    if (!viewUid) { showToast('この共有ではUIDが非公開のためビルドカードを表示できません', 'info'); return; }
                    window.open(`/uid/${viewUid}`, '_blank');
                    showToast('共有者のビルドカードページを開きました。キャラ一覧から該当キャラを選んでください', 'info');
                });
            });
            showToast(`幽境の共有（${d.version || ''}）を表示しています`, 'info');
        }

        // ---- 設定引き継ぎ（合言葉） ----
        function collectSettingsSnapshot() {
            const out = {};
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                if (!k) continue;
                if (k.startsWith('genshin_build_card_') || k.startsWith('team_slots_') ||
                    k.startsWith('team_active_') || k === 'uid_history') {
                    out[k] = localStorage.getItem(k);
                }
            }
            return out;
        }
        async function exportSettingsPhrase() {
            const phrase = (document.getElementById('phraseExportInput')?.value || '').trim();
            const ttl = Number(document.getElementById('phraseTtlSelect')?.value || 24);
            const status = document.getElementById('phraseStatus');
            if (!phrase) { showToast('合言葉を入力してください', 'error'); return; }
            const data = collectSettingsSnapshot();
            if (!Object.keys(data).length) { showToast('共有できる設定がありません', 'error'); return; }
            try {
                const res = await fetch('/api/share_phrase', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ phrase, ttl_hours: ttl, data }),
                });
                const body = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(body.detail || `失敗 (${res.status})`);
                if (status) status.textContent = `保存しました（${ttl}時間後に自動削除）。同じ合言葉で読み込めます。`;
                showToast(`合言葉「${phrase}」で引き継ぎデータを保存しました（${ttl}時間）`, 'success');
            } catch (e) {
                if (status) status.textContent = '保存に失敗: ' + e.message;
                showToast('保存に失敗しました: ' + e.message, 'error');
            }
        }
        async function importSettingsPhrase() {
            const phrase = (document.getElementById('phraseImportInput')?.value || '').trim();
            const status = document.getElementById('phraseStatus');
            if (!phrase) { showToast('合言葉を入力してください', 'error'); return; }
            try {
                const res = await fetch(`/api/share_phrase/${encodeURIComponent(phrase)}`, { cache: 'no-store' });
                const body = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(body.detail || `失敗 (${res.status})`);
                const data = body.data || {};
                const keys = Object.keys(data);
                const ok = await requestConfirmation(`合言葉の設定（${keys.length}項目・残り約${body.expires_in_hours}時間）を取り込みます。\n現在の設定は上書きされます。よろしいですか？`, {
                    title: '設定の引き継ぎ', confirmText: '取り込む', cancelText: 'キャンセル'
                });
                if (!ok) return;
                keys.forEach(k => { try { localStorage.setItem(k, data[k]); } catch (e) {} });
                showToast('設定を取り込みました。ページを再読み込みします', 'success');
                setTimeout(() => location.reload(), 600);
            } catch (e) {
                if (status) status.textContent = '読み込みに失敗: ' + e.message;
                showToast('読み込みに失敗しました: ' + e.message, 'error');
            }
        }
        async function saveTeamShareImage() {
            const teams = getSelectedShareTeams();
            if (!teams.length) return;
            if (!(await confirmBetaShare())) return;
            const imgBtn = document.getElementById('teamShareImageBtn');
            if (imgBtn) { imgBtn.disabled = true; imgBtn.textContent = '生成中...'; }
            try {
                const res = await fetch('/api/team_share_image', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ uid, lang: GenshinI18n.isEn() ? 'en' : 'ja',
                        teams: teams.map(t => ({ chars: t.c, configs: buildTeamConfigs(t.c) })) }),
                });
                if (!res.ok) {
                    const body = await res.json().catch(() => ({}));
                    throw new Error(body.detail || `失敗 (${res.status})`);
                }
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `teams_${uid}_${teams.length}.png`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                setTimeout(() => URL.revokeObjectURL(url), 5000);
                showToast(`${teams.length}編成の共有画像を保存しました`, 'success');
            } catch (e) {
                showToast('画像の生成に失敗しました: ' + e.message, 'error');
            } finally {
                if (imgBtn) { imgBtn.disabled = false; imgBtn.textContent = '画像を保存'; updateTeamShareButtons(); }
            }
        }
        async function copyTextToClipboard(text, okMsg) {
            try {
                await navigator.clipboard.writeText(text);
                showToast(okMsg, 'success');
            } catch (e) {
                try {
                    const inp = document.createElement('textarea');
                    inp.value = text;
                    document.body.appendChild(inp);
                    inp.select();
                    document.execCommand('copy');
                    inp.remove();
                    showToast(okMsg, 'success');
                } catch (e2) {
                    showToast('コピーに失敗しました。リンク: ' + text, 'error');
                }
            }
        }
        async function confirmBetaShare() {
            if (ver === 'beta') {
                return await requestConfirmation('共有する編成には beta 版データが含まれています。正式版と異なる状態で共有されますがよろしいですか？', {
                    title: 'beta版データの確認', confirmText: '共有する', cancelText: 'キャンセル'
                });
            }
            return true;
        }
        async function copyTeamShareLink() {
            const teams = getSelectedShareTeams();
            if (!teams.length) return;
            if (!(await confirmBetaShare())) return;
            const showUid = !!document.getElementById('teamShareUidChk')?.checked;
            const link = await buildShareLink(teams, showUid); // sid優先。cards は必要時のみ収集
            await copyTextToClipboard(link, '共有リンクをコピーしました' + (showUid ? '' : '（UID非表示）'));
        }
        async function saveAbyssShareImage() {
            const data = collectAbyssShare();
            if (!(data.bosses || []).some(b => (b.chars || []).length)) return;
            if (!(await confirmBetaShare())) return;
            const sid = await createShareSnapshot(data.bosses.flatMap(b => b.chars));
            const imgBtn = document.getElementById('abyssShareImageBtn');
            if (imgBtn) { imgBtn.disabled = true; imgBtn.textContent = '生成中...'; }
            try {
                const res = await fetch('/api/team_share_image', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ uid, lang: GenshinI18n.isEn() ? 'en' : 'ja',
                        abyss: { version: data.version, difficulty: data.difficulty, links: data.links || {}, show_uid: !!data.uid, uid: uid, pname: sharePlayerName(), bosses: data.bosses, sid: sid || '' },
                        mode: 'abyss' }),
                });
                if (!res.ok) {
                    const body = await res.json().catch(() => ({}));
                    throw new Error(body.detail || `失敗 (${res.status})`);
                }
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `abyss_${uid}.png`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                setTimeout(() => URL.revokeObjectURL(url), 5000);
                showToast('幽境共有画像を保存しました', 'success');
                saveAbyssShareDraft();   // 下書きキャッシュも最新化
            } catch (e) {
                showToast('画像の生成に失敗しました: ' + e.message, 'error');
            } finally {
                if (imgBtn) { imgBtn.disabled = false; imgBtn.textContent = '画像を保存'; updateAbyssShareButtons(); }
            }
        }

        // ---- イベント ----
        if (teamCardBtn && TEAM_GEN_ENABLED) {
            teamCardBtn.addEventListener('click', () => { openTeamCardModal(); });
        }
        if (teamCardCancelBtn) {
            teamCardCancelBtn.addEventListener('click', closeTeamCardModal);
        }
        // メンバー選択ピッカー（1枠）
        const teamCharPickerCancelBtn = document.getElementById('teamCharPickerCancelBtn');
        const teamCharPickerRemoveBtn = document.getElementById('teamCharPickerRemoveBtn');
        if (teamCharPickerCancelBtn) teamCharPickerCancelBtn.addEventListener('click', closeTeamCharPicker);
        if (teamCharPickerRemoveBtn) teamCharPickerRemoveBtn.addEventListener('click', removeTeamMember);

        if (teamGenBtn) {
            teamGenBtn.addEventListener('click', generateActiveTeam);
        }
        const teamShareBtn = document.getElementById('teamShareBtn');
        if (teamShareBtn) teamShareBtn.addEventListener('click', () => openTeamShareModal(1));
        const teamShareAbyssBtn = document.getElementById('teamShareAbyssBtn');
        if (teamShareAbyssBtn) teamShareAbyssBtn.addEventListener('click', () => openAbyssShareModal());
        const teamShareLinkBtn = document.getElementById('teamShareLinkBtn');
        if (teamShareLinkBtn) teamShareLinkBtn.addEventListener('click', copyTeamShareLink);
        const teamShareImageBtn = document.getElementById('teamShareImageBtn');
        if (teamShareImageBtn) teamShareImageBtn.addEventListener('click', saveTeamShareImage);
        const teamShareCancelBtn = document.getElementById('teamShareCancelBtn');
        if (teamShareCancelBtn) teamShareCancelBtn.addEventListener('click', closeTeamShareModal);
        const abyssShareLinkBtn = document.getElementById('abyssShareLinkBtn');
        if (abyssShareLinkBtn) abyssShareLinkBtn.addEventListener('click', async () => {
            const data = collectAbyssShare();
            if (!(await confirmBetaShare())) return;
            copyTextToClipboard(await buildAbyssShareLink(data), '幽境共有リンクをコピーしました');
            saveAbyssShareDraft();   // 次回モーダルで復元する下書きをサーバーキャッシュへ保存
        });
        const abyssShareImageBtn = document.getElementById('abyssShareImageBtn');
        if (abyssShareImageBtn) abyssShareImageBtn.addEventListener('click', saveAbyssShareImage);
        const abyssShareCancelBtn = document.getElementById('abyssShareCancelBtn');
        if (abyssShareCancelBtn) abyssShareCancelBtn.addEventListener('click', closeAbyssShareModal);
        const abyssShareLinkAddBtn = document.getElementById('abyssShareLinkAddBtn');
        if (abyssShareLinkAddBtn) abyssShareLinkAddBtn.addEventListener('click', () => {
            const inp = document.getElementById('abyssShareLinkInput');
            const url = (inp?.value || '').trim();
            if (!url) { showToast('URLを入力してください', 'error'); return; }
            const svc = detectLinkService(url);
            if (!svc) { showToast('YouTube / bilibili / Twitter(X) のリンクのみ追加できます', 'error'); return; }
            if (abyssShareLinks.some(l => l.svc === svc)) { showToast('このサービスのリンクは既に追加されています', 'info'); return; }
            abyssShareLinks.push({ svc, url });
            if (inp) inp.value = '';
            renderAbyssShareLinkChips();
            showToast('リンクを追加しました', 'success');
        });
        // パーティ選択ボタン(モーダル内・委譲)
        const bossRows = document.getElementById('abyssShareBossRows');
        if (bossRows) bossRows.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-abyss-pick]');
            if (btn) openAbyssTeamPickModal(Number(btn.dataset.abyssPick));
        });
        const phraseExportBtn = document.getElementById('phraseExportBtn');
        if (phraseExportBtn) phraseExportBtn.addEventListener('click', exportSettingsPhrase);
        const phraseImportBtn = document.getElementById('phraseImportBtn');
        if (phraseImportBtn) phraseImportBtn.addEventListener('click', importSettingsPhrase);
        if (teamEditSlotBtn) {
            teamEditSlotBtn.addEventListener('click', () => openTeamCardModal());
        }
        if (teamExitBtn) {
            teamExitBtn.addEventListener('click', exitTeamMode);
        }

        // 再生成：Enka API にはアクセスせず、カード画像だけをサーバーに作り直してもらう。
        // 画像表示モード以外なら切り替えてから生成する。
        // 古いキャッシュが残らないよう、対象URLのキャッシュは先に削除する
        regenerateImageBtn.addEventListener('click', async function() {
            // 編成モード中は編成カードを再生成する
            if (teamMode) {
                generateActiveTeam();
                return;
            }
            if (!currentSelectedCharId) return;
            if (viewMode !== 'image') {
                viewMode = 'image';
                saveViewMode(viewMode);
                applyViewModeUI();
                applySettingsPanelUI();
                applyPageThemeUi();
                latestGlassRequestToken++;
                latestHtmlRequestToken++;
            }
            // 既存キャッシュを消してサーバー再生成（「生成」「再生成」共通）
            await deleteCachedCardImage(await getSignedCardUrl(buildCardImageUrl(currentSelectedCharId)));
            loadCardImage(currentSelectedCharId, true);
        });

        // ==========================================================
        //  幽境モード（レイラインの敵ボスを編成カード下部に表示）
        // ==========================================================
        const ABYSS_BOSS_KEY = 'abyss_boss_' + uid;
        const ABYSS_MODE_KEY = 'abyss_mode_' + uid;
        const ABYSS_PRE_VERSIONS = ['5.7', '5.8', '6.0 (luna1)', '6.1 (luna2)', '6.2 (luna3)', '6.3 (luna4)', '6.4 (luna5)', '6.5 (luna6)', '6.6 (luna7)', '6.7 (luna8)'];
        let abyssMode = false;
        let abyssBoss = null;
        try { abyssBoss = JSON.parse(localStorage.getItem(ABYSS_BOSS_KEY)) || null; } catch (e) { abyssBoss = null; }
        try { abyssMode = localStorage.getItem(ABYSS_MODE_KEY) === '1'; } catch (e) { abyssMode = false; }
        function saveAbyssMode() {
            try {
                if (abyssMode) localStorage.setItem(ABYSS_MODE_KEY, '1');
                else localStorage.removeItem(ABYSS_MODE_KEY);
            } catch (e) {}
        }

        function abyssVersionFor(id) {
            const n = Number(id);
            if (!Number.isFinite(n)) return '';
            if (n <= 5269010) {
                const idx = n - 5269001;
                return (idx >= 0 && idx < ABYSS_PRE_VERSIONS.length) ? ABYSS_PRE_VERSIONS[idx] : '';
            }
            if (n <= 5269011) return '7.0';
            return '7.' + (n - 5269011);
        }
        function saveAbyssBoss() {
            try {
                if (abyssBoss) localStorage.setItem(ABYSS_BOSS_KEY, JSON.stringify(abyssBoss));
                else localStorage.removeItem(ABYSS_BOSS_KEY);
            } catch (e) {}
        }
        function updateGenerateDisable() {
            // 編成モード中・幽境ONでボス未選択なら生成不可
            const needBoss = teamMode && abyssMode && !abyssBoss;
            if (regenerateImageBtn) {
                regenerateImageBtn.disabled = needBoss;
                regenerateImageBtn.classList.toggle('opacity-60', needBoss);
                regenerateImageBtn.classList.toggle('cursor-not-allowed', needBoss);
            }
        }
        function updateAbyssUI() {
            const row = document.getElementById('abyssRow');
            const btn = document.getElementById('abyssToggleBtn');
            const slotBtn = document.getElementById('abyssSlotBtn');
            // 編成カードから幽境セクションは廃止されたため、幽境モードUIは常に非表示
            if (row) row.classList.add('hidden');
            if (btn) { btn.classList.add('hidden'); btn.disabled = true; }
            if (slotBtn) slotBtn.classList.add('hidden');
            // 幽境モードは編成モード中のみ有効（通常モードでは無効化）

            if (btn) {
                btn.disabled = !teamMode;
                btn.classList.toggle('opacity-40', !teamMode);
                btn.classList.toggle('cursor-not-allowed', !teamMode);
                btn.classList.toggle('border-cyan-400', abyssMode);
                btn.classList.toggle('bg-cyan-500/20', abyssMode);
            }
            const icon = document.getElementById('abyssSlotIcon');
            const label = document.getElementById('abyssSlotLabel');
            if (abyssBoss && abyssBoss.img) {
                if (icon) { icon.src = abyssBoss.img; icon.classList.remove('hidden'); }
                if (label) label.textContent = `${abyssBoss.name || 'ボス'}（${abyssBoss.version || ''}）`;
            } else {
                if (icon) icon.classList.add('hidden');
                if (label) label.textContent = 'ボスを選択';
            }
            // ボス選択済みなら縁を明るく（選択中のように）
            if (slotBtn) {
                slotBtn.classList.toggle('border-cyan-400', !!abyssBoss);
                slotBtn.classList.toggle('ring-2', !!abyssBoss);
                slotBtn.classList.toggle('ring-cyan-400/60', !!abyssBoss);
            }
            updateGenerateDisable();
        }
        function getAbyssBossParam() {
            if (!abyssMode || !abyssBoss || !abyssBoss.version) return null;
            return { version: abyssBoss.version, name: abyssBoss.name || '', img: abyssBoss.img || '' };
        }

        // ---- ボス選択モーダル ----
        function openAbyssModal() {
            const modal = document.getElementById('abyssModal');
            const groupsEl = document.getElementById('abyssVersionGroups');
            openModal(modal, closeAbyssModal);
            groupsEl.innerHTML = '<div class="text-sm text-zinc-500 py-6 text-center">読み込み中...</div>';
            loadAbyssGroups().then(groups => renderAbyssGroups(groups));
        }
        function closeAbyssModal() {
            const modal = document.getElementById('abyssModal');
            closeModal(modal);
        }
        async function loadAbyssGroups() {
            const groups = [];
            try {
                const res = await fetch('/api/leyline_versions');
                const data = await res.json();
                if (!data.ok || !data.versions) return groups;
                for (const v of data.versions) {
                    try {
                        const jres = await fetch(v.url);
                        if (!jres.ok) continue;
                        const json = await jres.json();
                        Object.values(json || {}).forEach(entry => {
                            const verLabel = abyssVersionFor(entry.id);
                            if (!entry.enemies || !entry.enemies.length) return;
                            let g = groups.find(x => x.version === verLabel);
                            if (!g) { g = { version: verLabel, enemies: [] }; groups.push(g); }
                            entry.enemies.forEach(e => {
                                g.enemies.push({ leylineId: entry.id, leylineName: entry.name || '', name: e.name || '', img: e.img || '' });
                            });
                        });
                    } catch (e) { /* skip */ }
                }
            } catch (e) { /* ignore */ }
            groups.sort((a, b) => String(b.version || '').localeCompare(String(a.version || '')));
            return groups;
        }
        function renderAbyssGroups(groups) {
            const groupsEl = document.getElementById('abyssVersionGroups');
            if (!groups.length) {
                groupsEl.innerHTML = '<div class="text-sm text-zinc-500 py-6 text-center">レイラインデータがありません（管理画面で取得してください）</div>';
                return;
            }
            groupsEl.innerHTML = groups.map(g => `
                <div>
                    <div class="text-sm font-bold text-cyan-300 mb-2">${esc(g.version)}</div>
                    <div class="flex flex-wrap gap-2">
                        ${g.enemies.map(e => `
                            <button type="button" class="abyss-enemy-tile w-24 flex flex-col items-center gap-1 rounded-lg border border-zinc-700 bg-zinc-800/60 p-1.5 hover:border-cyan-400 transition-all"
                                data-abyss='${encodeURIComponent(JSON.stringify(e))}'>
                                <img src="${esc(e.img || '')}" class="w-16 h-16 rounded object-cover bg-zinc-800" onerror="this.style.visibility='hidden'">
                                <span class="text-[10px] text-zinc-400 text-center leading-tight">${esc(e.name)}</span>
                            </button>`).join('')}
                    </div>
                </div>`).join('');
            groupsEl.querySelectorAll('.abyss-enemy-tile').forEach(tile => {
                tile.addEventListener('click', () => {
                    try {
                        const e = JSON.parse(decodeURIComponent(tile.getAttribute('data-abyss')));
                        abyssBoss = { version: abyssVersionFor(e.leylineId), name: e.name, img: e.img, leylineId: e.leylineId };
                        saveAbyssBoss();
                        updateAbyssUI();
                        closeAbyssModal();
                    } catch (err) { /* ignore */ }
                });
            });
        }
        function clearAbyssBoss() {
            abyssBoss = null;
            saveAbyssBoss();
            updateAbyssUI();
            closeAbyssModal();
        }

        const abyssToggleBtn = document.getElementById('abyssToggleBtn');
        if (abyssToggleBtn) {
            abyssToggleBtn.addEventListener('click', () => { abyssMode = !abyssMode; saveAbyssMode(); updateAbyssUI(); });
        }
        const abyssSlotBtn = document.getElementById('abyssSlotBtn');
        if (abyssSlotBtn) abyssSlotBtn.addEventListener('click', openAbyssModal);
        const abyssCloseBtn = document.getElementById('abyssCloseBtn');
        if (abyssCloseBtn) abyssCloseBtn.addEventListener('click', closeAbyssModal);
        const abyssClearBtn = document.getElementById('abyssClearBtn');
        if (abyssClearBtn) abyssClearBtn.addEventListener('click', clearAbyssBoss);
        updateAbyssUI();
    