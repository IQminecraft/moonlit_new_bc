/* ============================================================
 *  nanoka.cc i18n（日本語 / 英語切替）
 *
 *  使い方:
 *    - 静的HTML: 対象要素に data-i18n（テキスト）/ data-i18n-placeholder /
 *      data-i18n-title / data-i18n-aria（aria-label）/ data-i18n-html（innerHTML）
 *      を付け、属性値は「元の日本語」のままでよい（キーとして使用）。
 *    - 動的JS: GenshinI18n.t('日本語') で現在言語のテキストを取得。
 *    - 言語切替: GenshinI18n.setLang('en' | 'ja')。'genshin:langchange'
 *      イベントが発火するので、動的描画はこれを受けて再描画する。
 *  言語設定は localStorage 'genshin_build_card_lang' に保存する。
 * ============================================================ */
(function () {
    'use strict';

    const LANG_KEY = 'genshin_build_card_lang';

    /* 日本語 → 英語 辞書（キー = 元の日本語テキスト） */
    const EN = {
        // 共通ヘッダー
        '原神ビルドカード': 'Genshin Build Card',
        'チュートリアル': 'Tutorial',
        'チュートリアルを表示します': 'Show tutorial',
        'ライト / ダーク': 'Light / Dark',
        'ライト / ダークを切り替えます': 'Toggle light / dark',
        '言語': 'Language',
        'サイトの表示言語を切り替えます': 'Switch the site language',
        '問い合わせ': 'Contact',
        'お問い合わせフォームを開きます': 'Open the contact form',
        '共有済み': 'Shared',
        'この端末で作った共有リンクを見ます': 'View share links made on this device',
        '共有履歴はありません': 'No shares on this device',
        'UID表示': 'UID on',
        'UID非表示': 'UID hidden',
        '開く': 'Open',
        'コピー': 'Copy',
        '共有リンクをコピーしました': 'Share link copied',
        '編集': 'Edit',
        '削除': 'Delete',
        'この共有を削除しますか？': 'Delete this share?',
        '編成': 'Team',
        '幽境': 'Abyss',
        '使用するゲームデータ': 'Game data in use',

        // フッター
        'moonlit.wiki はファンメイドのWebサイトです。': 'moonlit.wiki is a fan-made website.',
        'ゲーム関連の画像およびアセットの著作権・商標権はすべて HoYoverse に帰属します。': 'All game-related images and assets are properties of HoYoverse. All copyrights and trademarks belong to HoYoverse.',

        // 免責（ビルドカードページ）
        '※他のビルドカード生成サイトとは違い、武器の恒常ステータスなどは反映されていません。また、小数第二位まで表示している場合は他サイトとスコアが異なる可能性があります。': '※Unlike other build card generator sites, innate weapon stats are not reflected. Also, when precision is set to the 2nd decimal place, scores may differ from those on other sites.',

        // ホーム（UIDフォーム）
        'UIDからビルドを読み込む': 'Load a build from a UID',
        '公開プロフィールに登録されたキャラクター情報を取得します': 'Fetches character info registered on the public profile',
        'ゲーム内プロフィールのUIDを入力してください': 'Enter the UID shown in your in-game profile',
        'データを読み込む': 'Load data',
        'UIDのビルドデータを読み込みます': 'Load build data for this UID',
        '読み込み中...': 'Loading...',
        'データを取得できませんでした': 'Failed to fetch data',
        '保存したプレイヤー': 'Saved players',
        '保存したプレイヤーをワンクリックで再読み込みできます。': 'Reload a saved player with one click.',
        'すべて削除': 'Clear all',
        '検索履歴をすべて削除します': 'Clear all search history',
        '検索履歴はありません': 'No search history',

        // サーバーエラー（サーバー側メッセージの英訳）
        'UIDは数字のみで入力してください。': 'The UID must contain digits only.',
        '指定されたUIDのデータを取得できませんでした。UIDが存在しないか、ゲーム内プロフィールが公開されていない可能性があります。': 'Could not fetch data for the given UID. The UID may not exist, or the in-game profile may not be public.',
        '指定されたUIDのゲーム内プロフィールで『キャラクター詳細を公開』がオンになっていないか、ショーケースが空です。': 'For the given UID, "Show Character Details" may be turned off in the in-game profile, or the showcase is empty.',

        // 初期設定ウィザード
        '色のテーマを選ぶ': 'Choose a color theme',
        'サイト全体の見た目です。あとから「ライト / ダーク」ボタンでも切り替えられます。': 'The site-wide look. You can switch later with the "Light / Dark" button.',
        '黒（ダーク）': 'Black (Dark)',
        '白（ライト）': 'White (Light)',
        'HTML上のテーマを選ぶ': 'Choose the HTML theme',
        'サイト内でビルドカードを表示するときのデザインです。': 'The design used when showing the build card on the site.',
        'ガラス': 'Glass',
        'シネマ': 'Cinema',
        'スコアカード': 'Scorecard',
        '画像上のテーマを選ぶ': 'Choose the image theme',
        'カード画像を生成するときのデザインです。': 'The design used when generating card images.',
        '使用するゲームのデータを選ぶ': 'Choose the game data',
        'BETA は未公開の先行データ、LIVE は正式配信中のデータです。': 'BETA uses unreleased early data; LIVE uses officially released data.',
        'LIVE（正式版）': 'LIVE (Official)',
        'BETA（先行データ）': 'BETA (Early data)',
        '使い方': 'How to use',
        '3ステップでビルドカードを生成できます。': 'Generate a build card in 3 steps.',
        'オススメ': 'Recommended',
        '次へ': 'Next',
        '戻る': 'Back',
        'スキップ': 'Skip',
        '使い始める': 'Get started',
        'ダーク / ガラス / ガラス / LIVE で開始します': 'Start with Dark / Glass / Glass / LIVE',
        '設定を閉じる': 'Close settings',
        'UIDを入力': 'Enter your UID',
        'ゲーム内プロフィールのUIDを入力してデータを読み込みます。': 'Enter the UID from your in-game profile to load data.',
        'キャラクターを選ぶ': 'Choose a character',
        '一覧からビルドを見たいキャラクターを選択します。': 'Select the character whose build you want to view.',
        'カードを生成・保存': 'Generate & save a card',
        '好みのテーマを選んでカードを生成し、画像として保存できます。': 'Pick a theme, generate the card, and save it as an image.',

        // モーダル（ホーム）
        '未確定データの閲覧同意': 'Consent to view unreleased data',
        'これから表示する内容には、ゲーム内で正式配信される前の未確定データが含まれます。今後のアップデートで数値や仕様が変更・削除される可能性があります。': 'The content you are about to view contains unreleased data that has not been officially shipped in-game. Values and specifications may change or be removed in future updates.',
        '内容にご注意のうえ、ご自身の判断でご覧ください。': 'Please view it at your own discretion.',
        '同意して閲覧する': 'Agree and view',
        'チュートリアルをしますか？': 'Take the tutorial?',
        'ハイライトで使い方を案内します。あとから「チュートリアル」ボタンでも表示できます。': 'Highlights will guide you through the basics. You can reopen it later from the "Tutorial" button.',
        'はい': 'Yes',
        'いいえ': 'No',
        '原神ビルドカード was Updated!!': 'Genshin Build Card was Updated!!',
        'OK': 'OK',

        // ビルドカードページ（トップバー・ヘッダー）
        '← 戻る': '← Back',
        '編成カード': 'Team card',
        '設定': 'Settings',
        'ビルドカード': 'Build card',
        'HTMLでビルドカードを表示します': 'Show the build card in HTML',
        '画像生成': 'Generate image',
        '共有用のカード画像を生成します': 'Generate a shareable card image',
        '編成モード': 'Team mode',
        '通常モードに戻る': 'Back to normal mode',

        // サーバー負荷パネル
        '使用CPU': 'CPU',
        'メモリ': 'Memory',
        'プロセス': 'Process',

        // サイドバー
        'キャラクター': 'Character',
        'キャラクターと武器の設定': 'Character & weapon settings',
        '武器': 'Weapon',
        '差し替える武器を選択します': 'Select a weapon to swap in',
        '表示方法': 'Display',
        '計算方法や背景などの表示設定': 'Display settings such as calc method and background',
        '計算方法': 'Method',
        'スコア計算の中心にするステータス': 'Stat used as the focus of score calculation',
        '会心': 'CRIT',
        '攻撃力%': 'ATK%',
        'HP%': 'HP%',
        '防御%': 'DEF%',
        '元素熟知': 'Elemental Mastery',
        'チャージ効率': 'Energy Recharge',
        '表示の精度': 'Precision',
        'ステータス表示の小数精度': 'Decimal precision of stat values',
        '標準': 'Standard',
        '詳細(第2位)': 'Detailed (2nd decimal)',
        '育成情報': 'Growth info',
        '育成情報パネルを表示します': 'Show the growth info panel',
        '育成情報を表示': 'Show growth info',
        'サブステ伸び値': 'Substat growth',
        'サブステの伸び値をドットで表示します': 'Show substat growth as dots',
        '伸び値ドットを表示': 'Show growth dots',
        'UIDを表示': 'Show UID',
        'カード左下にUIDを表示します（共鳴チップと同じ高さ）': 'Show the UID at the bottom-left of the card (same height as resonance chips)',
        '背景': 'Background',
        'ビルドカード生成の背景': 'Background for generated build cards',
        'カード背景のモード': 'Card background mode',
        '元素背景': 'Element',
        '地域背景': 'Region',
        'カスタム色': 'Custom color',
        'カスタム背景色': 'Custom background color',
        'ステータス編集': 'Stat editing',
        '元素共鳴バフの選択': 'Select elemental resonance buffs',
        '元素共鳴': 'Elemental Resonance',
        '(最大2つ)': '(max 2)',
        '選択した共鳴バフを単体カードのステータスへ反映します。': 'Selected resonance buffs are applied to the single-card stats.',
        'キャラ差し替え': 'Swap character',
        '別のキャラクターに差し替えます': 'Swap to another character',
        '選択中': 'Selected',
        '再生成': 'Regenerate',
        '現在の設定でカードを再生成します': 'Regenerate the card with the current settings',
        '再取得': 'Refresh',
        '生成': 'Generate',

        // モーダル等（ビルドカードページ）
        '差し替え武器を選択': 'Select a swap weapon',
        '解除': 'Clear',
        '閉じる': 'Close',
        '確認': 'Confirm',
        '表示': 'Display',
        '生成画像': 'Generated image',
        '画像形式': 'Image format',
        '保存先': 'Save location',
        '生成した画像の保存場所': 'Where generated images are saved',
        '未装備': 'Not equipped',
        '総合スコア': 'Total Score',
        '伸び値': 'Growth',

        // 設定モーダル（ビルドカードページ）
        'データ': 'Data',
        'バージョン': 'Version',
        '正式配信中のデータと先行データを切り替え': 'Switch between live and early-access data',
        'テーマ': 'Theme',
        'サイト全体の見た目': 'Site-wide look',
        '言語': 'Language',
        'サイトとカード画像の表示言語': 'Display language for the site and card images',
        'ダーク': 'Dark',
        'ライト': 'Light',
        'クライアント': 'Client',
        'サーバー': 'Server',
        'なし（実データ）': 'None (real data)',
        'アイコンを読み込み中...': 'Loading icons...',
        'この武器種の候補が見つかりませんでした。': 'No candidates found for this weapon type.',
        'キャラクターを選択してください': 'Select a character',
        '右上の「生成」ボタンからビルドカード画像を作成できます': 'Use the "Generate" button at the top right to create the build card image',
        '画像を生成中...': 'Generating image...',
        '編成カードを生成中...': 'Generating team card...',
        '順番待ち': 'Queue',
        'スコア履歴': 'Score history',
        '件': ' entries',
        'シェア': 'Share',
        '先にカード画像を生成してください': 'Generate the card image first',
        'カード画像をコピーしました': 'Card image copied to clipboard',
        '画像を保存しました': 'Image saved',
        'シェアに失敗しました': 'Failed to share the image',
        '自動選択': 'Auto',
        'カード生成が混雑しています。しばらく待ってから「生成」を押してください。': 'Card generation is busy. Please wait a moment and press "Generate".',
        '画像の読み込みに失敗しました。もう一度お試しください。': 'Failed to load the image. Please try again.',
        '再試行': 'Retry',
        '今日読み込み': 'Loaded today',
        '昨日読み込み': 'Loaded yesterday',
        '日前読み込み': ' day(s) ago',
        '週間前読み込み': ' week(s) ago',
        'か月前読み込み': ' month(s) ago',
        'UID copy': 'Copy UID',
        'コピーしました': 'Copied',
        'さらに表示': 'Show more',
        '折りたたむ': 'Show less',
        'キャッシュ済みキャラ': 'Cached characters',
        'キャッシュデータなし': 'No cached data',
        '読み込み中': 'Loading',
        'アメリカサーバー': 'America server',
        'ヨーロッパサーバー': 'Europe server',
        'アジアサーバー': 'Asia server',
        '台湾・香港・マカオサーバー': 'TW/HK/MO server',
        '中国サーバー': 'China server',
        '削除': 'Delete',
        'クールタイムが終わりました。「再試行」を押してください。': 'Cooldown finished. Press "Retry" to try again.',
        'データの取得に失敗しました': 'Failed to load data',
        'デフォルト': 'Default',
        'デフォルト（実データ）': 'Default (real data)',
        'セット効果': 'Set Bonuses',
        '元素共鳴': 'Elemental Resonance',
        '聖遺物セット効果': 'Artifact Set Bonuses',
        '基礎ステータス %換算': 'Base Stats (%)',
        'サブステ伸び平均': 'Avg Substat Rolls',
        '1回あたりの平均': 'Avg per roll',
        '実数': ' (flat)',
        '2セット:': '2-Set:',
        '4セット:': '4-Set:',
        '1%あたり': 'per 1%',
        '編成スロット（最大12個）。選択するか「+」で新規作成してください': 'Team slots (max 12). Select one or press "+" to create a new slot.',

        // 共有
        '編成を共有': 'Share team',
        '幽境モードで共有': 'Share Stygian Onslaught',
        '3編成を1枚の画像で共有します（幽境は3パーティ制）': 'Share 3 teams as one image (Stygian Onslaught uses 3 parties)',
        '共有する編成を選択（1編成 または 3編成）': 'Select teams to share (1 or 3)',
        'UIDを表示する': 'Show UID',
        '（1編成共有は必須・OFFだと共有できません）': '(Required for a 1-team share)',
        '（OFFだとリンク先でビルドカードを見られません）': '(Required to open build cards on the shared page)',
        'リンクを受け取った人はページを開くと編成が取り込まれます。画像は3編成を縦に並べた1枚のPNGです。': 'Opening the link imports the teams. The image is one PNG with up to 3 teams stacked.',
        'リンクを生成': 'Create link',
        '画像を保存': 'Save image',
        '✓ リンクを生成しました！': '✓ Link created!',
        '共有リンク': 'Share link',
        'リンクを生成しました！': 'Link created!',
        'リンクの生成に失敗しました: ': 'Failed to create the link: ',
        '幽境共有リンクをコピーしました': 'Stygian Onslaught link copied',
        '画像の生成に失敗しました: ': 'Failed to generate the image: ',
        '幽境共有画像を保存しました': 'Stygian Onslaught image saved',
        'ボスとパーティ': 'Bosses and parties',
        '（バージョン選択で上3ボスを自動表示・全員埋まらなくても共有できます）': '(The first 3 bosses appear from the version. You can share even if parties are incomplete.)',
        '難易度': 'Difficulty',
        '確認用リンク': 'Proof link',
        '（貼って追加で自動判定）': '(Paste and add to detect the site)',
        '追加': 'Add',
        '編成を選択': 'Select a team',
        '幽境ボス選択': 'Select Stygian Onslaught boss',
        '閲覧モード': 'Viewing',
        '編成共有': 'Shared team',
        '幽境の激戦 編成': 'Stygian Onslaught team',
        '← 幽境画面に戻る': '← Back to Stygian Onslaught',
        '← 共有一覧に戻る': '← Back to share list',
        '共有する編成には beta 版データが含まれています。正式版と異なる状態で共有されますがよろしいですか？': 'This share includes beta data and will differ from the live version. Share it anyway?',
        'beta版データの確認': 'Beta data',
        '共有する': 'Share',
        'キャンセル': 'Cancel',
        'この幽境の共有には beta 版（未実装）の情報が含まれています。\n表示しますか？': 'This Stygian Onslaught share includes unreleased beta data.\nShow it?',
        'この共有には beta 版（未実装）のキャラクター・武器の情報が含まれています。\n表示しますか？': 'This share includes unreleased beta characters or weapons.\nShow it?',
        'beta版データが含まれています': 'This share includes beta data',
        '表示する': 'Show',
        '表示しない': 'Don\'t show',
        '共有データが見つかりません': 'Share data was not found',
        '生成中...': 'Creating...',
        'マスター': 'Master',
        'エクストラ': 'Expert',
        'アルティメット': 'Dire',

        // サーバー負荷 / 再取得 / 編成管理
        'サーバー負荷': 'Server load',
        'サーバー負荷表示': 'Show server load',
        'CPU / メモリ使用量を画面右端に表示': 'Show CPU / memory use on the right edge',
        'サーバーのCPU / メモリ使用量を表示します': 'Show the server CPU / memory use',
        '画像キャッシュ数': 'Cached images',
        '取得失敗': 'Failed to load',
        'Enka APIから最新データを再取得します': 'Refresh the latest data from Enka API',
        'たった今': 'Just now',
        '編成管理': 'Teams',
        '編成スロット管理': 'Team slots',
        '編成スロット管理（12スロット / 名前変更）': 'Manage 12 team slots / rename',
        '編成スロット（最大12個）。行をクリックで編成画面を開きます。名前は自由に付けられます': 'Team slots (max 12). Click a row to open it. Names are optional.',
        'クリックで編成画面を開く': 'Click to open this team',
        '新規編成を作成': 'Create a new team',
        '編成スロットの削除': 'Delete team slot',
        'メンバーを選択': 'Select a member',
        'ショーケースのキャラクターから選択してください': 'Choose a character from the showcase',
        'このメンバーを外す': 'Remove this member',
        'メンバーを追加': 'Add member',
        'このメンバーを変更': 'Change this member',
        '変更': 'Change',
        'クリックしてメンバーを追加': 'Click to add a member',
    };

    /* 動的メッセージ（数値を含む等）のパターン英訳 */
    const EN_PATTERNS = [
        [/^Enka APIのクールタイム中です（あと(\d+)秒）。しばらく待ってから再試行してください。$/, 'Enka API cooldown active ($1s left). Please wait a moment and try again.'],
        [/^カード生成が混雑しています。しばらくしてから再試行してください。$/, 'Card generation is busy. Please try again shortly.'],
        [/^カード生成がタイムアウトしました（(\d+)秒）。時間をおいて再試行してください。$/, 'Card generation timed out ($1s). Please try again later.'],
        [/^送信が頻繁すぎます。しばらく待ってから再試行してください。$/, 'Too many requests. Please wait a moment and try again.'],
        [/^再取得（(\d+)s）$/, 'Refresh ($1s)'],
        [/^(\d+)分前$/, '$1 min ago'],
        [/^(\d+)時間前$/, '$1 h ago'],
        [/^(\d+)日前$/, '$1 d ago'],
        [/^(.+)を削除しますか？$/, 'Delete $1?'],
        [/^ショーケースデータの最終取得: (.+)（サーバーキャッシュ）\nEnka API クールタイム: (\d+)秒$/, 'Last showcase fetch: $1 (server cache)\nEnka API cooldown: $2s'],
    ];

    function getLang() {
        try {
            return localStorage.getItem(LANG_KEY) === 'en' ? 'en' : 'ja';
        } catch (e) {
            return 'ja';
        }
    }

    function isEn() {
        return getLang() === 'en';
    }

    function patternTranslate(text) {
        if (!text) return null;
        for (let i = 0; i < EN_PATTERNS.length; i++) {
            const m = text.match(EN_PATTERNS[i][0]);
            if (m) return EN_PATTERNS[i][1].replace(/\$(\d)/g, (_, d) => (m[+d] != null ? m[+d] : ''));
        }
        return null;
    }

    function t(jaText) {
        if (!isEn()) return jaText;
        if (Object.prototype.hasOwnProperty.call(EN, jaText)) return EN[jaText];
        return patternTranslate(jaText) || jaText;
    }

    function _applyAttr(root, attr, propName) {
        root.querySelectorAll('[' + attr + ']').forEach((el) => {
            const origKey = 'i18nOrig' + propName;
            if (el.dataset[origKey] === undefined) {
                el.dataset[origKey] = el.getAttribute(propName) || '';
            }
            const orig = el.dataset[origKey];
            if (propName === 'aria-label') {
                el.setAttribute('aria-label', isEn() ? (EN[orig] || orig) : orig);
            } else {
                el.setAttribute(propName, isEn() ? (EN[orig] || orig) : orig);
            }
        });
    }

    function apply(root) {
        root = root || document;
        try { document.documentElement.lang = isEn() ? 'en' : 'ja'; } catch (e) {}

        // 変更がある場合のみ書き込む（変更なしで MutationObserver が再発火するのを防ぐ）
        root.querySelectorAll('[data-i18n]').forEach((el) => {
            if (el.dataset.i18nOrig === undefined) el.dataset.i18nOrig = el.textContent;
            const orig = el.dataset.i18nOrig;
            const target = t(orig);
            if (el.textContent !== target) el.textContent = target;
        });
        root.querySelectorAll('[data-i18n-html]').forEach((el) => {
            if (el.dataset.i18nHtmlOrig === undefined) el.dataset.i18nHtmlOrig = el.innerHTML;
            const orig = el.dataset.i18nHtmlOrig;
            const target = isEn() ? (EN[orig] || orig) : orig;
            if (el.innerHTML !== target) el.innerHTML = target;
        });
        root.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
            if (el.dataset.i18nPhOrig === undefined) el.dataset.i18nPhOrig = el.getAttribute('placeholder') || '';
            const orig = el.dataset.i18nPhOrig;
            const target = isEn() ? (EN[orig] || orig) : orig;
            if (el.getAttribute('placeholder') !== target) el.setAttribute('placeholder', target);
        });
        root.querySelectorAll('[data-i18n-title]').forEach((el) => {
            if (el.dataset.i18nTitleOrig === undefined) el.dataset.i18nTitleOrig = el.getAttribute('title') || '';
            const orig = el.dataset.i18nTitleOrig;
            const target = isEn() ? (EN[orig] || orig) : orig;
            if (el.getAttribute('title') !== target) el.setAttribute('title', target);
        });
        root.querySelectorAll('[data-i18n-aria]').forEach((el) => {
            if (el.dataset.i18nAriaOrig === undefined) el.dataset.i18nAriaOrig = el.getAttribute('aria-label') || '';
            const orig = el.dataset.i18nAriaOrig;
            const target = isEn() ? (EN[orig] || orig) : orig;
            if (el.getAttribute('aria-label') !== target) el.setAttribute('aria-label', target);
        });
    }

    // 動的に作り直された data-i18n 要素にも翻訳を自動適用する。
    // （キャラ差し替え列など、言語切替後に JS で再描画される箇所のため）
    let _applyQueued = false;
    const _i18nObserver = new MutationObserver(() => {
        if (_applyQueued) return;
        _applyQueued = true;
        queueMicrotask(() => {
            _applyQueued = false;
            apply(document);
        });
    });
    try {
        _i18nObserver.observe(document.documentElement, { childList: true, subtree: true });
    } catch (e) { /* 古い環境では無視（langchange 時の apply で代替） */ }

    function setLang(lang) {
        try {
            localStorage.setItem(LANG_KEY, lang === 'en' ? 'en' : 'ja');
        } catch (e) {}
        apply(document);
        try {
            document.dispatchEvent(new CustomEvent('genshin:langchange', { detail: { lang: getLang() } }));
        } catch (e) {}
    }

    window.GenshinI18n = {
        getLang: getLang,
        isEn: isEn,
        setLang: setLang,
        t: t,
        apply: apply,
        EN: EN,
    };

    // DOM 準備後に一度適用（<html lang> も更新）
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => apply(document));
    } else {
        apply(document);
    }
})();
