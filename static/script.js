function addToCart(bookId) {
    fetch(`/cart/add/${bookId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        if (data.cart_count !== undefined) {
            const badge = document.getElementById('cart-badge');
            if (badge) badge.innerText = data.cart_count;
        }
    })
    .catch(err => console.error('Error adding to cart:', err));
}

function toggleWishlist(bookId, btn) {
    fetch(`/wishlist/toggle/${bookId}`, { method: 'POST' })
    .then(res => res.json())
    .then(data => {
        if (data.added) {
            btn.classList.add('active');
            btn.innerText = '♥';
        } else {
            btn.classList.remove('active');
            btn.innerText = '♡';
        }
    })
    .catch(err => console.error('Error toggling wishlist:', err));
}