    // Data storage for the month detail table
    let monthDetailRows = [];
    let monthDetailSort = { field: 'bed', direction: 'asc' };
    let activeMonthReport = null;


    function lexicalCompare(a, b) {
        return a < b ? -1 : a > b ? 1 : 0;
    }

    function numericStringCompare(a, b) {
        const normalizedA = a.replace(/^0+/, '') || '0';
        const normalizedB = b.replace(/^0+/, '') || '0';
        return normalizedA.length - normalizedB.length || lexicalCompare(normalizedA, normalizedB);
    }

    function bedComparator(a, b) {
        const matchA = String(a).match(/^(\d+)(.*)$/);
        const matchB = String(b).match(/^(\d+)(.*)$/);

        if (!matchA || !matchB) {
            if (matchA) return -1;
            if (matchB) return 1;
            return lexicalCompare(String(a), String(b));
        }

        return numericStringCompare(matchA[1], matchB[1]) || lexicalCompare(matchA[2], matchB[2]);
    }

    // Single sort-field map driving both the comparator and the header
    // highlight. Unknown fields fall back to points, matching the old
    // final-else branch.
    const monthSortFields = {
        bed: { compare: (a, b) => bedComparator(a.bed, b.bed), headerIndex: 0 },
        name: { compare: (a, b) => a.display_name.localeCompare(b.display_name), headerIndex: 1 },
        frequency: { compare: (a, b) => a.frequency - b.frequency, headerIndex: 2 },
        minutes: { compare: (a, b) => a.total_minutes - b.total_minutes, headerIndex: 3 },
        points: { compare: (a, b) => a.total_points - b.total_points, headerIndex: 4 },
    };

    function compareMonthRows(a, b) {
        const entry = monthSortFields[monthDetailSort.field] || monthSortFields.points;
        let result = entry.compare(a, b);

        if (result === 0) {
            result = a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
        }
        return monthDetailSort.direction === 'asc' ? result : -result;
    }

    function sortMonthDetail(field) {
        if (monthDetailSort.field === field) {
            monthDetailSort.direction = monthDetailSort.direction === 'asc' ? 'desc' : 'asc';
        } else {
            monthDetailSort = { field: field, direction: 'asc' };
        }
        renderMonthDetail();
    }

    // Render month detail rows with the server-owned values in the selected order.
    function renderMonthDetail() {
        const tbody = document.getElementById('month-detail-body');
        const rows = [...monthDetailRows].sort(compareMonthRows);
        const headers = document.querySelectorAll('#month-detail-table thead th');
        headers.forEach(th => {
            th.classList.remove('sort-asc', 'sort-desc');
            th.setAttribute('aria-sort', 'none');
        });
        const sortEntry = monthSortFields[monthDetailSort.field];
        const sortIndex = sortEntry ? sortEntry.headerIndex : -1;
        if (sortIndex >= 0) {
            headers[sortIndex].classList.add(`sort-${monthDetailSort.direction}`);
            headers[sortIndex].setAttribute(
                'aria-sort',
                monthDetailSort.direction === 'asc' ? 'ascending' : 'descending'
            );
        }

        tbody.innerHTML = '';
        rows.forEach(row => {
            const tr = document.createElement('tr');
            if (row.frequency > 0) {
                tr.classList.add('month-report-late');
            }
            tr.innerHTML = `
                <td><strong>${escapeHtml(row.bed)}</strong></td>
                <td><a class="boarder-link" href="/boarder/${encodeURIComponent(row.name)}">${escapeHtml(row.display_name)}</a></td>
                <td>${row.frequency}</td>
                <td>${row.total_minutes}</td>
                <td>${row.total_points}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    // Tab switching logic
    const tabButtons = document.querySelectorAll('.tab-link');
    const panels = document.querySelectorAll('.panel');

    function activateTab(tabName) {
        tabButtons.forEach(btn => btn.classList.remove('active'));
        panels.forEach(panel => panel.classList.remove('active'));

        const button = document.querySelector(`.tab-link[data-tab="${tabName}"]`);
        if (button) button.classList.add('active');
        document.getElementById(tabName).classList.add('active');
    }

    tabButtons.forEach(button => {
        button.addEventListener('click', function(event) {
            const tabName = this.getAttribute('data-tab');
            if (tabName === 'punishments') {
                if (hasDirtyBoarderRow()) {
                    event.preventDefault();
                    showConfirmModal({
                        title: 'Discard changes?',
                        message: 'You have unsaved edits to a boarder. Discard them and open Punishments?',
                        confirmLabel: 'Discard',
                        onConfirm: () => {
                            discardBoarderEdits();
                            window.location.href = '/punishments';
                        }
                    });
                }
                return;
            }
            if (tabName === 'boarders') {
                activateTab('boarders');
                return;
            }
            if (hasDirtyBoarderRow()) {
                showConfirmModal({
                    title: 'Discard changes?',
                    message: 'You have unsaved edits to a boarder. Discard them and switch tabs?',
                    confirmLabel: 'Discard',
                    onConfirm: () => {
                        discardBoarderEdits();
                        activateTab(tabName);
                    }
                });
                return;
            }
            activateTab(tabName);
        });
    });

    // Session CSRF token for fetch mutations (HTML forms carry it as a
    // hidden field). Read once per call so tests can rotate the meta tag.
    function fetchCsrfToken() {
        const tag = document.querySelector('meta[name="csrf-token"]');
        return tag ? tag.content : '';
    }

    // Voiding a punishment is destructive and irreversible: route it through
    // the shared confirm dialog naming the Boarder and month. The optional
    // reason input stays in the form and submits with it.
    document.querySelectorAll('form.void-form').forEach(form => {        form.addEventListener('submit', function(event) {
            event.preventDefault();
            showConfirmModal({
                title: 'Void punishment?',
                message: `Void the ${form.dataset.month} punishment for ${form.dataset.boarder}? The voided punishment is kept for audit.`,
                confirmLabel: 'Void',
                onConfirm: () => form.submit()
            });
        });
    });

    // Month detail view
    let monthRequestToken = 0;

    function showMonthDetailError(messageText) {
        const errorEl = document.getElementById('month-detail-error');
        errorEl.textContent = messageText;
        errorEl.classList.remove('hidden');
    }

    function clearMonthDetailError() {
        document.getElementById('month-detail-error').classList.add('hidden');
    }

    // Print styles print the month-detail region only while a report is open.
    function setReportOpen(open) {
        document.body.classList.toggle('report-open', open);
    }

    function viewMonth(month) {
        const requestToken = ++monthRequestToken;
        activeMonthReport = null;
        monthDetailRows = [];
        monthDetailSort = { field: 'bed', direction: 'asc' };
        setReportOpen(false);
        document.getElementById('month-detail').classList.add('hidden');
        document.getElementById('month-detail-assign').classList.add('hidden');
        clearMonthDetailError();
        document.getElementById('month-detail-loading').classList.remove('hidden');
        renderMonthDetail();

        fetch(`/api/month/${encodeURIComponent(month)}`)
            .then(response => response.json())
            .then(data => {
                if (requestToken !== monthRequestToken) return;
                document.getElementById('month-detail-loading').classList.add('hidden');
                if (data.error) {
                    showMonthDetailError('Error: ' + data.error);
                    return;
                }

                // Store data and render in the selected order without mutating the API rows.
                activeMonthReport = month;
                monthDetailRows = data.boarders;
                setReportOpen(true);

                document.getElementById('month-detail-title').textContent = `Report for ${month}`;
                document.getElementById('month-detail-download').onclick = function() {
                    window.location.href = `/download_month/${encodeURIComponent(month)}`;
                };
                document.getElementById('month-detail-delete').onclick = function() {
                    showConfirmModal({
                        title: 'Delete report?',
                        message: `This deletes the ${month} Monthly Report. Punishments already issued for ${month} are kept.`,
                        confirmLabel: 'Yes, Delete',
                        onConfirm: () => deleteMonthReport(month)
                    });
                };
                document.getElementById('month-detail').classList.remove('hidden');
                document.getElementById('month-detail-assign').classList.add('hidden');
                const assignBtn = document.getElementById('month-detail-assign-btn');
                assignBtn.onclick = function() { showAssignPanel(month, data.boarders); };

                renderMonthDetail();
            })
            .catch(error => {
                if (requestToken !== monthRequestToken) return;
                document.getElementById('month-detail-loading').classList.add('hidden');
                console.error('Error:', error);
                showMonthDetailError('Failed to load month details.');
            });
    }

    // Clicking a month card opens its report detail
    document.querySelectorAll('.month-card').forEach(card => {
        card.addEventListener('click', function(event) {
            if (event.target.closest('button, a, form')) return;
            viewMonth(this.dataset.month);
        });
    });

    function closeMonthDetail() {
        activeMonthReport = null;
        setReportOpen(false);
        document.getElementById('month-detail').classList.add('hidden');
        document.getElementById('month-detail-assign').classList.add('hidden');
    }

    function showAssignPanel(month, boarders) {
        const panel = document.getElementById('month-detail-assign');
        const container = document.getElementById('assign-boarders');
        const counter = document.getElementById('assign-counter');
        const form = document.getElementById('assign-form');
        form.action = `/assign/${encodeURIComponent(month)}`;

        // Positive consent: every eligible boarder starts checked; unchecking
        // exempts them. Checked boxes submit as the `assign` selection.
        const lateBoarders = boarders.filter(row => row.total_points > 0);
        container.innerHTML = '';
        const checkboxes = [];
        function updateAssignCounter() {
            const count = checkboxes.filter(cb => cb.checked).length;
            counter.textContent = `${count} punishment${count === 1 ? '' : 's'} will be assigned.`;
        }

        lateBoarders.forEach(row => {
            const label = document.createElement('label');
            label.className = 'assign-boarder';
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.name = 'assign';
            checkbox.value = row.name;
            checkbox.checked = true;
            checkbox.addEventListener('change', updateAssignCounter);
            label.appendChild(checkbox);
            label.appendChild(document.createTextNode(` Assign punishment to ${row.display_name} (${row.total_points} pts)`));
            container.appendChild(label);
            checkboxes.push(checkbox);
        });

        if (lateBoarders.length === 0) {
            container.innerHTML = '<p>No boarders with points in this month.</p>';
        }
        updateAssignCounter();

        panel.classList.remove('hidden');
        panel.scrollIntoView({ behavior: 'smooth' });
    }

    function printCurrentMonthReport() {
        if (!activeMonthReport || document.getElementById('month-detail').classList.contains('hidden')) {
            alert('Open a report before printing.');
            return;
        }

        window.print();
    }

    // Delete a month report via AJAX (invoked from the confirm modal)
    function deleteMonthReport(month) {
        fetch(`/delete_month/${month}`, {
            method: 'DELETE',
            headers: { 'X-CSRF-Token': fetchCsrfToken() }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                closeMonthDetail();
                location.reload();
            } else {
                showMonthDetailError('Error: ' + (data.error || 'Failed to delete report'));
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showMonthDetailError('Failed to delete report.');
        });
    }

    // Boarders tab: explicit table editing, dirty-state, AJAX save/remove
    let boardersEditing = false;
    let boarderOriginals = new Map();
    const boarderEditButton = document.getElementById('boarder-edit');
    const boarderEditActions = document.getElementById('boarder-edit-actions');
    const boarderSaveButton = document.getElementById('boarder-save');
    const boarderCancelButton = document.getElementById('boarder-cancel');
    const boarderEditError = document.getElementById('boarder-edit-error');

    function boarderIsDirty(row) {
        const name = row.querySelector('.boarder-edit-name').value.trim();
        const bed = row.querySelector('.boarder-edit-bed').value.trim();
        const original = boarderOriginals.get(row.dataset.boarderId);
        return original && (name !== original.name || bed !== original.bed);
    }

    function hasDirtyBoarderRow() {
        if (!boardersEditing) return false;
        return Array.from(document.querySelectorAll('#boarders-table tbody tr')).some(boarderIsDirty);
    }

    function exitBoarderEditMode() {
        boardersEditing = false;
        boarderOriginals = new Map();
        boarderEditActions.hidden = true;
        boarderEditButton.disabled = false;
        boarderEditError.hidden = true;
        boarderEditError.textContent = '';
    }

    function renderBoarderStatic(row, staticValues = null) {
        const name = staticValues ? staticValues.name : row.querySelector('.boarder-edit-name').value.trim();
        const bed = staticValues ? staticValues.bed : row.querySelector('.boarder-edit-bed').value.trim();
        const key = row.dataset.boarderKey || '';
        row.innerHTML = `
            <td class="boarder-cell boarder-bed">${escapeHtml(bed)}</td>
            <td class="boarder-cell boarder-name"><a class="boarder-link" href="/boarder/${encodeURIComponent(key)}">${escapeHtml(name)}</a></td>
            <td class="boarder-actions"></td>
        `;
    }

    function discardBoarderEdits() {
        document.querySelectorAll('#boarders-table tbody tr').forEach(row => {
            const original = boarderOriginals.get(row.dataset.boarderId);
            if (original) renderBoarderStatic(row, original);
        });
        exitBoarderEditMode();
    }

    function enterBoarderEditMode() {
        if (boardersEditing) return;
        const rows = Array.from(document.querySelectorAll('#boarders-table tbody tr'));
        if (!rows.length) return;

        boardersEditing = true;
        boarderOriginals = new Map();
        boarderEditButton.disabled = true;
        boarderEditActions.hidden = false;
        rows.forEach(row => {
            const bed = row.querySelector('.boarder-bed').textContent.trim();
            const name = row.querySelector('.boarder-name').textContent.trim();
            boarderOriginals.set(row.dataset.boarderId, { name, bed });
            row.innerHTML = `
                <td class="boarder-bed">
                    <input class="boarder-edit-bed" type="text" value="${escapeAttr(bed)}" aria-label="Edit bed for ${escapeAttr(name)}">
                </td>
                <td class="boarder-name">
                    <input class="boarder-edit-name" type="text" value="${escapeAttr(name)}" aria-label="Edit name for ${escapeAttr(name)}">
                    <div class="boarder-inline-error" hidden></div>
                </td>
                <td class="boarder-actions">
                    <button type="button" class="btn btn-danger btn-sm boarder-remove" aria-label="Remove boarder" title="Remove boarder">
                        <svg viewBox="0 0 24 24" aria-hidden="true"><use href="#icon-trash"/></svg>
                    </button>
                </td>
            `;
            row.querySelector('.boarder-edit-name').addEventListener('input', updateBoarderSave);
            row.querySelector('.boarder-edit-bed').addEventListener('input', updateBoarderSave);
            row.querySelector('.boarder-remove').addEventListener('click', () => promptBoarderRemove(row));
            row.querySelectorAll('.boarder-edit-name, .boarder-edit-bed').forEach(input => {
                input.addEventListener('keydown', event => {
                    if (event.key === 'Escape') {
                        event.preventDefault();
                        discardBoarderEdits();
                    } else if (event.key === 'Enter') {
                        event.preventDefault();
                        if (!boarderSaveButton.disabled) saveBoarderEdits();
                    }
                });
            });
        });
        updateBoarderSave();
    }

    function updateBoarderSave() {
        if (!boardersEditing) return;
        const hasBlankValue = Array.from(document.querySelectorAll('#boarders-table tbody tr')).some(row => {
            return !row.querySelector('.boarder-edit-name').value.trim() || !row.querySelector('.boarder-edit-bed').value.trim();
        });
        boarderSaveButton.disabled = hasBlankValue;
        boarderEditError.hidden = true;
    }

    function saveBoarderEdits() {
        if (boarderSaveButton.disabled) return;
        const rows = Array.from(document.querySelectorAll('#boarders-table tbody tr'));
        if (!hasDirtyBoarderRow()) {
            rows.forEach(row => renderBoarderStatic(row));
            exitBoarderEditMode();
            return;
        }

        const updates = rows.map(row => ({
            id: Number(row.dataset.boarderId),
            name: row.querySelector('.boarder-edit-name').value.trim(),
            bed: row.querySelector('.boarder-edit-bed').value.trim()
        }));

        // Disabled while in flight so double-clicks cannot duplicate writes.
        boarderSaveButton.disabled = true;
        boarderSaveButton.textContent = 'Saving…';
        fetch('/api/boarders', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': fetchCsrfToken() },
            body: JSON.stringify({ boarders: updates })
        })
        .then(response => response.json())
        .then(data => {
            if (data.ok) {
                rows.forEach(row => renderBoarderStatic(row));
                exitBoarderEditMode();
            } else {
                boarderEditError.textContent = data.error || 'Failed to save boarders.';
                boarderEditError.hidden = false;
            }
        })
        .catch(() => {
            boarderEditError.textContent = 'Failed to save boarders.';
            boarderEditError.hidden = false;
        })
        .finally(() => {
            boarderSaveButton.textContent = 'Save';
            if (boardersEditing) updateBoarderSave();
        });
    }

    function promptBoarderRemove(row) {
        const name = row.querySelector('.boarder-edit-name').value.trim();
        showConfirmModal({
            title: 'Remove boarder?',
            message: `${name} will be dropped from the master list. Their history and punishments are kept, and future imports will no longer match them.`,
            confirmLabel: 'Remove',
            onConfirm: () => removeBoarder(row)
        });
    }

    function removeBoarder(row) {
        const id = row.dataset.boarderId;
        const errorEl = row.querySelector('.boarder-inline-error');
        const removeButton = row.querySelector('.boarder-remove');

        // Disabled while in flight so double-clicks cannot duplicate writes.
        removeButton.disabled = true;
        fetch(`/api/boarders/${id}`, {
            method: 'DELETE',
            headers: { 'X-CSRF-Token': fetchCsrfToken() }
        })
        .then(response => response.json())
        .then(data => {
            if (data.ok) {
                boarderOriginals.delete(id);
                row.remove();
                maybeShowBoarderEmptyState();
            } else {
                removeButton.disabled = false;
                errorEl.textContent = data.error || 'Failed to remove boarder.';
                errorEl.hidden = false;
            }
        })
        .catch(() => {
            removeButton.disabled = false;
            errorEl.textContent = 'Failed to remove boarder.';
            errorEl.hidden = false;
        });
    }

    function maybeShowBoarderEmptyState() {
        const tbody = document.querySelector('#boarders-table tbody');
        if (tbody && tbody.children.length === 0) {
            const table = document.getElementById('boarders-table');
            const container = table.closest('.table-scroll');
            container.innerHTML = '<div class="empty-state"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#icon-inbox"/></svg>No boarders on the master list. Add a boarder here, or import a CSV to replace it.</div>';
            exitBoarderEditMode();
            boarderEditButton.disabled = true;
        }
    }


    // The all-time view renders no editing affordances; the wiring
    // below only attaches when the Current view's controls are present.
    if (boarderEditButton && boarderSaveButton && boarderCancelButton) {
        boarderEditButton.addEventListener('click', enterBoarderEditMode);

        boarderSaveButton.addEventListener('click', () => {
            if (boardersEditing && !boarderSaveButton.disabled) saveBoarderEdits();
        });

        boarderCancelButton.addEventListener('click', discardBoarderEdits);
    }

    // Unsaved-edit guard for full page navigation (brand link, Back, refresh).
    // Tab clicks keep their own in-page confirm guard above.
    window.addEventListener('beforeunload', function(event) {
        if (hasDirtyBoarderRow()) {
            event.preventDefault();
            event.returnValue = '';
            return '';
        }
    });

    // Month picker popover for the Report Month import field. Replaces the
    // unstyleable native <input type="month"> popup; chevron browsing stops
    // at 2020 and next year so far-future/old typos stay out of reach.
    const MONTH_PICKER_MIN_YEAR = 2020;
    const MONTH_PICKER_NAMES = [
        'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];
    const monthPickerInput = document.getElementById('report_month');
    const monthPickerToggle = document.getElementById('report-month-toggle');
    const monthPickerPopover = document.getElementById('month-picker-popover');
    const monthPickerGrid = monthPickerPopover.querySelector('.month-grid');
    const monthPickerYearLabel = document.getElementById('month-picker-year');
    const monthPickerPrevYear = document.getElementById('month-picker-prev-year');
    const monthPickerNextYear = document.getElementById('month-picker-next-year');
    let monthPickerBrowsedYear = null;

    function monthPickerBounds() {
        return {
            min: MONTH_PICKER_MIN_YEAR,
            max: new Date().getFullYear() + 1
        };
    }

    function monthValue(year, zeroBasedMonth) {
        return `${year}-${String(zeroBasedMonth + 1).padStart(2, '0')}`;
    }

    function renderMonthGrid() {
        const selectedValue = monthPickerInput.value.trim();
        const now = new Date();
        monthPickerYearLabel.textContent = String(monthPickerBrowsedYear);

        let html = '';
        for (let rowStart = 0; rowStart < 12; rowStart += 4) {
            html += '<div role="row">';
            for (let offset = 0; offset < 4; offset++) {
                const zeroBasedMonth = rowStart + offset;
                const value = monthValue(monthPickerBrowsedYear, zeroBasedMonth);
                const classes = ['month-option'];
                if (value === selectedValue) classes.push('selected');
                if (
                    monthPickerBrowsedYear === now.getFullYear() &&
                    zeroBasedMonth === now.getMonth()
                ) {
                    classes.push('current');
                }
                html += `<div role="gridcell"><button type="button" tabindex="-1" class="${classes.join(' ')}" data-month-value="${value}">${MONTH_PICKER_NAMES[zeroBasedMonth]}</button></div>`;
            }
            html += '</div>';
        }
        monthPickerGrid.innerHTML = html;

        monthPickerPrevYear.disabled = monthPickerBrowsedYear <= monthPickerBounds().min;
        monthPickerNextYear.disabled = monthPickerBrowsedYear >= monthPickerBounds().max;
    }

    function monthPickerFocusTargetButton() {
        const selected = monthPickerGrid.querySelector('.month-option.selected');
        if (selected) return selected;
        const current = monthPickerGrid.querySelector('.month-option.current');
        if (current) return current;
        return monthPickerGrid.querySelector('.month-option');
    }

    function openMonthPicker() {
        const match = /^(\d{4})-(0[1-9]|1[0-2])$/.exec(monthPickerInput.value.trim());
        const typedYear = match ? parseInt(match[1], 10) : NaN;
        monthPickerBrowsedYear = Number.isInteger(typedYear)
            ? typedYear
            : new Date().getFullYear();

        renderMonthGrid();
        monthPickerPopover.classList.remove('hidden');
        monthPickerToggle.setAttribute('aria-expanded', 'true');

        // Roving tabindex keeps Tab cycling short; arrows handle the grid.
        const target = monthPickerFocusTargetButton();
        target.tabIndex = 0;
        target.focus();
    }

    function closeMonthPicker(restoreFocusToField) {
        monthPickerPopover.classList.add('hidden');
        monthPickerToggle.setAttribute('aria-expanded', 'false');
        if (restoreFocusToField) {
            monthPickerInput.focus();
        }
    }

    function isMonthPickerOpen() {
        return !monthPickerPopover.classList.contains('hidden');
    }

    monthPickerToggle.addEventListener('click', function() {
        if (isMonthPickerOpen()) {
            closeMonthPicker(false);
        } else {
            openMonthPicker();
        }
    });

    monthPickerPrevYear.addEventListener('click', function() {
        monthPickerBrowsedYear -= 1;
        renderMonthGrid();
    });

    monthPickerNextYear.addEventListener('click', function() {
        monthPickerBrowsedYear += 1;
        renderMonthGrid();
    });

    monthPickerGrid.addEventListener('click', function(event) {
        const option = event.target.closest('.month-option');
        if (!option) return;
        monthPickerInput.value = option.getAttribute('data-month-value');
        closeMonthPicker(true);
    });

    // Clicking anywhere outside the popover or its toggle dismisses it.
    document.addEventListener('mousedown', function(event) {
        if (!isMonthPickerOpen()) return;
        if (monthPickerPopover.contains(event.target)) return;
        if (monthPickerToggle.contains(event.target)) return;
        closeMonthPicker(false);
    });

    monthPickerPopover.addEventListener('keydown', function(event) {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeMonthPicker(true);
            return;
        }

        // Focus trap mirroring the confirm dialog: Tab cycles inside only.
        if (event.key === 'Tab') {
            const focusables = this.querySelectorAll(
                'button:not([disabled]):not([tabindex="-1"]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
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
            return;
        }

        const arrowMoves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -4, ArrowDown: 4 };
        if (!(event.key in arrowMoves)) return;
        const focusedOption =
            document.activeElement && document.activeElement.closest('.month-option');
        if (!focusedOption || !this.contains(document.activeElement)) return;

        event.preventDefault();
        const options = [...this.querySelectorAll('.month-option')];
        const index = options.indexOf(focusedOption);
        const nextIndex = (index + arrowMoves[event.key] + options.length) % options.length;
        focusedOption.tabIndex = -1;
        options[nextIndex].tabIndex = 0;
        options[nextIndex].focus();
    });

    // Card cue: shade sticky headers while rows scroll beneath them.
    document.querySelectorAll('.table-scroll').forEach(function(scroller) {
        scroller.addEventListener('scroll', function() {
            this.classList.toggle('scrolled', this.scrollTop > 0 || this.scrollLeft > 0);
        }, { passive: true });
    });

    document.addEventListener('DOMContentLoaded', function() {
        const initialMonthEl = document.getElementById('initial-month-data');
        const initialMonthToOpen = initialMonthEl ? JSON.parse(initialMonthEl.textContent) : null;
        if (initialMonthToOpen) {
            const reportsTab = document.querySelector('[data-tab="reports"]');
            if (reportsTab) {
                reportsTab.click();
            }
            viewMonth(initialMonthToOpen);
        }
    });

    window.addEventListener('keydown', function(event) {
        const isPrintShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'p';
        if (!isPrintShortcut) {
            return;
        }

        const reportsPanel = document.getElementById('reports');
        const monthDetail = document.getElementById('month-detail');
        const reportsActive = reportsPanel && reportsPanel.classList.contains('active');
        const reportOpen = monthDetail && !monthDetail.classList.contains('hidden') && !!activeMonthReport;

        if (reportsActive && reportOpen) {
            event.preventDefault();
            printCurrentMonthReport();
        }
    });
