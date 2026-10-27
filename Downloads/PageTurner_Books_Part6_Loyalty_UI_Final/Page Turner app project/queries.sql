-- 1. Books cheaper than ₹500 (prices are stored in paise)
SELECT * FROM books WHERE price < 50000 ORDER BY price;

-- 2. Technology books, highest price first
SELECT * FROM books WHERE category = 'Technology' ORDER BY price DESC;

-- 3. Number of books in each category
SELECT category, COUNT(*) AS book_count FROM books GROUP BY category ORDER BY category;

-- 4. Most expensive book
SELECT * FROM books WHERE price = (SELECT MAX(price) FROM books);

-- 5. Orders with customer name and total
SELECT id, customer_name, total_amount, status, created_at FROM orders ORDER BY created_at DESC;

-- 6. Total revenue by order status
SELECT status, SUM(total_amount) AS revenue FROM orders GROUP BY status;

-- 7. Best-selling books
SELECT b.id, b.title, SUM(oi.quantity) AS copies_sold FROM order_items oi JOIN books b ON b.id = oi.book_id GROUP BY b.id, b.title ORDER BY copies_sold DESC LIMIT 5;

-- 8. Average review rating by book (legacy review table uses book_title)
SELECT book_title, AVG(rating) AS average_rating, COUNT(*) AS review_count FROM reviews GROUP BY book_title;

-- 9. Low-stock books
SELECT id, title, stock FROM books WHERE stock < 5 ORDER BY stock;

-- 10. Wishlist items by customer
SELECT u.name, b.title FROM wishlist w JOIN users u ON u.id = w.user_id JOIN books b ON b.id = w.book_id ORDER BY u.name;

-- 11. Coupon use history
SELECT coupon_id, user_id, COUNT(*) AS uses FROM coupon_uses GROUP BY coupon_id, user_id;

-- 12. Order status history timeline
SELECT order_id, status, changed_by, changed_at, note FROM order_status_history ORDER BY order_id, changed_at;
