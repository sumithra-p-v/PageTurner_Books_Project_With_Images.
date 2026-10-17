import sqlite3
import secrets
import csv
import io
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, jsonify, flash, abort, make_response

app = Flask(__name__)
app.secret_key = 'super_secret_pageturner_key'
DATABASE = 'database.db'

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
        return f"{val // 100}"
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

    conn.commit()
    conn.close()

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

    cursor.execute("SELECT * FROM reviews ORDER BY id DESC")
    recent_reviews = cursor.fetchall()
    conn.close()

    wishlist_ids = session.get('wishlist', [])

    book_cards = ""
    for b in books:
        price_display = format_inr(b['price'])
        is_wishlisted = b['id'] in wishlist_ids
        heart_color = "#ef4444" if is_wishlisted else "#cbd5e1"
        heart_icon_class = "fa-solid" if is_wishlisted else "fa-regular"

        book_cards += f"""
        <div style="border: 1px solid #f1f5f9; border-radius: 12px; padding: 16px; background: #fff; position: relative;">
            <a href="/wishlist/toggle/{b['id']}" title="Toggle Wishlist" style="position: absolute; top: 12px; right: 12px; background: white; border-radius: 50%; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 6px rgba(0,0,0,0.1); text-decoration: none; color: {heart_color}; font-size: 16px;">
                <i class="{heart_icon_class} fa-heart"></i>
            </a>
            <span style="position: absolute; top: 12px; left: 12px; background: #ff5e62; color: white; font-size: 10px; padding: 3px 8px; border-radius: 4px; font-weight: 700;">POPULAR</span>
            <img src="{b['image_url']}" style="width: 100%; height: 200px; object-fit: contain; margin-bottom: 12px; border-radius: 4px;">
            <div style="font-size: 14px; font-weight: 700; margin: 4px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{b['title']}</div>
            <div style="font-size: 12px; color: #64748b; margin-bottom: 6px;">{b['author']}</div>
            <div style="color: #f59e0b; font-size: 11px; margin-bottom: 8px;">★ ★ ★ ★ ★ ({b['rating']})</div>
            <div style="font-size: 16px; font-weight: 800; color: #ff5e62; margin-bottom: 12px;">₹{price_display}</div>
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
            .logo { font-size: 24px; font-weight: 800; color: #f59e0b; text-decoration: none; display: flex; align-items: center; gap: 6px; }
            .nav-links { display: flex; gap: 24px; list-style: none; margin: 0; padding: 0; }
            .nav-links a { text-decoration: none; color: var(--text-dark); font-weight: 600; font-size: 14px; }
            .header-icons { display: flex; align-items: center; gap: 18px; font-size: 16px; }
            .header-icons a { color: var(--text-dark); text-decoration: none; }
            .hero-banner { background: linear-gradient(135deg, #e0f2fe 0%, #fbcfe8 50%, #fef08a 100%); border-radius: 20px; margin: 20px 40px; padding: 40px 60px; display: flex; align-items: center; justify-content: space-between; }
            .btn-shop { background: var(--pink-primary); color: white; padding: 12px 28px; border-radius: 30px; border: none; font-weight: 700; text-decoration: none; display: inline-block; box-shadow: 0 4px 14px rgba(255, 94, 98, 0.4); }
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
                <li><a href="/admin/dashboard" style="color: #2563eb; font-weight: bold;">ADMIN DASHBOARD</a></li>
            </ul>
            <div class="header-icons">
                <a href="/wishlist" title="Wishlist"><i class="fa-regular fa-heart"></i></a>
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

        <div style="padding: 0 40px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center;">
            <h3 style="font-weight: 700; margin: 0;">Best Selling Items</h3>
        </div>

        <div class="books-grid">
            {{ book_cards | safe }}
        </div>

        <div style="padding: 0 40px; margin-bottom: 40px;">
            <h4 style="font-weight: 700; margin-bottom: 16px;">💬 Customer Reviews</h4>
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
    </body>
    </html>
    """, book_cards=book_cards, review_cards=review_cards)

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

    csrf_tok = session.get('csrf_token', '')
    price_str = format_inr(book['price'])
    wishlist_ids = session.get('wishlist', [])
    is_wishlisted = book['id'] in wishlist_ids
    heart_text = "❤️ Remove from Wishlist" if is_wishlisted else "🤍 Add to Wishlist"

    recommendations_html = ""
    for r_book in recommended_books:
        r_price = format_inr(r_book['price'])
        recommendations_html += f"""
        <div style="background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px; text-align: center; box-shadow: 0 2px 6px rgba(0,0,0,0.02);">
            <img src="{r_book['image_url']}" style="height: 130px; object-fit: contain; margin-bottom: 8px; border-radius: 4px;">
            <div style="font-size: 13px; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 2px;">{r_book['title']}</div>
            <div style="color: #f59e0b; font-size: 11px; margin-bottom: 4px;">★ {r_book['rating']}</div>
            <div style="color: #ff5e62; font-weight: 800; font-size: 14px; margin-bottom: 8px;">₹{r_price}</div>
            <a href="/book/{r_book['id']}" style="display: block; background: #2563eb; color: white; padding: 6px; font-size: 12px; text-decoration: none; border-radius: 4px; font-weight: bold;">View Book</a>
        </div>
        """

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>{{ book.title }} - PageTurner</title></head>
    <body style="background-color: #f8fafc; padding: 40px; font-family: sans-serif;">
    <div style="max-width: 800px; padding: 24px; background:white; margin:auto; border-radius:12px; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
        <div style="display: flex; gap: 24px; align-items: center; flex-wrap: wrap;">
            <div style="text-align: center; flex: 1; min-width: 200px;">
                <img src="{{ book.image_url }}" style="max-height: 250px; max-width: 100%; border-radius: 8px;">
            </div>
            <div style="flex: 2; min-width: 250px;">
                <h2 style="font-weight: bold; margin-top: 0;">{{ book.title }}</h2>
                <h5 style="color:#64748b;">by {{ book.author }}</h5>
                <p style="margin: 8px 0;"><span style="background: #e2e8f0; padding: 3px 8px; border-radius: 4px; font-size: 12px;">{{ book.category }}</span> <span style="background: #fef08a; padding: 3px 8px; border-radius: 4px; font-size: 12px;">{{ book.rating }} ⭐</span></p>
                <h3 style="color: #ff5e62; font-weight: bold;">₹{{ price_str }}</h3>
                <p style="font-size: 13px; color:#64748b;">Stock Available: {{ book.stock }}</p>
                <form method="POST" action="/cart/add/{{ book.id }}">
                    <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
                    <button type="submit" style="font-weight: bold; width: 100%; margin-bottom: 8px; background:#fbbf24; border:none; padding:10px; border-radius:6px; cursor:pointer;">Add to Cart</button>
                </form>
                <a href="/wishlist/toggle/{{ book.id }}" style="display:block; text-align:center; font-size: 13px; width: 100%; color: #ef4444; border:1px solid #ef4444; padding:8px; box-sizing:border-box; border-radius:4px; text-decoration:none; font-weight:bold;">{{ heart_text }}</a>
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
    """, book=book, csrf_tok=csrf_tok, price_str=price_str, heart_text=heart_text, recommendations_html=recommendations_html)

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
                        <p style="margin: 0; color: #64748b; font-size: 12px;">by {b['author']}</p>
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

@app.route('/cart/add/<int:book_id>', methods=['POST'])
@csrf_required
def add_to_cart(book_id):
    cart = session.get('cart', {})
    cart[str(book_id)] = cart.get(str(book_id), 0) + 1
    session['cart'] = cart
    flash("Added book to cart!", "success")
    return redirect(request.referrer or url_for('cart'))

@app.route('/cart')
def cart():
    cart_items = session.get('cart', {})
    conn = get_db_connection()
    cursor = conn.cursor()
    subtotal = 0
    items_html = ""
    for book_id, qty in cart_items.items():
        cursor.execute("SELECT * FROM books WHERE id = ?", (int(book_id),))
        book = cursor.fetchone()
        if book:
            item_total = book['price'] * qty
            subtotal += item_total
            items_html += f"""
            <div style="display:flex; justify-content:space-between; align-items:center; padding:12px 0; border-bottom:1px solid #e2e8f0;">
                <div>
                    <strong>{book['title']}</strong>
                    <div style="font-size:12px; color:#64748b;">Qty: {qty}</div>
                </div>
                <div style="font-weight:bold; color:#ff5e62;">₹{format_inr(item_total)}</div>
            </div>
            """
    conn.close()

    session['calculated_total'] = subtotal
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Shopping Cart</title></head>
    <body style="font-family: sans-serif; padding: 40px; background: #f8fafc;">
        <div style="max-width: 500px; background: white; padding: 30px; border-radius: 10px; margin: auto; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
            <h2>🛒 Shopping Cart</h2>
            {{ items_html | safe if items_html else "<p style='color:#64748b;'>Your cart is empty.</p>" }}
            <p style="margin-top: 20px; font-size:18px;"><b>Subtotal: ₹{{ total_str }}</b></p>
            {% if items_html %}
            <a href="/checkout" style="display: block; text-align: center; background: #2563eb; color: white; padding: 12px; text-decoration: none; font-weight: bold; border-radius: 6px;">Proceed to Checkout</a>
            {% endif %}
            <a href="/" style="display: block; text-align: center; margin-top: 14px; color: #1e293b; text-decoration: none; font-weight:600;">← Continue Shopping</a>
        </div>
    </body>
    </html>
    """, items_html=items_html, total_str=format_inr(subtotal))

@app.route('/checkout', methods=['GET', 'POST'])
@csrf_required
def checkout():
    if not session.get('cart'):
        return redirect(url_for('cart'))
    if request.method == 'POST':
        total = session.get('calculated_total', 0)
        c_name = request.form.get('name', '').strip()
        c_email = request.form.get('email', '').strip()
        c_phone = request.form.get('phone', '').strip()
        c_address = request.form.get('address', '').strip()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO orders (user_id, customer_name, customer_email, customer_phone, customer_address, total_amount, status) 
            VALUES (?, ?, ?, ?, ?, ?, 'Placed')
        """, (session.get('user_id'), c_name, c_email, c_phone, c_address, total))
        conn.commit()
        conn.close()
        
        session.pop('cart', None)
        flash("Order placed successfully!", "success")
        return redirect(url_for('orders'))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Checkout</title></head>
    <body style="font-family: sans-serif; padding: 40px; background: #f8fafc;">
        <div style="max-width: 500px; background: white; padding: 30px; border-radius: 10px; margin: auto; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
            <h2>Checkout Details</h2>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div style="margin-bottom: 12px;"><label style="font-weight: bold; display: block; margin-bottom: 4px;">Full Name</label><input type="text" name="name" required style="width:100%; padding: 8px; border: 1px solid #cbd5e1; border-radius: 4px; box-sizing:border-box;"></div>
                <div style="margin-bottom: 12px;"><label style="font-weight: bold; display: block; margin-bottom: 4px;">Email</label><input type="email" name="email" required style="width:100%; padding: 8px; border: 1px solid #cbd5e1; border-radius: 4px; box-sizing:border-box;"></div>
                <div style="margin-bottom: 12px;"><label style="font-weight: bold; display: block; margin-bottom: 4px;">Phone Number</label><input type="text" name="phone" required style="width:100%; padding: 8px; border: 1px solid #cbd5e1; border-radius: 4px; box-sizing:border-box;"></div>
                <div style="margin-bottom: 16px;"><label style="font-weight: bold; display: block; margin-bottom: 4px;">Shipping Address</label><textarea name="address" required style="width:100%; padding: 8px; border: 1px solid #cbd5e1; border-radius: 4px; box-sizing:border-box; height:80px;"></textarea></div>
                <button type="submit" style="width:100%; background: #2563eb; color: white; padding: 12px; font-weight: bold; border: none; border-radius: 6px; cursor: pointer;">Place Order</button>
            </form>
        </div>
    </body>
    </html>
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
        status = o['status'] or 'Placed'
        discount_val = o['discount_amount'] or 0

        if status == 'Cancelled':
            badge_style = "background: #fee2e2; color: #991b1b;"
        elif status == 'Delivered':
            badge_style = "background: #d1fae5; color: #065f46;"
        else:
            badge_style = "background: #e0f2fe; color: #0369a1;"

        action_buttons = f'<a href="/order/track/{o["id"]}" style="background: #800000; color: white; padding: 10px 18px; border-radius: 8px; font-size: 13px; text-decoration: none; font-weight: 700; display: inline-flex; align-items: center; gap: 6px;">📦 Track Order</a>'
        
        if status == 'Delivered':
            action_buttons += f' <a href="/order/refund/{o["id"]}" style="background: #fef08a; color: #854d0e; padding: 10px 18px; border-radius: 8px; font-size: 13px; text-decoration: none; font-weight: 700; display: inline-block;">Refund Details</a>'
        elif status in ['Placed', 'Packed', 'Processing']:
            action_buttons += f' <a href="/order/cancel/{o["id"]}" style="background: #ef4444; color: white; padding: 10px 18px; border-radius: 8px; font-size: 13px; text-decoration: none; font-weight: 700; display: inline-block;">Cancel Order</a>'
        else:
            action_buttons += f' <a href="/order/refund/{o["id"]}" style="background: #fef08a; color: #854d0e; padding: 10px 18px; border-radius: 8px; font-size: 13px; text-decoration: none; font-weight: 700; display: inline-block;">Refund Details</a>'

        orders_html += f"""
        <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.02);">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
                <div>
                    <h3 style="margin: 0 0 6px 0; font-size: 20px; font-weight: 800; color: #0f172a;">Order #{o['id']}</h3>
                    <span style="padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; display: inline-block; {badge_style}">{status}</span>
                </div>
                <div style="text-align: right;">
                    <div style="font-size: 22px; font-weight: 800; color: #ff5e62;">Rs. {total_str}</div>
                    <div style="font-size: 12px; color: #64748b;">Discount: Rs. {discount_val}</div>
                </div>
            </div>

            <hr style="border: 0; border-top: 1px solid #f1f5f9; margin: 16px 0;">

            <div style="font-size: 13px; color: #334155; margin-bottom: 20px;">
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
                <div style="font-size: 11px; color: #64748b;">Status milestone for order fulfillment</div>
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
            <p style="color: #64748b; font-size: 13px; margin-bottom: 24px;">Order ID: <b>#{{ order.id }}</b> | Customer: <b>{{ order.customer_name }}</b></p>
            
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
        elif username == 'staff' and password == 'staff123':
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
                <div style="margin-bottom: 14px;"><label style="font-weight: 600; display: block; margin-bottom: 6px; font-size: 13px; color: #475569;">Username</label><input type="text" name="username" required style="width: 100%; padding: 10px; border: 1px solid #cbd5e1; border-radius: 6px; box-sizing: border-box;"></div>
                <div style="margin-bottom: 20px;"><label style="font-weight: 600; display: block; margin-bottom: 6px; font-size: 13px; color: #475569;">Password</label><input type="password" name="password" required style="width: 100%; padding: 10px; border: 1px solid #cbd5e1; border-radius: 6px; box-sizing: border-box;"></div>
                <button type="submit" style="width: 100%; background: #2563eb; color: white; padding: 11px; font-weight: bold; border: none; border-radius: 6px; cursor: pointer; font-size: 14px;">Login</button>
            </form>
            <a href="/" style="display: block; text-align: center; margin-top: 16px; color: #64748b; text-decoration: none; font-size: 13px;">← Return to Storefront</a>
        </div>
    </body>
    </html>
    """)

@app.route('/admin/dashboard', methods=['GET', 'POST'])
@role_required('admin', 'staff')
def admin_dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
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
        <tr style="border-bottom: 1px solid #e2e8f0; font-size: 13px; color: #334155;">
            <td style="padding: 14px; font-weight: 700;">#{o['id']}</td>
            <td style="padding: 14px;">
                <div style="font-weight: 700; color: #0f172a;">{o['customer_name'] or 'Customer'}</div>
                <div style="font-size: 11px; color: #64748b;">{o['customer_email'] or ''}</div>
                <div style="font-size: 11px; color: #64748b;">{o['customer_phone'] or ''}</div>
            </td>
            <td style="padding: 14px; max-width: 280px; font-size: 11px; color: #64748b; line-height: 1.4;">
                {o['customer_address'] or 'N/A'}
            </td>
            <td style="padding: 14px; font-weight: 800; color: #2563eb;">₹{total_str}</td>
            <td style="padding: 14px; font-size: 12px; color: #64748b;">{o['created_at'] or ''}</td>
            <td style="padding: 14px;">
                <span style="background: {badge_bg}; color: white; padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 700; display: inline-block;">{status}</span>
            </td>
            <td style="padding: 14px;">
                <form method="POST" style="display: flex; gap: 4px; margin: 0; align-items: center;">
                    <input type="hidden" name="csrf_token" value="{session.get('csrf_token', '')}">
                    <input type="hidden" name="order_id" value="{o['id']}">
                    <select name="status" style="padding: 6px; border-radius: 6px; border: 1px solid #cbd5e1; font-size: 12px; outline: none;">
                        <option value="Placed" {'selected' if status=='Placed' else ''}>Placed</option>
                        <option value="Processing" {'selected' if status=='Processing' else ''}>Processing</option>
                        <option value="Shipped" {'selected' if status=='Shipped' else ''}>Shipped</option>
                        <option value="Delivered" {'selected' if status=='Delivered' else ''}>Delivered</option>
                        <option value="Refund Requested" {'selected' if status=='Refund Requested' else ''}>Refund Requested</option>
                        <option value="Refunded" {'selected' if status=='Refunded' else ''}>Refunded</option>
                        <option value="Cancelled" {'selected' if status=='Cancelled' else ''}>Cancelled</option>
                    </select>
                    <button type="submit" style="background: #2563eb; color: white; border: none; padding: 6px 10px; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 12px;">✓</button>
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
            .nav-item { padding: 12px 20px; color: #94a3b8; text-decoration: none; font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 10px; transition: 0.2s; }
            .nav-item:hover { color: #ffffff; }
            .nav-item.active { background: #2563eb; color: #ffffff; border-radius: 0 20px 20px 0; margin-right: 15px; }
            .nav-sub { font-size: 12px; color: #64748b; padding: 20px 20px 8px; font-weight: 700; text-transform: uppercase; }
            .main-content { flex: 1; padding: 30px; background: #f1f5f9; min-height: 100vh; }
            .header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
            .btn-action { padding: 8px 14px; border-radius: 6px; text-decoration: none; font-size: 12px; font-weight: 700; display: inline-flex; align-items: center; gap: 6px; color: white; border: none; cursor: pointer; }
            .btn-amber { background: #f59e0b; color: #000; }
            .btn-blue { background: #2563eb; }
            .btn-emerald { background: #059669; }
            .data-table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
            .data-table th { background: #181920; color: white; text-align: left; padding: 12px 14px; font-size: 12px; font-weight: 700; }
        </style>
    </head>
    <body>
        <div class="sidebar">
            <a href="/" class="brand">PageTurner</a>
            <a href="/admin/dashboard" class="nav-item active"><i class="fa-solid fa-gauge"></i> Dashboard</a>
            <a href="/admin/inventory" class="nav-item"><i class="fa-solid fa-boxes-stacked"></i> Inventory</a>
            <a href="/admin/coupons" class="nav-item"><i class="fa-solid fa-ticket"></i> Coupons</a>
            <a href="/admin/analytics" class="nav-item"><i class="fa-solid fa-chart-simple"></i> Analytics</a>
            <a href="/admin/reviews" class="nav-item"><i class="fa-solid fa-comment"></i> Reviews</a>
            
            <div class="nav-sub">Storefront</div>
            <a href="/" class="nav-item"><i class="fa-solid fa-shop"></i> Storefront</a>
            
            <div style="margin-top: auto; padding: 20px;">
                <a href="/logout" style="color: #ef4444; text-decoration: none; font-size: 13px; font-weight: bold;"><i class="fa-solid fa-right-from-bracket"></i> Logout Admin</a>
            </div>
        </div>

        <div class="main-content">
            <div class="header-bar">
                <div>
                    <h2 style="margin: 0 0 4px 0; font-size: 24px; font-weight: 800; color: #0f172a;">Interactive Order Management</h2>
                    <p style="margin: 0; color: #64748b; font-size: 13px;">Track customer orders, manage shipping status, and view sales insights.</p>
                </div>
                <div style="display: flex; gap: 10px;">
                    <a href="/admin/inventory" class="btn-action btn-amber"><i class="fa-solid fa-boxes-stacked"></i> Inventory Control</a>
                    <a href="/admin/analytics" class="btn-action btn-blue"><i class="fa-solid fa-chart-simple"></i> Sales Analytics</a>
                    <a href="/admin/export_orders" class="btn-action btn-emerald"><i class="fa-solid fa-file-csv"></i> Export Orders CSV</a>
                </div>
            </div>

            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}
                {% for cat, msg in messages %}
                  <div style="padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; background: #d1fae5; color: #065f46; font-size: 13px; font-weight: 600;">{{ msg }}</div>
                {% endfor %}
              {% endif %}
            {% endwith %}

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
@role_required('admin', 'staff')
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
@role_required('admin', 'staff')
def admin_inventory():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        author = request.form.get('author', '').strip()
        price = int(float(request.form.get('price', '0')) * 100)
        stock = int(request.form.get('stock', '10'))
        category = request.form.get('category', 'Fiction').strip()
        cursor.execute("INSERT INTO books (title, author, price, stock, category, image_url) VALUES (?, ?, ?, ?, ?, ?)",
                       (title, author, price, stock, category, "https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=400"))
        conn.commit()
        flash("Book added successfully!", "success")

    cursor.execute("SELECT * FROM books ORDER BY id DESC")
    books = cursor.fetchall()
    conn.close()

    book_rows = ""
    for b in books:
        book_rows += f"""
        <tr style="border-bottom: 1px solid #f1f5f9; font-size: 13px;">
            <td style="padding: 10px;">{b['id']}</td>
            <td style="padding: 10px; font-weight: bold;">{b['title']}</td>
            <td style="padding: 10px;">{b['author']}</td>
            <td style="padding: 10px; color: #2563eb; font-weight: bold;">₹{format_inr(b['price'])}</td>
            <td style="padding: 10px;">{b['stock']}</td>
            <td style="padding: 10px;">{b['category']}</td>
        </tr>
        """

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Inventory Control</title></head>
    <body style="font-family: sans-serif; margin: 0; background: #f1f5f9; display: flex;">
        <div style="width: 210px; background: #181920; color: white; min-height: 100vh; padding: 20px 0;">
            <a href="/" style="color: #f59e0b; font-size: 20px; font-weight: 800; padding: 0 20px 24px; text-decoration: none; display: block;">PageTurner</a>
            <a href="/admin/dashboard" style="display: block; color: #94a3b8; text-decoration: none; padding: 12px 20px; font-size: 13px;">Dashboard</a>
            <a href="/admin/inventory" style="display: block; color: white; text-decoration: none; padding: 12px 20px; background: #2563eb; font-size: 13px; font-weight: bold;">Inventory Control</a>
            <a href="/" style="display: block; color: #ef4444; text-decoration: none; padding: 12px 20px; font-size: 13px; margin-top: 30px;">Storefront</a>
        </div>
        <div style="flex: 1; padding: 30px;">
            <h2>Inventory Control & Add Book</h2>
            <form method="POST" style="background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; display: grid; grid-template-columns: repeat(3, 1fr) auto; gap: 10px;">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <input type="text" name="title" placeholder="Book Title" required style="padding: 8px; border: 1px solid #cbd5e1; border-radius: 4px;">
                <input type="text" name="author" placeholder="Author" required style="padding: 8px; border: 1px solid #cbd5e1; border-radius: 4px;">
                <input type="number" name="price" placeholder="Price (in ₹ integer)" required style="padding: 8px; border: 1px solid #cbd5e1; border-radius: 4px;">
                <input type="number" name="stock" placeholder="Stock" value="10" required style="padding: 8px; border: 1px solid #cbd5e1; border-radius: 4px;">
                <input type="text" name="category" placeholder="Category" value="Fiction" required style="padding: 8px; border: 1px solid #cbd5e1; border-radius: 4px;">
                <button type="submit" style="background: #2563eb; color: white; border: none; padding: 8px 16px; font-weight: bold; border-radius: 4px; cursor: pointer;">Add Book</button>
            </form>
            <div style="background: white; border-radius: 8px; padding: 15px;">
                <table style="width: 100%; border-collapse: collapse; text-align: left;">
                    <thead>
                        <tr style="border-bottom: 2px solid #e2e8f0; font-size: 13px; color: #475569;">
                            <th style="padding: 10px;">ID</th><th style="padding: 10px;">Title</th><th style="padding: 10px;">Author</th><th style="padding: 10px;">Price</th><th style="padding: 10px;">Stock</th><th style="padding: 10px;">Category</th>
                        </tr>
                    </thead>
                    <tbody>
                        {{ book_rows | safe }}
                    </tbody>
                </table>
            </div>
        </div>
    </body>
    </html>
    """, book_rows=book_rows)

@app.route('/admin/coupons')
@role_required('admin', 'staff')
def admin_coupons():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM coupons")
    coupons = cursor.fetchall()
    conn.close()
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Coupons Management</title></head>
    <body style="font-family: sans-serif; padding: 40px; background: #f8fafc;">
        <div style="max-width: 600px; background: white; padding: 30px; border-radius: 10px; margin: auto;">
            <h2>🎟️ Active Coupons</h2>
            {% for c in coupons %}
            <div style="padding: 10px; border-bottom: 1px solid #e2e8f0;">
                <b>{{ c.code }}</b> - {{ c.discount_value }} {{ c.discount_type }}
            </div>
            {% endfor %}
            <a href="/admin/dashboard" style="display: inline-block; margin-top: 20px; background: #2563eb; color: white; padding: 8px 16px; text-decoration: none; border-radius: 4px; font-weight: bold;">Back to Dashboard</a>
        </div>
    </body>
    </html>
    """, coupons=coupons)

@app.route('/admin/analytics')
@role_required('admin', 'staff')
def admin_analytics():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(total_amount) as rev, COUNT(*) as cnt FROM orders")
    res = cursor.fetchone()
    conn.close()
    revenue = res['rev'] or 0
    count = res['cnt'] or 0
    aov = revenue / count if count > 0 else 0
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Sales Analytics</title></head>
    <body style="font-family: sans-serif; padding: 40px; background: #f8fafc;">
        <div style="max-width: 600px; background: white; padding: 30px; border-radius: 10px; margin: auto; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
            <h2>Sales Analytics</h2>
            <p><b>Total Revenue:</b> ₹{{ revenue | inr }}</p>
            <p><b>Total Orders:</b> {{ count }}</p>
            <p><b>Average Order Value (AOV):</b> ₹{{ aov | inr }}</p>
            <a href="/admin/dashboard" style="display: inline-block; margin-top: 20px; background: #2563eb; color: white; padding: 8px 16px; text-decoration: none; border-radius: 4px; font-weight: bold;">Back to Dashboard</a>
        </div>
    </body>
    </html>
    """, revenue=revenue, count=count, aov=aov)

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

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE (email = ? OR username = ?) AND password = ?", (email, email, password))
        user = cursor.fetchone()

        if user:
            conn.close()
            session['user_id'] = user['id']
            session['user_name'] = user['name'] or user['username']
            session['user_email'] = user['email']
            session['user_role'] = user['role'] if 'role' in user.keys() and user['role'] else 'customer'
            flash(f"Welcome back, {session['user_name']}.", "success")
            return redirect(url_for('home'))
        else:
            conn.close()
            flash("Invalid credentials.", "danger")
            return redirect(url_for('login'))

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head><title>Customer Login - PageTurner</title></head>
    <body style="background:#f8fafc; display:flex; align-items:center; justify-content:center; height:100vh; font-family:sans-serif;">
        <div style="background:white; padding:30px; border-radius:10px; width:350px; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
            <h4 style="text-align:center; margin-top:0;">Customer Login</h4>
            <form method="POST">
                <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
                <div style="margin-bottom:10px;"><label style="font-weight:bold; font-size:13px;">Email / Username</label><input type="text" name="email" required style="width:100%; padding:8px; border:1px solid #cbd5e1; border-radius:4px; box-sizing:border-box;"></div>
                <div style="margin-bottom:16px;"><label style="font-weight:bold; font-size:13px;">Password</label><input type="password" name="password" required style="width:100%; padding:8px; border:1px solid #cbd5e1; border-radius:4px; box-sizing:border-box;"></div>
                <button type="submit" style="width:100%; padding:10px; background:#2563eb; color:white; border:none; border-radius:4px; font-weight:bold; cursor:pointer;">Login</button>
            </form>
            <div style="text-align:center; margin-top:12px; font-size:13px;">Don't have an account? <a href="/register" style="color:#2563eb;">Sign up here</a></div>
        </div>
    </body>
    </html>
    """)

@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for('home'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)