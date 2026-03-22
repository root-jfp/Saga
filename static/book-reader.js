// ============================================================================
// Book Reader - PDF.js Frontend Logic
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
let audioTiming = [];  // Real timing data from TTS: [{text, offset, duration}, ...]

// Voice state
let availableVoices = [];
let selectedVoice = null;


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

        // Load books and voices in parallel for faster loading
        const [booksResult, voicesResult] = await Promise.all([
            loadBooks(),
            loadVoices()
        ]);

        setupDropdowns();
        renderLibraryWithAnimation();
        updateFontSizeDisplay();
        setViewMode(currentViewMode);  // Initialize view mode
        hideLoading();
    } catch (error) {
        console.error('Book Reader init error:', error);
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

        await loadBooks();
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
        await loadBooks();
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
    const noBooks = document.getElementById('noBooks');

    if (!booksGrid) {
        console.warn('booksGrid element not found');
        return;
    }

    // Always clear grid first to prevent stale content
    booksGrid.innerHTML = '';

    if (books.length === 0) {
        if (noBooks) noBooks.style.display = 'block';
        return;
    }

    if (noBooks) noBooks.style.display = 'none';

    booksGrid.innerHTML = books.map(book => `
        <div class="book-card" data-book-id="${book.id}">
            <div class="book-cover" onclick="openBook(${book.id})">
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
            <button class="book-delete-btn" onclick="event.stopPropagation(); deleteBook(${book.id})" title="Delete">X</button>
        </div>
    `).join('');

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

        // Load book content
        await loadBook(bookId);
        await loadBookmarks();

        switchView('reader');
        updateReaderHeader();

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

        // Update page number in book view footer
        const pageNumberDisplay = document.getElementById('pageNumberDisplay');
        if (pageNumberDisplay) {
            pageNumberDisplay.textContent = `Page ${pageNumber}`;
        }

        // Render sentences in appropriate view
        if (currentViewMode === 'book') {
            renderBookSentences();
        } else if (currentViewMode === 'text') {
            renderSentences();
        }
    } catch (error) {
        console.error('Failed to load page sentences:', error);
        pageSentences = [];
        audioTiming = [];
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
async function loadVoices() {
    try {
        const response = await fetch(`${API_BASE}/tts/voices`);
        availableVoices = await response.json();

        const voiceMenu = document.getElementById('voiceMenu');
        if (!voiceMenu) {
            console.warn('voiceMenu element not found');
            return;
        }
        voiceMenu.innerHTML = '';

        if (availableVoices.length === 0) {
            voiceMenu.innerHTML = '<div class="dropdown-item">No voices available</div>';
            return;
        }

        // Load saved voice preference
        const savedVoice = localStorage.getItem('selectedVoice');

        availableVoices.forEach(voice => {
            const isFemale = voice.gender === 'Female';
            const genderClass = isFemale ? 'female' : 'male';
            const genderIcon = isFemale ? 'F' : 'M';
            const isSelected = savedVoice === voice.id || (!savedVoice && availableVoices[0].id === voice.id);

            const item = document.createElement('div');
            item.className = `dropdown-item voice-item${isSelected ? ' selected' : ''}`;
            item.dataset.id = voice.id;
            item.dataset.gender = voice.gender;
            item.innerHTML = `
                <div class="item-avatar ${genderClass}">${genderIcon}</div>
                <div class="item-info">
                    <div class="item-name">${voice.name}</div>
                    <div class="item-locale">${voice.locale} - ${voice.quality}</div>
                </div>
            `;
            item.addEventListener('click', () => selectVoice(voice));
            voiceMenu.appendChild(item);

            if (isSelected) {
                selectedVoice = voice.id;
                updateVoiceDisplay(voice);
            }
        });

        if (!selectedVoice && availableVoices.length > 0) {
            selectedVoice = availableVoices[0].id;
            updateVoiceDisplay(availableVoices[0]);
        }

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
    localStorage.setItem('selectedVoice', voice.id);
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
function renderBookSentences() {
    if (!bookPageContent) return;

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

    bookPageContent.innerHTML = html;

    // Apply current font size
    updateFontSizeDisplay();
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

// ============ START ============
document.addEventListener('DOMContentLoaded', init);
