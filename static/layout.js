    // Shared helpers and the accessible confirm dialog, available to
    // every page extending this layout.
    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function escapeAttr(str) {
        return escapeHtml(str).replace(/'/g, '&#39;');
    }

    // Design-token colors for charts, read once per page so the palette
    // keeps a single source of truth in the CSS tokens.
    function dbsChartColors() {
        var styles = getComputedStyle(document.documentElement);
        return {
            navy: styles.getPropertyValue('--navy').trim() || '#1d2b53',
            red: styles.getPropertyValue('--brand-red').trim() || '#E51A3C',
            neutral: styles.getPropertyValue('--neutral').trim() || '#6c757d'
        };
    }

    // Reusable confirm modal
    let confirmAction = null;
    let confirmLastFocused = null;

    function showConfirmModal({ title, message, confirmLabel = 'Yes', onConfirm }) {
        confirmLastFocused = document.activeElement;
        document.getElementById('confirm-modal-title').textContent = title;
        document.getElementById('confirm-modal-message').textContent = message;
        const confirmButton = document.querySelector('#confirmModal .btn-danger');
        confirmButton.textContent = confirmLabel;
        confirmAction = onConfirm;
        document.getElementById('confirmModal').classList.add('show');
        confirmButton.focus();
    }

    function closeConfirmModal() {
        confirmAction = null;
        document.getElementById('confirmModal').classList.remove('show');
        if (confirmLastFocused && typeof confirmLastFocused.focus === 'function') {
            confirmLastFocused.focus();
        }
        confirmLastFocused = null;
    }

    function runConfirmModal() {
        const action = confirmAction;
        closeConfirmModal();
        if (action) action();
    }

    // The modal buttons belong to the layout, so their handlers live here.
    document.querySelector('#confirmModal .btn-danger').addEventListener('click', runConfirmModal);
    document.querySelector('#confirmModal .btn-neutral').addEventListener('click', closeConfirmModal);

    // Dialog behavior: Esc cancels; Tab cycles within the modal only.
    document.getElementById('confirmModal').addEventListener('keydown', function(event) {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeConfirmModal();
            return;
        }
        if (event.key !== 'Tab') return;

        const focusables = this.querySelectorAll(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });
