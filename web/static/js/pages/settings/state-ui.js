// 61b-3: 合法 tab id ↔ URL hash fragment（CD-61-1，防 typo）。
// 非法 hash / localStorage 值一律 fallback 預設 'display'。
const SETTINGS_TAB_IDS = ['display', 'scraping', 'sources', 'organize', 'translate', 'advanced'];

export function stateUI() {
    return {
        // ===== UI State =====
        // 61b-3: 當前 tab（預設第一個 tab；初始化由 _initActiveTab 處理，禁加 init()）
        activeTab: 'display',
        newSuffixInput: '',
        showPathHelp: false,
        showSampleImagesHelp: false,

        // Toast state
        _toast: { message: '', type: 'success', visible: false },
        _toastTimer: null,

        // Dirty Check Modal State
        dirtyCheckModalOpen: false,

        // Reset Config Modal State (T3.4)
        resetConfigModalOpen: false,
        _resetConfigLoading: false,

        // B1: Scanner directory link state
        favoriteScannerLink: null,   // null=隱藏, {linked, matched_directory}=已查
        showDirDropdown: false,
        scannerDirectories: [],

        // ===== Methods =====
        showToast(message, type = 'success', duration = 2500) {
            this._toast.message = message;
            this._toast.type = type;
            this._toast.visible = true;
            if (this._toastTimer) clearTimeout(this._toastTimer);
            this._toastTimer = setTimeout(() => {
                this._toast.visible = false;
                this._toastTimer = null;
            }, duration);
        },

        async selectOutputFolder() {
            if (typeof window.pywebview === 'undefined' || !window.pywebview.api) {
                this.showToast(window.t('settings.toast.desktop_only'), 'info');
                return;
            }

            try {
                const result = await window.pywebview.api.select_folder();
                if (result && result.folder) {
                    this.form.avlistOutputDir = result.folder;
                }
            } catch (e) {
                console.error('選擇資料夾失敗:', e);
            }
        },

        // Dirty check modal — 儲存更改後離開
        async dirtyCheckSave() {
            await this.saveConfig();
            // saveConfig 成功會更新 savedState，isDirty 變 false
            if (!this.isDirty) {
                // 儲存成功，透過 lifecycle API 執行 cleanup 再跳轉
                this.dirtyCheckModalOpen = false;
                if (window.__leavePage) {
                    if (!window.__leavePage(this.pendingNavigationUrl)) return;
                }
                window.location.href = this.pendingNavigationUrl;
            }
            // 儲存失敗：modal 保持開啟，toast 已顯示錯誤
            // 用戶可選「不儲存」離開或「取消」留下
        },

        // Dirty check modal — 不儲存直接離開
        dirtyCheckDiscard() {
            this.savedState = null;  // 防止殘留
            // T3(40b): 透過 lifecycle API 執行 cleanup 再跳轉
            // __leavePage 回傳 false 表示 cleanup 阻止導航（例如仍有進行中請求）
            if (window.__leavePage) {
                if (!window.__leavePage(this.pendingNavigationUrl)) return;
            }
            window.location.href = this.pendingNavigationUrl;
        },

        // Dirty check modal — 取消（留在 settings）
        dirtyCheckCancel() {
            this.pendingNavigationUrl = '';
            this.dirtyCheckModalOpen = false;
        },

        // ─── 61b-3: activeTab / URL hash / localStorage ───────────────────
        // ⚠️ 具名 init helper（禁加 stateUI.init() — 會覆蓋 stateConfig.init）。
        // 由 state-config.js init() 末尾呼叫，比照 _initB1 慣例。
        _initActiveTab() {
            // 優先序：URL hash > localStorage('settings_active_tab') > 'display'
            let resolved = 'display';

            // 1) URL hash（去掉前導 #）
            const hashId = (location.hash || '').replace(/^#/, '');
            if (SETTINGS_TAB_IDS.includes(hashId)) {
                resolved = hashId;
            } else {
                // 2) localStorage（隱私模式 / storage 不可用會拋，包 try-catch）
                try {
                    const stored = localStorage.getItem('settings_active_tab');
                    if (SETTINGS_TAB_IDS.includes(stored)) {
                        resolved = stored;
                    }
                } catch (e) {
                    console.warn('[settings] read settings_active_tab failed:', e);
                }
            }

            this.activeTab = resolved;

            // tab 變更 → 記憶 + 同步 URL（replaceState，不堆瀏覽歷史）
            this.$watch('activeTab', (val) => {
                if (!SETTINGS_TAB_IDS.includes(val)) return;
                try {
                    localStorage.setItem('settings_active_tab', val);
                } catch (e) {
                    console.warn('[settings] write settings_active_tab failed:', e);
                }
                history.replaceState(null, '', '#' + val);
            });

            // 外部深連結（如 /settings#translate）：監聽 hashchange
            window.addEventListener('hashchange', () => {
                const id = (location.hash || '').replace(/^#/, '');
                if (SETTINGS_TAB_IDS.includes(id) && id !== this.activeTab) {
                    this.activeTab = id;
                }
            });
        },

        // ─── B1: Scanner directory link ───────────────────────────────────

        async _initB1() {
            try {
                const cfg = await fetch('/api/config').then(r => r.json());
                // response 結構：{success, data: {gallery: {directories}}}
                this.scannerDirectories =
                    (cfg.data && cfg.data.gallery && cfg.data.gallery.directories) || [];
            } catch (e) {
                console.error('[B1] _initB1: fetch /api/config failed', e);
                this.scannerDirectories = [];
            }
            // $watch 在 Alpine init() hook 內才能呼叫（由 state-config.js init() 呼叫 _initB1 後掛）
            this.$watch('form.searchFavoriteFolder', () => this.refreshScannerLink());
            // 初始刷新一次（反映 loadConfig 填好的值）
            await this.refreshScannerLink();
        },

        async refreshScannerLink() {
            const fav = (this.form && this.form.searchFavoriteFolder) || '';
            if (!fav.trim()) {
                this.favoriteScannerLink = null;
                return;
            }
            try {
                const resp = await fetch(
                    '/api/settings/favorite-scanner-link?favorite=' + encodeURIComponent(fav)
                );
                this.favoriteScannerLink = await resp.json();
            } catch (e) {
                console.error('[B1] refreshScannerLink failed', e);
                this.favoriteScannerLink = null;
            }
        },

        pickScannerDirectory(dir) {
            if (this.form) this.form.searchFavoriteFolder = dir;
            this.showDirDropdown = false;
            this.refreshScannerLink();
        },
    };
}
