SELECT * FROM books WHERE (title LIKE ? OR author LIKE ?) AND category = ? LIMIT ? OFFSET ?;

SELECT * FROM books WHERE id = ?;

UPDATE books SET stock = stock - ? WHERE id = ? AND stock >= ?;

INSERT INTO orders (user_id, total_amount, discount_amount, status) VALUES (?, ?, ?, ?);

INSERT INTO order_items (order_id, book_id, quantity, price) VALUES (?, ?, ?, ?);

INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?);

SELECT * FROM users WHERE email = ?;

INSERT OR IGNORE INTO wishlist (user_id, book_id) VALUES (?, ?);

SELECT b.* FROM books b JOIN wishlist w ON b.id = w.book_id WHERE w.user_id = ?;

SELECT * FROM coupons WHERE code = ? AND active = 1 AND expiry_date >= DATE('now');

UPDATE orders SET status = ? WHERE id = ?;

EXPLAIN QUERY PLAN SELECT * FROM books WHERE category = 'Technology';