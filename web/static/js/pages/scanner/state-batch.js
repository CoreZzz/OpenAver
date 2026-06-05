export function stateBatch() {
    return {
        // ===== Missing NFO/Cover Enrich =====
        missingPillVisible: false,
        missingBothCount: 0,
        missingNfoCount: 0,
        missingCoverCount: 0,
        missingItems: [],
        missingEnrichOffset: 0,
        missingEnrichSuccess: 0,
        missingEnrichFailed: 0,
        missingEnrichJobId: '',
        missingEnrichPollTimer: null,
        _missingEnrichLastLogSeq: 0,
        resumePillVisible: false,
        missingConfirmModalOpen: false,

        get missingPillLabel() {
            const parts = [];
            if (this.missingBothCount > 0) {
                parts.push(window.t('scanner.stats.missing_both_prefix') + ' ' + this.missingBothCount + window.t('scanner.stats.missing_suffix'));
            }
            if (this.missingNfoCount > 0) {
                parts.push(window.t('scanner.stats.missing_nfo_prefix') + ' ' + this.missingNfoCount + window.t('scanner.stats.missing_suffix'));
            }
            if (this.missingCoverCount > 0) {
                parts.push(window.t('scanner.stats.missing_cover_prefix') + ' ' + this.missingCoverCount + window.t('scanner.stats.missing_suffix'));
            }
            return parts.join(' ');
        },

        get missingEnrichButtonText() {
            if (this.state === 'enriching') {
                return '<span class="loading loading-spinner loading-sm"></span> ' + window.t('scanner.stats.missing_enrich_loading');
            }
            return '<i class="bi bi-file-earmark-plus"></i> ' + window.t('scanner.stats.missing_enrich_idle');
        },

        async checkMissing() {
            try {
                const resp = await fetch('/api/gallery/missing-check');
                const result = await resp.json();
                if (!result.success) return;

                const d = result.data;
                if (d.total_missing > 0) {
                    this.missingBothCount = d.missing_both || 0;
                    this.missingNfoCount = d.missing_nfo || 0;
                    this.missingCoverCount = d.missing_cover || 0;
                    this.missingItems = Array.isArray(d.items) ? d.items : [];
                    this.missingPillVisible = true;
                    return;
                }

                this.missingBothCount = 0;
                this.missingNfoCount = 0;
                this.missingCoverCount = 0;
                this.missingItems = [];
                this.missingPillVisible = false;
            } catch (e) {
                console.error('checkMissing failed:', e);
            }
        },

        async runMissingEnrich({ skipConfirm = false } = {}) {
            if (this.isGenerating || this.missingItems.length === 0) return;

            if (!skipConfirm && this.missingItems.length > 500) {
                this.missingConfirmModalOpen = true;
                return;
            }

            this.state = 'enriching';
            this.missingEnrichOffset = 0;
            this.missingEnrichSuccess = 0;
            this.missingEnrichFailed = 0;
            this.progressStatus = window.t('scanner.stats.missing_enrich_loading');
            this.progressCurrent = 0;
            this.progressTotal = this.missingItems.length;
            this.clearLogs();
            this._missingEnrichLastLogSeq = 0;
            localStorage.removeItem('avlist_enrich_pending');

            try {
                const resp = await fetch('/api/gallery/missing-enrich/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ items: this.missingItems.slice() }),
                });
                if (!resp.ok) {
                    this.state = 'error';
                    this.showToast(window.t('scanner.stats.missing_enrich_error'), 'error');
                    return;
                }

                const result = await resp.json();
                if (!result.success) {
                    this.state = 'error';
                    this.showToast(window.t('scanner.stats.missing_enrich_error'), 'error');
                    return;
                }

                const terminal = this.applyMissingEnrichStatus(result.data, { finalToast: true });
                if (!terminal) {
                    this.startMissingEnrichPolling();
                }
            } catch (e) {
                console.error('runMissingEnrich error:', e);
                this.state = 'error';
                this.showToast(window.t('scanner.stats.missing_enrich_interrupted'), 'error');
            }
        },

        async restoreMissingEnrichStatus() {
            try {
                const resp = await fetch('/api/gallery/missing-enrich/status');
                if (!resp.ok) return;
                const result = await resp.json();
                if (!result.success || !result.data || result.data.state === 'idle') return;

                this.clearLogs();
                this._missingEnrichLastLogSeq = 0;
                const terminal = this.applyMissingEnrichStatus(result.data);
                if (!terminal && result.data.state === 'running') {
                    this.startMissingEnrichPolling();
                }
            } catch (e) {
                console.error('restoreMissingEnrichStatus failed:', e);
            }
        },

        startMissingEnrichPolling() {
            this.stopMissingEnrichPolling();
            this.missingEnrichPollTimer = setInterval(() => {
                this.pollMissingEnrichStatus();
            }, 1500);
        },

        stopMissingEnrichPolling() {
            if (this.missingEnrichPollTimer) {
                clearInterval(this.missingEnrichPollTimer);
                this.missingEnrichPollTimer = null;
            }
        },

        async pollMissingEnrichStatus() {
            try {
                const resp = await fetch('/api/gallery/missing-enrich/status');
                if (!resp.ok) return;
                const result = await resp.json();
                if (!result.success || !result.data || result.data.state === 'idle') return;

                const wasRunning = this.state === 'enriching';
                this.applyMissingEnrichStatus(result.data, { finalToast: wasRunning });
            } catch (e) {
                console.error('pollMissingEnrichStatus failed:', e);
            }
        },

        applyMissingEnrichStatus(status, { finalToast = false } = {}) {
            if (!status || status.state === 'idle') return false;

            this.missingEnrichJobId = status.job_id || '';
            this.missingEnrichOffset = status.current || 0;
            this.missingEnrichSuccess = status.success_count || 0;
            this.missingEnrichFailed = status.failed_count || 0;
            this.progressCurrent = status.current || 0;
            this.progressTotal = status.total || 0;
            this.progressStatus = status.message || window.t('scanner.stats.missing_enrich_loading');

            const logs = Array.isArray(status.logs) ? status.logs : [];
            logs.forEach((entry) => {
                const seq = Number(entry.seq || 0);
                if (seq <= this._missingEnrichLastLogSeq) return;
                this.addLog(entry.level || 'info', entry.message || '');
                this._missingEnrichLastLogSeq = seq;
            });
            if (logs.length > 0) {
                this.flushLogs();
            }

            if (status.state === 'running') {
                this.state = 'enriching';
                return false;
            }

            this.stopMissingEnrichPolling();
            localStorage.removeItem('avlist_enrich_pending');

            if (status.state === 'done') {
                this.state = 'done';
                this.progressStatus = status.message || window.t('scanner.stats.missing_enrich_done');
                this.progressCurrent = status.total || this.progressCurrent;
                if (finalToast) {
                    const summary = this.missingEnrichFailed > 0
                        ? window.t('scanner.stats.missing_enrich_toast_mixed', {
                            success: this.missingEnrichSuccess,
                            failed: this.missingEnrichFailed,
                        })
                        : window.t('scanner.stats.missing_enrich_toast_success', {
                            success: this.missingEnrichSuccess,
                        });
                    this.showToast(summary, this.missingEnrichFailed > 0 ? 'warn' : 'success');
                }
                this.checkMissing();
                return true;
            }

            if (status.state === 'error') {
                this.state = 'error';
                if (finalToast) {
                    this.showToast(window.t('scanner.stats.missing_enrich_interrupted'), 'error');
                }
                this.checkMissing();
                return true;
            }

            return false;
        },

        resumeMissingEnrich() {
            this.resumePillVisible = false;
            this.runMissingEnrich({ skipConfirm: true });
        },

        async confirmLargeMissingEnrich() {
            this.missingConfirmModalOpen = false;
            await this.runMissingEnrich({ skipConfirm: true });
        },

        cancelLargeMissingEnrich() {
            this.missingConfirmModalOpen = false;
        },

        dismissResume() {
            this.resumePillVisible = false;
            localStorage.removeItem('avlist_enrich_pending');
            this.checkMissing();
        },
    };
}
