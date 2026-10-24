import sqlite3

conn = sqlite3.connect('books.db')
cursor = conn.cursor()

cursor.executescript("""
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS wishlist;
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS books;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS coupons;

CREATE TABLE books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    category TEXT NOT NULL,
    price REAL NOT NULL,
    stock INTEGER NOT NULL DEFAULT 15,
    rating REAL DEFAULT 5.0,
    image_url TEXT
);

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    customer_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    address TEXT NOT NULL,
    payment_method TEXT DEFAULT 'Cash on Delivery',
    total_amount REAL NOT NULL,
    discount_amount REAL DEFAULT 0.0,
    status TEXT DEFAULT 'Placed',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(id),
    FOREIGN KEY(book_id) REFERENCES books(id)
);

CREATE TABLE reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    rating INTEGER NOT NULL,
    comment TEXT NOT NULL,
    FOREIGN KEY(book_id) REFERENCES books(id)
);

CREATE TABLE wishlist (
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, book_id)
);

CREATE TABLE coupons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    percent REAL NOT NULL,
    expiry_date DATE NOT NULL,
    active INTEGER DEFAULT 1
);

CREATE INDEX idx_books_category ON books(category);
CREATE INDEX idx_orders_user_id ON orders(user_id);

INSERT OR IGNORE INTO coupons (code, percent, expiry_date, active)
VALUES ('SAVE10', 10.0, '2026-12-31', 1);
""")

# Sample catalog of 12 books
sample_books = [
    ("Python Crash Course", "Eric Matthes", "Technology", 29.99, 15, 4.8, "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=400&q=80"),
    ("Clean Code", "Robert C. Martin", "Technology", 38.50, 10, 4.9, "https://images.unsplash.com/photo-1532012197267-da84d127e765?auto=format&fit=crop&w=400&q=80"),
    ("Designing Data-Intensive Applications", "Martin Kleppmann", "Technology", 45.00, 12, 5.0, "https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=400&q=80"),
    ("The Pragmatic Programmer", "Andrew Hunt", "Technology", 42.00, 8, 4.7, "https://images.unsplash.com/photo-1555066931-4365d14bab8c?auto=format&fit=crop&w=400&q=80"),
    ("Atomic Habits", "James Clear", "Self-Help", 18.00, 20, 4.9, "https://images.unsplash.com/photo-1544716278-ca5e3f4abd8c?auto=format&fit=crop&w=400&q=80"),
    ("Deep Work", "Cal Newport", "Self-Help", 16.50, 10, 4.6, "https://images.unsplash.com/photo-1506880018603-83d5b814b5a6?auto=format&fit=crop&w=400&q=80"),
    ("Dune", "Frank Herbert", "Sci-Fi", 22.00, 14, 4.8, "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=400&q=80"),
    ("Project Hail Mary", "Andy Weir", "Sci-Fi", 24.50, 6, 4.9, "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=400&q=80"),
    ("To Kill a Mockingbird", "Harper Lee", "Fiction", 14.99, 18, 4.8, "https://images.unsplash.com/photo-1543002588-bfa74002ed7e?auto=format&fit=crop&w=400&q=80"),
    ("1984", "George Orwell", "Fiction", 12.99, 25, 4.7, "https://images.unsplash.com/photo-1497633762265-9d179a990aa6?auto=format&fit=crop&w=400&q=80"),
    ("Sapiens", "Yuval Noah Harari", "History", 21.00, 11, 4.8, "https://images.unsplash.com/photo-1461360370896-922624d12aa1?auto=format&fit=crop&w=400&q=80"),
    ("The Psychology of Money", "Morgan Housel", "Finance", 19.99, 16, 4.9, "https://images.unsplash.com/photo-1559526324-4b87b5e36e44?auto=format&fit=crop&w=400&q=80")
]

for b in sample_books:
    cursor.execute("""
        INSERT INTO books (title, author, category, price, stock, rating, image_url)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, b)

# Seed sample customer reviews
sample_reviews = [
    (1, "Aarav Sharma", 5, "Fantastic intro to Python programming! Everything is clearly explained."),
    (2, "Priya Nair", 5, "Every developer needs to read Clean Code. A timeless classic."),
    (5, "Rahul Verma", 4, "Atomic Habits completely transformed my daily routine.")
]

for r in sample_reviews:
    cursor.execute("INSERT INTO reviews (book_id, name, rating, comment) VALUES (?, ?, ?, ?)", r)

conn.commit()
conn.close()
print("Database schema built successfully with 12 catalog books!")