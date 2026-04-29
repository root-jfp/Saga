// ============================================================================
// Saga - PDF.js Frontend Logic
// ============================================================================

// Set PDF.js worker (with safety check in case CDN fails to load)
if (typeof pdfjsLib !== 'undefined') {
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
} else {
    console.warn('PDF.js not loaded - PDF rendering will be unavailable');
}

// ============ STATE ============
// Note: allUsers and currentUser are provided by shared.js
let books = [];
let currentBook = null;
let currentPage = 1;
let isPlaying = false;
let currentAudio = null;
let playbackSpeed = 1.0;
let sleepTimerId = null;
let sleepTimeRemaining = 0;
let currentSentenceIndex = 0;
let bookmarks = [];

// Page content state
let pageSentences = [];
let previewSentences = [];  // Next-page sentences shown in right column in spread mode
let audioTiming = [];  // Real timing data from TTS: [{text, offset, duration}, ...]

// Voice state
let availableVoices = [];
let selectedVoice = null;

// Categories state — populated by loadCategories / loadCategoryPresets.
// `currentCategoryId` semantics:
//   undefined → showing the category browser landing
//   null      → "All books" virtual selection
//   -1        → "Uncategorised" virtual selection
//   <number>  → specific category id
const categoriesState = {
    list: [],
    byId: {},
    presets: [],
    presetByKey: {},
    uncategorisedCount: 0,
    currentCategoryId: undefined,
    editingId: null,
    pendingVisual: { kind: 'preset', value: 'general' },  // form scratch state
};


// View mode: 'book' or 'text'
let currentViewMode = 'book';  // Default to book view

// Font size state for book view (percentage) - default 90% for better readability
let bookFontSize = 90;

// API Base URL - uses API_BASE from shared.js

// ============ DOM ELEMENTS ============
const libraryView = document.getElementById('libraryView');
const readerView = document.getElementById('readerView');
const booksGrid = document.getElementById('booksGrid');
const bookReadingArea = document.getElementById('bookReadingArea');
const bookPageContent = document.getElementById('bookPageContent');
const textReadingArea = document.getElementById('textReadingArea');
const sentenceList = document.getElementById('sentenceList');
const playPauseBtn = document.getElementById('playPauseBtn');
const progressBar = document.getElementById('progressBar');
const userPills = document.getElementById('userPills');
const themeToggle = document.getElementById('themeToggle');
const loadingOverlay = document.getElementById('loadingOverlay');
const toastContainer = document.getElementById('toastContainer');

// ============ INITIALIZATION ============
async function init() {
    // CRITICAL: Setup event listeners FIRST so buttons work even if data loading fails
    setupEventListeners();

    // Listen for user changes from sidebar (shared.js)
    document.addEventListener('userChanged', handleUserChanged);
    document.addEventListener('usersLoaded', handleUsersLoaded);

    try {
        showLoading();
        loadTheme();

        // Wait for users to be loaded (shared.js loads them)
        // If users already loaded, use them; otherwise wait briefly
        if (!allUsers || allUsers.length === 0) {
            await loadUsers();
        }

        // Track which user we loaded books for
        lastLoadedUserId = currentUser?.id || null;

        // Load books, categories, presets, and voices in parallel.
        await Promise.all([
            loadBooks(),
            loadCategories(),
            loadCategoryPresets(),
            loadVoices(),
        ]);

        setupDropdowns();
        renderLibraryWithAnimation();
        updateFontSizeDisplay();
        setViewMode(currentViewMode);  // Initialize view mode
        hideLoading();

        // If the URL contains #book/<id>, restore that book — survives refresh.
        const hashBookId = _bookIdFromHash();
        if (hashBookId) {
            const inLibrary = Array.isArray(books) && books.some(b => b.id === hashBookId);
            if (inLibrary) {
                await openBook(hashBookId);
            } else {
                // Book isn't in this user's library (different user, deleted, etc.)
                _clearBookHash();
            }
        }
    } catch (error) {
        console.error('Saga init error:', error);
        hideLoading();
        showToast('Failed to load book data. Please refresh.', 'error');
    }
}

// Handle user change from sidebar
// Track last loaded user to detect actual changes
let lastLoadedUserId = null;

async function handleUserChanged(event) {
    // Support both old format (user directly) and new format ({ user, previousUser })
    const user = event.detail?.user || event.detail;
    // Check against lastLoadedUserId, not currentUser (which shared.js already updated)
    if (user && user.id !== lastLoadedUserId) {
        console.log('[book-reader] User changed from', lastLoadedUserId, 'to', user.id);
        currentUser = user;
        lastLoadedUserId = user.id;
        renderUserPills();

        // Animate books out
        if (booksGrid) {
            booksGrid.classList.add('switching-out');
            await new Promise(resolve => setTimeout(resolve, 200));
        }

        await Promise.all([loadBooks(), loadCategories()]);
        // Reset to the category browser on user switch — a stale category id
        // from the previous user would mismatch this user's data.
        categoriesState.currentCategoryId = undefined;
        renderLibraryWithAnimation();
    }
}

// Render library with slide-in animation
function renderLibraryWithAnimation() {
    // Remove switching-out, render new content
    if (booksGrid) {
        booksGrid.classList.remove('switching-out');
    }

    renderLibrary();

    // Add switching-in animation
    if (booksGrid && books.length > 0) {
        booksGrid.classList.add('switching-in');
        // Remove animation class after it completes
        setTimeout(() => {
            booksGrid.classList.remove('switching-in');
        }, 500);
    }
}

// Handle users loaded from shared.js
function handleUsersLoaded(event) {
    const { users, currentUser: loadedUser } = event.detail;
    if (users && users.length > 0) {
        allUsers = users;
        if (loadedUser) {
            currentUser = loadedUser;
        }
        renderUserPills();
    }
}

function setupEventListeners() {
    // View toggle - with null checks
    document.getElementById('viewToggle')?.addEventListener('click', (e) => {
        if (e.target.classList.contains('view-btn')) {
            const view = e.target.dataset.view;
            if (view === 'library') {
                switchView('library');
            } else if (view === 'reader' && currentBook) {
                switchView('reader');
            }
        }
    });

    // Upload modal
    document.getElementById('uploadBookBtn')?.addEventListener('click', showUploadModal);
    document.getElementById('closeUploadModal')?.addEventListener('click', hideUploadModal);
    document.getElementById('uploadForm')?.addEventListener('submit', handleUpload);

    // Manage users modal
    document.getElementById('manageUsersBtn')?.addEventListener('click', showUsersModal);
    document.getElementById('closeUsersModal')?.addEventListener('click', hideUsersModal);
    document.getElementById('createUserForm')?.addEventListener('submit', handleCreateUser);

    // Categories
    document.getElementById('uploadBookBtnInside')?.addEventListener('click', showUploadModal);
    document.getElementById('manageCategoriesBtn')?.addEventListener('click', showCategoriesModal);
    document.getElementById('closeCategoriesModal')?.addEventListener('click', hideCategoriesModal);
    document.getElementById('createCategoryForm')?.addEventListener('submit', handleSubmitCategoryForm);
    document.getElementById('cancelCategoryEditBtn')?.addEventListener('click', () => {
        _resetCategoryForm();
        renderPresetGrid();
        renderCategoryParentSelect();
    });
    document.querySelectorAll('.visual-tab').forEach(btn => {
        btn.addEventListener('click', () => _switchVisualTab(btn.dataset.tab));
    });
    document.getElementById('backToCategories')?.addEventListener('click', () => {
        categoriesState.currentCategoryId = undefined;
        renderLibrary();
    });
    document.getElementById('closeAssignCategoryModal')?.addEventListener('click', hideAssignCategoryModal);

    // Refresh audio
    document.getElementById('regenerateAudioBtn')?.addEventListener('click', regenerateAudioForCurrentBook);

    // File drop zone
    const dropZone = document.getElementById('fileDropZone');
    if (dropZone) {
        dropZone.addEventListener('dragover', handleDragOver);
        dropZone.addEventListener('dragleave', handleDragLeave);
        dropZone.addEventListener('drop', handleDrop);
        dropZone.addEventListener('click', () => document.getElementById('fileInput')?.click());
    }
    document.getElementById('fileInput')?.addEventListener('change', handleFileSelect);

    // Back to library
    document.getElementById('backToLibrary')?.addEventListener('click', () => switchView('library'));

    // Player controls
    playPauseBtn?.addEventListener('click', togglePlayback);
    document.getElementById('prevPageBtn')?.addEventListener('click', prevPage);
    document.getElementById('nextPageBtn')?.addEventListener('click', nextPage);
    progressBar?.addEventListener('input', seekAudio);

    // Font size controls
    document.getElementById('fontSizeUpBtn')?.addEventListener('click', fontSizeUp);
    document.getElementById('fontSizeDownBtn')?.addEventListener('click', fontSizeDown);

    // View mode toggle (Book vs Text/List)
    document.getElementById('bookViewBtn')?.addEventListener('click', () => setViewMode('book'));
    document.getElementById('textViewBtn')?.addEventListener('click', () => setViewMode('text'));

    // Bookmarks
    document.getElementById('bookmarkBtn')?.addEventListener('click', addBookmark);
    document.getElementById('bookmarksListBtn')?.addEventListener('click', showBookmarksModal);
    document.getElementById('closeBookmarksModal')?.addEventListener('click', hideBookmarksModal);

    // Sleep timer
    document.getElementById('sleepTimerBtn')?.addEventListener('click', showSleepTimerModal);
    document.getElementById('closeSleepTimerModal')?.addEventListener('click', hideSleepTimerModal);
    document.getElementById('cancelSleepTimer')?.addEventListener('click', cancelSleepTimer);
    document.querySelectorAll('.sleep-option').forEach(btn => {
        btn.addEventListener('click', () => startSleepTimer(parseInt(btn.dataset.minutes)));
    });

    // Shortcuts modal
    document.getElementById('closeShortcutsModal')?.addEventListener('click', hideShortcutsModal);

    // Theme toggle
    themeToggle?.addEventListener('click', toggleTheme);

    // Keyboard shortcuts
    document.addEventListener('keydown', handleKeyboard);

    // Close modals on backdrop click
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.classList.remove('visible');
            }
        });
    });
}

// ============ USER MANAGEMENT ============
async function loadUsers() {
    try {
        const response = await fetch(`${API_BASE}/users`);
        allUsers = await response.json();

        const savedUserId = localStorage.getItem('currentUserId');
        currentUser = allUsers.find(u => u.id === parseInt(savedUserId)) || allUsers[0];

        if (currentUser) {
            localStorage.setItem('currentUserId', currentUser.id);
        }

        if (allUsers.length > 0 && userPills) {
            userPills.style.display = 'flex';
            renderUserPills();
        }
    } catch (error) {
        console.error('Error loading users:', error);
    }
}

function renderUserPills() {
    if (!userPills) return;
    userPills.innerHTML = '';
    allUsers.forEach(user => {
        const pill = document.createElement('button');
        pill.className = 'user-pill';
        if (currentUser && user.id === currentUser.id) {
            pill.classList.add('active');
        }

        const avatar = document.createElement('span');
        avatar.className = 'user-pill-avatar';
        avatar.textContent = user.avatar || 'U';

        const name = document.createElement('span');
        name.className = 'user-pill-name';
        name.textContent = user.name;

        pill.appendChild(avatar);
        pill.appendChild(name);
        pill.addEventListener('click', () => switchUser(user.id));
        userPills.appendChild(pill);
    });
}

async function switchUser(userId) {
    // Prevent switching to same user
    if (lastLoadedUserId === userId) return;

    const newUser = allUsers.find(u => u.id === userId);
    if (!newUser) return;

    console.log('[book-reader] switchUser from', lastLoadedUserId, 'to', userId);
    currentUser = newUser;
    lastLoadedUserId = userId;
    localStorage.setItem('currentUserId', userId);
    renderUserPills();

    // Animate books out
    if (booksGrid) {
        booksGrid.classList.add('switching-out');
        await new Promise(resolve => setTimeout(resolve, 200));
    }

    try {
        await Promise.all([loadBooks(), loadCategories()]);
        categoriesState.currentCategoryId = undefined;
        renderLibraryWithAnimation();
    } catch (error) {
        console.error('Error switching user:', error);
        showToast('Failed to load books for user', 'error');
        // Still render to show empty state
        renderLibraryWithAnimation();
    }
}

// ============ BOOKS ============
async function loadBooks() {
    // Clear books array first to prevent showing stale data
    books = [];

    if (!currentUser) {
        console.warn('No user selected, cannot load books');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/books?user_id=${currentUser.id}`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        books = await response.json();
    } catch (error) {
        console.error('Failed to load books:', error);
        showToast('Failed to load books', 'error');
        books = [];
    }
}

function renderLibrary() {
    // Dispatcher: either show the category browser landing or the contents
    // of the currently-selected category (incl. "All" and "Uncategorised").
    if (categoriesState.currentCategoryId === undefined) {
        showCategoryBrowser();
        return;
    }
    showCategoryContents(categoriesState.currentCategoryId);
}

function _renderBooksGrid(filteredBooks) {
    const noBooks = document.getElementById('noBooks');

    if (!booksGrid) {
        console.warn('booksGrid element not found');
        return;
    }

    booksGrid.innerHTML = '';

    if (!filteredBooks.length) {
        if (noBooks) noBooks.classList.remove('hidden');
        return;
    }
    if (noBooks) noBooks.classList.add('hidden');

    booksGrid.innerHTML = filteredBooks.map(book => {
        const cat = book.category_id ? categoriesState.byId[book.category_id] : null;
        const catBadge = cat
            ? `<div class="book-category-badge">${escapeHtml((cat.emoji || '') + ' ' + cat.name)}</div>`
            : '';
        return `
        <div class="book-card" data-book-id="${book.id}">
            <div class="book-cover" onclick="openBook(${book.id})">
                ${catBadge}
                ${book.cover_image_path
                    ? `<img src="${API_BASE}/books/${book.id}/thumbnail" alt="${escapeHtml(book.title)}" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                       <div class="book-cover-placeholder" style="display:none;">PDF</div>`
                    : `<div class="book-cover-placeholder">PDF</div>`
                }
                ${book.upload_status === 'processing'
                    ? `<div class="processing-badge">Processing...</div>`
                    : book.upload_status === 'failed'
                    ? `<div class="processing-badge error">Failed</div>`
                    : ''
                }
            </div>
            <div class="book-info" onclick="openBook(${book.id})">
                <h3 class="book-title">${escapeHtml(book.title)}</h3>
                ${book.author ? `<p class="book-author">${escapeHtml(book.author)}</p>` : ''}
                <div class="book-progress">
                    <div class="progress-bar-mini">
                        <div class="progress-fill-mini" style="width: ${getProgressPercent(book)}%"></div>
                    </div>
                    <span>${book.current_page || 1} / ${book.total_pages || '?'} pages</span>
                </div>
                ${getAudioProgressHtml(book)}
            </div>
            <button class="book-assign-btn" onclick="event.stopPropagation(); showAssignCategoryModal(${book.id})" title="Move to category">Move</button>
            <button class="book-delete-btn" onclick="event.stopPropagation(); deleteBook(${book.id})" title="Delete">X</button>
        </div>
        `;
    }).join('');

    // Start polling for audio progress on books that are generating
    pollAudioProgress();
}

function getProgressPercent(book) {
    if (!book.total_pages) return 0;
    return Math.round(((book.current_page || 1) / book.total_pages) * 100);
}

function getAudioProgressHtml(book) {
    // Only show for books that have pages and are generating audio
    if (!book.total_pages || book.upload_status !== 'ready') return '';

    const status = book.audio_generation_status;
    const completed = book.audio_pages_completed || 0;
    const total = book.total_pages;
    const percent = total > 0 ? Math.round((completed / total) * 100) : 0;

    if (status === 'completed') {
        return `<div class="audio-progress complete">
            <span class="audio-icon">🔊</span> Audio ready
        </div>`;
    } else if (status === 'in_progress') {
        return `<div class="audio-progress generating" data-book-id="${book.id}">
            <div class="audio-progress-bar">
                <div class="audio-progress-fill" style="width: ${percent}%"></div>
            </div>
            <span class="audio-label">🔊 ${percent}% (${completed}/${total})</span>
        </div>`;
    } else if (status === 'pending') {
        return `<div class="audio-progress pending">
            <span class="audio-icon">⏳</span> Queued for audio
        </div>`;
    }
    return '';
}

// Audio progress polling
let audioProgressInterval = null;

function pollAudioProgress() {
    // Clear existing interval
    if (audioProgressInterval) {
        clearInterval(audioProgressInterval);
        audioProgressInterval = null;
    }

    // Find books that are generating audio
    const generatingBooks = books.filter(b =>
        b.audio_generation_status === 'in_progress' || b.audio_generation_status === 'pending'
    );

    if (generatingBooks.length === 0) return;

    // Poll every 5 seconds
    audioProgressInterval = setInterval(async () => {
        let anyStillGenerating = false;

        for (const book of generatingBooks) {
            try {
                const response = await fetch(`${API_BASE}/books/${book.id}/audio-progress`);
                if (response.ok) {
                    const progress = await response.json();

                    // Update the book in our local array
                    const idx = books.findIndex(b => b.id === book.id);
                    if (idx >= 0) {
                        books[idx].audio_generation_status = progress.status;
                        books[idx].audio_pages_completed = progress.pages_completed;
                    }

                    // Update UI directly without full re-render
                    updateBookAudioProgress(book.id, progress);

                    if (progress.status === 'in_progress' || progress.status === 'pending') {
                        anyStillGenerating = true;
                    }
                }
            } catch (e) {
                console.warn('Failed to poll audio progress:', e);
            }
        }

        // Stop polling if no books are generating
        if (!anyStillGenerating) {
            clearInterval(audioProgressInterval);
            audioProgressInterval = null;
        }
    }, 5000);
}

function updateBookAudioProgress(bookId, progress) {
    const card = document.querySelector(`.book-card[data-book-id="${bookId}"]`);
    if (!card) return;

    const progressEl = card.querySelector('.audio-progress');
    if (!progressEl) return;

    const percent = progress.total_pages > 0
        ? Math.round((progress.pages_completed / progress.total_pages) * 100)
        : 0;

    if (progress.status === 'completed') {
        progressEl.className = 'audio-progress complete';
        progressEl.innerHTML = `<span class="audio-icon">🔊</span> Audio ready`;
    } else if (progress.status === 'in_progress') {
        progressEl.className = 'audio-progress generating';
        progressEl.innerHTML = `
            <div class="audio-progress-bar">
                <div class="audio-progress-fill" style="width: ${percent}%"></div>
            </div>
            <span class="audio-label">🔊 ${percent}% (${progress.pages_completed}/${progress.total_pages})</span>
        `;
    }
}

// ── URL-hash routing ───────────────────────────────────────────────────────
// The hash `#book/<id>` records which book is open so a page refresh
// re-opens it. Per-page position within the book is already remembered by
// the server-side book_progress table (saveProgress / current_page).
function _setBookHash(bookId) {
    const target = '#book/' + bookId;
    if (location.hash === target) return;
    if (history && history.replaceState) {
        history.replaceState(null, '', target);
    } else {
        location.hash = target;
    }
}
function _clearBookHash() {
    if (!location.hash) return;
    if (history && history.replaceState) {
        history.replaceState(null, '', location.pathname + location.search);
    } else {
        location.hash = '';
    }
}
function _bookIdFromHash() {
    const m = (location.hash || '').match(/^#book\/(\d+)/);
    return m ? parseInt(m[1], 10) : null;
}

async function openBook(bookId) {
    showLoading();

    try {
        const userParam = currentUser ? `?user_id=${currentUser.id}` : '';
        const response = await fetch(`${API_BASE}/books/${bookId}${userParam}`);
        currentBook = await response.json();

        if (currentBook.error) {
            throw new Error(currentBook.error);
        }

        if (currentBook.upload_status === 'processing') {
            showToast('Book is still processing. Please wait.', 'info');
            hideLoading();
            return;
        }

        if (currentBook.upload_status === 'failed') {
            showToast('Book processing failed: ' + (currentBook.processing_error || 'Unknown error'), 'error');
            hideLoading();
            return;
        }

        currentPage = currentBook.current_page || 1;
        playbackSpeed = currentBook.playback_speed || 1.0;
        updateSpeedDisplay();

        // IMPORTANT: settle the voice picker BEFORE loading book content.
        // loadBook → renderPage → loadPageAudio uses `selectedVoice` to build
        // the audio URL. If that runs before loadVoices, the previous book's
        // voice leaks into the new book's audio request — TTS then synthesises
        // (wrong voice on new script) and we get ~5s of silence/garbage.
        await loadVoices();

        // Load book content
        await loadBook(bookId);
        await loadBookmarks();

        switchView('reader');
        updateReaderHeader();
        _setBookHash(bookId);

        // Start in-reader audio progress bar (hides itself when generation complete)
        if (currentBook.audio_generation_status !== 'completed') {
            startReaderAudioProgress(bookId);
        }

    } catch (error) {
        console.error('Failed to open book:', error);
        showToast('Failed to open book', 'error');
    }

    hideLoading();
}

async function loadBook(bookId) {
    try {
        // Render current page (sentences are loaded from backend)
        await renderPage(currentPage);
    } catch (error) {
        console.error('Failed to load book:', error);
        showToast('Failed to load book', 'error');
        throw error;
    }
}


async function renderPage(pageNumber) {
    try {
        // Load sentences from backend for text display and audio sync
        await loadPageSentences(pageNumber);

        currentPage = pageNumber;
        updatePageInfo();
        saveProgress();

        // Load audio for this page
        loadPageAudio(pageNumber);

    } catch (error) {
        console.error('Failed to render page:', error);
        showToast('Failed to render page', 'error');
    }
}

// True when the viewport is wide enough for the two-page spread layout.
// Mirrors the CSS @media (min-width: 1100px) breakpoint.
function _isSpreadMode() {
    return typeof window !== 'undefined'
        && window.matchMedia
        && window.matchMedia('(min-width: 1100px)').matches;
}

async function loadPageSentences(pageNumber) {
    try {
        const response = await fetch(`${API_BASE}/books/${currentBook.id}/pages/${pageNumber}`);
        const pageData = await response.json();

        if (pageData.sentences) {
            pageSentences = pageData.sentences;
        } else {
            pageSentences = [];
        }

        // Load audio timing data if available
        if (pageData.audio_timing && Array.isArray(pageData.audio_timing)) {
            audioTiming = pageData.audio_timing;
            console.log(`Loaded ${audioTiming.length} timing entries for page ${pageNumber}`);
        } else {
            audioTiming = [];
        }

        // Reset current sentence index for new page
        currentSentenceIndex = 0;

        // In two-page spread mode, also fetch the next page's sentences so
        // they can be displayed in the right column as a "preview". Audio
        // still plays only for the current page.
        previewSentences = [];
        const total = (currentBook && currentBook.total_pages) || 0;
        if (_isSpreadMode() && pageNumber < total) {
            try {
                const r2 = await fetch(`${API_BASE}/books/${currentBook.id}/pages/${pageNumber + 1}`);
                if (r2.ok) {
                    const d2 = await r2.json();
                    if (d2 && d2.sentences && Array.isArray(d2.sentences)) {
                        previewSentences = d2.sentences;
                    } else if (typeof d2.sentences === 'string') {
                        try {
                            const parsed = JSON.parse(d2.sentences);
                            if (Array.isArray(parsed)) previewSentences = parsed;
                        } catch (_) { /* ignore */ }
                    }
                }
            } catch (e) {
                console.warn('[saga] preview page fetch failed:', e);
            }
        }

        // Update page numbers — single-page footer + spread footer slots
        _updatePageNumberFooters(pageNumber);

        // Render sentences in appropriate view
        if (currentViewMode === 'book') {
            renderBookSentences();
        } else if (currentViewMode === 'text') {
            renderSentences();
        }
    } catch (error) {
        console.error('Failed to load page sentences:', error);
        pageSentences = [];
        previewSentences = [];
        audioTiming = [];
    }
}

function _updatePageNumberFooters(pageNumber) {
    const center = document.getElementById('pageNumberDisplay');
    const left = document.getElementById('pageNumberDisplayLeft');
    const right = document.getElementById('pageNumberDisplayRight');
    if (center) center.textContent = `Page ${pageNumber}`;
    if (left)   left.textContent   = `Page ${pageNumber}`;
    if (right) {
        // Always show "Page N+1" on the right when in spread mode and a next
        // page exists — independent of whether the preview content has
        // finished loading.
        const total = (currentBook && currentBook.total_pages) || 0;
        const showRight = _isSpreadMode() && pageNumber < total;
        right.textContent = showRight ? `Page ${pageNumber + 1}` : '';
    }
}

async function deleteBook(bookId) {
    if (!confirm('Delete this book? This cannot be undone.')) {
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/books/${bookId}`, {
            method: 'DELETE',
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        });

        if (!response.ok) {
            throw new Error('Failed to delete book');
        }

        books = books.filter(b => b.id !== bookId);
        renderLibrary();
        showToast('Book deleted', 'success');

    } catch (error) {
        console.error('Failed to delete book:', error);
        showToast('Failed to delete book', 'error');
    }
}

// ============ VOICES ============

/**
 * Is voice `voiceId` plausible for `book`?
 * A voice's locale (e.g. 'ar-SA') must match the book's detected_language
 * (e.g. 'ar'). If we can't decide (missing data or unknown voice id), assume
 * yes — the caller will fall back if it's actually wrong.
 *
 * This guard prevents a stale per-book or global voice from speaking the
 * wrong script: e.g. the Korean voice silently producing 0s of audio when
 * pointed at Arabic text.
 */
function _isVoiceCompatibleWithBook(voiceId, book) {
    if (!voiceId || !book || !book.detected_language) return true;
    const voice = availableVoices && availableVoices.find(v => v.id === voiceId);
    if (!voice || !voice.locale) return true;
    const voiceLang = voice.locale.split('-')[0].toLowerCase();
    return voiceLang === String(book.detected_language).toLowerCase();
}

/**
 * Return the best voice id to use for the current book.
 * Priority:
 *   1. per-book override (only if compatible with the book's language)
 *   2. recommended voice for the book's detected language
 *   3. global last-selected voice (only if compatible)
 *   4. null (caller falls back to first available voice)
 *
 * Both the per-book and global entries are language-validated so a stale
 * pick from a previous book can't override a language-appropriate
 * recommendation.
 */
function _getEffectiveVoice() {
    if (currentBook) {
        const perBook = localStorage.getItem(`voice_book_${currentBook.id}`);
        if (perBook && _isVoiceCompatibleWithBook(perBook, currentBook)) return perBook;
        if (currentBook.recommended_voice_id) return currentBook.recommended_voice_id;
    }
    const global = localStorage.getItem('selectedVoice');
    if (global && _isVoiceCompatibleWithBook(global, currentBook)) return global;
    return null;
}

/**
 * Build a voice item DOM element and attach a click handler.
 */
function _buildVoiceItem(voice, effectiveVoice) {
    const isFemale = voice.gender === 'Female';
    const genderClass = isFemale ? 'female' : 'male';
    const genderIcon = isFemale ? 'F' : 'M';
    const isSelected = effectiveVoice === voice.id;

    const item = document.createElement('div');
    item.className = `dropdown-item voice-item${isSelected ? ' selected' : ''}`;
    item.dataset.id = voice.id;
    item.dataset.name = (voice.name || '').toLowerCase();
    item.dataset.locale = (voice.locale || '').toLowerCase();
    item.innerHTML = `
        <div class="item-avatar ${genderClass}">${genderIcon}</div>
        <div class="item-info">
            <div class="item-name">${escapeHtml(voice.name)}</div>
            <div class="item-locale">${escapeHtml(voice.locale)} &middot; ${escapeHtml(voice.quality || 'neural')}</div>
        </div>
    `;
    item.addEventListener('click', () => selectVoice(voice));
    return item;
}

/**
 * Load and render the voice picker with:
 * - Search box at the top
 * - "Recommended for this book" pinned section
 * - Grouped collapsible list by locale
 */
async function loadVoices() {
    const voiceMenu = document.getElementById('voiceMenu');
    if (!voiceMenu) return;

    try {
        // Fetch grouped voice list
        const response = await fetch(`${API_BASE}/tts/voices?grouped=1`);
        const groupedData = await response.json();

        // Build flat list for searching and for initialising selectedVoice
        availableVoices = [];
        groupedData.forEach(group => {
            (group.voices || []).forEach(v => availableVoices.push(v));
        });

        if (availableVoices.length === 0) {
            voiceMenu.innerHTML = '<div class="dropdown-item">No voices available</div>';
            return;
        }

        const effectiveVoice = _getEffectiveVoice() || availableVoices[0].id;

        // Apply effective voice
        const matchedVoice = availableVoices.find(v => v.id === effectiveVoice);
        if (matchedVoice) {
            selectedVoice = matchedVoice.id;
            updateVoiceDisplay(matchedVoice);
        } else {
            selectedVoice = availableVoices[0].id;
            updateVoiceDisplay(availableVoices[0]);
        }

        voiceMenu.innerHTML = '';

        // ── Search box ──────────────────────────────────────────────────────
        const searchWrapper = document.createElement('div');
        searchWrapper.className = 'voice-search-wrapper';
        searchWrapper.innerHTML = `
            <input type="text" class="voice-search-input" placeholder="Search voices..." autocomplete="off">
        `;
        voiceMenu.appendChild(searchWrapper);

        const searchInput = searchWrapper.querySelector('.voice-search-input');
        searchInput.addEventListener('input', () => {
            const query = searchInput.value.toLowerCase().trim();
            voiceMenu.querySelectorAll('.voice-item').forEach(el => {
                const name = el.dataset.name || '';
                const locale = el.dataset.locale || '';
                el.style.display = (!query || name.includes(query) || locale.includes(query)) ? '' : 'none';
            });
            // Hide section headers with no visible children
            voiceMenu.querySelectorAll('.voice-group-header').forEach(header => {
                const section = header.nextElementSibling;
                if (!section) return;
                const visibleItems = section.querySelectorAll('.voice-item:not([style*="display: none"])');
                header.style.display = visibleItems.length === 0 ? 'none' : '';
            });
        });
        // Prevent dropdown from closing when typing in search
        searchInput.addEventListener('click', e => e.stopPropagation());

        // ── Recommended section ──────────────────────────────────────────────
        const recommendedId = currentBook && currentBook.recommended_voice_id
            ? currentBook.recommended_voice_id
            : null;

        if (recommendedId) {
            // Show the recommended voice plus up to 2 siblings from the same locale
            const recVoice = availableVoices.find(v => v.id === recommendedId);
            if (recVoice) {
                const siblingsLocale = recVoice.locale;
                const siblings = availableVoices
                    .filter(v => v.locale === siblingsLocale && v.id !== recommendedId)
                    .slice(0, 2);
                const recGroup = [recVoice, ...siblings];

                const recHeader = document.createElement('div');
                recHeader.className = 'voice-group-header voice-group-recommended';
                recHeader.textContent = 'Recommended for this book';
                voiceMenu.appendChild(recHeader);

                const recSection = document.createElement('div');
                recSection.className = 'voice-group-section';
                recGroup.forEach(voice => {
                    recSection.appendChild(_buildVoiceItem(voice, effectiveVoice));
                });
                voiceMenu.appendChild(recSection);

                const divider = document.createElement('div');
                divider.className = 'voice-group-divider';
                voiceMenu.appendChild(divider);
            }
        }

        // ── Grouped locale sections ──────────────────────────────────────────
        // Try to use Intl.DisplayNames for human-readable language names
        let langNames = null;
        try {
            langNames = new Intl.DisplayNames([navigator.language || 'en'], { type: 'language' });
        } catch (_) { /* Safari < 14 doesn't support Intl.DisplayNames */ }

        groupedData.forEach(group => {
            if (!group.voices || group.voices.length === 0) return;

            const locale = group.locale || 'unknown';
            // Extract language code (e.g. 'en' from 'en-GB')
            const langCode = locale.split('-')[0];
            let langLabel = locale;
            try {
                if (langNames) langLabel = langNames.of(langCode) || locale;
            } catch (_) { langLabel = locale; }

            const header = document.createElement('div');
            header.className = 'voice-group-header';
            header.textContent = `${langLabel} (${locale})`;
            voiceMenu.appendChild(header);

            const section = document.createElement('div');
            section.className = 'voice-group-section';
            group.voices.forEach(voice => {
                section.appendChild(_buildVoiceItem(voice, effectiveVoice));
            });
            voiceMenu.appendChild(section);
        });

    } catch (error) {
        console.error('Failed to load voices:', error);
        const voiceText = document.getElementById('voiceText');
        if (voiceText) voiceText.textContent = 'Error';
    }
}

function updateVoiceDisplay(voice) {
    const avatar = document.getElementById('voiceAvatar');
    const text = document.getElementById('voiceText');
    if (!avatar || !text) return;

    const isFemale = voice.gender === 'Female';
    avatar.textContent = isFemale ? 'F' : 'M';
    avatar.className = `dropdown-avatar ${isFemale ? 'female' : 'male'}`;
    text.textContent = voice.name;
}

function selectVoice(voice) {
    const previousVoice = selectedVoice;
    selectedVoice = voice.id;
    // Per-book only — never write to the global slot. Writing globally
    // poisons every other book's auto-selection: opening a different
    // language book would inherit this voice and TTS would emit near
    // silence on a mismatched script.
    if (currentBook) {
        localStorage.setItem(`voice_book_${currentBook.id}`, voice.id);
    }
    updateVoiceDisplay(voice);

    // Update selected state in menu
    document.querySelectorAll('.voice-item').forEach(item => {
        item.classList.toggle('selected', item.dataset.id === voice.id);
    });

    // Close dropdown
    document.getElementById('voiceDropdown').classList.remove('open');
    showToast(`Voice: ${voice.name}`, 'info');

    // Reload audio with new voice if currently in reader view
    if (currentBook && previousVoice !== voice.id && !readerView.classList.contains('hidden')) {
        loadPageAudio(currentPage);
        // Queue whole-book regeneration with the new voice in background (priority=0)
        fetch(`${API_BASE}/books/${currentBook.id}/generate-audio`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: currentUser?.id, voice_id: voice.id })
        }).catch(() => {});
    }
}

// ============ CUSTOM DROPDOWNS ============
function setupDropdowns() {
    // Voice dropdown
    const voiceToggle = document.getElementById('voiceToggle');
    const voiceDropdown = document.getElementById('voiceDropdown');

    if (voiceToggle && voiceDropdown) {
        voiceToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            voiceDropdown.classList.toggle('open');
            const speedDropdown = document.getElementById('speedDropdown');
            if (speedDropdown) speedDropdown.classList.remove('open');
        });
    }

    // Speed dropdown
    const speedToggle = document.getElementById('speedToggle');
    const speedDropdown = document.getElementById('speedDropdown');

    if (speedToggle && speedDropdown) {
        speedToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            speedDropdown.classList.toggle('open');
            if (voiceDropdown) voiceDropdown.classList.remove('open');
        });
    }

    // Speed items
    document.querySelectorAll('.speed-item').forEach(item => {
        item.addEventListener('click', () => {
            const value = parseFloat(item.dataset.value);
            selectSpeed(value);
        });
    });

    // Close dropdowns when clicking outside
    document.addEventListener('click', () => {
        if (voiceDropdown) voiceDropdown.classList.remove('open');
        if (speedDropdown) speedDropdown.classList.remove('open');
    });
}

function selectSpeed(value) {
    playbackSpeed = value;

    // Update display
    const speedIcon = document.querySelector('#speedToggle .speed-icon');
    if (speedIcon) speedIcon.textContent = `${value}x`;

    // Update selected state
    document.querySelectorAll('.speed-item').forEach(item => {
        item.classList.toggle('selected', parseFloat(item.dataset.value) === value);
    });

    // Apply to audio
    if (currentAudio) {
        currentAudio.playbackRate = playbackSpeed;
    }

    // Close dropdown
    const speedDropdown = document.getElementById('speedDropdown');
    if (speedDropdown) speedDropdown.classList.remove('open');
    saveProgress();
}

function updateSpeedDisplay() {
    const speedIcon = document.querySelector('#speedToggle .speed-icon');
    if (speedIcon) speedIcon.textContent = `${playbackSpeed}x`;
    document.querySelectorAll('.speed-item').forEach(item => {
        item.classList.toggle('selected', parseFloat(item.dataset.value) === playbackSpeed);
    });
}

// ============ FONT SIZE ============
function updateFontSizeDisplay() {
    const fontSizeLevel = document.getElementById('fontSizeLevel');
    if (fontSizeLevel) {
        fontSizeLevel.textContent = `${bookFontSize}%`;
    }

    // Apply font size class to book page content
    if (bookPageContent) {
        bookPageContent.className = 'book-page-content font-size-' + bookFontSize;
    }
}

function fontSizeUp() {
    if (bookFontSize < 140) {
        bookFontSize += 10;
        updateFontSizeDisplay();
    }
}

function fontSizeDown() {
    if (bookFontSize > 80) {
        bookFontSize -= 10;
        updateFontSizeDisplay();
    }
}

// ============ VIEW MODE (BOOK vs TEXT) ============
function setViewMode(mode) {
    currentViewMode = mode;

    // Update button states
    const bookBtn = document.getElementById('bookViewBtn');
    const textBtn = document.getElementById('textViewBtn');

    if (bookBtn && textBtn) {
        bookBtn.classList.toggle('active', mode === 'book');
        textBtn.classList.toggle('active', mode === 'text');
    }

    // Toggle visibility of reading areas
    if (bookReadingArea && textReadingArea) {
        bookReadingArea.classList.add('hidden');
        textReadingArea.classList.add('hidden');

        if (mode === 'book') {
            bookReadingArea.classList.remove('hidden');
            renderBookSentences();
        } else if (mode === 'text') {
            textReadingArea.classList.remove('hidden');
            renderSentences();
        }
    }
}

// Render sentences as flowing text in Book View (looks like a book page)
// Languages whose script is read right-to-left.
const RTL_LANGS = new Set(['ar', 'he', 'fa', 'ur', 'yi', 'ps', 'sd']);

function _isRtlBook() {
    return !!(currentBook && currentBook.detected_language &&
              RTL_LANGS.has(currentBook.detected_language));
}

function renderBookSentences() {
    if (!bookPageContent) return;

    // Apply RTL direction at the page-content level when the book's detected
    // language is right-to-left. Without this the browser lays Arabic out
    // visually LTR which makes paragraphs unreadable.
    if (_isRtlBook()) {
        bookPageContent.setAttribute('dir', 'rtl');
        bookPageContent.lang = currentBook.detected_language;
    } else {
        bookPageContent.removeAttribute('dir');
        bookPageContent.removeAttribute('lang');
    }

    if (pageSentences.length === 0) {
        bookPageContent.innerHTML = '<p style="color: #888; font-style: italic;">No text available for this page.</p>';
        return;
    }

    let html = '';
    let inParagraph = false;

    pageSentences.forEach((sentence, index) => {
        const text = (sentence.text || '').trim();
        if (!text) return;

        const isActive = index === currentSentenceIndex;
        const isHeading = sentence.is_heading === true;

        // Paragraph break detection:
        // New sentences use is_paragraph_start (new format).
        // For old-format sentences without that field, fall back to index === 0.
        const hasStructuredData = typeof sentence.is_paragraph_start !== 'undefined';
        const startsNewParagraph = hasStructuredData
            ? sentence.is_paragraph_start === true
            : index === 0;

        // Close open paragraph before headings or new paragraphs
        if ((startsNewParagraph || isHeading) && inParagraph) {
            html += '</p>';
            inParagraph = false;
        }

        if (isHeading) {
            // Headings rendered as block-level elements, not inside <p>
            html += `<h3 class="book-heading${isActive ? ' active' : ''}" data-index="${index}" onclick="seekToSentence(${index})">${escapeHtml(text)}</h3>`;
        } else {
            if (!inParagraph) {
                html += '<p>';
                inParagraph = true;
            }
            html += `<span class="book-sentence${isActive ? ' active' : ''}" data-index="${index}" onclick="seekToSentence(${index})">${escapeHtml(text)}</span> `;
        }
    });

    if (inParagraph) {
        html += '</p>';
    }

    // Two-page spread: append the next page's sentences in a right-side
    // preview after a forced column break. Click handlers on preview
    // sentences advance to that page first, then seek.
    if (_isSpreadMode() && previewSentences.length > 0) {
        html += '<div class="spread-page-break" aria-hidden="true"></div>';
        html += '<div class="spread-preview">';
        let pInPara = false;
        previewSentences.forEach((sentence, index) => {
            const text = (sentence.text || '').trim();
            if (!text) return;

            const isHeading = sentence.is_heading === true;
            const hasStructured = typeof sentence.is_paragraph_start !== 'undefined';
            const startsPara = hasStructured ? sentence.is_paragraph_start === true : index === 0;

            if ((startsPara || isHeading) && pInPara) {
                html += '</p>';
                pInPara = false;
            }
            if (isHeading) {
                html += `<h3 class="book-heading" data-preview-index="${index}" onclick="seekToPreviewSentence(${index})">${escapeHtml(text)}</h3>`;
            } else {
                if (!pInPara) { html += '<p>'; pInPara = true; }
                html += `<span class="book-sentence" data-preview-index="${index}" onclick="seekToPreviewSentence(${index})">${escapeHtml(text)}</span> `;
            }
        });
        if (pInPara) html += '</p>';
        html += '</div>';
    }

    bookPageContent.innerHTML = html;

    // Apply current font size
    updateFontSizeDisplay();
}

// Click handler for sentences in the right-side preview (page N+1).
// Advance to that page, then seek to the clicked sentence.
async function seekToPreviewSentence(index) {
    const total = (currentBook && currentBook.total_pages) || 0;
    if (currentPage >= total) return;
    showLoading();
    try {
        await renderPage(currentPage + 1);
    } finally {
        hideLoading();
    }
    // After renderPage, currentPage is now the previously-previewed page,
    // and pageSentences holds its sentences. Seek to the clicked index.
    seekToSentence(index);
}

// When the viewport crosses the spread breakpoint, refresh the visual layout
// without going through renderPage — that would reload the audio and
// interrupt playback. We just (re)fetch the preview page if needed and
// re-render the sentences.
if (typeof window !== 'undefined' && window.matchMedia) {
    const _spreadMQ = window.matchMedia('(min-width: 1100px)');
    const _onSpreadChange = async () => {
        if (typeof currentBook === 'undefined' || !currentBook || typeof currentPage !== 'number') return;
        if (_isSpreadMode()) {
            const total = currentBook.total_pages || 0;
            if (currentPage < total) {
                try {
                    const r = await fetch(`${API_BASE}/books/${currentBook.id}/pages/${currentPage + 1}`);
                    if (r.ok) {
                        const d = await r.json();
                        previewSentences = Array.isArray(d.sentences) ? d.sentences : [];
                    } else {
                        previewSentences = [];
                    }
                } catch (e) {
                    previewSentences = [];
                }
            } else {
                previewSentences = [];
            }
        } else {
            previewSentences = [];
        }
        _updatePageNumberFooters(currentPage);
        if (currentViewMode === 'book') renderBookSentences();
    };
    if (_spreadMQ.addEventListener) {
        _spreadMQ.addEventListener('change', _onSpreadChange);
    } else if (_spreadMQ.addListener) {
        _spreadMQ.addListener(_onSpreadChange);
    }
}

function renderSentences() {
    if (!sentenceList) return;

    if (pageSentences.length === 0) {
        sentenceList.innerHTML = '<div class="no-sentences">No text available for this page.</div>';
        return;
    }

    sentenceList.innerHTML = pageSentences.map((sentence, index) => {
        const text = sentence.text || '';
        const isActive = index === currentSentenceIndex;
        return `
            <div class="sentence-item ${isActive ? 'active' : ''}"
                 data-index="${index}"
                 onclick="seekToSentence(${index})">
                <span class="sentence-number">${index + 1}</span>
                <span class="sentence-text">${escapeHtml(text)}</span>
            </div>
        `;
    }).join('');

    // Scroll to current sentence if active
    scrollToActiveSentence();
}

function seekToSentence(sentenceIndex) {
    if (!currentAudio || pageSentences.length === 0) {
        showToast('Audio not ready. Please wait...', 'warning');
        return;
    }

    const duration = currentAudio.duration;
    let targetTime = 0;

    // Use real timing data if available
    if (audioTiming.length > 0 && sentenceIndex < audioTiming.length) {
        targetTime = audioTiming[sentenceIndex].offset;
    } else {
        // Fallback: estimate timing
        const avgTimePerSentence = duration / pageSentences.length;
        targetTime = sentenceIndex * avgTimePerSentence;
    }

    // Seek to that position (clamped to duration)
    currentAudio.currentTime = Math.min(targetTime, duration - 0.1);
    currentSentenceIndex = sentenceIndex;

    // Update progress bar
    const percent = (currentAudio.currentTime / duration) * 100;
    if (progressBar) progressBar.value = percent;
    const currentTimeEl = document.getElementById('currentTime');
    if (currentTimeEl) currentTimeEl.textContent = formatTime(currentAudio.currentTime);

    // Start playing if not already
    if (!isPlaying) {
        togglePlayback();
    }

    // Update highlighting based on view mode
    if (currentViewMode === 'book') {
        updateBookSentenceHighlight(sentenceIndex);
    } else {
        updateSentenceHighlight(sentenceIndex);
    }
}

function updateSentenceHighlight(sentenceIndex) {
    // Update text view highlighting
    const sentenceItems = sentenceList?.querySelectorAll('.sentence-item');
    sentenceItems?.forEach((item, index) => {
        item.classList.toggle('active', index === sentenceIndex);
    });

    // Scroll to the active sentence
    scrollToActiveSentence();
}

function scrollToActiveSentence() {
    const activeItem = sentenceList?.querySelector('.sentence-item.active');
    if (activeItem) {
        activeItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

// ============ PAGE NAVIGATION ============
function updateReaderHeader() {
    document.getElementById('currentBookTitle').textContent = currentBook.title;
    updatePageInfo();
}

function updatePageInfo() {
    document.getElementById('currentPageNumber').textContent = currentPage;
    document.getElementById('totalPages').textContent = currentBook.total_pages || '?';

    // Disable side nav buttons at the first/last page boundaries
    const total = currentBook.total_pages || 1;
    const prevBtn = document.getElementById('pageNavPrev');
    const nextBtn = document.getElementById('pageNavNext');
    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= total;
}

async function prevPage() {
    if (currentPage > 1) {
        showLoading();
        await renderPage(currentPage - 1);
        hideLoading();
    }
}

async function nextPage() {
    if (currentPage < (currentBook.total_pages || 1)) {
        showLoading();
        await renderPage(currentPage + 1);
        hideLoading();
    }
}

// ============ AUDIO ============
async function loadPageAudio(pageNumber, _retryCount) {
    // Increment generation counter — any pending retry from a prior call will self-cancel
    const myGeneration = ++_audioLoadGeneration;

    // Reset audio state
    if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
    }
    isPlaying = false;
    if (playPauseBtn) playPauseBtn.textContent = 'Play';
    if (progressBar) progressBar.value = 0;
    const currentTimeEl = document.getElementById('currentTime');
    const totalTimeEl = document.getElementById('totalTime');
    if (currentTimeEl) currentTimeEl.textContent = '0:00';
    if (totalTimeEl) totalTimeEl.textContent = '0:00';

    const retryCount = _retryCount || 0;

    try {
        // Build URL with voice parameter if selected
        let audioUrl = `${API_BASE}/books/${currentBook.id}/pages/${pageNumber}/audio`;
        if (selectedVoice) {
            audioUrl += `?voice=${encodeURIComponent(selectedVoice)}`;
        }
        const response = await fetch(audioUrl);

        if (response.status === 202) {
            // Audio is being prepared in the background — no toast, just update status quietly
            setAudioPrepStatus('Audio being prepared…');
            // Retry silently every 10 s (max ~3 min = 18 attempts)
            // The generation check ensures stale retries (after page navigation) self-cancel
            if (retryCount < 18) {
                setTimeout(() => {
                    if (_audioLoadGeneration === myGeneration) {
                        loadPageAudio(pageNumber, retryCount + 1);
                    }
                }, 10000);
            }
            return;
        }

        // Clear prep status once audio is available
        setAudioPrepStatus('');

        if (!response.ok) {
            console.log('Audio not available for this page');
            return;
        }

        const audioBlob = await response.blob();
        const audioBlobUrl = URL.createObjectURL(audioBlob);

        currentAudio = new Audio(audioBlobUrl);
        currentAudio.playbackRate = playbackSpeed;

        currentAudio.addEventListener('timeupdate', updateProgress);
        currentAudio.addEventListener('ended', handleAudioEnded);
        currentAudio.addEventListener('loadedmetadata', () => {
            document.getElementById('totalTime').textContent = formatTime(currentAudio.duration);
        });
        currentAudio.addEventListener('error', (e) => {
            console.error('Audio error:', e);
        });

    } catch (error) {
        console.error('Failed to load audio:', error);
    }
}

// Set a quiet, non-intrusive status message in the audio controls area (not a toast)
function setAudioPrepStatus(msg) {
    const el = document.getElementById('audioPrepStatus');
    if (el) el.textContent = msg || '';
}

function togglePlayback() {
    if (!currentAudio) {
        showToast('Audio not ready. Please wait...', 'warning');
        return;
    }

    if (isPlaying) {
        currentAudio.pause();
        if (playPauseBtn) playPauseBtn.textContent = 'Play';
    } else {
        currentAudio.play();
        if (playPauseBtn) playPauseBtn.textContent = 'Pause';
    }

    isPlaying = !isPlaying;
}

function updateProgress() {
    if (!currentAudio) return;

    const percent = (currentAudio.currentTime / currentAudio.duration) * 100;
    if (progressBar) progressBar.value = percent || 0;
    const currentTimeEl = document.getElementById('currentTime');
    if (currentTimeEl) currentTimeEl.textContent = formatTime(currentAudio.currentTime);

    // Highlight current sentence in text layer
    highlightCurrentSentence(currentAudio.currentTime);
}

function highlightCurrentSentence(currentTime) {
    if (!currentAudio || pageSentences.length === 0) return;

    let newSentenceIdx = 0;

    // Use real timing data if available
    if (audioTiming.length > 0) {
        // Find which sentence we're in based on actual TTS timing
        for (let i = 0; i < audioTiming.length; i++) {
            const timing = audioTiming[i];
            const sentenceStart = timing.offset;
            const sentenceEnd = timing.offset + timing.duration;

            if (currentTime >= sentenceStart && currentTime < sentenceEnd) {
                newSentenceIdx = i;
                break;
            } else if (currentTime < sentenceStart) {
                // We're before this sentence, use previous
                newSentenceIdx = Math.max(0, i - 1);
                break;
            } else {
                // We're past this sentence
                newSentenceIdx = i;
            }
        }
    } else {
        // Fallback: estimate timing proportionally by character count
        const duration = currentAudio.duration;
        const totalChars = pageSentences.reduce((sum, s) => sum + (s.text?.length || 1), 0);
        let elapsed = 0;
        for (let i = 0; i < pageSentences.length; i++) {
            const sentDuration = duration * ((pageSentences[i].text?.length || 1) / totalChars);
            if (currentTime < elapsed + sentDuration) {
                newSentenceIdx = i;
                break;
            }
            elapsed += sentDuration;
            newSentenceIdx = i;
        }
    }

    // Only update if sentence changed
    if (newSentenceIdx !== currentSentenceIndex) {
        currentSentenceIndex = newSentenceIdx;

        // Update highlighting based on current view mode
        if (currentViewMode === 'book') {
            updateBookSentenceHighlight(currentSentenceIndex);
        } else if (currentViewMode === 'text') {
            updateSentenceHighlight(currentSentenceIndex);
        }
    }
}

// Update highlighting in Book View
// Uses data-index attribute (set at render time) to match sentences and headings
// correctly regardless of how many heading elements precede a sentence.
function updateBookSentenceHighlight(sentenceIndex) {
    bookPageContent?.querySelectorAll('.book-sentence, .book-heading').forEach(item => {
        const idx = parseInt(item.dataset.index, 10);
        item.classList.toggle('active', idx === sentenceIndex);
    });

    // Scroll to keep the active element visible
    const activeItem = bookPageContent?.querySelector('.book-sentence.active, .book-heading.active');
    if (activeItem) {
        activeItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

function seekAudio() {
    if (!currentAudio) return;

    if (!progressBar) return;
    const percent = progressBar.value;
    currentAudio.currentTime = (percent / 100) * currentAudio.duration;
}

async function handleAudioEnded() {
    isPlaying = false;
    if (playPauseBtn) playPauseBtn.textContent = 'Play';

    // Auto-advance to next page if not last
    if (currentPage < (currentBook.total_pages || 1)) {
        setTimeout(async () => {
            showLoading();
            await renderPage(currentPage + 1);
            hideLoading();
            // Auto-play next page after a short delay
            setTimeout(() => {
                if (currentAudio) {
                    togglePlayback();
                }
            }, 1000);
        }, 500);
    } else {
        showToast('End of book', 'info');
    }
}

// ============ PROGRESS ============
async function saveProgress() {
    if (!currentBook || !currentUser) return;

    try {
        await fetch(`${API_BASE}/books/${currentBook.id}/progress`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: currentUser.id,
                current_page: currentPage,
                current_sentence: currentSentenceIndex,
                playback_speed: playbackSpeed,
                voice_id: selectedVoice || null
            })
        });
    } catch (error) {
        console.error('Failed to save progress:', error);
    }
}

// ============ BOOKMARKS ============
async function loadBookmarks() {
    if (!currentBook || !currentUser) return;

    try {
        const response = await fetch(`${API_BASE}/books/${currentBook.id}/bookmarks?user_id=${currentUser.id}`);
        bookmarks = await response.json();
    } catch (error) {
        console.error('Failed to load bookmarks:', error);
        bookmarks = [];
    }
}

async function addBookmark() {
    if (!currentBook || !currentUser) return;

    try {
        const response = await fetch(`${API_BASE}/books/${currentBook.id}/bookmarks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: currentUser.id,
                page_number: currentPage,
                sentence_index: currentSentenceIndex,
                label: `Page ${currentPage}`
            })
        });

        if (response.ok) {
            const bookmark = await response.json();
            bookmarks.push(bookmark);
            showToast('Bookmark added!', 'success');
        }
    } catch (error) {
        console.error('Failed to add bookmark:', error);
        showToast('Failed to add bookmark', 'error');
    }
}

async function deleteBookmark(bookmarkId) {
    try {
        await fetch(`${API_BASE}/bookmarks/${bookmarkId}`, {
            method: 'DELETE',
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        });
        bookmarks = bookmarks.filter(b => b.id !== bookmarkId);
        renderBookmarks();
        showToast('Bookmark deleted', 'success');
    } catch (error) {
        showToast('Failed to delete bookmark', 'error');
    }
}

function renderBookmarks() {
    const list = document.getElementById('bookmarksList');
    const noBookmarks = document.getElementById('noBookmarks');

    if (bookmarks.length === 0) {
        list.innerHTML = '';
        noBookmarks.style.display = 'block';
        return;
    }

    noBookmarks.style.display = 'none';
    list.innerHTML = bookmarks.map(b => `
        <div class="bookmark-item">
            <span class="bookmark-icon">B</span>
            <span class="bookmark-label" onclick="goToBookmark(${b.page_number})">${escapeHtml(b.label || `Page ${b.page_number}`)}</span>
            <button class="bookmark-delete" onclick="deleteBookmark(${b.id})">x</button>
        </div>
    `).join('');
}

async function goToBookmark(pageNumber) {
    hideBookmarksModal();
    showLoading();
    await renderPage(pageNumber);
    hideLoading();
}

// ============ SLEEP TIMER ============
function startSleepTimer(minutes) {
    if (sleepTimerId) {
        clearInterval(sleepTimerId);
    }

    if (minutes === 0) {
        document.getElementById('sleepTimerActive').classList.add('hidden');
        sleepTimeRemaining = 0;
        hideSleepTimerModal();
        showToast('Sleep timer cancelled', 'info');
        return;
    }

    sleepTimeRemaining = minutes * 60;
    document.getElementById('sleepTimerActive').classList.remove('hidden');
    document.getElementById('sleepTimeRemaining').textContent = formatTime(sleepTimeRemaining);

    sleepTimerId = setInterval(() => {
        sleepTimeRemaining--;
        document.getElementById('sleepTimeRemaining').textContent = formatTime(sleepTimeRemaining);

        if (sleepTimeRemaining <= 0) {
            clearInterval(sleepTimerId);
            sleepTimerId = null;
            if (currentAudio && isPlaying) {
                togglePlayback();
            }
            document.getElementById('sleepTimerActive').classList.add('hidden');
            showToast('Sleep timer ended. Playback stopped.', 'info');
        }
    }, 1000);

    hideSleepTimerModal();
    showToast(`Sleep timer set for ${minutes} minutes`, 'success');
}

function cancelSleepTimer() {
    if (sleepTimerId) {
        clearInterval(sleepTimerId);
        sleepTimerId = null;
    }
    sleepTimeRemaining = 0;
    document.getElementById('sleepTimerActive').classList.add('hidden');
    showToast('Sleep timer cancelled', 'info');
}

// ============ FILE UPLOAD ============
function handleDragOver(e) {
    e.preventDefault();
    e.currentTarget.classList.add('dragover');
}

function handleDragLeave(e) {
    e.currentTarget.classList.remove('dragover');
}

function handleDrop(e) {
    e.preventDefault();
    e.currentTarget.classList.remove('dragover');

    const files = e.dataTransfer.files;
    if (files.length > 0) {
        handleFile(files[0]);
    }
}

function handleFileSelect(e) {
    if (e.target.files.length > 0) {
        handleFile(e.target.files[0]);
    }
}

function handleFile(file) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
        showToast('Only PDF files are allowed', 'error');
        return;
    }

    document.getElementById('selectedFileName').textContent = file.name;
    document.getElementById('fileInput').files = createFileList(file);

    // Auto-fill title from filename
    const title = file.name.replace('.pdf', '').replace(/_/g, ' ');
    document.getElementById('bookTitle').value = title;
}

function createFileList(file) {
    const dt = new DataTransfer();
    dt.items.add(file);
    return dt.files;
}

async function handleUpload(e) {
    e.preventDefault();

    const fileInput = document.getElementById('fileInput');
    const file = fileInput.files[0];

    if (!file) {
        showToast('Please select a file', 'error');
        return;
    }

    const formData = new FormData();
    formData.append('file', file);
    formData.append('user_id', currentUser?.id || '');
    formData.append('title', document.getElementById('bookTitle').value || file.name);
    formData.append('author', document.getElementById('bookAuthor').value || '');

    document.getElementById('uploadProgress').classList.remove('hidden');
    document.getElementById('uploadSubmitBtn').disabled = true;

    try {
        const response = await fetch(`${API_BASE}/books`, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: formData
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Upload failed');
        }

        const book = await response.json();
        books.unshift(book);
        renderLibrary();

        hideUploadModal();
        showToast('Book uploaded! Processing...', 'success');

        // Poll for processing completion
        pollBookStatus(book.id);

    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        document.getElementById('uploadProgress').classList.add('hidden');
        document.getElementById('uploadSubmitBtn').disabled = false;
    }
}

async function pollBookStatus(bookId) {
    try {
        const response = await fetch(`${API_BASE}/books/${bookId}`);
        const book = await response.json();

        const index = books.findIndex(b => b.id === bookId);
        if (index !== -1) {
            books[index] = book;
            renderLibrary();
        }

        if (book.upload_status === 'processing') {
            setTimeout(() => pollBookStatus(bookId), 3000);
        } else if (book.upload_status === 'ready') {
            showToast('Book ready to read!', 'success');
        } else if (book.upload_status === 'failed') {
            showToast('Book processing failed', 'error');
        }
    } catch (error) {
        console.error('Failed to poll book status:', error);
    }
}

// ============ IN-READER AUDIO PROGRESS BAR ============

let _readerAudioProgressInterval = null;
// Incremented on every loadPageAudio call so stale retries self-cancel
let _audioLoadGeneration = 0;

function startReaderAudioProgress(bookId) {
    stopReaderAudioProgress();
    _checkReaderAudioProgress(bookId);  // immediate first check
    _readerAudioProgressInterval = setInterval(
        () => _checkReaderAudioProgress(bookId), 5000
    );
}

function stopReaderAudioProgress() {
    if (_readerAudioProgressInterval) {
        clearInterval(_readerAudioProgressInterval);
        _readerAudioProgressInterval = null;
    }
    const bar = document.getElementById('audioPrepBar');
    if (bar) bar.classList.add('hidden');
}

async function _checkReaderAudioProgress(bookId) {
    try {
        const r = await fetch(`${API_BASE}/books/${bookId}/audio-progress`);
        if (!r.ok) return;
        const p = await r.json();

        if (p.status === 'completed') {
            stopReaderAudioProgress();
            return;
        }

        const bar  = document.getElementById('audioPrepBar');
        const fill = document.getElementById('audioPrepFill');
        const lbl  = document.getElementById('audioPrepLabel');
        if (!bar) return;

        const pct = p.percentage || 0;
        bar.classList.remove('hidden');
        if (fill) fill.style.width = `${pct}%`;
        if (lbl) lbl.textContent =
            `Preparing audio ${pct}%\u2002(${p.pages_completed}\u202f/\u202f${p.total_pages} pages)`;
    } catch (e) {
        // silent — don't spam console during normal idle periods
    }
}

// ============ VIEW SWITCHING ============
function switchView(view) {
    const viewBtns = document.querySelectorAll('.view-btn');
    viewBtns.forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === view);
    });

    // Get the container for adding reader-active class
    const container = document.querySelector('.nav-minimal-page');

    if (view === 'library') {
        if (libraryView) libraryView.classList.remove('hidden');
        if (readerView) readerView.classList.add('hidden');
        if (container) container.classList.remove('reader-active');
        // Stop in-reader progress bar and restart library card polling
        stopReaderAudioProgress();
        pollAudioProgress();
        if (currentAudio && isPlaying) {
            currentAudio.pause();
            isPlaying = false;
        }
        // Drop the #book/<id> hash so a refresh on the library doesn't
        // jump straight back into the last book.
        _clearBookHash();
    } else {
        if (libraryView) libraryView.classList.add('hidden');
        if (readerView) readerView.classList.remove('hidden');
        if (container) container.classList.add('reader-active');
        // Pause library card polling while in reader (in-reader bar handles progress)
        if (audioProgressInterval) {
            clearInterval(audioProgressInterval);
            audioProgressInterval = null;
        }
    }
}

// ============ MODALS ============
function showUploadModal() {
    const modal = document.getElementById('uploadModal');
    if (!modal) return;
    modal.classList.add('visible');
    // Reset form
    const form = document.getElementById('uploadForm');
    if (form) form.reset();
    const fileName = document.getElementById('selectedFileName');
    if (fileName) fileName.textContent = '';
    const progress = document.getElementById('uploadProgress');
    if (progress) progress.classList.add('hidden');
}

function hideUploadModal() {
    const modal = document.getElementById('uploadModal');
    if (modal) modal.classList.remove('visible');
}

// ── Users management modal ───────────────────────────────────────────────────

function showUsersModal() {
    const modal = document.getElementById('usersModal');
    if (!modal) return;
    renderUsersList();
    const form = document.getElementById('createUserForm');
    if (form) form.reset();
    const avatar = document.getElementById('newUserAvatar');
    if (avatar) avatar.value = '📚';
    modal.classList.add('visible');
}

function hideUsersModal() {
    const modal = document.getElementById('usersModal');
    if (modal) modal.classList.remove('visible');
}

function renderUsersList() {
    const list = document.getElementById('usersList');
    const empty = document.getElementById('noUsersMessage');
    if (!list) return;

    list.innerHTML = '';

    if (!allUsers.length) {
        if (empty) empty.classList.remove('hidden');
        return;
    }
    if (empty) empty.classList.add('hidden');

    const isOnlyUser = allUsers.length === 1;

    allUsers.forEach(user => {
        const li = document.createElement('li');
        li.className = 'user-row';
        li.dataset.userId = user.id;

        const avatar = document.createElement('span');
        avatar.className = 'user-row-avatar';
        avatar.textContent = user.avatar || 'U';

        const name = document.createElement('span');
        name.className = 'user-row-name';
        name.textContent = user.name;

        li.appendChild(avatar);
        li.appendChild(name);

        if (currentUser && user.id === currentUser.id) {
            const badge = document.createElement('span');
            badge.className = 'user-row-active';
            badge.textContent = 'Active';
            li.appendChild(badge);
        }

        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'user-row-delete';
        del.textContent = 'Delete';
        del.disabled = isOnlyUser;
        del.title = isOnlyUser ? 'Cannot delete the only user' : `Delete ${user.name}`;
        del.addEventListener('click', () => handleDeleteUser(user));
        li.appendChild(del);

        list.appendChild(li);
    });
}

async function handleCreateUser(event) {
    event.preventDefault();
    const nameInput = document.getElementById('newUserName');
    const avatarInput = document.getElementById('newUserAvatar');
    const submitBtn = document.getElementById('createUserSubmitBtn');

    const name = (nameInput?.value || '').trim();
    const avatar = (avatarInput?.value || '').trim() || '📚';

    if (!name) {
        showToast('Name is required', 'error');
        return;
    }

    if (submitBtn) submitBtn.disabled = true;
    try {
        const response = await fetch(`${API_BASE}/users`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, avatar }),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const created = await response.json();
        await loadUsers();
        renderUsersList();
        renderUserPills();
        showToast(`Created ${created.name}`, 'success');
        if (nameInput) nameInput.value = '';
        if (avatarInput) avatarInput.value = '📚';
    } catch (err) {
        console.error('[users] create failed:', err);
        showToast(`Failed to create user: ${err.message}`, 'error');
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function handleDeleteUser(user) {
    if (allUsers.length <= 1) {
        showToast('Cannot delete the only user', 'error');
        return;
    }
    const confirmed = window.confirm(
        `Delete ${user.name}? This will also remove all their books, bookmarks, and progress.`
    );
    if (!confirmed) return;

    try {
        const response = await fetch(`${API_BASE}/users/${user.id}`, { method: 'DELETE' });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${response.status}`);
        }

        const wasCurrent = currentUser && currentUser.id === user.id;
        await loadUsers();

        if (wasCurrent && allUsers.length > 0) {
            await switchUser(allUsers[0].id);
        } else {
            renderUserPills();
        }
        renderUsersList();
        showToast(`Deleted ${user.name}`, 'success');
    } catch (err) {
        console.error('[users] delete failed:', err);
        showToast(`Failed to delete user: ${err.message}`, 'error');
    }
}

function showBookmarksModal() {
    renderBookmarks();
    const modal = document.getElementById('bookmarksModal');
    if (modal) modal.classList.add('visible');
}

function hideBookmarksModal() {
    const modal = document.getElementById('bookmarksModal');
    if (modal) modal.classList.remove('visible');
}

function showSleepTimerModal() {
    const modal = document.getElementById('sleepTimerModal');
    if (modal) modal.classList.add('visible');
}

function hideSleepTimerModal() {
    const modal = document.getElementById('sleepTimerModal');
    if (modal) modal.classList.remove('visible');
}

function showShortcutsModal() {
    const modal = document.getElementById('shortcutsModal');
    if (modal) modal.classList.add('visible');
}

function hideShortcutsModal() {
    const modal = document.getElementById('shortcutsModal');
    if (modal) modal.classList.remove('visible');
}

// ============ KEYBOARD SHORTCUTS ============
function handleKeyboard(e) {
    // Ignore if typing in input
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    // Helper to check if reader view is visible
    const isReaderVisible = readerView && !readerView.classList.contains('hidden');

    switch (e.key.toLowerCase()) {
        case ' ':
            if (isReaderVisible) {
                e.preventDefault();
                togglePlayback();
            }
            break;
        case 'arrowleft':
            if (isReaderVisible) {
                prevPage();
            }
            break;
        case 'arrowright':
            if (isReaderVisible) {
                nextPage();
            }
            break;
        case 'b':
            if (isReaderVisible) {
                addBookmark();
            }
            break;
        case '+':
        case '=':
            if (isReaderVisible) {
                fontSizeUp();
            }
            break;
        case '-':
            if (isReaderVisible) {
                fontSizeDown();
            }
            break;
        case 'escape':
            // Close any open modal or go back to library
            const openModal = document.querySelector('.modal.visible');
            if (openModal) {
                openModal.classList.remove('visible');
            } else if (isReaderVisible) {
                switchView('library');
            }
            break;
        case '?':
            showShortcutsModal();
            break;
    }
}

// ============ THEME ============
// Theme functions are now handled by shared.js
// loadTheme and toggleTheme are defined globally

// ============ UTILITIES ============
function formatTime(seconds) {
    if (!seconds || isNaN(seconds)) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showLoading() {
    if (loadingOverlay) {
        loadingOverlay.classList.remove('hidden');
    }
}

function hideLoading() {
    if (loadingOverlay) {
        loadingOverlay.classList.add('hidden');
    }
}

function showToast(message, type = 'info') {
    if (!toastContainer) {
        console.warn('Toast:', message);
        return;
    }
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('toast-fade');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ============ CATEGORIES ============

async function loadCategoryPresets() {
    if (categoriesState.presets.length) return;
    try {
        const res = await fetch(`${API_BASE}/categories/presets`);
        if (!res.ok) return;
        categoriesState.presets = await res.json();
        categoriesState.presetByKey = {};
        for (const p of categoriesState.presets) {
            categoriesState.presetByKey[p.key] = p;
        }
    } catch (err) {
        console.error('[categories] presets load failed', err);
    }
}

async function loadCategories() {
    if (!currentUser) {
        categoriesState.list = [];
        categoriesState.byId = {};
        categoriesState.uncategorisedCount = 0;
        return;
    }
    try {
        const res = await fetch(`${API_BASE}/categories?user_id=${currentUser.id}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        categoriesState.list = data.categories || [];
        categoriesState.uncategorisedCount = data.uncategorised_count || 0;
        categoriesState.byId = {};
        for (const c of categoriesState.list) categoriesState.byId[c.id] = c;
    } catch (err) {
        console.error('[categories] load failed', err);
        categoriesState.list = [];
        categoriesState.byId = {};
    }
}

function _categoryArt(cat) {
    if (cat.image_path) {
        return `<div class="category-tile-art"><img src="${API_BASE}/categories/${cat.id}/image?v=${Date.now()}" alt=""></div>`;
    }
    if (cat.preset_image) {
        const preset = categoriesState.presetByKey[cat.preset_image];
        // Prefer the illustrated icon (Iconify SVG) over the bare emoji.
        // The colour-blocked background comes from CSS class `preset-<key>`.
        if (preset && preset.icon) {
            return `<div class="category-tile-art preset-${escapeHtml(cat.preset_image)}">
                <img class="category-tile-icon" src="${preset.icon}" alt="${escapeHtml(preset.label || '')}"
                     onerror="this.replaceWith(Object.assign(document.createTextNode('${preset.emoji || '📚'}')));">
            </div>`;
        }
        const emoji = preset ? preset.emoji : '📚';
        return `<div class="category-tile-art preset-${escapeHtml(cat.preset_image)}">${emoji}</div>`;
    }
    return `<div class="category-tile-art">${escapeHtml(cat.emoji || '📚')}</div>`;
}

function _bookCountFor(category) {
    // Prefer the API-supplied count (sees all books, not just paginated). Fall
    // back to filtering the in-memory list, which the API matches anyway.
    if (typeof category.book_count === 'number') return category.book_count;
    return books.filter(b => b.category_id === category.id).length;
}

function showCategoryBrowser() {
    categoriesState.currentCategoryId = undefined;

    const browser = document.getElementById('categoryBrowser');
    const contents = document.getElementById('categoryContents');
    if (browser) browser.classList.remove('hidden');
    if (contents) contents.classList.add('hidden');

    const grid = document.getElementById('categoryGrid');
    if (!grid) return;
    grid.innerHTML = '';

    // Virtual tiles first.
    const allTile = _buildCategoryTile({
        id: '__all__',
        name: 'All books',
        emoji: '📖',
        bookCount: books.length,
        virtual: true,
        onClick: () => { categoriesState.currentCategoryId = null; renderLibrary(); },
    });
    grid.appendChild(allTile);

    if (categoriesState.uncategorisedCount > 0) {
        const uncTile = _buildCategoryTile({
            id: '__uncategorised__',
            name: 'Uncategorised',
            emoji: '🗂️',
            bookCount: categoriesState.uncategorisedCount,
            virtual: true,
            onClick: () => { categoriesState.currentCategoryId = -1; renderLibrary(); },
        });
        grid.appendChild(uncTile);
    }

    // Top-level categories only on the landing screen; sub-categories appear
    // as a strip inside their parent's contents view.
    const topLevel = categoriesState.list.filter(c => !c.parent_id);
    for (const cat of topLevel) {
        const tile = document.createElement('div');
        tile.className = 'category-tile';
        tile.dataset.categoryId = cat.id;
        tile.innerHTML = `
            ${_categoryArt(cat)}
            <div class="category-tile-meta">
                <div class="category-tile-name">${escapeHtml(cat.name)}</div>
                <div class="category-tile-count">${_bookCountFor(cat)} book${_bookCountFor(cat) === 1 ? '' : 's'}</div>
            </div>
        `;
        tile.addEventListener('click', () => {
            categoriesState.currentCategoryId = cat.id;
            renderLibrary();
        });
        grid.appendChild(tile);
    }
}

function _buildCategoryTile({ id, name, emoji, bookCount, virtual, onClick }) {
    const tile = document.createElement('div');
    tile.className = 'category-tile' + (virtual ? ' is-virtual' : '');
    tile.dataset.categoryId = id;
    tile.innerHTML = `
        <div class="category-tile-art">${escapeHtml(emoji)}</div>
        <div class="category-tile-meta">
            <div class="category-tile-name">${escapeHtml(name)}</div>
            <div class="category-tile-count">${bookCount} book${bookCount === 1 ? '' : 's'}</div>
        </div>
    `;
    tile.addEventListener('click', onClick);
    return tile;
}

function showCategoryContents(categoryId) {
    const browser = document.getElementById('categoryBrowser');
    const contents = document.getElementById('categoryContents');
    const titleEl = document.getElementById('categoryContentsTitle');
    const strip = document.getElementById('subcategoryStrip');

    if (browser) browser.classList.add('hidden');
    if (contents) contents.classList.remove('hidden');

    let label = 'All books';
    let filtered = books;

    if (categoryId === null) {
        label = 'All books';
        filtered = books;
    } else if (categoryId === -1) {
        label = 'Uncategorised';
        filtered = books.filter(b => !b.category_id);
    } else {
        const cat = categoriesState.byId[categoryId];
        label = cat ? `${cat.emoji ? cat.emoji + ' ' : ''}${cat.name}` : 'Category';
        // Include books in this category and any of its sub-categories.
        const subIds = categoriesState.list.filter(c => c.parent_id === categoryId).map(c => c.id);
        const idSet = new Set([categoryId, ...subIds]);
        filtered = books.filter(b => idSet.has(b.category_id));
    }

    if (titleEl) titleEl.textContent = label;

    // Sub-category chips (only when viewing a real top-level category).
    if (strip) {
        strip.innerHTML = '';
        if (typeof categoryId === 'number' && categoryId > 0) {
            const subs = categoriesState.list.filter(c => c.parent_id === categoryId);
            for (const sub of subs) {
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'subcategory-chip';
                const subBookCount = books.filter(b => b.category_id === sub.id).length;
                chip.textContent = `${sub.emoji || ''} ${sub.name} (${subBookCount})`;
                chip.addEventListener('click', () => {
                    categoriesState.currentCategoryId = sub.id;
                    renderLibrary();
                });
                strip.appendChild(chip);
            }
        }
    }

    _renderBooksGrid(filtered);
}

// ── Manage Categories modal ─────────────────────────────────────────────────

function showCategoriesModal() {
    if (!currentUser) {
        showToast('Pick a user first', 'error');
        return;
    }
    Promise.all([loadCategoryPresets(), loadCategories()]).then(() => {
        _resetCategoryForm();
        renderCategoriesList();
        renderCategoryParentSelect();
        renderPresetGrid();
        const modal = document.getElementById('categoriesModal');
        if (modal) modal.classList.add('visible');
    });
}

function hideCategoriesModal() {
    const modal = document.getElementById('categoriesModal');
    if (modal) modal.classList.remove('visible');
}

function renderCategoriesList() {
    const list = document.getElementById('categoriesList');
    if (!list) return;
    list.innerHTML = '';

    const top = categoriesState.list.filter(c => !c.parent_id);
    for (const cat of top) {
        list.appendChild(_buildCategoryRow(cat, false));
        const subs = categoriesState.list.filter(c => c.parent_id === cat.id);
        for (const sub of subs) list.appendChild(_buildCategoryRow(sub, true));
    }
}

function _categoryRowIcon(cat) {
    if (cat.image_path) return `<img src="${API_BASE}/categories/${cat.id}/image?v=${Date.now()}" alt="">`;
    if (cat.preset_image) {
        const preset = categoriesState.presetByKey[cat.preset_image];
        if (preset && preset.icon) {
            // Inline icon for the manage-categories list. Background colour
            // comes from the CSS preset class on the parent .cat-row-icon.
            return `<img class="cat-row-icon-img" src="${preset.icon}" alt="${escapeHtml(preset.label || '')}">`;
        }
        return preset ? preset.emoji : '📚';
    }
    return cat.emoji || '📚';
}

function _buildCategoryRow(cat, isChild) {
    const li = document.createElement('li');
    li.className = 'category-row' + (isChild ? ' is-child' : '');
    li.innerHTML = `
        <div class="cat-row-icon">${_categoryRowIcon(cat)}</div>
        <div class="cat-row-name">${escapeHtml(cat.name)}</div>
        <div class="cat-row-count">${cat.book_count || 0} book${(cat.book_count || 0) === 1 ? '' : 's'}</div>
    `;
    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'cat-row-action edit';
    editBtn.textContent = 'Edit';
    editBtn.addEventListener('click', () => _populateCategoryFormForEdit(cat));
    li.appendChild(editBtn);

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'cat-row-action delete';
    delBtn.textContent = 'Delete';
    delBtn.addEventListener('click', () => handleDeleteCategory(cat));
    li.appendChild(delBtn);

    return li;
}

function renderCategoryParentSelect() {
    const sel = document.getElementById('newCategoryParent');
    if (!sel) return;
    const editingId = categoriesState.editingId;
    sel.innerHTML = '<option value="">— top level —</option>';
    for (const cat of categoriesState.list) {
        if (cat.parent_id) continue;          // only top-level can be parents
        if (editingId && cat.id === editingId) continue;  // can't be its own parent
        const opt = document.createElement('option');
        opt.value = cat.id;
        opt.textContent = `${cat.emoji || '📚'} ${cat.name}`;
        sel.appendChild(opt);
    }
}

function renderPresetGrid() {
    const grid = document.getElementById('presetGrid');
    if (!grid) return;
    grid.innerHTML = '';
    for (const preset of categoriesState.presets) {
        const tile = document.createElement('button');
        tile.type = 'button';
        tile.className = 'preset-tile';
        tile.style.background = preset.colour || '#475569';
        tile.dataset.presetKey = preset.key;
        tile.title = preset.label;
        if (preset.icon) {
            const img = document.createElement('img');
            img.className = 'preset-tile-icon';
            img.alt = preset.label || '';
            img.src = preset.icon;
            // If Iconify is unreachable, fall back to the emoji glyph.
            img.addEventListener('error', () => {
                tile.removeChild(img);
                tile.textContent = preset.emoji || '📚';
            });
            tile.appendChild(img);
        } else {
            tile.textContent = preset.emoji || '📚';
        }
        tile.addEventListener('click', () => {
            categoriesState.pendingVisual = { kind: 'preset', value: preset.key };
            grid.querySelectorAll('.preset-tile').forEach(t => t.classList.toggle('selected', t === tile));
        });
        if (categoriesState.pendingVisual.kind === 'preset' &&
            categoriesState.pendingVisual.value === preset.key) {
            tile.classList.add('selected');
        }
        grid.appendChild(tile);
    }
}

function _resetCategoryForm() {
    categoriesState.editingId = null;
    categoriesState.pendingVisual = { kind: 'preset', value: 'general' };
    document.getElementById('categoryEditId').value = '';
    document.getElementById('newCategoryName').value = '';
    document.getElementById('newCategoryParent').value = '';
    document.getElementById('newCategoryEmoji').value = '';
    document.getElementById('newCategoryImage').value = '';
    document.getElementById('categoryFormTitle').textContent = 'New category';
    document.getElementById('saveCategoryBtn').textContent = 'Create category';
    document.getElementById('cancelCategoryEditBtn').classList.add('hidden');
    _switchVisualTab('preset');
}

function _populateCategoryFormForEdit(cat) {
    categoriesState.editingId = cat.id;
    document.getElementById('categoryEditId').value = cat.id;
    document.getElementById('newCategoryName').value = cat.name || '';
    document.getElementById('newCategoryParent').value = cat.parent_id || '';
    document.getElementById('newCategoryEmoji').value = cat.emoji || '';
    document.getElementById('categoryFormTitle').textContent = `Edit “${cat.name}”`;
    document.getElementById('saveCategoryBtn').textContent = 'Save changes';
    document.getElementById('cancelCategoryEditBtn').classList.remove('hidden');

    if (cat.image_path) {
        categoriesState.pendingVisual = { kind: 'image', value: null };
        _switchVisualTab('upload');
    } else if (cat.preset_image) {
        categoriesState.pendingVisual = { kind: 'preset', value: cat.preset_image };
        _switchVisualTab('preset');
    } else if (cat.emoji) {
        categoriesState.pendingVisual = { kind: 'emoji', value: cat.emoji };
        _switchVisualTab('emoji');
    }
    renderPresetGrid();
    renderCategoryParentSelect();
}

function _switchVisualTab(tab) {
    document.querySelectorAll('.visual-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    document.getElementById('visualPanePreset').classList.toggle('hidden', tab !== 'preset');
    document.getElementById('visualPaneEmoji').classList.toggle('hidden', tab !== 'emoji');
    document.getElementById('visualPaneUpload').classList.toggle('hidden', tab !== 'upload');
    if (tab !== 'preset' && categoriesState.pendingVisual.kind === 'preset') {
        // Tab switch implies the user wants the other visual; leave value
        // alone until they actually pick to make accidental clicks recoverable.
    }
}

async function handleSubmitCategoryForm(event) {
    event.preventDefault();
    const name = document.getElementById('newCategoryName').value.trim();
    const parentRaw = document.getElementById('newCategoryParent').value;
    const parent_id = parentRaw ? parseInt(parentRaw, 10) : null;
    const emojiVal = document.getElementById('newCategoryEmoji').value.trim();
    const editingId = categoriesState.editingId;

    if (!name) {
        showToast('Name is required', 'error');
        return;
    }

    // Determine which visual the user actually configured. Active tab wins.
    const activeTab = document.querySelector('.visual-tab.active')?.dataset.tab || 'preset';
    let payload = { name, parent_id };
    if (activeTab === 'preset') {
        payload.preset_image = categoriesState.pendingVisual.kind === 'preset'
            ? categoriesState.pendingVisual.value
            : 'general';
        payload.emoji = null;
    } else if (activeTab === 'emoji') {
        payload.emoji = emojiVal || '📚';
        payload.preset_image = null;
    } else {
        // upload tab — for create, fall back to a preset; image upload follows
        // creation as a separate POST below.
        if (!editingId) {
            payload.preset_image = 'general';
            payload.emoji = null;
        }
    }

    try {
        let categoryId;
        if (editingId) {
            const res = await fetch(`${API_BASE}/categories/${editingId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.error || `HTTP ${res.status}`);
            }
            categoryId = editingId;
        } else {
            const res = await fetch(`${API_BASE}/categories`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...payload, user_id: currentUser.id }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.error || `HTTP ${res.status}`);
            }
            const created = await res.json();
            categoryId = created.id;
        }

        // If user picked the upload tab, send the image now (works for both
        // create and edit; for create we do it after the row exists).
        if (activeTab === 'upload') {
            const fileInput = document.getElementById('newCategoryImage');
            const file = fileInput?.files?.[0];
            if (file) {
                const fd = new FormData();
                fd.append('file', file);
                const upRes = await fetch(`${API_BASE}/categories/${categoryId}/image`, {
                    method: 'POST',
                    body: fd,
                });
                if (!upRes.ok) {
                    const err = await upRes.json().catch(() => ({}));
                    throw new Error(err.error || `image upload HTTP ${upRes.status}`);
                }
            }
        }

        showToast(editingId ? 'Category updated' : 'Category created', 'success');
        await loadCategories();
        renderCategoriesList();
        renderCategoryParentSelect();
        _resetCategoryForm();
        // Refresh whatever library view is currently open.
        renderLibrary();
    } catch (err) {
        console.error('[categories] save failed', err);
        showToast(`Failed: ${err.message}`, 'error');
    }
}

async function handleDeleteCategory(cat) {
    const confirmed = window.confirm(
        `Delete "${cat.name}"? Books in it will become uncategorised; sub-categories will also be deleted.`
    );
    if (!confirmed) return;
    try {
        const res = await fetch(`${API_BASE}/categories/${cat.id}`, { method: 'DELETE' });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${res.status}`);
        }
        showToast(`Deleted ${cat.name}`, 'success');
        await loadBooks();   // category_id on books may have been nulled by FK
        await loadCategories();
        renderCategoriesList();
        renderCategoryParentSelect();
        renderLibrary();
    } catch (err) {
        console.error('[categories] delete failed', err);
        showToast(`Failed: ${err.message}`, 'error');
    }
}

// ── Assign book → category modal ───────────────────────────────────────────

function showAssignCategoryModal(bookId) {
    const book = books.find(b => b.id === bookId);
    if (!book) return;
    const modal = document.getElementById('assignCategoryModal');
    const titleEl = document.getElementById('assignCategoryBookTitle');
    const list = document.getElementById('assignCategoryList');
    if (!modal || !list) return;

    if (titleEl) titleEl.textContent = book.title;

    Promise.all([loadCategoryPresets(), loadCategories()]).then(() => {
        list.innerHTML = '';
        // "Uncategorised" option first.
        list.appendChild(_buildAssignRow(book, null, 'Uncategorised', '🗂️'));
        const top = categoriesState.list.filter(c => !c.parent_id);
        for (const cat of top) {
            list.appendChild(_buildAssignRow(book, cat.id, cat.name, _categoryRowIcon(cat)));
            const subs = categoriesState.list.filter(c => c.parent_id === cat.id);
            for (const sub of subs) {
                list.appendChild(_buildAssignRow(book, sub.id, '↳ ' + sub.name, _categoryRowIcon(sub)));
            }
        }
        modal.classList.add('visible');
    });
}

function _buildAssignRow(book, categoryId, label, icon) {
    const li = document.createElement('li');
    li.className = 'category-row is-selectable';
    if (book.category_id === categoryId || (book.category_id == null && categoryId == null)) {
        li.style.borderColor = 'var(--br-accent)';
    }
    li.innerHTML = `
        <div class="cat-row-icon">${icon || '📚'}</div>
        <div class="cat-row-name">${escapeHtml(label)}</div>
    `;
    li.addEventListener('click', () => assignBookToCategory(book.id, categoryId));
    return li;
}

function hideAssignCategoryModal() {
    const modal = document.getElementById('assignCategoryModal');
    if (modal) modal.classList.remove('visible');
}

async function assignBookToCategory(bookId, categoryId) {
    try {
        const res = await fetch(`${API_BASE}/books/${bookId}/category`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ category_id: categoryId }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${res.status}`);
        }
        const book = books.find(b => b.id === bookId);
        if (book) book.category_id = categoryId;
        showToast('Moved', 'success');
        hideAssignCategoryModal();
        await loadCategories();   // recompute book_count
        renderLibrary();
    } catch (err) {
        console.error('[categories] assign failed', err);
        showToast(`Failed: ${err.message}`, 'error');
    }
}

// ── Refresh / regenerate audio ─────────────────────────────────────────────

async function regenerateAudioForCurrentBook() {
    if (!currentBook) return;
    const confirmed = window.confirm(
        `Regenerate audio for "${currentBook.title}"? All cached MP3s for this book will be deleted and re-synthesised in the background.`
    );
    if (!confirmed) return;

    try {
        const body = selectedVoice ? { voice_id: selectedVoice } : {};
        const res = await fetch(`${API_BASE}/books/${currentBook.id}/regenerate-audio`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${res.status}`);
        }
        const data = await res.json();
        showToast(
            `Regenerating: removed ${data.files_removed} files, queued ${data.pages_queued} pages`,
            'success'
        );
        // Drop the in-memory audio so the next play re-fetches from the server.
        if (currentAudio) {
            currentAudio.pause();
            currentAudio = null;
        }
        isPlaying = false;
        if (playPauseBtn) playPauseBtn.textContent = 'Play';
        // Restart audio progress polling for this book.
        if (typeof startReaderAudioProgress === 'function') {
            startReaderAudioProgress(currentBook.id);
        }
        // Trigger a fresh load on the current page; server will return 202 until ready.
        if (typeof loadPageAudio === 'function') {
            loadPageAudio(currentPage);
        }
    } catch (err) {
        console.error('[regenerate] failed', err);
        showToast(`Failed: ${err.message}`, 'error');
    }
}

// ============ START ============
document.addEventListener('DOMContentLoaded', init);
