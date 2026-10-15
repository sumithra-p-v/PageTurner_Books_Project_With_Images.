import sqlite3
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, jsonify, flash, abort

app = Flask(__name__)
app.secret_key = 'super_secret_pageturner_key'
DATABASE = 'database.db'

ALLOWED_STATUS_TRANSITIONS = {
    'Placed': ['Packed', 'Shipped', 'Delivered', 'Cancelled'],
    'Packed': ['Shipped', 'Delivered', 'Cancelled'],
    'Shipped': ['Delivered', 'Cancelled'],
    'Delivered': ['Placed', 'Packed', 'Shipped', 'Cancelled'],
    'Cancelled': ['Placed', 'Packed', 'Shipped', 'Delivered']
}

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

@app.template_filter('inr')
def format_inr(paise):
    try:
        val = int(paise or 0)
        if val % 100 == 0:
            return f"{val // 100}"
        return f"{val / 100:.2f}"
    except (ValueError, TypeError):
        return "0"

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
            if user_role not in allowed_roles:
                return """
                <!DOCTYPE html>
                <html>
                <head><title>403 Forbidden - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
                <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
                    <div class="card p-5 text-center shadow-sm" style="max-width: 450px; margin: 100px auto;">
                        <h1 class="display-1 text-danger fw-bold" style="color: var(--danger);">403</h1>
                        <h4 class="fw-bold mb-3">Access Forbidden</h4>
                        <p class="text-muted">You do not have permission to access this resource.</p>
                        <a href="/" class="btn btn-primary mt-3 fw-bold">Return to Storefront</a>
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
            password TEXT NOT NULL
        )
    ''')

    for col, col_type in [("username", "TEXT"), ("name", "TEXT"), ("role", "TEXT DEFAULT 'customer'")]:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass

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
        CREATE TABLE IF NOT EXISTS stock_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            change INTEGER NOT NULL,
            reason TEXT NOT NULL,
            user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books (id)
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    for col_name, col_type in [("customer_name", "TEXT"), ("customer_email", "TEXT"), ("customer_phone", "TEXT"), ("customer_address", "TEXT"), ("discount_amount", "INTEGER DEFAULT 0")]:
        try:
            cursor.execute(f"ALTER TABLE orders ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            note TEXT,
            FOREIGN KEY (order_id) REFERENCES orders (id)
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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            price_at_purchase INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (order_id) REFERENCES orders (id),
            FOREIGN KEY (book_id) REFERENCES books (id)
        )
    ''')

    try:
        cursor.execute("ALTER TABLE order_items ADD COLUMN price_at_purchase INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS login_attempts (
            ip_or_email TEXT PRIMARY KEY,
            attempts INTEGER DEFAULT 0,
            locked_until TIMESTAMP
        )
    ''')

    cursor.execute('SELECT COUNT(*) FROM books')
    if cursor.fetchone()[0] == 0:
        sample_books = [
            ("The Great Gatsby", "F. Scott Fitzgerald", 49900, 4.8, 15, "Fiction", "https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=400"),
            ("To Kill a Mockingbird", "Harper Lee", 55000, 4.9, 12, "Fiction", "https://images.unsplash.com/photo-1512820790803-83ca734da794?w=400"),
            ("1984", "George Orwell", 39900, 4.7, 8, "Sci-Fi", "https://images.unsplash.com/photo-1543002588-bfa74002ed7e?w=400"),
            ("Atomic Habits", "James Clear", 65000, 5.0, 20, "Self-Help", "https://images.unsplash.com/photo-1589829085413-56de8ae18c73?w=400"),
            ("The Hobbit", "J.R.R. Tolkien", 59900, 4.8, 10, "Fantasy", "https://images.unsplash.com/photo-1629992101753-56d196c8aced?w=400"),
            ("Sapiens", "Yuval Noah Harari", 75000, 4.6, 14, "History", "https://images.unsplash.com/photo-1497633762265-9d179a990aa6?w=400"),
            ("Clean Code", "Robert C. Martin", 125000, 4.9, 6, "Technology", "https://images.unsplash.com/photo-1532012197267-da84d127e765?w=400"),
            ("Deep Work", "Cal Newport", 49900, 4.7, 11, "Self-Help", "https://images.unsplash.com/photo-1524995997946-a1c2e315a42f?w=400")
        ]
        cursor.executemany('INSERT INTO books (title, author, price, rating, stock, category, image_url) VALUES (?, ?, ?, ?, ?, ?, ?)', sample_books)

    cursor.execute('SELECT COUNT(*) FROM coupons')
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO coupons (code, discount_type, discount_value, is_active) VALUES ('SAVE10', 'percentage', 10, 1)")
        cursor.execute("INSERT INTO coupons (code, discount_type, discount_value, is_active) VALUES ('FLAT100', 'fixed', 10000, 1)")

    conn.commit()
    conn.close()

def get_recommendations(book_id, limit=4):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT b.*, COUNT(o2.book_id) AS frequency
        FROM order_items o1
        JOIN order_items o2 ON o1.order_id = o2.order_id AND o1.book_id != o2.book_id
        JOIN books b ON o2.book_id = b.id
        WHERE o1.book_id = ?
        GROUP BY o2.book_id
        ORDER BY frequency DESC
        LIMIT ?
    """
    cursor.execute(query, (book_id, limit))
    recs = cursor.fetchall()
    conn.close()
    return recs

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

    cursor.execute(query, params)
    books = cursor.fetchall()

    cursor.execute("SELECT DISTINCT category FROM books")
    categories = [row['category'] for row in cursor.fetchall()]

    cursor.execute("SELECT * FROM reviews ORDER BY id DESC")
    recent_reviews = cursor.fetchall()
    conn.close()

    book_cards = ""
    for b in books:
        price_display = format_inr(b['price'])
        book_cards += f"""
        <div style="border: 1px solid #f1f5f9; border-radius: 12px; padding: 16px; background: #fff; transition: transform 0.2s, box-shadow 0.2s; position: relative;">
            <span style="position: absolute; top: 12px; left: 12px; background: #ff5e62; color: white; font-size: 10px; padding: 3px 8px; border-radius: 4px; font-weight: 700;">POPULAR</span>
            <img src="{b['image_url']}" style="width: 100%; height: 200px; object-fit: contain; margin-bottom: 12px; border-radius: 4px;">
            <div style="font-size: 14px; font-weight: 700; margin: 4px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{b['title']}</div>
            <div style="font-size: 12px; color: #64748b; margin-bottom: 6px;">{b['author']}</div>
            <div style="color: #f59e0b; font-size: 11px; margin-bottom: 8px;">★ ★ ★ ★ ★ ({b['rating']})</div>
            <div style="font-size: 16px; font-weight: 800; color: #ff5e62; margin-bottom: 12px;">{price_display}</div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
                <a href="/book/{b['id']}" style="padding: 6px; border-radius: 6px; font-size: 12px; font-weight: 600; text-align: center; text-decoration: none; border: 1px solid #cbd5e1; background: #fff; color: #1e293b;">View</a>
                <form method="POST" action="/cart/add/{b['id']}" style="margin:0;">
                    <input type="hidden" name="csrf_token" value="{session.get('csrf_token', '')}">
                    <button type="submit" style="width:100%; padding: 6px; border-radius: 6px; font-size: 12px; font-weight: 600; text-align: center; border: 1px solid #fef08a; background: #fef08a; color: #000; cursor: pointer;">Add Cart</button>
                </form>
            </div>
        </div>
        """

    review_cards = ""
    for r in recent_reviews:
        review_cards += f"""
        <div style="background: #fff; border: 1px solid #f1f5f9; border-radius: 8px; padding: 14px; margin-bottom: 12px;">
            <div style="display: flex; justify-content: space-between; font-weight: bold; font-size: 13px;">
                <span>{r['name']} <small style="color: #64748b;">on {r['book_title']}</small></span>
                <span style="color: #f59e0b;">{r['rating']} ⭐</span>
            </div>
            <p style="margin: 6px 0 0 0; font-size: 13px; color: #475569;">"{r['comment']}"</p>
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
            .top-bar { background-color: #f8fafc; border-bottom: 1px solid #e2e8f0; font-size: 12px; padding: 6px 40px; display: flex; justify-content: space-between; align-items: center; color: var(--text-muted); }
            .header-nav { display: flex; justify-content: space-between; align-items: center; padding: 16px 40px; background: #fff; box-shadow: 0 2px 10px rgba(0,0,0,0.03); position: sticky; top: 0; z-index: 100; }
            .logo { font-size: 24px; font-weight: 800; color: var(--pink-primary); text-decoration: none; display: flex; align-items: center; gap: 6px; }
            .nav-links { display: flex; gap: 24px; list-style: none; margin: 0; padding: 0; }
            .nav-links a { text-decoration: none; color: var(--text-dark); font-weight: 600; font-size: 14px; }
            .header-icons { display: flex; align-items: center; gap: 18px; font-size: 16px; }
            .header-icons a { color: var(--text-dark); text-decoration: none; }
            .hero-banner { background: linear-gradient(135deg, #e0f2fe 0%, #fbcfe8 50%, #fef08a 100%); border-radius: 20px; margin: 20px 40px; padding: 40px 60px; display: flex; align-items: center; justify-content: space-between; }
            .btn-shop { background: var(--pink-primary); color: white; padding: 12px 28px; border-radius: 30px; border: none; font-weight: 700; text-decoration: none; display: inline-block; box-shadow: 0 4px 14px rgba(255, 94, 98, 0.4); }
            .features-bar { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; padding: 20px 40px; }
            .feature-item { display: flex; align-items: center; gap: 14px; }
            .feature-item i { font-size: 24px; color: var(--pink-primary); }
            .books-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 24px; padding: 0 40px; margin-bottom: 40px; }
        </style>
    </head>
    <body>
        <div class="top-bar">
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
                <li><a href="/admin/dashboard" style="color: var(--pink-primary);">ADMIN DASHBOARD</a></li>
            </ul>
            <div class="header-icons">
                <a href="/wishlist"><i class="fa-regular fa-heart"></i></a>
                <a href="/cart"><i class="fa-solid fa-cart-shopping"></i></a>
                {% if session.user_id or session.admin_logged_in %}
                    <a href="/logout" title="Logout"><i class="fa-solid fa-right-from-bracket"></i></a>
                {% else %}
                    <a href="/login" title="Login"><i class="fa-regular fa-user"></i></a>
                {% endif %}
            </div>
        </header>

        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}<div style="padding: 10px 40px;">{% for cat, msg in messages %}<div class="alert alert-{{cat}}" style="padding: 10px; border-radius: 6px; margin-bottom: 10px;">{{msg}}</div>{% endfor %}</div>{% endif %}
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

        <section class="features-bar">
            <div class="feature-item"><i class="fa-solid fa-truck-fast"></i><div><h6 style="margin:0; font-weight:700;">Free Delivery</h6><small style="color:var(--text-muted);">Fast delivery to your home.</small></div></div>
            <div class="feature-item"><i class="fa-solid fa-award"></i><div><h6 style="margin:0; font-weight:700;">Quality Guarantee</h6><small style="color:var(--text-muted);">Authentic book editions.</small></div></div>
            <div class="feature-item"><i class="fa-solid fa-tags"></i><div><h6 style="margin:0; font-weight:700;">Daily Offers</h6><small style="color:var(--text-muted);">Enjoy discount coupons.</small></div></div>
            <div class="feature-item"><i class="fa-solid fa-shield-halved"></i><div><h6 style="margin:0; font-weight:700;">100% Secure Payment</h6><small style="color:var(--text-muted);">Protected transactions.</small></div></div>
        </section>

        <div style="padding: 0 40px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center;">
            <h3 style="font-weight: 700; margin: 0;">Best Selling Items</h3>
        </div>

        <div class="books-grid">
            """ + (book_cards or "<p class='text-muted'>No books found.</p>") + """
        </div>

        <div style="padding: 0 40px; margin-bottom: 40px;">
            <h4 style="font-weight: 700; margin-bottom: 16px;">💬 Customer Reviews</h4>
            """ + (review_cards or "<p class='text-muted'>No reviews submitted yet.</p>") + """
            
            <div class="card" style="padding: 16px; background: #f8fafc; border-radius: 8px; margin-top: 20px;">
                <h5 style="font-weight: bold; margin-top: 0;">Leave a Review</h5>
                <form method="POST" action="/review/add" style="display: grid; grid-template-columns: repeat(3, 1fr) auto; gap: 8px;">
                    <input type="hidden" name="csrf_token" value=" """ + session.get('csrf_token', '') + """ ">
                    <input type="text" name="name" class="form-control" placeholder="Your Name" required>
                    <input type="text" name="book_title" class="form-control" placeholder="Book Title" required>
                    <input type="number" min="1" max="5" name="rating" class="form-control" placeholder="Rating (1-5)" required>
                    <input type="text" name="comment" class="form-control" placeholder="Your comment..." style="grid-column: span 3;" required>
                    <button type="submit" class="btn btn-primary" style="grid-column: span 1; font-weight: bold;">Submit</button>
                </form>
            </div>
        </div>
    </body>
    </html>
    """)

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
    conn.close()

    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for('home'))

    recs = get_recommendations(book_id)
    recs_cards = ""
    for r in recs:
        formatted_price = format_inr(r['price'])
        recs_cards += f"""
        <div class="card" style="text-align: center; padding: 10px;">
            <img src="{r['image_url']}" style="height: 140px; object-fit: contain; margin: 0 auto;">
            <div style="padding: 8px 0;">
                <h6 style="font-weight: bold; margin: 4px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{r['title']}</h6>
                <small class="text-muted" style="display: block;">{r['author']}</small>
                <span style="font-weight: bold; color: var(--primary);">{formatted_price}</span>
                <a href="/book/{r['id']}" class="btn btn-outline" style="display: block; margin-top: 8px; padding: 4px 8px; font-size: 12px;">View</a>
            </div>
        </div>
        """

    recs_section = f"""
    <div style="margin-top: 30px;">
        <h5 style="font-weight: bold; margin-bottom: 12px;">🛒 Customers Also Bought</h5>
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;">
            {recs_cards}
        </div>
    </div>
    """ if recs else ""

    csrf_tok = session.get('csrf_token', '')
    price_str = format_inr(book['price'])

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>""" + book['title'] + """ - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
    <body style="background-color: var(--bg-light); padding: 20px;">
    <div class="container card" style="max-width: 800px; padding: 24px;">
        <div style="display: flex; gap: 24px; align-items: center; flex-wrap: wrap;">
            <div style="text-align: center; flex: 1; min-width: 200px;">
                <img src=" """ + book['image_url'] + """ " style="max-height: 250px; max-width: 100%; border-radius: var(--radius);">
            </div>
            <div style="flex: 2; min-width: 250px;">
                <h2 style="font-weight: bold; margin-top: 0;">""" + book['title'] + """</h2>
                <h5 class="text-muted">by """ + book['author'] + """</h5>
                <p style="margin: 8px 0;"><span style="background: #e2e8f0; padding: 3px 8px; border-radius: 4px; font-size: 12px;">""" + book['category'] + """</span> <span style="background: #fef08a; padding: 3px 8px; border-radius: 4px; font-size: 12px;">""" + str(book['rating']) + """ ⭐</span></p>
                <h3 style="color: var(--primary); font-weight: bold;">""" + price_str + """</h3>
                <p class="text-muted" style="font-size: 13px;">Stock Available: """ + str(book['stock']) + """</p>
                <form method="POST" action="/cart/add/""" + str(book['id']) + """">
                    <input type="hidden" name="csrf_token" value=" """ + csrf_tok + """ ">
                    <button type="submit" class="btn btn-warning" style="font-weight: bold; width: 100%; margin-bottom: 8px;">Add to Cart</button>
                </form>
                <form method="POST" action="/wishlist/add/""" + str(book['id']) + """" style="margin: 0;">
                    <input type="hidden" name="csrf_token" value=" """ + csrf_tok + """ ">
                    <button type="submit" class="btn btn-outline" style="font-size: 13px; width: 100%; color: var(--danger); border-color: var(--danger);">❤️ Add to Wishlist</button>
                </form>
            </div>
        </div>
        """ + recs_section + """
        <hr style="margin: 24px 0; border: 0; border-top: 1px solid var(--border-color);">
        <a href="/" class="btn btn-outline">Return to Catalog</a>
    </div>
    </body>
    </html>
    """)

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

        cursor.execute("INSERT INTO users (username, name, email, password, role) VALUES (?, ?, ?, ?, 'customer')", (email, name, email, password))
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
    <head><title>Customer Register - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="card p-4 shadow-sm" style="width: 380px; margin: 100px auto;">
            <h4 class="fw-bold text-center mb-3">Create Customer Account</h4>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div class="form-group"><label class="form-label">Full Name</label><input type="text" name="name" class="form-control" required></div>
                <div class="form-group"><label class="form-label">Email Address</label><input type="email" name="email" class="form-control" required></div>
                <div class="form-group"><label class="form-label">Password</label><input type="password" name="password" class="form-control" required></div>
                <button type="submit" class="btn btn-primary w-100 fw-bold">Sign Up</button>
            </form>
            <div class="text-center mt-3" style="font-size: 13px;">Already have an account? <a href="/login">Login here</a></div>
        </div>
    </body>
    </html>
    """)

@app.route('/login', methods=['GET', 'POST'])
@csrf_required
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        identifier = request.remote_addr

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT attempts, locked_until FROM login_attempts WHERE ip_or_email = ?", (identifier,))
        record = cursor.fetchone()

        if record and record['locked_until']:
            lock_time = datetime.strptime(record['locked_until'], "%Y-%m-%d %H:%M:%S.%f") if "." in record['locked_until'] else datetime.strptime(record['locked_until'], "%Y-%m-%d %H:%M:%S")
            if datetime.now() < lock_time:
                conn.close()
                flash("Account temporarily locked. Try again in 5 minutes.", "danger")
                return redirect(url_for('login'))

        cursor.execute("SELECT * FROM users WHERE (email = ? OR username = ?) AND password = ?", (email, email, password))
        user = cursor.fetchone()

        if user:
            cursor.execute("DELETE FROM login_attempts WHERE ip_or_email = ?", (identifier,))
            conn.commit()
            conn.close()
            session['user_id'] = user['id']
            session['user_name'] = user['name'] or user['username']
            session['user_email'] = user['email']
            session['user_role'] = user['role'] if 'role' in user.keys() and user['role'] else 'customer'
            flash(f"Welcome back, {session['user_name']}.", "success")
            return redirect(url_for('home'))
        else:
            current_attempts = (record['attempts'] + 1) if record else 1
            lock_until = datetime.now() + timedelta(minutes=5) if current_attempts >= 5 else (datetime.now() if not record else record['locked_until'])
            cursor.execute("INSERT OR REPLACE INTO login_attempts (ip_or_email, attempts, locked_until) VALUES (?, ?, ?)", (identifier, current_attempts, lock_until))
            conn.commit()
            conn.close()
            if current_attempts >= 5:
                flash("Too many failed attempts. Account locked for 5 minutes.", "danger")
            else:
                flash(f"Invalid credentials. Attempt {current_attempts} of 5.", "danger")
            return redirect(url_for('login'))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Customer Login - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="card p-4 shadow-sm" style="width: 380px; margin: 100px auto;">
            <h4 class="fw-bold text-center mb-3">Customer Login</h4>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div class="form-group"><label class="form-label">Email Address</label><input type="email" name="email" class="form-control" required></div>
                <div class="form-group"><label class="form-label">Password</label><input type="password" name="password" class="form-control" required></div>
                <button type="submit" class="btn btn-primary w-100 fw-bold">Login</button>
            </form>
            <div style="display: flex; justify-content: space-between; margin-top: 15px; font-size: 13px;">
                <a href="/register">Register here</a>
                <a href="/forgot_password" class="text-muted">Forgot password?</a>
            </div>
        </div>
    </body>
    </html>
    """)

@app.route('/forgot_password', methods=['GET', 'POST'])
@csrf_required
def forgot_password():
    reset_link = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            expires_at = datetime.now() + timedelta(minutes=15)
            cursor.execute("INSERT INTO reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)", (user['id'], token, expires_at))
            conn.commit()
            reset_link = url_for('reset_password', token=token, _external=True)
            flash("Password reset link generated.", "info")
        else:
            flash("No account found with that email address.", "danger")
        conn.close()
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Forgot Password - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="card p-4 shadow-sm" style="width: 400px; margin: 100px auto;">
            <h4 class="fw-bold text-center mb-3">🔑 Forgot Password</h4>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div class="form-group"><label class="form-label">Email Address</label><input type="email" name="email" class="form-control" required></div>
                <button type="submit" class="btn btn-primary w-100 fw-bold">Generate Reset Link</button>
            </form>
            {% if reset_link %}<div style="margin-top: 15px; padding: 8px; background: var(--bg-light); border: 1px solid var(--border-color); border-radius: 4px; font-size: 12px;"><a href="{{ reset_link }}">{{ reset_link }}</a></div>{% endif %}
            <div class="text-center mt-3" style="font-size: 13px;"><a href="/login">Back to Login</a></div>
        </div>
    </body>
    </html>
    """, reset_link=reset_link)

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
@csrf_required
def reset_password(token):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reset_tokens WHERE token = ?", (token,))
    token_record = cursor.fetchone()
    if not token_record or token_record['used'] == 1:
        conn.close()
        flash("Invalid or used reset token.", "danger")
        return redirect(url_for('forgot_password'))
    conn.close()

    if request.method == 'POST':
        new_password = request.form.get('password', '').strip()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, token_record['user_id']))
        cursor.execute("UPDATE reset_tokens SET used = 1 WHERE id = ?", (token_record['id'],))
        conn.commit()
        conn.close()
        flash("Password successfully updated! Please log in.", "success")
        return redirect(url_for('login'))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Reset Password - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="card p-4 shadow-sm" style="width: 380px; margin: 100px auto;">
            <h4 class="fw-bold text-center mb-3">Set New Password</h4>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div class="form-group"><label class="form-label">New Password</label><input type="password" name="password" class="form-control" required></div>
                <button type="submit" class="btn btn-success w-100 fw-bold">Update Password</button>
            </form>
        </div>
    </body>
    </html>
    """)

@app.route('/addresses')
def list_addresses():
    if not session.get('user_id'):
        flash("Please log in to manage your addresses.", "warning")
        return redirect(url_for('login'))
        
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM addresses WHERE user_id = ? ORDER BY is_default DESC, id DESC", (user_id,))
    addr_list = cursor.fetchall()
    conn.close()

    cards = ""
    for a in addr_list:
        default_badge = '<span style="background: var(--success); color: #fff; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin-left: 8px;">Default</span>' if a['is_default'] else ''
        default_btn = '' if a['is_default'] else f'''
        <form method="POST" action="/address/{a['id']}/default" style="display: inline;">
            <input type="hidden" name="csrf_token" value="{session.get('csrf_token', '')}">
            <button type="submit" class="btn btn-outline" style="padding: 4px 8px; font-size: 12px; color: var(--success); border-color: var(--success);">Set as Default</button>
        </form>
        '''
        cards += f"""
        <div class="card mb-3 shadow-sm">
            <div style="padding: 12px;">
                <h5 style="font-weight: bold; margin: 0 0 6px 0;">{a['label']}{default_badge}</h5>
                <p style="margin: 0 0 4px 0;">{a['line1']}</p>
                <p class="text-muted" style="margin: 0 0 10px 0; font-size: 13px;">{a['city']} - {a['pincode']}</p>
                <div style="display: flex; gap: 8px;">
                    <a href="/address/{a['id']}/edit" class="btn btn-outline" style="padding: 4px 8px; font-size: 12px;">Edit</a>
                    <a href="/address/{a['id']}/delete" class="btn btn-outline" style="padding: 4px 8px; font-size: 12px; color: var(--danger); border-color: var(--danger);">Delete</a>
                    {default_btn}
                </div>
            </div>
        </div>
        """

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Address Book - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light" style="padding: 24px;">
    <div class="container card" style="max-width: 700px; padding: 24px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
            <h3 style="font-weight: bold; margin: 0;">📍 Shipping Address Book</h3>
            <a href="/address/add" class="btn btn-primary" style="font-size: 13px;">+ Add New Address</a>
        </div>
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}{% for cat, msg in messages %}<div class="alert alert-{{cat}}">{{msg}}</div>{% endfor %}{% endif %}
        {% endwith %}
        """ + (cards or "<p class='text-muted text-center' style='padding: 20px 0;'>No addresses saved yet.</p>") + """
        <a href="/" class="btn btn-outline w-100" style="margin-top: 15px; text-align: center;">Back to Storefront</a>
    </div>
    </body>
    </html>
    """)

@app.route('/address/add', methods=['GET', 'POST'])
@csrf_required
def add_address():
    if not session.get('user_id'):
        flash("Please log in to add an address.", "warning")
        return redirect(url_for('login'))

    if request.method == 'POST':
        user_id = session['user_id']
        label = request.form.get('label', '').strip()
        line1 = request.form.get('line1', '').strip()
        city = request.form.get('city', '').strip()
        pincode = request.form.get('pincode', '').strip()
        is_default = 1 if request.form.get('is_default') else 0

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN TRANSACTION")
            if is_default:
                cursor.execute("UPDATE addresses SET is_default = 0 WHERE user_id = ?", (user_id,))
            cursor.execute("INSERT INTO addresses (user_id, label, line1, city, pincode, is_default) VALUES (?, ?, ?, ?, ?, ?)",
                           (user_id, label, line1, city, pincode, is_default))
            conn.commit()
            flash("Address saved successfully!", "success")
            return redirect(url_for('list_addresses'))
        except Exception as e:
            conn.rollback()
            flash(f"Error adding address: {str(e)}", "danger")
        finally:
            conn.close()

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Add Address - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light" style="padding: 24px;">
    <div class="container card" style="max-width: 500px; padding: 24px;">
        <h4 style="font-weight: bold; margin-bottom: 16px;">Add New Address</h4>
        <form method="POST">
            <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
            <div class="form-group"><label class="form-label">Address Label (e.g. Home, Office)</label><input type="text" name="label" class="form-control" required></div>
            <div class="form-group"><label class="form-label">Address Line 1</label><input type="text" name="line1" class="form-control" required></div>
            <div class="form-group"><label class="form-label">City</label><input type="text" name="city" class="form-control" required></div>
            <div class="form-group"><label class="form-label">Pincode</label><input type="text" name="pincode" class="form-control" required></div>
            <div style="margin-bottom: 16px;"><input type="checkbox" name="is_default" id="def"> <label for="def" style="font-size: 14px;">Set as Default Address</label></div>
            <button type="submit" class="btn btn-success w-100 fw-bold" style="margin-bottom: 8px;">Save Address</button>
            <a href="/addresses" style="display: block; text-align: center; color: var(--text-muted); font-size: 13px; text-decoration: none;">Cancel</a>
        </form>
    </div>
    </body>
    </html>
    """)

@app.route('/address/<int:address_id>/edit', methods=['GET', 'POST'])
@csrf_required
def edit_address(address_id):
    if not session.get('user_id'): return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM addresses WHERE id = ? AND user_id = ?", (address_id, user_id))
    addr = cursor.fetchone()

    if not addr:
        conn.close()
        flash("Address not found.", "danger")
        return redirect(url_for('list_addresses'))

    if request.method == 'POST':
        label = request.form.get('label', '').strip()
        line1 = request.form.get('line1', '').strip()
        city = request.form.get('city', '').strip()
        pincode = request.form.get('pincode', '').strip()
        is_default = 1 if request.form.get('is_default') else 0

        try:
            cursor.execute("BEGIN TRANSACTION")
            if is_default:
                cursor.execute("UPDATE addresses SET is_default = 0 WHERE user_id = ?", (user_id,))
            cursor.execute("UPDATE addresses SET label = ?, line1 = ?, city = ?, pincode = ?, is_default = ? WHERE id = ? AND user_id = ?",
                           (label, line1, city, pincode, is_default, address_id, user_id))
            conn.commit()
            flash("Address updated successfully!", "success")
            return redirect(url_for('list_addresses'))
        except Exception as e:
            conn.rollback()
            flash(f"Error updating address: {str(e)}", "danger")
        finally:
            conn.close()

    conn.close()
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Edit Address - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light" style="padding: 24px;">
    <div class="container card" style="max-width: 500px; padding: 24px;">
        <h4 style="font-weight: bold; margin-bottom: 16px;">Edit Address</h4>
        <form method="POST">
            <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
            <div class="form-group"><label class="form-label">Address Label</label><input type="text" name="label" value=""" + f'"{addr["label"]}"' + """ class="form-control" required></div>
            <div class="form-group"><label class="form-label">Address Line 1</label><input type="text" name="line1" value=""" + f'"{addr["line1"]}"' + """ class="form-control" required></div>
            <div class="form-group"><label class="form-label">City</label><input type="text" name="city" value=""" + f'"{addr["city"]}"' + """ class="form-control" required></div>
            <div class="form-group"><label class="form-label">Pincode</label><input type="text" name="pincode" value=""" + f'"{addr["pincode"]}"' + """ class="form-control" required></div>
            <div style="margin-bottom: 16px;"><input type="checkbox" name="is_default" id="def" """ + ('checked' if addr['is_default'] else '') + """> <label for="def" style="font-size: 14px;">Set as Default Address</label></div>
            <button type="submit" class="btn btn-primary w-100 fw-bold" style="margin-bottom: 8px;">Update Address</button>
            <a href="/addresses" style="display: block; text-align: center; color: var(--text-muted); font-size: 13px; text-decoration: none;">Cancel</a>
        </form>
    </div>
    </body>
    </html>
    """)

@app.route('/address/<int:address_id>/delete')
def delete_address(address_id):
    if not session.get('user_id'): return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM addresses WHERE id = ? AND user_id = ?", (address_id, user_id))
    conn.commit()
    conn.close()
    flash("Address deleted.", "info")
    return redirect(url_for('list_addresses'))

@app.route('/address/<int:address_id>/default', methods=['POST'])
@csrf_required
def set_default_address(address_id):
    if not session.get('user_id'): return redirect(url_for('login'))
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION")
        cursor.execute("UPDATE addresses SET is_default = 0 WHERE user_id = ?", (user_id,))
        cursor.execute("UPDATE addresses SET is_default = 1 WHERE id = ? AND user_id = ?", (address_id, user_id))
        conn.commit()
        flash("Default address updated.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error: {str(e)}", "danger")
    finally:
        conn.close()
    return redirect(url_for('list_addresses'))

@app.route('/cart/add/<int:book_id>', methods=['GET', 'POST'])
@csrf_required
def add_to_cart(book_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM books WHERE id = ?", (book_id,))
    book = cursor.fetchone()
    conn.close()
    if not book:
        return redirect(url_for('home'))
    cart = session.get('cart', {})
    cart[str(book_id)] = cart.get(str(book_id), 0) + 1
    session['cart'] = cart
    flash(f"Added '{book['title']}' to cart!", "success")
    return redirect(url_for('cart'))

@app.route('/coupon/apply', methods=['POST'])
@csrf_required
def apply_coupon():
    code = request.form.get('coupon_code', '').strip().upper()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM coupons WHERE code = ? AND is_active = 1", (code,))
    coupon = cursor.fetchone()
    conn.close()

    if coupon:
        session['applied_coupon'] = {
            'code': coupon['code'],
            'discount_type': coupon['discount_type'],
            'discount_value': coupon['discount_value']
        }
        flash(f"Promo code '{code}' applied successfully!", "success")
    else:
        flash("Invalid or expired coupon code.", "danger")
    return redirect(url_for('cart'))

@app.route('/coupon/remove')
def remove_coupon():
    session.pop('applied_coupon', None)
    flash("Promo code removed.", "info")
    return redirect(url_for('cart'))

@app.route('/wishlist/add/<int:book_id>', methods=['POST'])
@csrf_required
def add_to_wishlist(book_id):
    if not session.get('user_id'):
        flash("Please log in to manage your wishlist.", "warning")
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM wishlist WHERE user_id = ? AND book_id = ?", (user_id, book_id))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO wishlist (user_id, book_id) VALUES (?, ?)", (user_id, book_id))
        conn.commit()
        flash("Added book to your wishlist!", "success")
    else:
        flash("Book is already in your wishlist.", "info")
    conn.close()
    return redirect(url_for('wishlist'))

@app.route('/wishlist')
def wishlist():
    if not session.get('user_id'):
        flash("Please log in to view your wishlist.", "warning")
        return redirect(url_for('login'))
        
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT b.*, w.id as wishlist_id 
        FROM wishlist w 
        JOIN books b ON w.book_id = b.id 
        WHERE w.user_id = ?
    """, (user_id,))
    wishlist_items = cursor.fetchall()
    conn.close()

    items_html = ""
    for item in wishlist_items:
        price_str = format_inr(item['price'])
        items_html += f"""
        <div class="card" style="display: flex; flex-direction: row; justify-content: space-between; align-items: center; padding: 12px; margin-bottom: 12px;">
            <div style="display: flex; align-items: center; gap: 16px;">
                <img src="{item['image_url']}" style="height: 60px; object-fit: contain;">
                <div>
                    <h5 style="font-weight: bold; margin: 0 0 4px 0;">{item['title']}</h5>
                    <small class="text-muted">by {item['author']}</small>
                    <div style="color: var(--primary); font-weight: bold; margin-top: 4px;">{price_str}</div>
                </div>
            </div>
            <div style="display: flex; gap: 8px;">
                <form method="POST" action="/cart/add/{item['id']}" style="margin: 0;">
                    <input type="hidden" name="csrf_token" value="{session.get('csrf_token', '')}">
                    <button type="submit" class="btn btn-warning" style="padding: 6px 12px; font-size: 13px;">Move to Cart</button>
                </form>
                <a href="/wishlist/remove/{item['wishlist_id']}" class="btn btn-outline" style="padding: 6px 12px; font-size: 13px; color: var(--danger); border-color: var(--danger);">Remove</a>
            </div>
        </div>
        """

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>My Wishlist - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light" style="padding: 24px;">
    <div class="container card" style="max-width: 700px; padding: 24px;">
        <h3 style="font-weight: bold; margin-bottom: 20px;">❤️ My Wishlist</h3>
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}{% for cat, msg in messages %}<div class="alert alert-{{cat}}">{{msg}}</div>{% endfor %}{% endif %}
        {% endwith %}
        """ + (items_html or "<p class='text-muted text-center' style='padding: 20px 0;'>Your wishlist is empty.</p>") + """
        <a href="/" class="btn btn-outline w-100" style="margin-top: 15px; text-align: center;">Back to Storefront</a>
    </div>
    </body>
    </html>
    """)

@app.route('/wishlist/remove/<int:wishlist_id>')
def remove_from_wishlist(wishlist_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM wishlist WHERE id = ?", (wishlist_id,))
    conn.commit()
    conn.close()
    flash("Item removed from wishlist.", "info")
    return redirect(url_for('wishlist'))

@app.route('/cart')
def cart():
    cart_items = session.get('cart', {})
    conn = get_db_connection()
    cursor = conn.cursor()
    books_in_cart, subtotal = [], 0
    for book_id, qty in cart_items.items():
        cursor.execute("SELECT * FROM books WHERE id = ?", (int(book_id),))
        book = cursor.fetchone()
        if book:
            item_total = book['price'] * qty
            subtotal += item_total
            books_in_cart.append({
                'title': book['title'], 
                'price': book['price'], 
                'quantity': qty, 
                'item_total': item_total, 
                'image_url': book['image_url']
            })
    conn.close()

    discount = 0
    applied_coupon = session.get('applied_coupon')
    if applied_coupon and books_in_cart:
        if applied_coupon['discount_type'] == 'percentage':
            discount = int(subtotal * applied_coupon['discount_value'] // 100)
        elif applied_coupon['discount_type'] == 'fixed':
            discount = min(subtotal, int(applied_coupon['discount_value']))

    final_total = max(0, subtotal - discount)
    session['calculated_total'] = final_total
    session['applied_discount'] = discount

    items_html = ""
    for i in books_in_cart:
        price_str = format_inr(i['item_total'])
        items_html += f"<li class='list-group-item' style='display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid var(--border-color);'><span>{i['title']} (x{i['quantity']})</span><span>{price_str}</span></li>"
    
    if not items_html:
        items_html = "<div class='text-muted text-center' style='padding: 15px 0;'>Cart is empty</div>"

    coupon_section = f"""
    <form method="POST" action="/coupon/apply" style="display: flex; gap: 8px; margin-bottom: 16px;">
        <input type="hidden" name="csrf_token" value="{session.get('csrf_token', '')}">
        <input type="text" name="coupon_code" class="form-control" placeholder="Promo Code (e.g. SAVE10)" required style="flex: 1;">
        <button class="btn btn-outline" type="submit">Apply Coupon</button>
    </form>
    """

    subtotal_str = format_inr(subtotal)
    discount_str = format_inr(discount)
    final_total_str = format_inr(final_total)
    disabled_cls = '' if books_in_cart else 'opacity: 0.6; pointer-events: none;'

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Cart - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light" style="padding: 24px;">
    <div class="container card" style="max-width: 600px; padding: 24px;">
        <h3 style="font-weight: bold; margin-bottom: 20px;">Shopping Cart</h3>
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}{% for cat, msg in messages %}<div class="alert alert-{{cat}}">{{msg}}</div>{% endfor %}{% endif %}
        {% endwith %}
        <ul style="list-style: none; padding: 0; margin: 0 0 16px 0;">""" + items_html + """</ul>
        """ + coupon_section + f"""
        <div style="display: flex; justify-content: space-between; color: var(--text-muted); font-size: 14px; margin-bottom: 4px;"><span>Subtotal:</span><span>{subtotal_str}</span></div>
        <div style="display: flex; justify-content: space-between; color: var(--text-muted); font-size: 14px; margin-bottom: 12px;"><span>Discount:</span><span>-{discount_str}</span></div>
        <div style="display: flex; justify-content: space-between; font-weight: bold; font-size: 18px; margin-bottom: 20px;"><span>Total Payable:</span><span style="color: var(--primary);">{final_total_str}</span></div>
        <a href="/checkout" class="btn btn-warning w-100 fw-bold" style="text-align: center; {disabled_cls}">Proceed to Checkout</a>
        <a href="/" class="btn btn-outline w-100" style="margin-top: 8px; text-align: center;">Back to Home Storefront</a>
    </div>
    </body>
    </html>
    """)

@app.route('/checkout', methods=['GET', 'POST'])
@csrf_required
def checkout():
    cart = session.get('cart', {})
    if not cart: return redirect(url_for('home'))
    total = session.get('calculated_total', 0)
    discount = session.get('applied_discount', 0)
    user_id = session.get('user_id')

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        saved_addresses = []
        default_address_str = ""
        if user_id:
            cursor.execute("SELECT * FROM addresses WHERE user_id = ? ORDER BY is_default DESC, id DESC", (user_id,))
            saved_addresses = cursor.fetchall()
            if saved_addresses:
                def_addr = saved_addresses[0]
                default_address_str = f"{def_addr['line1']}, {def_addr['city']} - {def_addr['pincode']}"

        if request.method == 'POST':
            try:
                cursor.execute("BEGIN TRANSACTION")
                cursor.execute("""
                    INSERT INTO orders (user_id, customer_name, customer_email, customer_phone, customer_address, total_amount, discount_amount, status) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'Placed')
                """, (user_id, request.form.get('name'), request.form.get('email'), request.form.get('phone'), request.form.get('address'), total, discount))
                order_id = cursor.lastrowid

                changed_by = session.get('user_name') or session.get('user_email') or 'Customer'
                cursor.execute("""
                    INSERT INTO order_status_history (order_id, status, changed_by, changed_at, note)
                    VALUES (?, 'Placed', ?, DATETIME('now'), 'Order placed successfully')
                """, (order_id, changed_by))

                for book_id_str, qty in cart.items():
                    book_id = int(book_id_str)
                    cursor.execute("SELECT price, stock FROM books WHERE id = ?", (book_id,))
                    book = cursor.fetchone()
                    if not book or book['stock'] < qty:
                        raise Exception(f"Insufficient stock for book ID {book_id}")
                    
                    cursor.execute("UPDATE books SET stock = stock - ? WHERE id = ?", (qty, book_id))
                    cursor.execute("INSERT INTO order_items (order_id, book_id, quantity, price_at_purchase) VALUES (?, ?, ?, ?)", (order_id, book_id, qty, book['price']))
                    cursor.execute("INSERT INTO stock_log (book_id, change, reason, user_id) VALUES (?, ?, 'purchase', ?)", (book_id, -qty, user_id))

                conn.commit()
                session['cart'] = {}
                session.pop('applied_coupon', None)
                flash("Order placed successfully!", "success")
                return redirect(url_for('orders'))
            except Exception as e:
                conn.rollback()
                flash(f"Checkout failed: {str(e)}", "danger")
                return redirect(url_for('cart'))

        name = session.get('user_name', '')
        email = session.get('user_email', '')
    finally:
        conn.close()

    address_options = ""
    if saved_addresses:
        address_options = "<label class='form-label' style='margin-top: 12px;'>Or Pick a Saved Address:</label><select id='saved_addr' class='form-control' style='margin-bottom: 12px;' onchange='document.getElementById(\"addr_input\").value = this.value;'><option value=''>-- Select Saved Address --</option>"
        for a in saved_addresses:
            full_str = f"{a['line1']}, {a['city']} - {a['pincode']}"
            is_def_lbl = " (Default)" if a['is_default'] else ""
            address_options += f"<option value='{full_str}'>{a['label']}{is_def_lbl}: {full_str}</option>"
        address_options += "</select>"

    total_str = format_inr(total)
    discount_str = format_inr(discount)

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Checkout</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light" style="padding: 24px;">
    <div class="container card" style="max-width: 600px; padding: 24px;">
    <h3 style="font-weight: bold; margin-bottom: 20px;">Checkout</h3>
    <form method="POST">
    <input type="hidden" name="csrf_token" value=""" + f'"{session.get("csrf_token", "")}"' + """>
    <div class="form-group"><label class="form-label">Full Name</label><input type="text" name="name" value=""" + f'"{name}"' + """ class="form-control" required></div>
    <div class="form-group"><label class="form-label">Email</label><input type="email" name="email" value=""" + f'"{email}"' + """ class="form-control" required></div>
    <div class="form-group"><label class="form-label">Phone</label><input type="tel" name="phone" class="form-control" required></div>
    """ + address_options + """
    <div class="form-group"><label class="form-label" style="margin-top: 12px;">Delivery Address</label>
    <textarea id="addr_input" name="address" class="form-control" placeholder="Enter delivery address..." required>""" + default_address_str + """</textarea></div>
    <h5 style="margin: 20px 0; color: var(--success); font-weight: bold;">Total Payable: """ + total_str + f" (Saved {discount_str})" + """</h5>
    <button type="submit" class="btn btn-success w-100 fw-bold">Place Order</button>
    </form></div></body></html>
    """)

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
        discount_str = format_inr(o['discount_amount'])
        
        cancel_btn = f"""
        <form method="POST" action="/order/cancel/{o['id']}" style="margin-top: 8px;">
            <input type="hidden" name="csrf_token" value="{session.get('csrf_token', '')}">
            <button type="submit" class="btn btn-outline w-100" style="color: var(--danger); border-color: var(--danger); font-size: 12px;">Cancel & Refund Order</button>
        </form>
        """ if o['status'] == 'Placed' else ""

        orders_html += f"""
        <div class="card" style="padding: 16px; margin-bottom: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h5 style="margin: 0; font-weight: bold;">Order #{o['id']}</h5>
                <span style="background: #e2e8f0; padding: 2px 6px; border-radius: 4px; font-size: 12px;">{o['status']}</span>
            </div>
            <p style="margin: 4px 0;"><b>Shipping Address:</b> {o['customer_address'] or 'N/A'}</p>
            <p style="margin: 4px 0 12px 0;"><b>Total:</b> {total_str} (Discount: {discount_str})</p>
            <a href="/order/track/{o['id']}" class="btn btn-primary w-100" style="text-align: center; font-size: 13px; font-weight: 600; text-decoration: none; display: block; padding: 6px 0;">📦 Track Order</a>
            {cancel_btn}
        </div>
        """

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Orders</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light" style="padding: 24px;">
    <div class="container card" style="max-width: 600px; padding: 24px;">
        <h3 style="font-weight: bold; margin-bottom: 20px;">My Orders</h3>
        """ + (orders_html or "<p class='text-muted'>No orders.</p>") + """
        <a href="/" class="btn btn-outline w-100" style="margin-top: 15px; text-align: center;">Back to Home</a>
    </div>
    </body>
    </html>
    """)

@app.route('/order/track/<int:order_id>')
def track_order(order_id):
    if not session.get('user_id') and not session.get('admin_logged_in'):
        flash("Please log in to track orders.", "warning")
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()

    if not order:
        conn.close()
        return "Order not found.", 404

    user_role = session.get('user_role', 'customer')
    if user_role != 'admin' and user_role != 'staff' and order['user_id'] != session.get('user_id'):
        conn.close()
        return """
        <!DOCTYPE html>
        <html>
        <head><title>403 Forbidden - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
        <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
            <div class="card p-5 text-center shadow-sm" style="max-width: 450px; margin: 100px auto;">
                <h1 class="display-1 text-danger fw-bold" style="color: var(--danger);">403</h1>
                <h4 class="fw-bold mb-3">Access Forbidden</h4>
                <p class="text-muted">You do not have permission to view this order.</p>
                <a href="/orders" class="btn btn-primary mt-3 fw-bold">Return to My Orders</a>
            </div>
        </body>
        </html>
        """, 403

    cursor.execute("SELECT * FROM order_status_history WHERE order_id = ? ORDER BY id ASC", (order_id,))
    history = cursor.fetchall()
    conn.close()

    current_status = order['status'] or 'Placed'
    stages = ['Placed', 'Packed', 'Shipped', 'Delivered']
    
    if current_status == 'Cancelled':
        timeline_html = '<div style="color: var(--danger); font-weight: bold; text-align: center; padding: 20px;">Order has been Cancelled</div>'
    else:
        current_idx = stages.index(current_status) if current_status in stages else 0
        steps_html = ""
        for idx, stage in enumerate(stages):
            is_active = idx <= current_idx
            bg_color = "var(--primary)" if is_active else "#e2e8f0"
            text_color = "#fff" if is_active else "var(--text-dark)"
            steps_html += f"""
            <div style="display: flex; align-items: center; flex: 1; position: relative; text-align: center; flex-direction: column;">
                <div style="width: 36px; height: 36px; border-radius: 50%; background: {bg_color}; color: {text_color}; display: flex; align-items: center; justify-content: center; font-weight: bold; margin-bottom: 8px; z-index: 2;">{idx + 1}</div>
                <div style="font-size: 13px; font-weight: bold; color: {'var(--text-dark)' if is_active else 'var(--text-muted)'};">{stage}</div>
            </div>
            """
        timeline_html = f"""
        <div style="display: flex; justify-content: space-between; position: relative; margin: 30px 0;">
            {steps_html}
        </div>
        """

    history_rows = ""
    for h in history:
        history_rows += f"<tr><td>{h['status']}</td><td>{h['changed_by']}</td><td>{h['changed_at']}</td><td>{h['note'] or ''}</td></tr>"

    history_table = f"""
    <h5 style="font-weight: bold; margin-top: 24px;">Status History Log</h5>
    <table class="table" style="font-size: 13px; margin-top: 8px;">
        <thead><tr><th>Status</th><th>Changed By</th><th>Timestamp</th><th>Note</th></tr></thead>
        <tbody>{history_rows or '<tr><td colspan="4" class="text-center text-muted">No history logged yet.</td></tr>'}</tbody>
    </table>
    """

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Track Order #""" + str(order_id) + """ - PageTurner</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light" style="padding: 24px;">
    <div class="container card" style="max-width: 700px; padding: 24px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
            <h3 style="font-weight: bold; margin: 0;">📦 Order Tracking #""" + str(order_id) + """</h3>
            <a href="/orders" class="btn btn-outline" style="font-size: 13px;">Back to Orders</a>
        </div>
        <p><b>Current Status:</b> <span style="background: #e2e8f0; padding: 2px 6px; border-radius: 4px; font-size: 12px;">""" + current_status + """</span></p>
        """ + timeline_html + """
        """ + history_table + """
    </div>
    </body>
    </html>
    """)

@app.route('/order/cancel/<int:order_id>', methods=['POST'])
@csrf_required
def cancel_order(order_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION")
        cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        order = cursor.fetchone()
        if not order:
            raise Exception("Order not found.")
        if order['status'] != 'Placed':
            raise Exception("Only 'Placed' orders can be cancelled.")
            
        cursor.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,))
        items = cursor.fetchall()
        for item in items:
            cursor.execute("UPDATE books SET stock = stock + ? WHERE id = ?", (item['quantity'], item['book_id']))
            cursor.execute("INSERT INTO stock_log (book_id, change, reason, user_id) VALUES (?, ?, 'cancelled_order', ?)", (item['book_id'], item['quantity'], session.get('user_id')))
            
        cursor.execute("UPDATE orders SET status = 'Cancelled' WHERE id = ?", (order_id,))
        changed_by = session.get('user_name') or session.get('user_email') or 'Customer'
        cursor.execute("""
            INSERT INTO order_status_history (order_id, status, changed_by, changed_at, note)
            VALUES (?, 'Cancelled', ?, DATETIME('now'), 'Order cancelled by customer')
        """, (order_id, changed_by))

        conn.commit()
        flash(f"Order #{order_id} cancelled and refunded.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error: {str(e)}", "danger")
    finally:
        conn.close()
    return redirect(url_for('orders'))

@app.route('/admin/login', methods=['GET', 'POST'])
@csrf_required
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if username == 'admin' and password == 'admin123':
            session['admin_logged_in'] = True
            session['user_role'] = 'admin'
            return redirect(url_for('admin_dashboard'))
        elif username == 'staff' and password == 'staff123':
            session['admin_logged_in'] = True
            session['user_role'] = 'staff'
            return redirect(url_for('admin_dashboard'))
            
        flash("Invalid credentials.", "danger")
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Admin Login</title><link rel="stylesheet" href="/static/style.css"></head>
    <body style="background: var(--text-dark); display: flex; align-items: center; justify-content: center; min-vh-100;">
        <div class="card" style="width: 350px; padding: 24px; margin: 100px auto;">
            <h4 style="text-align: center; margin-bottom: 16px; font-weight: bold;">Admin / Staff Login</h4>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <input type="text" name="username" class="form-control" placeholder="admin or staff" required style="margin-bottom: 10px;">
                <input type="password" name="password" class="form-control" placeholder="password" required style="margin-bottom: 16px;">
                <button type="submit" class="btn btn-warning w-100 fw-bold">Login</button>
            </form>
        </div>
    </body>
    </html>
    """)

@app.route('/admin/dashboard')
@role_required('admin', 'staff')
def admin_dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders ORDER BY id DESC")
    orders_list = cursor.fetchall()
    conn.close()
    return render_template('admin.html', orders=orders_list)

@app.route('/admin/coupons', methods=['GET', 'POST'])
@role_required('admin', 'staff')
@csrf_required
def admin_coupons():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        code = request.form.get('code', '').strip().upper()
        dtype = request.form.get('discount_type', 'percentage')
        dval_raw = float(request.form.get('discount_value', 0))
        dval = int(dval_raw * 100) if dtype == 'fixed' else int(dval_raw)
        cursor.execute("INSERT INTO coupons (code, discount_type, discount_value, is_active) VALUES (?, ?, ?, 1)", (code, dtype, dval))
        conn.commit()
        flash("Coupon created successfully!", "success")
    
    cursor.execute("SELECT * FROM coupons ORDER BY id DESC")
    coupons = cursor.fetchall()
    conn.close()

    rows = ""
    for c in coupons:
        val_display = f"{c['discount_value']}%" if c['discount_type'] == 'percentage' else format_inr(c['discount_value'])
        rows += f"<tr><td>{c['id']}</td><td style='font-weight: bold;'>{c['code']}</td><td>{c['discount_type']}</td><td>{val_display}</td><td><a href='/admin/coupon/delete/{c['id']}' class='btn btn-outline' style='padding: 2px 6px; font-size: 11px; color: var(--danger); border-color: var(--danger);'>Delete</a></td></tr>"

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Manage Coupons</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light" style="padding: 24px;">
    <div class="container card" style="padding: 24px;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;"><h3 style="margin: 0; font-weight: bold;">🏷️ Promo Codes Management</h3><a href="/admin/dashboard" class="btn btn-outline" style="font-size: 13px;">Dashboard</a></div>
    <form method="POST" style="display: grid; grid-template-columns: repeat(4, 1fr) auto; gap: 8px; margin-bottom: 20px;">
    <input type="hidden" name="csrf_token" value=""" + f'"{session.get("csrf_token", "")}"' + """>
    <input type="text" name="code" class="form-control" placeholder="Code (e.g., SUMMER20)" required>
    <select name="discount_type" class="form-control"><option value="percentage">Percentage (%)</option><option value="fixed">Fixed</option></select>
    <input type="number" step="0.01" name="discount_value" class="form-control" placeholder="Value (e.g. 10 or 100)" required>
    <button type="submit" class="btn btn-success" style="font-weight: bold;">Add Code</button>
    </form>
    <table class="table"><thead><tr><th>ID</th><th>Code</th><th>Type</th><th>Value</th><th>Action</th></tr></thead><tbody>""" + rows + """</tbody></table>
    </div></body></html>
    """)

@app.route('/admin/coupon/delete/<int:coupon_id>')
@role_required('admin', 'staff')
def delete_coupon(coupon_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM coupons WHERE id = ?", (coupon_id,))
    conn.commit()
    conn.close()
    flash("Coupon removed.", "info")
    return redirect(url_for('admin_coupons'))

@app.route('/admin/export_orders', endpoint='export_orders')
@role_required('admin')
def export_orders():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders")
    orders_list = cursor.fetchall()
    conn.close()
    csv_data = "Order ID,Customer Name,Email,Phone,Address,Total Amount,Discount,Status,Created At\n"
    for o in orders_list:
        total_inr = f"{o['total_amount'] / 100:.2f}"
        discount_inr = f"{(o['discount_amount'] or 0) / 100:.2f}"
        csv_data += f"{o['id']},{o['customer_name'] or 'Guest'},{o['customer_email'] or ''},{o['customer_phone'] or ''},\"{o['customer_address'] or ''}\",{total_inr},{discount_inr},{o['status']},{o['created_at']}\n"
    return csv_data, 200, {'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename=orders_report.csv'}

@app.route('/admin/analytics')
@role_required('admin', 'staff')
def admin_analytics():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), SUM(total_amount), AVG(total_amount) FROM orders")
    row = cursor.fetchone()
    total_orders, total_revenue_paise, avg_order_paise = row[0] or 0, row[1] or 0, row[2] or 0.0
    conn.close()
    
    rev_str = format_inr(total_revenue_paise)
    aov_str = format_inr(int(avg_order_paise))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Analytics</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light" style="padding: 24px;"><div class="container card" style="padding: 24px;"><h3 style="font-weight: bold;">Sales Analytics</h3>
    <div style="display: grid; grid-template-columns: repeat(3, 1fr); text-align: center; margin: 30px 0;">
        <div><h4 style="color: var(--primary); margin: 0;">Revenue: """ + rev_str + """</h4></div>
        <div><h4 style="margin: 0;">Orders: """ + str(total_orders) + """</h4></div>
        <div><h4 style="margin: 0;">AOV: """ + aov_str + """</h4></div>
    </div>
    <a href="/admin/dashboard" class="btn btn-outline" style="text-align: center; display: block;">Back to Dashboard</a></div></body></html>
    """)

@app.route('/admin/inventory', methods=['GET', 'POST'])
@role_required('admin', 'staff')
@csrf_required
def admin_inventory():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if request.method == 'POST':
            title = request.form.get('title')
            author = request.form.get('author')
            price_rupees = float(request.form.get('price', 0))
            price_paise = int(price_rupees * 100)
            stock = int(request.form.get('stock', 10))
            category = request.form.get('category')
            image_url = request.form.get('image_url', 'https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=400')
            
            cursor.execute("INSERT INTO books (title, author, price, stock, category, image_url) VALUES (?, ?, ?, ?, ?, ?)",
                           (title, author, price_paise, stock, category, image_url))
            book_id = cursor.lastrowid
            cursor.execute("INSERT INTO stock_log (book_id, change, reason, user_id) VALUES (?, ?, 'admin_edit', ?)", (book_id, stock, session.get('user_id')))
            conn.commit()
            flash("Book added successfully!", "success")

        cursor.execute("SELECT * FROM books ORDER BY id DESC")
        books = cursor.fetchall()
    finally:
        conn.close()
    
    books_rows = ""
    for b in books:
        price_str = format_inr(b['price'])
        books_rows += f"<tr><td>{b['id']}</td><td style='font-weight: bold;'>{b['title']}</td><td>{b['author']}</td><td>{price_str}</td><td>{b['stock']}</td><td><span style='background: #e2e8f0; padding: 2px 6px; border-radius: 4px; font-size: 11px;'>{b['category']}</span></td></tr>"
    
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Inventory Control</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light" style="padding: 24px;">
    <div class="container card" style="padding: 24px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
            <h3 style="font-weight: bold; margin: 0;">📦 Inventory Control & Add Book</h3>
            <a href="/admin/dashboard" class="btn btn-outline" style="font-size: 13px;">Dashboard</a>
        </div>
        
        <div class="card" style="background: var(--bg-light); border: 0; padding: 16px; margin-bottom: 20px;">
            <h5 style="font-weight: bold; margin-top: 0;">Add New Book</h5>
            <form method="POST" style="display: grid; grid-template-columns: repeat(5, 1fr) auto; gap: 8px;">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <input type="text" name="title" class="form-control" placeholder="Book Title" required>
                <input type="text" name="author" class="form-control" placeholder="Author" required>
                <input type="number" step="0.01" name="price" class="form-control" placeholder="Price" required>
                <input type="number" name="stock" class="form-control" placeholder="Stock" required>
                <input type="text" name="category" class="form-control" placeholder="Category" required>
                <button type="submit" class="btn btn-success" style="font-weight: bold;">Add Book</button>
            </form>
        </div>

        <table class="table">
            <thead>
                <tr><th>ID</th><th>Title</th><th>Author</th><th>Price</th><th>Stock</th><th>Category</th></tr>
            </thead>
            <tbody>""" + books_rows + """</tbody>
        </table>
    </div>
    </body>
    </html>
    """)

@app.route('/admin/reviews')
@role_required('admin', 'staff')
def admin_reviews():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reviews ORDER BY id DESC")
    reviews = cursor.fetchall()
    conn.close()

    rows = "".join([f"<tr><td>{r['id']}</td><td style='font-weight: bold;'>{r['name']}</td><td>{r['book_title']}</td><td><span style='background: #fef08a; padding: 2px 6px; border-radius: 4px; font-size: 11px;'>{r['rating']} ⭐</span></td><td>{r['comment']}</td><td><a href='/admin/review/delete/{r['id']}' class='btn btn-outline' style='padding: 2px 6px; font-size: 11px; color: var(--danger); border-color: var(--danger);'>Delete</a></td></tr>" for r in reviews])
    
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Review Moderation</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light" style="padding: 24px;">
        <div class="container card" style="padding: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                <h3 style="font-weight: bold; margin: 0;">💬 Customer Review Moderation</h3>
                <a href="/admin/dashboard" class="btn btn-outline" style="font-size: 13px;">Return to Dashboard</a>
            </div>
            <table class="table">
                <thead>
                    <tr><th>ID</th><th>Reviewer</th><th>Book Title</th><th>Rating</th><th>Comment</th><th>Action</th></tr>
                </thead>
                <tbody>""" + (rows or '<tr><td colspan="6" class="text-center text-muted" style="padding: 20px;">No reviews submitted yet.</td></tr>') + """</tbody>
            </table>
        </div>
    </body>
    </html>
    """)

@app.route('/admin/review/delete/<int:review_id>')
@role_required('admin', 'staff')
def delete_review(review_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
    conn.commit()
    conn.close()
    flash("Review deleted successfully.", "info")
    return redirect(url_for('admin_reviews'))

@app.route('/admin/order/update_status/<int:order_id>', methods=['POST'])
@role_required('admin', 'staff')
@csrf_required
def update_order_status(order_id: int):
    new_status = request.form.get('status', '').strip()
    if not new_status:
        flash("Please select a valid status.", "danger")
        return redirect(url_for('admin_dashboard'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
        order = cursor.fetchone()
        if not order:
            flash("Order not found.", "danger")
            return redirect(url_for('admin_dashboard'))
        
        current_status = order['status'] or 'Placed'
        
        cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        
        changed_by = session.get('user_name') or session.get('user_email') or 'Admin'
        cursor.execute("""
            INSERT INTO order_status_history (order_id, status, changed_by, changed_at, note)
            VALUES (?, ?, ?, DATETIME('now'), ?)
        """, (order_id, new_status, changed_by, f"Status updated from {current_status} to {new_status}"))
        
        conn.commit()
        flash(f"Order #{order_id} status updated to {new_status} successfully!", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error updating status: {str(e)}", "danger")
    finally:
        conn.close()
    return redirect(url_for('admin_dashboard'))

@app.errorhandler(403)
def forbidden_error(error):
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>403 Forbidden</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="container card text-center" style="max-width: 450px; padding: 32px; margin: 100px auto;">
            <h1 style="font-size: 72px; color: var(--danger); margin: 0;">403</h1>
            <h3 style="font-weight: bold; margin: 10px 0;">Access Forbidden</h3>
            <p class="text-muted">You do not have permission to access this resource.</p>
            <a href="/" class="btn btn-primary w-100" style="margin-top: 16px;">Return to Home</a>
        </div>
    </body>
    </html>
    """), 403

@app.errorhandler(404)
def not_found_error(error):
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>404 Not Found</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="container card text-center" style="max-width: 450px; padding: 32px; margin: 100px auto;">
            <h1 style="font-size: 72px; color: var(--primary); margin: 0;">404</h1>
            <h3 style="font-weight: bold; margin: 10px 0;">Page Not Found</h3>
            <p class="text-muted">The page you are looking for does not exist or has been moved.</p>
            <a href="/" class="btn btn-primary w-100" style="margin-top: 16px;">Return to Home</a>
        </div>
    </body>
    </html>
    """), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>500 Server Error</title><link rel="stylesheet" href="/static/style.css"></head>
    <body class="bg-light d-flex align-items-center justify-content-center min-vh-100">
        <div class="container card text-center" style="max-width: 450px; padding: 32px; margin: 100px auto;">
            <h1 style="font-size: 72px; color: var(--warning); margin: 0;">500</h1>
            <h3 style="font-weight: bold; margin: 10px 0;">Internal Server Error</h3>
            <p class="text-muted">Something went wrong on our end. Please try again later.</p>
            <a href="/" class="btn btn-primary w-100" style="margin-top: 16px;">Return to Home</a>
        </div>
    </body>
    </html>
    """), 500

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)