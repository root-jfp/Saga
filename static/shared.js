// Shared utilities for Book Reader microservice

// API base URL (same origin)
const API_BASE = '/api';

// Current user state
let currentUser = null;
let allUsers = [];

// ── XSS Protection ────────────────────────────────────────────────────────────

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ── Theme ─────────────────────────────────────────────────────────────────────

function loadTheme() {
    const theme = localStorage.getItem('theme') || 'dark';
    document.body.classList.remove('light-theme', 'dark-theme');
    document.body.classList.add(theme === 'light' ? 'light-theme' : 'dark-theme');
}

function toggleTheme() {
    const isLight = document.body.classList.contains('light-theme');
    document.body.classList.remove('light-theme', 'dark-theme');
    document.body.classList.add(isLight ? 'dark-theme' : 'light-theme');
    localStorage.setItem('theme', isLight ? 'dark' : 'light');
}

// ── Toast Notifications ───────────────────────────────────────────────────────

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('toast-fade');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ── User Helpers ──────────────────────────────────────────────────────────────

function saveCurrentUser(userId) {
    localStorage.setItem('currentUserId', userId);
}

async function loadUsers() {
    try {
        const response = await fetch(`${API_BASE}/users`);
        allUsers = await response.json();

        const savedId = localStorage.getItem('currentUserId');
        currentUser = allUsers.find(u => u.id === parseInt(savedId)) || allUsers[0];

        if (currentUser) {
            saveCurrentUser(currentUser.id);
        }

        document.dispatchEvent(new CustomEvent('usersLoaded', {
            detail: { users: allUsers, currentUser }
        }));

        return allUsers;
    } catch (err) {
        console.error('[shared] Failed to load users:', err);
        return [];
    }
}
