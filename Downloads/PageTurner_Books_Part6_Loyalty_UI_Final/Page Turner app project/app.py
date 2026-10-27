import sqlite3
import os
import logging
import secrets
import csv
import io
import re
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, jsonify, flash, abort, make_response
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
from advanced_features import bp as advanced_bp, init_advanced_db
app.register_blueprint(advanced_bp)
app.secret_key = secrets.token_hex(32) if not __import__('os').environ.get('PAGETURNER_SECRET_KEY') else __import__('os').environ['PAGETURNER_SECRET_KEY']
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')

ALLOWED_STATUS_TRANSITIONS = {
    'Placed': ['Packed', 'Processing', 'Shipped', 'Delivered', 'Cancelled'],
    'Processing': ['Shipped', 'Delivered', 'Cancelled'],
    'Packed': ['Shipped', 'Delivered', 'Cancelled'],
    'Shipped': ['Delivered', 'Cancelled'],
    'Delivered': ['Refund Requested', 'Cancelled', 'Returned'],
    'Refund Requested': ['Refunded', 'Cancelled'],
    'Refunded': [],
    'Cancelled': [],
    'Returned': []
}

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

@app.template_filter('inr')
def format_inr(paise):
    try:
        val = int(paise or 0)
        return f"Rs {val / 100:,.2f}"
    except (ValueError, TypeError):
        return "Rs 0.00"

@app.context_processor
def inject_template_globals():
    return {'current_year': datetime.now().year}

FREE_SHIPPING_LIMIT_PAISE = 50000

@app.before_request
def ensure_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)

def csrf_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method == 'POST':
            token_in_form = request.form.get('csrf_token')
            token_in_session = session.get('csrf_token')
            if not token_in_form or not token_in_session or token_in_form != token_in_session:
                return "400 Bad Request: Invalid or missing CSRF token.", 400
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('user_id') and not session.get('admin_logged_in'):
                flash("Please log in to access this page.", "warning")
                return redirect(url_for('admin_login'))
            
            user_role = session.get('user_role', 'customer')
            if session.get('admin_logged_in'):
                user_role = session.get('staff_role', 'admin')

            if user_role not in allowed_roles:
                return """
                <!DOCTYPE html>
                <html>
                <head><title>403 Forbidden - PageTurner</title></head>
                <body style="font-family:sans-serif; background:#f8fafc; display:flex; align-items:center; justify-content:center; height:100vh; margin:0;">
                    <div style="background:white; padding:40px; border-radius:12px; text-align:center; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
                        <h1 style="color:#ef4444; margin-top:0;">403</h1>
                        <h3>Access Forbidden</h3>
                        <p style="color:#64748b;">You do not have permission to access this resource.</p>
                        <a href="/" style="display:inline-block; margin-top:15px; background:#2563eb; color:white; padding:10px 20px; text-decoration:none; border-radius:6px; font-weight:bold;">Return to Storefront</a>
                    </div>
                </body>
                </html>
                """, 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            name TEXT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'customer'
        )
    ''')

    # Loyalty points: one point for every completed ₹100 of order value.
    user_columns_for_loyalty = {row[1] for row in cursor.execute("PRAGMA table_info(users)").fetchall()}
    if 'loyalty_points' not in user_columns_for_loyalty:
        cursor.execute("ALTER TABLE users ADD COLUMN loyalty_points INTEGER NOT NULL DEFAULT 0")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            price INTEGER NOT NULL CHECK(price >= 0),
            rating REAL DEFAULT 4.5 CHECK(rating >= 0 AND rating <= 5),
            stock INTEGER DEFAULT 10 CHECK(stock >= 0),
            category TEXT NOT NULL,
            image_url TEXT
        )
    ''')
    # Safe, repeatable migration for release scheduling and flash-sale fields.
    # Existing rows are preserved; omitted release dates mean ordinary available books.
    book_columns = {row[1] for row in cursor.execute("PRAGMA table_info(books)").fetchall()}
    if 'release_date' not in book_columns:
        cursor.execute("ALTER TABLE books ADD COLUMN release_date TEXT")
    if 'sale_price_paise' not in book_columns:
        cursor.execute("ALTER TABLE books ADD COLUMN sale_price_paise INTEGER")
    if 'sale_start' not in book_columns:
        cursor.execute("ALTER TABLE books ADD COLUMN sale_start TEXT")
    if 'sale_end' not in book_columns:
        cursor.execute("ALTER TABLE books ADD COLUMN sale_end TEXT")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            book_title TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
            comment TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            discount_type TEXT NOT NULL, 
            discount_value INTEGER NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS wishlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (book_id) REFERENCES books (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            customer_name TEXT,
            customer_email TEXT,
            customer_phone TEXT,
            customer_address TEXT,
            total_amount INTEGER NOT NULL CHECK(total_amount >= 0),
            discount_amount INTEGER DEFAULT 0,
            status TEXT DEFAULT 'Placed',
            payment_method TEXT DEFAULT 'Cash on Delivery',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS addresses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            line1 TEXT NOT NULL,
            city TEXT NOT NULL,
            pincode TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('SELECT COUNT(*) FROM books')
    if cursor.fetchone()[0] == 0:
        sample_books = [
            ("The Great Gatsby", "F. Scott Fitzgerald", 49900, 4.8, 15, "Fiction", "https://images.unsplash.com/photo-1543002588-bfa74002ed7e?w=400"),
            ("To Kill a Mockingbird", "Harper Lee", 55000, 4.9, 12, "Fiction", "https://images.unsplash.com/photo-1512820790803-83ca734da794?w=400"),
            ("1984", "George Orwell", 39900, 4.7, 8, "Sci-Fi", "https://images.unsplash.com/photo-1532012197267-da84d127e765?w=400"),
            ("Atomic Habits", "James Clear", 65000, 5.0, 20, "Self-Help", "https://images.unsplash.com/photo-1589829085413-56de8ae18c73?w=400"),
            ("The Hobbit", "J.R.R. Tolkien", 59900, 4.8, 10, "Fantasy", "https://images.unsplash.com/photo-1629992101753-56d196c8aced?w=400"),
            ("Sapiens", "Yuval Noah Harari", 75000, 4.6, 14, "History", "https://images.unsplash.com/photo-1497633762265-9d179a990aa6?w=400"),
            ("Clean Code", "Robert C. Martin", 125000, 4.9, 6, "Technology", "https://images.unsplash.com/photo-1524995997946-a1c2e315a42f?w=400"),
            ("Deep Work", "Cal Newport", 49900, 4.7, 11, "Self-Help", "https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=400")
        ]
        cursor.executemany('INSERT INTO books (title, author, price, rating, stock, category, image_url) VALUES (?, ?, ?, ?, ?, ?, ?)', sample_books)

    cursor.execute('SELECT COUNT(*) FROM coupons')
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO coupons (code, discount_type, discount_value, is_active) VALUES ('SAVE10', 'percentage', 10, 1)")
        cursor.execute("INSERT INTO coupons (code, discount_type, discount_value, is_active) VALUES ('FLAT100', 'fixed', 10000, 1)")

    # Keep line items separately so one order can contain multiple books.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            price_at_purchase INTEGER NOT NULL CHECK(price_at_purchase >= 0),
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(book_id) REFERENCES books(id)
        )
    ''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS order_status_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, status TEXT NOT NULL,
        changed_by INTEGER, changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, note TEXT,
        FOREIGN KEY(order_id) REFERENCES orders(id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS stock_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, book_id INTEGER NOT NULL, change INTEGER NOT NULL,
        reason TEXT NOT NULL, user_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(book_id) REFERENCES books(id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS coupon_uses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coupon_id INTEGER NOT NULL, user_id INTEGER, order_id INTEGER NOT NULL,
        UNIQUE(coupon_id, user_id, order_id))''')
    # Safe, repeatable migrations for existing project databases.
    order_columns = {row[1] for row in cursor.execute("PRAGMA table_info(orders)").fetchall()}
    if 'payment_method' not in order_columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT DEFAULT 'Cash on Delivery'")
    if 'discount_amount' not in order_columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN discount_amount INTEGER DEFAULT 0")
    if 'gift_wrap' not in order_columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN gift_wrap INTEGER NOT NULL DEFAULT 0")
    if 'gift_message' not in order_columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN gift_message TEXT NOT NULL DEFAULT ''")
    cursor.execute('''CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, order_id INTEGER,
        title TEXT NOT NULL, message TEXT NOT NULL, is_read INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(order_id) REFERENCES orders(id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, token TEXT UNIQUE NOT NULL,
        expires_at TEXT NOT NULL, used INTEGER DEFAULT 0,
        FOREIGN KEY(user_id) REFERENCES users(id))''')
    user_columns = {row[1] for row in cursor.execute("PRAGMA table_info(users)").fetchall()}
    if 'failed_login_attempts' not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0")
    if 'locked_until' not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN locked_until TEXT")
    if 'is_locked' not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN is_locked INTEGER NOT NULL DEFAULT 0")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_category ON books(category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)")
    conn.commit()
    conn.close()
    init_advanced_db()
    # Request aggregation/status fields support the customer request form and admin approvals.
    c2 = get_db_connection()
    c2.execute("CREATE TABLE IF NOT EXISTS book_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL COLLATE NOCASE, author TEXT NOT NULL DEFAULT '', request_count INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'requested', requested_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, reviewed_at TEXT)")
    c2.execute("CREATE INDEX IF NOT EXISTS idx_book_requests_status_count ON book_requests(status, request_count DESC)")
    c2.commit(); c2.close()

@app.route('/')
def home():
    q = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip()
    sort = request.args.get('sort', '').strip()

    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM books WHERE 1=1"
    params = []

    if q:
        query += " AND (title LIKE ? OR author LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    if category:
        query += " AND category = ?"
        params.append(category)

    if sort == 'price_asc': query += " ORDER BY price ASC"
    elif sort == 'price_desc': query += " ORDER BY price DESC"
    elif sort == 'rating': query += " ORDER BY rating DESC"
    else: query += " ORDER BY id DESC"

    # Keep search, category and sorting active while moving between pages.
    page = max(1, request.args.get('page', 1, type=int) or 1)
    count_query = query.replace("SELECT *", "SELECT COUNT(*)", 1).split(" ORDER BY ")[0]
    cursor.execute(count_query, params)
    total_books = cursor.fetchone()[0]
    page_size = 8
    total_pages = max(1, (total_books + page_size - 1) // page_size)
    page = min(page, total_pages)
    query += " LIMIT ? OFFSET ?"
    cursor.execute(query, params + [page_size, (page - 1) * page_size])
    books = cursor.fetchall()

    cursor.execute("SELECT * FROM reviews WHERE COALESCE(hidden,0)=0 ORDER BY id DESC")
    recent_reviews = cursor.fetchall()
    cursor.execute("SELECT DISTINCT category FROM books WHERE TRIM(COALESCE(category,'')) <> '' ORDER BY category COLLATE NOCASE")
    categories = [row['category'] for row in cursor.fetchall()]

    # Load active bundles and their included books for the customer storefront.
    # Bundle prices are stored in integer paise; book prices use the existing store convention.
    bundles = []
    try:
        cursor.execute("SELECT id, name, price_paise FROM book_bundles WHERE active = 1 ORDER BY id DESC")
        for bundle_row in cursor.fetchall():
            bundle = dict(bundle_row)
            cursor.execute("SELECT b.id, b.title, b.author FROM bundle_items bi JOIN books b ON b.id = bi.book_id WHERE bi.bundle_id = ? ORDER BY b.title COLLATE NOCASE", (bundle['id'],))
            bundle['books'] = [dict(row) for row in cursor.fetchall()]
            if bundle['books']:
                bundles.append(bundle)
    except sqlite3.OperationalError:
        # Keep the storefront usable before advanced-feature migrations have run.
        bundles = []

    # Live flash sale: only books with a valid current sale window appear.
    try:
        now_text = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        flash_sale_books = cursor.execute("SELECT id,title,author,price,sale_price_paise,image_url,sale_start,sale_end FROM books WHERE sale_price_paise IS NOT NULL AND sale_price_paise >= 0 AND sale_price_paise < price AND (sale_start IS NULL OR sale_start <= ?) AND (sale_end IS NULL OR sale_end >= ?) ORDER BY sale_price_paise ASC LIMIT 8", (now_text, now_text)).fetchall()
    except sqlite3.OperationalError:
        flash_sale_books = []

    # Moderation policy: hidden reviews are excluded from public display and any rating averages.
    # Resolve the session's recently viewed IDs in the same order, skipping deleted books.
    recent_ids = []
    for value in session.get('recent_books', []):
        try:
            book_id = int(value)
        except (TypeError, ValueError):
            continue
        if book_id not in recent_ids:
            recent_ids.append(book_id)
        if len(recent_ids) == 5:
            break
    recent_books = []
    for recent_id in recent_ids:
        cursor.execute("SELECT id, title, author, price, image_url, rating FROM books WHERE id = ?", (recent_id,))
        recent_book = cursor.fetchone()
        if recent_book:
            recent_books.append(recent_book)
    # Clean stale IDs from the session so deleted books don't linger.
    session['recent_books'] = [b['id'] for b in recent_books]
    loyalty_points = 0
    if session.get('user_id'):
        try:
            points_conn = get_db_connection()
            points_row = points_conn.execute("SELECT loyalty_points FROM users WHERE id = ?", (session['user_id'],)).fetchone()
            loyalty_points = int(points_row['loyalty_points'] or 0) if points_row else 0
            points_conn.close()
        except sqlite3.Error:
            loyalty_points = 0
    conn.close()

    wishlist_ids = session.get('wishlist', [])

    book_cards = ""
    for b in books:
        price_display = format_inr(b['price'])
        is_wishlisted = b['id'] in wishlist_ids
        heart_color = "#ef4444" if is_wishlisted else "#cbd5e1"
        heart_icon_class = "fa-solid" if is_wishlisted else "fa-regular"

        release_date = b['release_date'] if 'release_date' in b.keys() else None
        today_iso = datetime.utcnow().date().isoformat()
        coming_soon = bool(release_date and release_date > today_iso)
        is_new = bool(release_date and not coming_soon and release_date >= (datetime.utcnow().date() - timedelta(days=14)).isoformat())
        ribbon = '<span style="position:absolute;top:12px;left:12px;background:#7c3aed;color:white;font-size:10px;padding:3px 8px;border-radius:4px;font-weight:700">COMING SOON</span>' if coming_soon else ('<span style="position:absolute;top:12px;left:12px;background:#059669;color:white;font-size:10px;padding:3px 8px;border-radius:4px;font-weight:700">NEW</span>' if is_new else '<span style="position:absolute;top:12px;left:12px;background:#ff5e62;color:white;font-size:10px;padding:3px 8px;border-radius:4px;font-weight:700">POPULAR</span>')
        cart_button = '<span style="display:block;padding:6px;text-align:center;border-radius:6px;background:#e2e8f0;color:#475569">Coming soon</span>' if coming_soon else f'<form method="POST" action="/cart/add/{b["id"]}" style="margin:0;"><input type="hidden" name="csrf_token" value="{session.get("csrf_token", "")}"><button type="submit" style="width:100%;padding:6px;border-radius:6px;font-size:15px;font-weight:600;border:1px solid #fef08a;background:#fef08a;color:#000;cursor:pointer;">Add Cart</button></form>'
        book_cards += f"""
        <div style="border: 1px solid #f1f5f9; border-radius: 12px; padding: 16px; background: #fff; position: relative;">
            <a href="/wishlist/toggle/{b['id']}" title="Toggle Wishlist" style="position: absolute; top: 12px; right: 12px; background: white; border-radius: 50%; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 6px rgba(0,0,0,0.1); text-decoration: none; color: {heart_color}; font-size: 16px;">
                <i class="{heart_icon_class} fa-heart"></i>
            </a>
            {ribbon}
            <img src="{b['image_url'] or ''}" alt="{b['title']}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';" style="width: 100%; height: 160px; aspect-ratio: 4 / 3; object-fit: cover; margin-bottom: 12px; border-radius: 10px; background:#e5e7eb;"><div style="display:{'none' if b['image_url'] else 'flex'};height:160px;align-items:center;justify-content:center;text-align:center;padding:12px;background:#e5e7eb;color:#555;border-radius:10px;margin-bottom:12px;font-weight:700;">{b['title']}</div>
            <div style="font-size: 14px; font-weight: 700; margin: 4px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{b['title']}</div>
            <div style="font-size: 15px; color: #64748b; margin-bottom: 6px;">{b['author']}</div>
            <div style="color: #f59e0b; font-size: 14px; margin-bottom: 8px;">★ ★ ★ ★ ★ ({b['rating']})</div>
            <div style="font-size: 16px; font-weight: 800; color: #ff5e62; margin-bottom: 12px;">₹{price_display}</div>
            {('<p class="book-description">' + str(b['description'] or '')[:100] + ('...' if len(str(b['description'] or '')) > 100 else '') + '</p>') if ('description' in b.keys() and b['description']) else ''}
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
                <a href="/book/{b['id']}" style="padding: 6px; border-radius: 6px; font-size: 15px; font-weight: 600; text-align: center; text-decoration: none; border: 1px solid #cbd5e1; background: #fff; color: #1e293b;">View</a>
                {cart_button}
            </div>
        </div>
        """

    review_cards = ""
    for r in recent_reviews:
        review_cards += f"""
        <div style="background: #fff; border: 1px solid #f1f5f9; border-radius: 8px; padding: 14px; margin-bottom: 12px;">
            <div style="display: flex; justify-content: space-between; font-weight: bold; font-size: 15px;">
                <span>{r['name']} <small style="color: #64748b;">on {r['book_title']}</small></span>
                <span style="color: #f59e0b;">{r['rating']} ⭐</span>
            </div>
            <p style="margin: 6px 0 0 0; font-size: 15px; color: #475569;">"{r['comment']}"</p>
            <form method="POST" action="/review/report/{r['id']}" style="margin-top:8px"><input type="hidden" name="csrf_token" value="{session.get('csrf_token','')}"><input name="reason" maxlength="250" placeholder="Why report this review?" aria-label="Report reason" style="padding:6px;border:1px solid #cbd5e1;border-radius:5px"><button type="submit" style="padding:6px 10px;border:0;border-radius:5px;background:#fee2e2;color:#991b1b;cursor:pointer">Report review</button></form>
        </div>
        """

    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>PageTurner - Modern BookStore</title>
        <link rel="stylesheet" href="/static/style.css">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        <style>
            :root { --pink-primary: #ff5e62; --text-dark: #1e293b; --text-muted: #64748b; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background-color: #fff; color: var(--text-dark); }
            .top-bar { background-color: #f8fafc; border-bottom: 1px solid #e2e8f0; font-size: 15px; padding: 6px 40px; display: flex; justify-content: space-between; align-items: center; color: var(--text-muted); }
            .header-nav { display: flex; justify-content: space-between; align-items: center; padding: 16px 40px; background: #fff; box-shadow: 0 2px 10px rgba(0,0,0,0.03); position: sticky; top: 0; z-index: 100; }
            .logo { font-size: 24px; font-weight: 800; color: #f59e0b; text-decoration: none; display: flex; align-items: center; gap: 6px; }
            .nav-links { display: flex; gap: 24px; list-style: none; margin: 0; padding: 0; }
            .nav-links a { text-decoration: none; color: var(--text-dark); font-weight: 600; font-size: 14px; }
            .header-icons { display: flex; align-items: center; gap: 18px; font-size: 16px; }
            .header-icons a { color: var(--text-dark); text-decoration: none; }
            .hero-banner { background: linear-gradient(135deg, #e0f2fe 0%, #fbcfe8 50%, #fef08a 100%); border-radius: 24px; margin: 28px 40px; padding: clamp(34px,5vw,76px) clamp(28px,6vw,84px); min-height: 340px; display: flex; align-items: center; justify-content: space-between; gap:32px; }
            .hero-banner h1 {font-size:clamp(38px,4.5vw,62px)!important;line-height:1.08;max-width:700px}
            .hero-banner p {font-size:clamp(17px,1.5vw,21px)!important;max-width:560px;line-height:1.6}
            .hero-banner img {max-height:340px!important;width:min(32vw,340px);object-fit:cover}
            .explore-features {margin:30px 40px 42px;padding:30px;background:linear-gradient(135deg,#fff7ed,#fff1f2 55%,#eef2ff);border:1px solid #fed7aa;border-radius:24px;box-shadow:0 12px 32px #17203312}
            .explore-features h2 {font-size:clamp(27px,3vw,38px);margin:0 0 8px;color:#172033}
            .explore-grid {display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:16px;margin-top:22px}
            .explore-card {background:#fff;border:1px solid #e2e8f0;border-radius:18px;padding:22px 18px;min-height:170px;box-shadow:0 5px 16px #1720330b}
            .explore-card .feature-icon {font-size:32px;margin-bottom:12px;display:block}
            .explore-card h3 {font-size:18px;margin:0 0 8px;color:#1e293b}
            .explore-card p {font-size:15px;line-height:1.55;margin:0;color:#64748b}
            .loyalty-feature {background:linear-gradient(145deg,#4f46e5,#7c3aed);color:white;border:0}
            .loyalty-feature h3,.loyalty-feature p {color:white}
            .loyalty-feature .feature-icon {color:#fde68a}
            .site-footer {padding:26px 20px;text-align:center;background:#172033;color:#e2e8f0;margin-top:45px;font-size:15px}
            .book-description {font-size:14px;line-height:1.5;color:#64748b;margin:0 0 12px}
            @media(max-width:1050px){.explore-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
            @media(max-width:700px){.explore-features{margin:20px 14px;padding:20px}.explore-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.hero-banner img{width:min(70vw,300px);max-height:240px!important}.hero-banner{min-height:unset}}
            @media(max-width:420px){.explore-grid{grid-template-columns:1fr}}
            .btn-shop { background: var(--pink-primary); color: white; padding: 12px 28px; border-radius: 30px; border: none; font-weight: 700; text-decoration: none; display: inline-block; box-shadow: 0 4px 14px rgba(255, 94, 98, 0.4); }
            .books-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 24px; padding: 0 40px; margin-bottom: 40px; }
        @media(max-width:1000px){.books-grid{grid-template-columns:repeat(2,minmax(0,1fr));padding:0 20px}}
        @media(max-width:560px){.books-grid{grid-template-columns:minmax(0,1fr);padding:0 14px}.hero-banner{margin:14px;padding:24px;flex-wrap:wrap}.header-nav{padding:12px;flex-wrap:wrap}.nav-links{gap:10px;flex-wrap:wrap}}

        .customer-bundles{margin:34px 40px 42px;padding:26px;background:linear-gradient(135deg,#fff7ed,#fff 65%,#eef2ff);border:1px solid #fed7aa;border-radius:22px}
        .bundle-heading{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:20px}.bundle-eyebrow{font-size:11px;letter-spacing:1.8px;font-weight:800;color:#c2410c}.bundle-heading h2{font-size:clamp(23px,3vw,30px);color:#172554;margin:4px 0}.bundle-heading p{color:#64748b;margin:0}.bundle-count{background:#fff;border:1px solid #fed7aa;border-radius:999px;padding:7px 12px;color:#9a3412;font-weight:700;white-space:nowrap}
        .bundle-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:16px}.bundle-card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:20px;box-shadow:0 8px 24px rgba(23,37,84,.06);display:flex;flex-direction:column;min-width:0}.bundle-top{display:flex;justify-content:space-between;align-items:center;gap:10px}.bundle-tag{font-size:11px;font-weight:800;color:#9a3412;background:#ffedd5;border-radius:999px;padding:6px 9px}.bundle-price{font-size:21px;font-weight:850;color:#3157c8;white-space:nowrap}.bundle-card h3{font-size:20px;color:#172554;margin:15px 0 4px;overflow-wrap:anywhere}.bundle-caption{font-size:13px;color:#64748b;margin:0 0 8px}.bundle-books{list-style:none;margin:0;padding:0}.bundle-books li{padding:9px 0;border-bottom:1px solid #f1f5f9;display:flex;flex-direction:column;gap:2px}.bundle-books a{color:#3157c8;text-decoration:none;font-weight:700}.bundle-books a:hover{text-decoration:underline}.bundle-books span{font-size:12px;color:#64748b}.bundle-note{font-size:12px;color:#64748b;margin:14px 0 0}
        @media(max-width:700px){.customer-bundles{margin:24px 14px;padding:18px}.bundle-heading{align-items:flex-start}.bundle-price{font-size:19px}}
        </style>
    </head>
    <body>
        <div class="top-bar">
            <span>{% if session.get('user_id') %}Hello, {{ session.get('user_name', 'reader') }}! Welcome back.{% else %}Welcome, guest! Discover your next favourite book.{% endif %}</span>
            <span>{% if session.get('user_id') %}⭐ Loyalty points: {{ loyalty_points }}{% else %}<a href="/login">Login</a> to earn loyalty points{% endif %}</span>
            <span>Need any help? Call us +91 98765 43210</span>
            <span>Summer sale discount off 50%! <b>Shop Now</b></span>
            <span>2-3 business days delivery & free returns</span>
        </div>

        <header class="header-nav">
            <a href="/" class="logo"><i class="fa-solid fa-book-open"></i> PageTurner</a>
            <ul class="nav-links">
                <li><a href="/">HOME</a></li>
                <li><a href="/?category=Fiction">FICTION</a></li>
                <li><a href="/?category=Self-Help">SELF-HELP</a></li>
                <li><a href="/orders">MY ORDERS</a></li>
                <li><a href="/addresses">ADDRESSES</a></li>
                <li><a href="/admin/dashboard" style="color: #2563eb; font-weight: bold;">ADMIN DASHBOARD</a></li>
            </ul>
            <div class="header-icons">
                <a href="/wishlist" title="Wishlist"><i class="fa-regular fa-heart"></i></a>
                <a href="/notifications" title="Notifications"><i class="fa-regular fa-bell"></i></a>
                <a href="/help" title="Help and contact"><i class="fa-regular fa-circle-question"></i></a>
                <a href="/cart" title="Cart"><i class="fa-solid fa-cart-shopping"></i></a>
                {% if session.user_id or session.admin_logged_in %}
                    <a href="/logout" title="Logout"><i class="fa-solid fa-right-from-bracket"></i></a>
                {% else %}
                    <a href="/login" title="Login"><i class="fa-regular fa-user"></i></a>
                {% endif %}
            </div>
        </header>

        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}<div style="padding: 10px 40px;">{% for cat, msg in messages %}<div class="alert alert-{{cat}}" style="padding: 10px; border-radius: 6px; margin-bottom: 10px; background:#d1fae5; color:#065f46;">{{msg}}</div>{% endfor %}</div>{% endif %}
        {% endwith %}

        <section class="hero-banner">
            <div>
                <h1 style="font-size: 42px; font-weight: 800; margin-bottom: 12px;">How Innovation Works</h1>
                <p style="color: var(--text-muted); font-size: 14px; margin-bottom: 24px;">Discount available. Grab your copy now!</p>
                <a href="/cart" class="btn-shop">Shop Product</a>
            </div>
            <div>
                <img src="https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=400" style="max-height: 280px; border-radius: 8px; box-shadow: -10px 15px 30px rgba(0,0,0,0.15);">
            </div>
        </section>

        <section class="explore-features" aria-labelledby="explore-features-title">
          <div><span style="font-weight:900;color:#c2410c;letter-spacing:1.5px;font-size:13px">MORE THAN JUST BOOKS</span><h2 id="explore-features-title">Explore Features</h2><p style="margin:0;color:#64748b;font-size:17px;line-height:1.6">Everything you need for a smoother, more rewarding reading experience.</p></div>
          <div class="explore-grid">
            <article class="explore-card"><span class="feature-icon">🚚</span><h3>Free Shipping</h3><p>Enjoy free delivery on orders of ₹500 and above.</p></article>
            <article class="explore-card"><span class="feature-icon">🔎</span><h3>Find Your Next Read</h3><p>Search, filter by category, and sort books to discover your next favourite.</p></article>
            <article class="explore-card"><span class="feature-icon">❤️</span><h3>Wishlist</h3><p>Save books you love and return to them whenever you are ready.</p></article>
            <article class="explore-card"><span class="feature-icon">🔔</span><h3>Order Updates</h3><p>Check notifications and track updates for your orders.</p></article>
            <article class="explore-card loyalty-feature"><span class="feature-icon">⭐</span><h3>Loyalty Points</h3><p>{% if session.get('user_id') %}You have {{ loyalty_points }} points. Earn 1 point for every ₹100 spent on an order.{% else %}<a href="/login" style="color:#fde68a;font-weight:800">Sign in</a> to earn 1 loyalty point for every ₹100 spent on an order.{% endif %}</p></article>
          </div>
        </section>

        {% if bundles %}
        <section class="customer-bundles" aria-labelledby="bundle-heading">
            <div class="bundle-heading">
                <div><span class="bundle-eyebrow">CURATED SAVINGS</span><h2 id="bundle-heading">Book Bundles &amp; Combo Offers</h2><p>Great reads together, at one special bundle price.</p></div>
                <span class="bundle-count">{{ bundles|length }} offer{% if bundles|length != 1 %}s{% endif %}</span>
            </div>
            <div class="bundle-grid">
                {% for bundle in bundles %}
                <article class="bundle-card">
                    <div class="bundle-top"><span class="bundle-tag">📚 BOOK COMBO</span><span class="bundle-price">₹{{ '%.2f'|format(bundle.price_paise / 100) }}</span></div>
                    <h3>{{ bundle.name }}</h3>
                    <p class="bundle-caption">Included in this bundle:</p>
                    <ul class="bundle-books" id="bundle-books-{{ bundle.id }}">{% for item in bundle.books %}<li><a href="/book/{{ item.id }}">{{ item.title }}</a><span>{{ item.author }}</span></li>{% endfor %}</ul>
                    <div class="bundle-actions" style="display:flex;gap:10px;flex-wrap:wrap;margin-top:14px">
                    <form method="post" action="/bundles/{{ bundle.id }}/add-to-cart" style="margin:0">
                      <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                      <button type="submit" class="bundle-buy-button" style="background:#3157c8;color:#fff;border:0;border-radius:999px;padding:11px 16px;font-weight:800;cursor:pointer">Add bundle to cart 🛒</button>
                    </form>
                    <a href="#bundle-books-{{ bundle.id }}" style="display:inline-block;background:#eef2ff;color:#243f98;border-radius:999px;padding:11px 16px;font-weight:800;text-decoration:none">View included books</a>
                  </div>
                  <p class="bundle-note">Adds included books to your cart. Bundle discount at checkout is not yet implemented.</p>
                </article>
                {% endfor %}
            </div>
        </section>
        {% endif %}

        {% if flash_sale_books %}
        <section style="margin:28px 40px;padding:24px;border-radius:20px;background:linear-gradient(120deg,#fff1f2,#fff7ed);border:1px solid #fecdd3;">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap"><div><span style="font-weight:900;color:#be123c;letter-spacing:1px">⚡ LIVE FLASH SALE</span><h2 style="margin:6px 0;color:#881337">Limited-time book deals</h2><p style="margin:0;color:#64748b">Sale prices are active only during the configured sale window.</p></div><span style="background:#be123c;color:white;border-radius:999px;padding:8px 13px;font-weight:800">LIVE NOW</span></div>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-top:18px">
          {% for sale in flash_sale_books %}<article style="background:#fff;border:1px solid #ffe4e6;border-radius:14px;padding:15px"><a href="/book/{{ sale.id }}" style="color:#1e293b;text-decoration:none"><img src="{{ sale.image_url or 'https://placehold.co/180x220?text=Book' }}" alt="{{ sale.title }}" style="width:100%;height:130px;object-fit:contain"><h3 style="font-size:16px;margin:8px 0">{{ sale.title }}</h3></a><p style="margin:0;color:#64748b;font-size:13px">{{ sale.author }}</p><p style="margin:8px 0"><b style="color:#be123c">₹{{ sale.sale_price_paise|inr }}</b> <s style="color:#64748b">₹{{ sale.price|inr }}</s></p><span style="font-size:12px;color:#be123c;font-weight:800">FLASH DEAL</span>{% if sale.sale_end %}<p class="sale-timer" data-end="{{ sale.sale_end }}" style="font-size:12px;color:#9f1239;font-weight:800;margin:6px 0">Sale ends: {{ sale.sale_end }}</p>{% endif %}</article>{% endfor %}
          </div>
        </section>
        {% endif %}
        <section style="margin:28px 40px;padding:24px;border-radius:20px;background:linear-gradient(120deg,#eff6ff,#f5f3ff);border:1px solid #c7d2fe;display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap">
          <div><span style="font-weight:900;color:#4338ca;letter-spacing:1px">🎁 GIFT VOUCHERS</span><h2 style="margin:6px 0;color:#1e1b4b">A little reading joy for someone special</h2><p style="margin:0;color:#64748b">Gift voucher ledger tables are included, but issuing and redeeming vouchers at checkout is not yet integrated.</p></div><a href="/checkout" style="background:#4338ca;color:#fff;text-decoration:none;border-radius:999px;padding:12px 18px;font-weight:800">Shop books</a>
        </section>

        {% if recent_books %}
        <section style="margin: 28px 0 36px;">
            <div style="padding: 0 40px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center;">
                <h3 style="font-weight: 700; margin: 0;">Recently Viewed Books</h3>
                <span style="color:#64748b;font-size:13px;">Your last {{ recent_books|length }} viewed book(s)</span>
            </div>
            <div class="books-grid">
                {% for recent in recent_books %}
                <div style="border:1px solid #e2e8f0;border-radius:12px;padding:14px;background:#fff;">
                    <a href="/book/{{ recent.id }}" style="text-decoration:none;color:inherit;">
                        <img src="{{ recent.image_url }}" alt="{{ recent.title }}" style="width:100%;height:150px;object-fit:contain;border-radius:8px;background:#f8fafc;">
                        <div style="font-weight:700;margin:8px 0 4px;">{{ recent.title }}</div>
                        <div style="color:#64748b;font-size:14px;">{{ recent.author }}</div>
                        <div style="font-weight:800;color:#ff5e62;margin-top:8px;">₹{{ recent.price|inr }}</div>
                    </a>
                    <a href="/book/{{ recent.id }}" style="display:block;text-align:center;margin-top:10px;padding:8px;border:1px solid #cbd5e1;border-radius:7px;text-decoration:none;color:#1e293b;">View book</a>
                </div>
                {% endfor %}
            </div>
        </section>
        {% endif %}

        <div style="padding: 0 40px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center;">
            <div><h3 style="font-weight: 700; margin: 0;">Best Selling Items</h3><p style="margin:4px 0 0;color:#64748b;font-size:14px">{% if total_books == 0 %}No books yet{% elif total_books == 1 %}Showing 1 book{% else %}Showing {{ total_books }} books{% endif %}</p></div>
        </div>

        <div class="books-grid">
            {{ book_cards | safe if book_cards else "<p>No books found.</p>" }}
        </div>
        <div style="display:flex;justify-content:center;gap:16px;margin:0 40px 32px;">
          {% if page > 1 %}<a href="{{ url_for('home', q=q, category=category, sort=sort, page=page-1) }}">← Previous</a>{% endif %}
          <span>Page {{ page }} of {{ total_pages }}</span>
          {% if page < total_pages %}<a href="{{ url_for('home', q=q, category=category, sort=sort, page=page+1) }}">Next →</a>{% endif %}
        </div>

        <div style="padding: 0 40px; margin-bottom: 40px;">
            <h4 style="font-weight: 700; margin-bottom: 16px;">💬 Customer Reviews</h4><p><a href="/book-requests" style="display:inline-block;padding:9px 13px;background:#ede9fe;color:#5b21b6;border-radius:8px;text-decoration:none;font-weight:700">📚 Request a book we don't stock</a></p>
            {{ review_cards | safe if review_cards else "<p class='text-muted'>No reviews submitted yet.</p>" }}
            
            <div class="card" style="padding: 16px; background: #f8fafc; border-radius: 8px; margin-top: 20px;">
                <h5 style="font-weight: bold; margin-top: 0;">Leave a Review</h5>
                <form method="POST" action="/review/add" style="display: grid; grid-template-columns: repeat(3, 1fr) auto; gap: 8px;">
                    <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                    <input type="text" name="name" class="form-control" placeholder="Your Name" required style="padding:8px; border:1px solid #cbd5e1; border-radius:4px;">
                    <input type="text" name="book_title" class="form-control" placeholder="Book Title" required style="padding:8px; border:1px solid #cbd5e1; border-radius:4px;">
                    <input type="number" min="1" max="5" name="rating" class="form-control" placeholder="Rating (1-5)" required style="padding:8px; border:1px solid #cbd5e1; border-radius:4px;">
                    <input type="text" name="comment" class="form-control" placeholder="Your comment..." style="grid-column: span 3; padding:8px; border:1px solid #cbd5e1; border-radius:4px;" required>
                    <button type="submit" class="btn btn-primary" style="grid-column: span 1; font-weight: bold; background:#2563eb; color:white; border:none; border-radius:4px; padding:8px 16px; cursor:pointer;">Submit</button>
                </form>
            </div>
        </div>
    <footer class="site-footer">© {{ current_year }} PageTurner Books. All rights reserved.</footer>
    <script>document.querySelectorAll('.sale-timer').forEach(el=>{const raw=el.dataset.end;const end=new Date(raw.replace(' ','T')+'Z').getTime();function tick(){const left=end-Date.now();if(left<=0){el.textContent='Sale ended — refresh to see current price';return}const d=Math.floor(left/86400000),h=Math.floor(left%86400000/3600000),m=Math.floor(left%3600000/60000),sec=Math.floor(left%60000/1000);el.textContent='Sale ends in '+(d?d+'d ':'')+h+'h '+m+'m '+sec+'s';}tick();setInterval(tick,1000)});</script></body>
    </html>
    """, book_cards=book_cards, review_cards=review_cards, page=page, total_pages=total_pages, total_books=total_books, q=q, category=category, sort=sort, categories=categories, recent_books=recent_books, bundles=bundles, flash_sale_books=flash_sale_books, loyalty_points=loyalty_points)

@app.route('/recently-viewed')
def recently_viewed():
    recent_ids = []
    for value in session.get('recent_books', []):
        try:
            book_id = int(value)
        except (TypeError, ValueError):
            continue
        if book_id not in recent_ids:
            recent_ids.append(book_id)
        if len(recent_ids) == 5:
            break
    books = []
    conn = get_db_connection()
    cursor = conn.cursor()
    for book_id in recent_ids:
        cursor.execute("SELECT id, title, author, price, image_url, rating FROM books WHERE id = ?", (book_id,))
        row = cursor.fetchone()
        if row:
            books.append(row)
    conn.close()
    session['recent_books'] = [book['id'] for book in books]
    return render_template_string("""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Recently Viewed Books</title><style>body{font:16px system-ui,sans-serif;margin:0;background:#f8fafc;color:#172033}.wrap{max-width:1100px;margin:30px auto;padding:20px}a{color:#5638b8}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px}.book{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px}.book img{width:100%;height:150px;object-fit:contain}.muted{color:#64748b}</style></head><body><main class='wrap'><p><a href='/'>← Home</a></p><h1>Recently Viewed Books</h1>{% if books %}<div class='grid'>{% for b in books %}<article class='book'><a href='/book/{{ b.id }}'><img src='{{ b.image_url }}' alt='{{ b.title }}'><h3>{{ b.title }}</h3></a><p class='muted'>{{ b.author }}</p><p>₹{{ b.price|inr }}</p></article>{% endfor %}</div>{% else %}<p>No recently viewed books yet. Open a book's details and return here.</p>{% endif %}</main></body></html>""", books=books)

@app.route('/review/add', methods=['POST'])
@csrf_required
def add_review():
    name = request.form.get('name', '').strip()
    book_title = request.form.get('book_title', '').strip()
    rating = request.form.get('rating', '5')
    comment = request.form.get('comment', '').strip()

    if name and book_title and comment:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO reviews (name, book_title, rating, comment) VALUES (?, ?, ?, ?)", (name, book_title, rating, comment))
        conn.commit()
        conn.close()
        flash("Thank you! Your review has been submitted.", "success")
    else:
        flash("Please fill out all fields to submit a review.", "danger")
    return redirect(url_for('home'))

@app.route('/book/<int:book_id>')
def book_detail(book_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM books WHERE id = ?", (book_id,))
    book = cursor.fetchone()

    # Fetch other books for "Customers Also Bought" recommendations
    cursor.execute("SELECT * FROM books WHERE id != ? LIMIT 4", (book_id,))
    recommended_books = cursor.fetchall()
    conn.close()

    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for('home'))

    recent = session.get('recent_books', [])
    recent = [int(x) for x in recent if str(x).isdigit() and int(x) != book_id]
    session['recent_books'] = ([book_id] + recent)[:5]

    csrf_tok = session.get('csrf_token', '')
    price_str = format_inr(book['price'])
    coming_soon = bool(book['release_date'] and book['release_date'] > datetime.utcnow().date().isoformat())
    is_new_release = bool(book['release_date'] and not coming_soon and book['release_date'] >= (datetime.utcnow().date() - timedelta(days=14)).isoformat())
    wishlist_ids = session.get('wishlist', [])
    is_wishlisted = book['id'] in wishlist_ids
    heart_text = "❤️ Remove from Wishlist" if is_wishlisted else "🤍 Add to Wishlist"

    recommendations_html = ""
    for r_book in recommended_books:
        r_price = format_inr(r_book['price'])
        recommendations_html += f"""
        <div style="background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px; text-align: center; box-shadow: 0 2px 6px rgba(0,0,0,0.02);">
            <img src="{r_book['image_url']}" style="height: 130px; object-fit: contain; margin-bottom: 8px; border-radius: 4px;">
            <div style="font-size: 15px; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 2px;">{r_book['title']}</div>
            <div style="color: #f59e0b; font-size: 14px; margin-bottom: 4px;">★ {r_book['rating']}</div>
            <div style="color: #ff5e62; font-weight: 800; font-size: 14px; margin-bottom: 8px;">₹{r_price}</div>
            <a href="/book/{r_book['id']}" style="display: block; background: #2563eb; color: white; padding: 6px; font-size: 15px; text-decoration: none; border-radius: 4px; font-weight: bold;">View Book</a>
        </div>
        """

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><meta name="viewport" content="width=device-width, initial-scale=1"><title>{{ book.title }} - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
    <body style="background-color: #f8fafc; padding: 40px; font-family: sans-serif;">
    <div style="max-width: 800px; padding: 24px; background:white; margin:auto; border-radius:12px; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
        <div style="display: flex; gap: 24px; align-items: center; flex-wrap: wrap;">
            <div style="text-align: center; flex: 1; min-width: 200px;">
                <img src="{{ book.image_url }}" style="width:100%; max-width:520px; height:280px; object-fit:cover; aspect-ratio:16/9; border-radius:12px;">
            </div>
            <div style="flex: 2; min-width: 250px;">
                {% if coming_soon %}<p style="display:inline-block;background:#7c3aed;color:white;padding:5px 10px;border-radius:6px;font-weight:800">COMING SOON · {{ book.release_date }}</p>{% elif is_new_release %}<p style="display:inline-block;background:#059669;color:white;padding:5px 10px;border-radius:6px;font-weight:800">NEW RELEASE</p>{% endif %}
                <h2 style="font-weight: bold; margin-top: 0;">{{ book.title }}</h2>
                <h5 style="color:#64748b;">by <a href="{{ url_for('advanced.author_page', name=book.author) }}" style="color:#2563eb;text-decoration:underline;">{{ book.author }}</a></h5>
                <p style="margin: 8px 0;"><span style="background: #e2e8f0; padding: 3px 8px; border-radius: 4px; font-size: 15px;">{{ book.category }}</span> <span style="background: #fef08a; padding: 3px 8px; border-radius: 4px; font-size: 15px;">{{ book.rating }} ⭐</span></p>
                <h3 style="color: #ff5e62; font-weight: bold;">₹{{ price_str }}</h3>
                <p style="font-size: 15px; color:#64748b;">Stock: {% if book.stock > 0 %}<span class="stock-status in-stock">In stock</span>{% else %}<span class="stock-status out-of-stock">Out of stock</span>{% endif %}</p>
                {% if coming_soon %}<p style="padding:12px;background:#f3e8ff;color:#6b21a8;border-radius:8px;font-weight:700">This book is not available to order until its release date.</p>{% else %}<form method="POST" action="/cart/add/{{ book.id }}"><input type="hidden" name="csrf_token" value="{{ csrf_tok }}"><button type="submit" style="font-weight: bold; width: 100%; margin-bottom: 8px; background:#fbbf24; border:none; padding:10px; border-radius:6px; cursor:pointer;">Add to Cart</button></form>{% endif %}
                <a href="/wishlist/toggle/{{ book.id }}" style="display:block; text-align:center; font-size: 15px; width: 100%; color: #ef4444; border:1px solid #ef4444; padding:8px; box-sizing:border-box; border-radius:4px; text-decoration:none; font-weight:bold;">{{ heart_text }}</a>
            </div>
        </div>

        <!-- Customers Also Bought Recommendation Section -->
        <div style="margin-top: 35px; border-top: 1px solid #e2e8f0; padding-top: 24px;">
            <h4 style="font-size: 18px; font-weight: 800; color: #0f172a; margin-bottom: 16px;">🔥 Customers Also Bought</h4>
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px;">
                {{ recommendations_html | safe }}
            </div>
        </div>

        <hr style="margin: 24px 0; border: 0; border-top: 1px solid #e2e8f0;">
        <a href="/" style="color:#2563eb; text-decoration:none; font-weight:bold;">← Return to Catalog</a>
    </div>
    </body>
    </html>
    """, book=book, csrf_tok=csrf_tok, price_str=price_str, heart_text=heart_text, recommendations_html=recommendations_html, coming_soon=coming_soon, is_new_release=is_new_release)

@app.route('/wishlist/toggle/<int:book_id>')
def toggle_wishlist(book_id):
    wishlist = session.get('wishlist', [])
    if book_id in wishlist:
        wishlist.remove(book_id)
        flash("Removed book from your wishlist.", "info")
    else:
        wishlist.append(book_id)
        flash("Added book to your wishlist!", "success")
    session['wishlist'] = wishlist
    return redirect(request.referrer or url_for('home'))

@app.route('/wishlist')
def wishlist():
    wishlist_ids = session.get('wishlist', [])
    conn = get_db_connection()
    cursor = conn.cursor()
    items_html = ""
    for b_id in wishlist_ids:
        cursor.execute("SELECT * FROM books WHERE id = ?", (b_id,))
        b = cursor.fetchone()
        if b:
            items_html += f"""
            <div style="background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center;">
                <div style="display: flex; gap: 16px; align-items: center;">
                    <img src="{b['image_url']}" style="width: 50px; height: 70px; object-fit: contain;">
                    <div>
                        <h4 style="margin: 0 0 4px 0;">{b['title']}</h4>
                        <p style="margin: 0; color: #64748b; font-size: 15px;">by {b['author']}</p>
                        <div style="color: #ff5e62; font-weight: bold; margin-top: 4px;">₹{format_inr(b['price'])}</div>
                    </div>
                </div>
                <div style="display: flex; gap: 8px; align-items:center;">
                    <form method="POST" action="/cart/add/{b['id']}" style="margin:0;">
                        <input type="hidden" name="csrf_token" value="{session.get('csrf_token', '')}">
                        <button type="submit" style="background: #fbbf24; border: none; padding: 6px 12px; font-weight: bold; border-radius: 4px; cursor: pointer;">Move to Cart</button>
                    </form>
                    <a href="/wishlist/toggle/{b['id']}" style="background: #fff; border: 1px solid #ef4444; color: #ef4444; padding: 6px 12px; text-decoration: none; border-radius: 4px; font-weight: bold; font-size:12px;">Remove</a>
                </div>
            </div>
            """
    conn.close()
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>My Wishlist</title></head>
    <body style="font-family: sans-serif; padding: 40px; background: #f8fafc;">
        <div style="max-width: 700px; margin: auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
            <h3>❤️ My Wishlist</h3>
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}{% for cat, msg in messages %}<div style="padding: 10px; background: #d1fae5; color: #065f46; border-radius: 6px; margin-bottom: 15px;">{{ msg }}</div>{% endfor %}{% endif %}
            {% endwith %}
            {{ items_html | safe if items_html else "<p style='color: #64748b;'>Your wishlist is empty.</p>" }}
            <a href="/" style="display: block; text-align: center; margin-top: 20px; color: #1e293b; text-decoration: none; font-weight: bold;">← Back to Storefront</a>
        </div>
    </body>
    </html>
    """, items_html=items_html)

@app.route('/bundles/<int:bundle_id>/add-to-cart', methods=['POST'])
@csrf_required
def add_bundle_to_cart(bundle_id):
    """Validate every component first, then add the bundle's books to the session cart."""
    conn = get_db_connection()
    try:
        bundle = conn.execute("SELECT id, name, active FROM book_bundles WHERE id = ?", (bundle_id,)).fetchone()
        if not bundle or not bundle['active']:
            flash("This offer is no longer available.", "danger")
            return redirect(url_for('home') + '#book-bundles')
        items = conn.execute("SELECT bi.book_id, bi.quantity, b.title, b.stock FROM bundle_items bi JOIN books b ON b.id=bi.book_id WHERE bi.bundle_id=?", (bundle_id,)).fetchall()
        if not items:
            flash("This bundle has no books available.", "danger")
            return redirect(url_for('home') + '#book-bundles')
        cart = dict(session.get('cart', {}))
        for item in items:
            key = str(item['book_id']); quantity = int(item['quantity'] or 1)
            if int(item['stock'] or 0) < int(cart.get(key, 0) or 0) + quantity:
                flash(f"Not enough stock for {item['title']}. Your cart was not changed.", "danger")
                return redirect(url_for('home') + '#book-bundles')
        for item in items:
            key = str(item['book_id']); cart[key] = int(cart.get(key, 0) or 0) + int(item['quantity'] or 1)
        session['cart'] = cart
        flash(f"{bundle['name']} books were added to your cart. The current checkout still charges regular book prices.", "success")
        return redirect(url_for('cart'))
    finally:
        conn.close()


@app.route('/cart/add/<int:book_id>', methods=['POST'])
@csrf_required
def add_to_cart(book_id):
    conn = get_db_connection()
    book = conn.execute("SELECT stock, title, release_date FROM books WHERE id = ?", (book_id,)).fetchone()
    conn.close()
    if not book:
        flash("That book could not be found.", "danger")
        return redirect(url_for('home'))
    if book['release_date'] and book['release_date'] > datetime.utcnow().date().isoformat():
        flash("This book is coming soon and cannot be ordered yet.", "warning")
        return redirect(request.referrer or url_for('home'))
    cart = session.get('cart', {})
    key = str(book_id)
    if book['stock'] <= 0 or cart.get(key, 0) >= book['stock']:
        flash("Sorry, there are not enough copies in stock.", "danger")
    else:
        cart[key] = cart.get(key, 0) + 1
        session['cart'] = cart
        flash("Added book to cart!", "success")
    return redirect(request.referrer or url_for('cart'))

@app.route('/cart/update/<int:book_id>', methods=['POST'])
@csrf_required
def update_cart(book_id):
    try:
        quantity = int(request.form.get('quantity', '0'))
    except (TypeError, ValueError):
        quantity = 0
    conn = get_db_connection()
    book = conn.execute("SELECT stock FROM books WHERE id = ?", (book_id,)).fetchone()
    conn.close()
    cart = session.get('cart', {})
    if not book or quantity < 1 or quantity > book['stock']:
        flash("Choose a quantity between 1 and the available stock.", "danger")
    else:
        cart[str(book_id)] = quantity
        session['cart'] = cart
        flash("Cart quantity updated.", "success")
    return redirect(url_for('cart'))

@app.route('/cart/remove/<int:book_id>', methods=['POST'])
@csrf_required
def remove_from_cart(book_id):
    cart = session.get('cart', {})
    cart.pop(str(book_id), None)
    session['cart'] = cart
    flash("Book removed from cart.", "success")
    return redirect(url_for('cart'))

@app.route('/cart')
def cart():
    cart_session = session.get('cart', {})
    conn = get_db_connection()
    cart_items = []
    subtotal = 0
    for raw_id, raw_qty in cart_session.items():
        try:
            book_id, qty = int(raw_id), int(raw_qty)
        except (TypeError, ValueError):
            continue
        book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if book and qty > 0:
            line_total = int(book['price']) * qty
            subtotal += line_total
            cart_items.append({'id': book['id'], 'title': book['title'], 'author': book['author'], 'price': int(book['price']), 'stock': int(book['stock']), 'category': book['category'], 'image_url': book['image_url'], 'quantity': qty, 'line_total': line_total})
    conn.close()
    session['calculated_total'] = subtotal
    return render_template_string("""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Your Cart | PageTurner Books</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f5f7fc;color:#172033;font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}header{padding:18px clamp(18px,4vw,60px);background:#fff;display:flex;justify-content:space-between;align-items:center;gap:15px;box-shadow:0 2px 15px #1720330b;position:sticky;top:0;z-index:3}header a{color:#4338ca;text-decoration:none;font-weight:750}.wrap{max-width:1400px;margin:auto;padding:32px clamp(16px,4vw,55px)}.head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.muted{color:#687386}.layout{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(280px,.8fr);gap:24px;margin-top:22px}.panel{background:#fff;border:1px solid #e6eaf2;border-radius:18px;padding:22px;box-shadow:0 8px 28px #17203308}.item{display:grid;grid-template-columns:115px minmax(0,1fr) auto;gap:18px;align-items:center;padding:20px 0;border-bottom:1px solid #edf0f5}.item:last-child{border-bottom:0}.cover{width:115px;height:145px;object-fit:cover;background:#f1f5f9;border-radius:10px}.title{font-size:18px;font-weight:800;margin:0}.qty{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}.qty input{width:72px;padding:9px;border:1px solid #cbd5e1;border-radius:8px}.btn{border:0;border-radius:9px;padding:10px 14px;font-weight:750;cursor:pointer;text-decoration:none;display:inline-block}.primary{background:#5b43d6;color:white}.soft{background:#fff2a3;color:#172033}.outline{border:1px solid #d6dbea;background:white;color:#172033}.danger{background:#fff1f2;color:#be123c}.btn:focus,input:focus{outline:3px solid #c7d2fe;outline-offset:2px}.summaryline{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-bottom:1px solid #edf0f5}.total{font-size:22px;font-weight:850}.empty{text-align:center;padding:65px 20px}.toast{padding:12px 16px;background:#ecfdf5;color:#166534;border:1px solid #bbf7d0;border-radius:10px;margin:16px 0}.toast.danger{background:#fff1f2;color:#9f1239}@media(max-width:850px){.layout{grid-template-columns:1fr}.item{grid-template-columns:82px minmax(0,1fr)}.cover{width:82px;height:110px}.itemprice{grid-column:2}.panel{padding:16px}}@media(max-width:460px){.item{gap:12px}.title{font-size:16px}header{align-items:flex-start;flex-direction:column}}
    </style></head><body><header><a href="/" style="font-size:21px">📚 PageTurner Books</a><nav style="display:flex;gap:18px;flex-wrap:wrap"><a href="/">Storefront</a><a href="/notifications">🔔 Notifications</a><a href="/help">❔ Help</a></nav></header><main class="wrap"><div class="head"><div><h1 style="margin:0;font-size:clamp(27px,3vw,36px)">Your shopping cart 🛒</h1><p class="muted" style="margin:5px 0 0">Review your books, update quantities, and continue to secure checkout.</p></div><a class="btn outline" href="/">← Continue shopping</a></div>
    {% with messages = get_flashed_messages(with_categories=true) %}{% for category,message in messages %}<div class="toast {% if category in ['danger','error'] %}danger{% endif %}" role="status">{{ message }}</div>{% endfor %}{% endwith %}
    {% if cart_items %}<div class="layout"><section class="panel"><h2 style="margin-top:0">Your books <span class="muted" style="font-size:15px">({{ cart_items|length }} item types)</span></h2>{% for item in cart_items %}<article class="item"><img class="cover" src="{{ item.image_url }}" alt="Cover of {{ item.title }}" onerror="this.onerror=null;this.src='https://placehold.co/240x300/f3f4f6/374151?text=Book+Cover'"><div><p class="title">{{ item.title }}</p><p class="muted" style="margin:3px 0">by {{ item.author }} · {{ item.category }}</p><p style="font-weight:800;color:#ef5350;margin:8px 0">₹{{ item.price|inr }}</p><div class="qty"><form method="POST" action="/cart/update/{{ item.id }}" style="display:flex;gap:7px;align-items:center"><input type="hidden" name="csrf_token" value="{{ session.csrf_token }}"><label for="qty{{ item.id }}" class="muted">Qty</label><input id="qty{{ item.id }}" type="number" name="quantity" min="1" max="{{ item.stock }}" value="{{ item.quantity }}" aria-label="Quantity for {{ item.title }}"><button class="btn outline" type="submit">Update</button></form><form method="POST" action="/cart/remove/{{ item.id }}"><input type="hidden" name="csrf_token" value="{{ session.csrf_token }}"><button class="btn danger" type="submit">Remove</button></form></div><small class="muted">{{ item.stock }} available in stock</small></div><div class="itemprice"><span class="muted">Item total</span><div style="font-size:20px;font-weight:850">₹{{ item.line_total|inr }}</div></div></article>{% endfor %}<p class="muted" style="margin-bottom:0">🔒 Your cart is saved for this browsing session. You can update or remove items before checkout.</p></section><aside class="panel"><h2 style="margin-top:0">Order summary</h2><div class="summaryline"><span class="muted">Items subtotal</span><b>₹{{ subtotal|inr }}</b></div><div class="summaryline"><span class="muted">Delivery</span><b style="color:#15803d">Calculated at checkout</b></div><div class="summaryline total"><span>Total</span><span>₹{{ subtotal|inr }}</span></div><p class="shipping-hint" style="font-size:14px;color:#166534">{% if subtotal < free_shipping_limit %}Add ₹{{ (free_shipping_limit - subtotal)|inr }} more for free shipping (orders ₹500 and above).{% else %}You get free shipping!{% endif %}</p><p class="muted" style="font-size:14px">Coupons and available payment methods can be selected on the next step.</p><a class="btn primary" href="/checkout" style="display:block;text-align:center;padding:14px">Proceed to checkout →</a><a class="btn soft" href="/" style="display:block;text-align:center;margin-top:10px">＋ Add more books</a><div style="margin-top:20px;padding:14px;background:#f8fafc;border-radius:12px"><b>Need help?</b><p class="muted" style="margin:4px 0 8px;font-size:14px">Questions about a book or your order?</p><a href="/help">Visit Help Centre →</a></div></aside></div>{% else %}<section class="panel empty"><div style="font-size:62px">🛒</div><h2>Your cart is waiting for a good book</h2><p class="muted">Browse our collection and add a book to see its cover, details, quantity controls, and order total here.</p><a class="btn primary" href="/">Explore books</a></section>{% endif %}</main></body></html>""", cart_items=cart_items, subtotal=subtotal, free_shipping_limit=FREE_SHIPPING_LIMIT_PAISE)

@app.route('/checkout', methods=['GET', 'POST'])
@csrf_required
def checkout():
    if not session.get('cart'):
        flash('Your cart is empty. Add a book before checkout.', 'warning')
        return redirect(url_for('cart'))
    if request.method == 'POST':
        c_name = request.form.get('name', '').strip()
        c_email = request.form.get('email', '').strip()
        c_phone = request.form.get('phone', '').strip()
        c_address = request.form.get('address', '').strip()
        payment_method = request.form.get('payment_method', 'Cash on Delivery').strip()
        gift_wrap = 1 if request.form.get('gift_wrap') == 'on' else 0
        gift_message = request.form.get('gift_message', '').strip()
        if len(gift_message) > 150:
            flash('Gift message must be 150 characters or fewer.', 'danger')
            return redirect(url_for('checkout'))
        if not gift_wrap:
            gift_message = ''
        allowed_payments = {'Cash on Delivery', 'Google Pay (demo)', 'Paytm (demo)', 'PhonePe (demo)', 'Other UPI (demo)', 'Credit/Debit Card (demo)'}
        if payment_method not in allowed_payments:
            payment_method = 'Cash on Delivery'
        if not c_name or not c_email or not c_address or not c_phone.isdigit() or len(c_phone) != 10:
            flash("Enter all details and a valid 10-digit phone number.", "danger")
            return redirect(url_for('checkout'))
        cart = session.get('cart', {})
        if not cart:
            flash("Your cart is empty.", "danger")
            return redirect(url_for('cart'))
        conn = get_db_connection()
        try:
            # The order, line items and stock reductions succeed or fail together.
            conn.execute("BEGIN IMMEDIATE")
            # A stale browser session can contain a user ID that no longer exists.
            # Keep guest checkout working and avoid foreign-key failures in stock_log.
            checkout_user_id = session.get('user_id')
            if checkout_user_id is not None:
                existing_user = conn.execute('SELECT id FROM users WHERE id = ?', (checkout_user_id,)).fetchone()
                if not existing_user:
                    checkout_user_id = None
            lines = []
            total = 0
            for raw_id, raw_qty in cart.items():
                book_id, qty = int(raw_id), int(raw_qty)
                if qty < 1:
                    raise ValueError("Invalid quantity in cart.")
                book = conn.execute("SELECT id, title, price, sale_price_paise, sale_start, sale_end, release_date FROM books WHERE id = ?", (book_id,)).fetchone()
                if not book:
                    raise ValueError("A book in your cart no longer exists.")
                if book['release_date'] and book['release_date'] > datetime.utcnow().date().isoformat():
                    raise ValueError(f"'{book['title']}' is coming soon and cannot be ordered yet.")
                changed = conn.execute("UPDATE books SET stock = stock - ? WHERE id = ? AND stock >= ?", (qty, book_id, qty))
                if changed.rowcount != 1:
                    raise ValueError(f"Sorry, '{book['title']}' just sold out or has insufficient stock.")
                regular_price = int(book['price'])
                now_sale = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                sale_price = book['sale_price_paise']
                sale_active = sale_price is not None and 0 <= int(sale_price) < regular_price and (not book['sale_start'] or book['sale_start'] <= now_sale) and (not book['sale_end'] or book['sale_end'] >= now_sale)
                effective_price = int(sale_price) if sale_active else regular_price
                line_total = effective_price * qty
                total += line_total
                lines.append((book_id, qty, effective_price, line_total))
                conn.execute("INSERT INTO stock_log (book_id, change, reason, user_id) VALUES (?, ?, ?, ?)", (book_id, -qty, 'purchase', checkout_user_id))
            subtotal = total
            coupon_code = session.get('coupon_code', '').strip().upper()
            coupon = conn.execute("SELECT * FROM coupons WHERE code = ? AND is_active = 1", (coupon_code,)).fetchone() if coupon_code else None
            discount = 0
            if coupon:
                if coupon['discount_type'] == 'percentage':
                    discount = subtotal * int(coupon['discount_value']) // 100
                elif coupon['discount_type'] == 'fixed':
                    discount = int(coupon['discount_value'])
                discount = min(subtotal, max(0, discount))
            total = subtotal - discount + (3000 if gift_wrap else 0)
            cur = conn.execute("INSERT INTO orders (user_id, customer_name, customer_email, customer_phone, customer_address, total_amount, discount_amount, status, payment_method, gift_wrap, gift_message) VALUES (?, ?, ?, ?, ?, ?, ?, 'Placed', ?, ?, ?)", (checkout_user_id, c_name, c_email, c_phone, c_address, total, discount, payment_method, gift_wrap, gift_message))
            order_id = cur.lastrowid
            if coupon and discount:
                conn.execute("INSERT OR IGNORE INTO coupon_uses (coupon_id, user_id, order_id) VALUES (?, ?, ?)", (coupon['id'], checkout_user_id, order_id))
            for book_id, qty, price, line_total in lines:
                conn.execute("INSERT INTO order_items (order_id, book_id, quantity, price_at_purchase) VALUES (?, ?, ?, ?)", (order_id, book_id, qty, price))
            conn.execute("INSERT INTO order_status_history (order_id, status, changed_by, note) VALUES (?, 'Placed', ?, 'Order created')", (order_id, str(checkout_user_id) if checkout_user_id is not None else 'guest'))
            earned_points = max(0, total // 10000)  # 1 point per ₹100 spent (money stored in paise).
            if checkout_user_id is not None and earned_points:
                conn.execute("UPDATE users SET loyalty_points = COALESCE(loyalty_points, 0) + ? WHERE id = ?", (earned_points, checkout_user_id))
            notice = f'Your PageTurner order #{order_id} has been placed. Payment method: {payment_method}.'
            if checkout_user_id is not None:
                notice += f' You earned {earned_points} loyalty points.'
            conn.execute("INSERT INTO notifications (user_id, order_id, title, message) VALUES (?, ?, ?, ?)", (checkout_user_id, order_id, 'Order placed successfully', notice))
            conn.commit()
        except (ValueError, sqlite3.Error) as exc:
            conn.rollback()
            if isinstance(exc, ValueError):
                message = str(exc)
            else:
                # Log the technical cause in the terminal for easy troubleshooting,
                # while giving the shopper a clear message instead of a silent failure.
                app.logger.exception("Checkout database transaction failed")
                message = "Your order was not placed, and no stock was deducted. Please check that the project is running from its extracted folder and try again. If it continues, share the terminal error with the developer."
            flash(message, "danger")
            conn.close()
            return redirect(url_for('checkout'))
        conn.close()
        session.pop('cart', None)
        session.pop('calculated_total', None)
        session.pop('coupon_code', None)
        session['last_order_id'] = order_id
        if checkout_user_id is not None:
            flash(f"Order #{order_id} placed successfully! You earned {max(0, total // 10000)} loyalty points (1 point per ₹100).", "success")
        else:
            flash(f"Order #{order_id} placed successfully! Sign in next time to earn loyalty points.", "success")
        return redirect(url_for('order_success', order_id=order_id))

    cart = session.get('cart', {})
    conn = get_db_connection()
    cart_items = []
    total_paise = 0
    for raw_id, raw_qty in cart.items():
        book = conn.execute("SELECT id, title, price, sale_price_paise, sale_start, sale_end, release_date FROM books WHERE id = ?", (int(raw_id),)).fetchone()
        if book:
            qty = int(raw_qty)
            regular_price = int(book['price'])
            now_sale = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            sale_price = book['sale_price_paise']
            sale_active = sale_price is not None and 0 <= int(sale_price) < regular_price and (not book['sale_start'] or book['sale_start'] <= now_sale) and (not book['sale_end'] or book['sale_end'] >= now_sale)
            effective_price = int(sale_price) if sale_active else regular_price
            line = effective_price * qty
            total_paise += line
            cart_items.append({'title': book['title'], 'quantity': qty, 'price': effective_price, 'subtotal': line, 'on_sale': sale_active})
    coupon_code = session.get('coupon_code', '').strip().upper()
    coupon = conn.execute("SELECT * FROM coupons WHERE code = ? AND is_active = 1", (coupon_code,)).fetchone() if coupon_code else None
    subtotal_paise = total_paise
    discount_paise = 0
    if coupon:
        if coupon['discount_type'] == 'percentage':
            discount_paise = subtotal_paise * int(coupon['discount_value']) // 100
        else:
            discount_paise = int(coupon['discount_value'])
        discount_paise = min(subtotal_paise, max(0, discount_paise))
    total_paise = subtotal_paise - discount_paise
    conn.close()
    return render_template_string("""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Checkout | PageTurner</title><style>
    *{box-sizing:border-box}body{margin:0;font:17px/1.6 system-ui,Segoe UI,sans-serif;background:#f4f6fb;color:#172033}header{background:#fff;padding:18px 5%;box-shadow:0 2px 12px #15213a12;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}header a{color:#5638b8;text-decoration:none;font-weight:700}.page{width:100%;padding:34px 5%;}.layout{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(300px,.8fr);gap:26px;max-width:1500px;margin:auto}.card{background:white;border-radius:18px;padding:28px;box-shadow:0 8px 28px #17203310;margin-bottom:20px}.fields{display:grid;grid-template-columns:1fr 1fr;gap:18px}.field{display:flex;flex-direction:column;gap:7px}.field.full{grid-column:1/-1}label{font-weight:650}input,textarea{width:100%;padding:13px 14px;border:1px solid #d6dbe7;border-radius:10px;font:inherit}textarea{min-height:105px}.payment{display:flex;align-items:center;gap:12px;border:1px solid #dfe3ee;border-radius:12px;padding:15px;margin:10px 0;cursor:pointer;transition:.2s}.payment:has(input:checked){border-color:#6b4ce6;background:#f4f0ff;box-shadow:0 0 0 2px #6b4ce61a}.payment input{width:auto;accent-color:#6b4ce6}.btn{display:block;width:100%;border:0;border-radius:12px;padding:15px;background:#5c3cc8;color:white;font-weight:750;font-size:17px;cursor:pointer;margin-top:20px}.btn:hover{background:#4326a5;transform:translateY(-1px)}.line{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-bottom:1px solid #edf0f5}.muted{color:#697386}.total{font-size:24px;font-weight:800}.demo{font-size:14px;color:#8a5a00;background:#fff6db;border-radius:9px;padding:11px;margin-top:12px}@media(max-width:800px){.layout{grid-template-columns:1fr}.fields{grid-template-columns:1fr}.field.full{grid-column:auto}.page{padding:20px 4%}.card{padding:20px}}
    </style></head><body><header><a href="/">📚 PageTurner Books</a><nav><a href="/orders">My Orders</a> &nbsp; · &nbsp; <a href="/notifications">Notifications</a> &nbsp; · &nbsp; <a href="/help">Help Centre</a></nav></header><main class="page"><h1>Secure checkout</h1><p class="muted">Add your delivery details and choose how you would like to pay.</p><div class="layout"><section class="card"><h2>🏷️ Promo code</h2><form method="POST" action="/coupon/apply" style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:24px;"><input type="hidden" name="csrf_token" value="{{ session.csrf_token }}"><input name="coupon_code" value="{{ coupon_code }}" placeholder="Enter coupon code" style="flex:1;min-width:180px;padding:12px;border:1px solid #d6dbe7;border-radius:10px;"><button type="submit" style="border:0;border-radius:10px;padding:12px 20px;background:#16805d;color:white;font-weight:700;cursor:pointer;">Apply coupon</button></form>{% if coupon_code and discount_paise > 0 %}<p style="color:#16805d;font-weight:700;">✓ {{ coupon_code }} applied successfully.</p>{% endif %}<form method="POST"><input type="hidden" name="csrf_token" value="{{ session.csrf_token }}"><h2>Delivery information</h2><div class="fields"><div class="field"><label>Full name</label><input name="name" value="{{ session.get('user_name','') }}" required></div><div class="field"><label>Email address</label><input type="email" name="email" required placeholder="you@example.com"></div><div class="field"><label>Mobile number</label><input type="tel" name="phone" pattern="[0-9]{10}" maxlength="10" required placeholder="10-digit number"></div><div class="field full"><label>Delivery address</label><textarea name="address" required placeholder="House / street / city / pincode"></textarea></div></div><section style="margin-top:22px;padding:16px;background:#fff7ed;border:1px solid #fed7aa;border-radius:12px"><label style="display:flex;gap:10px;align-items:center"><input type="checkbox" name="gift_wrap" id="giftWrap" style="width:auto"> 🎁 Add gift wrap (+₹30)</label><label for="giftMessage" style="display:block;margin-top:12px">Gift message (up to 150 characters)</label><textarea id="giftMessage" name="gift_message" maxlength="150" placeholder="Write a short gift message…" style="min-height:75px"></textarea><small id="giftCount">0/150 characters</small></section><h2 style="margin-top:28px">Choose payment method</h2><label class="payment"><input type="radio" name="payment_method" value="Cash on Delivery" checked><span>💵 <b>Cash on Delivery</b><br><span class="muted">Pay when your books arrive</span></span></label><label class="payment"><input type="radio" name="payment_method" value="Google Pay (demo)"><span>🟢 <b>Google Pay / GPay</b><br><span class="muted">Demo selection — no money is charged</span></span></label><label class="payment"><input type="radio" name="payment_method" value="Paytm (demo)"><span>🔵 <b>Paytm</b><br><span class="muted">Demo selection — no money is charged</span></span></label><label class="payment"><input type="radio" name="payment_method" value="PhonePe (demo)"><span>🟣 <b>PhonePe</b><br><span class="muted">Demo selection — no money is charged</span></span></label><label class="payment"><input type="radio" name="payment_method" value="Other UPI (demo)"><span>📲 <b>Other UPI app</b><br><span class="muted">Demo selection — no money is charged</span></span></label><label class="payment"><input type="radio" name="payment_method" value="Credit/Debit Card (demo)"><span>💳 <b>Credit / debit card</b><br><span class="muted">Demo option — do not enter card details</span></span></label><div class="demo">🔒 This is a project demo. UPI and card choices are recorded as a selected method only; no real payment gateway is connected.</div><button class="btn" id="placeOrderBtn" type="submit">Place order · ₹{{ total_paise|inr }}</button></form></section><aside class="card"><h2>Order summary</h2>{% for item in cart_items %}<div class="line"><div><b>{{ item.title }}</b><br><span class="muted">{{ item.quantity }} × ₹{{ item.price|inr }}</span></div><b>₹{{ item.subtotal|inr }}</b></div>{% endfor %}<div class="line"><span>Subtotal</span><span>₹{{ subtotal_paise|inr }}</span></div>{% if discount_paise > 0 %}<div class="line" style="color:#16805d;font-weight:700;"><span>Coupon discount ({{ coupon_code }})</span><span>− ₹{{ discount_paise|inr }}</span></div>{% endif %}{% if total_paise > 0 %}<div class="line"><span>Gift wrap (optional)</span><span>+ ₹30 when selected</span></div>{% endif %}<div class="line total"><span>Total before optional gift wrap</span><span>₹{{ total_paise|inr }}</span></div><p class="muted">After placing your order, you can view order updates in the Notifications section.</p></aside></div></main><script>const gm=document.getElementById('giftMessage'),gc=document.getElementById('giftCount'),gw=document.getElementById('giftWrap');function giftCount(){gc.textContent=gm.value.length+'/150 characters';gm.disabled=!gw.checked;document.getElementById('placeOrderBtn').textContent='Place order · ₹'+Math.floor(({{ total_paise }}+(gw.checked?3000:0))/100);}gw.addEventListener('change',giftCount);gm.addEventListener('input',giftCount);giftCount();</script></body></html>""", cart_items=cart_items, total_paise=total_paise, subtotal_paise=subtotal_paise, discount_paise=discount_paise, coupon_code=coupon_code)

@app.route('/coupon/apply', methods=['POST'])
@csrf_required
def apply_coupon():
    code = request.form.get('coupon_code', '').strip().upper()
    if not code:
        session.pop('coupon_code', None)
        flash('Enter a coupon code first.', 'warning')
        return redirect(url_for('checkout'))
    conn = get_db_connection()
    coupon = conn.execute('SELECT * FROM coupons WHERE code = ? AND is_active = 1', (code,)).fetchone()
    conn.close()
    if not coupon:
        session.pop('coupon_code', None)
        flash('That coupon code is invalid or inactive.', 'danger')
    else:
        session['coupon_code'] = code
        flash(f'Coupon {code} applied.', 'success')
    return redirect(url_for('checkout'))

@app.route('/order/<int:order_id>')
def order_detail(order_id):
    if not session.get('user_id') and not session.get('admin_logged_in'):
        return redirect(url_for('login'))
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        abort(404)
    if not session.get('admin_logged_in') and order['user_id'] != session.get('user_id'):
        conn.close()
        abort(403)
    items = conn.execute("SELECT b.title, oi.quantity, oi.price_at_purchase, oi.quantity * oi.price_at_purchase AS subtotal FROM order_items oi JOIN books b ON b.id = oi.book_id WHERE oi.order_id = ?", (order_id,)).fetchall()
    conn.close()
    return render_template_string("""<!doctype html><html><head><title>Order #{{ order.id }}</title><meta name='viewport' content='width=device-width, initial-scale=1'><style>body{font-family:Arial;max-width:760px;margin:30px auto;padding:20px;color:#1e293b}table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}.btn{display:inline-block;padding:10px 14px;background:#2563eb;color:white;text-decoration:none;border-radius:6px}</style></head><body><h1>Thank you for your order!</h1><h2>Order #{{ order.id }}</h2><p>Status: <strong>{{ order.status }}</strong></p><p>Payment method: <strong>{{ order.payment_method or 'Cash on Delivery' }}</strong></p><p>Delivery address: {{ order.customer_address }}</p>{% if order.gift_wrap %}<p><b>Gift wrap:</b> Yes (+₹30)</p>{% if order.gift_message %}<p><b>Gift message:</b> {{ order.gift_message }}</p>{% endif %}{% endif %}<table><tr><th>Book</th><th>Qty</th><th>Price</th><th>Subtotal</th></tr>{% for item in items %}<tr><td>{{ item.title }}</td><td>{{ item.quantity }}</td><td>₹{{ item.price_at_purchase|inr }}</td><td>₹{{ item.subtotal|inr }}</td></tr>{% endfor %}</table><h2>Total: ₹{{ order.total_amount|inr }}</h2><a class='btn' href='/order/{{ order.id }}/invoice'>Printable invoice</a> <a href='/orders'>My orders</a></body></html>""", order=order, items=items)

@app.route('/orders')
def orders():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders ORDER BY id DESC")
    user_orders = cursor.fetchall()
    conn.close()

    orders_html = ""
    for o in user_orders:
        total_str = format_inr(o['total_amount'])
        status = o['status'] or 'Placed'
        discount_val = o['discount_amount'] or 0

        if status == 'Cancelled':
            badge_style = "background: #fee2e2; color: #991b1b;"
        elif status == 'Delivered':
            badge_style = "background: #d1fae5; color: #065f46;"
        else:
            badge_style = "background: #e0f2fe; color: #0369a1;"

        action_buttons = f'<a href="/order/track/{o["id"]}" style="background: #800000; color: white; padding: 10px 18px; border-radius: 8px; font-size: 15px; text-decoration: none; font-weight: 700; display: inline-flex; align-items: center; gap: 6px;">📦 Track Order</a>'
        
        if status == 'Delivered':
            action_buttons += f' <a href="/order/refund/{o["id"]}" style="background: #fef08a; color: #854d0e; padding: 10px 18px; border-radius: 8px; font-size: 15px; text-decoration: none; font-weight: 700; display: inline-block;">Refund Details</a>'
        elif status in ['Placed', 'Packed', 'Processing']:
            action_buttons += f' <a href="/order/cancel/{o["id"]}" style="background: #ef4444; color: white; padding: 10px 18px; border-radius: 8px; font-size: 15px; text-decoration: none; font-weight: 700; display: inline-block;">Cancel Order</a>'
        else:
            action_buttons += f' <a href="/order/refund/{o["id"]}" style="background: #fef08a; color: #854d0e; padding: 10px 18px; border-radius: 8px; font-size: 15px; text-decoration: none; font-weight: 700; display: inline-block;">Refund Details</a>'

        orders_html += f"""
        <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.02);">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
                <div>
                    <h3 style="margin: 0 0 6px 0; font-size: 20px; font-weight: 800; color: #0f172a;">Order #{o['id']}</h3>
                    <span style="padding: 4px 12px; border-radius: 20px; font-size: 15px; font-weight: 700; display: inline-block; {badge_style}">{status}</span>
                </div>
                <div style="text-align: right;">
                    <div style="font-size: 22px; font-weight: 800; color: #ff5e62;">Rs. {total_str}</div>
                    <div style="font-size: 15px; color: #64748b;">Discount: Rs. {discount_val}</div>
                </div>
            </div>

            <hr style="border: 0; border-top: 1px solid #f1f5f9; margin: 16px 0;">

            <div style="font-size: 15px; color: #334155; margin-bottom: 20px;">
                <b>Shipping Address:</b> {o['customer_address'] or 'N/A'}
            </div>

            <div style="display: flex; gap: 12px; align-items: center;">
                {action_buttons}
            </div>
        </div>
        """

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>My Orders</title></head>
    <body style="font-family: 'Segoe UI', sans-serif; padding: 40px; background: #f8fafc;">
        <div style="max-width: 850px; margin: auto;">
            <h2 style="font-size: 26px; font-weight: 800; color: #0f172a; margin-bottom: 20px;">📦 My Orders</h2>
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}{% for cat, msg in messages %}<div style="padding: 12px; background: #d1fae5; color: #065f46; border-radius: 8px; margin-bottom: 20px; font-weight:600;">{{ msg }}</div>{% endfor %}{% endif %}
            {% endwith %}
            {{ orders_html | safe if orders_html else "<p style='color:#64748b;'>No orders placed yet.</p>" }}
            <a href="/" style="text-decoration: none; color: #2563eb; font-weight: bold; display: inline-block; margin-top: 15px;">← Back to Storefront</a>
        </div>
    </body>
    </html>
    """, orders_html=orders_html)

@app.route('/order/cancel/<int:order_id>')
def cancel_order(order_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
    ord_rec = cursor.fetchone()
    if ord_rec and ord_rec['status'] in ['Placed', 'Packed', 'Processing']:
        cursor.execute("UPDATE orders SET status = 'Cancelled' WHERE id = ?", (order_id,))
        conn.commit()
        flash(f"Order #{order_id} has been cancelled successfully.", "success")
    else:
        flash("This order cannot be cancelled at its current status.", "danger")
    conn.close()
    return redirect(url_for('orders'))

@app.route('/order/refund/<int:order_id>')
def request_refund(order_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
    ord_rec = cursor.fetchone()
    if ord_rec and ord_rec['status'] == 'Delivered':
        cursor.execute("UPDATE orders SET status = 'Refund Requested' WHERE id = ?", (order_id,))
        conn.commit()
        flash(f"Refund requested successfully for Order #{order_id}.", "success")
    else:
        flash("Refund can only be requested for delivered orders.", "danger")
    conn.close()
    return redirect(url_for('orders'))

@app.route('/order/track/<int:order_id>')
def track_order(order_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    conn.close()
    if not order:
        return redirect(url_for('orders'))
    
    current_status = order['status']
    steps = ['Placed', 'Processing', 'Packed', 'Shipped', 'Delivered']
    
    status_indices = {'Placed': 0, 'Processing': 1, 'Packed': 2, 'Shipped': 3, 'Delivered': 4, 'Refund Requested': 4, 'Refunded': 4, 'Cancelled': -1}
    current_idx = status_indices.get(current_status, 0)

    steps_html = ""
    for idx, step in enumerate(steps):
        is_completed = idx <= current_idx and current_idx != -1
        bg_color = "#2563eb" if is_completed else "#cbd5e1"
        text_color = "#0f172a" if is_completed else "#94a3b8"
        steps_html += f"""
        <div style="display: flex; align-items: center; margin-bottom: 20px;">
            <div style="width: 32px; height: 32px; border-radius: 50%; background: {bg_color}; color: white; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 14px; margin-right: 14px;">{idx+1}</div>
            <div>
                <div style="font-weight: bold; color: {text_color}; font-size: 15px;">{step}</div>
                <div style="font-size: 14px; color: #64748b;">Status milestone for order fulfillment</div>
            </div>
        </div>
        """

    if current_status == 'Cancelled':
        steps_html = "<div style='color: #ef4444; font-weight: bold; font-size: 16px; padding: 20px 0;'>❌ This order has been cancelled.</div>"
    elif current_status in ['Refund Requested', 'Refunded']:
        steps_html += f"<div style='color: #d97706; font-weight: bold; margin-top: 15px;'>⚠️ Status Update: {current_status}</div>"

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Interactive Status Map - Order #{{ order.id }}</title></head>
    <body style="font-family: sans-serif; padding: 40px; background: #f8fafc;">
        <div style="max-width: 500px; background: white; padding: 35px; border-radius: 12px; margin: auto; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
            <h3 style="margin-top: 0;">📍 Interactive Order Map</h3>
            <p style="color: #64748b; font-size: 15px; margin-bottom: 24px;">Order ID: <b>#{{ order.id }}</b> | Customer: <b>{{ order.customer_name }}</b></p>
            
            <div style="border-left: 2px dashed #e2e8f0; margin-left: 15px; padding-left: 10px;">
                {{ steps_html | safe }}
            </div>

            <a href="/orders" style="display: block; text-align: center; margin-top: 30px; text-decoration: none; color: white; background: #181920; padding: 10px; border-radius: 6px; font-weight: bold;">← Back to My Orders</a>
        </div>
    </body>
    </html>
    """, order=order, steps_html=steps_html)

@app.route('/addresses', methods=['GET', 'POST'])
def addresses():
    conn = get_db_connection()
    cursor = conn.cursor()
    u_id = session.get('user_id', 1)
    
    if request.method == 'POST':
        label = request.form.get('label', 'Home').strip()
        line1 = request.form.get('line1', '').strip()
        city = request.form.get('city', '').strip()
        pincode = request.form.get('pincode', '').strip()
        if line1 and city and pincode:
            cursor.execute("INSERT INTO addresses (user_id, label, line1, city, pincode) VALUES (?, ?, ?, ?, ?)", (u_id, label, line1, city, pincode))
            conn.commit()
            flash("Address added successfully!", "success")
        else:
            flash("Please fill in all address fields.", "danger")
        return redirect(url_for('addresses'))

    cursor.execute("SELECT * FROM addresses WHERE user_id = ?", (u_id,))
    user_addresses = cursor.fetchall()
    conn.close()

    addr_html = ""
    for a in user_addresses:
        addr_html += f"""
        <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; margin-bottom: 10px;">
            <div style="font-weight:bold; color:#0f172a;">{a['label']}</div>
            <div style="font-size:13px; color:#475569; margin-top:4px;">{a['line1']}, {a['city']} - {a['pincode']}</div>
        </div>
        """

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>My Addresses</title></head>
    <body style="font-family: sans-serif; padding: 40px; background: #f8fafc;">
        <div style="max-width: 600px; background: white; padding: 30px; border-radius: 10px; margin: auto; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
            <h3>📍 Saved Addresses</h3>
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}{% for cat, msg in messages %}<div style="padding: 10px; background: #d1fae5; color: #065f46; border-radius: 6px; margin-bottom: 15px;">{{ msg }}</div>{% endfor %}{% endif %}
            {% endwith %}
            {{ addr_html | safe if addr_html else "<p style='color: #64748b;'>No addresses saved yet.</p>" }}
            
            <h4 style="margin-top:24px;">Add New Address</h4>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div style="margin-bottom:10px;"><label style="font-weight:bold; font-size:13px;">Label (e.g. Home, Office)</label><input type="text" name="label" value="Home" required style="width:100%; padding:8px; border:1px solid #cbd5e1; border-radius:4px; box-sizing:border-box;"></div>
                <div style="margin-bottom:10px;"><label style="font-weight:bold; font-size:13px;">Address Line</label><input type="text" name="line1" required style="width:100%; padding:8px; border:1px solid #cbd5e1; border-radius:4px; box-sizing:border-box;"></div>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:14px;">
                    <div><label style="font-weight:bold; font-size:13px;">City</label><input type="text" name="city" required style="width:100%; padding:8px; border:1px solid #cbd5e1; border-radius:4px; box-sizing:border-box;"></div>
                    <div><label style="font-weight:bold; font-size:13px;">Pincode</label><input type="text" name="pincode" required style="width:100%; padding:8px; border:1px solid #cbd5e1; border-radius:4px; box-sizing:border-box;"></div>
                </div>
                <button type="submit" style="background:#2563eb; color:white; border:none; padding:10px 16px; border-radius:6px; font-weight:bold; cursor:pointer;">Save Address</button>
            </form>
            <a href="/" style="display: block; text-align: center; margin-top: 20px; color: #2563eb; font-weight: bold; text-decoration: none;">← Back to Storefront</a>
        </div>
    </body>
    </html>
    """, addr_html=addr_html)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username == 'admin' and password == 'admin123':
            session['admin_logged_in'] = True
            session['staff_role'] = 'admin'
            flash("Welcome Admin!", "success")
            return redirect(url_for('admin_dashboard'))
        elif username.lower() == 'staff' and password in {'staff123', '123'}:
            session['admin_logged_in'] = True
            session['staff_role'] = 'staff'
            flash("Welcome Staff!", "success")
            return redirect(url_for('admin_dashboard'))
        else:
            flash("Invalid credentials.", "danger")
            return redirect(url_for('admin_login'))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Admin / Staff Login</title></head>
    <body style="font-family: 'Segoe UI', sans-serif; background: #181920; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0;">
        <div style="background: #ffffff; padding: 35px; border-radius: 12px; width: 360px; box-shadow: 0 10px 25px rgba(0,0,0,0.3);">
            <h3 style="text-align: center; margin-top: 0; color: #181920; font-size: 22px;">PageTurner Admin Login</h3>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div style="margin-bottom: 14px;"><label style="font-weight: 600; display: block; margin-bottom: 6px; font-size: 15px; color: #475569;">Username</label><input type="text" name="username" required style="width: 100%; padding: 10px; border: 1px solid #cbd5e1; border-radius: 6px; box-sizing: border-box;"></div>
                <div style="margin-bottom: 20px;"><label style="font-weight: 600; display: block; margin-bottom: 6px; font-size: 15px; color: #475569;">Password</label><input type="password" name="password" required style="width: 100%; padding: 10px; border: 1px solid #cbd5e1; border-radius: 6px; box-sizing: border-box;"></div>
                <button type="submit" style="width: 100%; background: #2563eb; color: white; padding: 11px; font-weight: bold; border: none; border-radius: 6px; cursor: pointer; font-size: 14px;">Login</button>
            </form>
            <a href="/" style="display: block; text-align: center; margin-top: 16px; color: #64748b; text-decoration: none; font-size: 15px;">← Return to Storefront</a>
        </div>
    </body>
    </html>
    """)

@app.route('/admin/dashboard', methods=['GET', 'POST'])
@role_required('admin', 'staff')
@csrf_required
def admin_dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action', 'update_status')
        if action == 'create_coupon':
            if session.get('staff_role') != 'admin':
                conn.close()
                abort(403)
            code = request.form.get('code', '').strip().upper()
            discount_type = request.form.get('discount_type', 'percentage').strip()
            raw_value = request.form.get('discount_value', '0').strip()
            try:
                discount_value = int(raw_value)
                if not code or not re.fullmatch(r'[A-Z0-9_-]{3,24}', code):
                    raise ValueError
                if discount_type not in {'percentage', 'fixed'} or discount_value <= 0:
                    raise ValueError
                if discount_type == 'percentage' and discount_value > 100:
                    raise ValueError
                if discount_type == 'fixed':
                    discount_value *= 100  # Store fixed discounts in paise.
                cursor.execute("INSERT INTO coupons (code, discount_type, discount_value, is_active) VALUES (?, ?, ?, 1)", (code, discount_type, discount_value))
                conn.commit()
                flash(f'Coupon {code} created successfully.', 'success')
            except ValueError:
                flash('Enter a valid coupon code and discount value.', 'danger')
            except sqlite3.IntegrityError:
                flash('That coupon code already exists.', 'warning')
        else:
            order_id = request.form.get('order_id')
            new_status = request.form.get('status')
            if order_id and new_status:
                cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
                conn.commit()
                flash("Order status updated successfully!", "success")

    cursor.execute("SELECT * FROM orders ORDER BY id DESC")
    orders = cursor.fetchall()
    conn.close()

    orders_rows = ""
    for o in orders:
        total_str = format_inr(o['total_amount'])
        status = o['status'] or 'Placed'
        
        badge_bg = '#2563eb'
        if status == 'Cancelled': badge_bg = '#1d4ed8'
        elif status in ['Processing', 'Packed', 'Refund Requested']: badge_bg = '#d97706'
        elif status in ['Delivered', 'Refunded']: badge_bg = '#059669'

        orders_rows += f"""
        <tr style="border-bottom: 1px solid #e2e8f0; font-size: 15px; color: #334155;">
            <td style="padding: 14px; font-weight: 700;">#{o['id']}</td>
            <td style="padding: 14px;">
                <div style="font-weight: 700; color: #0f172a;">{o['customer_name'] or 'Customer'}</div>
                <div style="font-size: 14px; color: #64748b;">{o['customer_email'] or ''}</div>
                <div style="font-size: 14px; color: #64748b;">{o['customer_phone'] or ''}</div>
            </td>
            <td style="padding: 14px; max-width: 280px; font-size: 14px; color: #64748b; line-height: 1.4;">
                {o['customer_address'] or 'N/A'}
            </td>
            <td style="padding: 14px; font-weight: 800; color: #2563eb;">₹{total_str}</td>
            <td style="padding: 14px; font-size: 15px; color: #64748b;">{o['created_at'] or ''}</td>
            <td style="padding: 14px;">
                <span style="background: {badge_bg}; color: white; padding: 4px 10px; border-radius: 6px; font-size: 14px; font-weight: 700; display: inline-block;">{status}</span>
            </td>
            <td style="padding: 14px;">
                <form method="POST" style="display: flex; gap: 4px; margin: 0; align-items: center;">
                    <input type="hidden" name="csrf_token" value="{session.get('csrf_token', '')}">
                    <input type="hidden" name="order_id" value="{o['id']}">
                    <select name="status" style="padding: 6px; border-radius: 6px; border: 1px solid #cbd5e1; font-size: 15px; outline: none;">
                        <option value="Placed" {'selected' if status=='Placed' else ''}>Placed</option>
                        <option value="Processing" {'selected' if status=='Processing' else ''}>Processing</option>
                        <option value="Shipped" {'selected' if status=='Shipped' else ''}>Shipped</option>
                        <option value="Delivered" {'selected' if status=='Delivered' else ''}>Delivered</option>
                        <option value="Refund Requested" {'selected' if status=='Refund Requested' else ''}>Refund Requested</option>
                        <option value="Refunded" {'selected' if status=='Refunded' else ''}>Refunded</option>
                        <option value="Cancelled" {'selected' if status=='Cancelled' else ''}>Cancelled</option>
                    </select>
                    <button type="submit" style="background: #2563eb; color: white; border: none; padding: 6px 10px; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 15px;">✓</button>
                </form>
            </td>
        </tr>
        """

    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>PageTurner Admin - Dashboard</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        <style>
            * { box-sizing: border-box; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; background: #e2e8f0; display: flex; }
            .sidebar { width: 210px; background: #181920; color: #94a3b8; min-height: 100vh; padding: 20px 0; display: flex; flex-direction: column; }
            .brand { color: #f59e0b; font-size: 20px; font-weight: 800; padding: 0 20px 24px; text-decoration: none; display: flex; align-items: center; gap: 8px; }
            .nav-item { padding: 12px 20px; color: #94a3b8; text-decoration: none; font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 10px; transition: 0.2s; }
            .nav-item:hover { color: #ffffff; }
            .nav-item.active { background: #2563eb; color: #ffffff; border-radius: 0 20px 20px 0; margin-right: 15px; }
            .nav-sub { font-size: 15px; color: #64748b; padding: 20px 20px 8px; font-weight: 700; text-transform: uppercase; }
            .main-content { flex: 1; min-width:0; width:calc(100% - 210px); padding:clamp(24px,3vw,48px); background:#f1f5f9; min-height:100vh; font-size:17px; }
            @media(max-width:900px){body{display:block}.sidebar{width:100%;min-height:auto}.main-content{width:100%;padding:20px}.header-bar{align-items:flex-start;flex-direction:column;gap:16px}.data-table{display:block;overflow-x:auto}}
            .header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
            .btn-action { padding: 8px 14px; border-radius: 6px; text-decoration: none; font-size: 15px; font-weight: 700; display: inline-flex; align-items: center; gap: 6px; color: white; border: none; cursor: pointer; }
            .btn-amber { background: #f59e0b; color: #000; }
            .btn-blue { background: #2563eb; }
            .btn-emerald { background: #059669; }
            .data-table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
            .data-table th { background: #181920; color: white; text-align: left; padding: 12px 14px; font-size: 15px; font-weight: 700; }
        </style>
    </head>
    <body>
        <div class="sidebar">
            <a href="/" class="brand">PageTurner</a>
            <a href="/admin/dashboard" class="nav-item active"><i class="fa-solid fa-gauge"></i> Dashboard</a>
            <a href="/admin/inventory" class="nav-item"><i class="fa-solid fa-boxes-stacked"></i> Inventory</a>
            <a href="/admin/coupons" class="nav-item"><i class="fa-solid fa-ticket"></i> Coupons</a>
            <a href="/admin/analytics" class="nav-item"><i class="fa-solid fa-chart-simple"></i> Analytics</a>
            <a href="/admin/reviews" class="nav-item"><i class="fa-solid fa-comment"></i> Reviews</a><a href="/admin/requests-reviews" class="nav-item">📬 Book Requests & Moderation</a>
            
            <div class="nav-sub">Storefront</div>
            <a href="/" class="nav-item"><i class="fa-solid fa-shop"></i> Storefront</a>
            
            <div style="margin-top: auto; padding: 20px;">
                <a href="/logout" style="color: #ef4444; text-decoration: none; font-size: 15px; font-weight: bold;"><i class="fa-solid fa-right-from-bracket"></i> Logout Admin</a>
            </div>
        </div>

        <div class="main-content">
            <div class="header-bar">
                <div>
                    <h2 style="margin: 0 0 4px 0; font-size: 24px; font-weight: 800; color: #0f172a;">Interactive Order Management</h2>
                    <p style="margin: 0; color: #64748b; font-size: 15px;">Track customer orders, manage shipping status, and view sales insights.</p>
                </div>
                <div style="display: flex; gap: 10px;">
                    <a href="/admin/inventory" class="btn-action btn-amber"><i class="fa-solid fa-boxes-stacked"></i> Inventory Control</a>
                    <a href="/admin/analytics" class="btn-action btn-blue"><i class="fa-solid fa-chart-simple"></i> Sales Analytics</a><a href="/admin/requests-reviews" class="btn-action btn-blue">📬 Requests & Review Reports</a>
                    <a href="/admin/export_orders" class="btn-action btn-emerald"><i class="fa-solid fa-file-csv"></i> Export Orders CSV</a>
                </div>
            </div>

            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}
                {% for cat, msg in messages %}
                  <div style="padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; background: #d1fae5; color: #065f46; font-size: 15px; font-weight: 600;">{{ msg }}</div>
                {% endfor %}
              {% endif %}
            {% endwith %}

            {% if session.get('staff_role') == 'admin' %}
            <section style="background:#fff;border-radius:14px;padding:24px;margin:0 0 24px;box-shadow:0 4px 18px #1720330d;">
                <h3 style="margin:0 0 14px;font-size:22px;">🏷️ Create promotional coupon</h3>
                <form method="POST" action="/admin/dashboard" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;align-items:end;">
                    <input type="hidden" name="csrf_token" value="{{ session.get('csrf_token','') }}">
                    <input type="hidden" name="action" value="create_coupon">
                    <label style="font-size:16px;font-weight:650;">Coupon code<input name="code" required minlength="3" maxlength="24" placeholder="e.g. BOOKS15" style="display:block;width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:8px;margin-top:6px;text-transform:uppercase;"></label>
                    <label style="font-size:16px;font-weight:650;">Discount type<select name="discount_type" style="display:block;width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:8px;margin-top:6px;"><option value="percentage">Percentage (%)</option><option value="fixed">Fixed amount (₹)</option></select></label>
                    <label style="font-size:16px;font-weight:650;">Discount value<input name="discount_value" type="number" min="1" required placeholder="15" style="display:block;width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:8px;margin-top:6px;"></label>
                    <button type="submit" class="btn-action btn-blue" style="padding:13px 18px;font-size:16px;justify-content:center;">＋ Create coupon</button>
                </form>
                <p style="color:#64748b;margin:12px 0 0;font-size:15px;">Percentage discounts accept 1–100. Fixed discounts are entered in rupees.</p><script>document.querySelector("select[name=discount_type]").addEventListener("change",function(){const field=document.querySelector("input[name=discount_value]");if(this.value==="percentage"){field.max="100";field.placeholder="15";}else{field.removeAttribute("max");field.placeholder="100";}});</script>
            </section>
            {% endif %}
            <table class="data-table">
                <thead>
                    <tr>
                        <th>Order #</th>
                        <th>Customer Details</th>
                        <th>Address</th>
                        <th>Amount</th>
                        <th>Date & Time</th>
                        <th>Status</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    {{ orders_rows | safe if orders_rows else "<tr><td colspan='7' style='padding: 20px; text-align: center; color: #64748b;'>No orders found.</td></tr>" }}
                </tbody>
            </table>
        </div>
    </body>
    </html>
    """, orders_rows=orders_rows)

@app.route('/admin/export_orders')
@app.route('/admin/export-csv')
@app.route('/admin/export_orders.csv')
@role_required('admin')
def export_orders():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders ORDER BY id DESC")
    orders = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Order ID', 'Customer Name', 'Customer Email', 'Phone', 'Address', 'Total Amount', 'Status', 'Date'])

    for o in orders:
        writer.writerow([o['id'], o['customer_name'], o['customer_email'], o['customer_phone'], o['customer_address'], f"{o['total_amount']//100}", o['status'], o['created_at']])

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=orders_export.csv"
    response.headers["Content-type"] = "text/csv"
    return response

@app.route('/admin/inventory', methods=['GET', 'POST'])
@app.route('/admin/inventory-control', methods=['GET', 'POST'])
@role_required('admin')
@csrf_required
def admin_inventory():
    conn = get_db_connection()
    cursor = conn.cursor()
    edit_id = request.args.get('edit', type=int)
    if request.method == 'POST':
        action = request.form.get('action', 'add')
        try:
            title = request.form.get('title', '').strip()
            author = request.form.get('author', '').strip()
            price_rupees = float(request.form.get('price', '0'))
            stock = int(request.form.get('stock', '0'))
            category = request.form.get('category', 'Fiction').strip()
            image_url = request.form.get('image_url', '').strip()
            release_date = request.form.get('release_date', '').strip() or None
            sale_price_raw = request.form.get('sale_price_rupees', '').strip()
            sale_price_paise = int(round(float(sale_price_raw) * 100)) if sale_price_raw else None
            sale_start = request.form.get('sale_start', '').strip().replace('T', ' ') or None
            sale_end = request.form.get('sale_end', '').strip().replace('T', ' ') or None
            book_id = request.form.get('book_id', type=int)
            if not title or not author or price_rupees < 0 or stock < 0:
                raise ValueError
            if not image_url:
                image_seed = re.sub(r'[^a-zA-Z0-9]+', '-', title).strip('-').lower() or 'book'
                image_url = f'https://placehold.co/600x800/f3f4f6/374151?text={image_seed}'
            if not (image_url.startswith('https://') or image_url.startswith('http://')):
                flash('Please enter a valid image URL starting with http:// or https://.', 'danger')
                conn.close()
                return redirect(url_for('admin_inventory', edit=book_id) if action == 'update' and book_id else url_for('admin_inventory'))
            if action == 'update' and book_id:
                cursor.execute("UPDATE books SET title=?, author=?, price=?, stock=?, category=?, image_url=?, release_date=?, sale_price_paise=?, sale_start=?, sale_end=? WHERE id=?", (title, author, int(round(price_rupees*100)), stock, category, image_url, release_date, sale_price_paise, sale_start, sale_end, book_id))
                flash('Book details and cover image updated successfully.', 'success')
            else:
                cursor.execute("INSERT INTO books (title, author, price, stock, category, image_url, release_date, sale_price_paise, sale_start, sale_end) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (title, author, int(round(price_rupees*100)), stock, category, image_url, release_date, sale_price_paise, sale_start, sale_end))
                flash('Book added successfully!', 'success')
            conn.commit()
            conn.close()
            return redirect(url_for('admin_inventory'))
        except (TypeError, ValueError):
            flash('Please enter a valid title, author, price, and stock quantity.', 'danger')
    edit_book = cursor.execute("SELECT * FROM books WHERE id=?", (edit_id,)).fetchone() if edit_id else None
    books = cursor.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
    conn.close()
    book_rows = []
    for b in books:
        book_rows.append({'id': b['id'], 'title': b['title'], 'author': b['author'], 'price': int(b['price']), 'stock': b['stock'], 'category': b['category'], 'image_url': b['image_url']})
    return render_template_string("""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Inventory Control | PageTurner</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f1f5f9;color:#172033;font:16px/1.5 system-ui,sans-serif}.sidebar{width:220px;flex-shrink:0;background:#181920;color:white;min-height:100vh;padding:22px 0}.sidebar a{display:block;padding:12px 20px;color:#cbd5e1;text-decoration:none}.sidebar a.active{background:#2563eb;color:white}.main{flex:1;min-width:0;padding:clamp(20px,4vw,55px)}.panel{background:white;border-radius:16px;padding:22px;margin:20px 0;box-shadow:0 8px 26px #1720330a}.formgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}.formgrid label{font-size:14px;font-weight:750;color:#475569}.formgrid input{display:block;width:100%;padding:12px;margin-top:6px;border:1px solid #cbd5e1;border-radius:9px;font:inherit}.btn{border:0;border-radius:9px;padding:11px 16px;background:#2563eb;color:white;font-weight:750;text-decoration:none;display:inline-block;cursor:pointer}.btn.secondary{background:#e2e8f0;color:#172033}.btn.green{background:#059669}.flash{padding:12px 16px;border-radius:10px;background:#dcfce7;color:#166534;margin:14px 0}.flash.danger{background:#ffe4e6;color:#9f1239}.tablewrap{overflow:auto}table{border-collapse:collapse;width:100%;min-width:800px}th,td{text-align:left;padding:12px;border-bottom:1px solid #edf0f5}th{background:#f8fafc;color:#475569;font-size:13px;text-transform:uppercase}td img{width:54px;height:68px;object-fit:cover;border-radius:6px;background:#f1f5f9}.actions{display:flex;gap:8px;flex-wrap:wrap}.preview{display:flex;align-items:center;gap:12px;margin-top:10px}.preview img{width:66px;height:86px;object-fit:cover;border-radius:8px;border:1px solid #e2e8f0}@media(max-width:780px){body{display:block!important}.sidebar{width:100%;min-height:auto;padding:10px 0}.main{padding:18px}.formgrid{grid-template-columns:1fr}.panel{padding:15px}}
    </style></head><body style="display:flex;min-height:100vh"><aside class="sidebar"><a href="/" style="font-size:22px;font-weight:850;color:#fbbf24">📚 PageTurner</a><a href="/admin/dashboard">Dashboard</a><a class="active" href="/admin/inventory">Inventory Control</a><a href="/admin/analytics">Sales Analytics</a><a href="/">Storefront</a><a href="/admin/logout" style="color:#fca5a5">Logout</a></aside><main class="main"><h1 style="font-size:clamp(28px,3vw,40px);margin:0">Inventory control</h1><p style="color:#64748b">Add new books or edit an existing book to correct its title, price, stock, category, or cover image.</p>{% with messages=get_flashed_messages(with_categories=true) %}{% for category,message in messages %}<div class="flash {% if category in ['danger','error'] %}danger{% endif %}" role="status">{{ message }}</div>{% endfor %}{% endwith %}<section class="panel"><h2 style="margin-top:0">{% if edit_book %}✏️ Edit book #{{ edit_book.id }}{% else %}＋ Add a new book{% endif %}</h2><form method="POST" class="formgrid"><input type="hidden" name="csrf_token" value="{{ session.csrf_token }}"><input type="hidden" name="action" value="{% if edit_book %}update{% else %}add{% endif %}">{% if edit_book %}<input type="hidden" name="book_id" value="{{ edit_book.id }}">{% endif %}<label>Book title<input name="title" required placeholder="Book title" value="{{ edit_book.title if edit_book else '' }}"></label><label>Author<input name="author" required placeholder="Author name" value="{{ edit_book.author if edit_book else '' }}"></label><label>Price (₹)<input type="number" name="price" min="0" step="0.01" required value="{{ '%.2f'|format(edit_book.price/100) if edit_book else '' }}"></label><label>Stock quantity<input type="number" name="stock" min="0" required value="{{ edit_book.stock if edit_book else 10 }}"></label><label>Category<input name="category" required value="{{ edit_book.category if edit_book else 'Fiction' }}"></label><label>Release date<input type="date" name="release_date" value="{{ edit_book.release_date if edit_book and edit_book.release_date else '' }}"><small>Future dates show Coming soon.</small></label><label>Flash sale price (₹)<input type="number" name="sale_price_rupees" min="0" step="0.01" value="{{ '%.2f'|format(edit_book.sale_price_paise/100) if edit_book and edit_book.sale_price_paise is not none else '' }}"></label><label>Sale starts<input type="datetime-local" name="sale_start" value="{{ edit_book.sale_start.replace(' ', 'T') if edit_book and edit_book.sale_start else '' }}"></label><label>Sale ends<input type="datetime-local" name="sale_end" value="{{ edit_book.sale_end.replace(' ', 'T') if edit_book and edit_book.sale_end else '' }}"></label><label>Book cover image URL<input id="imageUrl" type="url" name="image_url" placeholder="https://.../book-cover.jpg" value="{{ edit_book.image_url if edit_book else '' }}"><small style="display:block;font-weight:400;color:#64748b;margin-top:5px">Paste the correct direct image URL. You can update it anytime.</small></label><div class="preview"><img id="coverPreview" src="{{ edit_book.image_url if edit_book else 'https://placehold.co/600x800/f3f4f6/374151?text=Book+Cover' }}" alt="Cover preview" onerror="this.src='https://placehold.co/100x130/f3f4f6/374151?text=Preview'"><span style="color:#64748b;font-size:13px">Cover preview</span></div><div style="display:flex;gap:10px;align-items:end;flex-wrap:wrap"><button class="btn" type="submit">{% if edit_book %}Save changes{% else %}Add book to store{% endif %}</button>{% if edit_book %}<a class="btn secondary" href="/admin/inventory">Cancel edit</a>{% endif %}</div></form></section><section class="panel"><h2 style="margin-top:0">📚 All books <span style="color:#64748b;font-size:15px;font-weight:500">({{ book_rows|length }} titles)</span></h2><div class="tablewrap"><table><thead><tr><th>Cover</th><th>ID</th><th>Title</th><th>Author</th><th>Price</th><th>Stock</th><th>Category</th><th>Action</th></tr></thead><tbody>{% for book in book_rows %}<tr><td><img src="{{ book.image_url }}" alt="{{ book.title }} cover" onerror="this.onerror=null;this.src='https://placehold.co/54x68/f3f4f6/374151?text=Book'"></td><td>{{ book.id }}</td><td><b>{{ book.title }}</b></td><td>{{ book.author }}</td><td>₹{{ book.price|inr }}</td><td>{{ book.stock }}</td><td>{{ book.category }}</td><td><div class="actions"><a class="btn" href="/admin/inventory?edit={{ book.id }}">✏️ Edit</a><a class="btn green" href="/?book={{ book.id }}">View</a></div></td></tr>{% else %}<tr><td colspan="8">No books found yet.</td></tr>{% endfor %}</tbody></table></div></section></main><script>const urlInput=document.getElementById('imageUrl'),preview=document.getElementById('coverPreview');urlInput.addEventListener('input',()=>{if(urlInput.value.trim())preview.src=urlInput.value.trim()});</script></body></html>""", book_rows=book_rows, edit_book=edit_book)

@app.route('/admin/coupons', methods=['GET', 'POST'])
@role_required('admin', 'staff')
@csrf_required
def admin_coupons():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        if session.get('staff_role') != 'admin':
            conn.close()
            abort(403)
        coupon_id = request.form.get('coupon_id', type=int)
        action = request.form.get('action', 'update')
        if action == 'toggle' and coupon_id:
            cursor.execute('UPDATE coupons SET is_active=CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?', (coupon_id,))
            conn.commit()
            flash('Coupon status updated.', 'success')
        elif coupon_id:
            code = request.form.get('code', '').strip().upper()
            dtype = request.form.get('discount_type', 'percentage')
            try:
                value = int(request.form.get('discount_value', '0'))
                if not re.fullmatch(r'[A-Z0-9_-]{3,24}', code) or dtype not in {'percentage', 'fixed'} or value <= 0 or (dtype == 'percentage' and value > 100):
                    raise ValueError
                value = value * 100 if dtype == 'fixed' else value
                cursor.execute('UPDATE coupons SET code=?, discount_type=?, discount_value=? WHERE id=?', (code, dtype, value, coupon_id))
                conn.commit()
                flash('Coupon updated successfully.', 'success')
            except ValueError:
                flash('Enter a valid coupon code and discount value.', 'danger')
            except sqlite3.IntegrityError:
                flash('That coupon code already exists.', 'danger')
        return redirect(url_for('admin_coupons'))
    cursor.execute('SELECT * FROM coupons ORDER BY id DESC')
    coupons = cursor.fetchall()
    edit_id = request.args.get('edit', type=int)
    edit_coupon = next((c for c in coupons if c['id'] == edit_id), None)
    conn.close()
    return render_template_string("""
    <!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Coupons | PageTurner</title>
    <style>*{box-sizing:border-box}body{margin:0;background:#f1f5f9;color:#172033;font:16px/1.5 system-ui,sans-serif}.wrap{max-width:1250px;margin:auto;padding:clamp(18px,4vw,48px)}.head{display:flex;justify-content:space-between;align-items:center;gap:15px;flex-wrap:wrap}.panel{background:white;padding:24px;border-radius:18px;box-shadow:0 8px 26px #1720330b;margin:22px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;align-items:end}label{font-weight:700;color:#475569}input,select{display:block;width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:10px;margin-top:6px;font:inherit}.btn{border:0;border-radius:10px;padding:10px 14px;background:#2563eb;color:white;text-decoration:none;display:inline-block;font-weight:750;cursor:pointer}.gray{background:#e2e8f0;color:#172033}.green{background:#059669}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:680px}th,td{text-align:left;padding:14px;border-bottom:1px solid #edf0f5}th{background:#f8fafc;color:#64748b;font-size:12px;text-transform:uppercase}.code{font-weight:850;letter-spacing:1px;color:#4338ca;background:#eef2ff;padding:7px 10px;border-radius:8px}.badge{padding:5px 9px;border-radius:999px;font-size:12px;font-weight:800}.on{background:#dcfce7;color:#166534}.off{background:#fee2e2;color:#991b1b}.msg{padding:12px;background:#dcfce7;color:#166534;border-radius:10px;margin:12px 0}</style></head><body><main class="wrap"><div class="head"><div><p><a href="/admin/dashboard">← Dashboard</a></p><h1 style="font-size:clamp(30px,4vw,42px);margin:0">🏷️ Coupons & promotions</h1><p style="color:#64748b">Manage discount codes and control whether they are available at checkout.</p></div><a class="btn gray" href="/">View storefront ↗</a></div>
    {% with messages=get_flashed_messages(with_categories=true) %}{% for cat,msg in messages %}<div class="msg">{{ msg }}</div>{% endfor %}{% endwith %}
    {% if session.get('staff_role') == 'admin' and edit_coupon %}<section class="panel"><h2 style="margin-top:0">✏️ Edit {{ edit_coupon.code }}</h2><form method="POST" class="grid"><input type="hidden" name="csrf_token" value="{{ session.csrf_token }}"><input type="hidden" name="coupon_id" value="{{ edit_coupon.id }}"><label>Coupon code<input name="code" required minlength="3" maxlength="24" value="{{ edit_coupon.code }}"></label><label>Discount type<select name="discount_type"><option value="percentage" {% if edit_coupon.discount_type=='percentage' %}selected{% endif %}>Percentage (%)</option><option value="fixed" {% if edit_coupon.discount_type=='fixed' %}selected{% endif %}>Fixed amount (₹)</option></select></label><label>Discount value<input name="discount_value" type="number" min="1" max="100" required value="{{ (edit_coupon.discount_value/100)|int if edit_coupon.discount_type=='fixed' else edit_coupon.discount_value }}"></label><button class="btn" type="submit">Save coupon</button><a class="btn gray" href="/admin/coupons">Cancel</a></form></section>{% endif %}
    <section class="panel"><h2 style="margin-top:0">Added coupons ({{ coupons|length }})</h2><div class="tablewrap"><table><thead><tr><th>Coupon</th><th>Discount</th><th>Status</th><th>Actions</th></tr></thead><tbody>{% for c in coupons %}<tr><td><span class="code">{{ c.code }}</span></td><td>{% if c.discount_type=='percentage' %}{{ c.discount_value }}% off{% else %}₹{{ (c.discount_value/100)|int }} off{% endif %}</td><td><span class="badge {{ 'on' if c.is_active else 'off' }}">{{ 'Active' if c.is_active else 'Paused' }}</span></td><td>{% if session.get('staff_role')=='admin' %}<a class="btn" href="/admin/coupons?edit={{ c.id }}">✏️ Edit</a> <form method="POST" style="display:inline"><input type="hidden" name="csrf_token" value="{{ session.csrf_token }}"><input type="hidden" name="coupon_id" value="{{ c.id }}"><button class="btn {{ 'gray' if c.is_active else 'green' }}" name="action" value="toggle">{{ 'Pause' if c.is_active else 'Activate' }}</button></form>{% else %}View only{% endif %}</td></tr>{% else %}<tr><td colspan="4">No coupons have been added yet.</td></tr>{% endfor %}</tbody></table></div></section></main></body></html>
    """, coupons=coupons, edit_coupon=edit_coupon)

@app.route('/admin/analytics')
@role_required('admin', 'staff')
def admin_analytics():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(total_amount) as rev, COUNT(*) as cnt FROM orders")
    res = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) AS book_count, COALESCE(SUM(stock),0) AS stock_units, COALESCE(SUM(price * stock),0) AS stock_value FROM books")
    inv = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) FROM books WHERE stock <= 3")
    low_stock = cursor.fetchone()[0]
    cursor.execute("SELECT substr(created_at,1,7) AS month, COUNT(*) AS orders_count, COALESCE(SUM(total_amount),0) AS revenue FROM orders GROUP BY substr(created_at,1,7) ORDER BY month DESC LIMIT 6")
    monthly = list(reversed([dict(r) for r in cursor.fetchall()]))
    cursor.execute("SELECT category, COUNT(*) AS titles, COALESCE(SUM(stock),0) AS units FROM books GROUP BY category ORDER BY titles DESC")
    category_stats = [dict(r) for r in cursor.fetchall()]
    conn.close()
    revenue = res['rev'] or 0
    count = res['cnt'] or 0
    aov = revenue / count if count > 0 else 0
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en">
    <head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sales & Inventory Analysis</title><style>*{box-sizing:border-box}body{font:18px/1.6 system-ui,Segoe UI,sans-serif;padding:clamp(18px,4vw,56px);margin:0;background:#f1f5f9;color:#172033}.wrap{width:100%;max-width:1600px;margin:auto}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:20px}.card{background:white;padding:26px;border-radius:16px;box-shadow:0 8px 24px #1720330d}.label{color:#64748b;font-size:16px}.value{font-size:clamp(25px,3vw,36px);font-weight:800;margin-top:6px}.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:26px}.actions a{padding:12px 18px;border-radius:9px;background:#2563eb;color:white;text-decoration:none;font-weight:700}@media(max-width:600px){body{font-size:16px}}</style></head>
    <body><main class="wrap"><p><a href="/admin/dashboard" style="color:#2563eb;text-decoration:none;font-weight:700;">← Back to dashboard</a></p><h1 style="font-size:clamp(30px,4vw,44px);margin-bottom:8px;">Sales & Inventory Analysis</h1><p style="color:#64748b;margin-top:0;margin-bottom:28px;">A clear overview of revenue, orders, stock levels and inventory value.</p>
      <section class="cards"><article class="card"><div class="label">Total revenue</div><div class="value">₹{{ revenue | inr }}</div></article><article class="card"><div class="label">Total orders</div><div class="value">{{ count }}</div></article><article class="card"><div class="label">Average order value</div><div class="value">₹{{ aov | inr }}</div></article><article class="card"><div class="label">Book titles</div><div class="value">{{ inv.book_count }}</div></article><article class="card"><div class="label">Units in stock</div><div class="value">{{ inv.stock_units }}</div></article><article class="card"><div class="label">Inventory value</div><div class="value">₹{{ inv.stock_value | inr }}</div></article><article class="card"><div class="label">Low-stock titles (3 or fewer)</div><div class="value" style="color:{% if low_stock %}#b45309{% else %}#15803d{% endif %};">{{ low_stock }}</div></article></section>
      <section class="charts"><article class="card"><h2>Monthly sales</h2><div style="height:300px"><canvas id="salesChart"></canvas></div></article><article class="card"><h2>Book titles by category</h2><div style="height:300px"><canvas id="categoryChart"></canvas></div></article><article class="card"><h2>Stock units by category</h2><div style="height:300px"><canvas id="stockChart"></canvas></div></article></section>
      <div class="actions"><a href="/admin/inventory">📚 Manage inventory</a><a href="/admin/coupons">🏷️ Manage coupons</a><a href="/admin/export_orders">📥 Export orders CSV</a></div></main><script src="https://cdn.jsdelivr.net/npm/chart.js"></script><script>const m={{ monthly|tojson }},c={{ category_stats|tojson }};new Chart(document.getElementById('salesChart'),{type:'line',data:{labels:m.map(x=>x.month||'Unknown'),datasets:[{label:'Revenue (₹)',data:m.map(x=>x.revenue/100),borderColor:'#4f46e5',backgroundColor:'rgba(79,70,229,.12)',fill:true,tension:.3},{label:'Orders',data:m.map(x=>x.orders_count),borderColor:'#059669',tension:.3}]},options:{responsive:true,maintainAspectRatio:false}});new Chart(document.getElementById('categoryChart'),{type:'doughnut',data:{labels:c.map(x=>x.category),datasets:[{data:c.map(x=>x.titles),backgroundColor:['#6366f1','#ec4899','#14b8a6','#f59e0b','#8b5cf6','#0ea5e9','#f97316','#84cc16']}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom'}}}});new Chart(document.getElementById('stockChart'),{type:'bar',data:{labels:c.map(x=>x.category),datasets:[{label:'Units',data:c.map(x=>x.units),backgroundColor:'#0d9488'}]},options:{responsive:true,maintainAspectRatio:false,scales:{y:{beginAtZero:true}}}});</script><style>.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,400px),1fr));gap:20px;margin-top:24px}.charts .card{padding:24px;background:white;border-radius:16px;box-shadow:0 8px 24px #1720330d}</style></body></html>
    """, revenue=revenue, count=count, aov=aov, inv=inv, low_stock=low_stock, monthly=monthly, category_stats=category_stats)

@app.route('/admin/reviews')
@role_required('admin', 'staff')
def admin_reviews():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reviews ORDER BY id DESC")
    reviews = cursor.fetchall()
    conn.close()
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Customer Reviews Management</title></head>
    <body style="font-family: sans-serif; padding: 40px; background: #f8fafc;">
        <div style="max-width: 600px; background: white; padding: 30px; border-radius: 10px; margin: auto;">
            <h2>💬 Customer Reviews</h2>
            {% for r in reviews %}
            <div style="padding: 10px; border-bottom: 1px solid #e2e8f0;">
                <b>{{ r.name }}</b> on <i>{{ r.book_title }}</i> ({{ r.rating }} ⭐)
                <p style="margin: 4px 0 0 0; color: #475569;">"{{ r.comment }}"</p>
            </div>
            {% endfor %}
            <a href="/admin/dashboard" style="display: inline-block; margin-top: 20px; background: #2563eb; color: white; padding: 8px 16px; text-decoration: none; border-radius: 4px; font-weight: bold;">Back to Dashboard</a>
        </div>
    </body>
    </html>
    """, reviews=reviews)


@app.route('/book-requests', methods=['GET', 'POST'])
@csrf_required
def book_requests():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        author = request.form.get('author', '').strip()
        if not title or len(title) > 160 or len(author) > 120:
            flash('Enter a book title and an optional author (title max 160 characters).', 'danger')
            return redirect(url_for('book_requests'))
        conn = get_db_connection()
        uid = session.get('user_id')
        # One row per normalized title/author. Repeat requests increment the shared count.
        row = conn.execute("SELECT id FROM book_requests WHERE lower(trim(title))=lower(trim(?)) AND lower(trim(author))=lower(trim(?)) AND status='requested'", (title, author)).fetchone()
        if row:
            conn.execute("UPDATE book_requests SET request_count=request_count+1 WHERE id=?", (row['id'],))
        else:
            conn.execute("INSERT INTO book_requests(title,author,request_count,status,requested_by) VALUES(?,?,1,'requested',?)", (title, author, uid))
        conn.commit(); conn.close()
        flash('Book request submitted. The admin can review and approve or reject it.', 'success')
        return redirect(url_for('book_requests'))
    return render_template_string('''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Request a book</title><style>body{font:16px system-ui;background:#f5f7fb;color:#172033;margin:0}.wrap{max-width:900px;margin:35px auto;padding:20px}.panel{background:white;border-radius:14px;padding:22px;margin:16px 0}input,button{font:inherit;padding:11px;border-radius:8px;border:1px solid #cbd5e1}button{background:#2563eb;color:white;border:0}</style></head><body><main class="wrap"><p><a href="/">← Storefront</a> · <a href="/admin/dashboard">Admin dashboard</a></p><h1>📚 Request a book</h1><section class="panel"><form method="post"><input type="hidden" name="csrf_token" value="{{ session.csrf_token }}"><label>Book title <input name="title" maxlength="160" required></label> <label>Author (optional) <input name="author" maxlength="120"></label> <button>Submit request</button></form></section><p>Requests for the same title and author are grouped so the admin can prioritize popular requests.</p></main></body></html>''')

@app.route('/review/report/<int:review_id>', methods=['POST'])
@csrf_required
def report_review(review_id):
    uid = session.get('user_id')
    if not uid:
        flash('Please log in before reporting a review.', 'warning')
        return redirect(url_for('login'))
    reason = request.form.get('reason', '').strip()[:250] or 'No reason provided'
    conn = get_db_connection()
    review = conn.execute('SELECT id FROM reviews WHERE id=? AND COALESCE(hidden,0)=0', (review_id,)).fetchone()
    if not review:
        conn.close(); flash('Review not found or already hidden.', 'warning'); return redirect(url_for('home'))
    conn.execute("INSERT OR IGNORE INTO review_reports(review_id,user_id,reason,status) VALUES(?,?,?,'reported')", (review_id, uid, reason))
    conn.commit(); conn.close()
    flash('Thank you. The review was reported to the admin for moderation.', 'success')
    return redirect(url_for('home'))

@app.route('/admin/requests-reviews', methods=['GET','POST'])
@role_required('admin')
@csrf_required
def admin_requests_reviews():
    conn = get_db_connection()
    if request.method == 'POST':
        action = request.form.get('action','')
        if action in {'approve_request','reject_request'}:
            rid = request.form.get('request_id', type=int)
            row = conn.execute("SELECT * FROM book_requests WHERE id=?", (rid,)).fetchone()
            if row and row['status'] == 'requested':
                if action == 'approve_request':
                    title, author = row['title'], row['author'] or 'Unknown'
                    conn.execute("INSERT INTO books(title,author,price,stock,category,image_url,release_date) VALUES(?,?,0,0,'Requested','https://placehold.co/600x800?text=Book',?)", (title, author, datetime.utcnow().date().isoformat()))
                    conn.execute("UPDATE book_requests SET status='approved', reviewed_at=CURRENT_TIMESTAMP WHERE id=?", (rid,))
                    flash('Request approved and a draft inventory book created. Set its price and stock in Inventory Control before selling.', 'success')
                else:
                    conn.execute("UPDATE book_requests SET status='rejected', reviewed_at=CURRENT_TIMESTAMP WHERE id=?", (rid,))
                    flash('Book request rejected.', 'info')
        elif action in {'hide_review','keep_review'}:
            rid = request.form.get('review_id', type=int)
            if action == 'hide_review':
                conn.execute('UPDATE reviews SET hidden=1 WHERE id=?', (rid,))
                conn.execute("UPDATE review_reports SET status='hidden' WHERE review_id=?", (rid,))
                flash('Review hidden. Hidden reviews are excluded from storefront display and rating averages.', 'success')
            else:
                conn.execute('UPDATE reviews SET hidden=0 WHERE id=?', (rid,))
                conn.execute("UPDATE review_reports SET status='kept' WHERE review_id=?", (rid,))
                flash('Review kept and visible again.', 'success')
        conn.commit()
    requests_rows = conn.execute("SELECT * FROM book_requests ORDER BY CASE status WHEN 'requested' THEN 0 ELSE 1 END, request_count DESC, id DESC").fetchall()
    reported = conn.execute("SELECT r.*, GROUP_CONCAT(rr.reason, ' | ') AS report_reasons, COUNT(rr.id) AS reports FROM reviews r JOIN review_reports rr ON rr.review_id=r.id WHERE rr.status='reported' GROUP BY r.id ORDER BY reports DESC,r.id DESC").fetchall()
    conn.close()
    return render_template_string('''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Requests & Review Moderation</title><style>body{font:15px system-ui;background:#f4f6fb;color:#172033;margin:0}.wrap{max-width:1200px;margin:30px auto;padding:20px}.panel{background:white;border-radius:14px;padding:20px;margin:18px 0;overflow:auto}table{border-collapse:collapse;width:100%;min-width:720px}td,th{text-align:left;padding:12px;border-bottom:1px solid #e2e8f0}button{border:0;border-radius:7px;padding:8px 11px;cursor:pointer;background:#2563eb;color:white}.reject{background:#be123c}.muted{color:#64748b}</style></head><body><main class="wrap"><p><a href="/admin/dashboard">← Admin dashboard</a> · <a href="/admin/inventory">Inventory control</a></p><h1>📬 Book requests & review moderation</h1><section class="panel"><h2>Book requests (popular requests first)</h2><table><thead><tr><th>Title</th><th>Author</th><th>Request count</th><th>Status</th><th>Admin action</th></tr></thead><tbody>{% for r in requests_rows %}<tr><td>{{ r.title }}</td><td>{{ r.author or 'Not specified' }}</td><td><b>{{ r.request_count }}</b></td><td>{{ r.status }}</td><td>{% if r.status=='requested' %}<form method="post" style="display:inline"><input type="hidden" name="csrf_token" value="{{ session.csrf_token }}"><input type="hidden" name="request_id" value="{{ r.id }}"><button name="action" value="approve_request">Approve / create draft book</button></form> <form method="post" style="display:inline"><input type="hidden" name="csrf_token" value="{{ session.csrf_token }}"><input type="hidden" name="request_id" value="{{ r.id }}"><button class="reject" name="action" value="reject_request">Reject</button></form>{% else %}Reviewed{% endif %}</td></tr>{% else %}<tr><td colspan="5">No book requests yet.</td></tr>{% endfor %}</tbody></table></section><section class="panel"><h2>Reported reviews</h2><p class="muted">Policy: hidden reviews do not appear publicly and do not count toward rating averages.</p>{% for r in reported %}<article style="border-bottom:1px solid #e2e8f0;padding:14px 0"><b>{{ r.name }} — {{ r.book_title }} ({{ r.rating }}/5)</b><p>{{ r.comment }}</p><p class="muted">Reports: {{ r.reports }} · Reasons: {{ r.report_reasons }}</p><form method="post" style="display:inline"><input type="hidden" name="csrf_token" value="{{ session.csrf_token }}"><input type="hidden" name="review_id" value="{{ r.id }}"><button name="action" value="hide_review" class="reject">Hide review</button></form> <form method="post" style="display:inline"><input type="hidden" name="csrf_token" value="{{ session.csrf_token }}"><input type="hidden" name="review_id" value="{{ r.id }}"><button name="action" value="keep_review">Keep review</button></form></article>{% else %}<p>No pending reports.</p>{% endfor %}</section></main></body></html>''', requests_rows=requests_rows, reported=reported)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    session.pop('staff_role', None)
    flash('You have been logged out of the admin area.', 'info')
    return redirect(url_for('admin_login'))

@app.route('/order-success/<int:order_id>')
def order_success(order_id):
    if not session.get('last_order_id') or int(session.get('last_order_id')) != order_id:
        return redirect(url_for('orders'))
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    if not order:
        flash("We could not find that order. Please check My Orders.", "warning")
        return redirect(url_for('orders'))
    return render_template_string("""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Order confirmed | PageTurner</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:linear-gradient(135deg,#f0fdf4,#eff6ff);font:17px/1.6 system-ui;color:#172033}.card{background:white;padding:clamp(28px,5vw,60px);border-radius:24px;box-shadow:0 18px 60px #17203316;text-align:center;max-width:620px;margin:20px}.tick{margin:auto;width:100px;height:100px;border-radius:50%;background:#dcfce7;color:#15803d;display:grid;place-items:center;font-size:62px;font-weight:800;animation:pop .65s cubic-bezier(.2,.8,.2,1)}@keyframes pop{0%{transform:scale(.1);opacity:0}70%{transform:scale(1.15)}100%{transform:scale(1);opacity:1}}.btn{display:inline-block;margin:10px;padding:12px 20px;border-radius:10px;text-decoration:none;background:#5c3cc8;color:white;font-weight:700}.secondary{background:#f1f5f9;color:#172033}</style></head><body><main class='card'><div class='tick'>✓</div><h1>Order placed successfully!</h1><p>Thank you for shopping with PageTurner Books.</p><h2>Order #{{ order.id }}</h2><p>Payment method: <b>{{ order.payment_method or 'Cash on Delivery' }}</b></p><p>Total: <b>₹{{ order.total_amount|inr }}</b></p><p>Your order has been saved. You can view the full details and updates below.</p><a class='btn' href='/order/{{ order.id }}'>View order details</a><a class='btn secondary' href='/notifications'>Notifications</a><a class='btn secondary' href='/'>Continue shopping</a></main></body></html>""", order=order)


@app.route('/notifications')
def notifications():
    if not session.get('user_id') and not session.get('admin_logged_in') and not session.get('last_order_id'):
        flash('Log in to view your order notifications.', 'warning')
        return redirect(url_for('login'))
    conn = get_db_connection()
    if session.get('admin_logged_in'):
        items = conn.execute('SELECT * FROM notifications ORDER BY id DESC LIMIT 100').fetchall()
    elif session.get('user_id'):
        items = conn.execute('SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 100', (session.get('user_id'),)).fetchall()
    else:
        items = conn.execute('SELECT * FROM notifications WHERE order_id = ? AND user_id IS NULL ORDER BY id DESC', (session.get('last_order_id'),)).fetchall()
    conn.close()
    return render_template_string("""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Notifications | PageTurner</title><style>body{margin:0;background:#f4f6fb;color:#172033;font:17px/1.6 system-ui,sans-serif}header{padding:20px 5%;background:white;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}a{color:#5c3cc8;text-decoration:none;font-weight:650}.wrap{width:100%;padding:35px 5%;}.item{background:white;padding:22px 24px;margin:15px 0;border-radius:14px;box-shadow:0 6px 24px #1720330d;border-left:5px solid #6d4bd1}.muted{color:#697386}.order-success{display:flex;align-items:center;gap:18px;border-left-color:#16a34a;animation:popIn .5s ease-out}.success-tick{width:58px;height:58px;flex:0 0 58px;border-radius:50%;display:grid;place-items:center;background:#dcfce7;color:#15803d;font-size:36px;font-weight:900;animation:tickPop .6s cubic-bezier(.2,.8,.2,1)}@keyframes tickPop{0%{transform:scale(.2);opacity:0}70%{transform:scale(1.15)}100%{transform:scale(1);opacity:1}}@keyframes popIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}</style></head><body><header><a href="/">📚 PageTurner Books</a><nav><a href="/orders">My Orders</a> · <a href="/help">Help Centre</a></nav></header><main class="wrap">{% if session.get('last_order_id') %}<section class="item order-success"><div class="success-tick">✓</div><div><h2 style="margin:0 0 5px;color:#166534;">Order placed successfully!</h2><p style="margin:0;">Your order #{{ session.get('last_order_id') }} has been confirmed. Your order notification is below.</p><a href="/order/{{ session.get('last_order_id') }}">View order details →</a></div></section>{% endif %}<h1>🔔 Notifications</h1><p class="muted">Order confirmations and updates appear here.</p>{% for n in items %}<article class="item"><h3>{{ n.title }}</h3><p>{{ n.message }}</p><p class="muted">{{ n.created_at }}{% if n.order_id %} · <a href="/order/{{ n.order_id }}">View order #{{ n.order_id }}</a>{% endif %}</p></article>{% else %}<article class="item"><h3>You’re all caught up</h3><p>No notifications yet. After placing an order, its confirmation will appear here.</p><a href="/">Browse books →</a></article>{% endfor %}</main></body></html>""", items=items)

@app.route('/help')
def help_centre():
    return render_template_string("""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Help Centre | PageTurner</title><style>body{margin:0;background:#f4f6fb;color:#172033;font:17px/1.7 system-ui,sans-serif}header{padding:20px 5%;background:#fff;display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px}a{color:#5c3cc8;text-decoration:none;font-weight:650}.wrap{padding:36px 5%;width:100%;max-width:1500px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px}.card{background:#fff;border-radius:16px;padding:25px;box-shadow:0 8px 25px #1720330b}.card h2{margin-top:0}.muted{color:#697386}</style></head><body><header><a href="/">📚 PageTurner Books</a><nav><a href="/notifications">Notifications</a> · <a href="/orders">My Orders</a></nav></header><main class="wrap"><h1>Help Centre</h1><p class="muted">Quick answers for shopping and managing your PageTurner order.</p><div class="grid"><section class="card"><h2>📦 Track an order</h2><p>Open <a href="/orders">My Orders</a> to check your order status and open an order for details.</p></section><section class="card"><h2>💳 Payment options</h2><p>Checkout supports Cash on Delivery and demo UPI/card selections. Online payment choices are for demonstration only; this project does not charge a card or transfer money.</p></section><section class="card"><h2>🔔 Order notifications</h2><p>After placing an order, visit the <a href="/notifications">Notifications section</a> for the confirmation message.</p></section><section class="card"><h2>🛒 Change your cart</h2><p>Visit your cart before checkout to change quantities or remove books.</p><a href="/cart">Open cart →</a></section><section class="card"><h2>👤 Account help</h2><p>Sign in to see your order history and notifications connected to your account.</p><a href="/login">Customer login →</a></section><section class="card"><h2>✉️ Need more help?</h2><p>For this demo project, contact the bookstore administrator through the project owner. Do not enter real payment-card details.</p></section></div><p style="margin-top:25px"><a href="/">← Back to bookstore</a></p></main></body></html>""")

@app.route('/register', methods=['GET', 'POST'])
@csrf_required
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        if not name or not email or not password:
            flash("Please fill in all fields.", "danger")
            return redirect(url_for('register'))

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            conn.close()
            flash("This email is already registered. Please log in instead.", "danger")
            return redirect(url_for('login'))

        cursor.execute("INSERT INTO users (username, name, email, password, role) VALUES (?, ?, ?, ?, 'customer')", (email, name, email, generate_password_hash(password)))
        conn.commit()
        session['user_id'] = cursor.lastrowid
        session['user_name'] = name
        session['user_email'] = email
        session['user_role'] = 'customer'
        conn.close()
        flash(f"Registration successful! Welcome, {name}.", "success")
        return redirect(url_for('home'))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Customer Register - PageTurner</title></head>
    <body style="background:#f8fafc; display:flex; align-items:center; justify-content:center; height:100vh; font-family:sans-serif;">
        <div style="background:white; padding:30px; border-radius:10px; width:350px; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
            <h4 style="text-align:center; margin-top:0;">Create Customer Account</h4>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div style="margin-bottom:10px;"><label style="font-weight:bold; font-size:13px;">Full Name</label><input type="text" name="name" required style="width:100%; padding:8px; border:1px solid #cbd5e1; border-radius:4px; box-sizing:border-box;"></div>
                <div style="margin-bottom:10px;"><label style="font-weight:bold; font-size:13px;">Email Address</label><input type="email" name="email" required style="width:100%; padding:8px; border:1px solid #cbd5e1; border-radius:4px; box-sizing:border-box;"></div>
                <div style="margin-bottom:16px;"><label style="font-weight:bold; font-size:13px;">Password</label><input type="password" name="password" required style="width:100%; padding:8px; border:1px solid #cbd5e1; border-radius:4px; box-sizing:border-box;"></div>
                <button type="submit" style="width:100%; padding:10px; background:#2563eb; color:white; border:none; border-radius:4px; font-weight:bold; cursor:pointer;">Sign Up</button>
            </form>
            <div style="text-align:center; margin-top:12px; font-size:13px;">Already have an account? <a href="/login" style="color:#2563eb;">Login here</a></div>
        </div>
    </body>
    </html>
    """)

@app.route('/forgot-password', methods=['GET', 'POST'])
@csrf_required
def forgot_password():
    reset_url = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        conn = get_db_connection()
        user = conn.execute('SELECT id FROM users WHERE lower(email) = ?', (email,)).fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            expires = (datetime.utcnow() + timedelta(minutes=30)).isoformat()
            conn.execute('INSERT INTO password_reset_tokens (user_id, token, expires_at, used) VALUES (?, ?, ?, 0)', (user['id'], token, expires))
            conn.commit()
            # This local/demo project has no email provider configured; display a temporary link.
            reset_url = url_for('reset_password', token=token, _external=True)
        conn.close()
        if not user:
            flash('If that email is registered, a password reset link would be sent. For this local demo, no email service is configured.', 'info')
    return render_template_string("""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Forgot Password | PageTurner</title><style>body{margin:0;background:#f4f6fb;font:18px/1.6 system-ui,sans-serif;color:#172033;min-height:100vh;display:grid;place-items:center;padding:24px}.panel{background:white;width:min(100%,560px);padding:36px;border-radius:18px;box-shadow:0 10px 35px #17203314}label,input,button{display:block;width:100%;font:inherit}input{padding:13px;border:1px solid #cbd5e1;border-radius:9px;margin:8px 0 18px}button{padding:13px;border:0;border-radius:9px;background:#5638b8;color:white;font-weight:750;cursor:pointer}a{color:#5638b8}</style></head><body><main class='panel'><h1>Reset your password</h1><p>Enter the email address linked to your PageTurner account.</p><form method='POST'><input type='hidden' name='csrf_token' value='{{ session.csrf_token }}'><label for='email'>Email address</label><input id='email' type='email' name='email' required placeholder='you@example.com'><button type='submit'>Generate reset link</button></form>{% if reset_url %}<div style='background:#ecfdf5;border:1px solid #a7f3d0;border-radius:10px;padding:16px;margin-top:20px'><b>Demo reset link (valid for 30 minutes)</b><p><a href='{{ reset_url }}'>Reset my password</a></p><small>This link is shown here because email delivery is not configured.</small></div>{% endif %}<p><a href='/login'>← Back to login</a></p></main></body></html>""", reset_url=reset_url)

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
@csrf_required
def reset_password(token):
    conn = get_db_connection()
    record = conn.execute('SELECT * FROM password_reset_tokens WHERE token = ? AND used = 0', (token,)).fetchone()
    if not record or datetime.fromisoformat(record['expires_at']) < datetime.utcnow():
        conn.close()
        return render_template_string("""<!doctype html><html><body style='font:18px system-ui;padding:40px'><h1>Reset link expired</h1><p>This password reset link is invalid, expired, or already used.</p><a href='/forgot-password'>Request another link</a></body></html>"""), 400
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if len(password) < 8 or password != confirm:
            conn.close()
            flash('Passwords must match and contain at least 8 characters.', 'danger')
            return redirect(url_for('reset_password', token=token))
        conn.execute('UPDATE users SET password = ? WHERE id = ?', (generate_password_hash(password), record['user_id']))
        conn.execute('UPDATE password_reset_tokens SET used = 1 WHERE id = ?', (record['id'],))
        conn.commit()
        conn.close()
        flash('Password reset successfully. Please log in.', 'success')
        return redirect(url_for('login'))
    conn.close()
    return render_template_string("""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Choose New Password</title><style>body{margin:0;background:#f4f6fb;font:18px/1.6 system-ui,sans-serif;color:#172033;min-height:100vh;display:grid;place-items:center;padding:24px}.panel{background:white;width:min(100%,560px);padding:36px;border-radius:18px;box-shadow:0 10px 35px #17203314}label,input,button{display:block;width:100%;font:inherit}input{padding:13px;border:1px solid #cbd5e1;border-radius:9px;margin:8px 0 18px}button{padding:13px;border:0;border-radius:9px;background:#5638b8;color:white;font-weight:750;cursor:pointer}</style></head><body><main class='panel'><h1>Choose a new password</h1><form method='POST'><input type='hidden' name='csrf_token' value='{{ session.csrf_token }}'><label>New password (at least 8 characters)</label><input type='password' name='password' minlength='8' required><label>Confirm new password</label><input type='password' name='confirm_password' minlength='8' required><button type='submit'>Update password</button></form></main></body></html>""")

@app.route('/login', methods=['GET', 'POST'])
@csrf_required
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE lower(email) = lower(?) OR lower(username) = lower(?)", (email, email)).fetchone()
        if user:
            if user['is_locked']:
                conn.close()
                flash("This account is locked. Please contact the bookstore administrator to unlock it.", "danger")
                return redirect(url_for('login'))
            if user['locked_until']:
                try:
                    locked_until = datetime.fromisoformat(user['locked_until'])
                except (TypeError, ValueError):
                    locked_until = datetime.min
                if datetime.utcnow() < locked_until:
                    remaining = max(1, int((locked_until - datetime.utcnow()).total_seconds() / 60) + 1)
                    conn.close()
                    flash(f"Too many failed attempts. Try again in about {remaining} minute(s).", "danger")
                    return redirect(url_for('login'))
                conn.execute("UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = ?", (user['id'],))
                conn.commit()
            stored_password = user['password'] or ''
            valid_password = False
            try:
                valid_password = check_password_hash(stored_password, password)
            except (ValueError, TypeError):
                valid_password = False
            if not valid_password and stored_password == password:
                valid_password = True
                conn.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(password), user['id']))
            if valid_password:
                conn.execute("UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = ?", (user['id'],))
                conn.commit()
                conn.close()
                # Second-factor demo: one-time six-digit code expires after five minutes.
                otp = f"{secrets.randbelow(1000000):06d}"
                session['pending_otp_user_id'] = user['id']
                session['pending_otp_name'] = user['name'] or user['username'] or user['email']
                session['pending_otp_email'] = user['email']
                session['pending_otp_hash'] = __import__('hashlib').sha256(otp.encode()).hexdigest()
                session['pending_otp_expiry'] = (datetime.utcnow() + timedelta(minutes=5)).isoformat(timespec='seconds')
                session['pending_otp_attempts'] = 0
                app.logger.info("PageTurner demo login OTP for %s: %s (expires in 5 minutes)", user['email'], otp)
                flash("Password accepted. Enter the 6-digit one-time code printed in the VS Code terminal. It expires in 5 minutes.", "info")
                return redirect(url_for('verify_login_otp'))
            attempts = int(user['failed_login_attempts'] or 0) + 1
            if attempts >= 5:
                until = (datetime.utcnow() + timedelta(minutes=15)).isoformat(timespec='seconds')
                conn.execute("UPDATE users SET failed_login_attempts = 0, locked_until = ? WHERE id = ?", (until, user['id']))
                conn.commit()
                conn.close()
                flash("Too many failed login attempts. Your account is locked for 15 minutes.", "danger")
                return redirect(url_for('login'))
            conn.execute("UPDATE users SET failed_login_attempts = ? WHERE id = ?", (attempts, user['id']))
            conn.commit()
        conn.close()
        flash("Invalid credentials.", "danger")
        return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/login/otp', methods=['GET', 'POST'])
@csrf_required
def verify_login_otp():
    user_id = session.get('pending_otp_user_id')
    if not user_id or not session.get('pending_otp_hash'):
        flash("Please sign in with your email and password first.", "warning")
        return redirect(url_for('login'))
    if request.method == 'POST':
        code = request.form.get('otp', '').strip()
        expiry = session.get('pending_otp_expiry', '')
        try:
            expired = datetime.utcnow() > datetime.fromisoformat(expiry)
        except (TypeError, ValueError):
            expired = True
        attempts = int(session.get('pending_otp_attempts', 0))
        if expired or attempts >= 5:
            for key in ('pending_otp_user_id','pending_otp_name','pending_otp_email','pending_otp_hash','pending_otp_expiry','pending_otp_attempts'):
                session.pop(key, None)
            flash("Your one-time code expired or too many attempts were made. Please sign in again.", "danger")
            return redirect(url_for('login'))
        import hashlib, hmac
        submitted_hash = hashlib.sha256(code.encode()).hexdigest()
        if len(code) != 6 or not code.isdigit() or not hmac.compare_digest(submitted_hash, session.get('pending_otp_hash', '')):
            session['pending_otp_attempts'] = attempts + 1
            flash("That one-time code is incorrect.", "danger")
            return redirect(url_for('verify_login_otp'))
        uid = int(user_id)
        conn = get_db_connection()
        user = conn.execute("SELECT id,name,username,email,role,is_locked FROM users WHERE id = ?", (uid,)).fetchone()
        conn.close()
        if not user or user['is_locked']:
            for key in list(session.keys()):
                if key.startswith('pending_otp_'): session.pop(key, None)
            flash("This account is unavailable. Please contact the administrator if it is locked.", "danger")
            return redirect(url_for('login'))
        name = user['name'] or user['username'] or user['email']
        for key in list(session.keys()):
            if key.startswith('pending_otp_'): session.pop(key, None)
        session['user_id'] = user['id']
        session['user_name'] = name
        session['user_email'] = user['email']
        session['user_role'] = user['role'] or 'customer'
        flash("Welcome back", "success")
        return redirect(url_for('home'))
    return render_template_string("""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Verify OTP | PageTurner</title><style>body{font:16px system-ui;background:#f4f6fb;color:#172033;max-width:480px;margin:8vh auto;padding:24px}.card{background:white;padding:28px;border-radius:18px;box-shadow:0 8px 28px #17203312}input{box-sizing:border-box;width:100%;padding:13px;border:1px solid #cbd5e1;border-radius:9px;font:inherit;margin:8px 0 16px}button{width:100%;padding:13px;background:#4338ca;color:white;border:0;border-radius:9px;font-weight:800;cursor:pointer}.muted{color:#64748b;font-size:14px}</style></head><body><div class='card'><h1>Verify your login</h1><p>Enter the six-digit OTP printed in the VS Code terminal. This local demo does not send email or SMS.</p>{% with messages=get_flashed_messages(with_categories=true) %}{% for cat,msg in messages %}<p>{{ msg }}</p>{% endfor %}{% endwith %}<form method='post'><input type='hidden' name='csrf_token' value='{{ session.csrf_token }}'><label for='otp'>One-time password</label><input id='otp' name='otp' inputmode='numeric' pattern='[0-9]{6}' maxlength='6' autocomplete='one-time-code' required autofocus><button type='submit'>Verify and sign in</button></form><p class='muted'>Code expires in 5 minutes. Up to 5 attempts are allowed.</p><a href='/login'>Cancel</a></div></body></html>""")

@app.route('/admin/customers', methods=['GET', 'POST'])
@role_required('admin')
@csrf_required
def admin_customers():
    conn = get_db_connection()
    if request.method == 'POST':
        try:
            customer_id = int(request.form.get('customer_id', '0'))
        except ValueError:
            customer_id = 0
        action = request.form.get('action')
        customer = conn.execute("SELECT id, role FROM users WHERE id = ?", (customer_id,)).fetchone()
        if not customer or customer['role'] not in ('customer', None):
            conn.close()
            flash('Customer account not found.', 'danger')
            return redirect(url_for('admin_customers'))
        if action == 'lock':
            conn.execute("UPDATE users SET is_locked = 1 WHERE id = ?", (customer_id,))
            flash('Customer account locked.', 'success')
        elif action == 'unlock':
            conn.execute("UPDATE users SET is_locked = 0, failed_login_attempts = 0, locked_until = NULL WHERE id = ?", (customer_id,))
            flash('Customer account unlocked and failed attempts cleared.', 'success')
        else:
            conn.close()
            abort(400)
        conn.commit()
        conn.close()
        return redirect(url_for('admin_customers'))
    customers = conn.execute("SELECT id, name, username, email, is_locked, failed_login_attempts, locked_until FROM users WHERE role = 'customer' OR role IS NULL ORDER BY id DESC").fetchall()
    conn.close()
    return render_template('admin_customers.html', customers=customers)

@app.route('/logout')
def logout():
    session.clear()
    flash("You have logged out", "success")
    return redirect(url_for('home'))

@app.route('/api/books')
def api_books():
    """Read-only JSON endpoint for catalogue search and live suggestions."""
    q = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip()
    sort = request.args.get('sort', 'title').strip()
    order_by = {'price': 'price ASC', 'newest': 'id DESC', 'rating': 'rating DESC', 'title': 'title COLLATE NOCASE ASC'}.get(sort, 'title COLLATE NOCASE ASC')
    sql = "SELECT id, title, author, category, price, rating, stock, image_url FROM books WHERE 1=1"
    params = []
    if q:
        sql += " AND (title LIKE ? OR author LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    if category:
        sql += " AND category = ?"
        params.append(category)
    after = request.args.get('after', type=int)
    limit = min(50, max(1, request.args.get('limit', 10, type=int)))
    conn = get_db_connection()
    if after is not None:
        sql += " AND id > ?"
        params.append(max(0, after))
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return jsonify({'books': [dict(row) for row in rows], 'next_cursor': rows[-1]['id'] if len(rows) == limit else None})
    sql += " ORDER BY " + order_by + " LIMIT 50"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])

@app.route('/api/books/<int:book_id>')
def api_book_detail(book_id):
    conn = get_db_connection()
    row = conn.execute("SELECT id, title, author, category, price, rating, stock, image_url FROM books WHERE id = ?", (book_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))

@app.route('/api/cart/add/<int:book_id>', methods=['POST'])
@csrf_required
def api_add_to_cart(book_id):
    conn = get_db_connection()
    book = conn.execute("SELECT id, title, stock FROM books WHERE id = ?", (book_id,)).fetchone()
    conn.close()
    if not book:
        return jsonify({"error": "not found"}), 404
    cart = session.get('cart', {})
    key = str(book_id)
    if book['stock'] < cart.get(key, 0) + 1:
        return jsonify({"error": "Insufficient stock", "cart_count": sum(cart.values())}), 409
    cart[key] = cart.get(key, 0) + 1
    session['cart'] = cart
    return jsonify({"message": "Added to cart", "cart_count": sum(cart.values()), "book_id": book_id})

@app.route('/order/<int:order_id>/invoice')
def order_invoice(order_id):
    if not session.get('user_id') and not session.get('admin_logged_in'):
        return redirect(url_for('login'))
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        abort(404)
    if not session.get('admin_logged_in') and order['user_id'] != session.get('user_id'):
        conn.close()
        abort(403)
    items = conn.execute("SELECT b.title, oi.quantity, oi.price_at_purchase, (oi.quantity * oi.price_at_purchase) AS subtotal FROM order_items oi JOIN books b ON b.id = oi.book_id WHERE oi.order_id = ?", (order_id,)).fetchall()
    conn.close()
    return render_template_string("""<!doctype html><html><head><title>Invoice #{{ order.id }}</title><style>body{font-family:Arial;max-width:800px;margin:30px auto;padding:20px}table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}@media print{.no-print{display:none}}</style></head><body><div class='no-print'><button onclick='window.print()'>Print / Save PDF</button></div><h1>PageTurner Books — Invoice #{{ order.id }}</h1><p>{{ order.customer_name }} · {{ order.customer_address }}</p>{% if order.gift_wrap %}<section style="border:1px solid #f59e0b;background:#fffbeb;padding:12px;border-radius:8px;margin:16px 0"><b>🎁 Gift wrap included — ₹30</b>{% if order.gift_message %}<p style="white-space:pre-wrap">{{ order.gift_message }}</p>{% endif %}</section>{% endif %}<table><tr><th>Book</th><th>Qty</th><th>Price</th><th>Subtotal</th></tr>{% for item in items %}<tr><td>{{ item.title }}</td><td>{{ item.quantity }}</td><td>{{ item.price_at_purchase|inr }}</td><td>{{ item.subtotal|inr }}</td></tr>{% endfor %}</table><h2>Total: ₹{{ order.total_amount|inr }}</h2><p>Status: {{ order.status }}</p></body></html>""", order=order, items=items)

@app.errorhandler(403)
def forbidden_page(error):
    return render_template_string("<h1>403 — Access denied</h1><p>You do not have permission to view this page.</p><a href='/'>Back to home</a>"), 403

@app.errorhandler(404)
def not_found_page(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error_page(error):
    return render_template('500.html'), 500

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)