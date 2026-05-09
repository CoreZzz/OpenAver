/**
 * state-clip.js — Showcase Clip Mode Alpine mixin（56c-T4）
 *
 * 從 web/static/js/pages/motion-lab/constellation-host.js 搬遷 carry-over
 * 機制（spec §1 CD-56C-7）：T4 onMainSwap / T4fix shimmer / T5 dust + corridor /
 * T6 hover reveal + guide rail / T7 idle pulse + keystone pulse。
 *
 * 三處改動（其餘原樣搬遷）：
 *   A. 圖片來源 — 不走 sc-{N}.jpg 隨機池，改從 this.clipResults[].cover_url 取
 *      （T4 用 mock data 對齊 SimilarCoversResponse contract，T5 換 API fetch）
 *   B. onMainSwap hook — t=0.30 mock 重抽 12 筆（T5 改 API fetch + image preload）
 *   C. onExit 行為 — 走 closeClipMode（playExit → ghost-fly back → fade-in lightbox）
 *
 * 命名規則（CD-56C-6）：
 *   - 公開 method 以 clip / Clip 標識（avoid state-lightbox naming clash）
 *   - 內部 _clip* 前綴標 private
 *
 * Lifecycle 銜接：
 *   - x-effect="clipModeOpen ? initClipStage() : destroyClipStage()" 觸發
 *   - initClipStage 開頭 await this.$nextTick()（Alpine 10 gotcha：x-for refs flush）
 *   - destroyClipStage cleanup breathing / timer / GSAP context（連續開關 5 次無殘留）
 *
 * Ghost-fly 整合：透過 window.GhostFly.play56c*（保持 caller pattern 與 state-lightbox
 * 一致；ESM 命名 export 不可用 — ghost-fly 對外只有 `export { GhostFly }` namespace）。
 */

import { _videos, _filteredVideos } from '@/showcase/state-base.js';
import {
  ANCHORS,
  pickEight,
  nearestNeighbors,
  railEndpoint,
} from '@/shared/constellation/anchors.js';
import {
  setRailCoords,
  railSweep,
  resetSweepLine,
} from '@/shared/constellation/rails.js';
import {
  playInitialExpand,
  playSlipThrough,
  playExit,
} from '@/shared/constellation/animations.js';
import { BreathingManager } from '@/shared/constellation/breathing.js';

// T6 (CD-T6-1)：hover corridor half-width（與 motion-lab host 同值 40px）
const HOVER_DISTANCE = 40;

/**
 * pointToSegmentDist — 點到線段最短距離（T6 CD-T6-1）
 * 與 motion-lab/constellation-host.js 內 helper 同義；shared/constellation/anchors.js
 * 不 export 此 helper，per-host 自帶。
 */
function pointToSegmentDist(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(px - x1, py - y1);
  const t = Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / len2));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

/**
 * CLIP_ANCHORS — Alpine x-for 用的 anchor 視圖（id + idShort）
 * 揭露成模組常數而非每次 Alpine init 重建，state-clip.js export 給 main.js mergeState 用。
 * idShort 用於 DOM id 對映（'#01' → '01' → 'clip-card-01'）。
 */
export const CLIP_ANCHORS = ANCHORS.map(a => ({
  id: a.id,
  idShort: a.id.slice(1),
}));

export function stateClip() {
  return {
    // ── Reactive state（CD-56C-6）─────────────────────────────────────────
    clipModeOpen: false,
    clipModeAnimating: false,
    clipQueryVideo: null,
    clipResults: [],
    clipVisibleSlots: new Set(),
    clipMainSlot: null,
    clipCards: {},        // slotId -> HTMLElement
    clipRailLines: {},    // slotId -> SVGLineElement
    clipSweepLines: {},   // slotId -> SVGLineElement (sweep overlay)
    clipBreathingManager: null,

    // ── Internal（_clip* 前綴標 private）────────────────────────────────
    _clipActiveTimeline: null,
    _clipIdleAcknowledgeTimer: null,
    _clipRailStarMap: {},
    _clipGsapCtx: null,
    _clipGeneration: 0,            // stale callback invalidation
    // 56c-T4: ghost-fly enter onComplete 後 park 到 .clip-stage-inner 變 static img
    // 取代既有 fixed ghost（resize-frozen），與 8 cards 同 layout 路徑 → resize-safe
    _clipMainStatic: null,
    _clipActiveFocusedRailId: null,
    _clipActiveNeighborRailIds: [],
    _clipActiveHoverSlot: null,
    _clipMainBreathTween: null,
    _clipLastDrilledNumber: null,   // T5/T6 closeClipMode silent switch 用（T4 不讀）
    _clipLastDrilledItem: null,    // 56c-fix-v2：snapshot clickedItem，避免 clipResults 替換後找不到
    clipModeMobileOpen: false,     // 56c-T7：手機 x-collapse toggle

    // 揭露給 Alpine template（x-for="anchor in CLIP_ANCHORS"）
    CLIP_ANCHORS,

    // ── Lifecycle（x-effect 觸發）─────────────────────────────────────────

    /**
     * initClipStage — x-effect 在 clipModeOpen=true 時觸發。
     * Alpine 10 gotcha：12 slot card / 12 rail 由 <template x-for> 渲染，
     * Alpine reactive flush 是 microtask，同 sync 路徑 querySelector 拿到 0 個。
     * 必須 await this.$nextTick() 才能取到 DOM refs（56b motion-lab host 已踩過）。
     */
    async initClipStage() {
      await this.$nextTick();
      const generation = ++this._clipGeneration;

      // 1. 建立 GSAP context（destroy 時 revert 收所有 ctx scope tween）
      if (window.OpenAver && window.OpenAver.motion && window.OpenAver.motion.createContext) {
        this._clipGsapCtx = window.OpenAver.motion.createContext(this.$el);
      }

      // 2. Build DOM refs + rail 端點座標 + cards 初始放中央
      ANCHORS.forEach(a => {
        const idShort = a.id.slice(1);
        this.clipCards[a.id] = document.getElementById('clip-card-' + idShort);
        this.clipRailLines[a.id] = document.getElementById('clip-rail-' + idShort);
        this.clipSweepLines[a.id] = document.getElementById('clip-sweep-' + idShort);
        if (this.clipRailLines[a.id]) {
          setRailCoords(this.clipRailLines[a.id], a);
        }
        if (this.clipCards[a.id]) {
          gsap.set(this.clipCards[a.id], { left: 480, top: 310 });
        }
      });

      // 3. BreathingManager（即使 PRM 也建，避免 hover 觸發 null 錯誤）
      this.clipBreathingManager = new BreathingManager(this.clipCards, this.clipRailLines, ANCHORS);

      // 4. T6 corridor pre-compute（必須在 dust 100 顆 mount 後）+ T7 idle timer
      this._buildClipRailStarMap();
      this._startClipIdleAcknowledge();

      // 5. PRM short-circuit 直呈終態（spec §2.6 C3）
      if (window.OpenAver && window.OpenAver.prefersReducedMotion) {
        this._setClipInitialState();
        return;
      }

      // 6. playInitialExpand（8 卡 stagger 從中央湧出）
      const initSlots = new Set(['#01', '#02', '#03', '#04', '#05', '#06', '#07', '#08']);
      this._clipActiveTimeline = playInitialExpand(this.clipCards, this.clipRailLines, initSlots, () => {
        if (generation !== this._clipGeneration) return; // destroyed during expand
        this._clipActiveTimeline = null;
        this.clipVisibleSlots = new Set(initSlots);
        if (this.clipBreathingManager) this.clipBreathingManager.start(initSlots);
        this._startClipMainBreath();
      });
    },

    /**
     * destroyClipStage — x-effect 在 clipModeOpen=false 時觸發。
     * 連續開關 5 次無殘留契約：breathing.stop + timer 清 + ctx.revert + ghost cleanup（caller 處理）
     */
    destroyClipStage() {
      this._clipGeneration += 1; // invalidate in-flight callbacks

      this._cancelClipIdleAcknowledge();

      if (this._clipActiveTimeline) {
        this._clipActiveTimeline.kill();
        this._clipActiveTimeline = null;
      }

      if (this.clipBreathingManager) {
        this.clipBreathingManager.stop();
        this.clipBreathingManager = null;
      }

      this._stopClipMainBreath();

      // 56c-T4: cleanup static if 殘留（連續開關 5 次契約）
      if (this._clipMainStatic) {
        this._clipMainStatic.remove();
        this._clipMainStatic = null;
      }

      // Phase transition：abort 全 100 顆 dust pulse（spec §5.2「強制暫停」）
      this._abortAllClipDustPulses();

      // 主動 killTweensOf host 直呼的 tween（gsap.context 不收 host module 層 tween）
      // 56c-T4fix7: 補清 --slot-dim-opacity inline style（ctx.revert 不清 CSS var inline）
      ANCHORS.forEach(a => {
        if (this.clipCards[a.id]) {
          gsap.killTweensOf(this.clipCards[a.id]);
          gsap.set(this.clipCards[a.id], { clearProps: '--slot-dim-opacity' });
        }
        if (this.clipRailLines[a.id]) gsap.killTweensOf(this.clipRailLines[a.id]);
        if (this.clipSweepLines[a.id]) gsap.killTweensOf(this.clipSweepLines[a.id]);
      });

      if (this._clipGsapCtx) {
        this._clipGsapCtx.revert();
        this._clipGsapCtx = null;
      }

      this.clipCards = {};
      this.clipRailLines = {};
      this.clipSweepLines = {};
      this.clipVisibleSlots = new Set();
      this._clipActiveFocusedRailId = null;
      this._clipActiveNeighborRailIds = [];
      this._clipActiveHoverSlot = null;
      this._clipRailStarMap = {};
    },

    // ── 主流程 ─────────────────────────────────────────────────────────

    /**
     * openClipMode — magic icon click → sparkle burst → stage mount → ghost-fly enter
     * spec §1 CD-56C-11：stage mount 在 ghost-fly 之前（stageInner.getBoundingClientRect 才有效）
     * spec §1 CD-56C-13：actress mode fail-safe（理論上 magic 按鈕在 actress mode 不顯，仍守一道）
     */
    async openClipMode() {
      if (this.clipModeAnimating) return;
      if (this.showFavoriteActresses) return;     // CD-56C-13 fail-safe
      if (!this.currentLightboxVideo) return;     // 無 lightbox video metadata 時 no-op

      // 56c-T7：手機降級 — viewport < 768px 不進 constellation
      if (window.innerWidth < 768) {
        if (this.clipModeAnimating) return;
        if (this.clipModeMobileOpen) {
          this.clipModeMobileOpen = false;   // toggle：再點收合
          return;
        }
        this.clipModeAnimating = true;
        try {
          const data = await this._fetchClipResults(this.currentLightboxVideo.number);
          this.clipResults = data.results;
          this.clipQueryVideo = data.query_video;
          this.clipModeMobileOpen = true;    // 展開 x-collapse
        } catch (_err) {
          // _fetchClipResults 已 showToast；clipModeMobileOpen 保持 false
        }
        this.clipModeAnimating = false;
        return;
      }

      this.clipModeAnimating = true;

      const coverEl = (this.$refs && this.$refs.lightboxCoverImg) || null;
      const lightboxEl = document.querySelector('.showcase-lightbox');
      const isPRM = !!(window.OpenAver && window.OpenAver.prefersReducedMotion);

      // 1. 並行：先發 fetch（縮短 perceived latency）+ sparkle burst（PRM 跳過）
      const fetchPromise = this._fetchClipResults(this.currentLightboxVideo.number);

      // 2. Sparkle burst 0.4s（PRM 跳過）— C23 per-callsite PRM guard
      const scanPromise = new Promise(resolve => {
        if (isPRM) return resolve();
        if (!coverEl) return resolve();
        const coverContainer = coverEl.closest('.lightbox-cover');
        if (!coverContainer) return resolve();
        if (window.GhostFly && typeof window.GhostFly.play56cClipScanPreview === 'function') {
          window.GhostFly.play56cClipScanPreview(coverContainer, resolve);
        } else {
          resolve();
        }
      });

      // Promise.all 等兩者完成；fetchPromise reject 則 Promise.all 立即 reject → catch 段攔截
      let data;
      try {
        [data] = await Promise.all([fetchPromise, scanPromise]);
      } catch (_err) {
        // _fetchClipResults 已 showToast，此處只重置 animating guard 並提前退出
        this.clipModeAnimating = false;
        return;
      }

      // 3. 填入 API 資料 + 重置鑽入歷史
      this.clipResults = data.results;
      this.clipQueryVideo = data.query_video;
      this._clipLastDrilledNumber = null;
      this._clipLastDrilledItem = null;    // 56c-fix-v2: reset snapshot

      // 4. Preload 前 8 張封面圖（避免空白幀）
      await this._preloadImages(data.results.slice(0, 8).map(r => r.cover_url));

      // 5. lightbox content fade-out + stage mount（cards/rails 仍 hidden；dust 開始閃爍）
      //    順序鐵律（spec §1 CD-56C-11）：先 mount stage 才能取 stageInner rect。
      if (lightboxEl) lightboxEl.classList.add('clip-mode-active');
      this.clipModeOpen = true;
      // 等 layout flush（讓 .clip-stage.show 生效，stageInner getBoundingClientRect 才有值）
      await new Promise(r => requestAnimationFrame(r));

      // 5.5. 56c-T4: 算 scale + 寫 CSS variable（必須在 ghost-fly enter 之前，
      //      因 ghost-fly target 公式從 scaled stageRect 反推 scale）
      const stageEl = document.querySelector('.clip-stage');
      if (stageEl) {
        const scale = this._calcClipStageScale();
        stageEl.style.setProperty('--clip-stage-scale', String(scale));
        // 等下一 frame 讓 transform 生效，stageInner.getBoundingClientRect() 才回 scaled rect
        await new Promise(r => requestAnimationFrame(r));
      }

      // 6. ghost-fly enter（C22 顯式檢查，禁用 ?.() 短路：fail open 會讓 stage 永遠 mount 不起來）
      // 56c-T4: 進場後 ghost park 成 .clip-stage-inner 子元素（resize-safe）
      const stageInnerEl = document.querySelector('.clip-stage-inner');
      if (isPRM) {
        // PRM 路徑：跳過 ghost-fly enter，直接建 static 終態 src
        const src = coverEl ? coverEl.src : '';
        this._clipMainStatic = this._buildClipMainStatic(src);
      } else if (
        coverEl &&
        stageInnerEl &&
        window.GhostFly &&
        typeof window.GhostFly.play56cConstellationEnter === 'function'
      ) {
        await new Promise(resolve => {
          window.GhostFly.play56cConstellationEnter(coverEl, stageInnerEl, {
            onComplete: (ghost) => {
              // 56c-T4: ghost 飛到中央後，park 成 .clip-stage-inner 子元素
              // → resize-safe + 與 cards 同 layout 路徑
              const src = (ghost && ghost.src) || (coverEl && coverEl.src) || '';
              this._clipMainStatic = this._buildClipMainStatic(src);
              // cleanup fly ghost（cleanupGhost 還原 lightbox coverImg opacity）
              if (ghost && window.GhostFly && typeof window.GhostFly.cleanupGhost === 'function') {
                window.GhostFly.cleanupGhost(ghost, coverEl);
              }
              resolve();
            },
          });
        });
      }
      // ghost 抵達中央後 initClipStage（x-effect 已在 step 3 觸發）內部正在 / 已完成
      // playInitialExpand。clipModeAnimating 在此 release，hover/click 可重新接受。
      this.clipModeAnimating = false;
    },

    /**
     * closeClipMode — playExit → ghost-fly back → lightbox fade-in
     * 4 路徑退出（spec §1 CD-56C-4）：ESC（T6 路由）/ X / 背景 / 點主圖非 play 區
     */
    async closeClipMode() {
      if (this.clipModeAnimating) return;
      this.clipModeAnimating = true;

      const lightboxEl = document.querySelector('.showcase-lightbox');
      const targetCoverEl = (this.$refs && this.$refs.lightboxCoverImg) || null;
      const isPRM = !!(window.OpenAver && window.OpenAver.prefersReducedMotion);

      // 1. playExit（8 卡 fade + rails fade；mainImg ghost 由 ghost-fly 獨立處理，傳 null）
      await new Promise(resolve => {
        if (isPRM) {
          // PRM：直呈終態，不 tween（避免 playExit 的 stagger 仍走 GSAP timeline）
          // playExit 內部仍會處理 PRM；保留呼叫並 resolve
        }
        const tl = playExit(
          this.clipCards,
          this.clipRailLines,
          this.clipVisibleSlots,
          null, // mainImg ghost 走獨立路徑
          () => resolve()
        );
        this._clipActiveTimeline = tl;
      });
      this._clipActiveTimeline = null;

      // 2. 56c-T4: 從 static 當前 viewport rect 新建臨時 fly ghost 起飛
      //    gotchas C25 順序鐵律：先 read static rect → 建 fly ghost → 才 remove static
      // 56c-T4: PRM 對稱 — 進場 PRM 已直建 static 跳過 ghost-fly，
      //   退場 PRM 也必須直接 cleanup，不建 fly ghost、不播 0.333s 飛行動畫
      //   （否則違反 PRM 契約：用戶看到原本不該看到的動畫）
      if (isPRM && this._clipMainStatic) {
        this._clipMainStatic.remove();
        this._clipMainStatic = null;
      } else if (
        this._clipMainStatic &&
        targetCoverEl &&
        window.GhostFly &&
        typeof window.GhostFly.createCoverGhost === 'function' &&
        typeof window.GhostFly.play56cConstellationExit === 'function'
      ) {
        const staticRect = this._clipMainStatic.getBoundingClientRect();
        const staticSrc = this._clipMainStatic.src;
        // 起飛 ghost：cropMode 'full' 不可用 'right-half'（static 已是 right-half crop，
        // 再切半會錯位）；手動 set objectPosition 維持右半視覺
        const flyGhost = window.GhostFly.createCoverGhost(staticSrc, staticRect, {
          cropMode: 'full',
          parent: document.querySelector('.clip-stage') || document.body,
        });
        if (flyGhost) {
          flyGhost.style.objectPosition = 'right center';
        }
        // 建好 fly ghost 才 remove static（C25 順序鐵律）
        this._clipMainStatic.remove();
        this._clipMainStatic = null;
        if (flyGhost) {
          await new Promise(resolve => {
            window.GhostFly.play56cConstellationExit(flyGhost, targetCoverEl, {
              onComplete: () => resolve(),
            });
          });
        }
      } else if (this._clipMainStatic) {
        // graceful: GhostFly 缺失也要 cleanup static
        this._clipMainStatic.remove();
        this._clipMainStatic = null;
      }

      // 3. silent switch lightbox to last drilled-into video（T5：CD-56C-12）
      // 必須在 clip-mode-active 移除前完成，確保 currentLightboxVideo 已更新
      // 才觸發 lightbox content fade-in（顯示正確影片）
      //
      // 56c-fix 方案 B：若 _clipLastDrilledNumber 不在 _filteredVideos（active filter 下 slip-through
      // 到 filter 外影片）→ findIndex = -1 → _silentSwitchLightboxByNumber no-op → lightbox 顯示舊影片。
      // 修法：找不到時改用 clipResults 的最後一個鑽入 item 當 standalone lightbox source（clipExitVideo）。
      if (this._clipLastDrilledNumber) {
        const drilledIdx = _filteredVideos.findIndex(v => v.number === this._clipLastDrilledNumber);
        if (drilledIdx >= 0) {
          // 找到 → 沿用既有邏輯（_setLightboxIndex + currentLightboxVideo 同步）
          this._silentSwitchLightboxByNumber(this._clipLastDrilledNumber);
        } else {
          // 找不到 → standalone 模式：用 _clipLastDrilledItem snapshot（v2 修法；
          // v1 用 clipResults.find 常找不到 — clipResults 在 onComplete 已替換為 clickedItem 的鄰居，
          // 不保證包含 clickedItem 本身，導致 fallback no-op 回原始 lightbox。）
          const drilledItem = this._clipLastDrilledItem;
          if (drilledItem) {
            // actresses 在 clipResults 是 array，lightbox template 用 .split(',') 解析；轉字串對齊
            const actressStr = Array.isArray(drilledItem.actresses)
              ? drilledItem.actresses.join(', ')
              : (drilledItem.actresses || '');
            this.clipExitVideo = {
              number:    drilledItem.number,
              title:     drilledItem.title,
              cover_url: drilledItem.cover_url,
              actresses: actressStr,
              // path 故意留 undefined — lightbox template 用 ?.path guard，path 缺失時
              // user-tags 區、play/open-folder 按鈕靜默不渲染（方案 1 optional-chaining）
            };
            // currentLightboxVideo 直接指向 clipExitVideo（_silentSwitchLightboxByNumber 不呼叫）
            this.currentLightboxVideo = this.clipExitVideo;
          } else {
            // clipResults 裡也找不到（理論上不發生）→ 靜默，顯示進場前舊影片（同 no-op 行為）
          }
        }
      } else {
        // 無 slip-through（進 clip 後直接關）→ 不動，沿用既有行為
        this._silentSwitchLightboxByNumber(this._clipLastDrilledNumber);
      }

      // 3.5. lightbox content fade-in（silent switch 完成後才移除 active class）
      if (lightboxEl) lightboxEl.classList.remove('clip-mode-active');

      // 3.6. 56c-T4: cleanup CSS variable（lifecycle 衛生，避免殘留；與 lightbox 顯示無關）
      const stageEl = document.querySelector('.clip-stage');
      if (stageEl) {
        stageEl.style.removeProperty('--clip-stage-scale');
      }

      // 5. unmount stage（x-effect 觸發 destroyClipStage cleanup）
      this.clipModeOpen = false;
      this.clipModeMobileOpen = false;  // 56c-T7：關閉 clip mode 時 reset，避免下次開 lightbox 殘留

      this.clipModeAnimating = false;
    },

    // ── 互動 ───────────────────────────────────────────────────────────

    /**
     * onClipCardClick — 點 8 顆可見卡 → slip-through → 新批
     * 56c-T4：mock 隨機重抽 12 筆（已在 openClipMode 寫入，此處 onMainSwap 無實質 swap）
     * 56c-T5：改為 API fetch + image preload + onMainSwap 在 t=0.30 替換 clipResults
     */
    async onClipCardClick(slotId) {
      if (this.clipModeAnimating || !this.clipVisibleSlots.has(slotId)) return;

      // 取得被點卡的 item（fetch 前先取，避免 slip-through 期間 clipResults 已被替換）
      const clickedItem = this._getSlotItem(slotId);
      if (!clickedItem) return;

      // 確認 clickedItem 有效後才中止 idle / dust（避免 early return 洩漏 UI 狀態）
      this._cancelClipIdleAcknowledge();
      // T7 phase transition：abort 全 100 顆 dust pulse（spec §5.2 強制暫停）
      this._abortAllClipDustPulses();

      // T5: clipModeAnimating = true 在 fetch await 之前設定（race guard：防連點穿透）
      this.clipModeAnimating = true;

      // T5: fetch + preload（PRM 不跳過 fetch，只動畫路徑走 PRM 短路）
      let newData;
      try {
        newData = await this._fetchClipResults(clickedItem.number);
      } catch (_err) {
        // _fetchClipResults 已 showToast；clip mode 保持開啟，只放棄本次 slip-through
        this.clipModeAnimating = false;
        return;
      }
      await this._preloadImages(newData.results.slice(0, 8).map(r => r.cover_url));

      const prevVisible = new Set(this.clipVisibleSlots);
      const nextVisible = pickEight(slotId, prevVisible, Math.random);
      const isPRM = !!(window.OpenAver && window.OpenAver.prefersReducedMotion);

      // PRM 短路（C23 per-callsite）：sync state update，不播動畫
      if (isPRM) {
        // PRM 路徑也需要完成資料替換（fetch 已做，這裡同步更新 clipResults / clipQueryVideo）
        this.clipResults = newData.results;
        this.clipQueryVideo = newData.query_video;
        if (this._clipMainStatic && clickedItem.cover_url) {
          this._clipMainStatic.src = clickedItem.cover_url;
        }
        if (this._clipActiveHoverSlot !== null) {
          this._resetClipHoverCard(this._clipActiveHoverSlot);
          this._clipActiveHoverSlot = null;
        }
        this._resetClipHoverRails();
        prevVisible.forEach(id => {
          const card = this.clipCards[id];
          if (card) {
            card.classList.add('slot--hidden');
            gsap.set(card, { opacity: 0 });
          }
        });
        nextVisible.forEach(id => {
          const anchor = ANCHORS.find(a => a.id === id);
          const card = this.clipCards[id];
          if (anchor && card) {
            card.classList.remove('slot--hidden');
            gsap.set(card, { left: anchor.x, top: anchor.y, opacity: 1, width: 120, height: 150 });
          }
        });
        ANCHORS.forEach(a => {
          const line = this.clipRailLines[a.id];
          if (!line) return;
          if (nextVisible.has(a.id)) {
            line.classList.remove('rail--hidden');
            // C26：clearProps 精確列表
            gsap.set(line, { opacity: 1, clearProps: 'strokeOpacity' });
          } else {
            line.classList.add('rail--hidden');
            gsap.set(line, { opacity: 0, clearProps: 'strokeOpacity' });
          }
        });
        this.clipMainSlot = slotId;
        this.clipVisibleSlots = nextVisible;
        this._clipLastDrilledNumber = clickedItem.number;
        this._clipLastDrilledItem = clickedItem;   // 56c-fix-v2: snapshot before clipResults swap
        this.clipModeAnimating = false;
        return;
      }

      // 停 main 自呼吸（C25 順序：先 ghost ref 已在 enter 取，這裡單純 absorb 期間 yoyo 殘留）
      this._stopClipMainBreath();

      // 同步清 hover 殘留 tween + 視覺 state（CD-56B-T2 codex P1 沿用）
      if (this._clipActiveHoverSlot !== null) {
        this._resetClipHoverCard(this._clipActiveHoverSlot);
        this._clipActiveHoverSlot = null;
      }
      this._resetClipHoverRails();

      this.clipVisibleSlots.forEach(id => {
        const rl = this.clipRailLines[id];
        if (rl) rl.classList.remove('rail--bright', 'rail--neighbor');
      });

      // T4fix codex round 4 P2-1：kill 上輪 survivor shimmer 的 untracked strokeOpacity tween
      prevVisible.forEach(id => {
        const line = this.clipRailLines[id];
        if (line) {
          gsap.killTweensOf(line, 'strokeOpacity');
          gsap.set(line, { clearProps: 'strokeOpacity' });
        }
      });

      // 同步清 hover card dim / scale 殘留（C26 精確 clearProps，禁用 'all'）
      // 56c-T4fix7: filter → --slot-dim-opacity（dim 路徑已改 CSS var）
      this.clipVisibleSlots.forEach(id => {
        const card = this.clipCards[id];
        if (!card) return;
        gsap.killTweensOf(card, 'scale,opacity,--slot-dim-opacity');
        if (id !== slotId) gsap.set(card, { opacity: 1 });
        gsap.set(card, { clearProps: 'scale,rotation,--slot-dim-opacity' });
      });

      // 停呼吸（slip-through 期間 ticker 不殘留）
      if (this.clipBreathingManager) this.clipBreathingManager.stop();

      const generation = this._clipGeneration;

      this._clipActiveTimeline = playSlipThrough(
        slotId,
        prevVisible,
        nextVisible,
        this.clipCards,
        this.clipRailLines,
        this._clipMainStatic,  // 56c-T4: 從 _clipMainGhost 改為 inner static img
        () => {
          if (generation !== this._clipGeneration) return; // destroyed during slip-through
          // codex-fix5: 移到 onComplete（t≈1.10s+），此時 pureExit 卡 opacity 已 0、
          // 主圖 ghost / clicked 卡都已 hidden、carry-over 在 fade-in 後完整就位。
          // onBeforeCardEnter 已 imperative 寫好所有 visible slot 的 img.src 為新批同 idx 的
          // cover_url，Alpine 重 evaluate 後值相同 → 無 flicker。
          // （fix3 把 swap 放 t=0.46 解決 clicked 中央閃換，但 pureExit 卡 opacity tween 直到
          // t=0.55 才結束，t=0.46~0.55 間 reactive rebind 還是會在 fading-out 卡上閃新圖，
          // fix5 把 swap 推到 timeline 結束才安全）
          this.clipResults = newData.results;
          this.clipQueryVideo = newData.query_video;
          this._clipActiveTimeline = null;
          this.clipMainSlot = slotId;
          this.clipVisibleSlots = nextVisible;
          this.clipModeAnimating = false;
          // T5: onComplete 內賦值（closure 抓 clickedItem ref，不從 this.clipResults 重抓）
          this._clipLastDrilledNumber = clickedItem.number;
          this._clipLastDrilledItem = clickedItem;   // 56c-fix-v2: snapshot before clipResults swap
          if (this.clipBreathingManager) this.clipBreathingManager.start(nextVisible);
          this._startClipMainBreath();

          // T4fix carry-over survivor shimmer
          const carryIds = [...prevVisible].filter(id => id !== slotId && nextVisible.has(id));
          this._playClipSurvivorShimmer(carryIds);

          // T5 enter rails sweep feedback（host onComplete 補；hover 不再呼叫 sweep）
          const enterRailIds = [...nextVisible].filter(id => !prevVisible.has(id));
          enterRailIds.forEach(id => {
            if (this.clipSweepLines[id] && this.clipRailLines[id]) {
              railSweep(this.clipSweepLines[id], this.clipRailLines[id]);
            }
          });

          // T7 keystone pulse + 重啟 idle timer
          this._fireClipKeystonePulse(slotId);
          this._startClipIdleAcknowledge();
        },
        // T5 onMainSwap：t=0.30 callback（main img fade-out 點換主圖 src）
        // codex-fix3: 拆分 onMainSwap（DOM 直寫）；codex-fix5: reactive swap 移至 onComplete
        {
          onMainSwap: () => {
            if (generation !== this._clipGeneration) return;
            // t=0.30: 只換主圖 DOM src（直寫不走 reactive）。
            // clipResults / clipQueryVideo 延後到 onComplete（codex-fix5）才 swap，
            // 避免 Alpine 把中央 clicked slot card 重綁新批圖（codex-fix3 rebind bug）。
            if (this._clipMainStatic && clickedItem.cover_url) {
              this._clipMainStatic.src = clickedItem.cover_url;
            }
          },
          // codex-fix4: 每張 enter/persist slot 在 reset callback（slot--hidden 期）由 host
          // imperative 設 img.src 為新批同 idx cover_url，避免 fade-in 初期讀舊批 clipResults
          // 顯示錯誤封面（fix3 把 swap 延到 t=0.46 解決 clicked card 中央閃換，但讓 fresh slot
          // 在 t=0.20~0.46 顯示舊批同 idx 圖片，這個 callback 是補上）
          onBeforeCardEnter: (slotId) => {
            if (generation !== this._clipGeneration) return;
            const slotIdx = parseInt(slotId.slice(1), 10) - 1;
            const item = newData.results[slotIdx];
            if (!item || !item.cover_url) return;
            const card = this.clipCards[slotId];
            if (!card) return;
            const imgEl = card.querySelector('.clip-slot-img');
            if (imgEl) imgEl.src = item.cover_url;
          },
        }
      );
    },

    /**
     * onClipCardHoverEnter — 從 motion-lab/constellation-host.js 搬遷，命名前綴改 clip
     */
    onClipCardHoverEnter(slotId) {
      if (this.clipModeAnimating || !this.clipVisibleSlots.has(slotId)) return;

      const isPRM = !!(window.OpenAver && window.OpenAver.prefersReducedMotion);

      // 防 enter→enter 殘留：先清舊張 card / dust class
      if (this._clipActiveHoverSlot && this._clipActiveHoverSlot !== slotId) {
        this._resetClipHoverCard(this._clipActiveHoverSlot);
        (this._clipRailStarMap[this._clipActiveHoverSlot] || [])
          .forEach(el => el.classList.remove('in-constellation'));
      }
      this._resetClipHoverRails();
      this._cancelClipIdleAcknowledge();

      // hover-pulse race fix：清 corridor 內仍在 pulse 的 dust
      this._abortClipActiveDustPulses(slotId);

      // ── State 操作（PRM 也執行，C3 契約）──
      // 1. corridor dust 切到 .in-constellation bright twinkle
      (this._clipRailStarMap[slotId] || []).forEach(el => el.classList.add('in-constellation'));

      // 2. Guide rail 終態（CD-T6-3）：strokeOpacity 0 → 0.10 極淡引導線
      const guideLine = this.clipRailLines[slotId];
      if (guideLine) {
        gsap.killTweensOf(guideLine, 'strokeOpacity,strokeWidth');
        if (isPRM) {
          gsap.set(guideLine, { strokeOpacity: 0.10, strokeWidth: 1.0 });
        } else {
          gsap.set(guideLine, { strokeWidth: 1.0 });
          gsap.to(guideLine, { strokeOpacity: 0.10, duration: 0.25, ease: 'fluent-decel' });
        }
      }
      this._clipActiveFocusedRailId = slotId;

      // ── Motion 操作（PRM 跳過 / 用 gsap.set 同步終態）──
      if (!isPRM) {
        if (this.clipBreathingManager) this.clipBreathingManager.pauseOne(slotId);
        gsap.to(this.clipCards[slotId], {
          scale: 1.06, duration: 0.18, ease: 'fluent', transformOrigin: '50% 50%',
        });
        // 56c-T4fix7: 改用 _applyHoverDim 一次性設定 8 卡目標 dim 狀態，
        // 取代 filter brightness 兩段式 race + compositing layer 重建
        this._applyClipHoverDim(slotId);
        const neighbors = nearestNeighbors(slotId, [...this.clipVisibleSlots], 3);
        neighbors.forEach(nid => {
          const nline = this.clipRailLines[nid];
          if (nline) {
            // 56c-T4fix7: 改 fromTo 去掉 entry pulse 0.70 瞬閃，消除 set→to 兩段閃感
            gsap.killTweensOf(nline, 'strokeOpacity');
            nline.classList.add('rail--neighbor');
            gsap.fromTo(nline,
              { strokeOpacity: 0 },
              {
                strokeOpacity: 0.55,
                duration: 0.20,
                ease: 'fluent-decel',
                onComplete: () => gsap.set(nline, { clearProps: 'strokeOpacity' }),
              }
            );
          }
        });
        this._clipActiveNeighborRailIds = neighbors;
      } else {
        if (this.clipCards[slotId]) gsap.set(this.clipCards[slotId], { scale: 1.06 });
        // PRM：_applyClipHoverDim 內 isPRM 分支走 gsap.set
        this._applyClipHoverDim(slotId);
      }

      this._clipActiveHoverSlot = slotId;
    },

    /**
     * onClipCardHoverLeave — 對稱 leave；stale guard 防舊張 leave 清掉新 hover state
     */
    onClipCardHoverLeave(slotId) {
      if (this.clipModeAnimating || !this.clipVisibleSlots.has(slotId)) return;
      if (this._clipActiveHoverSlot !== slotId) return; // stale leave guard

      this._resetClipHoverCard(slotId);
      this._resetClipHoverRails();
      this._clipActiveHoverSlot = null;

      // hover 結束 → idle 重新計時
      this._startClipIdleAcknowledge();
    },

    /**
     * onClipMainImgClick — 點主圖區：play button 內不退、其他位置退（CD-56C-4）
     */
    onClipMainImgClick(event) {
      if (event && event.target && event.target.closest && event.target.closest('.clip-play-button')) {
        return;
      }
      this.closeClipMode();
    },

    /**
     * 56c-T5 codex-fix1: 播放 clip mode 中央主圖對應的影片
     * 56c-T5 codex-fix2 (P1 二次修法): 改用 `_videos`（未過濾）read-only lookup，
     * 避免 filtered view 下 slip-through 到範圍外影片時 fallback 舊片。
     * _filteredVideos 是 Showcase 篩選後的子集；CLIP 結果來自全 DB，若影片被 filter 排除
     * 則 findIndex 回 -1，導致 play button 靜默 fallback 播放進場前的舊片（P1 bug）。
     * _videos（state-base.js:22 普通 JS Array）透過 _setVideos() in-place mutate，
     * 跨模組 reference 永遠有效，search scope 為全庫。
     *
     * slip-through 後主圖視覺已是新影片，但 currentLightboxVideo 直到 closeClipMode 才更新
     * （CD-56C-12 ordering）。play button 用 _videos read-only lookup 取對應 path，
     * 不呼叫 _setLightboxIndex（不違反 CD-56C-12：slip-through 期間不更新底層 lightbox state）。
     *
     * fallback 鏈：
     *   1. _clipLastDrilledNumber（最近一次 slip-through 鑽入的 number）
     *   2. clipQueryVideo.number（進入 clip mode 時的 query video）
     *   3. currentLightboxVideo.path（未 slip-through 過時最終 fallback）
     */
    playClipMainVideo() {
      // T5 codex-fix2 (P1 二次修法): _videos（未過濾全庫）read-only lookup，
      // 避免 filtered view 下 slip-through 到範圍外影片時 fallback 舊片。
      // （_silentSwitchLightboxByNumber 的 _filteredVideos 用法是 by design，CD-56C-12，不改）
      const targetNumber = this._clipLastDrilledNumber || this.clipQueryVideo?.number;
      if (targetNumber) {
        const found = _videos.find(v => v.number === targetNumber);
        if (found?.path) {
          this.playVideo(found.path);
          return;
        }
      }
      // fallback：未 slip-through 過 + clipQueryVideo 無 number → 播 lightbox 原影片
      const path = this.currentLightboxVideo?.path;
      if (!path) return;  // graceful no-op，不 toast（避免重複錯誤訊息）
      this.playVideo(path);
    },

    // ── 內部 helpers（T6/T7 carry-over，照搬 motion-lab）──────────────────

    /**
     * 56c-T4: 在 .clip-stage-inner 內建 main img static element
     * 取代 fixed ghost，享受 inner scale + flex centering 的 layout-driven resize 跟隨。
     *
     * codex P1-2 修：原本 hit area 走 .clip-main-overlay (z=2001 在 .clip-stage 直屬層)，
     * 但 .clip-play-button (z=12) 被 .clip-stage-inner 的 transform stacking context 囚禁，
     * overlay 在 root level 蓋過 play button → 點擊 button 實際打到 overlay。
     * 改在 main static <img> 直接掛 click → onClipMainImgClick；同 inner stacking 內
     * play button z=12 > main static z=11，button 仍在 main static 之上命中正確。
     * @click.stop 防冒泡的責任由 play button 的 Alpine handler 負責（已在 template）。
     */
    _buildClipMainStatic(src) {
      const stageInner = document.querySelector('.clip-stage-inner');
      if (!stageInner) return null;
      const img = document.createElement('img');
      img.className = 'clip-main-static';
      img.src = src || '';
      img.alt = '';
      img.setAttribute('aria-hidden', 'true');
      // codex P1-2: 取代已移除的 .clip-main-overlay click handler。
      // closeClipMode 會 .remove() 此 element，listener 隨 GC 一起清。
      img.addEventListener('click', (e) => this.onClipMainImgClick(e));
      stageInner.appendChild(img);
      return img;
    },

    /**
     * 56c-T4: 計算 clip stage 視覺 scale factor
     * design-space 維持 960×620 不動，CSS transform: scale(var(--clip-stage-scale,1))
     * 讓 inner stage 視覺層等比放大充滿 viewport。
     * cap 1.6 折衷視覺合理性（4K 不爆肥；1080p 約 80%×92% 充滿）。
     */
    _calcClipStageScale() {
      const sx = window.innerWidth  / 960;
      const sy = window.innerHeight / 620;
      return Math.min(sx, sy, 1.6);
    },

    /**
     * _getSlotItem — slotId('#01'..'#12') → clipResults[i] 對映（spec §6-A）
     * Alpine template 裡用 :src="(_getSlotItem && _getSlotItem(anchor.id))?.cover_url"
     */
    _getSlotItem(slotId) {
      if (!slotId) return null;
      const idx = parseInt(slotId.slice(1), 10) - 1;
      if (Number.isNaN(idx)) return null;
      return this.clipResults[idx] || null;
    },

    /**
     * _buildClipRailStarMap — T6 corridor 預算（HOVER_DISTANCE=40px 不變）
     * Selector 改 .clip-stage-dust circle（搬遷自 .clip-lab-dust circle）
     */
    _buildClipRailStarMap() {
      const dustEls = [...document.querySelectorAll('.clip-stage-dust circle')];
      this._clipRailStarMap = {};
      ANCHORS.forEach(a => {
        const ep = railEndpoint(a);
        this._clipRailStarMap[a.id] = dustEls.filter(el => {
          const cx = parseFloat(el.getAttribute('cx'));
          const cy = parseFloat(el.getAttribute('cy'));
          return pointToSegmentDist(cx, cy, 480, 310, ep.x, ep.y) <= HOVER_DISTANCE;
        });
      });
    },

    /**
     * _startClipIdleAcknowledge — T7 idle pulse 排程（8-15s 隨機）
     * PRM guard：reduced-motion 下首行 return，timer 永不啟動
     */
    _startClipIdleAcknowledge() {
      if (window.OpenAver && window.OpenAver.prefersReducedMotion) return;
      this._cancelClipIdleAcknowledge();
      const delay = Math.random() * 7000 + 8000; // 8000-15000 ms
      this._clipIdleAcknowledgeTimer = setTimeout(() => {
        this._fireClipIdlePulse();
        this._startClipIdleAcknowledge();
      }, delay);
    },

    _cancelClipIdleAcknowledge() {
      if (this._clipIdleAcknowledgeTimer) {
        clearTimeout(this._clipIdleAcknowledgeTimer);
        this._clipIdleAcknowledgeTimer = null;
      }
    },

    /**
     * _getClipKeystoneStars — corridor stars 中距 anchor 端最近的 N 顆（T7 CD-T7-1）
     */
    _getClipKeystoneStars(slotId, count) {
      count = count || 2;
      const anchor = ANCHORS.find(a => a.id === slotId);
      if (!anchor) return [];
      const stars = this._clipRailStarMap[slotId] || [];
      if (stars.length === 0) return [];
      return [...stars]
        .map(el => {
          const cx = parseFloat(el.getAttribute('cx'));
          const cy = parseFloat(el.getAttribute('cy'));
          const d = Math.hypot(cx - anchor.x, cy - anchor.y);
          return { el, d };
        })
        .sort((a, b) => a.d - b.d)
        .slice(0, count)
        .map(item => item.el);
    },

    /**
     * _fireClipKeystonePulse — T7 CD-T7-3：slip-through 完成後新主圖最近 1-2 顆 dust pulse
     */
    _fireClipKeystonePulse(slotId) {
      if (window.OpenAver && window.OpenAver.prefersReducedMotion) return;
      const stars = this._getClipKeystoneStars(slotId, 2);
      if (stars.length === 0) return;
      stars.forEach(el => {
        gsap.killTweensOf(el);
        el.classList.add('dust-pulsing');
        const baseSeed = parseFloat(el.style.getPropertyValue('--dust-base-seed')) || 0.08;
        gsap.timeline({
          onComplete: () => {
            // C26：clearProps 精確列表，禁用 'all'
            gsap.set(el, { clearProps: 'opacity,scale,transform' });
            el.classList.remove('dust-pulsing');
          },
        })
          .to(el, {
            opacity: 0.95, scale: 1.10, duration: 0.20,
            ease: 'fluent-decel', transformOrigin: '50% 50%',
          })
          .to(el, {
            opacity: baseSeed, scale: 1.0, duration: 0.20,
            ease: 'fluent-accel',
          });
      });
    },

    /**
     * _fireClipIdlePulse — T7 CD-T7-3：idle 8-15s 隨機抽 1 顆 non-bright dust pulse
     */
    _fireClipIdlePulse() {
      if (window.OpenAver && window.OpenAver.prefersReducedMotion) return;
      const dustEls = [...document.querySelectorAll('.clip-stage-dust circle')]
        .filter(el => !el.classList.contains('in-constellation'));
      if (dustEls.length === 0) return;
      const target = dustEls[Math.floor(Math.random() * dustEls.length)];
      const baseSeed = parseFloat(target.style.getPropertyValue('--dust-base-seed')) || 0.08;
      gsap.killTweensOf(target);
      target.classList.add('dust-pulsing');
      gsap.timeline({
        onComplete: () => {
          gsap.set(target, { clearProps: 'opacity,scale,transform' });
          target.classList.remove('dust-pulsing');
        },
      })
        .to(target, {
          opacity: 0.95, scale: 1.08, duration: 0.20,
          ease: 'fluent-decel', transformOrigin: '50% 50%',
        })
        .to(target, {
          opacity: baseSeed, scale: 1.0, duration: 0.20,
          ease: 'fluent-accel',
        });
    },

    /**
     * _abortClipActiveDustPulses — hover-pulse race fix（單 corridor scope）
     */
    _abortClipActiveDustPulses(slotId) {
      const corridor = this._clipRailStarMap[slotId] || [];
      corridor.forEach(el => {
        if (el.classList.contains('dust-pulsing')) {
          gsap.killTweensOf(el);
          gsap.set(el, { clearProps: 'opacity,scale,transform' });
          el.classList.remove('dust-pulsing');
        }
      });
    },

    /**
     * _abortAllClipDustPulses — phase transition（slip-through / exit 開始時全 100 顆強制暫停）
     */
    _abortAllClipDustPulses() {
      document.querySelectorAll('.clip-stage-dust circle.dust-pulsing').forEach(el => {
        gsap.killTweensOf(el);
        gsap.set(el, { clearProps: 'opacity,scale,transform' });
        el.classList.remove('dust-pulsing');
      });
    },

    /**
     * _resetClipHoverCard — hover card 視覺還原（scale + dim + breathing）
     * 56c-T4fix7: 移除 filter brightness 路徑，改呼叫 _resetClipHoverDim
     */
    _resetClipHoverCard(slotId) {
      const card = this.clipCards[slotId];
      const useSet = !!(window.OpenAver && window.OpenAver.prefersReducedMotion);

      if (card) {
        if (useSet) {
          gsap.set(card, { scale: 1.0 });
        } else {
          gsap.to(card, { scale: 1.0, duration: 0.18, ease: 'fluent' });
        }
      }
      // 56c-T4fix7: 一次性還原 8 卡 --slot-dim-opacity → 0
      this._resetClipHoverDim();
      if (!this.clipModeAnimating && !useSet && this.clipBreathingManager) {
        this.clipBreathingManager.resumeOne(slotId);
      }
    },

    /**
     * _resetClipHoverRails — focused rail strokeOpacity fade-out + dust class remove + neighbor 清
     */
    _resetClipHoverRails() {
      if (this._clipActiveFocusedRailId) {
        const focusedId = this._clipActiveFocusedRailId;
        const line = this.clipRailLines[focusedId];
        if (line) {
          gsap.killTweensOf(line, 'strokeOpacity,opacity,strokeWidth');
          line.classList.remove('rail--bright');
          if (window.OpenAver && window.OpenAver.prefersReducedMotion) {
            gsap.set(line, { strokeOpacity: 0, clearProps: 'strokeOpacity,strokeWidth' });
          } else {
            gsap.set(line, { clearProps: 'strokeWidth' });
            gsap.to(line, {
              strokeOpacity: 0,
              duration: 0.20,
              ease: 'fluent-accel',
              onComplete: () => gsap.set(line, { clearProps: 'strokeOpacity' }),
            });
          }
        }
        const sw = this.clipSweepLines[focusedId];
        if (sw) resetSweepLine(sw);
        (this._clipRailStarMap[focusedId] || [])
          .forEach(el => el.classList.remove('in-constellation'));
        this._clipActiveFocusedRailId = null;
      }
      this._clipActiveNeighborRailIds.forEach(nid => {
        const nline = this.clipRailLines[nid];
        if (nline) {
          gsap.killTweensOf(nline, 'strokeOpacity');
          nline.classList.remove('rail--neighbor');
          gsap.set(nline, { clearProps: 'strokeOpacity' });
        }
      });
      this._clipActiveNeighborRailIds = [];
    },

    /**
     * 56c-T4fix7: _applyClipHoverDim — 一次性設定 8 卡目標 dim 狀態
     * 取代 filter brightness 兩段式 race，消除 enter→enter 亮閃。
     * activeSlotId：hover 中的卡（dim=0）；其餘 7 卡 dim=1。
     * activeSlotId === null：reset 全還原（8 卡全部 dim=0），由 _resetClipHoverDim 呼叫。
     * overwrite: 'auto' 自動 stomp 前一輪同 property tween（取代 killTweensOf）。
     */
    _applyClipHoverDim(activeSlotId) {
      const isPRM = !!(window.OpenAver && window.OpenAver.prefersReducedMotion);
      this.clipVisibleSlots.forEach(id => {
        const card = this.clipCards[id];
        if (!card) return;
        const target = activeSlotId === null ? 0 : (id === activeSlotId ? 0 : 1);
        if (isPRM) {
          gsap.set(card, { '--slot-dim-opacity': target });
        } else {
          gsap.to(card, {
            '--slot-dim-opacity': target,
            duration: 0.20,
            ease: 'fluent',
            overwrite: 'auto',
          });
        }
      });
    },

    /**
     * 56c-T4fix7: _resetClipHoverDim — hover leave 全還原（8 卡 dim → 0）
     * 對稱 _applyClipHoverDim，由 _resetClipHoverCard 呼叫。
     */
    _resetClipHoverDim() {
      this._applyClipHoverDim(null);
    },

    /**
     * _resetAllClipSlotsToBaseline — 12 個 slot / rail / sweep 全回到 init 前 baseline
     * （onExit 後若上一輪含 #09-#12 殘留，避免成為隱形 hover/click 目標）
     */
    _resetAllClipSlotsToBaseline() {
      ANCHORS.forEach(a => {
        const card = this.clipCards[a.id];
        const line = this.clipRailLines[a.id];
        const sweep = this.clipSweepLines[a.id];
        if (card) {
          gsap.killTweensOf(card);
          card.classList.add('slot--hidden');
          card.classList.remove('rail--bright', 'rail--neighbor');
          gsap.set(card, {
            left: 480,
            top: 310,
            width: 120,
            height: 150,
            opacity: 0,
            zIndex: '',
            // 56c-T4fix7: 移除 filter，加 --slot-dim-opacity（C26 精確列表）
            clearProps: 'scale,rotation,transform,--card-glow-opacity,--slot-dim-opacity',
          });
        }
        if (line) {
          gsap.killTweensOf(line);
          line.classList.add('rail--hidden');
          line.classList.remove('rail--bright', 'rail--neighbor');
          gsap.set(line, { opacity: 0, clearProps: 'strokeOpacity,strokeWidth' });
        }
        if (sweep) resetSweepLine(sweep);
      });
      this._clipActiveFocusedRailId = null;
      this._clipActiveNeighborRailIds = [];
    },

    /**
     * _playClipSurvivorShimmer — T4fix carry-over 卡片 glow 雙脈衝 + rail strokeOpacity 短脈衝
     */
    _playClipSurvivorShimmer(carryIds) {
      if (window.OpenAver && window.OpenAver.prefersReducedMotion) return;
      carryIds.forEach(id => {
        const card = this.clipCards[id];
        const line = this.clipRailLines[id];
        if (card) {
          gsap.killTweensOf(card, '--card-glow-opacity');
          gsap.to(card, { '--card-glow-opacity': 0.85, duration: 0.25, ease: 'fluent-decel' });
          gsap.to(card, { '--card-glow-opacity': 0, duration: 0.40, ease: 'fluent', delay: 0.25 });
        }
        if (line) {
          gsap.killTweensOf(line, 'strokeOpacity');
          gsap.to(line, { strokeOpacity: 0.50, duration: 0.14, ease: 'fluent' });
          gsap.to(line, {
            strokeOpacity: 0.30,
            duration: 0.24,
            ease: 'fluent',
            delay: 0.14,
            onComplete: () => gsap.set(line, { clearProps: 'strokeOpacity' }),
          });
        }
      });
    },

    /**
     * _setClipInitialState — PRM short-circuit 終態（gsap.set 8 卡 + rails，不啟 breathing）
     */
    _setClipInitialState() {
      const initSlots = new Set(['#01', '#02', '#03', '#04', '#05', '#06', '#07', '#08']);
      initSlots.forEach(id => {
        const anchor = ANCHORS.find(a => a.id === id);
        const card = this.clipCards[id];
        const line = this.clipRailLines[id];
        if (anchor && card) {
          card.classList.remove('slot--hidden');
          gsap.set(card, { left: anchor.x, top: anchor.y, opacity: 1 });
        }
        if (line) {
          line.classList.remove('rail--hidden');
          gsap.set(line, { opacity: 1 });
        }
      });
      this.clipVisibleSlots = new Set(initSlots);
    },

    /**
     * _startClipMainBreath — main img 自呼吸（scale 1→1.018，5.6s sine.inOut yoyo）
     * 56c-T4: target 從 fixed ghost 改為 inner static img；GSAP scale tween
     * 直接 set transform，static 沒 CSS transform 衝突（.clip-main-static 不用 translate）
     * 56c plan §1 CD-56C-7「先保留」：視覺驗收後若覺得多餘再砍。
     */
    _startClipMainBreath() {
      if (window.OpenAver && window.OpenAver.prefersReducedMotion) return;
      if (!this._clipMainStatic) return;
      this._clipMainBreathTween = gsap.to(this._clipMainStatic, {
        scale: 1.018,
        duration: 5.6,
        ease: 'sine.inOut',
        yoyo: true,
        repeat: -1,
      });
    },

    _stopClipMainBreath() {
      if (this._clipMainBreathTween) {
        this._clipMainBreathTween.kill();
        this._clipMainBreathTween = null;
      }
      if (this._clipMainStatic) {
        // C26：clearProps 精確列表（static 用 left/top 定位，scale 由 GSAP 直設）
        gsap.set(this._clipMainStatic, { scale: 1 });
      }
    },

    // ── T5 新增 method ─────────────────────────────────────────────────────

    /**
     * onClipMobileCardClick — 手機 mobile card 點擊：fetch 新一批 + swap + silent switch
     * 56c-T7 CD-56C-8：手機無動畫 takeover，直接 reactive swap + silent switch（同 CD-56C-12 邏輯）
     */
    onClipMobileCardClick(item) {
      if (this.clipModeAnimating) return;
      if (!item) return;
      // T7 codex-fix1: 對齊 onClipCardClick race guard，fetch 前鎖定防連點覆蓋
      this.clipModeAnimating = true;
      this._fetchClipResults(item.number).then(data => {
        this.clipResults = data.results;
        this.clipQueryVideo = data.query_video;
        // Alpine x-for 自動 reactive，畫面 swap
        this._silentSwitchLightboxByNumber(item.number);
      }).catch(() => {
        // _fetchClipResults 已 showToast；x-for 不刷新，留原 4 張
      }).finally(() => {
        this.clipModeAnimating = false;
      });
    },

    /**
     * _fetchClipResults — fetch /api/similar-covers/by-number/{number}
     * 非 2xx → showToast + throw（呼叫端 catch 決定 fallback 行為）
     * T5 CD-56C-5：by-number 端點，limit=12
     */
    async _fetchClipResults(number) {
      const url = '/api/similar-covers/by-number/' + encodeURIComponent(number) + '?limit=12';
      const resp = await fetch(url);
      if (!resp.ok) {
        this.showToast(window.t('similar_mode.fetch_failed'), 'error');
        throw new Error('clip fetch failed: ' + resp.status);
      }
      return resp.json();
    },

    /**
     * _preloadImages — Promise.all 預載 urls 陣列
     * onerror = resolve：圖不存在也不阻塞；timeout 依賴 browser 預設行為
     */
    _preloadImages(urls) {
      return Promise.all((urls || []).map(url => new Promise(resolve => {
        if (!url) return resolve();
        const img = new Image();
        img.onload = img.onerror = resolve;
        img.src = url;
      })));
    },

    /**
     * _silentSwitchLightboxByNumber — closeClipMode 末尾呼叫，silent 切換 lightbox 到最後鑽入的影片
     * number = null → no-op（沒有鑽入過，留在原 lightbox 影片）
     * 找不到（被 filter 排除 / 不在 _filteredVideos 範圍）→ 靜默 no-op，不報錯
     * CD-56C-12
     */
    _silentSwitchLightboxByNumber(number) {
      if (!number) return;
      const idx = _filteredVideos.findIndex(v => v.number === number);
      if (idx >= 0) this._setLightboxIndex(idx);
    },
  };
}
