/* PageTurner frontend helpers. All state-changing requests include the CSRF token. */
function csrfToken() {
    const field = document.querySelector('input[name="csrf_token"]');
    return field ? field.value : '';
}

async function addToCart(bookId) {
    try {
        const body = new URLSearchParams({ csrf_token: csrfToken() });
        const response = await fetch(`/api/cart/add/${bookId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not add this book.');
        const badge = document.getElementById('cart-badge');
        if (badge) badge.textContent = data.cart_count;
        showNotice(data.message || 'Added to cart.');
    } catch (error) {
        showNotice(error.message, true);
    }
}

function toggleWishlist(bookId, btn) {
    // The normal server-rendered wishlist link remains available as a fallback.
    window.location.href = `/wishlist/toggle/${bookId}`;
}

function showNotice(message, isError = false) {
    let notice = document.getElementById('page-turner-notice');
    if (!notice) {
        notice = document.createElement('div');
        notice.id = 'page-turner-notice';
        notice.setAttribute('role', 'status');
        notice.style.cssText = 'position:fixed;right:20px;top:20px;z-index:9999;padding:12px 18px;border-radius:8px;color:white;box-shadow:0 4px 14px #0002;';
        document.body.appendChild(notice);
    }
    notice.style.background = isError ? '#b91c1c' : '#047857';
    notice.textContent = message;
    window.clearTimeout(notice._hideTimer);
    notice._hideTimer = window.setTimeout(() => notice.remove(), 3000);
}

// Live suggestions use the read-only JSON endpoint and never build SQL in the browser.
document.addEventListener('DOMContentLoaded', () => {
    const search = document.querySelector('input[name="q"]');
    if (!search) return;
    const suggestions = document.createElement('div');
    suggestions.id = 'book-search-suggestions';
    suggestions.style.cssText = 'position:absolute;z-index:1000;background:white;border:1px solid #e2e8f0;border-radius:8px;box-shadow:0 8px 20px #0001;max-width:420px;width:calc(100% - 20px);display:none;';
    search.parentElement.style.position = 'relative';
    search.parentElement.appendChild(suggestions);
    let requestId = 0;
    search.addEventListener('input', async () => {
        const term = search.value.trim();
        const current = ++requestId;
        if (term.length < 2) { suggestions.style.display = 'none'; suggestions.replaceChildren(); return; }
        try {
            const response = await fetch(`/api/books?q=${encodeURIComponent(term)}`);
            const books = await response.json();
            if (current !== requestId) return;
            suggestions.replaceChildren();
            books.slice(0, 5).forEach(book => {
                const link = document.createElement('a');
                link.href = `/book/${book.id}`;
                link.textContent = `${book.title} — ${book.author}`;
                link.style.cssText = 'display:block;padding:10px 12px;color:#1e293b;text-decoration:none;border-bottom:1px solid #f1f5f9;';
                suggestions.appendChild(link);
            });
            suggestions.style.display = books.length ? 'block' : 'none';
        } catch (_) { suggestions.style.display = 'none'; }
    });
    document.addEventListener('click', event => {
        if (!suggestions.contains(event.target) && event.target !== search) suggestions.style.display = 'none';
    });
});
